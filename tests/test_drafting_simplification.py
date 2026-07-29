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
    _Section,
    _FanoutStrategy,
    _reference_excerpt,
    _judge_fanout,
    _generate_sectionwise,
    _generate_draft,
    _chunk_user_facts,
    _pick_relevant_chunk_indices,
    _SelectedChunks,
    _translate_query_for_es_match,
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
# Fan-out judge + section-wise generation — unit tests
# ---------------------------------------------------------------------------

class TestReferenceExcerpt:
    """`_reference_excerpt` formats the reference for the judge's view."""

    def test_empty_reference_returns_placeholder(self):
        out = _reference_excerpt("")
        assert "no reference draft" in out.lower()

    def test_short_reference_passes_through(self):
        ref = "A short reference draft body."
        out = _reference_excerpt(ref)
        assert out == ref

    def test_long_reference_keeps_head_and_tail(self):
        head = "HEAD_MARKER " * 400        # ~4800 chars
        middle = "MIDDLE_NOISE " * 500      # ~6500 chars — should be elided
        tail = "TAIL_MARKER " * 100        # ~1200 chars
        ref = head + middle + tail
        out = _reference_excerpt(ref, head=3000, tail=1000)
        assert "HEAD_MARKER" in out
        assert "TAIL_MARKER" in out
        assert "MIDDLE_NOISE" not in out
        assert "omitted for brevity" in out


class TestJudgeFanout:
    """`_judge_fanout` is a Flash-Lite call; we mock the chain to test wiring."""

    def test_returns_strategy_from_llm(self):
        strategy = _FanoutStrategy(
            should_fanout=True,
            sections=[
                _Section(id="cause_title", heading="CAUSE TITLE", summary="court + parties block"),
                _Section(id="facts", heading="FACTS", summary="numbered facts"),
                _Section(id="grounds", heading="GROUNDS", summary="numbered grounds"),
                _Section(id="prayer", heading="PRAYER", summary="reliefs sought"),
            ],
            reasoning="Long writ — 4-section fan-out.",
        )
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value={
            "raw": MagicMock(usage_metadata={"total_tokens": 200}),
            "parsed": strategy,
        })
        with patch("langchain.chat_models.init_chat_model") as mock_init:
            mock_llm = MagicMock()
            mock_llm.with_structured_output.return_value = mock_llm
            mock_init.return_value = mock_llm
            with patch("agents.drafting.ChatPromptTemplate") as mock_prompt:
                mock_prompt.from_template.return_value.__or__ = MagicMock(return_value=mock_chain)
                got = _run(_judge_fanout(
                    query="Draft a writ petition for quashing FIR No. 123/2024.",
                    reference_draft="LONG REFERENCE DRAFT BODY ...",
                    user_language="en",
                    user_intent=None,
                ))
        assert got.should_fanout is True
        assert len(got.sections) == 4
        assert got.sections[0].id == "cause_title"

    def test_llm_failure_defaults_to_single_pass(self):
        with patch("langchain.chat_models.init_chat_model",
                   side_effect=RuntimeError("LLM blip")):
            got = _run(_judge_fanout(
                query="Draft a notice", reference_draft="ref",
                user_language="en", user_intent=None,
            ))
        assert got.should_fanout is False
        assert got.sections == []
        assert "failed" in got.reasoning.lower()

    def test_marathi_language_flows_into_judge_call(self):
        """The judge must receive the user's target language so it emits
        headings in the right script."""
        captured: dict = {}

        async def _capture_invoke(payload):
            captured.update(payload)
            return {
                "raw": MagicMock(usage_metadata={"total_tokens": 100}),
                "parsed": _FanoutStrategy(should_fanout=False, reasoning="short"),
            }

        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(side_effect=_capture_invoke)
        with patch("langchain.chat_models.init_chat_model") as mock_init:
            mock_llm = MagicMock()
            mock_llm.with_structured_output.return_value = mock_llm
            mock_init.return_value = mock_llm
            with patch("agents.drafting.ChatPromptTemplate") as mock_prompt:
                mock_prompt.from_template.return_value.__or__ = MagicMock(return_value=mock_chain)
                _run(_judge_fanout(
                    query="मसुदा तयार करा", reference_draft="REF",
                    user_language="mr", user_intent=None,
                ))
        assert captured.get("user_language_name") == "Marathi"

    def test_detailed_depth_passes_bias_fan_out_directive(self):
        """The judge previously never saw response_depth, so "in depth"
        legal-notice requests collapsed to single-pass and produced thin
        output. The judge must now receive an explicit "bias toward
        fan-out" directive when intent.response_depth == 'detailed'."""
        from config.intent import UserIntent
        captured: dict = {}

        async def _capture_invoke(payload):
            captured.update(payload)
            return {
                "raw": MagicMock(usage_metadata={"total_tokens": 100}),
                "parsed": _FanoutStrategy(should_fanout=True, sections=[
                    _Section(id="a", heading="A", summary=""),
                    _Section(id="b", heading="B", summary=""),
                ], reasoning="detailed"),
            }

        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(side_effect=_capture_invoke)
        with patch("langchain.chat_models.init_chat_model") as mock_init:
            mock_llm = MagicMock()
            mock_llm.with_structured_output.return_value = mock_llm
            mock_init.return_value = mock_llm
            with patch("agents.drafting.ChatPromptTemplate") as mock_prompt:
                mock_prompt.from_template.return_value.__or__ = MagicMock(return_value=mock_chain)
                _run(_judge_fanout(
                    query="Draft a legal notice in depth.",
                    reference_draft="REF",
                    user_language="en",
                    user_intent=UserIntent(response_depth="detailed",
                                           confidence=0.9),
                ))
        directive = captured.get("depth_directive", "")
        assert "DETAILED" in directive
        assert "FAN-OUT" in directive.upper()

    def test_brief_depth_passes_prefer_single_pass_directive(self):
        from config.intent import UserIntent
        captured: dict = {}

        async def _capture_invoke(payload):
            captured.update(payload)
            return {
                "raw": MagicMock(usage_metadata={"total_tokens": 100}),
                "parsed": _FanoutStrategy(should_fanout=False, reasoning="brief"),
            }

        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(side_effect=_capture_invoke)
        with patch("langchain.chat_models.init_chat_model") as mock_init:
            mock_llm = MagicMock()
            mock_llm.with_structured_output.return_value = mock_llm
            mock_init.return_value = mock_llm
            with patch("agents.drafting.ChatPromptTemplate") as mock_prompt:
                mock_prompt.from_template.return_value.__or__ = MagicMock(return_value=mock_chain)
                _run(_judge_fanout(
                    query="Draft a brief notice.",
                    reference_draft="REF", user_language="en",
                    user_intent=UserIntent(response_depth="brief",
                                           confidence=0.9),
                ))
        directive = captured.get("depth_directive", "")
        assert "BRIEF" in directive
        assert "single-pass" in directive.lower()

    def test_none_intent_passes_default_directive(self):
        """user_intent=None → default depth directive (no bias either way)."""
        captured: dict = {}

        async def _capture_invoke(payload):
            captured.update(payload)
            return {
                "raw": MagicMock(usage_metadata={"total_tokens": 100}),
                "parsed": _FanoutStrategy(should_fanout=False, reasoning=""),
            }

        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(side_effect=_capture_invoke)
        with patch("langchain.chat_models.init_chat_model") as mock_init:
            mock_llm = MagicMock()
            mock_llm.with_structured_output.return_value = mock_llm
            mock_init.return_value = mock_llm
            with patch("agents.drafting.ChatPromptTemplate") as mock_prompt:
                mock_prompt.from_template.return_value.__or__ = MagicMock(return_value=mock_chain)
                _run(_judge_fanout(
                    query="Draft a notice.", reference_draft="REF",
                    user_language="en", user_intent=None,
                ))
        directive = captured.get("depth_directive", "")
        assert "default" in directive.lower() or "no explicit" in directive.lower()


class TestGenerateSectionwise:
    """`_generate_sectionwise` walks pairs and threads prior_text through."""

    @staticmethod
    def _silent_progress(*args, **kwargs):
        pass

    def test_walks_pairs_and_threads_prior_text(self):
        sections = [
            _Section(id="cause_title", heading="CAUSE TITLE", summary="court + parties"),
            _Section(id="facts", heading="FACTS", summary="numbered facts"),
            _Section(id="grounds", heading="GROUNDS", summary="numbered grounds"),
            _Section(id="prayer", heading="PRAYER", summary="reliefs"),
            _Section(id="verification", heading="VERIFICATION", summary="declaration"),
        ]

        calls: list[dict] = []

        async def _fake_pair(
            *, sections_to_write, section_position_start, total_sections,
            query, user_facts, reference_draft, prior_text,
            gathered_context, user_intent, user_language,
            review_and_redraft_mode=False,
        ):
            calls.append({
                "sections_to_write": list(sections_to_write),
                "position_start": section_position_start,
                "total": total_sections,
                "prior_len": len(prior_text or ""),
                "review_and_redraft_mode": review_and_redraft_mode,
            })
            return "\n".join(
                f"## {s.heading}\nBody of {s.id}." for s in sections_to_write
            )

        with patch("agents.drafting._generate_section_pair",
                   side_effect=_fake_pair):
            out = _run(_generate_sectionwise(
                sections=sections,
                query="Draft a writ petition",
                user_facts="",
                reference_draft="REF",
                user_intent=None,
                user_language="en",
                progress_emit=self._silent_progress,
                gathered_context=None,
            ))

        # 5 sections → 3 pair calls: [s1,s2], [s3,s4], [s5 solo]
        assert len(calls) == 3
        assert [c["position_start"] for c in calls] == [1, 3, 5]
        assert len(calls[0]["sections_to_write"]) == 2
        assert len(calls[2]["sections_to_write"]) == 1  # solo last
        # All calls see the same total
        assert all(c["total"] == 5 for c in calls)
        # prior_text grows: pair 1 has no prior, pair 2 has pair-1 output, etc.
        assert calls[0]["prior_len"] == 0
        assert calls[1]["prior_len"] > 0
        assert calls[2]["prior_len"] > calls[1]["prior_len"]
        # Final assembly contains every section's body
        for s in sections:
            assert f"Body of {s.id}." in out

    def test_failed_pair_is_skipped_loop_continues(self):
        sections = [
            _Section(id="a", heading="A", summary=""),
            _Section(id="b", heading="B", summary=""),
            _Section(id="c", heading="C", summary=""),
            _Section(id="d", heading="D", summary=""),
        ]

        async def _flaky_pair(*, sections_to_write, **kwargs):
            ids = "+".join(s.id for s in sections_to_write)
            if "a+b" in ids:
                raise RuntimeError("Gemini blip")
            return "\n".join(
                f"## {s.heading}\nBody of {s.id}." for s in sections_to_write
            )

        with patch("agents.drafting._generate_section_pair",
                   side_effect=_flaky_pair):
            out = _run(_generate_sectionwise(
                sections=sections,
                query="Draft", user_facts="",
                reference_draft="REF",
                user_intent=None, user_language="en",
                progress_emit=self._silent_progress,
                gathered_context=None,
            ))

        # First pair raised → skipped silently; remaining pair drafted.
        assert "Body of c." in out
        assert "Body of d." in out
        assert "Body of a." not in out

    def test_empty_pair_output_does_not_append(self):
        """A section pair that returns empty text (e.g. Gemini blocked twice)
        is skipped — the final assembly does not contain an empty entry."""
        sections = [
            _Section(id="a", heading="A", summary=""),
            _Section(id="b", heading="B", summary=""),
        ]

        async def _empty_pair(**kwargs):
            return ""

        with patch("agents.drafting._generate_section_pair",
                   side_effect=_empty_pair):
            out = _run(_generate_sectionwise(
                sections=sections,
                query="Draft", user_facts="",
                reference_draft="REF",
                user_intent=None, user_language="en",
                progress_emit=self._silent_progress,
                gathered_context=None,
            ))
        assert out == ""

    def test_progress_event_per_section(self):
        """One progress event per section (not per pair) — frontend needs
        section-level granularity for the per-section checklist UI."""
        sections = [
            _Section(id="a", heading="A", summary=""),
            _Section(id="b", heading="B", summary=""),
            _Section(id="c", heading="C", summary=""),
        ]
        events: list[tuple] = []

        def _capture_progress(*args, **kwargs):
            events.append((args, kwargs))

        async def _fake_pair(*, sections_to_write, **kwargs):
            return "ok"

        with patch("agents.drafting._generate_section_pair",
                   side_effect=_fake_pair):
            _run(_generate_sectionwise(
                sections=sections,
                query="Draft", user_facts="",
                reference_draft="REF",
                user_intent=None, user_language="en",
                progress_emit=_capture_progress,
                gathered_context=None,
            ))
        # One event per section (3 events for 3 sections).
        assert len(events) == 3
        # Each event carries a section-specific `step` identifier so the
        # frontend can route per-section UI updates without re-parsing the
        # human-readable message.
        steps = [e[1].get("step") for e in events]
        assert steps == ["section:1", "section:2", "section:3"]
        # The message embeds the human-readable position + heading.
        messages = [e[0][1] if len(e[0]) > 1 else "" for e in events]
        assert all(
            f"section {i} of 3" in m for i, m in zip([1, 2, 3], messages)
        )


class TestGenerateDraftDispatcher:
    """`_generate_draft` routes between single-pass and sectionwise based
    on `_judge_fanout`'s decision."""

    @staticmethod
    def _silent_progress(*args, **kwargs):
        pass

    def test_should_fanout_false_routes_to_single_pass(self):
        single_called: list[bool] = []
        section_called: list[bool] = []

        async def _fake_single_pass(**kwargs):
            single_called.append(True)
            return "SINGLE_PASS_OUTPUT"

        async def _fake_sectionwise(**kwargs):
            section_called.append(True)
            return "SECTIONWISE_OUTPUT"

        async def _fake_judge(**kwargs):
            return _FanoutStrategy(
                should_fanout=False,
                reasoning="short notice — single-pass",
            )

        with patch("agents.drafting._judge_fanout", side_effect=_fake_judge), \
             patch("agents.drafting._generate_single_pass",
                   side_effect=_fake_single_pass), \
             patch("agents.drafting._generate_sectionwise",
                   side_effect=_fake_sectionwise):
            out = _run(_generate_draft(
                query="Draft a Section 138 NI Act notice",
                user_facts="", reference_draft="short ref",
                user_intent=None, user_language="en",
                progress_emit=self._silent_progress,
                gathered_context=None,
            ))
        assert out == "SINGLE_PASS_OUTPUT"
        assert single_called == [True]
        assert section_called == []

    def test_should_fanout_true_routes_to_sectionwise(self):
        single_called: list[bool] = []
        section_called: list[bool] = []

        async def _fake_single_pass(**kwargs):
            single_called.append(True)
            return "SINGLE_PASS_OUTPUT"

        async def _fake_sectionwise(**kwargs):
            section_called.append(True)
            return "SECTIONWISE_OUTPUT"

        async def _fake_judge(**kwargs):
            return _FanoutStrategy(
                should_fanout=True,
                sections=[
                    _Section(id="a", heading="A", summary=""),
                    _Section(id="b", heading="B", summary=""),
                ],
                reasoning="long writ — 2-section fan-out",
            )

        with patch("agents.drafting._judge_fanout", side_effect=_fake_judge), \
             patch("agents.drafting._generate_single_pass",
                   side_effect=_fake_single_pass), \
             patch("agents.drafting._generate_sectionwise",
                   side_effect=_fake_sectionwise):
            out = _run(_generate_draft(
                query="Draft a writ petition for quashing FIR 123/2024.",
                user_facts="", reference_draft="LONG REFERENCE",
                user_intent=None, user_language="en",
                progress_emit=self._silent_progress,
                gathered_context=None,
            ))
        assert out == "SECTIONWISE_OUTPUT"
        assert section_called == [True]
        assert single_called == []

    def test_fanout_true_with_empty_sections_falls_back_to_single_pass(self):
        """Defensive: should_fanout=True but sections=[] should NOT crash —
        dispatcher falls through to single-pass."""
        single_called: list[bool] = []

        async def _fake_single_pass(**kwargs):
            single_called.append(True)
            return "SINGLE_PASS_OUTPUT"

        async def _fake_sectionwise(**kwargs):
            return "SECTIONWISE_OUTPUT"

        async def _fake_judge(**kwargs):
            return _FanoutStrategy(
                should_fanout=True, sections=[],
                reasoning="judge returned empty list",
            )

        with patch("agents.drafting._judge_fanout", side_effect=_fake_judge), \
             patch("agents.drafting._generate_single_pass",
                   side_effect=_fake_single_pass), \
             patch("agents.drafting._generate_sectionwise",
                   side_effect=_fake_sectionwise):
            out = _run(_generate_draft(
                query="Draft", user_facts="", reference_draft="ref",
                user_intent=None, user_language="en",
                progress_emit=self._silent_progress,
                gathered_context=None,
            ))
        assert out == "SINGLE_PASS_OUTPUT"
        assert single_called == [True]


# ---------------------------------------------------------------------------
# Per-section source-chunk router — unit tests
# ---------------------------------------------------------------------------

class TestChunkUserFacts:
    """`_chunk_user_facts` splits raw source into paragraph-like chunks."""

    def test_empty_returns_empty_list(self):
        assert _chunk_user_facts("") == []
        assert _chunk_user_facts(None) == []

    def test_single_paragraph_returns_one_chunk(self):
        text = "This is one paragraph with a single sentence."
        assert _chunk_user_facts(text) == [text]

    def test_splits_on_blank_lines(self):
        text = "Para 1 first line.\nPara 1 second line.\n\nPara 2 body.\n\n\nPara 3 body."
        chunks = _chunk_user_facts(text)
        assert len(chunks) == 3
        assert "Para 1 first line." in chunks[0]
        assert chunks[1] == "Para 2 body."
        assert chunks[2] == "Para 3 body."

    def test_preserves_internal_newlines(self):
        text = "Heading\n1. First point\n2. Second point\n\nNext block."
        chunks = _chunk_user_facts(text)
        assert len(chunks) == 2
        # Numbered structure inside the first chunk is preserved
        assert "1. First point" in chunks[0]
        assert "2. Second point" in chunks[0]

    def test_drops_whitespace_only_chunks(self):
        text = "Real content.\n\n   \n\nMore content."
        chunks = _chunk_user_facts(text)
        assert chunks == ["Real content.", "More content."]


class TestPickRelevantChunkIndices:
    """`_pick_relevant_chunk_indices` routes via Gemini Flash Lite; empty
    chunks / router failures / empty picks always yield []."""

    def test_empty_chunks_returns_empty_without_llm_call(self):
        section = _Section(id="s", heading="H", summary="")
        result = _run(_pick_relevant_chunk_indices(
            user_facts_chunks=[], section=section, query="q",
        ))
        assert result == []

    def test_router_returns_picked_indices(self):
        chunks = [f"paragraph {i} content" for i in range(6)]
        section = _Section(
            id="facts", heading="Facts", summary="Numbered facts",
        )
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value={
            "raw": MagicMock(usage_metadata={"total_tokens": 90}),
            "parsed": _SelectedChunks(
                chunk_indices=[0, 2, 4],
                reasoning="Facts para content lives in even indices.",
            ),
        })
        with patch("langchain.chat_models.init_chat_model") as mock_init:
            mock_llm = MagicMock()
            mock_llm.with_structured_output.return_value = mock_llm
            mock_init.return_value = mock_llm
            with patch("agents.drafting.ChatPromptTemplate") as mock_prompt:
                mock_prompt.from_template.return_value.__or__ = MagicMock(
                    return_value=mock_chain,
                )
                result = _run(_pick_relevant_chunk_indices(
                    user_facts_chunks=chunks, section=section, query="q",
                ))
        assert result == [0, 2, 4]

    def test_router_drops_out_of_range_and_non_int_indices(self):
        """Guard against a router that hallucinates bad indices — we must
        never index past the chunk list or bubble a TypeError."""
        chunks = ["a", "b", "c"]
        section = _Section(id="s", heading="H", summary="")
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value={
            "raw": MagicMock(usage_metadata={"total_tokens": 40}),
            "parsed": _SelectedChunks(
                chunk_indices=[0, 99, -1, 1],
                reasoning="mixed valid + invalid",
            ),
        })
        with patch("langchain.chat_models.init_chat_model") as mock_init:
            mock_llm = MagicMock()
            mock_llm.with_structured_output.return_value = mock_llm
            mock_init.return_value = mock_llm
            with patch("agents.drafting.ChatPromptTemplate") as mock_prompt:
                mock_prompt.from_template.return_value.__or__ = MagicMock(
                    return_value=mock_chain,
                )
                result = _run(_pick_relevant_chunk_indices(
                    user_facts_chunks=chunks, section=section, query="q",
                ))
        # Only 0 and 1 are valid; 99 and -1 dropped.
        assert result == [0, 1]

    def test_llm_failure_returns_empty(self):
        """Router exception → caller falls back to raw source. Returning []
        is the signal for that fallback."""
        chunks = ["a", "b"]
        section = _Section(id="s", heading="H", summary="")
        with patch("langchain.chat_models.init_chat_model",
                   side_effect=RuntimeError("Gemini blip")):
            result = _run(_pick_relevant_chunk_indices(
                user_facts_chunks=chunks, section=section, query="q",
            ))
        assert result == []


class TestSectionwiseChunkingIntegration:
    """`_generate_sectionwise` routes per-pair when the flag is ON and the
    upload is above threshold. Fallbacks preserve current behavior."""

    @staticmethod
    def _silent_progress(*args, **kwargs):
        pass

    def test_flag_off_never_calls_router(self):
        """DRAFTING_PER_SECTION_CHUNKING unset → raw user_facts flows to
        every pair-call unchanged (CLAUDE.md invariant #6)."""
        sections = [
            _Section(id="a", heading="A", summary=""),
            _Section(id="b", heading="B", summary=""),
        ]
        big_facts = "para one.\n\npara two.\n\npara three.\n\npara four." * 5000
        assert len(big_facts) > 100_000  # above threshold

        seen_user_facts: list[str] = []
        router_calls: list[bool] = []

        async def _fake_pair(*, user_facts, **kwargs):
            seen_user_facts.append(user_facts)
            return "ok"

        async def _fake_router(**kwargs):
            router_calls.append(True)
            return [0]

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DRAFTING_PER_SECTION_CHUNKING", None)
            with patch("agents.drafting._generate_section_pair",
                       side_effect=_fake_pair), \
                 patch("agents.drafting._pick_relevant_chunk_indices",
                       side_effect=_fake_router):
                _run(_generate_sectionwise(
                    sections=sections,
                    query="Draft", user_facts=big_facts,
                    reference_draft="REF",
                    user_intent=None, user_language="en",
                    progress_emit=self._silent_progress,
                    gathered_context=None,
                ))
        assert router_calls == []
        assert seen_user_facts == [big_facts]

    def test_flag_on_below_threshold_never_calls_router(self):
        """Flag ON but upload below threshold → passthrough (no router)."""
        sections = [
            _Section(id="a", heading="A", summary=""),
            _Section(id="b", heading="B", summary=""),
        ]
        small_facts = "para one.\n\npara two.\n\npara three."
        assert len(small_facts) < 100_000

        seen_user_facts: list[str] = []
        router_calls: list[bool] = []

        async def _fake_pair(*, user_facts, **kwargs):
            seen_user_facts.append(user_facts)
            return "ok"

        async def _fake_router(**kwargs):
            router_calls.append(True)
            return [0]

        with patch.dict(os.environ,
                        {"DRAFTING_PER_SECTION_CHUNKING": "1"}, clear=False):
            with patch("agents.drafting._generate_section_pair",
                       side_effect=_fake_pair), \
                 patch("agents.drafting._pick_relevant_chunk_indices",
                       side_effect=_fake_router):
                _run(_generate_sectionwise(
                    sections=sections,
                    query="Draft", user_facts=small_facts,
                    reference_draft="REF",
                    user_intent=None, user_language="en",
                    progress_emit=self._silent_progress,
                    gathered_context=None,
                ))
        assert router_calls == []
        assert seen_user_facts == [small_facts]

    def test_flag_on_above_threshold_calls_router_and_shrinks_user_facts(self):
        """Flag ON + above threshold + router picks indices → each pair
        sees a reduced user_facts blob (union of the two sections' picks)."""
        sections = [
            _Section(id="a", heading="A", summary=""),
            _Section(id="b", heading="B", summary=""),
        ]
        # Build a facts blob with 10 distinct chunks, each > 12K chars so
        # the total is well above the 100K threshold.
        chunk_bodies = [f"CHUNK_{i}_" + ("x" * 12_000) for i in range(10)]
        big_facts = "\n\n".join(chunk_bodies)
        assert len(big_facts) > 100_000

        seen_user_facts: list[str] = []

        async def _fake_pair(*, user_facts, **kwargs):
            seen_user_facts.append(user_facts)
            return "ok"

        # First section wants chunks [0, 2]; second wants [2, 5]; union = [0,2,5]
        async def _fake_router(*, section, **kwargs):
            if section.id == "a":
                return [0, 2]
            return [2, 5]

        with patch.dict(os.environ,
                        {"DRAFTING_PER_SECTION_CHUNKING": "1"}, clear=False):
            with patch("agents.drafting._generate_section_pair",
                       side_effect=_fake_pair), \
                 patch("agents.drafting._pick_relevant_chunk_indices",
                       side_effect=_fake_router):
                _run(_generate_sectionwise(
                    sections=sections,
                    query="Draft", user_facts=big_facts,
                    reference_draft="REF",
                    user_intent=None, user_language="en",
                    progress_emit=self._silent_progress,
                    gathered_context=None,
                ))
        assert len(seen_user_facts) == 1
        pair_facts = seen_user_facts[0]
        # Union contains CHUNK 0, 2, 5 — nothing else
        assert "CHUNK_0_" in pair_facts
        assert "CHUNK_2_" in pair_facts
        assert "CHUNK_5_" in pair_facts
        assert "CHUNK_1_" not in pair_facts
        assert "CHUNK_9_" not in pair_facts
        # And the reduced blob is smaller than the original
        assert len(pair_facts) < len(big_facts)

    def test_flag_on_router_empty_selection_falls_back_to_raw(self):
        """Router returns [] for every section → fallback to raw user_facts
        so the section-writer never sees LESS than the current pipeline."""
        sections = [
            _Section(id="a", heading="A", summary=""),
            _Section(id="b", heading="B", summary=""),
        ]
        big_facts = "\n\n".join(f"chunk_{i}_" + "y" * 12_000 for i in range(10))
        assert len(big_facts) > 100_000

        seen_user_facts: list[str] = []

        async def _fake_pair(*, user_facts, **kwargs):
            seen_user_facts.append(user_facts)
            return "ok"

        async def _fake_router(**kwargs):
            return []  # No indices picked → fallback

        with patch.dict(os.environ,
                        {"DRAFTING_PER_SECTION_CHUNKING": "1"}, clear=False):
            with patch("agents.drafting._generate_section_pair",
                       side_effect=_fake_pair), \
                 patch("agents.drafting._pick_relevant_chunk_indices",
                       side_effect=_fake_router):
                _run(_generate_sectionwise(
                    sections=sections,
                    query="Draft", user_facts=big_facts,
                    reference_draft="REF",
                    user_intent=None, user_language="en",
                    progress_emit=self._silent_progress,
                    gathered_context=None,
                ))
        assert seen_user_facts == [big_facts]

    def test_flag_on_too_few_chunks_skips_routing(self):
        """Above threshold but <4 chunks (huge single-paragraph blob) →
        no routing signal, passthrough raw."""
        sections = [
            _Section(id="a", heading="A", summary=""),
            _Section(id="b", heading="B", summary=""),
        ]
        # Single huge paragraph — no blank lines → 1 chunk after splitting.
        big_facts = "z" * 150_000
        assert len(big_facts) > 100_000
        assert len(_chunk_user_facts(big_facts)) == 1

        seen_user_facts: list[str] = []
        router_calls: list[bool] = []

        async def _fake_pair(*, user_facts, **kwargs):
            seen_user_facts.append(user_facts)
            return "ok"

        async def _fake_router(**kwargs):
            router_calls.append(True)
            return [0]

        with patch.dict(os.environ,
                        {"DRAFTING_PER_SECTION_CHUNKING": "1"}, clear=False):
            with patch("agents.drafting._generate_section_pair",
                       side_effect=_fake_pair), \
                 patch("agents.drafting._pick_relevant_chunk_indices",
                       side_effect=_fake_router):
                _run(_generate_sectionwise(
                    sections=sections,
                    query="Draft", user_facts=big_facts,
                    reference_draft="REF",
                    user_intent=None, user_language="en",
                    progress_emit=self._silent_progress,
                    gathered_context=None,
                ))
        assert router_calls == []
        assert seen_user_facts == [big_facts]


class TestPreflightBudget:
    """Language-aware preflight: Devanagari uploads get a tighter budget
    because they tokenise ~2.5 chars/token (vs ~4 for Latin)."""

    @staticmethod
    def _silent_progress(*args, **kwargs):
        pass

    def test_latin_upload_at_2_5m_chars_proceeds(self):
        """Latin upload above the Devanagari cap but below the Latin cap
        must NOT be rejected — the preflight branches on script."""
        latin_facts = "a" * 2_500_000  # above Devanagari cap (2.4M)

        async def _fake_judge(**kwargs):
            return _FanoutStrategy(should_fanout=False, reasoning="short")

        async def _fake_single(**kwargs):
            return "OK"

        async def _fake_section(**kwargs):
            return "OK"

        with patch("agents.drafting._judge_fanout", side_effect=_fake_judge), \
             patch("agents.drafting._generate_single_pass",
                   side_effect=_fake_single), \
             patch("agents.drafting._generate_sectionwise",
                   side_effect=_fake_section):
            out = _run(_generate_draft(
                query="q", user_facts=latin_facts,
                reference_draft="", user_intent=None,
                user_language="en",
                progress_emit=self._silent_progress,
                gathered_context=None,
            ))
        assert out == "OK"

    def test_devanagari_upload_at_2_5m_chars_is_rejected(self):
        """Devanagari-dominant upload above the 2.4M cap → friendly
        message, no Gemini call fires."""
        # 20K chars of Devanagari sample, then padding — sampler reads
        # first 20K only, so the sample is 100% Devanagari.
        deva_sample = "अ" * 20_000
        deva_facts = deva_sample + "z" * 2_490_000

        single_called: list[bool] = []
        section_called: list[bool] = []

        async def _fake_judge(**kwargs):
            return _FanoutStrategy(should_fanout=False, reasoning="short")

        async def _fake_single(**kwargs):
            single_called.append(True)
            return "OK"

        async def _fake_section(**kwargs):
            section_called.append(True)
            return "OK"

        with patch("agents.drafting._judge_fanout", side_effect=_fake_judge), \
             patch("agents.drafting._generate_single_pass",
                   side_effect=_fake_single), \
             patch("agents.drafting._generate_sectionwise",
                   side_effect=_fake_section):
            out = _run(_generate_draft(
                query="q", user_facts=deva_facts,
                reference_draft="", user_intent=None,
                user_language="hi",
                progress_emit=self._silent_progress,
                gathered_context=None,
            ))
        # Friendly message returned; no generation calls fired.
        assert "too large" in out.lower()
        assert single_called == []
        assert section_called == []


# ---------------------------------------------------------------------------
# Regional-language → English translation for ES corpus lookup
# ---------------------------------------------------------------------------

class TestTranslateQueryForEsMatch:
    """`_translate_query_for_es_match` bridges regional-language queries to
    the English-only ES `drafting` corpus. Fires only when user_language !=
    'en'; falls back to empty string on any failure so the caller uses the
    original query and preserves current behaviour."""

    def test_english_target_returns_empty_without_llm_call(self):
        """English is a no-op — no translation needed, no LLM call fires."""
        with patch("langchain.chat_models.init_chat_model") as mock_init:
            result = _run(_translate_query_for_es_match(
                "Draft a Section 138 notice.", "en",
            ))
        assert result == ""
        mock_init.assert_not_called()

    def test_empty_language_returns_empty_without_llm_call(self):
        with patch("langchain.chat_models.init_chat_model") as mock_init:
            result = _run(_translate_query_for_es_match("some text", ""))
        assert result == ""
        mock_init.assert_not_called()

    def test_empty_query_returns_empty_without_llm_call(self):
        with patch("langchain.chat_models.init_chat_model") as mock_init:
            result = _run(_translate_query_for_es_match("", "hi"))
        assert result == ""
        mock_init.assert_not_called()

    def test_hindi_translates_to_english(self):
        """Successful translation returns the English text."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Application to obtain accused's signature on Vakalatnama."
        mock_response.content = mock_response.text
        mock_response.usage_metadata = {"total_tokens": 40}
        mock_llm.invoke = MagicMock(return_value=mock_response)
        with patch("langchain.chat_models.init_chat_model", return_value=mock_llm):
            result = _run(_translate_query_for_es_match(
                "अभियुक्त के हस्ताक्षर वकालतनामा पर प्राप्त करने के लिए आवेदन।",
                "hi",
            ))
        assert "Vakalatnama" in result
        assert "Application" in result

    def test_llm_failure_returns_empty(self):
        """Translator errors → empty string → caller uses original query."""
        with patch("langchain.chat_models.init_chat_model",
                   side_effect=RuntimeError("Gemini blip")):
            result = _run(_translate_query_for_es_match(
                "अभियुक्त के हस्ताक्षर वकालतनामा पर प्राप्त करने के लिए आवेदन।",
                "hi",
            ))
        assert result == ""

    def test_devanagari_echo_response_is_rejected(self):
        """When the LLM echoes back Devanagari (didn't actually translate),
        the low-Latin-ratio guard rejects it and returns empty."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "अभियुक्त के हस्ताक्षर वकालतनामा पर प्राप्त करने के लिए आवेदन।"
        mock_response.content = mock_response.text
        mock_response.usage_metadata = {"total_tokens": 40}
        mock_llm.invoke = MagicMock(return_value=mock_response)
        with patch("langchain.chat_models.init_chat_model", return_value=mock_llm):
            result = _run(_translate_query_for_es_match(
                "अभियुक्त के हस्ताक्षर वकालतनामा पर प्राप्त करने के लिए आवेदन।",
                "hi",
            ))
        assert result == ""


class TestAcquireReferenceDraftTranslatesRegionalQuery:
    """`_acquire_reference_draft` calls the translator when `user_language`
    is not 'en' and uses the translated query for both the ES `match` body
    and the picker call."""

    @staticmethod
    def _silent_progress(*args, **kwargs):
        pass

    def test_regional_language_translates_query_for_es(self):
        """user_language='hi' → translator fires; ES match uses English text."""
        seen_es_body: dict = {}
        seen_picker_query: list[str] = []

        async def _fake_translate(query, user_language):
            assert user_language == "hi"
            return "Application to obtain accused signature on Vakalatnama"

        mock_es = MagicMock()
        def _fake_search(index, body):
            seen_es_body["match_text"] = body["query"]["match"]["page_content"]
            return {"hits": {"hits": [
                {"_source": {"source": "/tpl/Vakalatnama.csv"}},
            ]}}
        mock_es.search = _fake_search

        async def _fake_picker(query, file_paths):
            seen_picker_query.append(query)
            return None  # Reject → skip fetch, go to web

        async def _fake_web(query, **kwargs):
            return "SYNTHESIZED WEB TEXT"

        with patch("agents.drafting.get_es_client", return_value=mock_es), \
             patch("agents.drafting._translate_query_for_es_match",
                   side_effect=_fake_translate), \
             patch("agents.drafting._pick_reference_source",
                   side_effect=_fake_picker), \
             patch("agents.drafting._acquire_reference_via_web",
                   side_effect=_fake_web):
            _run(_acquire_reference_draft(
                query="अभियुक्त के हस्ताक्षर वकालतनामा पर प्राप्त करने के लिए आवेदन।",
                progress_emit=self._silent_progress,
                user_language="hi",
            ))
        assert "Vakalatnama" in seen_es_body["match_text"]
        assert "Application" in seen_es_body["match_text"]
        assert seen_picker_query == ["Application to obtain accused signature on Vakalatnama"]

    def test_english_language_skips_translator(self):
        """user_language='en' → translator not invoked; original query
        flows to ES match + picker unchanged."""
        seen_es_body: dict = {}
        seen_picker_query: list[str] = []
        translator_calls: list[bool] = []

        async def _fake_translate(query, user_language):
            translator_calls.append(True)
            return "SHOULD NOT BE CALLED"

        mock_es = MagicMock()
        def _fake_search(index, body):
            seen_es_body["match_text"] = body["query"]["match"]["page_content"]
            return {"hits": {"hits": [
                {"_source": {"source": "/tpl/Vakalatnama.csv"}},
            ]}}
        mock_es.search = _fake_search

        async def _fake_picker(query, file_paths):
            seen_picker_query.append(query)
            return None

        async def _fake_web(query, **kwargs):
            return "SYNTHESIZED WEB TEXT"

        with patch("agents.drafting.get_es_client", return_value=mock_es), \
             patch("agents.drafting._translate_query_for_es_match",
                   side_effect=_fake_translate), \
             patch("agents.drafting._pick_reference_source",
                   side_effect=_fake_picker), \
             patch("agents.drafting._acquire_reference_via_web",
                   side_effect=_fake_web):
            _run(_acquire_reference_draft(
                query="Application to obtain signature on Vakalatnama.",
                progress_emit=self._silent_progress,
                user_language="en",
            ))
        assert translator_calls == []
        assert "Vakalatnama" in seen_es_body["match_text"]
        assert seen_picker_query == ["Application to obtain signature on Vakalatnama."]

    def test_translator_failure_falls_back_to_original_query(self):
        """Translator returns empty → ES match + picker use the original
        regional-language query (current behaviour — worst case is a
        web-fallback which is what we had before this fix)."""
        seen_es_body: dict = {}

        async def _fake_translate(query, user_language):
            return ""  # Translator failed / rejected — signals fallback

        mock_es = MagicMock()
        def _fake_search(index, body):
            seen_es_body["match_text"] = body["query"]["match"]["page_content"]
            return {"hits": {"hits": []}}
        mock_es.search = _fake_search

        async def _fake_web(query, **kwargs):
            return "SYNTHESIZED WEB TEXT"

        with patch("agents.drafting.get_es_client", return_value=mock_es), \
             patch("agents.drafting._translate_query_for_es_match",
                   side_effect=_fake_translate), \
             patch("agents.drafting._acquire_reference_via_web",
                   side_effect=_fake_web):
            _run(_acquire_reference_draft(
                query="अभियुक्त के हस्ताक्षर वकालतनामा पर प्राप्त करने के लिए आवेदन।",
                progress_emit=self._silent_progress,
                user_language="hi",
            ))
        # Match body should contain the original Devanagari text (sanitized).
        assert "अभियुक्त" in seen_es_body["match_text"] or \
               "वकालतनामा" in seen_es_body["match_text"]


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

    def test_long_writ_petition_exercises_sectionwise_path(self):
        """Long writ petition → judge fans out → sectionwise loop produces
        a multi-section document with multiple `## ` headings.

        We don't assert on internal strategy state (the public signature is
        `str`) — we verify the output shape that section-wise generation
        produces: long body + several distinct section headings + the
        expected legal scaffolding (Article 226, Prayer)."""
        from agents.drafting import drafting_node
        from config.intent import default_intent
        query = (
            "Draft a detailed writ petition under Article 226 of the "
            "Constitution of India before the Bombay High Court for "
            "quashing FIR No. 145/2024 registered at Vile Parle Police "
            "Station, Mumbai, against the Petitioner Mr. Suresh Patil "
            "(resident of 12 MG Road, Mumbai, age 45, occupation: "
            "Managing Director of XYZ Constructions Pvt. Ltd.) under "
            "Sections 420 and 406 of the IPC. The FIR is a malicious "
            "complaint by a disgruntled contractor over a contractual "
            "dispute that is already pending in arbitration. Include "
            "Cause Title, Brief Facts, Questions of Law, multiple "
            "Grounds, Prayer, Verification, and Affidavit. Cite the "
            "State of Haryana v. Bhajan Lal guidelines for quashing."
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
        # Long writ — sectionwise output should be substantial.
        assert len(result.content) > 2000, (
            f"expected >2000 chars, got {len(result.content)}"
        )
        # Section-wise emits a `## ` heading per section. A writ this size
        # should have at least 4 distinct top-level sections.
        heading_count = result.content.count("\n## ") + (
            1 if result.content.startswith("## ") else 0
        )
        assert heading_count >= 4, (
            f"expected ≥4 `## ` headings, got {heading_count}"
        )
        # Core legal scaffolding present.
        assert "Article 226" in result.content
        # User-named party survives into the output.
        assert "Suresh Patil" in result.content

    def test_marathi_demand_notice_renders_in_devanagari(self):
        """Marathi-language drafting query → output dominantly in
        Devanagari script (the localize_prompt strict directive flows
        through both single-pass and section-pair generation)."""
        from agents.drafting import drafting_node
        from config.intent import default_intent
        query = (
            "मराठीत कलम १३८ निगोशिएबल इंस्ट्रुमेंट्स कायद्याअंतर्गत "
            "मागणी नोटीस तयार करा. श्री राजेश कुमार यांनी श्री अनिल "
            "शर्मा यांना दिनांक १५ मार्च २०२६ रोजी ५,००,००० रुपयांचा "
            "धनादेश दिला होता, जो अपुऱ्या निधीच्या कारणावरून परत आला."
        )
        state: dict = {
            "query": query, "original_query": query,
            "agent_queries": {"Drafting": query},
            "user_context": "", "user_language": "mr",
            "user_intent": default_intent(),
            "chat_history": [], "agent_results": {},
        }
        out = _run(drafting_node(state))
        result = out["agent_results"]["Drafting"]
        assert result.error is None
        assert len(result.content) > 300
        # Devanagari dominates the output — count chars in the Devanagari
        # Unicode block (U+0900–U+097F) vs Latin letters. Verbatim
        # case-name citations may have some Latin; cause title party
        # names are transliterated to Devanagari per the strict directive.
        deva = sum(1 for c in result.content if "ऀ" <= c <= "ॿ")
        latin = sum(1 for c in result.content if c.isascii() and c.isalpha())
        assert deva > latin, (
            f"expected Devanagari dominant in Marathi draft; "
            f"got {deva} Devanagari vs {latin} Latin"
        )

    def test_per_section_chunking_router_fires_on_large_upload(self):
        """Chunking-router live smoke — the only E2E that exercises the
        per-section chunking path end-to-end.

        Constructs a synthetic 150K-char user_facts blob with clear
        paragraph structure and a query that triggers sectionwise
        fan-out. Requires BOTH env flags:
          DRAFTING_SIMPLIFICATION_E2E=1  — the E2E class gate
          DRAFTING_PER_SECTION_CHUNKING=1 — turns the router on

        Assertions:
          - No pipeline error, non-empty output, sectionwise shape
          - Router path was entered — verified by patching
            `_pick_relevant_chunk_indices` with a tracking wrapper that
            still calls through to the real Flash-Lite router. Log
            capture would be cleaner but loguru's default sink binds
            sys.stderr at import time, so pytest fixtures (caplog /
            capsys / capfd) all fail to see the log lines even though
            they show up in pytest's own captured-stderr dump."""
        if os.environ.get("DRAFTING_PER_SECTION_CHUNKING") != "1":
            pytest.skip(
                "Requires DRAFTING_PER_SECTION_CHUNKING=1 to exercise the router."
            )
        from agents.drafting import drafting_node
        from config.intent import default_intent

        # Synthetic 150K-char user_facts blob with a paragraph structure
        # the router can see and pick over — 30 numbered paragraphs, each
        # ~5K chars, with distinct topical labels the router prompt can
        # match against the drafting sections.
        topics = [
            "Cause of action and parties",
            "Chronology of correspondence",
            "Statutory framework — Section 138 NI Act",
            "Dishonour of cheque particulars",
            "Notice of demand and response",
            "Cheque no. 001234 dated 15-Mar-2026",
            "Bank memo — funds insufficient",
            "Prior transactions between parties",
            "Guarantee and consideration",
            "Consequences under Section 141 NI Act",
            "Statutory notice under Section 138 proviso",
            "Legal advice sought and received",
            "Documentary evidence — bank statements",
            "Witnesses to the transaction",
            "Attempts at amicable resolution",
        ] * 2  # 30 paragraphs total
        para_body_filler = "This paragraph describes the material fact. " * 100
        fact_paras = [
            f"Paragraph {i+1}. Topic: {t}.\n{para_body_filler}"
            for i, t in enumerate(topics)
        ]
        user_facts = "\n\n".join(fact_paras)
        assert len(user_facts) > 100_000, (
            f"synthetic blob {len(user_facts)} chars — must exceed 100K "
            f"threshold to exercise the router"
        )

        # Long-writ query so the fan-out judge picks section-wise (the
        # router only fires on the section-wise path).
        query = (
            "Draft a detailed complaint under Section 138 of the "
            "Negotiable Instruments Act, 1881, against Mr. Rajesh "
            "Kumar for dishonouring cheque no. 001234 dated 15-Mar-2026 "
            "for Rs. 5,00,000 issued to the Complainant Mr. Anil Sharma. "
            "Include Cause Title, Parties, Facts, Cause of Action, "
            "Statutory Framework, Prayer, and Verification."
        )
        # Inject via file_context.extracted_texts — the uploaded-document
        # path Drafting agents prefer. The `user_context` (pasted-context)
        # path is truncated to 30K chars at drafting_node, which would
        # slice our 150K blob below the 100K router threshold.
        state: dict = {
            "query": query, "original_query": query,
            "agent_queries": {"Drafting": query},
            "user_context": "",
            "file_context": {
                "extracted_texts": [
                    {"name": "large_case_record.txt", "text": user_facts},
                ],
            },
            "user_language": "en",
            "user_intent": default_intent(),
            "chat_history": [], "agent_results": {},
        }

        # Track router invocations while letting the real Flash-Lite
        # picker run. Each call records (section heading, count of picked
        # indices) so we can assert "the router fired at least once" —
        # this is a direct-observation check that doesn't depend on log
        # capture (loguru binds sys.stderr at import time so pytest's
        # caplog / capsys / capfd all fail to capture its output).
        import agents.drafting as drafting_mod
        real_router = drafting_mod._pick_relevant_chunk_indices
        router_invocations: list[tuple[str, int]] = []

        async def _tracking_router(**kwargs):
            picks = await real_router(**kwargs)
            router_invocations.append(
                (kwargs["section"].heading[:40], len(picks)),
            )
            return picks

        with patch(
            "agents.drafting._pick_relevant_chunk_indices",
            side_effect=_tracking_router,
        ):
            out = _run(drafting_node(state))

        result = out["agent_results"]["Drafting"]
        assert result.error is None, f"unexpected pipeline error: {result.error}"
        assert len(result.content) > 500, (
            f"expected substantive draft, got {len(result.content)} chars"
        )
        # Router fired for at least one section pair. The sectionwise loop
        # calls the router once per section per pair, so a fanned-out
        # 6-section draft would produce ≥6 invocations. On any fewer we
        # know the guard `chunking_enabled and len(all_chunks) >= 4`
        # tripped incorrectly.
        assert len(router_invocations) > 0, (
            f"expected the router to fire on a {len(user_facts)}-char "
            f"upload with flag ON; got 0 invocations."
        )
