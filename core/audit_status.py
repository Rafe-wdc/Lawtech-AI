"""Request-scoped audit-status flag.

Used by verifier layers (currently only ``core.self_refine``) to signal that
they were unable to run — transport error, parse failure, timeout, rate
limit — so the response ships behind an ``x_audit_status="unverified"``
envelope field instead of a fail-open silent pass.

The pipeline's default is ``""`` (empty — critic ran normally). When a
verifier sets ``"unverified"`` on a request, ``core.chat_runner`` picks
it up while building the final ``done`` SSE event.

The ContextVar holds a mutable holder, not the status string itself.
Verifiers run inside LangGraph nodes, and LangGraph runs each node in its
own asyncio task or worker thread with a COPY of the request's context.
A ``ContextVar.set`` inside a node changes only that copy, so with a plain
string the flag never reached ``chat_runner`` (fixed 2026-11-12; until
then ``x_audit_status`` was never emitted). Every copy of the context
points at the same holder, so a write in any nested task is seen by the
request that called ``reset_audit_status``. Each request gets a fresh
holder, so concurrent requests stay separate.
"""
from __future__ import annotations

from contextvars import ContextVar

# The default must stay None: a shared mutable default would be one holder
# for every request in the process.
_audit_status_var: ContextVar[dict | None] = ContextVar("lawtech_audit_status", default=None)


def mark_unverified() -> None:
    """Flag the current request as shipping without a completed audit."""
    holder = _audit_status_var.get()
    if holder is None:
        # No request pipeline started a holder (a script or test calling a
        # verifier directly). The flag then lives in this context only.
        _audit_status_var.set({"status": "unverified"})
    else:
        holder["status"] = "unverified"


def get_audit_status() -> str:
    """Return ``"unverified"`` when a verifier signalled unavailability, else ``""``."""
    holder = _audit_status_var.get()
    return holder["status"] if holder else ""


def reset_audit_status() -> None:
    """Start a fresh flag — called at the start of each request pipeline."""
    _audit_status_var.set({"status": ""})
