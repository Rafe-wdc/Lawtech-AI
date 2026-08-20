"""Langfuse tracing integration (optional, fail-safe).

Wires a Langfuse `CallbackHandler` into the LangGraph run so every request
produces a full trace in the Langfuse UI — the whole agent fan-out plus each
underlying LangChain LLM call, with prompts, responses, latency, and tokens.

Design rules:
- **Never break the app.** If the `langfuse` package is missing, the keys are
  unset, or the Langfuse server is unreachable, every function here degrades to
  a no-op (returns None). The SDK itself queues events in a background thread
  with backoff, so a down server does not block or error the request path.
- **Self-hosted friendly.** This repo configures the host as `LANGFUSE_BASE_URL`
  (e.g. the Docker default `http://localhost:3000`); the SDK's own env var is
  `LANGFUSE_HOST`. We read `LANGFUSE_BASE_URL` first and fall back to
  `LANGFUSE_HOST`.

Usage (see core/chat_runner.py and core/gateway.py):

    from core.tracing import get_langfuse_callback, trace_metadata
    handler = get_langfuse_callback()
    config = {"configurable": {"thread_id": tid}}
    if handler:
        config["callbacks"] = [handler]
        config["metadata"] = trace_metadata(thread_id=tid, endpoint="chat")
"""
from __future__ import annotations

import os

from core.logger import get_logger

log = get_logger("Tracing")

# Module-level init guard so we only build the global client once.
_initialized = False
_enabled = False


def _init() -> bool:
    """Initialize the global Langfuse client once. Returns True if tracing is on."""
    global _initialized, _enabled
    if _initialized:
        return _enabled
    _initialized = True

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    # This repo's convention is LANGFUSE_BASE_URL; the SDK default is LANGFUSE_HOST.
    host = (
        os.getenv("LANGFUSE_BASE_URL")
        or os.getenv("LANGFUSE_HOST")
        or "http://localhost:3000"
    )

    if not (public_key and secret_key):
        log.info(
            "Langfuse tracing disabled — LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set"
        )
        return False

    try:
        # Langfuse 3.x/4.x: initialize the global client. This is lazy and
        # non-blocking — it does NOT fail if the server at `host` is down;
        # events queue and flush in a background thread with backoff.
        from langfuse import Langfuse

        Langfuse(public_key=public_key, secret_key=secret_key, host=host)
        _enabled = True
        log.info("Langfuse tracing enabled", host=host)
    except Exception as e:  # ImportError, bad config, etc.
        log.warning(
            "Langfuse init failed — tracing disabled",
            error=str(e).splitlines()[0][:200],
        )
        _enabled = False

    return _enabled


def get_langfuse_callback():
    """Return a Langfuse LangChain `CallbackHandler`, or None if unavailable.

    Attach the returned handler to a LangGraph/LangChain `config["callbacks"]`
    list. Returns None (no-op) whenever tracing is disabled or the SDK is
    missing, so callers can `if handler:` without any other guard.
    """
    if not _init():
        return None
    try:
        from langfuse.langchain import CallbackHandler

        # v3/v4: no constructor args — reads the global client configured above.
        return CallbackHandler()
    except Exception as e:
        log.warning(
            "Langfuse CallbackHandler unavailable",
            error=str(e).splitlines()[0][:200],
        )
        return None


def trace_metadata(*, thread_id: str | None = None,
                   endpoint: str | None = None,
                   user_id: str | None = None,
                   tags: list[str] | None = None) -> dict:
    """Build the LangChain-run metadata that Langfuse reads for a trace.

    Langfuse's callback picks up `langfuse_session_id`, `langfuse_user_id`, and
    `langfuse_tags` from the run metadata, letting you group a conversation's
    turns by thread in the Langfuse UI.
    """
    meta: dict = {}
    if thread_id:
        meta["langfuse_session_id"] = thread_id
    if user_id:
        meta["langfuse_user_id"] = user_id
    _tags = list(tags or [])
    if endpoint:
        _tags.append(f"endpoint:{endpoint}")
    if _tags:
        meta["langfuse_tags"] = _tags
    return meta


def is_enabled() -> bool:
    """True when Langfuse tracing is active (for health/status reporting)."""
    return _init()
