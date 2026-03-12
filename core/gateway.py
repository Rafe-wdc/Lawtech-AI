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
import json
import os

# asyncio.timeout() is Python 3.11+; fall back to async_timeout on 3.10
try:
    from asyncio import timeout as _async_timeout
except ImportError:
    from async_timeout import timeout as _async_timeout
import shutil
import tempfile
import threading
import time
import uuid
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Request, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel, Field, ConfigDict
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from werkzeug.utils import secure_filename

from .settings import HOST, PORT, RATE_LIMIT_PER_MINUTE, CHROMA_STORE_ROOT
from .graph import compile_graph
from .logger import get_logger, set_request_id, log_time
from .chat_store import chat_store

log = get_logger("Gateway")

# --- Collection-level locks for concurrent upload protection ---
_collection_locks: dict[str, threading.Lock] = {}
_collection_locks_guard = threading.Lock()

def _get_collection_lock(unique_string: str) -> threading.Lock:
    """Get or create a per-collection lock to prevent concurrent modification."""
    with _collection_locks_guard:
        if unique_string not in _collection_locks:
            _collection_locks[unique_string] = threading.Lock()
        return _collection_locks[unique_string]

# --- Rate Limiter ---
limiter = Limiter(key_func=get_remote_address)

# --- App ---
app = FastAPI(title="Legal AI API v2", version="2.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Compile the agent graph (done once at startup)
log.info("Compiling agent graph at startup")
agent_graph = compile_graph()
log.info("Agent graph compiled successfully")

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


# --- Request / Response Schemas ---

_VALID_LANGUAGES = {
    "en", "hi", "bn", "te", "mr", "ta", "kn", "ml", "gu", "pa", "ur", "or", "as", "sa",
}


class SearchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    # alias keeps backward-compat with existing clients sending "Promptquery"
    prompt_query: str = Field(..., alias="Promptquery", min_length=1, max_length=5000)
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
    question: str = Field(..., min_length=1, max_length=5000)


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

@app.post("/pyapi/search", response_model=SearchResponse)
@limiter.limit(f"{RATE_LIMIT_PER_MINUTE}/minute")
async def search(data: SearchRequest, request: Request):
    """Main legal Q&A endpoint (batch mode).

    Invokes the full multi-agent graph and returns the complete response:
    guardrail → memory → orchestrator → [domain agents] → synthesize → guardrail
    """
    thread_id = data.globalThreadId or str(uuid.uuid4())
    req_id = set_request_id(thread_id[:8])
    initial_state = _build_initial_state(
        data.prompt_query, thread_id, preferred_language=data.preferred_language
    )
    config = {"configurable": {"thread_id": thread_id}}

    log.info("Search request received",
             query=data.prompt_query[:100], thread_id=thread_id,
             query_len=len(data.prompt_query))

    try:
        with log_time(log, "Full graph execution"):
            final_state = await asyncio.wait_for(
                agent_graph.ainvoke(initial_state, config=config),
                timeout=300,  # 5 minute hard timeout
            )
    except asyncio.TimeoutError:
        log.error("Agent graph execution timed out after 300s", query=data.prompt_query[:100])
        raise HTTPException(status_code=504, detail="Request timed out. Please try a simpler query.")
    except Exception as e:
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


@app.post("/pyapi/search/stream")
@limiter.limit(f"{RATE_LIMIT_PER_MINUTE}/minute")
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
    thread_id = data.globalThreadId or str(uuid.uuid4())
    req_id = set_request_id(thread_id[:8])
    initial_state = _build_initial_state(
        data.prompt_query, thread_id, preferred_language=data.preferred_language
    )
    config = {"configurable": {"thread_id": thread_id}}

    log.info("Stream request received",
             query=data.prompt_query[:100], thread_id=thread_id)

    async def event_generator():
        """Generate SSE events from the agent graph stream."""
        # Send thread_id as first event
        yield f"data: {json.dumps({'type': 'thread_id', 'data': thread_id})}\n\n"
        start = time.perf_counter()
        step_count = 0
        final_response = ""
        agents_used = []
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
                        agents_used = update["tasks_planned"]
                        yield f"data: {json.dumps({'type': 'agents_planned', 'agents': agents_used})}\n\n"

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

@app.post("/pyapi/chat")
@limiter.limit(f"{RATE_LIMIT_PER_MINUTE}/minute")
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

    thread_id = globalThreadId or str(uuid.uuid4())
    req_id = set_request_id(thread_id[:8])

    log.info("Chat with files request",
             query=query[:100], thread_id=thread_id,
             file_count=len(files) if files else 0)

    # Validate query
    if not query or len(query) > 5000:
        raise HTTPException(status_code=400, detail="Query must be 1-5000 characters")

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


@app.post("/pyapi/continue_draft")
@limiter.limit(f"{RATE_LIMIT_PER_MINUTE}/minute")
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


# ============================================================
# PDF Upload / Process / Chat Routes (direct, outside LangGraph)
# ============================================================

@app.post("/pyapi/mainqa")
@limiter.limit(f"{RATE_LIMIT_PER_MINUTE}/minute")
async def mainqa(
    request: Request,
    usecase: str = Form(...),
    uniqueString: str = Form(...),
    file: Optional[List[UploadFile]] = File(None),
    question: Optional[str] = Form(None),
):
    """Unified PDF endpoint matching v1 API contract.

    usecase='upload': Upload and process PDF files into ChromaDB.
    usecase='qa': Query the user's uploaded documents.
    """
    import fitz
    from langchain_core.documents import Document
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    from tools.shared.document_tools import extract_text_vision
    from tools.shared.guardrail_tools import validate_input
    from tools.shared.storage_tools import load_pdf_chat_history, save_pdf_chat_history
    from tools.shared.memory_tools import rewrite_query
    from core.clients import get_qa_embeddings, get_gemini_pro

    req_id = set_request_id()

    if usecase == "upload":
        start_time = time.time()
        log.info("PDF upload started", unique_string=uniqueString,
                 file_count=len(file) if file else 0)

        if not file or all(f.filename == "" for f in file):
            log.warning("No files selected for upload")
            return {"status": False, "error": "No selected files"}

        documents = []
        processed_filenames = []

        for filex in file:
            if not filex or not filex.filename.lower().endswith(".pdf"):
                continue

            filename = secure_filename(filex.filename)
            if not filename:
                continue
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    filex.file.seek(0)
                    content_bytes = await filex.read()
                    # Validate file size (max 50MB)
                    if len(content_bytes) > 50 * 1024 * 1024:
                        log.warning("PDF too large in mainqa",
                                    file=filename,
                                    size_mb=len(content_bytes) / (1024 * 1024))
                        continue
                    tmp.write(content_bytes)
                    tmp_path = tmp.name

                pdf_doc = fitz.open(tmp_path)
                num_pages = len(pdf_doc)
                text_threshold = num_pages * 700

                extracted_text = ""
                for page in pdf_doc:
                    extracted_text += page.get_text("text") + "\n"
                pdf_doc.close()

                log.debug("PDF text extracted",
                          filename=filename, pages=num_pages,
                          text_len=len(extracted_text.strip()),
                          threshold=text_threshold)

                # Fallback to vision OCR if text is sparse
                if len(extracted_text.strip()) < text_threshold:
                    log.info("Low text density, using Vision OCR fallback",
                             filename=filename,
                             extracted=len(extracted_text.strip()),
                             threshold=text_threshold)
                    vision_result = extract_text_vision.invoke(
                        {"file_path": tmp_path, "start_page": 0, "end_page": -1}
                    )
                    pdf_text = vision_result.get("text", "")
                else:
                    pdf_text = extracted_text

                if pdf_text.strip():
                    documents.append(
                        Document(page_content=pdf_text, metadata={"source": filename})
                    )
                    processed_filenames.append(filename)

            except Exception as e:
                log.error("Error processing PDF file",
                          filename=filename, error=str(e))
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)

        if not documents:
            log.warning("No text extracted from any PDF")
            return {"status": False, "error": "No text extracted from PDFs"}

        # Chunk documents
        text_splitter = RecursiveCharacterTextSplitter(
            separators=[""], chunk_size=15000, chunk_overlap=200
        )
        texts = text_splitter.split_documents(documents)
        log.info("Documents chunked", chunks=len(texts), files=len(processed_filenames))

        # Store in ChromaDB (locked per-collection to prevent concurrent races)
        collection_name = f"collection_{uniqueString}"
        persist_dir = _safe_persist_dir(uniqueString)
        os.makedirs(persist_dir, exist_ok=True)

        from langchain_community.vectorstores import Chroma

        embeddings = get_qa_embeddings()
        col_lock = _get_collection_lock(uniqueString)
        with col_lock:
            collection_exists = os.path.exists(os.path.join(persist_dir, "chroma.sqlite3"))

            existing_filenames = []
            if collection_exists:
                vectordb = Chroma(
                    embedding_function=embeddings,
                    collection_name=collection_name,
                    persist_directory=persist_dir,
                )
                try:
                    metadata = vectordb._collection.metadata
                    if metadata:
                        fnames_str = metadata.get("filenames", "")
                        existing_filenames = [f for f in fnames_str.split(",") if f]
                except Exception:
                    pass
                # Skip files already in the collection to prevent duplicate chunks
                existing_set = set(existing_filenames)
                new_texts = [doc for doc in texts if doc.metadata.get("source") not in existing_set]
                if new_texts:
                    vectordb.add_documents(new_texts)
                    log.info("Added new chunks to existing collection",
                             new_chunks=len(new_texts), skipped=len(texts) - len(new_texts))
                else:
                    log.info("All files already in collection, skipping re-upload",
                             unique_string=uniqueString, existing=list(existing_set))
            else:
                vectordb = Chroma.from_documents(
                    documents=texts,
                    embedding=embeddings,
                    collection_name=collection_name,
                    persist_directory=persist_dir,
                )

            all_filenames = list(set(existing_filenames + processed_filenames))
            vectordb._collection.modify(
                metadata={
                    "filenames": ",".join(all_filenames),
                    "upload_date": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "file_count": str(len(all_filenames)),
                }
            )

        total_time = round(time.time() - start_time, 2)
        log.info("PDF upload completed",
                 chunks=len(texts), filenames=all_filenames,
                 upload_time_sec=total_time, collection=collection_name)
        return {
            "status": True,
            "chunks": len(texts),
            "filenames": all_filenames,
            "upload_time_sec": total_time,
        }

    elif usecase == "qa":
        if not question:
            return {"error": "question is required"}

        log.info("PDF QA request", unique_string=uniqueString,
                 question=question[:100])

        # Input guardrails
        guardrail_result = validate_input.invoke({"text": question})
        if not guardrail_result.get("valid", True):
            log.warning("PDF QA query blocked by guardrail",
                        reason=guardrail_result.get("reason"))
            return {"answer": guardrail_result.get("reason", "Query blocked."), "filenames": [], "upload_date": "N/A"}

        # Load chat history
        history_result = load_pdf_chat_history.invoke({"unique_string": uniqueString})
        chat_history = history_result.get("messages", [])
        all_chats = history_result.get("all_chats", [])

        # Rewrite query with context
        rewrite_result = rewrite_query.invoke(
            {"query": question, "chat_history_text": str(chat_history[-5:])}
        )
        search_query = rewrite_result.get("rewritten_query", question)
        if search_query != question:
            log.debug("PDF QA query rewritten",
                      original=question[:60], rewritten=search_query[:60])

        # Load collection
        collection_name = f"collection_{uniqueString}"
        persist_dir = _safe_persist_dir(uniqueString)

        from langchain_community.vectorstores import Chroma
        from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

        embeddings = get_qa_embeddings()
        vectordb = Chroma(
            embedding_function=embeddings,
            collection_name=collection_name,
            persist_directory=persist_dir,
        )

        # Get filenames from metadata
        filenames = []
        upload_date = "Unknown"
        try:
            metadata = vectordb._collection.metadata
            filenames = [f for f in metadata.get("filenames", "").split(",") if f]
            upload_date = metadata.get("upload_date", "Unknown")
        except Exception:
            pass

        # Retrieve and generate answer
        with log_time(log, "PDF QA retrieval + generation"):
            retriever = vectordb.as_retriever(search_kwargs={"k": 10})
            docs = retriever.invoke(search_query)
            log.debug("PDF QA retrieval done", docs_found=len(docs))
            context = "\n\n".join(doc.page_content for doc in docs)

            llm = get_gemini_pro(temperature=0.3)
            qa_prompt = ChatPromptTemplate.from_messages([
                ("system", (
                    "You are a legal AI Assistant. Use the following context to answer the question.\n\n"
                    "IMPORTANT: If the question or context refers to old provisions such as IPC, CrPC, or IEA, "
                    "mention both old and new provisions side by side:\n"
                    "- IPC → Bharatiya Nyaya Sanhita (BNS)\n"
                    "- CrPC → Bharatiya Nagrik Suraksha Sanhita (BNSS)\n"
                    "- IEA → Bharatiya Sakshya Adhiniyam (BSA)\n\n"
                    "Format: Use valid GitHub-flavored Markdown with proper line breaks and bullet points."
                )),
                ("user", "Document Context:\n{context}"),
                ("user", "{question}"),
            ])
            chain = qa_prompt | llm
            response = chain.invoke({"context": context, "question": question})

        # Save chat history
        save_pdf_chat_history.invoke({
            "unique_string": uniqueString,
            "question": question,
            "answer": response.content,
        })

        log.info("PDF QA completed",
                 response_len=len(response.content), filenames=filenames)
        return {"answer": response.content, "filenames": filenames, "upload_date": upload_date}

    else:
        log.warning("Invalid usecase", usecase=usecase)
        raise HTTPException(status_code=400, detail=f"Invalid usecase: {usecase}")


# ============================================================
# Delete VectorDB Route
# ============================================================

@app.delete("/pyapi/delete_vectordb/{unique_string}")
@limiter.limit(f"{RATE_LIMIT_PER_MINUTE}/minute")
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


# ============================================================
# Async PDF Upload (background worker)
# ============================================================

@app.post("/pyapi/upload_async")
@limiter.limit(f"{RATE_LIMIT_PER_MINUTE}/minute")
async def upload_async(
    request: Request,
    uniqueString: str = Form(...),
    file: UploadFile = File(...),
):
    """Upload a PDF for background processing. Returns a job_id for polling.

    Use GET /pyapi/job_status/{job_id} to check progress.
    """
    from workers.pdf_processor import submit_pdf_job

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    filename = secure_filename(file.filename)

    # Save to temp file
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            file.file.seek(0)
            tmp.write(await file.read())
            tmp_path = tmp.name

        job_id = submit_pdf_job(tmp_path, uniqueString, filename)
        log.info("Async PDF upload submitted",
                 job_id=job_id, filename=filename, unique_string=uniqueString)
        return {"job_id": job_id, "status": "pending", "filename": filename}
    except Exception as e:
        # Clean up temp file if job submission fails
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


@app.get("/pyapi/job_status/{job_id}")
async def job_status(job_id: str):
    """Check the status of a background PDF processing job."""
    from workers.pdf_processor import get_job_status

    status = get_job_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return status


# ============================================================
# Health Check
# ============================================================

@app.get("/pyapi/health")
async def health():
    """Simple health check endpoint."""
    return {"status": "ok", "version": "2.0.0"}


# ------------------------------------------------------------------
# Admin: Fallback Log (ES backfill pipeline)
# ------------------------------------------------------------------

@app.get("/pyapi/admin/fallback_logs")
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


@app.get("/pyapi/admin/fallback_stats")
async def admin_fallback_stats():
    """Aggregate stats: totals by agent, date, top repeated queries."""
    try:
        return await chat_store.get_fallback_stats()
    except Exception as e:
        log.error("Failed to fetch fallback stats", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch fallback stats")


@app.get("/pyapi/threads")
async def list_threads(limit: int = 50, offset: int = 0):
    """List recent chat sessions for the sidebar history."""
    try:
        threads = await chat_store.list_threads(limit=min(limit, 100), offset=offset)
        return {"status": "ok", "threads": threads}
    except Exception as e:
        log.error("Failed to list threads", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to list threads")


@app.get("/pyapi/threads/{thread_id}/messages")
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


@app.post("/pyapi/feedback")
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
