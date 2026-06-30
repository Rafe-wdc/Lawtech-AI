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
# Threshold past which a same-char run is treated as a runaway. Bumped from
# 100 to 400 after the threshold fired on legitimate wide markdown-table
# header / separator rows (e.g. "Section 125 of CRPC" produced a 3-column
# comparison table whose separator row was emitted as ~120 dashes in a
# single cell — a normal LLM output, NOT the pathological 124k-dash loop
# the killer was designed for). 400 still catches the real runaway with
# orders-of-magnitude headroom.
_STREAM_PAD_RUN_THRESHOLD = 400
# When the run crosses the threshold, we DROP the excess pad chars but keep
# streaming. The first this-many chars of any run are emitted normally so
# small / medium tables render cleanly; only the tail above the threshold
# is silently swallowed. This replaces the previous "abort the entire
# stream" behaviour, which destroyed responses that had a single overly-
# padded cell (the user got nothing but the table header + a truncation
# notice). Post-stream sanitize still collapses anything that slips
# through to 8 chars per run.
_STREAM_PAD_RUN_EMIT_CAP = 64


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

    When the LLM gets stuck emitting a same-char run (the markdown-table
    column-alignment pathology — historically up to 124k dashes), we DROP
    the excess pad chars past _STREAM_PAD_RUN_THRESHOLD but keep the
    stream alive. Up to _STREAM_PAD_RUN_EMIT_CAP same-char pads are
    emitted per run; everything past that is silently swallowed until
    the LLM resumes with a different character.

    This replaces the earlier "abort the entire stream" behaviour, which
    destroyed otherwise-fine responses when a single cell had aggressive
    column padding (the user got the table header plus a truncation
    notice and nothing else — reported for "Section 125 of CRPC"). Post-
    stream sanitize still collapses anything that slips through.
    """
    full = ""
    usage = {}
    last_ch: str | None = None  # most recent same-char run anchor
    same_char_run = 0
    runaway_dropped = 0   # for log telemetry
    stream = chain.astream(inputs)
    async for chunk in stream:
        # `.text` normalizes Gemini 3.x list-of-content-blocks to a string and
        # leaves Gemini 2.5 plain string content unchanged. AIMessageChunk
        # exposes the same `.text` property as AIMessage.
        token = (chunk.text or "") if hasattr(chunk, "text") else (chunk.content or "")
        if not token:
            continue

        # Per-token output buffer. We walk char-by-char to detect same-char
        # runs of pad chars (NEVER mixed pad chars — those are normal
        # output, e.g. "99 dashes then a space" is fine). Pad chars past
        # _STREAM_PAD_RUN_EMIT_CAP within an active runaway are dropped
        # from both `full` and the emitted token.
        out_chars: list[str] = []
        for ch in token:
            if ch == last_ch and ch in _PAD_CHARS:
                same_char_run += 1
            else:
                last_ch = ch
                same_char_run = 1 if ch in _PAD_CHARS else 0

            # Inside the runaway window — drop the char from emission.
            if same_char_run > _STREAM_PAD_RUN_EMIT_CAP:
                runaway_dropped += 1
                # Log once per crossing the (much higher) original
                # threshold so we can still spot real pathologies in prod.
                if same_char_run == _STREAM_PAD_RUN_THRESHOLD + 1:
                    log.warning(
                        "Pad-char runaway in stream — collapsing "
                        "(char=%r threshold=%d emit_cap=%d run=%d)",
                        ch, _STREAM_PAD_RUN_THRESHOLD,
                        _STREAM_PAD_RUN_EMIT_CAP, same_char_run,
                    )
                continue
            out_chars.append(ch)

        if out_chars:
            out_token = "".join(out_chars)
            full += out_token
            writer({"type": "token", "content": out_token})

        if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
            usage = chunk.usage_metadata

    if runaway_dropped:
        log.info("Stream completed with pad-char runaway suppression",
                 chars_dropped=runaway_dropped, emitted_chars=len(full))

    return SimpleNamespace(content=full, usage_metadata=usage)
