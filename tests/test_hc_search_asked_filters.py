"""Fallback searches keep the court and year the question asked for (2026-11-17).

The last-resort search dropped both, the exact-section search dropped the
year, and the case-name fallback dropped the year. Each now applies exactly
what the question specified: both, either, or neither, and the year exactly
as asked, never a year either side (2026-11-18).
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


def test_the_case_name_fallbacks_keep_the_exact_year(bodies):
    # Every case-name search (exact, swapped, fuzzy, single party) and every
    # later strategy keeps 2019 exactly: no 2018 or 2020, no range.
    result = js.smart_judgment_search("Ram v. Shyam", petitioner="Ram Kumar", respondent="Shyam Lal",
                                      year=2019, court="Bombay High Court")
    assert result["hits"] == [] and "party_near_year" not in result["strategies_tried"]
    assert {"party_both_exact", "party_swapped", "party_fuzzy", "single_party"} <= set(result["strategies_tried"])
    party = [b for b in bodies if "petitioner_names" in str(b)]
    assert len(party) >= 4
    for body in bodies:
        filters = body["query"]["bool"]["filter"]
        assert {"term": {"year": 2019}} in filters
        assert {"match_phrase": {"court_name": "bombay high court"}} in filters
        assert "range" not in str(filters)


@pytest.mark.parametrize("search", [
    lambda: js.search_by_party_fuzzy("Ram Kumar", "Shyam Lal", 2026, "Bombay High Court"),
    lambda: js.search_by_single_party("Ram Kumar", "any", 2026, "Bombay High Court"),
    lambda: js.search_by_case_type("bail", "bail", court="Bombay High Court", year=2026),
    lambda: js.search_by_legal_provision("Section 34 of the Arbitration Act", "arbitration",
                                         court="Bombay High Court", year=2026),
])
def test_every_strategy_uses_the_same_exact_filters(bodies, search):
    search()
    assert bodies
    for body in bodies:
        filters = body["query"]["bool"]["filter"]
        assert YEAR in filters and COURT in filters
        assert "range" not in str(filters)


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
