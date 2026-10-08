"""Supreme Court agent: a case-name lookup whose parties are not in the index
returns "not found" without the ReAct research loop (cost audit 2026-10-08).
Pure tests; ES is mocked.
"""
from __future__ import annotations

import pytest

import agents.sci_judgment as sj


@pytest.mark.parametrize("q,pet,resp", [
    ("Laxman B. Kamble vs MHADA and ors.", "Laxman B. Kamble", "MHADA and ors"),
    ("Korshi Virpar Dadhlia Shah vs State of Maharashtra and ors.", "Korshi Virpar Dadhlia Shah", "State of Maharashtra and ors"),
    ("Arnesh Kumar versus State of Bihar 2014", "Arnesh Kumar", "State of Bihar"),
    ("arnesh kumar v. state of bihar", "arnesh kumar", "state of bihar"),
])
def test_case_name_queries_are_recognised(q, pet, resp):
    assert sj._case_name_lookup(q) == (pet, resp)


@pytest.mark.parametrize("q", [
    "Supreme Court judgments on anticipatory bail under Section 438 CrPC",
    "What did the court hold in cases on dowry death vs cruelty distinction and how do I argue it?",
    "Draft a bail application for my client vs the State with case laws\nFacts: ...",
    "",
])
def test_topic_and_long_queries_are_not_lookups(q):
    assert sj._case_name_lookup(q) is None


def test_significant_tokens_drop_initials_honorifics_and_state_words():
    assert sj._significant_party_tokens("Laxman B. Kamble") == ["laxman", "kamble"]
    assert sj._significant_party_tokens("State of Maharashtra and ors.") == ["maharashtra"]
    assert sj._significant_party_tokens("M/s. Shah & Co. Pvt. Ltd.") == ["shah"]
    assert sj._significant_party_tokens("Union of India") == []


class _ES:
    def __init__(self, total):
        self.total = total
        self.bodies = []

    def search(self, index=None, body=None, **kw):
        self.bodies.append(body)
        return {"hits": {"total": {"value": self.total}, "hits": []}}


def test_gate_uses_an_and_match_on_parties(monkeypatch):
    es = _ES(total=0)
    monkeypatch.setattr("core.clients.get_es_client", lambda: es)
    assert sj._party_in_sci_index("Korshi Virpar Dadhlia Shah", "State of Maharashtra") is False
    q = es.bodies[0]["query"]["match"]["parties"]
    assert q["operator"] == "and" and q["query"] == "korshi virpar dadhlia shah"


def test_gate_falls_back_to_respondent_when_petitioner_is_the_state(monkeypatch):
    es = _ES(total=3)
    monkeypatch.setattr("core.clients.get_es_client", lambda: es)
    assert sj._party_in_sci_index("State of Maharashtra", "Tulshiram Bhanudas Kamble") is True
    assert es.bodies[0]["query"]["match"]["parties"]["query"] == "tulshiram bhanudas kamble"


def test_gate_fails_open_on_es_error_or_no_tokens(monkeypatch):
    class _Boom:
        def search(self, **kw):
            raise RuntimeError("down")
    monkeypatch.setattr("core.clients.get_es_client", lambda: _Boom())
    assert sj._party_in_sci_index("Arnesh Kumar", "State of Bihar") is None
    assert sj._party_in_sci_index("Union of India", "State of Bihar") is None


def test_not_found_result_shape():
    r = sj._not_found_result("Laxman B. Kamble", "MHADA and ors")
    assert r.agent_name == "SCI_Judgment" and r.sources == [] and r.tokens_consumed == 0
    assert "not found" in r.content.lower() or "was found" in r.content
    assert "Laxman B. Kamble vs MHADA and ors" in r.content


def test_node_short_circuits_before_building_the_react_agent():
    import inspect
    src = inspect.getsource(sj.sci_judgment_node)
    assert src.index("_case_name_lookup(query)") < src.index("create_react_agent(")
    assert "None if user_context else _case_name_lookup" in src     # document-context queries never gated
