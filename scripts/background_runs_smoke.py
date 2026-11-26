"""Smoke test for background runs against a running front door + runner.

    python scripts/background_runs_smoke.py --base http://127.0.0.1:5001

Uses real questions, so it calls the models (a few cents at most). Run it on
the box after switching BACKGROUND_RUNS on (DEPLOYMENT.md "Background runs").
The API key is read from the X_API_KEY environment variable, else from the
first key in API_KEYS; it is never printed.

Checks, in order:
  1. /pyapi/health/runs: a runner is alive
  2. ask, close the connection after a few events: the run keeps going and
     finishes; reattaching with ?after=<last seq> returns the rest with no
     gap and no repeat, ending in `done`
  3. a second question on the same thread while one runs: 409 with the
     active run id; Stop frees the thread at once; the next question runs
  4. (optional, --pause-for-restart) restart the web server or the runner by
     hand mid-answer, then reattach: the answer still completes
Exit code 0 when every check passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid

import httpx

Q_LONG = "Explain the ingredients of an offence of cheating under the Bharatiya Nyaya Sanhita, 2023, with the section number."
Q_SHORT = "What is the punishment for theft under the Bharatiya Nyaya Sanhita?"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


def events(resp, stop=None):
    out = []
    for line in resp.iter_lines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
            if stop and stop(out):
                break
    return out


def post_stream(base, headers, body, stop=None, timeout=600):
    with httpx.stream("POST", f"{base}/pyapi/search/stream", json=body, headers=headers,
                      timeout=timeout) as r:
        if r.status_code != 200:
            return r.status_code, r.read().decode(errors="replace")[:300], []
        return 200, "", events(r, stop)


def reattach(base, headers, run_id, after, timeout=600):
    with httpx.stream("GET", f"{base}/pyapi/runs/{run_id}/stream", params={"after": after},
                      headers=headers, timeout=timeout) as r:
        r.raise_for_status()
        return events(r)


def wait_terminal(base, headers, run_id, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = httpx.get(f"{base}/pyapi/runs/{run_id}", headers=headers, timeout=30).json()
        if s.get("status") in ("done", "failed", "cancelled"):
            return s
        time.sleep(2)
    return {"status": "timeout"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5001")
    ap.add_argument("--pause-for-restart", action="store_true",
                    help="stop after the first events so you can restart a service, then reattach")
    a = ap.parse_args()
    key = os.getenv("X_API_KEY") or (os.getenv("API_KEYS", "").split(",")[0].strip())
    headers = {"X-API-Key": key} if key else {}
    base = a.base.rstrip("/")

    h = httpx.get(f"{base}/pyapi/health/runs", timeout=30).json()
    print("health/runs:", {k: h.get(k) for k in ("status", "enabled", "backend", "runners",
                                                 "capacity", "queued", "running", "listener_connected")})
    if not check("runner alive and flag on", h.get("status") == "ok" and h.get("enabled") is True,
                 f"status={h.get('status')} enabled={h.get('enabled')}"):
        return 1

    # 2. disconnect mid-answer, then reattach
    thread = f"smoke-{uuid.uuid4().hex[:8]}"
    t0 = time.time()
    code, err, first = post_stream(base, headers, {"Promptquery": Q_LONG, "globalThreadId": thread,
                                                   "user_id": "smoke-user"},
                                   stop=lambda es: len(es) >= 6)
    if not check("stream starts", code == 200 and len(first) >= 2 and first[1].get("type") == "run_id",
                 err or f"first events: {[e.get('type') for e in first]}"):
        return 1
    run_id, last_seq = first[1]["data"], first[-1].get("seq", 0)
    print(f"closed the connection after {len(first)} events (seq {last_seq}); run {run_id[:12]}")
    if a.pause_for_restart:
        input("Restart the web server or the runner now, then press Enter to reattach... ")
    st = wait_terminal(base, headers, run_id)
    check("answer completed after the app left", st["status"] == "done",
          f"status={st['status']} after {time.time() - t0:.0f}s")
    rest = reattach(base, headers, run_id, last_seq)
    seqs = [e.get("seq") for e in first[2:]] + [e.get("seq") for e in rest]
    check("reattach: no gap, no repeat", seqs == list(range(1, len(seqs) + 1)),
          f"{len(seqs)} events, last seq {seqs[-1] if seqs else None}")
    check("reattach ends in done", bool(rest) and rest[-1].get("type") == "done")
    resp = next((e.get("content") for e in reversed(first + rest) if e.get("type") == "response"), "")
    check("a response was delivered", len(resp or "") > 50, f"{len(resp or '')} chars")

    # 3. busy thread, Stop, next question
    thread2 = f"smoke-{uuid.uuid4().hex[:8]}"
    code, err, first2 = post_stream(base, headers, {"Promptquery": Q_LONG, "globalThreadId": thread2},
                                    stop=lambda es: len(es) >= 2)
    run2 = first2[1]["data"] if code == 200 and len(first2) > 1 else ""
    r409 = httpx.post(f"{base}/pyapi/search/stream", headers=headers, timeout=60,
                      json={"Promptquery": Q_SHORT, "globalThreadId": thread2})
    check("second question on a busy thread gets 409",
          r409.status_code == 409 and r409.json().get("active_run_id") == run2,
          f"HTTP {r409.status_code}")
    rc = httpx.post(f"{base}/pyapi/runs/{run2}/cancel", headers=headers, timeout=60)
    check("Stop cancels the run", rc.status_code == 200 and rc.json().get("status") == "cancelled",
          f"HTTP {rc.status_code}")
    code, err, third = post_stream(base, headers, {"Promptquery": Q_SHORT, "globalThreadId": thread2})
    check("thread free right after Stop", code == 200 and third and third[-1].get("type") == "done",
          err or f"last event {third[-1].get('type') if third else None}")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
