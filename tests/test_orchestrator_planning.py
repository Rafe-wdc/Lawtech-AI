"""Regression tests for orchestrator planning.

Guards against re-introduction of the SCI keyword pre-check that
substring-matched "supreme court judgment" and hardcoded
`tasks_planned=["SCI_Judgment"]`, collapsing multi-task case-pack prompts
(e.g. "draft FIR + charge sheet + cite Supreme Court judgments") down to a
single agent. Removed 2026-07-26. See memory
`project_sci_precheck_collapses_multitask_2026_07_26.md`.

Two layers:

1. **Source invariant** (fast, no LLM) — asserts the removed symbols do not
   reappear in `agents/orchestrator.py`. This is the essential regression
   guard; if someone re-adds a pre-check tuple, CI catches it in <1s.

2. **End-to-end plan smoke** (slow, gated on env var) — actually runs
   `orchestrator_plan_node` against the client's 12-part fintech prompt and
   asserts the plan contains multiple agents. Requires OPENAI_API_KEY +
   GOOGLE_API_KEY + a reachable ES. Gate:

       ORCHESTRATOR_PLAN_E2E=1 pytest tests/test_orchestrator_planning.py::TestE2EFintechCasePack -v

Usage:
    pytest tests/test_orchestrator_planning.py -v
"""
from __future__ import annotations

import inspect
import os
import re
import sys

import pytest

sys.path.insert(0, ".")

from agents import orchestrator as orch_module


FINTECH_CASE_PACK_PROMPT = (
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


class TestSCIPreCheckStaysRemoved:
    """Source-level invariant. If any of these strings reappear in
    orchestrator.py, the SCI collapse bug is back.
    """

    def _orch_source(self) -> str:
        return inspect.getsource(orch_module)

    def test_no_sci_strong_keywords_tuple(self):
        assert "_SCI_STRONG_KEYWORDS" not in self._orch_source(), (
            "The SCI keyword pre-check tuple has been re-added. It collapses "
            "multi-task prompts like 'draft FIR + cite Supreme Court judgments' "
            "to a single SCI_Judgment agent. See memory "
            "project_sci_precheck_collapses_multitask_2026_07_26.md."
        )

    def test_no_is_sci_variable(self):
        # Match `is_sci =` or `is_sci=` as an assignment; ignore comments.
        source_no_comments = re.sub(r"#.*", "", self._orch_source())
        assert not re.search(r"\bis_sci\s*=", source_no_comments), (
            "The `is_sci` short-circuit variable has been re-added. See "
            "memory project_sci_precheck_collapses_multitask_2026_07_26.md."
        )

    def test_no_sibling_court_precheck(self):
        """Same failure mode would recur for HC/GST/Tax pre-checks."""
        source_no_comments = re.sub(r"#.*", "", self._orch_source())
        forbidden = ("_HC_STRONG_KEYWORDS", "_TAX_STRONG_KEYWORDS",
                     "_GST_STRONG_KEYWORDS", "_LEGISLATION_STRONG_KEYWORDS")
        offenders = [k for k in forbidden if k in source_no_comments]
        assert not offenders, (
            f"Court/act pre-check tuples found: {offenders}. Any keyword "
            f"substring-match that hardcodes tasks_planned=[<Agent>] before "
            f"the LLM planner runs will collapse multi-task prompts. Use "
            f"CLASSIFY_AND_PLAN_PROMPT rules instead."
        )


@pytest.mark.skipif(
    os.getenv("ORCHESTRATOR_PLAN_E2E") != "1",
    reason="E2E plan smoke requires ORCHESTRATOR_PLAN_E2E=1 (needs OPENAI/GOOGLE keys + ES)",
)
class TestE2EFintechCasePack:
    """Slow end-to-end smoke. Fires the client's 12-part prompt into the
    live planner and asserts the plan enumerates multiple agents.

    Not a fixed-output test (LLM temperature 0.1 has some noise) — asserts
    only the shape properties that the pre-check removal must guarantee:

    * plan has >= 2 agents (never collapses to one)
    * plan is not exactly ["SCI_Judgment"] (that is the bug's signature)
    * plan includes at least one of Drafting / Newacts / Legislation
      (any of these means the LLM planner saw the multi-task nature)
    """

    @pytest.mark.asyncio
    async def test_fintech_case_pack_plans_multiple_agents(self):
        from core.state import LegalAgentState  # local import so unit tests don't need it

        state: LegalAgentState = {  # type: ignore[assignment]
            "query": FINTECH_CASE_PACK_PROMPT,
            "original_query": FINTECH_CASE_PACK_PROMPT,
            "user_language": "en",
            "summary_text": "",
            "agent_results": {},
            "final_response": "",
            "is_blocked": False,
            "source_metadata": [],
            "tokens_consumed": 0,
        }

        result = await orch_module.orchestrator_plan_node(state)
        plan = result.get("tasks_planned") or [result.get("task")]

        assert plan != ["SCI_Judgment"], (
            f"Bug signature: 12-part fintech prompt collapsed to "
            f"['SCI_Judgment']. Pre-check may have been re-added. Plan: {plan}"
        )
        assert len(plan) >= 2, (
            f"Multi-task case-pack prompt planned only {len(plan)} agent(s): "
            f"{plan}. Expected >=2 agents."
        )
        expected_any = {"Drafting", "Newacts", "Legislation", "Legal_Concepts"}
        assert expected_any & set(plan), (
            f"Plan {plan} contains none of {expected_any}. LLM planner "
            f"did not decompose the 12-part prompt correctly."
        )
