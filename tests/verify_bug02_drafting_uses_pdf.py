"""BUG-02 verification: drafting agent uses PDF facts (not template placeholders).

Sends an explicit drafting prompt + plaint.pdf to LOCAL /pyapi/chat. Streams the
full SSE response to the final answer, then asserts:

  PASS criteria:
    1. The draft contains the REAL plaintiff name 'Arun' (or 'Deshmukh').
    2. The draft contains the REAL defendant name 'Kunal' (or 'Patil').
    3. The draft contains the REAL loan amount '10,00,000' or '10 lakh' / '10 lakhs'.
    4. The draft contains a real date — '15-Apr-2023' or '15 April 2023' or '15.04.2023' or '2023'.
    5. The draft does NOT contain the placeholder strings the buggy version used:
       '[Plaintiff Name]', '[Loan Amount]', '[Date]', '[Defendant Name]'.
    6. The draft does NOT copy template specifics from unrelated cases
       (e.g. 'Maharashtra Rent Control Act', 'Specific Relief Act Section 6 trespass').

Run:  python tests/verify_bug02_drafting_uses_pdf.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

import os
BASE = os.environ.get("LAWTECH_TEST_BASE", "http://localhost:5000/pyapi")
API_KEY = os.environ.get("LAWTECH_TEST_API_KEY", "")
PDF_PATH = Path(r"D:\agentic_proj\Lawtech-AI\test_pdfs\plaint.pdf")

# Single drafting prompt. We use the exact P6 from the original eval — it was
# the only PASS in the bad run, but even there a deeper look showed it copied
# generic template language. Now we want REAL facts woven through.
PROMPT = (
    "Draft a written statement on behalf of the defendant Kunal Rajendra Patil "
    "denying the friendly loan alleged in the plaint."
)

# Facts that MUST appear (case-insensitive, regex)
MUST_CONTAIN = [
    ("plaintiff name (Arun/Deshmukh)",          r"\b(arun|deshmukh)\b"),
    ("defendant name (Kunal/Patil)",            r"\b(kunal|patil)\b"),
    ("loan amount Rs 10,00,000 / 10 lakh",      r"(10[,\.]?00[,\.]?000|10\s*lakhs?|ten\s+lakhs?)"),
    ("year/date 2023",                          r"\b2023\b"),
]

# Buggy template-only placeholders we should NOT see for facts that ARE in the PDF
MUST_NOT_CONTAIN = [
    ("placeholder [Plaintiff Name]",            r"\[plaintiff\s+name\]"),
    ("placeholder [Defendant Name]",            r"\[defendant\s+name\]"),
    ("placeholder [Loan Amount]",               r"\[loan\s+amount\]"),
    ("placeholder [Date of Loan Disbursement]", r"\[date\s+of\s+loan"),
    ("placeholder [Plaintiff Address]",         r"\[plaintiff\s+address\]"),
    ("unrelated template: Rent Control Act",    r"maharashtra\s+rent\s+control"),
    ("unrelated template: Section 6 SRA",       r"specific\s+relief\s+act.{0,40}section\s+6.{0,80}trespass"),
    ("unrelated template: goods sold",          r"goods\s+sold\s+and\s+delivered"),
    ("unrelated template: summons to witness",  r"application\s+for\s+(issue\s+of\s+)?summons\s+to\s+witness"),
]


async def collect_full_answer(client: httpx.AsyncClient, query: str, pdf_bytes: bytes) -> str:
    final_answer = ""
    answer_chunks: list[str] = []
    async with client.stream(
        "POST", f"{BASE}/chat",
        headers={"X-API-Key": API_KEY},
        data={"query": query},
        files={"files": (PDF_PATH.name, pdf_bytes, "application/pdf")},
        timeout=600.0,
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                evt = json.loads(line[6:])
            except Exception:
                continue
            t = evt.get("type")
            if t == "token":
                tok = evt.get("data") or evt.get("content") or evt.get("token") or ""
                if isinstance(tok, str):
                    answer_chunks.append(tok)
            elif t in ("response", "final_answer", "answer"):
                txt = evt.get("content") or evt.get("data") or evt.get("text") or ""
                if isinstance(txt, str) and len(txt) > len(final_answer):
                    final_answer = txt
            elif t == "done":
                txt = evt.get("response") or evt.get("answer") or evt.get("data") or ""
                if isinstance(txt, str) and len(txt) > len(final_answer):
                    final_answer = txt
    if not final_answer and answer_chunks:
        final_answer = "".join(answer_chunks)
    return final_answer


async def main() -> int:
    pdf_bytes = PDF_PATH.read_bytes()
    print(f"Loaded plaint.pdf ({len(pdf_bytes)/1024:.1f} KB) -> {BASE}/chat\n")
    print(f"Prompt: {PROMPT!r}\n")

    async with httpx.AsyncClient() as client:
        try:
            answer = await collect_full_answer(client, PROMPT, pdf_bytes)
        except Exception as e:
            print(f"[ERR] request failed: {e!r}")
            return 1

    print(f"Got answer: {len(answer)} chars\n")
    out_path = Path(__file__).parent / "_bug02_answer.txt"
    out_path.write_text(answer, encoding="utf-8")
    print(f"Saved full answer -> {out_path}\n")

    if not answer.strip():
        print("[FAIL] empty answer")
        return 1

    text_lower = answer.lower()
    failures: list[str] = []

    print("MUST CONTAIN (real facts from PDF):")
    for label, pattern in MUST_CONTAIN:
        ok = bool(re.search(pattern, text_lower, re.IGNORECASE | re.DOTALL))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}  /{pattern}/")
        if not ok:
            failures.append(f"missing: {label}")

    print("\nMUST NOT CONTAIN (template placeholders / unrelated templates):")
    for label, pattern in MUST_NOT_CONTAIN:
        bad = bool(re.search(pattern, text_lower, re.IGNORECASE | re.DOTALL))
        mark = "FAIL" if bad else "PASS"
        print(f"  [{mark}] {label}  /{pattern}/")
        if bad:
            failures.append(f"present: {label}")

    print("\n" + "="*70)
    if failures:
        print(f"FAILED ({len(failures)} issues): {failures}")
        return 1
    print("BUG-02 verification: ALL checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
