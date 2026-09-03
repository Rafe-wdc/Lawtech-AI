"""Reproduce the two client-reported drafting complaints:
   (a) repeated draft generation — same content appearing multiple times
   (b) inappropriate responses — wrong content, invented facts, missing structure

Runs both prompts against a target server, dumps the full SSE stream +
final response to markdown, and scores each for:
  - repetition (multiple `response` events, duplicate paragraphs, duplicate headings)
  - fact preservation (does the draft use the actual case facts supplied?)
  - structural completeness (cause title, parties, prayer, verification)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_URL = "http://127.0.0.1:5000/pyapi/search/stream"
DEFAULT_KEY = "ff6c3e959de2bf4f73901db1ff797ea484d326ac6e2622067493352435f23a51"

PROMPTS = [
    {
        "id": "medical_negligence",
        "text": (
            "Draft a civil suit for damages arising from medical negligence, "
            "incorporating detailed legal reasoning and relevant case laws. "
            "Scenario: My client, Mrs. Anjali Deshmukh, aged 42, and a resident "
            "of Pune, underwent gallbladder surgery at XYZ Multispecialty "
            "Hospital on 12th March 2024. During the procedure, the attending "
            "surgeon, Dr. Rakesh Nair, negligently caused damage to her bile "
            "duct. Following the surgery, she experienced severe abdominal pain "
            "and had to be admitted to another hospital for corrective surgery. "
            "As a result, she incurred additional medical expenses amounting to "
            "₹3.5 lakhs and endured significant physical pain and mental "
            "agony. Despite repeated follow-ups, both the hospital and the "
            "doctor have refused to acknowledge their negligence or offer any "
            "compensation. Mrs. Deshmukh now seeks damages of ₹25 lakhs, "
            "covering medical expenses, mental harassment, pain and suffering, "
            "and anticipated costs of future treatment."
        ),
        # Facts that MUST appear in a correct draft (case-insensitive substring).
        "required_facts": [
            "Anjali Deshmukh",
            "42",
            "Pune",
            "XYZ Multispecialty Hospital",
            "12th March 2024",
            "Rakesh Nair",
            "bile duct",
            "gallbladder",
        ],
        "required_amounts": ["3.5", "25"],  # lakhs figures
        "structural_markers": [
            "plaintiff",       # cause title / parties
            "defendant",
            "prayer",          # or "wherefore" — checked separately
            "verification",    # or "verify"
        ],
    },
    {
        "id": "partition_injunction",
        "text": (
            "Draft a civil suit for partition along with a prayer for temporary "
            "injunction. Facts: Rupa is an adopted daughter of the deceased "
            "owner. She has no siblings. Her father died 5 years ago, leaving "
            "behind two flats in Pune. The only legal heirs are Rupa and her "
            "mother. Her name is not yet on the property documents. Her mother "
            "is attempting to dispossess her and has expressed intent to "
            "transfer the properties to her cousin. The suit should include "
            "legal provisions for partition of the properties, declaration of "
            "her share, and a temporary injunction restraining the mother or "
            "any third party from selling, transferring, or creating "
            "third-party rights in the properties until disposal of the suit."
        ),
        "required_facts": [
            "Rupa",
            "adopted",
            "Pune",
            "two flats",
            "mother",
            "cousin",
        ],
        "required_amounts": [],
        "structural_markers": [
            "plaintiff",
            "defendant",
            "partition",
            "injunction",
            "prayer",
            "verification",
        ],
    },
]


def call_stream(url: str, api_key: str, prompt: str) -> dict:
    """POST + consume SSE. Return everything the client observes."""
    thread_id = str(uuid.uuid4())
    payload = {"Promptquery": prompt, "globalThreadId": thread_id}
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    events: list[dict] = []
    token_buf: list[str] = []
    response_events: list[str] = []   # every terminal `response` event
    final_response: str | None = None
    agents_used: list[str] = []
    sources_count = 0
    token_resets = 0
    errors: list[str] = []

    t0 = time.time()
    with requests.post(url, json=payload, headers=headers,
                       stream=True, timeout=600) as r:
        r.raise_for_status()
        for raw in r.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            try:
                ev = json.loads(raw[5:].strip())
            except json.JSONDecodeError:
                continue
            events.append(ev)
            t = ev.get("type")
            if t == "token":
                chunk = ev.get("content") or ev.get("data") or ""
                if chunk:
                    token_buf.append(chunk)
            elif t == "token_reset":
                token_resets += 1
                token_buf.clear()
            elif t == "response":
                text = ev.get("content") or ev.get("data") or ""
                response_events.append(text)
                final_response = text
            elif t == "agents_planned":
                agents_used = ev.get("agents") or []
            elif t == "sources":
                sources_count = len(ev.get("data") or [])
            elif t == "error":
                errors.append(str(ev.get("data")))
            elif t == "done":
                break
    elapsed = time.time() - t0

    return {
        "thread_id": thread_id,
        "elapsed_s": round(elapsed, 2),
        "events_count": len(events),
        "events": events,
        "response_event_count": len(response_events),
        "final_response": final_response or "".join(token_buf) or "",
        "agents_used": agents_used,
        "sources_count": sources_count,
        "token_resets": token_resets,
        "errors": errors,
    }


def _normalize_line(s: str) -> str:
    """Aggressive normalisation for duplicate detection."""
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[^\w\s]", "", s)  # strip punctuation
    return s.lower()


def detect_repetition(text: str) -> dict:
    """Find repeated content inside a single response.

    Client complaint (a) — "repeated draft generated" — could mean:
      * multiple `response` events (measured elsewhere)
      * the SAME paragraph appearing twice in one response
      * the SAME heading appearing twice
      * a whole section duplicated
    """
    # 1. Duplicate paragraphs (paragraphs of >=200 chars, ignore trivial ones)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    normed = [_normalize_line(p) for p in paragraphs if len(p) >= 200]
    para_counts = Counter(normed)
    dup_paragraphs = [(cnt, p[:120]) for p, cnt in para_counts.items() if cnt > 1]

    # 2. Duplicate headings (## or ### lines)
    headings = re.findall(r"^\s*#{1,6}\s+(.+)$", text, re.MULTILINE)
    heading_counts = Counter(h.strip().lower() for h in headings)
    dup_headings = [(cnt, h) for h, cnt in heading_counts.items() if cnt > 1]

    # 3. Repeated substrings — look for a long span (>=300 chars) that
    # appears more than once verbatim
    long_dup_spans = []
    if len(text) > 1000:
        # sliding window hash approach: check 300-char windows every 100 chars
        seen: dict[str, int] = {}
        for i in range(0, len(text) - 300, 100):
            w = text[i:i + 300]
            h = hashlib.md5(w.encode("utf-8", errors="replace")).hexdigest()
            if h in seen:
                long_dup_spans.append(w[:80])
            else:
                seen[h] = i

    # 4. Total draft length + heading count as sanity
    return {
        "paragraph_count": len(paragraphs),
        "heading_count": len(headings),
        "duplicate_paragraphs": dup_paragraphs,
        "duplicate_headings": dup_headings,
        "duplicate_long_spans_count": len(long_dup_spans),
        "duplicate_long_spans_sample": long_dup_spans[:3],
    }


def score_fact_preservation(text: str, required: list[str]) -> dict:
    text_lc = text.lower()
    found = [f for f in required if f.lower() in text_lc]
    missing = [f for f in required if f.lower() not in text_lc]
    return {
        "total": len(required),
        "found": found,
        "missing": missing,
        "pct": round(100.0 * len(found) / len(required), 1) if required else 100.0,
    }


def score_amounts(text: str, required: list[str]) -> dict:
    found = []
    missing = []
    for amt in required:
        # look for the number nearby "lakh" / "lakhs" / "₹" / "rupees"
        pattern = rf"\b{re.escape(amt)}\b\s*(?:lakh|lakhs|lac|lacs|crore)"
        if re.search(pattern, text, re.IGNORECASE) or f"₹{amt}" in text or f"Rs. {amt}" in text or f"Rs {amt}" in text:
            found.append(amt)
        else:
            missing.append(amt)
    return {"found": found, "missing": missing}


def score_structure(text: str, markers: list[str]) -> dict:
    text_lc = text.lower()
    found = []
    missing = []
    for m in markers:
        if m == "prayer":
            # accept "prayer" OR "wherefore" OR "prayed"
            if "prayer" in text_lc or "wherefore" in text_lc:
                found.append(m); continue
        if m == "verification":
            if "verification" in text_lc or "verified" in text_lc or "solemnly affirm" in text_lc:
                found.append(m); continue
        if m in text_lc:
            found.append(m)
        else:
            missing.append(m)
    return {"found": found, "missing": missing,
            "pct": round(100.0 * len(found) / len(markers), 1) if markers else 100.0}


def write_report(target_label: str, results: list[dict]) -> Path:
    # Also dump raw responses next to the report so we can inspect the full text
    for r in results:
        raw = Path(f"_drafting_complaints_{target_label}_{r['prompt']['id']}.txt")
        raw.write_text(r["run"]["final_response"], encoding="utf-8")

    out = Path(f"_drafting_complaints_{target_label}.md")
    lines: list[str] = []
    lines.append(f"# Drafting complaint probe — target: {target_label}\n")
    for r in results:
        p = r["prompt"]
        run = r["run"]
        rep = r["repetition"]
        facts = r["facts"]
        amounts = r["amounts"]
        struct = r["structure"]
        badge = "OK" if rep["duplicate_paragraphs"] == [] and rep["duplicate_long_spans_count"] == 0 and facts["pct"] >= 80 and struct["pct"] >= 80 else "ISSUES"
        lines.append(f"## {p['id']} — `[{badge}]`\n")
        lines.append(f"- **Prompt**: `{p['text'][:150]}...`")
        lines.append(f"- **Thread**: `{run['thread_id']}`")
        lines.append(f"- **Elapsed**: {run['elapsed_s']}s | events: {run['events_count']} | sources: {run['sources_count']}")
        lines.append(f"- **Agents**: {run['agents_used']}")
        lines.append(f"- **Response events**: {run['response_event_count']} (expected 1)")
        lines.append(f"- **Token resets**: {run['token_resets']}")
        lines.append(f"- **Errors**: {run['errors']}")
        lines.append("")
        lines.append("### Repetition analysis")
        lines.append(f"- Paragraph count: {rep['paragraph_count']}")
        lines.append(f"- Heading count: {rep['heading_count']}")
        lines.append(f"- Duplicate paragraphs (>=200 chars appearing >1x): **{len(rep['duplicate_paragraphs'])}**")
        for cnt, preview in rep["duplicate_paragraphs"][:5]:
            lines.append(f"    - {cnt}x: `{preview}...`")
        lines.append(f"- Duplicate headings: **{len(rep['duplicate_headings'])}**")
        for cnt, h in rep["duplicate_headings"][:8]:
            lines.append(f"    - {cnt}x: `{h}`")
        lines.append(f"- Duplicate long spans (>=300 chars repeated verbatim): **{rep['duplicate_long_spans_count']}**")
        for s in rep["duplicate_long_spans_sample"]:
            lines.append(f"    - `{s}...`")
        lines.append("")
        lines.append("### Fact preservation")
        lines.append(f"- {facts['pct']}% — found: {facts['found']}, missing: {facts['missing']}")
        lines.append(f"- Amounts: found={amounts['found']}, missing={amounts['missing']}")
        lines.append("")
        lines.append("### Structural markers")
        lines.append(f"- {struct['pct']}% — found: {struct['found']}, missing: {struct['missing']}")
        lines.append("")
        lines.append("### Final response")
        lines.append("```markdown")
        lines.append(run["final_response"][:8000])
        if len(run["final_response"]) > 8000:
            lines.append(f"\n... (truncated, total {len(run['final_response'])} chars)")
        lines.append("```\n")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--key", default=DEFAULT_KEY)
    ap.add_argument("--label", default="local",
                    help="tag written into the output filename")
    args = ap.parse_args()

    results = []
    for p in PROMPTS:
        print(f"\n=== running {p['id']} against {args.url} ===")
        run = call_stream(args.url, args.key, p["text"])
        print(f"  elapsed {run['elapsed_s']}s, response events {run['response_event_count']}, agents {run['agents_used']}")

        rep = detect_repetition(run["final_response"])
        facts = score_fact_preservation(run["final_response"], p["required_facts"])
        amounts = score_amounts(run["final_response"], p["required_amounts"])
        struct = score_structure(run["final_response"], p["structural_markers"])

        print(f"  duplicate paragraphs: {len(rep['duplicate_paragraphs'])}")
        print(f"  duplicate headings:   {len(rep['duplicate_headings'])}")
        print(f"  duplicate long spans: {rep['duplicate_long_spans_count']}")
        print(f"  facts:      {facts['pct']}% (missing: {facts['missing']})")
        print(f"  amounts:    missing {amounts['missing']}")
        print(f"  structure:  {struct['pct']}% (missing: {struct['missing']})")

        results.append({
            "prompt": p, "run": run,
            "repetition": rep, "facts": facts,
            "amounts": amounts, "structure": struct,
        })

    out = write_report(args.label, results)
    print(f"\n[done] report -> {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
