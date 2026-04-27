"""BUG-01 verification: confirm orchestrator routing is fixed.

Sends prompts to LOCAL /pyapi/chat with plaint.pdf attached, then inspects
the `agents_planned` SSE event to confirm:
  * Pure Q&A prompts no longer route to Drafting.
  * Explicit drafting prompts still do.

Closes the SSE stream as soon as `agents_planned` arrives — we don't need
the agents to actually finish (and locally they would error on ES kwarg).

Run:  python tests/verify_bug01_routing.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

import os
BASE = os.environ.get("LAWTECH_TEST_BASE", "http://localhost:5000/pyapi")
API_KEY = os.environ.get("LAWTECH_TEST_API_KEY", "ff6c3e959de2bf4f73901db1ff797ea484d326ac6e2622067493352435f23a51")
PDF_PATH = Path(r"D:\agentic_proj\Lawtech-AI\test_pdfs\plaint.pdf")

CASES: list[tuple[str, str, bool]] = [
    # (label, prompt, drafting_should_be_planned)
    ("P1_summarize",          "Summarize the attached plaint in 8-10 lines, including parties, claim and reliefs.", False),
    ("P2_who_is_plaintiff",   "Who is the plaintiff, who is the defendant, and what is the cause of action in the attached document?", False),
    ("P3_loan_amount",        "What is the principal loan amount claimed and the rate of interest sought in this plaint?", False),
    ("P5_evidence",           "What documentary and oral evidence should the plaintiff produce to strengthen his case in this suit?", False),
    ("P6_draft_ws",           "Draft a written statement on behalf of the defendant Kunal Rajendra Patil denying the friendly loan alleged in the plaint.", True),
    ("P8_explain_o7r1",       "Explain Order VII Rule 1 of the Code of Civil Procedure, 1908, which is referenced in this plaint.", False),
    ("P10_defenses",          "What jurisdictional and limitation defences could the defendant raise against this suit, and how strong are they?", False),
]


async def get_agents_planned(client: httpx.AsyncClient, query: str, pdf_bytes: bytes) -> tuple[list[str] | None, str]:
    """Stream chat until agents_planned arrives, then close. Returns (agents, identified_str)."""
    agents: list[str] | None = None
    identified: str = ""
    async with client.stream(
        "POST", f"{BASE}/chat",
        headers={"X-API-Key": API_KEY},
        data={"query": query},
        files={"files": (PDF_PATH.name, pdf_bytes, "application/pdf")},
        timeout=60.0,
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
            if t == "progress" and evt.get("step") == "classify" and evt.get("substep"):
                identified = evt.get("message", "")
            if t == "agents_planned":
                agents = evt.get("agents", [])
                break
    return agents, identified


async def main() -> int:
    pdf_bytes = PDF_PATH.read_bytes()
    print(f"Loaded plaint.pdf ({len(pdf_bytes)/1024:.1f} KB) -> {BASE}/chat\n")

    failures: list[str] = []
    async with httpx.AsyncClient() as client:
        for label, query, expect_drafting in CASES:
            try:
                agents, identified = await get_agents_planned(client, query, pdf_bytes)
            except Exception as e:
                print(f"[ERR] {label}: {e!r}")
                failures.append(label)
                continue

            if agents is None:
                print(f"[ERR] {label}: never received agents_planned")
                failures.append(label)
                continue

            has_drafting = "Drafting" in agents
            ok = (has_drafting == expect_drafting)
            status = "[PASS]" if ok else "[FAIL]"
            print(f"{status} {label}")
            print(f"        query={query[:80]!r}")
            print(f"        identified={identified!r}")
            print(f"        agents={agents}  (expected drafting={expect_drafting}, got drafting={has_drafting})")
            if not ok:
                failures.append(label)

    print("\n" + "="*70)
    if failures:
        print(f"FAILED: {len(failures)}/{len(CASES)}  -> {failures}")
        return 1
    print(f"All {len(CASES)} routing checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
