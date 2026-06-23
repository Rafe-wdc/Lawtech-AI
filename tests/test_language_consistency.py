"""Unit tests for language-consistency safeguards.

Covers the bug fix for "Marathi PDF + English query → mixed-language response"
and related failure modes:

  1. lang='en' with no source-language hint  → soft English directive emitted
  2. lang='en' + source_languages=('mr',)    → STRICT English directive
                                                naming Marathi as forbidden
  3. lang='hi' + source_languages=('mr',)    → cross-language warning so the
                                                model translates Marathi source
                                                into Hindi
  4. lang='mr' (strict intent)               → existing Marathi strict
                                                directive intact (regression)
  5. detect_source_languages()               → returns expected codes for
                                                Latin / Devanagari / Tamil text
  6. self_refine source-language mismatch    → forces critic run even when
                                                intent has no explicit
                                                directives

The tests do NOT hit any LLM — they verify the prompt-construction layer is
emitting the right directives. The actual model adherence is exercised by
the existing live smoke tests.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from core.language import (
    SUPPORTED_LANGUAGES,
    detect_source_languages,
    localize_prompt,
)
from core.self_refine import (
    Critique,
    _intent_has_directives,
    _source_language_mismatch,
    self_refine,
)
from config.intent import UserIntent, default_intent


BASE = "You are Lawttorney."


# ---------------------------------------------------------------------------
# localize_prompt — English target
# ---------------------------------------------------------------------------

class TestEnglishDirective:
    def test_default_intent_emits_soft_english_directive(self):
        """No source hint, no strict intent → soft directive present."""
        out = localize_prompt(BASE, "en")
        assert "LANGUAGE INSTRUCTION" in out
        assert "Respond entirely in English" in out
        # Soft variant — does NOT promise the strong "non-Latin script" rule
        assert "(STRICT)" not in out

    def test_marathi_source_triggers_strict_directive(self):
        """When source is Marathi but target is English → strong directive
        names Marathi and forbids Devanagari script in the response."""
        out = localize_prompt(BASE, "en", source_languages=("mr",))
        assert "LANGUAGE INSTRUCTION (STRICT)" in out
        assert "Marathi" in out
        assert "Devanagari" in out
        # The narrow case-name exception must survive so legal citations
        # don't get mangled.
        assert "case-name" in out.lower() or "case name" in out.lower()

    def test_multiple_non_english_sources_listed(self):
        """Tamil + Hindi sources both surface in the directive."""
        out = localize_prompt(BASE, "en", source_languages=("ta", "hi"))
        assert "Tamil" in out
        assert "Hindi" in out

    def test_strict_intent_for_english_triggers_strong_directive(self):
        """Explicit strict_language=True with English target also triggers
        the strong variant even without source hints."""
        intent = UserIntent(
            language="en", strict_language=True, language_explicit=True,
            confidence=0.9,
        )
        out = localize_prompt(BASE, "en", intent)
        assert "STRICT" in out
        assert "Respond entirely in English" in out

    def test_english_source_does_not_trigger_strict(self):
        """When source_languages contains only 'en', the soft directive
        is used — there's no cross-script risk to warn about."""
        out = localize_prompt(BASE, "en", source_languages=("en",))
        assert "LANGUAGE INSTRUCTION" in out
        assert "(STRICT)" not in out

    def test_empty_source_languages_default_to_soft(self):
        out = localize_prompt(BASE, "en", source_languages=())
        assert "(STRICT)" not in out
        assert "Respond entirely in English" in out


# ---------------------------------------------------------------------------
# localize_prompt — non-English target (regression + cross-language)
# ---------------------------------------------------------------------------

class TestNonEnglishDirective:
    def test_marathi_strict_directive_preserved(self):
        """Regression: the existing Marathi strict-language directive
        (ceremonial blocks + numerals) must still appear when target=mr."""
        intent = UserIntent(
            language="mr", language_explicit=True, strict_language=True,
            confidence=0.9,
        )
        out = localize_prompt(BASE, "mr", intent)
        # Marathi ceremonial-block markers from the existing directive
        assert "वादी" in out
        # Devanagari numerals must be mentioned
        assert "१" in out or "Devanagari" in out

    def test_hindi_with_marathi_source_appends_cross_warning(self):
        """When the user wants Hindi but the source is Marathi, the
        cross-language warning must surface so the model translates."""
        intent = UserIntent(
            language="hi", language_explicit=True, confidence=0.9,
        )
        out = localize_prompt(BASE, "hi", intent, source_languages=("mr",))
        # The new cross-language note explicitly names BOTH languages
        assert "Marathi" in out
        assert "Hindi" in out
        assert "SOURCE-LANGUAGE NOTE" in out

    def test_same_target_and_source_no_cross_warning(self):
        """Marathi target + Marathi source → no cross-language warning."""
        intent = UserIntent(
            language="mr", language_explicit=True, confidence=0.9,
        )
        out = localize_prompt(BASE, "mr", intent, source_languages=("mr",))
        assert "SOURCE-LANGUAGE NOTE" not in out

    def test_english_source_into_marathi_target_no_extra_note(self):
        """The cross-language warning targets foreign Indic sources;
        English source into Marathi target is the common citation case
        and shouldn't trigger the extra note."""
        intent = UserIntent(
            language="mr", language_explicit=True, confidence=0.9,
        )
        out = localize_prompt(BASE, "mr", intent, source_languages=("en",))
        assert "SOURCE-LANGUAGE NOTE" not in out


# ---------------------------------------------------------------------------
# detect_source_languages helper
# ---------------------------------------------------------------------------

class TestDetectSourceLanguages:
    def test_latin_text_returns_english(self):
        text = (
            "The Plaintiff is the lawful owner of the suit property bearing "
            "Survey No. 208 situated at Mauje Shivari, Tehsil Purandar, "
            "District Pune. The Defendants have wrongfully attempted to "
            "alienate the said property."
        )
        out = detect_source_languages(text)
        assert out == ("en",)

    def test_devanagari_returns_indic(self):
        # Marathi text from the bug-report PDF
        text = (
            "आमचे अशील श्री. कैलास अनंत जगताप यांची मौजे शिवरी, ता. पुरंदर, "
            "जि. पुणे येथील गट क्र. २०८ ही जमीन मिळकत आहे. सदर जमिनीवर मालकी "
            "हक्क, ताबा त्यांचा आहे."
        )
        out = detect_source_languages(text)
        # langdetect may classify Devanagari as 'hi' or 'mr' — both are
        # in our supported set and both trip the cross-script directive
        # downstream, which is the behaviour we want.
        assert len(out) == 1
        assert out[0] in {"mr", "hi"}

    def test_empty_and_none_inputs_ignored(self):
        assert detect_source_languages() == ()
        assert detect_source_languages(None, "", "  ") == ()

    def test_short_samples_skipped(self):
        # < 10 chars after strip — too short to detect reliably
        assert detect_source_languages("hi") == ()
        assert detect_source_languages("लोक") == ()

    def test_multiple_samples_deduplicated(self):
        en = "The Plaintiff filed a suit for partition of ancestral property."
        out = detect_source_languages(en, en, en)
        assert out == ("en",)

    def test_truncation_bounded(self):
        # Pass a sample longer than sample_chars; should still return ("en",)
        big = ("The Plaintiff filed a suit. " * 5000)
        out = detect_source_languages(big, sample_chars=200)
        assert out == ("en",)


# ---------------------------------------------------------------------------
# self_refine — source-language mismatch gating
# ---------------------------------------------------------------------------

class TestSelfRefineLanguageMismatch:
    def test_mismatch_helper_true_for_en_target_mr_source(self):
        intent = UserIntent(language="en", confidence=0.9)
        assert _source_language_mismatch(intent, ("mr",)) is True

    def test_mismatch_helper_false_when_aligned(self):
        intent = UserIntent(language="mr", language_explicit=True, confidence=0.9)
        assert _source_language_mismatch(intent, ("mr",)) is False

    def test_mismatch_helper_false_when_no_source(self):
        intent = UserIntent(language="en", confidence=0.9)
        assert _source_language_mismatch(intent, ()) is False

    def test_mismatch_helper_handles_none_intent(self):
        # None intent defaults to target=en; mr source should mismatch.
        assert _source_language_mismatch(None, ("mr",)) is True
        assert _source_language_mismatch(None, ("en",)) is False
        assert _source_language_mismatch(None, ()) is False

    def test_mismatch_helper_ignores_unsupported_codes(self):
        intent = UserIntent(language="en", confidence=0.9)
        # 'zz' is not in SUPPORTED_LANGUAGES; must not trip mismatch.
        assert _source_language_mismatch(intent, ("zz",)) is False

    def test_self_refine_force_runs_on_lang_mismatch_with_default_intent(self):
        """Default intent normally skips the critic. With source_languages
        signalling a cross-script case, the critic must run."""
        intent = default_intent()  # confidence=0.0 — would normally skip
        assert _intent_has_directives(intent) is False

        # Stub the critic to return passes=True so we don't actually call
        # the LLM. The key assertion is that the critic IS called.
        fake_critique = Critique(passes=True, confidence=0.9)

        with patch(
            "core.self_refine._critique", new=AsyncMock(return_value=fake_critique)
        ) as mock_crit:
            # 600-char response so we're over the min_response_chars=500 gate
            response = "X" * 600
            out, history = asyncio.run(self_refine(
                response,
                user_query="Summarize the attached notice.",
                intent=intent,
                source_languages=("mr",),
            ))

        assert out == response
        assert len(history) == 1
        mock_crit.assert_called_once()

    def test_self_refine_skips_when_no_mismatch_and_default_intent(self):
        """Sanity: existing skip-when-trivial behaviour preserved."""
        intent = default_intent()
        with patch(
            "core.self_refine._critique", new=AsyncMock()
        ) as mock_crit:
            out, history = asyncio.run(self_refine(
                "X" * 600,
                user_query="What is Section 138 NI Act?",
                intent=intent,
                source_languages=(),  # no source lang
            ))
        assert history == []
        mock_crit.assert_not_called()

    def test_self_refine_force_runs_when_intent_is_none(self):
        """None intent + language mismatch → critic still runs, using a
        synthesized default_intent under the hood."""
        fake_critique = Critique(passes=True, confidence=0.9)
        with patch(
            "core.self_refine._critique", new=AsyncMock(return_value=fake_critique)
        ) as mock_crit:
            response = "X" * 600
            out, history = asyncio.run(self_refine(
                response,
                user_query="What does this notice say?",
                intent=None,
                source_languages=("hi",),
            ))
        assert len(history) == 1
        mock_crit.assert_called_once()


# ---------------------------------------------------------------------------
# Smoke: end-to-end prompt contains all expected directives for the
# Marathi-PDF + English-query bug-report scenario.
# ---------------------------------------------------------------------------

class TestBugReportScenario:
    """The original bug: user typed an English question, uploaded a
    Marathi legal notice PDF, got a mixed-language response.

    The fixed `localize_prompt` must produce a prompt that (a) explicitly
    forbids Devanagari in the response and (b) names Marathi as the
    source-content language so the model has unambiguous context.
    """

    def test_marathi_pdf_english_query_prompt_is_strict(self):
        # Memory agent detects English from the query → user_language="en"
        # Document agent detects Marathi from retrieved chunks
        # → source_languages=("mr",)
        out = localize_prompt(
            BASE, "en", intent=None, source_languages=("mr",),
        )
        # Strong English directive present
        assert "(STRICT)" in out
        # Marathi source explicitly named
        assert "Marathi" in out
        # Devanagari script explicitly forbidden in the response
        assert "Devanagari" in out
        # Latin digits required
        assert "Latin digits" in out
        # Translation directive present
        assert "TRANSLATE" in out or "translate" in out


# Async tests above use asyncio.run() directly (matching tests/test_self_refine.py)
# to avoid taking a hard dep on pytest-asyncio, which isn't pinned in the project.
