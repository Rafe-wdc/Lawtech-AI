"""Shared Tools: Elasticsearch search operations.

Reusable @tool functions for searching across all ES indices:
- legislation, judgments, newacts, drafting, constitution, legal_maxims

These tools encapsulate the raw ES query logic so agents
can call them without embedding search code inline.

Also includes domain-specific helper tools:
- create_search_variations, search_high_court, map_act_to_source,
  map_old_to_new_law, validate_draft_format, translate_draft
- search_constitution, search_constitution_by_part, search_legal_maxims

Uses: Elasticsearch client from core.clients
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections import Counter
from typing import Optional

from langchain.tools import tool

from core.clients import get_es_client, get_retriever_embeddings
from core.logger import get_logger
from core.settings import ES_INDICES
from tools.inline.section_parser import parse_section_info

log = get_logger("ElasticsearchTools")


# --- Input Sanitization ---

# Whitelist of indices this module is allowed to search.
# Used to block index-name injection in get_docs_by_source.
_ALLOWED_ES_INDICES: frozenset[str] = frozenset(ES_INDICES.values())

# Lucene / ES query_string metacharacters that can alter DSL structure
# if a query_string clause is ever added. Escaping them here is defence-in-depth.
_LUCENE_SPECIAL = re.compile(r'([+\-=&|!(){}\[\]^"~*?:\\/])')


def _sanitize_es_input(text: str, max_length: int = 500) -> str:
    """Sanitize a user-supplied string before embedding it in an ES query body.

    Steps applied in order:
    1. Unicode NFC normalization — prevents homoglyph bypass tricks.
    2. Strip ASCII control characters (\\x00-\\x1f, \\x7f) except \\t and \\n.
    3. Truncate to max_length — stops expensive tokenisation of huge inputs.
    4. Escape Lucene metacharacters — defence-in-depth for any future
       query_string clause; harmless for match / match_phrase (they analyse
       the string and ignore escaped chars).

    Args:
        text:       Raw user-supplied string.
        max_length: Hard cap on character length (default 500).

    Returns:
        Sanitized string safe to embed in ES query values.
    """
    if not isinstance(text, str):
        return ""
    # 1. Unicode normalise
    text = unicodedata.normalize("NFC", text)
    # 2. Strip control chars (keep \t and \n for readability)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # 3. Truncate
    text = text[:max_length]
    # 4. Escape Lucene special chars
    text = _LUCENE_SPECIAL.sub(r"\\\1", text)
    return text


def _sanitize_section_number(section: str) -> str:
    """Validate that a section number contains only safe characters.

    Allows digits, letters, dots, brackets, and hyphens — the characters
    that legitimately appear in Indian legal section numbers like
    "65b", "3(5)", "10-A", "302".  Strips everything else.
    """
    return re.sub(r"[^A-Za-z0-9.()\-]", "", section)[:20]


def _validate_index_name(index_name: str) -> bool:
    """Return True only if index_name is one of the known ES indices."""
    return index_name in _ALLOWED_ES_INDICES


# --- Search Variation Helpers ---

def _create_search_variations(query: str, parsed_info: dict | None) -> list[str]:
    """Create multiple search query variations from parsed section info."""
    import re

    queries = [query]

    if parsed_info:
        st = parsed_info["section_type"]
        sn = parsed_info["section_number"]
        act = parsed_info["act_name"]

        queries.extend([
            f"{st} {sn} of {act}",
            f"{st} {sn} of the {act}",
            f"{st.title()} {sn} of {act.title()}",
            f"{st.title()} {sn} of the {act.title()}",
        ])

        if st == "rule":
            queries.append(f"rules {sn} of {act}")
        elif st == "rules":
            queries.append(f"rule {sn} of {act}")

    # Deduplicate preserving order
    seen = set()
    unique = []
    for q in queries:
        q_clean = re.sub(r"\s+", " ", q.strip().lower())
        if q_clean not in seen:
            seen.add(q_clean)
            unique.append(q)
    return unique


# --- Legislation Search ---

@tool
def search_legislation(query: str) -> dict:
    """Search the legislation Elasticsearch index for Indian legislation provisions.

    Performs multi-query search with section parsing, source aggregation,
    and targeted sub-search within the most relevant source document.

    Args:
        query: Natural language legal query, e.g. "Section 44 of Transfer of Property Act"

    Returns:
        Dict with keys: hits (list of doc dicts), source (most common source name)
    """
    query = _sanitize_es_input(query)
    es = get_es_client()
    index = ES_INDICES["legislation"]

    parsed_info = parse_section_info(query)
    search_queries = _create_search_variations(query, parsed_info)

    all_hits = []
    sources_counter: Counter = Counter()

    for query_text in search_queries:
        es_query = {
            "size": 20,
            "query": {
                "bool": {
                    "should": [
                        {"match": {
                            "page_content": {
                                "query": query_text,
                                "boost": 2.0 if query_text == query else 1.0,
                            }
                        }},
                        {"match_phrase": {
                            "page_content": {
                                "query": query_text,
                                "boost": 1.5,
                            }
                        }},
                    ]
                }
            },
        }

        try:
            response = es.search(index=index, body=es_query)
            current_hits = response["hits"]["hits"]

            for hit in current_hits:
                sources_counter[hit["_source"]["source"]] += hit["_score"]
            all_hits.extend(current_hits)

            if len(current_hits) > 5 and any(h["_score"] > 5.0 for h in current_hits):
                break
        except Exception as e:
            log.error(f"[ES:Legislation] Search failed for '{query_text}': {e}")
            continue

    if not sources_counter:
        return {"hits": [], "source": None}

    most_common_source = sources_counter.most_common(1)[0][0]

    # Targeted search within the most common source
    if parsed_info:
        st = parsed_info["section_type"]
        sn = parsed_info["section_number"]

        should_clauses = []
        for qt in search_queries[:5]:
            should_clauses.extend([
                {"match_phrase": {"page_content": {"query": qt, "boost": 3.0}}},
                {"match": {"page_content": {"query": qt, "boost": 1.5}}},
            ])

        for variation in [f"{st} {sn}", f"{st.title()} {sn}", f"{st.upper()} {sn}"]:
            should_clauses.append(
                {"match_phrase": {"page_content": {"query": variation, "boost": 4.0}}}
            )

        # Boost section header pattern to prioritize the actual section
        # over cross-referencing sections
        for header_var in [
            f"section number: {st.title()} {sn}",
            f"section number: {st} {sn}",
        ]:
            should_clauses.append(
                {"match_phrase": {"page_content": {"query": header_var, "boost": 8.0}}}
            )

        source_query = {
            "query": {
                "bool": {
                    "must": [{"term": {"source.keyword": most_common_source}}],
                    "should": should_clauses,
                    "minimum_should_match": 1,
                }
            },
            "size": 5,
            "sort": [{"_score": {"order": "desc"}}],
        }
    else:
        source_query = {
            "query": {
                "bool": {
                    "must": [{"match": {"page_content": query}}],
                    "filter": [{"term": {"source.keyword": most_common_source}}],
                }
            },
            "size": 5,
        }

    source_response = es.search(index=index, body=source_query)
    final_hits = source_response["hits"]["hits"]

    return {
        "hits": [
            {
                "content": h["_source"]["page_content"],
                "source": h["_source"].get("source", "unknown"),
                "score": h["_score"],
            }
            for h in final_hits
        ],
        "source": most_common_source,
    }


# --- Legislation Topic Search ---

def _search_legislation_by_topic(
    query: str, act_name: Optional[str] = None, size: int = 10,
) -> dict:
    """Internal: topic-based legislation search without section numbers."""
    query = _sanitize_es_input(query)
    if act_name:
        act_name = _sanitize_es_input(act_name, max_length=200)
    es = get_es_client()
    index = ES_INDICES["legislation"]

    # BM25 match on page_content
    must_clause = {
        "match": {
            "page_content": {
                "query": query,
                "minimum_should_match": "75%",
            }
        }
    }

    filters = []
    if act_name:
        # Fuzzy match against source field — use match instead of term
        # since the caller may pass a human-readable act name
        filters.append({"match": {"source": {"query": act_name, "minimum_should_match": "80%"}}})

    # Phase 1: discover most common source
    discover_query = {
        "size": 30,
        "query": {
            "bool": {
                "must": [must_clause],
                "filter": filters,
            }
        },
    }

    response = es.search(index=index, body=discover_query)
    discover_hits = response["hits"]["hits"]

    if not discover_hits:
        return {"hits": [], "source": None, "topic_matched": False}

    # Aggregate by source
    sources_counter: Counter = Counter()
    for h in discover_hits:
        sources_counter[h["_source"]["source"]] += h["_score"]

    most_common_source = sources_counter.most_common(1)[0][0]

    # Phase 2: targeted retrieval within best source
    targeted_query = {
        "size": size,
        "query": {
            "bool": {
                "must": [must_clause],
                "filter": [{"term": {"source.keyword": most_common_source}}],
            }
        },
        "sort": [{"_score": {"order": "desc"}}],
    }

    targeted_response = es.search(index=index, body=targeted_query)
    final_hits = targeted_response["hits"]["hits"]

    return {
        "hits": [
            {
                "content": h["_source"]["page_content"],
                "source": h["_source"].get("source", "unknown"),
                "score": h["_score"],
            }
            for h in final_hits
        ],
        "source": most_common_source,
        "topic_matched": True,
    }


@tool
def search_legislation_by_topic(
    query: str, act_name: Optional[str] = None, size: int = 10,
) -> dict:
    """Search legislation by topic/concept without requiring section numbers.

    For vague or conceptual queries like "director duties in companies act"
    or "consumer refund rights". Uses BM25 full-text matching.

    Args:
        query: Conceptual/topic legal query (no section number needed)
        act_name: Optional act name to filter results to a specific statute
        size: Number of results to return

    Returns:
        Dict with keys: hits (list of doc dicts), source (most common source), topic_matched (bool)
    """
    return _search_legislation_by_topic(query, act_name, size)


# --- Legislation Multi-Section Search ---

def _search_legislation_multi_section(
    query: str, section_numbers: list[str], act_name: str = "",
) -> dict:
    """Internal: search for multiple specific sections of an act.

    Phase 1 — Source discovery: broad search with first section + act name
    to identify the most relevant source document.
    Phase 2 — Per-section retrieval: search within that source for each section.
    """
    import re as _re

    query = _sanitize_es_input(query)
    act_name = _sanitize_es_input(act_name, max_length=200) if act_name else ""
    # Validate each section number — only keep safe chars, cap at 20
    section_numbers = [_sanitize_section_number(s) for s in section_numbers if s]
    section_numbers = [s for s in section_numbers if s]  # drop any that became empty

    es = get_es_client()
    index = ES_INDICES["legislation"]

    # Clean act_name: strip trailing punctuation, filler words
    clean_act = act_name.strip()
    clean_act = _re.sub(r'[?!.,;]+$', '', clean_act)
    clean_act = _re.sub(r'\b(say|explain|what|does|do|the)\b', '', clean_act, flags=_re.IGNORECASE)
    clean_act = _re.sub(r'\s+', ' ', clean_act).strip()

    # --- Phase 1: Source discovery ---
    # Strategy: match act name against source field (file path), ordered by
    # doc_count so the actual act (with many sections) outranks peripheral
    # acts that merely contain the same words (e.g. "Companies Act" vs
    # "Gas Companies Act").
    best_source = None

    if clean_act:
        try:
            source_discovery = es.search(index=index, body={
                "size": 0,
                "query": {"match_phrase": {"source": clean_act}},
                "aggs": {
                    "by_source": {
                        "terms": {
                            "field": "source.keyword",
                            "size": 5,
                            "order": {"_count": "desc"},
                        },
                    }
                },
            })
            buckets = (
                source_discovery
                .get("aggregations", {})
                .get("by_source", {})
                .get("buckets", [])
            )
            if buckets:
                best_source = buckets[0]["key"]
        except Exception:
            pass

    # Fallback: content-based discovery if source matching failed
    if not best_source:
        discovery_text = (
            f"section {section_numbers[0]} of the {clean_act}" if clean_act
            else f"section {section_numbers[0]}"
        )
        try:
            fallback_resp = es.search(index=index, body={
                "size": 30,
                "query": {"match": {"page_content": discovery_text}},
            })
            sources_counter: Counter = Counter()
            for h in fallback_resp["hits"]["hits"]:
                sources_counter[h["_source"]["source"]] += h["_score"]
            if sources_counter:
                best_source = sources_counter.most_common(1)[0][0]
        except Exception:
            pass

    # --- Phase 2: Per-section retrieval within best source ---
    all_hits = []
    seen_ids: set[str] = set()
    hits_by_section: dict[str, list] = {}

    for sec_num in section_numbers:
        should_clauses = []
        for variation in [
            f"section {sec_num} of {act_name}",
            f"section {sec_num} of the {act_name}",
            f"Section {sec_num} of {act_name.title()}" if act_name else f"Section {sec_num}",
            f"Section {sec_num}",
        ]:
            should_clauses.append(
                {"match_phrase": {"page_content": {"query": variation, "boost": 3.0}}}
            )
        should_clauses.append(
            {"match": {"page_content": {"query": f"section {sec_num} {act_name}", "boost": 1.5}}}
        )

        # Apply source filter if discovery found a best source
        filter_clauses = []
        if best_source:
            filter_clauses.append({"term": {"source.keyword": best_source}})

        es_query = {
            "size": 5,
            "query": {
                "bool": {
                    "should": should_clauses,
                    "minimum_should_match": 1,
                    "filter": filter_clauses,
                }
            },
            "sort": [{"_score": {"order": "desc"}}],
        }

        try:
            response = es.search(index=index, body=es_query)
            section_hits = []
            for h in response["hits"]["hits"]:
                doc_id = h["_id"]
                if doc_id not in seen_ids:
                    seen_ids.add(doc_id)
                    hit_dict = {
                        "content": h["_source"]["page_content"],
                        "source": h["_source"].get("source", "unknown"),
                        "score": h["_score"],
                        "section_queried": sec_num,
                    }
                    all_hits.append(hit_dict)
                    section_hits.append(hit_dict)
            hits_by_section[sec_num] = section_hits
        except Exception:
            hits_by_section[sec_num] = []
            continue

    return {
        "hits": all_hits,
        "hits_by_section": hits_by_section,
        "total": len(all_hits),
        "source": best_source,
    }


@tool
def search_legislation_multi_section(
    query: str, section_numbers: list[str], act_name: str = "",
) -> dict:
    """Search legislation for multiple specific sections of an act.

    For queries like "Sections 44 and 45 of Transfer of Property Act"
    or "Sections 302, 307 and 420 of IPC".

    Args:
        query: The original user query
        section_numbers: List of section numbers to look up (e.g. ["44", "45"])
        act_name: The act name (e.g. "transfer of property act")

    Returns:
        Dict with keys: hits (all results), hits_by_section (dict mapping section→hits), total
    """
    return _search_legislation_multi_section(query, section_numbers, act_name)


# --- Judgment Search ---

@tool
def search_judgments(
    query: str,
    court_name: Optional[str] = None,
    petitioner_names: Optional[list[str]] = None,
    respondent_names: Optional[list[str]] = None,
    year: Optional[int] = None,
    topics: Optional[list[str]] = None,
    acts_or_sections: Optional[list[str]] = None,
    lexical_query: Optional[str] = None,
    size: int = 3,
) -> dict:
    """Search the judgments Elasticsearch index for Indian court judgments.

    Uses a multi-tier query strategy:
    Tier 1: Exact party name match (boost 20)
    Tier 2: Topic keywords match (boost 15)
    Tier 3: Acts/sections invoked match (boost 15)
    Tier 4: Lexical BM25 fallback (boost 8)
    Tier 5: Full-text fallback (boost 3)

    Args:
        query: The original user query text
        court_name: Court name filter (e.g. "supreme court", "bombay high court")
        petitioner_names: List of petitioner names to match
        respondent_names: List of respondent names to match
        year: Year filter (4-digit)
        topics: Legal topics to match (e.g. ["murder", "bail"])
        acts_or_sections: Legal provisions (e.g. ["section 138 ni act"])
        lexical_query: Normalized BM25 search string
        size: Number of results to return (1-50)

    Returns:
        Dict with keys: hits (list of doc dicts with content, metadata, score), total
    """
    query = _sanitize_es_input(query)
    petitioner_names = [_sanitize_es_input(n, max_length=200) for n in (petitioner_names or []) if n]
    respondent_names = [_sanitize_es_input(n, max_length=200) for n in (respondent_names or []) if n]
    topics = [_sanitize_es_input(t, max_length=200) for t in (topics or []) if t]
    acts_or_sections = [_sanitize_es_input(a, max_length=200) for a in (acts_or_sections or []) if a]
    lexical_query = _sanitize_es_input(lexical_query, max_length=300) if lexical_query else query
    court_name = _sanitize_es_input(court_name, max_length=100) if court_name else None

    es = get_es_client()
    should_clauses = []
    filters = []

    # TIER 1: Exact Party Match
    if petitioner_names and respondent_names:
        for petitioner in petitioner_names:
            for respondent in respondent_names:
                should_clauses.append({
                    "bool": {
                        "must": [
                            {"match": {"petitioner_names": {"query": petitioner, "boost": 20, "minimum_should_match": "95%"}}},
                            {"match": {"respondent_names": {"query": respondent, "boost": 20, "minimum_should_match": "95%"}}},
                        ]
                    }
                })

    # TIER 2: Topics
    for topic in topics:
        should_clauses.append(
            {"match": {"keywords": {"query": topic, "boost": 15, "minimum_should_match": "95%"}}}
        )

    # TIER 3: Acts / Sections
    for section in acts_or_sections:
        should_clauses.append(
            {"match": {"acts_or_sections_invoked": {"query": section, "boost": 15, "minimum_should_match": "95%"}}}
        )

    # TIER 4: Lexical Query
    if lexical_query:
        should_clauses.append(
            {"match": {"page_content": {"query": lexical_query, "boost": 8, "minimum_should_match": "95%"}}}
        )

    # TIER 5: Full-text fallback
    if query and not lexical_query:
        should_clauses.append(
            {"match": {"page_content": {"query": query, "boost": 3}}}
        )

    # Filters
    if year:
        filters.append({"term": {"year": year}})
    if court_name:
        filters.append({"term": {"court_name": court_name.lower()}})

    es_query = {
        "size": min(size, 50),
        "query": {
            "bool": {
                "should": should_clauses,
                "filter": filters if filters else [],
                "minimum_should_match": 1,
            }
        },
    }

    response = es.options(request_timeout=50).search(
        index=ES_INDICES["judgments"], body=es_query
    )

    hits = response["hits"]["hits"]
    return {
        "hits": [
            {
                "content": h["_source"]["page_content"],
                "source": h["_source"].get("source", "unknown"),
                "court_name": h["_source"].get("court_name", ""),
                "petitioner_names": h["_source"].get("petitioner_names", []),
                "respondent_names": h["_source"].get("respondent_names", []),
                "score": h["_score"],
            }
            for h in hits
        ],
        "total": len(hits),
    }


# --- Newacts Search ---

# Act Name → ES Source Path Mapping
ACTS_PATHS = {
    "The Bharatiya Nyaya Sanhita, 2023": "/content/Bharitya New Acts/The Bharatiya Nyaya Sanhita,2023.csv",
    "The Bharatiya Nagarik Suraksha Sanhita, 2023": "/content/Bharitya New Acts/The Bharatiya Nagarik Suraksha Sanhita, 2023.csv",
    "The Bharatiya Sakshya Adhiniyam, 2023": "/content/Bharitya New Acts/The Bharatiya Sakshya Adhiniyam, 2023.csv",
    "The Code of Criminal Procedure 1973": "/content/Bharitya New Acts/Code of Criminal Procedure 1974.csv",
    "The Indian Penal Code, 1860": "/content/Bharitya New Acts/Indian Penal Code 1860.csv",
    "Indian Evidence Act 1872": "/content/Bharitya New Acts/Indian Evidence Act 1872.csv",
}


@tool
def search_newacts(
    query: str,
    section_numbers: Optional[list[str]] = None,
    act_name: Optional[str] = None,
    hybrid_search: bool = False,
) -> dict:
    """Search the newacts Elasticsearch index for Indian codes (BNS, BNSS, BSA, IPC, CrPC, IEA).

    Supports two modes:
    - Exact filter: when section number and act name are known
    - Hybrid BM25 + cosine: when query is vague/conceptual

    Args:
        query: The user's query text
        section_numbers: List of section numbers to filter (e.g. ["302", "307"])
        act_name: Exact act name from allowed mapping (e.g. "The Bharatiya Nyaya Sanhita, 2023")
        hybrid_search: Whether to use BM25 + vector hybrid scoring

    Returns:
        Dict with keys: hits (list of doc dicts), total
    """
    query = _sanitize_es_input(query)
    # section_numbers: validated to safe chars only; act_name gated by ACTS_PATHS whitelist
    if section_numbers:
        section_numbers = [_sanitize_section_number(s) for s in section_numbers if s]
        section_numbers = [s for s in section_numbers if s]

    es = get_es_client()
    filters = []

    if act_name and act_name in ACTS_PATHS:
        filters.append({"term": {"source": ACTS_PATHS[act_name]}})

    if section_numbers:
        filters.append({"terms": {"section_number": section_numbers}})

    if hybrid_search and query:
        embeddings = get_retriever_embeddings()
        query_vector = embeddings.embed_query(query)

        should_clauses = [{"match": {"page_content": query}}]
        if act_name and act_name in ACTS_PATHS:
            should_clauses.append({"term": {"source": ACTS_PATHS[act_name]}})

        es_query = {
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
    else:
        es_query = {
            "query": {
                "bool": {
                    "must": [],
                    "filter": filters,
                }
            },
            "size": 10,
            "sort": [{"section_number": {"order": "asc"}}],
        }

    response = es.search(index=ES_INDICES["newacts"], body=es_query)
    hits = response["hits"]["hits"]

    return {
        "hits": [
            {
                "content": h["_source"]["page_content"],
                "source": h["_source"].get("source", "unknown"),
                "section_number": h["_source"].get("section_number"),
                "score": h.get("_score"),
            }
            for h in hits
        ],
        "total": len(hits),
    }


# --- Newacts Topic Search (BM25-only, safe fallback) ---

def _search_newacts_by_topic(
    query: str, act_name: Optional[str] = None, size: int = 15,
) -> dict:
    """Internal: BM25-only topic search in newacts. Avoids embedding/script errors."""
    query = _sanitize_es_input(query)
    es = get_es_client()

    filters = []
    if act_name and act_name in ACTS_PATHS:
        filters.append({"term": {"source": ACTS_PATHS[act_name]}})

    es_query = {
        "size": size,
        "query": {
            "bool": {
                "must": [
                    {"match": {
                        "page_content": {
                            "query": query,
                            "minimum_should_match": "60%",
                        }
                    }}
                ],
                "filter": filters,
            }
        },
        "sort": [
            {"_score": {"order": "desc"}},
            {"section_number": {"order": "asc"}},
        ],
    }

    response = es.search(index=ES_INDICES["newacts"], body=es_query)
    hits = response["hits"]["hits"]

    return {
        "hits": [
            {
                "content": h["_source"]["page_content"],
                "source": h["_source"].get("source", "unknown"),
                "section_number": h["_source"].get("section_number"),
                "score": h.get("_score"),
            }
            for h in hits
        ],
        "total": len(hits),
    }


@tool
def search_newacts_by_topic(
    query: str, act_name: Optional[str] = None, size: int = 15,
) -> dict:
    """Search newacts by topic using BM25 only (no vector/embedding search).

    Safe fallback for conceptual queries like "punishment for theft in BNS"
    or "bail provisions in BNSS". Avoids the cosineSimilarity script that
    can fail if embedding fields are missing.

    Args:
        query: Conceptual/topic query text
        act_name: Optional exact act name from ACTS_PATHS mapping
        size: Number of results to return

    Returns:
        Dict with keys: hits (list of doc dicts), total
    """
    return _search_newacts_by_topic(query, act_name, size)


# --- Nearby Sections in Newacts ---

def _get_nearby_sections(
    section_number: str, act_name: str, window: int = 2,
    timeout: int = 5,
) -> dict:
    """Internal: retrieve adjacent sections from an act in newacts index.

    Args:
        timeout: Per-request ES timeout in seconds (default 5).
                 Keeps enrichment fast; callers should handle failure gracefully.
    """
    es = get_es_client()

    if act_name not in ACTS_PATHS:
        return {"hits": [], "center_section": section_number, "range": []}

    try:
        center = int(section_number)
    except ValueError:
        return {"hits": [], "center_section": section_number, "range": []}

    start = max(1, center - window)
    end = center + window
    range_values = [str(n) for n in range(start, end + 1)]

    es_query = {
        "size": len(range_values) + 5,  # a little headroom
        "query": {
            "bool": {
                "filter": [
                    {"term": {"source": ACTS_PATHS[act_name]}},
                    {"terms": {"section_number": range_values}},
                ]
            }
        },
        "sort": [{"section_number": {"order": "asc"}}],
    }

    response = es.search(
        index=ES_INDICES["newacts"], body=es_query, request_timeout=timeout,
    )
    hits = response["hits"]["hits"]

    return {
        "hits": [
            {
                "content": h["_source"]["page_content"],
                "source": h["_source"].get("source", "unknown"),
                "section_number": h["_source"].get("section_number"),
                "score": h.get("_score"),
            }
            for h in hits
        ],
        "center_section": section_number,
        "range": [str(start), str(end)],
    }


@tool
def get_nearby_sections(
    section_number: str, act_name: str, window: int = 2,
) -> dict:
    """Get sections adjacent to a given section in a newacts act.

    Useful for providing context around a specific section, or answering
    queries like "what comes after section 35 of BNS".

    Args:
        section_number: The center section number (e.g. "35")
        act_name: Exact act name from ACTS_PATHS (e.g. "The Bharatiya Nyaya Sanhita, 2023")
        window: How many sections on each side to retrieve (default 2)

    Returns:
        Dict with keys: hits (list of doc dicts sorted by section), center_section, range
    """
    return _get_nearby_sections(section_number, act_name, window)


# --- Drafting Search ---

@tool
def search_drafts(query: str, size: int = 100) -> dict:
    """Search the drafting Elasticsearch index for legal document templates.

    Returns matching templates with their source file paths for template selection.

    Args:
        query: Description of the legal document to draft (e.g. "bail application")
        size: Number of results to return (default 100 for broad template matching)

    Returns:
        Dict with keys: hits (list of doc dicts), file_paths (unique source paths)
    """
    query = _sanitize_es_input(query)
    es = get_es_client()
    index = ES_INDICES["drafting"]

    es_query = {
        "size": size,
        "query": {"match": {"page_content": query}},
    }

    response = es.search(index=index, body=es_query)
    hits = response["hits"]["hits"]

    file_paths = list(dict.fromkeys(h["_source"]["source"] for h in hits))

    return {
        "hits": [
            {
                "content": h["_source"]["page_content"],
                "source": h["_source"].get("source", "unknown"),
                "score": h["_score"],
            }
            for h in hits
        ],
        "file_paths": file_paths,
    }


# --- Get Docs by Source ---

@tool
def get_docs_by_source(index_name: str, source_path: str, size: int = 1) -> dict:
    """Retrieve all documents from a specific source file in an Elasticsearch index.

    Used after template selection to fetch the full template document,
    or after source identification to get complete legislation text.

    Args:
        index_name: ES index to search (e.g. "drafting", "legislation")
        source_path: Exact source file path to filter on
        size: Number of documents to return

    Returns:
        Dict with keys: hits (list of doc dicts), total
    """
    # Block index-name injection — only allow indices declared in settings.py
    if not _validate_index_name(index_name):
        return {"hits": [], "total": 0, "error": f"Unknown index: '{index_name}'"}
    source_path = _sanitize_es_input(source_path, max_length=500)

    es = get_es_client()

    es_query = {
        "size": size,
        "query": {"term": {"source.keyword": source_path}},
    }

    response = es.search(index=index_name, body=es_query)
    hits = response["hits"]["hits"]

    return {
        "hits": [
            {
                "content": h["_source"]["page_content"],
                "source": h["_source"].get("source", "unknown"),
            }
            for h in hits
        ],
        "total": len(hits),
    }


# --- Domain-Specific Helper Tools ---

@tool
def create_search_variations(query: str) -> list[str]:
    """Create multiple search query variations from a legal query.

    Parses section type, number, and act name from the query,
    then generates alternative phrasings for better ES recall.

    Args:
        query: The legal query to generate variations for

    Returns:
        List of query variation strings (deduplicated)
    """
    parsed_info = parse_section_info(query)
    return _create_search_variations(query, parsed_info)


@tool
def search_high_court(
    query: str,
    court_name: Optional[str] = None,
    size: int = 5,
) -> dict:
    """Search for High Court judgments in Elasticsearch.

    Specialized search for High Court-specific cases with court name filtering.
    Uses the same judgment index but filters for High Court documents.

    Args:
        query: The legal query to search for
        court_name: Specific High Court name (e.g. "bombay high court", "delhi high court")
        size: Number of results to return

    Returns:
        Dict with keys: hits (list of doc dicts), total
    """
    query = _sanitize_es_input(query)
    court_name = _sanitize_es_input(court_name, max_length=100) if court_name else None
    es = get_es_client()

    should_clauses = [
        {"match": {"page_content": {"query": query, "boost": 5}}},
        {"match": {"keywords": {"query": query, "boost": 10}}},
    ]

    filters = []
    if court_name:
        filters.append({"match": {"court_name": court_name.lower()}})
    else:
        # Default to any High Court
        filters.append({"wildcard": {"court_name": "*high court*"}})

    es_query = {
        "size": size,
        "query": {
            "bool": {
                "should": should_clauses,
                "filter": filters,
                "minimum_should_match": 1,
            }
        },
    }

    response = es.options(request_timeout=50).search(
        index=ES_INDICES["judgments"], body=es_query
    )

    hits = response["hits"]["hits"]
    return {
        "hits": [
            {
                "content": h["_source"]["page_content"],
                "source": h["_source"].get("source", "unknown"),
                "court_name": h["_source"].get("court_name", ""),
                "petitioner_names": h["_source"].get("petitioner_names", []),
                "respondent_names": h["_source"].get("respondent_names", []),
                "score": h["_score"],
            }
            for h in hits
        ],
        "total": len(hits),
    }


@tool
def map_act_to_source(act_name: str) -> dict:
    """Map an act name to its Elasticsearch source file path.

    Looks up the act name in the ACTS_PATHS mapping used for
    the newacts ES index. Handles case-insensitive matching.

    Args:
        act_name: The act name or abbreviation (e.g. "BNS", "The Bharatiya Nyaya Sanhita, 2023")

    Returns:
        Dict with keys: source_path (str or None), act_name (str or None)
    """
    # Direct match
    if act_name in ACTS_PATHS:
        return {"source_path": ACTS_PATHS[act_name], "act_name": act_name}

    # Case-insensitive match
    act_lower = act_name.lower()
    for key, path in ACTS_PATHS.items():
        if key.lower() == act_lower:
            return {"source_path": path, "act_name": key}

    # Abbreviation mapping
    ABBREVIATION_MAP = {
        "bns": "The Bharatiya Nyaya Sanhita, 2023",
        "bnss": "The Bharatiya Nagarik Suraksha Sanhita, 2023",
        "bsa": "The Bharatiya Sakshya Adhiniyam, 2023",
        "crpc": "The Code of Criminal Procedure 1973",
        "ipc": "The Indian Penal Code, 1860",
        "iea": "Indian Evidence Act 1872",
    }

    mapped = ABBREVIATION_MAP.get(act_lower)
    if mapped and mapped in ACTS_PATHS:
        return {"source_path": ACTS_PATHS[mapped], "act_name": mapped}

    return {"source_path": None, "act_name": None}


# Old ↔ New Act Section Mapping
_OLD_NEW_MAPPING = {
    # IPC → BNS (select important sections)
    "ipc": {
        "302": {"new_act": "BNS", "new_section": "103", "description": "Murder"},
        "304": {"new_act": "BNS", "new_section": "105", "description": "Culpable homicide not amounting to murder"},
        "307": {"new_act": "BNS", "new_section": "109", "description": "Attempt to murder"},
        "376": {"new_act": "BNS", "new_section": "64", "description": "Rape"},
        "420": {"new_act": "BNS", "new_section": "318", "description": "Cheating"},
        "498a": {"new_act": "BNS", "new_section": "85", "description": "Cruelty by husband"},
        "354": {"new_act": "BNS", "new_section": "74", "description": "Assault to outrage modesty"},
        "304a": {"new_act": "BNS", "new_section": "106", "description": "Death by negligence"},
        "299": {"new_act": "BNS", "new_section": "100", "description": "Culpable homicide"},
        "34": {"new_act": "BNS", "new_section": "3(5)", "description": "Common intention"},
    },
    # CrPC → BNSS
    "crpc": {
        "125": {"new_act": "BNSS", "new_section": "144", "description": "Maintenance"},
        "154": {"new_act": "BNSS", "new_section": "173", "description": "FIR"},
        "161": {"new_act": "BNSS", "new_section": "180", "description": "Examination of witnesses"},
        "164": {"new_act": "BNSS", "new_section": "183", "description": "Recording of confessions"},
        "438": {"new_act": "BNSS", "new_section": "482", "description": "Anticipatory bail"},
        "439": {"new_act": "BNSS", "new_section": "483", "description": "Special powers of High Court/Sessions Court"},
        "482": {"new_act": "BNSS", "new_section": "528", "description": "Inherent powers of High Court"},
    },
    # IEA → BSA
    "iea": {
        "3": {"new_act": "BSA", "new_section": "2", "description": "Interpretation clause"},
        "24": {"new_act": "BSA", "new_section": "22", "description": "Confession by accused"},
        "25": {"new_act": "BSA", "new_section": "23", "description": "Confession to police officer"},
        "27": {"new_act": "BSA", "new_section": "25", "description": "Information received from accused"},
        "45": {"new_act": "BSA", "new_section": "39", "description": "Expert opinion"},
        "65b": {"new_act": "BSA", "new_section": "63", "description": "Electronic evidence"},
    },
}


@tool
def map_old_to_new_law(section: str, old_act: str) -> dict:
    """Map a section from an old Indian law to its corresponding new law section.

    Maps between IPC↔BNS, CrPC↔BNSS, and IEA↔BSA.

    Args:
        section: The section number (e.g. "302", "438", "65b")
        old_act: The old act abbreviation (e.g. "ipc", "crpc", "iea")

    Returns:
        Dict with keys: found (bool), new_act (str), new_section (str), description (str)
    """
    act_lower = old_act.lower()
    section_lower = section.lower().strip()

    mapping = _OLD_NEW_MAPPING.get(act_lower, {})
    result = mapping.get(section_lower)

    if result:
        return {
            "found": True,
            "old_act": old_act.upper(),
            "old_section": section,
            "new_act": result["new_act"],
            "new_section": result["new_section"],
            "description": result["description"],
        }

    return {
        "found": False,
        "old_act": old_act.upper(),
        "old_section": section,
        "new_act": None,
        "new_section": None,
        "description": "Mapping not found — check the full act text",
    }


@tool
def validate_draft_format(draft: str) -> dict:
    """Validate a legal draft for formatting and structural issues.

    Checks for common formatting problems in legal documents:
    proper header, signature blocks, legal citations, clause numbering,
    and markdown formatting.

    Args:
        draft: The generated legal draft text to validate

    Returns:
        Dict with keys: valid (bool), issues (list of issue descriptions)
    """
    issues = []

    if not draft or len(draft) < 100:
        issues.append("Draft is too short — likely incomplete")

    lines = draft.split("\n")

    # Check for header/title
    has_header = any(
        line.strip().startswith("#") or line.strip().startswith("IN THE")
        or line.strip().startswith("BEFORE") or line.strip().isupper()
        for line in lines[:10]
    )
    if not has_header:
        issues.append("Missing header/title section")

    # Check for placeholder markers
    placeholder_patterns = [r"\[.*?\]", r"\{.*?\}", r"___+", r"\.\.\.\.", r"<.*?>"]
    import re
    placeholder_count = 0
    for pattern in placeholder_patterns:
        placeholder_count += len(re.findall(pattern, draft))
    if placeholder_count > 20:
        issues.append(f"Too many placeholders ({placeholder_count}) — may need more user details")

    # Check for signature block
    signature_indicators = ["signature", "signed", "deponent", "advocate", "counsel", "notary"]
    has_signature = any(ind in draft.lower() for ind in signature_indicators)
    if not has_signature and len(draft) > 500:
        issues.append("Missing signature/verification block")

    # Check for date
    date_patterns = [r"\bdate\b", r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", r"\bday of\b"]
    has_date = any(re.search(p, draft, re.IGNORECASE) for p in date_patterns)
    if not has_date and len(draft) > 500:
        issues.append("Missing date field")

    # Line length check
    long_lines = [i + 1 for i, line in enumerate(lines) if len(line) > 150]
    if len(long_lines) > 5:
        issues.append(f"Multiple lines exceed 150 chars (lines: {long_lines[:5]}...)")

    return {"valid": len(issues) == 0, "issues": issues}


@tool
def translate_draft(draft: str, target_language: str) -> dict:
    """Translate a legal draft into the specified language.

    Uses Gemini Flash Lite to translate while preserving legal terminology,
    formatting, section references, and formal legal register.

    Args:
        draft: The legal draft text to translate
        target_language: Target language (e.g. "Hindi", "Marathi", "Tamil", "Bengali")

    Returns:
        Dict with keys: translated (str), language (str), tokens_consumed (int)
    """
    from core.clients import get_gemini_flash as _get_flash

    try:
        llm = _get_flash(temperature=0.1)
        prompt = (
            f"Translate this legal document into {target_language}.\n\n"
            "Rules:\n"
            "1. Preserve all section numbers, act names, and legal citations in English\n"
            "2. Maintain formal legal register and tone\n"
            "3. Keep the original document structure and formatting\n"
            "4. Translate legal terms accurately using standard legal terminology\n"
            "5. Preserve all placeholders and formatting markers\n\n"
            f"Document:\n{draft}"
        )

        response = llm.invoke(prompt)
        tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            tokens = response.usage_metadata.get("total_tokens", 0)

        return {
            "translated": response.content,
            "language": target_language,
            "tokens_consumed": tokens,
        }

    except Exception as e:
        log.error(f"[Drafting] Translation failed: {e}")
        return {"translated": "", "language": target_language, "tokens_consumed": 0}


# ============================================================
# Constitution Search Tools
# ============================================================

@tool
def search_constitution(query: str, article_number: Optional[str] = None) -> dict:
    """Search the Constitution of India index in Elasticsearch.

    Supports full-text search on article text and optional filtering by
    article number. Uses a two-phase approach: first discovers the best
    matching articles, then retrieves full content.

    Args:
        query: The legal query about constitutional provisions
        article_number: Optional specific article number to filter (e.g. "21", "19", "14")

    Returns:
        Dict with keys: documents (list of {content, source, article_number, article_name, part_name}), total
    """
    es = get_es_client()
    index = ES_INDICES.get("constitution", "constitution")
    query_text = _sanitize_es_input(query)

    must_clauses = []
    should_clauses = [
        {"match": {"page_content": {"query": query_text, "boost": 2.0}}},
        {"match_phrase": {"page_content": {"query": query_text, "boost": 3.0}}},
        {"match": {"article_name": {"query": query_text, "boost": 2.5}}},
        {"match": {"part_name": {"query": query_text, "boost": 1.0}}},
    ]

    if article_number:
        sanitized_article = _sanitize_es_input(article_number, max_length=50)
        # Search for "Article X" pattern in article_number field
        should_clauses.append(
            {"match_phrase": {"article_number": {"query": f"Article {sanitized_article}", "boost": 10.0}}}
        )

    body = {
        "query": {
            "bool": {
                "must": must_clauses if must_clauses else [{"match_all": {}}],
                "should": should_clauses,
                "minimum_should_match": 1,
            }
        },
        "size": 10,
    }

    try:
        result = es.search(index=index, body=body)
        hits = result["hits"]["hits"]

        documents = []
        for hit in hits:
            src = hit["_source"]
            documents.append({
                "content": src.get("page_content", ""),
                "source": src.get("source", ""),
                "article_number": src.get("article_number", ""),
                "article_name": src.get("article_name", ""),
                "part_number": src.get("part_number", ""),
                "part_name": src.get("part_name", ""),
                "score": hit["_score"],
            })

        return {"documents": documents, "total": len(documents)}

    except Exception as e:
        log.error(f"[Constitution] Search failed: {e}")
        return {"documents": [], "total": 0}


@tool
def search_constitution_by_part(query: str, part_name: str) -> dict:
    """Search Constitution articles filtered by a specific Part.

    Useful when the user asks about all articles in a specific Part
    (e.g. "fundamental rights" → Part III, "directive principles" → Part IV).

    Args:
        query: The legal query about constitutional provisions
        part_name: The Part name to filter by (e.g. "Fundamental Rights", "Part III")

    Returns:
        Dict with keys: documents (list of {content, article_number, article_name, part_name}), total
    """
    es = get_es_client()
    index = ES_INDICES.get("constitution", "constitution")
    query_text = _sanitize_es_input(query)
    part_text = _sanitize_es_input(part_name, max_length=100)

    body = {
        "query": {
            "bool": {
                "must": [
                    {"match": {"part_name": {"query": part_text}}},
                ],
                "should": [
                    {"match": {"page_content": {"query": query_text, "boost": 2.0}}},
                    {"match": {"article_name": {"query": query_text, "boost": 1.5}}},
                ],
            }
        },
        "size": 20,
    }

    try:
        result = es.search(index=index, body=body)
        hits = result["hits"]["hits"]

        documents = []
        for hit in hits:
            src = hit["_source"]
            documents.append({
                "content": src.get("page_content", ""),
                "source": src.get("source", ""),
                "article_number": src.get("article_number", ""),
                "article_name": src.get("article_name", ""),
                "part_name": src.get("part_name", ""),
                "score": hit["_score"],
            })

        return {"documents": documents, "total": len(documents)}

    except Exception as e:
        log.error(f"[Constitution] Part search failed: {e}")
        return {"documents": [], "total": 0}


# ============================================================
# Legal Maxim Search Tools
# ============================================================

@tool
def search_legal_maxims(query: str, maxim_name: Optional[str] = None) -> dict:
    """Search the Legal Maxims index in Elasticsearch.

    Searches across maxim names, meanings, and full text content.
    Supports optional filtering by a specific maxim name.

    Args:
        query: The legal query about maxims (e.g. "hearing both sides", "res judicata")
        maxim_name: Optional specific maxim name to search for (e.g. "Audi Alteram Partem")

    Returns:
        Dict with keys: documents (list of {content, source, maxim_name}), total
    """
    es = get_es_client()
    index = ES_INDICES.get("maxims", "legal_maxims")
    query_text = _sanitize_es_input(query)

    should_clauses = [
        {"match": {"page_content": {"query": query_text, "boost": 2.0}}},
        {"match_phrase": {"page_content": {"query": query_text, "boost": 3.0}}},
    ]

    if maxim_name:
        sanitized_maxim = _sanitize_es_input(maxim_name, max_length=100)
        should_clauses.append(
            {"match_phrase": {"maxim_name": {"query": sanitized_maxim, "boost": 10.0}}}
        )
    else:
        # Also search the maxim_name field with the general query
        should_clauses.append(
            {"match": {"maxim_name": {"query": query_text, "boost": 5.0}}}
        )

    body = {
        "query": {
            "bool": {
                "should": should_clauses,
                "minimum_should_match": 1,
            }
        },
        "size": 10,
    }

    try:
        result = es.search(index=index, body=body)
        hits = result["hits"]["hits"]

        documents = []
        for hit in hits:
            src = hit["_source"]
            documents.append({
                "content": src.get("page_content", ""),
                "source": src.get("source", ""),
                "maxim_name": src.get("maxim_name", ""),
                "score": hit["_score"],
            })

        return {"documents": documents, "total": len(documents)}

    except Exception as e:
        log.error(f"[Maxim] Search failed: {e}")
        return {"documents": [], "total": 0}
