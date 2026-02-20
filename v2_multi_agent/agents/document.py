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

import json
import os
from datetime import date

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from langchain_community.vectorstores import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import get_gemini_pro, get_qa_embeddings
from core.settings import CHROMA_STORE_ROOT

import chromadb


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
    chat_dir = os.path.join(CHROMA_STORE_ROOT, "chat_histories")
    chat_file = os.path.join(chat_dir, f"{unique_string}_chat.json")

    if not os.path.exists(chat_file):
        return [], []

    try:
        with open(chat_file, "r", encoding="utf-8") as f:
            all_chats = json.load(f)
        recent = all_chats[-5:] if len(all_chats) > 5 else all_chats
        return recent, all_chats
    except Exception as e:
        print(f"[Document] Failed to load chat history: {e}")
        return [], []


def _save_pdf_chat_history(
    unique_string: str, question: str, answer: str, all_chats: list[dict]
) -> None:
    """Save a Q&A pair to the PDF-specific chat history."""
    chat_dir = os.path.join(CHROMA_STORE_ROOT, "chat_histories")
    os.makedirs(chat_dir, exist_ok=True)
    chat_file = os.path.join(chat_dir, f"{unique_string}_chat.json")

    all_chats.append({"question": question, "answer": answer})

    try:
        with open(chat_file, "w", encoding="utf-8") as f:
            json.dump(all_chats, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Document] Failed to save chat history: {e}")


# --- ChromaDB Collection Management ---

def _get_or_create_collection(unique_string: str) -> Chroma:
    """Get or create a ChromaDB collection for a user's uploaded documents."""
    embeddings = get_qa_embeddings()
    persist_dir = os.path.join(CHROMA_STORE_ROOT, unique_string)

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
    print(f"[Document] Stored {len(documents)} chunks for {file_name}")
    return len(documents)


def _retrieve_and_answer(
    unique_string: str, query: str, chat_history_messages: list[dict]
) -> tuple[str, int]:
    """Retrieve from user's collection and generate answer.
    Returns (answer_text, tokens_consumed).
    """
    vectordb = _get_or_create_collection(unique_string)

    # MMR retrieval for diversity
    retriever = vectordb.as_retriever(
        search_type="mmr",
        search_kwargs={"k": 30, "fetch_k": 50},
    )
    docs = retriever.invoke(query)

    if not docs:
        return "No relevant content found in the uploaded document(s) for this query.", 0

    docs_text = "\n\n".join(d.page_content for d in docs)

    # Format chat history for prompt
    history_text = ""
    for msg in chat_history_messages[-5:]:
        history_text += f"Q: {msg.get('question', '')}\nA: {msg.get('answer', '')[:500]}\n\n"

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

    return response.content, tokens


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
    query = state.get("query", state["original_query"])
    unique_string = state.get("unique_string")
    print(f"[Document] Processing for collection: {unique_string}")

    if not unique_string:
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
        # Load chat history
        recent_history, all_chats = _load_pdf_chat_history(unique_string)

        # Retrieve and answer
        answer, tokens = _retrieve_and_answer(unique_string, query, recent_history)

        # Save chat history
        _save_pdf_chat_history(unique_string, query, answer, all_chats)

        result = AgentResult(
            agent_name="Document",
            content=answer,
            sources=[SourceMetadata(
                title="Uploaded Document",
                content=["Response based on uploaded PDF content"],
            )],
            tokens_consumed=tokens,
        )

    except Exception as e:
        print(f"[Document] Error: {e}")
        result = AgentResult(
            agent_name="Document",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )

    return {"agent_results": {"Document": result}}
