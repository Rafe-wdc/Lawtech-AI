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
from datetime import date

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from core.state import LegalAgentState, AgentResult, SourceMetadata, FileContextData
from core.clients import get_gemini_pro, get_gemini_flash_full
from core.settings import TIMEOUT_CHROMADB_SEC
from core.language import localize_prompt, detect_source_languages
from core.logger import get_logger, log_time, short_err
from core.progress import progress
from config.intent import UserIntent

log = get_logger("Document")


# Specialized-artifact dispatch, `_pick_specialized_prompt`,
# `_llm_config_for_artifact`, `_SPECIALIZED_PROMPTS`, and the ChromaDB
# retrieval helpers (`_get_or_create_collection`, `_collection_has_data`,
# `_retrieve_from_collections`, `_retrieve_docs`) were removed 2026-08-02
# (P2 dead-code sweep). Collection-name validation now lives only in
# `tools.shared.vectordb_tools._validate_collection_name` — the copy that
# used to shadow it here has been dropped. The current `document_node`
# uses the extracted text via `get_full_attachment` and a single generic
# prompt — no artifact dispatch or Chroma retrieval path is wired.


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

    # Route by document size: small docs go to Flash (~4x cheaper per token,
    # quality delta minimal for short-context Q&A); large docs stay on Pro
    # for the deeper reasoning it brings to complex/dense legal text.
    # 60_000 chars ≈ ~15K input tokens ≈ ~30 pages of typical legal PDF.
    # Tune based on observed quality; log both signals to inform tuning.
    # 2026-09-10: the large-doc branch moved off gemini-2.5-pro to
    # gemini-3.8-flash - cheaper than Pro on both input and output.
    _FLASH_ROUTING_THRESHOLD_CHARS = 60_000
    use_flash = len(docs_text) < _FLASH_ROUTING_THRESHOLD_CHARS
    picked_model = "gemini-2.5-flash" if use_flash else "gemini-3.8-flash"
    log.info(
        "PDF chat model routing",
        docs_chars=len(docs_text),
        threshold=_FLASH_ROUTING_THRESHOLD_CHARS,
        model=picked_model,
    )

    with log_time(log, f"LLM generation ({picked_model})"):
        if use_flash:
            llm = get_gemini_flash_full(
                temperature=0.3,
                max_output_tokens=8000,
                thinking_budget=0,  # small-doc Q&A doesn't need thinking trace
            )
        else:
            llm = get_gemini_pro(
                temperature=0.3,
                max_output_tokens=8000,
                thinking_budget=1024,
            )
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
    tokens = _record_tokens("Document", "qa_chromadb", response, model=picked_model)

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
        err_str = str(e).lower()
        # Gemini 400 INVALID_ARGUMENT when combined docs exceed the model's
        # 1M-token context. Surface a user-actionable message instead of the
        # raw SDK error, which is opaque and not fixable by retrying.
        if "input token count" in err_str and "maximum" in err_str:
            log.warning("Document input exceeded model context limit",
                        collection=unique_string, error=str(e)[:200])
            friendly_msg = (
                "The uploaded documents are too large to analyze in a "
                "single response — they exceed the model's context limit. "
                "Please narrow your question to a specific section (for "
                "example, \"summarize the arguments on page 4\" or \"what "
                "does clause 7 say\"), or upload smaller documents so I "
                "can process them properly."
            )
            result = AgentResult(
                agent_name="Document",
                content=friendly_msg,
                sources=[],
                tokens_consumed=0,
            )
        else:
            from core.metrics import record_agent_error
            record_agent_error("Document", e)
            log.error("Agent failed",
                      collection=unique_string, error=short_err(e), exc_info=True)
            result = AgentResult(
                agent_name="Document",
                content="",
                sources=[],
                tokens_consumed=0,
                error=short_err(e),
            )

    return {"agent_results": {"Document": result}}
