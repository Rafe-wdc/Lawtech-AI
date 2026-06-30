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
            query, case_facts, reference_draft, prior_text,
            gathered_context, user_intent, user_language,
        ):
            calls.append({
                "sections_to_write": list(sections_to_write),
                "position_start": section_position_start,
                "total": total_sections,
                "prior_len": len(prior_text or ""),
            })
            return "\n".join(
                f"## {s.heading}\nBody of {s.id}." for s in sections_to_write
            )

        with patch("agents.drafting._generate_section_pair",
                   side_effect=_fake_pair):
            out = _run(_generate_sectionwise(
                sections=sections,
                query="Draft a writ petition",
                case_facts="",
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
                query="Draft", case_facts="",
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
                query="Draft", case_facts="",
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
                query="Draft", case_facts="",
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
                case_facts="", reference_draft="short ref",
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
                case_facts="", reference_draft="LONG REFERENCE",
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
                query="Draft", case_facts="", reference_draft="ref",
                user_intent=None, user_language="en",
                progress_emit=self._silent_progress,
                gathered_context=None,
            ))
        assert out == "SINGLE_PASS_OUTPUT"
        assert single_called == [True]


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
