"""Agent #6 — Newacts Agent

Handles queries about the 6 core Indian codes:
BNS, BNSS, BSA (new) ↔ IPC, CrPC, IEA (old)

Supports section lookup, old↔new law mapping, and hybrid vector search.

Uses: GPT-4o (metadata extraction with regex fallback), Gemini Flash Lite (generation)
Data Source: Elasticsearch "newacts_v1" index

Search strategy:
1. Extract metadata via GPT-4o (with regex fallback if GPT-4o fails)
2. Build ES query (exact filter or hybrid BM25+vector)
3. If hybrid search fails (embedding errors), fall back to BM25-only
4. If result set is small, enrich with nearby sections
5. If no section/act identified, use topic-based BM25 search
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import date
from typing import Optional, List

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import (
    get_es_client, get_gemini_flash, get_gemini_flash_full, get_retriever_embeddings,
)
from core.retrieval_relevance import (
    check_retrieval_relevance, head_tail_apology_detected,
)
from core.settings import ES_INDICES, TIMEOUT_METADATA_SEC
from core.language import localize_prompt
from core.logger import get_logger, log_time, short_err
from core.progress import progress
from config.prompts import NEWACTS_SYSTEM_PROMPT

from tools.inline.section_parser import parse_multi_section_info
from tools.shared.elasticsearch_tools import (
    ACTS_PATHS,
    _OLD_NEW_MAPPING,
    _search_newacts_by_topic,
    _get_nearby_sections,
)

log = get_logger("Newacts")


# --- Abbreviation Mapping (for regex fallback) ---

_ABBREVIATION_MAP = {
    "bns": "The Bharatiya Nyaya Sanhita, 2023",
    "bnss": "The Bharatiya Nagarik Suraksha Sanhita, 2023",
    "bsa": "The Bharatiya Sakshya Adhiniyam, 2023",
    "crpc": "The Code of Criminal Procedure 1973",
    "ipc": "The Indian Penal Code, 1860",
    "iea": "Indian Evidence Act 1872",
    "bharatiya nyaya sanhita": "The Bharatiya Nyaya Sanhita, 2023",
    "bharatiya nagarik suraksha sanhita": "The Bharatiya Nagarik Suraksha Sanhita, 2023",
    "bharatiya sakshya adhiniyam": "The Bharatiya Sakshya Adhiniyam, 2023",
    "code of criminal procedure": "The Code of Criminal Procedure 1973",
    "indian penal code": "The Indian Penal Code, 1860",
    "indian evidence act": "Indian Evidence Act 1872",
    # Informal / partial name variants
    "new penal code": "The Bharatiya Nyaya Sanhita, 2023",
    "new criminal law": "The Bharatiya Nyaya Sanhita, 2023",
    "new criminal procedure": "The Bharatiya Nagarik Suraksha Sanhita, 2023",
    "new criminal procedure code": "The Bharatiya Nagarik Suraksha Sanhita, 2023",
    "new code of criminal procedure": "The Bharatiya Nagarik Suraksha Sanhita, 2023",
    "new evidence act": "The Bharatiya Sakshya Adhiniyam, 2023",
    "new evidence law": "The Bharatiya Sakshya Adhiniyam, 2023",
    "criminal procedure code": "The Code of Criminal Procedure 1973",
    "penal code": "The Indian Penal Code, 1860",
    "evidence act": "Indian Evidence Act 1872",
}


# --- Mapping Query Detection ---

_MAPPING_KEYWORDS = {
    "equivalent", "corresponding", "new law", "replaced", "new section",
    "old section", "new provision", "what replaced", "comparison", "compare",
    "mapped to", "replaces", "counterpart", "analogous", "new code",
}

# Full act name → abbreviation key used in _OLD_NEW_MAPPING
_OLD_ACTS_ABBREV = {
    "The Indian Penal Code, 1860": "ipc",
    "The Code of Criminal Procedure 1973": "crpc",
    "Indian Evidence Act 1872": "iea",
}

# New act abbreviation → (new full name, old abbreviation key)
_NEW_ACTS_ABBREV = {
    "The Bharatiya Nyaya Sanhita, 2023": ("BNS", "ipc"),
    "The Bharatiya Nagarik Suraksha Sanhita, 2023": ("BNSS", "crpc"),
    "The Bharatiya Sakshya Adhiniyam, 2023": ("BSA", "iea"),
}

# Build reverse mapping: new act abbrev → {new_section: {old_act, old_section, description}}
_NEW_OLD_MAPPING: dict[str, dict[str, dict]] = {}
for _old_abbrev, _sections in _OLD_NEW_MAPPING.items():
    for _old_sec, _info in _sections.items():
        _new_abbrev = _info["new_act"].lower()
        if _new_abbrev not in _NEW_OLD_MAPPING:
            _NEW_OLD_MAPPING[_new_abbrev] = {}
        _NEW_OLD_MAPPING[_new_abbrev][_info["new_section"].lower()] = {
            "old_act": _old_abbrev.upper(),
            "old_section": _old_sec,
            "description": _info["description"],
        }


# --- Metadata Extraction ---

class ActQueryMetadata(BaseModel):
    section_number: Optional[List[str]] = Field(
        None, description="List of section numbers, normalized (remove parentheses contents)"
    )
    act_name: Optional[str] = Field(
        None, description="Exact act name from allowed mapping"
    )
    hybrid_search: Optional[bool] = Field(
        False, description="Whether to use semantic + keyword hybrid search"
    )


METADATA_PROMPT = """You are a legal AI assistant specialized in analyzing user queries to extract act metadata.

## Task
Extract:
1. **section_number** — list of section numbers (remove parentheses contents, e.g., 20(1) → "20")
2. **act_name** — exact name from allowed mapping or null
3. **hybrid_search** — true if query is vague/conceptual, false if exact section + act identified

## Allowed mapping (case-insensitive + fuzzy)
- BNS  → The Bharatiya Nyaya Sanhita, 2023
- BNSS → The Bharatiya Nagarik Suraksha Sanhita, 2023
- BSA  → The Bharatiya Sakshya Adhiniyam, 2023
- CrPC → The Code of Criminal Procedure 1973
- IPC  → The Indian Penal Code, 1860
- IEA  → Indian Evidence Act 1872

## Rules for hybrid_search
1. true if query is vague/conceptual
2. false if query has exact section number(s) AND act name
3. false if query has only act name/acronym
4. else → true

User query: {query}

Return only valid JSON:
{{"section_number": ["<string>", ...] or null, "act_name": "<string or null>", "hybrid_search": true/false}}"""


def _extract_act_metadata(query: str) -> ActQueryMetadata:
    """Use Gemini Flash Lite to extract act metadata from a newacts query."""
    with log_time(log, "Act metadata extraction"):
        llm = get_gemini_flash(temperature=0.1).with_structured_output(
            ActQueryMetadata, include_raw=True,
        )
        prompt = ChatPromptTemplate.from_template(METADATA_PROMPT)
        chain = prompt | llm
        raw_and_parsed = chain.invoke({"query": query})
    from core.token_tracker import record as _record_tokens
    _record_tokens("Newacts", "extract_metadata", raw_and_parsed.get("raw"))
    result = raw_and_parsed["parsed"]

    log.info("Metadata extracted",
             act=result.act_name, sections=result.section_number,
             hybrid=result.hybrid_search)
    return result


def _regex_fallback_metadata(query: str) -> ActQueryMetadata:
    """Regex-based fallback when GPT-4o metadata extraction fails.

    Uses parse_multi_section_info() + abbreviation mapping to construct
    ActQueryMetadata without any LLM call.
    """
    parsed = parse_multi_section_info(query)
    query_lower = query.lower()

    # Try to find act name from abbreviations in query
    act_name = None
    for abbrev, full_name in _ABBREVIATION_MAP.items():
        # Match whole word or at word boundary
        if re.search(rf'\b{re.escape(abbrev)}\b', query_lower):
            act_name = full_name
            break

    section_numbers = None
    hybrid_search = True  # default to hybrid if we can't parse well

    if parsed and parsed["section_numbers"]:
        section_numbers = parsed["section_numbers"]
        if act_name and section_numbers:
            hybrid_search = False  # exact section + act → no hybrid needed

    log.info("Regex fallback metadata",
             act=act_name, sections=section_numbers, hybrid=hybrid_search)

    return ActQueryMetadata(
        section_number=section_numbers,
        act_name=act_name,
        hybrid_search=hybrid_search,
    )


# --- ES Query Builder ---

def _build_newacts_query(metadata: ActQueryMetadata, query_text: str) -> dict:
    """Build ES query for newacts — either exact filter or hybrid BM25+vector."""
    filters = []

    # Cross-act handling is now done at the agent level
    # (_build_and_search_per_act) which calls this function ONCE PER
    # mentioned act with metadata.act_name overridden to that act.
    # So _build_newacts_query always honours metadata.act_name as
    # the authoritative filter — the caller decides whether to run
    # per-act searches or a single one.
    q_lower = (query_text or "").lower()

    if metadata.act_name and metadata.act_name in ACTS_PATHS:
        filters.append({"term": {"source.keyword": ACTS_PATHS[metadata.act_name]}})

    if metadata.section_number:
        filters.append({"terms": {"section_number.keyword": metadata.section_number}})

    # Hybrid search: BM25 + cosine similarity on embedding field
    if metadata.hybrid_search and query_text:
        log.debug("Building hybrid BM25+vector query",
                  act_name=metadata.act_name)
        embeddings = get_retriever_embeddings()
        query_vector = embeddings.embed_query(query_text)

        should_clauses = [
            # Base BM25 on the raw query (existing behaviour)
            {"match": {"page_content": query_text}},
            # Phrase boost: doctrine-relevant chunks contain the user's
            # multi-word topic (e.g. "right of private defence") verbatim
            # or nearly so. Boost = 6 lifts them above lone-token matches.
            {"match_phrase": {
                "page_content": {"query": query_text, "slop": 3, "boost": 6.0}
            }},
        ]
        # British-vs-American spelling alternative for the most common
        # Indian-legal doctrine words (defense ↔ defence, defenses ↔
        # defences). The corpus is British-spelled; users frequently
        # type American spelling.
        if "defense" in q_lower:
            should_clauses.append({"match_phrase": {
                "page_content": {
                    "query": query_text.replace("defense", "defence")
                                       .replace("Defense", "Defence"),
                    "slop": 3, "boost": 6.0,
                }
            }})
        elif "defence" in q_lower:
            should_clauses.append({"match_phrase": {
                "page_content": {
                    "query": query_text.replace("defence", "defense")
                                       .replace("Defence", "Defense"),
                    "slop": 3, "boost": 4.0,
                }
            }})
        if metadata.act_name and metadata.act_name in ACTS_PATHS:
            should_clauses.append(
                {"term": {"source.keyword": ACTS_PATHS[metadata.act_name]}}
            )

        # Negative query (boilerplate demotion): every act's preamble/
        # short-title sections contain act-name tokens ("BNS", "IPC")
        # and dominate BM25 for cross-act topic queries. negative_boost
        # 0.3 multiplies their score by ~0.3 without filtering them out.
        negative_query = {
            "bool": {
                "should": [
                    {"match_phrase": {"page_content": "short title"}},
                    {"match_phrase": {"page_content": "commencement"}},
                    {"match_phrase": {"page_content": "extent and commencement"}},
                    {"match_phrase": {"page_content": "this Act may be called"}},
                    {"match_phrase": {"page_content": "shall come into force"}},
                ],
                "minimum_should_match": 1,
            }
        }

        return {
            "size": 20,
            "query": {
                "script_score": {
                    "query": {
                        "boosting": {
                            "positive": {
                                "bool": {
                                    "should": should_clauses,
                                    "filter": filters,
                                }
                            },
                            "negative": negative_query,
                            "negative_boost": 0.3,
                        }
                    },
                    "script": {
                        "source": """
                            double bm25 = _score;
                            double vector_score = cosineSimilarity(params.query_vector, 'embedding');
                            return bm25 + (100 * vector_score);
                        """,
                        "params": {"query_vector": query_vector},
                    },
                }
            },
            "sort": [
                {"_score": {"order": "desc"}},
                {"section_number.keyword": {"order": "asc"}},
            ],
        }

    # Non-hybrid: exact filter search
    # Size adapts to the number of sections requested -- a multi-section query
    # ("compare BNS 115, 118, 189, 190, 191, 351, 352") would silently lose
    # hits if we capped at 10. Floor of 10 preserves prior behaviour for
    # single-section queries; cap at 50 so a stray very-large list cannot
    # blow up the response.
    size = max(10, min(50, len(metadata.section_number) * 2)) if metadata.section_number else 10
    log.debug("Building exact filter query",
              filters_count=len(filters), size=size,
              sections=len(metadata.section_number or []))
    return {
        "query": {
            "bool": {
                "must": [],
                "filter": filters,
            }
        },
        "size": size,
        "sort": [{"section_number.keyword": {"order": "asc"}}],
    }


def _is_exact_filter_query(metadata: ActQueryMetadata) -> bool:
    """True when the ES query was an exact filter (act + section, no BM25).

    The relevance gate exists to catch false positives from keyword/vector
    retrieval (e.g. "BNS Section 318" returning another act's Section 318
    because of keyword overlap). When the query is an exact filter on
    `source.keyword == act_path` AND `section_number.keyword IN [...]`,
    every hit is by construction a section of the requested act -- the gate
    cannot improve precision, but it CAN reject perfectly good hits when
    the judge only sees the top-N chunks (Sec 125 CrPC / BNS 115-352
    incidents). Skipping the gate in this mode prevents that false negative.
    """
    return (
        not metadata.hybrid_search
        and bool(metadata.section_number)
        and bool(metadata.act_name)
    )


# --- Agent Node ---

async def newacts_node(state: LegalAgentState) -> dict:
    """Retrieve new/old act provisions and generate response.

    Flow:
    1. Extract act metadata from query (GPT-4o, with regex fallback)
    2. Map act name to ES source path
    3. Build ES query (exact filter or hybrid)
    4. Search ES "newacts_v1" index (with BM25 fallback on hybrid failure)
    5. If few results, enrich with nearby sections
    6. If no section/act, fall back to topic-based search
    7. Generate response with provisions
    """
    _AGENT_BUDGET_SEC = 150.0  # leave 30s margin before 180s gateway timeout
    _t0 = time.monotonic()

    def _budget_remaining() -> float:
        return _AGENT_BUDGET_SEC - (time.monotonic() - _t0)

    agent_queries = state.get("agent_queries", {})
    # Prefer agent-specific query > normalized English query > original (for multilingual support)
    query = agent_queries.get("Newacts", state.get("query", state.get("original_query", "")))
    user_context = state.get("user_context", "")
    _system_prompt = localize_prompt(
        NEWACTS_SYSTEM_PROMPT,
        state.get("user_language", "en"),
        state.get("user_intent"),
    )
    chat_history = state.get("chat_history", [])
    log.info("Agent started", query=query[:100],
             using_agent_query="Newacts" in agent_queries)

    try:
        # Step 1: Extract metadata (GPT-4o in a thread, with timeout + regex fallback)
        progress("newacts", "Extracting act and section details...", step="parse")
        try:
            metadata = await asyncio.wait_for(
                asyncio.to_thread(_extract_act_metadata, query),
                timeout=TIMEOUT_METADATA_SEC,
            )
        except asyncio.TimeoutError:
            log.warning("GPT-4o metadata extraction timed out, using regex fallback")
            metadata = _regex_fallback_metadata(query)
        except Exception as meta_err:
            log.warning("GPT-4o metadata extraction failed, using regex fallback",
                        error=str(meta_err))
            metadata = _regex_fallback_metadata(query)

        # Step 1b: Detect old↔new mapping queries
        has_section = bool(metadata.section_number)
        has_act = bool(metadata.act_name and metadata.act_name in ACTS_PATHS)
        if has_section and metadata.section_number:
            sections_str = ", ".join(metadata.section_number[:5])
            act_str = metadata.act_name or "unknown act"
            progress("newacts", f"Detected: Section {sections_str}", detail=act_str, substep=True, step="parse")
        query_lower = query.lower()
        is_mapping_query = any(kw in query_lower for kw in _MAPPING_KEYWORDS)

        mapping_extra_hits = []  # extra hits from the counterpart act

        if is_mapping_query and has_section and metadata.section_number:
            progress("newacts", "Checking old↔new law mapping...", step="mapping")
            log.info("Mapping query detected", act=metadata.act_name,
                     sections=metadata.section_number)

            # Scan ALL acts mentioned in query (not just metadata.act_name)
            mentioned_old: list[tuple[str, str]] = []  # (full_name, abbrev)
            mentioned_new: list[tuple[str, str, str]] = []  # (full_name, short, old_key)
            for abbrev, full_name in _ABBREVIATION_MAP.items():
                if re.search(rf'\b{re.escape(abbrev)}\b', query_lower):
                    if full_name in _OLD_ACTS_ABBREV:
                        mentioned_old.append((full_name, _OLD_ACTS_ABBREV[full_name]))
                    elif full_name in _NEW_ACTS_ABBREV:
                        short, old_key = _NEW_ACTS_ABBREV[full_name]
                        mentioned_new.append((full_name, short, old_key))

            log.info("Acts mentioned in mapping query",
                     old_acts=[a for _, a in mentioned_old],
                     new_acts=[s for _, s, _ in mentioned_new])

            # Collect all mapping fetch tasks to run in parallel
            _mapping_tasks: list[asyncio.Task] = []

            async def _fetch_nearby(section: str, act_full: str) -> list[dict]:
                """Fetch nearby sections and return as hit dicts."""
                nearby = await asyncio.to_thread(
                    _get_nearby_sections, section, act_full, 0,
                )
                return [
                    {
                        "_source": {
                            "page_content": nh["content"],
                            "source": nh["source"],
                            "section_number": nh.get("section_number"),
                        },
                        "_score": nh.get("score"),
                    }
                    for nh in nearby["hits"]
                ]

            # Strategy: find mappings from old→new
            for old_full, old_abbrev in mentioned_old:
                mapping_table = _OLD_NEW_MAPPING.get(old_abbrev, {})
                for sec in metadata.section_number:
                    mapped = mapping_table.get(sec.lower())
                    if mapped:
                        new_act_full = None
                        for full_name, (abbrev, _) in _NEW_ACTS_ABBREV.items():
                            if abbrev == mapped["new_act"]:
                                new_act_full = full_name
                                break
                        if new_act_full:
                            log.info("Mapping found",
                                     old=f"{old_abbrev.upper()} {sec}",
                                     new=f"{mapped['new_act']} {mapped['new_section']}",
                                     desc=mapped["description"])
                            _mapping_tasks.append(
                                _fetch_nearby(mapped["new_section"], new_act_full)
                            )
                            # Also fetch old section if metadata points elsewhere
                            if metadata.act_name != old_full:
                                log.info("Overriding metadata act for old section fetch",
                                         metadata_act=metadata.act_name, old_act=old_full)
                                metadata = ActQueryMetadata(
                                    section_number=metadata.section_number,
                                    act_name=old_full,
                                    hybrid_search=False,
                                )
                                has_act = True

            # Also try new→old direction
            for new_full, new_short, old_key in mentioned_new:
                reverse_table = _NEW_OLD_MAPPING.get(new_short.lower(), {})
                for sec in metadata.section_number:
                    mapped = reverse_table.get(sec.lower())
                    if mapped:
                        old_act_full = None
                        for full_name, abbrev in _OLD_ACTS_ABBREV.items():
                            if abbrev == mapped["old_act"].lower():
                                old_act_full = full_name
                                break
                        if old_act_full:
                            log.info("Reverse mapping found",
                                     new=f"{new_short} {sec}",
                                     old=f"{mapped['old_act']} {mapped['old_section']}",
                                     desc=mapped["description"])
                            _mapping_tasks.append(
                                _fetch_nearby(mapped["old_section"], old_act_full)
                            )

            # Run all mapping fetches in parallel
            if _mapping_tasks:
                with log_time(log, "Parallel mapping fetch", count=len(_mapping_tasks)):
                    results = await asyncio.gather(*_mapping_tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception):
                        log.warning("Mapping fetch failed", error=str(r))
                    else:
                        mapping_extra_hits.extend(r)

        # Step 2: Decide search path
        progress("newacts", "Searching new acts database...", step="search")

        # Large-range path: > 20 sections requested → switch to chapter overview
        # strategy instead of per-section ES retrieval.
        # Rationale:
        #   - A terms-filter with 100 values is slow and ES caps results at
        #     query size (10-20), so most sections would be silently missing.
        #   - The user asking for "Section 1 to 100" wants a chapter overview,
        #     not individual section text.
        #   - Avoids DoS via unbounded range expansion (C4).
        _SECTION_RANGE_CAP = 20
        if has_section and metadata.section_number and len(metadata.section_number) > _SECTION_RANGE_CAP:
            log.info(
                "Large section range detected — switching to topic/overview search",
                requested=len(metadata.section_number),
                cap=_SECTION_RANGE_CAP,
                act=metadata.act_name,
            )
            # Truncate the section list so the LLM prompt stays reasonable
            metadata.section_number = metadata.section_number[:_SECTION_RANGE_CAP]
            # Use BM25 topic search across the act — returns representative sections
            with log_time(log, "Topic BM25 search (large range)"):
                topic_result = await asyncio.to_thread(
                    _search_newacts_by_topic,
                    query, metadata.act_name if has_act else None, 20,
                )
            hits = [
                {
                    "_source": {
                        "page_content": h["content"],
                        "source": h["source"],
                        "section_number": h.get("section_number"),
                    },
                    "_score": h.get("score"),
                }
                for h in topic_result["hits"]
            ]

        # Topic-only path: no section and no act identified → BM25 topic search
        elif not has_section and not has_act and metadata.hybrid_search:
            log.info("No section/act identified, using topic search")
            with log_time(log, "Topic BM25 search"):
                topic_result = await asyncio.to_thread(_search_newacts_by_topic, query)

            hits = [
                {
                    "_source": {
                        "page_content": h["content"],
                        "source": h["source"],
                        "section_number": h.get("section_number"),
                    },
                    "_score": h.get("score"),
                }
                for h in topic_result["hits"]
            ]

        else:
            # Step 3: Build and execute query
            # Cross-act queries (e.g. "BNS vs IPC comparison"): run ONE
            # ES search per named act, filtered to that act, then merge.
            # Otherwise BM25 + cross-encoder favours whichever act has
            # more token density in the query, drowning the other act's
            # sections (the LLM then apologises and the response gets
            # bumped to web fallback).
            # Match both abbreviations and full names. The memory node
            # rewrites "BNS"/"IPC" into "Bharatiya Nyaya Sanhita 2023" /
            # "Indian Penal Code 1860" before this agent runs, so the
            # bare \bbns\b / \bipc\b form silently misses on the rewritten
            # query. Search original_query too for safety.
            _ACT_NAME_MAP = [
                ("bns",  "The Bharatiya Nyaya Sanhita, 2023",
                    r"\bbns\b|bharatiya\s+nyaya\s+sanhita"),
                ("bnss", "The Bharatiya Nagarik Suraksha Sanhita, 2023",
                    r"\bbnss\b|bharatiya\s+nagarik\s+suraksha\s+sanhita"),
                ("bsa",  "The Bharatiya Sakshya Adhiniyam, 2023",
                    r"\bbsa\b|bharatiya\s+sakshya\s+adhiniyam"),
                ("ipc",  "The Indian Penal Code, 1860",
                    r"\bipc\b|indian\s+penal\s+code"),
                ("crpc", "The Code of Criminal Procedure 1973",
                    r"\bcrpc\b|\bcr\.?p\.?c\.?\b|code\s+of\s+criminal\s+procedure"),
                ("iea",  "Indian Evidence Act 1872",
                    r"\biea\b|indian\s+evidence\s+act"),
            ]
            _q_lower = (
                (query or "").lower()
                + " "
                + (state.get("original_query") or "").lower()
            )
            _mentioned_acts = [
                full_name
                for _tok, full_name, pat in _ACT_NAME_MAP
                if re.search(pat, _q_lower)
            ]
            is_cross_act = len(_mentioned_acts) >= 2

            def _build_and_search() -> list:
                """Single-search path — used when query isn't cross-act."""
                q = _build_newacts_query(metadata, query)
                es = get_es_client()
                return es.search(index=ES_INDICES["newacts"], body=q)["hits"]["hits"]

            def _build_and_search_per_act() -> list:
                """Cross-act path — run one search per mentioned act,
                filter each to that act explicitly via metadata.act_name
                override, merge results (top _PER_ACT_LIMIT from each).

                Also drops section_number from the per-act metadata: the
                metadata extractor sometimes guesses a section number for
                a cross-act comparison query ("compare BNS and IPC on
                private defense" → extractor returns sections=[38]
                because BNS sec 38 is one private-defense provision).
                Filtering to that single section in EACH act returns 0
                hits when that section is unrelated (IPC 38 isn't about
                private defense). Strip it so per-act searches see the
                full doctrine sections for each act.
                """
                _PER_ACT_LIMIT = 8
                es = get_es_client()
                all_hits: list = []
                for act_full_name in _mentioned_acts:
                    # Build a copy of metadata with this act forced in
                    # AND section_number dropped (see docstring).
                    m_copy = metadata.model_copy(update={
                        "act_name": act_full_name,
                        "section_number": None,
                    })
                    q = _build_newacts_query(m_copy, query)
                    try:
                        res = es.search(index=ES_INDICES["newacts"], body=q)
                        hits_for_act = res["hits"]["hits"][:_PER_ACT_LIMIT]
                        log.info("Cross-act per-act search",
                                 act=act_full_name, hits=len(hits_for_act))
                        all_hits.extend(hits_for_act)
                    except Exception as e:
                        log.warning("Cross-act per-act search failed",
                                    act=act_full_name, error=str(e)[:120])
                return all_hits

            with log_time(log, "ES search"):
                try:
                    if is_cross_act:
                        log.info("Cross-act query detected — running per-act searches",
                                 mentioned=_mentioned_acts)
                        hits = await asyncio.to_thread(_build_and_search_per_act)
                        # If per-act search comes up empty (e.g. the forced
                        # act_name filter was too strict, ES quirk, or
                        # metadata extraction guessed a non-existent section
                        # number that the per-act path inherits), fall back
                        # to the broad single-search. Without this, the
                        # `if not hits:` block below routes us to
                        # web_search_fallback and the user sees web sources
                        # for a query that the corpus DOES contain.
                        if not hits:
                            log.info("Per-act search returned 0 hits — "
                                     "falling back to BM25-only broad search")
                            try:
                                # BM25-only via _search_newacts_by_topic is
                                # much more reliable than the hybrid
                                # script_score path for cross-act fallback
                                # — no embedding field requirement, no
                                # cosineSimilarity script that can fail
                                # silently on certain docs. We saw the
                                # hybrid broad-search return 0 hits twice
                                # in 3 runs even with the act_name filter
                                # stripped. BM25 + phrase boost surfaces
                                # both BNS and IPC doctrine sections
                                # reliably.
                                bm25_broad = await asyncio.to_thread(
                                    _search_newacts_by_topic, query, None,
                                )
                                hits = [
                                    {
                                        "_source": {
                                            "page_content": h["content"],
                                            "source": h["source"],
                                            "section_number": h.get("section_number"),
                                        },
                                        "_score": h.get("score"),
                                    }
                                    for h in bm25_broad["hits"]
                                ]
                                log.info("Cross-act BM25 broad-search fallback",
                                         hits=len(hits))
                            except Exception as broad_err:
                                log.warning("Broad-search fallback failed — "
                                            "letting outer no-hits path handle it",
                                            error=str(broad_err)[:200],
                                            exc_info=True)
                                hits = []
                    else:
                        hits = await asyncio.to_thread(_build_and_search)
                except Exception as es_err:
                    err_msg = str(es_err).lower()
                    if any(k in err_msg for k in (
                        "embedding", "script", "illegal_argument",
                        "compile error", "class_cast",
                    )):
                        log.warning("Hybrid search failed, falling back to BM25-only",
                                    error=str(es_err))
                        bm25_result = await asyncio.to_thread(
                            _search_newacts_by_topic, query, metadata.act_name,
                        )
                        hits = [
                            {
                                "_source": {
                                    "page_content": h["content"],
                                    "source": h["source"],
                                    "section_number": h.get("section_number"),
                                },
                                "_score": h.get("score"),
                            }
                            for h in bm25_result["hits"]
                        ]
                    else:
                        raise

        # Step 4: Enrich with nearby sections if result set is small
        if (
            len(hits) < 3
            and has_section
            and has_act
            and metadata.section_number
        ):
            center_sec = metadata.section_number[0]
            log.info("Enriching with nearby sections",
                     center=center_sec, current_hits=len(hits))
            try:
                nearby = await asyncio.to_thread(
                    _get_nearby_sections, center_sec, metadata.act_name, 1, 5,
                )
                added = 0
                existing_ids = {
                    h.get("_source", {}).get("section_number")
                    for h in hits
                    if h.get("_source", {}).get("section_number")
                }
                for nh in nearby["hits"]:
                    if nh.get("section_number") not in existing_ids:
                        hits.append({
                            "_source": {
                                "page_content": nh["content"],
                                "source": nh["source"],
                                "section_number": nh.get("section_number"),
                            },
                            "_score": nh.get("score"),
                        })
                        added += 1
                log.info("Nearby sections enriched",
                         added=added, total_nearby=len(nearby["hits"]))
            except Exception as nearby_err:
                log.warning("Nearby sections enrichment failed (skipping)",
                            error=str(nearby_err))

        # Step 4b: Append mapping extra hits (counterpart act sections)
        if mapping_extra_hits:
            existing_secs = {
                h["_source"].get("section_number")
                for h in hits
                if h["_source"].get("section_number")
            }
            added = 0
            for mh in mapping_extra_hits:
                if mh["_source"].get("section_number") not in existing_secs:
                    hits.append(mh)
                    added += 1
            if added:
                log.info("Added mapping counterpart sections", count=added)


        if not hits:
            # Phase 1: Regex-only query rewrite + retry (no LLM call — fast)
            from core.agent_fallback import web_search_fallback
            progress("newacts", "No results — retrying with rewritten query...", step="fallback")
            retry_meta = _regex_fallback_metadata(query)
            if retry_meta.act_name != metadata.act_name or retry_meta.section_number != metadata.section_number:
                log.info("Retrying with regex-rewritten metadata",
                         act=retry_meta.act_name, sections=retry_meta.section_number)
                try:
                    retry_es_query = _build_newacts_query(retry_meta, query)
                    es = get_es_client()
                    # Off-thread: the opensearch-py client is synchronous, so
                    # calling it directly here would block the event loop for
                    # the whole round-trip — stalling every other in-flight
                    # request, not just this one. Every other ES call in this
                    # module already goes through to_thread (either directly,
                    # or via the sync `_build_and_search*` helpers above); this
                    # retry path was the one that got missed.
                    retry_resp = await asyncio.to_thread(
                        es.search,
                        index=ES_INDICES["newacts"],
                        body=retry_es_query,
                    )
                    hits = retry_resp["hits"]["hits"]
                    if hits:
                        log.info("Retry search succeeded", hit_count=len(hits))
                except Exception as retry_err:
                    log.warning("Retry search failed", error=str(retry_err))

            # Phase 2: Web search fallback if still empty (with budget check)
            if not hits:
                remaining = _budget_remaining()
                if remaining < 15:
                    log.warning("Skipping web fallback — budget exhausted",
                                remaining=f"{remaining:.1f}s")
                else:
                    progress("newacts", "Searching the web for latest information...", step="fallback")
                    log.warning("All searches exhausted, using web fallback",
                                budget_remaining=f"{remaining:.1f}s")
                    fallback_result = await web_search_fallback(query, "Newacts", _system_prompt)
                    fallback_result.retry_attempted = True
                    return {
                        "agent_results": {"Newacts": fallback_result},
                    }

        progress("newacts", f"Found {len(hits)} matching provisions", found=len(hits), step="search")
        log.info("ES results found", hit_count=len(hits))

        # Step 4.5: Relevance gate — reject section-number-collision matches
        # (e.g. query for BNS Section 318 returning a different act's Section
        # 318) and unrelated-act content with shared rare phrases.
        #
        # SKIPPED for exact-filter queries (act source + section_number terms)
        # because every hit is by construction a section of the requested act.
        # Running the gate in that mode trades false positives for false
        # negatives: the judge only sees the top-_RELEVANCE_TOP_N chunks of
        # what may be a 7+ hit result set, so multi-section comparison queries
        # ("BNS Sections 115, 118, 189, 190, 191, 351, 352") get rejected
        # despite all sections being present in the data.
        if hits and _is_exact_filter_query(metadata):
            log.info("Relevance gate skipped -- exact filter query",
                     act=metadata.act_name,
                     sections=len(metadata.section_number or []))

        # Skip the relevance judge for cross-act queries: when we ran
        # per-act searches (`_build_and_search_per_act`), each hit is
        # GUARANTEED to be a section from one of the user's named acts —
        # the judge can't improve precision, only false-reject. Gemini
        # Flash has known floating-point non-determinism even at temp=0,
        # so on the same input the verdict varies between runs. Skipping
        # here makes the response deterministic.
        # Detect cross-act queries on BOTH the rewritten query (post-memory)
        # AND the original_query: memory expands "BNS"→"Bharatiya Nyaya Sanhita
        # 2023" and "IPC"→"Indian Penal Code 1860", which strips the bare
        # `\bbns\b` / `\bipc\b` abbreviations. Without the full-name regex
        # below the cross-act path silently turned off post-rewrite and the
        # relevance judge + apology-detector fell back to web for valid
        # multi-act comparisons.
        _ACT_PATTERNS = {
            "bns":  r"\bbns\b|bharatiya\s+nyaya\s+sanhita",
            "bnss": r"\bbnss\b|bharatiya\s+nagarik\s+suraksha\s+sanhita",
            "bsa":  r"\bbsa\b|bharatiya\s+sakshya\s+adhiniyam",
            "ipc":  r"\bipc\b|indian\s+penal\s+code",
            "crpc": r"\bcrpc\b|\bcr\.?p\.?c\.?\b|code\s+of\s+criminal\s+procedure",
            "iea":  r"\biea\b|indian\s+evidence\s+act",
        }
        _q_lower_check = (
            (query or "").lower()
            + " "
            + (state.get("original_query") or "").lower()
        )
        _is_cross_act_query = sum(
            1 for pat in _ACT_PATTERNS.values()
            if re.search(pat, _q_lower_check)
        ) >= 2
        log.info("Cross-act detection",
                 is_cross_act=_is_cross_act_query,
                 query_preview=_q_lower_check[:160])

        if hits and _is_cross_act_query:
            log.info("Relevance gate skipped -- cross-act per-act search",
                     hits=len(hits))

        if hits and not _is_exact_filter_query(metadata) and not _is_cross_act_query:
            chunks_for_judge = [
                h["_source"].get("page_content", "") for h in hits
            ]
            source_for_judge = hits[0]["_source"].get("source", "unknown")
            progress("newacts", "Verifying retrieval relevance...",
                     step="relevance_check")
            is_relevant, judge_telemetry = await check_retrieval_relevance(
                query, chunks_for_judge, source_for_judge,
                agent_name="Newacts",
            )
            log.info("Relevance judge verdict",
                     passed=is_relevant, source=source_for_judge,
                     **judge_telemetry)
            if not is_relevant:
                log.warning("Retrieved provisions failed relevance gate — "
                            "falling back to web search",
                            rejected_source=source_for_judge, **judge_telemetry)
                mismatch = judge_telemetry.get("matched_subject") or "different subject"
                progress("newacts",
                         f"Retrieved provisions don't match the query "
                         f"({mismatch[:60]}) — searching the web...",
                         step="fallback", substep=True)
                from core.agent_fallback import web_search_fallback
                fallback_result = await web_search_fallback(
                    query, "Newacts", _system_prompt)
                fallback_result.retry_attempted = True
                return {"agent_results": {"Newacts": fallback_result}}

        # Step 5: Convert hits to documents
        docs_text = "\n\n".join(h["_source"]["page_content"] for h in hits)
        source_file = hits[0]["_source"].get("source", "unknown")

        # Step 6: Generate response (with 1 retry on disconnect)
        # max_output_tokens capped at 8192 (~32k chars) to bound the response.
        # Default 65535 allowed runaway markdown-column-padding loops on wide
        # multi-section comparison tables -- one BNS comparison ran 4m46s and
        # emitted 859k chars of whitespace padding inside a single table cell.
        # 8k tokens is comfortably above any legitimate provisions lookup
        # (typical: 2-6k chars per section, 7 sections = ~30k chars).
        progress("newacts", "Generating response...", step="generate")
        with log_time(log, "LLM generation"):
            llm = get_gemini_flash_full(temperature=0.1, max_output_tokens=8192)
            prompt = ChatPromptTemplate.from_messages([
                ("system", _system_prompt),
                MessagesPlaceholder(variable_name="chat_history", optional=True),
                ("user", "Act Provisions:\n{docs}"),
                ("user", "Current Date: {date}"),
                ("user", "User Query: {query}"),
            ])
            chain = prompt | llm
            gen_query = f"User's document/context:\n{user_context}\n\nUser's question:\n{query}" if user_context else query
            invoke_kwargs = {
                "query": gen_query,
                "docs": docs_text,
                "chat_history": chat_history,
                "date": str(date.today()),
            }

            from core.streaming import stream_chain_response
            llm_response = await stream_chain_response(chain, invoke_kwargs)

        from core.token_tracker import record as _record_tokens
        tokens = _record_tokens("Newacts", "generate", llm_response)

        # Check if LLM apologized (ES hits were irrelevant) — fall back to web search.
        # Uses the shared head+tail scanner so end-of-response hedges don't slip through.
        #
        # SKIPPED for cross-act queries: when we ran per-act searches, both
        # acts' sections are in `docs_text` by construction. If the LLM still
        # hedges ("a direct comparison is not possible..."), the right answer
        # is to keep the corpus-grounded output and let the user see what was
        # retrieved — NOT to overwrite it with web-scraped sources. Apology
        # detector here was hiding real corpus content behind testbook.com /
        # ipleaders.in links in the cross-act case.
        if head_tail_apology_detected(llm_response.text) and not _is_cross_act_query:
            log.warning("LLM response is an apology or too short, using web fallback",
                        response_preview=llm_response.text[:100])
            try:
                from langgraph.config import get_stream_writer
                writer = get_stream_writer()
                writer({"type": "token_reset"})
            except RuntimeError:
                pass
            from core.agent_fallback import web_search_fallback
            fallback_result = await web_search_fallback(query, "Newacts", _system_prompt)
            fallback_result.tokens_consumed += tokens
            return {
                "agent_results": {"Newacts": fallback_result},
            }
        elif head_tail_apology_detected(llm_response.text) and _is_cross_act_query:
            log.info("Apology detected but suppressing web fallback — cross-act query "
                     "(per-act hits are in docs_text, trust corpus output)",
                     response_preview=llm_response.text[:120])

        # Determine display name for the act
        act_display = metadata.act_name or source_file

        log.info("Agent completed",
                 act=act_display, response_len=len(llm_response.text),
                 tokens=tokens, hits=len(hits))

        sources = []
        for h in hits[:10]:
            src = h["_source"]
            sec_num = src.get("section_number", "")
            sec_str = str(sec_num) if sec_num else None
            sources.append(SourceMetadata(
                source_type="newacts",
                title=f"{act_display} - Section {sec_str}" if sec_str else act_display,
                content=[src["page_content"][:300]],
                file_name=src.get("source", "unknown"),
                agent_name="Newacts",
                relevance_score=h.get("_score"),
                section_number=sec_str,
                act_name=metadata.act_name,
            ))

        result = AgentResult(
            agent_name="Newacts",
            content=llm_response.text,
            sources=sources,
            tokens_consumed=tokens,
        )

    except Exception as e:
        from core.metrics import record_agent_error
        record_agent_error("Newacts", e)
        log.error("Agent failed", error=short_err(e), exc_info=True)
        result = AgentResult(
            agent_name="Newacts",
            content="",
            sources=[],
            tokens_consumed=0,
            error=short_err(e),
        )

    return {
        "agent_results": {"Newacts": result},
    }
