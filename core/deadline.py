"""Request-scoped deadline propagation.

Solves the "300 s outer timeout is a hard cutoff, not a budget" gap from the
2026-08-02 deep pipeline audit (see F.2). When a request hits the outer
`asyncio.wait_for(..., 300)` at [core/gateway.py] or the outer
`async_timeout(300)` in [core/chat_runner.py], every downstream
`asyncio.wait_for(local_timeout)` runs blind — none of them know the caller
only has, say, 15 seconds left.

Bug #9 (polish-prior-draft 300 s timeouts) is the canonical failure this
prevents: `self_refine` fires with its default 45 s critic + 60 s refiner
even when the wall clock has 10 s left, causing the outer timeout to fire
mid-refine and leave the user with an error banner instead of a partial
draft.

## How to use

1. **Seed at the request boundary** (once per request, in the gateway):

    from core.deadline import deadline_scope
    with deadline_scope(seconds=285):  # 15 s slack for finalisation
        result = await agent_graph.ainvoke(state)

2. **Query the remaining budget** inside any deeply nested code:

    from core.deadline import remaining
    local_timeout = min(45, remaining_or(45))
    await asyncio.wait_for(coro, timeout=local_timeout)

3. **Bounded wait_for helper** (convenience — clamps a local timeout to the
   deadline and raises `TimeoutError` immediately if the deadline is
   already past):

    from core.deadline import bounded_wait_for
    result = await bounded_wait_for(coro, local_timeout=45)

The module is contextvar-based. Every asyncio task spawned from a scope
inherits the deadline. When no deadline has been seeded, `remaining()`
returns `None` and `bounded_wait_for` uses the caller's `local_timeout`
unchanged — safe fallback for tests and non-request contexts.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Awaitable, Optional, TypeVar

_deadline: ContextVar[Optional[float]] = ContextVar("_deadline", default=None)

T = TypeVar("T")


def set_deadline_seconds(seconds: float) -> None:
    """Set the deadline as `now + seconds`.

    Prefer `deadline_scope()` unless you specifically need the deadline to
    outlive the current lexical scope (e.g., seeding from middleware).
    """
    _deadline.set(time.monotonic() + seconds)


def clear_deadline() -> None:
    """Remove any deadline from the current context."""
    _deadline.set(None)


def deadline_at() -> Optional[float]:
    """Return the monotonic-time at which the current request's deadline
    fires, or None if no deadline is set."""
    return _deadline.get()


def remaining() -> Optional[float]:
    """Return seconds remaining until the deadline (may be negative), or
    None if no deadline is set."""
    dl = _deadline.get()
    if dl is None:
        return None
    return dl - time.monotonic()


def remaining_or(default: float) -> float:
    """Return the remaining budget, clamped to [0, default]. When no
    deadline is set, returns `default` unchanged (safe for tests).

    Use this to derive a local timeout that respects the request's outer
    envelope:

        local = remaining_or(45)         # never exceed 45s AND never
                                          # exceed request budget
        await asyncio.wait_for(coro, timeout=local)
    """
    rem = remaining()
    if rem is None:
        return default
    if rem <= 0:
        return 0.0
    return min(rem, default)


def is_expired() -> bool:
    """True if a deadline is set AND has already passed."""
    rem = remaining()
    return rem is not None and rem <= 0


@contextmanager
def deadline_scope(seconds: float):
    """Context manager that seeds a deadline for its body and restores the
    previous deadline on exit.

    Idiomatic usage in the gateway:

        with deadline_scope(seconds=285):
            result = await run_chat_pipeline(...)
    """
    token = _deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _deadline.reset(token)


async def bounded_wait_for(
    coro: Awaitable[T],
    *,
    local_timeout: float,
) -> T:
    """Like `asyncio.wait_for` but clamps `local_timeout` to the remaining
    request deadline.

    - If no deadline is set, behaves exactly like `asyncio.wait_for(coro,
      timeout=local_timeout)`.
    - If the deadline is already past, raises `asyncio.TimeoutError`
      immediately without awaiting — cheaper than firing the LLM and
      failing after 45 s.
    - Otherwise uses `min(local_timeout, remaining())` as the effective
      timeout.
    """
    if is_expired():
        raise asyncio.TimeoutError(
            "Request deadline already exceeded; refusing to start work"
        )
    effective = remaining_or(local_timeout)
    # remaining_or clamps to 0 when the deadline has just passed; guard
    # against a 0s wait_for which would raise immediately anyway.
    if effective <= 0:
        raise asyncio.TimeoutError(
            "Request deadline exceeded before wait_for could start"
        )
    return await asyncio.wait_for(coro, timeout=effective)
