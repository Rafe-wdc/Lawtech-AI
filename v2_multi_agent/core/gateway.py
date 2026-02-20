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

import json
import os
import shutil
import tempfile
import uuid
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Request, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from werkzeug.utils import secure_filename

from .settings import HOST, PORT, RATE_LIMIT_PER_MINUTE, CHROMA_STORE_ROOT
from .graph import compile_graph

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
agent_graph = compile_graph()


# --- Request / Response Schemas ---

class SearchRequest(BaseModel):
    Promptquery: str = Field(..., min_length=1, max_length=5000)
    globalThreadId: Optional[str] = None


class SearchResponse(BaseModel):
    globalThreadId: Optional[str]
    result: str
    total_tokens_consumed: int
    source: dict
    agents_used: list[str]


class PdfChatRequest(BaseModel):
    uniqueString: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1, max_length=5000)


# --- Helpers ---

def _build_initial_state(query: str, thread_id: str, unique_string: str | None = None) -> dict:
    """Build the initial LangGraph state with all required fields."""
    return {
        "messages": [],
        "original_query": query,
        "query": query,
        "thread_id": thread_id,
        "unique_string": unique_string,
        "task": None,
        "tasks_planned": [],
        "chat_history": [],
        "summary_text": "",
        "agent_results": {},
        "is_blocked": False,
        "block_reason": None,
        "final_response": "",
        "source_metadata": {},
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
    initial_state = _build_initial_state(data.Promptquery, thread_id)
    config = {"configurable": {"thread_id": thread_id}}

    try:
        final_state = await agent_graph.ainvoke(initial_state, config=config)
    except Exception as e:
        print(f"Agent graph error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

    # Handle blocked queries
    if final_state.get("is_blocked"):
        return SearchResponse(
            globalThreadId=thread_id,
            result=final_state.get("block_reason", "Query blocked by safety filter."),
            total_tokens_consumed=0,
            source={"title": "Blocked", "content": ["Query blocked by safety filter"], "docLink": None},
            agents_used=[],
        )

    # Build response
    agent_results = final_state.get("agent_results", {})
    agents_used = list(agent_results.keys())

    return SearchResponse(
        globalThreadId=thread_id,
        result=final_state.get("final_response", ""),
        total_tokens_consumed=final_state.get("tokens_consumed", 0),
        source=final_state.get("source_metadata", {}),
        agents_used=agents_used,
    )


@app.post("/pyapi/search/stream")
@limiter.limit(f"{RATE_LIMIT_PER_MINUTE}/minute")
async def search_stream(data: SearchRequest, request: Request):
    """Streaming legal Q&A endpoint (SSE).

    Streams real-time progress updates as Server-Sent Events:
    - Agent step completions (stream_mode="updates")
    - LLM token chunks (stream_mode="messages")

    Frontend can consume with EventSource or useStream hook.
    Ref: https://docs.langchain.com/oss/python/langchain/streaming/frontend
    """
    thread_id = data.globalThreadId or str(uuid.uuid4())
    initial_state = _build_initial_state(data.Promptquery, thread_id)
    config = {"configurable": {"thread_id": thread_id}}

    async def event_generator():
        """Generate SSE events from the agent graph stream."""
        # Send thread_id as first event
        yield f"data: {json.dumps({'type': 'thread_id', 'data': thread_id})}\n\n"

        try:
            # Stream both updates (agent steps) and messages (LLM tokens)
            async for stream_mode, chunk in agent_graph.astream(
                initial_state,
                config=config,
                stream_mode=["updates", "messages"],
            ):
                if stream_mode == "updates":
                    # Agent step completed — send step name + partial state
                    for node_name, update in chunk.items():
                        event = {
                            "type": "agent_step",
                            "agent": node_name,
                            "data": _serialize_update(update),
                        }
                        yield f"data: {json.dumps(event, default=str)}\n\n"

                elif stream_mode == "messages":
                    # LLM token chunk — stream to frontend
                    token, metadata = chunk
                    if hasattr(token, "content") and token.content:
                        event = {
                            "type": "token",
                            "node": metadata.get("langgraph_node", ""),
                            "content": token.content if isinstance(token.content, str) else "",
                        }
                        yield f"data: {json.dumps(event)}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)})}\n\n"

        # Send completion event
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _serialize_update(update: dict) -> dict:
    """Serialize a graph state update for SSE transmission."""
    serializable = {}
    for key, value in update.items():
        if key in ("final_response", "task", "tasks_planned", "is_blocked", "block_reason"):
            serializable[key] = value
        elif key == "agent_results":
            serializable["agent_results"] = {
                name: {"content_preview": r.content[:200] if hasattr(r, "content") else ""}
                for name, r in value.items()
            }
    return serializable


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
    import time
    from langchain_core.documents import Document
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    from tools.shared.document_tools import extract_text_vision
    from tools.shared.guardrail_tools import validate_input
    from tools.shared.storage_tools import load_pdf_chat_history, save_pdf_chat_history
    from tools.shared.memory_tools import rewrite_query
    from core.clients import get_qa_embeddings, get_gemini_pro

    if usecase == "upload":
        start_time = time.time()

        if not file or all(f.filename == "" for f in file):
            return {"status": False, "error": "No selected files"}

        documents = []
        processed_filenames = []

        for filex in file:
            if not filex or not filex.filename.lower().endswith(".pdf"):
                continue

            filename = secure_filename(filex.filename)
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    filex.file.seek(0)
                    tmp.write(await filex.read())
                    tmp_path = tmp.name

                pdf_doc = fitz.open(tmp_path)
                num_pages = len(pdf_doc)
                text_threshold = num_pages * 700

                extracted_text = ""
                for page in pdf_doc:
                    extracted_text += page.get_text("text") + "\n"
                pdf_doc.close()

                # Fallback to vision OCR if text is sparse
                if len(extracted_text.strip()) < text_threshold:
                    print(f"[Upload] Low text in {filename}, using Vision OCR fallback")
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
                print(f"[Upload] Error processing {filename}: {e}")
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)

        if not documents:
            return {"status": False, "error": "No text extracted from PDFs"}

        # Chunk documents
        text_splitter = RecursiveCharacterTextSplitter(
            separators=[""], chunk_size=15000, chunk_overlap=200
        )
        texts = text_splitter.split_documents(documents)

        # Store in ChromaDB
        collection_name = f"collection_{uniqueString}"
        persist_dir = _safe_persist_dir(uniqueString)
        os.makedirs(persist_dir, exist_ok=True)

        from langchain_community.vectorstores import Chroma

        embeddings = get_qa_embeddings()
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
            vectordb.add_documents(texts)
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
        return {
            "status": True,
            "chunks": len(texts),
            "filenames": all_filenames,
            "upload_time_sec": total_time,
        }

    elif usecase == "qa":
        if not question:
            return {"error": "question is required"}

        # Input guardrails
        guardrail_result = validate_input.invoke({"text": question})
        if not guardrail_result.get("valid", True):
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
        retriever = vectordb.as_retriever(search_kwargs={"k": 10})
        docs = retriever.invoke(search_query)
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

        return {"answer": response.content, "filenames": filenames, "upload_date": upload_date}

    else:
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

        return {"message": f'VectorDB for "{unique_string}" successfully deleted.'}
    except Exception as e:
        print(f"[Gateway] Failed to delete vectordb {unique_string}: {e}")
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
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        file.file.seek(0)
        tmp.write(await file.read())
        tmp_path = tmp.name

    job_id = submit_pdf_job(tmp_path, uniqueString, filename)
    return {"job_id": job_id, "status": "pending", "filename": filename}


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


# --- Entrypoint ---

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
