"""Unit tests for core.self_refine — schemas, skip-when-trivial, loop control.

These tests stub the critic + refiner LLMs so the loop is exercised
deterministically. The live end-to-end check against Gemini is covered
by the smoke runs in tests/test_user_intent_extraction.py and the
ad-hoc Marathi WS rerun.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import pytest

from core.self_refine import (
    Critique,
    Violation,
    _intent_has_directives,
    _format_violations,
    self_refine,
)
from config.intent import (
    LegalArtifact,
    ResponseFormat,
    UserIntent,
    default_intent,
)


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------

class TestSchemas:
    def test_violation_required_fields(self):
        v = Violation(
            field="strict_language",
            issue="Latin digits used where Devanagari required.",
            severity="major",
            suggested_fix="Replace 1,2,3 with १,२,३ in numbered lists.",
        )
        assert v.field == "strict_language"
        assert v.severity == "major"

    def test_violation_rejects_unknown_severity(self):
        with pytest.raises(Exception):
            Violation(field="x", issue="y", severity="weird", suggested_fix="z")

    def test_critique_defaults(self):
        c = Critique(passes=True, confidence=0.9)
        assert c.violations == []
        assert c.overall_quality_notes == ""

    def test_critique_confidence_bounds(self):
        with pytest.raises(Exception):
            Critique(passes=True, confidence=1.5)
        with pytest.raises(Exception):
            Critique(passes=True, confidence=-0.1)

    def test_critique_quality_notes_capped(self):
        # Schema allows up to 400 chars (100-char slack vs the 300 target).
        ok = Critique(passes=True, confidence=0.9,
                      overall_quality_notes="x" * 400)
        assert len(ok.overall_quality_notes) == 400
        with pytest.raises(Exception):
            Critique(passes=True, confidence=0.9,
                     overall_quality_notes="x" * 401)


# ---------------------------------------------------------------------------
# _intent_has_directives — the skip-when-trivial gate
# ---------------------------------------------------------------------------

class TestSkipWhenTrivial:
    def test_none_skips(self):
        assert _intent_has_directives(None) is False

    def test_default_intent_skips(self):
        # default_intent() has confidence=0.0 — sub-threshold
        assert _intent_has_directives(default_intent()) is False

    def test_low_confidence_skips(self):
        i = UserIntent(strict_language=True, language="mr",
                       language_explicit=True, confidence=0.3)
        assert _intent_has_directives(i) is False

    def test_strict_language_triggers(self):
        i = UserIntent(strict_language=True, language="mr",
                       language_explicit=True, confidence=0.95)
        assert _intent_has_directives(i) is True

    def test_explicit_format_triggers(self):
        i = UserIntent(response_format=ResponseFormat.TABLE,
                       format_explicit=True, confidence=0.9)
        assert _intent_has_directives(i) is True

    def test_legal_artifact_triggers(self):
        i = UserIntent(legal_artifact=LegalArtifact.CROSS_EXAMINATION,
                       confidence=0.9)
        assert _intent_has_directives(i) is True

    def test_brief_depth_triggers(self):
        i = UserIntent(response_depth="brief", confidence=0.9)
        assert _intent_has_directives(i) is True

    def test_additional_instructions_triggers(self):
        i = UserIntent(additional_instructions="Cite only post-2020 cases.",
                       confidence=0.9)
        assert _intent_has_directives(i) is True

    def test_blank_additional_instructions_does_not_trigger(self):
        i = UserIntent(additional_instructions="   ", confidence=0.9)
        assert _intent_has_directives(i) is False


# ---------------------------------------------------------------------------
# CRITIQUE_PROMPT categories — presence pinning
# ---------------------------------------------------------------------------

class TestCritiqueCategories:
    """The critic prompt carries a fixed set of category descriptions the
    LLM uses to classify violations. When a new category is added, pin its
    key phrases so a future prompt-cleanup PR doesn't silently drop the
    trigger condition."""

    def test_under_detailed_draft_category_present(self):
        """Path-2 depth fix (2026-07-29) added `under_detailed_draft`.
        Only fires when `intent.response_depth == 'detailed'` AND the
        response is a legal draft."""
        from core.self_refine import CRITIQUE_PROMPT
        # Category name is the machine-readable field the LLM emits.
        assert "under_detailed_draft" in CRITIQUE_PROMPT
        # Trigger condition — only fires when depth=detailed.
        assert "response_depth" in CRITIQUE_PROMPT
        assert "detailed" in CRITIQUE_PROMPT
        # Concrete asks the tester's anticipatory-bail complaint drove:
        assert "15 numbered grounds" in CRITIQUE_PROMPT or "15+ numbered" in CRITIQUE_PROMPT
        assert "Sibbia" in CRITIQUE_PROMPT  # anticipatory bail anchor
        assert "custodial" in CRITIQUE_PROMPT.lower()
        assert "character" in CRITIQUE_PROMPT.lower() or "conduct" in CRITIQUE_PROMPT.lower()

    def test_prior_drafting_categories_preserved(self):
        """Regression: the new category must not have displaced the
        drafting-specific categories the pipeline already relied on."""
        from core.self_refine import CRITIQUE_PROMPT
        for cat in (
            "forbidden_statute_pair",
            "orphan_citation_tail",
            "canonical_example_substitution",
            "prayer_relief_mismatch",
            "prayer_generic_boilerplate",
        ):
            assert cat in CRITIQUE_PROMPT, f"category {cat!r} missing"


# ---------------------------------------------------------------------------
# _format_violations — refiner prompt rendering
# ---------------------------------------------------------------------------

class TestFormatViolations:
    def test_empty_list_returns_marker(self):
        assert "no violations" in _format_violations([]).lower()

    def test_single_violation_rendered(self):
        v = Violation(
            field="strict_language",
            issue="Latin digits.",
            severity="major",
            suggested_fix="Use Devanagari.",
        )
        out = _format_violations([v])
        assert "[MAJOR]" in out
        assert "strict_language" in out
        assert "Latin digits." in out
        assert "Use Devanagari." in out

    def test_multiple_violations_numbered(self):
        vs = [
            Violation(field="a", issue="x", severity="critical",
                      suggested_fix="fix x"),
            Violation(field="b", issue="y", severity="minor",
                      suggested_fix="fix y"),
        ]
        out = _format_violations(vs)
        assert "1." in out and "2." in out
        assert "[CRITICAL]" in out and "[MINOR]" in out


# ---------------------------------------------------------------------------
# Loop control — self_refine end-to-end with the critic + refiner stubbed
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine to completion synchronously.

    Avoids depending on the pytest-asyncio plugin (not in the project's
    pinned test deps) by wrapping each async assertion in a sync test.
    """
    return asyncio.run(coro)


class TestSelfRefineLoop:
    def test_skips_when_intent_none(self):
        result, history = _run(self_refine("some response", "q", None))
        assert result == "some response"
        assert history == []

    def test_skips_when_intent_trivial(self):
        result, history = _run(self_refine(
            "x" * 800, "q", default_intent(),
        ))
        assert result == "x" * 800
        assert history == []

    def test_skips_when_response_too_short(self):
        i = UserIntent(strict_language=True, language="mr",
                       language_explicit=True, confidence=0.95)
        result, history = _run(self_refine("short", "q", i,
                                           min_response_chars=500))
        assert result == "short"
        assert history == []

    def test_passes_first_try(self):
        i = UserIntent(strict_language=True, language="mr",
                       language_explicit=True, confidence=0.95)
        with patch("core.self_refine._critique",
                   new=AsyncMock(return_value=Critique(
                       passes=True, confidence=0.9))) as mock_c, \
             patch("core.self_refine._refine",
                   new=AsyncMock()) as mock_r:
            result, history = _run(self_refine("x" * 800, "q", i))
        assert result == "x" * 800
        assert len(history) == 1
        assert history[0].passes is True
        mock_r.assert_not_called()

    def test_refines_then_passes(self):
        i = UserIntent(strict_language=True, language="mr",
                       language_explicit=True, confidence=0.95)
        c_fail = Critique(
            passes=False, confidence=0.85,
            violations=[Violation(field="strict_language", issue="x",
                                  severity="major", suggested_fix="y")],
        )
        c_pass = Critique(passes=True, confidence=0.9)
        with patch("core.self_refine._critique",
                   new=AsyncMock(side_effect=[c_fail, c_pass])), \
             patch("core.self_refine._refine",
                   new=AsyncMock(return_value="refined")) as mock_r:
            result, history = _run(self_refine("orig" + "x" * 500, "q", i))
        assert result == "refined"
        assert len(history) == 2
        assert history[0].passes is False
        assert history[1].passes is True
        assert mock_r.await_count == 1

    def test_stops_on_low_confidence_critique(self):
        i = UserIntent(strict_language=True, language="mr",
                       language_explicit=True, confidence=0.95)
        c_unsure = Critique(
            passes=False, confidence=0.3,
            violations=[Violation(field="x", issue="y", severity="minor",
                                  suggested_fix="z")],
        )
        with patch("core.self_refine._critique",
                   new=AsyncMock(return_value=c_unsure)), \
             patch("core.self_refine._refine",
                   new=AsyncMock()) as mock_r:
            result, history = _run(self_refine("x" * 800, "q", i))
        assert result == "x" * 800
        assert len(history) == 1
        mock_r.assert_not_called()

    def test_max_iterations_exhausted(self):
        i = UserIntent(strict_language=True, language="mr",
                       language_explicit=True, confidence=0.95)
        c_fail = Critique(
            passes=False, confidence=0.85,
            violations=[Violation(field="x", issue="y", severity="major",
                                  suggested_fix="z")],
        )
        # Critic always fails. Refiner keeps returning a new string. After
        # max_iterations refinements we expect to stop.
        refine_outputs = ["refine1" + "x" * 500, "refine2" + "x" * 500]
        with patch("core.self_refine._critique",
                   new=AsyncMock(return_value=c_fail)) as mock_c, \
             patch("core.self_refine._refine",
                   new=AsyncMock(side_effect=refine_outputs + ["unused"])) as mock_r:
            result, history = _run(self_refine(
                "orig" + "x" * 500, "q", i, max_iterations=2,
            ))
        # 2 refines + 3 critiques (initial + after refine1 + after refine2)
        assert mock_c.await_count == 3
        assert mock_r.await_count == 2
        assert result == "refine2" + "x" * 500
        assert len(history) == 3
        assert history[-1].passes is False

    def test_destructive_refinement_is_rejected(self):
        """Guard added 2026-07-30 — observed on a Hindi anticipatory-bail
        draft where the critic flagged 14 violations against a well-formed
        7,643-char draft and the refiner rewrote it into 3,233 chars
        (-58%) trying to fix all of them. Refinements that drop the
        response length below 70% of the input are rejected; the loop
        stops with the pre-refine draft."""
        i = UserIntent(response_depth="detailed", confidence=0.9)
        c_fail = Critique(
            passes=False, confidence=0.9,
            violations=[Violation(field="under_detailed_draft",
                                  issue="too short",
                                  severity="major",
                                  suggested_fix="expand")],
        )
        # Refiner produces a MUCH shorter output — simulating the observed
        # destructive rewrite.
        original = "orig" + "x" * 7000  # 7004 chars
        catastrophically_short = "x" * 3000  # < 70% of 7004
        with patch("core.self_refine._critique",
                   new=AsyncMock(return_value=c_fail)) as mock_c, \
             patch("core.self_refine._refine",
                   new=AsyncMock(return_value=catastrophically_short)) as mock_r:
            result, history = _run(self_refine(
                original, "draft in detail", i, max_iterations=2,
            ))
        # Refiner ran once, but its output was rejected — we kept the
        # original 7004-char draft.
        assert result == original
        assert mock_r.await_count == 1
        # No re-critique on the rejected output — loop stopped.
        assert mock_c.await_count == 1

    def test_short_response_can_still_shrink_after_refine(self):
        """Guard applies ONLY to responses >2K chars — short responses
        (like a 500-char Q&A) can legitimately shrink after e.g. a table
        format fix. Regression: don't over-scope the guard."""
        i = UserIntent(strict_language=True, language="mr",
                       language_explicit=True, confidence=0.95)
        c_fail = Critique(
            passes=False, confidence=0.85,
            violations=[Violation(field="x", issue="y", severity="major",
                                  suggested_fix="z")],
        )
        c_pass = Critique(passes=True, confidence=0.9)
        # Original is 700 chars — below the 2K guard threshold. Refiner
        # shrinks it to 200 chars (>70% drop). Should be ACCEPTED.
        with patch("core.self_refine._critique",
                   new=AsyncMock(side_effect=[c_fail, c_pass])), \
             patch("core.self_refine._refine",
                   new=AsyncMock(return_value="y" * 200)):
            result, history = _run(self_refine(
                "x" * 700, "q", i, max_iterations=2,
            ))
        # Refinement was applied even though it shortened the response —
        # because the input was below the 2K guard threshold.
        assert result == "y" * 200
