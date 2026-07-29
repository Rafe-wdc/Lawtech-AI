"""Drafting Agent — raw-source pipeline (2026-07-02).

Flow:
  1. Build `user_facts` blob from attachments / pasted context / integrations.
     This is the RAW text of every uploaded document, passed straight
     through to generation. No extraction / bullet-summary middleman.
  2. Acquire a reference draft:
       a. ES `match` on the `drafting` index for candidate file names.
       b. Single LLM picker call — returns the best path OR 'none'.
       c. If 'none' or empty corpus → web fallback (Gemini 2.5 Flash + Google
          Search grounding) synthesises a reference draft using
          `DRAFTING_WEB_FALLBACK_PROMPT`.
  3. Generation: Gemini 2.5 Pro given
     (user_query + reference_draft + UPLOADED SOURCE DOCUMENTS + user_intent).
     Dispatcher picks single-pass or per-section fan-out via `_judge_fanout`.
     Raw source documents are threaded into EVERY section-pair call so any
     section that needs to walk the source paragraph-by-paragraph
     (para-wise reply, rejoinder para-wise denials, counter-affidavit)
     has direct access.
  4. Mechanical cleanup: `validate_draft` strips mojibake / HTML tags /
     leftover `[CITE: ...]` placeholders / empty numbered paragraphs.
  5. `core.self_refine.self_refine` audits the draft against typed UserIntent
     and refines on violations. Includes `canonical_example_substitution`
     category to catch training-set-artefact names (Sneha / Priyanka /
     Nashik / Sangamner / etc.) leaking into a draft whose sources named
     different real parties.
  6. Return `AgentResult` with source attribution (`reference_kind`: 'es' | 'web').

No doc-type classifier, no synthetic skeletons, no doctrinal-stance JSON,
no mandatory-section injection, no footer template, no case-fact bullet
extraction. The reference draft is the structural anchor; the user's query
shapes scope; the uploaded source documents provide para structure and
authoritative facts.

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
    get_es_client, get_gemini_pro,
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
# Review-and-Redraft short-circuit
#
# When the user uploaded a document AND their query has review/redraft/revise
# verbs, the uploaded document IS the reference draft the user wants preserved.
# The ES picker rejects this case because it looks for a template that matches
# the QUERY, not the attachment; the web fallback then returns a generic
# template unrelated to the actual uploaded doc (observed on smoke: contract-
# analysis / money-recovery-plaint templates returned for a Section 290 BNSS
# plea-bargaining application). Both outcomes discard the format the user
# already showed us — the exact "not specific format" client complaint.
#
# Detection is verb-based (surface signal). The typed UserIntent does not yet
# carry a `document_analysis_mode` field; when it does, this regex should be
# replaced by an intent-driven check. Both conditions must hold — a fresh
# draft without a file uses the picker+web path unchanged; an uploaded file
# WITHOUT review verbs (e.g. "write a rejoinder to this notice") also uses
# the unchanged path so a proper rejoinder template is fetched.
# ---------------------------------------------------------------------------
_REVIEW_REDRAFT_VERBS_RE = re.compile(
    r"\b("
    r"review|redraft|revise|revised|revising|revision|"
    r"correct|corrected|correcting|"
    r"fix|fixing|"
    r"audit|auditing|"
    r"rectif|"          # rectify / rectifying / rectification
    r"amend|amending|amendment|"
    r"error|errors|mistake|mistakes"
    r")\b",
    re.IGNORECASE,
)


def _is_review_and_redraft_of_upload(query: str, user_facts: str) -> bool:
    """True when the user uploaded a document AND asked to review/redraft it.

    Both conditions must hold. Detection is verb-based (see
    `_REVIEW_REDRAFT_VERBS_RE` above for the trigger set and rationale).
    A short user_facts blob (<200 chars) is treated as "no meaningful upload"
    to avoid triggering on placeholder / metadata-only extractions.
    """
    if not user_facts or len(user_facts.strip()) < 200:
        return False
    if not query:
        return False
    return bool(_REVIEW_REDRAFT_VERBS_RE.search(query))


# System-prompt-level override prepended to DRAFTING_SYSTEM_PROMPT and
# DRAFTING_SECTION_PAIR_PROMPT when review_and_redraft_mode=True. Without
# this, both default system prompts authoritatively frame REFERENCE DRAFT
# as "from a DIFFERENT matter — MUST NOT appear in your output" (see
# config/prompts.py DRAFTING_SYSTEM_PROMPT and DRAFTING_SECTION_PAIR_PROMPT
# rule #1), which causes the section-writer LLM to discard the uploaded
# document's party names, court, case number, and statutory citations —
# defaulting to a generic template (observed on smoke: Master Services
# Agreement / Contract-Analysis Memorandum output for a Section 290 BNSS
# plea-bargaining application). Prepending the override at the system
# prompt level flips the framing before the default rules are read.
_REVIEW_AND_REDRAFT_MODE_OVERRIDE = """## MODE OVERRIDE — REVIEW-AND-REDRAFT OF THE USER'S OWN UPLOADED DOCUMENT

The user uploaded a legal document AND explicitly asked you to REVIEW it for legal errors + REDRAFT the corrected version. This is NOT a fresh drafting task. The uploaded document is the FORMAT ANCHOR, the FACT ANCHOR, and the IDENTITY ANCHOR of your output.

The rules below (in the default drafting system prompt) frame REFERENCE DRAFT as "from a DIFFERENT matter — MUST NOT appear in your output". In THIS mode that framing is INVERTED:

  - The REFERENCE DRAFT and the UPLOADED SOURCE DOCUMENTS are the SAME document — the user's own file.
  - PRESERVE every party name, court name, case number, forum, statutory citation, Act name, section number, address, date, and monetary amount from that document VERBATIM in your output.
  - Your job is to CORRECT the substantive legal errors and formatting in that document — nothing more.
  - Common corrections in scope: wrong statute cited for the relief, wrong Act name / wrong section number, misidentified chapter / part, missing procedural block (verification / prayer / cause title), misstated law, missing landmark-precedent citation.
  - OUT OF SCOPE: changing the document type. If the user uploaded a plea-bargaining application, produce a corrected plea-bargaining application. If they uploaded a bail application, produce a corrected bail application. If they uploaded a rejoinder, produce a corrected rejoinder. NEVER convert their document to a Master Services Agreement, a Contract-Analysis Memorandum, an MOU, a lease deed, or any other template.
  - Do NOT replace real values from the uploaded document with `[Placeholder]` / `[Date]` / `[Full Legal Name of Party A]` fields.
  - Do NOT introduce boilerplate WHEREAS / NOW THEREFORE / IN WITNESS WHEREOF blocks unless the uploaded document itself uses them.
  - When adding landmark Supreme Court case-law citations, add them inline where they support the legal argument, NOT as a bibliography appendix at the end.

If any rule in the default drafting system prompt below CONTRADICTS this MODE OVERRIDE, this MODE OVERRIDE wins."""


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


# ---------------------------------------------------------------------------
# Regional-language → English translation for ES corpus lookup.
#
# The `drafting` ES index carries English file names and English template
# text. When the user's query is in a regional Indian language (Hindi /
# Marathi / Gujarati / Kannada / Tamil / Telugu / Bengali / Punjabi / Odia /
# Urdu / Assamese / Sanskrit / Malayalam), the raw ES `match` on the
# regional-script tokens returns 0 candidates → picker gets an empty list →
# fallback to web synthesis → thinner reference draft → much shorter final
# output than the English-equivalent query would produce.
#
# The fix is a single Gemini Flash Lite call that translates the user's
# regional-language query into a compact English drafting request. That
# translation drives BOTH the ES `match` and the picker's LLM reasoning
# (file names are English). Output-language stays the user's original
# choice — the reference draft is a STRUCTURAL anchor only; the
# section-writer produces body text in the user's target language.
#
# Fires only when `user_language != "en"`. Falls back to the original
# query on any translation failure so English-language traffic and
# regional-language traffic on translator errors both keep current
# behaviour.
# ---------------------------------------------------------------------------


async def _translate_query_for_es_match(
    query: str, user_language: str,
) -> str:
    """Translate a regional-language drafting query to English for ES lookup.

    Returns empty string on any failure — caller uses the original query
    in that case. Language codes match `core.language.SUPPORTED_LANGUAGES`.
    """
    if not query or not query.strip():
        return ""
    if not user_language or user_language == "en":
        return ""
    try:
        from langchain.chat_models import init_chat_model
        from core.language import language_name

        llm = init_chat_model(
            "google_genai:gemini-2.5-flash-lite",
            temperature=0.0,
        )
        source_lang = language_name(user_language)
        prompt = (
            f"Translate the following legal-drafting query from {source_lang} "
            f"to English. Return ONLY the translation — no preamble, no "
            f"quotes, no explanation. Preserve legal terms of art in their "
            f"standard English equivalents (e.g. 'वकालतनामा' → 'Vakalatnama', "
            f"'अभियुक्त' → 'accused', 'आवेदन' → 'application', 'याचिका' → "
            f"'petition', 'शपथपत्र' → 'affidavit'). Keep the translation "
            f"concise — one to three lines of English.\n\n"
            f"Query ({source_lang}):\n{query}"
        )
        with log_time(log, "Translate query for ES match"):
            response = await asyncio.wait_for(
                asyncio.to_thread(llm.invoke, prompt),
                timeout=10,
            )
        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "translate_query", response)

        text = (getattr(response, "text", "") or getattr(response, "content", "") or "").strip()
        # Guard against the model echoing the original when it can't
        # translate — a translated string should have some Latin content.
        latin_ratio = sum(1 for c in text if c.isascii() and c.isalpha()) / max(len(text), 1)
        if not text or latin_ratio < 0.3:
            log.warning(
                "Translator returned insufficient Latin content; ignoring",
                user_language=user_language, latin_ratio=round(latin_ratio, 2),
                text_preview=text[:100],
            )
            return ""
        log.info(
            "Translated query for ES match",
            user_language=user_language,
            original=query[:80], translated=text[:80],
        )
        return text
    except Exception as e:
        log.warning(
            "Query translation failed; caller will use original",
            user_language=user_language,
            error=str(e).splitlines()[0][:200],
        )
        return ""


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

    # Regional-language queries need to be translated to English for the ES
    # match — the drafting corpus is English-only, and raw regional-script
    # tokens don't match English template names. Falls back to the original
    # query on any translation failure. English queries skip translation.
    search_query = query
    if user_language and user_language != "en":
        translated = await _translate_query_for_es_match(query, user_language)
        if translated:
            search_query = translated

    sanitized = _sanitize_es_input(search_query)
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

    # Use the (possibly translated) English `search_query` for the picker so
    # the LLM reasons about English file names against English intent —
    # regional-script tokens against English paths produced picker-rejections
    # even when a good template existed in the corpus.
    picked = await _pick_reference_source(search_query, file_paths)

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
# Stage 1.5 — Gather relevant legal context (Newacts / Legislation /
# Judgments / SCI). Runs the same ES retrievers the domain agents use, in
# parallel, and packages the top hits into a labelled context bundle the
# drafting LLM can anchor on. Replaces the "drafting reads a single
# template" approach with "drafting reads template + relevant statutes +
# relevant precedents." Heavy on context, light on prompt engineering —
# Gemini 2.5 Pro has a 1M-token window; we use a few thousand tokens of
# real grounding instead of begging the model not to hallucinate.
# ---------------------------------------------------------------------------

async def _gather_relevant_context(query: str) -> dict[str, str]:
    """Run the domain retrievers in parallel; return labelled blocks.

    Each block is a string ready to drop into the generation prompt.
    Returns {} when ES is down — generation still proceeds (degraded).
    """
    def _safe_invoke(tool, kwargs):
        try:
            return tool.invoke(kwargs)
        except Exception as e:
            log.warning("Context retriever failed",
                        tool=getattr(tool, "name", "?"),
                        error=str(e)[:200])
            return {"hits": [], "total": 0}

    async def _run(tool, kwargs):
        return await asyncio.to_thread(_safe_invoke, tool, kwargs)

    from tools.shared.elasticsearch_tools import (
        search_newacts, search_legislation, search_judgments,
    )
    from tools.shared import sci_judgment_tools

    # Run all retrievers in parallel.
    newacts_t, legis_t, judg_t, sci_t = await asyncio.gather(
        _run(search_newacts, {"query": query}),
        _run(search_legislation, {"query": query}),
        _run(search_judgments, {"query": query}),
        _run(sci_judgment_tools.search_by_topic, {"query": query, "top_k": 3}),
        return_exceptions=False,
    )

    def _format_es_hits(result, label: str, max_hits: int = 3,
                       max_chars_per_hit: int = 800) -> str:
        """Format ES-tool hits as a compact bullet block."""
        hits = (result or {}).get("hits", [])[:max_hits]
        if not hits:
            return ""
        chunks = []
        for h in hits:
            src = h.get("source") or h.get("metadata", {}).get("source") or "(unknown)"
            content = h.get("page_content") or h.get("content") or ""
            if not content:
                continue
            chunks.append(
                f"- **Source**: `{src}`\n  {content[:max_chars_per_hit].strip()}"
            )
        if not chunks:
            return ""
        body = "\n\n".join(chunks)
        return f"## {label}\n{body}\n"

    def _format_sci(result, max_chars: int = 2400) -> str:
        """SCI tool returns a JSON-stringified result. Trim to a sane size."""
        if not result:
            return ""
        text = result if isinstance(result, str) else str(result)
        text = text.strip()
        if not text or text.startswith("No "):
            return ""
        return f"## RELEVANT SUPREME COURT JUDGMENTS\n{text[:max_chars]}\n"

    blocks: dict[str, str] = {}
    n = _format_es_hits(newacts_t, "RELEVANT BNS / BNSS / BSA SECTIONS")
    if n: blocks["newacts"] = n
    l = _format_es_hits(legis_t, "RELEVANT LEGISLATION SECTIONS")
    if l: blocks["legislation"] = l
    j = _format_es_hits(judg_t, "RELEVANT HIGH COURT JUDGMENTS")
    if j: blocks["judgments"] = j
    s = _format_sci(sci_t)
    if s: blocks["sci"] = s

    log.info("Context gather completed",
             blocks=list(blocks.keys()),
             total_chars=sum(len(v) for v in blocks.values()))
    return blocks


# ---------------------------------------------------------------------------
# Stage 2 — Generation.
#
# Public entry: `_generate_draft(...) -> str`. Thin DISPATCHER — asks
# `_judge_fanout` whether the document benefits from section-by-section
# generation, then runs one of:
#
#   - `_generate_single_pass` — one Gemini 2.5 Pro call for the whole
#     document. Used for short letters, notices, single-page applications,
#     simple transactional instruments.
#
#   - `_generate_sectionwise` — sequential per-section loop, two sections
#     per Pro call, each call seeing the document drafted so far. Used for
#     long multi-section instruments (writs, plaints, written statements,
#     detailed bail applications).
#
# Multilingual: both paths flow `user_language` through `localize_prompt`,
# so Hindi, Marathi, Gujarati, Kannada, Tamil, Telugu, Malayalam, Bengali,
# Punjabi, Urdu, Odia, Assamese, Sanskrit drafts inherit the same script /
# numeral / ceremonial-block directives. The judge call ALSO emits each
# section's heading in the user's target language and script, so the
# section pair generator gets a localized heading directly.
#
# The public `_generate_draft` signature stays `-> str` so callers (i.e.
# `drafting_node`) don't change.
# ---------------------------------------------------------------------------

async def _generate_single_pass(
    query: str,
    user_facts: str,
    reference_draft: str,
    user_intent,
    user_language: str,
    progress_emit,
    gathered_context: dict[str, str] | None = None,
    review_and_redraft_mode: bool = False,
) -> str:
    """Produce the full document in one Gemini 2.5 Pro call.

    Inputs: (system_prompt + user_query + UPLOADED SOURCE DOCUMENTS +
    reference_draft + gathered legal context). The LLM decides headings,
    sections, length, footer, signature block based on the reference and
    the user's ask.

    `user_facts` is the RAW extracted text of uploaded documents, passed
    verbatim — no bullet-summary middleman. Gemini 2.5 Pro's 2M-token
    window can consume multi-100K-char PDFs; a raw source is required for
    tasks like rejoinder / para-wise reply where the model must walk the
    source document paragraph-by-paragraph.
    """
    from config.prompts import DRAFTING_SYSTEM_PROMPT
    from langchain_core.messages import SystemMessage, HumanMessage

    system_prompt = localize_prompt(DRAFTING_SYSTEM_PROMPT, user_language, user_intent)
    if review_and_redraft_mode:
        # The default system prompt frames REFERENCE DRAFT as "from a
        # different matter — MUST NOT appear in your output". In
        # review-and-redraft mode the reference IS the uploaded document,
        # so that default framing produces a generic template (Master
        # Services Agreement / Contract-Analysis Memorandum) instead of a
        # corrected version of the uploaded document. Prepend a MODE
        # OVERRIDE that flips the framing at the system-prompt level.
        system_prompt = _REVIEW_AND_REDRAFT_MODE_OVERRIDE + "\n\n---\n\n" + system_prompt

    source_docs_block = ""
    if user_facts and user_facts.strip():
        source_docs_block = (
            "## UPLOADED SOURCE DOCUMENTS (verbatim raw text — every "
            "party name, date, address, amount, statutory reference, and "
            "paragraph-level assertion in your output MUST be sourced "
            "from this block or the USER QUERY below. Do NOT compress, "
            "summarise, or skip content. When a section requires walking "
            "the source paragraph-by-paragraph — a rejoinder, para-wise "
            "reply, counter-affidavit, or written statement — use the "
            "paragraph structure and numbering from this block directly.)\n"
            f"{user_facts.strip()}\n\n"
        )

    if review_and_redraft_mode:
        # The reference draft IS the uploaded document. The user is asking
        # us to REVIEW-AND-REDRAFT their own document — every party name,
        # court name, case number, statutory citation, and case-specific
        # detail must be PRESERVED VERBATIM. Only the substantive legal
        # errors and formatting are to be corrected.
        reference_block = (
            "## REFERENCE DRAFT (this is the SAME document the user uploaded — "
            "the user is performing a REVIEW-AND-REDRAFT of THEIR OWN document. "
            "PRESERVE every party name, court name, case number, forum, statutory "
            "citation, address, date, monetary amount, and case-specific detail "
            "in this block VERBATIM. Correct ONLY the substantive legal errors "
            "(wrong statute, wrong Act name, missing procedural section, "
            "misstated law, missing verification / prayer conventions) and the "
            "formatting. Do NOT rewrite this as a generic template. Do NOT "
            "replace real values with `[placeholders]`. Do NOT introduce a "
            "contract-analysis / memorandum / MOU / lease-deed shape unless the "
            "uploaded document itself is one of those.)\n"
            f"{reference_draft.strip()}\n\n"
        )
    else:
        reference_block = (
            "## REFERENCE DRAFT (STRUCTURE-ONLY example from a different matter — "
            "IGNORE every name, date, address, amount, party detail, and case-"
            "specific value in this block. They belong to a different person's "
            "matter and MUST NOT appear in your output. Use ONLY the reference's "
            "shape: section ordering, headings, salutations, conventions, "
            "phrasing patterns, and statutory-citation style.)\n"
            f"{reference_draft.strip() if reference_draft else '(no reference draft available — produce the document from the user query and uploaded source documents alone, following Indian-law conventions for the document type)'}\n\n"
        )

    # Gathered context — relevant statutes / judgments retrieved by the
    # domain agents' own ES tools. Injected BEFORE the reference / source
    # blocks because it's legal-grounding background; the user-specific
    # blocks sit closer to the closing instruction (recency).
    context_block = ""
    if gathered_context:
        parts = []
        for key in ("newacts", "legislation", "judgments", "sci"):
            v = gathered_context.get(key)
            if v:
                parts.append(v)
        if parts:
            context_block = (
                "## RELEVANT LEGAL CONTEXT (statutes and precedents the "
                "drafting system retrieved for this matter — use these for "
                "INLINE STATUTORY CITATIONS and LEGAL REASONING; do NOT "
                "wholesale copy their party names or case facts into the "
                "draft)\n"
                + "\n".join(parts)
                + "\n"
            )

    user_block = (
        f"{context_block}"
        f"{reference_block}"
        f"{source_docs_block}"
        "## USER QUERY (the document-type request — what to draft)\n"
        f"{query.strip()}\n\n"
        "Produce the complete document the user asked for, in standard "
        "Indian-law conventions for the document type the user named. "
        "EVERY party name, date, address, monetary amount, statutory "
        "reference, and case-specific detail MUST come VERBATIM from the "
        "UPLOADED SOURCE DOCUMENTS block or the USER QUERY above — those "
        "are the only sources of fact for this matter. Cite inline statutes "
        "and (where applicable) precedents from the RELEVANT LEGAL CONTEXT "
        "above. Do NOT invent, substitute, paraphrase, or carry over "
        "canonical-sounding Indian-law example values from your training "
        "data (e.g. 'Priyanka', 'Sneha', 'Bhausaheb', 'Sakore', 'Anjali "
        "Deshmukh', 'Nashik', 'Sangamner', 'Ahmednagar', '29 May 2022', "
        "'1 June 2020') — using any of those when the source names "
        "different real parties is a CRITICAL error. If a value is "
        "genuinely absent from both case sources above, use a clearly-"
        "bracketed placeholder (e.g. [Advocate's Address], [Reference "
        "Number]). NEVER copy a party name / fact value from the REFERENCE "
        "DRAFT — its values belong to a different matter. Output ONLY the "
        "document itself — no preamble, no postscript, no meta-commentary."
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

    # Gemini occasionally trips the RECITATION safety filter on redraft
    # prompts (it thinks the model is reciting the source document too
    # directly). When that happens, `response.content` is "" and
    # `response_metadata.finish_reason` is "RECITATION". On a single retry,
    # we append an instruction asking the model to substantially paraphrase
    # — that usually clears the filter. If the retry also comes back empty,
    # we surface a clean error instead of leaking the LangChain AIMessage
    # repr (which used to ship as the final response, looking like
    # "content='' additional_kwargs={} response_metadata={...}").
    async def _invoke_once(extra_instruction: str = "") -> "object":
        final_user_block = (
            user_block + "\n\n" + extra_instruction if extra_instruction
            else user_block
        )
        return await asyncio.to_thread(
            llm.invoke,
            [SystemMessage(content=system_prompt),
             HumanMessage(content=final_user_block)],
        )

    from core.token_tracker import record as _record_tokens

    def _finish_reason(r) -> str:
        meta = getattr(r, "response_metadata", None) or {}
        return str(meta.get("finish_reason") or "").upper()

    try:
        with log_time(log, "Single-pass draft generation"):
            response = await _invoke_once()
    except Exception as e:
        log.error("Draft generation LLM call failed",
                  error=str(e)[:200], exc_info=True)
        raise

    _record_tokens("Drafting", "generate", response)
    # `.text` flattens Gemini 3.x list-of-content-blocks into a string and
    # returns the plain `.content` for Gemini 2.5 unchanged. See LangChain
    # docs on "Gemini 3 series models return a list of content blocks".
    text = getattr(response, "text", "") or ""

    if not text:
        reason = _finish_reason(response)
        log.warning(
            "Draft generation produced empty content — Gemini block",
            finish_reason=reason or "unknown",
        )

        if reason in ("RECITATION", "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            paraphrase_instruction = (
                "IMPORTANT: Your previous draft response was empty because "
                f"Gemini's {reason.title()} filter flagged it. Rewrite the "
                "document so that, while every party name, date, monetary "
                "amount, and case-specific fact still comes VERBATIM from "
                "the CASE FACTS and USER QUERY NARRATIVE above (those are "
                "non-negotiable), the surrounding LEGAL PROSE, statutory "
                "phrasing, headings, and structural language are "
                "substantially paraphrased in your own words — do not copy "
                "long verbatim passages from the REFERENCE DRAFT or the "
                "uploaded source document. Vary sentence structure, choose "
                "synonyms for non-fact language, and re-order grounds and "
                "sub-clauses where it does not change meaning."
            )
            try:
                with log_time(log, "Draft generation retry (paraphrase)"):
                    response = await _invoke_once(paraphrase_instruction)
                _record_tokens("Drafting", "generate_retry", response)
                text = getattr(response, "text", "") or ""
            except Exception as e:
                log.error("Draft generation retry failed",
                          error=str(e)[:200], exc_info=True)
                # Keep text="" so we surface the error below.

        if not text:
            second_reason = _finish_reason(response)
            log.error(
                "Draft generation blocked twice; surfacing clean error",
                first_reason=reason or "unknown",
                second_reason=second_reason or "unknown",
            )
            raise RuntimeError(
                "Draft generation was blocked by the language model's "
                f"safety filter ({second_reason or reason or 'unknown'}). "
                "Please try rephrasing your request or removing direct "
                "copies of source-document text from the prompt."
            )

    log.info("Draft generated", length=len(text))
    return text


# ---------------------------------------------------------------------------
# Stage 2 — Fan-out judge + section-by-section generator.
#
# The judge runs ONCE per drafting request to decide single-pass vs
# section-wise. When section-wise, it also emits the section list with
# headings already adapted to the user's matter and rendered in the
# user's target output language. The section pair generator then walks
# the list in pairs of two (last is solo if odd), each pair seeing the
# document drafted so far for continuity of numbering / party labels /
# tone.
# ---------------------------------------------------------------------------


class _Section(BaseModel):
    """One section of a fan-out section list. Emitted by `_judge_fanout`."""
    id: str = Field(
        ...,
        description=(
            "Short English slug for internal control (e.g. 'cause_title', "
            "'facts', 'grounds', 'prayer', 'verification'). NOT user-visible."
        ),
    )
    heading: str = Field(
        ...,
        description=(
            "Display heading for the FINAL draft, written in the user's "
            "target output language and script."
        ),
    )
    summary: str = Field(
        "",
        description=(
            "One short sentence (English) of what content goes in this "
            "section. Used internally to brief the section writer."
        ),
    )


class _FanoutStrategy(BaseModel):
    """Structured output from `_judge_fanout`."""
    should_fanout: bool = Field(
        ...,
        description=(
            "True iff the document benefits from sequential per-section "
            "generation. False = single-pass for short documents."
        ),
    )
    sections: list[_Section] = Field(
        default_factory=list,
        description=(
            "Ordered section list — only meaningful when should_fanout=True. "
            "Soft-capped at 15 by the prompt; no code-level cap."
        ),
    )
    reasoning: str = Field(
        "",
        description="One short sentence explaining the decision.",
    )


# ---------------------------------------------------------------------------
# Per-section source-chunk router (opt-in, off by default).
#
# When enabled and the uploaded user_facts blob is large, each section-pair
# call routes to a subset of paragraph chunks instead of receiving the full
# raw source. This is a cost + overflow optimisation:
#
#   - Before: raw user_facts sent 1× per pair (N pairs × full source)
#   - After:  each pair sees only chunks the router picked as relevant
#
# Preserves CLAUDE.md invariant #6 (raw source flows into every pair) via
# fallback: when the flag is OFF, the router is never called; when it's ON
# but the router fails / returns empty / the upload is below threshold, the
# full raw source is sent unchanged.
#
# Enable per-worker via `DRAFTING_PER_SECTION_CHUNKING=1`. Threshold below
# which chunking is skipped is `_PER_SECTION_CHUNKING_MIN_CHARS`.
# ---------------------------------------------------------------------------

_PER_SECTION_CHUNKING_MIN_CHARS = 100_000

# Split on 2+ newlines. Legal PDFs / DOCX extractions typically have blank
# lines between paragraphs; when they don't (single-paragraph huge blob) we
# return a single chunk and the router-guard bails to passthrough.
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


def _chunk_user_facts(user_facts: str) -> list[str]:
    """Split user_facts into paragraph-like chunks preserving order.

    Empty user_facts → empty list. Single-paragraph blob (no blank lines) →
    one chunk containing the whole text. Chunks preserve internal newlines
    and original numbering / heading text so `## paragraph 5` in the source
    still starts with `## paragraph 5` after picking.
    """
    if not user_facts:
        return []
    return [c.strip() for c in _PARAGRAPH_SPLIT_RE.split(user_facts) if c.strip()]


class _SelectedChunks(BaseModel):
    """Structured output from `_pick_relevant_chunk_indices`."""
    chunk_indices: list[int] = Field(
        default_factory=list,
        description=(
            "0-indexed integers into the paragraph-chunk catalog. Empty "
            "list = 'no chunk-level filtering possible for this section' — "
            "the caller falls back to sending the raw source unchanged."
        ),
    )
    reasoning: str = Field(
        "", description="One short sentence rationale.",
    )


async def _pick_relevant_chunk_indices(
    *,
    user_facts_chunks: list[str],
    section: "_Section",
    query: str,
    preview_chars_per_chunk: int = 300,
) -> list[int]:
    """Ask Gemini Flash Lite which chunks the section-writer will need.

    Returns a list of valid indices into `user_facts_chunks` (0-based, in
    the router's picked order, deduplicated by the caller). On any failure
    or empty selection, returns [] — the caller then falls back to sending
    the full raw source unchanged, preserving the current pipeline's
    "raw source into every pair-call" behaviour.
    """
    if not user_facts_chunks:
        return []
    try:
        from langchain.chat_models import init_chat_model
        from config.prompts import DRAFTING_CHUNK_ROUTER_PROMPT

        preview_lines: list[str] = []
        for i, chunk in enumerate(user_facts_chunks):
            preview = chunk[:preview_chars_per_chunk].replace("\n", " ").strip()
            if len(chunk) > preview_chars_per_chunk:
                preview += " ..."
            preview_lines.append(f"[{i}] {preview}")
        catalog = "\n".join(preview_lines)

        llm = init_chat_model(
            "google_genai:gemini-2.5-flash-lite",
            temperature=0.0,
        ).with_structured_output(_SelectedChunks, include_raw=True)

        prompt = ChatPromptTemplate.from_template(DRAFTING_CHUNK_ROUTER_PROMPT)
        chain = prompt | llm

        with log_time(log, f"Chunk router (section {section.heading[:40]})"):
            raw_and_parsed = await asyncio.wait_for(
                chain.ainvoke({
                    "query": query[:2000],
                    "section_heading": section.heading,
                    "section_summary": section.summary or "(no summary)",
                    "chunk_catalog": catalog,
                    "total_chunks": len(user_facts_chunks),
                }),
                timeout=20,
            )
        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "chunk_router", raw_and_parsed.get("raw"))

        parsed: _SelectedChunks = raw_and_parsed["parsed"]
        picked = [
            i for i in parsed.chunk_indices
            if isinstance(i, int) and 0 <= i < len(user_facts_chunks)
        ]
        log.info(
            "Chunk router selected",
            section=section.heading[:40],
            picked=len(picked),
            total=len(user_facts_chunks),
            reasoning=parsed.reasoning[:120],
        )
        return picked
    except Exception as e:
        log.warning(
            "Chunk router failed; caller will fall back to raw source",
            section=section.heading[:40],
            error=str(e).splitlines()[0][:200],
        )
        return []


def _reference_excerpt(
    reference_draft: str, head: int = 3000, tail: int = 1000,
) -> str:
    """Format a reference draft for the judge's view.

    For short references (<= head+tail), pass the whole thing. For long
    references, take the head (cause title + opening Parts) and tail
    (Prayer + Verification + signature) so the judge sees the structural
    bookends without paying for the full middle.
    """
    if not reference_draft:
        return "(no reference draft available — fan-out is unlikely)"
    text = reference_draft.strip()
    if len(text) <= head + tail:
        return text
    return (
        f"{text[:head]}\n\n"
        f"[...middle of reference omitted for brevity — "
        f"{len(text) - head - tail} chars elided...]\n\n"
        f"{text[-tail:]}"
    )


async def _judge_fanout(
    query: str,
    reference_draft: str,
    user_language: str,
    user_intent,
) -> _FanoutStrategy:
    """Decide single-pass vs section-by-section. Always falls back to
    single-pass on any error so traffic never breaks.
    """
    try:
        from langchain.chat_models import init_chat_model
        from core.language import language_name
        from config.prompts import DRAFTING_FANOUT_JUDGE_PROMPT

        lang_name = language_name(user_language)
        excerpt = _reference_excerpt(reference_draft)

        # Depth hint — the fan-out judge previously never saw the depth
        # intent, so "in depth" / "detailed" requests on medium docs
        # (legal notices, complaints, one-page applications) collapsed
        # into single-pass and produced thin output even after the
        # per-section depth-directive shipped. Pass an explicit hint so
        # the judge can bias toward fan-out on depth=detailed.
        depth = getattr(user_intent, "response_depth", "standard") if user_intent else "standard"
        if depth == "detailed":
            depth_directive = (
                "The user asked for a DETAILED draft (response_depth = "
                "'detailed'). Bias toward FAN-OUT even for medium document "
                "types you would normally single-pass — single-pass cannot "
                "adequately deliver the depth the user requested."
            )
        elif depth == "brief":
            depth_directive = (
                "The user asked for a BRIEF draft (response_depth = 'brief'). "
                "Prefer single-pass unless the reference explicitly demands "
                "fan-out."
            )
        else:
            depth_directive = (
                "The user did not express an explicit depth preference. "
                "Apply the default fan-out rules above."
            )

        llm = init_chat_model(
            "google_genai:gemini-2.5-flash-lite",
            temperature=0.0,
        ).with_structured_output(_FanoutStrategy, include_raw=True)

        prompt = ChatPromptTemplate.from_template(DRAFTING_FANOUT_JUDGE_PROMPT)
        chain = prompt | llm

        with log_time(log, "Fan-out judge"):
            raw_and_parsed = await asyncio.wait_for(
                chain.ainvoke({
                    "query": query[:2000],
                    "reference_excerpt": excerpt,
                    "user_language_name": lang_name,
                    "depth_directive": depth_directive,
                }),
                timeout=15,
            )
        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "fanout_judge", raw_and_parsed.get("raw"))

        parsed = raw_and_parsed["parsed"]
        log.info(
            "Fan-out judge decided",
            should_fanout=parsed.should_fanout,
            sections=len(parsed.sections),
            reasoning=parsed.reasoning[:160],
        )
        return parsed
    except Exception as e:
        log.warning(
            "Fan-out judge failed; defaulting to single-pass",
            error=str(e).splitlines()[0][:200],
        )
        return _FanoutStrategy(
            should_fanout=False,
            reasoning="judge call failed; defaulted to single-pass",
        )


async def _generate_section_pair(
    *,
    sections_to_write: list[_Section],
    section_position_start: int,
    total_sections: int,
    query: str,
    user_facts: str,
    reference_draft: str,
    prior_text: str,
    gathered_context: dict[str, str] | None,
    user_intent,
    user_language: str,
    review_and_redraft_mode: bool = False,
) -> str:
    """Produce 1 or 2 consecutive sections of the document in one Gemini
    2.5 Pro call. Mirrors the safety / retry pattern of single-pass.

    `user_facts` is the RAW extracted text of uploaded documents, threaded
    into every section-pair call so any section that walks the source
    paragraph-by-paragraph (para-wise reply, rejoinder denials, counter-
    affidavit response) has direct access to the source's paragraph
    structure and numbering.
    """
    from config.prompts import DRAFTING_SECTION_PAIR_PROMPT
    from langchain_core.messages import SystemMessage, HumanMessage

    system_prompt = localize_prompt(
        DRAFTING_SECTION_PAIR_PROMPT, user_language, user_intent,
    )
    if review_and_redraft_mode:
        # See rationale in _generate_single_pass — the section-pair prompt
        # carries the same "REFERENCE DRAFT belongs to a DIFFERENT matter"
        # framing (config/prompts.py DRAFTING_SECTION_PAIR_PROMPT rule #1)
        # that must be flipped at the system-prompt level for review-and-
        # redraft mode to actually preserve the uploaded document's
        # party names, court, case number, etc.
        system_prompt = _REVIEW_AND_REDRAFT_MODE_OVERRIDE + "\n\n---\n\n" + system_prompt

    section_lines: list[str] = []
    for offset, sec in enumerate(sections_to_write):
        position = section_position_start + offset
        section_lines.append(
            f"{offset + 1}. **{sec.heading}** — {sec.summary or '(see structure in reference)'}\n"
            f"   (Section {position} of {total_sections} in the full document; "
            f"slug: `{sec.id}`)"
        )
    sections_block = "\n\n".join(section_lines)

    source_docs_block = (
        "## UPLOADED SOURCE DOCUMENTS (verbatim raw text — every party "
        "name, date, address, amount, statutory reference, and paragraph-"
        "level assertion in your output MUST be sourced from this block or "
        "the USER QUERY below. When your section requires walking the "
        "source paragraph-by-paragraph — para-wise reply, rejoinder "
        "denials, counter-affidavit response — use the paragraph structure "
        "and numbering from this block directly, quoting or paraphrasing "
        "the specific assertions your section is responding to.)\n"
        f"{user_facts.strip()}\n\n"
    ) if user_facts and user_facts.strip() else ""

    context_block = ""
    if gathered_context:
        parts = []
        for key in ("newacts", "legislation", "judgments", "sci"):
            v = gathered_context.get(key)
            if v:
                parts.append(v)
        if parts:
            context_block = (
                "## RELEVANT LEGAL CONTEXT (statutes and precedents retrieved "
                "for this matter — use these for INLINE STATUTORY CITATIONS "
                "and LEGAL REASONING; do NOT wholesale copy their party names "
                "or case facts into the draft)\n"
                + "\n".join(parts)
                + "\n"
            )

    if review_and_redraft_mode:
        reference_block = (
            "## REFERENCE DRAFT (this is the SAME document the user uploaded — "
            "the user is performing a REVIEW-AND-REDRAFT of THEIR OWN document. "
            "PRESERVE every party name, court name, case number, forum, statutory "
            "citation, address, date, monetary amount, and case-specific detail "
            "VERBATIM. Correct ONLY the substantive legal errors (wrong statute, "
            "wrong Act name, missing procedural section, misstated law, missing "
            "verification / prayer conventions) and formatting. Do NOT rewrite "
            "this as a generic template. Do NOT replace real values with "
            "`[placeholders]`. Do NOT introduce a contract-analysis / "
            "memorandum / MOU / lease-deed shape unless the uploaded document "
            "itself is one of those.)\n"
            f"{reference_draft.strip()}\n\n"
        )
    else:
        reference_block = (
            "## REFERENCE DRAFT (STRUCTURE-ONLY example from a different matter — "
            "IGNORE every name, date, address, amount, party detail in this block. "
            "Use ONLY the reference's shape: section ordering, headings, conventions, "
            "phrasing patterns, and statutory-citation style.)\n"
            f"{reference_draft.strip() if reference_draft else '(no reference draft available — follow Indian-law conventions for the document type)'}\n\n"
        )

    if prior_text and prior_text.strip():
        prior_block = (
            "## DOCUMENT SO FAR (sections of THIS document already drafted — "
            "continue numbering and party labels from here; do NOT re-emit "
            "any of this content)\n"
            f"{prior_text.strip()}\n\n"
        )
    else:
        prior_block = (
            "## DOCUMENT SO FAR\n"
            "(This is the FIRST section batch — no prior content. Start the "
            "global paragraph counter at 1 where appropriate.)\n\n"
        )

    user_block = (
        f"{context_block}"
        f"{reference_block}"
        f"{prior_block}"
        f"{source_docs_block}"
        "## USER QUERY (the full document the user asked for — your section(s) "
        "are part of this larger document)\n"
        f"{query.strip()}\n\n"
        "## SECTIONS YOU MUST WRITE NOW\n"
        f"{sections_block}\n\n"
        "Produce ONLY the section bodies named above, in order, each starting "
        "with its own `## ` heading line. No preamble. No postscript. No "
        "transition text between two sections. Continue paragraph numbering "
        "from DOCUMENT SO FAR. Every party name, date, address, monetary "
        "amount, and case-specific detail MUST come VERBATIM from the "
        "UPLOADED SOURCE DOCUMENTS or the USER QUERY. Do NOT substitute "
        "canonical Indian-legal example values (e.g. 'Priyanka', 'Sneha', "
        "'Bhausaheb', 'Sakore', 'Anjali Deshmukh', 'Nashik', 'Sangamner', "
        "'Ahmednagar', '29 May 2022', '1 June 2020') for the real parties "
        "and dates named in the source — that is a CRITICAL error. Cite "
        "statutes inline from RELEVANT LEGAL CONTEXT where applicable."
    )

    llm = get_gemini_pro(
        temperature=0.0,
        max_output_tokens=32768,
        thinking_budget=2048,
    )

    async def _invoke_once(extra_instruction: str = "") -> object:
        final_user_block = (
            user_block + "\n\n" + extra_instruction if extra_instruction
            else user_block
        )
        return await asyncio.to_thread(
            llm.invoke,
            [SystemMessage(content=system_prompt),
             HumanMessage(content=final_user_block)],
        )

    from core.token_tracker import record as _record_tokens

    def _finish_reason(r) -> str:
        meta = getattr(r, "response_metadata", None) or {}
        return str(meta.get("finish_reason") or "").upper()

    if len(sections_to_write) == 1:
        section_label = f"section {section_position_start}"
    else:
        end = section_position_start + len(sections_to_write) - 1
        section_label = f"sections {section_position_start}-{end}"

    try:
        with log_time(log, f"Section pair gen ({section_label})"):
            response = await _invoke_once()
    except Exception as e:
        log.error(
            "Section pair LLM call failed",
            section_label=section_label,
            error=str(e)[:200], exc_info=True,
        )
        raise

    _record_tokens("Drafting", "generate_section_pair", response)
    text = getattr(response, "text", "") or ""

    if not text:
        reason = _finish_reason(response)
        log.warning(
            "Section pair produced empty content",
            section_label=section_label, finish_reason=reason or "unknown",
        )
        if reason in ("RECITATION", "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            paraphrase_instruction = (
                f"IMPORTANT: Your previous response was empty because Gemini's "
                f"{reason.title()} filter flagged it. Rewrite the section(s) "
                f"so that the surrounding LEGAL PROSE, statutory phrasing, "
                f"headings, and structural language are substantially "
                f"paraphrased in your own words — do not copy long verbatim "
                f"passages from the REFERENCE DRAFT or any source document. "
                f"Every party name, date, monetary amount, and case-specific "
                f"fact still comes VERBATIM from CASE FACTS and the USER "
                f"QUERY (those are non-negotiable). Vary sentence structure, "
                f"choose synonyms for non-fact language, and re-order "
                f"sub-clauses where it does not change meaning."
            )
            try:
                with log_time(log, f"Section pair retry ({section_label})"):
                    response = await _invoke_once(paraphrase_instruction)
                _record_tokens(
                    "Drafting", "generate_section_pair_retry", response,
                )
                text = getattr(response, "text", "") or ""
            except Exception as e:
                log.error(
                    "Section pair retry failed",
                    section_label=section_label,
                    error=str(e)[:200], exc_info=True,
                )

        if not text:
            log.error(
                "Section pair blocked twice; emitting empty section",
                section_label=section_label,
            )
            return ""

    log.info(
        "Section pair generated",
        section_label=section_label, length=len(text),
    )
    return text


async def _generate_sectionwise(
    *,
    sections: list[_Section],
    query: str,
    user_facts: str,
    reference_draft: str,
    user_intent,
    user_language: str,
    progress_emit,
    gathered_context: dict[str, str] | None,
    review_and_redraft_mode: bool = False,
) -> str:
    """Walk the section list in pairs of two (last is solo if odd).

    Each pair is one Gemini Pro call seeing the document so far AND the
    uploaded source documents. `user_facts` is threaded into every pair
    call (not just the first) so any section that needs to walk the source
    paragraph-by-paragraph (para-wise reply, rejoinder denials, counter-
    affidavit response) has direct access. A failed pair is logged and
    skipped — the loop continues so the user gets a partial draft instead
    of a hard failure.
    """
    completed: list[str] = []
    total = len(sections)

    # Per-section source-chunk routing (opt-in via env flag).
    # When ON and user_facts is above threshold, each pair sees only the
    # paragraph chunks the router picked as relevant to that pair's
    # sections (union across the pair). Union is taken so a section that
    # needs paragraphs 3-5 and its pair-mate that needs paragraphs 7-9
    # still see 3,4,5,7,8,9 in a single call. On router failure / empty
    # selection, the pair falls back to the raw user_facts unchanged —
    # this preserves CLAUDE.md invariant #6 whenever the router can't help.
    chunking_enabled = (
        os.getenv("DRAFTING_PER_SECTION_CHUNKING", "0") == "1"
        and user_facts
        and len(user_facts) > _PER_SECTION_CHUNKING_MIN_CHARS
    )
    all_chunks: list[str] = _chunk_user_facts(user_facts) if chunking_enabled else []
    # A single-chunk (no blank lines) or trivially-few-chunks blob has no
    # routing signal to extract; skip the router and pass raw source.
    if chunking_enabled and len(all_chunks) < 4:
        chunking_enabled = False
        log.info(
            "Per-section chunking skipped — too few paragraph chunks to route",
            chunks=len(all_chunks),
        )

    i = 0
    while i < total:
        pair = sections[i:i + 2]
        position_start = i + 1

        for offset, sec in enumerate(pair):
            position = position_start + offset
            progress_emit(
                "drafting",
                f"Drafting section {position} of {total}: {sec.heading}",
                substep=True,
                step=f"section:{position}",
            )

        prior_text = "\n\n".join(completed)

        pair_user_facts = user_facts
        if chunking_enabled:
            per_section_picks = await asyncio.gather(*[
                _pick_relevant_chunk_indices(
                    user_facts_chunks=all_chunks,
                    section=sec,
                    query=query,
                )
                for sec in pair
            ])
            union_indices = sorted({idx for picks in per_section_picks for idx in picks})
            if union_indices:
                pair_user_facts = "\n\n".join(all_chunks[idx] for idx in union_indices)
                log.info(
                    "Per-section chunking applied",
                    pair_start=position_start,
                    picked_chunks=len(union_indices),
                    total_chunks=len(all_chunks),
                    original_chars=len(user_facts),
                    reduced_chars=len(pair_user_facts),
                    reduction_pct=round(
                        100 * (1 - len(pair_user_facts) / max(len(user_facts), 1)), 1,
                    ),
                )
            else:
                log.info(
                    "Per-section chunking: no chunks picked — using raw source",
                    pair_start=position_start,
                )

        try:
            pair_text = await _generate_section_pair(
                sections_to_write=pair,
                section_position_start=position_start,
                total_sections=total,
                query=query,
                user_facts=pair_user_facts,
                reference_draft=reference_draft,
                prior_text=prior_text,
                gathered_context=gathered_context,
                user_intent=user_intent,
                user_language=user_language,
                review_and_redraft_mode=review_and_redraft_mode,
            )
        except Exception as e:
            log.warning(
                "Section pair generation failed; continuing to next pair",
                position_start=position_start,
                error=str(e).splitlines()[0][:200],
            )
            pair_text = ""

        if pair_text.strip():
            completed.append(pair_text.strip())

        i += 2

    return "\n\n".join(completed)


async def _generate_draft(
    query: str,
    user_facts: str,
    reference_draft: str,
    user_intent,
    user_language: str,
    progress_emit,
    gathered_context: dict[str, str] | None = None,
    review_and_redraft_mode: bool = False,
) -> str:
    """Thin dispatcher: judge call decides single-pass vs section-wise.

    `user_facts` is the RAW extracted text of uploaded documents, passed
    verbatim through to whichever generation strategy runs. Returns a
    single assembled string regardless of the strategy.
    """
    # Preflight: Gemini 2.5 Pro caps input at 1,048,576 tokens. English
    # text tokenises at ~4 chars/token so the raw-char ceiling is ~4M
    # chars, and 3.5M leaves room for system prompt + reference + context
    # + query. Devanagari (Hindi / Marathi / Sanskrit) and other non-Latin
    # Indic scripts tokenise DENSER — roughly 2.5 chars/token — so the
    # effective ceiling drops to ~2.4M chars for those uploads. When we
    # exceed the applicable budget, every downstream generation call will
    # 400 INVALID_ARGUMENT and the sectionwise path silently produces an
    # empty / badly-holed draft. Short-circuit here with a user-actionable
    # message instead.
    _USER_FACTS_BUDGET_LATIN = 3_500_000
    _USER_FACTS_BUDGET_DEVANAGARI = 2_400_000
    budget = _USER_FACTS_BUDGET_LATIN
    if user_facts:
        sample = user_facts[:20_000]
        deva = sum(1 for c in sample if "ऀ" <= c <= "ॿ")
        if deva / max(len(sample), 1) > 0.3:
            budget = _USER_FACTS_BUDGET_DEVANAGARI
    if user_facts and len(user_facts) > budget:
        log.warning(
            "Drafting user_facts exceeds token budget — returning friendly message",
            facts_chars=len(user_facts),
            budget=budget,
            script="devanagari" if budget == _USER_FACTS_BUDGET_DEVANAGARI else "latin",
        )
        return (
            "The uploaded documents are too large to draft from in a "
            "single response — they exceed the model's context limit. "
            "Please narrow the drafting task to a specific section (for "
            "example, \"draft a reply to paragraph 3 of the notice\"), "
            "or upload smaller or fewer documents so I can process them "
            "properly."
        )

    strategy = await _judge_fanout(
        query=query,
        reference_draft=reference_draft,
        user_language=user_language,
        user_intent=user_intent,
    )

    if not strategy.should_fanout or not strategy.sections:
        log.info(
            "Drafting: single-pass selected",
            should_fanout=strategy.should_fanout,
            section_count=len(strategy.sections),
            reasoning=strategy.reasoning[:200],
        )
        return await _generate_single_pass(
            query=query,
            user_facts=user_facts,
            reference_draft=reference_draft,
            user_intent=user_intent,
            user_language=user_language,
            progress_emit=progress_emit,
            gathered_context=gathered_context,
            review_and_redraft_mode=review_and_redraft_mode,
        )

    log.info(
        "Drafting: section-wise selected",
        sections=len(strategy.sections),
        reasoning=strategy.reasoning[:200],
    )
    progress_emit(
        "drafting",
        f"Drafting {len(strategy.sections)} sections one by one...",
        step="generate",
    )
    return await _generate_sectionwise(
        sections=strategy.sections,
        query=query,
        user_facts=user_facts,
        reference_draft=reference_draft,
        user_intent=user_intent,
        user_language=user_language,
        progress_emit=progress_emit,
        gathered_context=gathered_context,
        review_and_redraft_mode=review_and_redraft_mode,
    )


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
    #
    # Source priority for uploaded-document facts:
    #
    #   1. ``fc.extracted_texts`` — the raw per-file text PyMuPDF/python-docx
    #      produced before chunking. Carries the *whole* document, has no
    #      Chroma dependency, and survives the Chroma pool-exhaustion failure
    #      mode (2026-06-28 incident) where ``chromadb_collections`` ends up
    #      empty. Drafting wants every fact in the document — semantic-search
    #      chunks lose information by design — so the raw text is preferred
    #      even when Chroma is healthy.
    #
    #   2. ``get_full_attachment`` over ``chromadb_collections`` — used only
    #      as a fallback when no raw text was retained (e.g. legacy state
    #      written before extracted_texts was added, or future file types
    #      that route directly to Chroma without staging through
    #      ``pf.extracted_text``).
    #
    # Per-file resolution is tracked in ``seen_names`` so we never duplicate
    # a document's text into the prompt when both paths happen to surface it.
    fact_blocks: list[str] = []
    seen_names: set[str] = set()
    facts_source = "none"   # "extracted" | "chroma" | "mixed" | "none"

    if fc and fc.extracted_texts:
        for entry in fc.extracted_texts:
            text = (entry.get("text") or "").strip()
            name = entry.get("name") or "attached"
            if not text:
                continue
            fact_blocks.append(f"[Uploaded document — {name}]\n{text}")
            seen_names.add(name)
        if fact_blocks:
            facts_source = "extracted"

    if fc and fc.chromadb_collections:
        chroma_added = False
        from tools.shared.vectordb_tools import get_full_attachment
        for cid in fc.chromadb_collections:
            try:
                attached = get_full_attachment.invoke({"collection_id": cid})
                full_text = (attached or {}).get("full_text", "")
                source_file = (attached or {}).get("source_file") or "attached"
                if full_text and source_file not in seen_names:
                    fact_blocks.append(
                        f"[Uploaded document — {source_file}]\n{full_text}"
                    )
                    seen_names.add(source_file)
                    chroma_added = True
            except Exception as e:
                log.warning("get_full_attachment failed",
                            collection=cid, error=str(e))
        if chroma_added:
            facts_source = "mixed" if facts_source == "extracted" else "chroma"

    if user_context:
        fact_blocks.append(f"[Pasted context]\n{user_context[:30000]}")
    if integration_ctx and integration_ctx.has_content:
        fact_blocks.append(integration_ctx.as_prompt_prefix().rstrip())
    user_facts = "\n\n".join(fact_blocks)

    log.info(
        "Agent started",
        query=query[:100],
        has_user_context=bool(user_context),
        has_file_context=bool(fc and (fc.chromadb_collections or fc.extracted_texts)),
        attachment_collections=len(fc.chromadb_collections) if fc else 0,
        extracted_text_files=len(fc.extracted_texts) if fc else 0,
        facts_source=facts_source,
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
        # --- 4. Reference draft acquisition + relevant-context gather.
        #
        # Two branches:
        #
        #   (a) REVIEW-AND-REDRAFT of an uploaded document: the uploaded
        #       document IS the reference the user wants preserved. Skip the
        #       ES picker + web fallback (both discard the format the user
        #       already showed us — see _is_review_and_redraft_of_upload).
        #       Only gather relevant legal context (statutes + precedents).
        #
        #   (b) Fresh draft (or upload without review verbs): existing
        #       parallel gather — ES picker → web fallback for reference,
        #       plus context retrieval.
        #
        # No case-fact extraction step. The raw `user_facts` blob (verbatim
        # extracted text of every uploaded document) flows straight into
        # generation — Gemini 2.5 Pro's 2M-token window can consume
        # multi-100K-char PDFs, and the raw source is required for tasks
        # like rejoinder / para-wise reply where the model must walk the
        # source paragraph-by-paragraph.
        use_upload_as_ref = _is_review_and_redraft_of_upload(
            original_query, user_facts
        )
        if use_upload_as_ref:
            # Restore the raw user prompt for downstream generation. The
            # per-agent rewriter compresses "Review the attached word document
            # and Find out every legal error from the application and redraft
            # with removing all legal error and with most relevant and
            # landmark case laws of supreme court" into a topic-loose
            # "Redraft legal document to remove all legal errors, incorporating
            # relevant Supreme Court landmark cases", which the fanout judge
            # and section-writer LLM cannot anchor to the actual matter
            # (Section 290 BNSS plea bargaining / MV Act 185 in the smoke).
            # The result was a generic contract-analysis memorandum even
            # though the reference draft was correctly set to the uploaded
            # DOCX. Per feedback_preserve_user_query: never lose information
            # from the user's prompt anywhere in the pipeline.
            if query != original_query:
                log.info(
                    "Review-and-redraft mode: restoring raw prompt for downstream",
                    rewritten_len=len(query), raw_len=len(original_query),
                )
                query = original_query
            log.info(
                "Review-and-redraft mode: uploaded document is the reference",
                query_len=len(original_query), user_facts_chars=len(user_facts),
            )
            progress(
                "drafting",
                "Using uploaded document as the reference (review-and-redraft mode)",
                step="reference",
            )
            reference_text = user_facts
            reference_source = "<uploaded:review_and_redraft>"
            reference_kind = "uploaded"
            gathered_ctx = await _gather_relevant_context(query)
        else:
            progress("drafting", "Searching templates and relevant law...",
                     step="reference")
            (reference_text, reference_source, reference_kind), gathered_ctx = \
                await asyncio.gather(
                    _acquire_reference_draft(
                        query,
                        progress,
                        user_language=user_language,
                        intent=intent_obj,
                        original_query=original_query,
                    ),
                    _gather_relevant_context(query),
                )
        if gathered_ctx:
            progress(
                "drafting",
                f"Gathered legal context: {', '.join(gathered_ctx.keys())}",
                substep=True, step="reference",
                found=len(gathered_ctx),
            )

        # --- 5. Generation (Gemini 2.5 Pro) with raw source + gathered context.
        # The dispatcher picks single-pass or per-section fan-out based on
        # `_judge_fanout`. Raw `user_facts` is threaded through both paths.
        #
        # `use_upload_as_ref` propagates to the generators so the reference
        # block wording flips to "PRESERVE these values verbatim" instead of
        # the default "IGNORE these values (they belong to a different
        # matter)". Without this flip, the section writer receives the
        # uploaded doc twice (once as reference, once as source) with
        # contradictory directives and falls back to a generic template.
        draft = await _generate_draft(
            query=query,
            user_facts=user_facts,
            reference_draft=reference_text,
            user_intent=intent_obj,
            user_language=user_language,
            progress_emit=progress,
            gathered_context=gathered_ctx,
            review_and_redraft_mode=use_upload_as_ref,
        )

        # --- 6. Mechanical cleanup (mojibake, HTML strip, [CITE:] strip) ---
        progress("drafting", "Cleaning up draft...", step="cleanup")
        draft, draft_warnings = validate_draft(draft)

        # --- 7. self_refine — the scope critic + audit pass against UserIntent ---
        #
        # Skipped in review-and-redraft mode. The critic is calibrated against
        # a "fresh draft produced from scratch" — it expects things like a
        # full landmark-precedent block, standard prayer/verification wording,
        # or specific procedural blocks the user's uploaded document may
        # legitimately have omitted. When the user's task is "review my
        # existing document and correct the legal errors", the critic
        # reliably fires 3+ violations against the correct section-writer
        # output, and the refiner then wholesale rewrites the corrected draft
        # into a generic template (observed on smoke: Master Services
        # Agreement / Contract-Analysis Memorandum replacing the Section 290
        # BNSS plea-bargaining redraft that the section writer had produced
        # correctly — confirmed via SECTION_PAIR_PEEK diagnostic).
        #
        # Long-term the critic prompt should gain a review-and-redraft mode
        # branch that trusts the uploaded document's shape; for now the
        # cleanest fix is to short-circuit the loop.
        if draft and intent_obj is not None and not use_upload_as_ref:
            try:
                progress(
                    "drafting",
                    "Auditing draft against your directives...",
                    step="self_refine",
                )
                source_langs = detect_source_languages(user_facts)
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
        elif use_upload_as_ref:
            log.info(
                "Review-and-redraft mode: self_refine skipped "
                "(critic reliably rewrites correct redrafts into generic templates)"
            )

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
        elif reference_kind == "uploaded":
            sources = [SourceMetadata(
                source_type="drafting",
                title="Reference draft (uploaded document — review-and-redraft mode)",
                content=[reference_text[:300]] if reference_text else [],
                file_name=reference_source,
                agent_name="Drafting",
                template_type="uploaded",
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
