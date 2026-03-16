"""Agent #10 — Document Agent

Handles PDF upload, processing, OCR, and document-specific Q&A.
Manages per-user ChromaDB collections for uploaded documents.

3-step flow:
1. Upload & Validate → check file size, page count, format
2. Process → extract text, OCR scanned pages, chunk, embed, store
3. Chat → retrieve from user's collection, generate answer

Uses: Gemini 2.5 Pro (Q&A), Gemini Flash Lite (Vision OCR)
Data Source: ChromaDB (per-user collections in chroma_store/)
Embedding: all-MiniLM-L6-v2
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import date

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from langchain_community.vectorstores import Chroma
from core.state import LegalAgentState, AgentResult, SourceMetadata, FileContextData
from core.clients import get_gemini_pro, get_qa_embeddings
from core.settings import CHROMA_STORE_ROOT, TIMEOUT_CHROMADB_SEC
from core.language import localize_prompt
from core.logger import get_logger, log_time
from core.progress import progress

import chromadb
import threading

_chroma_cache_lock = threading.Lock()
log = get_logger("Document")

_SAFE_COLLECTION_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$')


def _format_chat_history(chat_history: list) -> str:
    """Format LangGraph chat history messages into a text block for prompts.

    Takes the last 3 turns (6 messages) to keep context concise.
    """
    if not chat_history:
        return ""
    # Take last 6 messages (3 turns of Q+A)
    recent = chat_history[-6:]
    parts = []
    for msg in recent:
        role = getattr(msg, "type", "unknown")
        content = getattr(msg, "content", "")
        if role == "human":
            parts.append(f"User: {content[:500]}")
        elif role == "ai":
            parts.append(f"Assistant: {content[:500]}")
    return "\n".join(parts)


def _validate_collection_name(unique_string: str) -> None:
    if not unique_string or not _SAFE_COLLECTION_RE.match(unique_string):
        raise ValueError(f"Invalid or unsafe collection name: {unique_string!r}")




# --- ChromaDB Collection Management ---

def _get_or_create_collection(unique_string: str) -> Chroma:
    """Get or create a ChromaDB collection for a user's uploaded documents."""
    _validate_collection_name(unique_string)
    embeddings = get_qa_embeddings()
    persist_dir = os.path.join(CHROMA_STORE_ROOT, unique_string)

    with _chroma_cache_lock:
        chromadb.api.client.SharedSystemClient.clear_system_cache()

    return Chroma(
        collection_name=unique_string,
        persist_directory=persist_dir,
        embedding_function=embeddings,
    )



def _collection_has_data(unique_string: str) -> bool:
    """Check if a ChromaDB collection exists and has documents.

    Used to verify background OCR has completed before attempting retrieval.
    Returns False if collection is empty or doesn't exist yet.
    """
    try:
        vectordb = _get_or_create_collection(unique_string)
        count = vectordb._collection.count()
        return count > 0
    except Exception:
        return False


def _retrieve_from_collections(
    collections: list[str], query: str, k_per_collection: int = 15,
) -> list[Document]:
    """Retrieve from multiple ChromaDB collections and merge results.

    When only one collection exists, retrieves k=30 from it.
    When multiple exist, retrieves k_per_collection from each and merges.
    Skips empty collections (background OCR may not have completed yet).
    """
    all_docs: list[Document] = []
    ready_collections = [c for c in collections if _collection_has_data(c)]

    if not ready_collections:
        log.warning("No ChromaDB collections have data yet",
                    total=len(collections))
        return []

    if len(ready_collections) != len(collections):
        log.info("Some collections not ready (background OCR pending)",
                 ready=len(ready_collections), total=len(collections))

    if len(ready_collections) == 1:
        # Single collection — use full k budget
        vectordb = _get_or_create_collection(ready_collections[0])
        with log_time(log, "MMR retrieval", collection=ready_collections[0]):
            retriever = vectordb.as_retriever(
                search_type="mmr",
                search_kwargs={"k": 30, "fetch_k": 50},
            )
            all_docs = retriever.invoke(query)
    else:
        # Multiple collections — retrieve from each, merge
        for coll_id in ready_collections:
            try:
                vectordb = _get_or_create_collection(coll_id)
                with log_time(log, "MMR retrieval", collection=coll_id):
                    retriever = vectordb.as_retriever(
                        search_type="mmr",
                        search_kwargs={"k": k_per_collection, "fetch_k": k_per_collection * 2},
                    )
                    docs = retriever.invoke(query)
                    all_docs.extend(docs)
            except Exception as e:
                log.warning("Collection retrieval failed",
                            collection=coll_id, error=str(e))
        log.info("Multi-collection retrieval",
                 collections=len(ready_collections), total_docs=len(all_docs))

    return all_docs


def _retrieve_and_answer(
    unique_string: str, query: str, history_text: str = "",
    collections: list[str] | None = None,
) -> tuple[str, int, list[Document]]:
    """Retrieve from user's collection(s) and generate answer.
    Returns (answer_text, tokens_consumed, retrieved_docs).
    """
    coll_list = collections or [unique_string]
    docs = _retrieve_from_collections(coll_list, query)

    if not docs:
        log.warning("No relevant docs found", collection=unique_string)
        return "No relevant content found in the uploaded document(s) for this query.", 0, []

    log.debug("Documents retrieved",
              collection=unique_string, docs_found=len(docs))

    docs_text = "\n\n".join(d.page_content for d in docs)

    with log_time(log, "LLM generation"):
        llm = get_gemini_pro(temperature=0.3)
        prompt_messages = [
            ("system", "You are Lawttorney, a legal AI assistant. Answer questions about the uploaded document(s) using only the provided context. Be thorough and cite specific sections when possible."),
        ]
        if history_text:
            prompt_messages.append(("user", "Previous conversation:\n{history}"))
        prompt_messages.extend([
            ("user", "Document content:\n{docs}"),
            ("user", "Current Date: {date}"),
            ("user", "Question: {query}"),
        ])
        prompt = ChatPromptTemplate.from_messages(prompt_messages)
        chain = prompt | llm

        invoke_args = {
            "query": query,
            "docs": docs_text,
            "date": str(date.today()),
        }
        if history_text:
            invoke_args["history"] = history_text
        response = chain.invoke(invoke_args)

    tokens = 0
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        tokens = response.usage_metadata.get("total_tokens", 0)

    return response.content, tokens, docs


# --- Agent Node ---

async def document_node(state: LegalAgentState) -> dict:
    """Process PDF documents or answer questions about them.

    For CHAT (main use case in the multi-agent graph):
    1. Load PDF-specific chat history
    2. Retrieve from user's ChromaDB collection (MMR, k=30)
    3. Generate answer with Gemini 2.5 Pro
    4. Save chat history

    Note: PDF upload/processing is handled separately via the gateway
    API route, not through the LangGraph agent flow.
    """
    query = state.get("query") or state.get("original_query", "")
    unique_string = state.get("unique_string")
    user_language = state.get("user_language", "en")
    chat_history = state.get("chat_history", [])

    # Check file_context — prefer Gemini file parts (native PDF reading)
    # over ChromaDB (OCR'd text chunks) when both are available
    all_collections: list[str] = []
    if not unique_string:
        fc = FileContextData.from_state(state)
        if fc and fc.all_gemini_parts:
            # Gemini URIs available — use multimodal path (better quality)
            pass  # handled below
        elif fc and fc.chromadb_collections:
            all_collections = fc.chromadb_collections
            unique_string = all_collections[0]
            log.info("Using inline file collections",
                     collections=all_collections, primary=unique_string)

    progress("document", "Loading uploaded documents...", step="load")
    log.info("Agent started",
             collection=unique_string, query=query[:100])

    if not unique_string:
        fc = FileContextData.from_state(state)

        # Check for Gemini file parts (images, PDFs, TXT, CSV via Files API)
        # all_gemini_parts includes legacy base64 images for backward compat
        if fc and fc.all_gemini_parts:
            parts = fc.all_gemini_parts
            log.info("Using Gemini file parts for multimodal document QA",
                     parts=len(parts), files=fc.file_names)
            try:
                with log_time(log, "Gemini file parts document QA"):
                    llm = get_gemini_pro(temperature=0.3)

                    # Build content list: file parts + question text
                    user_content: list = []
                    for part in parts:
                        if "file_data" in part:
                            # Gemini Files API URI — pass as file_data dict
                            user_content.append(part)
                        elif "inline_data" in part:
                            # Legacy base64 — pass as image_url for LangChain
                            d = part["inline_data"]
                            user_content.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{d['mime_type']};base64,{d['data']}"
                                },
                            })
                    user_content.append({
                        "type": "text",
                        "text": f"Current Date: {date.today()}\n\nQuestion: {query}",
                    })

                    _doc_system = localize_prompt(
                        "You are Lawttorney, a legal AI assistant. Analyze the uploaded "
                        "document(s) carefully. Extract all visible text, identify the "
                        "document type, and answer the user's question thoroughly. Cite "
                        "specific details: names, dates, section numbers, case numbers, "
                        "court names, and legal provisions visible in the document.",
                        user_language,
                    )
                    messages = [("system", _doc_system)]
                    history_text = _format_chat_history(chat_history)
                    if history_text:
                        messages.append(("user", f"Previous conversation:\n{history_text}"))
                    if fc.inline_text:
                        messages.append(("user", f"Additional document text:\n{fc.inline_text[:40000]}"))
                    messages.append(("user", user_content))

                    response = llm.invoke(messages)

                tokens = 0
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    tokens = response.usage_metadata.get("total_tokens", 0)

                log.info("Gemini file parts document QA completed",
                         response_len=len(response.content), tokens=tokens)

                sources = [SourceMetadata(
                    source_type="document",
                    title=f"Uploaded: {fn}",
                    content=["Multimodal file analysis (Gemini Files API)"],
                    file_name=fn,
                    agent_name="Document",
                ) for fn in fc.file_names[:5]]

                return {"agent_results": {"Document": AgentResult(
                    agent_name="Document",
                    content=response.content,
                    sources=sources,
                    tokens_consumed=tokens,
                )}}

            except Exception as e:
                log.error("Gemini file parts document QA failed", error=str(e), exc_info=True)
                return {"agent_results": {"Document": AgentResult(
                    agent_name="Document",
                    content="",
                    sources=[],
                    tokens_consumed=0,
                    error=str(e),
                )}}

        # Check for inline file text (small files not stored in ChromaDB)
        if fc and fc.inline_text:
            log.info("Using inline file text for document QA",
                     inline_chars=len(fc.inline_text), files=fc.file_names)
            try:
                with log_time(log, "Inline document QA"):
                    llm = get_gemini_pro(temperature=0.3)
                    history_text = _format_chat_history(chat_history)
                    prompt_messages = [
                        ("system", localize_prompt(
                            "You are Lawttorney, a legal AI assistant. Answer questions about "
                            "the uploaded document(s) using only the provided content. Be "
                            "thorough, detailed, and cite specific sections, clauses, parties, "
                            "dates, and legal provisions when possible.",
                            user_language,
                        )),
                    ]
                    if history_text:
                        prompt_messages.append(("user", "Previous conversation:\n{history}"))
                    prompt_messages.extend([
                        ("user", "Document content:\n{docs}"),
                        ("user", "Current Date: {date}"),
                        ("user", "Question: {query}"),
                    ])
                    prompt = ChatPromptTemplate.from_messages(prompt_messages)
                    chain = prompt | llm
                    invoke_args = {
                        "query": query,
                        "docs": fc.inline_text[:80000],
                        "date": str(date.today()),
                    }
                    if history_text:
                        invoke_args["history"] = history_text
                    response = chain.invoke(invoke_args)

                tokens = 0
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    tokens = response.usage_metadata.get("total_tokens", 0)

                log.info("Inline document QA completed",
                         response_len=len(response.content), tokens=tokens)

                sources = [SourceMetadata(
                    source_type="document",
                    title=f"Uploaded: {fn}",
                    content=["Inline file analysis"],
                    file_name=fn,
                    agent_name="Document",
                ) for fn in fc.file_names[:5]]

                return {"agent_results": {"Document": AgentResult(
                    agent_name="Document",
                    content=response.content,
                    sources=sources,
                    tokens_consumed=tokens,
                )}}

            except Exception as e:
                log.error("Inline document QA failed", error=str(e), exc_info=True)
                return {"agent_results": {"Document": AgentResult(
                    agent_name="Document",
                    content="",
                    sources=[],
                    tokens_consumed=0,
                    error=str(e),
                )}}

        log.warning("No unique_string provided and no file content")
        return {
            "agent_results": {"Document": AgentResult(
                agent_name="Document",
                content="No document collection specified. Please upload a PDF first.",
                sources=[],
                tokens_consumed=0,
                error="missing_unique_string",
            )},
        }

    try:
        # Use chat history from state (loaded by memory node from SQLite)
        history_text = _format_chat_history(chat_history)

        # Retrieve from ChromaDB and generate answer (blocking I/O + LLM → off-thread)
        # 90s timeout: ChromaDB MMR (k=30) + Gemini Pro generation can be slow,
        # but if ChromaDB hangs on a locked/stalled disk this prevents infinite hang.
        n_colls = len(all_collections) if all_collections else 1
        progress("document", f"Searching across {n_colls} document collections...", found=n_colls, step="search")
        with log_time(log, "Full document QA pipeline"):
            answer, tokens, retrieved_docs = await asyncio.wait_for(
                asyncio.to_thread(
                    _retrieve_and_answer, unique_string, query, history_text,
                    collections=all_collections or None,
                ),
                timeout=TIMEOUT_CHROMADB_SEC,
            )
        # Note: chat history is saved by the gateway after the full graph run,
        # not by the document agent. No duplicate save needed here.

        progress("document", f"Found {len(retrieved_docs)} relevant passages", found=len(retrieved_docs), substep=True, step="search")
        progress("document", "Generating answer from documents...", step="generate")
        log.info("Agent completed",
                 collection=unique_string,
                 response_len=len(answer), tokens=tokens)

        sources = []
        for d in retrieved_docs[:5]:
            src_file = d.metadata.get("source", "PDF")
            sources.append(SourceMetadata(
                source_type="document",
                title=f"Uploaded: {src_file}",
                content=[d.page_content[:200]],
                file_name=src_file,
                agent_name="Document",
            ))
        if not sources:
            sources.append(SourceMetadata(
                source_type="document",
                title="Uploaded Document",
                content=["Response based on uploaded PDF content"],
                agent_name="Document",
            ))

        result = AgentResult(
            agent_name="Document",
            content=answer,
            sources=sources,
            tokens_consumed=tokens,
        )

    except Exception as e:
        log.error("Agent failed",
                  collection=unique_string, error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="Document",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )

    return {"agent_results": {"Document": result}}
