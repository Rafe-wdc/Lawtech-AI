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
        # Use a non-strict directive (TABLE format) so the dynamic
        # iteration bump for strict_language doesn't kick in. The base
        # max_iterations=2 stays in effect.
        i = UserIntent(response_format=ResponseFormat.TABLE,
                       format_explicit=True, confidence=0.95)
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

    def test_strict_language_bumps_iterations_to_4(self):
        # The strict-language path overrides the caller's max_iterations
        # to 4, so the loop runs more critique passes for high-stakes
        # localization-sensitive output.
        i = UserIntent(strict_language=True, language="mr",
                       language_explicit=True, confidence=0.95)
        c_fail = Critique(
            passes=False, confidence=0.85,
            violations=[Violation(field="x", issue="y", severity="major",
                                  suggested_fix="z")],
        )
        # Refiner returns a fresh string each time so loop can continue.
        refine_outputs = [f"refine{n}" + "x" * 500 for n in range(1, 6)]
        with patch("core.self_refine._critique",
                   new=AsyncMock(return_value=c_fail)) as mock_c, \
             patch("core.self_refine._refine",
                   new=AsyncMock(side_effect=refine_outputs)) as mock_r:
            result, history = _run(self_refine(
                "orig" + "x" * 500, "q", i, max_iterations=2,
            ))
        # Effective max = 4 due to strict_language bump → 4 refines + 5
        # critiques (initial + after each refine).
        assert mock_c.await_count == 5
        assert mock_r.await_count == 4
        assert len(history) == 5
