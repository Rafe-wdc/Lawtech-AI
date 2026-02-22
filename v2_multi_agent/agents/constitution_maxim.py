"""Agent #9 — Constitution & Maxim Agent

Handles constitutional provisions, fundamental rights/duties,
directive principles, legal maxims, and general legal concepts.

Uses ChromaDB vectorstores with MultiQuery + BM25 ensemble retrieval.

Handles: "Article 21", "fundamental duties", "audi alteram partem", etc.

Uses: Gemini Flash Lite (generation), GPT-4o (multi-query generation)
Data Source: ChromaDB vectorstores (constitution db, legal maxim db)
"""

from __future__ import annotations

import asyncio
import os
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
from core.logger import get_logger, log_time
from config.prompts import (
    CONSTITUTION_SYSTEM_PROMPT,
    MAXIM_SYSTEM_PROMPT,
    LEGAL_CONCEPTS_PROMPT,
)

import chromadb

log = get_logger("ConstitutionMaxim")


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
        log.info("ChromaDB initialized", task=task, persist_dir=persist_dir)

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
        with log_time(log, "MultiQuery retrieval", task=task):
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

        log.info("MultiQuery returned docs",
                 count=len(unique_docs), task=task)

    except Exception as e:
        log.warning("MultiQuery failed, using fallback",
                    error=str(e), task=task)
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
    log.info("Top source identified",
             source=top_source, frequency=source_counts[top_source],
             total_sources=len(source_counts))

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

    log.debug("Source docs loaded",
              source=top_source, doc_count=len(source_docs))

    # Step 4: BM25 + Chroma ensemble
    with log_time(log, "BM25+Chroma ensemble"):
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

    log.info("Ensemble retrieval done", result_count=len(results))
    return results


# --- Task Handlers ---

async def _handle_legal_concepts(query: str, chat_history: list) -> AgentResult:
    """Handle Legal_Concepts task — direct LLM response, no retrieval."""
    with log_time(log, "Legal concepts LLM generation"):
        llm = get_gemini_flash(temperature=0.3).bind(max_tokens=8192)
        prompt = ChatPromptTemplate.from_messages([
            ("user", LEGAL_CONCEPTS_PROMPT),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("user", "Current Date: {date}"),
            ("user", "{query}"),
        ])
        chain = prompt | llm
        from core.streaming import stream_chain_response
        response = await stream_chain_response(chain, {
            "query": query,
            "chat_history": chat_history,
            "date": str(date.today()),
        })

    tokens = 0
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        tokens = response.usage_metadata.get("total_tokens", 0)

    log.info("Legal concepts completed",
             response_len=len(response.content), tokens=tokens)

    return AgentResult(
        agent_name="Legal_Concepts",
        content=response.content,
        sources=[SourceMetadata(
            source_type="legal_concepts",
            title="AI-Generated Legal Explanation",
            content=["Response generated from AI legal knowledge"],
            agent_name="Legal_Concepts",
        )],
        tokens_consumed=tokens,
    )


async def _handle_constitution_or_maxim(task: str, query: str, chat_history: list) -> AgentResult:
    """Handle Constitution or Maxim task — ChromaDB retrieval + LLM generation."""
    system_prompt = (
        CONSTITUTION_SYSTEM_PROMPT if task == "Constitution"
        else MAXIM_SYSTEM_PROMPT
    )

    with log_time(log, "ChromaDB retrieval", task=task):
        docs = await asyncio.to_thread(_retrieve_from_chromadb, task, query)

    if not docs:
        log.warning("No documents found", task=task)
        return AgentResult(
            agent_name=task,
            content="",
            sources=[],
            tokens_consumed=0,
        )

    docs_text = "\n\n".join(d.page_content for d in docs)
    source_name = docs[0].metadata.get("source", "unknown")

    log.debug("Generating response",
              task=task, docs_count=len(docs),
              source=source_name, context_len=len(docs_text))

    with log_time(log, "LLM generation", task=task):
        llm = get_gemini_flash(temperature=0.1)
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("user", "Retrieved provisions:\n{docs}"),
            ("user", "Current Date: {date}"),
            ("user", "User Query: {query}"),
        ])
        chain = prompt | llm

        from core.streaming import stream_chain_response
        response = await stream_chain_response(chain, {
            "query": query,
            "docs": docs_text,
            "chat_history": chat_history,
            "date": str(date.today()),
        })

    tokens = 0
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        tokens = response.usage_metadata.get("total_tokens", 0)

    log.info("Task completed",
             task=task, source=source_name,
             response_len=len(response.content), tokens=tokens)

    sources = []
    for d in docs[:5]:
        src_name = d.metadata.get("source", "unknown")
        sources.append(SourceMetadata(
            source_type=task.lower(),
            title=os.path.splitext(os.path.basename(src_name))[0] if src_name != "unknown" else task,
            content=[d.page_content[:300]],
            file_name=src_name,
            agent_name=task,
        ))

    return AgentResult(
        agent_name=task,
        content=response.content,
        sources=sources,
        tokens_consumed=tokens,
    )


# --- Agent Node ---

async def constitution_maxim_node(state: LegalAgentState) -> dict:
    """Retrieve constitutional provisions, legal maxims, or explain legal concepts.

    Handles ALL matching tasks from tasks_planned — not just the first.
    For example, if tasks_planned=["Constitution", "Maxim"], both are processed
    and returned as separate entries in agent_results for synthesis.

    Routes based on task type:
    - Constitution: ChromaDB retrieval + LLM generation
    - Maxim: ChromaDB retrieval + LLM generation
    - Legal_Concepts: Direct LLM response (no retrieval)
    """
    query = state.get("query", state["original_query"])
    primary_task = state.get("task", "Legal_Concepts")
    chat_history = state.get("chat_history", [])

    # Determine ALL tasks this node should handle from the plan.
    # Previously used next() which picked only the first match — Bug #1.
    our_tasks = {"Constitution", "Maxim", "Legal_Concepts"}
    planned = state.get("tasks_planned", [])
    active_tasks = [t for t in planned if t in our_tasks]

    # Fallback: if no planned tasks matched, use the primary task or default
    if not active_tasks:
        active_tasks = [primary_task if primary_task in our_tasks else "Legal_Concepts"]

    log.info("Agent started", tasks=active_tasks, query=query[:100])

    all_results: dict[str, AgentResult] = {}

    for task in active_tasks:
        try:
            if task == "Legal_Concepts":
                result = await _handle_legal_concepts(query, chat_history)
            else:
                result = await _handle_constitution_or_maxim(task, query, chat_history)
            all_results[task] = result
        except Exception as e:
            log.error("Task failed", task=task, error=str(e), exc_info=True)
            all_results[task] = AgentResult(
                agent_name=task,
                content="",
                sources=[],
                tokens_consumed=0,
                error=str(e),
            )

    log.info("Agent completed all tasks",
             tasks=list(all_results.keys()),
             success_count=sum(1 for r in all_results.values() if not r.error))

    return {"agent_results": all_results}
