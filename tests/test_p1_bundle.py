"""Regression tests for the P1 bundle (2026-08-02).

Covers:
- G-11: `core/deadline.py` — scope, remaining, bounded_wait_for.
- G-12: `_handle_integrations` bounded.
- G-13: `/pyapi/chat` file-processing wall-clock envelope.
- G-14: Cached-response `done` event schema alignment.
- G-15: `record_agent_error` wired at every domain agent's outer catch.
- G-16: Gemini circuit breakers wired into self_refine + drafting.
- G-17: 429 backoff in agent_fallback and streaming.
- G-18: Fan-out hard cap + judge sees chat_history hint.
- G-19: Indic budget detection covers all 9 dense scripts.
- G-20: gunicorn max_requests set.
- G-21: ES request_timeout=8 on legislation + constitution_maxim.
- G-22: LOG_LEVEL default is INFO.

Run:
    pytest tests/test_p1_bundle.py -v
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# G-11: core/deadline.py
# ---------------------------------------------------------------------------

class TestDeadlineModule:
    def test_no_deadline_returns_none(self):
        from core.deadline import remaining, deadline_at, is_expired
        # Ensure a clean slate
        from core.deadline import clear_deadline
        clear_deadline()
        assert deadline_at() is None
        assert remaining() is None
        assert is_expired() is False

    def test_remaining_or_uses_default_when_no_deadline(self):
        from core.deadline import remaining_or, clear_deadline
        clear_deadline()
        assert remaining_or(45) == 45
        assert remaining_or(60) == 60

    def test_deadline_scope_sets_and_restores(self):
        from core.deadline import deadline_scope, remaining, remaining_or
        with deadline_scope(seconds=30):
            r = remaining()
            assert r is not None
            assert 25 < r <= 30
            assert remaining_or(60) <= 30  # clamped
        # Restored after exit
        assert remaining() is None

    def test_bounded_wait_for_clamps_to_deadline(self):
        """bounded_wait_for should use min(local_timeout, remaining)."""
        from core.deadline import deadline_scope, bounded_wait_for

        async def slow():
            await asyncio.sleep(5)
            return "done"

        async def run():
            with deadline_scope(seconds=1):
                with pytest.raises(asyncio.TimeoutError):
                    await bounded_wait_for(slow(), local_timeout=45)

        asyncio.run(run())

    def test_bounded_wait_for_fast_returns_when_within_budget(self):
        from core.deadline import deadline_scope, bounded_wait_for

        async def quick():
            await asyncio.sleep(0.05)
            return "ok"

        async def run():
            with deadline_scope(seconds=10):
                result = await bounded_wait_for(quick(), local_timeout=5)
                assert result == "ok"

        asyncio.run(run())

    def test_bounded_wait_for_raises_when_deadline_already_past(self):
        """Deadline scope negative slack → wait_for refuses to start."""
        from core.deadline import set_deadline_seconds, bounded_wait_for, clear_deadline

        async def anything():
            return "should not run"

        async def run():
            set_deadline_seconds(-1)  # already expired within THIS task
            coro = anything()
            try:
                with pytest.raises(asyncio.TimeoutError):
                    await bounded_wait_for(coro, local_timeout=5)
            finally:
                # bounded_wait_for raises before awaiting the coroutine, so
                # close it explicitly to avoid the RuntimeWarning.
                coro.close()
                clear_deadline()

        asyncio.run(run())


# ---------------------------------------------------------------------------
# G-12: _handle_integrations bounded
# ---------------------------------------------------------------------------

class TestIntegrationBounded:
    def test_process_integration_urls_wrapped_in_wait_for(self):
        with open("core/chat_runner.py", encoding="utf-8") as f:
            source = f.read()
        # The unbounded call was `await process_integration_urls(...)`.
        # After the fix it must live under asyncio.wait_for with
        # INTEGRATION_POLL_TIMEOUT_SEC.
        assert "process_integration_urls" in source
        # Locate the process_integration_urls call and check for wait_for
        # in a nearby window.
        idx = source.index("process_integration_urls(query,")
        window = source[max(0, idx - 300):idx + 100]
        assert "asyncio.wait_for" in window, (
            "core/chat_runner.py must wrap process_integration_urls in "
            "asyncio.wait_for(INTEGRATION_POLL_TIMEOUT_SEC)"
        )
        assert "INTEGRATION_POLL_TIMEOUT_SEC" in window


# ---------------------------------------------------------------------------
# G-13: file processing envelope
# ---------------------------------------------------------------------------

class TestFileProcessingEnvelope:
    def test_gateway_has_total_file_processing_budget(self):
        with open("core/gateway.py", encoding="utf-8") as f:
            source = f.read()
        # A named budget constant must exist and be enforced.
        assert "_FILE_PROC_TOTAL_TIMEOUT" in source
        # And a TimeoutError branch handles the case.
        assert "File processing exceeded" in source


# ---------------------------------------------------------------------------
# G-14: cached-response SSE schema drift
# ---------------------------------------------------------------------------

class TestCachedDoneEventSchema:
    def test_cached_done_event_has_context_fields(self):
        with open("core/chat_runner.py", encoding="utf-8") as f:
            source = f.read()
        # Find the cache-hit yield block (the `type: done` inside the
        # `if cached:` branch). Locate by the alignment comment we added
        # to make future maintenance easy.
        idx = source.index("Align the cached")
        # Widen window forward to capture the whole yield.
        window = source[idx:idx + 900]
        assert '"conversation_turn": 0' in window
        assert '"query_rewritten": False' in window
        assert '"effective_query": None' in window


# ---------------------------------------------------------------------------
# G-15: metrics wiring
# ---------------------------------------------------------------------------

class TestMetricsWiring:
    def test_record_agent_error_helper_exists(self):
        from core.metrics import record_agent_error, METRICS
        # Should be a callable
        assert callable(record_agent_error)
        # And the underlying counter now has error_class label
        assert "agent_errors_total" in METRICS

    def test_record_agent_error_never_raises(self):
        """Metric failure must NOT cascade — the helper is a no-op on any error."""
        from core.metrics import record_agent_error
        # None exception, no error_class → should default to "unknown"
        record_agent_error("test_agent")
        # With an exception
        record_agent_error("test_agent", RuntimeError("x"))
        # With explicit error_class
        record_agent_error("test_agent", None, error_class="empty_result")

    def test_time_node_context_manager(self):
        from core.metrics import time_node
        with time_node("test_node"):
            time.sleep(0.001)
        # Should not raise, no return value expected.

    def test_every_domain_agent_wired_to_record_agent_error(self):
        """Every domain agent's outer catch must call record_agent_error."""
        import re
        expected = {
            "agents/gst_judgment.py": "GST_Judgment",
            "agents/sci_judgment.py": "SCI_Judgment",
            "agents/judgment.py": "Judgment",
            "agents/drafting.py": "Drafting",
            "agents/newacts.py": "Newacts",
            "agents/scenario.py": "Scenario",
            "agents/legislation.py": "Legislation",
            "agents/document.py": "Document",
            "agents/non_legal.py": "Non_legal",
            "agents/constitution_maxim.py": "Constitution",
        }
        for path, agent in expected.items():
            with open(path, encoding="utf-8") as f:
                source = f.read()
            pat = re.compile(rf'record_agent_error\("{agent}"')
            assert pat.search(source), (
                f"{path} must call record_agent_error(\"{agent}\", ...) "
                "in its outer catch (G-15 wiring)"
            )

    def test_graph_wraps_nodes_in_timed_node(self):
        with open("core/graph.py", encoding="utf-8") as f:
            source = f.read()
        assert "_timed_node" in source
        assert "observe_node_duration" in source
        # Every add_node call should now be timed
        add_nodes = source.count("graph.add_node(")
        timed = source.count("_timed_node(")
        # There should be at least one _timed_node per add_node
        assert timed >= add_nodes


# ---------------------------------------------------------------------------
# G-16: Gemini circuit breaker
# ---------------------------------------------------------------------------

class TestGeminiCircuitBreaker:
    def test_flash_breaker_primitives(self):
        from core.clients import (
            is_gemini_flash_available, record_gemini_flash_failure,
            record_gemini_flash_success,
        )
        # Starts closed (available)
        record_gemini_flash_success()  # reset
        assert is_gemini_flash_available() is True

        # 5 failures trip the breaker
        for _ in range(5):
            record_gemini_flash_failure()
        assert is_gemini_flash_available() is False

        # Reset restores it
        record_gemini_flash_success()
        assert is_gemini_flash_available() is True

    def test_pro_breaker_independent(self):
        from core.clients import (
            is_gemini_pro_available, record_gemini_pro_failure,
            record_gemini_pro_success, record_gemini_flash_success,
        )
        record_gemini_pro_success()
        record_gemini_flash_success()
        # Trip Pro only
        for _ in range(5):
            record_gemini_pro_failure()
        assert is_gemini_pro_available() is False
        # Flash unaffected
        from core.clients import is_gemini_flash_available
        assert is_gemini_flash_available() is True
        record_gemini_pro_success()

    def test_self_refine_checks_flash_breaker(self):
        with open("core/self_refine.py", encoding="utf-8") as f:
            source = f.read()
        # Both critic and refiner branches must reference the breaker
        assert "is_gemini_flash_available" in source
        assert "is_gemini_pro_available" in source
        assert "record_gemini_flash_failure" in source
        assert "record_gemini_pro_failure" in source

    def test_drafting_judge_and_router_check_breaker(self):
        with open("agents/drafting.py", encoding="utf-8") as f:
            source = f.read()
        # Both _judge_fanout and _pick_relevant_chunk_indices must check
        # is_gemini_flash_available and short-circuit.
        judge_idx = source.index("async def _judge_fanout")
        judge_end = source.find("\n\nasync def", judge_idx + 1)
        judge_body = source[judge_idx:judge_end]
        assert "is_gemini_flash_available" in judge_body

        router_idx = source.index("async def _pick_relevant_chunk_indices")
        router_end = source.find("\n\nasync def", router_idx + 1)
        router_body = source[router_idx:router_end]
        assert "is_gemini_flash_available" in router_body


# ---------------------------------------------------------------------------
# G-17: 429 backoff
# ---------------------------------------------------------------------------

class TestRateLimitBackoff:
    def test_agent_fallback_has_429_branch(self):
        with open("core/agent_fallback.py", encoding="utf-8") as f:
            source = f.read()
        assert 'RESOURCE_EXHAUSTED' in source
        assert 'rate limit' in source
        assert 'rate_limit_retries' in source

    def test_streaming_has_429_branch(self):
        with open("core/streaming.py", encoding="utf-8") as f:
            source = f.read()
        assert 'RESOURCE_EXHAUSTED' in source or '429' in source
        assert 'backing off' in source.lower()


# ---------------------------------------------------------------------------
# G-18: Fan-out cap + chat_history hint
# ---------------------------------------------------------------------------

class TestFanoutCap:
    def test_section_ceiling_is_defined_once(self):
        """G-18's cap, renamed to MAX_SECTIONS and raised to the product limit.

        The old assertion pinned `_FANOUT_HARD_CAP = 12` as source text. The
        ceiling now lives in ONE place and is interpolated into the judge
        prompt, so assert the constant rather than a literal — stating it twice
        is what let prompt and code drift apart and silently delete closing
        sections.
        """
        from agents.drafting import MAX_SECTIONS

        assert MAX_SECTIONS == 20

        with open("agents/drafting.py", encoding="utf-8") as f:
            source = f.read()
        assert "MAX_SECTIONS = 20" in source
        assert "_FANOUT_HARD_CAP" not in source, "old constant should be gone"

    def test_over_ceiling_trim_preserves_the_closing_section(self):
        """The ceiling must never be applied as a plain head slice.

        `sections[:N]` drops the LAST planned section, which is always the
        prayer / verification / signature / execution block.
        """
        with open("agents/drafting.py", encoding="utf-8") as f:
            source = f.read()
        assert "strategy.sections[:MAX_SECTIONS - 1] + strategy.sections[-1:]" in source
        assert "strategy.sections[:MAX_SECTIONS]" not in source

    def test_judge_prompt_takes_the_ceiling_as_a_variable(self):
        with open("config/prompts.py", encoding="utf-8") as f:
            source = f.read()
        idx = source.index("DRAFTING_FANOUT_JUDGE_PROMPT")
        end = source.find("DRAFTING_CHUNK_ROUTER_PROMPT", idx)
        judge_prompt = source[idx:end]
        assert "{max_sections}" in judge_prompt
        for stale in ("AT MOST 12 sections", "AT MOST 15 sections", "at or below 12"):
            assert stale not in judge_prompt, f"competing hard-coded ceiling: {stale}"

    def test_judge_prompt_has_chat_history_hint_slot(self):
        with open("config/prompts.py", encoding="utf-8") as f:
            source = f.read()
        # The prompt template now takes a {chat_history_hint} variable
        idx = source.index("DRAFTING_FANOUT_JUDGE_PROMPT")
        end = source.find("DRAFTING_CHUNK_ROUTER_PROMPT", idx)
        judge_prompt = source[idx:end]
        assert "{chat_history_hint}" in judge_prompt

    def test_summarise_prior_ai_turn_no_history(self):
        from agents.drafting import _summarise_prior_ai_turn
        result = _summarise_prior_ai_turn(None)
        assert "no prior AI turn" in result.lower() or "fresh drafting" in result.lower()
        # Empty list treated the same
        result = _summarise_prior_ai_turn([])
        assert "no prior AI turn" in result.lower() or "fresh drafting" in result.lower()

    def test_summarise_prior_ai_turn_with_ai_message(self):
        from agents.drafting import _summarise_prior_ai_turn
        from langchain.messages import HumanMessage, AIMessage
        history = [
            HumanMessage(content="write me a plaint"),
            AIMessage(content="Here is your plaint...\n1. Introduction\n2. Facts"),
        ]
        result = _summarise_prior_ai_turn(history)
        assert "PRIOR AI TURN" in result
        assert "Here is your plaint" in result


# ---------------------------------------------------------------------------
# G-19: Indic budget detection
# ---------------------------------------------------------------------------

class TestIndicBudgetDetection:
    def test_bengali_uses_indic_budget(self):
        # We can't easily unit-test _generate_draft without full mocks;
        # instead assert the source file contains the Bengali range so
        # the budget helper detects it.
        with open("agents/drafting.py", encoding="utf-8") as f:
            source = f.read()
        assert "_USER_FACTS_BUDGET_INDIC" in source
        assert '"bengali"' in source
        assert '"tamil"' in source
        assert '"telugu"' in source
        assert '"kannada"' in source
        assert '"malayalam"' in source
        assert '"gujarati"' in source
        assert '"gurmukhi"' in source
        assert '"oriya"' in source
        assert '"devanagari"' in source


# ---------------------------------------------------------------------------
# G-20: gunicorn max_requests
# ---------------------------------------------------------------------------

class TestGunicornConfig:
    def test_max_requests_set(self):
        with open("gunicorn.conf.py", encoding="utf-8") as f:
            source = f.read()
        assert "max_requests" in source
        assert "max_requests_jitter" in source


# ---------------------------------------------------------------------------
# G-21: ES request_timeout
# ---------------------------------------------------------------------------

class TestEsRequestTimeout:
    def test_legislation_es_calls_have_request_timeout(self):
        with open("agents/legislation.py", encoding="utf-8") as f:
            source = f.read()
        # Every `es.search(...)` call must include `request_timeout=`
        import re
        calls = re.findall(r"es\.search\([^)]*\)", source)
        assert calls, "legislation.py should have at least one es.search call"
        for call in calls:
            assert "request_timeout=" in call, (
                f"unguarded es.search call in legislation.py: {call}"
            )

    def test_constitution_maxim_es_has_request_timeout(self):
        with open("agents/constitution_maxim.py", encoding="utf-8") as f:
            source = f.read()
        import re
        calls = re.findall(r"es\.search\([^)]*\)", source)
        for call in calls:
            assert "request_timeout=" in call, (
                f"unguarded es.search call in constitution_maxim.py: {call}"
            )


# ---------------------------------------------------------------------------
# G-22: LOG_LEVEL default INFO
# ---------------------------------------------------------------------------

class TestLogLevelDefault:
    def test_settings_default_is_info(self, monkeypatch):
        # Ensure env is not overriding
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        import importlib
        import core.settings
        importlib.reload(core.settings)
        assert core.settings.LOG_LEVEL == "INFO"
