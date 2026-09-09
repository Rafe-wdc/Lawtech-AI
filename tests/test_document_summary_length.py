"""Summary length: a minimum computed from the source size and told to the
model. No maximum.

Advocate feedback 2026-09-09 on a 30-page PDF: "Summary is too short ...
at least 10% summary hona chahiye". The prompt's length tiers were written
in pages, but the model never saw a page count, so it had to guess the tier
from the text. agents.document.summary_length_bounds now derives an
estimated page count and a minimum character count from the source length,
and _source_size_note puts that number in front of the model as a word
floor. The prompt's long-standing rule that over-producing is fine stays.
Pure functions, no LLM.
"""
from __future__ import annotations

import pytest

from agents.document import (
    _SUMMARY_ABS_MIN_CHARS,
    _SUMMARY_MIN_CAP_CHARS,
    _source_size_note,
    summary_length_bounds,
)


@pytest.mark.parametrize("src_chars", [2_000, 13_000, 32_000, 51_000, 75_000])
def test_minimum_is_ten_percent_of_source(src_chars):
    _, lo = summary_length_bounds(src_chars)
    assert lo == max(_SUMMARY_ABS_MIN_CHARS, int(src_chars * 0.10))


def test_thirty_page_filing_floor_is_above_five_thousand_chars():
    # The reported case: a 30-page PDF is roughly 50-75K characters.
    for src in (51_000, 75_000):
        pages, lo = summary_length_bounds(src)
        assert lo >= 5_100
        assert pages >= 25


def test_very_long_source_floor_is_capped_to_the_output_budget():
    for src in (400_000, 1_600_000):
        _, lo = summary_length_bounds(src)
        assert lo == _SUMMARY_MIN_CAP_CHARS


def test_tiny_source_floor_is_the_absolute_minimum():
    pages, lo = summary_length_bounds(800)
    assert pages == 1
    assert lo == _SUMMARY_ABS_MIN_CHARS


def test_empty_or_negative_source_is_safe():
    for src in (0, -5, None):
        pages, lo = summary_length_bounds(src)
        assert pages == 1 and lo == _SUMMARY_ABS_MIN_CHARS


def test_note_states_the_floor_and_no_ceiling():
    note = _source_size_note(51_127)
    assert "51,127 characters" in note
    # 5,112 chars at 6.5 chars per word -> 786 words
    assert "write at least 786 words" in note
    assert "10% of the source" in note
    assert "defect" in note
    assert "Longer is fine" in note
    for banned in ("never more than", "MUST NOT exceed", "maximum"):
        assert banned not in note
    # 10+ pages: per-section minimums, Material Facts largest
    assert "Minimum by section" in note
    assert "Material Facts (chronological): at least" in note


def test_short_source_gets_no_section_split():
    note = _source_size_note(13_000)   # ~6 pages
    assert "Minimum by section" not in note
    assert "write at least" in note
