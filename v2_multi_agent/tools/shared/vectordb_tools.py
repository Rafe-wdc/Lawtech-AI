"""Shared Tools: ChromaDB vector store operations.

Reusable @tool functions for searching and storing in ChromaDB:
- Constitution and Legal Maxim vectorstores (MultiQuery + BM25 ensemble)
- Per-user PDF document collections (MMR retrieval)

Uses: ChromaDB, HuggingFace embeddings from core.clients
"""

from __future__ import annotations

import os
from typing import Optional, List

from langchain.tools import tool
from langchain_core.documents import Document
from langchain_core.output_parsers import BaseOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_community.vectorstores import Chroma
from langchain_classic.retrievers.multi_query import MultiQueryRetriever
from langchain_classic.retrievers.ensemble import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.clients import get_gpt4o, get_retriever_embeddings, get_qa_embeddings
from core.settings import CHROMA_PERSIST_DIRS, CHROMA_STORE_ROOT

import chromadb


# --- Multi-Query Output Parser ---

class LineListOutputParser(BaseOutputParser[List[str]]):
    """Parse LLM output into a list of query variations (one per line)."""
    def parse(self, text: str) -> List[str]:
        lines = text.strip().split("\n")
        return list(filter(None, lines))


_output_parser = LineListOutputParser()

_MULTI_QUERY_PROMPT = PromptTemplate(
    input_variables=["question"],
    template="""You are an AI language model assistant. Your task is to generate five
    different versions of the given user question to retrieve relevant documents from a vector
    database. By generating multiple perspectives on the user question, your goal is to help
    the user overcome some of the limitations of the distance-based similarity search.
    Provide these alternative questions separated by newlines.
    Original question: {question}""",
)


# --- ChromaDB Singleton Cache ---

_vectordbs: dict[str, Chroma] = {}


def _get_vectordb(task: str) -> Chroma:
    """Get or initialize a ChromaDB vectorstore for the given task."""
    if task not in _vectordbs:
        task_lower = task.lower()
        if task_lower not in CHROMA_PERSIST_DIRS:
            raise ValueError(f"No ChromaDB directory configured for task: {task}")

        persist_dir = CHROMA_PERSIST_DIRS[task_lower]
        embeddings = get_retriever_embeddings()

        chromadb.api.client.SharedSystemClient.clear_system_cache()

        _vectordbs[task] = Chroma(
            persist_directory=persist_dir,
            embedding_function=embeddings,
        )
        print(f"[VectorDB] Initialized ChromaDB for {task} at {persist_dir}")

    return _vectordbs[task]


# --- Text Splitter ---

_text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,
    chunk_overlap=200,
    length_function=len,
)


# --- Constitution / Maxim Ensemble Search ---

@tool
def search_chromadb_ensemble(task: str, query: str) -> dict:
    """Search Constitution or Legal Maxim ChromaDB with MultiQuery + BM25 ensemble retrieval.

    Steps:
    1. Generate 5 query variations via LLM (GPT-4o)
    2. MultiQuery retrieval from ChromaDB
    3. Find the most frequently occurring source document
    4. Get all documents from that source
    5. BM25 + Chroma ensemble retrieval for final ranking

    Args:
        task: Which vectorstore to search — "Constitution" or "Maxim"
        query: The legal query to search for

    Returns:
        Dict with keys: documents (list of {content, source}), top_source
    """
    vectordb = _get_vectordb(task)

    # Step 1: MultiQuery retrieval
    try:
        llm = get_gpt4o(temperature=0.0)
        llm_chain = _MULTI_QUERY_PROMPT | llm | _output_parser

        base_retriever = vectordb.as_retriever(search_kwargs={"k": 10})
        multi_retriever = MultiQueryRetriever(
            retriever=base_retriever,
            llm_chain=llm_chain,
            parser_key="lines",
        )
        unique_docs = multi_retriever.invoke(query)

        if not unique_docs:
            raise ValueError("MultiQueryRetriever returned no documents")

        print(f"[VectorDB] MultiQuery returned {len(unique_docs)} docs for {task}")

    except Exception as e:
        print(f"[VectorDB] MultiQuery failed for {task}: {e}, using fallback")
        fallback = vectordb.as_retriever(search_kwargs={"k": 10})
        unique_docs = fallback.invoke(query)
        if not unique_docs:
            return {"documents": [], "top_source": None}

    # Step 2: Find most common source
    source_counts: dict[str, int] = {}
    for doc in unique_docs:
        source = doc.metadata.get("source")
        if source:
            source_counts[source] = source_counts.get(source, 0) + 1

    if not source_counts:
        return {
            "documents": [
                {"content": d.page_content, "source": d.metadata.get("source")}
                for d in unique_docs[:5]
            ],
            "top_source": None,
        }

    top_source = max(source_counts, key=source_counts.get)
    print(f"[VectorDB] Most frequent source for {task}: {top_source}")

    # Step 3: Get all docs from that source
    source_file = vectordb.get(where={"source": top_source})
    source_docs = []
    for content, metadata in zip(source_file["documents"], source_file["metadatas"]):
        source_docs.append(Document(
            page_content=content,
            metadata={"source": metadata.get("source"), "row": metadata.get("row")},
        ))

    if not source_docs:
        return {
            "documents": [
                {"content": d.page_content, "source": d.metadata.get("source")}
                for d in unique_docs[:5]
            ],
            "top_source": top_source,
        }

    # Step 4: BM25 + Chroma ensemble
    bm25_retriever = BM25Retriever.from_documents(source_docs)
    bm25_retriever.k = 4

    chroma_retriever = vectordb.as_retriever(
        search_type="mmr",
        search_kwargs={"filter": {"source": top_source}, "k": 4},
    )

    ensemble = EnsembleRetriever(
        retrievers=[bm25_retriever, chroma_retriever],
        weights=[0.5, 0.5],
    )

    results = ensemble.invoke(query)
    print(f"[VectorDB] Ensemble returned {len(results)} docs for {task}")

    return {
        "documents": [
            {"content": d.page_content, "source": d.metadata.get("source")}
            for d in results
        ],
        "top_source": top_source,
    }


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
    embeddings = get_qa_embeddings()
    persist_dir = os.path.join(CHROMA_STORE_ROOT, unique_string)

    chromadb.api.client.SharedSystemClient.clear_system_cache()

    vectordb = Chroma(
        collection_name=unique_string,
        persist_directory=persist_dir,
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
    chunks = _text_splitter.split_text(text)
    if not chunks:
        return {"chunks_stored": 0, "collection": unique_string}

    documents = [
        Document(page_content=chunk, metadata={"source": file_name, "chunk_id": i})
        for i, chunk in enumerate(chunks)
    ]

    embeddings = get_qa_embeddings()
    persist_dir = os.path.join(CHROMA_STORE_ROOT, unique_string)

    chromadb.api.client.SharedSystemClient.clear_system_cache()

    vectordb = Chroma(
        collection_name=unique_string,
        persist_directory=persist_dir,
        embedding_function=embeddings,
    )
    vectordb.add_documents(documents)

    print(f"[VectorDB] Stored {len(documents)} chunks for {file_name} in {unique_string}")
    return {"chunks_stored": len(documents), "collection": unique_string}
