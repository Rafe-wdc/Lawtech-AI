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
#
# Chunk size raised from 800 → 8000 on 2026-06-23 to match the V1 baseline
# (V1 used 15000) and reduce embedding-service round-trips by ~10×. The
# shared chunk constants live in core.file_processor; we import them so a
# single source of truth governs both this in-process API and the
# inline-chat persistence path. Legal documents are coherent paragraphs and
# tolerate large chunks well — the per-retrieval k=30 MMR keeps recall
# strong even with fewer total chunks per document.
from core.file_processor import CHROMA_CHUNK_SIZE, CHROMA_CHUNK_OVERLAP

_text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHROMA_CHUNK_SIZE,
    chunk_overlap=CHROMA_CHUNK_OVERLAP,
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
        search_kwargs={"k": 5, "fetch_k": 15},
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

    # Embed-truncation telemetry: all-MiniLM-L6-v2 truncates inputs at ~256
    # tokens (~1K chars). With 15K-char chunks each embedding represents
    # only the first ~1K chars. Logged so we can see the ratio if RAG
    # retrieval quality ever becomes a concern. (Default agent path is
    # get_full_attachment, so this is only material when RAG is used.)
    total_chars = sum(len(c) for c in chunks)
    embedded_chars = min(1000, max(len(c) for c in chunks)) * len(chunks) if chunks else 0
    truncation_ratio = round(embedded_chars / total_chars, 3) if total_chars else 1.0
    log.info(
        f"Stored {len(documents)} chunks for {file_name} in {unique_string} "
        f"(total_chars={total_chars}, embed_truncation_ratio={truncation_ratio})"
    )
    return {"chunks_stored": len(documents), "collection": unique_string}


# --- RAG Attachment Routing (Phase C, 2026-06-28) ---
#
# Two tools used by agents that consume uploaded-file content. The DEFAULT
# tool is get_full_attachment (lossless, full document). retrieve_attachment_context
# is kept for future cost-optimization scenarios (Options B/C in the plan).


def _chunk_index(meta: dict | None) -> int:
    """Best-effort recovery of original chunk ordering from metadata."""
    if not meta:
        return 0
    raw = meta.get("chunk")
    if raw is None:
        raw = meta.get("chunk_id")
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


@tool
def get_full_attachment(collection_id: str) -> dict:
    """Return the full content of an uploaded attachment as concatenated text.

    DEFAULT tool for any question about an attachment. Lossless — every
    chunk stored in ChromaDB is returned in original order. May be large
    (up to ~3.5 M chars for a 500-page PDF); Gemini Pro's 1 M-token window
    handles it. Use this whenever in doubt.

    Args:
        collection_id: ChromaDB collection identifier (from
            FileContextData.chromadb_collections).

    Returns:
        Dict with keys:
            full_text (str): concatenated chunk content in original order
            char_count (int): len(full_text)
            source_file (str): original filename if recorded in metadata
            chunk_count (int): number of chunks recovered
    """
    _validate_collection_name(collection_id)
    embeddings = get_qa_embeddings()
    vectordb = Chroma(
        client=get_chroma_client(),
        collection_name=collection_id,
        embedding_function=embeddings,
    )
    raw = vectordb.get()
    documents = raw.get("documents") or []
    metadatas = raw.get("metadatas") or []

    pairs = list(zip(documents, metadatas))
    pairs.sort(key=lambda p: _chunk_index(p[1]))

    source_file = ""
    for _, m in pairs:
        if m and m.get("source"):
            source_file = m["source"]
            break

    full_text = "\n".join(d for d, _ in pairs if d)
    log.info(
        f"get_full_attachment served collection={collection_id} "
        f"chunks={len(pairs)} chars={len(full_text)} source={source_file!r}"
    )
    return {
        "full_text": full_text,
        "char_count": len(full_text),
        "source_file": source_file,
        "chunk_count": len(pairs),
    }


@tool
def retrieve_attachment_context(query: str, collection_ids: list[str]) -> dict:
    """Top-K chunk retrieval across one or more attachments. OPTIONAL.

    Use ONLY when the question targets a clearly named clause/section AND
    token cost matters. Otherwise prefer get_full_attachment (lossless).

    Per-collection similarity search with k=5; results from all collections
    merged and the global top-5 by similarity score returned.

    Args:
        query: The question to search for.
        collection_ids: List of ChromaDB collection identifiers.

    Returns:
        Dict with keys:
            chunks (list of {content, source, chunk_id, score, collection}):
                global top-5 across all collections
            total (int): len(chunks)
    """
    if not collection_ids:
        return {"chunks": [], "total": 0}
    embeddings = get_qa_embeddings()
    client = get_chroma_client()
    all_hits: list[dict] = []
    for cid in collection_ids:
        try:
            _validate_collection_name(cid)
            vectordb = Chroma(
                client=client,
                collection_name=cid,
                embedding_function=embeddings,
            )
            hits = vectordb.similarity_search_with_score(query, k=5)
            for doc, score in hits:
                all_hits.append({
                    "content": doc.page_content,
                    "source": doc.metadata.get("source"),
                    "chunk_id": _chunk_index(doc.metadata),
                    "score": float(score),
                    "collection": cid,
                })
        except Exception as e:
            log.warning(
                f"retrieve_attachment_context: collection {cid} failed: {e}"
            )
            continue

    # Chroma default L2 distance: lower = more similar
    all_hits.sort(key=lambda h: h["score"])
    top = all_hits[:5]
    return {"chunks": top, "total": len(top)}
