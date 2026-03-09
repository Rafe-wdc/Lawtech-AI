"""Agents #9a, #9b, #9c — Constitution, Maxim, and Legal Concepts

Split into THREE separate node functions so they can run in parallel
via LangGraph Send API.

Now:
- constitution_node: Handles Constitution queries (Elasticsearch + LLM fallback)
- maxim_node: Handles Maxim queries (Elasticsearch + LLM fallback)
- legal_concepts_node: Handles Legal_Concepts queries (direct LLM, no retrieval)

Uses Elasticsearch indices for Constitution and Legal Maxim retrieval.

Handles: "Article 21", "fundamental duties", "audi alteram partem", etc.

Uses: Gemini Flash Lite (generation)
Data Source: Elasticsearch indices (constitution, legal_maxims)
"""

from __future__ import annotations

import asyncio
import os
from datetime import date

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import get_gemini_flash, get_es_client
from core.settings import ES_INDICES
from core.logger import get_logger, log_time
from config.prompts import (
    CONSTITUTION_SYSTEM_PROMPT,
    MAXIM_SYSTEM_PROMPT,
    LEGAL_CONCEPTS_PROMPT,
)
from tools.shared.elasticsearch_tools import (
    _sanitize_es_input,
)

log = get_logger("ConstitutionMaxim")


# --- Elasticsearch Retrieval ---

def _retrieve_from_es(task: str, query: str) -> list[Document]:
    """Retrieve documents from Elasticsearch for Constitution or Maxim queries.

    Uses BM25 full-text search with boosted match_phrase for precision.
    """
    es = get_es_client()
    query_text = _sanitize_es_input(query)

    if task == "Constitution":
        index = ES_INDICES.get("constitution", "constitution")
        body = {
            "query": {
                "bool": {
                    "should": [
                        {"match": {"page_content": {"query": query_text, "boost": 2.0}}},
                        {"match_phrase": {"page_content": {"query": query_text, "boost": 3.0}}},
                        {"match": {"article_name": {"query": query_text, "boost": 2.5}}},
                        {"match": {"part_name": {"query": query_text, "boost": 1.0}}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "size": 10,
        }
    else:  # Maxim
        index = ES_INDICES.get("maxims", "legal_maxims")
        body = {
            "query": {
                "bool": {
                    "should": [
                        {"match": {"page_content": {"query": query_text, "boost": 2.0}}},
                        {"match_phrase": {"page_content": {"query": query_text, "boost": 3.0}}},
                        {"match": {"maxim_name": {"query": query_text, "boost": 5.0}}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "size": 10,
        }

    try:
        with log_time(log, "ES retrieval", task=task):
            result = es.search(index=index, body=body)

        hits = result["hits"]["hits"]
        if not hits:
            log.warning("ES returned no results", task=task, query=query_text[:80])
            return []

        docs = []
        for hit in hits:
            src = hit["_source"]
            metadata = {"source": src.get("source", ""), "score": hit["_score"]}
            if task == "Constitution":
                metadata["article_number"] = src.get("article_number", "")
                metadata["article_name"] = src.get("article_name", "")
                metadata["part_name"] = src.get("part_name", "")
            else:
                metadata["maxim_name"] = src.get("maxim_name", "")
            docs.append(Document(page_content=src.get("page_content", ""), metadata=metadata))

        log.info("ES retrieval done", task=task, result_count=len(docs),
                 top_score=hits[0]["_score"])
        return docs

    except Exception as e:
        log.error("ES retrieval failed", task=task, error=str(e), exc_info=True)
        return []


# --- Task Handlers ---

async def _handle_legal_concepts(query: str, chat_history: list) -> AgentResult:
    """Handle Legal_Concepts task — web-grounded AI response for comprehensive coverage."""
    from core.agent_fallback import web_search_fallback
    log.info("Legal concepts using web search for comprehensive response")
    result = await web_search_fallback(query, "Legal_Concepts", LEGAL_CONCEPTS_PROMPT)
    return result


async def _handle_constitution_or_maxim(task: str, query: str, chat_history: list) -> AgentResult:
    """Handle Constitution or Maxim task — ES retrieval + web enrichment + LLM generation."""
    system_prompt = (
        CONSTITUTION_SYSTEM_PROMPT if task == "Constitution"
        else MAXIM_SYSTEM_PROMPT
    )

    # Run ES retrieval and web enrichment in parallel
    from core.agent_fallback import get_web_context
    with log_time(log, "ES + web enrichment (parallel)", task=task):
        docs, web_context = await asyncio.gather(
            asyncio.to_thread(_retrieve_from_es, task, query),
            get_web_context(query, task),
        )

    if not docs:
        log.warning("No documents found in ES, using web search fallback", task=task)
        from core.agent_fallback import web_search_fallback
        result = await web_search_fallback(query, task, system_prompt)
        return result

    docs_text = "\n\n".join(d.page_content for d in docs)
    source_name = docs[0].metadata.get("source", "unknown")

    # Combine ES text with web context for richer input
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
    """Handle Constitution queries — ES retrieval + LLM fallback.

    Runs as its own graph node so it executes in parallel with maxim_node.
    """
    agent_queries = state.get("agent_queries", {})
    query = agent_queries.get("Constitution", state.get("query", state["original_query"]))
    chat_history = state.get("chat_history", [])
    log.info("Constitution agent started", query=query[:100],
             using_agent_query="Constitution" in agent_queries)

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
    """Handle Maxim queries — ES retrieval + LLM fallback.

    Runs as its own graph node so it executes in parallel with constitution_node.
    """
    agent_queries = state.get("agent_queries", {})
    query = agent_queries.get("Maxim", state.get("query", state["original_query"]))
    chat_history = state.get("chat_history", [])
    log.info("Maxim agent started", query=query[:100],
             using_agent_query="Maxim" in agent_queries)

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
    agent_queries = state.get("agent_queries", {})
    query = agent_queries.get("Legal_Concepts", state.get("query", state["original_query"]))
    chat_history = state.get("chat_history", [])
    log.info("Legal Concepts agent started", query=query[:100],
             using_agent_query="Legal_Concepts" in agent_queries)

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
