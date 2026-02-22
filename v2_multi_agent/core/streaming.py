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

from types import SimpleNamespace

from langgraph.config import get_stream_writer


async def stream_chain_response(chain, inputs: dict):
    """Invoke a LangChain chain with token-by-token streaming.

    In streaming context (graph.astream with custom mode):
        Uses chain.astream() and emits each token via get_stream_writer().
    In batch context (graph.ainvoke):
        Falls back to chain.ainvoke() — no streaming, no writer needed.

    Returns an object with .content (str) and .usage_metadata (dict),
    matching the AIMessage interface so agent code needs minimal changes.
    """
    try:
        writer = get_stream_writer()
    except RuntimeError:
        # Not in streaming context (batch endpoint) — use regular async invoke
        return await chain.ainvoke(inputs)

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
