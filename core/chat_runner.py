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
import re
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
    regenerate_of: str | None = None            # Sagar bug #5: refine previous response
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


# Defense-in-depth HTML stripper for final response. The drafting validator
# already strips HTML from drafting outputs, but ANY agent (Legislation,
# Judgment, Scenario, Constitution/Maxim, etc.) could in principle emit
# HTML the frontend renders as literal text. This is the last line of
# defense between the LLM and the user.
#
# <br> and <hr> are DELIBERATELY preserved. They are the only way to
# express a line break or horizontal rule INSIDE a markdown table cell
# (where "\n" terminates the row and breaks the table). The upstream
# sanitize_markdown folds multi-line cell content via <br>; converting
# them to "\n" here would undo that fold and re-break the same tables
# the sanitizer just fixed. Both tags are structural, not executable —
# no XSS surface. Every other tag is stripped by _FINAL_TAG_RE.
_FINAL_BR_RE = re.compile(r"<br\s*/?>", flags=re.IGNORECASE)
_FINAL_HR_RE = re.compile(r"<hr\s*/?>", flags=re.IGNORECASE)
_FINAL_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?/?>")

# Placeholder tokens used to shuttle <br>/<hr> past the general tag
# strip. Chosen to be tokens the LLM will never legitimately produce.
_BR_SENTINEL = "\x00LAWTECHBRSENTINEL\x00"
_HR_SENTINEL = "\x00LAWTECHHRSENTINEL\x00"


def _strip_html_from_response(text: str) -> str:
    """Strip HTML tags from a final response. See module-level comment."""
    if not text or "<" not in text:
        return text
    # Swap <br>/<hr> for sentinel tokens so the general tag strip
    # doesn't erase them; restore after.
    out = _FINAL_BR_RE.sub(_BR_SENTINEL, text)
    out = _FINAL_HR_RE.sub(_HR_SENTINEL, out)
    out, n = _FINAL_TAG_RE.subn("", out)
    out = out.replace(_BR_SENTINEL, "<br>").replace(_HR_SENTINEL, "<hr>")
    if n:
        log.warning("Final-response HTML strip", tags_removed=n,
                    preview=text[:200])
    return out


# Defense-in-depth: scrub tag-shaped fragments from every token chunk
# BEFORE it reaches the client. The final strip above already fires on
# the terminal `response` event, but token events flow to the browser
# in real time — if an agent emits a full `<script>...</script>` inside
# a single chunk, the frontend markdown renderer sees it before the
# terminal strip runs. Multi-token spanning attacks (e.g. `<scri` +
# `pt>`) still fall to the terminal strip; single-token attacks stop
# here. Cheap (compiled regex), preserves legit `<= 5` etc.
def _strip_html_from_token(text: str) -> str:
    if not text or "<" not in text:
        return text
    scrubbed, n = _FINAL_TAG_RE.subn("", text)
    if n:
        log.warning("Token-level HTML strip",
                    tags_removed=n, preview=text[:120])
    return scrubbed


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

    # Bound the integration status + content extraction call. A stalled
    # FSD JWT check would otherwise hang the entire SSE stream until the
    # outer 300s envelope fires (with no informative event in between).
    # On timeout we emit an integration_error and continue without
    # integration context — the graph proceeds normally.
    from core.settings import INTEGRATION_POLL_TIMEOUT_SEC
    try:
        _, statuses, contents = await asyncio.wait_for(
            process_integration_urls(query, integration_token, client),
            timeout=INTEGRATION_POLL_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        log.warning(
            "Integration status/content fetch timed out",
            timeout_s=INTEGRATION_POLL_TIMEOUT_SEC,
            providers=providers_needed,
        )
        for provider in providers_needed:
            yield (_sse({
                "type": "integration_error",
                "provider": provider,
                "message": (
                    f"{provider.title()} connection is unreachable right "
                    "now — continuing without linked content."
                ),
            }), None)
        yield ("", None)
        return

    # For each disconnected provider, emit an auth_url event
    for provider in providers_needed:
        if not statuses.get(provider, False):
            try:
                auth_url = await asyncio.wait_for(
                    client.get_auth_url(provider, integration_token),
                    timeout=10,
                )
            except asyncio.TimeoutError:
                auth_url = None
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
        cached = response_cache.get(
            i.query,
            language=(i.preferred_language or ""),
            cite_appendix=i.cite_appendix,
        )
        if cached:
            _hit_start = time.perf_counter()
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

            # Persist this turn to chat history even on a cache hit — otherwise
            # Turn 2 loads empty context from Postgres and the assistant
            # silently "forgets" Turn 1. Typed state (user_intent, task,
            # tasks_planned) is empty because the orchestrator did not run;
            # tasks_planned_json carries agents_used from the cached entry so
            # the follow-up router still sees what produced the answer.
            cached_turn = 0
            try:
                cached_turn = await chat_store.save_turn(
                    i.thread_id, i.query, cached.response,
                    user_intent_json="",
                    task="",
                    tasks_planned_json=json.dumps(cached.agents_used or []),
                    primary_artifact_kind="",
                )
            except Exception as e:
                log.error("Failed to save cached turn to chat history",
                          endpoint=i.endpoint_name, error=str(e))

            # Observability: record request in the same log as live paths,
            # tagged with fallback_used=False so operators can distinguish
            # cache-served turns from graph-served ones via total_latency_ms.
            _hit_latency_ms = int((time.perf_counter() - _hit_start) * 1000)
            _fire_and_forget(chat_store.log_request(
                thread_id=i.thread_id,
                endpoint=i.endpoint_name,
                query_preview=i.query[:300],
                user_language=(i.preferred_language or "en"),
                tasks_planned=[],
                agents_used=cached.agents_used or [],
                total_latency_ms=_hit_latency_ms,
                total_tokens=cached.tokens_consumed or 0,
                fallback_used=False,
                is_blocked=False,
            ))

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
                "calls": [],
            }
            # Align the cached `done` event with the non-cached emission
            # (see the terminal `done` yield below). Frontend consumers
            # rely on `conversation_turn`, `query_rewritten`,
            # `effective_query` being present on EVERY done event;
            # omitting them on cache hits broke consumers that treat the
            # payload as invariant. Cache hits use turn from save_turn,
            # query not rewritten (no memory step ran), effective_query=None.
            yield _sse({
                "type": "done",
                "agents_used": cached.agents_used,
                "token_usage": cached_token_usage,
                "thread_id": i.thread_id,
                "conversation_turn": cached_turn,
                "query_rewritten": False,
                "effective_query": None,
                "source_metadata": cached.source_metadata,  # kept for backward compat
                "cached": True,
            })
            log.info("Cache hit", endpoint=i.endpoint_name,
                     agents=cached.agents_used, turn=cached_turn)
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
        regenerate_of=i.regenerate_of,
    )
    if integration_context_dict:
        initial_state["integration_context"] = integration_context_dict

    config = {"configurable": {"thread_id": i.thread_id}}

    # Initialise the per-request token tracker. Every LLM call made by
    # any agent / orchestrator step on this asyncio task (and its
    # spawned children, since asyncio propagates contextvars) will
    # accumulate into this instance. Read out at the end and surface
    # in the `done` SSE event.
    # /pyapi/chat starts the tracker before process_files runs so Vision
    # OCR tokens land in the same tracker; reuse it when present instead
    # of overwriting (which would drop the FileProcessor calls already
    # recorded during file processing).
    from core.token_tracker import (
        get_tracker as _get_token_tracker,
        start_request as _start_token_tracking,
    )
    token_tracker = _get_token_tracker() or _start_token_tracking()

    step_count = 0
    final_response = ""
    agents_used: list[str] = []
    tasks_planned_stream: list[str] = []
    user_language_stream = "en"
    total_tokens = 0
    all_source_metadata: list[dict] = []
    effective_query = i.query
    query_rewritten = False
    # Track whether any agent fell back to web-grounded search (Tier 3).
    # If true, the response reflects a non-deterministic branch (ES-hits
    # vs web-fallback flips on repeat calls when the relevance gate is
    # borderline), so we must NOT cache it — the next user with the same
    # query would get the wrong branch pinned for the TTL. See the
    # RESPONSE_CACHE_ENABLED comment in core/settings.py.
    any_fallback_used = False
    # Track whether the guardrail rewrote final_response into a block
    # message. Caching a block would replay it to every subsequent user
    # of the same query text for the TTL.
    turn_is_blocked = False

    # Per-turn typed state captured for Level 1 of the follow-up simplification
    # (see docs/followup_pipeline_simplification_plan.md). Persisted alongside
    # user_query/ai_response so Turn N+1 can inherit the previous turn's
    # decisions instead of re-deriving them from chat-history text.
    turn_user_intent = None            # UserIntent | None from orchestrator plan
    turn_task = ""                     # primary task from orchestrator plan
    turn_primary_artifact_kind = ""    # "draft" when Drafting produced content

    try:
        # Seed the request-scoped deadline (285s = 300s outer envelope
        # minus 15s slack for final `response`/`sources`/`done` events).
        # Deeply nested `bounded_wait_for` calls clamp their local
        # timeouts to what's left, so a slow judge doesn't blow the
        # budget for the refiner. Contextvar propagates to every child
        # asyncio task spawned during the graph run; when this SSE
        # generator ends the contextvar's scope ends with it.
        from core.deadline import set_deadline_seconds
        set_deadline_seconds(285)
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
                            yield _sse({
                                "type": "token",
                                "content": _strip_html_from_token(chunk["content"]),
                            })
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

                    # Capture the primary task + typed intent from the
                    # orchestrator plan node so we can persist them on this
                    # turn (see docs/followup_pipeline_simplification_plan.md).
                    # `task` is the LLM-picked primary; `user_intent` is the
                    # typed extractor output. Both may appear in the same
                    # update or in separate updates depending on the graph
                    # flush schedule; both branches are last-write-wins which
                    # matches the graph's own reducer behaviour.
                    if "task" in update and update["task"]:
                        turn_task = update["task"]
                    if "user_intent" in update and update["user_intent"] is not None:
                        turn_user_intent = update["user_intent"]

                    if "agent_results" in update:
                        for name, r in update["agent_results"].items():
                            if hasattr(r, "tokens_consumed"):
                                total_tokens += r.tokens_consumed or 0
                            # Any agent that fell back to web-grounded
                            # search (Tier 3) taints the response for
                            # caching purposes — see any_fallback_used
                            # init comment above.
                            if getattr(r, "fallback_used", False):
                                any_fallback_used = True
                            # Mark the turn as producing a modifiable draft
                            # artefact whenever Drafting returned content
                            # without an error. Level 2 uses this signal to
                            # route follow-up directives ("in Marathi", "add
                            # a prayer clause") through a fast-path modifier
                            # instead of re-running the full drafting pipeline.
                            if (
                                name == "Drafting"
                                and getattr(r, "content", "")
                                and not getattr(r, "error", None)
                            ):
                                turn_primary_artifact_kind = "draft"

                    if "source_metadata" in update and update["source_metadata"]:
                        all_source_metadata = update["source_metadata"]

                    if "is_blocked" in update and update["is_blocked"]:
                        turn_is_blocked = True

    except TimeoutError:
        log.error("Stream timed out after 300s",
                  endpoint=i.endpoint_name, thread_id=i.thread_id[:12])
        yield _sse({"type": "error", "data": "Request timed out. Please try a simpler query."})
    except Exception as e:
        log.error("Stream error", endpoint=i.endpoint_name, error=str(e))
        # Scrub brand/model tokens before surfacing the upstream exception
        # text. SDK errors often carry "google.genai" / "gemini" /
        # "openai" / "anthropic" identifiers that must not reach the user.
        from core.redact import redact_brands
        yield _sse({"type": "error", "data": redact_brands(str(e))})

    # --- Final events ------------------------------------------------------
    # Strip any HTML before delivery. validate_draft already runs in the
    # drafting agent, but this catches HTML from any other agent path too.
    final_response = _strip_html_from_response(final_response)

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

    # Quality scoring — per-agent sample rate handled by should_score()
    # (retrieval agents 10%, Drafting 5%). Fire-and-forget.
    if i.enable_quality_scoring and final_response:
        from core.quality import score_response, should_score
        if should_score(agents_used):
            _fire_and_forget(score_response(
                query=i.query,
                response=final_response,
                agents_used=agents_used,
                thread_id=i.thread_id,
            ))

    # Save chat history + per-turn typed state (Level 1 of the follow-up
    # simplification: docs/followup_pipeline_simplification_plan.md).
    conversation_turn = 0
    if final_response:
        # Serialise UserIntent to JSON if we captured one. UserIntent is a
        # Pydantic BaseModel so model_dump_json() is available; a bare LLM-
        # failure fallback (default_intent) still serialises cleanly.
        _intent_json = ""
        if turn_user_intent is not None:
            try:
                _intent_json = turn_user_intent.model_dump_json()
            except Exception as e:
                # Non-fatal — turn still persists, next turn just won't
                # have the typed intent to inherit.
                log.warning("Failed to serialise user_intent; storing empty",
                            error=str(e)[:200])
        try:
            conversation_turn = await chat_store.save_turn(
                i.thread_id, effective_query, final_response,
                user_intent_json=_intent_json,
                task=turn_task,
                tasks_planned_json=json.dumps(tasks_planned_stream or []),
                primary_artifact_kind=turn_primary_artifact_kind,
            )
        except Exception as e:
            log.error("Failed to save chat history",
                      endpoint=i.endpoint_name, error=str(e))

    # Compute token usage NOW so it's available for both the cache write
    # and the done event below. Previously this assignment lived AFTER the
    # cache-set block, which crashed every streaming first-turn request
    # with UnboundLocalError on `token_usage_dict` (see prod error.log
    # circa 2026-06-04, plus the chronic flakiness of
    # tests/integration/test_api.py::test_stream_final_event_has_result).
    token_usage_dict = token_tracker.to_dict(include_calls=True)

    # Cache first-turn responses. We SKIP caching when:
    #   - uploads / integration context were used (per-request content)
    #   - the guardrail rewrote the response into a block message
    #     (would replay the block to every future user of the same query)
    #   - any agent fell back to web-grounded search (the response reflects
    #     a non-deterministic branch — see any_fallback_used init comment)
    cacheable = (
        i.enable_cache
        and i.is_first_turn
        and final_response
        and not i.file_context
        and not integration_context_dict
        and not turn_is_blocked
        and not any_fallback_used
    )
    if cacheable:
        response_cache.set(
            i.query,
            CacheEntry(
                response=final_response,
                source_metadata=all_source_metadata,
                agents_used=agents_used,
                tokens_consumed=total_tokens,
                token_usage=token_usage_dict,
            ),
            language=(i.preferred_language or ""),
            cite_appendix=i.cite_appendix,
        )

    # Done event — includes the detailed per-agent / per-model / per-call
    # token breakdown captured by the request-scoped tracker.
    # The previously-emitted flat `total_tokens` int has been removed in
    # favour of `token_usage.total_tokens`; clients should read that.
    yield _sse({
        "type": "done",
        "agents_used": agents_used,
        "token_usage": token_usage_dict,
        "thread_id": i.thread_id,
        "conversation_turn": conversation_turn,
        "query_rewritten": query_rewritten,
        "effective_query": effective_query if query_rewritten else None,
    })