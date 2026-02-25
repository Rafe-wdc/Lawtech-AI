"""Agents #9a, #9b, #9c — Constitution, Maxim, and Legal Concepts

Split into THREE separate node functions so they can run in parallel
via LangGraph Send API. Previously a single combined node ran them
sequentially, which meant if Constitution failed, Maxim was unaffected
but the results were still merged in one pass.

Now:
- constitution_node: Handles Constitution queries (ChromaDB + LLM fallback)
- maxim_node: Handles Maxim queries (ChromaDB + LLM fallback)
- legal_concepts_node: Handles Legal_Concepts queries (direct LLM, no retrieval)

Uses ChromaDB vectorstores with MultiQuery + BM25 ensemble retrieval.

Handles: "Article 21", "fundamental duties", "audi alteram partem", etc.

Uses: Gemini Flash Lite (generation), GPT-4o (multi-query generation)
Data Source: ChromaDB vectorstores (constitution db, legal maxim db)
"""

from __future__ import annotations

import asyncio
import os
import threading
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
_vectordb_lock = threading.Lock()


def _get_vectordb(task: str) -> Chroma:
    """Get or initialize a ChromaDB vectorstore for the given task.

    Thread-safe: uses a lock to prevent race conditions when Constitution
    and Maxim agents initialize their vectorstores in parallel.
    """
    if task in _vectordbs:
        return _vectordbs[task]

    with _vectordb_lock:
        # Double-check after acquiring lock
        if task in _vectordbs:
            return _vectordbs[task]

        task_lower = task.lower()
        if task_lower not in CHROMA_PERSIST_DIRS:
            raise ValueError(f"No ChromaDB directory configured for task: {task}")

        persist_dir = CHROMA_PERSIST_DIRS[task_lower]
        embeddings = get_retriever_embeddings()

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
    """Handle Legal_Concepts task — web-grounded AI response for comprehensive coverage."""
    from core.agent_fallback import web_search_fallback
    log.info("Legal concepts using web search for comprehensive response")
    result = await web_search_fallback(query, "Legal_Concepts", LEGAL_CONCEPTS_PROMPT)
    return result


async def _handle_constitution_or_maxim(task: str, query: str, chat_history: list) -> AgentResult:
    """Handle Constitution or Maxim task — ChromaDB retrieval + web enrichment + LLM generation."""
    system_prompt = (
        CONSTITUTION_SYSTEM_PROMPT if task == "Constitution"
        else MAXIM_SYSTEM_PROMPT
    )

    # Run ChromaDB retrieval and web enrichment in parallel
    from core.agent_fallback import get_web_context
    with log_time(log, "ChromaDB + web enrichment (parallel)", task=task):
        docs, web_context = await asyncio.gather(
            asyncio.to_thread(_retrieve_from_chromadb, task, query),
            get_web_context(query, task),
        )

    if not docs:
        log.warning("No documents found in ChromaDB, using web search fallback", task=task)
        from core.agent_fallback import web_search_fallback
        result = await web_search_fallback(query, task, system_prompt)
        return result

    docs_text = "\n\n".join(d.page_content for d in docs)
    source_name = docs[0].metadata.get("source", "unknown")

    # Combine ChromaDB text with web context for richer input
    combined_context = docs_text
    if web_context:
        combined_context = (
            f"{docs_text}\n\n"
            f"--- Additional Context from Web Research ---\n{web_context}"
        )
        log.debug("Web enrichment added",
                  task=task, web_context_len=len(web_context))

    log.debug("Generating response",
              task=task, docs_count=len(docs),
              source=source_name, context_len=len(combined_context))

    with log_time(log, "LLM generation", task=task):
        llm = get_gemini_flash(temperature=0.3)
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("user", "Retrieved provisions and context:\n{docs}"),
            ("user", "Current Date: {date}"),
            ("user", "User Query: {query}"),
        ])
        chain = prompt | llm

        from core.streaming import stream_chain_response
        response = await stream_chain_response(chain, {
            "query": query,
            "docs": combined_context,
            "chat_history": chat_history,
            "date": str(date.today()),
        })

    tokens = 0
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        tokens = response.usage_metadata.get("total_tokens", 0)

    # Check if LLM apologized (docs were irrelevant) — fall back to web search
    _sorry_patterns = ["i am sorry", "i'm sorry", "does not contain", "no information",
                       "not contain information", "cannot find", "no relevant"]
    content_lower = response.content.lower()[:200]
    if any(p in content_lower for p in _sorry_patterns) or len(response.content) < 50:
        log.warning("LLM response is an apology or too short, using web fallback",
                    task=task, response_preview=response.content[:100])
        # Emit token_reset so frontend clears the sorry text
        try:
            from langgraph.config import get_stream_writer
            writer = get_stream_writer()
            writer({"type": "token_reset"})
        except RuntimeError:
            pass  # not in streaming context
        from core.agent_fallback import web_search_fallback
        result = await web_search_fallback(query, task, system_prompt)
        result.tokens_consumed += tokens  # include the wasted tokens
        # Stream the fallback content as tokens so frontend displays it
        try:
            writer = get_stream_writer()
            for i in range(0, len(result.content), 20):
                writer({"type": "token", "content": result.content[i:i+20]})
        except (RuntimeError, NameError):
            pass
        return result

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


# --- Separate Agent Nodes (parallel via LangGraph Send) ---

async def constitution_node(state: LegalAgentState) -> dict:
    """Handle Constitution queries — ChromaDB retrieval + LLM fallback.

    Runs as its own graph node so it executes in parallel with maxim_node.
    """
    query = state.get("query", state["original_query"])
    chat_history = state.get("chat_history", [])
    log.info("Constitution agent started", query=query[:100])

    try:
        result = await _handle_constitution_or_maxim("Constitution", query, chat_history)
    except Exception as e:
        log.error("Constitution agent failed", error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="Constitution",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )

    log.info("Constitution agent completed",
             has_content=bool(result.content), error=result.error)
    return {"agent_results": {"Constitution": result}}


async def maxim_node(state: LegalAgentState) -> dict:
    """Handle Maxim queries — ChromaDB retrieval + LLM fallback.

    Runs as its own graph node so it executes in parallel with constitution_node.
    """
    query = state.get("query", state["original_query"])
    chat_history = state.get("chat_history", [])
    log.info("Maxim agent started", query=query[:100])

    try:
        result = await _handle_constitution_or_maxim("Maxim", query, chat_history)
    except Exception as e:
        log.error("Maxim agent failed", error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="Maxim",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )

    log.info("Maxim agent completed",
             has_content=bool(result.content), error=result.error)
    return {"agent_results": {"Maxim": result}}


async def legal_concepts_node(state: LegalAgentState) -> dict:
    """Handle Legal_Concepts queries — direct LLM response, no retrieval."""
    query = state.get("query", state["original_query"])
    chat_history = state.get("chat_history", [])
    log.info("Legal Concepts agent started", query=query[:100])

    try:
        result = await _handle_legal_concepts(query, chat_history)
    except Exception as e:
        log.error("Legal Concepts agent failed", error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="Legal_Concepts",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )

    log.info("Legal Concepts agent completed",
             has_content=bool(result.content), error=result.error)
    return {"agent_results": {"Legal_Concepts": result}}
