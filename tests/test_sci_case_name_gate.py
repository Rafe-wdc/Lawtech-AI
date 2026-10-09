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
    """total hits for the combined query, then for the petitioner-only query."""
    def __init__(self, combined, petitioner_only=0):
        self.combined, self.petitioner_only, self.bodies = combined, petitioner_only, []

    def search(self, index=None, body=None, **kw):
        self.bodies.append(body)
        n = self.combined if len(self.bodies) == 1 else self.petitioner_only
        hits = [{"_source": {"parties": f"RAJIV MEHTA VS STATE OF MAHARASHTRA {i}", "case_no": f"Crl.A. {i}/2019", "judgment_date": f"2019-0{i}-01"}} for i in range(1, n + 1)]
        return {"hits": {"total": {"value": n}, "hits": hits[:body.get("size", 1)]}}


def test_gate_matches_petitioner_and_respondent_together(monkeypatch):
    es = _ES(combined=0, petitioner_only=2)
    monkeypatch.setattr("core.clients.get_es_client", lambda: es)
    present, related = sj._party_in_sci_index("Rajiv Mehta", "State of Gujarat")
    assert present is False
    q = es.bodies[0]["query"]["match"]["parties"]
    assert q["operator"] == "and" and q["query"] == "rajiv mehta gujarat"
    assert len(related) == 2 and "RAJIV MEHTA VS STATE OF MAHARASHTRA 1 (Crl.A. 1/2019, 2019-01-01)" in related


def test_real_title_is_present_with_one_query(monkeypatch):
    es = _ES(combined=5)
    monkeypatch.setattr("core.clients.get_es_client", lambda: es)
    assert sj._party_in_sci_index("Arnesh Kumar", "State of Bihar") == (True, [])
    assert len(es.bodies) == 1 and es.bodies[0]["query"]["match"]["parties"]["query"] == "arnesh kumar bihar"


def test_gate_falls_back_to_respondent_when_petitioner_is_the_state(monkeypatch):
    es = _ES(combined=3)
    monkeypatch.setattr("core.clients.get_es_client", lambda: es)
    assert sj._party_in_sci_index("State of Maharashtra", "Tulshiram Bhanudas Kamble")[0] is True
    assert es.bodies[0]["query"]["match"]["parties"]["query"] == "tulshiram bhanudas kamble maharashtra"


def test_gate_fails_open_on_es_error_or_no_tokens(monkeypatch):
    class _Boom:
        def search(self, **kw):
            raise RuntimeError("down")
    monkeypatch.setattr("core.clients.get_es_client", lambda: _Boom())
    assert sj._party_in_sci_index("Arnesh Kumar", "State of Bihar") == (None, [])
    assert sj._party_in_sci_index("Union of India", "State of Bihar") == (None, [])


def test_not_found_result_shape():
    r = sj._not_found_result("Laxman B. Kamble", "MHADA and ors", related=["LAXMAN KAMBLE VS STATE (2010-01-01)"])
    assert r.agent_name == "SCI_Judgment" and r.sources == [] and r.tokens_consumed == 0
    assert "not found" in r.content.lower() or "was found" in r.content
    assert "Laxman B. Kamble vs MHADA and ors" in r.content and "LAXMAN KAMBLE VS STATE" in r.content


def test_node_short_circuits_before_building_the_react_agent():
    import inspect
    src = inspect.getsource(sj.sci_judgment_node)
    assert src.index("_case_name_lookup(query)") < src.index("create_react_agent(")
    assert "None if user_context else (" in src     # document-context queries never gated



@pytest.mark.parametrize("q,pet,resp", [
    ("Supreme Court judgment in Rajiv Mehta vs State of Gujarat 2021", "Rajiv Mehta", "State of Gujarat"),
    ("Supreme Court judgment in Arnesh Kumar vs State of Bihar on arrest guidelines", "Arnesh Kumar", "State of Bihar"),
    ("Rajiv Mehta v State of Gujarat 2021 Supreme Court judgment", "Rajiv Mehta", "State of Gujarat"),
    ("Arnesh Kumar v. State of Bihar Supreme Court judgment arrest guidelines Section 41A CrPC", "Arnesh Kumar", "State of Bihar"),
    ("judgment of Laxman B. Kamble vs MHADA and ors.", "Laxman B. Kamble", "MHADA and ors"),
    ("give me the citation for Vishaka vs State of Rajasthan", "Vishaka", "State of Rajasthan"),
    ("State of Maharashtra vs Tulshiram Bhanudas Kamble", "State of Maharashtra", "Tulshiram Bhanudas Kamble"),
])
def test_embedded_case_names_are_found(q, pet, resp):
    assert sj._case_name_lookup(q) == (pet, resp)


@pytest.mark.parametrize("q", [
    "dowry death vs cruelty: what is the distinction?",
    "Section 420 IPC vs Section 318 BNS in a table",
    "anticipatory bail vs regular bail difference",
    "Supreme Court judgments on anticipatory bail under Section 438 CrPC",
    "",
])
def test_topic_and_statute_comparisons_are_not_case_names(q):
    assert sj._case_name_lookup(q) is None


def test_node_tries_the_original_query_first():
    import inspect
    src = inspect.getsource(sj.sci_judgment_node)
    assert '_case_name_lookup(state.get("original_query") or "") or _case_name_lookup(query)' in src
