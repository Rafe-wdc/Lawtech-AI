"""Agent #3 — Memory Agent

Manages conversation state across turns:
- Expands legal abbreviations
- Loads chat history from external API
- Rewrites follow-up queries into standalone queries

Uses: Gemini 2.5 Flash Lite for query rewriting.
"""

from __future__ import annotations

import json
import requests
from typing import Union

from langchain.messages import HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate

from core.state import LegalAgentState
from core.clients import get_gemini_flash
from core.settings import LAWTTORNEY_API_BASE
from core.logger import get_logger, log_time
from tools.inline.abbreviation import expand_abbreviations

log = get_logger("Memory")


# --- Chat History Loading ---

def _load_chat_history_from_api(thread_id: str) -> tuple[list, str]:
    """Fetch chat history from external lawttorney.ai API.

    Returns: (chat_history as HumanMessage/AIMessage list, raw summary_text)
    """
    chat_history = []
    summary_text = ""

    try:
        url = f"{LAWTTORNEY_API_BASE}/users/getChatSummary/{thread_id}"
        with log_time(log, "Chat history API call", thread_id=thread_id):
            response = requests.get(url, timeout=10)

        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("status") and res_json.get("data"):
                summary_text = res_json["data"].get("chatSummary", "").strip()
                log.debug("Chat summary received",
                          thread_id=thread_id,
                          summary_len=len(summary_text))
        else:
            log.warning("Chat history API returned non-200",
                        status_code=response.status_code,
                        thread_id=thread_id)
    except Exception as e:
        log.error("Error fetching chat history",
                  thread_id=thread_id, error=str(e))

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

REWRITE_PROMPT = """You are a legal query rewriting assistant. Rewrite the user's latest query into a standalone, self-contained query.

Rules:
1. Must be understandable WITHOUT the conversation history.
2. Preserve all legal specificity: section numbers, act names, party names, dates.
3. If the user refers to something from the conversation (e.g., "that section", "the same act"), resolve using history.
4. If already standalone, return as-is.
5. Do NOT answer the query. Only rewrite it.
6. Keep concise — a search query, not a paragraph.
7. Return ONLY the rewritten query text.

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
        # Skip if no meaningful history
        if not chat_history or len(chat_history) <= 2:
            log.debug("Skipping rewrite — no meaningful history",
                      history_len=len(chat_history))
            return query

        # Skip placeholder history
        if (
            len(chat_history) == 2
            and isinstance(chat_history[1], AIMessage)
            and "Fresh chat started" in chat_history[1].content
        ):
            log.debug("Skipping rewrite — fresh chat placeholder")
            return query

        # Format history as text
        history_lines = []
        for msg in chat_history:
            if isinstance(msg, HumanMessage):
                history_lines.append(f"User: {msg.content}")
            elif isinstance(msg, AIMessage):
                content = msg.content[:500] + "..." if len(msg.content) > 500 else msg.content
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
    2. If thread_id provided → load chat history from lawttorney.ai API
    3. Rewrite query with conversation context if follow-up
    4. Return processed query + chat history
    """
    original_query = state["original_query"]
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
        log.debug("Loading chat history", thread_id=thread_id)
        chat_history, summary_text = _load_chat_history_from_api(thread_id)
        log.info("Chat history loaded",
                 messages=len(chat_history),
                 has_summary=bool(summary_text))

        # Step 3: Rewrite query with context
        query = _rewrite_query(query, chat_history)

    log.info("Agent completed",
             final_query=query[:100],
             query_changed=query != original_query,
             history_messages=len(chat_history))

    return {
        "query": query,
        "chat_history": chat_history,
        "summary_text": summary_text,
    }
