"""Regression tests for the self_refine fail-closed cutover (2026-09-09).

Bug 4 (harness audit finding #4): every unrecoverable critic path in
``core/self_refine.py`` used to return ``Critique(passes=True, confidence=0.0)``.
Downstream code checked only ``if critique.passes:`` — so timeouts,
malformed JSON, and rate-limit errors were logged as "passed" and the
response shipped unaudited with no visible signal. That's the exact
wrong-language / hallucination-substitution defect the self_refine layer
exists to catch.

Fix: every fail path returns a Critique flagged with a synthetic
``verifier_unavailable`` violation. Downstream:
    1. Counter `_verifier_failure_counters[(agent, reason)]` bumps.
    2. Structured `self_refine_critic_unavailable reason=...` WARN log.
    3. `core.audit_status.mark_unverified()` sets a request-scoped flag
       that `chat_runner` reads onto the `done` SSE event as
       ``x_audit_status="unverified"``.
    4. One fallback refine attempt on the same violations block.
    5. `AuditOutcome.unverified_critic_failed` on the completion log.

This module verifies each of those pieces without hitting Gemini.

Test infrastructure note: `core.logger.get_logger` sets
``propagate=False`` on its named loggers, so pytest's `caplog` (which
taps the root logger) doesn't see them by default. Tests attach a
capture handler directly to ``v2.SelfRefine``. The ``audit_status``
ContextVar is scoped to the current task, so assertions on it must run
inside the same coroutine that invoked ``self_refine``.

Usage:
    pytest tests/test_self_refine_fail_closed.py -v
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from config.intent import UserIntent
from core import audit_status
from core.self_refine import (
    AuditOutcome,
    Critique,
    Violation,
    VERIFIER_UNAVAILABLE_FIELD,
    _critique_is_verifier_unavailable,
    _verifier_unavailable_critique,
    get_verifier_stats,
    reset_verifier_stats,
    self_refine,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

class _LogCaptureHandler(logging.Handler):
    """Direct handler for `v2.SelfRefine` since propagate=False."""
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def sr_logs():
    """Attach a capture handler to the SelfRefine logger for the duration
    of the test. Removed on teardown so state doesn't leak across tests."""
    handler = _LogCaptureHandler()
    logger = logging.getLogger("v2.SelfRefine")
    logger.addHandler(handler)
    prev_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)


def _log_messages(handler: _LogCaptureHandler, min_level: int = logging.INFO) -> list[str]:
    """Format captured records the way the structured logger renders — the
    message plus every extra kv concatenated so assertions can grep for
    outcome=X, reason=X, etc."""
    out: list[str] = []
    for r in handler.records:
        if r.levelno < min_level:
            continue
        extras = getattr(r, "_extra_kv", {}) or {}
        rendered = r.getMessage()
        if extras:
            rendered += " | " + " | ".join(f"{k}={v}" for k, v in extras.items())
        out.append(rendered)
    return out


def _strong_intent() -> UserIntent:
    """An intent that forces the self_refine loop to run (has directives,
    high confidence). Not a language directive — those bring in the
    language-mismatch code path we don't need here."""
    return UserIntent(
        response_depth="detailed",
        additional_instructions="Cite only Supreme Court judgments.",
        confidence=0.95,
    )


def _big_response() -> str:
    """A response above ``min_response_chars`` (500) so the length gate
    doesn't skip the loop before the critic even runs."""
    return "This is a well-formed legal response. " * 25  # ~1000 chars


@pytest.fixture(autouse=True)
def _reset_state():
    """Every test starts with a clean counter."""
    reset_verifier_stats()
    yield
    reset_verifier_stats()


# ---------------------------------------------------------------------------
# Schema pins — the fail-closed path uses these
# ---------------------------------------------------------------------------

class TestVerifierUnavailableCritique:
    def test_returns_passes_false(self):
        c = _verifier_unavailable_critique("timeout")
        assert c.passes is False
        assert c.confidence == 0.0

    def test_carries_verifier_unavailable_violation(self):
        c = _verifier_unavailable_critique("timeout", "detail here")
        assert len(c.violations) == 1
        v = c.violations[0]
        assert v.field == VERIFIER_UNAVAILABLE_FIELD
        assert "timeout" in v.issue
        assert v.severity == "critical"  # survives the placeholder_marker filter

    def test_helper_detects_unavailable_critique(self):
        c = _verifier_unavailable_critique("parse_error")
        assert _critique_is_verifier_unavailable(c) is True

    def test_helper_rejects_normal_critique(self):
        c = Critique(passes=True, confidence=0.9, violations=[])
        assert _critique_is_verifier_unavailable(c) is False

    def test_helper_rejects_critique_with_real_violations(self):
        v = Violation(
            field="language", issue="wrong language",
            severity="critical", suggested_fix="rewrite in Marathi",
        )
        c = Critique(passes=False, confidence=0.85, violations=[v])
        assert _critique_is_verifier_unavailable(c) is False


# ---------------------------------------------------------------------------
# Test D — critic timeout is unverified, not passed
# ---------------------------------------------------------------------------

class TestCriticTimeoutFailsClosed:
    def test_circuit_open_returns_unverified_response(self, sr_logs):
        """Exercise the fail-closed path via the Flash-circuit-open early
        return in `_critique`. The audit_status contextvar is scoped to
        the task, so assertions on it must run inside the same async
        body that invoked self_refine.
        """
        async def _body():
            audit_status.reset_audit_status()
            with patch(
                "core.clients.is_gemini_flash_available", return_value=False,
            ):
                out, history = await self_refine(
                    _big_response(), "test query", _strong_intent(),
                )
            # 1. History contains a verifier_unavailable Critique
            assert len(history) >= 1
            assert _critique_is_verifier_unavailable(history[0])
            # 2. Audit flag set inside the task
            assert audit_status.get_audit_status() == "unverified"
            return out

        out = asyncio.run(_body())

        # 3. Best-effort ship: response returned
        assert isinstance(out, str)
        assert len(out) > 0

        # 4. Counter bumped for (self_refine, flash_circuit_open)
        stats = get_verifier_stats()
        assert stats["total"] >= 1
        reasons = [row["reason"] for row in stats["by_agent_reason"]]
        assert "flash_circuit_open" in reasons

        # 5. WARN log emitted
        msgs = _log_messages(sr_logs, logging.WARNING)
        assert any("self_refine_critic_unavailable" in m for m in msgs), (
            f"Expected `self_refine_critic_unavailable` in {msgs!r}"
        )

    def test_asyncio_timeout_bucketed_correctly(self):
        """A raw ``asyncio.TimeoutError`` inside the critic path should
        classify as ``reason='timeout'`` in the counter."""
        from core.self_refine import _classify_critic_exception
        assert _classify_critic_exception(asyncio.TimeoutError()) == "timeout"


# ---------------------------------------------------------------------------
# Test E — critic parse/unexpected error is unverified, not passed
# ---------------------------------------------------------------------------

class TestCriticParseErrorFailsClosed:
    def test_generic_exception_returns_unverified(self, sr_logs):
        """Any unexpected exception inside the critic invocation routes
        through `_critique`'s outer `except Exception` — the fail-closed
        handler that increments the counter and marks the request
        unverified."""
        async def _body():
            audit_status.reset_audit_status()
            with patch(
                "core.clients.is_gemini_flash_available", return_value=True,
            ), patch(
                "core.self_refine.get_gemini_flash_lite",
                side_effect=RuntimeError("simulated LLM boot failure"),
            ):
                out, history = await self_refine(
                    _big_response(), "test query", _strong_intent(),
                )
            assert len(history) >= 1
            assert _critique_is_verifier_unavailable(history[0])
            assert audit_status.get_audit_status() == "unverified"
            return out

        out = asyncio.run(_body())
        assert isinstance(out, str) and out

        stats = get_verifier_stats()
        assert stats["total"] >= 1
        # RuntimeError classified as "unexpected"
        reasons = [row["reason"] for row in stats["by_agent_reason"]]
        assert "unexpected" in reasons


# ---------------------------------------------------------------------------
# Test F — genuine critic pass is untouched
# ---------------------------------------------------------------------------

class TestGenuinePassIsUntouched:
    def test_healthy_pass_returns_response_unchanged(self, sr_logs):
        """When the critic returns a real passes=True with non-zero
        confidence, no fail-closed machinery fires:
          - No counter bump
          - No `x_audit_status` mark
          - AuditOutcome.passed on the terminal log
          - Response byte-identical to input"""
        async def _healthy_critique(*args, **kwargs):
            return Critique(passes=True, confidence=0.9, violations=[])

        async def _body():
            audit_status.reset_audit_status()
            with patch("core.self_refine._critique", new=_healthy_critique):
                out, history = await self_refine(
                    _big_response(), "test query", _strong_intent(),
                )
            assert audit_status.get_audit_status() == ""
            return out, history

        out, history = asyncio.run(_body())
        response = _big_response()

        assert out == response
        assert len(history) == 1
        assert history[0].passes is True
        assert history[0].confidence == 0.9
        assert not _critique_is_verifier_unavailable(history[0])

        # No verifier failure counter change
        stats = get_verifier_stats()
        assert stats["total"] == 0

        # Terminal outcome log carries AuditOutcome.passed
        msgs = _log_messages(sr_logs, logging.INFO)
        assert any(
            "self_refine_completed" in m
            and f"outcome={AuditOutcome.passed.value}" in m
            for m in msgs
        ), (f"Expected `self_refine_completed outcome={AuditOutcome.passed.value}` "
            f"in {msgs!r}")


# ---------------------------------------------------------------------------
# Test G — counter increments monotonically
# ---------------------------------------------------------------------------

class TestCounterIncrementsOnRepeatedFailures:
    def test_three_failures_bump_counter_to_three(self):
        """Force the fail-closed path three times, assert the counter
        for (self_refine, flash_circuit_open) reads 3."""
        async def _body():
            for _ in range(3):
                with patch(
                    "core.clients.is_gemini_flash_available", return_value=False,
                ):
                    await self_refine(
                        _big_response(), "q", _strong_intent(),
                    )

        asyncio.run(_body())

        stats = get_verifier_stats()
        entry = next(
            (row for row in stats["by_agent_reason"]
             if row["agent"] == "self_refine"
             and row["reason"] == "flash_circuit_open"),
            None,
        )
        assert entry is not None, f"expected flash_circuit_open row in {stats}"
        assert entry["count"] == 3
        # Only one reason exercised, so total equals the row count.
        assert stats["total"] == 3


# ---------------------------------------------------------------------------
# Skip-when-trivial still works after the cutover
# ---------------------------------------------------------------------------

class TestSkipWhenTrivialUnchanged:
    def test_no_intent_still_skips(self):
        """The fail-closed cutover MUST NOT change the skip-when-trivial
        behavior — that would blow the request budget for every
        low-intent query."""
        async def _body():
            audit_status.reset_audit_status()
            out, history = await self_refine(
                _big_response(), "q", intent=None,
            )
            # No critic ran, no audit flag set.
            assert audit_status.get_audit_status() == ""
            return out, history

        out, history = asyncio.run(_body())
        assert history == []
        # A skipped run does not bump the verifier failure counter.
        assert get_verifier_stats()["total"] == 0
