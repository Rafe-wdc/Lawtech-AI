"""Agent #5 — Judgment Agent

Searches court judgments using smart multi-strategy ES queries.
Extracts structured case metadata and generates S3 PDF links.

Handles: "State vs Doe 2020", "SC cases on anticipatory bail",
         "bail cases under section 438", "Kirloskar vs Kirloskar", etc.

Uses: GPT-4o (metadata extraction), Gemini Flash (response generation)
Data Source: Elasticsearch "judgements" index + AWS S3

Search Strategies (tried in order of specificity):
1. Citation / case number direct lookup
2. Both party names (exact → no-year → swapped → fuzzy)
3. Single party name
4. Case type specialized (bail, quashing, writ, appeal, etc.)
5. Legal provision (acts/sections invoked)
6. Multi-tier (topics + acts + lexical BM25)
7. Relaxed full-text fallback
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import get_gemini_flash, get_gemini_flash_full
from core.retrieval_relevance import (
    check_retrieval_relevance, head_tail_apology_detected,
)
from core.language import localize_prompt
from core.logger import get_logger, log_time, short_err
from core.settings import TIMEOUT_ES_PARALLEL_SEC
from config.prompts import JUDGMENT_SYSTEM_PROMPT
from tools.shared.judgment_search import smart_judgment_search
from tools.shared.storage_tools import generate_s3_link
from tools.shared.llm_tools import CaseMetadata
from core.progress import progress

log = get_logger("Judgment")


# --- Exact-section lookup detection -------------------------------------
#
# When the user asks "case law on Section 313 CrPC", the metadata extractor
# fills `acts_or_sections` and the ES search runs against those provisions
# directly. The Judgment relevance gate then routinely false-positives on
# results like "Section 302 IPC murder appeal that mentions Section 313
# procedurally" -- the judge sees a case that's "primarily about Sec 302"
# and rejects, triggering a 17-second Gemini + Google Search fallback whose
# sources are web URLs (vertexaisearch.cloud.google.com redirectors)
# instead of the user's S3-hosted PDF judgments.
#
# Fix mirrors the Newacts gate-skip from yesterday: when the user has
# explicitly named a section AND most retrieved hits' acts_or_sections_invoked
# metadata cites that same section, the hits are by-construction on-topic.
# Running the gate cannot improve precision and routinely sends users to
# the web fallback path.

# Normalisation helpers -- "Section 313 CrPC" vs "s.313 cr.p.c." vs "Sec 313 of the
# Code of Criminal Procedure" must all reduce to the same comparison token.
_SECTION_RE = re.compile(r'(?:section|sec|s\.?)\s*(\d+)', flags=re.IGNORECASE)


def _section_numbers(text: str) -> set[str]:
    """Extract every "Section <N>" reference (case-insensitive)."""
    return set(_SECTION_RE.findall(text or ""))


def _is_exact_section_lookup(
    metadata: "CaseMetadata", sources: list[SourceMetadata],
    min_match_ratio: float = 0.5,
) -> bool:
    """True when the query is an explicit-section lookup AND at least half
    the retrieved hits cite that section in their `acts_or_sections_invoked`.

    Both conditions must hold:
      (a) `metadata.acts_or_sections` is non-empty -- the extractor identified
          one or more named provisions in the user query.
      (b) >= `min_match_ratio` of `sources` have AT LEAST ONE of the queried
          section numbers in their `acts_or_sections_invoked` metadata.
    """
    if not metadata.acts_or_sections or not sources:
        return False
    queried_secs: set[str] = set()
    for provision in metadata.acts_or_sections:
        queried_secs |= _section_numbers(provision)
    if not queried_secs:
        return False
    matched = 0
    for src in sources:
        hit_secs: set[str] = set()
        for invoked in (src.acts_or_sections_invoked or []):
            hit_secs |= _section_numbers(invoked)
        if hit_secs & queried_secs:
            matched += 1
    ratio = matched / len(sources)
    return ratio >= min_match_ratio


# --- Court allowlist for the `judgements` ES index ---------------------
#
# The `judgements` index has exactly 17 distinct court_name values (per
# an ES terms aggregation on 2026-09-01). When the metadata extractor
# emits a *specific* named court that is NOT in this set (e.g.
# "karnataka high court", "allahabad high court", "punjab and haryana
# high court", "andhra pradesh high court", "telangana high court",
# "himachal pradesh high court", "patna high court", "chhattisgarh
# high court", "jharkhand high court"), passing it as a hard
# `match_phrase` filter guarantees zero ES hits. The pipeline still
# falls through to the web fallback via the 0-hits path, but that
# wastes ~3-5s of ES latency (preliminary + refined + rewrite+retry
# all return empty). Detect the mismatch up front and route straight
# to the web fallback.
#
# Two normalisation edge cases baked into `_normalize_court_filter`:
#   1. The index stores "odisa high court" (source-data typo).
#      Users typing the correct "odisha high court" would otherwise
#      miss their own court's docs. Normalise both spellings to the
#      typo'd bucket.
#   2. The index stores "Tribunal" (capital T). `smart_judgment_search`
#      lowercases the filter before `match_phrase`, so the comparison
#      here is lowercase-only.

_INDEXED_COURTS: frozenset[str] = frozenset({
    "bombay high court",
    "kerala high court",
    "rajasthan high court",
    "madhya pradesh high court",
    "odisa high court",  # sic — matches the source-data typo
    "gujarat high court",
    "calcutta high court",
    "delhi high court",
    "gauhati high court",
    "uttarakhand high court",
    "jammu and kashmir high court",
    "supreme court",
    "tripura high court",
    "manipur high court",
    "meghalaya high court",
    "sikkim high court",
    "tribunal",
})


def _normalize_court_filter(court: str | None) -> tuple[str | None, bool]:
    """Normalise an extracted court name against the ES index allowlist.

    Returns `(filter_value, court_not_indexed)`:
      - `(None, False)` — no court, empty string, or generic "high court"
        with no location prefix. The search should run without any court
        filter (all courts eligible).
      - `(str, False)` — court exists in the index. Pass as a filter.
      - `(None, True)` — court is a *specific* named court NOT in the
        index. The pipeline should skip ES and route straight to the web
        fallback. `filter_value` is None (unused in that path).
    """
    if not court:
        return None, False
    normalized = court.strip().lower()
    if not normalized:
        return None, False
    # Fold correct Odisha / Orissa spellings into the index's typo bucket.
    if normalized in ("odisha high court", "orissa high court"):
        normalized = "odisa high court"
    if normalized in _INDEXED_COURTS:
        return normalized, False
    # Generic "high court" (no location prefix) — user did not name a
    # specific court, so drop the filter and search across all courts.
    if normalized == "high court":
        return None, False
    # Specific named court not present in the ES index.
    return None, True


# --- Case Metadata Extraction ---

METADATA_EXTRACTION_PROMPT = """You are an expert in legal text parsing and Elasticsearch query formulation.

Extract structured metadata from the user query and generate a normalized lexical search string.

Return valid JSON with these keys:
- court_name: string (lowercase, e.g., "supreme court", "bombay high court")
- petitioner_names: list of strings
- respondent_names: list of strings
- year: integer or null
- topics: list of legal topics
- acts_or_sections: list of provisions (e.g., "section 138 ni act")
- lexical_query: normalized BM25 search string
- size: integer (1-50)

## Rules

1. **Court name detection** (normalize to lowercase):
   - "supreme court" — strip "of India" / "at" suffixes
     ("Supreme Court of India" → "supreme court")
   - "[location] high court" — preserve the location prefix
     ("High Court of Delhi" → "delhi high court";
      "Bombay High Court" → "bombay high court")
   - Other forums: "district court", "family court", "consumer court",
     "tribunal", "nclt", "nclat", "itat", "cat", "drt"
   - Recognize abbreviations:
     * "SC" → "supreme court"
     * "HC" alone → "high court"
     * "<location> HC" → "<location> high court"
       ("Delhi HC" → "delhi high court", "Bombay HC" → "bombay high court")
   - If no court is mentioned, output empty string "".

2. Party before "vs"/"versus"/"v." = petitioner, after = respondent.
   Preserve multi-word names with "& Ors." intact — do NOT split them.

3. Normalize provisions: lowercase, remove "of"/"under", join with spaces.
   "Section 482 of CrPC" → "section 482 crpc".

4. lexical_query construction:
   - Both petitioner and respondent → "{{petitioner}} versus {{respondent}} {{year}}"
   - Both acts_or_sections and topics → "{{acts_or_sections}} {{topics}}"
   - Only one of them exists → use it directly
   - Prepend court_name when present and not already in the party names.

5. Remove filler words: "give me", "find", "show", "tell", "list",
   "latest", "recent", "important", "famous", "landmark".

6. Size determination (priority order):
   - Explicit user request ("top 5", "give me 10", "first 20",
     "show 1") → extract that integer; clamp to [1, 50].
   - "one" / "single" / "best" → size=1.
   - Heuristic (only if no explicit size):
     * Both petitioner + respondent → size=1
     * acts_or_sections exist → size=5
     * Only topics → size=10
     * Otherwise → size=3

## Examples

Example 1 — Court + statutory reference
Query: "Supreme Court judgments under Section 138 of NI Act"
{{"court_name": "supreme court", "petitioner_names": [], "respondent_names": [],
 "year": null, "topics": [], "acts_or_sections": ["section 138 ni act"],
 "lexical_query": "supreme court section 138 ni act", "size": 5}}

Example 2 — High Court + topic
Query: "Bombay HC cases on negligence"
{{"court_name": "bombay high court", "petitioner_names": [], "respondent_names": [],
 "year": null, "topics": ["negligence"], "acts_or_sections": [],
 "lexical_query": "bombay high court negligence", "size": 10}}

Example 3 — Case title with year
Query: "State of Maharashtra Vs John Doe 2020"
{{"court_name": "", "petitioner_names": ["State of Maharashtra"],
 "respondent_names": ["John Doe"], "year": 2020, "topics": [],
 "acts_or_sections": [],
 "lexical_query": "state of maharashtra versus john doe 2020", "size": 1}}

User query: {query}

Output only valid JSON."""


def _extract_case_metadata(query: str) -> CaseMetadata:
    """Use Gemini Flash Lite to extract structured metadata from a judgment query."""
    with log_time(log, "Case metadata extraction"):
        llm = get_gemini_flash(temperature=0.1).with_structured_output(
            CaseMetadata, include_raw=True,
        )
        prompt = ChatPromptTemplate.from_template(METADATA_EXTRACTION_PROMPT)
        chain = prompt | llm
        raw_and_parsed = chain.invoke({"query": query})
    from core.token_tracker import record as _record_tokens
    _record_tokens("Judgment", "extract_metadata", raw_and_parsed.get("raw"))
    result = raw_and_parsed["parsed"]

    log.info("Metadata extracted",
             court=result.court_name,
             petitioners=result.petitioner_names,
             respondents=result.respondent_names,
             year=result.year,
             topics=result.topics,
             size=result.size)
    return result


_LEGAL_TOPICS = [
    "bail", "anticipatory bail", "murder", "rape", "cheating", "fraud",
    "negligence", "contract", "property", "divorce", "custody", "contempt",
    "quashing", "injunction", "defamation", "accident", "compensation",
    "writ", "habeas corpus", "mandamus", "eviction", "arbitration",
]


def _judgment_regex_fallback(query: str) -> CaseMetadata:
    """Regex-based fallback when GPT-4o metadata extraction fails or times out.

    Extracts party names, year, court, and legal topics without any LLM call.
    Deliberately conservative — returns broad search params to avoid missing results.
    """
    query_lower = query.lower()

    # Party names: "X vs Y" or "X versus Y"
    petitioners: list[str] = []
    respondents: list[str] = []
    vs_match = re.search(
        r'(.+?)\s+(?:vs?\.?|versus)\s+(.+?)(?:\s+\d{4}|\s*$)',
        query, re.IGNORECASE,
    )
    if vs_match:
        petitioners = [vs_match.group(1).strip()[:80]]
        respondents = [vs_match.group(2).strip()[:80]]

    # Year: 4-digit number 1900-2099
    year: int | None = None
    yr_match = re.search(r'\b(19|20)\d{2}\b', query)
    if yr_match:
        year = int(yr_match.group(0))

    # Court name
    court_name: str | None = None
    if "supreme court" in query_lower or re.search(r'\bsc\b', query_lower):
        court_name = "supreme court"
    elif "high court" in query_lower:
        hc = re.search(r'(\w+)\s+high court', query_lower)
        court_name = f"{hc.group(1)} high court" if hc else "high court"

    # Legal topics
    topics = [t for t in _LEGAL_TOPICS if t in query_lower]

    # Lexical query: combine key tokens
    lexical_parts = []
    if petitioners:
        lexical_parts.append(petitioners[0])
    if respondents:
        lexical_parts.append("vs")
        lexical_parts.append(respondents[0])
    if year:
        lexical_parts.append(str(year))
    lexical = " ".join(lexical_parts) if lexical_parts else query

    # size: narrow if both parties known, else broader
    size = 1 if (petitioners and respondents) else (5 if topics else 10)

    log.info("Regex fallback metadata",
             petitioners=petitioners, respondents=respondents,
             year=year, court=court_name, topics=topics, size=size)

    return CaseMetadata(
        petitioner_names=petitioners,
        respondent_names=respondents,
        year=year,
        court_name=court_name,
        topics=topics,
        acts_or_sections=[],
        lexical_query=lexical,
        size=size,
    )


# --- Hit Processing ---

def _process_hits(hits: list[dict]) -> tuple[list[str], list[SourceMetadata]]:
    """Convert smart search hits to doc texts and SourceMetadata list."""
    docs_text_parts = []
    sources = []

    for hit in hits:
        content = hit["content"]
        court = hit.get("court_name", "")
        source_file = hit.get("source", "unknown")
        year = hit.get("year")
        keywords = hit.get("keywords", [])
        acts_sections = hit.get("acts_or_sections_invoked", [])
        score = hit.get("score")

        petitioner = hit.get("petitioner_names", ["Unknown"])
        respondent = hit.get("respondent_names", ["Unknown"])
        # Handle string, list, or other types from ES
        if isinstance(petitioner, str):
            pet_name = petitioner or "Unknown"
        elif isinstance(petitioner, list):
            pet_name = petitioner[0] if petitioner else "Unknown"
        else:
            pet_name = str(petitioner) if petitioner else "Unknown"
        if isinstance(respondent, str):
            resp_name = respondent or "Unknown"
        elif isinstance(respondent, list):
            resp_name = respondent[0] if respondent else "Unknown"
        else:
            resp_name = str(respondent) if respondent else "Unknown"
        title = f"{pet_name} vs {resp_name}"

        docs_text_parts.append(content)

        # Generate S3 link
        file_str = str(source_file) if isinstance(source_file, dict) else source_file
        s3_link = generate_s3_link.invoke({"court": court, "file_name": file_str, "title": title}) if court and file_str else None

        sources.append(SourceMetadata(
            source_type="judgment",
            title=title,
            content=[content[:300]],
            file_name=file_str,
            doc_link=s3_link,
            agent_name="Judgment",
            relevance_score=score,
            court_name=court,
            year=year,
            petitioner_names=petitioner if isinstance(petitioner, list) else [petitioner],
            respondent_names=respondent if isinstance(respondent, list) else [respondent],
            keywords=keywords if isinstance(keywords, list) else [],
            acts_or_sections_invoked=acts_sections if isinstance(acts_sections, list) else [],
        ))

    return docs_text_parts, sources


# --- Agent Node ---

async def judgment_node(state: LegalAgentState) -> dict:
    """Search judgments and generate response with case citations.

    Flow:
    1. Extract structured metadata from query (GPT-4o)
    2. Run smart multi-strategy ES search (citation → party → case type → topic → fallback)
    3. Generate S3 PDF links for each hit
    4. Generate response with citations (Gemini Flash, streaming)
    """
    agent_queries = state.get("agent_queries", {})
    # Prefer agent-specific query > normalized English query > original (for multilingual support)
    query = agent_queries.get("Judgment", state.get("query", state.get("original_query", "")))
    original_query = state.get("original_query", query)
    user_context = state.get("user_context", "")
    chat_history = state.get("chat_history", [])
    _user_language = state.get("user_language", "en")
    _intent = state.get("user_intent")
    # Localized copy for the ES-grounded generation path. The web fallback
    # below receives the RAW prompt + language/intent so IT appends the
    # directive last — see core/agent_fallback.web_search_fallback.
    _system_prompt = localize_prompt(
        JUDGMENT_SYSTEM_PROMPT,
        _user_language,
        _intent,
    )
    log.info("Agent started", query=query[:100],
             using_agent_query="Judgment" in agent_queries)

    try:
        # Steps 1 + 2: Run metadata extraction and preliminary ES search in parallel.
        progress("judgment", "Extracting case details from query...", step="metadata")
        # Preliminary search uses regex-fallback metadata (fast) while GPT-4o extracts
        # richer metadata. If preliminary hits are found, we skip the refined search.
        def _preliminary_search():
            prelim_meta = _judgment_regex_fallback(query)
            prelim_court, _ = _normalize_court_filter(prelim_meta.court_name)
            return smart_judgment_search(
                query=query,
                petitioner=(prelim_meta.petitioner_names or [""])[0],
                respondent=(prelim_meta.respondent_names or [""])[0],
                year=prelim_meta.year,
                court=prelim_court,
                topics=prelim_meta.topics,
                acts_or_sections=prelim_meta.acts_or_sections,
                lexical_query=prelim_meta.lexical_query or "",
                size=min(prelim_meta.size or 10, 50),
            )

        progress("judgment", "Searching court judgments (2 strategies in parallel)...",
                 step="search")
        with log_time(log, "Parallel metadata + ES search"):
            metadata_task = asyncio.create_task(
                asyncio.to_thread(_extract_case_metadata, query)
            )
            es_task = asyncio.create_task(asyncio.to_thread(_preliminary_search))

            done, _ = await asyncio.wait(
                [metadata_task, es_task],
                timeout=TIMEOUT_ES_PARALLEL_SEC,
                return_when=asyncio.ALL_COMPLETED,
            )

        # Resolve metadata (prefer GPT-4o result, fall back to regex)
        try:
            metadata = metadata_task.result() if metadata_task in done else _judgment_regex_fallback(query)
        except Exception as meta_err:
            log.warning("Metadata extraction failed, using regex fallback", error=str(meta_err))
            metadata = _judgment_regex_fallback(query)

        # Resolve preliminary ES result
        try:
            prelim_result = es_task.result() if es_task in done else {"hits": [], "strategy_used": "none", "strategies_tried": []}
        except Exception as es_err:
            log.warning("Preliminary ES search failed; will retry via refined search",
                        error=short_err(es_err))
            prelim_result = {"hits": [], "strategy_used": "none", "strategies_tried": []}

        # Normalise the extracted court against the ES index allowlist.
        # If the extractor named a specific court NOT in our index (e.g.
        # Karnataka / Allahabad / Punjab & Haryana / Andhra / Telangana /
        # etc.), skip ES entirely and route to the web fallback — no
        # amount of ES retrying will surface docs that aren't there.
        # Preliminary hits (if any) are discarded in this branch because
        # the extractor's LLM view of the court intent is more reliable
        # than the regex fallback that ran during preliminary.
        court_filter, court_not_indexed = _normalize_court_filter(metadata.court_name)
        if court_not_indexed:
            log.info("Requested court not in ES judgements index — skipping "
                     "ES searches and routing to web fallback",
                     requested_court=metadata.court_name,
                     preliminary_hits=len(prelim_result["hits"]))
            progress("judgment",
                     f"'{metadata.court_name}' isn't in our judgment index "
                     "— searching the web...",
                     step="fallback")
            from core.agent_fallback import web_search_fallback
            fallback_result = await web_search_fallback(
                query, "Judgment", JUDGMENT_SYSTEM_PROMPT,
                original_query=original_query,
                user_language=_user_language, intent=_intent)
            fallback_result.fallback_used = True
            return {"agent_results": {"Judgment": fallback_result}}

        # If preliminary search found hits, use them directly
        if prelim_result["hits"]:
            search_result = prelim_result
            progress("judgment",
                     f"Found {len(prelim_result['hits'])} judgments via {search_result['strategy_used']}",
                     found=len(prelim_result["hits"]), substep=True, step="search")
            log.info("Preliminary ES search hit — skipping refined search",
                     strategy=search_result["strategy_used"])
        else:
            # Preliminary missed — do refined search with full GPT-4o metadata
            progress("judgment", "Refining search with extracted metadata...",
                     substep=True, step="search")
            with log_time(log, "Refined ES search with metadata"):
                try:
                    search_result = await asyncio.to_thread(
                        smart_judgment_search,
                        query=query,
                        petitioner=(metadata.petitioner_names or [""])[0],
                        respondent=(metadata.respondent_names or [""])[0],
                        year=metadata.year,
                        court=court_filter,
                        topics=metadata.topics,
                        acts_or_sections=metadata.acts_or_sections,
                        lexical_query=metadata.lexical_query or "",
                        size=min(metadata.size or 10, 50),
                    )
                except Exception as es_err:
                    err_msg = str(es_err).lower()
                    # Query-shape failures ES can throw on long / complex
                    # queries: `too_many_clauses`, `maxClauseCount`,
                    # `compile error`, `class_cast_exception`. Any of these
                    # mean "this query is not answerable by ES as built" —
                    # let the empty-hits path below trigger rewrite + web
                    # fallback instead of failing the whole agent with an
                    # opaque 400.
                    if any(k in err_msg for k in (
                        "maxclausecount", "too_many_clauses",
                        "compile error", "class_cast",
                    )):
                        log.warning("Judgment ES retrieval failed on query shape — "
                                    "falling through to rewrite/web fallback",
                                    error=str(es_err)[:200])
                        search_result = {
                            "hits": [],
                            "strategy_used": "none",
                            "strategies_tried": [],
                        }
                    else:
                        raise

        hits = search_result["hits"]
        strategy = search_result["strategy_used"]
        strategies_tried = search_result["strategies_tried"]

        if not hits:
            # Phase 1: Query rewrite + retry
            progress("judgment", "No results — rewriting query...", step="fallback")
            from core.agent_fallback import rewrite_query_for_domain, web_search_fallback
            rewritten = await asyncio.to_thread(rewrite_query_for_domain, query, "Judgment")
            if rewritten != query:
                log.info("Retrying with rewritten query", rewritten=rewritten[:100])
                try:
                    retry_result = await asyncio.to_thread(
                        smart_judgment_search,
                        query=rewritten,
                        petitioner=(metadata.petitioner_names or [""])[0],
                        respondent=(metadata.respondent_names or [""])[0],
                        year=metadata.year,
                        court=court_filter,
                        topics=metadata.topics,
                        acts_or_sections=metadata.acts_or_sections,
                        lexical_query=rewritten,
                        size=min(metadata.size or 10, 50),
                    )
                    hits = retry_result["hits"]
                    if hits:
                        strategy = retry_result["strategy_used"]
                        log.info("Retry search succeeded", hit_count=len(hits))
                except Exception as retry_err:
                    log.warning("Retry search failed", error=str(retry_err))

            # Phase 2: Web search fallback if still empty
            if not hits:
                progress("judgment", "Searching the web for case law...",
                         step="fallback")
                log.warning("All searches exhausted, using web fallback",
                            strategies_tried=strategies_tried)
                fallback_result = await web_search_fallback(
                    query, "Judgment", JUDGMENT_SYSTEM_PROMPT,
                    original_query=original_query,
                    user_language=_user_language, intent=_intent)
                fallback_result.retry_attempted = True
                return {"agent_results": {"Judgment": fallback_result}}

        log.info("ES results found",
                 hit_count=len(hits),
                 top_score=hits[0].get("score", 0),
                 strategy=strategy,
                 strategies_tried=len(strategies_tried))

        # Step 3: Process hits into docs + source metadata
        docs_text_parts, sources = _process_hits(hits)
        first_title = sources[0].title if sources else ""

        if first_title:
            progress("judgment", f"Top match: {first_title[:70]}",
                     found=len(hits), substep=True, step="search")

        # Step 3.5: Relevance gate — reject keyword-shared-but-subject-different
        # judgments (e.g. same parties named but unrelated dispute, or citation
        # match against a case that decides a different question).
        #
        # SKIPPED for "exact section lookup" queries (the user named a specific
        # section AND >=50% of hits cite that section in their
        # acts_or_sections_invoked metadata). Without this skip, the gate
        # rejects perfectly relevant Sec 302 IPC murder appeals that also
        # discuss Sec 313 CrPC procedurally, and forces a web-search fallback
        # whose sources are vertexaisearch.cloud.google.com redirector URLs
        # instead of the user's S3-hosted PDFs.
        if _is_exact_section_lookup(metadata, sources):
            log.info("Relevance gate skipped -- exact section lookup",
                     sections=metadata.acts_or_sections,
                     hits=len(sources),
                     strategy=strategy)
            is_relevant, judge_telemetry = True, {"reason": "skipped_exact_section"}
        else:
            progress("judgment", "Verifying retrieval relevance...",
                     step="relevance_check")
            is_relevant, judge_telemetry = await check_retrieval_relevance(
                query, docs_text_parts,
                source_name=first_title or hits[0].get("source"),
                agent_name="Judgment",
            )
            log.info("Relevance judge verdict",
                     passed=is_relevant, strategy=strategy,
                     top_match=first_title[:60], **judge_telemetry)

        if not is_relevant:
            log.warning("Retrieved judgments failed relevance gate — "
                        "falling back to web search",
                        top_match=first_title[:60], **judge_telemetry)
            progress("judgment",
                     "Retrieved cases don't match the query "
                     "— searching the web...",
                     step="fallback", substep=True)
            from core.agent_fallback import web_search_fallback
            fallback_result = await web_search_fallback(
                query, "Judgment", JUDGMENT_SYSTEM_PROMPT,
                original_query=original_query,
                user_language=_user_language, intent=_intent)
            fallback_result.retry_attempted = True
            return {"agent_results": {"Judgment": fallback_result}}

        progress("judgment", "Generating response with citations...", step="generate")

        # Step 4: Generate response
        docs_text = "\n\n".join(docs_text_parts)

        with log_time(log, "LLM generation"):
            # max_output_tokens capped at 8192 (~32k chars) to bound responses
            # and prevent runaway markdown-table padding loops.
            llm = get_gemini_flash_full(temperature=0.1, max_output_tokens=8192)
            prompt = ChatPromptTemplate.from_messages([
                ("system", _system_prompt),
                MessagesPlaceholder(variable_name="chat_history", optional=True),
                ("user", "Court Judgments:\n{docs}"),
                ("user", "Current Date: {date}"),
                ("user", "User Query: {query}"),
            ])
            chain = prompt | llm

            gen_query = f"User's document/context:\n{user_context}\n\nUser's question:\n{query}" if user_context else query
            from core.streaming import stream_chain_response
            llm_response = await stream_chain_response(chain, {
                "query": gen_query,
                "docs": docs_text,
                "chat_history": chat_history,
                "date": str(date.today()),
            })

        from core.token_tracker import record as _record_tokens
        tokens = _record_tokens("Judgment", "generate", llm_response)

        # Check if LLM apologized (ES hits were irrelevant) — fall back to web search.
        # Uses the shared head+tail scanner so end-of-response hedges don't slip through.
        if head_tail_apology_detected(llm_response.content):
            log.warning("LLM response is an apology or too short, using web fallback",
                        response_preview=llm_response.content[:100], strategy=strategy)
            try:
                from langgraph.config import get_stream_writer
                writer = get_stream_writer()
                writer({"type": "token_reset"})
            except RuntimeError:
                pass
            from core.agent_fallback import web_search_fallback
            fallback_result = await web_search_fallback(
                query, "Judgment", JUDGMENT_SYSTEM_PROMPT,
                user_language=_user_language, intent=_intent)
            fallback_result.tokens_consumed += tokens
            return {"agent_results": {"Judgment": fallback_result}}

        log.info("Agent completed",
                 cases=len(hits), response_len=len(llm_response.content),
                 tokens=tokens, strategy=strategy,
                 first_case=first_title[:60])

        result = AgentResult(
            agent_name="Judgment",
            content=llm_response.content,
            sources=sources,
            tokens_consumed=tokens,
        )

    except Exception as e:
        from core.metrics import record_agent_error
        record_agent_error("Judgment", e)
        log.error("Agent failed", error=short_err(e), exc_info=True)
        result = AgentResult(
            agent_name="Judgment",
            content="",
            sources=[],
            tokens_consumed=0,
            error=short_err(e),
        )

    return {"agent_results": {"Judgment": result}}
