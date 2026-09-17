"""Reproduce the client-reported bug: system produces ARGUMENTS instead of
COUNTER-arguments when the user explicitly asks for counter-arguments on
behalf of the Opponent (Section 33(2)(b) Industrial Disputes Act context).

Target: env SMOKE_BASE (defaults to localhost:5000 for local repro).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:5000/pyapi")
KEY = os.environ["SMOKE_KEY"]
TAG = os.environ.get("SMOKE_TAG", "local")

PROMPT = (
    "Make notes of counter arguments on behalf of Opponent, Cover all the "
    "points including Violation of 33(2)(b) by non payment of one month's "
    "wages or lesser wages including deductions, in any case a lesser "
    "payment than what has been mandated, So also not considered the past "
    "record of service and victimizing the workmen for the union "
    "activities. Also provide relevant case laws."
)

OUT_DIR = Path(__file__).parent
OUT_MD = OUT_DIR / f"_counter_args_{TAG}.md"
OUT_META = OUT_DIR / f"_counter_args_{TAG}_meta.json"
OUT_RAW = OUT_DIR / f"_counter_args_{TAG}_events.jsonl"


async def main() -> int:
    print(f"BASE: {BASE}")
    print(f"PROMPT ({len(PROMPT)} chars): {PROMPT[:120]}...")

    t0 = time.time()
    payload = {"Promptquery": PROMPT}
    answer = ""
    events_seen: dict[str, int] = {}
    agents_planned: list[str] = []
    unique_steps: set[str] = set()
    errors: list[str] = []
    done_event: dict | None = None
    ttfb_ms: int | None = None
    raw_lines = 0
    thread_id: str | None = None
    effective_query: str | None = None
    query_rewritten: bool | None = None

    with OUT_RAW.open("w", encoding="utf-8") as raw_fh:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{BASE}/search/stream",
                headers={"X-API-Key": KEY, "Content-Type": "application/json"},
                json=payload,
                timeout=1500.0,
            ) as resp:
                if ttfb_ms is None:
                    ttfb_ms = round((time.time() - t0) * 1000)
                if resp.status_code != 200:
                    err = await resp.aread()
                    print(f"HTTP {resp.status_code}: {err.decode(errors='replace')[:500]}")
                    return 1
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        e = json.loads(line[6:])
                    except Exception:
                        continue
                    raw_fh.write(line[6:] + "\n")
                    raw_lines += 1
                    t = e.get("type") or "<no-type>"
                    events_seen[t] = events_seen.get(t, 0) + 1
                    if t == "thread_id":
                        thread_id = e.get("data")
                    elif t == "token":
                        tok = e.get("data") or ""
                        if isinstance(tok, str):
                            answer += tok
                    elif t in ("response", "final_answer"):
                        txt = e.get("content") or e.get("data") or ""
                        if isinstance(txt, str) and len(txt) > len(answer):
                            answer = txt
                    elif t == "agents_planned":
                        agents_planned = e.get("agents") or e.get("data") or []
                    elif t == "progress":
                        step = e.get("step") or e.get("stage")
                        if step:
                            unique_steps.add(step)
                    elif t == "context":
                        effective_query = e.get("effective_query")
                        query_rewritten = e.get("query_rewritten")
                    elif t in ("error", "guardrail_error"):
                        errors.append(json.dumps(e, ensure_ascii=False)[:500])
                    elif t == "done":
                        done_event = e
                        for k in ("final_response", "response", "final_answer"):
                            v = e.get(k)
                            if isinstance(v, str) and len(v) > len(answer):
                                answer = v

    elapsed = round(time.time() - t0, 2)
    OUT_MD.write_text(answer or "(empty)", encoding="utf-8")

    meta = {
        "base": BASE,
        "prompt": PROMPT,
        "elapsed_s": elapsed,
        "ttfb_ms": ttfb_ms,
        "thread_id": thread_id,
        "effective_query": effective_query,
        "query_rewritten": query_rewritten,
        "agents_planned": agents_planned,
        "answer_len": len(answer),
        "answer_words": len(answer.split()),
        "event_type_counts": events_seen,
        "unique_progress_steps": sorted(unique_steps),
        "errors": errors,
        "raw_events_written": raw_lines,
        "done_event_keys": list((done_event or {}).keys()),
    }
    OUT_META.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(meta, indent=2, ensure_ascii=False))
    print()
    print(f"MD:   {OUT_MD}")
    print(f"Meta: {OUT_META}")
    print(f"Raw:  {OUT_RAW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
