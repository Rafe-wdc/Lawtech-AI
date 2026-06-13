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
from core.language import localize_prompt
from core.logger import get_logger, log_time
from core.progress import progress
from config.intent import LegalArtifact, UserIntent
from config.prompts import CROSS_EXAMINATION_PROMPT
log = get_logger("Document")


# ---------------------------------------------------------------------------
# Specialized-artifact dispatch (Phase A — cross_examination only).
#
# When `intent.legal_artifact == CROSS_EXAMINATION`, the document agent:
#   1. Picks CROSS_EXAMINATION_PROMPT instead of the generic file-Q&A prompt.
#   2. Configures Gemini 2.5 Pro with a thinking budget (planning helps for
#      strategic legal output) and a larger output cap.
#   3. After generation, applies a quality gate (min 600 words AND min 20
#      numbered questions). If the gate fails, retries ONCE with a stronger
#      preamble injected before the original query.
#
# Adding a new artifact = add a branch in _pick_specialized_prompt + an entry
# in _QUALITY_THRESHOLDS + (optional) a custom retry preamble.
# ---------------------------------------------------------------------------

_QUALITY_THRESHOLDS: dict = {
    LegalArtifact.CROSS_EXAMINATION: {
        "min_words": 600,
        "min_numbered_questions": 20,
    },
}

_RETRY_PREAMBLE: dict = {
    LegalArtifact.CROSS_EXAMINATION: (
        "The previous attempt fell short of the required output. You MUST "
        "produce a comprehensive cross-examination kit: a Legal Analysis "
        "section, a Strategic Objectives bullet list, and AT LEAST 30 "
        "numbered cross-examination questions organised into 5-7 strategic "
        "PARTS. Use formal Indian courtroom language including 'I put it to "
        "you that...', 'Is it not a fact that...', 'Do you deny that...'. "
        "Extract only facts visible in the attached document.\n\nOriginal "
        "question:\n"
    ),
}


def _pick_specialized_prompt(intent, generic_prompt: str) -> str:
    """Return CROSS_EXAMINATION_PROMPT (or another specialized prompt) when
    intent surfaces a legal_artifact request; otherwise return the generic
    prompt unchanged.
    """
    if intent is None:
        return generic_prompt
    artifact = getattr(intent, "legal_artifact", LegalArtifact.NONE)
    if artifact == LegalArtifact.CROSS_EXAMINATION:
        return CROSS_EXAMINATION_PROMPT
    return generic_prompt


def _llm_config_for_artifact(intent) -> dict:
    """Return Gemini Pro kwargs tuned for the requested artifact.

    Generic Q&A: temperature=0.3, no thinking budget, 12K max output.
    Specialized (cross_examination): temperature=0.4, thinking_budget=4096
    (strategic planning helps), 20K max output (long structured outputs).
    """
    if intent is None:
        return {"temperature": 0.3}
    artifact = getattr(intent, "legal_artifact", LegalArtifact.NONE)
    if artifact == LegalArtifact.CROSS_EXAMINATION:
        return {
            "temperature": 0.4,
            "max_output_tokens": 20000,
            "thinking_budget": 4096,
        }
    return {"temperature": 0.3}


def _passes_quality_gate(intent, content: str) -> tuple[bool, str]:
    """Return (passed, reason). When passed=False the caller retries once.

    Generic intents (artifact=NONE) always pass — quality gating is only
    applied to specialized artifacts with explicit thresholds.
    """
    if intent is None:
        return True, ""
    artifact = getattr(intent, "legal_artifact", LegalArtifact.NONE)
    thresholds = _QUALITY_THRESHOLDS.get(artifact)
    if not thresholds:
        return True, ""
    words = len(content.split())
    numbered = len(re.findall(r"(?:^|\n)\s*\d+\.\s+", content))
    if words < thresholds["min_words"]:
        return False, f"word_count_low ({words} < {thresholds['min_words']})"
    if numbered < thresholds["min_numbered_questions"]:
        return False, (
            f"numbered_questions_low ({numbered} < "
            f"{thresholds['min_numbered_questions']})"
        )
    return True, ""


def _retry_question(intent, original_query: str) -> str:
    """Build a stronger query for a quality-gate retry."""
    if intent is None:
        return original_query
    preamble = _RETRY_PREAMBLE.get(
        getattr(intent, "legal_artifact", LegalArtifact.NONE), ""
    )
    if not preamble:
        return original_query
    return preamble + original_query

_SAFE_COLLECTION_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$')


def _format_chat_history(chat_history: list) -> str:
    """Format LangGraph chat history messages into a text block for prompts.

    Takes the last 3 turns (6 messages) to keep context concise.
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
            parts.append(f"User: {content[:500]}")
        elif role == "ai":
            parts.append(f"Assistant: {content[:500]}")
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


def _retrieve_and_answer(
    unique_string: str, query: str, history_text: str = "",
    collections: list[str] | None = None,
) -> tuple[str, int, list[Document]]:
    """Retrieve from user's collection(s) and generate answer.
    Returns (answer_text, tokens_consumed, retrieved_docs).
    """
    coll_list = collections or [unique_string]
    docs = _retrieve_from_collections(coll_list, query)

    if not docs:
        log.warning("No relevant docs found", collection=unique_string)
        return "No relevant content found in the uploaded document(s) for this query.", 0, []

    log.debug("Documents retrieved",
              collection=unique_string, docs_found=len(docs))

    docs_text = "\n\n".join(d.page_content for d in docs)

    with log_time(log, "LLM generation"):
        llm = get_gemini_pro(temperature=0.3)
        prompt_messages = [
            ("system", "You are Lawttorney, a legal AI assistant. Answer questions about the uploaded document(s) using only the provided context. Be thorough and cite specific sections when possible."),
        ]
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

    return response.content, tokens, docs


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

                    # Quality gate: when intent requested a specialized
                    # legal artifact, verify the output meets minimum word /
                    # numbered-question counts. Retry ONCE with a stronger
                    # preamble if the first attempt fell short.
                    passed, reason = _passes_quality_gate(intent_obj, response.content)
                    if not passed:
                        log.warning(
                            "Specialized artifact output below quality gate; retrying once",
                            artifact=artifact.value if isinstance(artifact, LegalArtifact) else str(artifact),
                            reason=reason,
                            first_attempt_len=len(response.content),
                        )
                        retry_query = _retry_question(intent_obj, query)
                        response = llm.invoke(_build_messages(retry_query))
                        passed_after, reason_after = _passes_quality_gate(intent_obj, response.content)
                        log.info(
                            "Quality-gate retry result",
                            artifact=artifact.value if isinstance(artifact, LegalArtifact) else str(artifact),
                            passed_after_retry=passed_after,
                            reason=reason_after or "ok",
                            final_len=len(response.content),
                        )

                from core.token_tracker import record as _record_tokens
                tokens = _record_tokens("Document", "qa_gemini_files", response)

                log.info("Gemini file parts document QA completed",
                         response_len=len(response.content), tokens=tokens,
                         legal_artifact=artifact.value if isinstance(artifact, LegalArtifact) else str(artifact))

                sources = [SourceMetadata(
                    source_type="document",
                    title=f"Uploaded: {fn}",
                    content=["Multimodal file analysis (Gemini Files API)"],
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

                    # Quality gate (same pattern as the Gemini-Files path).
                    passed, reason = _passes_quality_gate(intent_obj, response.content)
                    if not passed:
                        log.warning(
                            "Inline document QA fell below quality gate; retrying once",
                            artifact=artifact.value if isinstance(artifact, LegalArtifact) else str(artifact),
                            reason=reason,
                            first_attempt_len=len(response.content),
                        )
                        response = _run(_retry_question(intent_obj, query))

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

        # Retrieve from ChromaDB and generate answer (blocking I/O + LLM → off-thread)
        # 90s timeout: ChromaDB MMR (k=30) + Gemini Pro generation can be slow,
        # but if ChromaDB hangs on a locked/stalled disk this prevents infinite hang.
        n_colls = len(all_collections) if all_collections else 1
        progress("document", f"Searching across {n_colls} document collections...", found=n_colls, step="search")
        with log_time(log, "Full document QA pipeline"):
            answer, tokens, retrieved_docs = await asyncio.wait_for(
                asyncio.to_thread(
                    _retrieve_and_answer, unique_string, query, history_text,
                    collections=all_collections or None,
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
