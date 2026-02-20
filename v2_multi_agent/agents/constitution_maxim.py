"""Agent #9 — Constitution & Maxim Agent

Handles constitutional provisions, fundamental rights/duties,
directive principles, legal maxims, and general legal concepts.

Uses ChromaDB vectorstores with MultiQuery + BM25 ensemble retrieval.

Handles: "Article 21", "fundamental duties", "audi alteram partem", etc.

Uses: Gemini Flash Lite (generation), GPT-4o (multi-query generation)
Data Source: ChromaDB vectorstores (constitution db, legal maxim db)
"""

from __future__ import annotations

from datetime import date
from typing import List

from langchain_core.documents import Document
from langchain_core.output_parsers import BaseOutputParser
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate, MessagesPlaceholder
from langchain_community.vectorstores import Chroma
from langchain_classic.retrievers.multi_query import MultiQueryRetriever
from langchain_classic.retrievers.ensemble import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import get_gpt4o, get_gemini_flash, get_retriever_embeddings
from core.settings import CHROMA_PERSIST_DIRS
from config.prompts import (
    CONSTITUTION_SYSTEM_PROMPT,
    MAXIM_SYSTEM_PROMPT,
    LEGAL_CONCEPTS_PROMPT,
)

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


# --- ChromaDB Initialization ---

_vectordbs: dict[str, Chroma] = {}


def _get_vectordb(task: str) -> Chroma:
    """Get or initialize a ChromaDB vectorstore for the given task."""
    if task not in _vectordbs:
        task_lower = task.lower()
        if task_lower not in CHROMA_PERSIST_DIRS:
            raise ValueError(f"No ChromaDB directory configured for task: {task}")

        persist_dir = CHROMA_PERSIST_DIRS[task_lower]
        embeddings = get_retriever_embeddings()

        # Clear cache to avoid stale connections
        chromadb.api.client.SharedSystemClient.clear_system_cache()

        _vectordbs[task] = Chroma(
            persist_directory=persist_dir,
            embedding_function=embeddings,
        )
        print(f"[Constitution/Maxim] Initialized ChromaDB for {task} at {persist_dir}")

    return _vectordbs[task]


# --- ChromaDB Retrieval with MultiQuery + BM25 Ensemble ---

def _retrieve_from_chromadb(task: str, query: str) -> list[Document]:
    """Retrieve documents using MultiQuery → find top source → BM25+Chroma ensemble.

    Steps:
    1. Generate 5 query variations via LLM
    2. MultiQuery retrieval from ChromaDB
    3. Find the most frequently occurring source document
    4. Get all documents from that source
    5. BM25 + Chroma ensemble retrieval for final ranking
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

        print(f"[Constitution/Maxim] MultiQuery returned {len(unique_docs)} docs")

    except Exception as e:
        print(f"[Constitution/Maxim] MultiQuery failed: {e}, using fallback")
        fallback = vectordb.as_retriever(search_kwargs={"k": 10})
        unique_docs = fallback.invoke(query)
        if not unique_docs:
            return []

    # Step 2: Find most common source
    source_counts: dict[str, int] = {}
    for doc in unique_docs:
        source = doc.metadata.get("source")
        if source:
            source_counts[source] = source_counts.get(source, 0) + 1

    if not source_counts:
        return unique_docs[:5]

    top_source = max(source_counts, key=source_counts.get)
    print(f"[Constitution/Maxim] Most frequent source: {top_source}")

    # Step 3: Get all docs from that source
    source_file = vectordb.get(where={"source": top_source})
    source_docs = []
    for content, metadata in zip(source_file["documents"], source_file["metadatas"]):
        source_docs.append(Document(
            page_content=content,
            metadata={"source": metadata.get("source"), "row": metadata.get("row")},
        ))

    if not source_docs:
        return unique_docs[:5]

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
    print(f"[Constitution/Maxim] Ensemble returned {len(results)} docs")
    return results


# --- Agent Node ---

async def constitution_maxim_node(state: LegalAgentState) -> dict:
    """Retrieve constitutional provisions, legal maxims, or explain legal concepts.

    Routes based on task type:
    - Constitution: ChromaDB retrieval + LLM generation
    - Maxim: ChromaDB retrieval + LLM generation
    - Legal_Concepts: Direct LLM response (no retrieval)
    """
    query = state.get("query", state["original_query"])
    task = state.get("task", "Legal_Concepts")
    chat_history = state.get("chat_history", [])

    # When invoked as part of a multi-agent plan, the primary task in state
    # may not be ours. Determine our effective task from the planned tasks list.
    our_tasks = {"Constitution", "Maxim", "Legal_Concepts"}
    if task not in our_tasks:
        planned = state.get("tasks_planned", [])
        task = next((t for t in planned if t in our_tasks), "Legal_Concepts")

    print(f"[Constitution/Maxim] Task={task}, Query: {query[:80]}...")

    try:
        # Legal_Concepts → direct LLM response (no retrieval needed)
        if task == "Legal_Concepts":
            llm = get_gemini_flash(temperature=0.3)
            prompt = ChatPromptTemplate.from_messages([
                ("user", LEGAL_CONCEPTS_PROMPT),
                MessagesPlaceholder(variable_name="chat_history", optional=True),
                ("user", "Current Date: {date}"),
                ("user", "{query}"),
            ])
            chain = prompt | llm
            response = chain.invoke({
                "query": query,
                "chat_history": chat_history,
                "date": str(date.today()),
            })

            tokens = 0
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                tokens = response.usage_metadata.get("total_tokens", 0)

            result = AgentResult(
                agent_name="Legal_Concepts",
                content=response.content,
                sources=[SourceMetadata(
                    title="Disclaimer",
                    content=["AI-generated response based on Legal Intelligence"],
                )],
                tokens_consumed=tokens,
            )
            return {"agent_results": {"Legal_Concepts": result}}

        # Constitution or Maxim → ChromaDB retrieval + generation
        system_prompt = (
            CONSTITUTION_SYSTEM_PROMPT if task == "Constitution"
            else MAXIM_SYSTEM_PROMPT
        )

        docs = _retrieve_from_chromadb(task, query)

        if not docs:
            print(f"[Constitution/Maxim] No documents found for {task}")
            return {
                "agent_results": {task: AgentResult(
                    agent_name=task,
                    content="",
                    sources=[],
                    tokens_consumed=0,
                )},
            }

        docs_text = "\n\n".join(d.page_content for d in docs)
        source_name = docs[0].metadata.get("source", "unknown")

        llm = get_gemini_flash(temperature=0.1)
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("user", "Retrieved provisions:\n{docs}"),
            ("user", "Current Date: {date}"),
            ("user", "User Query: {query}"),
        ])
        chain = prompt | llm

        response = chain.invoke({
            "query": query,
            "docs": docs_text,
            "chat_history": chat_history,
            "date": str(date.today()),
        })

        tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            tokens = response.usage_metadata.get("total_tokens", 0)

        result = AgentResult(
            agent_name=task,
            content=response.content,
            sources=[SourceMetadata(
                title=source_name,
                content=[d.page_content[:200] for d in docs[:3]],
                file_name=source_name,
            )],
            tokens_consumed=tokens,
        )

    except Exception as e:
        print(f"[Constitution/Maxim] Error: {e}")
        result = AgentResult(
            agent_name=task or "Constitution",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )
        task = task or "Constitution"

    return {"agent_results": {task: result}}
