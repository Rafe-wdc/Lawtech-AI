"""Token-by-token streaming helper for LangChain chains.

Uses LangGraph's get_stream_writer() to emit tokens during execution.
Falls back to regular ainvoke() when not in a streaming context (batch endpoint).

Usage:
    from core.streaming import stream_chain_response

    # In an agent node (replaces chain.invoke(inputs)):
    llm_response = await stream_chain_response(chain, inputs)
    # llm_response.content  — full response text
    # llm_response.usage_metadata  — token usage dict
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from langgraph.config import get_stream_writer

log = logging.getLogger("Streaming")

# Default timeout for a complete LLM generation.
# 180s covers large streaming responses with max_output_tokens=65535 plus
# occasional Google Gemini API stream hangs (observed: response_stream.readline()
# blocking indefinitely on transient network issues). One retry on timeout
# inside stream_chain_response gives a second chance before surfacing the error.
# Batch calls (no streaming) use 60s — they're simpler tasks.
_STREAM_TIMEOUT = 180
_BATCH_TIMEOUT = 60

# --- Streaming runaway protection ---
#
# LLMs sometimes get stuck "aligning" wide markdown tables and stream tens
# of thousands of repeated padding characters (dashes, spaces, asterisks)
# in a single cell. The post-stream guardrail catches this at the final
# response, but during streaming the user watches the dashes scroll past
# for minutes -- a terrible UX. We track the running tail of padding chars
# and abort the stream as soon as it crosses a threshold.
_PAD_CHARS = frozenset("-_= *")
_STREAM_PAD_RUN_THRESHOLD = 100
_STREAM_TRUNCATION_NOTICE = (
    "\n\n... [output truncated -- streaming runaway detected; please try a "
    "narrower query] ..."
)


async def stream_chain_response(
    chain,
    inputs: dict,
    *,
    retry: bool = True,
    timeout: int | None = None,
):
    """Invoke a LangChain chain with token-by-token streaming.

    In streaming context (graph.astream with custom mode):
        Uses chain.astream() and emits each token via get_stream_writer().
        If retry=True and the first attempt fails, emits a token_reset event
        so the frontend clears its buffer, then retries once.
    In batch context (graph.ainvoke):
        Falls back to chain.ainvoke() — no streaming, no writer needed.

    Args:
        chain: A runnable LangChain chain (prompt | llm).
        inputs: Dict of template variables.
        retry: Retry once on streaming failure (default True).
        timeout: Override the default generation timeout in seconds.
                 Defaults to _STREAM_TIMEOUT (streaming) or _BATCH_TIMEOUT (batch).

    Returns an object with .content (str) and .usage_metadata (dict),
    matching the AIMessage interface so agent code needs minimal changes.

    Raises:
        asyncio.TimeoutError: if the LLM does not respond within timeout seconds.
    """
    try:
        writer = get_stream_writer()
    except RuntimeError:
        # Not in streaming context (batch endpoint) — use regular async invoke
        t = timeout or _BATCH_TIMEOUT
        return await asyncio.wait_for(chain.ainvoke(inputs), timeout=t)

    t = timeout or _STREAM_TIMEOUT
    try:
        return await asyncio.wait_for(
            _stream_with_writer(chain, inputs, writer), timeout=t
        )
    except asyncio.TimeoutError:
        # Stream hung (often Google Gemini API transient — response_stream.readline()
        # blocking indefinitely). Retry once before giving up.
        if not retry:
            log.error("LLM streaming timed out after %ds", t)
            writer({"type": "token_reset"})
            raise
        log.warning("LLM streaming timed out after %ds, retrying once", t)
        writer({"type": "token_reset"})
        try:
            return await asyncio.wait_for(
                _stream_with_writer(chain, inputs, writer), timeout=t
            )
        except asyncio.TimeoutError:
            log.error("LLM streaming timed out after %ds (after retry)", t)
            writer({"type": "token_reset"})
            raise
    except Exception as e:
        if not retry:
            raise
        log.warning("Streaming failed, resetting and retrying once: %s",
                    str(e)[:200])
        # Tell the frontend to clear its token buffer before retry
        writer({"type": "token_reset"})
        return await asyncio.wait_for(
            _stream_with_writer(chain, inputs, writer), timeout=t
        )


async def _stream_with_writer(chain, inputs: dict, writer):
    """Stream chain output token-by-token via the writer.

    Aborts the stream early if the LLM emits >_STREAM_PAD_RUN_THRESHOLD
    consecutive padding chars (dashes, spaces, asterisks, etc.) -- a
    near-certain sign of a markdown-table column-alignment runaway. The
    truncation marker is appended to both the writer (so the client sees
    the cut-off) and the returned content (so the post-stream guardrail
    knows the response is intentionally truncated).
    """
    full = ""
    usage = {}
    last_ch: str | None = None  # most recent char (for "same-char" run tracking)
    same_char_run = 0
    truncated = False
    stream = chain.astream(inputs)
    async for chunk in stream:
        token = chunk.content or ""
        if token:
            # Walk char-by-char. The runaway pathology is always the
            # SAME char repeated (e.g. 124k dashes, 10k spaces, never
            # a mix). Tracking "same char" rather than "any pad char"
            # avoids false positives like "99 dashes then a space then
            # prose" -- which is normal output, not a runaway.
            for ch in token:
                if ch == last_ch and ch in _PAD_CHARS:
                    same_char_run += 1
                    if same_char_run > _STREAM_PAD_RUN_THRESHOLD:
                        truncated = True
                        break
                else:
                    last_ch = ch
                    same_char_run = 1 if ch in _PAD_CHARS else 0
            if truncated:
                # Do NOT emit this token's pad-char runaway to the
                # client; append a clear notice instead and abort.
                full += _STREAM_TRUNCATION_NOTICE
                writer({"type": "token",
                        "content": _STREAM_TRUNCATION_NOTICE})
                log.warning(
                    "Stream truncated -- pad-char runaway detected "
                    "(threshold=%d; emitted so far=%d chars)",
                    _STREAM_PAD_RUN_THRESHOLD, len(full),
                )
                # ONLY aclose the underlying stream when we explicitly
                # truncated -- this cancels the LLM call server-side. On
                # NORMAL stream completion we must NOT aclose, because
                # LangChain's astream wraps a network connection that
                # downstream nodes (synthesize, guardrail) rely on for
                # the final-event emission. An earlier version of this
                # code called aclose() unconditionally in a finally
                # block and broke the SSE `result` event on the
                # integration test (CI run 26947404618).
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:
                        pass
                break
            full += token
            writer({"type": "token", "content": token})
        if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
            usage = chunk.usage_metadata

    return SimpleNamespace(content=full, usage_metadata=usage)
