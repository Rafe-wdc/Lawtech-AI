"""Tests for the Judgment agent's exact-section-lookup relevance-gate skip.

Background:
    Queries like "Case law or Citations on Section 313 of CrPC" retrieve
    real ES hits where the cases discuss Section 313 procedurally inside
    a Section 302 IPC murder appeal. The judge LLM looks at the top
    chunks, sees "primarily about Section 302 murder", and rejects --
    forcing a 17-second Gemini + Google Search fallback whose sources are
    vertexaisearch redirector URLs, not the S3 PDFs.

    This file pins the rule that the gate is skipped when:
      (a) the user named one or more specific sections (metadata.acts_or_sections
          non-empty), AND
      (b) >=50% of retrieved hits cite that section in their
          `acts_or_sections_invoked` metadata.

Mirror of yesterday's Newacts gate-skip work (commit 4bd3d76).

Pure-Python tests -- no server / no LLM calls.

Usage:
    pytest tests/test_judgment_relevance_gate.py -v
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pytest

from agents.judgment import (
    _is_exact_section_lookup,
    _section_numbers,
)
from core.state import SourceMetadata


# Lightweight stand-in for CaseMetadata (the real one has more fields than
# the helper cares about; we only need acts_or_sections).
@dataclass
class _FakeMeta:
    acts_or_sections: List[str] = field(default_factory=list)


def _src(acts_invoked: list[str]) -> SourceMetadata:
    """Build a minimal SourceMetadata with the given section invocations."""
    return SourceMetadata(
        source_type="judgment",
        title="X v. Y",
        agent_name="Judgment",
        acts_or_sections_invoked=list(acts_invoked),
    )


# ---------------------------------------------------------------------------
# _section_numbers
# ---------------------------------------------------------------------------

class TestSectionNumbers:
    def test_section_word_variants(self):
        assert _section_numbers("Section 313") == {"313"}
        assert _section_numbers("section 138") == {"138"}
        assert _section_numbers("Sec 302") == {"302"}
        assert _section_numbers("s.313") == {"313"}
        assert _section_numbers("s 313") == {"313"}
        assert _section_numbers("S.302") == {"302"}

    def test_no_section_keyword(self):
        assert _section_numbers("Just some plain text") == set()
        # A bare number with no "section" prefix should NOT match
        assert _section_numbers("article 21") == set()

    def test_multiple_sections_in_one_string(self):
        assert _section_numbers("Section 302 and Section 313 CrPC") == {"302", "313"}

    def test_empty_or_none(self):
        assert _section_numbers("") == set()
        assert _section_numbers(None) == set()


# ---------------------------------------------------------------------------
# _is_exact_section_lookup
# ---------------------------------------------------------------------------

class TestIsExactSectionLookup:
    def test_explicit_section_and_matching_hits(self):
        """The bug case: query says 'Section 313 CrPC' and all hits cite 313."""
        meta = _FakeMeta(acts_or_sections=["section 313 crpc"])
        sources = [
            _src(["Section 302 IPC", "Section 313 CrPC"]),
            _src(["Section 302 IPC", "Section 313 CrPC", "Section 84 IPC"]),
            _src(["Section 313 CrPC"]),
        ]
        assert _is_exact_section_lookup(meta, sources) is True

    def test_some_hits_dont_match_but_majority_does(self):
        """50% threshold: 3 of 5 hits cite the section -> skip the gate."""
        meta = _FakeMeta(acts_or_sections=["section 138 ni act"])
        sources = [
            _src(["Section 138 NI Act"]),
            _src(["Section 138 of the Negotiable Instruments Act"]),
            _src(["Section 138 NI Act", "Section 142 NI Act"]),
            _src(["Section 420 IPC"]),
            _src(["Section 482 CrPC"]),
        ]
        # 3 of 5 cite 138 -> ratio 0.6 -> skip
        assert _is_exact_section_lookup(meta, sources) is True

    def test_minority_match_keeps_gate(self):
        """1 of 5 hits cite the section -> don't skip the gate."""
        meta = _FakeMeta(acts_or_sections=["section 313 crpc"])
        sources = [
            _src(["Section 313 CrPC"]),
            _src(["Section 482 CrPC"]),
            _src(["Section 420 IPC"]),
            _src(["Article 226 Constitution"]),
            _src(["Section 9 CPC"]),
        ]
        assert _is_exact_section_lookup(meta, sources) is False

    def test_empty_metadata_keeps_gate(self):
        """Topic-only query (no section named) -> gate stays on to catch
        false positives like 'bail cases' returning unrelated bail-mentions."""
        meta = _FakeMeta(acts_or_sections=[])
        sources = [_src(["Section 313 CrPC"]) for _ in range(5)]
        assert _is_exact_section_lookup(meta, sources) is False

    def test_no_hits_keeps_gate(self):
        meta = _FakeMeta(acts_or_sections=["section 313 crpc"])
        assert _is_exact_section_lookup(meta, []) is False

    def test_metadata_provision_without_section_number(self):
        """If the user query says 'CrPC' generically (no section number),
        the helper shouldn't fire -- there's nothing concrete to match."""
        meta = _FakeMeta(acts_or_sections=["criminal procedure code"])
        sources = [_src(["Section 313 CrPC"]) for _ in range(5)]
        assert _is_exact_section_lookup(meta, sources) is False

    def test_section_in_invoked_field_no_section_keyword(self):
        """If a hit's acts_or_sections_invoked is missing the 'Section'
        keyword (e.g. just '313 Cr.P.C.'), the helper still recognises it
        via the regex (s? prefix)."""
        meta = _FakeMeta(acts_or_sections=["section 313 crpc"])
        sources = [
            _src(["302 IPC", "Section 313 CrPC"]),
            _src(["Section 313 of the CrPC"]),
            _src(["s.313 Cr.P.C."]),
            _src(["Section 313 Cr.P.C."]),
        ]
        assert _is_exact_section_lookup(meta, sources) is True

    def test_multiple_sections_queried_any_match_counts(self):
        meta = _FakeMeta(acts_or_sections=[
            "section 313 crpc", "section 161 crpc",
        ])
        sources = [
            _src(["Section 313 CrPC"]),
            _src(["Section 161 CrPC"]),
            _src(["Section 420 IPC"]),
        ]
        # 2 of 3 hit either 313 or 161 -> ratio 0.67 -> skip
        assert _is_exact_section_lookup(meta, sources) is True


# ---------------------------------------------------------------------------
# Belt-and-braces: ensure the gate-skip code path is actually wired in
# the judgment_node
# ---------------------------------------------------------------------------

class TestGateSkipIsWired:
    def test_helper_imported_in_judgment_module(self):
        import agents.judgment as j
        assert hasattr(j, "_is_exact_section_lookup")

    def test_judgment_node_source_references_helper(self):
        """If a refactor removes the call, this test fires."""
        import inspect
        import agents.judgment as j
        src = inspect.getsource(j.judgment_node) if hasattr(j, "judgment_node") else ""
        if not src:
            # Some installations may name the entry point differently
            # -- inspect the whole module instead.
            src = inspect.getsource(j)
        assert "_is_exact_section_lookup(" in src, (
            "judgment_node must call _is_exact_section_lookup() before the "
            "relevance gate -- otherwise queries like 'Section 313 CrPC' "
            "keep getting routed to web fallback. See incident threads "
            "73a59cc4, 12c1f08e, 8fece469."
        )


# ---------------------------------------------------------------------------
# Domain hint update
# ---------------------------------------------------------------------------

class TestDomainHintLoosened:
    def test_judgment_hint_mentions_section_discussion_is_ok(self):
        """The hint should explicitly tell the judge that a case discussing
        the cited section IS relevant even if it ALSO arose under other
        provisions. Without this, the judge keeps over-rejecting."""
        from core.retrieval_relevance import _DOMAIN_HINTS
        hint = _DOMAIN_HINTS["Judgment"].lower()
        assert "discusses" in hint or "discussed" in hint
        assert "even if" in hint
