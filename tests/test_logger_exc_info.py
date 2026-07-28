"""Regression tests for `StructuredLogger` `exc_info` handling.

Prior to this fix, `StructuredLogger._log` passed the `exc_info` argument
straight through to `Logger.makeRecord`. Stdlib's own `Logger._log` first
coerces `exc_info=True` to the current `sys.exc_info()` tuple, and
`exc_info=<exception_instance>` to `(type, value, tb)`, before making the
record — our wrapper skipped that coercion.

The consequence was silent and severe. Every call site using the idiomatic
`log.error("msg", exc_info=True)` from inside an `except` block (~38 sites
across the codebase: Drafting, Document, Judgment, SCI, Legislation,
Newacts, Scenario, Constitution/Maxim, Orchestrator, Memory, ChatStore,
AgentFallback, gateway handlers) ended up storing `record.exc_info = True`.
Later, the `AgentFormatter.format` line

    record.exc_text = self.formatException(record.exc_info)

crashed inside stdlib's `formatException` with

    TypeError: 'bool' object is not subscriptable

because stdlib does `tb = ei[2]` on the passed-in value. That TypeError
propagated as if it were the original exception, shadowing whatever the
caller was actually trying to log (e.g. a Gemini 429 RESOURCE_EXHAUSTED).

The fix mirrors stdlib's coercion in `StructuredLogger._log`. These tests
pin all three input shapes and confirm no `TypeError` escapes the logger.
"""
from __future__ import annotations

import logging

import pytest

from core.logger import StructuredLogger


@pytest.fixture()
def slog() -> StructuredLogger:
    """Fresh StructuredLogger backed by an isolated stdlib logger.

    Adds a NullHandler so the formatter path runs but nothing prints;
    what matters is that `handle` completes without raising.
    """
    base = logging.getLogger(f"test_logger_exc_info.{id(object())}")
    base.setLevel(logging.DEBUG)
    base.addHandler(logging.NullHandler())
    return StructuredLogger(base)


def test_error_exc_info_true_inside_except_does_not_raise(slog):
    """The pre-fix TypeError signature. Must not raise."""
    try:
        raise ValueError("simulated upstream failure")
    except ValueError:
        # `exc_info=True` is the idiom the codebase uses everywhere.
        # Before the fix this crashed inside stdlib's formatException.
        slog.error("upstream call failed", error="simulated", exc_info=True)


def test_error_exc_info_exception_instance_does_not_raise(slog):
    """Passing the exception object directly is also stdlib-supported."""
    try:
        raise RuntimeError("boom")
    except RuntimeError as e:
        slog.error("boom happened", exc_info=e)


def test_error_exc_info_none_does_not_raise(slog):
    """Default path — no exception context at all."""
    slog.error("nothing exceptional here")


def test_error_exc_info_true_outside_except_does_not_raise(slog):
    """`exc_info=True` outside an active exception is a no-op in stdlib —
    `sys.exc_info()` returns `(None, None, None)`. Must not crash."""
    slog.error("no active exception", exc_info=True)


def test_info_debug_warning_do_not_accept_exc_info():
    """Guardrail on the public API: only error/critical take exc_info.
    If someone adds exc_info to info() by mistake, this test flags it."""
    import inspect
    for name in ("debug", "info", "warning"):
        sig = inspect.signature(getattr(StructuredLogger, name))
        assert "exc_info" not in sig.parameters, (
            f"StructuredLogger.{name} should not accept exc_info — "
            f"it's not an error-level log."
        )
    for name in ("error", "critical"):
        sig = inspect.signature(getattr(StructuredLogger, name))
        assert "exc_info" in sig.parameters, (
            f"StructuredLogger.{name} must accept exc_info."
        )
