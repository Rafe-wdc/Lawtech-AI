"""Prod smoke: client-reported "length not as per Writ Petition" bug.

Prompt: "Draft a writ petition challenging proceedings initiated beyond the
limitation prescribed under Section 149"

The client says the drafted Writ Petition is too short. This runner submits
the exact prompt against api.lawttorney.com/pyapi/chat, saves the answer +
SSE meta, and computes structure signals used to judge whether the output
resembles a full-blown Writ Petition (cause title, memo of parties, list of
dates, grounds, prayer, verification, affidavit, court fee) or a
compressed advisory-style note.
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
    "Draft a writ petition challenging proceedings initiated beyond the "
    "limitation prescribed under Section 149"
)

OUT_DIR = Path(__file__).parent
LABEL = "_writ_s149_prod"
OUT_MD = OUT_DIR / f"{LABEL}.md"
OUT_META = OUT_DIR / f"{LABEL}_meta.json"
OUT_RAW = OUT_DIR / f"{LABEL}_events.jsonl"
OUT_TXT = OUT_DIR / f"{LABEL}_run.txt"


WRIT_STRUCTURE_KEYS = [
    ("cause_title",
     r"IN THE HIGH COURT|IN THE SUPREME COURT|WRIT PETITION|W\.P\.\s*\(?[A-Z]?\)?"),
    ("memo_of_parties",
     r"MEMO OF PARTIES|MEMORANDUM OF PARTIES|petitioner[s]?\s*:?[\s\S]{0,80}?respondent"),
    ("versus_line", r"\bVERSUS\b|\bVs?\.\b"),
    ("article_226_or_32", r"Article\s*22[67]|Article\s*32"),
    ("humbly_showeth", r"humbly\s+showeth|most\s+respectfully\s+showeth"),
    ("facts_section",
     r"^\s*(#+\s*)?(FACTS|BRIEF\s+FACTS|STATEMENT\s+OF\s+FACTS)"),
    ("list_of_dates", r"LIST\s+OF\s+DATES(?:\s+AND\s+EVENTS)?"),
    ("grounds_section", r"^\s*(#+\s*)?GROUNDS?"),
    ("prayer_section", r"^\s*(#+\s*)?PRAYER"),
    ("interim_relief", r"INTERIM\s+RELIEF|INTERIM\s+PRAYER|Stay"),
    ("verification", r"VERIFICATION"),
    ("affidavit", r"AFFIDAVIT|SUPPORTING\s+AFFIDAVIT"),
    ("through_counsel",
     r"Through\s+Counsel|Advocate\s+for\s+the\s+Petitioner"),
    ("place_date", r"Place\s*:|Date\s*:"),
    ("court_fee",
     r"court\s*fee|Rs\.?\s*\d+/-\s*as\s*court\s*fee"),
    ("annexures", r"ANNEXURE(?:\s+[A-Z]-?\d?)?"),
    ("numbered_paras",
     r"^\s*\d+\.\s+That\b"),
]


def structure_signals(s: str) -> dict:
    signals = {}
    for name, pat in WRIT_STRUCTURE_KEYS:
        m = re.search(pat, s, re.IGNORECASE | re.MULTILINE)
        signals[name] = bool(m)
    numbered_paras = re.findall(r"^\s*(\d+)\.\s+", s, re.MULTILINE)
    signals["numbered_paragraph_count"] = len(numbered_paras)
    signals["max_para_number"] = (
        max((int(x) for x in numbered_paras), default=0)
    )
    return signals


def h2_headings(s: str) -> list[str]:
    return re.findall(r"^\s*##\s+(.+)$", s, re.MULTILINE)


def count_cases(s: str) -> int:
    return len(re.findall(
        r"[A-Z][A-Za-z\.\-\s]{2,40}\s+(?:v\.|vs\.?|Versus)\s+[A-Z][A-Za-z\.\-\s]{2,40}",
        s,
    ))


async def main() -> int:
    print(f"BASE: {BASE}")
    print(f"PROMPT ({len(PROMPT)} chars): {PROMPT}")

    t0 = time.time()
    headers = {"X-API-Key": KEY}
    data = {"query": PROMPT}

    answer = ""
    events_seen: dict[str, int] = {}
    agents_planned: list[str] = []
    tasks_planned: list[str] = []
    intent_dump: dict | None = None
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
                headers=headers, data=data, timeout=1500.0,
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
                    payload = line[6:]
                    try:
                        e = json.loads(payload)
                    except Exception:
                        continue
                    raw_fh.write(payload + "\n")
                    raw_lines += 1
                    t = e.get("type") or "<no-type>"
                    events_seen[t] = events_seen.get(t, 0) + 1
                    if t == "thread_id":
                        thread_id = e.get("data")
                    elif t == "token":
                        tok = (e.get("data") or e.get("content")
                               or e.get("token") or "")
                        if isinstance(tok, str):
                            answer += tok
                    elif t in ("response", "final_answer", "answer"):
                        txt = (e.get("content") or e.get("data")
                               or e.get("text") or "")
                        if isinstance(txt, str) and len(txt) > len(answer):
                            answer = txt
                    elif t == "agents_planned":
                        agents_planned = e.get("agents") or e.get("data") or []
                    elif t == "plan":
                        tasks_planned = e.get("tasks") or e.get("data") or []
                    elif t == "user_intent":
                        intent_dump = e
                    elif t == "progress":
                        step = e.get("step") or e.get("stage")
                        if step:
                            progress_steps.append(step)
                    elif t in ("error", "guardrail_error"):
                        errors.append(json.dumps(e, ensure_ascii=False)[:500])
                    elif t == "done":
                        done_event = e
                        for k in ("final_response", "response",
                                  "final_answer"):
                            v = e.get(k)
                            if isinstance(v, str) and len(v) > len(answer):
                                answer = v

    elapsed = round(time.time() - t0, 2)
    OUT_MD.write_text(answer or "(empty)", encoding="utf-8")

    sig = structure_signals(answer)
    hits = sum(1 for k in [
        "cause_title", "memo_of_parties", "versus_line", "article_226_or_32",
        "humbly_showeth", "facts_section", "list_of_dates", "grounds_section",
        "prayer_section", "verification", "affidavit", "through_counsel",
        "place_date", "numbered_paras",
    ] if sig.get(k))

    meta = {
        "run_label": LABEL,
        "elapsed_s": elapsed,
        "ttfb_ms": ttfb_ms,
        "http_ok": True,
        "raw_events_written": raw_lines,
        "thread_id": thread_id,
        "answer_len_chars": len(answer),
        "answer_len_words": len(answer.split()),
        "answer_case_citations_count_regex": count_cases(answer),
        "structure_signals": sig,
        "structure_key_hits_out_of_14": hits,
        "h2_headings": h2_headings(answer),
        "agents_planned": agents_planned,
        "tasks_planned": tasks_planned,
        "intent_dump_keys": list((intent_dump or {}).keys()),
        "event_type_counts": events_seen,
        "progress_step_count": len(progress_steps),
        "unique_progress_steps": sorted(set(progress_steps)),
        "errors": errors,
        "done_event_keys": list((done_event or {}).keys()),
    }
    OUT_META.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    with OUT_TXT.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(meta, indent=2, ensure_ascii=False))
        fh.write("\n\n=== ANSWER (first 6000 chars) ===\n")
        fh.write(answer[:6000])
        fh.write("\n\n=== ANSWER TAIL (last 3000 chars) ===\n")
        fh.write(answer[-3000:])

    print(json.dumps(meta, indent=2, ensure_ascii=False))
    print()
    print(f"Response saved: {OUT_MD}")
    print(f"Meta saved:     {OUT_META}")
    print(f"Run summary:    {OUT_TXT}")
    print(f"Raw SSE saved:  {OUT_RAW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
