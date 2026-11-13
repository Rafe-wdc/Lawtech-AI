"""Token blow-up in the High Court Judgment agent (2026-11-13).

Production probe on 12 Nov: the agent's generate call carried 792,536 input
tokens on one question and 176,046 on another (~6k each in September). The
`judgements` index holds ~3,900 whole Supreme Court judgments (up to 231k
chars) that out-score the ~2k-char High Court chunks, and `_process_hits`
appended every hit in full. Three guards, pinned here:

  A. every search the agent runs against the High Court index excludes
     court_name "supreme court" (kill switch HC_SEARCH_INCLUDE_SUPREME_COURT=1)
  B. the text handed to the model is capped per hit and in total; the source
     previews and the hit count are unchanged
  C. a failure of the Anthropic generation model is logged and counted, so a
     silent fallback to Gemini cannot pass for the configured model again

Pure tests, no network.
"""
from __future__ import annotations

import pytest

from tools.shared import judgment_search as js
from agents import judgment as jm


# ---------------------------------------------------------------- A: exclusion

def test_bool_query_gets_a_must_not_on_the_supreme_court_label(monkeypatch):
    monkeypatch.delenv("HC_SEARCH_INCLUDE_SUPREME_COURT", raising=False)
    body = {"size": 10, "query": {"bool": {"should": [{"match": {"page_content": "bail"}}],
                                           "filter": [{"term": {"year": 2024}}],
                                           "minimum_should_match": 1}}}
    out = js._without_supreme_court(body)
    assert out["query"]["bool"]["must_not"] == [{"match_phrase": {"court_name": "supreme court"}}]
    # everything else is preserved and the input is not mutated
    assert out["query"]["bool"]["should"] == body["query"]["bool"]["should"]
    assert out["query"]["bool"]["filter"] == body["query"]["bool"]["filter"]
    assert out["size"] == 10
    assert "must_not" not in body["query"]["bool"]


def test_existing_must_not_is_extended_not_replaced_and_not_duplicated(monkeypatch):
    monkeypatch.delenv("HC_SEARCH_INCLUDE_SUPREME_COURT", raising=False)
    other = {"term": {"case_type": "writ"}}
    body = {"query": {"bool": {"must_not": [other]}}}
    out = js._without_supreme_court(js._without_supreme_court(body))
    assert out["query"]["bool"]["must_not"] == [other, {"match_phrase": {"court_name": "supreme court"}}]


def test_non_bool_query_is_wrapped(monkeypatch):
    monkeypatch.delenv("HC_SEARCH_INCLUDE_SUPREME_COURT", raising=False)
    out = js._without_supreme_court({"query": {"match_all": {}}})
    assert out["query"]["bool"]["must"] == [{"match_all": {}}]
    assert out["query"]["bool"]["must_not"] == [{"match_phrase": {"court_name": "supreme court"}}]


def test_kill_switch_restores_the_old_query(monkeypatch):
    monkeypatch.setenv("HC_SEARCH_INCLUDE_SUPREME_COURT", "1")
    body = {"query": {"bool": {"should": []}}}
    assert js._without_supreme_court(body) is body


def test_every_strategy_sends_the_exclusion(monkeypatch):
    """The exclusion lives in `_es_search`, the one path every strategy uses."""
    monkeypatch.delenv("HC_SEARCH_INCLUDE_SUPREME_COURT", raising=False)
    sent = []

    class _FakeES:
        def search(self, index, body, request_timeout):
            sent.append(body)
            return {"hits": {"hits": []}}

    monkeypatch.setattr(js, "get_es_client", lambda: _FakeES())
    js.search_by_citation("2020 SCC 1")
    js.search_by_party_names("Ram", "Shyam", None, None, 5)
    js.search_by_case_type("bail", "anticipatory bail", None, None, 5)
    js.search_by_legal_provision("section 138", None, None, 5)
    assert len(sent) >= 4
    for body in sent:
        assert {"match_phrase": {"court_name": "supreme court"}} in body["query"]["bool"]["must_not"], body


# ---------------------------------------------------------------- A2: step-aside

def test_supreme_court_request_steps_aside_when_the_sci_agent_is_planned():
    state = {"tasks_planned": ["SCI_Judgment", "Judgment"], "agent_queries": {}}
    assert jm._supreme_court_route("Supreme Court", state) == "step_aside"
    assert jm._supreme_court_route("supreme court", {"agent_queries": {"SCI_Judgment": "q"}}) == "step_aside"


def test_supreme_court_request_goes_to_the_web_path_when_sci_is_not_planned():
    assert jm._supreme_court_route("supreme court", {"tasks_planned": ["Judgment"]}) == "web"
    assert jm._supreme_court_route("supreme court", {}) == "web"


def test_other_courts_are_not_rerouted():
    state = {"tasks_planned": ["SCI_Judgment", "Judgment"]}
    for court in (None, "", "bombay high court", "high court", "sikkim high court"):
        assert jm._supreme_court_route(court, state) is None


# ---------------------------------------------------------------- B: the cap

def _hit(content: str, **extra) -> dict:
    return {"content": content, "court_name": "bombay high court", "source": "x.pdf",
            "petitioner_names": ["A"], "respondent_names": ["B"], "score": 12.0, **extra}


def test_ordinary_high_court_chunks_pass_untouched():
    hits = [_hit("word " * 400) for _ in range(10)]        # 2k chars each
    parts, sources = jm._process_hits(hits)
    assert [p for p in parts] == [h["content"] for h in hits]
    assert len(sources) == 10


def test_a_whole_judgment_is_cut_to_the_per_hit_cap_with_a_note():
    big = "x" * 231_000
    parts, sources = jm._process_hits([_hit(big)])
    assert len(parts[0]) < jm._HIT_MAX_CHARS + 200
    assert parts[0].startswith("x" * 1000)
    assert "truncated for length" in parts[0] and "219,000 more characters" in parts[0]
    # the preview shown to the user is still taken from the original text
    assert sources[0].content == [big[:300]]


def test_total_budget_bounds_ten_large_hits():
    hits = [_hit("y" * 50_000) for _ in range(10)]
    parts, sources = jm._process_hits(hits)
    total = sum(len(p) for p in parts)
    assert total <= jm._DOCS_MAX_CHARS + 10 * 120   # ten truncation notes at most
    assert len(parts) == len(sources) == 10          # no hit is dropped, only trimmed
    assert parts[0].startswith("y" * 1000)
    assert parts[-1] == "" or "truncated" in parts[-1]


def test_cap_helper_arithmetic():
    text, dropped = jm._cap_doc_text("abc", 100)
    assert (text, dropped) == ("abc", 0)
    text, dropped = jm._cap_doc_text("a" * 20_000, 5_000)
    assert dropped == 15_000 and text.startswith("a" * 5_000) and "15,000" in text
    text, dropped = jm._cap_doc_text("a" * 10, 0)
    assert (text, dropped) == ("", 10)


# ---------------------------------------------------------------- C: fallback signal

def test_anthropic_failure_is_logged_and_counted(monkeypatch):
    from core import clients
    from core.metrics import METRICS
    counter = METRICS["fallback_total"].labels(agent="generation", tier="anthropic_to_gemini")
    before = counter._value.get()
    logged = []
    monkeypatch.setattr(clients._log, "warning", lambda msg, **kw: logged.append((msg, kw)))
    clients._AnthropicFallbackSignal().on_llm_error(ValueError("credit balance is too low"), run_id=None)
    assert counter._value.get() == before + 1
    assert logged and logged[0][1]["error_class"] == "ValueError"
    assert "Gemini fallback" in logged[0][0]


def test_signal_handler_is_a_real_callback_handler_with_no_ops_elsewhere():
    import uuid
    from langchain_core.callbacks import BaseCallbackHandler
    h = __import__("core.clients", fromlist=["_AnthropicFallbackSignal"])._AnthropicFallbackSignal()
    assert isinstance(h, BaseCallbackHandler)
    assert h.on_llm_start({}, ["p"], run_id=uuid.uuid4()) is None
    assert h.on_llm_end(None, run_id=uuid.uuid4()) is None
    with pytest.raises(AttributeError):
        h.not_a_callback
