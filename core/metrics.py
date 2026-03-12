"""Prometheus metrics definitions — shared across all modules.

Import these counters/histograms in gateway.py and agents to record events.
All metrics are lazily registered on first import.

Usage:
    from core.metrics import METRICS
    METRICS["requests_total"].labels(endpoint="/pyapi/search", method="POST").inc()
    METRICS["agent_invocations"].labels(agent="judgment").inc()
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram, Gauge, REGISTRY

# ── Buckets ──────────────────────────────────────────────────────────────────
# Latency buckets in seconds: 0.1s → 120s (covers fast ES hits to slow Gemini Pro)
_LATENCY_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120)

# ── Metrics registry ─────────────────────────────────────────────────────────

METRICS: dict = {

    # --- HTTP Layer ---

    "requests_total": Counter(
        "lawtech_requests_total",
        "Total HTTP requests received",
        ["endpoint", "method", "status_code"],
    ),

    "request_latency_seconds": Histogram(
        "lawtech_request_latency_seconds",
        "HTTP request latency in seconds",
        ["endpoint"],
        buckets=_LATENCY_BUCKETS,
    ),

    # --- Agent Layer ---

    "agent_invocations_total": Counter(
        "lawtech_agent_invocations_total",
        "Number of times each agent was invoked",
        ["agent"],
    ),

    "agent_latency_seconds": Histogram(
        "lawtech_agent_latency_seconds",
        "Per-agent execution latency in seconds",
        ["agent"],
        buckets=_LATENCY_BUCKETS,
    ),

    "agent_errors_total": Counter(
        "lawtech_agent_errors_total",
        "Agent-level errors (empty result or exception)",
        ["agent"],
    ),

    # --- Fallback Layer ---

    "fallback_total": Counter(
        "lawtech_fallback_total",
        "Fallback events by agent and tier",
        ["agent", "tier"],   # tier: query_rewrite | web_search
    ),

    # --- Guardrail Layer ---

    "guardrail_blocks_total": Counter(
        "lawtech_guardrail_blocks_total",
        "Queries blocked by guardrail (input + output)",
        ["stage"],   # stage: input | output
    ),

    # --- LLM Layer ---

    "llm_tokens_total": Counter(
        "lawtech_llm_tokens_total",
        "Total LLM tokens consumed",
        ["model", "token_type"],   # token_type: input | output
    ),

    # --- Task Classification ---

    "tasks_planned_total": Counter(
        "lawtech_tasks_planned_total",
        "Task types planned by the orchestrator",
        ["task_type"],
    ),

    # --- System ---

    "active_requests": Gauge(
        "lawtech_active_requests",
        "Number of requests currently being processed",
    ),
}
