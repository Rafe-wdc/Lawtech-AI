"""Local AFTER-FIX smoke: same DOCX + prompt as _bnss290_local_baseline.py,
run against the same localhost:5000 after Fix 1 (uploaded-DOCX-as-reference)
and Fix 2 (strip citation agents on review+redraft-with-upload) landed.

Compares its own meta to `_bnss290_local_baseline_meta.json` so a side-by-
side improvement/regression signal is emitted on stdout at the end.
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

BASE = "http://localhost:5000/pyapi"
import os
KEY = os.environ["LAWTECH_API_KEY"]

DOCX_PATH = Path(r"D:\agentic_proj\Lawtech-AI\test_docx\APPLICATION UNDER SECTION 290 OF BNSS.docx")

PROMPT = (
    "Review the attached word document and Find out every legal error from the "
    "application and redraft with removing all legal error and with most "
    "relevant and landmark case laws of supreme court"
)

OUT_DIR = Path(__file__).parent
LABEL = "local_afterfix"
OUT_MD = OUT_DIR / f"_bnss290_{LABEL}.md"
OUT_META = OUT_DIR / f"_bnss290_{LABEL}_meta.json"
OUT_RAW = OUT_DIR / f"_bnss290_{LABEL}_events.jsonl"
OUT_TXT = OUT_DIR / f"_bnss290_{LABEL}_run.txt"
BASELINE_META = OUT_DIR / "_bnss290_local_baseline_meta.json"


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
        "found_errors_section": bool(re.search(r"(?im)^\s*(#+\s*)?(legal\s+errors?|errors?\s+identified|issues?\s+identified)", s)),
        "found_redraft_section": bool(re.search(r"(?im)^\s*(#+\s*)?(redraft|revised\s+application|revised\s+draft|corrected\s+application)", s)),
        "found_case_laws_section": bool(re.search(r"(?im)^\s*(#+\s*)?(supreme\s+court\s+case\s+laws?|landmark\s+case|precedents?)", s)),
        "found_prayer": "prayer" in low,
        "found_shows_bnss_290": "290" in s,
        "found_shows_bnss_293": "293" in s,
        "found_mv_act_185": bool(re.search(r"section\s*185|sections?\s+184\s+and\s+185", s, re.I)),
        "found_alister_anthony": "alister anthony" in low,
    }


def _offtopic_signals(s: str) -> dict:
    return {
        "order_vii_rule_11_count": len(re.findall(r"Order\s+VII\s+Rule\s+11", s, re.I)),
        "indian_evangelical_count": s.count("Indian Evangelical"),
        "sri_bala_count": s.count("Sri Bala"),
        "dindigul_count": s.count("Dindigul"),
        "specific_performance_count": s.lower().count("specific performance"),
        "limitation_count": s.count("Limitation") + s.count("limitation"),
        "income_tax_count": s.count("Income Tax"),
        "cinema_count": s.lower().count("cinema"),
        "sci_citations_header_present": "SCI_JUDGMENT CITATIONS" in s,
        "agreement_name_placeholder_present": "[AGREEMENT NAME]" in s or "AGREEMENT NAME" in s.upper(),
        "money_recovery_plaint_shape_present": "PLAINT FOR RECOVERY OF MONEY" in s.upper(),
    }


def _compare_signals(after: dict, before: dict) -> dict:
    """Return per-signal diff: 'improved', 'regressed', 'unchanged'."""
    def cmp(k: str, higher_is_better: bool) -> str:
        b = before.get(k)
        a = after.get(k)
        if b == a:
            return "unchanged"
        if isinstance(b, bool) and isinstance(a, bool):
            if higher_is_better:
                return "improved" if a and not b else "regressed"
            return "improved" if not a and b else "regressed"
        if isinstance(b, (int, float)) and isinstance(a, (int, float)):
            if higher_is_better:
                return "improved" if a > b else "regressed"
            return "improved" if a < b else "regressed"
        return "changed"

    return {
        "found_shows_bnss_290": cmp("found_shows_bnss_290", True),
        "found_shows_bnss_293": cmp("found_shows_bnss_293", True),
        "found_mv_act_185": cmp("found_mv_act_185", True),
        "found_alister_anthony": cmp("found_alister_anthony", True),
        "found_prayer": cmp("found_prayer", True),
        "sci_citations_header_present": cmp("sci_citations_header_present", False),
        "order_vii_rule_11_count": cmp("order_vii_rule_11_count", False),
        "indian_evangelical_count": cmp("indian_evangelical_count", False),
        "specific_performance_count": cmp("specific_performance_count", False),
        "income_tax_count": cmp("income_tax_count", False),
        "cinema_count": cmp("cinema_count", False),
        "agreement_name_placeholder_present": cmp("agreement_name_placeholder_present", False),
        "money_recovery_plaint_shape_present": cmp("money_recovery_plaint_shape_present", False),
    }


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

    struct = _structure_signals(answer)
    offtopic = _offtopic_signals(answer)

    baseline = None
    if BASELINE_META.exists():
        try:
            baseline = json.loads(BASELINE_META.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"warning: could not read baseline meta: {e}")

    comparison = None
    if baseline:
        merged = dict(baseline.get("structure_signals", {}))
        merged.update(baseline.get("offtopic_signals", {}))
        after_merged = dict(struct)
        after_merged.update(offtopic)
        comparison = _compare_signals(after_merged, merged)

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
        "structure_signals": struct,
        "offtopic_signals": offtopic,
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
        "compared_to_baseline": comparison,
        "baseline_agents_planned": baseline.get("agents_planned") if baseline else None,
        "baseline_answer_len": baseline.get("answer_len") if baseline else None,
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
    print(f"structure_signals: {struct}")
    print(f"offtopic_signals: {offtopic}")
    print()
    if comparison:
        print("=== COMPARISON vs baseline ===")
        for k, v in comparison.items():
            arrow = {
                "improved": "PASS", "regressed": "FAIL",
                "unchanged": "same", "changed": "delta",
            }.get(v, v)
            print(f"  {arrow:>7}  {k}")
    print()
    print(f"Response saved: {OUT_MD}")
    print(f"Meta saved:     {OUT_META}")
    print(f"Run summary:    {OUT_TXT}")
    print(f"Raw SSE saved:  {OUT_RAW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
