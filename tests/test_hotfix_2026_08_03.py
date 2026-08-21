"""Regression tests for the 2026-08-03 hotfix.

Covers two issues surfaced after the P0+P1+P2 deploy:
1. Fan-out judge biased to single-pass on fresh drafts because the P2
   G-18 addition ended with a "when in doubt, prefer single-pass" line
   that the LLM read as a general bias. Fresh-request bail-app prompts
   were coming back ~30% shorter than pre-audit.
2. `self_refine`'s per-iteration destructive-refinement guard misses
   the case where 2-3 individually-small shrinks cumulatively drop the
   response by >30%. Observed: iter1 -16%, iter2 -23%, cumulative -35%.

Run:
    pytest tests/test_hotfix_2026_08_03.py -v
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fan-out judge no longer biased to single-pass on fresh drafts
# ---------------------------------------------------------------------------

class TestFanoutJudgeNotBiasedOnFreshDraft:
    def test_prompt_no_longer_carries_generic_single_pass_bias(self):
        """The 'when in doubt, prefer single-pass' line was the culprit —
        it wasn't gated on follow-up detection, so the LLM read it as a
        general single-pass bias. Assert it's gone from the fan-out
        judge template."""
        with open("config/prompts.py", encoding="utf-8") as f:
            source = f.read()
        # Find the fan-out judge prompt block
        start = source.index("DRAFTING_FANOUT_JUDGE_PROMPT")
        end = source.find("DRAFTING_CHUNK_ROUTER_PROMPT", start)
        judge_prompt = source[start:end]
        # The problematic line must NOT appear at the top level of the
        # prompt (it may still appear inside the helper's prior-turn
        # branch, which only fires when a real prior AI turn exists).
        assert "When in doubt about whether it's a follow-up" not in judge_prompt, (
            "The generic 'prefer single-pass when in doubt' line must not "
            "live at the prompt-template level — that biased fresh drafts. "
            "It now lives only inside the prior-turn branch of "
            "_summarise_prior_ai_turn()."
        )

    def test_fresh_request_hint_has_no_single_pass_bias(self):
        """When _summarise_prior_ai_turn sees no prior turn, its output
        must NOT contain any single-pass bias language."""
        from agents.drafting import _summarise_prior_ai_turn
        for empty in (None, [], ()):
            hint = _summarise_prior_ai_turn(empty)
            lower = hint.lower()
            assert "single-pass" not in lower, (
                f"Fresh-request hint should not mention single-pass: {hint!r}"
            )
            assert "prefer single" not in lower
            assert "when in doubt" not in lower
            # Must positively signal that follow-up detection is off
            assert "does not apply" in lower or "does NOT apply" in hint

    def test_prior_turn_hint_still_carries_follow_up_rules(self):
        """When a prior AI turn exists, the follow-up rules and the
        'prefer single-pass when in doubt' hint MUST still be emitted
        (that's Bug #9's polish-prior-draft mitigation)."""
        from agents.drafting import _summarise_prior_ai_turn
        from langchain.messages import HumanMessage, AIMessage

        history = [
            HumanMessage(content="draft a plaint"),
            AIMessage(content="Here is your plaint...\n1. Facts\n2. Prayer"),
        ]
        hint = _summarise_prior_ai_turn(history)
        assert "PRIOR AI TURN" in hint
        assert "STAY SINGLE-PASS" in hint
        # The "when in doubt" bias applies to follow-ups specifically
        assert "when in doubt" in hint.lower() or "prefer single-pass" in hint.lower()


# ---------------------------------------------------------------------------
# self_refine cumulative-shrink guard
# ---------------------------------------------------------------------------

class TestSelfRefineCumulativeGuard:
    def test_cumulative_guard_present_in_source(self):
        """Static: the guard branch exists and references the ORIGINAL
        response length (not just per-iteration current)."""
        with open("core/self_refine.py", encoding="utf-8") as f:
            source = f.read()
        assert "original_response_len" in source
        assert "cumulative-shrink" in source or "cumulative_shrink" in source

    def test_cumulative_guard_returns_original_not_shrunk(self):
        """Behavioural: when a refinement chain cumulatively shrinks
        below 65% of the ORIGINAL length (even if per-iter each was
        under the 30% guard), revert to the ORIGINAL response, not the
        partially-shrunk mid-state."""
        from config.intent import default_intent
        from core.self_refine import self_refine, Critique, Violation

        # Original response: 10K chars. Refiner shrinks to 8K (-20%),
        # then 6K (-25%). Cumulative: -40%. Guard should trip on iter2
        # and return the ORIGINAL 10K response.
        original = "X" * 10_000
        first_refined = "Y" * 8_000    # -20% single step (passes per-iter guard)
        second_refined = "Z" * 6_000   # -25% single step vs `current`, but
                                       # -40% cumulative vs `original`

        # Critic always says fail with high confidence to keep the loop going
        fail_verdict = Critique(
            passes=False, confidence=0.95, violations=[
                Violation(field="x", severity="major",
                          issue="test", suggested_fix="test")
            ],
        )

        refined_outputs = iter([first_refined, second_refined])

        async def mock_critique(*_args, **_kwargs):
            return fail_verdict

        async def mock_refine(*_args, **_kwargs):
            return next(refined_outputs)

        # Confidence must be >=0.5 to pass the directives gate in
        # self_refine._intent_has_directives; default_intent() sets it to
        # 0.0 which skips the loop entirely.
        intent = default_intent()
        intent.confidence = 0.9
        intent.response_depth = "detailed"  # forces the loop to run

        with patch("core.self_refine._critique", side_effect=mock_critique), \
             patch("core.self_refine._refine", side_effect=mock_refine):
            result, history = asyncio.run(self_refine(
                original, "test query", intent,
                max_iterations=3, min_response_chars=100,
            ))

        # Cumulative guard should have fired on iter1 (10K → 6K = -40%
        # cumulative on the SECOND refinement). Result should be the
        # ORIGINAL 10K response, not the shrunk 6K or 8K mid-state.
        assert len(result) == 10_000, (
            f"cumulative guard should return the 10K original when "
            f"refinement chain drops below 65% of original length; got "
            f"{len(result)} chars"
        )
        # History records both critic calls
        assert len(history) >= 2

    def test_per_iteration_guard_still_fires_on_single_big_shrink(self):
        """Sanity: the older per-iteration guard (30% single-step drop)
        still fires. Prevents regression on Bug #1 fix behaviour."""
        from config.intent import default_intent
        from core.self_refine import self_refine, Critique, Violation

        original = "X" * 8_000
        # Single refinement drops to 4K (-50% in one step)
        big_shrink = "Y" * 4_000

        fail_verdict = Critique(
            passes=False, confidence=0.95, violations=[
                Violation(field="x", severity="major",
                          issue="test", suggested_fix="test")
            ],
        )

        async def mock_critique(*_args, **_kwargs):
            return fail_verdict

        async def mock_refine(*_args, **_kwargs):
            return big_shrink

        # Confidence must be >=0.5 to pass the directives gate in
        # self_refine._intent_has_directives; default_intent() sets it to
        # 0.0 which skips the loop entirely.
        intent = default_intent()
        intent.confidence = 0.9
        intent.response_depth = "detailed"

        with patch("core.self_refine._critique", side_effect=mock_critique), \
             patch("core.self_refine._refine", side_effect=mock_refine):
            result, _ = asyncio.run(self_refine(
                original, "test query", intent,
                max_iterations=2, min_response_chars=100,
            ))

        # Per-iteration guard fires: returns `current` (which is the
        # original since no refinement has been accepted yet).
        assert len(result) == 8_000, (
            f"per-iteration guard should keep the 8K original on a "
            f"50% single-step shrink; got {len(result)} chars"
        )

    def test_legitimate_growth_still_allowed(self):
        """Sanity: refinement that GROWS the draft (or stays roughly
        the same) is not blocked by either guard."""
        from config.intent import default_intent
        from core.self_refine import self_refine, Critique, Violation

        original = "X" * 6_000
        # Refiner adds ~10% content (fixes violations by adding text)
        grown = "Y" * 6_600

        # First critique fails, second passes.
        pass_verdict = Critique(passes=True, confidence=0.9)
        fail_verdict = Critique(
            passes=False, confidence=0.9, violations=[
                Violation(field="x", severity="major",
                          issue="test", suggested_fix="test")
            ],
        )
        critiques = iter([fail_verdict, pass_verdict])

        async def mock_critique(*_args, **_kwargs):
            return next(critiques)

        async def mock_refine(*_args, **_kwargs):
            return grown

        # Confidence must be >=0.5 to pass the directives gate in
        # self_refine._intent_has_directives; default_intent() sets it to
        # 0.0 which skips the loop entirely.
        intent = default_intent()
        intent.confidence = 0.9
        intent.response_depth = "detailed"

        with patch("core.self_refine._critique", side_effect=mock_critique), \
             patch("core.self_refine._refine", side_effect=mock_refine):
            result, _ = asyncio.run(self_refine(
                original, "test query", intent,
                max_iterations=2, min_response_chars=100,
            ))

        # Refinement should be kept (it grew, which is fine).
        assert len(result) == 6_600
