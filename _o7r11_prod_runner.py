"""Prod smoke: fire the Order VII Rule 11 CPC drafting prompt (no attachment)
against https://api.lawttorney.com/pyapi/chat and record the streamed answer,
event breakdown, agents planned, and timing.

Modeled after _bnss290_prod_runner.py — same auth + SSE drain — minus the
multipart file upload and the review-and-redraft heuristics that are not
relevant to a fresh drafting request.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "https://api.lawttorney.com/pyapi"
import os
KEY = os.environ["LAWTECH_API_KEY"]

PROMPT = (
    "Prepare an application under Order VII Rule 11 of the CPC seeking "
    "rejection of the plaint"
)

OUT_DIR = Path(__file__).parent
LABEL = "o7r11_prod"
OUT_MD = OUT_DIR / f"_{LABEL}.md"
OUT_META = OUT_DIR / f"_{LABEL}_meta.json"
OUT_RAW = OUT_DIR / f"_{LABEL}_events.jsonl"
OUT_TXT = OUT_DIR / f"_{LABEL}_run.txt"


def _count_cases(s: str) -> int:
    return len(re.findall(
        r"[A-Z][A-Za-z\.\-\s]{2,40}\s+(?:v\.|vs\.?|Versus)\s+[A-Z][A-Za-z\.\-\s]{2,40}", s))


def _list_case_names(s: str, limit: int = 40) -> list[str]:
    matches = re.findall(
        r"([A-Z][A-Za-z\.\-\s]{2,60}\s+(?:v\.|vs\.?|Versus)\s+[A-Z][A-Za-z\.\-\s]{2,60})", s
    )
    seen: set[str] = set()
    out: list[str] = []
    for m in matches:
        m_clean = re.sub(r"\s+", " ", m).strip(" ,.:;")
        if m_clean not in seen:
            seen.add(m_clean)
            out.append(m_clean)
        if len(out) >= limit:
            break
    return out


def _structure_signals(s: str) -> dict:
    low = s.lower()
    return {
        "found_order_vii_rule_11": bool(re.search(r"Order\s+VII\s+Rule\s+11", s, re.I)),
        "found_cpc_reference": bool(re.search(r"\bC\.?\s*P\.?\s*C\.?\b|Code\s+of\s+Civil\s+Procedure", s, re.I)),
        "found_cause_title": bool(re.search(r"(?im)^(?:\s*#+\s*)?(IN\s+THE\s+COURT|BEFORE\s+THE)", s)),
        "found_prayer": "prayer" in low,
        "found_verification": "verification" in low,
        "found_affidavit": "affidavit" in low,
        "found_placeholders": bool(re.search(r"\[[A-Z ]{3,}\]", s)),
        "found_grounds_section": bool(re.search(r"(?im)^\s*(#+\s*)?grounds?\b", s)),
    }


async def main() -> int:
    print(f"BASE: {BASE}")
    print(f"PROMPT ({len(PROMPT)} chars): {PROMPT}")

    t0 = time.time()
    headers = {"X-API-Key": KEY, "Accept": "text/event-stream"}
    # /pyapi/chat is a multipart endpoint (query: str = Form(...)) — no JSON.
    form_data = {"query": PROMPT}

    answer = ""
    events_seen: dict[str, int] = {}
    agents_planned: list[str] = []
    progress_steps: list[str] = []
    errors: list[str] = []
    done_event: dict | None = None
    ttfb_ms: int | None = None
    raw_lines = 0
    thread_id = None

    with OUT_RAW.open("w", encoding="utf-8") as raw_fh:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST", f"{BASE}/chat",
                headers=headers, data=form_data, timeout=1500.0,
            ) as resp:
                if ttfb_ms is None:
                    ttfb_ms = round((time.time() - t0) * 1000)
                if resp.status_code != 200:
                    err = await resp.aread()
                    msg = err.decode(errors="replace")[:1000]
                    print(f"HTTP {resp.status_code}: {msg}")
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
                    elif t == "done":
                        done_event = e
                        for k in ("final_response", "response", "final_answer"):
                            v = e.get(k)
                            if isinstance(v, str) and len(v) > len(answer):
                                answer = v

    elapsed = round(time.time() - t0, 2)
    OUT_MD.write_text(answer or "(empty)", encoding="utf-8")

    meta = {
        "run_label": LABEL,
        "elapsed_s": elapsed,
        "ttfb_ms": ttfb_ms,
        "http_ok": True,
        "raw_events_written": raw_lines,
        "thread_id": thread_id,
        "answer_len": len(answer),
        "answer_words": len(answer.split()),
        "answer_case_citations_count_regex": _count_cases(answer),
        "answer_case_citations_sample": _list_case_names(answer),
        "structure_signals": _structure_signals(answer),
        "agents_planned": agents_planned,
        "event_type_counts": events_seen,
        "progress_step_count": len(progress_steps),
        "unique_progress_steps": sorted(set(progress_steps)),
        "errors": errors,
        "done_event_keys": list((done_event or {}).keys()),
    }
    OUT_META.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    with OUT_TXT.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(meta, indent=2, ensure_ascii=False))
        fh.write("\n\n=== ANSWER (first 4000 chars) ===\n")
        fh.write(answer[:4000])
        fh.write("\n\n=== ANSWER TAIL (last 2000 chars) ===\n")
        fh.write(answer[-2000:])

    print()
    print(f"elapsed: {elapsed}s  answer_len: {len(answer):,}  agents: {agents_planned}")
    print(f"structure_signals: {meta['structure_signals']}")
    print(f"event_type_counts: {events_seen}")
    if errors:
        print(f"errors ({len(errors)}): {errors[0][:300]}")
    print()
    print(f"Response saved: {OUT_MD}")
    print(f"Meta saved:     {OUT_META}")
    print(f"Run summary:    {OUT_TXT}")
    print(f"Raw SSE saved:  {OUT_RAW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
