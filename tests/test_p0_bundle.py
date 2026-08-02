"""Regression tests for the P0 bundle (2026-08-02).

Covers:
- G-1: `short_err()` helper — never crashes on empty exceptions.
- G-2: Orchestrator guard — Drafting + files → no web fallback.
- G-3: `_error_response` correct signature after arg-order fixes.
- G-5: SCI / GST / non_legal LLM calls are bounded.
- G-6: Silent bare `except: pass` sites now log a warning.
- G-8: `self_refine` critic/refiner have local timeouts + short_err.
- G-10: Checkpointer raises when `REQUIRE_POSTGRES=1` and no Postgres URL.

Run all with:
    pytest tests/test_p0_bundle.py -v
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# G-1: short_err() helper
# ---------------------------------------------------------------------------

class TestShortErr:
    """Verify short_err never crashes on empty exceptions."""

    def test_timeout_error_empty_message(self):
        """TimeoutError has empty str() — must still surface the type name."""
        from core.logger import short_err
        assert short_err(asyncio.TimeoutError()) == "TimeoutError"

    def test_runtime_error_with_message(self):
        from core.logger import short_err
        assert short_err(RuntimeError("boom")) == "RuntimeError: boom"

    def test_runtime_error_multiline_takes_first_line(self):
        from core.logger import short_err
        result = short_err(RuntimeError("line1\nline2\nline3"))
        assert result == "RuntimeError: line1"

    def test_long_message_capped_at_200_chars(self):
        from core.logger import short_err
        result = short_err(RuntimeError("x" * 500))
        # "RuntimeError: " + 200 chars of x
        assert result.startswith("RuntimeError: ")
        assert len(result) == len("RuntimeError: ") + 200

    def test_none_is_defensive(self):
        from core.logger import short_err
        assert short_err(None) == "unknown"  # type: ignore[arg-type]

    def test_no_crash_on_empty_message_variants(self):
        """The core Buglist Bug #1 failure mode — must not raise."""
        from core.logger import short_err
        # All these had `str(e) == ""` in prod and crashed `splitlines()[0]`
        assert short_err(asyncio.CancelledError()) == "CancelledError"
        assert short_err(ValueError()) == "ValueError"

    def test_return_type_is_str(self):
        from core.logger import short_err
        assert isinstance(short_err(Exception("x")), str)
        assert isinstance(short_err(asyncio.TimeoutError()), str)


# ---------------------------------------------------------------------------
# G-3: _error_response arg order
# ---------------------------------------------------------------------------

class TestErrorResponseSignature:
    """Every gateway `_error_response(...)` call must now pass ints for status."""

    def test_signature_accepts_int_first(self):
        from core.gateway import _error_response
        resp = _error_response(404, "not_found", "Message not found")
        # JSONResponse.status_code should be the int we passed
        assert resp.status_code == 404

    def test_signature_returns_json_response(self):
        from fastapi.responses import JSONResponse
        from core.gateway import _error_response
        resp = _error_response(400, "validation_error", "Bad input")
        assert isinstance(resp, JSONResponse)

    def test_no_flipped_calls_remain_in_gateway(self):
        """Grep guard: no live callsite should pass a string as status_code."""
        import re
        with open("core/gateway.py", encoding="utf-8") as f:
            source = f.read()
        # The flipped-arg pattern is `_error_response("`
        # (string first). After the P0 fix, no such calls should remain.
        flipped = re.findall(r'_error_response\(\s*"', source)
        assert flipped == [], (
            f"Found {len(flipped)} flipped _error_response call(s); "
            "the P0 fix requires (status_code:int, error:str, message:str)"
        )


# ---------------------------------------------------------------------------
# G-8: self_refine now has explicit timeouts + short_err
# ---------------------------------------------------------------------------

class TestSelfRefineTimeouts:
    def test_critic_wrapped_in_wait_for(self):
        """Static: `_critique` must bound its LLM call. Either
        `asyncio.wait_for` directly or `bounded_wait_for` (P1) counts —
        both enforce a local timeout ceiling."""
        with open("core/self_refine.py", encoding="utf-8") as f:
            source = f.read()
        idx = source.index("async def _critique(")
        next_def = source.find("async def ", idx + 1)
        critique_body = source[idx:next_def]
        assert (
            "asyncio.wait_for" in critique_body
            or "bounded_wait_for" in critique_body
        ), (
            "core/self_refine.py _critique must wrap the LLM call in "
            "asyncio.wait_for (P0) or bounded_wait_for (P1)"
        )

    def test_refiner_wrapped_in_wait_for(self):
        with open("core/self_refine.py", encoding="utf-8") as f:
            source = f.read()
        idx = source.index("async def _refine(")
        next_def = source.find("async def ", idx + 1)
        refine_body = source[idx:next_def]
        assert (
            "asyncio.wait_for" in refine_body
            or "bounded_wait_for" in refine_body
        ), (
            "core/self_refine.py _refine must wrap the LLM call in "
            "asyncio.wait_for (P0) or bounded_wait_for (P1)"
        )

    def test_no_splitlines_crash_pattern_in_self_refine(self):
        with open("core/self_refine.py", encoding="utf-8") as f:
            source = f.read()
        # `str(e).splitlines()[0]` is the crash pattern from Bug #1
        assert "str(e).splitlines()[0]" not in source
        assert "str(_refine_err).splitlines()[0]" not in source


class TestDraftingChunkRouterGather:
    """G-8 second half: gather uses return_exceptions=True so a single router
    crash cannot kill the whole sectionwise loop."""

    def test_gather_uses_return_exceptions(self):
        with open("agents/drafting.py", encoding="utf-8") as f:
            source = f.read()
        # Find the sectionwise gather call site — the last occurrence of
        # `_pick_relevant_chunk_indices(` (definition is earlier in the
        # file). Assert return_exceptions=True is in that window.
        call_idx = source.rindex("_pick_relevant_chunk_indices(")
        # Look for the gather( that wraps this call — up to 1500 chars back.
        window = source[max(0, call_idx - 1500):call_idx + 500]
        assert "asyncio.gather" in window, (
            "call site should live under asyncio.gather"
        )
        assert "return_exceptions=True" in window, (
            "asyncio.gather over _pick_relevant_chunk_indices must "
            "pass return_exceptions=True (per audit finding D.3)"
        )


# ---------------------------------------------------------------------------
# G-5: SCI + GST + non_legal LLM calls are bounded
# ---------------------------------------------------------------------------

class TestReactAgentTimeouts:
    def test_sci_react_wrapped_in_wait_for(self):
        with open("agents/sci_judgment.py", encoding="utf-8") as f:
            source = f.read()
        # The two agent.ainvoke sites must both live under wait_for
        # We assert the file no longer contains a bare `await agent.ainvoke(`
        # without a preceding `wait_for(`.
        import re
        # Bare pattern: `= await agent.ainvoke(` NOT preceded by `wait_for(`.
        # Simplest check: bare `await agent.ainvoke` should be zero occurrences.
        bare = re.findall(r"^\s*(?:result|retry_result)\s*=\s*await\s+agent\.ainvoke\(", source, flags=re.M)
        assert bare == [], (
            f"agents/sci_judgment.py has {len(bare)} unbounded agent.ainvoke "
            "call(s) — G-5 requires asyncio.wait_for wrapping"
        )
        assert "asyncio.wait_for" in source

    def test_gst_react_wrapped_in_wait_for(self):
        with open("agents/gst_judgment.py", encoding="utf-8") as f:
            source = f.read()
        import re
        bare = re.findall(r"^\s*(?:result|retry_result)\s*=\s*await\s+agent\.ainvoke\(", source, flags=re.M)
        assert bare == [], (
            f"agents/gst_judgment.py has {len(bare)} unbounded agent.ainvoke "
            "call(s) — G-5 requires asyncio.wait_for wrapping"
        )

    def test_non_legal_chain_wrapped_in_wait_for(self):
        with open("agents/non_legal.py", encoding="utf-8") as f:
            source = f.read()
        assert "asyncio.wait_for" in source, (
            "agents/non_legal.py must wrap chain.ainvoke in asyncio.wait_for"
        )

    def test_memory_rewrite_wrapped_in_wait_for(self):
        with open("agents/memory.py", encoding="utf-8") as f:
            source = f.read()
        # `asyncio.to_thread(_rewrite_query, ...)` must sit inside
        # `asyncio.wait_for(...)`.
        idx = source.index("to_thread(_rewrite_query")
        window = source[max(0, idx - 200):idx + 100]
        assert "wait_for" in window, (
            "agents/memory.py must wrap the rewrite call in asyncio.wait_for"
        )


# ---------------------------------------------------------------------------
# G-6: silent bare `except: pass` sites now log
# ---------------------------------------------------------------------------

class TestSilentExceptFixes:
    def test_judgment_es_task_result_now_logs(self):
        with open("agents/judgment.py", encoding="utf-8") as f:
            source = f.read()
        # The old pattern was `except Exception:` on a bare line followed
        # by `prelim_result = {...}`. The fix adds `as es_err` and a log.
        assert "except Exception as es_err" in source, (
            "agents/judgment.py:415 must catch with `as es_err` and log — "
            "the bare except silently hid ES failures in prod"
        )

    def test_document_collection_probe_now_logs(self):
        with open("agents/document.py", encoding="utf-8") as f:
            source = f.read()
        # State-tolerant: either (a) the helper still exists AND its
        # silent-except was P0-fixed to log; OR (b) the whole helper was
        # deleted in P2 (dead-code sweep). Either state is acceptable;
        # what's NOT acceptable is a bare `except Exception:` returning
        # False with no logging.
        if "def _collection_has_data" in source:
            idx = source.index("def _collection_has_data(")
            next_def = source.find("\ndef ", idx + 1)
            probe_body = source[idx:next_def] if next_def > 0 else source[idx:]
            assert "except Exception as e" in probe_body, (
                "agents/document.py _collection_has_data must log "
                "ChromaDB probe failures (P0 short_err fix)"
            )
        # Regardless of whether the helper survives, the raw bare-except
        # pattern that used to hide prod failures must not exist.
        assert "except Exception:\n        return False" not in source


# ---------------------------------------------------------------------------
# G-2: Orchestrator guard on Drafting + has_files
# ---------------------------------------------------------------------------

class TestOrchestratorDraftingGuard:
    """When Drafting is planned AND files were uploaded AND every agent returns
    empty, the synthesizer must NOT fire the generic web-search fallback —
    it must return a specific temporary-failure message."""

    def test_guard_message_present_in_source(self):
        with open("agents/orchestrator.py", encoding="utf-8") as f:
            source = f.read()
        # The guard message the audit specifies
        assert "high load" in source
        assert "still attached" in source
        # Reference the Buglist for future maintainers
        assert "Bug #5" in source

    def test_guard_suppresses_web_fallback_when_drafting_and_files(self):
        """Behavioural: when Drafting is planned and files are attached and
        all agent results are empty, `web_search_fallback` must NOT be
        called and the returned final_response must name the retry policy."""
        # Import lazily so pytest can collect even without full env
        from agents.orchestrator import orchestrator_synthesize_node
        from core.state import FileContextData

        # Craft a minimal state that hits the "not valid_results" branch
        # with Drafting planned and files present.
        state = {
            "agent_results": {
                "Drafting": MagicMock(content="", error=None, sources=[]),
            },
            "task": "Drafting",
            "tasks_planned": ["Drafting"],
            "query": "make me a writ of mandamus from these PDFs",
            "original_query": "make me a writ of mandamus from these PDFs",
            "user_intent": None,
            "file_context": {
                "file_names": ["case1.pdf", "case2.pdf"],
                "chromadb_collections": ["thread_xyz"],
                "extracted_texts": {},
            },
            "response_instructions": "",
        }

        # Make .content empty so it hits the empty-results branch
        state["agent_results"]["Drafting"].content = ""
        state["agent_results"]["Drafting"].error = None

        # Sanity: FileContextData.from_state reports files
        fc = FileContextData.from_state(state)
        assert fc and fc.has_content

        with patch("core.agent_fallback.web_search_fallback",
                   new_callable=AsyncMock) as mock_web:
            mock_web.return_value = MagicMock(content="generic essay",
                                              sources=[],
                                              tokens_consumed=0)
            result = asyncio.run(orchestrator_synthesize_node(state))

        # web_search_fallback must NOT have been called
        assert mock_web.call_count == 0, (
            "web_search_fallback fired despite Drafting+files guard "
            "(this is Buglist Bug #5's exact regression)"
        )
        # The temporary-failure message must mention high load and files
        response = result.get("final_response", "")
        assert "high load" in response
        assert "case1.pdf" in response or "attached" in response.lower()


# ---------------------------------------------------------------------------
# G-10: Checkpointer hard-fail when REQUIRE_POSTGRES=1
# ---------------------------------------------------------------------------

class TestCheckpointerHardFail:
    def test_require_postgres_without_url_raises(self, monkeypatch):
        """REQUIRE_POSTGRES=1 + no POSTGRES_URL must raise RuntimeError."""
        # Re-import cleanly with the required env
        import importlib
        import core.settings
        import core.checkpointer

        monkeypatch.setenv("REQUIRE_POSTGRES", "1")
        monkeypatch.setattr(core.settings, "POSTGRES_URL", "")

        importlib.reload(core.checkpointer)

        with pytest.raises(RuntimeError, match="REQUIRE_POSTGRES"):
            asyncio.run(core.checkpointer.create_checkpointer())

        # Clean up the module state so other tests don't inherit the reload
        monkeypatch.delenv("REQUIRE_POSTGRES", raising=False)
        importlib.reload(core.checkpointer)

    def test_no_env_falls_back_to_memory(self, monkeypatch):
        """No REQUIRE_POSTGRES + no POSTGRES_URL = MemorySaver, no raise."""
        import importlib
        import core.settings
        import core.checkpointer

        monkeypatch.delenv("REQUIRE_POSTGRES", raising=False)
        monkeypatch.setattr(core.settings, "POSTGRES_URL", "")

        importlib.reload(core.checkpointer)

        checkpointer, pool = asyncio.run(core.checkpointer.create_checkpointer())
        assert pool is None
        from langgraph.checkpoint.memory import MemorySaver
        assert isinstance(checkpointer, MemorySaver)
