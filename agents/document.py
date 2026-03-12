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
import json
import os
import re
from datetime import date

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from langchain_community.vectorstores import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.state import LegalAgentState, AgentResult, SourceMetadata, FileContextData
from core.clients import get_gemini_pro, get_qa_embeddings
from core.settings import CHROMA_STORE_ROOT
from core.language import localize_prompt
from core.logger import get_logger, log_time

import chromadb
import threading

_chroma_cache_lock = threading.Lock()
log = get_logger("Document")

_SAFE_COLLECTION_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$')


def _validate_collection_name(unique_string: str) -> None:
    if not unique_string or not _SAFE_COLLECTION_RE.match(unique_string):
        raise ValueError(f"Invalid or unsafe collection name: {unique_string!r}")


# --- Text Chunking ---

_text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,
    chunk_overlap=200,
    length_function=len,
)


# --- Chat History for PDF Sessions ---

def _load_pdf_chat_history(unique_string: str) -> tuple[list[dict], list[dict]]:
    """Load PDF-specific chat history from local JSON file.
    Returns (recent_messages, all_chats).
    """
    _validate_collection_name(unique_string)
    chat_dir = os.path.join(CHROMA_STORE_ROOT, "chat_histories")
    chat_file = os.path.join(chat_dir, f"{unique_string}_chat.json")

    if not os.path.exists(chat_file):
        return [], []

    try:
        with open(chat_file, "r", encoding="utf-8") as f:
            all_chats = json.load(f)
        recent = all_chats[-5:] if len(all_chats) > 5 else all_chats
        log.debug("PDF chat history loaded",
                  collection=unique_string, total=len(all_chats),
                  recent=len(recent))
        return recent, all_chats
    except Exception as e:
        log.error("Failed to load chat history",
                  collection=unique_string, error=str(e))
        return [], []


def _save_pdf_chat_history(
    unique_string: str, question: str, answer: str, all_chats: list[dict]
) -> None:
    """Save a Q&A pair to the PDF-specific chat history."""
    _validate_collection_name(unique_string)
    chat_dir = os.path.join(CHROMA_STORE_ROOT, "chat_histories")
    os.makedirs(chat_dir, exist_ok=True)
    chat_file = os.path.join(chat_dir, f"{unique_string}_chat.json")

    all_chats.append({"question": question, "answer": answer})

    try:
        with open(chat_file, "w", encoding="utf-8") as f:
            json.dump(all_chats, f, ensure_ascii=False, indent=2)
        log.debug("Chat history saved",
                  collection=unique_string, total_entries=len(all_chats))
    except Exception as e:
        log.error("Failed to save chat history",
                  collection=unique_string, error=str(e))


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


def process_and_store_text(
    unique_string: str, text: str, file_name: str
) -> int:
    """Chunk text and store in ChromaDB. Returns number of chunks stored.

    This is called from the gateway API route during PDF upload,
    not from the LangGraph agent flow.
    """
    chunks = _text_splitter.split_text(text)
    if not chunks:
        return 0

    documents = [
        Document(page_content=chunk, metadata={"source": file_name, "chunk_id": i})
        for i, chunk in enumerate(chunks)
    ]

    vectordb = _get_or_create_collection(unique_string)
    vectordb.add_documents(documents)
    log.info("Chunks stored",
             collection=unique_string, chunks=len(documents),
             file=file_name)
    return len(documents)


def _retrieve_and_answer(
    unique_string: str, query: str, chat_history_messages: list[dict]
) -> tuple[str, int, list[Document]]:
    """Retrieve from user's collection and generate answer.
    Returns (answer_text, tokens_consumed, retrieved_docs).
    """
    vectordb = _get_or_create_collection(unique_string)

    # MMR retrieval for diversity
    with log_time(log, "MMR retrieval", collection=unique_string):
        retriever = vectordb.as_retriever(
            search_type="mmr",
            search_kwargs={"k": 30, "fetch_k": 50},
        )
        docs = retriever.invoke(query)

    if not docs:
        log.warning("No relevant docs found", collection=unique_string)
        return "No relevant content found in the uploaded document(s) for this query.", 0, []

    log.debug("Documents retrieved",
              collection=unique_string, docs_found=len(docs))

    docs_text = "\n\n".join(d.page_content for d in docs)

    # Format chat history for prompt
    history_text = ""
    for msg in chat_history_messages[-5:]:
        history_text += f"Q: {msg.get('question', '')}\nA: {msg.get('answer', '')[:500]}\n\n"

    with log_time(log, "LLM generation"):
        llm = get_gemini_pro(temperature=0.3)
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are Lawttorney, a legal AI assistant. Answer questions about the uploaded document(s) using only the provided context. Be thorough and cite specific sections when possible."),
            ("user", "Previous conversation:\n{history}"),
            ("user", "Document content:\n{docs}"),
            ("user", "Current Date: {date}"),
            ("user", "Question: {query}"),
        ])
        chain = prompt | llm

        response = chain.invoke({
            "query": query,
            "docs": docs_text,
            "history": history_text,
            "date": str(date.today()),
        })

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

    # Check file_context for inline-uploaded collections
    if not unique_string:
        fc = FileContextData.from_state(state)
        if fc and fc.chromadb_collections:
            unique_string = fc.chromadb_collections[0]
            log.info("Using inline file collection", collection=unique_string)

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
                    prompt = ChatPromptTemplate.from_messages([
                        ("system", localize_prompt(
                            "You are Lawttorney, a legal AI assistant. Answer questions about "
                            "the uploaded document(s) using only the provided content. Be "
                            "thorough, detailed, and cite specific sections, clauses, parties, "
                            "dates, and legal provisions when possible.",
                            user_language,
                        )),
                        ("user", "Document content:\n{docs}"),
                        ("user", "Current Date: {date}"),
                        ("user", "Question: {query}"),
                    ])
                    chain = prompt | llm
                    response = chain.invoke({
                        "query": query,
                        "docs": fc.inline_text[:80000],
                        "date": str(date.today()),
                    })

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
        # Load chat history (sync file I/O → off-thread)
        recent_history, all_chats = await asyncio.to_thread(
            _load_pdf_chat_history, unique_string
        )

        # Retrieve from ChromaDB and generate answer (blocking I/O + LLM → off-thread)
        with log_time(log, "Full document QA pipeline"):
            answer, tokens, retrieved_docs = await asyncio.to_thread(
                _retrieve_and_answer, unique_string, query, recent_history
            )

        # Save chat history (sync file I/O → off-thread)
        await asyncio.to_thread(_save_pdf_chat_history, unique_string, query, answer, all_chats)

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
