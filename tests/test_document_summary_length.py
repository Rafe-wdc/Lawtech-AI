"""Summary length is computed from the source size and told to the model.

Advocate feedback 2026-09-09 on a 30-page PDF: "Summary is too short ...
at least 10% summary hona chahiye". The prompt's length tiers were written
in pages, but the model never saw a page count, so it had to guess the tier
from the text. agents.document.summary_length_bounds now derives an
estimated page count and a minimum / maximum character count from the
source length, and _source_size_note puts those numbers in front of the
model. Pure functions, no LLM.
"""
from __future__ import annotations

import pytest

from agents.document import (
    _SUMMARY_ABS_MAX_CHARS,
    _SUMMARY_ABS_MIN_CHARS,
    _source_size_note,
    summary_length_bounds,
)


@pytest.mark.parametrize("src_chars", [2_000, 13_000, 32_000, 51_000, 75_000, 400_000])
def test_minimum_is_ten_percent_of_source(src_chars):
    _, lo, _ = summary_length_bounds(src_chars)
    expected = max(_SUMMARY_ABS_MIN_CHARS, int(src_chars * 0.10))
    assert lo == min(expected, _SUMMARY_ABS_MAX_CHARS - 200)


@pytest.mark.parametrize("src_chars", [2_000, 13_000, 32_000, 51_000, 75_000])
def test_maximum_is_fifteen_percent_for_ordinary_sizes(src_chars):
    _, lo, hi = summary_length_bounds(src_chars)
    assert hi == max(lo + 200, int(src_chars * 0.15))
    assert hi > lo


def test_thirty_page_filing_lands_above_five_thousand_chars():
    # The reported case: a 30-page PDF is roughly 50-75K characters.
    for src in (51_000, 75_000):
        pages, lo, hi = summary_length_bounds(src)
        assert lo >= 5_100
        assert pages >= 25


def test_very_long_source_is_capped_and_ordered():
    for src in (400_000, 1_600_000):
        _, lo, hi = summary_length_bounds(src)
        assert lo < hi <= _SUMMARY_ABS_MAX_CHARS


def test_tiny_source_never_demands_more_than_it_holds():
    pages, lo, hi = summary_length_bounds(800)
    assert pages == 1
    assert lo == _SUMMARY_ABS_MIN_CHARS
    assert hi == lo + 200


def test_empty_or_negative_source_is_safe():
    for src in (0, -5, None):
        pages, lo, hi = summary_length_bounds(src)
        assert pages == 1 and lo >= _SUMMARY_ABS_MIN_CHARS and hi > lo


def test_note_states_the_numbers_the_model_must_hit():
    note = _source_size_note(51_127)
    assert "51,127 characters" in note
    # 5,112-7,669 chars at 6.5 chars per word -> 786-1,179 words
    assert "never fewer than 786" in note and "never more than 1,179" in note
    # target sits a quarter into the band (786 + 393 // 4 = 884)
    assert "write about 884 words" in note
    assert "10-15% of the source" in note
    assert "defect" in note
    # 10+ pages: a per-section budget is given, Material Facts largest
    assert "Budget by section" in note
    assert "Material Facts (chronological): about" in note


def test_short_source_gets_no_section_budget():
    note = _source_size_note(13_000)   # ~6 pages
    assert "Budget by section" not in note
    assert "never fewer than" in note
