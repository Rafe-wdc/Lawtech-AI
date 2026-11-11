"""Section-specific case-law requests answer from the judgments that name the section.

BACKGROUND (2026-11-10)
=======================
"Provide me 50 Case laws or Citation u/s 154B-29 of Maharashtra Co-operative
Society act 1960" returned 33 cases on prod. One was about Section 154B-29;
the rest were older Section 101 cases, housing-society cases on other points
and unrelated Supreme Court judgments (an assassination appeal, an MTP
petition, an Arms Act prosecution) each with an invented line on why it
applied. The High Court search had treated "154B-29" as loose words, and the
writer padded the list to approach 50.

These tests pin: reading the asked section; the exact-phrase spellings; the
checked list (complete only when the database holds every match in one
page); the block and rules handed to the writer; the orchestrator wiring;
duplicate removal and the exact-section strategy in the High Court search.
No network: the search client is faked. Party names are invented.
"""
from __future__ import annotations

import asyncio

import pytest

import core.section_cases as sc
from core.section_cases import (
    SectionAsk, SectionJudgment, asked_section, find_section_judgments, format_block,
    phrase_variants, section_in, to_sources,
)


# ---------------------------------------------------------------------------
# Reading the request
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query, section, act, count", [
    ("Provide me 50 Case laws or Citation u/s 154B-29 of Maharashtra Copreative Socitey act 1960",
     "154B-29", "Maharashtra Copreative Socitey act 1960", 50),
    ("case laws on Section 13(1)(ia) of the Hindu Marriage Act mental cruelty",
     "13(1)(ia)", "Hindu Marriage Act", None),
    ("landmark judgments on Section 498A IPC misuse", "498A", "", None),
    ("give 10 judgments under sec. 52A of the NDPS Act", "52A", "NDPS Act", 10),
    ("Supreme Court cases under Section 138 of the Negotiable Instruments Act",
     "138", "Negotiable Instruments Act", None),
])
def test_the_asked_section_is_read(query, section, act, count):
    ask = asked_section(query)
    assert ask is not None
    assert (ask.section, ask.act, ask.count) == (section, act, count)


@pytest.mark.parametrize("query", [
    "What is the punishment for cheating under BNS?",           # no section
    "Explain Section 154B-29 of the MCS Act",                   # not a case-law ask
    "cases under section 138",                                  # bare number, no act
])
def test_no_section_case_list_request(query):
    assert asked_section(query) is None


def test_section_in_needs_no_case_law_words_but_a_distinctive_number():
    assert section_in("Section 154B-29 of the Maharashtra Co-operative Societies Act").section == "154B-29"
    assert section_in("Section 138 of the NI Act") is None


def test_phrase_spellings_cover_what_the_analyzer_sees():
    assert phrase_variants("154B-29") == ["154 B-29", "154B-29"]      # [154,b,29] and [154b,29]
    assert phrase_variants("13(1)(ia)") == ["13(1)(ia)"]
    assert phrase_variants("138") == ["s 138", "sec 138", "section 138"]


@pytest.mark.parametrize("text, hit", [
    ("passed under Section 154B-29 of the Act", True),
    ("under Section 154-B-29 of M.C.S. Act", True),
    ("u/s 154 B-29", True),
    ("Section 1154B-29", False),
    ("Section 154B-290", False),
])
def test_mention_regex(text, hit):
    assert bool(sc._mention_re("154B-29").search(text)) is hit


# ---------------------------------------------------------------------------
# The checked list, against a fake search client
# ---------------------------------------------------------------------------

class _FakeES:
    """Answers the count probe with `totals[index]` and the fetch with `docs[index]`."""

    def __init__(self, totals, docs, fail=()):
        self.totals, self.docs, self.fail, self.calls = totals, docs, set(fail), []

    def search(self, index, body, request_timeout=None):
        self.calls.append((index, body.get("size")))
        if index in self.fail:
            raise ConnectionError("search down")
        if body.get("size") == 0:
            return {"hits": {"total": {"value": self.totals.get(index, 0)}, "hits": []}}
        return {"hits": {"total": {"value": len(self.docs.get(index, []))},
                         "hits": self.docs.get(index, [])}}


def _hc(pet, resp, date, case_no, passage):
    return {"_id": f"{pet}-{date}", "_score": 3.0,
            "_source": {"petitioner_names": [pet], "respondent_names": [resp],
                        "court_name": "bombay high court", "judgment_date": date,
                        "source": f"bombay/{case_no}.pdf", "case_number": case_no},
            "highlight": {"page_content": [passage]}}


_HC_DOCS = [
    _hc("Sunrise CHS Ltd.", "Deputy Registrar", "2024-03-19", "2221",
        "refusing to issue a certificate under Section 154B-29 of the Act"),
    _hc("Sunrise CHS Ltd.", "Deputy Registrar", "2024-03-19", "2221",       # exact copy
        "refusing to issue a certificate under Section 154B-29 of the Act"),
    _hc("Meera Kulkarni", "State of Maharashtra", "2024-02-06", "4149",
        "order passed by the Deputy Registrar under Section 154-B-29"),
]


@pytest.fixture
def no_links(monkeypatch):
    import tools.shared.storage_tools as st

    class _NoLink:
        @staticmethod
        def invoke(_args):
            return "https://example.invalid/judgment.pdf"
    monkeypatch.setattr(st, "generate_s3_link", _NoLink)


def _use(monkeypatch, fake):
    import core.clients
    monkeypatch.setattr(core.clients, "get_es_client", lambda *a, **k: fake)


def test_complete_set_is_returned_once_per_judgment(monkeypatch, no_links):
    fake = _FakeES(totals={"judgements": 3, "supreme_court_judgement": 0},
                   docs={"judgements": _HC_DOCS})
    _use(monkeypatch, fake)
    found, complete = find_section_judgments(SectionAsk("154B-29", "MCS Act", 50))
    assert complete is True
    assert [j.title for j in found] == ["Sunrise CHS Ltd. v. Deputy Registrar",
                                        "Meera Kulkarni v. State of Maharashtra"]
    assert "154B-29" in found[0].snippet and found[0].court == "Bombay High Court"


def test_a_common_section_is_not_the_complete_set(monkeypatch, no_links):
    fake = _FakeES(totals={"judgements": 5000, "supreme_court_judgement": 900}, docs={})
    _use(monkeypatch, fake)
    found, complete = find_section_judgments(SectionAsk("498A", "", None))
    assert complete is False and found == []
    assert all(size == 0 for _, size in fake.calls)       # nothing fetched, only counted


def test_a_failed_search_makes_the_list_incomplete(monkeypatch, no_links):
    fake = _FakeES(totals={"judgements": 3}, docs={"judgements": _HC_DOCS},
                   fail={"supreme_court_judgement"})
    _use(monkeypatch, fake)
    found, complete = find_section_judgments(SectionAsk("154B-29", "", None))
    assert complete is False


def test_block_states_the_count_and_the_rules():
    found = [SectionJudgment("Sunrise CHS Ltd. v. Deputy Registrar", "Bombay High Court",
                             "2024-03-19", None, "certificate under Section 154B-29")]
    block = format_block(SectionAsk("154B-29", "Maharashtra Co-operative Societies Act", 50), found)
    assert "MENTION SECTION 154B-29" in block
    assert "found: 1. The user asked for 50." in block
    assert "fewer than the 50 asked for" in block
    assert "Never pad the list" in block
    assert "Sunrise CHS Ltd. v. Deputy Registrar" in block


def test_block_for_no_judgments_says_so():
    block = format_block(SectionAsk("154B-29", "", 50), [])
    assert "None. No judgment in our database mentions this section." in block


def test_sources_are_registered_with_their_links():
    found = [SectionJudgment("A v. B", "Supreme Court of India", "12-01-2021", "https://x/1.pdf", "s. 498A"),
             SectionJudgment("C v. D", "Bombay High Court", "2024-03-19", None, "154B-29")]
    recs = to_sources(found)
    assert [r.type for r in recs] == ["sci_judgment", "judgment"]
    assert recs[0].pdf_urls == ["https://x/1.pdf"] and recs[0].year == "2021"
    assert all(r.id.startswith("sec-") for r in recs)


# ---------------------------------------------------------------------------
# Orchestrator wiring
# ---------------------------------------------------------------------------

def _run_wiring(monkeypatch, query, result):
    from agents import orchestrator as o
    from core.source_registry import RetrievedSource, SourceRegistry
    monkeypatch.setattr(sc, "find_section_judgments", lambda ask: result)
    existing = SourceRegistry()
    existing.add(RetrievedSource(id="sci-1", agent="SCI_Judgment", type="sci_judgment",
                                 canonical_citation="Unrelated v. State"))
    return asyncio.run(o._with_section_case_list(query, "### SCI_JUDGMENT AGENT RESULTS:\n...", existing))


def test_wiring_appends_the_block_and_puts_checked_sources_first(monkeypatch):
    found = [SectionJudgment("Sunrise CHS Ltd. v. Deputy Registrar", "Bombay High Court",
                             "2024-03-19", None, "certificate under Section 154B-29")]
    text, reg = _run_wiring(monkeypatch, "Provide me 50 case laws u/s 154B-29 of the MCS Act 1960",
                            (found, True))
    assert "JUDGMENTS IN OUR DATABASE THAT MENTION SECTION 154B-29" in text
    ids = [r.id for r in reg.all()]
    assert ids[0].startswith("sec-hc-") and "sci-1" in ids


def test_wiring_leaves_an_incomplete_list_out(monkeypatch):
    text, reg = _run_wiring(monkeypatch, "landmark judgments on Section 498A IPC", ([], False))
    assert "JUDGMENTS IN OUR DATABASE" not in text
    assert [r.id for r in reg.all()] == ["sci-1"]


def test_wiring_ignores_questions_without_a_section(monkeypatch):
    text, reg = _run_wiring(monkeypatch, "What is anticipatory bail?", (["never used"], True))
    assert text == "### SCI_JUDGMENT AGENT RESULTS:\n..."


def test_wiring_survives_a_failure(monkeypatch):
    from agents import orchestrator as o
    from core.source_registry import SourceRegistry

    def boom(ask):
        raise RuntimeError("index down")
    monkeypatch.setattr(sc, "find_section_judgments", boom)
    text, reg = asyncio.run(o._with_section_case_list(
        "50 case laws u/s 154B-29 of the MCS Act", "results", SourceRegistry()))
    assert text == "results"


def test_writer_and_reviewer_rules_are_in_place():
    import inspect
    from langchain_core.prompts import ChatPromptTemplate
    import core.self_refine as sr
    from config.prompts import SYNTHESIS_PROMPT
    assert "SECTION CASE LISTS ARE CHECKED, NOT FILLED" in SYNTHESIS_PROMPT
    assert sorted(ChatPromptTemplate.from_template(SYNTHESIS_PROMPT).input_variables) == [
        "agent_results", "query", "response_instructions", "retrieved_sources"]
    assert "never grounds for adding cases" in inspect.getsource(sr)


# ---------------------------------------------------------------------------
# High Court search: duplicates and the exact-section strategy
# ---------------------------------------------------------------------------

def _raw(pet, date, case_no, _id):
    return {"_id": _id, "_score": 2.0,
            "_source": {"page_content": f"judgment {case_no}", "petitioner_names": [pet],
                        "respondent_names": ["State"], "court_name": "bombay high court",
                        "judgment_date": date, "case_number": case_no}}


def test_exact_copies_are_dropped_from_search_results():
    from tools.shared.judgment_search import _format_hits
    hits = [_raw("Ravi Shah", "2020-11-06", "5792020", "a"), _raw("Ravi Shah", "2020-11-06", "5792020", "b"),
            _raw("Ravi Shah", "2020-11-06", "5792020", "c"), _raw("Asha Rao", "2023-07-21", "72982023", "d")]
    assert [h["petitioner_names"][0] for h in _format_hits(hits)] == ["Ravi Shah", "Asha Rao"]


def test_records_without_metadata_are_not_merged():
    from tools.shared.judgment_search import _format_hits
    bare = [{"_id": i, "_score": 1.0, "_source": {"page_content": f"t{i}"}} for i in ("x", "y")]
    assert len(_format_hits(bare)) == 2


def test_section_phrase_strategy_returns_the_complete_set(monkeypatch):
    import tools.shared.judgment_search as js
    hits = [_raw("Sunrise CHS Ltd.", "2024-03-19", "2221", "a"), _raw("Meera Kulkarni", "2024-02-06", "4149", "b")]
    fake = _FakeES(totals={js.INDEX: 2}, docs={js.INDEX: hits})
    monkeypatch.setattr(js, "get_es_client", lambda *a, **k: fake)
    r = js.smart_judgment_search("judgments under Section 154B-29 of the MCS Act", size=5)
    assert r["strategy_used"] == "section_phrase"
    assert len(r["hits"]) == 2


def test_section_phrase_strategy_steps_aside_for_a_common_section(monkeypatch):
    import tools.shared.judgment_search as js
    fake = _FakeES(totals={js.INDEX: 9999}, docs={})
    monkeypatch.setattr(js, "get_es_client", lambda *a, **k: fake)
    results, complete = js.search_by_section_phrase("498A", "quashing on settlement")
    assert results == [] and complete is False
    assert [size for _, size in fake.calls] == [0]          # counted, nothing fetched


def test_both_synthesis_paths_use_the_checked_list():
    """Case-law requests reach the generic synthesis or the primary-task merge
    depending on the plan; both must hand the writer the checked list."""
    import inspect
    from agents import orchestrator as o
    assert inspect.getsource(o.orchestrator_synthesize_node).count("await _with_section_case_list(") == 2
