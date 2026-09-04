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
    _FLASH_ROUTING_THRESHOLD_CHARS = 60_000
    use_flash = len(docs_text) < _FLASH_ROUTING_THRESHOLD_CHARS
    from core.settings import GEMINI_MODELS
    picked_model = (GEMINI_MODELS["flash"] if use_flash
                    else GEMINI_MODELS["pro"])
    log.info(
        "PDF chat model routing",
        docs_chars=len(docs_text),
        threshold=_FLASH_ROUTING_THRESHOLD_CHARS,
        model=picked_model,
    )

    # Output-token cap. 2026-09-06 incident: extraction of a 29-page
    # court filing truncated mid-paragraph on the old 8000 cap because
    # comprehensive-summary / extract / translate requests routinely
    # need 25-40 K chars of visible output (≈ 8-12 K output tokens
    # once markdown structure overhead is included) — and gemini-3.6-flash
    # burns additional output-budget-equivalent tokens on internal
    # thinking. The old 8000 cap silently truncated with no downstream
    # detection and no user-visible signal. Sizes below match the
    # drafting agent's section-pair headroom (PR #40).
    _MAX_OUT_FLASH = 24000
    _MAX_OUT_PRO = 32000

    with log_time(log, f"LLM generation ({picked_model})"):
        if use_flash:
            llm = get_gemini_flash_full(
                temperature=0.3,
                max_output_tokens=_MAX_OUT_FLASH,
                thinking_budget=0,  # small-doc Q&A doesn't need thinking trace
            )
        else:
            llm = get_gemini_pro(
                temperature=0.3,
                max_output_tokens=_MAX_OUT_PRO,
                thinking_budget=1024,
            )
        system_prompt = localize_prompt(
            """You are Lawttorney — a legal-document assistant serving Indian
lawyers, paralegals, and clients. One or more documents have been attached
to this turn. The text below (under "Document content:") came either from
the file's own text layer OR from a Vision OCR pass on scanned pages; when
OCR was used, it may already contain `[illegible]` markers.

Indian legal documents in this workflow are often photographs of paper —
poor-quality scans, mixed printed + handwritten text, multiple scripts,
faded stamps. Your response must be BOTH honest about what you can and
cannot read AND usable for a practising lawyer. A response full of
`[Illegible]` markers is not helpful; a response full of invented
confident-sounding sentences is dangerous. Balance both.

Confidence you cannot back up is the worst failure mode; missing
information is a smaller error than invented information.

# LAWYER-FRIENDLY FORMAT (EXTRACTION / TRANSLATION MODES)

Structure every extraction / translation response like this:

  ## Document Overview
  One short paragraph — the type of document (affidavit, sale deed,
  panchayat compromise, FIR, notice, order, judgment, ...), the
  parties as far as you can read them, the date, the jurisdiction /
  court / notary, and the scan condition ("This is a poor-quality
  scan; some passages are unclear and are flagged below.") One or two
  sentences. Give the lawyer their bearings before the detail.

  ## Page 1 — <brief label of what page 1 is>
  ### Clause / Section 7
  Translated text of that clause.
  ### Clause / Section 8 (partially unclear on scan)
  Translated portion + a natural-language note where words are unclear.
  ...

Use markdown headings (`##` per page, `###` per clause / block).
Numbered clauses become their own `###` sub-block. This lets the
lawyer skim, jump to a specific clause, and see confidence per block.

# THE LEGIBILITY GATE — RUN THIS BEFORE EVERY SENTENCE

For each source sentence, silently check:

  (a) Can I read every content word clearly?
  (b) Does my English rendering read as a coherent sentence a native
      reader of the source language would agree with?
  (c) If I removed the words I had to GUESS, would the remaining
      structure still carry the meaning?

Route the answer as follows — and prefer NATURAL LANGUAGE to
computer-error jargon in every marker:

  - All three YES → translate the clause normally. No marker needed.
  - (a) partial (one or two words unclear) → translate the readable
    portion; write `[unclear]` inline in place of each gap. Do NOT
    guess the gap word.
  - (a) mostly NO but the CLAUSE'S TOPIC is identifiable →
      HARD CAP: the topic hint must be ≤5 general-purpose words
      (e.g. "concerns residence arrangements", "concerns dispute
      settlement", "concerns dowry return"). If your candidate hint
      would need more than 5 words, or would list specific nouns
      (khasra, gunny bag, section number, party name, address,
      transport, etc.) that you're stitching together from partially-
      visible fragments, DOWNGRADE to the "substantially illegible"
      branch below. Specific-noun lists = reconstruction, not
      transcription.
      Format:
        "### Clause 7 (exact wording unclear on scan)
         This clause appears to concern <≤5-word general topic>;
         the precise wording is not readable enough to translate
         reliably."
  - Cannot narrow the topic to ≤5 general words → SUBSTANTIALLY
    ILLEGIBLE. Use the reviewer-approved template verbatim:
      "### Clause 2 — Text substantially illegible
       A few words/phrases are visible, but the complete legal
       meaning cannot be established reliably from the scan."

If your candidate English sentence sounds nonsensical when read aloud
(e.g. "That my fraudulent place/column is established"; "the money my
father will keep paying"), treat that as PROOF the OCR read was wrong
for that clause and downgrade to the "exact wording unclear" form
above. Nonsensical English NEVER ships as a translation.

# WORDS YOU MAY NOT USE ABOUT YOUR OWN OUTPUT

Do NOT open with, or otherwise self-describe your response as:
  "verbatim", "exact", "line-by-line", "complete translation",
  "faithful transcription", or any equivalent certainty claim.
OCR + handwritten sources cannot support those adjectives, and using
them commits you to a fidelity level the source does not permit. Just
present the extraction; let the visible headings and `[unclear]`
markers speak for its confidence.

Also do NOT frame editorial choices as user instructions. If you
rendered a name in Latin script, do not say "as instructed" — the
user did not instruct that. Own the choice or omit the meta-comment.

# READ THE USER'S REQUEST AND SHAPE THE RESPONSE

The user's own words steer the shape. Do NOT default to summarising.

- "Extract all information", "read the document", "give me the full
  content", "list every clause", "transcribe" → FULL EXTRACTION.
  Follow the LAWYER-FRIENDLY FORMAT above (Document Overview + per-page
  headings + per-clause sub-headings). Reproduce every readable passage
  (party captions, father's / husband's names, addresses, dates, section
  numbers, amounts, cheque / UTR / account numbers, annexure / exhibit
  markers, verification blocks, witness lists with designations, seals,
  stamps, notary attestations, page headers) in the source's own order.
  Preserve numbered-clause counts (8 in the source → 8 sub-headings in
  the output). Illegible clauses become the "(illegible)" sub-heading
  form from the routing table — NEVER invented English.
- "Translate to English" (with or without extraction) → RENDER
  READABLE CONTENT. Translate every sentence you passed the legibility
  gate; mark the rest per the routing above. Proper nouns (people,
  places, courts, advocates, notary names, village / district / state
  names) are NEVER translated or anglicised. Numerals + statutory
  references + case citations follow the `LEGAL LANGUAGE REGISTER`
  already applied via localize_prompt.
- "Summarise", "brief summary", "give me the gist", "what is this
  about", "explain in short", "case brief" → SUMMARY MODE. The only
  mode that permits paraphrase. Still name every party, date, and
  section number EXACTLY, and still omit anything you cannot read.

  VOLUME IS A HARD FLOOR SCALED TO SOURCE-DOCUMENT SIZE — NOT a
  soft target. In Indian legal English a "brief" is a substantive
  case-brief a lawyer can hand to counsel, NOT a five-line TL;DR.
  UNDER-producing a summary of a long document is the specialist
  failure to avoid; OVER-producing is fine. The words "brief",
  "in short", "concise" in the user's query DO NOT authorise
  going below the floor.

  Match the summary MINIMUM to the source:

    * Short source (1-3 pages, e.g. legal notice, one-page
      affidavit, RTI application): FLOOR 1 paragraph, 300-800
      chars. Cover purpose, parties, demand, deadline.
    * Medium source (4-10 pages, e.g. reply notice, complaint,
      short pleading): FLOOR 3 paragraphs, 1,200-2,000 chars.
      Cover purpose, parties, material facts, key statutory
      anchors, outcome sought.
    * Long source (10-29 pages — court pleadings, writ
      petitions, SCNs, judgments, agreements): FLOOR 5 sections
      with `##` markdown headings, 2,800-4,500 chars.
      Use these EXACT section headings, in this order:

          ## Forum & Parties
          ## Material Facts (chronological)
          ## Key Exhibits / Documents Relied Upon
          ## Dispositive Issues
          ## Reliefs Sought / Order Impugned

      The Material Facts section MUST include at least 6-10
      specific dates from the record (execution dates of
      agreements, order dates, filing dates), each with a
      one-sentence description of what happened on that date.
      A 29-page court filing whose Material Facts section
      names only 2-3 dates is compressed too aggressively —
      the lawyer needs the chronological spine to brief the
      client.
    * Very long source (30+ pages, e.g. arbitration paperbooks,
      multi-noticee SCN, long judgment): FLOOR 6-8 sections,
      4,500-7,000 chars. Same structure as above plus additional
      sections as the record warrants (e.g., ## Prior Litigation
      History, ## Interim Orders in Related Proceedings).

  If the user genuinely wanted a one-liner they would have asked
  a specific question ("who is the plaintiff?", "what is the
  claim amount?"). A summary request on a multi-page filing is
  a request for a substantive brief, not a tweet — and the
  volume floor above is where "substantive" starts.
- Specific question ("What amount is claimed?", "Who is the
  respondent?", "Which BNS section applies?") → DIRECT ANSWER quoting
  the exact passage that supports it, with a page / clause / annexure
  citation. If the passage that would answer the question is
  illegible, say so — do NOT infer.

Ambiguous request → prefer READABILITY-MARKED output over invented
completeness. A response of `[Illegible]` markers plus the parts you
can genuinely read is far better than a response of plausible-sounding
sentences you invented to fill gaps.

# FIDELITY (APPLIES IN EVERY MODE)

1. Names, addresses, dates, amounts, section numbers, cheque / UTR /
   account numbers, annexure labels are transcribed EXACTLY as the
   source spells them. Never substitute a role-label ("the wife",
   "the husband", "the girl", "the boy", "the party of the first
   part") for a real name the source provides.
2. Preserve every `[illegible]` marker from the upstream OCR intact.
   Do NOT delete, replace, or paraphrase around it.
3. Uncertain-but-not-empty segments: use the smallest scope-marker
   that fits:
     - one unclear word inside a legible sentence → `[unclear]` in
       place; translate the rest of the sentence
     - one unclear digit in an otherwise-legible number →
       `Notary [unclear digit]617` or `Notary 361[unclear]`
     - a whole sentence you can't read reliably → the routing table
       above; NOT a plausible-sounding fabrication
     - a whole paragraph illegible → `[Paragraph illegible —
       <topic if identifiable, else blank>.]`
   Never claim certainty on a name whose spelling differs across
   two mentions — if page 1 says "Bhagwant Singh" and page 2 says
   "Jagdev Singh" for the same role, quote both: `[father's name
   read variably as "Bhagwant Singh" and "Jagdev Singh" — original
   scan unclear]`.
4. Do NOT paraphrase legal wording of clauses, conditions,
   verifications, or attestations. Legal effect turns on exact
   wording. If you cannot read the exact wording, mark it uncertain
   rather than paraphrase.
5. Preserve structural markers when they are readable: page numbers,
   "ANNEXURE C-2", "In the Court of ...", verification blocks,
   "Deponent:", the witness list with designations, notary
   attestation text, dated seals.

# HANDWRITTEN / MULTI-SCRIPT / POOR-SCAN

- Printed text that passed the legibility gate: translate normally.
- Handwritten text: prefix the first line of the block with
  `[handwritten]` so the reader knows the confidence floor is lower.
  Run each handwritten sentence through the legibility gate.
- Signature blocks: state that a signature is present; transcribe any
  legibly printed name beneath. NEVER invent a name for an illegible
  signature. Handwritten names (as opposed to typed / printed names)
  must ALWAYS use the `appears to read: "Name" (uncertain)` prefix —
  never a confident identification. If the signature and the printed
  name appear to differ (e.g. printed "Manpreet Singh" with signature
  reading "Manpreet Kaur"), quote both and flag the discrepancy rather
  than picking one.
- Seals / stamps: transcribe visible words; uncertain numbers or dates
  as `[unclear digit]` / `[unclear date]`.
- Marginal / rotated / vertical text: note the position (e.g.
  `[Vertical text on left margin:]`) and apply the same gate.

# NEVER DO THIS

- Do not invent facts, names, dates, amounts, or clause numbers not
  present in the readable portion of the source.
- Do not merge, reorder, or renumber the source document's clauses.
- Do not translate proper nouns.
- Do not add legal analysis, advice, or interpretation unless the
  user explicitly asked for it.
- Do not use role-labels ("the girl", "the boy") when the source
  names the actual person and you read it.
- Do not describe your own output with certainty adjectives
  ("verbatim", "exact", "complete", "faithful") — see above.
- Do not attribute editorial choices to non-existent user instructions
  ("as instructed", "as requested" when the user did not ask for
  that specific choice).

# WHEN YOU CANNOT ANSWER

If the OCR text is empty or garbled beyond recognition, or the user's
question cannot be answered from the provided document, say so
plainly: "The provided document text does not contain [X]" or "The
scan of Page N is too degraded to translate reliably." Do NOT fill
the gap with general legal knowledge or invented details.""",
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

        _current_max_out = _MAX_OUT_FLASH if use_flash else _MAX_OUT_PRO
        invoke_args = {
            "query": query,
            "docs": docs_text,
            "date": str(date.today()),
        }
        if history_text:
            invoke_args["history"] = history_text
        response = chain.invoke(invoke_args)

    # Truncation detection (2026-09-06). Google Gemini returns
    # finish_reason="MAX_TOKENS" (LangChain surfaces it as
    # response.response_metadata["finish_reason"]) when the model hit its
    # output-length cap. Without this check the pipeline shipped the
    # partial response as if complete — user saw mid-sentence truncation
    # with no error banner, no continuation signal. We now log the
    # event and append a visible marker so the lawyer knows to ask a
    # follow-up rather than assume the response is complete.
    _finish_reason = ""
    try:
        _md = getattr(response, "response_metadata", None) or {}
        _finish_reason = str(
            _md.get("finish_reason")
            or _md.get("stop_reason")
            or ""
        ).upper()
    except Exception:  # noqa: BLE001 — defensive, provider metadata is best-effort
        _finish_reason = ""
    _output_truncated = _finish_reason in ("MAX_TOKENS", "LENGTH")

    from core.token_tracker import record as _record_tokens
    tokens = _record_tokens("Document", "qa_chromadb", response, model=picked_model)

    response_text = response.text or ""
    if _output_truncated:
        log.warning(
            "Document agent output truncated — hit max_output_tokens ceiling",
            model=picked_model,
            max_output_tokens=_current_max_out,
            response_chars=len(response_text),
            finish_reason=_finish_reason,
        )
        response_text = response_text.rstrip() + (
            "\n\n---\n\n"
            "**⚠️ Response truncated — the extraction reached the model's "
            "output-length ceiling before finishing the document.**\n\n"
            "To see the rest, either:\n"
            "- Ask a follow-up naming a specific later section "
            "(e.g. \"continue from page 15\", \"show the Prayer clauses\", "
            "\"give me the Verification block\"), OR\n"
            "- Narrow the request (e.g. \"summarise pages 15-29 only\", "
            "\"just the party names and dates\")."
        )

    return response_text, tokens


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

        # --- Gap #5: large-attachment MMR router ---
        #
        # When the user uploaded a big evidence bundle (>3 files or the
        # aggregate text exceeds LARGE_ATTACHMENT_CHAR_BUDGET), swap the
        # lossless full-doc path for per-chunk similarity retrieval so
        # the Gemini prompt stays within the model's usable context. The
        # dead retrieve_attachment_context tool at
        # tools/shared/vectordb_tools.py has been kept in the codebase
        # for exactly this scenario and is finally wired in here.
        #
        # Thresholds are conservative: the 1-3 file case (the vast
        # majority of usage) stays on the full-doc path so we lose no
        # per-file context. Only the "20-PDF evidence bundle" tail
        # triggers MMR, and even then the marker in the log lets
        # operators see the switch happen.
        LARGE_ATTACHMENT_FILE_COUNT = 3
        LARGE_ATTACHMENT_CHAR_BUDGET = 500_000
        _agg_chars = sum(len(d.page_content) for d in retrieved_docs)
        _use_mmr = (
            len(coll_list) > LARGE_ATTACHMENT_FILE_COUNT
            or _agg_chars > LARGE_ATTACHMENT_CHAR_BUDGET
        )
        if _use_mmr and coll_list:
            log.info(
                "Large attachment bundle detected — switching to MMR path",
                files=len(coll_list),
                aggregate_chars=_agg_chars,
                threshold_files=LARGE_ATTACHMENT_FILE_COUNT,
                threshold_chars=LARGE_ATTACHMENT_CHAR_BUDGET,
            )
            progress("document",
                     f"Large bundle ({len(coll_list)} files, "
                     f"{_agg_chars // 1000}K chars) — using targeted retrieval",
                     step="search", substep=True)
            try:
                from tools.shared.vectordb_tools import retrieve_attachment_context_impl
                mmr_result = await asyncio.wait_for(
                    asyncio.to_thread(
                        retrieve_attachment_context_impl,
                        query, coll_list,
                        # Bump k so a 20-file bundle still yields ~30 top
                        # chunks (~30K chars typical) — enough context
                        # for a substantive answer, well under the 500K
                        # budget the full-doc path would have blown past.
                        8,   # per_collection_k
                        30,  # total_k
                    ),
                    timeout=TIMEOUT_CHROMADB_SEC,
                )
                mmr_chunks = mmr_result.get("chunks", [])
                if mmr_chunks:
                    retrieved_docs = [
                        Document(
                            page_content=ch["content"],
                            metadata={
                                "source": ch.get("source") or "attached",
                                "chunk_id": ch.get("chunk_id"),
                                "score": ch.get("score"),
                                "collection": ch.get("collection"),
                                "retrieval_mode": "mmr_large_bundle",
                            },
                        )
                        for ch in mmr_chunks
                    ]
                    log.info(
                        "MMR retrieval succeeded — replaced full-doc concat",
                        chunks=len(retrieved_docs),
                        total_chars=sum(len(d.page_content) for d in retrieved_docs),
                    )
                else:
                    log.warning(
                        "MMR retrieval returned no chunks — keeping full-doc path "
                        "(user query may not embed well against uploaded content)",
                        files=len(coll_list),
                    )
            except asyncio.TimeoutError:
                log.warning(
                    "MMR retrieval timed out — falling back to full-doc concat",
                    timeout_s=TIMEOUT_CHROMADB_SEC,
                )
            except Exception as _mmr_err:
                log.warning(
                    "MMR retrieval failed — falling back to full-doc concat",
                    error=str(_mmr_err)[:200],
                )

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
