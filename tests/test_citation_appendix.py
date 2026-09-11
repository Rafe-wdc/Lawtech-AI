"""The REFERENCES & CITATIONS appendix is never a second draft.

Advocate report 2026-09-11 on an SLP: the appendix under
'### SCI_JUDGMENT CITATIONS:' was a complete second Special Leave Petition
(cause title, synopsis, list of dates, questions of law, grounds). The
citation agent, handed a drafting-shaped query, obeyed the shared rule
'if the user asks for a draft, return ONLY the draft', and the append-only
synthesis glued its whole answer in. Pure tests.
"""
from __future__ import annotations

from types import SimpleNamespace

from agents.orchestrator import (
    _CITATIONS_ONLY_PREFIX,
    _citation_appendix_text,
    _is_draft_shaped,
    _sources_as_citation_list,
)

SECOND_SLP = (
    "IN THE SUPREME COURT OF INDIA [CRIMINAL APPELLATE JURISDICTION] SPECIAL LEAVE PETITION (CRIMINAL) NO. ____ OF 2024\n\n"
    "IN THE MATTER OF: [Petitioner's Name] ... PETITIONER\n\nVERSUS\n\nState of [Name of State] ... RESPONDENTS\n\n"
    "SYNOPSIS AND LIST OF DATES\nThe present Special Leave Petition ...\n\nQUESTIONS OF LAW\n1. Whether ...\n\nGROUNDS\nI. ...\n"
)
CITATION_LIST = (
    "1. Abdul Rehman Antulay v. R.S. Nayak, (1992) 1 SCC 225 - speedy trial is part of Article 21. (Judgment PDF)\n"
    "2. P. Ramachandra Rao v. State of Karnataka, (2002) 4 SCC 578 - no outer time limits, but delay is a ground. (Judgment PDF)\n"
    "3. Neeraj Dutta v. State (NCT of Delhi), (2023) 4 SCC 731 - demand and acceptance are sine qua non under ss. 7 and 13(1)(d).\n"
)


def _src(title, court=None, year=None, link=None):
    return SimpleNamespace(title=title, court_name=court, year=year, doc_link=link)


def test_second_pleading_is_draft_shaped():
    assert _is_draft_shaped(SECOND_SLP) is True


def test_citation_list_is_not_draft_shaped():
    assert _is_draft_shaped(CITATION_LIST) is False


def test_overlong_content_is_treated_as_draft_shaped():
    assert _is_draft_shaped("- Case v. State, (2020) 1 SCC 1\n" * 300) is True


def test_reported_shape_is_replaced_by_structured_sources():
    result = SimpleNamespace(content=SECOND_SLP, sources=[
        _src("Abdul Rehman Antulay v. R.S. Nayak", "Supreme Court of India", 1992, "https://api.sci.gov.in/jonew/judis/1.pdf"),
        _src("Neeraj Dutta v. State (NCT of Delhi)", "Supreme Court of India", 2023, None),
        _src("Abdul Rehman Antulay v. R.S. Nayak", "Supreme Court of India", 1992, None),   # duplicate
    ])
    out = _citation_appendix_text("SCI_Judgment", result)
    assert "SYNOPSIS" not in out and "QUESTIONS OF LAW" not in out and "VERSUS" not in out
    assert out.count("Abdul Rehman Antulay") == 1
    assert "- Abdul Rehman Antulay v. R.S. Nayak, Supreme Court of India, 1992 ([Judgment PDF](https://api.sci.gov.in/jonew/judis/1.pdf))" in out
    assert "- Neeraj Dutta v. State (NCT of Delhi), Supreme Court of India, 2023" in out


def test_citation_shaped_content_is_kept_verbatim():
    result = SimpleNamespace(content=CITATION_LIST, sources=[])
    assert _citation_appendix_text("SCI_Judgment", result) == CITATION_LIST.strip()


def test_draft_shaped_content_with_no_sources_yields_nothing():
    result = SimpleNamespace(content=SECOND_SLP, sources=[])
    assert _citation_appendix_text("SCI_Judgment", result) == ""


def test_sources_render_without_optional_fields():
    out = _sources_as_citation_list(SimpleNamespace(sources=[_src("X v. Y")]))
    assert out == "- X v. Y"


def test_citations_only_directive_forbids_drafting():
    assert "Do NOT draft" in _CITATIONS_ONLY_PREFIX and "CITATIONS ONLY" in _CITATIONS_ONLY_PREFIX


def test_plan_node_wraps_citation_agents_when_drafting_is_primary():
    import inspect
    import agents.orchestrator as orch
    src = inspect.getsource(orch)
    assert 'agent_queries[_ca] = _CITATIONS_ONLY_PREFIX + _base[:1500]' in src
    assert 'if task == "Drafting":' in src
