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
from functools import lru_cache
from typing import List

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from core.state import (
    LegalAgentState, AgentResult, SourceMetadata,
    IntegrationContextData, FileContextData,
)
from core.clients import (
    get_es_client, get_gemini_flash, get_drafting_llm,
)
from core.settings import ES_INDICES
from core.language import localize_prompt
from core.logger import get_logger, log_time
from core.progress import progress
from config.prompts import DRAFTING_SYSTEM_PROMPT, DRAFT_OUTLINE_PROMPT
from core.self_refine import self_refine

log = get_logger("Drafting")

# Max concurrent section generations (avoids Gemini rate limits)
_SECTION_CONCURRENCY = 3

# Max sections the outline can contain. Civil suits with the full procedural
# pack (Schedule, Court Fee, List of Docs, separate IA for TI, Verification,
# Affidavit) routinely need 14-16 sections, so the cap is generous.
_MAX_SECTIONS = 16

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
        description="Short subject heading (e.g. 'SUIT FOR COMPENSATION FOR MEDICAL NEGLIGENCE', 'WRIT PETITION UNDER ARTICLE 226 OF THE CONSTITUTION OF INDIA', 'APPLICATION FOR ANTICIPATORY BAIL UNDER SECTION 483 BNSS'). Will be rendered as the BOLDED SUBJECT HEADING at the END of the cause-title block — NOT as an H1 at top of document.",
    )
    court_details: str = Field(
        ...,
        description="""Pre-formatted markdown opening block of the
document — the LLM MUST emit this with proper blank lines (\\n\\n)
between every element (CommonMark renderers collapse single newlines).

PICK THE LAYOUT BASED ON DOCUMENT TYPE:

=== LAYOUT A: COURT FILING (plaint, petition, writ, complaint,
application, bail application, written statement) ===

Use when the document is filed in court.

**IN THE COURT OF <FORUM>, AT <CITY>**

**<SUIT/PETITION/COMPLAINT> NO. _______ OF <YEAR>**

**IN THE MATTER OF:**

<Plaintiff Full Name>

Age: <age>, Occupation: <occupation>

R/ Address: <residential address>

.....Plaintiff / Petitioner

**Versus**

<Defendant Full Name>

Age: <age>, Occupation: <occupation>

R/ Address: <residential address>

.....Defendant / Respondent

(Subject heading is appended at END by the assembler using
document_title — do NOT add it yourself.)

=== LAYOUT B: LEGAL NOTICE / REPLY TO LEGAL NOTICE ===

Use when the document is a notice (Section 138 NI Act notice, demand
notice, eviction notice, reply to legal notice). NOT filed in court.

**<NOTICE TITLE>**

(Examples: "LEGAL NOTICE", "REPLY TO LEGAL NOTICE", "NOTICE UNDER
SECTION 138 OF THE NEGOTIABLE INSTRUMENTS ACT, 1881")

Date: <date or blank line>

To,

<Recipient Name>

<Recipient Qualifications / Designation if known>

<Recipient Address>

**Subject: <Subject line, e.g. "Reply to your Legal Notice dated
DD/MM/YYYY issued on behalf of [Sender's Client Name]">**

Sir / Madam,

Under instructions from and on behalf of my client, <Client Full Name>,
<Client Description / Designation if applicable>, residing at
<Client Address>, I hereby send / address you the following <notice OR
reply>. The contents of <your notice are denied save and except those
specifically admitted herein / are as follows>.

(NO "IN THE MATTER OF", NO Plaintiff/Defendant blocks, NO Versus, NO
court name. A legal notice is a CORRESPONDENCE, not a pleading. The
title is at the TOP and is NOT re-appended at end by the assembler.)

=== LAYOUT D: POLICE COMPLAINT / FIR REGISTRATION APPLICATION ===

Use when the document is a complaint addressed to the POLICE STATION
(NOT to a magistrate). Common triggers: user says "police complaint" /
"FIR" / mentions Police Inspector / SHO / Station House Officer /
Section 154 CrPC / Section 173 BNSS. This is a LETTER, not a court
pleading. Do NOT use Layout A for these — that produces a Section 200
CrPC magistrate complaint which is filed in court, NOT what the user
asked for.

**<COMPLAINT TITLE>**

(Examples: "POLICE COMPLAINT", "APPLICATION FOR REGISTRATION OF FIR
UNDER SECTION 154 OF THE CODE OF CRIMINAL PROCEDURE, 1973", "COMPLAINT
UNDER SECTION 173 OF THE BHARATIYA NAGARIK SURAKSHA SANHITA, 2023")

Date: <date or blank line>

To,

The Police Inspector / Station House Officer,

<Name of Police Station>,

<Address of Police Station>.

**Subject: <Subject line, e.g. "Complaint regarding [offence] committed
on [date] by [accused name]">**

Sir / Madam,

I, <Complainant Full Name>, son / daughter / wife of <Father / Husband
Name>, aged <age> years, occupation <occupation>, residing at
<Complainant Address>, do hereby state and submit as follows:

(Then the body — numbered paragraphs of facts / sequence of events /
sections of BNS or IPC invoked — is emitted by the section LLM. Close
with "I therefore request your good office to register an FIR / take
cognizance and investigate the matter." The signature block is
appended automatically as footer.)

(NO "IN THE COURT OF", NO "IN THE MATTER OF", NO Plaintiff/Defendant
blocks, NO Versus. A police complaint is a LETTER addressed to the
police, not a pleading filed in court. The title is at the TOP and is
NOT re-appended at end by the assembler. If the user explicitly says
"Section 200 CrPC" / "Section 223 BNSS" / "magistrate complaint", USE
LAYOUT A instead — those ARE court filings.)

=== LAYOUT C: AGREEMENT / MOU / DEED / LEASE / SALE DEED / WILL ===

Use when the document is a private contract or testamentary instrument.

**<AGREEMENT TITLE>**

(Examples: "AGREEMENT TO SELL", "MEMORANDUM OF UNDERSTANDING",
"LEASE DEED", "POWER OF ATTORNEY", "LAST WILL AND TESTAMENT")

**THIS <AGREEMENT / DEED / WILL> IS MADE AND EXECUTED ON THIS <date>**

**BETWEEN**

<First Party Full Name>

Age: <age>, Occupation: <occupation>

R/ Address: <residential address>

(hereinafter referred to as the "<First Party Role>")

**AND**

<Second Party Full Name>

Age: <age>, Occupation: <occupation>

R/ Address: <residential address>

(hereinafter referred to as the "<Second Party Role>")

(The title is at the TOP and is NOT re-appended at end by the
assembler.)

=== UNIVERSAL RULES ===

- Every element above must have a BLANK LINE after it (use \\n\\n).
- Do NOT prefix the block with `# ` (the H1) — bolded `**markdown**`
  only for titles and headers.
- Use 'R/ Address' (Indian-court convention for 'Residing at') in
  party blocks.
- If you are unsure which layout, decide in this order:
  1. "Police complaint" / "FIR" / mentions Police Inspector / SHO /
     Section 154 CrPC / Section 173 BNSS → Layout D.
  2. "Notice" / "Reply to legal notice" / "demand notice" → Layout B.
  3. "Agreement" / "MOU" / "Deed" / "Will" / "Lease" / "Sale deed" /
     "Power of attorney" → Layout C.
  4. Everything else (Suit, Petition, Writ, Application, Bail
     application, Section 200 CrPC magistrate complaint) → Layout A.""",
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


# --- Step 1.5: Extract key facts from uploaded document for fact-grounded drafting ---
#
# When the user has attached a document (PDF, DOCX) or pasted long context, the
# raw 30K-char text is too long for parallel section-generation LLMs to reliably
# extract specific facts (names, amounts, dates) — they default to placeholders.
# So we run ONE fast Gemini Flash call up front to pull the structured entities
# and prepend them to every per-section prompt as a non-negotiable list.
#
# This is the key fix for BUG-02 (drafting agent ignores PDF facts).

_CASE_FACTS_PROMPT = """You are a legal entity extractor. Read the user-provided
document/context and produce a CONCISE list of the case-specific entities the
drafter MUST use verbatim. Pull only what is present; do NOT invent.

INPUT:
{facts_text}

Return a Markdown bullet list with these labels (omit any that are absent):
- **Court**: full court name as stated
- **Case Number**: case/suit number as stated
- **Plaintiff**: full name(s) — first occurrence's wording
- **Plaintiff Address**: as stated (one line)
- **Defendant**: full name(s)
- **Defendant Address**: as stated (one line)
- **Cause of Action / Claim**: 1-line summary (e.g. "recovery of Rs. 10L friendly loan")
- **Principal Amount**: with figure and words as stated
- **Interest Rate**: as claimed
- **Key Date - Loan/Agreement**: DD-Mon-YYYY
- **Key Date - Demand/Notice**: DD-Mon-YYYY
- **Key Date - Cause of Action accrual**: DD-Mon-YYYY
- **Witnesses**: comma-separated names
- **Statutory Provisions invoked**: e.g. "Order VII Rule 1 CPC"
- **Counsel**: as stated
- **Filing/Verification Date**: DD-Mon-YYYY
- **Other key facts**: any other specific details (sections, addresses, IDs)

Output ONLY the bullet list. No preamble, no explanations.
"""


async def _extract_case_facts(user_facts: str) -> str:
    """Pull structured entities from the user-provided document/context.

    Returns a markdown bullet list of case-specific facts (names, amounts, dates,
    court, etc.) that the section-generation LLMs must use verbatim. Used to
    prevent placeholder leakage when the underlying PDF text is long enough that
    parallel LLM calls might skim past specific values.

    Returns "" on failure (caller should treat as no extracted facts).
    """
    if not user_facts.strip():
        return ""
    try:
        with log_time(log, "Case-fact extraction"):
            llm = get_gemini_flash(temperature=0.0)
            prompt = ChatPromptTemplate.from_template(_CASE_FACTS_PROMPT)
            chain = prompt | llm
            response = await asyncio.wait_for(
                chain.ainvoke({"facts_text": user_facts[:30000]}),
                timeout=20,
            )
        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "extract_case_facts", response)
        extracted = response.content.strip()
        log.info("Case facts extracted",
                 chars=len(extracted), bullets=extracted.count("- **"))
        return extracted
    except Exception as e:
        log.warning("Case-fact extraction failed; falling back to raw text",
                    error=str(e)[:200])
        return ""


# --- Step 2: Template Selection (GPT-4o-mini with previews + validation) ---

TEMPLATE_SELECTION_PROMPT = """You are a legal AI assistant selecting the best legal document template for an Indian law drafting task.

User wants to draft: {query}

{facts_summary}Select the MOST relevant template based on (a) the user's instruction, (b) the case context (when supplied above). The case context tells you what KIND of dispute/matter the user is dealing with (e.g. money recovery, divorce, bail, property dispute) — use that to pick a template whose document type matches the user's actual case, NOT just keyword overlap with the question.

Each candidate shows the file path and a content preview:

{candidates}

Return only the file path of the best matching template."""


def _select_best_template(
    query: str, candidates: list[dict], user_facts: str = "",
) -> tuple[str, list[str]]:
    """Use Gemini Flash to select the most relevant template.

    Shows content previews alongside file paths for better selection.
    When `user_facts` is provided (PDF text, pasted context), a brief summary
    of the case context is included in the selection prompt so the LLM picks
    a template matching the user's actual case (BUG-05) — not just one whose
    preview shares keywords with the user's question.
    Returns (selected_source, all_valid_paths) for fallback support.
    """
    valid_paths = list(dict.fromkeys(c["_source"]["source"] for c in candidates))

    # Brief context from user_facts (first 1500 chars). Plenty for the LLM to
    # spot the case-type signals — court name, party titles, claim, statutory
    # references — without bloating the selection prompt.
    facts_summary = ""
    if user_facts.strip():
        facts_summary = (
            f"USER CASE CONTEXT (from uploaded document or pasted content — "
            f"use this to identify the case type, not the template's example):\n"
            f"{user_facts.strip()[:1500]}\n\n"
        )

    with log_time(log, "Template selection (LLM)"):
        # Build candidate list with previews
        candidate_lines = []
        for i, c in enumerate(candidates, 1):
            path = c["_source"]["source"]
            preview = c["_source"]["page_content"][:200].replace("\n", " ")
            candidate_lines.append(f"{i}. {path}\n   Preview: {preview}...")

        llm = get_gemini_flash(temperature=0.1).with_structured_output(
            TemplateSource, include_raw=True,
        )
        prompt = ChatPromptTemplate.from_template(TEMPLATE_SELECTION_PROMPT)
        chain = prompt | llm
        raw_and_parsed = chain.invoke({
            "query": query,
            "candidates": "\n".join(candidate_lines),
            "facts_summary": facts_summary,
        })
    from core.token_tracker import record as _record_tokens
    _record_tokens("Drafting", "select_template", raw_and_parsed.get("raw"))
    result = raw_and_parsed["parsed"]

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

async def _generate_outline(
    query: str, template_text: str, user_language: str = "en",
    user_facts: str = "", case_facts: str = "",
    format_block: str = "",
) -> DraftOutline:
    """Generate a structured outline with all sections for the document.

    Uses Gemini 2.5 Flash with structured output for reliable section list.

    When `case_facts` (structured bullets) and/or `user_facts` (raw text) are
    non-empty, the outline is tailored to those specific facts so e.g. a
    "Suit For Recovery Of Money" outline knows to include sections referring
    to the actual loan amount and dates.

    `format_block` carries layout/typographic conventions extracted from the
    chosen template (see _extract_format_spec). Empty string disables the
    block; the outline still works on template_text alone.
    """
    facts_block = ""
    if case_facts.strip() or user_facts.strip():
        parts = [
            "USER-PROVIDED FACTS — the outline must be tailored to THIS "
            "specific case, not the generic template scenario:"
        ]
        if case_facts.strip():
            parts.append("\nKEY ENTITIES:\n" + case_facts.strip())
        if user_facts.strip():
            parts.append("\nFULL DOCUMENT TEXT:\n" + user_facts[:25000])
        facts_block = "\n".join(parts) + "\n\n"

    with log_time(log, "Outline generation"):
        llm = get_drafting_llm().with_structured_output(
            DraftOutline, include_raw=True,
        )
        prompt = ChatPromptTemplate.from_messages([
            ("system", localize_prompt(DRAFT_OUTLINE_PROMPT, user_language)),
            ("user", "{facts_block}USER QUERY:\n{query}"),
            ("user",
             "Reference Template (use ONLY for STRUCTURE/section names — "
             "do NOT copy the template's facts/parties/amounts):\n{template}"),
            # Layout-spec block: distilled visual conventions from the
            # template, separated from the full template_text so the LLM
            # sees a clear "imitate these layout patterns" signal divorced
            # from the fact-isolation warning. Empty string when extraction
            # failed -- prompt still works on template_text alone.
            ("user", "{format_block}Current Date: {date}"),
        ])
        chain = prompt | llm
        try:
            raw_and_parsed = await chain.ainvoke({
                "query": query,
                "template": template_text,
                "date": str(date.today()),
                "facts_block": facts_block,
                "format_block": format_block,
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
            raw_and_parsed = None  # no token usage to record on fallback path
        else:
            from core.token_tracker import record as _record_tokens
            _record_tokens("Drafting", "outline", raw_and_parsed.get("raw"))
            outline = raw_and_parsed["parsed"]

    # Remove meta-sections that don't need LLM generation
    META_SECTION_KEYWORDS = ("index", "table of contents", "contents page")
    filtered = [s for s in outline.sections
                if not any(kw in s.title.lower() for kw in META_SECTION_KEYWORDS)]
    if len(filtered) < len(outline.sections):
        removed = [s.title for s in outline.sections if s not in filtered]
        log.info("Removed meta-sections from outline", removed=removed)
        outline.sections = filtered

    # NOTE: Procedural-section injection moved out of outline generation.
    # It now runs AFTER the doctrinal stance is generated (which enumerates
    # the procedural sections the pleading must carry) -- see
    # `_inject_procedural_sections` and its call after `_generate_doctrinal_stance`
    # in `drafting_node`.

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


# --- Pre-return validator ---
#
# Runs over the fully assembled draft, fixes cheap mojibake / placeholder
# leakage in-place, and returns a list of structural warnings for callers
# (logged + surfaced in the AgentResult). Designed to never raise -- a
# validator bug must not block delivery of the draft.

# Common UTF-8 -> cp1252 -> UTF-8 round-trip artefacts in Indian legal text.
_MOJIBAKE_REPLACEMENTS = [
    ("â€“", "–"),   # â€" -> en dash
    ("â€”", "—"),   # â€" -> em dash
    ("â€˜", "‘"),   # â€˜ -> left single quote
    ("â€™", "’"),   # â€™ -> right single quote
    ("â€œ", "“"),   # â€œ -> left double quote
    ("â€", "”"),   # â€ -> right double quote
    ("â€¦", "…"),   # â€¦ -> ellipsis
    ("Â ",       " "),   # Â  -> nbsp
    ("ï¿½", "?"),        # replacement char (no-info fallback)
]

# Internal LLM artefact placeholders that should never survive to the user.
# The section prompt forbids `[CITE: ...]` markers; this is the defensive
# fallback for the rare survivor. Substantive citation completeness checks
# (orphan tails, forbidden statute pairings, trailing prepositions) are now
# done by `core/self_refine.self_refine` against the typed DoctrinalStance.
_CITE_PLACEHOLDER_RE = re.compile(r"\[CITE:[^\]]*\]", flags=re.IGNORECASE)

# HTML tag cleanup. Gemini occasionally tries to fake centering/alignment in
# legal drafts by emitting <p align="center">TITLE</p>, <p align="right">...,
# or wrapping content in <div>/<span>/<center>. Our frontend renders markdown
# only — raw HTML shows up as ugly literal text. Strip every HTML-looking
# tag while preserving the inner content. Structural tags (<br>, <hr>) get
# converted to the markdown equivalent first so we don't lose line breaks.
_HTML_BR_RE = re.compile(r"<br\s*/?>", flags=re.IGNORECASE)
_HTML_HR_RE = re.compile(r"<hr\s*/?>", flags=re.IGNORECASE)
_HTML_ANY_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?/?>")


def validate_draft(
    full_draft: str,
    stance: "DoctrinalStance | None" = None,
) -> tuple[str, list[str]]:
    """Run cheap repairs and rule checks on the assembled draft.

    Returns (possibly-cleaned draft, list of warning messages).
    Never raises -- failures inside individual rules are logged and skipped.
    """
    warnings: list[str] = []
    cleaned = full_draft

    # --- Auto-fix: mojibake ---
    # The canonical fix for cp1252-misread-as-UTF-8: encode as cp1252 and
    # decode as UTF-8 again, recovering the original bytes. Skip if the round
    # trip would fail (string contains chars not in cp1252) -- those strings
    # are already clean. Fall back to the substring table for any survivors.
    if any(s in cleaned for s in ("â€", "Â ", "Ã©", "Ã ", "ï¿½")):
        try:
            roundtripped = cleaned.encode("cp1252", errors="strict").decode("utf-8", errors="strict")
            if roundtripped != cleaned:
                cleaned = roundtripped
                log.info("Validator: mojibake auto-fixed (cp1252->utf-8 roundtrip)")
        except (UnicodeEncodeError, UnicodeDecodeError):
            # Mixed encodings -- fall through to per-substring repair below.
            log.debug("Validator: full cp1252 roundtrip failed, using substring table")

    fixed_count = 0
    for bad, good in _MOJIBAKE_REPLACEMENTS:
        if bad in cleaned:
            cleaned = cleaned.replace(bad, good)
            fixed_count += 1
    if fixed_count:
        log.info("Validator: mojibake auto-fixed (substring fallback)",
                 patterns_fixed=fixed_count)

    # --- Auto-fix: strip HTML tags (frontend doesn't render raw HTML) ---
    # Order matters: convert <br>/<hr> to markdown equivalents first so we
    # don't lose line breaks, then strip any remaining tag (e.g. <p>, <div>,
    # <span>, <center>, attributes like align="center"/"right" that the LLM
    # uses to fake centering markdown can't produce). Inner text is always
    # preserved -- we never drop content, only the tag scaffolding.
    cleaned = _HTML_BR_RE.sub("\n", cleaned)
    cleaned = _HTML_HR_RE.sub("\n---\n", cleaned)
    cleaned, html_strip_count = _HTML_ANY_TAG_RE.subn("", cleaned)
    if html_strip_count:
        warnings.append(
            f"Stripped {html_strip_count} HTML tag(s) from draft. The "
            "section prompt forbids HTML -- if this keeps happening, the "
            "LLM is drifting; tighten Rule 12 in DRAFTING_SYSTEM_PROMPT."
        )

    # --- Auto-fix: strip [CITE: ...] markers + log if any survived ---
    cite_hits = _CITE_PLACEHOLDER_RE.findall(cleaned)
    if cite_hits:
        cleaned = _CITE_PLACEHOLDER_RE.sub("", cleaned)
        warnings.append(
            f"Stripped {len(cite_hits)} leftover [CITE: ...] placeholder(s). "
            "Drafting prompt was meant to prevent this -- check section "
            "prompts if it keeps happening."
        )

    # NOTE: orphan citation tails, forbidden statute pairings, trailing
    # prepositions, and stance compliance were retired from this validator.
    # They now flow through `core/self_refine.self_refine`, which audits
    # the draft against the typed DoctrinalStance + UserIntent (this is the
    # same dynamic critique loop used elsewhere). Adding a new "rule" =
    # extend the self_refine critic prompt, not this validator.

    if warnings:
        log.warning("Validator surfaced issues",
                    count=len(warnings),
                    sample=warnings[0][:120])

    return cleaned, warnings


# --- Step 3.25: Mandatory procedural section injection ---
#
# The LLM-generated outline sometimes drops procedural blocks that a real
# civil suit filing cannot omit (Schedule, Court Fee, List of Docs, etc.).
# This injector classifies the document type from query + outline title and
# appends any missing sections from the appropriate pack so every filing has
# the full skeleton -- regardless of which template was selected.

# Mandatory procedural sections are now enumerated by the doctrinal-stance
# LLM call (see `DoctrinalStance.procedural_sections`) instead of a per-doc-
# type hardcoded `_MANDATORY_PACKS` dict + regex doc-type classifier. The
# stance has full context (query + facts + template) and decides what the
# pleading needs under Indian procedural law — including whether a separate
# Interim Application under Order XXXIX CPC is required. Adding support
# for a new pleading type = extend the stance prompt's `procedural_sections`
# guidance; no doc-type substring matching, no per-type pack maintenance.


def _section_already_present(sections: list[SectionPlan], title: str) -> bool:
    """Fuzzy check: True if any existing section's title shares the head word.

    Avoids duplicates when the LLM emitted "Schedule A & B" and the stance
    suggests "Schedule of Properties". Compares lower-cased first
    significant word(s) — anything more strict over-inflates the outline.
    """
    incoming = title.lower().split()
    if not incoming:
        return False
    head = incoming[0]
    for s in sections:
        existing = s.title.lower()
        if head in existing:
            return True
    return False


def _inject_procedural_sections(
    outline: DraftOutline, stance: DoctrinalStance | None,
) -> list[SectionPlan]:
    """Append missing procedural sections from the doctrinal stance.

    When the stance call succeeded, its `procedural_sections` list is the
    source of truth for what the pleading must carry. We append any item
    the LLM-generated outline didn't already cover. Insertion order
    preserves the stance's enumeration (which the prompt asks be put in
    correct procedural order).

    When stance is None / has no procedural sections (e.g. the user asked
    for a simple legal notice that has no procedural attachments, or the
    stance call timed out), we leave the outline as the section-gen LLM
    produced it. No fallback regex; no hardcoded pack.
    """
    sections = list(outline.sections)
    if stance is None or not stance.procedural_sections:
        return sections

    appended: list[str] = []
    for ps in stance.procedural_sections:
        if _section_already_present(sections, ps.title):
            continue
        sections.append(SectionPlan(
            title=ps.title,
            description=ps.description,
            estimated_paragraphs=ps.estimated_paragraphs,
            needs_citations=ps.needs_citations,
        ))
        appended.append(ps.title)

    if appended:
        log.info("Injected procedural sections from doctrinal stance",
                 count=len(appended), titles=appended,
                 total_sections=len(sections))

    return sections


# --- Step 3.5: Doctrinal Stance (shared legal lane across sections) ---
#
# Sections are generated in parallel with no shared legal context, which led
# to contradictions within one draft — e.g. para 2.2 calling property
# "self-acquired" while para 4.3 called it "ancestral", or sections citing
# Sec 38 SRA for temporary injunction when Order XXXIX CPC is the right
# authority. The doctrinal stance is a one-shot Gemini Flash call that, given
# the facts + template + query, picks ONE legal lane for the draft and
# enumerates statutes/cases to use vs avoid. Every parallel section call gets
# this stance JSON prepended, so all sections plead from the same theory.

_DOCTRINAL_STANCE_SYSTEM = """You are a senior Indian litigator setting the
legal lane for ONE draft. Read the facts and the user's query, then commit to
a single coherent theory of the case. Other section-writers will follow your
stance verbatim — contradictions in their output are caused by ambiguity in
yours, so be decisive.

Return a JSON object with these keys:

- "property_lane" (string): for partition / inheritance / property suits, one
  of "self_acquired_intestate", "self_acquired_testamentary",
  "coparcenary_ancestral_2005", "coparcenary_ancestral_pre_2005",
  "joint_tenancy", "not_applicable". Pick exactly ONE.
- "injunction_lane" (string): "temporary_only", "permanent_only", "both",
  "not_applicable". A temporary injunction restrains conduct during
  pendency (Order XXXIX CPC); a permanent injunction is the final relief
  (Section 38 SRA). Pick what the user actually asked for.
- "applicable_statutes" (list of strings): every statute the draft SHOULD
  cite. Use full names + section numbers, e.g.
  "Section 8 + Schedule, Hindu Succession Act, 1956".
- "non_applicable_statutes" (list of strings): statutes that LOOK related
  but are wrong for this fact pattern. Always include here the alternatives
  to your chosen lanes (e.g. if injunction_lane is "temporary_only", list
  "Section 38, Specific Relief Act, 1963 — applies only to permanent
  injunction, not the temporary injunction sought here").
- "key_cases" (list of {{name, citation, holding}}): 3-6 real Indian SC/HC
  cases you will cite. Use confident citations only; if unsure, omit.
  Suggested anchors (cite only if relevant to the fact pattern):
    * Vineeta Sharma v. Rakesh Sharma, (2020) 9 SCC 1 — daughter coparcenary
    * Smt. Sitabai v. Ramchandra, AIR 1970 SC 343 — adopted child equal rights
    * Dalpat Kumar v. Prahlad Singh, (1992) 1 SCC 719 — three-fold injunction test
    * Sawarni v. Inder Kaur, (1996) 6 SCC 223 — mutation does not confer title
    * Wander Ltd. v. Antox India, 1990 (Supp) SCC 727 — interim injunction principles
- "must_plead" (list of strings): specific facts/elements every section
  writer must include where relevant (e.g. "Section 11 HAMA giving-and-taking
  ceremony with date and adoptive parents named").
- "must_not_plead" (list of strings): things to avoid (e.g. "do not invoke
  the 2005 HSA amendment when property_lane is self_acquired_intestate;
  the amendment governs coparcenary, not self-acquired property").
- "court_fee_rule" (string): one sentence on the court fee basis (e.g.
  "Section 6(vii) Maharashtra Court Fees Act, 1959 — ad valorem on share's
  market value since plaintiff is dispossessed from one flat").
- "footer_kind" (string): the footer convention this draft must end with.
  Pick exactly ONE based on the document type:
    "court_filing"  → plaints, petitions, written statements, bail
                      applications, writ petitions, appeals, revisions,
                      reviews — anything FILED in court. Footer:
                      Place / Date / Signature of the Petitioner /
                      Applicant / Through Counsel: [Advocate Name].
    "legal_notice"  → Section 138 NI notice, demand notice, eviction
                      notice, statutory notices. NOT filed in court;
                      sent by registered post. Footer signed by counsel
                      directly ("Yours sincerely, Sd. [Advocate Name],
                      [Enrolment No.]"). NO "Petitioner/Applicant" block.
    "agreement"     → contracts, MOUs, lease deeds, sale deeds, NDAs,
                      partnership deeds, settlement agreements. Footer:
                      all parties' signatures + 2 attesting witness
                      blocks. NO court-filing language.
    "will"          → wills, codicils. Footer: testator's signature +
                      2 attesting witness blocks per Section 63 of the
                      Indian Succession Act, 1925.
    "none"          → other document types where no automatic footer
                      should be added (e.g. legal opinion memo).
  DO NOT default to "court_filing" — a legal notice with a court-filing
  footer is broken output.
- "procedural_sections" (list of {{title, description, estimated_paragraphs,
  needs_citations}}): EVERY mandatory procedural section this pleading
  must carry under the Indian Code (CPC / BNSS-CrPC / SRA / Court Fees Act /
  Order XXXIX, etc.). Be exhaustive — if anything is missing the draft is
  not court-filing-ready. Examples by pleading type:
    * Civil suit / plaint → Schedule of Properties; Valuation and Court
      Fee; List of Documents (Order VII Rule 14 / Order XI Rule 14 CPC);
      Verification (Order VI Rule 15 CPC); Affidavit in Support (Order
      XIX Rule 3 CPC, notarised).
    * When ANY temporary / interim / ad-interim injunction is prayed for
      (whether in a suit, writ, or appeal) → add a separate Interim
      Application under Order XXXIX Rules 1 & 2 CPC with its own
      affidavit and the three-fold test (prima facie case, balance of
      convenience, irreparable injury).
    * Bail application → Verification by accused; List of Documents
      (FIR copy, prior bail orders).
    * Writ / PIL → Verification; List of Documents; Affidavit; Annexures
      Index.
    * Legal notice → Notarisation block; Signature block of counsel.
    * Appeal / revision / review → Memo of grounds; Application for
      condonation of delay if filed beyond limitation; Index; Verification.
  Include description + estimated paragraphs + needs_citations for each.
  The drafting pipeline appends any section listed here that isn't already
  in the outline.

Be terse. JSON only. No prose around it.
"""


class DoctrinalCase(BaseModel):
    name: str = Field(..., description="Case name (e.g. 'Vineeta Sharma v. Rakesh Sharma')")
    citation: str = Field(..., description="Full citation (e.g. '(2020) 9 SCC 1')")
    holding: str = Field(..., description="One-line holding")


class ProceduralSectionPlan(BaseModel):
    """A mandatory procedural section the pleading must carry.

    Read by `_inject_procedural_sections` and appended to the outline if
    not already present. Replaces the hardcoded `_MANDATORY_PACKS` dict.
    """
    title: str = Field(..., description="Section title (e.g. 'Schedule of Properties')")
    description: str = Field(..., description="One-paragraph guidance for the section writer")
    estimated_paragraphs: int = Field(2, ge=1, le=10)
    needs_citations: bool = Field(False)


class DoctrinalStance(BaseModel):
    property_lane: str = Field("not_applicable")
    injunction_lane: str = Field("not_applicable")
    applicable_statutes: List[str] = Field(default_factory=list)
    non_applicable_statutes: List[str] = Field(default_factory=list)
    key_cases: List[DoctrinalCase] = Field(default_factory=list)
    must_plead: List[str] = Field(default_factory=list)
    must_not_plead: List[str] = Field(default_factory=list)
    court_fee_rule: str = Field("")
    procedural_sections: List[ProceduralSectionPlan] = Field(
        default_factory=list,
        description="Mandatory procedural sections this pleading must carry. "
                    "Populated by the doctrinal-stance LLM call. The drafting "
                    "pipeline injects any missing section from this list into "
                    "the outline before parallel section generation.",
    )
    footer_kind: str = Field(
        "court_filing",
        description="What kind of footer this draft needs. One of: "
                    "'court_filing' (plaints, petitions, written statements, "
                    "bail applications, appeals, writs, Section 200 CrPC / "
                    "Section 223 BNSS magistrate complaints — anything filed "
                    "in court): Place / Date / Signature of the Petitioner / "
                    "Through Counsel; "
                    "'legal_notice' (Section 138 NI Act notice, demand notice, "
                    "vacate notice, reply to legal notice): no court footer; "
                    "signed by counsel directly with 'Yours sincerely / Sd. / "
                    "[Advocate Name] / [Enrolment No.]'; "
                    "'police_complaint' (FIR registration application under "
                    "Section 154 CrPC / Section 173 BNSS, complaint addressed "
                    "to Station House Officer / Police Inspector — NOT a "
                    "magistrate complaint): no court footer; signed by the "
                    "COMPLAINANT (not counsel) with 'Yours faithfully / Sd. / "
                    "[Complainant Name] / Place / Date'; "
                    "'agreement' (contracts, MOUs, leases, sale deeds, NDAs): "
                    "no court footer; signed by all parties with witness lines; "
                    "'will' (wills, codicils): testator + 2 attesting witnesses; "
                    "'none' (other / unknown): no automatic footer. "
                    "Pick based on the document type — DO NOT default to "
                    "'court_filing' for a notice, police complaint, or "
                    "agreement. DISAMBIGUATION: When the user says 'police "
                    "complaint' / 'FIR' / mentions Police Inspector / SHO / "
                    "Station House Officer / Section 154 CrPC / Section 173 "
                    "BNSS, use 'police_complaint' (letter format addressed to "
                    "police). Only use 'court_filing' for a 'complaint' when "
                    "the user explicitly says 'Section 200 CrPC' / 'Section "
                    "223 BNSS' / 'magistrate complaint' / 'private complaint "
                    "before Magistrate' — those are filed in court.",
    )


async def _generate_doctrinal_stance(
    query: str,
    doc_title: str,
    user_facts: str = "",
    case_facts: str = "",
) -> DoctrinalStance | None:
    """One-shot Flash call producing the legal lane the whole draft will follow.

    Returns None on failure — section generation falls back to template-only
    guidance, same as before this step existed.
    """
    facts_block = ""
    if case_facts.strip():
        facts_block += "KEY ENTITIES:\n" + case_facts.strip() + "\n\n"
    if user_facts.strip():
        facts_block += "FULL DOCUMENT TEXT:\n" + user_facts[:8000]

    with log_time(log, "Doctrinal stance generation"):
        try:
            llm = get_drafting_llm().with_structured_output(
                DoctrinalStance, include_raw=True,
            )
            prompt = ChatPromptTemplate.from_messages([
                ("system", _DOCTRINAL_STANCE_SYSTEM),
                ("user",
                 "USER QUERY:\n{query}\n\n"
                 "DOCUMENT TYPE (from template selection): {doc_title}\n\n"
                 "USER-PROVIDED FACTS (may be empty):\n{facts_block}"),
            ])
            chain = prompt | llm
            raw_and_parsed = await asyncio.wait_for(
                chain.ainvoke({
                    "query": query,
                    "doc_title": doc_title,
                    "facts_block": facts_block or "(none -- proceed from query alone)",
                }),
                timeout=30,
            )
        except Exception as e:
            log.warning("Doctrinal stance generation failed -- continuing without it",
                        error=str(e)[:200])
            return None

        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "doctrinal_stance", raw_and_parsed.get("raw"))
        stance = raw_and_parsed["parsed"]
        log.info("Doctrinal stance generated",
                 property_lane=stance.property_lane,
                 injunction_lane=stance.injunction_lane,
                 applicable_count=len(stance.applicable_statutes),
                 non_applicable_count=len(stance.non_applicable_statutes),
                 cases=len(stance.key_cases))
        return stance


async def _verify_stance_cases(stance: DoctrinalStance) -> DoctrinalStance:
    """Verify stance.key_cases against the judgment ES corpus in parallel.

    The doctrinal-stance LLM picks "real Indian SC/HC cases" without
    retrieval grounding, so a confident-sounding fabricated citation
    (e.g. "S.B. Gurbaksh Singh v. Union of India, (1976) 2 SCC 104")
    can land in stance.key_cases. The drafting section prompt then
    USES those cases verbatim, and self_refine WHITELISTS the stance
    cases — so a fabrication slips end-to-end with no corpus check.

    Per case, fire two parallel ES lookups:
      * search_by_citation(c.citation) — exact lookup on the citation /
        case_number keyword fields
      * search_by_party_names(petitioner, respondent) — fallback when
        the citation format is unusual but the parties are real

    Drop any case where BOTH lookups return zero hits AND the case is
    not in our small high-confidence anchor allowlist (the cases the
    stance prompt explicitly names as exemplars — these are vetted).

    Returns a new DoctrinalStance with key_cases filtered. Never raises.
    """
    if not stance.key_cases:
        return stance

    # Cases the stance prompt itself names as anchor exemplars are
    # known-real and don't need ES verification (saves 5 ES calls).
    _STANCE_PROMPT_ANCHORS = {
        ("vineeta sharma", "rakesh sharma"),
        ("smt. sitabai", "ramchandra"),
        ("dalpat kumar", "prahlad singh"),
        ("sawarni", "inder kaur"),
        ("wander", "antox"),
    }

    def _parties(name: str) -> tuple[str, str]:
        # "X v. Y" or "X vs Y" — return (petitioner, respondent) lowercased
        norm = name.lower().replace(" v. ", " v ").replace(" vs ", " v ")
        if " v " in norm:
            pet, _, res = norm.partition(" v ")
            return pet.strip(), res.strip()
        return name.lower().strip(), ""

    async def _verify_one(case) -> tuple[bool, str]:
        pet, res = _parties(case.name)
        # Anchor allowlist — skip ES round-trip.
        for a_pet, a_res in _STANCE_PROMPT_ANCHORS:
            if a_pet in pet and (not a_res or a_res in res):
                return True, "anchor_allowlist"
        # ES verification in parallel
        try:
            from tools.shared.judgment_search import (
                search_by_citation, search_by_party_names,
            )
            cite_hits = await asyncio.to_thread(
                search_by_citation, case.citation, 2,
            )
            party_hits: list = []
            if pet and res:
                try:
                    party_hits = await asyncio.to_thread(
                        search_by_party_names, pet, res, None, None, 2,
                    )
                except Exception:
                    party_hits = []
            if cite_hits or party_hits:
                return True, f"es_hits={len(cite_hits)}+{len(party_hits)}"
            return False, "es_zero_hits"
        except Exception as e:
            # If ES is unreachable, fail-OPEN (keep the case). We'd
            # rather have a potentially-fabricated citation than a
            # citation-less draft when ES is down.
            log.warning("Stance verification ES call failed; keeping case",
                        case=case.name[:80], error=str(e)[:120])
            return True, "es_unreachable"

    verdicts = await asyncio.gather(
        *[_verify_one(c) for c in stance.key_cases],
        return_exceptions=False,
    )

    verified_cases = []
    dropped = []
    for case, (ok, reason) in zip(stance.key_cases, verdicts):
        if ok:
            verified_cases.append(case)
        else:
            dropped.append(f"{case.name[:60]} ({reason})")

    if dropped:
        log.warning("Dropped unverified case citations from stance",
                    dropped_count=len(dropped),
                    kept_count=len(verified_cases),
                    sample=dropped[:3])

    # Return a NEW stance object with filtered cases (Pydantic immutable
    # via model_copy + update); preserves everything else verbatim.
    verified_stance = stance.model_copy(update={"key_cases": verified_cases})
    return verified_stance


def _format_stance_for_section(stance: DoctrinalStance | None) -> str:
    """Render the stance as a compact prompt block for each section call."""
    if stance is None:
        return ""
    lines = ["DOCTRINAL STANCE — every section in this draft must follow this lane:\n"]
    if stance.property_lane and stance.property_lane != "not_applicable":
        lines.append(f"- PROPERTY LANE: {stance.property_lane}")
    if stance.injunction_lane and stance.injunction_lane != "not_applicable":
        lines.append(f"- INJUNCTION LANE: {stance.injunction_lane}")
    if stance.court_fee_rule:
        lines.append(f"- COURT FEE: {stance.court_fee_rule}")
    if stance.applicable_statutes:
        lines.append("- USE THESE STATUTES (cite by name + section):")
        for s in stance.applicable_statutes:
            lines.append(f"    * {s}")
    if stance.non_applicable_statutes:
        lines.append("- DO NOT CITE THESE STATUTES (wrong for this fact pattern):")
        for s in stance.non_applicable_statutes:
            lines.append(f"    * {s}")
    if stance.key_cases:
        lines.append("- USE THESE CASE LAWS (cite by name + citation; never as [CITE: ...]):")
        for c in stance.key_cases:
            lines.append(f"    * {c.name}, {c.citation} -- {c.holding}")
    if stance.must_plead:
        lines.append("- MUST PLEAD where relevant:")
        for m in stance.must_plead:
            lines.append(f"    * {m}")
    if stance.must_not_plead:
        lines.append("- MUST NOT PLEAD:")
        for m in stance.must_not_plead:
            lines.append(f"    * {m}")
    return "\n".join(lines) + "\n\n"


# --- Step 3.7: Layout/Format extractor (per-template, cached) ---
#
# The chosen template_text is a fully-formatted exemplar of an Indian legal
# document -- it carries layout signals (centered "PRAYER" / "VERIFICATION"
# labels, right-aligned "______Plaintiff" tags, numbered "That ..." paragraphs,
# verbatim prayer/verification clauses, signature blocks). The outline + section
# prompts already pass the full template_text, but they tell the LLM "use it
# only for structure" because of BUG-02 (the LLM used to copy fake names and
# placeholder amounts straight from the template into the draft).
#
# This extractor distills the LAYOUT separately from the facts: one Flash call
# reads the template, produces a FormatSpec (alignment, numbering, openers,
# signature/verification blocks), which is then injected as its own block in
# the outline + section prompts. The LLM gets a clear "imitate this layout
# verbatim" signal divorced from the fact-isolation warnings, dramatically
# improving visual fidelity to real Indian court conventions.
#
# Cached per template_source (the ES `source` key) -- one extraction per
# template ever, then near-zero cost across all subsequent drafts that pick
# the same template.

class FormatSpec(BaseModel):
    """Layout/typographic conventions extracted from a template's exemplar.

    All fields are short strings or compact patterns -- never contain actual
    facts (names, dates, amounts). Placeholders like `[Plaintiff Name]` or
    `___` are used where the real document would carry case-specific data.
    """
    court_header_alignment: str = Field(
        "centered",
        description="Alignment of the court name and case-number header. "
                    "One of 'centered', 'left', 'right'.",
    )
    section_label_style: str = Field(
        "uppercase_centered",
        description="How section labels like PRAYER / VERIFICATION are styled. "
                    "Examples: 'uppercase_centered', 'titlecase_left', "
                    "'bold_left', 'underlined_centered'.",
    )
    paragraph_numbering: str = Field(
        "1., 2., 3.",
        description="Exact glyph pattern for numbered paragraphs. Examples: "
                    "'1., 2., 3.' or '(1), (2), (3)' or 'i, ii, iii'.",
    )
    paragraph_opener: str = Field(
        "",
        description="Verbatim opener that prefixes each numbered paragraph "
                    "(e.g. 'That '). Empty if no opener.",
    )
    sub_point_style: str = Field(
        "a., b., c.",
        description="Glyph pattern for sub-points within a paragraph or prayer "
                    "clause. Examples: 'a., b., c.' or '(a), (b), (c)'.",
    )
    party_block_tag_alignment: str = Field(
        "right",
        description="Alignment of the '______Plaintiff' / '______Defendant' "
                    "tags that close each party's block.",
    )
    party_block_separator: str = Field(
        "VERSUS",
        description="The divider phrase between plaintiff and defendant blocks.",
    )
    prayer_opener: str = Field(
        "",
        description="Verbatim sentence that opens the Prayer section. Use "
                    "[Hon'ble Court] etc. placeholders for any names. Example: "
                    "'It is therefore most humbly prayed that this Hon'ble "
                    "Court may be pleased to:'",
    )
    prayer_section_label: str = Field(
        "PRAYER",
        description="Exact label used for the Prayer section header.",
    )
    verification_label: str = Field(
        "VERIFICATION",
        description="Exact label for the verification section.",
    )
    verification_template: str = Field(
        "",
        description="1-3 line verification clause skeleton with [Plaintiff Name] "
                    "placeholders for case-specific data.",
    )
    signature_block: str = Field(
        "",
        description="Multi-line signature block skeleton (alignment hint may "
                    "be embedded as `[right-aligned]` etc.).",
    )
    place_date_format: str = Field(
        "PLACE: [City]\nDATE: [Date]",
        description="Format of the PLACE/DATE footer line(s).",
    )
    schedule_notation: str = Field(
        "",
        description="How Schedule annexes are referenced (e.g. 'Schedule A: "
                    "Description of property...'). Empty if not applicable.",
    )
    other_conventions: List[str] = Field(
        default_factory=list,
        description="Any other notable layout patterns (e.g. 'capitalised "
                    "RESPECTFULLY SHOWETH: before paragraph 1', 'each prayer "
                    "clause indented under sub-letter'). Short observations only.",
    )


_FORMAT_EXTRACTOR_SYSTEM = """You are a legal-document layout analyst for
Indian court filings. Extract ONLY the FORMATTING and LAYOUT conventions
from the supplied document exemplar.

CRITICAL: Do NOT extract any facts, names, dates, amounts, court locations,
party details, case numbers, or substantive content -- those belong to a
DIFFERENT case and would contaminate the user's draft. Where the exemplar
has specific values, substitute placeholders like `[Plaintiff Name]`,
`[Court Name]`, `[Date]`, `[Amount]`, `[Address]`.

Capture only the visual / typographic / structural conventions:
- Alignment patterns (centered / left / right) for headers, party tags,
  signature blocks
- Exact glyph pattern for paragraph numbering and sub-points
- Verbatim opening phrases for paragraphs, prayer, verification (with
  placeholders for any specific values)
- Section label styling (caps, alignment, decoration)
- Signature and PLACE/DATE block format
- Schedule annexure notation
- Any other unusual layout patterns

The output will be used to guide the LAYOUT of a NEW draft about a
DIFFERENT case. Imitable patterns ONLY -- no facts.
"""


@lru_cache(maxsize=256)
def _format_spec_cache_key(template_source: str, content_fingerprint: str) -> str:
    """Cache key combining template path + a short content hash so that
    re-ingesting a template into ES invalidates its cached FormatSpec."""
    return f"{template_source}::{content_fingerprint}"


_FORMAT_SPEC_STORE: dict[str, "FormatSpec | None"] = {}


async def _extract_format_spec(
    template_source: str, template_text: str,
) -> FormatSpec | None:
    """One-shot Flash call producing the template's layout conventions.

    Cached per (template_source, content_hash) so re-runs of the same
    template are free. Returns None on failure -- outline + section gen
    fall back to format-block-less prompts (same as before this step
    existed).
    """
    if not template_source or not template_text:
        return None

    import hashlib
    fp = hashlib.sha256(template_text[:200].encode("utf-8")).hexdigest()[:8]
    cache_key = _format_spec_cache_key(template_source, fp)
    if cache_key in _FORMAT_SPEC_STORE:
        log.debug("Format spec cache hit", source=template_source[-40:])
        return _FORMAT_SPEC_STORE[cache_key]

    with log_time(log, "Format spec extraction"):
        try:
            llm = get_drafting_llm().with_structured_output(
                FormatSpec, include_raw=True,
            )
            prompt = ChatPromptTemplate.from_messages([
                ("system", _FORMAT_EXTRACTOR_SYSTEM),
                ("user", "EXEMPLAR (analyse layout only, IGNORE all facts):\n{template}"),
            ])
            chain = prompt | llm
            # Cap the template fed to extractor at 6000 chars -- enough to
            # see headers + first paragraphs + prayer + verification.
            raw_and_parsed = await asyncio.wait_for(
                chain.ainvoke({"template": template_text[:6000]}),
                timeout=30,
            )
        except Exception as e:
            log.warning("Format spec extraction failed -- continuing without",
                        error=str(e)[:200], source=template_source[-40:])
            _FORMAT_SPEC_STORE[cache_key] = None
            return None

        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "format_spec", raw_and_parsed.get("raw"))
        spec = raw_and_parsed["parsed"]
        _FORMAT_SPEC_STORE[cache_key] = spec
        log.info("Format spec extracted",
                 source=template_source[-40:],
                 numbering=spec.paragraph_numbering,
                 opener=spec.paragraph_opener[:30],
                 prayer_label=spec.prayer_section_label)
        return spec


def _format_layout_block(spec: FormatSpec | None) -> str:
    """Render the FormatSpec as a compact prompt block for outline + section gen.

    Empty string when spec is None -- callers pass it as a template var so
    the prompt remains valid even when extraction failed.
    """
    if spec is None:
        return ""
    lines = [
        "TEMPLATE LAYOUT CONVENTIONS (use these for visual style only -- "
        "all facts must come from the USER QUERY / FACTS sections, NEVER "
        "from the template's example values):",
        f"- Court header alignment: {spec.court_header_alignment}",
        f"- Section labels: {spec.section_label_style} "
        f"(e.g. \"{spec.prayer_section_label}\", \"{spec.verification_label}\")",
        f"- Numbered paragraphs: {spec.paragraph_numbering}"
        + (f" -- prefix each with \"{spec.paragraph_opener}\"" if spec.paragraph_opener else ""),
        f"- Sub-points within paragraphs/prayer: {spec.sub_point_style}",
        f"- Party-block tag alignment: {spec.party_block_tag_alignment} "
        f"(divider: \"{spec.party_block_separator}\")",
    ]
    if spec.prayer_opener:
        lines.append(f"- Prayer opener (verbatim): {spec.prayer_opener}")
    if spec.verification_template:
        lines.append(f"- Verification skeleton: {spec.verification_template}")
    if spec.signature_block:
        # Indent multi-line signature for readability
        sig_lines = spec.signature_block.split("\n")
        lines.append(f"- Signature block:")
        for sl in sig_lines:
            lines.append(f"    {sl}")
    if spec.place_date_format:
        lines.append(f"- Footer (PLACE/DATE) format: {spec.place_date_format}")
    if spec.schedule_notation:
        lines.append(f"- Schedule annexure notation: {spec.schedule_notation}")
    if spec.other_conventions:
        lines.append("- Other conventions:")
        for c in spec.other_conventions:
            lines.append(f"    * {c}")
    lines.append(
        "Apply these conventions verbatim where applicable. Substitute "
        "[placeholders] for any case-specific values."
    )
    # IMPORTANT separation: this block governs LAYOUT only. Substantive
    # depth (paragraph count, detail, statutory citations, case-law
    # quotes) must follow the per-section targets the system prompt
    # specifies -- NOT the template's brevity. Real court templates are
    # terse exemplars; user drafts must be rich pleadings.
    lines.append(
        "DEPTH RULE: these conventions govern visual LAYOUT only. Do NOT "
        "let the template's brevity reduce the substantive depth of your "
        "pleading -- each section must hit its target paragraph count and "
        "include the full statutory + factual + case-law analysis the "
        "system prompt specifies."
    )
    return "\n".join(lines) + "\n\n"


# --- Step 4: Generate Each Section ---

async def _generate_section(
    query: str,
    template_text: str,
    section: SectionPlan,
    section_index: int,
    total_sections: int,
    outline: DraftOutline,
    user_language: str = "en",
    user_facts: str = "",
    case_facts: str = "",
    stance_block: str = "",
    format_block: str = "",
    start_para_num: int = 1,
) -> tuple[str, int]:
    """Generate one section of the document in full detail.

    Returns (section_text, tokens_consumed).

    Prompt order is critical: user-provided facts MUST appear before the
    reference template, otherwise the LLM apes the template's placeholders
    instead of inserting the real names/dates/amounts (BUG-02). When
    `case_facts` (a pre-extracted structured bullet list) is supplied, it is
    placed ABOVE the raw text so the LLM cannot miss key entities.
    """
    outline_summary = "\n".join(
        f"  {i+1}. {s.title}" for i, s in enumerate(outline.sections)
    )

    facts_block = ""
    if case_facts.strip() or user_facts.strip():
        parts = ["USER-PROVIDED FACTS — MANDATORY VALUES YOU MUST USE VERBATIM."]
        parts.append(
            "These are the REAL names, dates, amounts, addresses, courts, "
            "and statutory references for THIS case. The reference template "
            "below contains DIFFERENT (illustrative or fictional) values — "
            "you MUST IGNORE the template's specifics in favor of these:"
        )
        if case_facts.strip():
            parts.append("\nKEY ENTITIES (extracted from user's document):\n" + case_facts.strip())
        if user_facts.strip():
            parts.append(
                "\nFULL DOCUMENT TEXT (for additional context — refer back to "
                "this for any detail not in the KEY ENTITIES list):\n"
                + user_facts[:25000]
            )
        facts_block = "\n".join(parts) + "\n\n"

    facts_reminder = (
        "USE THE USER-PROVIDED FACTS ABOVE for all names, dates, amounts, "
        "addresses, court details, statutory references. Do NOT wrap any "
        "real value from the FACTS in [brackets]. Use [placeholder] only "
        "for information that is genuinely missing from the FACTS. "
        if (case_facts.strip() or user_facts.strip()) else ""
    )

    # Section-specific override for the cause-title block. Sagar's bug #6
    # (2026-06-16): the layout rules buried at rule #11 in DRAFTING_SYSTEM_PROMPT
    # were being skimmed past — drafts came back with squashed party blocks,
    # no "IN THE MATTER OF:" header, no bolded subject line, no R/ Address
    # convention. The reminder below repeats the must-have layout in the
    # section prompt itself, where the LLM is paying full attention.
    _CAUSE_TITLE_KEYWORDS = (
        "cause title", "memo of parties", "memorandum of parties",
        "memo of party", "parties", "title of the suit",
        "title of the petition", "court header",
    )
    _section_title_lc = (section.title or "").lower()
    _is_cause_title_section = any(
        kw in _section_title_lc for kw in _CAUSE_TITLE_KEYWORDS
    )
    cause_title_override = ""
    if _is_cause_title_section:
        cause_title_override = (
            "\n\n=== CRITICAL SECTION-SPECIFIC LAYOUT OVERRIDE — "
            "THE CAUSE TITLE BLOCK MUST FOLLOW THIS EXACT MARKDOWN ===\n\n"
            "Every element gets its OWN paragraph (i.e. blank line after it).\n"
            "CommonMark renderers collapse single newlines — DO NOT use them.\n\n"
            "Mandatory layout (Sagar's bug #6, 2026-06-16):\n\n"
            "```\n"
            "**IN THE COURT OF <FORUM NAME>, AT <CITY>**\n"
            "\n"
            "**<SUIT/PETITION/COMPLAINT> NO. _______ OF <YEAR>**\n"
            "\n"
            "**IN THE MATTER OF:**\n"
            "\n"
            "<Plaintiff Full Name>\n"
            "\n"
            "Age: <age>, Occupation: <occupation>\n"
            "\n"
            "R/ Address: <residential address>\n"
            "\n"
            ".....Plaintiff / Petitioner\n"
            "\n"
            "**Versus**\n"
            "\n"
            "<Defendant Full Name>\n"
            "\n"
            "Age: <age>, Occupation: <occupation>\n"
            "\n"
            "R/ Address: <residential address>\n"
            "\n"
            ".....Defendant / Respondent\n"
            "\n"
            "**<SUBJECT HEADING — e.g. SUIT FOR COMPENSATION FOR MEDICAL "
            "NEGLIGENCE / WRIT PETITION UNDER ARTICLE 226 OF THE "
            "CONSTITUTION OF INDIA / COMPLAINT UNDER SECTION 138 NI ACT>**\n"
            "```\n\n"
            "Rules:\n"
            "1. Court name on its OWN line, BOLDED.\n"
            "2. Suit/Petition number on its OWN line, BOLDED.\n"
            "3. 'IN THE MATTER OF:' header, BOLDED, between case number "
            "   and parties — MANDATORY.\n"
            "4. Party blocks: name → age+occupation → R/ Address → "
            "   '.....Plaintiff/Defendant' designation, each its own "
            "   paragraph (blank line between).\n"
            "5. 'Versus' BOLDED as `**Versus**` (NOT in backticks, NOT in "
            "   a code fence).\n"
            "6. Subject heading at the END (after defendant designation), "
            "   BOLDED, its own paragraph.\n"
            "7. Use 'R/' (Residing at) — Indian court convention.\n"
            "8. Do NOT put the subject heading at the TOP as an H1 title.\n"
            "9. Do NOT use H1 (#) anywhere in the cause title — only "
            "   bolded markdown (`**...**`).\n"
            "\n"
            "Output ONLY this cause-title block for this section. No "
            "introductory paragraph, no explanation, no closing notes.\n"
            "=== END CAUSE-TITLE OVERRIDE ===\n"
        )

    with log_time(log, f"Section {section_index+1}/{total_sections}: {section.title}"):
        llm = get_drafting_llm()
        prompt = ChatPromptTemplate.from_messages([
            ("system", localize_prompt(DRAFTING_SYSTEM_PROMPT, user_language)),
            # FACTS FIRST — most prominent position (BUG-02)
            ("user", "{facts_block}USER INSTRUCTION:\n{query}"),
            # Stance block: shared legal lane across all parallel sections
            # (empty string when stance generation failed -- silent fallback).
            ("user", "{stance_block}Document: {doc_title}\nCourt: {court_details}"),
            ("user", "Full Document Outline:\n{outline_summary}"),
            # Template AFTER facts, explicitly framed as structure-only
            ("user",
             "REFERENCE TEMPLATE (use ONLY for STRUCTURE, formatting style, "
             "section ordering, and clause organization — DO NOT copy any "
             "names, dates, amounts, addresses, or factual content from the "
             "template into the draft. The template's specifics are illustrative "
             "and UNRELATED to the user's case):\n{template}"),
            # Layout-spec block: distilled visual conventions from the
            # template (alignment, numbering glyphs, prayer/verification
            # openers, signature block). Distinct from the template itself
            # so the LLM treats it as "imitate this layout verbatim" rather
            # than getting lost in the warning about template facts.
            ("user", "{format_block}"),
            ("user",
             "NOW WRITE section {section_num} of {total} IN FULL DETAIL.\n"
             "{facts_reminder}\n\n"
             "## {section_title}\n{section_desc}\n\n"
             "PARAGRAPH NUMBERING — apply ONE of these schemes based on this "
             "section's role:\n\n"
             "  (A) **Substantive body sections** — Brief Facts, Statement of "
             "Facts, Cause of Action, Issues, Grounds, Pleadings, Defences, "
             "Legal Submissions, Counter-Submissions, Reply on Merits. Use "
             "the GLOBAL paragraph counter: this section's first numbered "
             "paragraph MUST be number {start_para_num}, continue from there "
             "for subsequent paragraphs in this section, DO NOT restart at 1. "
             "Render the number in the response language's native script "
             "(Devanagari for Marathi/Hindi/Sanskrit, Tamil for Tamil, etc.).\n\n"
             "  (B) **Prayer / Relief clause** — use the prayer's OWN scheme: "
             "fresh numbering starting at (a)/(b)/(c) or (i)/(ii)/(iii) or "
             "(1)/(2)/(3). NEVER continue the global body counter — the user "
             "expects clean (a), (b), (c) or (i), (ii), (iii) for relief "
             "clauses, not (34), (35), (36) carrying over from body paras.\n\n"
             "  (C) **Verification block** — single declaratory paragraph "
             "signed by the deponent. NO paragraph number on the verification "
             "statement itself. The deponent's signature line and place/date "
             "line are unnumbered too.\n\n"
             "  (D) **Court Fee Statement, Schedule of Properties, List of "
             "Documents, Affidavit-in-Support, Memo of Parties, Annexures "
             "Index** — descriptive procedural blocks. Either use a local "
             "scheme starting fresh (1, 2, 3 OR A, B, C OR i, ii, iii — "
             "depending on the section's tradition) OR unnumbered prose. "
             "DO NOT continue the global body counter into these — the user "
             "expects 1, 2, 3 fresh per section, not 39, 40, 41 spilling "
             "over from body paragraphs.\n\n"
             "Decide which scheme applies based on the section title above. "
             "When in doubt (e.g. an unfamiliar section name), prefer the "
             "section's own local scheme starting fresh.\n\n"
             "Expected paragraphs: {est_paragraphs}\n"
             "Needs case law citations: {needs_citations}"
             "{cause_title_override}"),
        ])
        chain = prompt | llm

        # Sections generate in parallel (Semaphore(3) + asyncio.gather), so
        # token streaming here produces an interleaved, unattributed stream
        # that the frontend can't reconstruct. token_reset from one section's
        # retry also wipes valid tokens from other concurrent sections. Use
        # chain.ainvoke instead — clients still see per-section status via the
        # drafting_progress events emitted in _gen_one(). Final response
        # streaming happens during orchestrator synthesis. See
        # docs/drafting_ux_improvement_plan.md (Phase A).
        response = await asyncio.wait_for(chain.ainvoke({
            "query": query,
            "doc_title": outline.document_title,
            "court_details": outline.court_details,
            "outline_summary": outline_summary,
            "template": template_text,  # Full template (2K-9K chars, no truncation)
            "section_num": str(section_index + 1),
            "total": str(total_sections),
            "section_title": section.title,
            "section_desc": section.description,
            "start_para_num": str(start_para_num),
            "est_paragraphs": str(section.estimated_paragraphs),
            "needs_citations": (
                "Yes — cite ONLY the cases listed in the DOCTRINAL STANCE "
                "block above under 'USE THESE CASE LAWS' (each is corpus-"
                "verified). If the stance lists no case relevant to this "
                "paragraph's point, cite the doctrine WITHOUT a case label "
                "(e.g. 'as consistently held by the Supreme Court in matters "
                "of partition between Class I heirs'). DO NOT invent case "
                "names or citations — even confident-sounding ones. NEVER "
                "emit [CITE: ...] placeholder markers."
            ) if section.needs_citations else "No",
            "facts_block": facts_block,
            "facts_reminder": facts_reminder,
            "stance_block": stance_block,
            "format_block": format_block,
            "cause_title_override": cause_title_override,
        }), timeout=180)

    from core.token_tracker import record as _record_tokens
    tokens = _record_tokens(
        "Drafting", f"section_{section_index + 1}_{section.title[:30]}", response,
    )

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
    user_facts: str = "",
    case_facts: str = "",
    stance_block: str = "",
    format_block: str = "",
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

    # Precompute cumulative paragraph offsets so each substantive body
    # section knows the global paragraph number it must start numbering
    # at. PROCEDURAL sections (Prayer, Verification, Court Fee Statement,
    # Schedule, List of Documents, Affidavit, Memo of Parties, Annexures
    # Index) use their OWN local numbering scheme — they don't consume
    # slots in the global body counter and don't shift downstream
    # sections' offsets. The per-section prompt rules (A-D) decide
    # which scheme to apply based on the section title.
    _PROCEDURAL_TITLE_KEYWORDS = (
        "prayer", "relief", "verification", "court fee", "court-fee",
        "schedule", "list of documents", "list of document",
        "affidavit", "memo of parties", "annexures index",
        "interim application", "ia under order",
    )

    def _is_procedural(title: str) -> bool:
        t = (title or "").lower()
        return any(k in t for k in _PROCEDURAL_TITLE_KEYWORDS)

    start_para_offsets: list[int] = []
    running = 1
    for plan in outline.sections:
        start_para_offsets.append(running)
        # Procedural sections don't consume body counter slots, so the
        # running counter stays put for the next section.
        if not _is_procedural(plan.title):
            running += max(1, plan.estimated_paragraphs)
    # start_para_offsets[i] = first global paragraph number for section i
    # (only meaningful for substantive body sections; procedural sections
    # are told to use their own local scheme via prompt rules B/C/D)

    async def _gen_one(i: int, plan: SectionPlan):
        async with sem:
            progress_counter["count"] += 1
            # `section`: NATURAL document index (1-based, stable across runs).
            # The frontend can render a fixed list of N sections and update
            # each slot's status as events arrive in arbitrary order (BUG-13).
            #
            # `start_order`: original ordering by which a section's coroutine
            # actually acquired the semaphore — useful for debugging only.
            section_num = i + 1
            start_order = progress_counter["count"]
            if writer:
                writer({
                    "type": "drafting_progress",
                    "section": section_num,
                    "index": i,                # 0-based for direct array indexing
                    "start_order": start_order,
                    "total": total,
                    "title": plan.title,
                    "status": "in_progress",
                })
            try:
                text, tokens = await _generate_section(
                    query, template_text, plan, i, total, outline, user_language,
                    user_facts=user_facts,
                    case_facts=case_facts,
                    stance_block=stance_block,
                    format_block=format_block,
                    start_para_num=start_para_offsets[i],
                )
                results[i] = (text, tokens, None)
                if writer:
                    writer({
                        "type": "drafting_progress",
                        "section": section_num,
                        "index": i,
                        "start_order": start_order,
                        "total": total,
                        "title": plan.title,
                        "status": "completed",
                        "char_count": len(text),
                    })
            except Exception as e:
                log.error("Section failed", section=i + 1, title=plan.title,
                          error=str(e)[:200])
                results[i] = (None, 0, e)
                if writer:
                    writer({
                        "type": "drafting_progress",
                        "section": section_num,
                        "index": i,
                        "start_order": start_order,
                        "total": total,
                        "title": plan.title,
                        "status": "failed",
                        "error": str(e)[:200],
                    })

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
#
# Footer label translations for the 10 P1/P2 Indian languages. This is
# prompt-side translation data, not routing logic — the labels are
# scaffolding the LLM is asked to render in the target language. Removing
# the dict and trusting the self-refine critic to translate the footer
# caused a measurable Marathi WS regression (English "Place:/Date:" leaked
# all the way to the user-visible final response). Per-language footer
# labels are higher-leverage than paragraph numerals for refinement
# because they appear once and the critic is more likely to under-prioritise
# them. The dict stays. Adding a new language is one entry.
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


def _build_footer(footer_kind: str, user_language: str) -> str:
    """Return the appropriate footer block for a given artifact kind.

    Reads stance.footer_kind ("court_filing" / "legal_notice" / "agreement"
    / "will" / "none") and picks the right footer template. Labels for
    "court_filing" come from _FOOTER_LABELS in the requested Indian
    language; other footer kinds are emitted in English (the self-refine
    critic translates them when strict_language is set).
    """
    if footer_kind == "none":
        return ""
    if footer_kind == "legal_notice":
        # Legal notices are sent by registered post and signed by
        # counsel; no "Petitioner/Applicant" line, no court filing.
        return (
            "Yours sincerely,\n\n"
            "Sd.\n"
            "**[Name of Advocate]**\n"
            "[Enrolment No.]\n"
            "[Address of Advocate]\n"
            "[Contact Details]"
        )
    if footer_kind == "police_complaint":
        # Police-station FIR registration application (Section 154 CrPC /
        # Section 173 BNSS) — addressed to SHO / Police Inspector, signed
        # by the COMPLAINANT, not by counsel. Place + date are mandatory
        # because police use them for FIR sequencing.
        return (
            "Yours faithfully,\n\n"
            "Sd.\n"
            "**[Name of Complainant]**\n"
            "[Father's / Husband's Name]\n"
            "[Full Address of Complainant]\n"
            "[Contact Number]\n\n"
            "Place: ___________\n\n"
            "Date: ___________"
        )
    if footer_kind == "agreement":
        return (
            "**IN WITNESS WHEREOF**, the parties have executed this "
            "Agreement on the date first written above.\n\n"
            "**FIRST PARTY:**\n"
            "Sd. _________________________\n"
            "[Name of First Party]\n\n"
            "**SECOND PARTY:**\n"
            "Sd. _________________________\n"
            "[Name of Second Party]\n\n"
            "**WITNESSES:**\n\n"
            "1. Sd. _________________________\n"
            "   [Name and Address of Witness 1]\n\n"
            "2. Sd. _________________________\n"
            "   [Name and Address of Witness 2]"
        )
    if footer_kind == "will":
        # Indian Succession Act, 1925 Section 63 — testator + 2 witnesses.
        return (
            "**IN WITNESS WHEREOF**, I have set my hand to this WILL on "
            "the date first written above.\n\n"
            "Sd. _________________________\n"
            "[Name of Testator]\n\n"
            "**Signed by the Testator in our presence and signed by us in "
            "the presence of the Testator and of each other:**\n\n"
            "1. Sd. _________________________\n"
            "   [Name and Address of Attesting Witness 1]\n\n"
            "2. Sd. _________________________\n"
            "   [Name and Address of Attesting Witness 2]"
        )
    # Default: court_filing
    labels = _FOOTER_LABELS.get(user_language, {})
    place_label = labels.get("place", "Place")
    date_label = labels.get("date", "Date")
    signature_label = labels.get("signature", "Signature of the Petitioner/Applicant")
    through_counsel_label = labels.get("through_counsel", "Through Counsel")
    return (
        f"**{place_label}:** [Place]\n\n"
        f"**{date_label}:** [Date]\n\n"
        f"**{signature_label}**\n\n"
        f"{through_counsel_label}:\n\n"
        "**[Name of Advocate]**\n"
        "[Enrollment No.]\n"
        "[Address of Advocate]"
    )


def _assemble_document(
    outline: DraftOutline,
    sections: list[str],
    user_language: str = "en",
    stance: "DoctrinalStance | None" = None,
) -> str:
    """Combine all sections into the final document with proper structure.

    Ensures each section has a heading (injects from outline if LLM omitted it).
    Picks the right footer based on `stance.footer_kind` (see `_build_footer`).
    Falls back to court_filing footer when no stance is available, which
    preserves the prior default for backward compatibility.

    Sagar's bug #6 (2026-06-16): for court-filing drafts, the subject
    heading (e.g. "SUIT FOR COMPENSATION FOR MEDICAL NEGLIGENCE") goes
    at the END of the cause-title block as a bolded paragraph, NOT at
    the top as an H1. The previous `# {document_title}` markup is dropped.

    Bug #6 followup (2026-06-17): for legal notices, replies to legal
    notices, and agreements/deeds/wills, the title belongs at the TOP
    of court_details (the LLM emits it inside the layout) — DO NOT
    re-append it at the end. The subject-append logic below is therefore
    gated on footer_kind == "court_filing".
    """
    cause_title = outline.court_details.rstrip()
    subject = (outline.document_title or "").strip()
    # Only append the subject heading at the END for court-filing drafts.
    # Notice/agreement/police_complaint layouts put the title at the TOP
    # of court_details (Layouts B/C/D in DraftOutline.court_details field
    # doc); appending again would produce duplicated title text and
    # pollute the layout with court-filing scaffolding.
    _footer_kind_for_subject = "court_filing"
    if stance is not None:
        _footer_kind_for_subject = (
            getattr(stance, "footer_kind", "court_filing") or "court_filing"
        )
    if (subject
            and _footer_kind_for_subject == "court_filing"
            and subject.upper() not in cause_title.upper()):
        cause_title += f"\n\n**{subject.upper()}**"

    parts = [
        cause_title,
        "---",
    ]

    for i, (section_plan, section_text) in enumerate(zip(outline.sections, sections)):
        text = section_text.strip()
        if not text.startswith("#"):
            text = f"## {i + 1}. {section_plan.title}\n\n{text}"
        parts.append(text)

    footer_kind = "court_filing"
    if stance is not None:
        footer_kind = getattr(stance, "footer_kind", "court_filing") or "court_filing"

    footer_block = _build_footer(footer_kind, user_language)
    if footer_block:
        parts.append("---")
        parts.append(footer_block)

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
    # Continuation: persisted facts (if first attempt had a file/context attached)
    user_facts = continuation.get("user_facts", "")
    case_facts = continuation.get("case_facts", "")

    log.info("Continue draft started",
             failed_sections=len(failed_indices),
             total_sections=len(outline.sections),
             facts_chars=len(user_facts))

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
                    user_facts=user_facts,
                    case_facts=case_facts,
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
    intent_obj = state.get("user_intent")
    integration_ctx = IntegrationContextData.from_state(state)
    fc = FileContextData.from_state(state)

    # Collect ALL fact sources (uploaded PDF, pasted long-form context,
    # third-party integration content) into a single `user_facts` blob.
    # These are passed to outline + section gen as a SEPARATE field so the
    # template doesn't dominate the prompt (BUG-02). The clean `query`
    # (user's actual instruction) is what we use for template search and
    # selection — keeping the BM25 query small and on-target (BUG-16).
    fact_blocks: list[str] = []
    if fc and fc.inline_text:
        # Uploaded document (PDF, DOCX, TXT) extracted text
        fact_blocks.append(
            f"[Uploaded document text — {', '.join(fc.file_names) or 'attached'}]\n"
            f"{fc.inline_text[:30000]}"
        )
    if user_context:
        # Long-form pasted content embedded in the user's typed query
        fact_blocks.append(f"[Pasted context]\n{user_context[:30000]}")
    if integration_ctx and integration_ctx.has_content:
        # Content fetched from Google Docs / Notion
        fact_blocks.append(integration_ctx.as_prompt_prefix().rstrip())
    user_facts = "\n\n".join(fact_blocks)

    log.info("Agent started", query=query[:100],
             has_user_context=bool(user_context),
             has_file_context=bool(fc and fc.inline_text),
             has_integration_context=bool(integration_ctx and integration_ctx.has_content),
             facts_chars=len(user_facts),
             using_agent_query="Drafting" in agent_queries)

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

        # Step 0: Extract structured key facts from any uploaded document /
        # pasted context. The structured list is prepended to outline +
        # section-gen prompts so the LLM uses real names/dates/amounts
        # instead of [Plaintiff Name] / [Loan Amount] placeholders. (BUG-02)
        case_facts = ""
        if user_facts:
            progress("drafting", "Extracting key facts from your document...", step="extract")
            case_facts = await _extract_case_facts(user_facts)

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

        # Step 2: Select best template (previews + case-context + validation)
        progress("drafting", "Selecting best template...", step="select")
        selected_source, all_paths = await asyncio.to_thread(
            _select_best_template, query, hits, user_facts,
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

        # Step 3.7: Extract layout/format conventions from the template, in
        # parallel with outline gen. Cached per template_source so this only
        # actually fires once per unique template -- afterwards it's an
        # in-memory lookup. format_block carries alignment, numbering glyphs,
        # prayer/verification clauses, signature block, etc. Empty string
        # when extraction fails (silent fallback, outline + sections still
        # run on template_text alone).
        format_spec = await _extract_format_spec(selected_source, template_text)
        format_block = _format_layout_block(format_spec)

        # Step 4: Generate document outline (max 12 sections)
        progress("drafting", "Generating document outline...", step="outline")
        outline = await _generate_outline(
            query, template_text, user_language,
            user_facts=user_facts,
            case_facts=case_facts,
            format_block=format_block,
        )
        progress("drafting", f"Outline ready: {len(outline.sections)} sections", found=len(outline.sections), substep=True, step="outline")

        # Step 4.5: Generate doctrinal stance (one-shot Flash call that fixes
        # the legal lane every parallel section must follow). Silent fallback
        # to empty stance_block if it fails — drafting still runs.
        stance = await _generate_doctrinal_stance(
            query, outline.document_title,
            user_facts=user_facts,
            case_facts=case_facts,
        )

        # Step 4.55: Verify stance.key_cases against the judgment ES corpus.
        # The stance LLM has no retrieval grounding, so confident-sounding
        # fabricated citations can land here and propagate end-to-end. The
        # verifier drops cases with zero ES hits; section prompts then only
        # see verified anchors.
        if stance is not None and stance.key_cases:
            progress("drafting", "Verifying anchor case citations...",
                     step="verify_cases")
            stance = await _verify_stance_cases(stance)

        stance_block = _format_stance_for_section(stance)

        # Step 4.6: Inject any mandatory procedural sections the stance
        # enumerated that the outline missed (Schedule, Court Fee, List of
        # Documents, separate IA for TI, notarised Affidavit, etc.). When
        # the stance call failed or returned no procedural list, the outline
        # is left as-is — no hardcoded fallback pack.
        outline.sections = _inject_procedural_sections(outline, stance)
        # Re-cap after injection
        if len(outline.sections) > _MAX_SECTIONS:
            log.warning("Outline expanded past cap after procedural injection",
                        before=_MAX_SECTIONS, after=len(outline.sections))
            outline.sections = outline.sections[:_MAX_SECTIONS]

        # Step 5: Generate sections in parallel (semaphore-limited to 3)
        try:
            from langgraph.config import get_stream_writer
            writer = get_stream_writer()
        except (RuntimeError, ImportError):
            writer = None

        sections, failed_indices, total_tokens = await _generate_sections_parallel(
            query, template_text, outline, writer, user_language,
            user_facts=user_facts,
            case_facts=case_facts,
            stance_block=stance_block,
            format_block=format_block,
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
        full_draft = _assemble_document(outline, sections, user_language, stance=stance)

        # Step 6.5: Validator -- auto-fix mojibake + leftover [CITE: ...], log
        # warnings for statute traps and orphan citation tails. Never raises.
        full_draft, draft_warnings = validate_draft(full_draft, stance)

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

        # Step 8: Dynamic self-refine — critic audits the assembled draft
        # against the typed UserIntent (strict_language, response_depth,
        # additional_instructions, etc.) and the refiner rewrites on
        # violations. Skips trivial intents internally; cheap when there's
        # nothing to fix. This is what catches Hindi/Marathi WS drafts
        # leaking Latin numerals or English clauses without bespoke
        # numeral substitution code.
        if full_draft and not failed_indices and intent_obj is not None:
            try:
                progress("drafting", "Auditing draft against your directives...", step="self_refine")
                refined_draft, refine_history = await self_refine(
                    full_draft,
                    user_query=query,
                    intent=intent_obj,
                )
                if refined_draft != full_draft:
                    log.info(
                        "Self-refine altered draft",
                        iterations=len(refine_history),
                        original_len=len(full_draft),
                        refined_len=len(refined_draft),
                    )
                    full_draft = refined_draft
            except Exception as refine_err:
                log.warning("Self-refine skipped due to error",
                            error=str(refine_err))

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
            meta=(
                {"draft_warnings": draft_warnings}
                if draft_warnings else {}
            ),
        )

        # Store continuation metadata for incomplete drafts
        state_update = {"agent_results": {"Drafting": result}}
        if failed_indices:
            state_update["draft_continuation"] = {
                "outline": outline.model_dump(),
                "template_text": template_text,
                "template_source": selected_source,
                "query": query,
                "user_facts": user_facts,
                "case_facts": case_facts,  # preserve extracted entities for retries
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
