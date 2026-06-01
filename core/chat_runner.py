"""Shared chat pipeline used by both `/pyapi/search/stream` and `/pyapi/chat`.

This module centralizes the SSE event generation, LangGraph streaming,
integration URL extraction, cache handling, and post-stream persistence —
so both endpoints share the same behavior and new features only need to be
added in one place.

The caller is responsible for parsing its own input format (JSON vs multipart)
and building a `ChatRunnerInputs` dataclass. The pipeline yields SSE-formatted
strings (`"data: {...}\n\n"`).
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass
from typing import AsyncIterator

from core.chat_store import chat_store
from core.logger import get_logger
from core.response_cache import response_cache, CacheEntry

log = get_logger("ChatRunner")


# ---------------------------------------------------------------------------
# Inputs & helpers
# ---------------------------------------------------------------------------

@dataclass
class ChatRunnerInputs:
    """All the per-request inputs the shared pipeline needs.

    Both `/pyapi/chat` and `/pyapi/search/stream` build one of these after
    parsing their own input format.
    """
    agent_graph: object                         # compiled LangGraph
    query: str                                  # user's message text
    thread_id: str                              # thread uuid
    is_first_turn: bool                         # for cache gating
    endpoint_name: str                          # for logging, e.g. "/pyapi/chat"
    preferred_language: str | None = None       # ISO 639-1 override
    file_context: dict | None = None            # pre-built by /chat caller
    integration_token: str | None = None        # FSD JWT for Google/Notion
    cite_appendix: bool | None = None           # Drafting: include REFERENCES & CITATIONS block
    enable_cache: bool = True                   # /chat disables for uploads
    enable_quality_scoring: bool = True         # 10% sampling
    skip_thread_id_event: bool = False          # caller already emitted it


def _fire_and_forget(coro) -> asyncio.Task:
    """Schedule a coroutine without awaiting it."""
    task = asyncio.create_task(coro)
    task.add_done_callback(lambda t: t.exception())
    return task


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


# ---------------------------------------------------------------------------
# Integration URL handling (extracted from gateway.py)
# ---------------------------------------------------------------------------

async def _handle_integrations(
    query: str, integration_token: str,
) -> AsyncIterator[tuple[str, dict | None]]:
    """Detect integration URLs, check status, extract content.

    Yields (sse_event_string, integration_context_dict_or_None).
    The final yielded item's dict is the integration_context to inject into state;
    intermediate items yield (event, None).
    """
    from core.integration_service import (
        detect_urls, IntegrationClient, process_integration_urls,
    )
    detected = detect_urls(query)
    if not detected:
        yield ("", None)
        return

    client = IntegrationClient()
    providers_needed = list({d.provider for d in detected})

    for provider in providers_needed:
        provider_urls = [d.url for d in detected if d.provider == provider]
        yield (_sse({
            "type": "integration_status",
            "provider": provider,
            "urls": provider_urls,
            "message": f"{provider.title()} link detected. Checking connection...",
        }), None)

    _, statuses, contents = await process_integration_urls(
        query, integration_token, client,
    )

    # For each disconnected provider, emit an auth_url event
    for provider in providers_needed:
        if not statuses.get(provider, False):
            auth_url = await client.get_auth_url(provider, integration_token)
            if auth_url:
                yield (_sse({
                    "type": "integration_auth",
                    "provider": provider,
                    "auth_url": auth_url,
                    "message": f"Please connect your {provider.title()} account to access this content.",
                }), None)
            else:
                yield (_sse({
                    "type": "integration_error",
                    "provider": provider,
                    "message": f"Failed to get {provider.title()} authorization URL.",
                }), None)

    # Build combined integration_context if we extracted anything
    if contents:
        combined_text = "\n\n".join(
            f"--- {c.title} ({c.provider}) ---\n{c.content}" for c in contents
        )
        title = contents[0].title if len(contents) == 1 else f"{len(contents)} documents"
        url = contents[0].url if len(contents) == 1 else ", ".join(c.url for c in contents)
        integration_context = {
            "provider": contents[0].provider,
            "title": title,
            "content": combined_text,
            "url": url,
            "metadata": contents[0].metadata if len(contents) == 1 else {},
            "documents": [
                {"provider": c.provider, "title": c.title, "url": c.url,
                 "word_count": len(c.content.split())}
                for c in contents
            ],
        }
        yield (_sse({
            "type": "integration_content",
            "provider": contents[0].provider,
            "title": title,
            "word_count": len(combined_text.split()),
            "message": f"Extracted content from {title}.",
        }), integration_context)
    else:
        yield ("", None)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def run_chat_pipeline(
    inputs: ChatRunnerInputs,
    build_initial_state,       # callable from gateway to build the state dict
    followup_suggestions_fn,   # async callable (query, response, agents) -> list[str]
    async_timeout_cm,          # context manager factory (async_timeout)
    node_status_map: dict[str, str],
) -> AsyncIterator[str]:
    """Run the full chat pipeline and yield SSE-formatted strings.

    Callbacks are passed in instead of imported to avoid circular imports
    with gateway.py.
    """
    i = inputs
    if not i.skip_thread_id_event:
        yield _sse({"type": "thread_id", "data": i.thread_id})
    start = time.perf_counter()

    # --- Cache check (first turn only) -------------------------------------
    if i.enable_cache and i.is_first_turn:
        cached = response_cache.get(i.query)
        if cached:
            yield _sse({"type": "status", "message": "Returning cached response..."})
            yield _sse({"type": "response", "content": cached.response})

            # Emit the same `sources` event the non-cached path emits so
            # frontend consumers see sources consistently.
            if cached.source_metadata:
                yield _sse({"type": "sources", "data": cached.source_metadata})

            # Generate follow-up suggestions on demand (cheap vs the full
            # agent pipeline we just avoided). Cached entries don't store
            # suggestions to keep CacheEntry simple.
            try:
                suggestions = await followup_suggestions_fn(
                    i.query, cached.response, cached.agents_used,
                )
                if suggestions:
                    yield _sse({"type": "followup_suggestions", "data": suggestions})
            except Exception as e:
                log.warning("Cached followup suggestions failed",
                            endpoint=i.endpoint_name, error=str(e))

            # On cache hit, replay the originally-captured per-LLM-call
            # token breakdown if it was stored. Older cache entries (pre
            # token_usage support) only stored the aggregate int — synthesise
            # a minimal token_usage from that for shape consistency.
            cached_token_usage = cached.token_usage or {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": cached.tokens_consumed or 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "reasoning_tokens": 0,
                "cost_usd": 0.0,
                "by_agent": {},
                "by_model": {},
                "calls": [],
            }
            yield _sse({
                "type": "done",
                "agents_used": cached.agents_used,
                "token_usage": cached_token_usage,
                "thread_id": i.thread_id,
                "source_metadata": cached.source_metadata,  # kept for backward compat
                "cached": True,
            })
            log.info("Cache hit", endpoint=i.endpoint_name,
                     agents=cached.agents_used)
            return

    # --- Integration URL handling ------------------------------------------
    integration_context_dict: dict | None = None
    if i.integration_token:
        async for sse_event, ctx in _handle_integrations(i.query, i.integration_token):
            if sse_event:
                yield sse_event
            if ctx is not None:
                integration_context_dict = ctx

    # --- Build state + run graph -------------------------------------------
    initial_state = build_initial_state(
        i.query, i.thread_id,
        file_context=i.file_context,
        preferred_language=i.preferred_language,
        cite_appendix=i.cite_appendix,
    )
    if integration_context_dict:
        initial_state["integration_context"] = integration_context_dict

    config = {"configurable": {"thread_id": i.thread_id}}

    # Initialise the per-request token tracker. Every LLM call made by
    # any agent / orchestrator step on this asyncio task (and its
    # spawned children, since asyncio propagates contextvars) will
    # accumulate into this instance. Read out at the end and surface
    # in the `done` SSE event.
    from core.token_tracker import start_request as _start_token_tracking
    token_tracker = _start_token_tracking()

    step_count = 0
    final_response = ""
    agents_used: list[str] = []
    tasks_planned_stream: list[str] = []
    user_language_stream = "en"
    total_tokens = 0
    all_source_metadata: list[dict] = []
    effective_query = i.query
    query_rewritten = False
    draft_continuation_data: dict | None = None

    try:
        async with async_timeout_cm(300):
            async for event in i.agent_graph.astream(
                initial_state,
                config=config,
                stream_mode=["updates", "custom"],
            ):
                mode, chunk = event

                # --- Custom events: token-by-token streaming ---
                if mode == "custom":
                    if isinstance(chunk, dict):
                        kind = chunk.get("type")
                        if kind == "token":
                            yield _sse({"type": "token", "content": chunk["content"]})
                        elif kind == "token_reset":
                            yield _sse({"type": "token_reset"})
                        elif kind == "drafting_progress":
                            # Required fields kept first so old consumers reading
                            # {section,total,title} stay happy. Optional fields
                            # added over time:
                            #   - status, char_count, error: Phase C
                            #   - index, start_order: BUG-13 (stable natural
                            #     section index for frontend slot rendering;
                            #     start_order is the parallel-execution order
                            #     for debugging only).
                            dp_event: dict = {
                                "type": "drafting_progress",
                                "section": chunk["section"],
                                "total": chunk["total"],
                                "title": chunk["title"],
                            }
                            for optional in ("status", "char_count", "error",
                                             "index", "start_order"):
                                if optional in chunk:
                                    dp_event[optional] = chunk[optional]
                            yield _sse(dp_event)
                        elif kind == "draft_incomplete":
                            yield _sse({
                                "type": "draft_incomplete",
                                "failed_sections": chunk["failed_sections"],
                                "total_sections": chunk["total_sections"],
                                "completed_sections": chunk["completed_sections"],
                            })
                        elif kind == "queue_status":
                            yield _sse({
                                "type": "status",
                                "agent": "queue",
                                "message": chunk.get("message", "Waiting for available slot..."),
                            })
                        elif kind == "progress":
                            yield _sse(chunk)
                    continue

                # --- Update events: node-level progress ---
                for node_name, update in chunk.items():
                    step_count += 1
                    status_msg = node_status_map.get(node_name, f"Processing {node_name}...")
                    yield _sse({
                        "type": "status",
                        "agent": node_name,
                        "message": status_msg,
                    })

                    if node_name == "memory" and "query" in update:
                        effective_query = update["query"]
                        query_rewritten = effective_query != i.query
                        history_msgs = update.get("chat_history", [])
                        yield _sse({
                            "type": "context",
                            "query_rewritten": query_rewritten,
                            "effective_query": effective_query if query_rewritten else None,
                            "history_turns": len(history_msgs) // 2,
                            "has_summary": bool(update.get("summary_text")),
                        })

                    if node_name == "memory" and "user_language" in update:
                        user_language_stream = update.get("user_language", "en") or "en"

                    if "final_response" in update and update["final_response"]:
                        final_response = update["final_response"]

                    if "tasks_planned" in update and update["tasks_planned"]:
                        tasks_planned_stream = update["tasks_planned"]
                        agents_used = update["tasks_planned"]
                        yield _sse({"type": "agents_planned", "agents": agents_used})

                    if "agent_results" in update:
                        for name, r in update["agent_results"].items():
                            if hasattr(r, "tokens_consumed"):
                                total_tokens += r.tokens_consumed or 0

                    if "source_metadata" in update and update["source_metadata"]:
                        all_source_metadata = update["source_metadata"]

                    if "draft_continuation" in update and update["draft_continuation"]:
                        draft_continuation_data = update["draft_continuation"]

    except TimeoutError:
        log.error("Stream timed out after 300s",
                  endpoint=i.endpoint_name, thread_id=i.thread_id[:12])
        yield _sse({"type": "error", "data": "Request timed out. Please try a simpler query."})
    except Exception as e:
        log.error("Stream error", endpoint=i.endpoint_name, error=str(e))
        yield _sse({"type": "error", "data": str(e)})

    # --- Final events ------------------------------------------------------
    if final_response:
        yield _sse({"type": "response", "content": final_response})

    if all_source_metadata:
        yield _sse({"type": "sources", "data": all_source_metadata})

    if final_response:
        try:
            suggestions = await followup_suggestions_fn(i.query, final_response, agents_used)
            if suggestions:
                yield _sse({"type": "followup_suggestions", "data": suggestions})
        except Exception as e:
            log.warning("Followup suggestions failed",
                        endpoint=i.endpoint_name, error=str(e))

    elapsed_ms = (time.perf_counter() - start) * 1000
    log.info("Stream completed",
             endpoint=i.endpoint_name, steps=step_count,
             duration_ms=f"{elapsed_ms:.0f}")

    # --- Post-stream persistence + logging ---------------------------------
    # Fire-and-forget request log (to SQLite for observability)
    _fire_and_forget(chat_store.log_request(
        thread_id=i.thread_id,
        endpoint=i.endpoint_name,
        query_preview=i.query[:300],
        user_language=user_language_stream,
        tasks_planned=tasks_planned_stream,
        agents_used=agents_used,
        total_latency_ms=int(elapsed_ms),
        total_tokens=total_tokens,
        fallback_used=False,
        is_blocked=False,
    ))

    # Quality scoring (10% sample, fire-and-forget)
    if i.enable_quality_scoring and final_response and random.random() < 0.10:
        from core.quality import score_response
        _fire_and_forget(score_response(
            query=i.query,
            response=final_response,
            agents_used=agents_used,
            thread_id=i.thread_id,
        ))

    # Save chat history
    conversation_turn = 0
    if final_response:
        try:
            conversation_turn = await chat_store.save_turn(
                i.thread_id, effective_query, final_response,
            )
        except Exception as e:
            log.error("Failed to save chat history",
                      endpoint=i.endpoint_name, error=str(e))

    # Persist draft continuation metadata for incomplete drafts
    if draft_continuation_data:
        try:
            await chat_store.save_draft_continuation(i.thread_id, draft_continuation_data)
        except Exception as e:
            log.error("Failed to save draft continuation", error=str(e))

    # Cache first-turn responses (skip if files/integration were used to keep
    # cache keys clean and avoid stale-content serving)
    cacheable = (
        i.enable_cache
        and i.is_first_turn
        and final_response
        and not draft_continuation_data
        and not i.file_context
        and not integration_context_dict
    )
    if cacheable:
        response_cache.set(i.query, CacheEntry(
            response=final_response,
            source_metadata=all_source_metadata,
            agents_used=agents_used,
            tokens_consumed=total_tokens,
            token_usage=token_usage_dict,
        ))

    # Done event — includes the detailed per-agent / per-model / per-call
    # token breakdown captured by the request-scoped tracker.
    # The previously-emitted flat `total_tokens` int has been removed in
    # favour of `token_usage.total_tokens`; clients should read that.
    token_usage_dict = token_tracker.to_dict(include_calls=True)
    yield _sse({
        "type": "done",
        "agents_used": agents_used,
        "token_usage": token_usage_dict,
        "thread_id": i.thread_id,
        "conversation_turn": conversation_turn,
        "query_rewritten": query_rewritten,
        "effective_query": effective_query if query_rewritten else None,
        "has_draft_continuation": draft_continuation_data is not None,
    })