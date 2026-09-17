"""Prod smoke: send two prompts in the SAME thread and capture both answers.

Prompt 1: "How to deal with a Criminal Case?"
Prompt 2: "When should I hire a lawyer?"  (follow-up in same thread)

Target: https://api.lawttorney.com/pyapi/search/stream  (JSON + SSE).

The first call is sent without ``globalThreadId``; the ``thread_id`` event
returned in the SSE stream is captured and re-used as ``globalThreadId`` for
the second call so the memory/orchestrator sees them as one conversation.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import os
BASE = os.environ.get("SMOKE_BASE", "https://api.lawttorney.com/pyapi")
KEY = os.environ["SMOKE_KEY"]
TAG = os.environ.get("SMOKE_TAG", "prod")

PROMPT_1 = "How to deal with a Criminal Case?"
PROMPT_2 = "When should I hire a lawyer?"

OUT_DIR = Path(__file__).parent
OUT_MD = OUT_DIR / f"_same_thread_{TAG}.md"
OUT_META = OUT_DIR / f"_same_thread_{TAG}_meta.json"
OUT_RAW = OUT_DIR / f"_same_thread_{TAG}_events.jsonl"


async def run_turn(
    client: httpx.AsyncClient,
    prompt: str,
    turn_idx: int,
    global_thread_id: str | None,
    raw_fh,
) -> dict:
    t0 = time.time()
    payload: dict = {"Promptquery": prompt}
    if global_thread_id:
        payload["globalThreadId"] = global_thread_id

    answer = ""
    events_seen: dict[str, int] = {}
    agents_planned: list[str] = []
    progress_steps: list[str] = []
    errors: list[str] = []
    done_event: dict | None = None
    ttfb_ms: int | None = None
    raw_lines = 0
    thread_id: str | None = None
    followup_suggestions: list = []
    quality_score = None

    async with client.stream(
        "POST", f"{BASE}/search/stream",
        headers={"X-API-Key": KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=1500.0,
    ) as resp:
        if ttfb_ms is None:
            ttfb_ms = round((time.time() - t0) * 1000)
        if resp.status_code != 200:
            err = await resp.aread()
            msg = err.decode(errors="replace")[:1000]
            return {
                "turn": turn_idx,
                "http_ok": False,
                "http_status": resp.status_code,
                "error_body": msg,
                "elapsed_s": round(time.time() - t0, 2),
            }
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            try:
                e = json.loads(line[6:])
            except Exception:
                continue
            raw_fh.write(json.dumps({"turn": turn_idx, "event": e}, ensure_ascii=False) + "\n")
            raw_lines += 1
            t = e.get("type") or "<no-type>"
            events_seen[t] = events_seen.get(t, 0) + 1
            if t == "thread_id":
                thread_id = e.get("data") or e.get("thread_id")
            elif t == "token":
                tok = e.get("data") or e.get("content") or e.get("token") or ""
                if isinstance(tok, str):
                    answer += tok
            elif t in ("response", "final_answer", "answer"):
                txt = e.get("content") or e.get("data") or e.get("text") or ""
                if isinstance(txt, str) and len(txt) > len(answer):
                    answer = txt
            elif t == "agents_planned":
                agents_planned = e.get("agents") or e.get("data") or []
            elif t == "progress":
                step = e.get("step") or e.get("stage")
                if step:
                    progress_steps.append(step)
            elif t in ("error", "guardrail_error"):
                errors.append(json.dumps(e, ensure_ascii=False)[:500])
            elif t == "followup_suggestions":
                followup_suggestions = e.get("data") or e.get("suggestions") or []
            elif t == "quality_score":
                quality_score = e.get("data") or e.get("score")
            elif t == "done":
                done_event = e
                for k in ("final_response", "response", "final_answer"):
                    v = e.get(k)
                    if isinstance(v, str) and len(v) > len(answer):
                        answer = v

    elapsed = round(time.time() - t0, 2)
    return {
        "turn": turn_idx,
        "prompt": prompt,
        "http_ok": True,
        "http_status": 200,
        "elapsed_s": elapsed,
        "ttfb_ms": ttfb_ms,
        "thread_id": thread_id,
        "answer": answer,
        "answer_len": len(answer),
        "answer_words": len(answer.split()),
        "agents_planned": agents_planned,
        "progress_step_count": len(progress_steps),
        "unique_progress_steps": sorted(set(progress_steps)),
        "event_type_counts": events_seen,
        "raw_events_written": raw_lines,
        "errors": errors,
        "followup_suggestions": followup_suggestions,
        "quality_score": quality_score,
        "done_event_keys": list((done_event or {}).keys()),
    }


async def main() -> int:
    print(f"BASE: {BASE}")
    print(f"PROMPT 1: {PROMPT_1}")
    print(f"PROMPT 2: {PROMPT_2}")
    print()

    with OUT_RAW.open("w", encoding="utf-8") as raw_fh:
        async with httpx.AsyncClient() as client:
            print(f"[turn 1] sending: {PROMPT_1!r} (no thread)")
            r1 = await run_turn(client, PROMPT_1, 1, None, raw_fh)
            if not r1.get("http_ok"):
                print(f"[turn 1] FAILED: {r1}")
                OUT_META.write_text(json.dumps({"turn_1": r1}, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
                return 1
            tid = r1.get("thread_id")
            print(f"[turn 1] done in {r1['elapsed_s']}s, len={r1['answer_len']}, thread={tid}")

            if not tid:
                print("[turn 1] WARNING: no thread_id captured; turn 2 will start a new thread")

            print(f"[turn 2] sending: {PROMPT_2!r} (thread={tid})")
            r2 = await run_turn(client, PROMPT_2, 2, tid, raw_fh)
            if not r2.get("http_ok"):
                print(f"[turn 2] FAILED: {r2}")
            else:
                print(f"[turn 2] done in {r2['elapsed_s']}s, len={r2['answer_len']}, thread={r2.get('thread_id')}")

    # Save meta + combined markdown
    meta = {
        "base": BASE,
        "prompt_1": PROMPT_1,
        "prompt_2": PROMPT_2,
        "thread_id": r1.get("thread_id"),
        "same_thread": (r1.get("thread_id") == r2.get("thread_id")),
        "turn_1": {k: v for k, v in r1.items() if k != "answer"},
        "turn_2": {k: v for k, v in r2.items() if k != "answer"},
    }
    OUT_META.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    md_lines = [
        f"# Same-thread smoke: two-turn conversation",
        "",
        f"- **Thread ID:** `{r1.get('thread_id')}`",
        f"- **Same thread on turn 2:** `{r1.get('thread_id') == r2.get('thread_id')}`",
        "",
        "---",
        "",
        f"## Turn 1 — {PROMPT_1!r}",
        f"- elapsed: {r1.get('elapsed_s')}s | ttfb: {r1.get('ttfb_ms')}ms | words: {r1.get('answer_words')}",
        f"- agents_planned: {r1.get('agents_planned')}",
        "",
        r1.get("answer") or "(empty)",
        "",
        "---",
        "",
        f"## Turn 2 — {PROMPT_2!r}",
        f"- elapsed: {r2.get('elapsed_s')}s | ttfb: {r2.get('ttfb_ms')}ms | words: {r2.get('answer_words')}",
        f"- agents_planned: {r2.get('agents_planned')}",
        "",
        r2.get("answer") or "(empty)",
    ]
    OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")

    print()
    print(json.dumps(meta, indent=2, ensure_ascii=False))
    print()
    print(f"Combined MD:    {OUT_MD}")
    print(f"Meta:           {OUT_META}")
    print(f"Raw SSE:        {OUT_RAW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
