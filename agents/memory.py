"""Agent #3 — Memory Agent

Manages conversation state across turns:
- Expands legal abbreviations
- Loads chat history from local SQLite store (with legacy API fallback)
- Rewrites follow-up queries into standalone queries

Uses: Gemini 2.5 Flash Lite for query rewriting.
"""

from __future__ import annotations

import asyncio
import json
import requests
from typing import Union

from langchain.messages import HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate

from core.state import LegalAgentState, FileContextData
from core.clients import get_gemini_flash
from core.chat_store import chat_store
from core.settings import LAWTTORNEY_API_BASE, CHAT_HISTORY_USE_LEGACY_API
from core.logger import get_logger, log_time
from tools.inline.abbreviation import expand_abbreviations

log = get_logger("Memory")


# --- Chat History Loading (SQLite-first with legacy fallback) ---

async def _load_chat_history(thread_id: str) -> tuple[list, str]:
    """Load chat history from SQLite, with optional legacy API fallback.

    Returns: (chat_history as HumanMessage/AIMessage list, summary_text str)
    """
    # Step 1: Try SQLite (fast, local)
    with log_time(log, "SQLite history load", thread_id=thread_id):
        result = await chat_store.load_history(thread_id, max_recent_turns=5)

    if result.total_turns > 0:
        log.info("Chat history loaded from SQLite",
                 thread_id=thread_id[:12],
                 turns=result.total_turns,
                 messages=len(result.chat_history),
                 has_summary=bool(result.summary_text))
        return result.chat_history, result.summary_text

    # Step 2: If SQLite empty and legacy API enabled, try external API
    if CHAT_HISTORY_USE_LEGACY_API:
        log.debug("SQLite empty, trying legacy API", thread_id=thread_id[:12])
        chat_history, summary_text = await asyncio.to_thread(
            _load_from_legacy_api, thread_id
        )

        # If API had real data, import into SQLite for future use
        if summary_text and "Fresh chat started" not in summary_text:
            try:
                await chat_store.import_from_api_response(thread_id, summary_text)
                log.info("Legacy history imported to SQLite",
                         thread_id=thread_id[:12])
            except Exception as e:
                log.error("Failed to import legacy history",
                          thread_id=thread_id[:12], error=str(e))

        return chat_history, summary_text

    # Step 3: No history found — return fresh-chat placeholder
    log.debug("No history found", thread_id=thread_id[:12])
    return [
        HumanMessage(content="Previous summary:"),
        AIMessage(content="Fresh chat started."),
    ], ""


def _load_from_legacy_api(thread_id: str) -> tuple[list, str]:
    """Fetch chat history from external lawttorney.ai API (legacy fallback).

    Returns: (chat_history as HumanMessage/AIMessage list, raw summary_text)
    """
    chat_history = []
    summary_text = ""

    try:
        url = f"{LAWTTORNEY_API_BASE}/users/getChatSummary/{thread_id}"
        with log_time(log, "Legacy API call", thread_id=thread_id):
            response = requests.get(url, timeout=10)

        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("status") and res_json.get("data"):
                summary_text = res_json["data"].get("chatSummary", "").strip()
                log.debug("Legacy API summary received",
                          thread_id=thread_id[:12],
                          summary_len=len(summary_text))
        else:
            log.warning("Legacy API returned non-200",
                        status_code=response.status_code,
                        thread_id=thread_id[:12])
    except Exception as e:
        log.error("Legacy API fetch failed",
                  thread_id=thread_id[:12], error=str(e))

    if summary_text:
        try:
            parsed = json.loads(summary_text)
            if isinstance(parsed, list):
                for turn in parsed[-5:]:
                    chat_history.append(HumanMessage(content=turn.get("user", "")))
                    chat_history.append(AIMessage(content=turn.get("ai", "")))
                log.debug("Chat history parsed as list",
                          turns=len(parsed), used=min(5, len(parsed)))
            else:
                chat_history.append(HumanMessage(content="Previous summary:"))
                chat_history.append(AIMessage(content=summary_text))
        except json.JSONDecodeError:
            chat_history.append(HumanMessage(content="Previous summary:"))
            chat_history.append(AIMessage(content=summary_text))
            log.debug("Chat history is raw text, not JSON")
    else:
        chat_history.append(HumanMessage(content="Previous summary:"))
        chat_history.append(AIMessage(content="Fresh chat started."))

    return chat_history, summary_text


# --- Query Rewriting ---

REWRITE_PROMPT = """You are a legal query rewriting assistant. Rewrite the user's latest query into a standalone, self-contained query that incorporates relevant context from the conversation history.

Rules:
1. The rewritten query must be understandable WITHOUT the conversation history.
2. Preserve all legal specificity: section numbers, act names, party names, dates.
3. If the user refers to something from the conversation (e.g., "that section", "the same act", "find cases on this", "related cases"), resolve the reference using the conversation history.
4. IMPORTANT: If the latest query is broad or generic (e.g., "find related cases", "what about supreme court cases", "more details"), it is almost certainly a follow-up. You MUST incorporate the specific topic/section/act from the conversation history into the rewritten query.
5. Only return the query as-is if it is BOTH grammatically standalone AND contains specific legal terms that need no context.
6. Do NOT answer the query. Only rewrite it.
7. Keep concise — a search query, not a paragraph.
8. Return ONLY the rewritten query text.

Examples:
- History: "User asked about Section 35 of BNS" + Query: "find relevant cases from supreme court" → "Supreme Court cases on right of private defence under Section 35 of Bharatiya Nyaya Sanhita 2023"
- History: "User asked about anticipatory bail" + Query: "what does the law say" → "Legal provisions on anticipatory bail"
- History: "User asked about Section 498A IPC" + Query: "related judgments" → "Supreme Court judgments on Section 498A IPC cruelty and dowry"

Conversation History:
{chat_history_text}

Latest User Query: {query}

Rewritten Standalone Query:"""


def _rewrite_query(
    query: str,
    chat_history: list[Union[HumanMessage, AIMessage]],
) -> str:
    """Rewrite a follow-up query into a standalone query using conversation context."""
    try:
        # Skip if no history at all
        if not chat_history:
            log.debug("Skipping rewrite — no chat history")
            return query

        # Skip placeholder history (fresh chat with no real context)
        if (
            len(chat_history) == 2
            and isinstance(chat_history[1], AIMessage)
            and "Fresh chat started" in chat_history[1].content
        ):
            log.debug("Skipping rewrite — fresh chat placeholder")
            return query

        # Skip if only 2 messages and the summary is trivially short
        if len(chat_history) <= 2:
            summary_content = ""
            for msg in chat_history:
                if isinstance(msg, AIMessage):
                    summary_content = msg.content
            if len(summary_content) < 20:
                log.debug("Skipping rewrite — summary too short",
                          history_len=len(chat_history),
                          summary_len=len(summary_content))
                return query
            log.debug("Summary has context, proceeding with rewrite",
                      summary_len=len(summary_content))

        # Format history as text
        history_lines = []
        for msg in chat_history:
            if isinstance(msg, HumanMessage):
                history_lines.append(f"User: {msg.content}")
            elif isinstance(msg, AIMessage):
                content = (msg.content[:1500] + "...") if msg.content and len(msg.content) > 1500 else (msg.content or "")
                history_lines.append(f"Assistant: {content}")

        chat_history_text = "\n".join(history_lines)

        with log_time(log, "Query rewrite (LLM)"):
            llm = get_gemini_flash(temperature=0.1)
            prompt = ChatPromptTemplate.from_template(REWRITE_PROMPT)
            chain = prompt | llm

            response = chain.invoke({"query": query, "chat_history_text": chat_history_text})
        rewritten = response.content.strip()

        if not rewritten or len(rewritten) > 1000:
            log.debug("Rewrite result discarded",
                      reason="empty" if not rewritten else "too_long",
                      result_len=len(rewritten) if rewritten else 0)
            return query

        log.info("Query rewritten",
                 original=query[:60], rewritten=rewritten[:60])
        return rewritten

    except Exception as e:
        log.error("Rewrite failed, using original query", error=str(e))
        return query


# --- Agent Node ---

async def memory_node(state: LegalAgentState) -> dict:
    """Load chat history, expand abbreviations, rewrite query if needed.

    Flow:
    1. Expand legal abbreviations (BNS → Bharatiya Nyaya Sanhita, etc.)
    2. If thread_id provided → load chat history from SQLite (or legacy API)
    3. Rewrite query with conversation context if follow-up
    4. Return processed query + chat history
    """
    original_query = state.get("original_query", "")
    thread_id = state.get("thread_id")

    log.info("Agent started",
             query=original_query[:100], thread_id=thread_id or "none")

    # Step 1: Expand abbreviations
    query = expand_abbreviations(original_query)
    if query != original_query:
        log.info("Abbreviations expanded",
                 original=original_query[:60], expanded=query[:60])

    # Step 2: Load chat history if thread exists
    chat_history = []
    summary_text = ""

    if thread_id:
        log.debug("Loading chat history", thread_id=thread_id[:12])
        chat_history, summary_text = await _load_chat_history(thread_id)
        log.info("Chat history loaded",
                 messages=len(chat_history),
                 has_summary=bool(summary_text))

        # Step 3: Rewrite query with context (run sync LLM call in thread)
        # Skip rewrite when files are attached — the query is about the file, not a follow-up
        fc = FileContextData.from_state(state)
        if fc and fc.has_content:
            log.info("Skipping query rewrite — files attached",
                     file_names=fc.file_names)
        else:
            query = await asyncio.to_thread(_rewrite_query, query, chat_history)

    log.info("Agent completed",
             final_query=query[:100],
             query_changed=query != original_query,
             history_messages=len(chat_history))

    return {
        "query": query,
        "chat_history": chat_history,
        "summary_text": summary_text,
    }
