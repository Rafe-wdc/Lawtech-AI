"""Regression tests for the drafting source-registry wiring.

`agents/drafting.py` used to call `self_refine(...)` without a
`source_registry`. When that argument is None, `core/self_refine.py` hands
the critic the literal string "(none — the caller passed no source registry;
skip the `unretrieved_citation` category for this call)" — so hallucinated
case citations inside drafts were never checked, in the output where a fake
citation does the most damage.

`_gather_relevant_context` retrieves the real statutes and judgments seconds
earlier; it used to discard the structured hits and keep only formatted
prompt text. These tests pin the plumbing that lifts them into a registry.

Two failure modes they exist to catch:

1. **Silent-empty registry.** The adapters in `core.source_registry` read
   attributes via `getattr`, but the ES tools return plain dicts. Passing a
   dict straight through returns None for every record and yields an empty
   registry — which looks exactly like the fix landed while changing nothing.

2. **SCI format drift.** Every SCI tool returns display prose, not data, so
   `_sci_records` parses `sci_judgment_tools._format_hit` output back. The
   round-trip test below fails loudly if that format ever changes, instead of
   silently emptying the Supreme Court half of the whitelist.

Run:
    pytest tests/test_drafting_source_registry.py -v
    python tests/test_drafting_source_registry.py     # no pytest required
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# `core.settings` raises at import without these. Nothing here makes an API
# call — the values only have to be non-empty. Set BEFORE importing anything
# under core/ or agents/. `load_dotenv()` does not override existing vars.
os.environ.setdefault("OPENAI_API_KEY", "test-key-unused")
os.environ.setdefault("GOOGLE_API_KEY", "test-key-unused")
# Skips the ~1.4 GB eager model load in core/clients.py at import time. No
# embedding call is made, so the URL is never dialled.
os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://localhost:1")

from agents.drafting import (  # noqa: E402
    _sci_records, _legislation_records, _judgment_records, _clean_na,
)
from core.source_registry import SourceRegistry  # noqa: E402
from tools.shared.sci_judgment_tools import _format_hit  # noqa: E402


# --- Fixtures ---------------------------------------------------------------

def _fake_sci_es_hit(db_id="44015", parties="Union of India v. Rajeev Bansal",
                     case_no="C.A. No. 8629 of 2024",
                     date="03-10-2024", pdf="https://example.test/j/44015.pdf"):
    """An ES hit in the shape `_format_hit` consumes."""
    return {
        "_id": db_id,
        "_score": 12.5,
        "_source": {
            "parties": parties,
            "case_no": case_no,
            "judgment_date": date,
            "bench": "Dr D.Y. Chandrachud, CJI",
            "judgment_by": "Dr D.Y. Chandrachud, CJI",
            "pdf_links": [{"url": pdf, "title": "Judgment"}] if pdf else [],
            "full_text": "Reassessment notices under section 148 ...",
        },
    }


NEWACTS_HITS = {"hits": [
    {"content": "Whoever commits murder shall be punished ...",
     "source": "/content/Bharitya New Acts/Bharatiya Nyaya Sanhita 2023.csv",
     "section_number": "103", "score": 9.1},
]}

# search_legislation returns NO section_number — citation must degrade to act.
LEGISLATION_HITS = {"hits": [
    {"content": "Transfer by one co-owner ...",
     "source": "/content/Acts/Transfer of Property Act 1882.csv", "score": 7.3},
]}

JUDGMENT_HITS = {"hits": [
    {"content": "The petitioner sought anticipatory bail ...",
     "source": "/content/HC/kar_2021.csv",
     "court_name": "Karnataka High Court",
     "petitioner_names": ["Ramesh Kumar"],
     "respondent_names": ["State of Karnataka"],
     "score": 8.8},
]}


# --- Tests ------------------------------------------------------------------

class TestSciRoundTrip:
    """Guards the coupling between `_format_hit` and `_sci_records`."""

    def test_roundtrip_preserves_parties_and_pdf(self):
        block = _format_hit(_fake_sci_es_hit())
        records = _sci_records(block)
        assert len(records) == 1, f"parse failed on _format_hit output:\n{block}"
        r = records[0]
        assert r.id == "sci-44015"
        assert "Union of India v. Rajeev Bansal" in r.canonical_citation
        assert "C.A. No. 8629 of 2024" in r.canonical_citation
        assert r.pdf_urls == ["https://example.test/j/44015.pdf"]

    def test_multiple_hits_are_split(self):
        block = "\n\n".join([
            _format_hit(_fake_sci_es_hit(db_id="1", parties="A v. B")),
            _format_hit(_fake_sci_es_hit(db_id="2", parties="C v. D")),
        ])
        records = _sci_records(block)
        assert {r.id for r in records} == {"sci-1", "sci-2"}

    def test_missing_pdf_yields_no_url(self):
        # `_format_hit` writes the literal "N/A" when pdf_links is empty.
        block = _format_hit(_fake_sci_es_hit(pdf=None))
        records = _sci_records(block)
        assert records and records[0].pdf_urls == []

    def test_clean_na(self):
        assert _clean_na("N/A") == ""
        assert _clean_na(None) == ""
        assert _clean_na("  ") == ""
        assert _clean_na(" C.A. 1/2024 ") == "C.A. 1/2024"


class TestEsHitAdaptation:
    """The dict-vs-attribute trap: these must NOT come back empty."""

    def test_newacts_hit_keeps_section_and_act(self):
        recs = _legislation_records(
            NEWACTS_HITS, source_type="newacts", agent_name="Newacts")
        assert len(recs) == 1
        assert recs[0].canonical_citation == (
            "Section 103, Bharatiya Nyaya Sanhita 2023")
        assert recs[0].type == "newacts"

    def test_legislation_hit_without_section_degrades_to_act(self):
        recs = _legislation_records(
            LEGISLATION_HITS, source_type="legislation", agent_name="Legislation")
        assert len(recs) == 1
        assert recs[0].canonical_citation == "Transfer of Property Act 1882"

    def test_judgment_hit_builds_parties_citation(self):
        recs = _judgment_records(JUDGMENT_HITS)
        assert len(recs) == 1
        cite = recs[0].canonical_citation
        assert "Ramesh Kumar" in cite and "State of Karnataka" in cite
        assert "Karnataka High Court" in cite

    def test_snippets_are_bounded(self):
        big = {"hits": [{"content": "x" * 5000, "source": "/a/Act.csv",
                         "section_number": "1", "score": 1.0}]}
        recs = _legislation_records(
            big, source_type="legislation", agent_name="Legislation")
        assert len(recs[0].snippet) <= 200


class TestDegradedInputs:
    """ES down / empty results must no-op, never raise."""

    def test_empty_and_malformed_inputs(self):
        for empty in ({}, {"hits": []}, None):
            assert _legislation_records(
                empty, source_type="legislation", agent_name="Legislation") == []
            assert _judgment_records(empty) == []
        assert _sci_records("") == []
        assert _sci_records("No matching judgments found.") == []

    def test_empty_registry_is_falsy(self):
        # self_refine keys off `len(registry) > 0`; an empty registry must
        # degrade to exactly today's behaviour rather than half-populating.
        assert not SourceRegistry()
        assert len(SourceRegistry()) == 0


class TestCriticWhitelist:
    """End-to-end: every retrieved source reaches the critic prompt."""

    def _full_registry(self) -> SourceRegistry:
        reg = SourceRegistry()
        reg.extend(_legislation_records(
            NEWACTS_HITS, source_type="newacts", agent_name="Newacts"))
        reg.extend(_legislation_records(
            LEGISLATION_HITS, source_type="legislation", agent_name="Legislation"))
        reg.extend(_judgment_records(JUDGMENT_HITS))
        reg.extend(_sci_records(_format_hit(_fake_sci_es_hit())))
        return reg

    def test_all_four_retrievers_represented(self):
        reg = self._full_registry()
        assert len(reg) == 4, [r.canonical_citation for r in reg.all()]
        assert {r.type for r in reg.all()} == {
            "newacts", "legislation", "judgment", "sci_judgment"}

    def test_serialize_for_critic_lists_every_citation(self):
        out = self._full_registry().serialize_for_critic()
        for expected in ("Section 103, Bharatiya Nyaya Sanhita 2023",
                         "Transfer of Property Act 1882",
                         "Ramesh Kumar",
                         "Union of India v. Rajeev Bansal"):
            assert expected in out, f"missing from critic whitelist: {expected}"
        assert "https://example.test/j/44015.pdf" in out

    def test_empty_registry_serializes_to_the_none_marker(self):
        assert "none" in SourceRegistry().serialize_for_critic().lower()


# --- Standalone runner (venv has no pytest) ---------------------------------

if __name__ == "__main__":
    failures = 0
    for cls in (TestSciRoundTrip, TestEsHitAdaptation,
                TestDegradedInputs, TestCriticWhitelist):
        inst = cls()
        for name in sorted(n for n in dir(inst) if n.startswith("test_")):
            try:
                getattr(inst, name)()
                print(f"  PASS  {cls.__name__}.{name}")
            except Exception as e:
                failures += 1
                print(f"  FAIL  {cls.__name__}.{name}: {type(e).__name__}: {e}")
    print("\nALL PASSED" if not failures else f"\n{failures} FAILURE(S)")
    sys.exit(1 if failures else 0)
