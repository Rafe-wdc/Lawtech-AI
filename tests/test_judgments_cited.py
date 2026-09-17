"""Judgments Cited: PDF links for the judgments a draft relies on.

Lawyer feedback (2026-09-17): a medical-negligence plaint cited Jacob Mathew,
V. Kishan Rao, Savita Garg, Laxman Balkrishna Joshi and others with no PDF
link, because drafts run without the judgment agents (cite_appendix is off by
default) and most citations come from model memory. The lookup pass appends a
"## Judgments Cited" block after the draft without touching the draft body.

The ES lookup is replaced by a fake here; extraction, matching, rendering and
the orchestrator wiring are exercised for real.
"""

from __future__ import annotations

import asyncio

import core.judgments_cited as jc
from core.judgments_cited import (
    HEADING, NOT_VERIFIED, CitedCase, FoundJudgment,
    _parties_match, _year_ok, append_judgments_cited, extract_cited_cases,
)

LAWYER_DRAFT = (
    "In P.B. Desai v. State of Maharashtra, (2013) 15 SCC 481, the Hon'ble Supreme Court "
    "affirmed that an omission to exercise a legal duty fastens civil liability.\n\n"
    "In Dr. Laxman Balkrishna Joshi v. Dr. Trimbak Bapu Godbole, AIR 1969 SC 128, the "
    "Hon'ble Supreme Court laid down the duties of a doctor.\n\n"
    "The standard is Bolam v. Friern Hospital Management Committee, [1957] 1 WLR 582, as "
    "applied in Jacob Mathew v. State of Punjab, (2005) 6 SCC 1.\n\n"
    "As reiterated in V. Kishan Rao v. Nikhil Super Speciality Hospital, (2010) 5 SCC 513, "
    "res ipsa loquitur applies. In Savita Garg v. National Heart Institute, (2004) 8 SCC 56, "
    "the hospital was held vicariously liable. Again Jacob Mathew v. State of Punjab.\n"
)


def _names(text):
    return [f"{c.petitioner} v. {c.respondent}" for c in extract_cited_cases(text)]


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def test_extracts_every_case_in_the_lawyer_draft_once():
    assert _names(LAWYER_DRAFT) == [
        "P.B. Desai v. State of Maharashtra",
        "Dr. Laxman Balkrishna Joshi v. Dr. Trimbak Bapu Godbole",
        "Bolam v. Friern Hospital Management Committee",
        "Jacob Mathew v. State of Punjab",
        "V. Kishan Rao v. Nikhil Super Speciality Hospital",
        "Savita Garg v. National Heart Institute",
    ]


def test_citation_and_year_are_captured():
    cases = {c.petitioner: c for c in extract_cited_cases(LAWYER_DRAFT)}
    assert cases["Jacob Mathew"].citation == "(2005) 6 SCC 1"
    assert cases["Jacob Mathew"].year == 2005
    assert cases["Dr. Laxman Balkrishna Joshi"].citation == "AIR 1969 SC 128"
    assert cases["Dr. Laxman Balkrishna Joshi"].year == 1969


def test_names_with_parentheses_abbreviations_and_italics():
    text = ("Relying on *Sushila Aggarwal v. State (NCT of Delhi)*, (2020) 5 SCC 1 and "
            "M/s. Indus Airways Pvt. Ltd. v. Magnum Aviation Pvt. Ltd., (2014) 12 SCC 539.")
    assert _names(text) == [
        "Sushila Aggarwal v. State (NCT of Delhi)",
        "M/s. Indus Airways Pvt. Ltd. v. Magnum Aviation Pvt. Ltd.",
    ]


def test_sentence_end_stops_the_respondent():
    assert _names("See Jacob Mathew v. State of Punjab. Section 304A was invoked.") == [
        "Jacob Mathew v. State of Punjab"]


def test_no_case_in_ordinary_prose_or_statutes():
    assert _names("The Plaintiff vs the Defendant dispute under Section 138 of the "
                  "Negotiable Instruments Act, 1881 and Order VII Rule 1 CPC.") == []


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def test_look_alike_party_is_rejected():
    case = CitedCase("Jacob Mathew", "State of Punjab")
    assert _parties_match(case, "JACOB MATHEW", "STATE OF PUNJAB AND ANR.")
    # the full-text match returns this for the same query
    assert not _parties_match(case, "MATHEW JACOB THOMAS MATHEW", "THE PUNJAB NATIONAL BANK")


def test_honorifics_and_initials_do_not_block_a_match():
    case = CitedCase("Dr. Laxman Balkrishna Joshi", "Dr. Trimbak Bapu Godbole")
    assert _parties_match(case, "LAXMAN BALKRISHNA JOSHI", "TRIMBAK BAPU GODBOLE AND ANR.")


def test_year_check_allows_reporting_lag_only():
    case = CitedCase("A", "B", "(2020) 5 SCC 1")
    assert _year_ok(case, 2020) and _year_ok(case, 2019)
    assert not _year_ok(case, 2015)
    assert _year_ok(CitedCase("A", "B"), 1999)  # no cited year, no check


# ---------------------------------------------------------------------------
# Appending
# ---------------------------------------------------------------------------

_DB = {
    "Jacob Mathew": FoundJudgment("Supreme Court of India", "05-08-2005",
                                  "https://api.sci.gov.in/jonew/judis/27088.pdf"),
    "V. Kishan Rao": FoundJudgment("Supreme Court of India", "08-03-2010",
                                   "https://api.sci.gov.in/jonew/judis/36301.pdf"),
    "Savita Garg": FoundJudgment("Supreme Court of India", "12-10-2004", None),
}


def _fake_lookup(case):
    return _DB.get(case.petitioner)


def test_block_is_appended_and_draft_body_is_untouched():
    out = asyncio.run(append_judgments_cited(LAWYER_DRAFT, lookup=_fake_lookup))
    assert out.startswith(LAWYER_DRAFT.rstrip())
    block = out[len(LAWYER_DRAFT.rstrip()):]
    assert HEADING in block
    assert ("- *Jacob Mathew v. State of Punjab*, (2005) 6 SCC 1 — Supreme Court of India, "
            "05-08-2005 ([Judgment PDF](https://api.sci.gov.in/jonew/judis/27088.pdf))") in block
    assert "36301.pdf" in block
    assert (f"*Bolam v. Friern Hospital Management Committee*, [1957] 1 WLR 582 — "
            f"{NOT_VERIFIED}") in block
    assert ("*Savita Garg v. National Heart Institute*, (2004) 8 SCC 56 — Supreme Court of "
            "India, 12-10-2004 (in Lawttorney database; PDF not available)") in block
    assert block.count("Jacob Mathew") == 1


def test_draft_without_cases_is_returned_unchanged():
    draft = "LEGAL NOTICE under Section 138 of the Negotiable Instruments Act, 1881."
    assert asyncio.run(append_judgments_cited(draft, lookup=_fake_lookup)) == draft


def test_existing_block_is_not_duplicated():
    once = asyncio.run(append_judgments_cited(LAWYER_DRAFT, lookup=_fake_lookup))
    assert asyncio.run(append_judgments_cited(once, lookup=_fake_lookup)) == once


def test_one_failing_lookup_marks_only_that_case_not_verified():
    def flaky(case):
        if case.petitioner == "Jacob Mathew":
            raise ConnectionError("search down")
        return _fake_lookup(case)
    out = asyncio.run(append_judgments_cited(LAWYER_DRAFT, lookup=flaky))
    assert f"*Jacob Mathew v. State of Punjab*, (2005) 6 SCC 1 — {NOT_VERIFIED}" in out
    assert "36301.pdf" in out


def test_overall_timeout_returns_the_draft_unchanged(monkeypatch):
    import time
    monkeypatch.setattr(jc, "TOTAL_TIMEOUT_S", 0.2)
    out = asyncio.run(append_judgments_cited(
        LAWYER_DRAFT, lookup=lambda c: time.sleep(1) or None))
    assert out == LAWYER_DRAFT


# ---------------------------------------------------------------------------
# Wiring through the real synthesis node
# ---------------------------------------------------------------------------

def test_drafting_solo_answer_gets_the_block(monkeypatch):
    from core.state import AgentResult
    from agents.orchestrator import orchestrator_synthesize_node

    monkeypatch.setattr(jc, "_lookup_one", _fake_lookup)
    state = {
        "original_query": "Draft a civil suit for medical negligence with case laws",
        "query": "Draft a civil suit for medical negligence with case laws",
        "task": "Drafting",
        "tasks_planned": ["Drafting"],
        "agent_results": {"Drafting": AgentResult(agent_name="Drafting", content=LAWYER_DRAFT)},
    }
    answer = asyncio.run(orchestrator_synthesize_node(state))["final_response"]
    assert answer.startswith(LAWYER_DRAFT.rstrip())
    assert HEADING in answer and "27088.pdf" in answer


def test_drafting_with_citation_agents_scans_only_the_draft(monkeypatch):
    from core.state import AgentResult
    from agents.orchestrator import orchestrator_synthesize_node

    seen = []
    monkeypatch.setattr(jc, "_lookup_one",
                        lambda c: seen.append(c.petitioner) or _fake_lookup(c))
    state = {
        "original_query": "Draft a civil suit for medical negligence with case laws",
        "query": "Draft a civil suit for medical negligence with case laws",
        "task": "Drafting",
        "tasks_planned": ["Drafting", "Judgment"],
        "agent_results": {
            "Drafting": AgentResult(agent_name="Drafting", content=LAWYER_DRAFT),
            "Judgment": AgentResult(
                agent_name="Judgment",
                content="Kusum Sharma v. Batra Hospital, (2010) 3 SCC 480 held that an error "
                        "of judgment is not negligence."),
        },
    }
    answer = asyncio.run(orchestrator_synthesize_node(state))["final_response"]
    assert HEADING in answer
    assert "Kusum Sharma" not in seen  # appendix content is not looked up
    assert answer.index(HEADING) < answer.index("REFERENCES & CITATIONS")
