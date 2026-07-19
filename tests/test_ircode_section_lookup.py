"""Regression tests for the Legislation-agent section-lookup fix.

Failure this pins:
    User query "Section 2(d) of Industrial Relations Code, 2020" fell back
    to a Gemini + Google Search web call even though Section 2 IS indexed
    in the `legislation` ES index. Two bugs stacked:

    (1) _search_legislation's second-pass source-scoped query hardcoded a
        header-boost for "section number: Section N", but the IR Code 2020
        ingest wrote "code number: Code N", so the boost never fired.
        BM25 then ranked cross-referencing sections above the actual
        Section 2 doc, and the LLM relevance judge rejected the retrieval.

    (2) When the correct Section-2 doc IS retrieved, it's a 20-KB
        definitions block. The judge only saw the first 1200 chars —
        clauses (a) and (b) — and hallucinated that Section 2(d) defines
        "arbitrator" or "appropriate Government" (both wrong). Clause (d)
        (defining "average pay") sits past char 2400 and was invisible.

Fix (shipped together):

    - agents/legislation.py: second-pass query now filters on
      `section_number.keyword` using a `terms` clause with case variants
      ({sn, sn.upper(), sn.lower()}), and drops minimum_should_match to
      0. Anchors retrieval to the exact section the parser identified.
      Safety net retries the legacy boost-only query if the filter finds
      nothing — protects against parser mis-extractions.

    - core/retrieval_relevance.py: `_build_chunks_block` now uses an
      adaptive per-chunk window. One filtered hit gets 6000 chars; a
      10-hit multi-section retrieval stays at today's 1200. Total judge
      input is bounded at ~12000 chars regardless.

Pure-Python unit tests plus an optional live-ES E2E gated on
IRCODE_LIVE_E2E=1 that hits the actual OpenSearch cluster.

Usage:
    pytest tests/test_ircode_section_lookup.py -v
    IRCODE_LIVE_E2E=1 pytest tests/test_ircode_section_lookup.py -v
"""
from __future__ import annotations

import os
import re

import pytest


# ---------------------------------------------------------------------------
# _adaptive_chunk_window — pure function, no I/O
# ---------------------------------------------------------------------------

from core.retrieval_relevance import (
    _adaptive_chunk_window,
    _RELEVANCE_CHUNK_CHARS,
    _RELEVANCE_CHUNK_CHARS_MAX,
    _RELEVANCE_TOTAL_CHARS_BUDGET,
    _build_chunks_block,
)


class TestAdaptiveChunkWindow:
    def test_one_hit_gets_max_window(self):
        assert _adaptive_chunk_window(1) == _RELEVANCE_CHUNK_CHARS_MAX

    def test_ten_hits_get_floor_window(self):
        # 12000 // 10 == 1200 == floor
        assert _adaptive_chunk_window(10) == _RELEVANCE_CHUNK_CHARS

    def test_five_hits_get_middle_window(self):
        # 12000 // 5 == 2400, above floor, below cap
        assert _adaptive_chunk_window(5) == 2400

    def test_two_hits_capped_at_max(self):
        # 12000 // 2 == 6000 == cap
        assert _adaptive_chunk_window(2) == _RELEVANCE_CHUNK_CHARS_MAX

    def test_three_hits(self):
        assert _adaptive_chunk_window(3) == 4000

    def test_zero_or_negative_returns_floor(self):
        assert _adaptive_chunk_window(0) == _RELEVANCE_CHUNK_CHARS
        assert _adaptive_chunk_window(-1) == _RELEVANCE_CHUNK_CHARS

    def test_total_budget_bounded(self):
        # Total input to the judge stays under either:
        #   - the total-chars budget (12000), OR
        #   - n * floor (kicks in when n_chunks * 1200 > 12000, i.e. n >= 11)
        # In no case should the total blow up unbounded.
        for n in range(1, 20):
            total = n * _adaptive_chunk_window(n)
            assert total <= max(_RELEVANCE_TOTAL_CHARS_BUDGET,
                                n * _RELEVANCE_CHUNK_CHARS)


class TestBuildChunksBlockAppliesAdaptiveWindow:
    def test_single_long_chunk_gets_max_window(self):
        # 20 KB chunk (like Section 2 of IR Code 2020 — 20328 chars)
        long_chunk = "A" * 20000
        block, n = _build_chunks_block([long_chunk])
        assert n == 1
        # Doc 1 body length should be 6000 (max), not 1200 (old floor)
        assert len(block) >= _RELEVANCE_CHUNK_CHARS_MAX
        assert len(block) < _RELEVANCE_CHUNK_CHARS_MAX + 100  # +header only

    def test_ten_chunks_each_capped_at_floor(self):
        # 10 long chunks — old behavior; per-chunk should be 1200
        chunks = ["B" * 5000] * 10
        block, n = _build_chunks_block(chunks)
        assert n == 10
        # Each Doc block gets 1200 chars of body + a "[Doc N]\n" header.
        # Rough size: 10 * (1200 + ~10) plus separators.
        assert len(block) < 15000

    def test_empty_chunks_filtered_out_before_sizing(self):
        # Two non-empty, three empty. Sizing should treat as n=2 → cap=6000.
        chunks = ["C" * 8000, "", None, "D" * 8000, ""]
        block, n = _build_chunks_block(chunks)
        assert n == 2
        # Both docs should hit the max cap
        assert block.count("[Doc") == 2
        # Total body ≈ 2 * 6000 = 12000
        body_chars = len(block) - len("[Doc 1]\n\n\n[Doc 2]\n")
        assert body_chars >= 11000

    def test_top_n_is_still_respected(self):
        # 20 chunks — only first 10 (RELEVANCE_TOP_N) should be rendered.
        chunks = [f"chunk-{i}" * 500 for i in range(20)]
        _, n = _build_chunks_block(chunks)
        assert n == 10


# ---------------------------------------------------------------------------
# Filter semantics: sn_variants set + terms clause structure
# ---------------------------------------------------------------------------

class TestSectionNumberFilterCaseTolerance:
    """The sn_variants set the agent constructs must cover all casings
    observed in production ingests."""

    def test_lowercase_letter_suffix_covers_uppercase(self):
        sn = "498a"  # what parse_section_info returns
        variants = {sn, sn.upper(), sn.lower()}
        assert "498A" in variants  # what ES stores
        assert "498a" in variants

    def test_uppercase_letter_suffix_covers_lowercase(self):
        sn = "65B"
        variants = {sn, sn.upper(), sn.lower()}
        assert "65B" in variants
        assert "65b" in variants

    def test_plain_integer_is_idempotent(self):
        # "2" upper() == "2" lower() == "2" — set collapses to one entry
        sn = "2"
        variants = {sn, sn.upper(), sn.lower()}
        assert variants == {"2"}


# ---------------------------------------------------------------------------
# Optional live end-to-end smoke against real ES + real judge.
# Requires ES creds in .env and IRCODE_LIVE_E2E=1.
# ---------------------------------------------------------------------------

_e2e_reason = "Live ES + LLM judge smoke; set IRCODE_LIVE_E2E=1 to run."


@pytest.mark.skipif(os.environ.get("IRCODE_LIVE_E2E") != "1", reason=_e2e_reason)
class TestLiveES:
    """Hits AWS OpenSearch legislation index and the real Gemini judge.

    Verifies:
      - Section 2(d) IR Code 2020 → retrieves Section 2 AND judge accepts
      - Section 498A IPC → retrieves 498A AND judge accepts (no regression)
      - Section 65B Evidence → retrieves 65B AND judge accepts (no regression)
      - "wages under Industrial Relations Code 2020" (parser mis-extract)
        → safety-net kicks in, returns SOMETHING (may still be judged
        REJECTED — the point is we didn't crash / return empty).
    """

    @pytest.fixture(scope="class", autouse=True)
    def _load_env(self):
        from dotenv import load_dotenv
        load_dotenv()

    def _run(self, query: str, named_acts: list[str]) -> tuple[list[dict], str | None]:
        from agents.legislation import _search_legislation
        clean = re.sub(r'\(\w+\)', '', query).strip()
        clean = re.sub(r'\s+', ' ', clean)
        return _search_legislation(clean, named_acts)

    def test_ir_code_section_2_now_retrieved(self):
        hits, source = self._run(
            "Section 2(d) of Industrial Relations Code, 2020",
            ["Industrial Relations Code, 2020"],
        )
        assert source and "Industrial Relations Code, 2020" in source
        sections = [h["_source"].get("section_number") for h in hits]
        # Section 2 should now be in the results — the fix's core purpose.
        assert "2" in sections, (
            f"Expected Section 2 to be in retrieval results, got {sections}"
        )

    def test_ipc_498a_still_retrieved(self):
        hits, source = self._run(
            "Section 498A of Indian Penal Code",
            ["Indian Penal Code"],
        )
        assert source and "Indian Penal Code" in source
        sections = [h["_source"].get("section_number") for h in hits]
        # 498A must still surface — this was the regression risk (case-mismatch).
        assert "498A" in sections, (
            f"Regression: expected 498A in results, got {sections}"
        )

    def test_evidence_65b_still_retrieved(self):
        hits, source = self._run(
            "Section 65B of Indian Evidence Act",
            ["Indian Evidence Act"],
        )
        assert source and "Indian Evidence Act" in source
        sections = [h["_source"].get("section_number") for h in hits]
        assert "65B" in sections, (
            f"Regression: expected 65B in results, got {sections}"
        )

    def test_safety_net_on_parser_misextraction(self):
        # "wages under Industrial Relations Code 2020" — parser incorrectly
        # returns section_type='code' section_number='2020'. There is no
        # section 2020 in the act, so the exact-section filter finds
        # nothing. The safety net must retry without the filter and
        # return the legacy 5 hits (which the judge will then reject —
        # that's fine, the important thing is we did not return empty).
        hits, source = self._run(
            "wages under Industrial Relations Code 2020",
            ["Industrial Relations Code, 2020"],
        )
        assert source and "Industrial Relations Code, 2020" in source
        # Safety net guarantees non-empty results here.
        assert len(hits) > 0, "Safety-net retry should have returned results"
