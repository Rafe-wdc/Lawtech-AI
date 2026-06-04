"""Tests for the streaming-time pad-char runaway sanitizer and the
guardrail's hard char cap.

Streaming sanitizer (core/streaming.py:_stream_with_writer):
    Aborts the LLM stream as soon as it emits >100 consecutive pad chars
    (-, _, =, space, *). The truncation notice is appended to both the
    writer (so the SSE client sees the cut-off) and the returned content.

Guardrail hard cap (agents/guardrail.py:guardrail_output_node):
    Even after sanitize_output collapses pad-char runs, the LLM can still
    legitimately emit more prose than is useful to display. MAX_FINAL_
    RESPONSE_CHARS (60k) clamps the final response with a suffix that
    nudges the user to narrow the query.

Usage:
    pytest tests/test_streaming_runaway.py -v
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# Streaming sanitizer -- exercise _stream_with_writer with a fake chain
# ---------------------------------------------------------------------------

class _FakeChunk:
    def __init__(self, content: str, usage: dict | None = None):
        self.content = content
        self.usage_metadata = usage


class _FakeChain:
    """Stand-in for a LangChain runnable that emits a scripted sequence
    of chunks via astream()."""
    def __init__(self, chunks: list[str]):
        self._chunks = chunks
        self._stream_consumed = 0

    def astream(self, inputs):
        # Async generator over the scripted chunks
        async def gen():
            for c in self._chunks:
                self._stream_consumed += 1
                yield _FakeChunk(c)
        return gen()


def _make_writer():
    """Test writer: stores everything emitted into a list."""
    events = []

    def writer(event):
        events.append(event)

    writer.events = events
    return writer


def _run(coro):
    """Run a coroutine synchronously -- avoids needing pytest-asyncio."""
    return asyncio.run(coro)


def test_clean_stream_passes_through_unchanged():
    """No pad-char runaway -- _stream_with_writer is a pass-through."""
    from core.streaming import _stream_with_writer

    chain = _FakeChain(["Hello ", "world. ", "This is fine."])
    writer = _make_writer()

    result = _run(_stream_with_writer(chain, {}, writer))

    assert result.content == "Hello world. This is fine."
    # All 3 tokens reached the writer in order
    contents = [e["content"] for e in writer.events if e.get("type") == "token"]
    assert contents == ["Hello ", "world. ", "This is fine."]


def test_runaway_dashes_aborts_stream():
    """An LLM streaming 200 dashes in one chunk should be cut off after 100."""
    from core.streaming import _stream_with_writer, _STREAM_PAD_RUN_THRESHOLD

    chain = _FakeChain([
        "| Aspect | A | B |\n",
        "| :--- | :",
        "-" * 200,            # the runaway -- triggers abort mid-chunk
        " more text that should never arrive",
        "additional chunks should never arrive either",
    ])
    writer = _make_writer()

    result = _run(_stream_with_writer(chain, {}, writer))

    # 1. Final content includes the truncation marker
    assert "truncated" in result.content.lower()
    # 2. Final content does NOT contain a giant dash run
    assert "-" * (_STREAM_PAD_RUN_THRESHOLD + 1) not in result.content
    # 3. Later chunks never reached the writer (stream was aborted)
    contents = " ".join(e["content"] for e in writer.events
                        if e.get("type") == "token")
    assert "more text" not in contents
    assert "additional chunks" not in contents
    # 4. The fake chain was NOT fully consumed (the LLM call was cancelled
    #    server-side via aclose)
    assert chain._stream_consumed < 5


def test_runaway_split_across_chunks_still_aborts():
    """Pad chars accumulating across multiple chunks should still trigger."""
    from core.streaming import _stream_with_writer

    # 50 dashes + 50 dashes + 5 dashes = 105 cumulative -- crosses threshold
    chain = _FakeChain(["pre ", "-" * 50, "-" * 50, "-" * 5, " never seen"])
    writer = _make_writer()

    result = _run(_stream_with_writer(chain, {}, writer))

    assert "truncated" in result.content.lower()
    assert "never seen" not in result.content


def test_pad_run_resets_on_non_pad_char():
    """If the LLM emits 99 dashes, then prose, then 99 more dashes,
    the counter must reset -- neither standalone run crosses 100."""
    from core.streaming import _stream_with_writer

    chain = _FakeChain([
        "-" * 99,
        " then real prose here ",
        "-" * 99,
        " done.",
    ])
    writer = _make_writer()

    result = _run(_stream_with_writer(chain, {}, writer))

    # Should NOT have been aborted -- run counter resets on " "
    assert "truncated" not in result.content.lower()
    assert "done." in result.content


def test_spaces_padding_also_aborts():
    """Padding can be spaces too (table cell alignment with whitespace)."""
    from core.streaming import _stream_with_writer

    chain = _FakeChain(["| Cell |", " " * 200, " trailing"])
    writer = _make_writer()

    result = _run(_stream_with_writer(chain, {}, writer))

    assert "truncated" in result.content.lower()


# ---------------------------------------------------------------------------
# Hard char cap in guardrail (Layer B)
# ---------------------------------------------------------------------------

class TestGuardrailCharCap:
    def test_cap_constant_is_sensible(self):
        from agents.guardrail import MAX_FINAL_RESPONSE_CHARS
        # Tight enough to bound display, generous enough for real legal answers
        assert 30_000 <= MAX_FINAL_RESPONSE_CHARS <= 200_000

    def test_truncation_suffix_present(self):
        from agents.guardrail import TRUNCATION_SUFFIX
        assert "truncated" in TRUNCATION_SUFFIX.lower()
        assert "narrower" in TRUNCATION_SUFFIX.lower()

    def test_cap_applied_in_handler_source(self):
        """The handler must actually call the cap logic. A future refactor
        that removes it should fail this test."""
        import agents.guardrail as g
        src = inspect.getsource(g.guardrail_output_node)
        assert "MAX_FINAL_RESPONSE_CHARS" in src
        assert "TRUNCATION_SUFFIX" in src


