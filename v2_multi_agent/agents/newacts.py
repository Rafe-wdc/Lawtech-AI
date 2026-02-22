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

import re
from datetime import date
from typing import Optional, List

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import (
    get_es_client, get_gpt4o, get_gemini_flash, get_retriever_embeddings,
)
from core.settings import ES_INDICES
from core.logger import get_logger, log_time
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
    """Use GPT-4o to extract act metadata from a newacts query."""
    with log_time(log, "Act metadata extraction"):
        llm = get_gpt4o().with_structured_output(ActQueryMetadata)
        prompt = ChatPromptTemplate.from_template(METADATA_PROMPT)
        chain = prompt | llm
        result = chain.invoke({"query": query})

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

    if metadata.act_name and metadata.act_name in ACTS_PATHS:
        filters.append({"term": {"source": ACTS_PATHS[metadata.act_name]}})

    if metadata.section_number:
        filters.append({"terms": {"section_number": metadata.section_number}})

    # Hybrid search: BM25 + cosine similarity on embedding field
    if metadata.hybrid_search and query_text:
        log.debug("Building hybrid BM25+vector query")
        embeddings = get_retriever_embeddings()
        query_vector = embeddings.embed_query(query_text)

        should_clauses = [{"match": {"page_content": query_text}}]
        if metadata.act_name and metadata.act_name in ACTS_PATHS:
            should_clauses.append(
                {"term": {"source": ACTS_PATHS[metadata.act_name]}}
            )

        return {
            "size": 20,
            "query": {
                "script_score": {
                    "query": {
                        "bool": {
                            "should": should_clauses,
                            "filter": filters,
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
                {"section_number": {"order": "asc"}},
            ],
        }

    # Non-hybrid: exact filter search
    log.debug("Building exact filter query", filters_count=len(filters))
    return {
        "query": {
            "bool": {
                "must": [],
                "filter": filters,
            }
        },
        "size": 10,
        "sort": [{"section_number": {"order": "asc"}}],
    }


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
    query = state.get("query", state["original_query"])
    chat_history = state.get("chat_history", [])
    log.info("Agent started", query=query[:100])

    try:
        # Step 1: Extract metadata (GPT-4o with regex fallback)
        try:
            metadata = _extract_act_metadata(query)
        except Exception as meta_err:
            log.warning("GPT-4o metadata extraction failed, using regex fallback",
                        error=str(meta_err))
            metadata = _regex_fallback_metadata(query)

        # Step 1b: Detect old↔new mapping queries
        has_section = bool(metadata.section_number)
        has_act = bool(metadata.act_name and metadata.act_name in ACTS_PATHS)
        query_lower = query.lower()
        is_mapping_query = any(kw in query_lower for kw in _MAPPING_KEYWORDS)

        mapping_extra_hits = []  # extra hits from the counterpart act

        if is_mapping_query and has_section and metadata.section_number:
            log.info("Mapping query detected", act=metadata.act_name,
                     sections=metadata.section_number)

            # Scan ALL acts mentioned in query (not just metadata.act_name)
            # This handles cases like "Section 498a of IPC in BNS" where
            # metadata may extract BNS but the section belongs to IPC
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

            # Strategy: try to find mapping from old→new first
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
                            # Fetch new section
                            try:
                                nearby = _get_nearby_sections(
                                    mapped["new_section"], new_act_full, window=0,
                                )
                                for nh in nearby["hits"]:
                                    mapping_extra_hits.append({
                                        "_source": {
                                            "page_content": nh["content"],
                                            "source": nh["source"],
                                            "section_number": nh.get("section_number"),
                                        },
                                        "_score": nh.get("score"),
                                    })
                            except Exception as map_err:
                                log.warning("Failed to fetch mapped section",
                                            error=str(map_err))
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
                            try:
                                nearby = _get_nearby_sections(
                                    mapped["old_section"], old_act_full, window=0,
                                )
                                for nh in nearby["hits"]:
                                    mapping_extra_hits.append({
                                        "_source": {
                                            "page_content": nh["content"],
                                            "source": nh["source"],
                                            "section_number": nh.get("section_number"),
                                        },
                                        "_score": nh.get("score"),
                                    })
                            except Exception as map_err:
                                log.warning("Failed to fetch reverse-mapped section",
                                            error=str(map_err))

        # Step 2: Decide search path

        # Topic-only path: no section and no act identified → BM25 topic search
        if not has_section and not has_act and metadata.hybrid_search:
            log.info("No section/act identified, using topic search")
            with log_time(log, "Topic BM25 search"):
                topic_result = _search_newacts_by_topic(query)

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
            es_query = _build_newacts_query(metadata, query)

            with log_time(log, "ES search"):
                es = get_es_client()
                try:
                    response = es.search(index=ES_INDICES["newacts"], body=es_query)
                    hits = response["hits"]["hits"]
                except Exception as es_err:
                    err_msg = str(es_err).lower()
                    if "embedding" in err_msg or "script" in err_msg or "illegal_argument" in err_msg:
                        log.warning("Hybrid search failed, falling back to BM25-only",
                                    error=str(es_err))
                        bm25_result = _search_newacts_by_topic(
                            query, metadata.act_name,
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
                nearby = _get_nearby_sections(
                    center_sec, metadata.act_name, window=1, timeout=5,
                )
                added = 0
                existing_ids = {
                    h["_source"].get("section_number")
                    for h in hits
                    if h["_source"].get("section_number")
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

        # Step 4c: Collect related sections for frontend (wider window, separate from hits)
        related_sections_data = []
        if has_section and has_act and metadata.section_number:
            try:
                center_sec = metadata.section_number[0]
                nearby_for_ui = _get_nearby_sections(
                    center_sec, metadata.act_name, window=2, timeout=5,
                )
                existing_secs_in_hits = {
                    str(h["_source"].get("section_number", ""))
                    for h in hits
                    if h["_source"].get("section_number")
                }
                for nh in nearby_for_ui["hits"]:
                    sec_str = str(nh.get("section_number", ""))
                    if sec_str and sec_str not in existing_secs_in_hits:
                        related_sections_data.append({
                            "section_number": sec_str,
                            "title": nh["content"].split('\n')[0][:120],
                            "act_name": metadata.act_name or "",
                            "content_preview": nh["content"][:200],
                        })
                if related_sections_data:
                    log.info("Related sections collected for UI",
                             count=len(related_sections_data))
            except Exception as rel_err:
                log.warning("Related sections collection failed",
                            error=str(rel_err))

        if not hits:
            log.warning("No results found")
            return {
                "agent_results": {"Newacts": AgentResult(
                    agent_name="Newacts",
                    content="",
                    sources=[],
                    tokens_consumed=0,
                )},
                "related_sections": related_sections_data,
            }

        log.info("ES results found", hit_count=len(hits))

        # Step 5: Convert hits to documents
        docs_text = "\n\n".join(h["_source"]["page_content"] for h in hits)
        source_file = hits[0]["_source"].get("source", "unknown")

        # Step 6: Generate response (with 1 retry on disconnect)
        with log_time(log, "LLM generation"):
            llm = get_gemini_flash(temperature=0.1)
            prompt = ChatPromptTemplate.from_messages([
                ("system", NEWACTS_SYSTEM_PROMPT),
                MessagesPlaceholder(variable_name="chat_history", optional=True),
                ("user", "Act Provisions:\n{docs}"),
                ("user", "Current Date: {date}"),
                ("user", "User Query: {query}"),
            ])
            chain = prompt | llm
            invoke_kwargs = {
                "query": query,
                "docs": docs_text,
                "chat_history": chat_history,
                "date": str(date.today()),
            }

            try:
                llm_response = chain.invoke(invoke_kwargs)
            except Exception as llm_err:
                log.warning("LLM generation failed, retrying once",
                            error=str(llm_err)[:200])
                llm_response = chain.invoke(invoke_kwargs)

        tokens = 0
        if hasattr(llm_response, "usage_metadata") and llm_response.usage_metadata:
            tokens = llm_response.usage_metadata.get("total_tokens", 0)

        # Determine display name for the act
        act_display = metadata.act_name or source_file

        log.info("Agent completed",
                 act=act_display, response_len=len(llm_response.content),
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
            content=llm_response.content,
            sources=sources,
            tokens_consumed=tokens,
        )

    except Exception as e:
        log.error("Agent failed", error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="Newacts",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )
        related_sections_data = []

    return {
        "agent_results": {"Newacts": result},
        "related_sections": related_sections_data,
    }
