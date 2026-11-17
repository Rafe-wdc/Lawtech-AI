"""Fallback searches keep the court and year the question asked for (2026-11-17).

The last-resort search dropped both, the exact-section search dropped the
year, and the case-name fallback dropped the year. Each now applies exactly
what the question specified: both, either, or neither.
"""
from __future__ import annotations

import pytest

from tools.shared import judgment_search as js

YEAR = {"term": {"year": 2026}}
COURT = {"match_phrase": {"court_name": "bombay high court"}}


@pytest.fixture
def bodies(monkeypatch):
    seen = []
    monkeypatch.setattr(js, "_es_search", lambda body, timeout=30: seen.append(body) or [])
    return seen


@pytest.mark.parametrize("year, court, expected", [
    (2026, "Bombay High Court", [YEAR, COURT]),     # both asked: both kept
    (None, "Bombay High Court", [COURT]),           # court only
    (2026, None, [YEAR]),                           # year only
    (None, None, []),                               # neither: all courts and years
])
def test_the_last_resort_search_keeps_what_was_asked(bodies, year, court, expected):
    result = js.smart_judgment_search("zxqv plinth encroachment easement", year=year, court=court)
    assert result["hits"] == [] and result["strategies_tried"][-1] == "full_text_relaxed"
    relaxed = bodies[-1]["query"]["bool"]
    assert relaxed["filter"] == expected
    multi_tier = bodies[-2]["query"]["bool"]
    assert multi_tier["filter"] == expected          # the strategy before it, unchanged


def test_the_case_name_fallback_keeps_the_year_give_or_take_one(bodies):
    js.smart_judgment_search("Ram v. Shyam", petitioner="Ram Kumar", respondent="Shyam Lal",
                             year=2019, court="Bombay High Court")
    party = [b for b in bodies if "petitioner_names" in str(b)]
    exact, near = party[0]["query"]["bool"]["filter"], party[1]["query"]["bool"]["filter"]
    assert {"term": {"year": 2019}} in exact
    assert {"range": {"year": {"gte": 2018, "lte": 2020}}} in near
    assert all({"match_phrase": {"court_name": "bombay high court"}} in b["query"]["bool"]["filter"]
               for b in party)


def test_the_section_phrase_search_keeps_the_year(monkeypatch):
    calls = []

    class _ES:
        def search(self, index, body, request_timeout=None):
            calls.append(body)
            return {"hits": {"total": {"value": 0}, "hits": []}}
    monkeypatch.setattr(js, "get_es_client", lambda: _ES())
    js.search_by_section_phrase("154B-29", "q", "bombay high court", 2026)
    filters = calls[0]["query"]["bool"]["filter"]
    assert YEAR in filters and COURT in filters
    js.search_by_section_phrase("154B-29", "q", None, None)
    assert len(calls[1]["query"]["bool"]["filter"]) == 1      # the phrase only
