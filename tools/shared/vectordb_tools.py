"""Shared Tools: ChromaDB vector store operations (PDF uploads only).

Reusable @tool functions for per-user PDF document collections:
- search_pdf_collection: MMR retrieval from uploaded PDFs
- store_pdf_chunks: Chunk and store uploaded PDF text

Uses: ChromaDB, HuggingFace embeddings from core.clients
"""

from __future__ import annotations

import os
import re

from langchain.tools import tool
from langchain_core.documents import Document
from langchain_community.vectorstores import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.clients import get_qa_embeddings, get_chroma_client
from core.logger import get_logger

log = get_logger("VectorDB")

# Only allow safe collection names: alphanumeric, hyphens, underscores, max 128 chars.
# Prevents path traversal (../), null bytes, and filesystem-special names.
_SAFE_COLLECTION_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$')


def _validate_collection_name(unique_string: str) -> None:
    if not unique_string or not _SAFE_COLLECTION_RE.match(unique_string):
        raise ValueError(f"Invalid or unsafe collection name: {unique_string!r}")


# --- Text Splitter ---

_text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,
    chunk_overlap=200,
    length_function=len,
)


# --- PDF Collection Search ---

@tool
def search_pdf_collection(unique_string: str, query: str) -> dict:
    """Search a user's uploaded PDF document collection in ChromaDB using MMR retrieval.

    Each user's uploaded PDFs are stored in a separate ChromaDB collection
    identified by a unique_string. Uses MMR (Maximal Marginal Relevance)
    for diverse document retrieval.

    Args:
        unique_string: The unique identifier for the user's document collection
        query: The question to search for in the uploaded documents

    Returns:
        Dict with keys: documents (list of {content, source, chunk_id}), total
    """
    _validate_collection_name(unique_string)
    embeddings = get_qa_embeddings()

    vectordb = Chroma(
        client=get_chroma_client(),
        collection_name=unique_string,
        embedding_function=embeddings,
    )

    retriever = vectordb.as_retriever(
        search_type="mmr",
        search_kwargs={"k": 30, "fetch_k": 50},
    )
    docs = retriever.invoke(query)

    return {
        "documents": [
            {
                "content": d.page_content,
                "source": d.metadata.get("source"),
                "chunk_id": d.metadata.get("chunk_id"),
            }
            for d in docs
        ],
        "total": len(docs),
    }


# --- PDF Chunk Storage ---

@tool
def store_pdf_chunks(unique_string: str, text: str, file_name: str) -> dict:
    """Chunk text and store in a user's ChromaDB collection.

    Splits the text into chunks of 800 characters with 200 character overlap,
    then stores in ChromaDB with all-MiniLM-L6-v2 embeddings.

    Args:
        unique_string: The unique identifier for the user's document collection
        text: The full text extracted from the PDF to be chunked and stored
        file_name: The original file name for source metadata

    Returns:
        Dict with keys: chunks_stored (int), collection (str)
    """
    _validate_collection_name(unique_string)
    chunks = _text_splitter.split_text(text)
    if not chunks:
        return {"chunks_stored": 0, "collection": unique_string}

    documents = [
        Document(page_content=chunk, metadata={"source": file_name, "chunk_id": i})
        for i, chunk in enumerate(chunks)
    ]

    embeddings = get_qa_embeddings()

    vectordb = Chroma(
        client=get_chroma_client(),
        collection_name=unique_string,
        embedding_function=embeddings,
    )
    vectordb.add_documents(documents)

    log.info(f"Stored {len(documents)} chunks for {file_name} in {unique_string}")
    return {"chunks_stored": len(documents), "collection": unique_string}
