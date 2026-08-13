"""BUG-04 verification: Document agent should NOT hallucinate facts.

Sends two pure Q&A prompts (P3 + P4 from the original eval) that should route
to the Document agent only (post BUG-01 fix). For each answer:

  PASS criteria:
    1. The answer references the REAL plaintiff name 'Arun' / 'Deshmukh'
       OR defendant name 'Kunal' / 'Patil' (proves Doc agent read the file).
    2. The answer contains the REAL loan amount '10,00,000' / '10 lakh' /
       'Ten Lakhs'  (NOT the hallucinated 'Rs. 8,00,000' from the original bug).
    3. The answer contains a date in the file ('15-Apr-2023', 'April 2023', or
       just the year '2023')  — and does NOT contain the hallucinated
       '15.01.2020', 'January 2020', '30.06.2023' part-payment, or '25.04.2026'
       verification date that the original buggy run fabricated.
    4. The answer does NOT contain the hallucinated 'Rs. 8,00,000', 'part
       payment of Rs. 2,00,000', '25,00,000 sale agreement', 'Chennai District
       Court', 'Mr. A in Bangalore', 'sale deed by April 15 2023'.

Run:  python tests/verify_bug04_document_qa.py
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

PROMPTS: list[tuple[str, str]] = [
    ("P3_loan_amount",
     "What is the principal loan amount claimed and the rate of interest "
     "sought in this plaint?"),
    ("P4_limitation",
     "Is the suit filed within the limitation period under the Limitation "
     "Act, 1963 for recovery of money? Explain."),
]

# Pieces of fiction the buggy Document agent fabricated in the original run
HALLUCINATIONS = [
    ("Rs. 8,00,000",                r"\b8,00,000\b|\bRs\.?\s*8\s*lakhs?\b|eight\s+lakhs?"),
    ("Rs. 2,00,000 part payment",    r"\bpart[\s-]?payment of (Rs\.?\s*)?2,00,000|2\s*lakhs?\s+part"),
    ("15.01.2020",                   r"\b15[./-]01[./-]2020\b|January\s+15,\s*2020|15\s+Jan(?:uary)?\s+2020"),
    ("25.04.2026",                   r"\b25[./-]04[./-]2026\b|April\s+25,\s*2026"),
    ("repayable on demand",          r"repayable\s+on\s+demand"),
    ("Chennai District Court",       r"chennai\s+district\s+(judge|court)"),
    ("Mr. A / Mr. B (placeholder)",  r"\b(?:plaintiff|defendant)\s+mr\.?\s+[ab]\b"),
    ("Bangalore",                    r"\bbangalore\b"),
    ("Rs. 25,00,000",                r"\b25,00,000\b|\bRs\.?\s*25\s*lakhs?\b|twenty[-\s]?five\s+lakhs?"),
    ("sale deed by April 2023",      r"sale\s+deed.{0,40}(?:april|15.04|15-04)"),
]

# Per-prompt MUST_CONTAIN — different questions warrant different facts.
PROMPT_MUST_CONTAIN: dict[str, list[tuple[str, str]]] = {
    "P3_loan_amount": [
        ("Real loan amount (10,00,000 / Ten Lakhs)",
         r"(10[,\.]?00[,\.]?000|10\s*lakhs?|ten\s+lakhs?)"),
        ("A real date or year (2023 / 2024 / 2025)",
         r"\b(2023|2024|2025)\b"),
    ],
    "P4_limitation": [
        # Limitation analysis — must cite real dates/years from the plaint
        ("A real date or year (2023 / 2024 / 2025 / 2026)",
         r"\b(2023|2024|2025|2026)\b"),
        ("Mentions the 3-year limitation period",
         r"\b(?:three\s+years?|3[-\s]?years?)\b"),
    ],
}

# Per-prompt extra correctness checks
PROMPT_SPECIFIC_CHECKS: dict[str, list[tuple[str, str, bool]]] = {
    # (description, regex, expected_present)
    "P3_loan_amount": [
        ("interest rate 12%",
         r"\b12\s*%|\btwelve\s+per[\s-]?cent",     True),
    ],
    "P4_limitation": [
        ("conclusion: suit IS within limitation (not barred)",
         r"(within\s+(the\s+)?(prescribed\s+)?limitation|not\s+barred|"
         r"is\s+(filed\s+)?within|within\s+(the\s+)?period\s+of\s+limitation)",
         True),
        ("does NOT conclude suit is barred",
         r"(suit\s+is\s+barred|is\s+(time[-\s]?barred|barred\s+by\s+limitation)|"
         r"beyond\s+the\s+(prescribed\s+)?(3[-\s]?year|three[-\s]?year)\s+limitation\s+period)",
         False),
    ],
}


async def stream_chat(client: httpx.AsyncClient, query: str, pdf_bytes: bytes) -> tuple[str, list[str]]:
    """Returns (final_answer, agents_planned)."""
    final_answer = ""
    answer_chunks: list[str] = []
    agents: list[str] = []
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
            if t == "agents_planned":
                agents = evt.get("agents") or []
            elif t == "token":
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
    return final_answer, agents


async def main() -> int:
    pdf_bytes = PDF_PATH.read_bytes()
    print(f"Loaded plaint.pdf ({len(pdf_bytes)/1024:.1f} KB) -> {BASE}/chat\n")

    failures: list[str] = []
    out_dir = Path(__file__).parent

    async with httpx.AsyncClient() as client:
        for label, query in PROMPTS:
            print(f"\n=== {label} ===")
            print(f"prompt: {query!r}")
            try:
                answer, agents = await stream_chat(client, query, pdf_bytes)
            except Exception as e:
                print(f"[ERR] {e!r}")
                failures.append(label)
                continue

            print(f"agents_planned: {agents}")
            print(f"answer: {len(answer)} chars")

            (out_dir / f"_bug04_answer_{label}.txt").write_text(answer or "", encoding="utf-8")

            t = answer.lower()

            print("must contain:")
            for descr, pat in PROMPT_MUST_CONTAIN.get(label, []):
                ok = bool(re.search(pat, t, re.IGNORECASE | re.DOTALL))
                print(f"  [{'PASS' if ok else 'FAIL'}] {descr}")
                if not ok:
                    failures.append(f"{label}: missing {descr}")

            print("must NOT contain (hallucinations from original buggy run):")
            for descr, pat in HALLUCINATIONS:
                bad = bool(re.search(pat, t, re.IGNORECASE | re.DOTALL))
                print(f"  [{'FAIL' if bad else 'PASS'}] {descr}")
                if bad:
                    failures.append(f"{label}: hallucination present: {descr}")

            # Per-prompt correctness checks
            extra = PROMPT_SPECIFIC_CHECKS.get(label, [])
            if extra:
                print("prompt-specific:")
                for descr, pat, expect_present in extra:
                    found = bool(re.search(pat, t, re.IGNORECASE | re.DOTALL))
                    ok = (found == expect_present)
                    print(f"  [{'PASS' if ok else 'FAIL'}] {descr}")
                    if not ok:
                        failures.append(f"{label}: {descr}")

    print("\n" + "="*70)
    if failures:
        print(f"FAILED ({len(failures)}): {failures[:10]}{'...' if len(failures) > 10 else ''}")
        return 1
    print("BUG-04 verification: ALL checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
