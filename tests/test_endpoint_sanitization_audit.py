"""Architectural test: every LLM-returning endpoint must sanitize its output.

Background:
    The guardrail-level runaway sanitizer (`core/sanitize.py:sanitize_output`)
    is the central defense against LLM streaming runaway bugs (e.g. the
    124k-dash table separator in thread
    73a59cc4-fbfa-4d7b-b493-948a974c1496). It only protects responses that
    pass through it. Endpoints that return LLM-generated text directly --
    without going through the LangGraph guardrail node and without calling
    sanitize_output -- are unprotected.

This test enumerates every gateway endpoint, classifies it (LLM-returning vs
not), and asserts the LLM-returning ones either:
    a) route through the agent graph (covered by guardrail_output_node), OR
    b) directly call sanitize_output() in the handler or its called modules.

If a new endpoint is added later that returns LLM content without
sanitization, this test catches it before it ships.

Pure-Python; no server needed.

Usage:
    pytest tests/test_endpoint_sanitization_audit.py -v
"""
from __future__ import annotations

import inspect
import re

import pytest


# ---------------------------------------------------------------------------
# Manifest of LLM-returning endpoints + how each one is expected to sanitize.
#
# When you add a new endpoint that returns LLM content, add it here. The test
# below asserts each handler's source / called-module source contains the
# `expected_via` token.
# ---------------------------------------------------------------------------

_LLM_ENDPOINTS = [
    # (handler_attr_name_in_gateway,         "expected_via")
    # Graph-routed endpoints -- covered by guardrail_output_node automatically:
    ("search",                                "run_chat_pipeline|agent_graph"),
    ("search_stream",                         "run_chat_pipeline"),
    ("chat_with_files",                       "run_chat_pipeline"),
    ("continue_draft",                        "agent_graph|continue_draft_node"),
    # Direct-LLM endpoints -- must call sanitize_output() in the handler chain:
    ("export_document_endpoint",              "sanitize_output"),
    ("fix_draft_endpoint",                    "sanitize_output"),
    ("compliance_check_endpoint",             "sanitize_output"),
    ("add_statute_refs_endpoint",             "sanitize_output"),
    ("generate_toa_endpoint",                 "sanitize_output"),
    ("generate_research_memo",                "sanitize_output"),
]


# Endpoints that do NOT return LLM content. They are exempt by inclusion in
# this set. Mostly: thread CRUD, file CRUD, metrics, frontend, etc.
_NON_LLM_ENDPOINTS = {
    "serve_frontend",
    "integration_poll",
    "delete_vectordb",
    "list_thread_files",
    "delete_thread_files",
    "prometheus_metrics",
    "admin_fallback_logs",
    "admin_fallback_stats",
    "admin_usage_stats",
    "admin_quality_stats",
    "list_threads",
    "get_thread_messages",
    "save_turn_endpoint",
    "submit_feedback",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _handler_source(handler_name: str) -> str:
    """Return the source of `core.gateway.<handler_name>` (the @app.* fn)."""
    import core.gateway as gw
    if not hasattr(gw, handler_name):
        pytest.fail(
            f"core.gateway has no handler named {handler_name!r}. "
            "Either the endpoint was renamed (update _LLM_ENDPOINTS) "
            "or removed (drop it from the manifest)."
        )
    fn = getattr(gw, handler_name)
    # The handler is wrapped by @app.post / @limiter.limit decorators. We
    # want the inner async def's source. inspect.getsource follows the
    # __wrapped__ chain for limiter, but FastAPI doesn't always preserve it.
    # Fallback: read core/gateway.py and grep around the def.
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):
        src_path = inspect.getsourcefile(gw)
        with open(src_path, encoding="utf-8") as f:
            content = f.read()
        # Find `async def <handler_name>(` and read until the next `^@app.`
        # or end of file.
        m = re.search(rf"async def {handler_name}\b.*?(?=\n@app\.|\Z)",
                      content, re.DOTALL)
        if not m:
            pytest.fail(f"Could not locate handler {handler_name} in source")
        return m.group(0)


def _module_source(module_path: str) -> str:
    """Read a module's source from disk (for transitive checks)."""
    import importlib
    mod = importlib.import_module(module_path)
    return inspect.getsource(mod)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLLMEndpointSanitization:
    """Every LLM-returning endpoint must sanitize its output."""

    @pytest.mark.parametrize("handler_name,expected_via", _LLM_ENDPOINTS)
    def test_handler_sanitizes(self, handler_name, expected_via):
        src = _handler_source(handler_name)
        patterns = expected_via.split("|")
        ok = any(p in src for p in patterns)

        # For graph-routed handlers, sanitize_output is reached transitively
        # via agents/guardrail.py. We accept any of the expected tokens.
        if not ok:
            # Last resort: scan a couple of modules the handler obviously
            # delegates to, in case the safe-call sits one level down.
            for delegate in ("core.fix_draft", "core.compliance",
                             "core.statute_refs", "core.toa",
                             "core.memo", "core.export"):
                if delegate.split(".")[-1] in src:
                    delegate_src = _module_source(delegate)
                    if "sanitize_output(" in delegate_src:
                        ok = True
                        break

        assert ok, (
            f"Endpoint handler {handler_name!r} did not match any of the "
            f"expected sanitization markers ({expected_via}). "
            f"If this endpoint genuinely doesn't return LLM content, add it "
            f"to _NON_LLM_ENDPOINTS in this test. Otherwise wire it through "
            f"the agent graph or call sanitize_output() before returning."
        )


class TestNoUnaccountedEndpoint:
    """If a new endpoint is added to core/gateway.py and isn't in either the
    LLM manifest or the non-LLM exempt set, fail loudly. Keeps the architecture
    audit comprehensive."""

    def test_every_endpoint_is_classified(self):
        import core.gateway
        src_path = inspect.getsourcefile(core.gateway)
        with open(src_path, encoding="utf-8") as f:
            content = f.read()
        # Find all `async def <name>(` immediately after `@app.<verb>(...)`
        endpoints = re.findall(
            r"@app\.(?:post|get|delete)\([^\n]+\n(?:@[^\n]+\n)*async def (\w+)\b",
            content,
        )
        known_llm = {e[0] for e in _LLM_ENDPOINTS}
        classified = known_llm | _NON_LLM_ENDPOINTS
        unclassified = sorted(set(endpoints) - classified)
        assert not unclassified, (
            f"Gateway has {len(unclassified)} endpoint(s) not classified in "
            f"this audit: {unclassified}. Decide for each: does it return "
            f"LLM content? If yes -> add to _LLM_ENDPOINTS with the expected "
            f"sanitization marker. If no -> add to _NON_LLM_ENDPOINTS."
        )


class TestSanitizeOutputIsImported:
    """A regression check: the modules we expect to use sanitize_output
    still import it. If a refactor removes the import, this fires before
    runtime."""

    @pytest.mark.parametrize("module_path", [
        "agents.guardrail",
        "core.compliance",
        "core.fix_draft",
        "core.memo",
        "core.toa",
        "core.statute_refs",
        "core.export",
    ])
    def test_module_imports_sanitize_output(self, module_path):
        src = _module_source(module_path)
        assert "sanitize_output" in src, (
            f"{module_path} no longer references sanitize_output -- a refactor "
            f"may have removed the central runaway-protection hook."
        )
