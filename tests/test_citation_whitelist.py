"""Tests for strict citation whitelisting (shadow by default).

The stakes are asymmetric: failing to flag a fabricated citation leaves a bad
authority in a filing, but wrongly flagging a real one leads — in enforce
mode — to DELETING a real authority from a filing. These tests weight the
second error more heavily, which is why matching is lenient by design.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from core.citation_whitelist import (
    ENFORCE, OFF, SHADOW, apply_citation_policy, audit_citations,
    extract_draft_citations, extract_retrieved_cases, strip_mode,
)

SCI_BLOCK = (
    "## RELEVANT SUPREME COURT JUDGMENTS\n"
    "Found 2 relevant judgment(s):\n\n"
    "**SANJAY CHANDRA VS CENTRAL BUREAU OF INVESTIGATION** (DB ID: 12345)\n"
    "- Case No: Crl.A. No.-000123 - 2011\n"
    "- Date: 23-11-2011\n\n"
    "**DATARAM SINGH VS STATE OF UTTAR PRADESH** (DB ID: 45087)\n"
    "- Case No: Crl.A. No.-000227 - 2018\n"
)


# --- mode handling ---------------------------------------------------------

def test_default_mode_is_shadow():
    with patch.dict(os.environ, {}, clear=True):
        assert strip_mode() == SHADOW


def test_unrecognised_mode_falls_back_to_shadow():
    with patch.dict(os.environ, {"CITATION_STRIP_MODE": "nonsense"}):
        assert strip_mode() == SHADOW


@pytest.mark.parametrize("val,expected", [
    ("shadow", SHADOW), ("enforce", ENFORCE), ("off", OFF), ("ENFORCE", ENFORCE),
])
def test_explicit_modes(val, expected):
    with patch.dict(os.environ, {"CITATION_STRIP_MODE": val}):
        assert strip_mode() == expected


# --- extraction ------------------------------------------------------------

def test_extracts_case_citations_from_a_draft():
    draft = ("As held in Sanjay Chandra v. CBI, (2012) 1 SCC 40, bail is the "
             "rule. See also Dataram Singh v. State of Uttar Pradesh & Anr.")
    cites = extract_draft_citations(draft)
    assert len(cites) == 2
    assert any("Sanjay Chandra" in c for c in cites)


def test_does_not_treat_boilerplate_as_a_citation():
    draft = "The Applicant v. Respondent heading is not a case citation."
    assert extract_draft_citations(draft) == []


def test_extracts_retrieved_cases_from_the_sci_block():
    got = extract_retrieved_cases({"sci": SCI_BLOCK})
    assert any("SANJAY CHANDRA" in g.upper() for g in got)
    assert any("DATARAM SINGH" in g.upper() for g in got)


# --- grounding -------------------------------------------------------------

def test_retrieved_citation_is_grounded_despite_formatting_differences():
    """'Sanjay Chandra v. CBI' vs retrieved 'SANJAY CHANDRA VS CENTRAL BUREAU
    OF INVESTIGATION' — same case, different rendering. Must NOT be flagged."""
    draft = "As held in Sanjay Chandra v. CBI, (2012) 1 SCC 40, bail is the rule."
    a = audit_citations(draft, {"sci": SCI_BLOCK})
    assert a["ungrounded"] == [], a
    assert len(a["grounded"]) == 1


def test_abbreviated_state_name_still_grounds():
    """'State of U.P.' vs retrieved 'STATE OF UTTAR PRADESH'."""
    draft = "See Dataram Singh v. State of U.P., (2018) 3 SCC 22."
    a = audit_citations(draft, {"sci": SCI_BLOCK})
    assert a["ungrounded"] == [], a


def test_a_case_absent_from_retrieval_is_flagged():
    draft = "As held in Parag Kishore Satoskar v. State of Jharkhand, the applicant..."
    a = audit_citations(draft, {"sci": SCI_BLOCK})
    assert len(a["ungrounded"]) == 1
    assert "Parag Kishore Satoskar" in a["ungrounded"][0]


def test_empty_retrieval_flags_everything_and_says_so():
    """The measured SCI reality for most bail queries. The flag matters:
    it is what separates 'fabricated' from 'retrieval returned nothing'."""
    draft = "As held in Sanjay Chandra v. CBI, bail is the rule."
    a = audit_citations(draft, {})
    assert a["retrieval_empty"] is True
    assert len(a["ungrounded"]) == 1


# --- policy application ----------------------------------------------------

def test_shadow_mode_changes_nothing():
    draft = "As held in Parag Kishore Satoskar v. State of Jharkhand, bail follows."
    with patch.dict(os.environ, {"CITATION_STRIP_MODE": "shadow"}):
        out, audit = apply_citation_policy(draft, {"sci": SCI_BLOCK})
    assert out == draft, "shadow mode must never modify the draft"
    assert audit["ungrounded"], "but it must still record what it would strip"


def test_off_mode_does_not_even_audit():
    draft = "As held in Some Case v. Another, bail follows."
    with patch.dict(os.environ, {"CITATION_STRIP_MODE": "off"}):
        out, audit = apply_citation_policy(draft, {"sci": SCI_BLOCK})
    assert out == draft
    assert audit["mode"] == OFF


def test_enforce_mode_removes_only_the_ungrounded_citation():
    draft = ("As held in Sanjay Chandra v. CBI, bail is the rule. "
             "See also Parag Kishore Satoskar v. State of Jharkhand.")
    with patch.dict(os.environ, {"CITATION_STRIP_MODE": "enforce"}):
        out, audit = apply_citation_policy(draft, {"sci": SCI_BLOCK})
    assert "Sanjay Chandra v. CBI" in out, "a retrieved case must survive"
    assert "Parag Kishore Satoskar" not in out


def test_empty_draft_is_safe():
    out, audit = apply_citation_policy("", {"sci": SCI_BLOCK})
    assert out == ""


# --- shadow-mode safety: EVERY mutation path, not just the obvious one -----
#
# A shadow-mode violation was found in review: apply_citation_policy returned
# the draft unchanged, but source_registry was still handed to self_refine,
# which arms the critic's `unretrieved_citation` category — and the REFINER
# acts on whatever the critic raises. Shadow has to mean shadow on every path.

def test_registry_is_withheld_from_self_refine_in_shadow_mode():
    """The regression that review caught. In shadow/off, self_refine must
    receive source_registry=None so the critic cannot arm citation stripping."""
    import agents.drafting as D
    from core.source_registry import RetrievedSource, SourceRegistry

    reg = SourceRegistry()
    reg.add(RetrievedSource(id="sci-1", agent="SCI_Judgment", type="sci_judgment",
                            canonical_citation="Sanjay Chandra v. CBI", title="x"))
    captured = {}

    async def fake_self_refine(draft, user_query, intent, **kw):
        captured["source_registry"] = kw.get("source_registry")
        return draft, []

    async def fake_generate(**kwargs):
        return "## Grounds\nAs held in Some Case v. Another, bail follows.\n"

    # Mirror `_acquire_reference_draft`'s CURRENT arity. It returns a 3-tuple
    # on this branch; the regional-language work widens it to 4 by adding the
    # English query. Built from the real signature so the fixture cannot drift
    # out of step with the function it stands in for.
    import inspect as _inspect
    _ret = str(_inspect.signature(D._acquire_reference_draft).return_annotation)
    _arity = _ret.count("str") or 3

    async def fake_acquire(*a, **kw):
        base = ("ref", "/corpus/bail.csv", "es", "english query")
        return base[:_arity]

    class Ctx(dict):
        registry = reg

    for mode, expect_registry in (("shadow", False), ("off", False), ("enforce", True)):
        captured.clear()
        with patch.dict(os.environ, {"CITATION_STRIP_MODE": mode}), \
             patch("agents.drafting._acquire_reference_draft", fake_acquire), \
             patch("agents.drafting._generate_draft", fake_generate), \
             patch("agents.drafting.self_refine", fake_self_refine), \
             patch("agents.drafting._gather_relevant_context",
                   new=lambda *a, **kw: _completed_ctx(Ctx())):
            from agents.drafting import drafting_node
            from config.intent import default_intent
            state = {
                "query": "draft a bail application", "original_query": "draft a bail application",
                "agent_queries": {"Drafting": "draft a bail application"},
                "user_context": "", "user_language": "en",
                "user_intent": default_intent(), "chat_history": [], "agent_results": {},
            }
            _run_node(drafting_node(state))
        got = captured.get("source_registry")
        if expect_registry:
            assert got is not None, f"{mode}: enforce must arm the critic"
        else:
            assert got is None, (
                f"{mode}: registry leaked to self_refine — the critic can strip "
                f"citations through the refiner even in shadow mode"
            )


def _completed_ctx(value):
    import asyncio as _a
    fut = _a.Future()
    fut.set_result(value)
    return fut


def _run_node(coro):
    import asyncio as _a
    return _a.get_event_loop().run_until_complete(coro)
