"""Unit tests for the Newacts agent's relevance-gate short-circuit.

Background:
    When a Newacts query uses exact filtering (act source + section_number
    terms, no BM25/vector hybrid), every ES hit is by construction a section
    of the requested act -- the relevance gate cannot improve precision and
    can only produce false negatives by judging on a truncated chunk view.
    This file pins the rule that the gate is skipped in that mode.

    Bug that motivated this: "Compare BNS Sections 115, 118, 189, 190, 191,
    351, 352" returned all 7 hits from ES, but the judge saw only the first
    3 (TOP_N=3 at the time) and declared sections 190-352 missing. Triggered
    a 35-second web fallback and the answer was lost downstream.

Pure-Python tests -- no server / no LLM calls.

Usage:
    pytest tests/test_newacts_relevance_gate.py -v
"""
from __future__ import annotations

import pytest

from agents.newacts import (
    ActQueryMetadata,
    _build_newacts_query,
    _is_exact_filter_query,
)
from core.retrieval_relevance import _RELEVANCE_TOP_N


# ---------------------------------------------------------------------------
# _is_exact_filter_query -- when should we skip the relevance gate?
# ---------------------------------------------------------------------------

class TestIsExactFilterQuery:
    def test_act_plus_sections_no_hybrid_is_exact(self):
        meta = ActQueryMetadata(
            act_name="The Bharatiya Nyaya Sanhita, 2023",
            section_number=["115", "118", "189"],
            hybrid_search=False,
        )
        assert _is_exact_filter_query(meta) is True

    def test_hybrid_search_disqualifies(self):
        """Hybrid (BM25 + vector) can produce keyword false positives; gate stays on."""
        meta = ActQueryMetadata(
            act_name="The Bharatiya Nyaya Sanhita, 2023",
            section_number=["115"],
            hybrid_search=True,
        )
        assert _is_exact_filter_query(meta) is False

    def test_no_section_number_disqualifies(self):
        """No section filter = topic-only query; gate stays on."""
        meta = ActQueryMetadata(
            act_name="The Bharatiya Nyaya Sanhita, 2023",
            section_number=None,
            hybrid_search=False,
        )
        assert _is_exact_filter_query(meta) is False

    def test_no_act_disqualifies(self):
        """No act filter = could match wrong act's section number; gate stays on."""
        meta = ActQueryMetadata(
            act_name=None,
            section_number=["115"],
            hybrid_search=False,
        )
        assert _is_exact_filter_query(meta) is False

    def test_empty_section_list_disqualifies(self):
        """[] is falsy; gate stays on (defensive)."""
        meta = ActQueryMetadata(
            act_name="X", section_number=[], hybrid_search=False,
        )
        assert _is_exact_filter_query(meta) is False


# ---------------------------------------------------------------------------
# _build_newacts_query -- adaptive size for multi-section queries
# ---------------------------------------------------------------------------

class TestAdaptiveSize:
    def test_single_section_keeps_default_size(self):
        meta = ActQueryMetadata(
            act_name="The Bharatiya Nyaya Sanhita, 2023",
            section_number=["115"],
            hybrid_search=False,
        )
        # Patch ACTS_PATHS to include the act so the filter is built
        import agents.newacts as na
        na.ACTS_PATHS = dict(na.ACTS_PATHS, **{
            "The Bharatiya Nyaya Sanhita, 2023": "/x/bns.csv",
        })
        q = _build_newacts_query(meta, "")
        assert q["size"] == 10, "single-section query should use default 10"

    def test_seven_sections_bumps_size(self):
        """The exact bug case: 7 sections must not be capped at 10."""
        meta = ActQueryMetadata(
            act_name="The Bharatiya Nyaya Sanhita, 2023",
            section_number=["115", "118", "189", "190", "191", "351", "352"],
            hybrid_search=False,
        )
        import agents.newacts as na
        na.ACTS_PATHS = dict(na.ACTS_PATHS, **{
            "The Bharatiya Nyaya Sanhita, 2023": "/x/bns.csv",
        })
        q = _build_newacts_query(meta, "")
        assert q["size"] >= 7
        # 2x sections per the formula: 7*2 = 14, capped at 50
        assert q["size"] == 14

    def test_very_long_section_list_capped_at_50(self):
        meta = ActQueryMetadata(
            act_name="The Bharatiya Nyaya Sanhita, 2023",
            section_number=[str(i) for i in range(100)],  # 100 sections
            hybrid_search=False,
        )
        import agents.newacts as na
        na.ACTS_PATHS = dict(na.ACTS_PATHS, **{
            "The Bharatiya Nyaya Sanhita, 2023": "/x/bns.csv",
        })
        q = _build_newacts_query(meta, "")
        assert q["size"] == 50, "size should be capped at 50 to bound response"


# ---------------------------------------------------------------------------
# Defense-in-depth: relevance judge sees enough chunks
# ---------------------------------------------------------------------------

class TestRelevanceTopN:
    def test_top_n_is_at_least_10(self):
        """At 3 (the old default), the judge missed 4 of 7 BNS sections."""
        assert _RELEVANCE_TOP_N >= 10, (
            "_RELEVANCE_TOP_N too small -- multi-section queries will be "
            "falsely rejected; see incident with BNS 115/118/189/190/191/351/352"
        )
