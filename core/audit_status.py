"""Request-scoped audit-status flag.

Used by verifier layers (currently only ``core.self_refine``) to signal that
they were unable to run — transport error, parse failure, timeout, rate
limit — so the response ships behind an ``x_audit_status="unverified"``
envelope field instead of a fail-open silent pass.

The pipeline's default is ``""`` (empty — critic ran normally). When a
verifier sets ``"unverified"`` on a request, ``core.chat_runner`` picks
it up while building the final ``done`` SSE event.

ContextVar propagates through ``asyncio.create_task`` and
``asyncio.to_thread``, so every nested critic/refiner call in the same
graph run shares the flag.
"""
from __future__ import annotations

from contextvars import ContextVar

_audit_status_var: ContextVar[str] = ContextVar("lawtech_audit_status", default="")


def mark_unverified() -> None:
    """Flag the current request as shipping without a completed audit."""
    _audit_status_var.set("unverified")


def get_audit_status() -> str:
    """Return ``"unverified"`` when a verifier signalled unavailability, else ``""``."""
    return _audit_status_var.get()


def reset_audit_status() -> None:
    """Clear the flag — called at the start of each request pipeline."""
    _audit_status_var.set("")