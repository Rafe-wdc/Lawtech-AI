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
    """Scripted index: a list of hit lists, one per search call."""
    def __init__(self, *responses):
        self.responses, self.bodies = list(responses), []

    def search(self, index=None, body=None, **kw):
        self.bodies.append(body)
        hits = self.responses.pop(0) if self.responses else []
        rows = [{"_source": {"parties": p, "case_no": "Crl.A. 1/2019", "judgment_date": "2019-01-01"}} for p in hits]
        return {"hits": {"total": {"value": len(rows)}, "hits": rows[:body.get("size", 1)]}}


def test_respondent_contributes_at_most_one_token():
    assert sj._respondent_key("State of Kerala basic structure") == "kerala"
    assert sj._respondent_key("State NCT of Delhi anticipatory bail") == "delhi"
    assert sj._respondent_key("Union of India privacy") is None
    assert sj._respondent_key("CBI bail guidelines") is None
    assert sj._respondent_key("Sri Mohan presumption under Section 139") == "mohan"
    assert sj._respondent_key("Shah Bano Begum") == "shah"


def test_full_title_present_in_one_query(monkeypatch):
    es = _ES(["ARNESH KUMAR VS STATE OF BIHAR"])
    monkeypatch.setattr("core.clients.get_es_client", lambda: es)
    assert sj._party_in_sci_index("Arnesh Kumar", "State of Bihar") == (True, [])
    assert es.bodies[0]["query"]["match"]["parties"] == {"query": "arnesh kumar bihar", "operator": "and"}


def test_variant_spelling_of_a_government_respondent_still_counts_as_present(monkeypatch):
    # query says "Government of Uttar Pradesh", index says "GOVT.OF U.P."
    es = _ES([], ["LALITA KUMARI VS GOVT.OF U.P.& ORS."])
    monkeypatch.setattr("core.clients.get_es_client", lambda: es)
    assert sj._party_in_sci_index("Lalita Kumari", "Government of Uttar Pradesh FIR registration") == (True, [])


def test_same_petitioner_against_a_private_party_is_not_the_case(monkeypatch):
    es = _ES([], ["MAHENDRASINH MADARSANG VAGHELA VS RAJIV MAHESHKUMAR MEHTA"])
    monkeypatch.setattr("core.clients.get_es_client", lambda: es)
    present, related = sj._party_in_sci_index("Rajiv Mehta", "State of Gujarat")
    assert present is False and related == ["MAHENDRASINH MADARSANG VAGHELA VS RAJIV MAHESHKUMAR MEHTA (Crl.A. 1/2019, 2019-01-01)"]


def test_name_cited_in_judgment_text_is_not_gated(monkeypatch):
    # not a party in the index, but later judgments cite the case: research runs
    es = _ES([], [], ["(full-text hit)"])
    monkeypatch.setattr("core.clients.get_es_client", lambda: es)
    assert sj._party_in_sci_index("Shreya Singhal", "Union of India Section 66A") == (None, [])
    assert "full_text" in str(es.bodies[-1])


def test_unknown_petitioner_is_absent(monkeypatch):
    es = _ES([], [], [])
    monkeypatch.setattr("core.clients.get_es_client", lambda: es)
    assert sj._party_in_sci_index("Korshi Virpar Dadhlia Shah", "State of Maharashtra and ors") == (False, [])
    assert "full_text" in str(es.bodies[-1])      # the text check ran and found nothing


def test_gate_falls_back_to_respondent_when_petitioner_is_the_state(monkeypatch):
    es = _ES(["STATE OF MAHARASHTRA VS TULSHIRAM BHANUDAS KAMBLE"])
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
