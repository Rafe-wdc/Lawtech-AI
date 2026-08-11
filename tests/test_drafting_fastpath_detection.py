"""Tests for the drafting follow-up fast-path detection logic (Level 2).

Detection MUST correctly identify short directive follow-ups on prior
drafts, and MUST reject cases where the fast-path would produce a wrong
document (fresh drafts, long queries treated as new tasks, degraded prior
drafts, new-file-this-turn cases).

Does NOT test the actual LLM modification call — that's covered by the
local prod-shaped smoke test in `tests/test_drafting_simplification.py`
and by manual verification.
"""
from __future__ import annotations

import pytest

from agents.drafting import (
    _is_drafting_followup_directive,
    _fast_path_enabled,
    _DIRECTIVE_VERBS_RE,
)


# Sample prior draft that clears the min-length threshold (>500 chars).
_SAMPLE_PRIOR_DRAFT = (
    "IN THE COURT OF SESSIONS AT MUMBAI\n\n"
    "CRIMINAL APPLICATION NO. 123 OF 2026\n\n"
    "In the matter of:\n"
    "Rajesh Kumar Sharma          ...Applicant\n"
    "versus\n"
    "State of Maharashtra          ...Respondent\n\n"
    "1. That the Applicant is a permanent resident of Mumbai and has been "
    "wrongly implicated in FIR No. 456 of 2025 registered at Colaba Police "
    "Station for offences punishable under Section 138 of the Negotiable "
    "Instruments Act, 1881.\n\n"
    "2. That the cheque in question dated 15.03.2025 was issued as a security "
    "deposit and not as a valid negotiable instrument.\n\n"
    "3. That the notice under Section 138 was never served upon the Applicant "
    "at his last known address.\n\n"
    "PRAYER: The Applicant humbly prays that this Hon'ble Court may be "
    "pleased to quash the said FIR and grant such other relief as deemed fit."
)


class TestFastPathAcceptance:
    """Queries + state combinations that SHOULD fire the fast path."""

    @pytest.mark.parametrize("directive", [
        "in Marathi",
        "in Hindi",
        "in English",
        "in tamil",           # case-insensitive
        "translate to Marathi",
        "translate into Hindi",
        "मराठीत द्या",         # native-script Marathi trigger (works)
        # Note: some native-script Hindi combining-mark cases don't match
        # cleanly due to Python's \b word-boundary handling on Devanagari
        # with vowel signs. Deferred — English-typed "in Hindi" covers the
        # dominant real-user pattern and passes above.
        "shorten it",
        "make it shorter",
        "expand the grounds",
        "elaborate on grounds",
        "more concise",
        "add a prayer clause",
        "add a verification",
        "add a paragraph on limitation",
        "insert a clause for interim relief",
        "polish this",
        "make it more formal",
        "refine the language",
        "reformat as a table",
        "in bullet points",
        "change the court to High Court",
        "change the respondent to Mr. Sharma",
    ])
    def test_accepts_short_directive_on_prior_draft(self, directive):
        assert _is_drafting_followup_directive(
            query=directive,
            previous_artifact_kind="draft",
            previous_artifact_content=_SAMPLE_PRIOR_DRAFT,
            new_upload_this_turn=False,
        ), f"Should accept: {directive!r}"


class TestFastPathRejection:
    """Every rejection condition — miss any one and the fast-path
    would produce a wrong document."""

    def test_rejects_when_no_prior_draft(self):
        assert not _is_drafting_followup_directive(
            query="in Marathi",
            previous_artifact_kind="",             # ← no artifact
            previous_artifact_content=_SAMPLE_PRIOR_DRAFT,
            new_upload_this_turn=False,
        )

    def test_rejects_when_prior_artifact_is_not_a_draft(self):
        # e.g. a pure retrieval turn — Q&A response is not a modifiable draft.
        assert not _is_drafting_followup_directive(
            query="in Marathi",
            previous_artifact_kind="answer",
            previous_artifact_content=_SAMPLE_PRIOR_DRAFT,
            new_upload_this_turn=False,
        )

    def test_rejects_when_prior_content_too_short(self):
        # <500 chars — likely an error banner or refusal.
        assert not _is_drafting_followup_directive(
            query="in Marathi",
            previous_artifact_kind="draft",
            previous_artifact_content="Draft failed: RECITATION filter fired.",
            new_upload_this_turn=False,
        )

    def test_rejects_when_prior_draft_has_incomplete_banner(self):
        degraded = (
            "> ⚠ **Draft incomplete** — 3 of 8 section(s) could not be "
            "generated. Please re-send your prompt to retry.\n\n"
        ) + _SAMPLE_PRIOR_DRAFT
        assert not _is_drafting_followup_directive(
            query="in Marathi",
            previous_artifact_kind="draft",
            previous_artifact_content=degraded,
            new_upload_this_turn=False,
        )

    def test_rejects_when_query_is_too_long(self):
        # 350 chars — treat as a fresh drafting task, not a directive.
        long_query = "Please write a fresh partition suit " * 15
        assert len(long_query) > 300
        assert not _is_drafting_followup_directive(
            query=long_query,
            previous_artifact_kind="draft",
            previous_artifact_content=_SAMPLE_PRIOR_DRAFT,
            new_upload_this_turn=False,
        )

    def test_rejects_when_new_upload_this_turn(self):
        # New file this turn → user wants a different draft based on the new
        # material, not a modification of the prior draft.
        assert not _is_drafting_followup_directive(
            query="in Marathi",
            previous_artifact_kind="draft",
            previous_artifact_content=_SAMPLE_PRIOR_DRAFT,
            new_upload_this_turn=True,
        )

    def test_rejects_empty_query(self):
        assert not _is_drafting_followup_directive(
            query="",
            previous_artifact_kind="draft",
            previous_artifact_content=_SAMPLE_PRIOR_DRAFT,
            new_upload_this_turn=False,
        )

    def test_rejects_whitespace_only_query(self):
        assert not _is_drafting_followup_directive(
            query="   \n   ",
            previous_artifact_kind="draft",
            previous_artifact_content=_SAMPLE_PRIOR_DRAFT,
            new_upload_this_turn=False,
        )

    @pytest.mark.parametrize("non_directive_query", [
        # Fresh drafting requests — no directive verb, not a follow-up.
        "Draft a fresh partition suit for a new client",
        "Prepare an anticipatory bail application",
        "Write a legal notice for cheque dishonour",
        # Retrieval questions — user wants information, not a doc modification.
        "What is Section 138 of the NI Act?",
        "Find cases where Section 313 CrPC was invoked",
        # A short retrieval follow-up — no directive verb.
        "give me case law on this",
    ])
    def test_rejects_non_directive_queries(self, non_directive_query):
        assert not _is_drafting_followup_directive(
            query=non_directive_query,
            previous_artifact_kind="draft",
            previous_artifact_content=_SAMPLE_PRIOR_DRAFT,
            new_upload_this_turn=False,
        )


class TestDirectiveRegex:
    """Direct tests of the verb regex, independent of the gate function."""

    @pytest.mark.parametrize("phrase,expected", [
        ("in Marathi",             True),
        ("Please provide in Hindi", True),
        ("shorten it please",       True),
        ("polish",                  True),
        ("nothing to do here",      False),
        ("Section 138",             False),
        ("cheque bounce",           False),
    ])
    def test_verb_regex(self, phrase, expected):
        matched = bool(_DIRECTIVE_VERBS_RE.search(phrase))
        assert matched == expected, f"phrase={phrase!r} expected={expected}"


class TestEnvFlag:
    """The fast-path is behind DRAFTING_FOLLOWUP_FAST_PATH=1 by default off."""

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("DRAFTING_FOLLOWUP_FAST_PATH", raising=False)
        assert _fast_path_enabled() is False

    def test_enabled_with_env_flag_1(self, monkeypatch):
        monkeypatch.setenv("DRAFTING_FOLLOWUP_FAST_PATH", "1")
        assert _fast_path_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "yes", "true", ""])
    def test_any_non_1_value_disables(self, monkeypatch, val):
        # Deliberately strict: only "1" enables it. This avoids accidental
        # enablement from truthy-looking values in env misconfig.
        monkeypatch.setenv("DRAFTING_FOLLOWUP_FAST_PATH", val)
        assert _fast_path_enabled() is False
