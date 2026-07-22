"""Agent #10 — Document Agent

Handles PDF upload, processing, OCR, and document-specific Q&A.
Manages per-user ChromaDB collections for uploaded documents.

3-step flow:
1. Upload & Validate → check file size, page count, format
2. Process → extract text, OCR scanned pages, chunk, embed, store
3. Chat → retrieve from user's collection, generate answer

Uses: Gemini 2.5 Pro (Q&A), Gemini 2.5 Flash (Vision OCR)
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

    return response.text, tokens


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

    # Phase D/E (RAG attachment routing plan, 2026-06-28): single text
    # pipeline — file content always lives in ChromaDB. Gemini Files URI
    # preference removed (the multimodal handling path is dead code).
    all_collections: list[str] = []
    fc = FileContextData.from_state(state)
    if not unique_string:
        if fc and fc.chromadb_collections:
            all_collections = fc.chromadb_collections
            unique_string = all_collections[0]
            log.info("Using file collections from FileContextData",
                     collections=all_collections, primary=unique_string)

    progress("document", "Loading uploaded documents...", step="load")
    log.info("Agent started",
             collection=unique_string, query=query[:100],
             has_extracted_texts=bool(fc and fc.has_extracted_text))

    # Bail only when BOTH the Chroma path and the raw-text path are empty.
    # When Chroma embed timed out (2026-06-28 pool-exhaustion pattern) the
    # file processor still surfaces the extracted OCR/PyMuPDF text via
    # fc.extracted_texts — reading that survives a Chroma outage and is what
    # Drafting already does (agents/drafting.py source-priority comment).
    if not unique_string and not (fc and fc.has_extracted_text):
        log.warning("No file content available for Document agent")
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

        # Step 1: Full-document read via get_full_attachment (Phase D default
        # lossless path). One Document per uploaded collection containing
        # the entire file text — agents that need it see everything, no
        # info loss. retrieve_attachment_context (top-K MMR) remains
        # available as a tool for confidently specific questions.
        from tools.shared.vectordb_tools import get_full_attachment
        coll_list = all_collections if all_collections else (
            [unique_string] if unique_string else []
        )
        n_colls = len(coll_list)
        progress("document", f"Loading {n_colls} document collection(s)...", found=n_colls, step="search")
        retrieved_docs: list[Document] = []
        with log_time(log, "Full-attachment read"):
            for cid in coll_list:
                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(
                            lambda c=cid: get_full_attachment.invoke({"collection_id": c})
                        ),
                        timeout=TIMEOUT_CHROMADB_SEC,
                    )
                    if result and result.get("full_text"):
                        retrieved_docs.append(Document(
                            page_content=result["full_text"],
                            metadata={
                                "source": result.get("source_file") or "attached",
                                "chunk_count": result.get("chunk_count"),
                            },
                        ))
                except Exception as e:
                    log.warning("get_full_attachment failed",
                                collection=cid, error=str(e))

        # Chroma-independent fallback: use the raw per-file text PyMuPDF /
        # DOCX / OCR wrote to fc.extracted_texts BEFORE Chroma embed ran
        # (see core/file_processor.py: `pf.extracted_text = text` after
        # each store attempt). Under Chroma pool exhaustion,
        # chromadb_collections is empty but extracted_texts still carries
        # every uploaded document verbatim. Without this fallback the
        # synthesizer receives an errored Document result, treats
        # valid_results as empty, and runs web_search_fallback on the raw
        # query — surfacing a generic "please provide a specific query"
        # reply instead of an answer grounded in the file the user just
        # uploaded.
        if not retrieved_docs and fc and fc.has_extracted_text:
            seen: set[str] = set()
            for entry in fc.extracted_texts:
                text = (entry.get("text") or "").strip()
                name = entry.get("name") or "attached"
                if not text or name in seen:
                    continue
                seen.add(name)
                retrieved_docs.append(Document(
                    page_content=text,
                    metadata={"source": name},
                ))
            log.info("Using extracted_texts fallback (Chroma path empty)",
                     files=len(retrieved_docs),
                     chroma_collections=len(coll_list))

        # Step 2: Relevance gate removed for the full-doc path (Phase D
        # switched retrieval from MMR top-30 to get_full_attachment, which
        # returns the entire file as one Document). The old gate was
        # designed to catch off-topic top-K chunks; against a single
        # whole-document "chunk" the judge prompt misfires (e.g. flags a
        # table-of-contents PDF as "off-topic for a summary request"). With
        # full-doc retrieval there is no off-topic possibility — the user
        # uploaded a specific file and is asking about it, and the LLM in
        # Step 3 can correctly say "this document doesn't contain that
        # information" when the answer truly isn't there.

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
