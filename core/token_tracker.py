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
    # Gemini 2.5 (legacy — retained so a GEMINI_*_MODEL rollback still costs)
    "gemini-2.5-flash":             (0.30,   2.50),
    "gemini-2.5-flash-lite":        (0.10,   0.40),
    "gemini-2.5-pro":               (1.25,  10.00),
    "google_genai:gemini-2.5-flash":      (0.30,  2.50),
    "google_genai:gemini-2.5-flash-lite": (0.10,  0.40),
    "google_genai:gemini-2.5-pro":        (1.25, 10.00),
    # Gemini 3.x — official list prices read from
    # https://ai.google.dev/gemini-api/docs/pricing on 2026-09-04.
    # Note gemini-3.8-flash is both NEWER and CHEAPER than 3.5-flash.
    # Pro tiers are tiered by context length; the >200k rate is used here so
    # long-document work is never under-reported.
    "gemini-3.8-flash":             (0.75,   3.75),
    "gemini-3.7-flash":             (0.75,   3.75),
    "gemini-3.6-flash":             (0.75,   3.75),
    "gemini-3.5-flash":             (1.50,   9.00),
    "gemini-3.5-flash-lite":        (0.30,   2.50),
    "gemini-3.1-flash-lite":        (0.25,   1.50),
    "gemini-3.1-pro-preview":       (4.00,  18.00),
    "gemini-pro-latest":            (4.00,  18.00),
    "google_genai:gemini-3.8-flash":       (0.75,  3.75),
    "google_genai:gemini-3.7-flash":       (0.75,  3.75),
    "google_genai:gemini-3.6-flash":       (0.75,  3.75),
    "google_genai:gemini-3.5-flash":       (1.50,  9.00),
    "google_genai:gemini-3.5-flash-lite":  (0.30,  2.50),
    "google_genai:gemini-3.1-flash-lite":  (0.25,  1.50),
    "google_genai:gemini-3.1-pro-preview": (4.00, 18.00),
    "google_genai:gemini-pro-latest":      (4.00, 18.00),
    # Claude (Anthropic) — official pricing from platform.claude.com read
    # 2026-09-07. The $2/$10 Sonnet 5 rate was introductory through 2026-08-31
    # and is now the permanent standard rate.
    "claude-sonnet-5":               (2.00,  10.00),
    "claude-opus-5":                 (5.00,  25.00),
    "claude-haiku-4-5":              (1.00,   5.00),
    "anthropic:claude-sonnet-5":     (2.00,  10.00),
    "anthropic:claude-opus-5":       (5.00,  25.00),
    "anthropic:claude-haiku-4-5":    (1.00,   5.00),
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
        #
        # Candidates are tried LONGEST-KEY-FIRST so the most specific price
        # wins. Without this, dict order decides: an unlisted variant such as
        # "gemini-3.5-flash-lite-preview" would match the shorter
        # "gemini-3.5-flash" ($1.50/$9.00) instead of "gemini-3.5-flash-lite"
        # ($0.30/$2.50) — a 5x cost over-report on the highest-volume tier.
        for k in sorted(_MODEL_PRICES_USD_PER_M, key=len, reverse=True):
            if k and (k in model or model in k):
                rates = _MODEL_PRICES_USD_PER_M[k]
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

        # Resolve model name: prefer what ACTUALLY served the response, and
        # only fall back to the caller-supplied label.
        #
        # The order used to be the reverse (caller first). That silently
        # mispriced every call served by a LangChain fallback: callers pass the
        # CONFIGURED model (e.g. MODELS["drafting"] = "anthropic:claude-sonnet-5"),
        # but when the Anthropic account has no credit the 400 is swallowed by
        # `.with_fallbacks()` in get_drafting_llm and Gemini answers. Measured
        # 2026-09-07: an 8-token call served by gemini-3.8-flash was billed at
        # Claude rates — $0.000032 instead of $0.000012, a 2.7x over-report on
        # every drafting call, invisible in the usage payload.
        #
        # `response_metadata["model_name"]` is written by the provider that
        # really answered, so it is authoritative. The caller's label is only a
        # hint about intent.
        served = (
            (getattr(response, "response_metadata", None) or {}).get("model_name")
            or (getattr(response, "response_metadata", None) or {}).get("model")
            or ""
        )
        model = served or model

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


def record_genai(agent: str, step: str, response: Any, model: str = "") -> int:
    """Feed a raw google-genai response's usage into the tracker.

    LangChain wraps usage under `.usage_metadata` as a dict — `record()`
    handles that shape natively. The native `genai.Client` exposes
    `.usage_metadata` as a Pydantic object with `prompt_token_count` /
    `candidates_token_count` / `total_token_count` attrs, which `record()`
    doesn't understand. This helper normalises the shape via a shim so
    per-request cost attribution captures Google-Search-grounded calls
    (Scenario, web_search_fallback, get_web_context) that would otherwise
    be invisible to `by_agent` / `by_model` / `cost_usd` rollups.
    """
    from types import SimpleNamespace
    um = getattr(response, "usage_metadata", None)
    if um is None:
        return 0
    shim = SimpleNamespace(
        usage_metadata={
            "input_tokens":  getattr(um, "prompt_token_count", 0) or 0,
            "output_tokens": getattr(um, "candidates_token_count", 0) or 0,
            "total_tokens":  getattr(um, "total_token_count", 0) or 0,
        },
    )
    return record(agent, step, shim, model=model)


def repair_zero_total(usage: dict | None, fallback_total: int) -> dict:
    """Guarantee `total_tokens > 0` on the emitted payload.

    Used at every emission site (live + cache-hit) so a lost-ContextVar
    tracker or a stored zero-tracker dict cannot propagate to clients when
    a non-zero per-agent AgentResult sum is available. Preserves any
    existing per-agent / per-call breakdown; only patches the aggregate
    (and mirrors it into `output_tokens` when both input and output are
    empty, to keep downstream sums balanced).
    """
    if not isinstance(usage, dict):
        usage = {
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "reasoning_tokens": 0, "cost_usd": 0.0,
            "by_agent": {}, "calls": [],
        }
    total = usage.get("total_tokens") or 0
    if total > 0 or fallback_total <= 0:
        return usage
    usage["total_tokens"] = fallback_total
    if (usage.get("input_tokens") or 0) == 0 and (usage.get("output_tokens") or 0) == 0:
        usage["output_tokens"] = fallback_total
    return usage
