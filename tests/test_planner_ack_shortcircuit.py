"""Acknowledgements ("thank you", "ok") take the greeting short-circuit in the
planner, so neither classification nor intent extraction runs for them
(cost audit 2026-10-08). Pure tests.
"""
from __future__ import annotations

import inspect

import agents.orchestrator as orch


def test_conversational_followup_takes_the_greeting_branch():
    src = inspect.getsource(orch.orchestrator_plan_node)
    assert 'if is_greeting or state.get("conversational_followup"):' in src
    # the short-circuit sits before the classify + intent gather
    assert src.index('if is_greeting or state.get("conversational_followup"):') < src.index("asyncio.gather(")


def test_followup_detector_still_flags_acknowledgements():
    from core.followup import is_conversational_followup
    for q in ("thank you", "thanks a lot", "ok", "great, thanks"):
        assert is_conversational_followup(q), q
    for q in ("explain section 138", "draft a notice", "what about the limitation period?"):
        assert not is_conversational_followup(q), q
