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
# 90s covers large streaming responses (drafts, scenario analysis).
# Batch calls (no streaming) use 60s — they're simpler tasks.
_STREAM_TIMEOUT = 90
_BATCH_TIMEOUT = 60


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
        log.error("LLM streaming timed out after %ds", t)
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
    """Stream chain output token-by-token via the writer."""
    full = ""
    usage = {}
    async for chunk in chain.astream(inputs):
        token = chunk.content or ""
        if token:
            full += token
            writer({"type": "token", "content": token})
        if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
            usage = chunk.usage_metadata

    return SimpleNamespace(content=full, usage_metadata=usage)
