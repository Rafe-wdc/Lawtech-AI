"""Tribunal orders: the tribunal_judgements index, its search tools, the
Tribunal_Judgment agent, routing, and the ingest scripts' pure helpers.

No network: OpenSearch and the LLM are faked.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "tribunal"))

from config.tribunals import TRIBUNAL_BY_CODE, tribunal_for_source  # noqa: E402
import tools.shared.tribunal_tools as tt  # noqa: E402


# --- tribunal table ----------------------------------------------------------

@pytest.mark.parametrize("source, code", [
    ("tribunal\\Customs, Excise and Gold Tribunal\\1998\\x.PDF", "CESTAT"),
    ("tribunal\\cestat\\M_S_Jindal_Saw_Ltd.PDF", "CESTAT"),
    ("tribunal\\Central Administrative Tribunal\\2010\\x.PDF", "CAT"),
    ("tribunal\\Authority Tribunal\\1999\\x.PDF", "AAR"),
    ("tribunal\\Consumer Disputes Redressal\\2001\\x.PDF", "CDRC"),
    ("tribunal/National Green Tribunal/2015/x.PDF", "NGT"),
    ("tribunal\\Copyright Board\\2005\\x.PDF", "CB"),
    ("tribunal\\weird", "OTHER"),
    ("", "OTHER"),
])
def test_tribunal_for_source(source, code):
    assert tribunal_for_source(source).code == code


@pytest.mark.parametrize("value, code", [
    ("TDSAT", "TDSAT"), ("tdsat", "TDSAT"), ("NGT", "NGT"),
    ("National Green Tribunal", "NGT"), ("green tribunal", "NGT"),
    ("Telecom Disputes Settlement and Appellate Tribunal", "TDSAT"),
    ("CEGAT", "CESTAT"), ("Customs, Excise and Gold Tribunal", "CESTAT"),
    ("Central Information Commission", "CIC"),
])
def test_resolve_tribunal(value, code):
    assert tt.resolve_tribunal(value).code == code


@pytest.mark.parametrize("value", [None, "", "  ", "Bombay High Court"])
def test_resolve_tribunal_unknown_means_no_filter(value):
    assert tt.resolve_tribunal(value) is None
    assert tt._filters(value) == []


def test_order_title_fallbacks():
    assert tt.order_title({"title": "A vs B"}) == "A vs B"
    assert tt.order_title({"petitioner_names": ["Almitra H. Patel"],
                           "respondent_names": ["Union of India"]}) == "Almitra H. Patel vs Union of India"
    assert tt.order_title({"petitioner_names": ["Almitra H. Patel"],
                           "respondent_names": ["Union of India", "State of Sikkim", "CPCB"]}
                          ) == "Almitra H. Patel vs Union of India & Ors."
    # migrated documents with no respondent: the file name carries the title
    assert tt.order_title({
        "petitioner_names": ["Chandrika Prasad Sonkar"],
        "source": "tribunal\\National Green Tribunal\\2014\\"
                  "Chandrika_Prasad_Sonkar_vs_Union_Of_India_on_7_May_2014.PDF",
    }) == "Chandrika Prasad Sonkar vs Union Of India"


# --- search tools ------------------------------------------------------------

class _FakeES:
    def __init__(self, hits=None, doc=None):
        self.hits, self.doc, self.bodies = hits or [], doc, []

    def search(self, index=None, body=None, **_):
        self.index = index
        self.bodies.append(body)
        return {"hits": {"hits": list(self.hits)}}

    def get(self, index=None, id=None, **_):
        self.index = index
        return {"_id": id, "_source": self.doc}


TDSAT_SRC = {
    "title": "KAL CABLES PVT. LTD VS J.K.R. NETWORK",
    "tribunal_name": "Telecom Disputes Settlement and Appellate Tribunal",
    "tribunal_code": "TDSAT",
    "case_no": "BROADCASTING PETITION/252/2017",
    "judgment_date": "2026-10-12",
    "bench_judge_names": ["Justice Ram Krishna Gautam"],
    "pdf_links": [{"label": "PDF (S3)", "url": "https://lawttorney.s3.ap-south-1.amazonaws.com/"
                                               "tribunal/tdsat/0001_BP_252_2017.pdf"}],
}


@pytest.fixture
def es(monkeypatch):
    fake = _FakeES(hits=[{"_id": "tdsat__070110005102017_1640", "_score": 7.5,
                          "_source": TDSAT_SRC,
                          "highlight": {"full_text": ["**carriage fee** was payable"]}}],
                   doc={**TDSAT_SRC, "full_text": "ORDER ... carriage fee ..."})
    monkeypatch.setattr(tt, "get_es_client", lambda: fake)
    return fake


def test_topic_search_filters_by_tribunal_and_year(es):
    out = tt.tribunal_search_by_topic.invoke(
        {"query": "carriage fee", "tribunal": "TDSAT", "year_from": 2020, "year_to": 2026})
    body = es.bodies[0]
    assert es.index == "tribunal_judgements"
    assert {"term": {"tribunal_code": "TDSAT"}} in body["query"]["bool"]["filter"]
    assert {"range": {"year": {"gte": 2020, "lte": 2026}}} in body["query"]["bool"]["filter"]
    assert body["_source"] == {"excludes": ["full_text"]}
    assert "Tribunal: Telecom Disputes Settlement and Appellate Tribunal (TDSAT)" in out
    assert "Bench: Justice Ram Krishna Gautam" in out
    assert "carriage fee" in out


def test_topic_search_without_tribunal_has_no_filter(es):
    tt.tribunal_search_by_topic.invoke({"query": "carriage fee"})
    assert es.bodies[0]["query"]["bool"]["filter"] == []


def test_other_tools_build_queries(es):
    tt.tribunal_search_by_keyword.invoke({"query": "Section 14 TRAI Act", "tribunal": "TDSAT"})
    tt.tribunal_search_by_case_number.invoke({"case_number": "252/2017"})
    tt.tribunal_search_by_party_name.invoke({"party_name": "Kal Cables"})
    tt.tribunal_search_by_member.invoke({"member_name": "S.B. Sinha", "query": "interconnection"})
    assert len(es.bodies) == 4
    assert all(b["_source"] == {"excludes": ["full_text"]} for b in es.bodies)


def test_search_error_is_reported_not_raised(monkeypatch):
    class Boom:
        def search(self, **_):
            raise ConnectionError("cluster down")
    monkeypatch.setattr(tt, "get_es_client", lambda: Boom())
    assert "Tribunal topic search error" in tt.tribunal_search_by_topic.invoke({"query": "x"})


# --- agent: parsing tool output into sources ---------------------------------

def test_tool_output_parses_into_tribunal_sources(es):
    from agents.tribunal_judgment import _parse_tribunal_blocks

    found = _parse_tribunal_blocks(tt.tribunal_search_by_topic.invoke({"query": "carriage fee"}))
    detail = _parse_tribunal_blocks(
        tt.tribunal_get_case_details.invoke({"db_id": "tdsat__070110005102017_1640"}))
    for sources in (found, detail):
        assert len(sources) == 1
        s = sources[0]
        assert s.source_type == "tribunal_judgment"
        assert s.db_id == "tdsat__070110005102017_1640"
        assert s.court_name == "Telecom Disputes Settlement and Appellate Tribunal"
        assert s.year == 2026
        assert s.doc_link.startswith("https://lawttorney.s3.")
        assert s.agent_name == "Tribunal_Judgment"
    assert found[0].case_no == "BROADCASTING PETITION/252/2017"


def test_tribunal_source_survives_the_url_whitelist(es):
    """The app drops sources whose PDF is not on our S3 bucket."""
    from dataclasses import asdict
    from agents.tribunal_judgment import _parse_tribunal_blocks
    from core.url_filter import sanitize_source_records

    s = _parse_tribunal_blocks(tt.tribunal_search_by_topic.invoke({"query": "x"}))[0]
    assert len(sanitize_source_records([asdict(s)])) == 1


def test_node_returns_answer_and_sources(es, monkeypatch):
    """End to end through the node with a fake ReAct agent that searched."""
    from langchain_core.messages import AIMessage, ToolMessage
    import agents.tribunal_judgment as agent_mod

    tool_text = tt.tribunal_search_by_topic.invoke({"query": "carriage fee"})

    class _FakeAgent:
        async def ainvoke(self, _):
            return {"messages": [
                AIMessage(content="", tool_calls=[{"name": "tribunal_search_by_topic",
                                                   "args": {"query": "carriage fee"}, "id": "1"}]),
                ToolMessage(content=tool_text, tool_call_id="1"),
                AIMessage(content="TDSAT held that carriage fee was payable."),
            ]}

    monkeypatch.setattr(agent_mod, "get_gemini_flash_full", lambda **_: object())
    monkeypatch.setattr(agent_mod, "create_react_agent", lambda *a, **k: _FakeAgent())
    out = asyncio.run(agent_mod.tribunal_judgment_node(
        {"query": "TDSAT orders on carriage fee", "user_language": "en"}))
    result = out["agent_results"]["Tribunal_Judgment"]
    assert result.error is None
    assert "carriage fee" in result.content
    assert [s.db_id for s in result.sources] == ["tdsat__070110005102017_1640"]


def test_node_error_is_recorded(monkeypatch):
    import agents.tribunal_judgment as agent_mod

    def boom(**_):
        raise RuntimeError("llm down")
    monkeypatch.setattr(agent_mod, "get_gemini_flash_full", boom)
    out = asyncio.run(agent_mod.tribunal_judgment_node({"query": "x"}))
    result = out["agent_results"]["Tribunal_Judgment"]
    assert result.error and result.content == ""


# --- routing -----------------------------------------------------------------

def _intent(**kw):
    from config.intent import UserIntent
    return UserIntent(confidence=0.9, task_intent="lookup", **kw)


def test_intent_routes_to_tribunal_agent():
    from agents.orchestrator import (_classify_task_from_intent, _detect_multi_intent,
                                     _validate_and_enrich_plan)
    from core.logger import get_logger

    intent = _intent(wants_tribunal_rulings=True)
    assert _classify_task_from_intent(intent) == "Tribunal_Judgment"
    assert "Tribunal_Judgment" in _detect_multi_intent(intent, "Legislation")
    assert "Tribunal_Judgment" not in _detect_multi_intent(intent, "Tribunal_Judgment")
    assert _validate_and_enrich_plan(["Legislation"], intent, get_logger("t")) == [
        "Legislation", "Tribunal_Judgment"]


def test_gst_still_wins_over_tribunal_for_task():
    from agents.orchestrator import _classify_task_from_intent
    assert _classify_task_from_intent(
        _intent(wants_gst_rulings=True, wants_tribunal_rulings=True)) == "GST_Judgment"


def test_agent_is_wired_into_graph_and_tools():
    from config.intent import TASK_TYPES
    from core.graph import AGENT_NODE_MAP
    from tools.shared import AGENT_TOOLS

    assert AGENT_NODE_MAP["Tribunal_Judgment"] == "tribunal_judgment"
    assert "Tribunal_Judgment" in TASK_TYPES
    assert {t.name for t in AGENT_TOOLS["tribunal_judgment"]} == {
        "tribunal_search_by_topic", "tribunal_search_by_keyword",
        "tribunal_search_by_case_number", "tribunal_search_by_party_name",
        "tribunal_search_by_member", "tribunal_get_case_details",
    }


# --- ingest scripts ----------------------------------------------------------

def test_migration_tags_documents_from_their_source_path():
    from tribunal_index import tag_document

    doc = tag_document({"source": "tribunal\\cestat\\x.PDF", "court_name": "Tribunal", "case_no": "1"})
    assert doc["tribunal_code"] == "CESTAT"
    assert doc["tribunal_name"] == TRIBUNAL_BY_CODE["CESTAT"].name
    assert doc["court_name"] == "tribunal" and doc["case_no"] == "1"


@pytest.mark.parametrize("raw, names", [
    ("HON BLE MR. JUSTICE S.B.SINHA, CHAIRPERSON HON BLE MR. P.K.RASTOGI, MEMBER",
     ["Justice S.B. Sinha", "P.K. Rastogi"]),
    ("MR. SUBODH KUMAR GUPTA(MEMBER),MR. JUSTICE SHIVA KIRTI SINGH(CHAIRPERSON)",
     ["Subodh Kumar Gupta", "Justice Shiva Kirti Singh"]),
    ("MR. JUSTICE N.SANTOSH HEGDE, MR. VINOD V AISH, MEMBER L T .GEN. D.P. SEHGAL (RETD)",
     ["Justice N. Santosh Hegde", "Vinod Vaish", "Lt. Gen. D.P. Sehgal (Retd.)"]),
    ("MR. JUSTICE SUHAS C. SEN, CHAIRPERSON MR. R.U.S. PRASAD, MEMBER MR. P.R. DASTUPTA, MEMBER",
     ["Justice Suhas C. Sen", "R.U.S. Prasad", "P.R. Dasgupta"]),
    ("", []),
])
def test_tdsat_bench_parsing(raw, names):
    from build_tdsat_docs import parse_bench
    assert parse_bench(raw) == names


def test_tdsat_document_links_to_our_s3_copy():
    from build_tdsat_docs import iso_date, with_s3_link

    doc = with_s3_link({"source": "tribunal\\TDSAT\\2026\\0001_BROADCASTING PETITION_252.pdf",
                        "pdf_links": []})
    assert doc["s3_key"] == "tribunal/tdsat/0001_BROADCASTING PETITION_252.pdf"
    assert doc["pdf_links"][0]["url"].endswith("/tribunal/tdsat/0001_BROADCASTING%20PETITION_252.pdf")
    assert iso_date("12-10-2026") == "2026-10-12"
    assert iso_date("bad") is None
