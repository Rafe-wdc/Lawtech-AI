"""BUG-13 / BUG-14 / BUG-15 verification.

  BUG-13: drafting_progress events carry a stable `index` field (natural section
          position), so the frontend can render a fixed list regardless of the
          arbitrary order in which parallel section-generation completes.
  BUG-14: A fresh thread reports `Found 0 previous turns` (not 1) — the memory
          agent must skip the placeholder history when counting turns.
  BUG-15: Every legal-task answer ends with the legal disclaimer (consistent
          across Document / Drafting / Scenario / Judgment etc.).

Run:  python tests/verify_bug13_14_15.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

import os
BASE = os.environ.get("LAWTECH_TEST_BASE", "http://localhost:5000/pyapi")
API_KEY = os.environ["LAWTECH_TEST_API_KEY"]
PDF_PATH = Path(r"D:\agentic_proj\Lawtech-AI\test_pdfs\plaint.pdf")

DISCLAIMER_TEXT = "does not constitute legal advice"  # canonical fragment


async def stream_chat(client: httpx.AsyncClient, query: str, pdf_bytes: bytes | None) -> tuple[list[dict], str]:
    events: list[dict] = []
    final = ""
    chunks: list[str] = []
    files = {"files": (PDF_PATH.name, pdf_bytes, "application/pdf")} if pdf_bytes else None
    async with client.stream(
        "POST", f"{BASE}/chat",
        headers={"X-API-Key": API_KEY},
        data={"query": query},
        files=files,
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
            events.append(e)
            t = e.get("type")
            if t == "token":
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
    return events, final


def check_bug14(events: list[dict]) -> tuple[bool, str]:
    """Find the memory progress event reporting `previous turns` and assert 0."""
    for e in events:
        if (
            e.get("type") == "progress"
            and e.get("agent") == "memory"
            and e.get("step") == "history"
            and e.get("substep")
            and "previous turns" in (e.get("message") or "")
        ):
            n = e.get("found")
            return (n == 0), f"found={n}, message={e.get('message')!r}"
    return False, "no memory history progress event observed"


def check_bug13(events: list[dict]) -> tuple[bool, str]:
    """Drafting_progress events must carry stable `index` matching `section`-1."""
    drafting_events = [e for e in events if e.get("type") == "drafting_progress"]
    if not drafting_events:
        return True, "no drafting_progress events (non-drafting query)"
    bad: list[dict] = []
    for e in drafting_events:
        idx = e.get("index")
        sec = e.get("section")
        if idx is None or sec is None or idx + 1 != sec:
            bad.append({"section": sec, "index": idx, "status": e.get("status")})
    if bad:
        return False, f"misaligned events: {bad[:5]}"
    return True, f"all {len(drafting_events)} events carry stable index aligned to section-1"


def check_bug15(answer: str) -> tuple[bool, str]:
    has = DISCLAIMER_TEXT in answer.lower()
    return has, f"disclaimer={'present' if has else 'MISSING'}"


async def main() -> int:
    pdf_bytes = PDF_PATH.read_bytes()
    failures: list[str] = []
    async with httpx.AsyncClient() as client:
        # --- Test A: Document-only Q&A on a fresh thread ---
        print("=== Test A: Document Q&A on fresh thread ===")
        events, answer = await stream_chat(
            client,
            "What is the principal loan amount claimed and the rate of interest sought in this plaint?",
            pdf_bytes,
        )
        ok14, msg14 = check_bug14(events)
        ok15, msg15 = check_bug15(answer)
        ok13, msg13 = check_bug13(events)
        print(f"  BUG-14 (fresh thread shows 0 previous turns): [{'PASS' if ok14 else 'FAIL'}]  {msg14}")
        print(f"  BUG-15 (Document answer has disclaimer):       [{'PASS' if ok15 else 'FAIL'}]  {msg15}")
        print(f"  BUG-13 (drafting_progress alignment):          [{'PASS' if ok13 else 'FAIL'}]  {msg13}")
        if not ok14: failures.append("A: BUG-14")
        if not ok15: failures.append("A: BUG-15")
        if not ok13: failures.append("A: BUG-13")

        # --- Test B: Drafting on a fresh thread ---
        print("\n=== Test B: Drafting on fresh thread ===")
        events, answer = await stream_chat(
            client,
            "Draft a written statement on behalf of the defendant Kunal Rajendra Patil "
            "denying the friendly loan alleged in the plaint.",
            pdf_bytes,
        )
        ok14, msg14 = check_bug14(events)
        ok15, msg15 = check_bug15(answer)
        ok13, msg13 = check_bug13(events)
        print(f"  BUG-14 (fresh thread shows 0 previous turns): [{'PASS' if ok14 else 'FAIL'}]  {msg14}")
        print(f"  BUG-15 (Drafting answer has disclaimer):       [{'PASS' if ok15 else 'FAIL'}]  {msg15}")
        print(f"  BUG-13 (drafting_progress alignment):          [{'PASS' if ok13 else 'FAIL'}]  {msg13}")
        if not ok14: failures.append("B: BUG-14")
        if not ok15: failures.append("B: BUG-15")
        if not ok13: failures.append("B: BUG-13")

    print("\n" + "=" * 70)
    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("BUG-13 + BUG-14 + BUG-15 verification: ALL checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
