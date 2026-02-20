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
from tools.inline.abbreviation import expand_abbreviations


# --- Chat History Loading ---

def _load_chat_history_from_api(thread_id: str) -> tuple[list, str]:
    """Fetch chat history from external lawttorney.ai API.

    Returns: (chat_history as HumanMessage/AIMessage list, raw summary_text)
    """
    chat_history = []
    summary_text = ""

    try:
        url = f"{LAWTTORNEY_API_BASE}/users/getChatSummary/{thread_id}"
        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("status") and res_json.get("data"):
                summary_text = res_json["data"].get("chatSummary", "").strip()
    except Exception as e:
        print(f"[Memory] Error fetching chat history for thread {thread_id}: {e}")

    if summary_text:
        try:
            parsed = json.loads(summary_text)
            if isinstance(parsed, list):
                for turn in parsed[-5:]:
                    chat_history.append(HumanMessage(content=turn.get("user", "")))
                    chat_history.append(AIMessage(content=turn.get("ai", "")))
            else:
                chat_history.append(HumanMessage(content="Previous summary:"))
                chat_history.append(AIMessage(content=summary_text))
        except json.JSONDecodeError:
            chat_history.append(HumanMessage(content="Previous summary:"))
            chat_history.append(AIMessage(content=summary_text))
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
            return query

        # Skip placeholder history
        if (
            len(chat_history) == 2
            and isinstance(chat_history[1], AIMessage)
            and "Fresh chat started" in chat_history[1].content
        ):
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

        llm = get_gemini_flash(temperature=0.1)
        prompt = ChatPromptTemplate.from_template(REWRITE_PROMPT)
        chain = prompt | llm

        response = chain.invoke({"query": query, "chat_history_text": chat_history_text})
        rewritten = response.content.strip()

        if not rewritten or len(rewritten) > 1000:
            return query

        print(f"[Memory] Query rewritten: '{query[:50]}' -> '{rewritten[:50]}'")
        return rewritten

    except Exception as e:
        print(f"[Memory] Rewrite failed, using original: {e}")
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

    # Step 1: Expand abbreviations
    query = expand_abbreviations(original_query)
    if query != original_query:
        print(f"[Memory] Abbreviations expanded: '{original_query[:50]}' -> '{query[:50]}'")

    # Step 2: Load chat history if thread exists
    chat_history = []
    summary_text = ""

    if thread_id:
        print(f"[Memory] Loading history for thread: {thread_id}")
        chat_history, summary_text = _load_chat_history_from_api(thread_id)
        print(f"[Memory] Loaded {len(chat_history)} messages")

        # Step 3: Rewrite query with context
        query = _rewrite_query(query, chat_history)

    return {
        "query": query,
        "chat_history": chat_history,
        "summary_text": summary_text,
    }
