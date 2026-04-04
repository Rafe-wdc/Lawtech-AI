"""Agent #7 -- Drafting Agent (Multi-Step Pipeline)

Court-filing quality legal document generation using a multi-step pipeline:
  Step 1: BM25 keyword search for matching templates
  Step 2: GPT-4o-mini selects best template (with content previews + validation)
  Step 3: Fetch full template (no truncation -- templates are 2K-9K chars)
  Step 4: Generate document outline (Gemini 2.5 Flash, structured output)
  Step 5: Generate sections in parallel (Gemini 2.5 Flash, semaphore-limited)
  Step 6: Assemble with section titles + court filing footer

Citations are injected later by the orchestrator's draft-aware synthesis,
which merges results from parallel Judgment/Legislation/Newacts agents.

Handles: "Draft bail application", "Legal notice for property dispute", etc.

Uses: Gemini 2.5 Flash (outline + sections), GPT-4o-mini (template selection)
Data Source: Elasticsearch "drafting" index (497 templates, BM25 keyword search)
"""

from __future__ import annotations

import asyncio
import os
import re
import unicodedata
from datetime import date
from typing import List

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import (
    get_es_client, get_gemini_flash, get_drafting_llm,
)
from core.settings import ES_INDICES
from core.language import localize_prompt
from core.logger import get_logger, log_time
from core.progress import progress
from config.prompts import DRAFTING_SYSTEM_PROMPT, DRAFT_OUTLINE_PROMPT

log = get_logger("Drafting")

# Max concurrent section generations (avoids Gemini rate limits)
_SECTION_CONCURRENCY = 3

# Max sections the outline can contain (aligned with prompt: 10-15 for complex docs)
_MAX_SECTIONS = 12

# Limit concurrent Drafting/ContinueDraft executions per worker process
_AGENT_SEMAPHORE = asyncio.Semaphore(3)


# --- Input Sanitization ---

_LUCENE_SPECIAL = re.compile(r'([+\-=&|!(){}\[\]^"~*?:\\/])')


def _sanitize_es_input(text: str, max_length: int = 500) -> str:
    """Sanitize user input before embedding in ES query."""
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = text[:max_length]
    text = _LUCENE_SPECIAL.sub(r"\\\1", text)
    return text


# --- Pydantic Models ---

class SectionPlan(BaseModel):
    title: str = Field(..., description="Section heading (e.g. 'Facts of the Case')")
    description: str = Field(
        ...,
        description="What this section should contain -- key points, arguments, details",
    )
    estimated_paragraphs: int = Field(
        5,
        description="Expected number of paragraphs (3-15)",
    )
    needs_citations: bool = Field(
        False,
        description="Whether this section needs case law citations",
    )


class DraftOutline(BaseModel):
    document_title: str = Field(
        ...,
        description="Full title (e.g. 'APPLICATION FOR ANTICIPATORY BAIL UNDER SECTION 483 BNSS')",
    )
    court_details: str = Field(
        ...,
        description="Court name, case type, party placeholders",
    )
    sections: List[SectionPlan] = Field(
        ...,
        description=f"Ordered list of all document sections (max {_MAX_SECTIONS} substantive sections)",
    )


class TemplateSource(BaseModel):
    source: str = Field(..., description="The most relevant source file path")


# --- Step 1: Hybrid Template Search (BM25 + kNN, Python-side RRF merge) ---

async def _search_templates(query: str) -> list[dict]:
    """Search drafting index with BM25 keyword matching.

    Returns top 15 candidates sorted by relevance.
    """
    es = get_es_client()
    index = ES_INDICES["drafting"]
    sanitized = _sanitize_es_input(query)

    bm25_query = {
        "size": 20,
        "query": {"match": {"page_content": sanitized}},
        "_source": ["source", "page_content"],
    }

    with log_time(log, "BM25 template search"):
        response = await asyncio.to_thread(
            es.search, index=index, body=bm25_query,
        )
        return response["hits"]["hits"][:15]


# --- Step 2: Template Selection (GPT-4o-mini with previews + validation) ---

TEMPLATE_SELECTION_PROMPT = """You are a legal AI assistant selecting the best legal document template.

User wants to draft: {query}

Select the MOST relevant template. Each entry shows the file path and a content preview:

{candidates}

Return only the file path of the best matching template."""


def _select_best_template(query: str, candidates: list[dict]) -> tuple[str, list[str]]:
    """Use GPT-4o-mini to select the most relevant template.

    Shows content previews alongside file paths for better selection.
    Returns (selected_source, all_valid_paths) for fallback support.
    """
    valid_paths = list(dict.fromkeys(c["_source"]["source"] for c in candidates))

    with log_time(log, "Template selection (LLM)"):
        # Build candidate list with previews
        candidate_lines = []
        for i, c in enumerate(candidates, 1):
            path = c["_source"]["source"]
            preview = c["_source"]["page_content"][:200].replace("\n", " ")
            candidate_lines.append(f"{i}. {path}\n   Preview: {preview}...")

        llm = get_gemini_flash(temperature=0.1).with_structured_output(TemplateSource)
        prompt = ChatPromptTemplate.from_template(TEMPLATE_SELECTION_PROMPT)
        chain = prompt | llm
        result = chain.invoke({
            "query": query,
            "candidates": "\n".join(candidate_lines),
        })

    selected = result.source.strip()

    # Validate: ensure selected path exists in candidates
    if selected not in valid_paths:
        # Fuzzy match
        matches = [p for p in valid_paths if selected in p or p in selected]
        if matches:
            log.warning("Template path fuzzy-matched",
                        returned=selected, matched=matches[0])
            selected = matches[0]
        else:
            log.warning("Template path not found, using top candidate",
                        returned=selected)
            selected = valid_paths[0]

    return selected, valid_paths


# --- Step 3: Generate Document Outline ---

async def _generate_outline(query: str, template_text: str, user_language: str = "en") -> DraftOutline:
    """Generate a structured outline with all sections for the document.

    Uses Gemini 2.5 Flash with structured output for reliable section list.
    """
    with log_time(log, "Outline generation"):
        llm = get_drafting_llm().with_structured_output(DraftOutline)
        prompt = ChatPromptTemplate.from_messages([
            ("system", localize_prompt(DRAFT_OUTLINE_PROMPT, user_language)),
            ("user", "Reference Template:\n{template}"),
            ("user", "Current Date: {date}"),
            ("user", "User Query:\n{query}"),
        ])
        chain = prompt | llm
        try:
            outline = await chain.ainvoke({
                "query": query,
                "template": template_text,
                "date": str(date.today()),
            })
        except Exception as e:
            log.error("Structured outline generation failed, using fallback",
                      error=str(e)[:200])
            # Fallback: 3-section outline (Facts/Arguments/Prayer)
            outline = DraftOutline(
                document_title="Legal Document",
                court_details="[Court Details]",
                sections=[
                    SectionPlan(
                        title="Facts and Background",
                        description=f"State the facts and background for: {query[:300]}",
                        estimated_paragraphs=8,
                        needs_citations=False,
                    ),
                    SectionPlan(
                        title="Legal Arguments and Grounds",
                        description=f"Present legal arguments and statutory grounds for: {query[:300]}",
                        estimated_paragraphs=10,
                        needs_citations=True,
                    ),
                    SectionPlan(
                        title="Prayer and Relief Sought",
                        description="State the relief sought and prayer to the court.",
                        estimated_paragraphs=3,
                        needs_citations=False,
                    ),
                ],
            )

    # Remove meta-sections that don't need LLM generation
    META_SECTION_KEYWORDS = ("index", "table of contents", "contents page")
    filtered = [s for s in outline.sections
                if not any(kw in s.title.lower() for kw in META_SECTION_KEYWORDS)]
    if len(filtered) < len(outline.sections):
        removed = [s.title for s in outline.sections if s not in filtered]
        log.info("Removed meta-sections from outline", removed=removed)
        outline.sections = filtered

    # Cap sections (aligned with prompt: bail 10-12, suits 12-15)
    if len(outline.sections) > _MAX_SECTIONS:
        log.warning("Outline has too many sections, capping",
                     original=len(outline.sections), capped=_MAX_SECTIONS)
        outline.sections = outline.sections[:_MAX_SECTIONS]

    log.info("Outline generated",
             title=outline.document_title[:80],
             sections=len(outline.sections),
             total_paragraphs=sum(s.estimated_paragraphs for s in outline.sections))
    return outline


# --- Step 4: Generate Each Section ---

async def _generate_section(
    query: str,
    template_text: str,
    section: SectionPlan,
    section_index: int,
    total_sections: int,
    outline: DraftOutline,
    user_language: str = "en",
) -> tuple[str, int]:
    """Generate one section of the document in full detail.

    Returns (section_text, tokens_consumed).
    Sends the FULL template (2K-9K chars) -- no truncation needed.
    """
    outline_summary = "\n".join(
        f"  {i+1}. {s.title}" for i, s in enumerate(outline.sections)
    )

    with log_time(log, f"Section {section_index+1}/{total_sections}: {section.title}"):
        llm = get_drafting_llm()
        prompt = ChatPromptTemplate.from_messages([
            ("system", localize_prompt(DRAFTING_SYSTEM_PROMPT, user_language)),
            ("user", "Document: {doc_title}\nCourt: {court_details}"),
            ("user", "Full Document Outline:\n{outline_summary}"),
            ("user", "Reference Template:\n{template}"),
            ("user",
             "NOW WRITE section {section_num} of {total} IN FULL DETAIL:\n\n"
             "## {section_title}\n{section_desc}\n\n"
             "Expected paragraphs: {est_paragraphs}\n"
             "Needs case law citations: {needs_citations}"),
            ("user", "User Query:\n{query}"),
        ])
        chain = prompt | llm

        from core.streaming import stream_chain_response
        response = await stream_chain_response(chain, {
            "query": query,
            "doc_title": outline.document_title,
            "court_details": outline.court_details,
            "outline_summary": outline_summary,
            "template": template_text,  # Full template (2K-9K chars, no truncation)
            "section_num": str(section_index + 1),
            "total": str(total_sections),
            "section_title": section.title,
            "section_desc": section.description,
            "est_paragraphs": str(section.estimated_paragraphs),
            "needs_citations": "Yes -- include [CITE: ...] markers" if section.needs_citations else "No",
        }, timeout=180)

    tokens = 0
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        tokens = response.usage_metadata.get("total_tokens", 0)

    log.debug("Section completed",
              section=section.title,
              content_len=len(response.content), tokens=tokens)
    return response.content, tokens


# --- Step 5: Parallel Section Generation ---

async def _generate_sections_parallel(
    query: str,
    template_text: str,
    outline: DraftOutline,
    writer=None,
    user_language: str = "en",
) -> tuple[list[str], list[int], int]:
    """Generate all sections with bounded parallelism via asyncio.Semaphore.

    Limits to _SECTION_CONCURRENCY concurrent Gemini calls.
    Sections that fail get placeholder text; their indices are tracked.

    Returns: (sections_list, failed_indices, total_tokens)
    """
    sem = asyncio.Semaphore(_SECTION_CONCURRENCY)
    total = len(outline.sections)
    # Each slot: (section_text | None, tokens, error | None)
    results: list[tuple[str | None, int, Exception | None]] = [None] * total
    progress_counter = {"count": 0}

    async def _gen_one(i: int, plan: SectionPlan):
        async with sem:
            progress_counter["count"] += 1
            if writer:
                writer({
                    "type": "drafting_progress",
                    "section": progress_counter["count"],
                    "total": total,
                    "title": plan.title,
                })
            try:
                text, tokens = await _generate_section(
                    query, template_text, plan, i, total, outline, user_language,
                )
                results[i] = (text, tokens, None)
            except Exception as e:
                log.error("Section failed", section=i + 1, title=plan.title,
                          error=str(e)[:200])
                results[i] = (None, 0, e)

    tasks = [_gen_one(i, plan) for i, plan in enumerate(outline.sections)]
    await asyncio.gather(*tasks)

    # Collect results in order
    sections: list[str] = []
    failed_indices: list[int] = []
    total_tokens = 0

    for i, (text, tokens, err) in enumerate(results):
        if err is not None:
            placeholder = (
                f"\n\n---\n\n"
                f"**[Section {i + 1}: {outline.sections[i].title} -- could not be generated. "
                f"Click \"Continue\" below to complete this section.]**"
                f"\n\n---\n"
            )
            sections.append(placeholder)
            failed_indices.append(i)
        else:
            sections.append(text)
            total_tokens += tokens

    return sections, failed_indices, total_tokens


# --- Step 6: Assemble Document ---

# Footer label translations for P1/P2 languages.
# Keys: place, date, signature, through_counsel
_FOOTER_LABELS: dict[str, dict[str, str]] = {
    "hi": {
        "place": "स्थान",
        "date": "दिनांक",
        "signature": "याचिकाकर्ता/आवेदक के हस्ताक्षर",
        "through_counsel": "अधिवक्ता के माध्यम से",
    },
    "bn": {
        "place": "স্থান",
        "date": "তারিখ",
        "signature": "আবেদনকারীর স্বাক্ষর",
        "through_counsel": "আইনজীবীর মাধ্যমে",
    },
    "ta": {
        "place": "இடம்",
        "date": "தேதி",
        "signature": "மனுதாரர்/விண்ணப்பதாரர் கையொப்பம்",
        "through_counsel": "வழக்கறிஞர் மூலம்",
    },
    "te": {
        "place": "స్థలం",
        "date": "తేదీ",
        "signature": "పిటిషనర్/దరఖాస్తుదారు సంతకం",
        "through_counsel": "న్యాయవాది ద్వారా",
    },
    "mr": {
        "place": "ठिकाण",
        "date": "दिनांक",
        "signature": "याचिकाकर्ता/अर्जदाराच्या सह्या",
        "through_counsel": "वकिलांमार्फत",
    },
    "kn": {
        "place": "ಸ್ಥಳ",
        "date": "ದಿನಾಂಕ",
        "signature": "ಅರ್ಜಿದಾರ/ಅರ್ಜಿದಾರರ ಸಹಿ",
        "through_counsel": "ವಕೀಲರ ಮೂಲಕ",
    },
    "ml": {
        "place": "സ്ഥലം",
        "date": "തീയതി",
        "signature": "ഹർജിക്കാരന്റെ/അപേക്ഷകന്റെ ഒപ്പ്",
        "through_counsel": "അഭിഭാഷകൻ വഴി",
    },
    "gu": {
        "place": "સ્થળ",
        "date": "તારીખ",
        "signature": "અરજદાર/અરજકર્તાની સહી",
        "through_counsel": "વકીલ મારફત",
    },
    "pa": {
        "place": "ਸਥਾਨ",
        "date": "ਮਿਤੀ",
        "signature": "ਅਰਜ਼ੀਕਰਤਾ ਦੇ ਦਸਤਖਤ",
        "through_counsel": "ਵਕੀਲ ਰਾਹੀਂ",
    },
    "ur": {
        "place": "جگہ",
        "date": "تاریخ",
        "signature": "درخواست گزار کے دستخط",
        "through_counsel": "وکیل کے ذریعے",
    },
}


def _assemble_document(outline: DraftOutline, sections: list[str], user_language: str = "en") -> str:
    """Combine all sections into the final document with proper structure.

    Ensures each section has a heading (injects from outline if LLM omitted it).
    Adds a court filing footer with labels translated for the user's language.
    """
    parts = [
        f"# {outline.document_title}",
        outline.court_details,
        "---",
    ]

    for i, (section_plan, section_text) in enumerate(zip(outline.sections, sections)):
        text = section_text.strip()

        # Inject section heading from outline if LLM didn't include one
        if not text.startswith("#"):
            text = f"## {i + 1}. {section_plan.title}\n\n{text}"

        parts.append(text)

    # Court filing footer — labels translated for regional languages
    labels = _FOOTER_LABELS.get(user_language, {})
    place_label = labels.get("place", "Place")
    date_label = labels.get("date", "Date")
    signature_label = labels.get("signature", "Signature of the Petitioner/Applicant")
    through_counsel_label = labels.get("through_counsel", "Through Counsel")

    parts.append("---")
    parts.append(
        f"**{place_label}:** [Place]\n\n"
        f"**{date_label}:** [Date]\n\n"
        f"**{signature_label}**\n\n"
        f"{through_counsel_label}:\n\n"
        "**[Name of Advocate]**\n"
        "[Enrollment No.]\n"
        "[Address of Advocate]"
    )

    return "\n\n".join(parts)


# --- Continue Draft (regenerate failed sections) ---

async def continue_draft_node(state: LegalAgentState) -> dict:
    """Continue an incomplete draft by regenerating only the failed sections.

    Reads continuation metadata from state (outline, template, completed sections)
    and only generates the sections that previously failed.
    """
    continuation = state.get("draft_continuation")
    if not continuation:
        log.error("continue_draft called but no continuation data in state")
        return {"agent_results": {"Drafting": AgentResult(
            agent_name="Drafting", content="",
            sources=[], tokens_consumed=0,
            error="No continuation data available",
        )}}

    query = continuation["query"]
    template_text = continuation["template_text"]
    template_source = continuation["template_source"]
    completed_sections = continuation["completed_sections"]
    failed_indices = continuation["failed_indices"]
    outline = DraftOutline.model_validate(continuation["outline"])

    log.info("Continue draft started",
             failed_sections=len(failed_indices),
             total_sections=len(outline.sections))

    # Acquire concurrency slot; emit queue_status SSE event if at capacity
    try:
        from langgraph.config import get_stream_writer
        _writer = get_stream_writer()
    except (RuntimeError, ImportError):
        _writer = None
    if _AGENT_SEMAPHORE.locked():
        log.warning("Concurrency limit reached, queuing ContinueDraft request")
        if _writer:
            _writer({"type": "queue_status", "status": "queued",
                     "message": "Drafting agent is busy, queuing your request..."})
    await _AGENT_SEMAPHORE.acquire()

    try:
        try:
            from langgraph.config import get_stream_writer
            writer = get_stream_writer()
        except (RuntimeError, ImportError):
            writer = None

        # Reconstruct sections list from completed sections (indexed by string key)
        sections: list[str] = [""] * len(outline.sections)
        for idx_str, text in completed_sections.items():
            idx = int(idx_str)
            if 0 <= idx < len(sections):
                sections[idx] = text

        total_tokens = 0
        new_failed: list[int] = []
        progress_step = 0

        for idx in failed_indices:
            progress_step += 1
            section_plan = outline.sections[idx]

            if writer:
                writer({
                    "type": "drafting_progress",
                    "section": progress_step,
                    "total": len(failed_indices),
                    "title": f"(Retry) {section_plan.title}",
                })

            try:
                section_text, section_tokens = await _generate_section(
                    query, template_text, section_plan,
                    idx, len(outline.sections), outline,
                    state.get("user_language", "en"),
                )
                sections[idx] = section_text
                total_tokens += section_tokens
            except Exception as sec_err:
                log.error("Continue: section still failed",
                          section=idx + 1, title=section_plan.title,
                          error=str(sec_err))
                sections[idx] = (
                    f"\n\n---\n\n"
                    f"**[Section {idx + 1}: {section_plan.title} -- "
                    f"could not be generated after retry.]**"
                    f"\n\n---\n"
                )
                new_failed.append(idx)

        if new_failed and writer:
            writer({
                "type": "draft_incomplete",
                "failed_sections": [
                    {"index": idx, "title": outline.sections[idx].title}
                    for idx in new_failed
                ],
                "total_sections": len(outline.sections),
                "completed_sections": len(outline.sections) - len(new_failed),
            })

        full_draft = _assemble_document(outline, sections, state.get("user_language", "en"))

        if new_failed:
            log.warning("Continue draft: some sections still failed",
                        still_failed=new_failed)
        else:
            log.info("Continue draft completed successfully",
                     sections_regenerated=len(failed_indices))

        template_display = os.path.splitext(os.path.basename(template_source))[0]
        result = AgentResult(
            agent_name="Drafting",
            content=full_draft,
            sources=[SourceMetadata(
                source_type="drafting",
                title=template_display,
                content=[template_text[:300]],
                file_name=template_source,
                agent_name="Drafting",
                template_type=template_display,
            )],
            tokens_consumed=total_tokens,
        )

        state_update: dict = {"agent_results": {"Drafting": result}}
        if new_failed:
            state_update["draft_continuation"] = {
                "outline": outline.model_dump(),
                "template_text": template_text,
                "template_source": template_source,
                "query": query,
                "completed_sections": {
                    str(i): sections[i] for i in range(len(sections))
                    if i not in new_failed
                },
                "failed_indices": new_failed,
            }
        else:
            state_update["draft_continuation"] = None

    except Exception as e:
        log.error("Continue draft failed", error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="Drafting", content="",
            sources=[], tokens_consumed=0, error=str(e),
        )
        state_update = {"agent_results": {"Drafting": result}}
    finally:
        _AGENT_SEMAPHORE.release()

    return state_update


# --- Agent Node ---

async def drafting_node(state: LegalAgentState) -> dict:
    """Multi-step legal draft generation pipeline.

    Flow:
    1. Hybrid search ES "drafting" index (BM25 + kNN vector, top 15)
    2. GPT-4o-mini selects best template (with content previews + validation)
    3. Fetch full template document (no truncation)
    4. Generate document outline (Gemini 2.5 Flash, structured output, max 12 sections)
    5. Generate sections in parallel (Gemini 2.5 Flash, 3 concurrent via semaphore)
    6. Assemble with section titles + court filing footer
    """
    agent_queries = state.get("agent_queries", {})
    query = agent_queries.get("Drafting") or state.get("query") or state.get("original_query", "")
    user_context = state.get("user_context", "")
    user_language = state.get("user_language", "en")
    log.info("Agent started", query=query[:100],
             has_user_context=bool(user_context),
             using_agent_query="Drafting" in agent_queries)
    # For long queries: include pasted content in the drafting query
    if user_context:
        query = f"User's document/context:\n{user_context}\n\nUser's instruction:\n{query}"

    # Acquire concurrency slot; emit queue_status SSE event if at capacity
    try:
        from langgraph.config import get_stream_writer as _get_writer
        _dwriter = _get_writer()
    except (RuntimeError, ImportError):
        _dwriter = None
    if _AGENT_SEMAPHORE.locked():
        log.warning("Concurrency limit reached, queuing Drafting request")
        if _dwriter:
            _dwriter({"type": "queue_status", "status": "queued",
                      "message": "Drafting agent is busy, queuing your request..."})
    await _AGENT_SEMAPHORE.acquire()

    try:
        es = get_es_client()
        index = ES_INDICES["drafting"]

        # Step 1: Hybrid search for templates (BM25 + kNN, top 15)
        progress("drafting", "Searching for document templates...", step="search")
        hits = await _search_templates(query)

        if not hits:
            log.warning("No templates found")
            return {
                "agent_results": {"Drafting": AgentResult(
                    agent_name="Drafting",
                    content="",
                    sources=[],
                    tokens_consumed=0,
                    error="No matching templates found in our database.",
                )},
            }

        progress("drafting", f"Found {len(hits)} matching templates", found=len(hits), substep=True, step="search")
        log.info("Template candidates found", count=len(hits))

        # Step 2: Select best template (with content previews + validation)
        progress("drafting", "Selecting best template...", step="select")
        selected_source, all_paths = await asyncio.to_thread(
            _select_best_template, query, hits
        )
        template_display_name = os.path.splitext(os.path.basename(selected_source))[0]
        progress("drafting", f"Selected: {template_display_name[:60]}", substep=True, step="select")
        log.info("Template selected", template=selected_source)

        # Step 3: Fetch the full template document
        source_query = {
            "size": 1,
            "query": {"term": {"source.keyword": selected_source}},
            "_source": ["page_content", "source"],
        }
        source_response = es.search(index=index, body=source_query)
        source_hits = source_response["hits"]["hits"]

        # Fallback: try next-best candidate if selected template not found
        if not source_hits:
            log.warning("Selected template not found, trying fallback",
                        template=selected_source)
            for path in all_paths:
                if path == selected_source:
                    continue
                fb_resp = es.search(index=index, body={
                    "size": 1,
                    "query": {"term": {"source.keyword": path}},
                    "_source": ["page_content", "source"],
                })
                if fb_resp["hits"]["hits"]:
                    source_hits = fb_resp["hits"]["hits"]
                    selected_source = path
                    log.info("Using fallback template", template=path)
                    break

        if not source_hits:
            return {
                "agent_results": {"Drafting": AgentResult(
                    agent_name="Drafting", content="", sources=[],
                    tokens_consumed=0,
                    error=f"Template source not found: {selected_source}",
                )},
            }

        template_text = source_hits[0]["_source"]["page_content"]
        log.debug("Template loaded",
                  template=selected_source, template_len=len(template_text))

        # Step 4: Generate document outline (max 12 sections)
        progress("drafting", "Generating document outline...", step="outline")
        outline = await _generate_outline(query, template_text, user_language)
        progress("drafting", f"Outline ready: {len(outline.sections)} sections", found=len(outline.sections), substep=True, step="outline")

        # Step 5: Generate sections in parallel (semaphore-limited to 3)
        try:
            from langgraph.config import get_stream_writer
            writer = get_stream_writer()
        except (RuntimeError, ImportError):
            writer = None

        sections, failed_indices, total_tokens = await _generate_sections_parallel(
            query, template_text, outline, writer, user_language,
        )

        # Emit incomplete event if needed
        if failed_indices and writer:
            writer({
                "type": "draft_incomplete",
                "failed_sections": [
                    {"index": idx, "title": outline.sections[idx].title}
                    for idx in failed_indices
                ],
                "total_sections": len(outline.sections),
                "completed_sections": len(outline.sections) - len(failed_indices),
            })

        # Step 6: Assemble complete document
        progress("drafting", "Assembling final document...", step="assemble")
        full_draft = _assemble_document(outline, sections, user_language)

        if failed_indices:
            log.warning("Agent completed with incomplete sections",
                        template=selected_source,
                        sections_generated=len(sections) - len(failed_indices),
                        sections_failed=len(failed_indices),
                        failed_indices=failed_indices,
                        total_content_len=len(full_draft),
                        total_tokens=total_tokens)
        else:
            log.info("Agent completed -- multi-step pipeline",
                     template=selected_source,
                     sections_generated=len(sections),
                     total_content_len=len(full_draft),
                     total_tokens=total_tokens)

        # Step 7: Auto-inject statute references into the draft
        if full_draft and not failed_indices:
            try:
                from core.statute_refs import add_statute_references
                progress("drafting", "Adding statute references...", step="statute_refs")
                full_draft = await add_statute_references(full_draft)
            except Exception as ref_err:
                log.warning("Statute reference injection skipped",
                            error=str(ref_err))

        template_display = os.path.splitext(os.path.basename(selected_source))[0]
        result = AgentResult(
            agent_name="Drafting",
            content=full_draft,
            sources=[SourceMetadata(
                source_type="drafting",
                title=template_display,
                content=[template_text[:300]],
                file_name=selected_source,
                agent_name="Drafting",
                template_type=template_display,
            )],
            tokens_consumed=total_tokens,
        )

        # Store continuation metadata for incomplete drafts
        state_update = {"agent_results": {"Drafting": result}}
        if failed_indices:
            state_update["draft_continuation"] = {
                "outline": outline.model_dump(),
                "template_text": template_text,
                "template_source": selected_source,
                "query": query,
                "completed_sections": {
                    str(i): sections[i] for i in range(len(sections))
                    if i not in failed_indices
                },
                "failed_indices": failed_indices,
            }

    except Exception as e:
        log.error("Agent failed", error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="Drafting",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )
        state_update = {"agent_results": {"Drafting": result}}
    finally:
        _AGENT_SEMAPHORE.release()

    return state_update
