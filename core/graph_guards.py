"""LangGraph node observability guards (2026-09-09).

Purpose
-------
Two silent-failure modes in the LangGraph harness need to become loud:

1. **In-node state mutation.** LangGraph reducers merge whatever a node
   RETURNS. Any `state["x"] = y` executed inside a node bypasses the
   reducer and, under `MemorySaver`, may or may not survive checkpoint
   serialization. Combined with a shared-state Send fan-out, mutations
   race across parallel siblings.

2. **Undeclared state keys.** A node that returns a key which is not
   declared in `LegalAgentState.__annotations__` has no reducer registered.
   LangGraph silently drops the update.

This module wraps every registered node with `guarded_node`. On each call
it:

- Records the input dict's `id()` and key-set.
- Runs the wrapped node.
- Warns if the input dict's identity changed (`in_node_state_mutation`).
- Warns if the return dict contains any key not in
  `LegalAgentState.__annotations__` (`undeclared_state_key`).

For local development, setting `LAWTECH_STATE_MUTATION_TRAP=1` wraps the
state in a `types.MappingProxyType` before it reaches the node, turning
any bare-assignment `state["x"] = y` into an immediate `TypeError`.
The trap MUST NOT be enabled in production — under a legitimate node
that returns a dict, LangGraph never mutates the input, so the WARN
wrapper alone is enough.

See docs/CLAUDE.md § "LangGraph state discipline (do not regress)".
"""

from __future__ import annotations

import functools
import inspect
import os
import types
from typing import Any, Callable

from .logger import get_logger
from .state import LegalAgentState

log = get_logger("Graph")


def _trap_enabled() -> bool:
    """Env-gated: only trip in local dev. Never in prod."""
    return os.environ.get("LAWTECH_STATE_MUTATION_TRAP", "0") == "1"


def _declared_keys() -> set[str]:
    """Every key declared on LegalAgentState (including inherited from
    MessagesState). Cached implicitly through TypedDict's own resolution."""
    return set(getattr(LegalAgentState, "__annotations__", {}).keys())


def _diff_state(before_keys: set[str], before_id: int,
                after_state: Any) -> set[str]:
    """Detect in-place mutation by comparing identity + key set.

    Returns the set of keys that appeared / disappeared / were assigned
    on the same dict instance. Empty means no mutation.
    """
    if not isinstance(after_state, dict):
        return set()
    if id(after_state) != before_id:
        return set()  # different object — not a mutation, likely a copy
    after_keys = set(after_state.keys())
    return (after_keys - before_keys) | (before_keys - after_keys)


def _warn_undeclared_keys(
    node_name: str,
    request_id: str | None,
    ret: Any,
) -> None:
    """Emit `undeclared_state_key` WARN for each returned key that
    LangGraph won't know how to merge."""
    if not isinstance(ret, dict):
        return
    declared = _declared_keys()
    for k in ret.keys():
        if k not in declared:
            log.warning(
                "undeclared_state_key",
                node=node_name,
                key=k,
                request_id=request_id,
            )


def _warn_in_place_mutation(
    node_name: str,
    request_id: str | None,
    mutated_keys: set[str],
) -> None:
    if not mutated_keys:
        return
    log.warning(
        "in_node_state_mutation",
        node=node_name,
        mutated_keys=sorted(mutated_keys),
        request_id=request_id,
    )


def guarded_node(fn: Callable) -> Callable:
    """Wrap a LangGraph node with observability + optional strict trap.

    The wrapper preserves `fn.__name__` so downstream wrappers
    (e.g. `_timed_node`) and log lines see the raw node name.

    Behaviour:
    - WARN on any returned key not in LegalAgentState.__annotations__.
    - WARN on any in-place mutation of the input state dict (identity +
      key-set diff before/after).
    - When LAWTECH_STATE_MUTATION_TRAP=1, wrap the input state in
      `types.MappingProxyType` — any `state["x"] = y` raises TypeError
      immediately at the point of mutation, making the offender
      trivially locatable in a stack trace.
    """
    node_name = fn.__name__

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def _aguard(state):
            request_id = state.get("request_id") if isinstance(state, dict) else None
            if _trap_enabled() and isinstance(state, dict):
                # MappingProxyType is read-only; mutations raise TypeError.
                # Node MUST return a dict — reducers merge that. We can't
                # audit in-place mutations in this branch because the
                # proxy short-circuits them into exceptions, which is
                # exactly what we want in dev.
                trapped = types.MappingProxyType(state)
                ret = await fn(trapped)
                _warn_undeclared_keys(node_name, request_id, ret)
                return ret

            before_keys = set(state.keys()) if isinstance(state, dict) else set()
            before_id = id(state) if isinstance(state, dict) else 0
            ret = await fn(state)
            mutated = _diff_state(before_keys, before_id, state)
            _warn_in_place_mutation(node_name, request_id, mutated)
            _warn_undeclared_keys(node_name, request_id, ret)
            return ret

        _aguard.__wrapped__ = fn  # type: ignore[attr-defined]
        return _aguard

    @functools.wraps(fn)
    def _sguard(state):
        request_id = state.get("request_id") if isinstance(state, dict) else None
        if _trap_enabled() and isinstance(state, dict):
            trapped = types.MappingProxyType(state)
            ret = fn(trapped)
            _warn_undeclared_keys(node_name, request_id, ret)
            return ret

        before_keys = set(state.keys()) if isinstance(state, dict) else set()
        before_id = id(state) if isinstance(state, dict) else 0
        ret = fn(state)
        mutated = _diff_state(before_keys, before_id, state)
        _warn_in_place_mutation(node_name, request_id, mutated)
        _warn_undeclared_keys(node_name, request_id, ret)
        return ret

    _sguard.__wrapped__ = fn  # type: ignore[attr-defined]
    return _sguard


def log_graph_state_schema() -> None:
    """Log the LegalAgentState schema at graph build time — one line per
    process, greppable for accidental removals or accidental additions.
    """
    keys = sorted(_declared_keys())
    log.info(
        "graph_state_schema",
        keys=keys,
        key_count=len(keys),
        trap_enabled=_trap_enabled(),
    )
