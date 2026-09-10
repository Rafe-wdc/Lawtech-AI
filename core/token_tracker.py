"""Per-request token-usage accumulator for the multi-agent pipeline.

Every LLM call in the pipeline (orchestrator classification, query rewriting,
domain-agent generation, drafting sub-steps, synthesis, output guardrail, …)
calls `record(...)` to log its `usage_metadata`. The accumulator is scoped
to a single request via a `ContextVar`, so concurrent requests are isolated
and parallel agents within one request all write to the same totals.

Public API
----------
* `start_request()`   — call once per request before the pipeline runs.
                        Returns the `TokenUsage` instance for that request.
* `record(agent, step, response, model="")`
                      — record one LLM call. Returns `total_tokens` so the
                        existing call sites can keep their `tokens = ...`
                        idiom unchanged.
* `get_tracker()`     — get the current request's `TokenUsage` (or `None`).
* `TokenUsage.to_dict()` — serializable summary for the API response.

Why a ContextVar (not a state field)?
The orchestrator makes several LLM calls OUTSIDE of `agent_results`
(classification, planning, normalization, synthesis, citation injection, …).
Capturing those via a state-field reducer would require plumbing returns
through every node. The ContextVar captures every call on the current
asyncio task — including spawned coroutines, since `asyncio.create_task` /
`asyncio.gather` propagate context automatically.
"""

from __future__ import annotations

import contextvars
import threading
from dataclasses import dataclass, field
from typing import Any


# ── Per-model price table ─────────────────────────────────────────────────
# USD per 1,000,000 tokens, current as of 2026-04. Update as Anthropic /
# OpenAI / Google revise rates. Override via env if needed.
_MODEL_PRICES_USD_PER_M: dict[str, tuple[float, float]] = {
    # (input, output) per 1M tokens
    "gpt-4o":                       (2.50,  10.00),
    "gpt-4o-mini":                  (0.15,   0.60),
    "openai:gpt-4o":                (2.50,  10.00),
    "openai:gpt-4o-mini":           (0.15,   0.60),
    "gemini-2.5-flash":             (0.30,   2.50),
    "gemini-2.5-flash-lite":        (0.10,   0.40),
    "gemini-2.5-pro":               (1.25,  10.00),
    "gemini-3.8-flash":             (0.75,   3.75),
    "google_genai:gemini-2.5-flash":      (0.30,  2.50),
    "google_genai:gemini-2.5-flash-lite": (0.10,  0.40),
    "google_genai:gemini-2.5-pro":        (1.25, 10.00),
    "google_genai:gemini-3.8-flash":      (0.75,  3.75),
}


def _estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Best-effort cost in USD. Returns 0.0 if model is unknown or empty."""
    if not model:
        return 0.0
    rates = _MODEL_PRICES_USD_PER_M.get(model)
    if not rates:
        # Try a fuzzy match on substring (e.g. "gemini-2.5-flash-lite-001"
        # should match "gemini-2.5-flash-lite"). Both sides must be non-empty
        # — `"" in "anything"` is always True and would match arbitrarily.
        for k, v in _MODEL_PRICES_USD_PER_M.items():
            if k and (k in model or model in k):
                rates = v
                break
    if not rates:
        return 0.0
    in_rate, out_rate = rates
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


# ── Per-call record ───────────────────────────────────────────────────────

@dataclass
class TokenCall:
    """One LLM call's token usage.

    `model` is kept INTERNALLY for cost calculation + per-model rollup,
    but is NEVER serialized into the public payload (see `to_dict`).
    Exposing the underlying model identifier would leak provider/family
    information to end users; admin and log paths are sanitized
    elsewhere too.
    """
    agent: str                    # "Drafting", "Document", "Orchestrator", …
    step: str                     # "classification", "section_3", "outline", …
    model: str = ""               # internal-only — see class docstring
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0    # context-cache hit (cheaper input tokens)
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0     # thinking-model tokens (priced as output)
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        # Deliberately omit `model` from the public per-call payload.
        return {
            "agent": self.agent,
            "step": self.step,
            "input": self.input_tokens,
            "output": self.output_tokens,
            "total": self.total_tokens,
            "cache_read": self.cache_read_tokens,
            "cache_creation": self.cache_creation_tokens,
            "reasoning": self.reasoning_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


# ── Per-request accumulator ───────────────────────────────────────────────

@dataclass
class TokenUsage:
    """All token usage for a single request, aggregated and per-call."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    by_agent: dict[str, dict[str, int]] = field(default_factory=dict)
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    calls: list[TokenCall] = field(default_factory=list)

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(
        self,
        agent: str,
        step: str,
        response: Any,
        model: str = "",
    ) -> int:
        """Extract usage from a langchain AIMessage and add to totals.

        Returns the call's `total_tokens` so legacy `tokens = ...` lines
        keep working unchanged.

        `response` is whatever `chain.invoke()` returned — typically an
        `AIMessage` with a `.usage_metadata` dict, but may be `None`,
        a string (rare), or any object without that attr; this is silently
        tolerated.
        """
        um = getattr(response, "usage_metadata", None)
        if not um:
            return 0

        ip = um.get("input_tokens") or 0
        op = um.get("output_tokens") or 0
        tt = um.get("total_tokens") or 0
        details_in = um.get("input_token_details") or {}
        details_out = um.get("output_token_details") or {}
        cache_read = details_in.get("cache_read") or 0
        cache_create = details_in.get("cache_creation") or 0
        reasoning = details_out.get("reasoning") or 0

        # Resolve model name: prefer caller-provided, fall back to response metadata
        if not model:
            model = (
                getattr(response, "response_metadata", {}).get("model_name")
                or getattr(response, "response_metadata", {}).get("model")
                or ""
            )

        cost = _estimate_cost_usd(model, ip, op + reasoning)

        call = TokenCall(
            agent=agent, step=step, model=model,
            input_tokens=ip, output_tokens=op, total_tokens=tt,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_create,
            reasoning_tokens=reasoning,
            cost_usd=cost,
        )

        with self._lock:
            self.input_tokens += ip
            self.output_tokens += op
            self.total_tokens += tt
            self.cache_read_tokens += cache_read
            self.cache_creation_tokens += cache_create
            self.reasoning_tokens += reasoning
            self.cost_usd += cost

            # Per-agent breakdown
            ag = self.by_agent.setdefault(agent, {
                "input": 0, "output": 0, "total": 0,
                "cache_read": 0, "calls": 0, "cost_usd": 0.0,
            })
            ag["input"] += ip
            ag["output"] += op
            ag["total"] += tt
            ag["cache_read"] += cache_read
            ag["calls"] += 1
            ag["cost_usd"] = round(ag["cost_usd"] + cost, 6)

            # Per-model breakdown (useful for cost attribution)
            if model:
                md = self.by_model.setdefault(model, {
                    "input": 0, "output": 0, "total": 0,
                    "calls": 0, "cost_usd": 0.0,
                })
                md["input"] += ip
                md["output"] += op
                md["total"] += tt
                md["calls"] += 1
                md["cost_usd"] = round(md["cost_usd"] + cost, 6)

            self.calls.append(call)

        return tt

    def to_dict(self, include_calls: bool = True) -> dict[str, Any]:
        """Serializable summary for the API response.

        Set `include_calls=False` for a lighter payload that omits the
        per-LLM-call list (still keeps the per-agent rollup).

        `by_model` and the per-call `model` field are NEVER serialized
        — they would leak the underlying LLM provider / family to end
        users. Internal cost tracking still works because the model
        identifier is kept in memory (TokenCall.model, self.by_model)
        and used by `_estimate_cost_usd`; only the public payload is
        stripped.
        """
        with self._lock:
            d = {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens,
                "cache_read_tokens": self.cache_read_tokens,
                "cache_creation_tokens": self.cache_creation_tokens,
                "reasoning_tokens": self.reasoning_tokens,
                "cost_usd": round(self.cost_usd, 6),
                "by_agent": {k: dict(v) for k, v in self.by_agent.items()},
            }
            if include_calls:
                d["calls"] = [c.to_dict() for c in self.calls]
        return d


# ── ContextVar plumbing ───────────────────────────────────────────────────

_tracker_var: contextvars.ContextVar[TokenUsage | None] = contextvars.ContextVar(
    "lawtech_token_tracker", default=None,
)


def start_request() -> TokenUsage:
    """Initialize a new TokenUsage for the current request scope.

    Call this exactly once at the top of a request handler, before invoking
    the agent graph. Subsequent `record(...)` calls on this asyncio task
    (and any spawned children) will accumulate into this instance.
    """
    tracker = TokenUsage()
    _tracker_var.set(tracker)
    return tracker


def get_tracker() -> TokenUsage | None:
    """Get the current request's tracker, or None if outside a tracked scope."""
    return _tracker_var.get()


def record(
    agent: str,
    step: str,
    response: Any,
    model: str = "",
) -> int:
    """Record one LLM call's usage and return its total_tokens.

    Drop-in replacement for the old idiom:
        tokens = response.usage_metadata.get("total_tokens", 0)

    becomes:
        tokens = record("Document", "qa", response)

    If no tracker is active (e.g. a unit test), falls back to the legacy
    "extract total only" behavior so existing call sites keep working.
    """
    tracker = get_tracker()
    if tracker is None:
        um = getattr(response, "usage_metadata", None)
        return (um.get("total_tokens") or 0) if um else 0
    return tracker.record(agent, step, response, model)
