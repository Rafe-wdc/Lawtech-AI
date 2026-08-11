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
import re
from typing import Union

from langchain.messages import HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate

from core.state import LegalAgentState, FileContextData
from core.clients import get_gemini_flash
from core.chat_store import chat_store, ChatHistoryResult
from core.language import detect_language
from core.logger import get_logger, log_time, short_err
from core.progress import progress
from tools.inline.abbreviation import expand_abbreviations

log = get_logger("Memory")


# --- Chat History Loading ---

async def _load_chat_history(thread_id: str) -> ChatHistoryResult:
    """Load chat history + per-turn typed state from the chat store.

    Returns the full ChatHistoryResult so callers can pull both the
    text-shaped fields (chat_history, summary_text) AND the Level 1
    per-turn typed state (previous_intent_json, previous_task, etc.
    — see docs/followup_pipeline_simplification_plan.md).

    On a fresh thread returns a ChatHistoryResult with the two-message
    placeholder chat_history — the rewriter and downstream consumers
    already tolerate this shape.
    """
    with log_time(log, "SQLite history load", thread_id=thread_id):
        result = await chat_store.load_history(thread_id, max_recent_turns=5)

    if result.total_turns > 0:
        log.info("Chat history loaded from SQLite",
                 thread_id=thread_id[:12],
                 turns=result.total_turns,
                 messages=len(result.chat_history),
                 has_summary=bool(result.summary_text),
                 has_prev_intent=bool(result.previous_intent_json),
                 prev_task=result.previous_task or "(none)",
                 prev_artifact_kind=result.previous_artifact_kind or "(none)")
        return result

    log.debug("No history found", thread_id=thread_id[:12])
    return ChatHistoryResult(
        chat_history=[
            HumanMessage(content="Previous summary:"),
            AIMessage(content="Fresh chat started."),
        ],
    )


# --- Query Rewriting ---

REWRITE_PROMPT = """You are a legal query rewriting assistant. Rewrite the user's latest query into a standalone, self-contained query that incorporates relevant context from the conversation history.

Two distinct followup shapes to recognise:

  (A) SEARCH followups — short, broad references to a topic in the history
      ("find related cases", "what does the law say", "supreme court cases on
      this"). Output a concise search query that names the specific
      topic/section/act/doctrine from history. Compressed, search-engine style.

  (B) DIRECTIVE followups — the user is refining a PRIOR PRODUCTION TASK from
      the conversation. Examples: "in marathi", "in English", "in Hindi", "in
      a table", "more concise prayer", "for Magistrate Court not Sessions",
      "translate to Tamil", "मराठीत द्या", "Hindi mein", "Marathi madhe sanga".
      The user is NOT asking a new question — they are saying "do the previous
      thing again, with this change". For these:
        - Reconstruct the FULL prior request with the directive applied.
        - Preserve EVERY substantive detail from the prior turn: document
          type, parties, dates, amounts, grounds, prayer, statute references,
          facts.
        - Do NOT compress to a search query.
        - The rewritten query should READ like a complete drafting / analysis
          / generation request, the way the user originally phrased their
          first ask (with the new directive folded in).

Rules:
1. The rewritten query must be understandable WITHOUT the conversation history.
2. Preserve all legal specificity: section numbers, act names, party names, dates, amounts, grounds.
3. If the user refers to something from the conversation (e.g., "that section", "the same act", "find cases on this", "related cases"), resolve the reference using the conversation history.
4. **Branch on shape:**
   - For SEARCH followups (shape A): produce a concise search query naming the specific topic/section/act/doctrine from history.
   - For DIRECTIVE followups (shape B): produce a FULL self-contained reconstruction of the prior request with the directive applied. Length is whatever it takes — do not compress.
5. Only return the query as-is if it is BOTH grammatically standalone AND contains specific legal terms that need no context.
6. Do NOT answer the query. Only rewrite it.
7. **Length policy**: concise for SEARCH followups (a search query); full reconstruction for DIRECTIVE followups (a paragraph or more is fine).
8. Return ONLY the rewritten query text.

Examples:

SEARCH followups:
- History: "User asked about Section 35 of BNS" + Query: "find relevant cases from supreme court" → "Supreme Court cases on right of private defence under Section 35 of Bharatiya Nyaya Sanhita 2023"
- History: "User asked about anticipatory bail" + Query: "what does the law say" → "Legal provisions on anticipatory bail"
- History: "User asked about Section 498A IPC" + Query: "related judgments" → "Supreme Court judgments on Section 498A IPC cruelty and dowry"

DIRECTIVE followups (language switch):
- History: "User asked: Prepare an NDPS bail application for an accused in judicial custody for 7 months under Section 20(b) NDPS Act on grounds of Section 42(2) non-compliance, panch contradictions, FSL delay. Assistant produced a Hindi draft."
  + Query: "In marathi"
  → "Prepare an NDPS bail application in Marathi language for an accused who has been in judicial custody for seven months under Section 20(b) of the NDPS Act, on grounds of non-compliance with Section 42(2), contradictions in panch witness statements, and delay in sending samples to the Forensic Science Laboratory. Maintain all the same facts, grounds, and prayer structure as previously discussed."

- History: "User asked: Prepare an NDPS bail application... Assistant produced a Marathi draft on the previous turn."
  + Query: "in English"
  → "Prepare the same NDPS bail application in English language for an accused in judicial custody for seven months under Section 20(b) of the NDPS Act, on the same grounds discussed (Section 42(2) non-compliance, panch witness contradictions, FSL sample delay) and the same prayer structure as previously discussed."

DIRECTIVE followups (format / scope):
- History: "User asked to compare Section 130 and Section 131 of Indian Evidence Act."
  + Query: "as a table"
  → "Compare Section 130 and Section 131 of the Indian Evidence Act in a markdown table format."

- History: "User asked for a partition suit plaint between two brothers over an ancestral property in Pune."
  + Query: "make the prayer more concise"
  → "Prepare the same partition suit plaint between the two brothers over the ancestral property in Pune, with a more concise prayer paragraph but all other content (cause title, parties, facts, grounds) unchanged."

Conversation History:
{chat_history_text}

Latest User Query: {query}

Rewritten Standalone Query:"""


def _rewrite_query(
    query: str,
    chat_history: list[Union[HumanMessage, AIMessage]],
    previous_task: str = "",
    previous_artifact_kind: str = "",
) -> str:
    """Rewrite a follow-up query into a standalone query using conversation context.

    `previous_task` and `previous_artifact_kind` (PR 3b of the follow-up
    simplification, docs/followup_pipeline_simplification_plan.md) let the
    rewriter SKIP itself when Turn N is a short directive on a prior drafting
    turn — the drafting fast-path (Level 2) needs the raw "in Marathi"
    directive intact, and the rewriter would otherwise expand it into a
    300+ char query that the fast-path detector then rejects.
    """
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

        # PR 3b: preserve short directive follow-ups on prior drafts.
        # When the previous turn was Drafting and produced a modifiable
        # artefact, AND the current query is a short directive (< 200
        # chars, matching the same verb regex the drafting fast-path uses),
        # keep the raw query so the fast-path sees "in Marathi" intact.
        #
        # CRITICAL: only fire this skip WHEN THE FAST-PATH IS ENABLED. With
        # the fast-path OFF the raw "in Marathi" query flows into the full
        # drafting pipeline unexpanded, and the classifier + reference
        # picker interpret it as "draft a notice ABOUT Marathi" — producing
        # a confabulated notice unrelated to the prior draft (regression
        # observed on prod smoke 2026-08-12: 'in Marathi' after a cheque
        # dishonour draft returned a notice to a Marathi-language
        # university's Vice-Chancellor). When the fast-path is off, we NEED
        # the rewriter's chat-history expansion so downstream retrieval sees
        # the actual matter, not the bare directive.
        from agents.drafting import _fast_path_enabled, _DIRECTIVE_VERBS_RE
        if (
            _fast_path_enabled()
            and previous_task == "Drafting"
            and previous_artifact_kind == "draft"
            and query
            and len(query) < 200
            and _DIRECTIVE_VERBS_RE.search(query)
        ):
            log.info(
                "Skipping rewrite — short directive on prior drafting turn (fast-path ON)",
                query_preview=query[:80],
            )
            return query

        # Skip when the query is already long enough to be standalone.
        # The REWRITE_PROMPT is tuned for SHORT follow-ups ("find related
        # cases", "what does the law say"); its rule "Keep concise — a
        # search query, not a paragraph" compresses fact-rich drafting
        # prompts (party names, dates, amounts, itemised lists) into a
        # generic search query, and downstream agents then fabricate
        # substitutes from template examples. Per feedback_preserve_user_query:
        # never lose information from the user's prompt anywhere in the
        # pipeline. A standalone query needs no rewrite; an existing thread
        # is irrelevant when the user wrote a self-contained ask.
        if len(query) >= 500:
            log.debug("Skipping rewrite — query already standalone-length",
                      query_chars=len(query))
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
        from core.token_tracker import record as _record_tokens
        _record_tokens("Memory", "rewrite_query", response)
        rewritten = response.text.strip()

        # Empty output is the only thing we can't recover from. The previous
        # hardcoded length cap (1000 → bumped to 5000 → removed on 2026-06-30)
        # was killing directive-followup rewrites that legitimately need to
        # reconstruct the prior task in full. The provenance check below is
        # the real safety net — it catches hallucinated legal anchors, which
        # is the failure mode that actually matters.
        if not rewritten:
            log.debug("Rewrite result discarded — empty output")
            return query

        # Provenance check — every specific legal anchor (section number,
        # article number, named act) introduced by the rewrite MUST appear
        # in either the chat history or the latest query. Otherwise the
        # rewrite invented a fact and would silently mis-route retrieval
        # to a wrong section/act. Heuristic: extract bare-token anchors
        # from the rewritten output and confirm presence in the corpus.
        if not _rewrite_anchors_supported(query, rewritten, chat_history_text):
            log.warning(
                "Rewrite introduced unsupported legal anchors; "
                "falling back to original query",
                original=query[:80], rewritten=rewritten[:120],
            )
            return query

        log.info("Query rewritten",
                 original=query[:60], rewritten=rewritten[:60])
        return rewritten

    except Exception as e:
        log.error("Rewrite failed, using original query", error=short_err(e))
        return query


# Regexes for the provenance check — bounded to the specific identifier
# patterns that, when wrong, change retrieval. We don't try to catch
# every possible legal anchor — only the highest-impact ones (section
# numbers, article numbers, act-shorthand acronyms).
_PROVENANCE_SECTION_RE = re.compile(
    r"\bsection\s*([0-9]+[A-Z]?(?:\([0-9]+\))?)", flags=re.IGNORECASE,
)
_PROVENANCE_ARTICLE_RE = re.compile(
    r"\barticle\s*([0-9]+[A-Z]?)", flags=re.IGNORECASE,
)
_PROVENANCE_ACT_ACRONYMS = (
    "ipc", "crpc", "iea", "bns", "bnss", "bsa", "ni act", "hama", "hma",
    "hsa", "cpc", "sra", "ibc", "cgst", "sgst", "igst",
)


def _rewrite_anchors_supported(
    original_query: str, rewritten_query: str, chat_history_text: str,
) -> bool:
    """True iff every specific legal anchor in the rewritten query also
    appears in either the original query or the chat history.

    Catches the failure mode where the LLM rewriter, given drifted chat
    history, substitutes a wrong section/article/act into the rewrite
    (e.g. history mentioned Section 138 NI Act but the rewriter wrote
    "cases on Section 188 IPC"). Any specific identifier that the
    rewrite added without provenance fails the check, and the caller
    falls back to the original query.

    Returns True when nothing was added (rewrite is a paraphrase) OR
    every added anchor is provenance-supported.
    """
    corpus = (original_query.lower() + " " + chat_history_text.lower())
    rewritten_lower = rewritten_query.lower()

    # Sections
    for m in _PROVENANCE_SECTION_RE.finditer(rewritten_lower):
        sec = m.group(1).lower()
        # Check if "section <num>" or just "<num>" with section keyword
        # appears in the corpus.
        if f"section {sec}" not in corpus and f"sec {sec}" not in corpus and f"sec. {sec}" not in corpus:
            return False

    # Articles
    for m in _PROVENANCE_ARTICLE_RE.finditer(rewritten_lower):
        art = m.group(1).lower()
        if f"article {art}" not in corpus and f"art. {art}" not in corpus and f"art {art}" not in corpus:
            return False

    # Act acronyms — case-sensitive on a word boundary in the rewrite,
    # case-insensitive lookup in the corpus.
    for acronym in _PROVENANCE_ACT_ACRONYMS:
        # Word-boundary check on the rewrite (avoid 'sra' matching 'sra' in 'asra')
        if re.search(r"\b" + re.escape(acronym) + r"\b", rewritten_lower):
            if acronym not in corpus:
                return False

    return True


# --- File Context Restoration ---

async def _restore_file_context(thread_id: str) -> dict | None:
    """Load thread files from SQLite, re-upload any expired Gemini URIs, and
    reconstruct a FileContext dict for injection into state.

    IMPORTANT: Only restores files from the MOST RECENT upload batch,
    not all historical files. This prevents old file context from bleeding
    into new turns when the user uploads a different file on the same thread.

    - Gemini URI valid  → use as-is (no upload)
    - Gemini URI expired → re-upload from local_path, update SQLite
    Phase F (2026-06-28): single text pipeline. Only chromadb_collection
    IDs are restored across turns; agents read content via
    get_full_attachment(collection_id).
    """
    all_thread_files = await chat_store.load_thread_files(thread_id)
    if not all_thread_files:
        return None

    # Only use files from the MOST RECENT upload batch.
    # Files uploaded in the same turn share the same created_at timestamp
    # (within a few seconds). We use the latest file's timestamp and include
    # all files within 60 seconds of it (covers multi-file uploads).
    latest_ts = all_thread_files[-1].get("created_at", "")  # already sorted ASC
    thread_files = []
    for record in reversed(all_thread_files):
        ts = record.get("created_at", "")
        if ts and latest_ts:
            # Simple comparison — both are ISO datetime strings
            # Include files from the same batch (within ~10s of latest)
            try:
                from datetime import datetime
                t_latest = datetime.fromisoformat(latest_ts.replace("Z", "+00:00"))
                t_this = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if abs((t_latest - t_this).total_seconds()) <= 10:
                    thread_files.append(record)
                else:
                    break  # older batch, stop
            except (ValueError, TypeError):
                thread_files.append(record)  # can't parse, include
        else:
            thread_files.append(record)

    thread_files.reverse()  # restore chronological order

    if not thread_files:
        return None

    log.info("Restoring file context (latest batch only)",
             total_thread_files=len(all_thread_files),
             latest_batch_files=len(thread_files),
             latest_ts=latest_ts)

    # Phase F (RAG attachment routing plan, 2026-06-28): only
    # chromadb_collections survive across turns. The Gemini Files URI
    # channel (re-upload on expiry) was removed; agents read attachment
    # content via get_full_attachment(collection_id) on demand.
    chromadb_collections: list[str] = []
    file_names: list[str] = []

    for record in thread_files:
        if record.get("upload_error") and not record.get("local_path"):
            continue  # permanently failed, skip

        filename = record.get("filename", "")
        file_names.append(filename)

        coll = record.get("chromadb_collection", "")
        if coll and coll not in chromadb_collections:
            chromadb_collections.append(coll)

    if not chromadb_collections:
        return None

    return {
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
        progress("memory", "Detecting language...", step="language")
        user_language = detect_language(original_query)
        progress("memory", f"Detected: {user_language}", substep=True, step="language")
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
    # Level 1 typed state carried from the previous turn — see
    # docs/followup_pipeline_simplification_plan.md. Empty defaults so
    # first-turn requests behave identically to today.
    prev_intent_obj = None
    prev_task = ""
    prev_artifact_kind = ""
    prev_artifact_content = ""

    if thread_id:
        progress("memory", "Loading conversation history...", step="history")
        log.debug("Loading chat history", thread_id=thread_id[:12])
        history_result = await _load_chat_history(thread_id)
        chat_history = history_result.chat_history
        summary_text = history_result.summary_text
        # Deserialise the previous turn's typed state. UserIntent is a Pydantic
        # BaseModel — the import happens lazily to avoid the state.py
        # import-cycle risk noted in core/state.py's LegalAgentState comments.
        if history_result.previous_intent_json:
            try:
                from config.intent import UserIntent
                prev_intent_obj = UserIntent.model_validate_json(
                    history_result.previous_intent_json,
                )
            except Exception as e:
                # Corrupt / schema-drifted intent in storage — degrade
                # gracefully. Consumers treat None as "no prior intent"
                # and fall through to today's behaviour.
                log.warning(
                    "Failed to deserialise previous_intent_json; treating as absent",
                    thread_id=thread_id[:12], error=short_err(e),
                    raw_preview=history_result.previous_intent_json[:120],
                )
        prev_task = history_result.previous_task
        prev_artifact_kind = history_result.previous_artifact_kind
        prev_artifact_content = history_result.previous_artifact_content
        # Compute REAL turn count: `_load_chat_history` returns a 2-message
        # placeholder ("Previous summary:" + "Fresh chat started.") for new
        # threads with no real history, and naive `len // 2` would mis-count
        # that as 1 prior turn (BUG-14). Detect and strip the placeholder.
        is_placeholder_history = (
            len(chat_history) == 2
            and isinstance(chat_history[1], AIMessage)
            and "Fresh chat started" in chat_history[1].content
        )
        turns = 0 if is_placeholder_history else len(chat_history) // 2
        progress("memory", f"Found {turns} previous turns", found=turns, substep=True, step="history")
        log.info("Chat history loaded",
                 messages=len(chat_history),
                 turns=turns,
                 placeholder_history=is_placeholder_history,
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
                         chromadb=len(restored_file_context.get("chromadb_collections", [])))

            progress("memory", "Rewriting follow-up query...", step="rewrite")
            # Bound the rewrite call: without a local ceiling, a slow
            # Gemini Flash blocks the memory step for the entire outer
            # timeout budget. On timeout, keep the original query.
            # Pass previous_task + previous_artifact_kind so the rewriter
            # can SKIP itself on short directive follow-ups after Drafting
            # — see PR 3b in _rewrite_query.
            try:
                query = await asyncio.wait_for(
                    asyncio.to_thread(
                        _rewrite_query, query, chat_history,
                        prev_task, prev_artifact_kind,
                    ),
                    timeout=15,
                )
            except asyncio.TimeoutError:
                log.warning("Rewrite timed out; using original query",
                            timeout_s=15, query=query[:80])
            if query != expand_abbreviations(original_query):
                progress("memory", f"Expanded: {query[:60]}", substep=True, detail=query[:80], step="rewrite")

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
        # Level 1: previous-turn typed state. Consumers (intent extractor,
        # rewriter, classifier, drafting fast-path) read these as optional
        # inputs — None / "" means "no prior turn to inherit from."
        "previous_intent": prev_intent_obj,
        "previous_task": prev_task,
        "previous_artifact_kind": prev_artifact_kind,
        "previous_artifact_content": prev_artifact_content,
    }
    # CRITICAL: Only set file_context if we're RESTORING from history.
    # If new files were uploaded this turn (fc.has_content), do NOT touch
    # file_context — the gateway already set it correctly in initial_state.
    # Setting it here would OVERWRITE the new file context due to
    # LangGraph's last-write-wins behavior (no custom reducer on file_context).
    fc_from_state = FileContextData.from_state(state)
    new_files_this_turn = fc_from_state and fc_from_state.has_content

    if restored_file_context is not None and not new_files_this_turn:
        result["file_context"] = restored_file_context
        log.info("Setting restored file_context (no new files this turn)")
    elif new_files_this_turn:
        log.info("Preserving new file_context from this turn (NOT overwriting)",
                 files=fc_from_state.file_names)
    return result
