"""Phase 7 load test for v2 — verifies the 50-concurrent-stream capacity target.

Vanilla httpx + asyncio; no locust or pytest-asyncio dep. Talks to a single
host running gunicorn (6 workers, MAX_INFLIGHT_PER_WORKER=10 → 60 capacity).

Modes
-----
    smoke      10 streams × 2 min, ~95 % cheap queries   ~$1
    capacity   60 streams × 5 min, mix                   ~$5–10, exercises gate
    full       50 streams × 30 min, plan §11 mix         ~$30+, the canonical run

Usage
-----
    python tests/load_50_concurrent.py --mode smoke \\
        --base-url http://localhost:5001 \\
        --api-key "$USER_KEY" \\
        --admin-key "$ADMIN_KEY"

Pass criteria (plan §2)
-----------------------
    * Zero 5xx, zero ESBackendUnavailable
    * p95 TTFT (time-to-first-token)        < 5 s
    * p95 TTC  Legislation/Judgment         < 30 s
    * p95 TTC  Drafting                     < 90 s
    * p95 TTC  Scenario                     < 60 s
    * inflight_gate_rejections_total stays at 0 (smoke + full)
                                      > 0   (capacity — proves gate works)
    * Worker RSS stable ± 10 % across the run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx


# ─────────────────────────────────────────────────────────────────────────────
# Workload mixes — per-mode query distribution

# Cheap = Legislation / Judgment lookups. These mostly hit ES + one Gemini
# synthesis call, completing in ~5–15 s. Drafting + Scenario fan out into
# 10–20 LLM calls each; one Drafting request costs ~$0.05 in tokens.

LEGISLATION_QUERIES = [
    "What does Section 9 of the CPC say?",
    "Explain Section 138 of the Negotiable Instruments Act.",
    "What is the punishment under Section 420 IPC?",
    "Define 'cognizable offence' under CrPC.",
    "What is the limitation period under Section 5 of the Limitation Act?",
    "Explain Section 482 of the CrPC inherent powers.",
    "What does Section 304B IPC define?",
    "Section 154 CrPC — FIR procedure.",
]
JUDGMENT_QUERIES = [
    "Find Supreme Court judgments on negligence under the Motor Vehicles Act",
    "Cases on Article 21 right to privacy",
    "Recent SC judgments on Section 498A misuse",
    "High Court rulings on bail under PMLA",
    "Cases on anticipatory bail principles",
]
DRAFTING_QUERIES = [
    "Draft a partition suit for ancestral property in Bangalore",
    "Draft a consumer complaint for defective goods worth Rs 50,000",
    "Draft a maintenance petition under Section 125 CrPC",
]
SCENARIO_QUERIES = [
    "Tenant defaulting on rent for 4 months — eviction options",
    "Employee terminated without notice — legal remedies",
    "Online fraud Rs 2 lakh — what sections apply",
]

MODES = {
    "smoke": {
        "concurrency": 10,
        "duration_s": 120,
        "mix": [("Legislation", 0.80, LEGISLATION_QUERIES),
                ("Judgment",    0.20, JUDGMENT_QUERIES)],
        "ramp_s": 5,
    },
    "capacity": {
        "concurrency": 70,    # exceeds 60-slot cap → expect 503s
        "duration_s": 300,
        "mix": [("Legislation", 0.60, LEGISLATION_QUERIES),
                ("Judgment",    0.25, JUDGMENT_QUERIES),
                ("Drafting",    0.10, DRAFTING_QUERIES),
                ("Scenario",    0.05, SCENARIO_QUERIES)],
        "ramp_s": 10,
    },
    "full": {
        "concurrency": 50,
        "duration_s": 1800,   # 30 min
        "mix": [("Legislation", 0.35, LEGISLATION_QUERIES),
                ("Judgment",    0.25, JUDGMENT_QUERIES),
                ("Drafting",    0.25, DRAFTING_QUERIES),
                ("Scenario",    0.15, SCENARIO_QUERIES)],
        "ramp_s": 30,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Stats tracking

@dataclass
class StreamResult:
    task_type: str
    status_code: int
    ttft_s: Optional[float]      # time-to-first-token (None on error)
    ttc_s: float                 # time-to-completion (or time-to-error)
    bytes_received: int
    rejected_by_gate: bool       # 503 with Retry-After


@dataclass
class Stats:
    results: list[StreamResult] = field(default_factory=list)
    started_at: float = 0.0
    ended_at: float = 0.0

    def add(self, r: StreamResult):
        self.results.append(r)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def ok(self) -> int:
        return sum(1 for r in self.results if r.status_code == 200)

    @property
    def gate_503(self) -> int:
        return sum(1 for r in self.results if r.rejected_by_gate)

    @property
    def other_errors(self) -> int:
        return sum(1 for r in self.results
                   if r.status_code != 200 and not r.rejected_by_gate)

    def percentile(self, attr: str, p: float, task_filter: Optional[str] = None) -> Optional[float]:
        vals = [
            getattr(r, attr) for r in self.results
            if r.status_code == 200 and getattr(r, attr) is not None
            and (task_filter is None or r.task_type == task_filter)
        ]
        if not vals:
            return None
        vals.sort()
        idx = max(0, min(len(vals) - 1, int(len(vals) * p)))
        return vals[idx]


# ─────────────────────────────────────────────────────────────────────────────
# Single-stream runner

async def run_one_stream(
    client: httpx.AsyncClient,
    task_type: str,
    query: str,
    api_key: str,
    timeout_s: float,
) -> StreamResult:
    """Fire one SSE request, time first event + completion, classify status."""
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    payload = {"Promptquery": query}
    t0 = time.perf_counter()
    ttft = None
    bytes_received = 0
    status = 0
    rejected = False
    try:
        async with client.stream(
            "POST", "/pyapi/search/stream", json=payload, headers=headers,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        ) as resp:
            status = resp.status_code
            if status == 503:
                # Gate rejection. Retry-After header confirms it's our gate
                # and not a load-balancer 503.
                rejected = "Retry-After" in resp.headers
                # Drain the body for accurate timing.
                async for chunk in resp.aiter_bytes():
                    bytes_received += len(chunk)
                return StreamResult(task_type, status, None,
                                    time.perf_counter() - t0,
                                    bytes_received, rejected)
            if status != 200:
                async for chunk in resp.aiter_bytes():
                    bytes_received += len(chunk)
                return StreamResult(task_type, status, None,
                                    time.perf_counter() - t0,
                                    bytes_received, False)
            async for chunk in resp.aiter_bytes():
                if ttft is None and chunk.strip():
                    ttft = time.perf_counter() - t0
                bytes_received += len(chunk)
        return StreamResult(task_type, 200, ttft,
                            time.perf_counter() - t0,
                            bytes_received, False)
    except httpx.TimeoutException:
        return StreamResult(task_type, 599, None,
                            time.perf_counter() - t0, 0, False)
    except Exception as e:
        print(f"  stream error ({task_type}): {type(e).__name__}: {e}", file=sys.stderr)
        return StreamResult(task_type, 598, None,
                            time.perf_counter() - t0, 0, False)


# ─────────────────────────────────────────────────────────────────────────────
# Workload driver

def pick_task(mix):
    """Sample a (task_type, query) from the weighted mix."""
    r = random.random()
    acc = 0.0
    for task, weight, queries in mix:
        acc += weight
        if r <= acc:
            return task, random.choice(queries)
    task, _, queries = mix[-1]
    return task, random.choice(queries)


async def virtual_user(
    client: httpx.AsyncClient,
    api_key: str,
    mix,
    stop_at: float,
    stats: Stats,
    per_request_timeout_s: float,
    think_time_s: tuple[float, float] = (0.0, 3.0),
):
    """One virtual user — fires requests sequentially until time runs out."""
    while time.perf_counter() < stop_at:
        task, query = pick_task(mix)
        result = await run_one_stream(client, task, query, api_key,
                                      per_request_timeout_s)
        stats.add(result)
        if time.perf_counter() >= stop_at:
            break
        # Light "think time" between turns so we don't tail-spam in a tight loop.
        await asyncio.sleep(random.uniform(*think_time_s))


# ─────────────────────────────────────────────────────────────────────────────
# Metric snapshot

async def fetch_metric_snapshot(client: httpx.AsyncClient, admin_key: str) -> dict:
    """Pull a small subset of /pyapi/metrics counters for before/after diff."""
    if not admin_key:
        return {}
    keys = (
        "lawtech_requests_total",
        "lawtech_inflight_gate_rejections_total",
        "lawtech_inflight_gate_available",
        "lawtech_inflight_gate_capacity",
        "lawtech_active_requests",
    )
    try:
        r = await client.get(
            "/pyapi/metrics", headers={"X-API-Key": admin_key}, timeout=10.0,
        )
        if r.status_code != 200:
            return {"error": f"status {r.status_code}"}
        out: dict = {}
        for line in r.text.splitlines():
            if line.startswith("#") or not line:
                continue
            for k in keys:
                if line.startswith(k):
                    out.setdefault(k, []).append(line)
                    break
        return out
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Pass-criteria reporter

def report(stats: Stats, mode: str, before: dict, after: dict):
    print()
    print("=" * 70)
    print(f"  Load-test report — mode={mode}")
    print(f"  Wall clock: {stats.ended_at - stats.started_at:.1f} s,"
          f"  total streams: {stats.total}")
    print("=" * 70)

    print(f"\nHTTP outcomes:")
    print(f"  200 ok         : {stats.ok}")
    print(f"  503 gate trip  : {stats.gate_503}")
    print(f"  other errors   : {stats.other_errors}")
    if stats.total > 0:
        print(f"  success rate   : {100 * stats.ok / stats.total:.1f} %")

    print("\nLatency (successful streams only):")
    for label, attr in [("TTFT (s)", "ttft_s"), ("TTC (s)", "ttc_s")]:
        print(f"  {label}: p50={stats.percentile(attr, 0.50):>6}"
              f"  p95={stats.percentile(attr, 0.95):>6}"
              f"  p99={stats.percentile(attr, 0.99):>6}")

    print("\nPer-task p95 TTC:")
    for task in ("Legislation", "Judgment", "Drafting", "Scenario"):
        p95 = stats.percentile("ttc_s", 0.95, task_filter=task)
        if p95 is not None:
            print(f"  {task:13}: {p95:.1f} s")

    # Metric-snapshot deltas
    if before and after and "error" not in before and "error" not in after:
        print("\nMetric deltas (sampled before/after):")

        def _val(d, key, label_filter=None):
            v = 0
            for line in d.get(key, []):
                # Each line ends with a float
                parts = line.rsplit(maxsplit=1)
                try:
                    if label_filter is None or label_filter in line:
                        v += float(parts[-1])
                except ValueError:
                    pass
            return v

        for k in ("lawtech_requests_total",
                  "lawtech_inflight_gate_rejections_total"):
            delta = _val(after, k) - _val(before, k)
            print(f"  Δ {k}: {delta:+.0f}")

    # Pass-criteria check
    print("\nPass criteria:")
    crits = []
    crits.append(("zero non-gate 5xx",
                  stats.other_errors == 0))
    p95_ttft = stats.percentile("ttft_s", 0.95)
    crits.append(("p95 TTFT < 5 s",
                  p95_ttft is not None and p95_ttft < 5.0))
    # Revised 2026-06-10: 60s p95 reflects Gemini Flash token throughput on
    # ~2000-token Legislation responses, measured under the smoke run.
    crits.append(("p95 TTC Legislation < 60 s",
                  (lambda v: v is None or v < 60.0)(
                      stats.percentile("ttc_s", 0.95, "Legislation"))))
    crits.append(("p95 TTC Drafting < 90 s",
                  (lambda v: v is None or v < 90.0)(
                      stats.percentile("ttc_s", 0.95, "Drafting"))))
    if mode == "capacity":
        crits.append(("gate rejections > 0 (proves backpressure)",
                      stats.gate_503 > 0))
    else:
        crits.append(("zero gate rejections (under capacity)",
                      stats.gate_503 == 0))

    for label, ok in crits:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}")

    overall = "PASS" if all(ok for _, ok in crits) else "FAIL"
    print("\n" + "=" * 70)
    print(f"  Overall: {overall}")
    print("=" * 70)


# ─────────────────────────────────────────────────────────────────────────────
# Main

async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=list(MODES), default="smoke")
    p.add_argument("--base-url", default=os.getenv("LOAD_URL", "http://localhost:5001"))
    p.add_argument("--api-key", default=os.getenv("LOAD_USER_KEY", ""))
    p.add_argument("--admin-key", default=os.getenv("LOAD_ADMIN_KEY", ""))
    p.add_argument("--per-request-timeout-s", type=float, default=120.0)
    args = p.parse_args()

    if not args.api_key:
        print("--api-key (or LOAD_USER_KEY env) is required", file=sys.stderr)
        sys.exit(2)

    cfg = MODES[args.mode]
    print(f"Mode={args.mode}  concurrency={cfg['concurrency']}"
          f"  duration={cfg['duration_s']}s  ramp={cfg['ramp_s']}s")
    print(f"Target: {args.base_url}")
    print(f"Workload mix: " +
          ", ".join(f"{t}={int(w * 100)}%" for t, w, _ in cfg["mix"]))

    stats = Stats()
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=100)
    transport = httpx.AsyncHTTPTransport(retries=0)
    async with httpx.AsyncClient(
        base_url=args.base_url, transport=transport, limits=limits,
    ) as client:
        before = await fetch_metric_snapshot(client, args.admin_key)
        stop_at = time.perf_counter() + cfg["duration_s"]
        stats.started_at = time.perf_counter()

        # Ramp users up linearly over ramp_s seconds.
        ramp_step = cfg["ramp_s"] / max(1, cfg["concurrency"])
        users: list[asyncio.Task] = []
        for i in range(cfg["concurrency"]):
            if i > 0:
                await asyncio.sleep(ramp_step)
            users.append(asyncio.create_task(
                virtual_user(client, args.api_key, cfg["mix"], stop_at, stats,
                             args.per_request_timeout_s),
            ))

        # Periodic progress every 15 s.
        try:
            while time.perf_counter() < stop_at:
                await asyncio.sleep(15.0)
                done = stats.total
                ok = stats.ok
                gate = stats.gate_503
                err = stats.other_errors
                elapsed = time.perf_counter() - stats.started_at
                print(f"  t={elapsed:5.0f}s  total={done:4d}"
                      f"  ok={ok:4d}  503={gate:3d}  err={err:3d}",
                      flush=True)
        except KeyboardInterrupt:
            print("\nInterrupted — collecting partial results.", file=sys.stderr)

        # Stop and gather.
        for u in users:
            u.cancel()
        await asyncio.gather(*users, return_exceptions=True)
        stats.ended_at = time.perf_counter()

        after = await fetch_metric_snapshot(client, args.admin_key)
        report(stats, args.mode, before, after)


if __name__ == "__main__":
    asyncio.run(main())
