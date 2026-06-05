"""Unit tests for the Drafting agent's per-template FormatSpec extractor.

Background:
    The template_text fetched from ES is a fully-formatted Indian-court
    document exemplar. Earlier prompt wording restricted it to STRUCTURE
    only (to avoid the BUG-02 problem of the LLM copying template facts
    into the draft). _extract_format_spec runs ONE Gemini Flash call that
    distills layout-only conventions (alignment, numbering, openers,
    signature blocks) into a structured FormatSpec, separately injected
    into outline + section prompts so the LLM sees a clear "imitate this
    layout" signal divorced from the fact-isolation warning.

Pure-Python tests; no LLM calls. The end-to-end LLM extraction is
exercised by the existing partition-suit e2e regression.

Usage:
    pytest tests/test_drafting_format_spec.py -v
"""
from __future__ import annotations

import pytest

from agents.drafting import (
    FormatSpec,
    _format_layout_block,
    _format_spec_cache_key,
    _FORMAT_SPEC_STORE,
)


# ---------------------------------------------------------------------------
# FormatSpec model -- defaults + field shape
# ---------------------------------------------------------------------------

class TestFormatSpecDefaults:
    def test_defaults_are_sensible_for_indian_court_filing(self):
        s = FormatSpec()
        assert s.court_header_alignment in ("centered", "left", "right")
        assert s.party_block_separator == "VERSUS"
        assert s.prayer_section_label == "PRAYER"
        assert s.verification_label == "VERIFICATION"
        # Other fields default to empty strings or empty lists so optional
        # sections render gracefully.
        assert s.prayer_opener == ""
        assert s.verification_template == ""
        assert s.other_conventions == []

    def test_round_trip_via_dict(self):
        s1 = FormatSpec(prayer_opener="It is humbly prayed that ...",
                       verification_template="I, [Name], hereby verify ...",
                       other_conventions=["RESPECTFULLY SHOWETH: before para 1"])
        d = s1.model_dump()
        s2 = FormatSpec(**d)
        assert s2.prayer_opener == s1.prayer_opener
        assert s2.other_conventions == s1.other_conventions


# ---------------------------------------------------------------------------
# _format_layout_block -- renders the spec into the prompt block
# ---------------------------------------------------------------------------

class TestFormatLayoutBlock:
    def test_none_returns_empty(self):
        """Empty string when extraction failed; outline + section prompts
        still work via the {format_block} template var being empty."""
        assert _format_layout_block(None) == ""

    def test_minimal_spec_renders_core_fields(self):
        s = FormatSpec()
        block = _format_layout_block(s)
        assert "TEMPLATE LAYOUT CONVENTIONS" in block
        assert "Section labels" in block
        assert "PRAYER" in block
        assert "VERIFICATION" in block
        assert "Numbered paragraphs" in block
        # Trailing newlines so the block can prefix the next template var
        assert block.endswith("\n\n")

    def test_paragraph_opener_inlined_when_present(self):
        s = FormatSpec(paragraph_opener="That ")
        block = _format_layout_block(s)
        # The opener phrase is shown verbatim so the LLM imitates it
        assert 'prefix each with "That "' in block

    def test_paragraph_opener_omitted_when_empty(self):
        s = FormatSpec(paragraph_opener="")
        block = _format_layout_block(s)
        # No "prefix each with" line when opener is empty
        assert "prefix each with" not in block

    def test_optional_fields_only_rendered_when_present(self):
        s = FormatSpec()  # all optional fields default to empty/[]
        block = _format_layout_block(s)
        # Optional sections that depend on populated fields stay absent
        assert "Prayer opener" not in block
        assert "Verification skeleton" not in block
        assert "Signature block" not in block
        assert "Schedule annexure" not in block
        assert "Other conventions" not in block

    def test_full_spec_renders_all_sections(self):
        s = FormatSpec(
            prayer_opener="It is therefore most humbly prayed that this "
                          "Hon'ble Court may be pleased to:",
            verification_template="I, [Plaintiff Name], the above-named "
                                  "Plaintiff, do hereby verify ...",
            signature_block="[right-aligned]\n      Plaintiff\n   "
                            "Through Advocate,\n    [Advocate Name]",
            schedule_notation="(Schedule A: Property description ...)",
            other_conventions=["RESPECTFULLY SHOWETH: as a capitalised "
                               "centered label before paragraph 1"],
        )
        block = _format_layout_block(s)
        assert "Prayer opener (verbatim):" in block
        assert "It is therefore most humbly prayed" in block
        assert "Verification skeleton:" in block
        assert "[Plaintiff Name]" in block
        assert "Signature block:" in block
        assert "Plaintiff" in block
        assert "Schedule annexure notation:" in block
        assert "Other conventions:" in block
        assert "RESPECTFULLY SHOWETH" in block

    def test_block_ends_with_apply_instruction(self):
        s = FormatSpec()
        block = _format_layout_block(s)
        # The final exhortation tells the LLM to USE these conventions
        assert "Apply these conventions verbatim" in block
        # And to substitute placeholders for case-specific values
        assert "[placeholders]" in block

    def test_block_makes_no_factual_references_when_spec_is_clean(self):
        """If the spec correctly used placeholders, the rendered block must
        not contain anything resembling real case data."""
        s = FormatSpec(
            prayer_opener="It is humbly prayed before the [Court Name] ...",
            verification_template="I, [Plaintiff Name], aged [Age] ...",
        )
        block = _format_layout_block(s)
        # No 4-digit year-looking strings
        import re
        # Year-like sequences would indicate fact leakage from the template
        years = re.findall(r'\b(19|20)\d{2}\b', block)
        assert not years, f"Format block contains year-like sequences: {years}"


# ---------------------------------------------------------------------------
# Cache key + store -- per-template caching with content-fingerprint
# invalidation
# ---------------------------------------------------------------------------

class TestFormatSpecCache:
    def test_cache_key_combines_source_and_fingerprint(self):
        k1 = _format_spec_cache_key("/x/template.csv", "abc12345")
        k2 = _format_spec_cache_key("/x/template.csv", "xyz98765")
        # Different content fingerprint -> different key
        assert k1 != k2
        # Same inputs -> same key
        assert k1 == _format_spec_cache_key("/x/template.csv", "abc12345")

    def test_cache_key_differs_per_source(self):
        k1 = _format_spec_cache_key("/x/template1.csv", "abc12345")
        k2 = _format_spec_cache_key("/x/template2.csv", "abc12345")
        assert k1 != k2

    def test_store_is_module_level_dict(self):
        """The cache is shared across requests in the same process."""
        from agents.drafting import _FORMAT_SPEC_STORE
        assert isinstance(_FORMAT_SPEC_STORE, dict)

    def test_store_round_trip(self):
        """We can stash and retrieve a spec by cache key."""
        from agents.drafting import _FORMAT_SPEC_STORE
        key = "test_round_trip_key"
        spec = FormatSpec(paragraph_opener="That ", prayer_section_label="PRAYER")
        _FORMAT_SPEC_STORE[key] = spec
        try:
            assert _FORMAT_SPEC_STORE[key].paragraph_opener == "That "
        finally:
            del _FORMAT_SPEC_STORE[key]


# ---------------------------------------------------------------------------
# Wiring -- the extractor is called and threaded through the right places
# ---------------------------------------------------------------------------

class TestWiring:
    def test_extractor_function_exists(self):
        from agents.drafting import _extract_format_spec
        import inspect
        # async function -- must be awaitable
        assert inspect.iscoroutinefunction(_extract_format_spec)

    def test_outline_accepts_format_block_kwarg(self):
        from agents.drafting import _generate_outline
        import inspect
        sig = inspect.signature(_generate_outline)
        assert "format_block" in sig.parameters
        assert sig.parameters["format_block"].default == ""

    def test_section_gen_accepts_format_block_kwarg(self):
        from agents.drafting import _generate_section
        import inspect
        sig = inspect.signature(_generate_section)
        assert "format_block" in sig.parameters
        assert sig.parameters["format_block"].default == ""

    def test_parallel_runner_accepts_format_block_kwarg(self):
        from agents.drafting import _generate_sections_parallel
        import inspect
        sig = inspect.signature(_generate_sections_parallel)
        assert "format_block" in sig.parameters
        assert sig.parameters["format_block"].default == ""

    def test_pipeline_invokes_extractor(self):
        """Grep-style assertion: the main drafting flow calls the extractor.
        Catches refactors that quietly drop the call."""
        import inspect
        import agents.drafting as d
        src = inspect.getsource(d)
        assert "_extract_format_spec(" in src
        assert "_format_layout_block(" in src
