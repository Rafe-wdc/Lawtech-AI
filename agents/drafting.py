"""Drafting Agent — simplified two-source pipeline (2026-06-28).

Flow:
  1. Build `user_facts` blob from attachments / pasted context / integrations.
  2. Extract structured case facts via `_extract_case_facts`.
  3. Acquire a reference draft:
       a. ES `match` on the `drafting` index for candidate file names.
       b. Single LLM picker call — returns the best path OR 'none'.
       c. If 'none' or empty corpus → web fallback (Gemini 2.5 Flash + Google
          Search grounding) synthesises a reference draft using
          `DRAFTING_WEB_FALLBACK_PROMPT`.
  4. Single-pass generation: one Gemini 2.5 Pro call given
     (user_query + case_facts + reference_draft + user_intent).
  5. Mechanical cleanup: `validate_draft` strips mojibake / HTML tags /
     leftover `[CITE: ...]` placeholders / empty numbered paragraphs.
  6. `core.self_refine.self_refine` audits the draft against typed UserIntent
     and refines on violations. (This is the scope critic.)
  7. Return `AgentResult` with source attribution (`reference_kind`: 'es' | 'web').

No doc-type classifier, no synthetic skeletons, no doctrinal-stance JSON,
no per-section fan-out, no mandatory-section injection, no footer template.
The reference draft is the structural anchor; the user's query shapes scope.

See `docs/drafting_simplification_plan.md` for the full design and rationale.
"""

from __future__ import annotations

import asyncio
import os
import re
import unicodedata

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from core.state import (
    LegalAgentState, AgentResult, SourceMetadata,
    IntegrationContextData, FileContextData,
)
from core.clients import (
    get_es_client, get_gemini_flash, get_gemini_pro,
)
from core.settings import ES_INDICES
from core.language import localize_prompt, detect_source_languages
from core.logger import get_logger, log_time
from core.progress import progress
from core.self_refine import self_refine

log = get_logger("Drafting")


# Limit concurrent Drafting executions per worker process (avoids Gemini
# rate limits and gives the orchestrator a back-pressure signal).
_AGENT_SEMAPHORE = asyncio.Semaphore(3)


# ---------------------------------------------------------------------------
# Input sanitisation (used by the ES `match` query in `_acquire_reference_draft`)
# ---------------------------------------------------------------------------

_LUCENE_SPECIAL = re.compile(r'([+\-=&|!(){}\[\]^"~*?:\\/])')


def _sanitize_es_input(text: str, max_length: int = 500) -> str:
    """Sanitize user input before embedding in an ES query."""
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = text[:max_length]
    text = _LUCENE_SPECIAL.sub(r"\\\1", text)
    return text


# ---------------------------------------------------------------------------
# Case-fact extraction — pulls structured entities from user-attached docs.
# Used by `drafting_node` BEFORE generation so the LLM substitutes real
# names / dates / amounts directly instead of leaving [bracketed] placeholders.
# ---------------------------------------------------------------------------

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

    Returns a markdown bullet list of case-specific facts (names, amounts,
    dates, court, etc.) for the generation LLM to use verbatim. Empty string
    on failure (caller treats as no extracted facts).
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


# ---------------------------------------------------------------------------
# Mechanical draft cleanup — runs after generation, before self_refine.
# Pure bug fixes only (mojibake / HTML / [CITE:] / empty paragraphs).
# Substantive critique (orphan citation tails, forbidden statute pairings,
# paragraph numbering, prayer-relief mismatch, missing sections, etc.) lives
# in `core/self_refine.self_refine` against the typed UserIntent.
# ---------------------------------------------------------------------------

# Common UTF-8 → cp1252 → UTF-8 round-trip artefacts in Indian legal text.
_MOJIBAKE_REPLACEMENTS = [
    ("â€“", "–"),   # â€" -> en dash
    ("â€”", "—"),   # â€" -> em dash
    ("â€˜", "‘"),   # â€˜ -> left single quote
    ("â€™", "’"),   # â€™ -> right single quote
    ("â€œ", "“"),   # â€œ -> left double quote
    ("â€",  "”"),   # â€  -> right double quote
    ("â€¦", "…"),   # â€¦ -> ellipsis
    ("Â ",  " "),   # Â   -> nbsp
    ("ï¿½", "?"),   # replacement char (no-info fallback)
    (" â ", " – "), # bare â between spaces -> en-dash (surviving fragment
                    # of truncated 3-byte UTF-8 dash E2 80 93/94)
]

_CITE_PLACEHOLDER_RE = re.compile(r"\[CITE:[^\]]*\]", flags=re.IGNORECASE)
_EMPTY_NUMBERED_PARA_RE = re.compile(r"(?m)^\s*\d+\.\s*$\n?")

_HTML_BR_RE = re.compile(r"<br\s*/?>", flags=re.IGNORECASE)
_HTML_HR_RE = re.compile(r"<hr\s*/?>", flags=re.IGNORECASE)
_HTML_ANY_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?/?>")


def validate_draft(
    full_draft: str,
    stance=None,  # kept for signature compatibility; unused
) -> tuple[str, list[str]]:
    """Cheap mechanical repairs on the generated draft.

    Returns (possibly-cleaned draft, list of warning messages). Never raises.

    Repairs:
      - cp1252 / latin-1 mojibake roundtrip + substring fallback
      - HTML tag strip (<br>, <hr> first, then any remaining tags)
      - leftover `[CITE: ...]` placeholder strip
      - empty numbered paragraphs (`N.` with no body)

    Substantive critique (orphan citation tails, forbidden statute pairs,
    paragraph numbering, prayer-relief mismatch, missing sections) is the
    responsibility of `core.self_refine.self_refine`. Do NOT add per-rule
    checks here — extend CRITIQUE_PROMPT instead.
    """
    warnings: list[str] = []
    cleaned = full_draft

    # Mojibake: codec roundtrip first, then substring fallback for mixed
    # cases where the roundtrip aborts (e.g. clean "café" — bytes E9 alone
    # is invalid UTF-8 lead, so the roundtrip raises and leaves the
    # string alone).
    for codec in ("cp1252", "latin-1"):
        try:
            roundtripped = cleaned.encode(codec, errors="strict").decode("utf-8", errors="strict")
            if roundtripped != cleaned:
                cleaned = roundtripped
                log.info("Validator: mojibake auto-fixed", codec=codec)
                break
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue

    fixed_count = 0
    for bad, good in _MOJIBAKE_REPLACEMENTS:
        if bad in cleaned:
            cleaned = cleaned.replace(bad, good)
            fixed_count += 1
    if fixed_count:
        log.info("Validator: mojibake auto-fixed (substring fallback)",
                 patterns_fixed=fixed_count)

    # HTML strip — convert <br> / <hr> to markdown equivalents first so
    # newlines survive, then drop any remaining tag scaffolding. Inner
    # text is preserved.
    cleaned = _HTML_BR_RE.sub("\n", cleaned)
    cleaned = _HTML_HR_RE.sub("\n---\n", cleaned)
    cleaned, html_strip_count = _HTML_ANY_TAG_RE.subn("", cleaned)
    if html_strip_count:
        warnings.append(
            f"Stripped {html_strip_count} HTML tag(s) from draft."
        )

    # [CITE: ...] survivors — the generation prompt forbids them, this is
    # the defensive fallback.
    cite_hits = _CITE_PLACEHOLDER_RE.findall(cleaned)
    if cite_hits:
        cleaned = _CITE_PLACEHOLDER_RE.sub("", cleaned)
        warnings.append(
            f"Stripped {len(cite_hits)} leftover [CITE: ...] placeholder(s)."
        )

    # Empty numbered paragraphs — a bare `N.` line with no body.
    empty_para_hits = _EMPTY_NUMBERED_PARA_RE.findall(cleaned)
    if empty_para_hits:
        cleaned = _EMPTY_NUMBERED_PARA_RE.sub("", cleaned)
        warnings.append(
            f"Removed {len(empty_para_hits)} empty numbered paragraph(s)."
        )

    if warnings:
        log.warning("Validator surfaced issues",
                    count=len(warnings),
                    sample=warnings[0][:120])

    return cleaned, warnings


# ---------------------------------------------------------------------------
# Stage 1 — Reference draft acquisition
# ---------------------------------------------------------------------------

class _PickerChoice(BaseModel):
    """Structured output for `_pick_reference_source`.

    'none' is a valid `selected_file` value so the picker can reject the
    entire candidate list and trigger the web fallback.
    """
    selected_file: str = Field(
        ...,
        description=(
            "Exact file name from the candidate list, OR the literal string "
            "'none' if no candidate fits the user's drafting query."
        ),
    )
    reasoning: str = Field(
        "",
        description="One short sentence explaining the choice.",
    )


async def _pick_reference_source(
    query: str, file_paths: list[str],
) -> str | None:
    """Single Gemini Flash Lite call. Picks the best-fitting file name from
    the candidate list, or returns None when no candidate fits (caller falls
    back to web search).

    File paths alone (no previews) — the 2026-06-28 index census confirmed
    corpus filenames are richly descriptive (avg 66.9 chars, 0.8% opaque).

    Falls back to None on any error so traffic never breaks — the worst
    case is a web fallback fire instead of a silent bad pick.
    """
    if not file_paths:
        return None
    try:
        from langchain.chat_models import init_chat_model
        from config.prompts import DRAFTING_PICKER_PROMPT

        llm = init_chat_model(
            "google_genai:gemini-2.5-flash-lite",
            temperature=0.0,
        ).with_structured_output(_PickerChoice, include_raw=True)

        candidates_block = "\n".join(f"- {p}" for p in file_paths)
        prompt = ChatPromptTemplate.from_template(DRAFTING_PICKER_PROMPT)
        chain = prompt | llm

        with log_time(log, "Reference picker"):
            raw_and_parsed = await asyncio.wait_for(
                chain.ainvoke({"query": query, "file_paths": candidates_block}),
                timeout=15,
            )
        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "pick_reference", raw_and_parsed.get("raw"))

        choice = raw_and_parsed["parsed"]
        picked = (choice.selected_file or "").strip()
        reasoning = (choice.reasoning or "")[:160]

        if picked.lower() == "none" or not picked:
            log.info("Picker returned 'none'", reasoning=reasoning)
            return None

        # Exact match preferred
        if picked in file_paths:
            log.info("Picker chose", picked=picked, reasoning=reasoning)
            return picked

        # Fuzzy match — LLM sometimes trims quotes / massages whitespace
        for p in file_paths:
            if p.lower() == picked.lower() or \
               os.path.basename(p).lower() == os.path.basename(picked).lower():
                log.info("Picker matched fuzzy",
                         picked=picked, matched=p, reasoning=reasoning)
                return p

        log.warning("Picker returned a path not in candidate list",
                    picked=picked, reasoning=reasoning)
        return None
    except Exception as e:
        log.warning(
            "Reference picker failed; treating as no reference",
            error=str(e).splitlines()[0][:200],
        )
        return None


async def _acquire_reference_draft(
    query: str,
    progress_emit,
    *,
    user_language: str = "en",
    intent=None,
    original_query: str = "",
) -> tuple[str, str, str]:
    """Stage 1 of the simplified drafting pipeline (v1 DraftRetriever pattern).

    Flow:
      1. ES `match` on `page_content` (size=100) → distinct `source` file paths
      2. `_pick_reference_source` returns the best path OR None
      3. If picked: ES term query on `source.keyword` → fetch full page_content
      4. If None / empty corpus / fetch miss: synthesize via web_search_fallback
         using DRAFTING_WEB_FALLBACK_PROMPT (instructs the web model to emit
         a draft, not an essay — v1's gap)

    Returns:
      (reference_text, source_attribution, source_kind)
        source_kind is 'es' (from drafting corpus) or 'web' (synthesized).
    """
    es = get_es_client()
    index = ES_INDICES["drafting"]

    sanitized = _sanitize_es_input(query)
    bm25_body = {
        "size": 100,
        "query": {"match": {"page_content": sanitized}},
        "_source": ["source"],
    }

    try:
        with log_time(log, "ES match for reference candidates"):
            response = await asyncio.to_thread(es.search, index=index, body=bm25_body)
        hits = response["hits"]["hits"]
    except Exception as e:
        log.warning("ES candidate search failed; going to web",
                    error=str(e).splitlines()[0][:200])
        hits = []

    # Distinct file paths preserving order (best-scoring first)
    seen: set[str] = set()
    file_paths: list[str] = []
    for hit in hits:
        src = hit.get("_source", {}).get("source", "")
        if src and src not in seen:
            seen.add(src)
            file_paths.append(src)

    progress_emit(
        "drafting",
        f"Found {len(file_paths)} candidate templates",
        substep=True, step="reference",
        found=len(file_paths),
    )

    if not file_paths:
        log.info("No corpus candidates; synthesizing reference via web")
        progress_emit(
            "drafting",
            "No matching template in corpus, searching the web...",
            substep=True, step="reference",
        )
        web_text = await _acquire_reference_via_web(
            query, user_language=user_language, intent=intent,
            original_query=original_query,
        )
        return web_text, "<web:no-corpus-hit>", "web"

    picked = await _pick_reference_source(query, file_paths)

    if picked is None:
        log.info("Picker rejected all candidates; synthesizing via web")
        progress_emit(
            "drafting",
            "No close template in corpus, searching the web...",
            substep=True, step="reference",
        )
        web_text = await _acquire_reference_via_web(
            query, user_language=user_language, intent=intent,
            original_query=original_query,
        )
        return web_text, "<web:picker-rejected>", "web"

    fetch_body = {
        "size": 1,
        "query": {"term": {"source.keyword": picked}},
        "_source": ["source", "page_content"],
    }
    try:
        with log_time(log, "ES fetch picked template"):
            fetch_resp = await asyncio.to_thread(
                es.search, index=index, body=fetch_body,
            )
        fetch_hits = fetch_resp["hits"]["hits"]
    except Exception as e:
        log.warning("ES fetch for picked template failed; going to web",
                    picked=picked, error=str(e).splitlines()[0][:200])
        fetch_hits = []

    if not fetch_hits:
        log.warning("Picker chose path but ES returned 0 hits; going to web",
                    picked=picked)
        progress_emit(
            "drafting",
            "Selected template missing in corpus, searching the web...",
            substep=True, step="reference",
        )
        web_text = await _acquire_reference_via_web(
            query, user_language=user_language, intent=intent,
            original_query=original_query,
        )
        return web_text, "<web:fetch-failed>", "web"

    reference_text = fetch_hits[0]["_source"].get("page_content", "") or ""
    display_name = os.path.basename(picked)
    progress_emit(
        "drafting",
        f"Using template: {display_name[:60]}",
        substep=True, step="reference",
    )
    log.info("Reference draft acquired from corpus",
             source=picked, length=len(reference_text))
    return reference_text, picked, "es"


async def _acquire_reference_via_web(
    query: str,
    *,
    user_language: str = "en",
    intent=None,
    original_query: str = "",
) -> str:
    """Synthesize a reference draft from the open web.

    Uses `core.agent_fallback.web_search_fallback` (Gemini 2.5 Flash + Google
    Search grounding) with `DRAFTING_WEB_FALLBACK_PROMPT`, which instructs the
    model to produce a SAMPLE DRAFT rather than an essay. This fixes v1's gap:
    v1's fallback (Scenario_qa) returned a legal-QA answer for niche formats,
    not a usable reference draft.
    """
    from core.agent_fallback import web_search_fallback
    from config.prompts import DRAFTING_WEB_FALLBACK_PROMPT

    try:
        result = await web_search_fallback(
            query=query,
            agent_name="Drafting",
            system_prompt=DRAFTING_WEB_FALLBACK_PROMPT,
            original_query=original_query,
            user_language=user_language,
            intent=intent,
        )
        text = (getattr(result, "content", "") or "").strip()
        if not text:
            log.warning("Web fallback returned empty content; no reference available")
        return text
    except Exception as e:
        log.warning(
            "Web fallback synthesis failed; returning empty reference",
            error=str(e).splitlines()[0][:200],
        )
        return ""


# ---------------------------------------------------------------------------
# Stage 2 — Single-pass draft generation.
# This function is the seam for future per-section fan-out — the public
# signature stays `-> str` so callers don't need to change. See
# `docs/drafting_simplification_plan.md` §2 "Generation seam".
# ---------------------------------------------------------------------------

async def _generate_draft(
    query: str,
    case_facts: str,
    reference_draft: str,
    user_intent,
    user_language: str,
    progress_emit,
) -> str:
    """Produce the full document in one Gemini 2.5 Pro call.

    Inputs: (system_prompt + user_query + case_facts + reference_draft).
    The LLM decides headings, sections, length, footer, signature block
    based on the reference and the user's ask.
    """
    from config.prompts import DRAFTING_SYSTEM_PROMPT
    from langchain_core.messages import SystemMessage, HumanMessage

    system_prompt = localize_prompt(DRAFTING_SYSTEM_PROMPT, user_language, user_intent)

    facts_block = ""
    if case_facts and case_facts.strip():
        facts_block = (
            "## CASE FACTS (extracted from the user's prompt / attachments — "
            "these are the ONLY real values; use them directly in the draft "
            "and do NOT bracket them as placeholders)\n"
            f"{case_facts.strip()}\n\n"
        )

    # When the user has provided rich facts (long inline query or
    # extracted attachment summary), the reference draft becomes net-
    # negative: it's a fully-formed document with its OWN names / dates /
    # amounts, and Gemini Pro at temp 0.4 over-attends to those concrete
    # values and copies them into the output even with explicit "do not
    # copy" instructions. Drop the reference in that case — the system
    # prompt (DRAFTING_SYSTEM_PROMPT) already carries Indian-legal
    # document conventions and the LLM can produce the right shape from
    # facts alone. The reference is still passed through on short queries
    # where the LLM genuinely needs a structural anchor.
    _has_rich_facts = bool(case_facts) and len(case_facts) >= 500
    if _has_rich_facts:
        reference_block = (
            "## REFERENCE DRAFT\n"
            "(Skipped — the user has provided rich case facts above. "
            "Produce the document directly from those facts, following "
            "standard Indian-law conventions for the document type the "
            "user named in the query.)\n\n"
        )
    else:
        reference_block = (
            "## REFERENCE DRAFT (STRUCTURE-ONLY example from a different matter — "
            "IGNORE every name, date, address, amount, party detail, and case-"
            "specific value in this block. They belong to a different person's "
            "matter and MUST NOT appear in your output. Use ONLY the reference's "
            "shape: section ordering, headings, salutations, conventions, "
            "phrasing patterns, and statutory-citation style.)\n"
            f"{reference_draft.strip() if reference_draft else '(no reference draft available — produce the document from the user query and case facts alone, following Indian-law conventions for the document type)'}\n\n"
        )

    # Prompt assembly. Two shapes depending on whether the user has
    # provided rich extractable facts:
    #
    #   (rich-facts path) The extractor produced a tight bullet list. Pass
    #     ONLY that as the LLM's source of truth — DROP the raw query
    #     narrative. Empirically (2026-06-28), keeping the 6.6 KB raw query
    #     alongside the bullet list caused the model to over-attend to the
    #     narrative and substitute canonical Indian-law example values
    #     (Sneha / Priyanka / Nashik) for the user's real facts. With just
    #     the structured bullet list + reliefs / statute call-outs, the
    #     model has no narrative noise to wander into.
    #
    #   (short path) Use the original layout: USER QUERY narrative +
    #     facts_block (which is empty here) + reference template.
    if _has_rich_facts:
        # Two-source layout:
        #   - CASE FACTS bullet list — labelled structured anchor
        #   - USER QUERY narrative — same facts in natural-language context,
        #     placed LAST so the LLM is freshly attending to it. The bullet
        #     list alone left the LLM unsure which entity went where (e.g.
        #     "Plaintiff: Jyoti" → "Jyoti is the wife? or just the
        #     plaintiff?" → falls back to [Wife's Name] placeholder).
        #     Including the natural-language narrative right before the
        #     closing instruction resolves the entity binding and produces
        #     real values in the draft.
        user_block = (
            f"{reference_block}"
            f"{facts_block}"
            "## USER QUERY AND CASE NARRATIVE (verbatim — every name, date, "
            "address, amount, and event below is real and must appear in "
            "your output where the document calls for it)\n"
            f"{query.strip()}\n\n"
            "Produce the legal document the user asked for in standard "
            "Indian-law conventions. EVERY party name, date, address, "
            "monetary amount, ornament / asset description, statutory "
            "citation, and case-specific detail MUST come VERBATIM from the "
            "CASE FACTS bullet list OR the USER QUERY NARRATIVE above. Do "
            "NOT invent, substitute, paraphrase, or carry over canonical-"
            "sounding Indian-law example values (e.g. 'Priyanka', 'Nashik', "
            "'29 May 2022', 'Sangamner', 'Sneha', 'Bhausaheb', 'Sakore', "
            "'Ahmednagar') — using them is a critical error. If a value is "
            "genuinely absent from both sources above, use a clearly-"
            "bracketed placeholder (e.g. [Advocate's Address], [Reference "
            "Number]). Output ONLY the document — no preamble, no "
            "postscript, no meta-commentary."
        )
    else:
        user_block = (
            f"{reference_block}"
            "## USER QUERY (the document-type request — what to draft)\n"
            f"{query.strip()}\n\n"
            f"{facts_block}"
            "Produce the complete document the user asked for, in standard "
            "Indian-law conventions for the document type the user named. "
            "If a value the document needs is not provided by the user, use "
            "a clearly-bracketed placeholder (e.g. [Address], [Date], "
            "[Reference Number]); NEVER copy a value from the REFERENCE "
            "DRAFT — its values belong to a different matter and using "
            "them is a critical error. Output ONLY the document itself — "
            "no preamble, no postscript, no meta-commentary."
        )

    # Temperature 0 + larger thinking budget. The previous default
    # (temperature 0.4) was producing creative deviations from the CASE
    # FACTS — Gemini Pro was substituting cliché Indian-law example
    # values (Sneha / Priyanka / Nashik / 29 May 2022) for the user's
    # actual party names and dates. Drafting is a fact-transcription task,
    # not a creative one — zero temperature forces strict instruction
    # following; the larger thinking budget gives the model headroom to
    # cross-reference each emitted entity back to the CASE FACTS block.
    llm = get_gemini_pro(
        temperature=0.0,
        max_output_tokens=65535,
        thinking_budget=4096,
    )

    progress_emit("drafting", "Generating your draft...", step="generate")

    try:
        with log_time(log, "Single-pass draft generation"):
            response = await asyncio.to_thread(
                llm.invoke,
                [SystemMessage(content=system_prompt), HumanMessage(content=user_block)],
            )
    except Exception as e:
        log.error("Draft generation LLM call failed",
                  error=str(e)[:200], exc_info=True)
        raise

    from core.token_tracker import record as _record_tokens
    _record_tokens("Drafting", "generate", response)

    text = getattr(response, "content", None) or str(response)
    log.info("Draft generated", length=len(text))
    return text


# ---------------------------------------------------------------------------
# Agent node — wired into the LangGraph multi-agent system.
# ---------------------------------------------------------------------------

async def drafting_node(state: LegalAgentState) -> dict:
    """Simplified legal-draft generation pipeline.

    No enums, no skeletons, no per-section fan-out, no mandatory injection.

    See `docs/drafting_simplification_plan.md` for the full design.
    """
    # --- 1. Read state ---
    agent_queries = state.get("agent_queries", {})
    query = (
        agent_queries.get("Drafting")
        or state.get("query")
        or state.get("original_query", "")
    )
    original_query = state.get("original_query", query)
    user_context = state.get("user_context", "")
    user_language = state.get("user_language", "en")
    intent_obj = state.get("user_intent")
    integration_ctx = IntegrationContextData.from_state(state)
    fc = FileContextData.from_state(state)

    # --- 2. Build user_facts blob from attachments / pasted context / integrations ---
    fact_blocks: list[str] = []
    if fc and fc.chromadb_collections:
        from tools.shared.vectordb_tools import get_full_attachment
        for cid in fc.chromadb_collections:
            try:
                attached = get_full_attachment.invoke({"collection_id": cid})
                full_text = (attached or {}).get("full_text", "")
                source_file = (attached or {}).get("source_file") or "attached"
                if full_text:
                    fact_blocks.append(
                        f"[Uploaded document — {source_file}]\n{full_text}"
                    )
            except Exception as e:
                log.warning("get_full_attachment failed",
                            collection=cid, error=str(e))
    if user_context:
        fact_blocks.append(f"[Pasted context]\n{user_context[:30000]}")
    if integration_ctx and integration_ctx.has_content:
        fact_blocks.append(integration_ctx.as_prompt_prefix().rstrip())
    user_facts = "\n\n".join(fact_blocks)

    log.info(
        "Agent started",
        query=query[:100],
        has_user_context=bool(user_context),
        has_file_context=bool(fc and fc.chromadb_collections),
        attachment_collections=len(fc.chromadb_collections) if fc else 0,
        has_integration_context=bool(integration_ctx and integration_ctx.has_content),
        facts_chars=len(user_facts),
        using_agent_query="Drafting" in agent_queries,
    )

    # --- 3. Acquire concurrency slot; emit queue_status SSE if at capacity ---
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
        # --- 4. Resolve case_facts (the structured anchor the generation LLM
        # uses as the source of truth for names/dates/amounts).
        #
        # ALWAYS run the Gemini Flash extractor when there's substantive
        # content (attachments OR long inline query). Empirically, passing
        # a 6.6K-char raw narrative through a 25K-char context window leads
        # Gemini Pro to fall back to its prior — substituting canonical
        # Indian-law example values (Sneha / Priyanka / Nashik / 29 May 2022)
        # for the user's actual party names and dates. A compact bullet
        # list ("- **Plaintiff**: Jyoti Narendra Amrutkar; - **Marriage
        # Date**: 16 May 2013; ...") gives the LLM crisp anchors the model
        # can lock onto. The raw query still appears in USER QUERY for the
        # narrative context, but the bullet list is what carries the
        # authoritative facts.
        case_facts = ""
        if user_facts:
            progress("drafting", "Extracting key facts from your document...",
                     step="extract")
            case_facts = await _extract_case_facts(user_facts)
        elif len(query) >= 500:
            progress("drafting", "Extracting key facts from your prompt...",
                     step="extract")
            case_facts = await _extract_case_facts(query)

        # --- 5. Acquire reference draft (ES picker → web fallback if none) ---
        progress("drafting", "Searching for a reference template...",
                 step="reference")
        reference_text, reference_source, reference_kind = \
            await _acquire_reference_draft(
                query,
                progress,
                user_language=user_language,
                intent=intent_obj,
                original_query=original_query,
            )

        # --- 6. Single-pass generation (Gemini 2.5 Pro) ---
        draft = await _generate_draft(
            query=query,
            case_facts=case_facts,
            reference_draft=reference_text,
            user_intent=intent_obj,
            user_language=user_language,
            progress_emit=progress,
        )

        # --- 7. Mechanical cleanup (mojibake, HTML strip, [CITE:] strip) ---
        progress("drafting", "Cleaning up draft...", step="cleanup")
        draft, draft_warnings = validate_draft(draft)

        # --- 8. self_refine — the scope critic + audit pass against UserIntent ---
        if draft and intent_obj is not None:
            try:
                progress(
                    "drafting",
                    "Auditing draft against your directives...",
                    step="self_refine",
                )
                source_langs = detect_source_languages(user_facts, case_facts)
                refined_draft, refine_history = await self_refine(
                    draft,
                    user_query=query,
                    intent=intent_obj,
                    source_languages=source_langs,
                )
                if refined_draft != draft:
                    log.info(
                        "Self-refine altered draft",
                        iterations=len(refine_history),
                        original_len=len(draft),
                        refined_len=len(refined_draft),
                    )
                    draft = refined_draft
            except Exception as refine_err:
                log.warning("Self-refine skipped due to error",
                            error=str(refine_err))

        # --- 9. Build AgentResult with source attribution ---
        if reference_kind == "es":
            template_display = os.path.splitext(os.path.basename(reference_source))[0]
            sources = [SourceMetadata(
                source_type="drafting",
                title=template_display,
                content=[reference_text[:300]],
                file_name=reference_source,
                agent_name="Drafting",
                template_type=template_display,
            )]
        else:
            sources = [SourceMetadata(
                source_type="drafting",
                title=f"Reference draft (web-synthesised: {reference_source})",
                content=[reference_text[:300]] if reference_text else [],
                file_name=reference_source,
                agent_name="Drafting",
                template_type="web-synthesized",
            )]

        log.info(
            "Agent completed -- simplified pipeline",
            reference_kind=reference_kind,
            reference_source=reference_source[:120],
            draft_len=len(draft),
        )

        result = AgentResult(
            agent_name="Drafting",
            content=draft,
            sources=sources,
            tokens_consumed=0,
            fallback_used=(reference_kind == "web"),
            meta=(
                {"draft_warnings": draft_warnings, "reference_kind": reference_kind}
                if draft_warnings else {"reference_kind": reference_kind}
            ),
        )
        state_update = {"agent_results": {"Drafting": result}}

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
