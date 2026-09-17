"""Standalone smoke: invoke orchestrator_plan_node directly on the client's
12-part fintech prompt and print what the planner now produces.

Purpose: prove that removing the SCI keyword pre-check (agents/orchestrator.py
diff +8/-17 on 2026-07-26) causes the planner to enumerate the multiple
agents this prompt actually needs, instead of collapsing to ["SCI_Judgment"].

Compare to the pre-fix prod capture in _verify_image_feedback_response.md
(agents_used=["SCI_Judgment"], source=[]).

Usage:
    python _verify_sci_fix_smoke.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

sys.path.insert(0, ".")

from agents.orchestrator import orchestrator_plan_node


FINTECH_PROMPT = (
    "A hacker gains unauthorized access to a fintech company's servers.\n\n"
    "He steals:\n\n"
    "Aadhaar records PAN data Bank account details Credit card information "
    "Source code\n\n"
    "The data is sold on the dark web.\n\n"
    "Prepare:\n\n"
    "Identify every applicable offence under BNS. Discuss provisions of the "
    "Information Technology Act. Explain investigation procedure. Digital "
    "evidence collection. Preservation of electronic evidence. Role of cyber "
    "forensic laboratory. Admissibility of electronic evidence under "
    "Bharatiya Sakshya Adhiniyam. Draft FIR. Draft seizure memo. Draft arrest "
    "memo. Draft charge sheet. Relevant Supreme Court judgments."
)


async def main() -> None:
    state = {
        "query": FINTECH_PROMPT,
        "original_query": FINTECH_PROMPT,
        "user_language": "en",
        "summary_text": "",
        "agent_results": {},
        "final_response": "",
        "is_blocked": False,
        "source_metadata": [],
        "tokens_consumed": 0,
    }

    print("=" * 72)
    print("FIXED PLANNER — 12-part fintech case-pack prompt")
    print("=" * 72)
    print(f"Prompt length: {len(FINTECH_PROMPT)} chars")
    print(f"Prompt ends with: ...{FINTECH_PROMPT[-60:]!r}")
    print()

    t0 = time.perf_counter()
    result = await orchestrator_plan_node(state)
    elapsed = time.perf_counter() - t0

    plan = result.get("tasks_planned") or [result.get("task")]
    task = result.get("task")
    intent = result.get("user_intent")

    print(f"Elapsed:            {elapsed:6.2f} s")
    print(f"Primary task:       {task}")
    print(f"tasks_planned:      {plan}")
    print(f"len(plan):          {len(plan)}")
    print(f"agent_queries keys: {list((result.get('agent_queries') or {}).keys())}")
    if intent:
        try:
            print(f"user_intent:        {intent.model_dump()}"
                  if hasattr(intent, "model_dump") else f"user_intent: {intent}")
        except Exception:
            pass
    print()

    print("--- Verdict ---")
    if plan == ["SCI_Judgment"]:
        print("FAIL: Still collapsed to ['SCI_Judgment']. Fix did NOT land.")
        sys.exit(1)
    elif len(plan) >= 2:
        print(f"PASS: Planner enumerated {len(plan)} agents "
              f"(vs. pre-fix baseline of 1). Multi-task decomposition working.")
    else:
        print(f"PARTIAL: Plan has {len(plan)} agent(s): {plan}. "
              f"Not the bug signature but not fully decomposed either.")


if __name__ == "__main__":
    asyncio.run(main())
