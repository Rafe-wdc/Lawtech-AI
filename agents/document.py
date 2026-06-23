"""Agent #10 — Document Agent

Handles PDF upload, processing, OCR, and document-specific Q&A.
Manages per-user ChromaDB collections for uploaded documents.

3-step flow:
1. Upload & Validate → check file size, page count, format
2. Process → extract text, OCR scanned pages, chunk, embed, store
3. Chat → retrieve from user's collection, generate answer

Uses: Gemini 2.5 Pro (Q&A), Gemini Flash Lite (Vision OCR)
Data Source: ChromaDB (per-user collections in chroma_store/)
Embedding: all-MiniLM-L6-v2
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import date

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from langchain_community.vectorstores import Chroma
from core.state import LegalAgentState, AgentResult, SourceMetadata, FileContextData
from core.clients import get_gemini_pro, get_qa_embeddings, get_chroma_client
from core.settings import CHROMA_STORE_ROOT, TIMEOUT_CHROMADB_SEC
from core.language import localize_prompt, detect_source_languages
from core.logger import get_logger, log_time
from core.progress import progress
from config.intent import LegalArtifact, UserIntent
from config.prompts import (
    CROSS_EXAMINATION_PROMPT,
    DEPOSITION_SUMMARY_PROMPT,
    CONTRACT_ANALYSIS_PROMPT,
    LEGAL_NOTICE_DRAFT_PROMPT,
    COMPLAINT_DRAFT_PROMPT,
    WITNESS_PREP_PROMPT,
    OPENING_STATEMENT_PROMPT,
    CLOSING_ARGUMENT_PROMPT,
)
from core.self_refine import self_refine
log = get_logger("Document")


# ---------------------------------------------------------------------------
# Specialized-artifact dispatch.
#
# When `intent.legal_artifact != NONE`, the document agent:
#   1. Picks the specialized prompt (e.g. CROSS_EXAMINATION_PROMPT) instead
#      of the generic file-Q&A prompt.
#   2. Configures Gemini 2.5 Pro with a thinking budget (planning helps for
#      strategic legal output) and a larger output cap.
#   3. After generation, runs the dynamic self-refine loop
#      (core.self_refine.self_refine) which critiques the response against
#      the typed UserIntent and refines on violations — no hardcoded
#      thresholds or retry preambles.
#
# Adding a new artifact = add an enum + an entry in _SPECIALIZED_PROMPTS.
# No quality-gate edits needed; the critic derives the rules from the
# intent fields themselves.
# ---------------------------------------------------------------------------

# Per-artifact specialized prompt — the picker reads this map. Add a new
# entry to extend; no other code needs to change.
_SPECIALIZED_PROMPTS: dict = {
    LegalArtifact.CROSS_EXAMINATION:   CROSS_EXAMINATION_PROMPT,
    LegalArtifact.DEPOSITION_SUMMARY:  DEPOSITION_SUMMARY_PROMPT,
    LegalArtifact.CONTRACT_ANALYSIS:   CONTRACT_ANALYSIS_PROMPT,
    LegalArtifact.LEGAL_NOTICE_DRAFT:  LEGAL_NOTICE_DRAFT_PROMPT,
    LegalArtifact.COMPLAINT_DRAFT:     COMPLAINT_DRAFT_PROMPT,
    LegalArtifact.WITNESS_PREP:        WITNESS_PREP_PROMPT,
    LegalArtifact.OPENING_STATEMENT:   OPENING_STATEMENT_PROMPT,
    LegalArtifact.CLOSING_ARGUMENT:    CLOSING_ARGUMENT_PROMPT,
}

# NOTE: Removed in the self-refine cutover. The mechanical quality gates
# (`_QUALITY_THRESHOLDS`) and hardcoded per-artifact retry preambles
# (`_RETRY_PREAMBLE`) used to live here. They have been replaced with a
# dynamic LLM-driven self-refine loop — see `core.self_refine.self_refine`.
#
# The new loop reads the typed `UserIntent` (the same object that picked
# the specialized prompt below) and derives the quality checks from the
# intent fields themselves. Adding a new artifact / language / depth
# directive no longer requires touching a thresholds dict or writing a
# bespoke retry preamble — the critic figures it out.


def _pick_specialized_prompt(intent, generic_prompt: str) -> str:
    """Return the specialized prompt registered for `intent.legal_artifact`,
    or the generic prompt when intent is None / artifact is NONE / artifact
    is not in the registry.
    """
    if intent is None:
        return generic_prompt
    artifact = getattr(intent, "legal_artifact", LegalArtifact.NONE)
    return _SPECIALIZED_PROMPTS.get(artifact, generic_prompt)


def _llm_config_for_artifact(intent) -> dict:
    """Return Gemini Pro kwargs tuned for the requested artifact.

    Generic (artifact=NONE): temperature=0.3, default output budget.
    Specialized artifacts: temperature=0.4, thinking_budget=4096 (strategic
    planning helps for structured legal output), 20K max output tokens
    (long structured outputs).
    """
    if intent is None:
        return {"temperature": 0.3}
    artifact = getattr(intent, "legal_artifact", LegalArtifact.NONE)
    if artifact != LegalArtifact.NONE and artifact in _SPECIALIZED_PROMPTS:
        return {
            "temperature": 0.4,
            "max_output_tokens": 20000,
            "thinking_budget": 4096,
        }
    return {"temperature": 0.3}


_SAFE_COLLECTION_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$')


def _smart_truncate_message(text: str, head_chars: int, tail_chars: int) -> str:
    """Head+tail truncation that preserves legal anchors at both ends.

    A fact pattern established in para 3-5 of a long prior turn (e.g.
    "the cheque was drawn on 2023-01-15 by ABC Corp for Rs. 50,00,000")
    is lost if we just take `text[:500]`. Head+tail keeps the opening
    context AND the closing summary (which usually contains the
    conclusion / next-step / cited sections). Falls back to plain
    truncation when text fits in the budget.
    """
    if not text:
        return ""
    budget = head_chars + tail_chars
    if len(text) <= budget:
        return text
    return text[:head_chars] + "\n[...]\n" + text[-tail_chars:]


def _format_chat_history(chat_history: list) -> str:
    """Format LangGraph chat history messages into a text block for prompts.

    Takes the last 3 turns (6 messages). Uses head+tail truncation to
    preserve legal anchors (parties, dates, section numbers) that often
    sit in the middle/end of substantive prior turns. The prior 500-char
    head-only truncation dropped fact patterns established later in the
    response, leading to multi-turn drift in Document Q&A.
    """
    if not chat_history:
        return ""
    # Take last 6 messages (3 turns of Q+A)
    recent = chat_history[-6:]
    parts = []
    for msg in recent:
        role = getattr(msg, "type", "unknown")
        content = getattr(msg, "content", "")
        if role == "human":
            # User turns are typically short — 800 chars covers most
            # legal-query phrasings without dropping facts.
            parts.append(f"User: {_smart_truncate_message(content, 600, 200)}")
        elif role == "ai":
            # Assistant turns can be long substantive answers — keep
            # the introduction (frame) and the conclusion (anchors,
            # cited sections, follow-up cues).
            parts.append(f"Assistant: {_smart_truncate_message(content, 1200, 600)}")
    return "\n".join(parts)


def _validate_collection_name(unique_string: str) -> None:
    if not unique_string or not _SAFE_COLLECTION_RE.match(unique_string):
        raise ValueError(f"Invalid or unsafe collection name: {unique_string!r}")




# --- ChromaDB Collection Management ---

def _get_or_create_collection(unique_string: str) -> Chroma:
    """Get or create a ChromaDB collection for a user's uploaded documents."""
    _validate_collection_name(unique_string)
    embeddings = get_qa_embeddings()

    return Chroma(
        client=get_chroma_client(),
        collection_name=unique_string,
        embedding_function=embeddings,
    )



def _collection_has_data(unique_string: str) -> bool:
    """Check if a ChromaDB collection exists and has documents.

    Used to verify background OCR has completed before attempting retrieval.
    Returns False if collection is empty or doesn't exist yet.
    """
    try:
        vectordb = _get_or_create_collection(unique_string)
        count = vectordb._collection.count()
        return count > 0
    except Exception:
        return False


def _retrieve_from_collections(
    collections: list[str], query: str, k_per_collection: int = 15,
) -> list[Document]:
    """Retrieve from multiple ChromaDB collections and merge results.

    When only one collection exists, retrieves k=30 from it.
    When multiple exist, retrieves k_per_collection from each and merges.
    Skips empty collections (background OCR may not have completed yet).
    """
    all_docs: list[Document] = []
    ready_collections = [c for c in collections if _collection_has_data(c)]

    if not ready_collections:
        log.warning("No ChromaDB collections have data yet",
                    total=len(collections))
        return []

    if len(ready_collections) != len(collections):
        log.info("Some collections not ready (background OCR pending)",
                 ready=len(ready_collections), total=len(collections))

    if len(ready_collections) == 1:
        # Single collection — use full k budget
        vectordb = _get_or_create_collection(ready_collections[0])
        with log_time(log, "MMR retrieval", collection=ready_collections[0]):
            retriever = vectordb.as_retriever(
                search_type="mmr",
                search_kwargs={"k": 30, "fetch_k": 50},
            )
            all_docs = retriever.invoke(query)
    else:
        # Multiple collections — retrieve from each, merge
        for coll_id in ready_collections:
            try:
                vectordb = _get_or_create_collection(coll_id)
                with log_time(log, "MMR retrieval", collection=coll_id):
                    retriever = vectordb.as_retriever(
                        search_type="mmr",
                        search_kwargs={"k": k_per_collection, "fetch_k": k_per_collection * 2},
                    )
                    docs = retriever.invoke(query)
                    all_docs.extend(docs)
            except Exception as e:
                log.warning("Collection retrieval failed",
                            collection=coll_id, error=str(e))
        log.info("Multi-collection retrieval",
                 collections=len(ready_collections), total_docs=len(all_docs))

    return all_docs


def _retrieve_docs(
    unique_string: str, query: str,
    collections: list[str] | None = None,
) -> list[Document]:
    """Retrieve documents from user's ChromaDB collection(s). No LLM call.

    Split out from _retrieve_and_answer so the async caller can run a
    `check_retrieval_relevance` gate between retrieval and generation —
    MMR with k=30 always returns 30 chunks regardless of similarity, so
    off-topic questions about an uploaded PDF would otherwise get 30
    unrelated chunks pasted into the prompt and produce a confidently-
    hallucinated answer. The gate lets the agent return a graceful
    "this PDF doesn't cover your question" response instead.
    """
    coll_list = collections or [unique_string]
    docs = _retrieve_from_collections(coll_list, query)
    log.debug("Documents retrieved",
              collection=unique_string, docs_found=len(docs))
    return docs


def _generate_from_docs(
    query: str,
    docs: list[Document],
    history_text: str = "",
    user_language: str = "en",
    intent=None,
) -> tuple[str, int]:
    """LLM-generate an answer from pre-retrieved chunks.

    The ChromaDB retrieval path used to ship a bare English system prompt with
    NO language directive — when the uploaded PDF was in an Indian language,
    Gemini would code-switch into that language even though the user typed
    their query in English. Wrapping the prompt in `localize_prompt` (with
    the source-content language detected from the retrieved chunks)
    propagates the same language-consistency rules every other agent honours
    and closes the Marathi-PDF + English-query mixing hole.

    Returns (answer_text, tokens_consumed).
    """
    if not docs:
        return "No relevant content found in the uploaded document(s) for this query.", 0

    docs_text = "\n\n".join(d.page_content for d in docs)

    # Detect the dominant language of the retrieved chunks so localize_prompt
    # can emit the STRONG English directive (e.g. "source is Marathi —
    # translate it") instead of the soft default. Sampling the first chunk is
    # enough — chunks from one PDF are uniform in language.
    source_langs = detect_source_languages(docs_text)

    with log_time(log, "LLM generation"):
        llm = get_gemini_pro(temperature=0.3)
        system_prompt = localize_prompt(
            "You are Lawttorney, a legal AI assistant. Answer questions about "
            "the uploaded document(s) using only the provided context. Be "
            "thorough and cite specific sections when possible.",
            user_language,
            intent,
            source_languages=source_langs,
        )
        prompt_messages = [("system", system_prompt)]
        if history_text:
            prompt_messages.append(("user", "Previous conversation:\n{history}"))
        prompt_messages.extend([
            ("user", "Document content:\n{docs}"),
            ("user", "Current Date: {date}"),
            ("user", "Question: {query}"),
        ])
        prompt = ChatPromptTemplate.from_messages(prompt_messages)
        chain = prompt | llm

        invoke_args = {
            "query": query,
            "docs": docs_text,
            "date": str(date.today()),
        }
        if history_text:
            invoke_args["history"] = history_text
        response = chain.invoke(invoke_args)

    from core.token_tracker import record as _record_tokens
    tokens = _record_tokens("Document", "qa_chromadb", response)

    return response.content, tokens


# --- Agent Node ---

async def document_node(state: LegalAgentState) -> dict:
    """Process PDF documents or answer questions about them.

    For CHAT (main use case in the multi-agent graph):
    1. Load PDF-specific chat history
    2. Retrieve from user's ChromaDB collection (MMR, k=30)
    3. Generate answer with Gemini 2.5 Pro
    4. Save chat history

    Note: PDF upload/processing is handled separately via the gateway
    API route, not through the LangGraph agent flow.
    """
    query = state.get("query") or state.get("original_query", "")
    unique_string = state.get("unique_string")
    user_language = state.get("user_language", "en")
    chat_history = state.get("chat_history", [])

    # Check file_context — prefer Gemini file parts (native PDF reading)
    # over ChromaDB (OCR'd text chunks) when both are available
    all_collections: list[str] = []
    if not unique_string:
        fc = FileContextData.from_state(state)
        if fc and fc.all_gemini_parts:
            # Gemini URIs available — use multimodal path (better quality)
            pass  # handled below
        elif fc and fc.chromadb_collections:
            all_collections = fc.chromadb_collections
            unique_string = all_collections[0]
            log.info("Using inline file collections",
                     collections=all_collections, primary=unique_string)

    progress("document", "Loading uploaded documents...", step="load")
    log.info("Agent started",
             collection=unique_string, query=query[:100])

    if not unique_string:
        fc = FileContextData.from_state(state)

        # Check for Gemini file parts (images, PDFs, TXT, CSV via Files API)
        # all_gemini_parts includes legacy base64 images for backward compat
        if fc and fc.all_gemini_parts:
            parts = fc.all_gemini_parts
            log.info("Using Gemini file parts for multimodal document QA",
                     parts=len(parts), files=fc.file_names)
            try:
                with log_time(log, "Gemini file parts document QA"):
                    intent_obj = state.get("user_intent")
                    artifact = getattr(intent_obj, "legal_artifact", LegalArtifact.NONE) if intent_obj else LegalArtifact.NONE
                    llm = get_gemini_pro(**_llm_config_for_artifact(intent_obj))

                    # Build content list: file parts + question text.
                    # Translate the internal {"file_data": {...}, "name": ...}
                    # representation into a LangChain media content block — raw
                    # Gemini-shaped dicts have no "type" key, so langchain-google-genai
                    # logs "Unrecognized message part format" and stringifies them,
                    # which silently drops the PDF and lets Gemini hallucinate.
                    def _build_user_content(active_query: str) -> list:
                        uc: list = []
                        for part in parts:
                            if "file_data" in part:
                                fd = part["file_data"]
                                uc.append({
                                    "type": "media",
                                    "file_uri": fd["file_uri"],
                                    "mime_type": fd.get("mime_type", "application/octet-stream"),
                                })
                            elif "inline_data" in part:
                                d = part["inline_data"]
                                uc.append({
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{d['mime_type']};base64,{d['data']}"
                                    },
                                })
                        uc.append({
                            "type": "text",
                            "text": f"Current Date: {date.today()}\n\nQuestion: {active_query}",
                        })
                        return uc

                    # Pick the system prompt: specialized one (e.g.
                    # CROSS_EXAMINATION_PROMPT) when intent surfaced a
                    # legal_artifact, otherwise the generic file-Q&A prompt.
                    # Source-language hint: Gemini reads the file directly so
                    # we lack raw text here; fall back to any inline_text
                    # OCR companion (e.g. when a scanned PDF was both
                    # uploaded and OCR-extracted). When unavailable, the
                    # soft English directive in localize_prompt still
                    # prevents most code-switching.
                    _source_langs_gemini = detect_source_languages(
                        fc.inline_text if fc and fc.inline_text else None
                    )
                    _doc_system = localize_prompt(
                        _pick_specialized_prompt(
                            intent_obj,
                            "You are Lawttorney, a legal AI assistant. Analyze the uploaded "
                            "file(s) carefully based on what you ACTUALLY SEE in them.\n\n"
                            "CRITICAL RULES:\n"
                            "1. Describe ONLY what is visible in the uploaded file. Do NOT "
                            "fabricate or hallucinate content that is not there.\n"
                            "2. If the file is a legal document (FIR, judgment, petition, "
                            "agreement, notice), extract: names, dates, section numbers, "
                            "case numbers, court names, and legal provisions.\n"
                            "3. If the file is NOT a legal document (e.g., a photo, diagram, "
                            "receipt, letter, screenshot), describe what you see accurately "
                            "and answer the user's question based on the actual content.\n"
                            "4. If the file content does not match the user's question, say so "
                            "clearly. Do NOT force a legal interpretation on non-legal content.\n"
                            "5. NEVER generate fake case names, case numbers, or court details "
                            "that are not visible in the uploaded file.",
                        ),
                        user_language,
                        intent_obj,
                        source_languages=_source_langs_gemini,
                    )
                    history_text = _format_chat_history(chat_history)

                    def _build_messages(active_query: str) -> list:
                        m: list = [("system", _doc_system)]
                        if history_text:
                            m.append(("user", f"Previous conversation:\n{history_text}"))
                        if fc.inline_text:
                            m.append(("user", f"Additional document text:\n{fc.inline_text[:40000]}"))
                        m.append(("user", _build_user_content(active_query)))
                        return m

                    response = llm.invoke(_build_messages(query))

                # Self-refine: dynamic LLM-driven critic + refiner loop.
                # Reads the typed UserIntent, surfaces violations, rewrites.
                # Skips trivial intents internally (no directives / low conf).
                # Replaces _passes_quality_gate + hardcoded retry preambles.
                # Pass detected source languages so the critic force-runs
                # when the file is in a different language than the
                # response target — the safety net for the prompt-level
                # English directive emitted by localize_prompt.
                refined_content, refine_history = await self_refine(
                    response.content,
                    user_query=query,
                    intent=intent_obj,
                    source_languages=_source_langs_gemini,
                )
                if refined_content != response.content:
                    log.info(
                        "Self-refine altered response",
                        artifact=artifact.value if isinstance(artifact, LegalArtifact) else str(artifact),
                        iterations=len(refine_history),
                        original_len=len(response.content),
                        refined_len=len(refined_content),
                    )
                    response.content = refined_content

                from core.token_tracker import record as _record_tokens
                tokens = _record_tokens("Document", "qa_gemini_files", response)

                log.info("Gemini file parts document QA completed",
                         response_len=len(response.content), tokens=tokens,
                         legal_artifact=artifact.value if isinstance(artifact, LegalArtifact) else str(artifact))

                sources = [SourceMetadata(
                    source_type="document",
                    title=f"Uploaded: {fn}",
                    content=["Multimodal file analysis"],
                    file_name=fn,
                    agent_name="Document",
                ) for fn in fc.file_names[:5]]

                return {"agent_results": {"Document": AgentResult(
                    agent_name="Document",
                    content=response.content,
                    sources=sources,
                    tokens_consumed=tokens,
                )}}

            except Exception as e:
                log.error("Gemini file parts document QA failed", error=str(e), exc_info=True)
                return {"agent_results": {"Document": AgentResult(
                    agent_name="Document",
                    content="",
                    sources=[],
                    tokens_consumed=0,
                    error=str(e),
                )}}

        # Check for inline file text (small files not stored in ChromaDB)
        if fc and fc.inline_text:
            log.info("Using inline file text for document QA",
                     inline_chars=len(fc.inline_text), files=fc.file_names)
            try:
                with log_time(log, "Inline document QA"):
                    intent_obj = state.get("user_intent")
                    artifact = getattr(intent_obj, "legal_artifact", LegalArtifact.NONE) if intent_obj else LegalArtifact.NONE
                    llm = get_gemini_pro(**_llm_config_for_artifact(intent_obj))
                    history_text = _format_chat_history(chat_history)
                    # Inline-text path: we have the full file text in hand,
                    # so detect its script directly and pass it as the
                    # source-language hint. localize_prompt uses this to
                    # escalate the English directive when the user typed
                    # English but the file is, say, Marathi.
                    _source_langs_inline = detect_source_languages(fc.inline_text)
                    system_prompt = localize_prompt(
                        _pick_specialized_prompt(
                            intent_obj,
                            "You are Lawttorney, a legal AI assistant. Answer questions about "
                            "the uploaded document(s) using ONLY the provided content. "
                            "Do NOT fabricate any information not present in the document. "
                            "If the content is a legal document, cite specific sections, clauses, "
                            "parties, dates, and legal provisions. If it is not a legal document, "
                            "describe the actual content accurately.",
                        ),
                        user_language,
                        intent_obj,
                        source_languages=_source_langs_inline,
                    )
                    prompt_messages = [("system", system_prompt)]
                    if history_text:
                        prompt_messages.append(("user", "Previous conversation:\n{history}"))
                    prompt_messages.extend([
                        ("user", "Document content:\n{docs}"),
                        ("user", "Current Date: {date}"),
                        ("user", "Question: {query}"),
                    ])
                    prompt = ChatPromptTemplate.from_messages(prompt_messages)
                    chain = prompt | llm

                    def _run(active_query: str):
                        args = {
                            "query": active_query,
                            "docs": fc.inline_text[:80000],
                            "date": str(date.today()),
                        }
                        if history_text:
                            args["history"] = history_text
                        return chain.invoke(args)

                    response = _run(query)

                # Self-refine: same pattern as the Gemini-Files path above.
                refined_content, refine_history = await self_refine(
                    response.content,
                    user_query=query,
                    intent=intent_obj,
                    source_languages=_source_langs_inline,
                )
                if refined_content != response.content:
                    log.info(
                        "Self-refine altered response",
                        artifact=artifact.value if isinstance(artifact, LegalArtifact) else str(artifact),
                        iterations=len(refine_history),
                        original_len=len(response.content),
                        refined_len=len(refined_content),
                    )
                    response.content = refined_content

                from core.token_tracker import record as _record_tokens
                tokens = _record_tokens("Document", "qa_inline_text", response)

                log.info("Inline document QA completed",
                         response_len=len(response.content), tokens=tokens,
                         legal_artifact=artifact.value if isinstance(artifact, LegalArtifact) else str(artifact))

                sources = [SourceMetadata(
                    source_type="document",
                    title=f"Uploaded: {fn}",
                    content=["Inline file analysis"],
                    file_name=fn,
                    agent_name="Document",
                ) for fn in fc.file_names[:5]]

                return {"agent_results": {"Document": AgentResult(
                    agent_name="Document",
                    content=response.content,
                    sources=sources,
                    tokens_consumed=tokens,
                )}}

            except Exception as e:
                log.error("Inline document QA failed", error=str(e), exc_info=True)
                return {"agent_results": {"Document": AgentResult(
                    agent_name="Document",
                    content="",
                    sources=[],
                    tokens_consumed=0,
                    error=str(e),
                )}}

        log.warning("No file content available for Document agent",
                    has_fc=fc is not None,
                    has_gemini=bool(fc and fc.all_gemini_parts),
                    has_inline=bool(fc and fc.inline_text),
                    has_chromadb=bool(fc and fc.chromadb_collections))
        return {
            "agent_results": {"Document": AgentResult(
                agent_name="Document",
                content=(
                    "I was unable to read the uploaded file. This can happen if:\n"
                    "- The file upload did not complete successfully\n"
                    "- The file format is not supported\n"
                    "- The file content could not be extracted\n\n"
                    "Please try uploading the file again, or use a different format (PDF, JPEG, PNG, DOCX)."
                ),
                sources=[],
                tokens_consumed=0,
                error="no_file_content",
            )},
        }

    try:
        # Use chat history from state (loaded by memory node from SQLite)
        history_text = _format_chat_history(chat_history)

        # Step 1: Retrieve from ChromaDB (off-thread; no LLM call yet)
        n_colls = len(all_collections) if all_collections else 1
        progress("document", f"Searching across {n_colls} document collections...", found=n_colls, step="search")
        with log_time(log, "ChromaDB retrieval"):
            retrieved_docs = await asyncio.wait_for(
                asyncio.to_thread(
                    _retrieve_docs, unique_string, query,
                    collections=all_collections or None,
                ),
                timeout=TIMEOUT_CHROMADB_SEC,
            )

        # Step 2: Relevance gate. MMR k=30 always returns 30 chunks regardless
        # of similarity, so an off-topic question against an uploaded PDF
        # (e.g. user uploaded a contract, asks about Section 138 NI Act)
        # would otherwise get 30 unrelated chunks pasted into the prompt and
        # produce a hallucinated answer. The judge inspects the chunks and
        # decides whether they actually cover the user's question.
        if retrieved_docs:
            progress("document", "Verifying retrieval relevance...",
                     step="relevance_check")
            chunks_for_judge = [d.page_content for d in retrieved_docs[:8]]
            from core.retrieval_relevance import check_retrieval_relevance
            uploaded_label = "Uploaded Document"
            try:
                fc_label = FileContextData.from_state(state)
                if fc_label and fc_label.file_names:
                    uploaded_label = f"Uploaded: {fc_label.file_names[0]}"
            except Exception:
                pass
            is_relevant, judge_telemetry = await check_retrieval_relevance(
                query, chunks_for_judge, uploaded_label, agent_name="Document",
            )
            log.info("Document relevance judge verdict",
                     passed=is_relevant, **judge_telemetry)
            if not is_relevant:
                mismatch = judge_telemetry.get("matched_subject") or "different subject"
                log.warning("Document retrieval failed relevance gate",
                            **judge_telemetry)
                progress("document",
                         f"The uploaded document doesn't appear to cover your question "
                         f"({mismatch[:60]}).", step="off_topic", substep=True)
                msg = (
                    "The uploaded document(s) don't appear to contain information "
                    f"that answers your question ({mismatch[:80]}). "
                    "Try asking about content that is in the file, or remove the "
                    "attachment and ask the question as a general legal query."
                )
                return {"agent_results": {"Document": AgentResult(
                    agent_name="Document",
                    content=msg,
                    sources=[],
                    tokens_consumed=0,
                    error="off_topic_for_uploaded_document",
                )}}

        # Step 3: Generate answer (off-thread; LLM call)
        intent_obj_chromadb = state.get("user_intent")
        with log_time(log, "Document QA generation"):
            answer, tokens = await asyncio.wait_for(
                asyncio.to_thread(
                    _generate_from_docs,
                    query,
                    retrieved_docs,
                    history_text,
                    user_language,
                    intent_obj_chromadb,
                ),
                timeout=TIMEOUT_CHROMADB_SEC,
            )
        # Note: chat history is saved by the gateway after the full graph run,
        # not by the document agent. No duplicate save needed here.

        progress("document", f"Found {len(retrieved_docs)} relevant passages", found=len(retrieved_docs), substep=True, step="search")
        progress("document", "Generating answer from documents...", step="generate")
        log.info("Agent completed",
                 collection=unique_string,
                 response_len=len(answer), tokens=tokens)

        sources = []
        for d in retrieved_docs[:5]:
            src_file = d.metadata.get("source", "PDF")
            sources.append(SourceMetadata(
                source_type="document",
                title=f"Uploaded: {src_file}",
                content=[d.page_content[:200]],
                file_name=src_file,
                agent_name="Document",
            ))
        if not sources:
            sources.append(SourceMetadata(
                source_type="document",
                title="Uploaded Document",
                content=["Response based on uploaded PDF content"],
                agent_name="Document",
            ))

        result = AgentResult(
            agent_name="Document",
            content=answer,
            sources=sources,
            tokens_consumed=tokens,
        )

    except Exception as e:
        log.error("Agent failed",
                  collection=unique_string, error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="Document",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )

    return {"agent_results": {"Document": result}}
