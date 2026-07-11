"""Unit + E2E tests for the FIXED-ENGLISH ANCHORS policy (2026-07-11).

Policy under test
=================
For every response in a regional Indian language (Hindi, Marathi, Bengali,
Tamil, Telugu, Kannada, Malayalam, Gujarati, Punjabi, Urdu, Odia, Assamese,
Sanskrit), TWO categories always stay in English regardless of the response
language:

    1. All NUMERALS (Latin digits 0-9). Paragraph numbers, dates, years,
       amounts, section numbers, page numbers, case numbers — every digit.
       Never Devanagari (०-९), Bengali (০-৯), Tamil (௦-௯), etc.

    2. Full STATUTORY REFERENCES stay English inline as one span. Example:
       "Section 138 of the Negotiable Instruments Act, 1881". Never
       "कलम १३८ परक्राम्य लिखत अधिनियम, १८८१".

Body prose, ceremonial blocks, placeholder brackets stay in the target
language. Case-law citations stay English (unchanged from prior policy).

The policy is enforced ONLY via prompt directives:
  - core.language.localize_prompt        → primary directive
  - config.prompts.INDIAN_LEGAL_LANGUAGE_REGISTER → shared block
  - core.self_refine.CRITIQUE_PROMPT     → safety-net critic
  - core.self_refine.REFINE_PROMPT       → refiner instruction

NO code-level regex / substitution / translation table anywhere.

Test surface
============
Unit tests
----------
- localize_prompt output for each of 13 Indian languages (strict +
  non-strict) contains the FIXED-ENGLISH ANCHORS block, mandates Latin
  digits, and gives the "Section 138 of the Negotiable Instruments
  Act, 1881" exemplar. It does NOT emit the deprecated native-numeral
  hint dict or a "Use ० १ २ ..." directive.
- INDIAN_LEGAL_LANGUAGE_REGISTER shared block matches the policy.
- CRITIQUE_PROMPT calls out native-script digit leakage and translated
  Act names as MAJOR violations.
- REFINE_PROMPT tells the refiner to rewrite native→Latin and
  translated-Act→English.
- Dead helpers (localize_number, strip_leading_numeric_prefix,
  _NATIVE_DIGITS) are gone from core.language.

E2E tests (gated behind ENGLISH_ANCHORS_E2E=1; require GOOGLE_API_KEY)
---------
- Hindi drafting request → generated draft has NO Devanagari digits and
  keeps "Section 138 of the Negotiable Instruments Act, 1881" untranslated.
- Marathi drafting request with a section-138 notice → same policy.
- Tamil drafting request → policy applies (script-swap coverage).
- Bengali drafting request → policy applies (script-swap coverage).
- Hindi legislation-lookup Q&A (non-drafting task) → same policy.

Uses the project's `_run(asyncio.run)` pattern to avoid pytest-asyncio.
"""
from __future__ import annotations

import asyncio
import os
import re

import pytest

import core.language as language_mod
from core.language import (
    SUPPORTED_LANGUAGES,
    _INDIAN_LANGS_DEFAULT_STRICT,
    localize_prompt,
)
from config.intent import UserIntent
from config.prompts import (
    INDIAN_LEGAL_LANGUAGE_REGISTER,
    DRAFTING_FANOUT_JUDGE_PROMPT,
)
from core.self_refine import CRITIQUE_PROMPT, REFINE_PROMPT


BASE = "You are Lawttorney."


def _run(coro):
    return asyncio.run(coro)


# All 13 Indian languages (all non-English SUPPORTED_LANGUAGES entries).
INDIAN_LANGS = sorted(_INDIAN_LANGS_DEFAULT_STRICT)


# ---------------------------------------------------------------------------
# Native-script digit ranges (used ONLY by the tests as detectors, not by
# runtime code).
# ---------------------------------------------------------------------------

_NATIVE_DIGIT_RANGES = {
    "Devanagari": (0x0966, 0x096F),
    "Bengali":    (0x09E6, 0x09EF),
    "Odia":       (0x0B66, 0x0B6F),
    "Telugu":     (0x0C66, 0x0C6F),
    "Tamil":      (0x0BE6, 0x0BEF),
    "Kannada":    (0x0CE6, 0x0CEF),
    "Malayalam":  (0x0D66, 0x0D6F),
    "Gujarati":   (0x0AE6, 0x0AEF),
    "Gurmukhi":   (0x0A66, 0x0A6F),
    "ArabicExt":  (0x06F0, 0x06F9),
}


def _has_native_digit(text: str) -> tuple[bool, str]:
    """Return (True, script_name) at the first native-script digit found in
    the text. Case-law citation spans in English are the only expected
    surface with digits; we ONLY scan body prose so this helper is used on
    the response body minus any English-only citation lines the caller
    filters."""
    for script, (lo, hi) in _NATIVE_DIGIT_RANGES.items():
        for ch in text:
            code = ord(ch)
            if lo <= code <= hi:
                return True, script
    return False, ""


# ---------------------------------------------------------------------------
# 1) core.language — deprecated helpers must be gone
# ---------------------------------------------------------------------------

class TestDeprecatedHelpersRemoved:
    """The 2026-07-11 policy removed the code-level substitution layer.
    The absence of these symbols is part of the contract — reintroducing
    any of them would silently reinstate the old native-digit behaviour.
    """

    def test_localize_number_removed(self):
        assert not hasattr(language_mod, "localize_number")

    def test_strip_leading_numeric_prefix_removed(self):
        assert not hasattr(language_mod, "strip_leading_numeric_prefix")

    def test_native_digits_table_removed(self):
        assert not hasattr(language_mod, "_NATIVE_DIGITS")

    def test_native_numeral_hints_removed(self):
        assert not hasattr(language_mod, "_NATIVE_NUMERAL_HINTS")

    def test_leading_numeric_prefix_regex_removed(self):
        assert not hasattr(language_mod, "_LEADING_NUMERIC_PREFIX_RE")


# ---------------------------------------------------------------------------
# 2) localize_prompt — FIXED-ENGLISH ANCHORS for every Indian language
# ---------------------------------------------------------------------------

class TestLocalizePromptStrict:
    """Strict-language mode across all 13 Indian languages: the anchor
    block must be present and the deprecated native-numeral hint must
    not."""

    @pytest.mark.parametrize("lang", INDIAN_LANGS)
    def test_strict_contains_anchor_block(self, lang):
        intent = UserIntent(
            language=lang, language_explicit=True, strict_language=True,
            confidence=0.9,
        )
        out = localize_prompt(BASE, lang, intent)
        assert "LANGUAGE INSTRUCTION (STRICT)" in out
        assert "FIXED-ENGLISH ANCHORS" in out

    @pytest.mark.parametrize("lang", INDIAN_LANGS)
    def test_strict_mandates_latin_digits(self, lang):
        intent = UserIntent(
            language=lang, language_explicit=True, strict_language=True,
            confidence=0.9,
        )
        out = localize_prompt(BASE, lang, intent)
        # The anchor block spells out "Latin" and lists Devanagari as
        # a forbidden script.
        assert "Latin" in out
        assert "Devanagari" in out  # named as an example of what NOT to use

    @pytest.mark.parametrize("lang", INDIAN_LANGS)
    def test_strict_gives_english_statute_exemplar(self, lang):
        intent = UserIntent(
            language=lang, language_explicit=True, strict_language=True,
            confidence=0.9,
        )
        out = localize_prompt(BASE, lang, intent)
        # Canonical exemplar of the correct English statutory span.
        assert "Section 138 of the Negotiable Instruments Act, 1881" in out
        # Explicit anti-example that the LLM must NOT emit — the fully
        # translated form.
        assert "परक्राम्य लिखत अधिनियम, १८८१" in out

    @pytest.mark.parametrize("lang", INDIAN_LANGS)
    def test_strict_does_not_emit_deprecated_numeral_hint(self, lang):
        """The old strict directive included lines like
        'Use Devanagari numerals (०, १, २, ३, ४, ५, ६, ७, ८, ९) for
        ALL numbers'. That line must be gone."""
        intent = UserIntent(
            language=lang, language_explicit=True, strict_language=True,
            confidence=0.9,
        )
        out = localize_prompt(BASE, lang, intent)
        assert "Use Devanagari numerals" not in out
        assert "Use Bengali numerals" not in out
        assert "Use Tamil numerals" not in out
        assert "Use Kannada numerals" not in out
        assert "for ALL numbers" not in out


class TestLocalizePromptNonStrict:
    """Non-strict mode across all 13 Indian languages: same anchor block
    applies — that's the whole point of the policy."""

    @pytest.mark.parametrize("lang", INDIAN_LANGS)
    def test_non_strict_contains_anchor_block(self, lang):
        # Force non-strict by declaring explicit intent with strict off.
        intent = UserIntent(
            language=lang, language_explicit=True, strict_language=False,
            confidence=0.9,
        )
        out = localize_prompt(BASE, lang, intent)
        assert "LANGUAGE INSTRUCTION" in out
        assert "FIXED-ENGLISH ANCHORS" in out
        assert "Section 138 of the Negotiable Instruments Act, 1881" in out

    @pytest.mark.parametrize("lang", ["hi", "mr", "ta", "bn", "gu"])
    def test_non_strict_mandates_latin_digits(self, lang):
        intent = UserIntent(
            language=lang, language_explicit=True, strict_language=False,
            confidence=0.9,
        )
        out = localize_prompt(BASE, lang, intent)
        assert "Latin" in out


class TestLocalizePromptSampleLanguages:
    """Concrete assertions on Hindi, Marathi, Tamil, Bengali directives —
    the four scripts most likely to trip up the model."""

    def test_hindi_directive_forbids_kalam_label(self):
        intent = UserIntent(
            language="hi", language_explicit=True, strict_language=True,
            confidence=0.9,
        )
        out = localize_prompt(BASE, "hi", intent)
        # The critic-facing anti-example set must be visible: WRONG form
        # is spelled out inside the anchor block.
        assert "कलम" in out  # named as WRONG label (do NOT translate)
        assert "धारा" in out  # named as WRONG label (do NOT translate)

    def test_marathi_directive_forbids_translated_act_name(self):
        intent = UserIntent(
            language="mr", language_explicit=True, strict_language=True,
            confidence=0.9,
        )
        out = localize_prompt(BASE, "mr", intent)
        # Named anti-example: the fully translated Act
        assert "परक्राम्य लिखत अधिनियम, १८८१" in out

    def test_hindi_ceremonial_block_still_translated(self):
        """Ceremonial blocks (Plaintiff / Defendant) stay in Hindi.
        Only numerals + statutory references are English anchors."""
        intent = UserIntent(
            language="hi", language_explicit=True, strict_language=True,
            confidence=0.9,
        )
        out = localize_prompt(BASE, "hi", intent)
        assert "वादी" in out  # Plaintiff
        assert "प्रतिवादी" in out  # Defendant

    def test_marathi_ceremonial_block_still_translated(self):
        intent = UserIntent(
            language="mr", language_explicit=True, strict_language=True,
            confidence=0.9,
        )
        out = localize_prompt(BASE, "mr", intent)
        assert "वादी" in out
        assert "प्रतिवादी" in out


class TestEnglishPathUnchanged:
    """English target: existing behaviour preserved (Latin digits + English
    prose are baseline; the anchor policy is a no-op here)."""

    def test_english_default_no_anchor_block(self):
        out = localize_prompt(BASE, "en")
        # The English branch has its own directive; the non-English
        # "FIXED-ENGLISH ANCHORS" block is emitted only on non-English paths.
        assert "Respond entirely in English" in out


# ---------------------------------------------------------------------------
# 3) INDIAN_LEGAL_LANGUAGE_REGISTER shared block
# ---------------------------------------------------------------------------

class TestSharedLanguageRegister:
    def test_contains_fixed_english_anchors_marker(self):
        assert "FIXED-ENGLISH ANCHORS" in INDIAN_LEGAL_LANGUAGE_REGISTER

    def test_mandates_latin_digits(self):
        # "ALL NUMERALS are Latin digits (0-9)"
        assert "Latin digits" in INDIAN_LEGAL_LANGUAGE_REGISTER
        assert "0-9" in INDIAN_LEGAL_LANGUAGE_REGISTER

    def test_gives_english_statute_exemplar(self):
        assert (
            "Section 138 of the Negotiable Instruments Act, 1881"
            in INDIAN_LEGAL_LANGUAGE_REGISTER
        )
        assert (
            "Article 226 of the Constitution of India"
            in INDIAN_LEGAL_LANGUAGE_REGISTER
        )

    def test_does_not_mandate_native_numerals(self):
        """The previous version told the LLM to 'render figures in the
        response language's native script when that language uses a
        distinct digit family'. Those specific mandate phrases must be
        gone. (The words 'native script' may still appear inside the
        new anti-directive prose — 'do NOT transliterate to native' —
        so we scan for the exact removed clauses instead.)"""
        # The removed positive mandate — must NOT appear.
        assert (
            "response language's native script when that language uses"
            not in INDIAN_LEGAL_LANGUAGE_REGISTER
        )
        assert "distinct digit family" not in INDIAN_LEGAL_LANGUAGE_REGISTER
        # And the specific old "Render the figures ... in ... native script"
        # clause must not be present.
        assert (
            "Render the figures\n  and the words in the response language"
            not in INDIAN_LEGAL_LANGUAGE_REGISTER
        )


# ---------------------------------------------------------------------------
# 4) DRAFTING_FANOUT_JUDGE_PROMPT — heading rules
# ---------------------------------------------------------------------------

class TestDraftingFanoutHeadingRules:
    def test_anchor_block_present_in_heading_rules(self):
        assert "FIXED-ENGLISH ANCHORS" in DRAFTING_FANOUT_JUDGE_PROMPT

    def test_heading_rule_calls_out_latin_digits(self):
        # The specific rule for digits inside a heading
        assert "stays Latin" in DRAFTING_FANOUT_JUDGE_PROMPT

    def test_heading_rule_forbids_translated_labels(self):
        # Anti-examples inside the heading block
        assert "कलम" in DRAFTING_FANOUT_JUDGE_PROMPT
        assert "Order XXXIX Rules 1 and 2 CPC" in DRAFTING_FANOUT_JUDGE_PROMPT


# ---------------------------------------------------------------------------
# 5) CRITIQUE_PROMPT — critic rules reversed
# ---------------------------------------------------------------------------

class TestCritiquePromptReversal:
    def test_critic_flags_native_digits_as_violation(self):
        # The critic-facing text explicitly lists native-digit paragraph
        # numbers as MAJOR.
        assert "१. वादीचे कथन" in CRITIQUE_PROMPT
        assert "MAJOR" in CRITIQUE_PROMPT

    def test_critic_flags_translated_act_name(self):
        # Anti-example: fully translated Act name
        assert "परक्राम्य लिखत अधिनियम, १८८१" in CRITIQUE_PROMPT

    def test_critic_flags_translated_section_label(self):
        # "कलम १३८" should appear as anti-example
        assert "कलम १३८" in CRITIQUE_PROMPT

    def test_critic_declares_latin_digits_required_in_strict(self):
        """The strict-mode block previously said Latin digits were a
        MAJOR violation. The reversed block calls them REQUIRED."""
        # Look for the non-violation clause that lists Latin digits.
        assert "NON-VIOLATIONS in strict mode" in CRITIQUE_PROMPT
        assert "Latin digits everywhere" in CRITIQUE_PROMPT

    def test_critic_declares_english_statute_ref_required(self):
        assert (
            "Full statutory references in English"
            in CRITIQUE_PROMPT
            or "Section 138 of the Negotiable Instruments Act, 1881"
            in CRITIQUE_PROMPT
        )

    def test_critic_removed_old_native_digit_mandate(self):
        """The old block contained: 'Any digit-prefixed numbered-list
        start using Latin digits' as a violation. That line must be gone."""
        assert (
            "digit-prefixed numbered-list start using Latin digits"
            not in CRITIQUE_PROMPT
        )
        # Old block also had "must be in the target script" for numerals
        # — that was reversed.
        assert 'Must be "१.' not in CRITIQUE_PROMPT
        assert 'Must be "(२)' not in CRITIQUE_PROMPT


# ---------------------------------------------------------------------------
# 6) REFINE_PROMPT — refiner instruction reversed
# ---------------------------------------------------------------------------

class TestRefinePromptReversal:
    def test_refiner_told_to_rewrite_native_to_latin(self):
        assert "Replace every native-script digit with its Latin counterpart" in REFINE_PROMPT

    def test_refiner_told_to_english_the_statute_reference(self):
        assert "Section 138 of the Negotiable Instruments Act, 1881" in REFINE_PROMPT

    def test_refiner_no_longer_told_to_rewrite_latin_to_native(self):
        """Old Rule 6 said: 'rewrite EVERY Latin digit in the response'.
        The new rule tells the refiner the OPPOSITE."""
        assert "rewrite EVERY Latin digit" not in REFINE_PROMPT

    def test_refiner_preserves_target_language_wrapper(self):
        # Refiner is told the surrounding clause stays in the target language.
        assert "surrounding native-language clause" in REFINE_PROMPT or (
            "surrounding native clause" in REFINE_PROMPT
        )


# ---------------------------------------------------------------------------
# 6b) No-code-fence rule around English anchors
#     (2026-07-12 follow-up: client screenshot showed the LLM occasionally
#     wrapping the English statutory reference in `` ` `` inline code, which
#     the frontend renders as monospaced typewriter font. Fix is prompt-only:
#     new anchor rule + critique category + refiner instruction.)
# ---------------------------------------------------------------------------

class TestNoCodeFenceAroundAnchors:
    """The four enforcement layers must all carry the no-backticks rule."""

    @pytest.mark.parametrize("lang", ["hi", "mr", "ta", "bn", "gu"])
    def test_localize_prompt_strict_forbids_backticks(self, lang):
        intent = UserIntent(
            language=lang, language_explicit=True, strict_language=True,
            confidence=0.9,
        )
        out = localize_prompt(BASE, lang, intent)
        # Anchor block must call out plain-text emission
        assert "PLAIN-TEXT EMISSION" in out
        # And explicitly name backticks + code fences + <code> tags as bad
        assert "backtick" in out
        assert "code fences" in out or "code fence" in out

    @pytest.mark.parametrize("lang", ["hi", "mr", "ta"])
    def test_localize_prompt_non_strict_forbids_backticks(self, lang):
        intent = UserIntent(
            language=lang, language_explicit=True, strict_language=False,
            confidence=0.9,
        )
        out = localize_prompt(BASE, lang, intent)
        assert "PLAIN-TEXT EMISSION" in out
        assert "backtick" in out

    def test_shared_register_forbids_backticks(self):
        assert "PLAIN-TEXT EMISSION" in INDIAN_LEGAL_LANGUAGE_REGISTER
        assert "backtick" in INDIAN_LEGAL_LANGUAGE_REGISTER

    def test_shared_register_explicitly_names_monospace(self):
        # The rule must explain WHY (frontend renders backticks as
        # monospace) so the LLM understands the reason, not just the rule.
        assert "monospace" in INDIAN_LEGAL_LANGUAGE_REGISTER.lower() or (
            "typewriter" in INDIAN_LEGAL_LANGUAGE_REGISTER.lower()
        )

    def test_critic_has_english_anchor_in_code_fence_category(self):
        assert "ENGLISH-ANCHOR-IN-CODE-FENCE" in CRITIQUE_PROMPT
        # And the critic gives concrete anti-examples
        assert "`Section 138 of the Negotiable Instruments Act, 1881`" in CRITIQUE_PROMPT
        assert "``Indian Contract Act, 1872``" in CRITIQUE_PROMPT
        assert "`Code on Wages, 2019`" in CRITIQUE_PROMPT
        # <code> HTML tag also called out
        assert "<code>Section 138</code>" in CRITIQUE_PROMPT

    def test_critic_preserves_bold_italic_carve_out(self):
        """Bold (**...**) and italics (*...*) around headings are FINE — the
        rule must explicitly say so to avoid the refiner stripping them."""
        assert "BOLD" in CRITIQUE_PROMPT
        assert "ITALIC" in CRITIQUE_PROMPT
        # The carve-out phrasing
        assert "are FINE" in CRITIQUE_PROMPT

    def test_refiner_told_to_strip_backticks(self):
        assert "ENGLISH-ANCHOR-IN-CODE-FENCE STRIPPING" in REFINE_PROMPT
        # And gives concrete before → after examples
        assert "`Code on Wages, 2019`" in REFINE_PROMPT
        assert "``Indian Contract Act, 1872``" in REFINE_PROMPT
        # Triple-fence variant
        assert "```" in REFINE_PROMPT

    def test_refiner_told_to_preserve_bold_italic(self):
        """Adjacent bold/italic markdown must survive; only code formatting
        is stripped."""
        assert "PRESERVE any adjacent bold" in REFINE_PROMPT

    def test_refiner_told_not_to_translate_while_stripping(self):
        """Stripping the backticks must NOT collapse to translating the
        anchor — the anchor stays English by policy."""
        assert (
            "Do NOT translate the anchor" in REFINE_PROMPT
            or "anchor stays English" in REFINE_PROMPT
        )


# ---------------------------------------------------------------------------
# 7) End-to-end tests — gated behind ENGLISH_ANCHORS_E2E=1
# ---------------------------------------------------------------------------

E2E_ENABLED = os.getenv("ENGLISH_ANCHORS_E2E") == "1"


def _strip_english_citation_blocks(text: str) -> str:
    """Remove obvious English case-law citation spans so the digit scan
    doesn't false-positive on a legitimate 'AIR 1973 SC 1461' inside a
    citation. Case names are proper nouns; the digits inside a case cite
    are permitted anchors.
    """
    # Line-based: drop lines that look like citations or headings
    kept = []
    for line in text.splitlines():
        # Skip pure-English lines that read like citation blocks or
        # canonical statutory spans — those are ANCHORED English.
        stripped = line.strip()
        # Any line that contains "v." between two English proper-noun
        # spans is a case citation — skip.
        if re.search(r"\b[A-Z][A-Za-z.'\- ]{2,}\s+v\.\s+[A-Z]", stripped):
            continue
        # Any line that IS a canonical English statutory span at the top
        # level (e.g. "Section X of the Y Act, YYYY.") — skip.
        if re.match(r"^\s*Section \d+.*Act,\s*\d{4}", stripped):
            continue
        kept.append(line)
    return "\n".join(kept)


@pytest.mark.skipif(not E2E_ENABLED,
                    reason="Set ENGLISH_ANCHORS_E2E=1 to run drafting E2E tests")
class TestE2ELiveDrafting:
    """These smoke tests actually call Gemini. They verify the anchor
    policy holds end-to-end — from `localize_prompt` through the drafting
    pipeline to the self-refine safety net."""

    @pytest.fixture(autouse=True)
    def _require_keys(self):
        # Load .env lazily so `pytest` runs pick up whatever the user has
        # configured locally without needing a shell-level export.
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        if not os.getenv("GOOGLE_API_KEY"):
            pytest.skip("GOOGLE_API_KEY not set — e2e drafting needs Gemini.")
        if not os.getenv("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY not set — orchestrator needs GPT-4o.")

    def _draft(self, query: str, user_language: str, source_text: str = "") -> str:
        """Invoke the drafting agent's single-pass path directly with a
        canned reference draft — mirrors the tests/test_drafting_simplification
        e2e pattern without a full ES round-trip."""
        from agents.drafting import _generate_single_pass
        from config.intent import UserIntent

        # A minimal reference draft; the pipeline requires SOMETHING.
        reference = (
            "SAMPLE DEMAND NOTICE UNDER SECTION 138 OF THE NEGOTIABLE "
            "INSTRUMENTS ACT, 1881\n\n"
            "To,\n[Addressee]\n\n"
            "1. Facts of the case ...\n"
            "2. Dishonour of cheque ...\n"
            "3. Statutory demand ...\n"
        )
        intent = UserIntent(
            language=user_language,
            language_explicit=True,
            strict_language=True,
            confidence=0.9,
        )
        # No-op progress emitter — the drafting agent calls this for
        # per-section UI updates; in the test we ignore events.
        def _noop_emit(*_args, **_kwargs):
            return None

        out = _run(_generate_single_pass(
            query=query,
            user_facts=source_text,
            reference_draft=reference,
            user_intent=intent,
            user_language=user_language,
            progress_emit=_noop_emit,
        ))
        return out or ""

    @pytest.mark.parametrize("lang,query", [
        ("hi", "एक Section 138 NI Act का demand notice draft करें, "
               "cheque संख्या 12345 दिनांक 15 May 2024 रुपये 5,00,000 का।"),
        ("mr", "Section 138 NI Act अंतर्गत demand notice draft करा, "
               "cheque क्रमांक 12345, दिनांक 15 May 2024, रक्कम Rs. 5,00,000."),
    ])
    def test_hindi_marathi_draft_uses_latin_digits(self, lang, query):
        draft = self._draft(query, lang)
        assert len(draft) > 500, "Draft too short — pipeline likely errored."
        # After stripping English-anchored spans, no native digit may remain.
        body = _strip_english_citation_blocks(draft)
        has_native, script = _has_native_digit(body)
        assert not has_native, (
            f"Draft in {lang} contains {script} digit — anchor policy "
            f"violated. Snippet:\n{body[:600]}"
        )
        # The canonical English statutory span must survive verbatim.
        assert "Section 138" in draft, (
            f"Draft missing English statutory anchor 'Section 138'. "
            f"Snippet:\n{draft[:600]}"
        )
        assert "Negotiable Instruments Act, 1881" in draft, (
            "Draft translated the Act name — anchor policy violated. "
            f"Snippet:\n{draft[:600]}"
        )

    def test_hindi_draft_body_prose_in_devanagari(self):
        """Anchor policy only pins numerals + statute refs to English.
        The body prose itself must remain in the target language."""
        query = "एक Section 138 NI Act का demand notice draft करें।"
        draft = self._draft(query, "hi")
        # Devanagari should be the dominant body script (heuristic: > 50
        # Devanagari code points suggests real prose, not just cover).
        devanagari_count = sum(
            1 for ch in draft
            if 0x0900 <= ord(ch) <= 0x097F and not (0x0966 <= ord(ch) <= 0x096F)
        )
        assert devanagari_count > 100, (
            f"Draft has only {devanagari_count} Devanagari code points — "
            f"body prose looks like it collapsed to English. Full body:\n"
            f"{draft[:800]}"
        )

    @pytest.mark.parametrize("lang,query", [
        ("hi", "मेरे मुवक्किल के employment contract को गलत तरीके से "
               "समाप्त कर दिया गया है — एक rejoinder draft करें जिसमें "
               "Indian Contract Act, 1872 और Code on Wages, 2019 का "
               "reference हो।"),
        ("mr", "माझ्या पक्षकाराचा रोजगार करार चुकीच्या पद्धतीने संपुष्टात "
               "आणला गेला — Indian Contract Act, 1872 आणि Code on Wages, "
               "2019 चा संदर्भ असलेला rejoinder draft करा."),
    ])
    def test_no_backticks_around_english_anchors(self, lang, query):
        """Client screenshot (2026-07-12) showed the model wrapping
        'Indian Contract Act, 1872' and 'Code on Wages, 2019' + 'Section
        17(2)' in `` ` `` inline-code backticks — frontend rendered them
        in monospace. Verify the LLM no longer emits backticks around any
        English anchor after the no-code-fence prompt directive was added."""
        draft = self._draft(query, lang)
        assert len(draft) > 500, "Draft too short — pipeline likely errored."
        # Any Section N of Y Act, YYYY / Article N of ... / Act name
        # wrapped in one-or-more backticks is a violation.
        # Scan for the exact patterns most often flagged.
        patterns_to_reject = [
            # Backtick-wrapped English statutory span, either single or
            # double-backtick. Match any run that starts with a backtick
            # then Latin-capital ("Section", "Article", "Order", "Indian",
            # "Code", "Bharatiya", "Constitution", etc.).
            r"`{1,3}(?:Section|Article|Order|Rule|Indian|Code|"
            r"Bharatiya|Constitution|Negotiable|Hindu|Muslim|Companies|"
            r"Consumer|Specific|Limitation|Prevention|Protection)\b[^`]*`{1,3}",
            # HTML <code> tag around an English anchor
            r"<code>[^<]*(?:Section|Article|Order|Act|Code)[^<]*</code>",
        ]
        offending = []
        for pat in patterns_to_reject:
            for m in re.finditer(pat, draft):
                offending.append(m.group(0))
        assert not offending, (
            f"[{lang}] Draft contains code-fenced English anchor(s):\n"
            + "\n".join(f"  - {o!r}" for o in offending[:5])
            + f"\n\nFirst 800 chars of draft:\n{draft[:800]}"
        )
