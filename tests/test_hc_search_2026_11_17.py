"""High Court search fixes found while testing judgements_v2 (2026-11-17).

- One grouped judgment stored once per petition filled every result slot.
- The first (regex) reading of a question never extracted "Section 138 of
  the Negotiable Instruments Act", so the provision search never ran.
- The provision search ignored the year asked for.
"""
from __future__ import annotations

from agents.judgment import _judgment_regex_fallback
from core import judgment_index as jx
from tools.shared import judgment_search as js


def _raw(i, pet, text):
    return {"_id": str(i), "_score": 5.0, "_source": {
        jx.TEXT_FIELD: text, "petitioner_names": [pet], "respondent_names": ["State"],
        "court_name": "bombay high court", "judgment_date": "2026-08-24", jx.CASE_NO_FIELD: f"WP/{i}/2026"}}


def test_a_grouped_judgment_stored_per_petition_is_one_result():
    group = "WRIT PETITION NO. 5476 OF 2026 WITH WRIT PETITION NO. 5477 OF 2026 ... disposed of on these terms. " * 5
    hits = [_raw(i, f"Sanstha {i}", group) for i in range(12)] + [_raw(99, "Other", "A different order. " * 20)]
    out = js._format_hits(hits)
    assert [h["petitioner_names"][0] for h in out] == ["Sanstha 0", "Other"]


def test_short_texts_are_not_merged_by_text():
    hits = [_raw(i, f"P{i}", "Disposed of.") for i in range(3)]
    assert len(js._format_hits(hits)) == 3


def test_the_first_reading_extracts_a_section_and_its_act():
    acts = lambda q: _judgment_regex_fallback(q).acts_or_sections
    assert acts("latest 2026 Bombay High Court judgment on Section 138 Negotiable Instruments Act") == \
        ["section 138 negotiable instruments act"]
    assert acts("bail under Section 483 BNSS") == ["section 483 bnss"]
    assert acts("Section 154B-29 of the Maharashtra Co-operative Societies Act") == \
        ["section 154b-29 maharashtra co-operative societies act"]
    assert acts("quashing u/s 482 CrPC of an FIR under Section 420 IPC") == ["section 482 crpc", "section 420 ipc"]
    assert acts("Kerala High Court judgments on bail in NDPS commercial quantity cases") == []


def test_the_first_reading_keeps_multi_word_court_names():
    court = lambda q: _judgment_regex_fallback(q).court_name
    assert court("Madhya Pradesh High Court judgment quashing a Section 498A FIR") == "madhya pradesh high court"
    assert court("Jammu and Kashmir High Court bail order") == "jammu and kashmir high court"
    assert court("Bombay High Court judgments of 2026") == "bombay high court"
    assert court("Odisha High Court judgments on bail") == "odisha high court"


def test_the_provision_search_filters_by_year(monkeypatch):
    bodies = []
    monkeypatch.setattr(js, "_es_search", lambda body, timeout=30: bodies.append(body) or [])
    js.search_by_legal_provision("section 138 negotiable instruments act", "q", "bombay high court", 30, 2026)
    assert {"term": {"year": 2026}} in bodies[0]["query"]["bool"]["filter"]
