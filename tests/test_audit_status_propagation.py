"""The "unverified" flag set inside a graph node must reach the request.

Regression for 2026-11-12: ``core.audit_status`` kept the status string in
a ContextVar. LangGraph runs every node in its own task or worker thread
with a copy of the request's context, so ``mark_unverified()`` inside the
drafting node changed only that copy, and ``chat_runner`` never put
``x_audit_status`` on the ``done`` event. These tests run a real LangGraph
graph, plain child tasks and threads, and two requests at once.
"""
from __future__ import annotations

import asyncio
from typing import TypedDict

from langgraph.graph import END, StateGraph

from core import audit_status


class _State(TypedDict):
    x: int


def _graph(node):
    g = StateGraph(_State)
    g.add_node("n", node)
    g.set_entry_point("n")
    g.add_edge("n", END)
    return g.compile()


async def _async_node(state):
    audit_status.mark_unverified()
    return {"x": 1}


def _sync_node(state):
    audit_status.mark_unverified()
    return {"x": 1}


async def _quiet_node(state):
    return {"x": 1}


async def _run_graph(node) -> str:
    """What chat_runner does: reset, stream the graph, read the flag."""
    audit_status.reset_audit_status()
    async for _ in _graph(node).astream({"x": 0}, stream_mode=["updates", "custom"]):
        pass
    return audit_status.get_audit_status()


def test_async_node_mark_reaches_the_request():
    assert asyncio.run(_run_graph(_async_node)) == "unverified"


def test_sync_node_mark_reaches_the_request():
    assert asyncio.run(_run_graph(_sync_node)) == "unverified"


def test_node_that_does_not_mark_leaves_it_empty():
    assert asyncio.run(_run_graph(_quiet_node)) == ""


def test_child_task_and_thread_marks_reach_the_request():
    async def via_task():
        audit_status.reset_audit_status()
        await asyncio.create_task(_mark_async())
        return audit_status.get_audit_status()

    async def via_thread():
        audit_status.reset_audit_status()
        await asyncio.to_thread(audit_status.mark_unverified)
        return audit_status.get_audit_status()

    assert asyncio.run(via_task()) == "unverified"
    assert asyncio.run(via_thread()) == "unverified"


async def _mark_async():
    audit_status.mark_unverified()


def test_concurrent_requests_stay_separate():
    async def request(node):
        return await _run_graph(node)

    async def both():
        return await asyncio.gather(
            asyncio.create_task(request(_async_node)),
            asyncio.create_task(request(_quiet_node)),
        )

    marked, quiet = asyncio.run(both())
    assert marked == "unverified"
    assert quiet == ""


def test_reset_clears_an_earlier_mark():
    async def run():
        audit_status.reset_audit_status()
        audit_status.mark_unverified()
        audit_status.reset_audit_status()
        return audit_status.get_audit_status()

    assert asyncio.run(run()) == ""


def test_mark_without_reset_still_works_in_the_same_context():
    async def run():
        audit_status.mark_unverified()
        return audit_status.get_audit_status()

    assert asyncio.run(run()) == "unverified"


def test_fresh_context_starts_empty():
    async def run():
        return audit_status.get_audit_status()

    assert asyncio.run(run()) == ""
