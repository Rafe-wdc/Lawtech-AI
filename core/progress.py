"""Agent progress streaming — emit granular status updates to SSE clients.

Usage inside any agent node:

    from core.progress import progress

    async def legislation_node(state):
        progress("legislation", "Parsing query for section references...")
        parsed = parse_section_info(query)

        progress("legislation", "Searching Elasticsearch...", detail="3 search strategies")
        hits = await search(...)

        progress("legislation", "Found 5 matching provisions", found=5)
        progress("legislation", "Generating response...")

Each call emits a custom SSE event instantly visible to the frontend.
"""

from __future__ import annotations

import time
from typing import Any

from core.logger import get_logger

log = get_logger("Progress")


def progress(
    agent: str,
    message: str,
    *,
    detail: str | None = None,
    found: int | None = None,
    step: str | None = None,
    substep: bool = False,
) -> None:
    """Emit a granular progress event to the SSE stream.

    Args:
        agent: Agent name (e.g. "legislation", "judgment")
        message: Human-readable status message
        detail: Optional extra detail (e.g. "Section 498A of IPC")
        found: Optional count of items found (e.g. 5 hits)
        step: Optional step identifier for frontend grouping
        substep: If True, this is a sub-step (indented in UI)
    """
    try:
        from langgraph.config import get_stream_writer
        writer = get_stream_writer()
    except (RuntimeError, Exception):
        # Not in a streaming context (batch mode) — log only
        log.debug("progress (non-streaming)", agent=agent, message=message)
        return

    event: dict[str, Any] = {
        "type": "progress",
        "agent": agent,
        "message": message,
        "ts": time.time(),
    }
    if detail is not None:
        event["detail"] = detail
    if found is not None:
        event["found"] = found
    if step is not None:
        event["step"] = step
    if substep:
        event["substep"] = True

    writer(event)
