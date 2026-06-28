"""Tests for the simplified drafting pipeline (Phase D, 2026-06-28).

Covers the acceptance scenarios from docs/drafting_simplification_plan.md §5:

  - Reference acquisition: ES hit usable → picker accepts → no web call
  - Reference acquisition: ES hit irrelevant → picker rejects → web fallback fires
  - Reference acquisition: empty corpus → web fallback fires
  - Mechanical cleanup: validate_draft fixes mojibake / HTML / [CITE:] / empty paras
  - Picker fuzzy match: massaged path still resolves

The unit tests stub ES and the LLM calls so the pipeline is exercised
deterministically. An optional end-to-end class is gated behind
`DRAFTING_SIMPLIFICATION_E2E=1` for the acceptance smokes listed in
the plan §7 (Section 138 demand notice, RTI application).

Uses the project's `_run(asyncio.run)` pattern (same as test_self_refine.py)
to avoid depending on pytest-asyncio.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from agents.drafting import (
    _PickerChoice,
    _pick_reference_source,
    _acquire_reference_draft,
    validate_draft,
)


def _run(coro):
    """Run an async coroutine to completion synchronously.

    Mirrors the helper in tests/test_self_refine.py — avoids the
    pytest-asyncio plugin which is not in the project's pinned test deps.
    """
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Reference picker — unit tests
# ---------------------------------------------------------------------------

class TestPickReferenceSource:
    """`_pick_reference_source` mirrors v1's file-path picker but allows
    'none' as a rejection signal."""

    def test_returns_none_for_empty_list(self):
        result = _run(_pick_reference_source("draft a notice", []))
        assert result is None

    def test_returns_picked_path_when_exact_match(self):
        candidates = [
            "/templates/Bail Application Section 478 BNSS.csv",
            "/templates/Notice under Section 138 NI Act.csv",
        ]
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value={
            "raw": MagicMock(usage_metadata={"total_tokens": 100}),
            "parsed": _PickerChoice(
                selected_file="/templates/Notice under Section 138 NI Act.csv",
                reasoning="Exact match for Section 138 NI Act demand notice.",
            ),
        })
        with patch("langchain.chat_models.init_chat_model") as mock_init:
            mock_llm = MagicMock()
            mock_llm.with_structured_output.return_value = mock_llm
            mock_init.return_value = mock_llm
            with patch("agents.drafting.ChatPromptTemplate") as mock_prompt:
                mock_prompt.from_template.return_value.__or__ = MagicMock(return_value=mock_chain)
                result = _run(_pick_reference_source(
                    "draft a demand notice under Sec 138 NI Act", candidates,
                ))
        assert result == "/templates/Notice under Section 138 NI Act.csv"

    def test_returns_none_when_llm_outputs_none(self):
        """When the picker says 'none', the caller should fall back to web."""
        candidates = [
            "/templates/Bail Application Section 478 BNSS.csv",
            "/templates/Suit for Partition.csv",
        ]
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value={
            "raw": MagicMock(usage_metadata={"total_tokens": 80}),
            "parsed": _PickerChoice(
                selected_file="none",
                reasoning="No candidate is an RTI application format.",
            ),
        })
        with patch("langchain.chat_models.init_chat_model") as mock_init:
            mock_llm = MagicMock()
            mock_llm.with_structured_output.return_value = mock_llm
            mock_init.return_value = mock_llm
            with patch("agents.drafting.ChatPromptTemplate") as mock_prompt:
                mock_prompt.from_template.return_value.__or__ = MagicMock(return_value=mock_chain)
                result = _run(_pick_reference_source(
                    "draft an RTI application to the PIO", candidates,
                ))
        assert result is None

    def test_fuzzy_match_when_basename_only(self):
        """LLM sometimes returns just the basename — fall back to a fuzzy
        match against `os.path.basename` of each candidate."""
        candidates = ["/templates/dir/Bail Application Section 478 BNSS.csv"]
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value={
            "raw": MagicMock(usage_metadata={"total_tokens": 50}),
            "parsed": _PickerChoice(
                selected_file="Bail Application Section 478 BNSS.csv",
                reasoning="Matched by basename.",
            ),
        })
        with patch("langchain.chat_models.init_chat_model") as mock_init:
            mock_llm = MagicMock()
            mock_llm.with_structured_output.return_value = mock_llm
            mock_init.return_value = mock_llm
            with patch("agents.drafting.ChatPromptTemplate") as mock_prompt:
                mock_prompt.from_template.return_value.__or__ = MagicMock(return_value=mock_chain)
                result = _run(_pick_reference_source("bail application", candidates))
        assert result == "/templates/dir/Bail Application Section 478 BNSS.csv"

    def test_unknown_path_returns_none(self):
        """If the LLM picks a path not in the candidate list AND it can't be
        fuzzy-matched, return None (caller falls back to web)."""
        candidates = ["/templates/Bail Application.csv"]
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value={
            "raw": MagicMock(usage_metadata={"total_tokens": 50}),
            "parsed": _PickerChoice(
                selected_file="/templates/Hallucinated File.csv",
                reasoning="hallucinated path",
            ),
        })
        with patch("langchain.chat_models.init_chat_model") as mock_init:
            mock_llm = MagicMock()
            mock_llm.with_structured_output.return_value = mock_llm
            mock_init.return_value = mock_llm
            with patch("agents.drafting.ChatPromptTemplate") as mock_prompt:
                mock_prompt.from_template.return_value.__or__ = MagicMock(return_value=mock_chain)
                result = _run(_pick_reference_source("bail application", candidates))
        assert result is None

    def test_llm_failure_returns_none(self):
        """Picker LLM failure should NOT raise — it should return None so the
        caller falls back to web instead of crashing the request."""
        candidates = ["/templates/Bail Application.csv"]
        with patch("langchain.chat_models.init_chat_model",
                   side_effect=RuntimeError("LLM blip")):
            result = _run(_pick_reference_source("bail application", candidates))
        assert result is None


# ---------------------------------------------------------------------------
# Reference acquisition — unit tests
# ---------------------------------------------------------------------------

class TestAcquireReferenceDraft:
    """Stage 1 of the simplified pipeline. Tests both branches: corpus hit
    and web fallback."""

    @staticmethod
    def _silent_progress(*args, **kwargs):
        pass

    def test_corpus_hit_returns_kind_es(self):
        """ES match returns candidates → picker chooses one → ES fetch
        returns the full content → kind='es', no web call."""
        fake_es = MagicMock()
        fake_es.search = MagicMock(side_effect=[
            {"hits": {"hits": [
                {"_source": {"source": "/templates/Notice 138 NI.csv"}},
                {"_source": {"source": "/templates/Bail Application.csv"}},
            ]}},
            {"hits": {"hits": [
                {"_source": {"source": "/templates/Notice 138 NI.csv",
                              "page_content": "REFERENCE NOTICE BODY ..."}},
            ]}},
        ])

        with patch("agents.drafting.get_es_client", return_value=fake_es), \
             patch("agents.drafting._pick_reference_source",
                   AsyncMock(return_value="/templates/Notice 138 NI.csv")), \
             patch("agents.drafting._acquire_reference_via_web",
                   AsyncMock(return_value="UNEXPECTED-WEB-CALL")) as web_mock:
            text, source, kind = _run(_acquire_reference_draft(
                "draft a demand notice under section 138 NI act",
                self._silent_progress,
            ))
        assert kind == "es"
        assert source == "/templates/Notice 138 NI.csv"
        assert "REFERENCE NOTICE BODY" in text
        web_mock.assert_not_called()

    def test_picker_rejection_triggers_web_fallback(self):
        """Picker returns None → web_search_fallback is invoked → kind='web'."""
        fake_es = MagicMock()
        fake_es.search = MagicMock(return_value={"hits": {"hits": [
            {"_source": {"source": "/templates/Civil Suit.csv"}},
        ]}})

        with patch("agents.drafting.get_es_client", return_value=fake_es), \
             patch("agents.drafting._pick_reference_source",
                   AsyncMock(return_value=None)), \
             patch("agents.drafting._acquire_reference_via_web",
                   AsyncMock(return_value="WEB-SYNTHESISED REFERENCE")) as web_mock:
            text, source, kind = _run(_acquire_reference_draft(
                "draft an RTI application to the PIO", self._silent_progress,
            ))
        assert kind == "web"
        assert source == "<web:picker-rejected>"
        assert text == "WEB-SYNTHESISED REFERENCE"
        web_mock.assert_called_once()

    def test_empty_corpus_triggers_web_fallback(self):
        """ES match returns 0 hits → web fallback fires (no picker call)."""
        fake_es = MagicMock()
        fake_es.search = MagicMock(return_value={"hits": {"hits": []}})

        with patch("agents.drafting.get_es_client", return_value=fake_es), \
             patch("agents.drafting._pick_reference_source",
                   AsyncMock()) as picker_mock, \
             patch("agents.drafting._acquire_reference_via_web",
                   AsyncMock(return_value="WEB-SYNTHESISED REFERENCE")):
            text, source, kind = _run(_acquire_reference_draft(
                "draft a niche-format submission", self._silent_progress,
            ))
        assert kind == "web"
        assert source == "<web:no-corpus-hit>"
        picker_mock.assert_not_called()

    def test_fetch_miss_triggers_web_fallback(self):
        """Picker returns a path but ES fetch returns 0 hits for it → web
        fallback (defensive — should never happen in practice but the code
        handles it)."""
        fake_es = MagicMock()
        fake_es.search = MagicMock(side_effect=[
            {"hits": {"hits": [
                {"_source": {"source": "/templates/Stale Path.csv"}},
            ]}},
            {"hits": {"hits": []}},  # fetch returns nothing
        ])

        with patch("agents.drafting.get_es_client", return_value=fake_es), \
             patch("agents.drafting._pick_reference_source",
                   AsyncMock(return_value="/templates/Stale Path.csv")), \
             patch("agents.drafting._acquire_reference_via_web",
                   AsyncMock(return_value="WEB-SYNTHESISED REFERENCE")) as web_mock:
            text, source, kind = _run(_acquire_reference_draft(
                "draft a notice", self._silent_progress,
            ))
        assert kind == "web"
        assert source == "<web:fetch-failed>"
        web_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Mechanical cleanup — unit tests
# ---------------------------------------------------------------------------

class TestValidateDraft:
    """`validate_draft` is a pure bug-fix layer. Substantive critique
    (orphan citation tails, forbidden statute pairs, paragraph numbering)
    lives in `core.self_refine.self_refine`."""

    def test_mojibake_substring_fallback(self):
        # Classic UTF-8-as-cp1252 misread of an em-dash.
        # Build the mojibake pattern via escape sequences so the file
        # stays clean ASCII / pure-UTF-8 source.
        moji_em_dash = "â€”"   # â€" sequence
        raw = f"The plaintiff {moji_em_dash} Mr. Sharma {moji_em_dash} filed the suit."
        cleaned, warnings = validate_draft(raw)
        # Either the codec roundtrip OR the substring fallback should
        # restore a proper dash. Both routes are acceptable.
        assert "â€" not in cleaned

    def test_strips_cite_placeholder(self):
        raw = "as held in [CITE: Kesavananda Bharati v. State of Kerala]"
        cleaned, warnings = validate_draft(raw)
        assert "[CITE:" not in cleaned
        assert any("CITE" in w for w in warnings)

    def test_strips_html_tags(self):
        raw = "<p>The accused</p> filed the application. <br/>\n<div>Next para.</div>"
        cleaned, warnings = validate_draft(raw)
        assert "<p>" not in cleaned and "<div>" not in cleaned
        assert "The accused" in cleaned
        assert "Next para." in cleaned

    def test_strips_empty_numbered_paragraphs(self):
        raw = "1. First para body.\n2.\n3. Third para body."
        cleaned, warnings = validate_draft(raw)
        # The bare `2.` line should be removed; the others stay.
        assert "1. First para body." in cleaned
        assert "3. Third para body." in cleaned
        assert "\n2.\n" not in cleaned

    def test_never_raises_on_clean_input(self):
        raw = "A perfectly clean draft with no issues whatsoever."
        cleaned, warnings = validate_draft(raw)
        assert cleaned == raw
        assert warnings == []


# ---------------------------------------------------------------------------
# Optional end-to-end smokes — gated behind DRAFTING_SIMPLIFICATION_E2E=1
# (live Gemini + ES required; ~30s per smoke).
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("DRAFTING_SIMPLIFICATION_E2E") != "1",
    reason="Live end-to-end smokes; set DRAFTING_SIMPLIFICATION_E2E=1 to run.",
)
class TestEndToEndSmokes:
    """Acceptance smokes from docs/drafting_simplification_plan.md §7.

    Each runs the full pipeline against the live AWS OpenSearch index
    and Gemini Pro. Verifies the shape of the output (what the user
    asked for, nothing more).
    """

    def test_section_138_notice_produces_letter_shape(self):
        """Demand notice → letter shape; NO court cause title, NO Prayer,
        NO Verification, NO Schedule of Properties."""
        from agents.drafting import drafting_node
        from config.intent import default_intent
        query = (
            "Draft a demand notice under Section 138 NI Act for a "
            "dishonoured cheque of Rs. 5,00,000 issued by Mr. Rajesh "
            "Kumar to Mr. Anil Sharma on 15-Mar-2026."
        )
        state: dict = {
            "query": query, "original_query": query,
            "agent_queries": {"Drafting": query},
            "user_context": "", "user_language": "en",
            "user_intent": default_intent(),
            "chat_history": [], "agent_results": {},
        }
        out = _run(drafting_node(state))
        result = out["agent_results"]["Drafting"]
        assert result.error is None
        assert len(result.content) > 500
        # Letter conventions present:
        assert "Section 138" in result.content
        # Court scaffolding absent:
        assert "IN THE COURT OF" not in result.content
        assert "..... Plaintiff" not in result.content
        # Reference came from ES (the corpus has a 138 NI Act notice).
        assert result.meta.get("reference_kind") == "es"

    def test_rti_application_triggers_web_fallback(self):
        """RTI application → corpus has no RTI templates → picker returns
        'none' → web fallback synthesises a reference → output is an
        RTI letter (NO court cause title, NO Prayer)."""
        from agents.drafting import drafting_node
        from config.intent import default_intent
        query = (
            "Draft an RTI application to the Public Information Officer "
            "of the Regional Passport Office, Pune asking for the status "
            "of passport application file number F-123456."
        )
        state: dict = {
            "query": query, "original_query": query,
            "agent_queries": {"Drafting": query},
            "user_context": "", "user_language": "en",
            "user_intent": default_intent(),
            "chat_history": [], "agent_results": {},
        }
        out = _run(drafting_node(state))
        result = out["agent_results"]["Drafting"]
        assert result.error is None
        assert len(result.content) > 500
        # RTI conventions present:
        assert "Right to Information" in result.content or "RTI" in result.content
        # Court scaffolding absent:
        assert "IN THE COURT OF" not in result.content
        assert "Plaintiff" not in result.content
        # Reference came from the web (corpus had no RTI template).
        assert result.meta.get("reference_kind") == "web"
        assert result.fallback_used is True
