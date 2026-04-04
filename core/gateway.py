"""FastAPI gateway — thin API layer.

This replaces v1's main.py + routes/llm_answer.py.
The gateway does NOT contain business logic. It only:
1. Accepts HTTP requests
2. Validates input schema
3. Invokes the LangGraph agent graph
4. Returns the response

Supports both batch (POST /search) and streaming (POST /search/stream) modes.
Also provides PDF upload/processing/chat endpoints that work outside the
LangGraph agent flow (direct tool calls).

Ref: https://docs.langchain.com/oss/python/langchain/streaming/overview
All intelligence lives in the agents.
"""

import asyncio
from contextlib import asynccontextmanager
import io
import json
import os
import random

# asyncio.timeout() is Python 3.11+; fall back to async_timeout on 3.10
try:
    from asyncio import timeout as _async_timeout
except ImportError:
    from async_timeout import timeout as _async_timeout
import psutil
import shutil
import tempfile
import time
import uuid
from typing import Optional, List

from fastapi import Depends, FastAPI, HTTPException, Request, File, Form, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from pydantic import BaseModel, Field, ConfigDict
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from werkzeug.utils import secure_filename

from .settings import HOST, PORT, RATE_LIMIT_PER_MINUTE, RATE_LIMIT_ADMIN_PER_MINUTE, CHROMA_STORE_ROOT, UPLOADS_ROOT
from .graph import compile_graph
from .checkpointer import create_checkpointer
from .logger import get_logger, set_request_id, log_time
from .chat_store import chat_store
from .metrics import METRICS
from .quality import score_response as _score_response
from .response_cache import response_cache, CacheEntry
from .auth import require_user_key, require_admin_key, get_key_identifier, _ADMIN_API_KEY

log = get_logger("Gateway")


def _fire_and_forget(coro) -> asyncio.Task:
    """Schedule a coroutine as a background task with error logging.

    Unlike bare asyncio.create_task(), exceptions are logged instead of
    silently discarded, so audit trail failures become visible.
    """
    task = asyncio.create_task(coro)

    def _on_done(t: asyncio.Task):
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            log.error("Background task failed", error=str(exc), exc_type=type(exc).__name__)

    task.add_done_callback(_on_done)
    return task

# --- Consistent error response helpers ---

_HTTP_ERROR_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    422: "validation_error",
    429: "rate_limit_exceeded",
    500: "internal_server_error",
    502: "bad_gateway",
    503: "service_unavailable",
    504: "gateway_timeout",
}


def _error_response(status_code: int, error: str, message: str, request_id: str = "") -> JSONResponse:
    """Return a uniform error JSON body across all error handlers."""
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "message": message, "request_id": request_id},
    )


# --- Rate Limiter (key by API key or IP; admin keys get distinct bucket) ---
def _rate_limit_key(request: Request) -> str:
    """Rate limit key function. Admin keys get a distinct key prefix
    so they don't share the per-user bucket."""
    key = request.headers.get("X-API-Key", "")
    if _ADMIN_API_KEY and key == _ADMIN_API_KEY:
        return "admin__exempt"
    if key:
        return get_key_identifier(key)
    return request.client.host if request.client else "unknown"


def _get_limit_for_request() -> str:
    """Callable for @limiter.limit — slowapi calls this with no args."""
    return f"{RATE_LIMIT_PER_MINUTE}/minute"


limiter = Limiter(key_func=_rate_limit_key)

# --- Lifespan: init checkpointer + graph on startup, close pool on shutdown ---
_APP_START_TIME = time.time()
_pg_pool = None  # kept for graceful shutdown


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pg_pool
    log.info("Startup: initializing checkpointer and compiling agent graph")
    checkpointer, _pg_pool = await create_checkpointer()
    app.state.agent_graph = compile_graph(checkpointer=checkpointer)
    log.info("Startup complete")
    yield
    # Shutdown
    if _pg_pool is not None:
        log.info("Shutdown: closing PostgreSQL checkpointer pool")
        await _pg_pool.close()
    # Close sync chat-store pool if PostgreSQL backend
    from .chat_store import chat_store as _cs
    if hasattr(_cs, "_pool") and _cs._pool is not None:
        await asyncio.to_thread(_cs._pool.close)
        log.info("Shutdown: closed chat-store pool")


# --- App ---
app = FastAPI(title="Legal AI API v2", version="2.0.0", lifespan=lifespan)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    req_id = getattr(request.state, "request_id", "")
    return _error_response(429, "rate_limit_exceeded", "Rate limit exceeded. Please slow down.", req_id)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    req_id = getattr(request.state, "request_id", "")
    error_code = _HTTP_ERROR_CODES.get(exc.status_code, f"http_{exc.status_code}")
    return _error_response(exc.status_code, error_code, str(exc.detail), req_id)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    req_id = getattr(request.state, "request_id", "")
    errors = exc.errors()
    message = errors[0]["msg"] if errors else "Request validation error"
    return _error_response(422, "validation_error", message, req_id)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    req_id = getattr(request.state, "request_id", "")
    log.error("Unhandled exception", exc_info=True, request_id=req_id, path=request.url.path)
    return _error_response(500, "internal_server_error", "An unexpected error occurred.", req_id)

# CORS: restrict to configured domain in production (set ALLOWED_ORIGINS in .env)
# Example: ALLOWED_ORIGINS=https://yourdomain.com,https://app.yourdomain.com
_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
_cors_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request ID middleware (must run before metrics so errors include request_id) ─

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Assign a short request ID to every request so exception handlers can include it."""
    rid = str(uuid.uuid4())[:8]
    request.state.request_id = rid
    set_request_id(rid)
    return await call_next(request)


# ── Prometheus metrics middleware ─────────────────────────────────────────────

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Record HTTP request count, latency, and active request gauge."""
    # Exclude the metrics endpoint itself to avoid noise
    if request.url.path == "/pyapi/metrics":
        return await call_next(request)

    METRICS["active_requests"].inc()
    t0 = time.time()
    response = None
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        latency = time.time() - t0
        endpoint = request.url.path
        METRICS["active_requests"].dec()
        METRICS["requests_total"].labels(
            endpoint=endpoint,
            method=request.method,
            status_code=str(status_code),
        ).inc()
        METRICS["request_latency_seconds"].labels(endpoint=endpoint).observe(latency)


# --- Frontend UI ---
_FRONTEND_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend.html")

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve the frontend UI at root."""
    try:
        with open(_FRONTEND_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Frontend not found</h1>", status_code=404)

# --- Word Add-in Static Files ---
_WORD_ADDIN_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "word-addin")
if os.path.isdir(_WORD_ADDIN_DIR):
    from fastapi.staticfiles import StaticFiles
    app.mount("/word-addin", StaticFiles(directory=_WORD_ADDIN_DIR, html=True), name="word-addin")


# --- Request / Response Schemas ---

_VALID_LANGUAGES = {
    "en", "hi", "bn", "te", "mr", "ta", "kn", "ml", "gu", "pa", "ur", "or", "as", "sa",
}


class SearchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    # alias keeps backward-compat with existing clients sending "Promptquery"
    prompt_query: str = Field(..., alias="Promptquery", min_length=1, max_length=30000)
    globalThreadId: Optional[str] = None
    preferred_language: Optional[str] = None  # ISO 639-1 override (skips auto-detection)


class SearchResponse(BaseModel):
    globalThreadId: Optional[str]
    result: str
    total_tokens_consumed: int
    source: list[dict]
    agents_used: list[str]
    # Memory context metadata
    effective_query: Optional[str] = None
    query_rewritten: bool = False
    conversation_turn: int = 0
    # UX features
    followup_suggestions: list[str] = []


class FeedbackRequest(BaseModel):
    thread_id: str = Field(..., min_length=1)
    turn_number: int = Field(..., ge=1)
    rating: str = Field(..., pattern=r"^(up|down)$")
    comment: Optional[str] = None


class PdfChatRequest(BaseModel):
    uniqueString: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1, max_length=30000)


# --- Helpers ---

async def _generate_followup_suggestions(
    query: str, response: str, agents_used: list[str],
) -> list[str]:
    """Generate 3 follow-up question suggestions using Gemini Flash Lite."""
    from core.clients import get_gemini_flash as get_gemini_flash_lite
    from langchain_core.prompts import ChatPromptTemplate
    from config.prompts import FOLLOWUP_SUGGESTIONS_PROMPT

    llm = get_gemini_flash_lite(temperature=0.7)
    prompt = ChatPromptTemplate.from_template(FOLLOWUP_SUGGESTIONS_PROMPT)
    chain = prompt | llm
    result = await chain.ainvoke({
        "query": query,
        "response_preview": response[:500],
        "agents_used": ", ".join(agents_used) if agents_used else "general",
    })

    # Parse JSON array from response — handle various LLM output formats
    text = result.content.strip()
    # Strip markdown code fences if present (``` or single `)
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    elif text.startswith("`") and text.endswith("`"):
        text = text.strip("`").strip()
    # Extract JSON array from surrounding text if needed
    import re
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        suggestions = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        log.debug("Followup suggestions JSON parse failed", text=text[:100])
        return []
    # Handle dict wrapper like {"suggestions": [...]}
    if isinstance(suggestions, dict):
        for v in suggestions.values():
            if isinstance(v, list):
                suggestions = v
                break
    if isinstance(suggestions, list) and len(suggestions) >= 1:
        return [str(s)[:60] for s in suggestions[:3]]
    return []


def _build_initial_state(
    query: str, thread_id: str,
    unique_string: str | None = None,
    draft_continuation: dict | None = None,
    file_context: dict | None = None,
    preferred_language: str | None = None,
) -> dict:
    """Build the initial LangGraph state with all required fields."""
    # Validate and normalise preferred_language (client override for user_language)
    lang = ""
    if preferred_language and preferred_language in _VALID_LANGUAGES:
        lang = preferred_language
    return {
        "messages": [],
        "original_query": query,
        "query": query,
        "thread_id": thread_id,
        "unique_string": unique_string,
        "user_language": lang,   # "" → memory node will auto-detect; non-empty → skip detection
        "task": None,
        "tasks_planned": [],
        "agent_queries": {},
        "response_instructions": "",
        "chat_history": [],
        "summary_text": "",
        "agent_results": {},
        "is_blocked": False,
        "block_reason": None,
        "file_context": file_context,
        "draft_continuation": draft_continuation,
        "final_response": "",
        "source_metadata": [],
        "tokens_consumed": 0,
    }


def _safe_persist_dir(unique_string: str) -> str:
    """Sanitize unique_string to prevent path traversal."""
    safe_name = secure_filename(unique_string)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid uniqueString")
    persist_dir = os.path.realpath(os.path.join(CHROMA_STORE_ROOT, safe_name))
    if not persist_dir.startswith(CHROMA_STORE_ROOT):
        raise HTTPException(status_code=400, detail="Invalid uniqueString")
    return persist_dir


# ============================================================
# Legal Q&A Routes (LangGraph agent flow)
# ============================================================

@app.post("/pyapi/search", response_model=SearchResponse, dependencies=[Depends(require_user_key)])
@limiter.limit(_get_limit_for_request)
async def search(data: SearchRequest, request: Request):
    """Main legal Q&A endpoint (batch mode).

    Invokes the full multi-agent graph and returns the complete response:
    guardrail → memory → orchestrator → [domain agents] → synthesize → guardrail
    """
    agent_graph = request.app.state.agent_graph
    thread_id = data.globalThreadId or str(uuid.uuid4())
    req_id = set_request_id(thread_id[:8])
    _req_start = time.perf_counter()

    log.info("Search request received",
             query=data.prompt_query[:100], thread_id=thread_id,
             query_len=len(data.prompt_query))

    # --- Cache check: skip graph for repeated first-turn queries ---
    _is_first_turn = not data.globalThreadId  # no thread = first turn
    if _is_first_turn:
        cached = response_cache.get(data.prompt_query)
        if cached:
            _latency_ms = int((time.perf_counter() - _req_start) * 1000)
            log.info("Cache hit, returning cached response",
                     latency_ms=_latency_ms, agents=cached.agents_used)
            return SearchResponse(
                globalThreadId=thread_id,
                result=cached.response,
                total_tokens_consumed=cached.tokens_consumed,
                source=cached.source_metadata,
                agents_used=cached.agents_used,
            )

    initial_state = _build_initial_state(
        data.prompt_query, thread_id, preferred_language=data.preferred_language
    )
    config = {"configurable": {"thread_id": thread_id}}

    _graph_error: str | None = None
    try:
        with log_time(log, "Full graph execution"):
            final_state = await asyncio.wait_for(
                agent_graph.ainvoke(initial_state, config=config),
                timeout=300,  # 5 minute hard timeout
            )
    except asyncio.TimeoutError:
        _graph_error = "timeout"
        log.error("Agent graph execution timed out after 300s", query=data.prompt_query[:100])
        raise HTTPException(status_code=504, detail="Request timed out. Please try a simpler query.")
    except Exception as e:
        _graph_error = str(e)[:200]
        log.error("Agent graph execution failed", exc_info=True, error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")

    # Handle blocked queries
    if final_state.get("is_blocked"):
        log.warning("Query blocked by safety filter",
                    reason=final_state.get("block_reason", "unknown"))
        return SearchResponse(
            globalThreadId=thread_id,
            result=final_state.get("block_reason", "Query blocked by safety filter."),
            total_tokens_consumed=0,
            source=[{"source_type": "blocked", "title": "Blocked", "content": ["Query blocked by safety filter"]}],
            agents_used=[],
        )

    # Build response
    agent_results = final_state.get("agent_results", {})
    agents_used = list(agent_results.keys())
    total_tokens = final_state.get("tokens_consumed", 0)
    response_len = len(final_state.get("final_response", ""))

    log.info("Search request completed",
             agents_used=agents_used, total_tokens=total_tokens,
             response_len=response_len, thread_id=thread_id)

    # --- Record metrics ---
    _latency_ms = int((time.perf_counter() - _req_start) * 1000)
    for agent in agents_used:
        METRICS["agent_invocations_total"].labels(agent=agent).inc()
    for task in final_state.get("tasks_planned", []):
        METRICS["tasks_planned_total"].labels(task_type=task).inc()
    if final_state.get("is_blocked"):
        METRICS["guardrail_blocks_total"].labels(stage="input").inc()
    if total_tokens > 0:
        METRICS["llm_tokens_total"].labels(model="total", token_type="output").inc(total_tokens)

    # --- Log request to SQLite (fire-and-forget) ---
    _fallback_used = any(
        r.get("fallback_used") for r in final_state.get("agent_results", {}).values()
        if isinstance(r, dict)
    )
    _fire_and_forget(chat_store.log_request(
        thread_id=thread_id,
        endpoint="/pyapi/search",
        query_preview=data.prompt_query[:300],
        user_language=final_state.get("user_language", "en"),
        tasks_planned=final_state.get("tasks_planned", []),
        agents_used=agents_used,
        total_latency_ms=_latency_ms,
        total_tokens=total_tokens,
        fallback_used=_fallback_used,
        is_blocked=bool(final_state.get("is_blocked")),
        error=_graph_error,
    ))

    # --- L4: Quality scoring (10% sample, fire-and-forget) ---
    _final_response = final_state.get("final_response", "")
    if _final_response and not final_state.get("is_blocked") and random.random() < 0.10:
        _fire_and_forget(_score_response(
            query=data.prompt_query,
            response=_final_response,
            agents_used=agents_used,
            thread_id=thread_id,
        ))

    # Memory context metadata
    effective_query = final_state.get("query", data.prompt_query)
    query_rewritten = effective_query != data.prompt_query
    conversation_turn = 0

    # Save chat history to SQLite (non-blocking, don't fail the response)
    final_response = final_state.get("final_response", "")
    if not final_state.get("is_blocked") and final_response:
        try:
            conversation_turn = await chat_store.save_turn(
                thread_id, effective_query, final_response
            )
            log.debug("Chat history saved",
                      thread_id=thread_id[:12], turn=conversation_turn)
        except Exception as e:
            log.error("Failed to save chat history",
                      thread_id=thread_id[:12], error=str(e))

    # Generate follow-up suggestions (non-blocking)
    followup_suggestions = []
    if final_response and not final_state.get("is_blocked"):
        try:
            followup_suggestions = await _generate_followup_suggestions(
                data.prompt_query, final_response, agents_used
            )
        except Exception as e:
            log.warning("Followup suggestions failed", error=str(e))

    # --- Cache store: save first-turn responses for future hits ---
    if _is_first_turn and final_response and not final_state.get("is_blocked"):
        response_cache.set(data.prompt_query, CacheEntry(
            response=final_response,
            source_metadata=final_state.get("source_metadata", []),
            agents_used=agents_used,
            tokens_consumed=total_tokens,
        ))

    return SearchResponse(
        globalThreadId=thread_id,
        result=final_response,
        total_tokens_consumed=total_tokens,
        source=final_state.get("source_metadata", []),
        agents_used=agents_used,
        effective_query=effective_query if query_rewritten else None,
        query_rewritten=query_rewritten,
        conversation_turn=conversation_turn,
        followup_suggestions=followup_suggestions,
    )


# --- Node → User-Friendly Status Messages ---

_NODE_STATUS = {
    "guardrail_input": "Validating query...",
    "memory": "Loading context...",
    "orchestrator_plan": "Planning search strategy...",
    "legislation": "Searching legislation...",
    "judgment": "Searching court judgments...",
    "newacts": "Searching legal provisions...",
    "drafting": "Generating legal draft...",
    "scenario": "Analyzing legal scenario...",
    "constitution": "Searching constitutional provisions...",
    "maxim": "Searching legal maxims...",
    "legal_concepts": "Explaining legal concepts...",
    "document": "Searching uploaded documents...",
    "sci_judgment": "Searching Supreme Court judgments...",
    "orchestrator_synthesize": "Injecting citations into draft...",
    "guardrail_output": "Finalizing...",
    "blocked_response": "Query blocked.",
}


@app.post("/pyapi/search/stream", dependencies=[Depends(require_user_key)])
@limiter.limit(_get_limit_for_request)
async def search_stream(data: SearchRequest, request: Request):
    """Streaming legal Q&A endpoint (SSE).

    Streams real-time progress as Server-Sent Events:
    - status: Human-readable progress messages for each agent step
    - response: The final clean answer (after all agents complete)
    - done: Completion signal with metadata

    Uses stream_mode=["updates", "custom"] to get both:
    - Node-level updates (status messages, state captures)
    - Token-by-token streaming from domain agents via get_stream_writer()
    """
    agent_graph = request.app.state.agent_graph
    thread_id = data.globalThreadId or str(uuid.uuid4())
    req_id = set_request_id(thread_id[:8])

    log.info("Stream request received",
             query=data.prompt_query[:100], thread_id=thread_id)

    # --- Cache check for streaming endpoint ---
    _is_first_turn = not data.globalThreadId
    if _is_first_turn:
        cached = response_cache.get(data.prompt_query)
        if cached:
            async def _cached_stream():
                yield f"data: {json.dumps({'type': 'thread_id', 'data': thread_id})}\n\n"
                yield f"data: {json.dumps({'type': 'status', 'message': 'Returning cached response...'})}\n\n"
                yield f"data: {json.dumps({'type': 'response', 'content': cached.response})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'agents_used': cached.agents_used, 'total_tokens': cached.tokens_consumed, 'source_metadata': cached.source_metadata})}\n\n"
            log.info("Stream cache hit", agents=cached.agents_used)
            return StreamingResponse(
                _cached_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    initial_state = _build_initial_state(
        data.prompt_query, thread_id, preferred_language=data.preferred_language
    )
    config = {"configurable": {"thread_id": thread_id}}

    async def event_generator():
        """Generate SSE events from the agent graph stream."""
        # Send thread_id as first event
        yield f"data: {json.dumps({'type': 'thread_id', 'data': thread_id})}\n\n"
        start = time.perf_counter()
        step_count = 0
        final_response = ""
        agents_used = []
        tasks_planned_stream: list[str] = []
        user_language_stream: str = "en"
        total_tokens = 0
        all_source_metadata = []
        effective_query = data.prompt_query
        query_rewritten = False
        draft_continuation_data = None

        try:
          async with _async_timeout(300):  # 5 min hard timeout on streaming
            async for event in agent_graph.astream(
                initial_state,
                config=config,
                stream_mode=["updates", "custom"],
            ):
                mode, chunk = event

                # --- Custom events: token-by-token streaming ---
                if mode == "custom":
                    if isinstance(chunk, dict):
                        if chunk.get("type") == "token":
                            yield f"data: {json.dumps({'type': 'token', 'content': chunk['content']})}\n\n"
                        elif chunk.get("type") == "token_reset":
                            yield f"data: {json.dumps({'type': 'token_reset'})}\n\n"
                        elif chunk.get("type") == "drafting_progress":
                            yield "data: {}\n\n".format(json.dumps({
                                "type": "drafting_progress",
                                "section": chunk["section"],
                                "total": chunk["total"],
                                "title": chunk["title"],
                            }))
                        elif chunk.get("type") == "draft_incomplete":
                            yield "data: {}\n\n".format(json.dumps({
                                "type": "draft_incomplete",
                                "failed_sections": chunk["failed_sections"],
                                "total_sections": chunk["total_sections"],
                                "completed_sections": chunk["completed_sections"],
                            }))
                        elif chunk.get("type") == "queue_status":
                            yield f"data: {json.dumps({'type': 'status', 'agent': 'queue', 'message': chunk.get('message', 'Waiting for available slot...')})}\n\n"
                        elif chunk.get("type") == "progress":
                            yield f"data: {json.dumps(chunk)}\n\n"
                    continue

                # --- Update events: node-level progress ---
                for node_name, update in chunk.items():
                    step_count += 1
                    log.debug("SSE agent step",
                              node=node_name, step=step_count)

                    # Send human-readable progress status
                    status_msg = _NODE_STATUS.get(node_name, f"Processing {node_name}...")
                    status_event = {
                        "type": "status",
                        "agent": node_name,
                        "message": status_msg,
                    }
                    yield f"data: {json.dumps(status_event)}\n\n"

                    # After memory agent: send context event with rewrite info
                    if node_name == "memory" and "query" in update:
                        effective_query = update["query"]
                        query_rewritten = effective_query != data.prompt_query
                        history_msgs = update.get("chat_history", [])
                        context_event = {
                            "type": "context",
                            "query_rewritten": query_rewritten,
                            "effective_query": effective_query if query_rewritten else None,
                            "history_turns": len(history_msgs) // 2,
                            "has_summary": bool(update.get("summary_text")),
                        }
                        yield f"data: {json.dumps(context_event)}\n\n"

                    # Track final response from guardrail_output
                    if "final_response" in update and update["final_response"]:
                        final_response = update["final_response"]

                    # Track task info from orchestrator + emit early agent badges
                    if "tasks_planned" in update and update["tasks_planned"]:
                        tasks_planned_stream = update["tasks_planned"]
                        agents_used = update["tasks_planned"]
                        yield f"data: {json.dumps({'type': 'agents_planned', 'agents': agents_used})}\n\n"

                    # Capture user_language from memory node
                    if node_name == "memory" and "user_language" in update:
                        user_language_stream = update.get("user_language", "en") or "en"

                    # Track tokens from agent results
                    if "agent_results" in update:
                        for name, r in update["agent_results"].items():
                            if hasattr(r, "tokens_consumed"):
                                total_tokens += r.tokens_consumed or 0

                    # Capture source_metadata when it appears
                    if "source_metadata" in update and update["source_metadata"]:
                        all_source_metadata = update["source_metadata"]

                    # Capture draft_continuation when it appears
                    if "draft_continuation" in update and update["draft_continuation"]:
                        draft_continuation_data = update["draft_continuation"]

        except TimeoutError:
            log.error("Stream timed out after 300s", thread_id=thread_id[:12])
            yield f"data: {json.dumps({'type': 'error', 'data': 'Request timed out. Please try a simpler query.'})}\n\n"
        except Exception as e:
            log.error("Stream error", error=str(e))
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)})}\n\n"

        # Send the clean final response
        if final_response:
            response_event = {
                "type": "response",
                "content": final_response,
            }
            yield f"data: {json.dumps(response_event)}\n\n"

        # Send sources
        if all_source_metadata:
            sources_event = {
                "type": "sources",
                "data": all_source_metadata,
            }
            yield f"data: {json.dumps(sources_event)}\n\n"

        # Generate and send follow-up suggestions
        if final_response:
            try:
                suggestions = await _generate_followup_suggestions(
                    data.prompt_query, final_response, agents_used
                )
                if suggestions:
                    yield f"data: {json.dumps({'type': 'followup_suggestions', 'data': suggestions})}\n\n"
            except Exception as e:
                log.warning("Followup suggestions failed (stream)", error=str(e))

        elapsed_ms = (time.perf_counter() - start) * 1000
        log.info("Stream completed",
                 steps=step_count, duration_ms=f"{elapsed_ms:.0f}")

        # --- Log request to SQLite (fire-and-forget) ---
        _fire_and_forget(chat_store.log_request(
            thread_id=thread_id,
            endpoint="/pyapi/search/stream",
            query_preview=data.prompt_query[:300],
            user_language=user_language_stream,
            tasks_planned=tasks_planned_stream,
            agents_used=agents_used,
            total_latency_ms=int(elapsed_ms),
            total_tokens=total_tokens,
            fallback_used=False,
            is_blocked=False,
        ))

        # --- L4: Quality scoring (10% sample, fire-and-forget) ---
        if final_response and random.random() < 0.10:
            _fire_and_forget(_score_response(
                query=data.prompt_query,
                response=final_response,
                agents_used=agents_used,
                thread_id=thread_id,
            ))

        # Save chat history to SQLite after stream completes
        conversation_turn = 0
        if final_response:
            try:
                conversation_turn = await chat_store.save_turn(
                    thread_id, effective_query, final_response
                )
                log.debug("Chat history saved (stream)",
                          thread_id=thread_id[:12], turn=conversation_turn)
            except Exception as e:
                log.error("Failed to save chat history (stream)",
                          thread_id=thread_id[:12], error=str(e))

        # Persist draft continuation metadata for incomplete drafts
        if draft_continuation_data:
            try:
                await chat_store.save_draft_continuation(thread_id, draft_continuation_data)
                log.debug("Draft continuation saved", thread_id=thread_id[:12])
            except Exception as e:
                log.error("Failed to save draft continuation", error=str(e))

        # Cache store for first-turn streaming responses
        if _is_first_turn and final_response and not draft_continuation_data:
            response_cache.set(data.prompt_query, CacheEntry(
                response=final_response,
                source_metadata=all_source_metadata,
                agents_used=agents_used,
                tokens_consumed=total_tokens,
            ))

        # Send completion event with metadata
        done_event = {
            "type": "done",
            "agents_used": agents_used,
            "total_tokens": total_tokens,
            "thread_id": thread_id,
            "conversation_turn": conversation_turn,
            "query_rewritten": query_rewritten,
            "effective_query": effective_query if query_rewritten else None,
            "has_draft_continuation": draft_continuation_data is not None,
        }
        yield f"data: {json.dumps(done_event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# Chat with Files (inline file attachments)
# ============================================================

@app.post("/pyapi/chat", dependencies=[Depends(require_user_key)])
@limiter.limit(_get_limit_for_request)
async def chat_with_files(
    request: Request,
    query: str = Form(...),
    globalThreadId: Optional[str] = Form(None),
    preferred_language: Optional[str] = Form(None),
    files: List[UploadFile] = File(default=[]),
):
    """Chat endpoint with inline file attachments (SSE streaming).

    Accepts multipart/form-data with query text and optional files.
    Processes files (PDF, images, DOCX, TXT, CSV, XLSX), then runs
    the full agent graph with file context injected into state.
    """
    from .file_processor import process_files, validate_upload
    from .settings import MAX_FILES_PER_REQUEST as MAX_FILES

    agent_graph = request.app.state.agent_graph
    thread_id = globalThreadId or str(uuid.uuid4())
    req_id = set_request_id(thread_id[:8])

    log.info("Chat with files request",
             query=query[:100], thread_id=thread_id,
             file_count=len(files) if files else 0)

    # Validate query
    if not query or len(query) > 30000:
        raise HTTPException(status_code=400, detail="Query must be 1-30000 characters")

    # Validate and save files to temp dir
    file_tuples: list[tuple[str, str, int]] = []
    temp_paths: list[str] = []

    if files:
        if len(files) > MAX_FILES:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum {MAX_FILES} files allowed",
            )

        for f in files:
            if not f.filename or f.filename == "":
                continue

            filename = secure_filename(f.filename)
            if not filename:
                continue

            # Save to temp file
            suffix = os.path.splitext(filename)[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                f.file.seek(0)
                content = await f.read()
                tmp.write(content)
                tmp_path = tmp.name
                temp_paths.append(tmp_path)

            err = validate_upload(filename, len(content))
            if err:
                log.warning("File rejected in chat", file=filename, reason=err)
                continue

            file_tuples.append((tmp_path, filename, len(content)))

    async def event_generator():
        yield f"data: {json.dumps({'type': 'thread_id', 'data': thread_id})}\n\n"
        start = time.perf_counter()

        # Process files if any
        file_context_dict = None
        if file_tuples:
            async def status_cb(msg: str):
                pass  # Status sent via SSE below

            yield f"data: {json.dumps({'type': 'file_processing', 'message': 'Processing uploaded files...'})}\n\n"

            try:
                fc = await process_files(file_tuples, thread_id)
                file_context_dict = fc.to_dict()
                # Note: process_files() already persists each file to thread_files table.
                # No need for save_file_context() here.

                # Send file processing summary
                yield f"data: {json.dumps({'type': 'file_processing', 'message': fc.summary, 'files': fc.file_names})}\n\n"
                log.info("Files processed for chat", summary=fc.summary)

            except Exception as e:
                log.error("File processing failed", error=str(e))
                yield f"data: {json.dumps({'type': 'file_processing', 'message': f'File processing failed: {e}', 'files': []})}\n\n"

        # Build state and run agent graph
        initial_state = _build_initial_state(
            query, thread_id, file_context=file_context_dict,
            preferred_language=preferred_language,
        )
        config = {"configurable": {"thread_id": thread_id}}

        step_count = 0
        final_response = ""
        agents_used = []
        total_tokens = 0
        all_source_metadata = []
        effective_query = query
        query_rewritten = False
        draft_continuation_data = None

        try:
          async with _async_timeout(300):  # 5 min hard timeout on streaming
            async for event in agent_graph.astream(
                initial_state,
                config=config,
                stream_mode=["updates", "custom"],
            ):
                mode, chunk = event

                if mode == "custom":
                    if isinstance(chunk, dict):
                        if chunk.get("type") == "token":
                            yield f"data: {json.dumps({'type': 'token', 'content': chunk['content']})}\n\n"
                        elif chunk.get("type") == "token_reset":
                            yield f"data: {json.dumps({'type': 'token_reset'})}\n\n"
                        elif chunk.get("type") == "drafting_progress":
                            yield "data: {}\n\n".format(json.dumps({
                                "type": "drafting_progress",
                                "section": chunk["section"],
                                "total": chunk["total"],
                                "title": chunk["title"],
                            }))
                        elif chunk.get("type") == "draft_incomplete":
                            yield "data: {}\n\n".format(json.dumps({
                                "type": "draft_incomplete",
                                "failed_sections": chunk["failed_sections"],
                                "total_sections": chunk["total_sections"],
                                "completed_sections": chunk["completed_sections"],
                            }))
                        elif chunk.get("type") == "queue_status":
                            yield f"data: {json.dumps({'type': 'status', 'agent': 'queue', 'message': chunk.get('message', 'Waiting for available slot...')})}\n\n"
                        elif chunk.get("type") == "progress":
                            yield f"data: {json.dumps(chunk)}\n\n"
                    continue

                for node_name, update in chunk.items():
                    step_count += 1
                    status_msg = _NODE_STATUS.get(node_name, f"Processing {node_name}...")
                    yield f"data: {json.dumps({'type': 'status', 'agent': node_name, 'message': status_msg})}\n\n"

                    if node_name == "memory" and "query" in update:
                        effective_query = update["query"]
                        query_rewritten = effective_query != query
                        history_msgs = update.get("chat_history", [])
                        yield f"data: {json.dumps({'type': 'context', 'query_rewritten': query_rewritten, 'effective_query': effective_query if query_rewritten else None, 'history_turns': len(history_msgs) // 2, 'has_summary': bool(update.get('summary_text'))})}\n\n"

                    if "final_response" in update and update["final_response"]:
                        final_response = update["final_response"]

                    if "tasks_planned" in update and update["tasks_planned"]:
                        agents_used = update["tasks_planned"]
                        yield f"data: {json.dumps({'type': 'agents_planned', 'agents': agents_used})}\n\n"

                    if "agent_results" in update:
                        for name, r in update["agent_results"].items():
                            if hasattr(r, "tokens_consumed"):
                                total_tokens += r.tokens_consumed or 0

                    if "source_metadata" in update and update["source_metadata"]:
                        all_source_metadata = update["source_metadata"]

                    if "draft_continuation" in update and update["draft_continuation"]:
                        draft_continuation_data = update["draft_continuation"]

        except TimeoutError:
            log.error("Chat stream timed out after 300s", thread_id=thread_id[:12])
            yield f"data: {json.dumps({'type': 'error', 'data': 'Request timed out. Please try a simpler query.'})}\n\n"
        except Exception as e:
            log.error("Chat stream error", error=str(e))
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)})}\n\n"

        # Send final response
        if final_response:
            yield f"data: {json.dumps({'type': 'response', 'content': final_response})}\n\n"

        if all_source_metadata:
            yield f"data: {json.dumps({'type': 'sources', 'data': all_source_metadata})}\n\n"

        # Follow-up suggestions
        if final_response:
            try:
                suggestions = await _generate_followup_suggestions(query, final_response, agents_used)
                if suggestions:
                    yield f"data: {json.dumps({'type': 'followup_suggestions', 'data': suggestions})}\n\n"
            except Exception as e:
                log.warning("Followup suggestions failed (chat)", error=str(e))

        elapsed_ms = (time.perf_counter() - start) * 1000
        log.info("Chat stream completed", steps=step_count, duration_ms=f"{elapsed_ms:.0f}")

        # Save chat history
        conversation_turn = 0
        if final_response:
            try:
                conversation_turn = await chat_store.save_turn(thread_id, effective_query, final_response)
            except Exception as e:
                log.error("Failed to save chat history (chat)", error=str(e))

        if draft_continuation_data:
            try:
                await chat_store.save_draft_continuation(thread_id, draft_continuation_data)
            except Exception as e:
                log.error("Failed to save draft continuation (chat)", error=str(e))

        # Done event
        yield f"data: {json.dumps({'type': 'done', 'agents_used': agents_used, 'total_tokens': total_tokens, 'thread_id': thread_id, 'conversation_turn': conversation_turn, 'query_rewritten': query_rewritten, 'effective_query': effective_query if query_rewritten else None, 'has_draft_continuation': draft_continuation_data is not None})}\n\n"

        # Cleanup temp files
        for tp in temp_paths:
            try:
                if os.path.exists(tp):
                    os.remove(tp)
            except Exception:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# Continue Draft (retry failed sections)
# ============================================================

class ContinueDraftRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    globalThreadId: str = Field(..., min_length=1)


@app.post("/pyapi/continue_draft", dependencies=[Depends(require_user_key)])
@limiter.limit(_get_limit_for_request)
async def continue_draft(data: ContinueDraftRequest, request: Request):
    """Continue an incomplete draft by regenerating failed sections.

    Loads the draft_continuation metadata from the previous turn in chat history,
    runs only the drafting agent to regenerate failed sections, then re-synthesizes.
    Returns an SSE stream.
    """
    thread_id = data.globalThreadId
    req_id = set_request_id(thread_id[:8])

    log.info("Continue draft request", thread_id=thread_id)

    # Load continuation metadata from chat_store
    continuation = await chat_store.load_draft_continuation(thread_id)
    if not continuation:
        raise HTTPException(
            status_code=404,
            detail="No incomplete draft found for this thread.",
        )

    query = continuation.get("query", "")
    log.info("Continuing draft",
             failed_sections=len(continuation.get("failed_indices", [])),
             query=query[:80])

    async def event_generator():
        yield f"data: {json.dumps({'type': 'thread_id', 'data': thread_id})}\n\n"
        yield f"data: {json.dumps({'type': 'status', 'agent': 'continue_draft', 'message': 'Retrying incomplete sections...'})}\n\n"
        yield f"data: {json.dumps({'type': 'agents_planned', 'agents': ['Drafting']})}\n\n"

        start = time.perf_counter()
        final_response = ""
        total_tokens = 0
        all_source_metadata = []
        draft_continuation_data = None

        try:
            from agents.drafting import continue_draft_node
            from core.state import AgentResult

            # Build a minimal state with continuation data
            state = _build_initial_state(
                query, thread_id,
                draft_continuation=continuation,
            )
            state["task"] = "Drafting"
            state["tasks_planned"] = ["Drafting"]

            # Run the continue_draft_node directly with streaming
            from langgraph.config import get_stream_writer

            # Use a simple streaming approach: run the node via a mini graph
            from langgraph.graph import StateGraph, END
            from core.state import LegalAgentState

            mini_graph = StateGraph(LegalAgentState)
            mini_graph.add_node("continue_draft", continue_draft_node)
            mini_graph.set_entry_point("continue_draft")
            mini_graph.add_edge("continue_draft", END)
            mini_compiled = mini_graph.compile()

            async for event in mini_compiled.astream(
                state, config={"configurable": {"thread_id": thread_id}},
                stream_mode=["updates", "custom"],
            ):
                mode, chunk = event

                if mode == "custom":
                    if isinstance(chunk, dict):
                        if chunk.get("type") == "token":
                            yield f"data: {json.dumps({'type': 'token', 'content': chunk['content']})}\n\n"
                        elif chunk.get("type") == "token_reset":
                            yield f"data: {json.dumps({'type': 'token_reset'})}\n\n"
                        elif chunk.get("type") == "drafting_progress":
                            yield "data: {}\n\n".format(json.dumps({
                                "type": "drafting_progress",
                                "section": chunk["section"],
                                "total": chunk["total"],
                                "title": chunk["title"],
                            }))
                        elif chunk.get("type") == "draft_incomplete":
                            yield "data: {}\n\n".format(json.dumps({
                                "type": "draft_incomplete",
                                "failed_sections": chunk["failed_sections"],
                                "total_sections": chunk["total_sections"],
                                "completed_sections": chunk["completed_sections"],
                            }))
                        elif chunk.get("type") == "queue_status":
                            yield f"data: {json.dumps({'type': 'status', 'agent': 'queue', 'message': chunk.get('message', 'Waiting for available slot...')})}\n\n"
                        elif chunk.get("type") == "progress":
                            yield f"data: {json.dumps(chunk)}\n\n"
                    continue

                for node_name, update in chunk.items():
                    yield f"data: {json.dumps({'type': 'status', 'agent': 'drafting', 'message': 'Generating legal draft...'})}\n\n"

                    if "agent_results" in update:
                        drafting_result = update["agent_results"].get("Drafting")
                        if drafting_result and drafting_result.content:
                            final_response = drafting_result.content
                            total_tokens += drafting_result.tokens_consumed or 0
                            if drafting_result.sources:
                                all_source_metadata = [
                                    {k: v for k, v in s.__dict__.items() if v}
                                    for s in drafting_result.sources
                                ]

                    if "draft_continuation" in update and update["draft_continuation"]:
                        draft_continuation_data = update["draft_continuation"]

        except Exception as e:
            log.error("Continue draft stream error", error=str(e))
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)})}\n\n"

        # Send final response
        if final_response:
            yield f"data: {json.dumps({'type': 'response', 'content': final_response})}\n\n"

        # Send sources
        if all_source_metadata:
            yield f"data: {json.dumps({'type': 'sources', 'data': all_source_metadata})}\n\n"

        # Save updated response to chat history
        conversation_turn = 0
        if final_response:
            try:
                conversation_turn = await chat_store.save_turn(
                    thread_id, f"[Continue draft] {query[:100]}", final_response
                )
            except Exception as e:
                log.error("Failed to save continued draft", error=str(e))

            # Save or clear continuation data
            if draft_continuation_data:
                await chat_store.save_draft_continuation(thread_id, draft_continuation_data)
            else:
                await chat_store.clear_draft_continuation(thread_id)

        elapsed_ms = (time.perf_counter() - start) * 1000
        log.info("Continue draft completed", duration_ms=f"{elapsed_ms:.0f}")

        done_event = {
            "type": "done",
            "agents_used": ["Drafting"],
            "total_tokens": total_tokens,
            "thread_id": thread_id,
            "conversation_turn": conversation_turn,
            "query_rewritten": False,
            "effective_query": None,
            "has_draft_continuation": draft_continuation_data is not None,
        }
        yield f"data: {json.dumps(done_event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Legacy /pyapi/mainqa removed — use /pyapi/chat (SSE streaming with file attachments)


# ============================================================
# Delete VectorDB Route
# ============================================================

@app.delete("/pyapi/delete_vectordb/{unique_string}", dependencies=[Depends(require_user_key)])
@limiter.limit(_get_limit_for_request)
async def delete_vectordb(unique_string: str, request: Request):
    """Delete a user's PDF document collection from ChromaDB."""
    persist_dir = _safe_persist_dir(unique_string)

    if not os.path.exists(persist_dir):
        raise HTTPException(status_code=404, detail=f"No vectordb found for {unique_string}")

    try:
        shutil.rmtree(persist_dir)

        # Also delete chat history
        chat_file = os.path.join(CHROMA_STORE_ROOT, "chat_histories", f"{unique_string}_chat.json")
        if os.path.exists(chat_file):
            os.remove(chat_file)

        log.info("VectorDB deleted", unique_string=unique_string)
        return {"message": f'VectorDB for "{unique_string}" successfully deleted.'}
    except Exception as e:
        log.error("Failed to delete vectordb",
                  unique_string=unique_string, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to delete: {e}")


# Legacy /pyapi/upload_async + /pyapi/job_status removed — file uploads handled inline by /pyapi/chat


# ============================================================
# Thread File Management
# ============================================================

@app.get("/pyapi/thread/{thread_id}/files", dependencies=[Depends(require_user_key)])
@limiter.limit(_get_limit_for_request)
async def list_thread_files(thread_id: str, request: Request):
    """List all files for a thread with their current status (including OCR status).

    Clients can poll this endpoint to check background OCR completion.
    """
    safe_tid = secure_filename(thread_id)
    if not safe_tid:
        raise HTTPException(status_code=400, detail="Invalid thread_id")
    records = await chat_store.load_thread_files(safe_tid)
    return {"thread_id": safe_tid, "file_count": len(records), "files": records}


@app.delete("/pyapi/thread/{thread_id}/files", dependencies=[Depends(require_user_key)])
@limiter.limit(_get_limit_for_request)
async def delete_thread_files(thread_id: str, request: Request):
    """Delete all files for a thread: local storage, Gemini URIs, ChromaDB, SQLite records."""
    from core.gemini_files import delete_gemini_file

    safe_tid = secure_filename(thread_id)
    if not safe_tid:
        raise HTTPException(status_code=400, detail="Invalid thread_id")

    records = await chat_store.delete_thread_files(safe_tid)
    if not records:
        raise HTTPException(status_code=404, detail=f"No files found for thread {safe_tid}")

    deleted = {"gemini_files": 0, "chromadb_collections": 0, "local_dir_removed": False, "db_records": len(records)}

    # Delete Gemini files
    for rec in records:
        gname = rec.get("gemini_name", "")
        if gname:
            try:
                await asyncio.to_thread(delete_gemini_file, gname)
                deleted["gemini_files"] += 1
            except Exception as e:
                log.warning("Gemini file deletion failed", name=gname, error=str(e))

    # Delete ChromaDB collections
    for rec in records:
        coll = rec.get("chromadb_collection", "")
        if coll:
            persist_dir = os.path.join(CHROMA_STORE_ROOT, coll)
            if os.path.exists(persist_dir):
                try:
                    shutil.rmtree(persist_dir)
                    deleted["chromadb_collections"] += 1
                except Exception as e:
                    log.warning("ChromaDB deletion failed", collection=coll, error=str(e))

    # Delete local upload directory
    upload_dir = os.path.join(UPLOADS_ROOT, safe_tid)
    if os.path.exists(upload_dir):
        try:
            shutil.rmtree(upload_dir)
            deleted["local_dir_removed"] = True
        except Exception as e:
            log.warning("Upload dir deletion failed", dir=upload_dir, error=str(e))

    log.info("Thread files deleted", thread_id=safe_tid[:12], **deleted)
    return {"message": f"Deleted files for thread {safe_tid}", "details": deleted}


# ============================================================
# Health Check
# ============================================================

@app.api_route("/pyapi/health", methods=["GET", "HEAD"])
async def health(request: Request):
    """Deep health check — verifies ES, API keys, memory, disk, SQLite.

    Returns HTTP 200 for healthy/degraded, HTTP 503 for unhealthy.
    Designed to work with UptimeRobot: a 503 triggers an alert.
    """
    from datetime import datetime, timezone
    from .settings import ELASTICSEARCH_URL, CHAT_HISTORY_DB_PATH
    from .clients import get_es_client

    checks: dict[str, dict] = {}
    overall = "healthy"  # healthy | degraded | unhealthy

    # --- 1. Elasticsearch ---
    try:
        t0 = time.time()
        es = get_es_client(max_retries=1, timeout=3)
        reachable = await asyncio.wait_for(
            asyncio.to_thread(es.ping), timeout=3.0
        )
        latency_ms = round((time.time() - t0) * 1000)
        if reachable:
            checks["elasticsearch"] = {"status": "ok", "latency_ms": latency_ms}
        else:
            checks["elasticsearch"] = {"status": "error", "detail": "ping returned False"}
            overall = "unhealthy"
    except Exception as e:
        checks["elasticsearch"] = {"status": "error", "detail": str(e)[:120]}
        overall = "unhealthy"

    # --- 2. OpenAI API key ---
    from .settings import OPENAI_API_KEY
    if OPENAI_API_KEY:
        checks["openai_key"] = {"status": "ok"}
    else:
        checks["openai_key"] = {"status": "error", "detail": "OPENAI_API_KEY not set"}
        overall = "unhealthy"

    # --- 3. Google API key ---
    from .settings import GOOGLE_API_KEY
    if GOOGLE_API_KEY:
        checks["google_key"] = {"status": "ok"}
    else:
        checks["google_key"] = {"status": "error", "detail": "GOOGLE_API_KEY not set"}
        overall = "unhealthy"

    # --- 4. Memory (RAM) ---
    try:
        vm = psutil.virtual_memory()
        used_pct = vm.percent
        available_gb = round(vm.available / (1024 ** 3), 1)
        if used_pct >= 95:
            mem_status = "critical"
            if overall == "healthy":
                overall = "degraded"
        elif used_pct >= 85:
            mem_status = "warn"
            if overall == "healthy":
                overall = "degraded"
        else:
            mem_status = "ok"
        checks["memory"] = {
            "status": mem_status,
            "used_pct": round(used_pct, 1),
            "available_gb": available_gb,
        }
    except Exception as e:
        checks["memory"] = {"status": "unknown", "detail": str(e)[:80]}

    # --- 5. Disk space ---
    try:
        disk = shutil.disk_usage("/")
        used_pct = round(disk.used / disk.total * 100, 1)
        free_gb = round(disk.free / (1024 ** 3), 1)
        if used_pct >= 95:
            disk_status = "critical"
            if overall == "healthy":
                overall = "degraded"
        elif used_pct >= 80:
            disk_status = "warn"
            if overall == "healthy":
                overall = "degraded"
        else:
            disk_status = "ok"
        checks["disk"] = {
            "status": disk_status,
            "used_pct": used_pct,
            "free_gb": free_gb,
        }
    except Exception as e:
        checks["disk"] = {"status": "unknown", "detail": str(e)[:80]}

    # --- 6. Chat store (PostgreSQL or SQLite) ---
    try:
        from .settings import POSTGRES_URL as _pg_url
        t0 = time.time()
        if _pg_url:
            # Probe via the same sync pool used by _PostgresChatHistoryStore
            def _pg_probe():
                from .chat_store import chat_store as _cs
                pool = _cs._get_pool()
                with pool.connection() as conn:
                    conn.execute("SELECT 1")
            await asyncio.wait_for(asyncio.to_thread(_pg_probe), timeout=3.0)
            latency_ms = round((time.time() - t0) * 1000)
            checks["chat_store"] = {"status": "ok", "backend": "postgresql", "latency_ms": latency_ms}
        else:
            import sqlite3
            db_path = CHAT_HISTORY_DB_PATH
            def _sqlite_probe():
                conn = sqlite3.connect(db_path, timeout=2.0)
                conn.execute("SELECT 1")
                conn.close()
            await asyncio.wait_for(asyncio.to_thread(_sqlite_probe), timeout=3.0)
            latency_ms = round((time.time() - t0) * 1000)
            checks["chat_store"] = {"status": "ok", "backend": "sqlite", "latency_ms": latency_ms}
    except Exception as e:
        checks["chat_store"] = {"status": "error", "detail": str(e)[:120]}
        if overall == "healthy":
            overall = "degraded"

    # --- 7. Checkpointer (PostgreSQL or in-memory) ---
    try:
        graph = request.app.state.agent_graph
        cp = graph.checkpointer
        cp_type = type(cp).__name__
        if "Postgres" in cp_type:
            # Quick pool probe
            t0 = time.time()
            async with _pg_pool.connection() as conn:
                await conn.execute("SELECT 1")
            latency_ms = round((time.time() - t0) * 1000)
            checks["checkpointer"] = {"status": "ok", "type": "postgresql", "latency_ms": latency_ms}
        else:
            checks["checkpointer"] = {"status": "warn", "type": "in_memory",
                                       "detail": "state lost on restart — set POSTGRES_URL"}
            if overall == "healthy":
                overall = "degraded"
    except Exception as e:
        checks["checkpointer"] = {"status": "unknown", "detail": str(e)[:80]}

    # --- 8. Response cache ---
    checks["response_cache"] = response_cache.stats

    uptime_seconds = int(time.time() - _APP_START_TIME)
    body = {
        "status": overall,
        "version": "2.0.0",
        "uptime_seconds": uptime_seconds,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }

    status_code = 503 if overall == "unhealthy" else 200
    from fastapi.responses import JSONResponse
    return JSONResponse(content=body, status_code=status_code)


# ============================================================
# Prometheus Metrics Endpoint
# ============================================================

@app.get("/pyapi/metrics", dependencies=[Depends(require_admin_key)])
async def prometheus_metrics():
    """Expose Prometheus metrics in text format.

    Scraped by Prometheus every 15 seconds.
    URL: http://host:5000/pyapi/metrics
    """
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from fastapi.responses import Response
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


# ------------------------------------------------------------------
# Admin: Fallback Log (ES backfill pipeline)
# ------------------------------------------------------------------

@app.get("/pyapi/admin/fallback_logs", dependencies=[Depends(require_admin_key)])
async def admin_fallback_logs(
    agent: Optional[str] = None,
    backfilled: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
):
    """List web-search fallback events for ES backfill review.

    Query params:
      agent      — filter by agent name (Judgment, SCI_Judgment, Legislation, ...)
      backfilled — 0 = pending only, 1 = done only, omit = all
      limit      — max rows (default 50, max 200)
      offset     — pagination offset
    """
    try:
        result = await chat_store.get_fallback_logs(
            agent=agent,
            backfilled=backfilled,
            limit=min(limit, 200),
            offset=offset,
        )
        return result
    except Exception as e:
        log.error("Failed to fetch fallback logs", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch fallback logs")


@app.get("/pyapi/admin/fallback_stats", dependencies=[Depends(require_admin_key)])
async def admin_fallback_stats():
    """Aggregate stats: totals by agent, date, top repeated queries."""
    try:
        return await chat_store.get_fallback_stats()
    except Exception as e:
        log.error("Failed to fetch fallback stats", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch fallback stats")


@app.get("/pyapi/admin/usage_stats", dependencies=[Depends(require_admin_key)])
async def admin_usage_stats(days: int = 7):
    """Per-request usage stats: tokens, cost, latency, agent distribution.

    Args:
        days: Number of days to look back (default: 7)
    """
    try:
        return await chat_store.get_usage_stats(days=days)
    except Exception as e:
        log.error("Failed to fetch usage stats", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch usage stats")


@app.get("/pyapi/admin/quality_stats", dependencies=[Depends(require_admin_key)])
async def admin_quality_stats(days: int = 7):
    """L4 quality scores: faithfulness, relevance, completeness per agent.

    Args:
        days: Number of days to look back (default: 7)
    """
    try:
        return await chat_store.get_quality_stats(days=days)
    except Exception as e:
        log.error("Failed to fetch quality stats", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch quality stats")


@app.get("/pyapi/threads", dependencies=[Depends(require_user_key)])
async def list_threads(limit: int = 50, offset: int = 0):
    """List recent chat sessions for the sidebar history."""
    try:
        threads = await chat_store.list_threads(limit=min(limit, 100), offset=offset)
        return {"status": "ok", "threads": threads}
    except Exception as e:
        log.error("Failed to list threads", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to list threads")


@app.get("/pyapi/threads/{thread_id}/messages", dependencies=[Depends(require_user_key)])
async def get_thread_messages(thread_id: str, limit: int = 50):
    """Load all messages for a specific thread (for session restore)."""
    try:
        messages = await chat_store.load_thread_messages(thread_id, limit=min(limit, 100))
        if not messages:
            raise HTTPException(status_code=404, detail="Thread not found or empty")
        return {"status": "ok", "thread_id": thread_id, "messages": messages}
    except HTTPException:
        raise
    except Exception as e:
        log.error("Failed to load thread messages", error=str(e), thread_id=thread_id[:12])
        raise HTTPException(status_code=500, detail="Failed to load thread messages")


# --- Export Endpoint ---

class ExportRequest(BaseModel):
    thread_id: Optional[str] = None
    turn_number: Optional[int] = None
    raw_text: Optional[str] = None
    title: Optional[str] = None
    format: str = Field(default="docx", pattern=r"^(docx|pdf)$")


@app.post("/pyapi/export", dependencies=[Depends(require_user_key)])
@limiter.limit(_get_limit_for_request)
async def export_document_endpoint(data: ExportRequest, request: Request):
    """Export an AI response as a downloadable Word (.docx) or PDF file.

    Provide either:
    - thread_id + turn_number: loads saved response from chat history
    - raw_text: exports the provided markdown text directly
    """
    from .export import export_document
    from .chat_store import chat_store

    try:
        md_text = None
        title = data.title
        sources = None
        created_at = None

        if data.raw_text:
            md_text = data.raw_text
            title = title or "Lawttorney Export"
        elif data.thread_id and data.turn_number:
            msg = await chat_store.load_single_turn(data.thread_id, data.turn_number)
            if not msg:
                return _error_response("not_found", "Message not found", 404)
            md_text = msg["ai_response"]
            title = title or msg.get("user_query", "Lawttorney Export")[:100]
            created_at = msg.get("created_at")
        else:
            return _error_response(
                "validation_error",
                "Provide either raw_text or thread_id + turn_number",
                400,
            )

        if not md_text or not md_text.strip():
            return _error_response("validation_error", "No content to export", 400)

        buf, filename, media_type = export_document(
            title=title,
            md_text=md_text,
            fmt=data.format,
            sources=sources,
            created_at=created_at,
        )

        return StreamingResponse(
            buf,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as e:
        log.error("Export failed", error=str(e), exc_info=True)
        return _error_response("export_error", f"Export failed: {e}", 500)


# --- Save Turn Endpoint (for persisting compliance reports, revised drafts) ---

class SaveTurnRequest(BaseModel):
    thread_id: str
    user_query: str
    ai_response: str


@app.post("/pyapi/save-turn", dependencies=[Depends(require_user_key)])
@limiter.limit(_get_limit_for_request)
async def save_turn_endpoint(data: SaveTurnRequest, request: Request):
    """Save a special turn to chat history (compliance reports, revised drafts)."""
    from .chat_store import chat_store

    try:
        turn = await chat_store.save_turn(data.thread_id, data.user_query, data.ai_response)
        return JSONResponse({"turn_number": turn})
    except Exception as e:
        log.error("Save turn failed", error=str(e), exc_info=True)
        return _error_response("save_error", f"Failed to save: {e}", 500)


# --- Fix Draft Endpoint ---

class FixDraftRequest(BaseModel):
    original_draft: str
    compliance_report: str
    format: str = Field(default="md", pattern=r"^(docx|pdf|md)$")


@app.post("/pyapi/fix-draft", dependencies=[Depends(require_user_key)])
@limiter.limit(_get_limit_for_request)
async def fix_draft_endpoint(data: FixDraftRequest, request: Request):
    """Revise a legal draft based on compliance report findings.

    Takes the original draft + compliance report, fixes all critical issues,
    addresses warnings, and returns a revised draft with [REVISED] markers
    and a revision summary table.
    """
    from .fix_draft import fix_draft
    from .export import export_document

    try:
        if not data.original_draft.strip():
            return _error_response("validation_error", "No original draft provided", 400)
        if not data.compliance_report.strip():
            return _error_response("validation_error", "No compliance report provided", 400)

        revised = await fix_draft(data.original_draft, data.compliance_report)

        if data.format == "md":
            buf = io.BytesIO(revised.encode("utf-8"))
            return StreamingResponse(
                buf,
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="revised_draft.md"'},
            )

        buf, filename, media_type = export_document(
            title="Revised Draft (Compliance Issues Fixed)",
            md_text=revised,
            fmt=data.format,
        )

        return StreamingResponse(
            buf,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as e:
        log.error("Fix draft failed", error=str(e), exc_info=True)
        return _error_response("fix_draft_error", f"Draft revision failed: {e}", 500)


# --- Compliance Check Endpoint ---

class ComplianceRequest(BaseModel):
    thread_id: Optional[str] = None
    turn_number: Optional[int] = None
    text: Optional[str] = None
    doc_type: Optional[str] = None
    format: str = Field(default="md", pattern=r"^(docx|pdf|md)$")


@app.post("/pyapi/compliance-check", dependencies=[Depends(require_user_key)])
@limiter.limit(_get_limit_for_request)
async def compliance_check_endpoint(data: ComplianceRequest, request: Request):
    """Run compliance check on a legal document.

    Flags missing clauses, procedural errors, incorrect statutes,
    jurisdictional issues, and format deficiencies.
    Returns a structured compliance report.
    """
    from .compliance import check_compliance
    from .export import export_document
    from .chat_store import chat_store

    try:
        text = data.text or ""

        if data.thread_id and data.turn_number and not text:
            msg = await chat_store.load_single_turn(data.thread_id, data.turn_number)
            if not msg:
                return _error_response("not_found", "Message not found", 404)
            text = msg["ai_response"]

        if not text.strip():
            return _error_response("validation_error", "No text to check", 400)

        report = await check_compliance(text, data.doc_type or "")

        if data.format == "md":
            buf = io.BytesIO(report.encode("utf-8"))
            return StreamingResponse(
                buf,
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="compliance_report.md"'},
            )

        buf, filename, media_type = export_document(
            title="Compliance Check Report",
            md_text=report,
            fmt=data.format,
        )

        return StreamingResponse(
            buf,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as e:
        log.error("Compliance check failed", error=str(e), exc_info=True)
        return _error_response("compliance_error", f"Compliance check failed: {e}", 500)


# --- Statute Referencing Endpoint ---

class StatuteRefRequest(BaseModel):
    thread_id: Optional[str] = None
    turn_number: Optional[int] = None
    text: Optional[str] = None
    format: str = Field(default="md", pattern=r"^(docx|pdf|md)$")


@app.post("/pyapi/statute-refs", dependencies=[Depends(require_user_key)])
@limiter.limit(_get_limit_for_request)
async def add_statute_refs_endpoint(data: StatuteRefRequest, request: Request):
    """Add automatic Indian statute references to legal text.

    Scans each clause and inserts applicable Act name + Section number.
    Returns enhanced text as .md, .docx, or .pdf.
    """
    from .statute_refs import add_statute_references
    from .export import export_document
    from .chat_store import chat_store

    try:
        text = data.text or ""

        if data.thread_id and data.turn_number and not text:
            msg = await chat_store.load_single_turn(data.thread_id, data.turn_number)
            if not msg:
                return _error_response("not_found", "Message not found", 404)
            text = msg["ai_response"]

        if not text.strip():
            return _error_response("validation_error", "No text to enhance", 400)

        enhanced = await add_statute_references(text)

        if data.format == "md":
            buf = io.BytesIO(enhanced.encode("utf-8"))
            return StreamingResponse(
                buf,
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="enhanced_with_statutes.md"'},
            )

        buf, filename, media_type = export_document(
            title="Legal Document (Statute References Added)",
            md_text=enhanced,
            fmt=data.format,
        )

        return StreamingResponse(
            buf,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as e:
        log.error("Statute referencing failed", error=str(e), exc_info=True)
        return _error_response("statute_ref_error", f"Statute referencing failed: {e}", 500)


# --- Table of Authorities Endpoint ---

class TOARequest(BaseModel):
    thread_id: Optional[str] = None
    turn_number: Optional[int] = None
    query: Optional[str] = None
    response_text: Optional[str] = None
    sources: Optional[list[dict]] = None
    format: str = Field(default="docx", pattern=r"^(docx|pdf|md)$")


@app.post("/pyapi/toa", dependencies=[Depends(require_user_key)])
@limiter.limit(_get_limit_for_request)
async def generate_toa_endpoint(data: TOARequest, request: Request):
    """Generate a Table of Authorities from an AI response.

    Extracts all legal citations (cases, statutes, constitutional provisions)
    and returns a formatted document.
    """
    from .toa import generate_toa
    from .export import export_document
    from .chat_store import chat_store

    try:
        query = data.query or ""
        response_text = data.response_text or ""
        sources = data.sources or []

        if data.thread_id and data.turn_number and not response_text:
            msg = await chat_store.load_single_turn(data.thread_id, data.turn_number)
            if not msg:
                return _error_response("not_found", "Message not found", 404)
            response_text = msg["ai_response"]
            query = query or msg.get("user_query", "")

        if not response_text.strip():
            return _error_response("validation_error", "No content for TOA extraction", 400)

        toa_md = await generate_toa(
            query=query,
            response_text=response_text,
            sources=sources,
        )

        if data.format == "md":
            buf = io.BytesIO(toa_md.encode("utf-8"))
            return StreamingResponse(
                buf,
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="table_of_authorities.md"'},
            )

        buf, filename, media_type = export_document(
            title="Table of Authorities",
            md_text=toa_md,
            fmt=data.format,
            sources=sources,
        )

        return StreamingResponse(
            buf,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as e:
        log.error("TOA generation failed", error=str(e), exc_info=True)
        return _error_response("toa_error", f"TOA generation failed: {e}", 500)


# --- Research Memo Endpoint ---

class MemoRequest(BaseModel):
    thread_id: Optional[str] = None
    turn_number: Optional[int] = None
    query: Optional[str] = None
    response_text: Optional[str] = None
    sources: Optional[list[dict]] = None
    agents_used: Optional[list[str]] = None
    format: str = Field(default="docx", pattern=r"^(docx|pdf|md)$")


@app.post("/pyapi/memo", dependencies=[Depends(require_user_key)])
@limiter.limit(_get_limit_for_request)
async def generate_research_memo(data: MemoRequest, request: Request):
    """Generate a structured legal research memorandum from an AI response.

    Restructures the AI response into a formal legal memo with:
    Issue, Brief Answer, Discussion, Sources, Conclusion.

    Accepts either thread_id+turn_number (load from history) or direct text.
    Returns downloadable .docx, .pdf, or .md file.
    """
    from .memo import generate_memo
    from .export import export_document
    from .chat_store import chat_store

    try:
        query = data.query or ""
        response_text = data.response_text or ""
        sources = data.sources or []
        agents_used = data.agents_used or []

        # Load from history if thread_id provided
        if data.thread_id and data.turn_number and not response_text:
            msg = await chat_store.load_single_turn(data.thread_id, data.turn_number)
            if not msg:
                return _error_response("not_found", "Message not found", 404)
            response_text = msg["ai_response"]
            query = query or msg.get("user_query", "")

        if not response_text.strip():
            return _error_response("validation_error", "No response content for memo", 400)

        # Generate the memo markdown using LLM
        memo_md = await generate_memo(
            query=query,
            response_text=response_text,
            sources=sources,
            agents_used=agents_used,
        )

        if data.format == "md":
            # Return raw markdown
            buf = io.BytesIO(memo_md.encode("utf-8"))
            return StreamingResponse(
                buf,
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="research_memo.md"'},
            )

        # Export as docx/pdf
        buf, filename, media_type = export_document(
            title="Legal Research Memorandum",
            md_text=memo_md,
            fmt=data.format,
            sources=sources,
        )

        return StreamingResponse(
            buf,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as e:
        log.error("Memo generation failed", error=str(e), exc_info=True)
        return _error_response("memo_error", f"Memo generation failed: {e}", 500)


@app.post("/pyapi/feedback", dependencies=[Depends(require_user_key)])
async def submit_feedback(data: FeedbackRequest, request: Request):
    """Submit thumbs-up/down feedback for a specific response."""
    try:
        await chat_store.save_feedback(
            data.thread_id,
            data.turn_number,
            data.rating,
            data.comment or "",
        )
        return {"status": "ok", "message": "Feedback recorded"}
    except Exception as e:
        log.error("Failed to save feedback", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to save feedback")


# --- Entrypoint ---

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
