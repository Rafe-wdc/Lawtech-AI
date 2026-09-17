"""Prod smoke: upload the BNSS Section 290 application DOCX and ask the model
to review + redraft, replicating the client's complaint scenario.

Target: https://api.lawttorney.com/pyapi/chat  (multipart + SSE).
Reference: modeled after _prasad_prod_runner.py (same auth + event drain).
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

DOCX_PATH = Path(r"D:\agentic_proj\Lawtech-AI\test_docx\APPLICATION UNDER SECTION 290 OF BNSS.docx")

PROMPT = (
    "Review the attached word document and Find out every legal error from the "
    "application and redraft with removing all legal error and with most "
    "relevant and landmark case laws of supreme court"
)

OUT_DIR = Path(__file__).parent
OUT_MD = OUT_DIR / "_bnss290_prod.md"
OUT_META = OUT_DIR / "_bnss290_prod_meta.json"
OUT_RAW = OUT_DIR / "_bnss290_prod_events.jsonl"
OUT_TXT = OUT_DIR / "_bnss290_prod_run.txt"


async def main() -> int:
    if not DOCX_PATH.exists():
        print(f"MISSING: {DOCX_PATH}")
        return 2
    body = DOCX_PATH.read_bytes()
    print(f"DOCX: {DOCX_PATH.name} ({len(body):,} bytes)")
    print(f"BASE: {BASE}")
    print(f"PROMPT ({len(PROMPT)} chars): {PROMPT}")

    t0 = time.time()
    headers = {"X-API-Key": KEY}
    # Prompt is English; do not force preferred_language so the model detects it.
    data = {"query": PROMPT}
    files = [
        ("files", (DOCX_PATH.name, body,
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
    ]

    answer = ""
    events_seen: dict[str, int] = {}
    agents_planned: list[str] = []
    progress_steps: list[str] = []
    file_processing_events: list[dict] = []
    errors: list[str] = []
    done_event: dict | None = None
    ttfb_ms: int | None = None
    raw_lines = 0
    thread_id = None

    with OUT_RAW.open("w", encoding="utf-8") as raw_fh:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST", f"{BASE}/chat",
                headers=headers, data=data, files=files, timeout=1500.0,
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
                    elif t == "file_processing":
                        file_processing_events.append(e)
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

    # Post-hoc heuristics to help diagnose the client's complaints
    def count_cases(s: str) -> int:
        # Rough "N v. M" / "N vs. M" citations
        return len(re.findall(r"[A-Z][A-Za-z\.\-\s]{2,40}\s+(?:v\.|vs\.?|Versus)\s+[A-Z][A-Za-z\.\-\s]{2,40}", s))

    def list_case_names(s: str, limit: int = 30) -> list[str]:
        matches = re.findall(
            r"([A-Z][A-Za-z\.\-\s]{2,60}\s+(?:v\.|vs\.?|Versus)\s+[A-Z][A-Za-z\.\-\s]{2,60})", s
        )
        # Dedup preserving order
        seen = set()
        out = []
        for m in matches:
            m_clean = re.sub(r"\s+", " ", m).strip(" ,.:;")
            if m_clean not in seen:
                seen.add(m_clean)
                out.append(m_clean)
            if len(out) >= limit:
                break
        return out

    def has_section_headers(s: str) -> dict:
        low = s.lower()
        return {
            "found_errors_section": bool(re.search(r"(?im)^\s*(#+\s*)?(legal\s+errors?|errors?\s+identified|issues?\s+identified)", s)),
            "found_redraft_section": bool(re.search(r"(?im)^\s*(#+\s*)?(redraft|revised\s+application|revised\s+draft|corrected\s+application)", s)),
            "found_case_laws_section": bool(re.search(r"(?im)^\s*(#+\s*)?(supreme\s+court\s+case\s+laws?|landmark\s+case|precedents?)", s)),
            "found_prayer": "prayer" in low,
            "found_shows_bnss_290": "290" in s,
            "found_shows_bnss_293": "293" in s,
            "found_mv_act_185": bool(re.search(r"section\s*185", s, re.I)),
            "found_alister_anthony": "alister anthony" in low,
        }

    meta = {
        "elapsed_s": elapsed,
        "ttfb_ms": ttfb_ms,
        "http_ok": True,
        "raw_events_written": raw_lines,
        "thread_id": thread_id,
        "answer_len": len(answer),
        "answer_words": len(answer.split()),
        "answer_case_citations_count_regex": count_cases(answer),
        "answer_case_citations_sample": list_case_names(answer, limit=40),
        "structure_signals": has_section_headers(answer),
        "agents_planned": agents_planned,
        "event_type_counts": events_seen,
        "progress_step_count": len(progress_steps),
        "unique_progress_steps": sorted(set(progress_steps)),
        "file_processing_summary": [
            {
                "message": e.get("message"),
                "files": e.get("files"),
                "stage": e.get("stage"),
                "rejected": e.get("rejected"),
            }
            for e in file_processing_events
        ],
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

    print(json.dumps(meta, indent=2, ensure_ascii=False))
    print()
    print(f"Response saved: {OUT_MD}")
    print(f"Meta saved:     {OUT_META}")
    print(f"Run summary:    {OUT_TXT}")
    print(f"Raw SSE saved:  {OUT_RAW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
