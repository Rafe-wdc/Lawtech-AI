"""BUG-06 + BUG-08 verification: P7 court-fee + P10 defenses no longer hallucinate."""
from __future__ import annotations

import asyncio, json, re, sys
from pathlib import Path
import httpx

import os
BASE = os.environ.get("LAWTECH_TEST_BASE", "http://localhost:5000/pyapi")
API_KEY = os.environ.get("LAWTECH_TEST_API_KEY", "ff6c3e959de2bf4f73901db1ff797ea484d326ac6e2622067493352435f23a51")
PDF_PATH = Path(r"D:\agentic_proj\Lawtech-AI\test_pdfs\plaint.pdf")

PROMPTS: list[tuple[str, str, list[tuple[str, str, bool]]]] = [
    (
        "P7_court_fee",
        "What court fee is payable on a money-recovery suit valued at Rs.10,00,000 under the Maharashtra Court Fees Act?",
        [
            # The original BUG-06: agent fabricated "Rs. 12,500 / Schedule I Article 1"
            # The plaint actually leaves the court fee BLANK ("₹___").
            ("does NOT fabricate Rs. 12,500 court fee from plaint",
             r"plaint\s+(?:states|says|mentions).{0,80}rs\.?\s*12,?500", False),
            ("does NOT claim 'Schedule I Article 1' is in the plaint",
             r"plaint\s+(?:states|says|mentions).{0,80}schedule\s+i\s*,?\s+article\s+1", False),
            # Either (a) acknowledges plaint left it blank, or (b) gives a
            # general legal answer about Maharashtra Court Fees Act calculation.
            ("answer addresses court fee for Rs.10L suit",
             r"(10[,\.]?00[,\.]?000|10\s*lakhs?|ten\s+lakhs?)", True),
            ("references Maharashtra Court Fees Act / Schedule",
             r"maharashtra\s+court[\s-]?fees?\s+act|bombay\s+court[\s-]?fees?\s+act", True),
        ],
    ),
    (
        "P10_defenses",
        "What jurisdictional and limitation defences could the defendant raise against this suit, and how strong are they?",
        [
            # Original BUG-08: agent invented Chennai/Bangalore/Rs.25L sale-deed case
            ("does NOT mention Chennai District Court (fictional)",
             r"chennai\s+district\s+(judge|court)", False),
            ("does NOT mention Bangalore (fictional)",
             r"\bbangalore\b", False),
            ("does NOT mention Rs. 25,00,000 (fictional)",
             r"\b25,00,000\b|twenty[-\s]?five\s+lakhs?", False),
            ("does NOT mention sale deed by April 2023 (fictional)",
             r"sale\s+deed.{0,40}(?:april|15.04|15-04)", False),
            ("does NOT mention 'Mr. A' / 'Mr. B' fictional party",
             r"\b(?:plaintiff|defendant)\s+mr\.?\s+[ab]\b", False),
            # Should reference real plaint's facts
            ("references real loan amount Rs. 10,00,000",
             r"(10[,\.]?00[,\.]?000|10\s*lakhs?|ten\s+lakhs?)", True),
            ("references real Pune court / Pune jurisdiction",
             r"\bpune\b", True),
            ("addresses limitation period",
             r"\blimitation\b.{0,200}\b(?:three\s+years?|3[-\s]?years?)\b|"
             r"\b(?:three\s+years?|3[-\s]?years?)\b.{0,200}\blimitation\b", True),
        ],
    ),
]


async def stream(client, query, pdf_bytes):
    final = ""
    chunks: list[str] = []
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
                e = json.loads(line[6:])
            except Exception:
                continue
            t = e.get("type")
            if t == "agents_planned":
                agents = e.get("agents") or []
            elif t == "token":
                tok = e.get("data") or e.get("content") or e.get("token") or ""
                if isinstance(tok, str):
                    chunks.append(tok)
            elif t in ("response", "final_answer", "answer"):
                txt = e.get("content") or e.get("data") or e.get("text") or ""
                if isinstance(txt, str) and len(txt) > len(final):
                    final = txt
            elif t == "done":
                txt = e.get("response") or e.get("answer") or e.get("data") or ""
                if isinstance(txt, str) and len(txt) > len(final):
                    final = txt
    if not final and chunks:
        final = "".join(chunks)
    return final, agents


async def main() -> int:
    pdf_bytes = PDF_PATH.read_bytes()
    failures: list[str] = []
    out_dir = Path(__file__).parent

    async with httpx.AsyncClient() as client:
        for label, query, checks in PROMPTS:
            print(f"\n=== {label} ===")
            print(f"prompt: {query!r}")
            try:
                ans, agents = await stream(client, query, pdf_bytes)
            except Exception as e:
                print(f"  [ERR] {e!r}")
                failures.append(label)
                continue
            print(f"agents_planned: {agents}")
            print(f"answer len: {len(ans)}")
            (out_dir / f"_bug6810_answer_{label}.txt").write_text(ans or "", encoding="utf-8")

            t = ans.lower()
            for descr, pat, expect in checks:
                found = bool(re.search(pat, t, re.IGNORECASE | re.DOTALL))
                ok = (found == expect)
                print(f"  [{'PASS' if ok else 'FAIL'}] {descr}")
                if not ok:
                    failures.append(f"{label}: {descr}")

    print("\n" + "="*70)
    if failures:
        print(f"FAILED ({len(failures)}): {failures}")
        return 1
    print("BUG-06 + BUG-08 verification: ALL checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
