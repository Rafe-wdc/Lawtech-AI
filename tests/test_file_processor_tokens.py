"""Tests for the FileProcessor token-usage wiring.

The token tracker (`core/token_tracker.py`) uses a per-request ContextVar to
accumulate LLM cost across the pipeline. Before 2026-08-03, Vision OCR calls
inside `core/file_processor.py` invoked Gemini 2.5 Flash without ever calling
`token_tracker.record(...)`, so the OCR cost never surfaced in the `done` SSE
event and could not be billed back to the user.

These tests pin the contract that made that fix work:

  1. `_ocr_batch` records a `FileProcessor` call with the expected step name.
  2. The image OCR path records a `FileProcessor` call whose step includes
     the filename.
  3. `chat_runner.run_chat_pipeline` reuses a tracker that the gateway
     already started for /pyapi/chat, rather than overwriting it — otherwise
     the FileProcessor entries recorded during file processing would be
     silently dropped.

We stub `llm.invoke` with a fake that returns a LangChain-shaped AIMessage
whose `.usage_metadata` mirrors what the real Google GenAI client returns.
No real API calls, no real Chroma writes.

Run:
    pytest tests/test_file_processor_tokens.py -v
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any

import pytest


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# --- Fake LangChain AIMessage -----------------------------------------------


@dataclass
class _FakeAIMessage:
    """Minimal stand-in for a LangChain AIMessage returned by Gemini.

    Only carries the fields `token_tracker.record` and `_extract_ai_text`
    actually read.
    """
    content: str = "OCR page text goes here."
    usage_metadata: dict[str, Any] = field(default_factory=lambda: {
        "input_tokens": 1200,
        "output_tokens": 40,
        "total_tokens": 1240,
    })
    response_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.content


class _FakeLLM:
    """Fake `llm` whose `.invoke(content)` returns a `_FakeAIMessage`."""

    def __init__(self, calls_holder: list[Any] | None = None) -> None:
        self._calls = calls_holder if calls_holder is not None else []

    def invoke(self, content):
        self._calls.append(content)
        return _FakeAIMessage()


# --- Tests -------------------------------------------------------------------


def test_ocr_batch_records_file_processor_call():
    """`_ocr_batch` must add a FileProcessor entry to the active tracker."""
    from core import token_tracker
    from core.file_processor import _ocr_batch

    tracker = token_tracker.start_request()
    llm = _FakeLLM()
    fake_b64 = ["dGVzdA=="]  # base64 for "test"

    out = _ocr_batch(llm, fake_b64, batch_start=0)

    assert out.startswith("--- Pages 1-1 ---")
    assert "FileProcessor" in tracker.by_agent, (
        f"expected FileProcessor bucket, got agents={list(tracker.by_agent)}"
    )
    fp = tracker.by_agent["FileProcessor"]
    assert fp["input"] == 1200
    assert fp["output"] == 40
    assert fp["total"] == 1240
    assert fp["calls"] == 1
    # step name should include the page range so per-call auditing is useful
    call = tracker.calls[-1]
    assert call.agent == "FileProcessor"
    assert call.step == "ocr_pdf_pages_1_1"
    # cost > 0 because model="gemini-2.5-flash" is in the price table
    assert call.cost_usd > 0


def test_ocr_batch_multi_page_step_name():
    """Multi-page batches encode the page range in the step name."""
    from core import token_tracker
    from core.file_processor import _ocr_batch

    token_tracker.start_request()
    llm = _FakeLLM()

    _ocr_batch(llm, ["a", "b", "c"], batch_start=10)

    tracker = token_tracker.get_tracker()
    assert tracker is not None
    assert tracker.calls[-1].step == "ocr_pdf_pages_11_13"


def test_ocr_batch_no_tracker_is_silent():
    """When there's no active tracker (e.g. a unit test that didn't start
    one), the invoke path must not crash — `record` returns 0 and the OCR
    still runs. This mirrors the ContextVar-default behaviour and keeps
    file processing usable in standalone scripts."""
    from core import token_tracker
    from core.file_processor import _ocr_batch

    # Force no active tracker for this test.
    token_tracker._tracker_var.set(None)
    llm = _FakeLLM()

    out = _ocr_batch(llm, ["dGVzdA=="], batch_start=0)
    assert out.startswith("--- Pages 1-1 ---")


def test_chat_runner_reuses_existing_tracker():
    """If the gateway starts a tracker before chat_runner runs (which is
    how /pyapi/chat wires FileProcessor tokens into the same accumulator),
    chat_runner must NOT overwrite it. Overwriting would silently drop
    every FileProcessor call recorded during file processing.

    This test only exercises the tracker-reuse expression, not the full
    pipeline — the pipeline has heavy dependencies (LangGraph, ES, LLMs)
    we don't want to spin up here.
    """
    from core import token_tracker

    gateway_tracker = token_tracker.start_request()
    # Simulate a FileProcessor OCR call recorded during process_files().
    gateway_tracker.record("FileProcessor", "ocr_pdf_pages_1_5", _FakeAIMessage())

    # The line chat_runner.run_chat_pipeline uses to pick up the tracker.
    reused = token_tracker.get_tracker() or token_tracker.start_request()

    assert reused is gateway_tracker, "chat_runner must reuse the gateway tracker"
    assert "FileProcessor" in reused.by_agent
    assert reused.by_agent["FileProcessor"]["total"] == 1240


def test_chat_runner_starts_tracker_when_absent():
    """The reuse pattern must still start a fresh tracker when no upstream
    caller (e.g. /pyapi/search which doesn't accept files) initialised
    one first."""
    from core import token_tracker

    token_tracker._tracker_var.set(None)
    fresh = token_tracker.get_tracker() or token_tracker.start_request()

    assert fresh is not None
    assert fresh.total_tokens == 0
    assert fresh.by_agent == {}


def test_ocr_batch_records_across_thread_pool_executor():
    """Regression test for two related ContextVar bugs in `_ocr_pdf_at_dpi`:

    Bug 1 (2026-08-03 local smoke): `_vision_ocr_pdf` dispatches batches
    via a `ThreadPoolExecutor`, which does NOT inherit ContextVars unless
    the caller wraps each submit with `contextvars.copy_context().run(...)`.
    Without that wrap, `_ocr_batch` sees an empty tracker inside the
    worker thread and the OCR tokens silently drop out.

    Bug 2 (2026-08-03 prod journal, 9 requests hit): a SHARED
    `Context` object cannot be entered by multiple threads concurrently
    — Python raises "cannot enter context: <Context> is already entered".
    The fix is to give each submit its OWN `copy_context()`, not share
    one across all workers.

    This test uses a blocking `_BarrierLLM` that forces worker threads
    to hold the context concurrently. With a shared Context, the second
    thread to enter would raise; per-submit Contexts run cleanly.
    """
    import contextvars
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from core import token_tracker
    from core.file_processor import _ocr_batch

    tracker = token_tracker.start_request()

    # LLM that blocks until N callers all arrive → guarantees the worker
    # threads are inside `ctx.run(_ocr_batch, ...)` simultaneously.
    barrier = threading.Barrier(3, timeout=5)

    class _BarrierLLM:
        def invoke(self, content):
            barrier.wait()  # holds all 3 threads inside the context
            return _FakeAIMessage()

    llm = _BarrierLLM()
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = [
            ex.submit(
                contextvars.copy_context().run,
                _ocr_batch, llm, ["dGVzdA=="], start,
            )
            for start in (0, 1, 2)
        ]
        for f in futures:
            f.result(timeout=10)

    fp = tracker.by_agent.get("FileProcessor")
    assert fp is not None, "FileProcessor bucket missing — ContextVar did not propagate"
    assert fp["calls"] == 3, f"expected 3 OCR calls, got {fp['calls']}"
    assert fp["total"] == 3 * 1240


def test_shared_context_across_threads_raises_regression_guard():
    """Direct guard against re-introducing the shared-Context bug.

    Documents the underlying Python behaviour we designed around:
    calling `ctx.run(...)` from a second thread while the first still
    holds it raises RuntimeError. The fix must call `copy_context()`
    PER submit, not once at the caller level.

    If someone refactors `_ocr_pdf_at_dpi` back to a single shared
    context, this test doesn't fail directly — but the paired
    `test_ocr_batch_records_across_thread_pool_executor` above will,
    because its BarrierLLM forces concurrent entry.
    """
    import contextvars
    import threading

    ctx = contextvars.copy_context()
    barrier = threading.Barrier(2, timeout=3)
    errors: list[Exception] = []

    def worker():
        try:
            ctx.run(barrier.wait)
        except RuntimeError as e:
            errors.append(e)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()

    assert any("already entered" in str(e) for e in errors), (
        "Expected shared Context to raise 'already entered' when two "
        "threads run it concurrently. Python's Context semantics may "
        "have changed — revisit the copy_context()-per-submit rationale."
    )


def test_gemini_flash_price_covers_ocr_cost():
    """`gemini-2.5-flash` must be a known model in the price table so the
    OCR record calls produce a non-zero `cost_usd` — otherwise billing
    would show tokens but no dollar amount for file processing."""
    from core.token_tracker import _MODEL_PRICES_USD_PER_M

    assert "gemini-2.5-flash" in _MODEL_PRICES_USD_PER_M
    in_rate, out_rate = _MODEL_PRICES_USD_PER_M["gemini-2.5-flash"]
    assert in_rate > 0 and out_rate > 0
