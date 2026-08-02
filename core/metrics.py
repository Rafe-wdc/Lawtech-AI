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
        "Agent-level errors (empty result or exception). `error_class` "
        "is the exception type name (e.g. TimeoutError, RuntimeError) "
        "or a synthetic label like `empty_result`.",
        ["agent", "error_class"],
    ),

    "node_duration_seconds": Histogram(
        "lawtech_node_duration_seconds",
        "Per-graph-node execution latency in seconds. `node` is the "
        "LangGraph node name (e.g. `orchestrator_plan`, `drafting`, "
        "`guardrail_input`). Separate from `agent_latency_seconds` "
        "because it also covers infrastructure nodes (guardrail, "
        "memory, orchestrator_plan/synthesize).",
        ["node"],
        buckets=_LATENCY_BUCKETS,
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
    # NOTE on Gauge multiprocess_mode: when PROMETHEUS_MULTIPROC_DIR is set
    # (gunicorn multi-worker, see Phase 6 in scale plan), each worker writes
    # its own value file. `livesum` aggregates by summing across live workers
    # so the scraped value reflects system-wide truth (e.g. total in-flight
    # across all workers, not per-pid). `livemostrecent` picks the freshest
    # value when only one worker tends to update it (rolling-window stats).
    # When the env var is unset (dev / single-worker), multiprocess_mode is
    # ignored by prometheus_client and Gauges behave normally.

    "active_requests": Gauge(
        "lawtech_active_requests",
        "Number of requests currently being processed",
        multiprocess_mode="livesum",
    ),

    # --- Per-worker in-flight gate (Phase 3 backpressure) ---

    "inflight_gate_rejections_total": Counter(
        "lawtech_inflight_gate_rejections_total",
        "Requests rejected because the per-worker in-flight cap was hit",
        ["endpoint"],
    ),

    "inflight_gate_capacity": Gauge(
        "lawtech_inflight_gate_capacity",
        "Total in-flight capacity across all workers (sum of per-worker caps)",
        multiprocess_mode="livesum",
    ),

    "inflight_gate_available": Gauge(
        "lawtech_inflight_gate_available",
        "Remaining in-flight slots across all workers",
        multiprocess_mode="livesum",
    ),

    # --- L4: Quality Scores (updated after each scored response) ---

    "quality_avg_score": Gauge(
        "lawtech_quality_avg_score",
        "Rolling average quality score (last 100 scored responses)",
        multiprocess_mode="livemostrecent",
    ),

    "quality_faithfulness": Gauge(
        "lawtech_quality_faithfulness",
        "Rolling average faithfulness score",
        multiprocess_mode="livemostrecent",
    ),

    "quality_relevance": Gauge(
        "lawtech_quality_relevance",
        "Rolling average relevance score",
        multiprocess_mode="livemostrecent",
    ),

    "quality_low_count": Counter(
        "lawtech_quality_low_count",
        "Total responses with quality score below 0.6",
    ),

    "quality_scored_total": Counter(
        "lawtech_quality_scored_total",
        "Total responses that have been quality scored",
    ),
}


# ---------------------------------------------------------------------------
# Convenience helpers (thin wrappers so callers don't type
# METRICS["agent_errors_total"].labels(...).inc() everywhere).
# ---------------------------------------------------------------------------

def record_agent_error(agent: str, exc: BaseException | None = None,
                       error_class: str | None = None) -> None:
    """Increment `agent_errors_total{agent, error_class}`.

    Pass the exception object OR an explicit `error_class` label. When
    both are None, defaults to `"unknown"` so the metric is never
    silently under-counted.
    """
    if error_class is None:
        error_class = type(exc).__name__ if exc is not None else "unknown"
    try:
        METRICS["agent_errors_total"].labels(
            agent=agent, error_class=error_class,
        ).inc()
    except Exception:  # pragma: no cover — metric failure must never
        # bring down the caller. The whole point of the wrapper is that
        # a broken metrics backend can't cascade into agent failure.
        pass


def observe_node_duration(node: str, seconds: float) -> None:
    """Observe a single per-node execution latency."""
    try:
        METRICS["node_duration_seconds"].labels(node=node).observe(seconds)
    except Exception:  # pragma: no cover — same rationale as above
        pass


from contextlib import contextmanager as _contextmanager
import time as _time


@_contextmanager
def time_node(node: str):
    """Context manager that observes `node_duration_seconds{node}`.

    Usage:
        with time_node("drafting"):
            result = await run_drafting(state)
    """
    start = _time.perf_counter()
    try:
        yield
    finally:
        observe_node_duration(node, _time.perf_counter() - start)
