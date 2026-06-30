"""Shared Tools: Memory and conversation management operations.

Reusable @tool functions for query rewriting, conversation summarization,
follow-up detection, and chat history storage.

Used by the Memory Agent (#3) for conversation state management.

Uses: Gemini Flash Lite for query rewriting, GPT-4o for summarization
"""

from __future__ import annotations

import json
import requests
from typing import Union

from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate

from core.clients import get_gemini_flash
from core.logger import get_logger

log = get_logger("MemoryTools")


# --- Prompts ---

_REWRITE_PROMPT = """You are a legal query rewriting assistant. Rewrite the user's latest query into a standalone, self-contained query.

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


_SUMMARIZE_PROMPT = """You are a legal-assistant summarizer.

Given a detailed multi-turn conversation related to legal queries,
summarize the chat with a focus on LEGAL DIRECTION (not detail).
This V1 5-section structure was used in production for thousands of
users; do not deviate.

## Guidelines

- Keep summary between 100 and 200 words.
- Maintain clarity; avoid overly deep legal analysis.
- Structure in 5 standard sections + optional 6th if drafting is
  involved.
- If a section has no content, write "None mentioned".

## Format

1. **Topics Discussed**
   - Key legal subjects or disputes discussed.

2. **Legal References**
   - Statutes, sections, amendments, case law.

3. **Named Entities**
   - Persons, courts, institutions, estates.

4. **Dates or Timelines**
   - Year of law enactment, amendment, ruling, or material dates.

5. **Key Legal Insights or Advice**
   - Core legal direction or implications.

6. **Draft Metadata** (include only if drafting was involved)
   - Draft Type:
   - Personal Info Supplied:
   - Requested Edits:

Conversation:
{conversation}

Summary:"""


_FOLLOWUP_INDICATORS = [
    "this", "that", "these", "those", "it", "its",
    "the same", "above", "mentioned", "said", "previous",
    "earlier", "what about", "how about", "and also",
    "in addition", "furthermore", "moreover", "similarly",
    "regarding", "concerning", "as for", "about the",
]


# --- Tool Functions ---

@tool
def rewrite_query(query: str, chat_history_text: str) -> str:
    """Rewrite a follow-up query into a standalone query using conversation context.

    Resolves pronoun references ("that section", "the same act") using
    the conversation history. Returns the original query unchanged if
    it's already standalone or if rewriting fails.

    Args:
        query: The user's latest query that may reference previous conversation
        chat_history_text: Formatted conversation history (e.g. "User: ...\\nAssistant: ...")

    Returns:
        The rewritten standalone query, or the original query if no rewriting needed
    """
    if not chat_history_text or not chat_history_text.strip():
        return query

    # Skip if history is just placeholder
    if "Fresh chat started" in chat_history_text and chat_history_text.count("\n") < 4:
        return query

    try:
        llm = get_gemini_flash(temperature=0.1)
        prompt = ChatPromptTemplate.from_template(_REWRITE_PROMPT)
        chain = prompt | llm

        response = chain.invoke({"query": query, "chat_history_text": chat_history_text})
        rewritten = response.text.strip()

        if not rewritten or len(rewritten) > 1000:
            return query

        log.info(f"[Memory] Query rewritten: '{query[:50]}' -> '{rewritten[:50]}'")
        return rewritten

    except Exception as e:
        log.warning(f"[Memory] Rewrite failed, using original: {e}")
        return query


@tool
def summarize_conversation(conversation_text: str, task: str = "General") -> str:
    """Summarize a multi-turn legal conversation preserving key details.

    For Drafting tasks, returns the latest draft verbatim.
    For other tasks, generates a structured summary covering topics,
    legal references, entities, dates, insights, and draft metadata.

    Args:
        conversation_text: The full conversation text to summarize
        task: The task type — "Drafting" preserves draft, others get summarized

    Returns:
        Structured summary text preserving legally significant details
    """
    if not conversation_text or not conversation_text.strip():
        return ""

    # For drafting, return latest draft verbatim
    if task == "Drafting":
        lines = conversation_text.strip().split("\n")
        # Find last assistant response
        last_ai_start = -1
        for i, line in enumerate(lines):
            if line.startswith("Assistant:") or line.startswith("A:"):
                last_ai_start = i
        if last_ai_start >= 0:
            return "\n".join(lines[last_ai_start:])
        return conversation_text

    try:
        llm = get_gemini_flash(temperature=0.2)
        prompt = ChatPromptTemplate.from_template(_SUMMARIZE_PROMPT)
        chain = prompt | llm

        response = chain.invoke({"conversation": conversation_text[:5000]})
        return response.text.strip()

    except Exception as e:
        log.error(f"[Memory] Summarization failed: {e}")
        return conversation_text[:2000]


@tool
def is_followup_query(query: str, chat_history_text: str) -> dict:
    """Determine if a query is a follow-up that references previous conversation.

    Uses keyword heuristics to detect pronouns, references, and continuation
    patterns that indicate the query depends on prior context.

    Args:
        query: The user's latest query
        chat_history_text: Previous conversation text (empty if new conversation)

    Returns:
        Dict with keys: is_followup (bool), indicators_found (list of matched phrases)
    """
    if not chat_history_text or not chat_history_text.strip():
        return {"is_followup": False, "indicators_found": []}

    if "Fresh chat started" in chat_history_text:
        return {"is_followup": False, "indicators_found": []}

    query_lower = query.lower()
    found = [ind for ind in _FOLLOWUP_INDICATORS if ind in query_lower]

    return {
        "is_followup": len(found) > 0,
        "indicators_found": found,
    }


@tool
def save_chat_history(
    thread_id: str,
    query: str,
    response: str,
    recent_chats: list[dict],
) -> dict:
    """Save a conversation turn to the local SQLite chat history store.

    Args:
        thread_id: The conversation thread ID
        query: The user's question
        response: The AI's response
        recent_chats: List of recent chat dicts (kept for API compatibility)

    Returns:
        Dict with keys: saved (bool), total_turns (int)
    """
    from core.chat_store import chat_store

    try:
        turn_number = chat_store._save_turn_sync(thread_id, query, response)
        return {"saved": True, "total_turns": turn_number}
    except Exception as e:
        log.error(f"[Memory] Failed to save chat history: {e}")
        return {"saved": False, "total_turns": 0}
