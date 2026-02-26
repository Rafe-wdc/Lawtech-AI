"""Agent #7 — Drafting Agent (Multi-Step Pipeline)

Court-filing quality legal document generation using a multi-step pipeline:
  Step 1: Retrieve best-matching template from Elasticsearch
  Step 2: Generate detailed document outline (Gemini 2.5 Pro, structured output)
  Step 3: Generate each section in full detail (Gemini 2.5 Pro × N sections)
  Step 4: Assemble all sections into complete document

Citations are injected later by the orchestrator's draft-aware synthesis,
which merges results from parallel Judgment/Legislation/Newacts agents.

Handles: "Draft bail application", "Legal notice for property dispute", etc.

Uses: Gemini 2.5 Pro (outline + sections), GPT-4o-mini (template selection)
Data Source: Elasticsearch "drafting" index (497 templates)
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import List

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import get_es_client, get_gpt4o_mini, get_drafting_llm
from core.settings import ES_INDICES
from core.logger import get_logger, log_time
from config.prompts import DRAFTING_SYSTEM_PROMPT, DRAFT_OUTLINE_PROMPT

log = get_logger("Drafting")


# --- Pydantic Models for Structured Outline ---

class SectionPlan(BaseModel):
    title: str = Field(..., description="Section heading (e.g. 'Facts of the Case')")
    description: str = Field(
        ...,
        description="What this section should contain — key points, arguments, details",
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
        description="Ordered list of all document sections (max 8 substantive sections)",
    )


# --- Template Selection (unchanged — uses GPT-4o-mini) ---

class TemplateSource(BaseModel):
    source: str = Field(..., description="The most relevant source file path")


TEMPLATE_SELECTION_PROMPT = """You are a legal AI assistant tasked with identifying the most relevant legal document template.

User query: {query}

Select the most relevant file path from the list below:
{files_path}

Return only the most relevant file path."""


def _select_best_template(query: str, file_paths: list[str]) -> str:
    """Use GPT-4o-mini to select the most relevant template from search results."""
    with log_time(log, "Template selection (LLM)"):
        llm = get_gpt4o_mini().with_structured_output(TemplateSource)
        prompt = ChatPromptTemplate.from_template(TEMPLATE_SELECTION_PROMPT)
        chain = prompt | llm
        result = chain.invoke({"query": query, "files_path": "\n".join(file_paths)})
    return result.source.strip()


# --- Step 1: Generate Document Outline ---

async def _generate_outline(
    query: str, template_text: str, chat_history: list
) -> DraftOutline:
    """Generate a structured outline with all sections for the document.

    Uses Gemini 2.5 Flash with structured output → reliable section list.
    """
    with log_time(log, "Outline generation"):
        llm = get_drafting_llm().with_structured_output(DraftOutline)
        prompt = ChatPromptTemplate.from_messages([
            ("system", DRAFT_OUTLINE_PROMPT),
            ("user", "Reference Template:\n{template}"),
            ("user", "Current Date: {date}"),
            ("user", "User Query:\n{query}"),
        ])
        chain = prompt | llm
        outline = await chain.ainvoke({
            "query": query,
            "template": template_text,
            "date": str(date.today()),
        })

    # Remove meta-sections that don't need LLM generation (Index, Table of Contents, etc.)
    # These cause the LLM to dump the entire document content into a single section
    META_SECTION_KEYWORDS = ("index", "table of contents", "contents page")
    filtered = [s for s in outline.sections
                if not any(kw in s.title.lower() for kw in META_SECTION_KEYWORDS)]
    if len(filtered) < len(outline.sections):
        removed = [s.title for s in outline.sections if s not in filtered]
        log.info("Removed meta-sections from outline", removed=removed)
        outline.sections = filtered

    # Cap sections at 8 to avoid excessive generation time
    MAX_SECTIONS = 8
    if len(outline.sections) > MAX_SECTIONS:
        log.warning("Outline has too many sections, capping",
                     original=len(outline.sections), capped=MAX_SECTIONS)
        outline.sections = outline.sections[:MAX_SECTIONS]

    log.info("Outline generated",
             title=outline.document_title[:80],
             sections=len(outline.sections),
             total_paragraphs=sum(s.estimated_paragraphs for s in outline.sections))
    return outline


# --- Step 2: Generate Each Section ---

async def _generate_section(
    query: str,
    template_text: str,
    section: SectionPlan,
    section_index: int,
    total_sections: int,
    outline: DraftOutline,
) -> tuple[str, int]:
    """Generate one section of the document in full detail.

    Returns (section_text, tokens_consumed).
    Each section gets the full token budget from Gemini 2.5 Pro.
    """
    # Build outline summary for context
    outline_summary = "\n".join(
        f"  {i+1}. {s.title}" for i, s in enumerate(outline.sections)
    )

    with log_time(log, f"Section {section_index+1}/{total_sections}: {section.title}"):
        llm = get_drafting_llm()
        prompt = ChatPromptTemplate.from_messages([
            ("system", DRAFTING_SYSTEM_PROMPT),
            ("user", "Document: {doc_title}\nCourt: {court_details}"),
            ("user", "Full Document Outline:\n{outline_summary}"),
            ("user", "Reference Template (excerpt):\n{template}"),
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
            "template": template_text[:3000],
            "section_num": str(section_index + 1),
            "total": str(total_sections),
            "section_title": section.title,
            "section_desc": section.description,
            "est_paragraphs": str(section.estimated_paragraphs),
            "needs_citations": "Yes — include [CITE: ...] markers" if section.needs_citations else "No",
        }, timeout=180)  # Drafting sections need more time than default 90s

    tokens = 0
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        tokens = response.usage_metadata.get("total_tokens", 0)

    log.debug("Section completed",
              section=section.title,
              content_len=len(response.content), tokens=tokens)
    return response.content, tokens


# --- Step 3: Assemble Document ---

def _assemble_document(outline: DraftOutline, sections: list[str]) -> str:
    """Combine all sections into the final document."""
    parts = [
        f"# {outline.document_title}",
        "",
        f"{outline.court_details}",
        "",
        "---",
        "",
    ]

    for section_plan, section_text in zip(outline.sections, sections):
        parts.append(section_text)
        parts.append("")

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
    outline = DraftOutline(**continuation["outline"])

    log.info("Continue draft started",
             failed_sections=len(failed_indices),
             total_sections=len(outline.sections))

    try:
        # Emit progress via stream writer if available
        try:
            from langgraph.config import get_stream_writer
            writer = get_stream_writer()
        except (RuntimeError, ImportError):
            writer = None

        # Rebuild full sections list, regenerating only the failed ones
        sections: list[str] = list(completed_sections.values()) if completed_sections else [""] * len(outline.sections)
        # Reconstruct sections list properly (indexed)
        sections = [""] * len(outline.sections)
        for idx_str, text in completed_sections.items():
            sections[int(idx_str)] = text

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
                )
                sections[idx] = section_text
                total_tokens += section_tokens
            except Exception as sec_err:
                log.error("Continue: section still failed",
                          section=idx + 1, title=section_plan.title,
                          error=str(sec_err))
                sections[idx] = (
                    f"\n\n---\n\n"
                    f"**[Section {idx + 1}: {section_plan.title} — "
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

        full_draft = _assemble_document(outline, sections)

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
            # Clear continuation data on success
            state_update["draft_continuation"] = None

    except Exception as e:
        log.error("Continue draft failed", error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="Drafting", content="",
            sources=[], tokens_consumed=0, error=str(e),
        )
        state_update = {"agent_results": {"Drafting": result}}

    return state_update


# --- Agent Node ---

async def drafting_node(state: LegalAgentState) -> dict:
    """Multi-step legal draft generation pipeline.

    Flow:
    1. Search ES "drafting" index for matching templates (top 100)
    2. Use GPT-4o-mini to select most relevant template source
    3. Fetch full template document from that source
    4. Generate detailed document outline (Gemini 2.5 Pro, structured output)
    5. Generate each section in full detail (Gemini 2.5 Pro × N sections)
    6. Assemble all sections into complete document
    """
    agent_queries = state.get("agent_queries", {})
    query = agent_queries.get("Drafting", state.get("query", state["original_query"]))
    chat_history = state.get("chat_history", [])
    log.info("Agent started", query=query[:100],
             using_agent_query="Drafting" in agent_queries)

    try:
        es = get_es_client()
        index = ES_INDICES["drafting"]

        # Step 1: Search for matching templates
        with log_time(log, "Template search (ES)"):
            search_query = {
                "size": 100,
                "query": {"match": {"page_content": query}},
            }
            response = es.search(index=index, body=search_query)
            hits = response["hits"]["hits"]

        if not hits:
            log.warning("No templates found")
            return {
                "agent_results": {"Drafting": AgentResult(
                    agent_name="Drafting",
                    content="",
                    sources=[],
                    tokens_consumed=0,
                )},
            }

        # Step 2: Get unique file paths and select best one
        file_paths = list(dict.fromkeys(
            h["_source"]["source"] for h in hits
        ))
        log.info("Template candidates found",
                 total_hits=len(hits), unique_templates=len(file_paths))

        selected_source = await asyncio.to_thread(
            _select_best_template, query, file_paths
        )
        log.info("Template selected", template=selected_source)

        # Step 3: Fetch the full template document
        source_query = {
            "size": 1,
            "query": {"term": {"source.keyword": selected_source}},
        }
        source_response = es.search(index=index, body=source_query)
        source_hits = source_response["hits"]["hits"]

        if not source_hits:
            log.error("Selected template not found in ES",
                      template=selected_source)
            return {
                "agent_results": {"Drafting": AgentResult(
                    agent_name="Drafting",
                    content="",
                    sources=[],
                    tokens_consumed=0,
                    error=f"Template source not found: {selected_source}",
                )},
            }

        template_text = source_hits[0]["_source"]["page_content"]
        log.debug("Template loaded",
                  template=selected_source, template_len=len(template_text))

        # Step 4: Generate document outline
        outline = await _generate_outline(query, template_text, chat_history)

        # Step 5: Generate each section (sequential, each streamed)
        # Per-section error handling: if a section fails, mark it incomplete
        # and continue with the rest. The partial draft is still returned.
        sections: list[str] = []
        failed_indices: list[int] = []
        total_tokens = 0

        # Emit progress via stream writer if available
        try:
            from langgraph.config import get_stream_writer
            writer = get_stream_writer()
        except (RuntimeError, ImportError):
            writer = None

        for i, section_plan in enumerate(outline.sections):
            if writer:
                writer({
                    "type": "drafting_progress",
                    "section": i + 1,
                    "total": len(outline.sections),
                    "title": section_plan.title,
                })

            try:
                section_text, section_tokens = await _generate_section(
                    query, template_text, section_plan,
                    i, len(outline.sections), outline,
                )
                sections.append(section_text)
                total_tokens += section_tokens
            except Exception as sec_err:
                log.error("Section failed, marking incomplete",
                          section=i + 1, total=len(outline.sections),
                          title=section_plan.title, error=str(sec_err))
                placeholder = (
                    f"\n\n---\n\n"
                    f"**[Section {i + 1}: {section_plan.title} — could not be generated. "
                    f"Click \"Continue\" below to complete this section.]**"
                    f"\n\n---\n"
                )
                sections.append(placeholder)
                failed_indices.append(i)

        # Emit incomplete event so the frontend knows to show Continue button
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

        # Step 6: Assemble complete document (may include placeholders)
        full_draft = _assemble_document(outline, sections)

        if failed_indices:
            log.warning("Agent completed with incomplete sections",
                        template=selected_source,
                        sections_generated=len(sections) - len(failed_indices),
                        sections_failed=len(failed_indices),
                        failed_indices=failed_indices,
                        total_content_len=len(full_draft),
                        total_tokens=total_tokens)
        else:
            log.info("Agent completed — multi-step pipeline",
                     template=selected_source,
                     sections_generated=len(sections),
                     total_content_len=len(full_draft),
                     total_tokens=total_tokens)

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

    return state_update
