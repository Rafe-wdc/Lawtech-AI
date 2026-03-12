"""Agent #3 — Memory Agent

Manages conversation state across turns:
- Expands legal abbreviations
- Loads chat history from local SQLite store
- Rewrites follow-up queries into standalone queries

Uses: Gemini 2.5 Flash Lite for query rewriting.
"""

from __future__ import annotations

import asyncio
import os
from typing import Union

from langchain.messages import HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate

from core.state import LegalAgentState, FileContextData
from core.clients import get_gemini_flash
from core.chat_store import chat_store
from core.language import detect_language
from core.logger import get_logger, log_time
from tools.inline.abbreviation import expand_abbreviations

log = get_logger("Memory")


# --- Chat History Loading ---

async def _load_chat_history(thread_id: str) -> tuple[list, str]:
    """Load chat history from SQLite.

    Returns: (chat_history as HumanMessage/AIMessage list, summary_text str)
    """
    with log_time(log, "SQLite history load", thread_id=thread_id):
        result = await chat_store.load_history(thread_id, max_recent_turns=5)

    if result.total_turns > 0:
        log.info("Chat history loaded from SQLite",
                 thread_id=thread_id[:12],
                 turns=result.total_turns,
                 messages=len(result.chat_history),
                 has_summary=bool(result.summary_text))
        return result.chat_history, result.summary_text

    log.debug("No history found", thread_id=thread_id[:12])
    return [
        HumanMessage(content="Previous summary:"),
        AIMessage(content="Fresh chat started."),
    ], ""


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

        # Format history as text — cap at last 10 messages to stay within token budget
        history_lines = []
        for msg in chat_history[-10:]:
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


# --- File Context Restoration ---

async def _restore_file_context(thread_id: str) -> dict | None:
    """Load thread files from SQLite, re-upload any expired Gemini URIs, and
    reconstruct a FileContext dict for injection into state.

    - Gemini URI valid  → use as-is (no upload)
    - Gemini URI expired → re-upload from local_path, update SQLite
    - DOCX / XLSX       → use extracted_text directly
    - Large PDFs        → restore chromadb_collection ID
    - Missing local file → log warning, skip
    """
    from core.gemini_files import is_uri_valid, upload_to_gemini

    thread_files = await chat_store.load_thread_files(thread_id)
    if not thread_files:
        return None

    gemini_file_parts: list[dict] = []
    chromadb_collections: list[str] = []
    inline_parts: list[str] = []
    file_names: list[str] = []

    for record in thread_files:
        if record.get("upload_error") and not record.get("local_path"):
            continue  # permanently failed, skip

        filename = record.get("filename", "")
        file_names.append(filename)

        # Restore Gemini-hosted files (images, PDFs, TXT, CSV, MD)
        if record.get("gemini_supported"):
            uri = record.get("gemini_uri", "")
            expiry = record.get("gemini_expiry", "")
            mime = record.get("mime_type", "")

            if uri and is_uri_valid(expiry):
                # URI still good — use directly
                gemini_file_parts.append({
                    "file_data": {"file_uri": uri, "mime_type": mime},
                    "name": filename,
                })
            else:
                # Expired — re-upload from local storage
                local_path = record.get("local_path", "")
                if local_path and os.path.exists(local_path):
                    try:
                        new_uri, new_name, new_expiry = await asyncio.to_thread(
                            upload_to_gemini, local_path, mime, filename,
                        )
                        await chat_store.update_gemini_uri(
                            thread_id, record["file_id"],
                            new_uri, new_name, new_expiry,
                        )
                        gemini_file_parts.append({
                            "file_data": {"file_uri": new_uri, "mime_type": mime},
                            "name": filename,
                        })
                        log.info("Re-uploaded expired Gemini file",
                                 file=filename, thread=thread_id[:12])
                    except Exception as e:
                        log.error("Failed to re-upload file",
                                  file=filename, error=str(e))
                else:
                    log.warning("Local file missing, cannot re-upload",
                                file=filename, path=local_path)

        # Restore ChromaDB collections for large PDFs
        coll = record.get("chromadb_collection", "")
        if coll and coll not in chromadb_collections:
            chromadb_collections.append(coll)

        # Restore extracted text (DOCX, XLSX, CSV fallback)
        text = record.get("extracted_text", "")
        if text:
            inline_parts.append(f"[File: {filename}]\n{text}")

    if not (gemini_file_parts or chromadb_collections or inline_parts):
        return None

    combined = "\n\n---\n\n".join(inline_parts)
    if len(combined) > 100_000:
        combined = combined[:100_000] + "\n\n[... truncated]"

    return {
        "inline_text": combined,
        "gemini_file_parts": gemini_file_parts,
        "chromadb_collections": chromadb_collections,
        "summary": f"Restored {len(file_names)} file(s) from thread history",
        "file_names": file_names,
    }


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

    # Step 1: Determine language — respect explicit client override, else auto-detect
    gateway_lang = state.get("user_language", "")
    if gateway_lang:
        user_language = gateway_lang
        log.info("Language: using client preference", lang=user_language)
    else:
        user_language = detect_language(original_query)
        log.info("Language detected", lang=user_language, query=original_query[:60])

    # Step 2: Expand abbreviations
    query = expand_abbreviations(original_query)
    if query != original_query:
        log.info("Abbreviations expanded",
                 original=original_query[:60], expanded=query[:60])

    # Step 3: Load chat history if thread exists
    chat_history = []
    summary_text = ""
    restored_file_context: dict | None = None

    if thread_id:
        log.debug("Loading chat history", thread_id=thread_id[:12])
        chat_history, summary_text = await _load_chat_history(thread_id)
        log.info("Chat history loaded",
                 messages=len(chat_history),
                 has_summary=bool(summary_text))

        # Step 4: Check file context — attached this turn OR restore from thread history
        fc = FileContextData.from_state(state)

        if fc and fc.has_content:
            # New files uploaded this turn — skip query rewrite
            log.info("Skipping query rewrite — files attached this turn",
                     file_names=fc.file_names)
        else:
            # No new files — restore from thread_files registry (re-uploads if expired)
            restored_file_context = await _restore_file_context(thread_id)
            if restored_file_context:
                log.info("Restored file context from thread history",
                         files=restored_file_context.get("file_names", []),
                         gemini_parts=len(restored_file_context.get("gemini_file_parts", [])),
                         chromadb=len(restored_file_context.get("chromadb_collections", [])))

            query = await asyncio.to_thread(_rewrite_query, query, chat_history)

    log.info("Agent completed",
             final_query=query[:100],
             query_changed=query != original_query,
             history_messages=len(chat_history),
             file_context_restored=restored_file_context is not None)

    result: dict = {
        "query": query,
        "chat_history": chat_history,
        "summary_text": summary_text,
        "user_language": user_language,
    }
    if restored_file_context is not None:
        result["file_context"] = restored_file_context
    return result
