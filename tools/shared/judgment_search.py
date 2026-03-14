"""Specialized judgment search strategies for the Judgment agent.

Provides multi-strategy search capabilities beyond the basic multi-tier
query in elasticsearch_tools.py:

- Fuzzy party name matching with name variations
- Citation / case number direct lookup
- Case type specialized search (bail, quashing, writ, appeal)
- Court + judge filtered search
- Smart multi-strategy search with automatic fallback

Uses: Elasticsearch client from core.clients
Index: ES_INDICES["judgments"] (= "judgements")
"""

from __future__ import annotations

import re
from typing import Optional

from core.clients import get_es_client
from core.settings import ES_INDICES
from core.logger import get_logger

log = get_logger("JudgmentSearch")

INDEX = ES_INDICES["judgments"]

# Minimum results / score thresholds for "good enough" results
_MIN_GOOD_HITS = 3
_MIN_SCORE = 10.0


# ================================================================
# Shared helpers
# ================================================================

def _format_hits(hits: list[dict]) -> list[dict]:
    """Convert raw ES hits to standardized format with all metadata."""
    return [
        {
            "content": h["_source"]["page_content"],
            "source": h["_source"].get("source", "unknown"),
            "court_name": h["_source"].get("court_name", ""),
            "petitioner_names": h["_source"].get("petitioner_names", []),
            "respondent_names": h["_source"].get("respondent_names", []),
            "year": h["_source"].get("year"),
            "keywords": h["_source"].get("keywords", []),
            "acts_or_sections_invoked": h["_source"].get("acts_or_sections_invoked", []),
            "case_type": h["_source"].get("case_type", ""),
            "citation": h["_source"].get("citation", ""),
            "case_number": h["_source"].get("case_number", ""),
            "judge_names": h["_source"].get("judge_names", ""),
            "bench_judge_names": h["_source"].get("bench_judge_names", ""),
            "judgment_date": h["_source"].get("judgment_date"),
            "score": h["_score"],
        }
        for h in hits
    ]


def _has_good_results(hits: list[dict], min_hits: int = _MIN_GOOD_HITS) -> bool:
    """Check if results are sufficient in quantity and quality."""
    if not hits or len(hits) < min_hits:
        return False
    return (hits[0].get("score", 0) or 0) >= _MIN_SCORE


def _es_search(body: dict, timeout: int = 30) -> list[dict]:
    """Execute ES search on judgment index and return formatted hits."""
    es = get_es_client()
    resp = es.options(request_timeout=timeout).search(
        index=INDEX, body=body,
    )
    return resp["hits"]["hits"]


# ================================================================
# Strategy 1: Party Name Search (exact → fuzzy → single)
# ================================================================

def search_by_party_names(
    petitioner: str,
    respondent: str,
    year: int | None = None,
    court: str | None = None,
    size: int = 5,
) -> list[dict]:
    """Search by both petitioner and respondent names (relaxed match)."""
    must = [
        {"match": {"petitioner_names": {
            "query": petitioner, "boost": 20, "minimum_should_match": "75%",
        }}},
        {"match": {"respondent_names": {
            "query": respondent, "boost": 20, "minimum_should_match": "75%",
        }}},
    ]

    filters = []
    if year:
        filters.append({"term": {"year": year}})
    if court:
        filters.append({"term": {"court_name": court.lower()}})

    hits = _es_search({
        "size": size,
        "query": {"bool": {"must": must, "filter": filters}},
    })
    return _format_hits(hits)


def search_by_party_fuzzy(
    petitioner: str = "",
    respondent: str = "",
    year: int | None = None,
    court: str | None = None,
    size: int = 5,
) -> list[dict]:
    """Fuzzy party name search — handles misspellings and name variations."""
    should = []
    if petitioner:
        should.append({"match": {"petitioner_names": {
            "query": petitioner, "fuzziness": "AUTO", "boost": 15,
        }}})
        # Try partial name — last token (surname)
        parts = petitioner.strip().split()
        if len(parts) > 1:
            should.append({"match": {"petitioner_names": {
                "query": parts[-1], "boost": 8,
            }}})
    if respondent:
        should.append({"match": {"respondent_names": {
            "query": respondent, "fuzziness": "AUTO", "boost": 15,
        }}})
        parts = respondent.strip().split()
        if len(parts) > 1:
            should.append({"match": {"respondent_names": {
                "query": parts[-1], "boost": 8,
            }}})

    if not should:
        return []

    filters = []
    if year:
        filters.append({"term": {"year": year}})
    if court:
        filters.append({"term": {"court_name": court.lower()}})

    hits = _es_search({
        "size": size,
        "query": {"bool": {
            "should": should, "filter": filters, "minimum_should_match": 1,
        }},
    })
    return _format_hits(hits)


def search_by_single_party(
    name: str,
    role: str = "any",
    year: int | None = None,
    court: str | None = None,
    size: int = 5,
) -> list[dict]:
    """Search by a single party name across petitioner and/or respondent."""
    if role == "petitioner":
        should = [{"match": {"petitioner_names": {
            "query": name, "boost": 10, "minimum_should_match": "75%",
        }}}]
    elif role == "respondent":
        should = [{"match": {"respondent_names": {
            "query": name, "boost": 10, "minimum_should_match": "75%",
        }}}]
    else:
        should = [
            {"match": {"petitioner_names": {
                "query": name, "boost": 10, "minimum_should_match": "75%",
            }}},
            {"match": {"respondent_names": {
                "query": name, "boost": 10, "minimum_should_match": "75%",
            }}},
        ]

    filters = []
    if year:
        filters.append({"term": {"year": year}})
    if court:
        filters.append({"term": {"court_name": court.lower()}})

    hits = _es_search({
        "size": size,
        "query": {"bool": {
            "should": should, "filter": filters, "minimum_should_match": 1,
        }},
    })
    return _format_hits(hits)


# ================================================================
# Strategy 2: Citation / Case Number Lookup
# ================================================================

CITATION_PATTERN = re.compile(
    r"(?:"
    r"\d{4}\s+SCC\s+\d+"               # 2020 SCC 123
    r"|AIR\s+\d{4}\s+\w+\s+\d+"        # AIR 2020 SC 123
    r"|\(\d{4}\)\s*\d+\s*SCC\s*\d+"    # (2020) 1 SCC 123
    r"|CRL\.?\s*[A-Z\.]+\s*No\.?\s*\d+" # CRL.A. No. 123
    r"|SLP\s*\(?\w*\)?\s*No\.?\s*\d+"  # SLP(Crl) No. 123
    r"|W\.?P\.?\s*\(?\w*\)?\s*No\.?\s*\d+"  # W.P.(C) No. 123
    r"|BAIL\s*APPLN\.?\s*No\.?\s*\d+"   # BAIL APPLN. No. 123
    r"|Criminal\s+Appeal\s+No\.?\s*\d+"  # Criminal Appeal No. 123
    r"|Civil\s+Appeal\s+No\.?\s*\d+"     # Civil Appeal No. 123
    r")",
    re.IGNORECASE,
)


def detect_citation(query: str) -> str | None:
    """Detect citation or case number in a query string."""
    m = CITATION_PATTERN.search(query)
    return m.group(0).strip() if m else None


def search_by_citation(citation: str, size: int = 3) -> list[dict]:
    """Direct lookup by citation or case number (keyword fields)."""
    should = [
        {"term": {"citation": {"value": citation.lower(), "boost": 50}}},
        {"term": {"case_number": {"value": citation.lower(), "boost": 50}}},
        {"match_phrase": {"page_content": {"query": citation, "boost": 10}}},
    ]

    hits = _es_search({
        "size": size,
        "query": {"bool": {"should": should, "minimum_should_match": 1}},
    })
    return _format_hits(hits)


# ================================================================
# Strategy 3: Case Type Specialized Search
# ================================================================

CASE_TYPE_KEYWORDS = {
    "bail": [
        "bail", "anticipatory bail", "regular bail", "default bail",
        "interim bail", "bail application",
    ],
    "quashing": [
        "quashing", "quash", "section 482", "section 528",
        "quash fir", "quashing of fir",
    ],
    "writ": [
        "writ petition", "writ", "habeas corpus", "mandamus",
        "certiorari", "prohibition", "quo warranto",
    ],
    "criminal_appeal": [
        "criminal appeal", "criminal revision", "criminal misc",
    ],
    "civil_appeal": [
        "civil appeal", "civil revision", "civil suit",
    ],
    "divorce": [
        "divorce", "matrimonial", "maintenance", "section 125",
        "restitution of conjugal rights", "judicial separation",
    ],
    "land": [
        "land acquisition", "property dispute", "eviction",
        "tenancy", "specific performance",
    ],
    "motor_accident": [
        "motor accident", "mact", "compensation claim",
        "road accident", "vehicular accident",
    ],
    "consumer": [
        "consumer complaint", "consumer dispute", "deficiency in service",
        "unfair trade practice",
    ],
    "arbitration": [
        "arbitration", "arbitral award", "section 34 arbitration",
        "section 11 arbitration",
    ],
}


def detect_case_type(query: str) -> str | None:
    """Detect specialized case type from query text."""
    q_lower = query.lower()
    for case_type, keywords in CASE_TYPE_KEYWORDS.items():
        if any(kw in q_lower for kw in keywords):
            return case_type
    return None


def search_by_case_type(
    query: str,
    case_type: str,
    court: str | None = None,
    year: int | None = None,
    size: int = 10,
) -> list[dict]:
    """Search judgments filtered by case type with topic matching."""
    type_keywords = CASE_TYPE_KEYWORDS.get(case_type, [case_type])

    should = [
        {"match": {"page_content": {"query": query, "boost": 5}}},
        {"match": {"keywords": {"query": query, "boost": 10}}},
    ]

    # Boost case type keywords in content and keywords fields
    for kw in type_keywords[:3]:
        should.append({"match": {"keywords": {"query": kw, "boost": 8}}})
        should.append({"match_phrase": {"page_content": {"query": kw, "boost": 3}}})

    # Build case_type field filter
    case_type_should = [
        {"term": {"case_type": kw.lower()}} for kw in type_keywords
    ]

    filters = []
    if court:
        filters.append({"term": {"court_name": court.lower()}})
    if year:
        filters.append({"term": {"year": year}})

    # Primary: filter by case_type field
    hits = _es_search({
        "size": size,
        "query": {"bool": {
            "should": should,
            "filter": [
                {"bool": {"should": case_type_should, "minimum_should_match": 1}},
            ] + filters,
            "minimum_should_match": 1,
        }},
    })

    # If case_type filter returns too few, fall back to keyword-only
    if len(hits) < _MIN_GOOD_HITS:
        fallback_hits = _es_search({
            "size": size,
            "query": {"bool": {
                "should": should, "filter": filters,
                "minimum_should_match": 1,
            }},
        })
        seen = {h["_id"] for h in hits}
        for h in fallback_hits:
            if h["_id"] not in seen:
                hits.append(h)
                seen.add(h["_id"])

    return _format_hits(hits[:size])


# ================================================================
# Strategy 4: Court + Judge Search
# ================================================================

def search_by_judge(
    judge_name: str,
    query: str = "",
    court: str | None = None,
    size: int = 10,
) -> list[dict]:
    """Search judgments by judge name."""
    must = [
        {"match": {"bench_judge_names": {
            "query": judge_name, "minimum_should_match": "75%", "boost": 15,
        }}},
    ]

    should = []
    if query:
        should.append({"match": {"page_content": {"query": query, "boost": 3}}})
        should.append({"match": {"keywords": {"query": query, "boost": 5}}})

    filters = []
    if court:
        filters.append({"term": {"court_name": court.lower()}})

    hits = _es_search({
        "size": size,
        "query": {"bool": {"must": must, "should": should, "filter": filters}},
    })
    return _format_hits(hits)


def search_by_date_range(
    query: str,
    start_date: str,
    end_date: str,
    court: str | None = None,
    size: int = 10,
) -> list[dict]:
    """Search judgments within a date range (YYYY-MM-DD format)."""
    should = [
        {"match": {"page_content": {"query": query, "boost": 5}}},
        {"match": {"keywords": {"query": query, "boost": 10}}},
    ]

    filters = [
        {"range": {"judgment_date": {"gte": start_date, "lte": end_date}}},
    ]
    if court:
        filters.append({"term": {"court_name": court.lower()}})

    hits = _es_search({
        "size": size,
        "query": {"bool": {
            "should": should, "filter": filters, "minimum_should_match": 1,
        }},
    })
    return _format_hits(hits)


# ================================================================
# Strategy 5: Acts / Sections Invoked
# ================================================================

def search_by_legal_provision(
    provision: str,
    query: str = "",
    court: str | None = None,
    size: int = 10,
) -> list[dict]:
    """Search judgments that invoke a specific legal provision."""
    must = [
        {"match": {"acts_or_sections_invoked": {
            "query": provision, "boost": 20, "minimum_should_match": "80%",
        }}},
    ]

    should = []
    if query:
        should.append({"match": {"page_content": {"query": query, "boost": 3}}})
        should.append({"match": {"keywords": {"query": query, "boost": 5}}})

    filters = []
    if court:
        filters.append({"term": {"court_name": court.lower()}})

    hits = _es_search({
        "size": size,
        "query": {"bool": {"must": must, "should": should, "filter": filters}},
    })
    return _format_hits(hits)


# ================================================================
# Smart Multi-Strategy Search
# ================================================================

def smart_judgment_search(
    query: str,
    petitioner: str = "",
    respondent: str = "",
    year: int | None = None,
    court: str | None = None,
    topics: list[str] | None = None,
    acts_or_sections: list[str] | None = None,
    lexical_query: str = "",
    size: int = 10,
) -> dict:
    """Smart multi-strategy judgment search with automatic fallback.

    Tries up to 8 strategies in order of specificity, stopping when
    sufficient good-quality results are found.

    Returns:
        dict with: hits (list), strategy_used (str), strategies_tried (list)
    """
    best_results: list[dict] = []
    strategies_tried: list[str] = []

    # Adjust "good enough" threshold based on requested size
    # If user requests 1-2 results, don't require 3 to stop early
    min_hits = min(_MIN_GOOD_HITS, max(1, size))

    def _update_best(new_hits: list[dict]) -> bool:
        nonlocal best_results
        if len(new_hits) > len(best_results):
            best_results = new_hits
        return _has_good_results(best_results, min_hits)

    # --- Strategy 1: Citation lookup ---
    citation = detect_citation(query)
    if citation:
        strategies_tried.append("citation")
        results = search_by_citation(citation, size)
        if results:
            log.info("citation match", citation=citation[:40], hits=len(results))
            return {
                "hits": results,
                "strategy_used": "citation",
                "strategies_tried": strategies_tried,
            }

    # --- Strategy 2: Both party names ---
    if petitioner and respondent:
        # 2a: Exact match with year
        strategies_tried.append("party_both_exact")
        results = search_by_party_names(
            petitioner, respondent, year, court, size,
        )
        if _update_best(results):
            log.info("party exact match", hits=len(best_results))
            return {
                "hits": best_results,
                "strategy_used": "party_both_exact",
                "strategies_tried": strategies_tried,
            }

        # 2b: Without year filter
        if year and len(best_results) < _MIN_GOOD_HITS:
            strategies_tried.append("party_no_year")
            results = search_by_party_names(
                petitioner, respondent, None, court, size,
            )
            if _update_best(results):
                log.info("party match (no year)", hits=len(best_results))
                return {
                    "hits": best_results,
                    "strategy_used": "party_no_year",
                    "strategies_tried": strategies_tried,
                }

        # 2c: Swapped names (petitioner/respondent reversed)
        strategies_tried.append("party_swapped")
        results = search_by_party_names(
            respondent, petitioner, year, court, size,
        )
        if _update_best(results):
            log.info("swapped party match", hits=len(best_results))
            return {
                "hits": best_results,
                "strategy_used": "party_swapped",
                "strategies_tried": strategies_tried,
            }

        # 2d: Fuzzy party match
        strategies_tried.append("party_fuzzy")
        results = search_by_party_fuzzy(
            petitioner, respondent, year, court, size,
        )
        if _update_best(results):
            log.info("fuzzy party match", hits=len(best_results))
            return {
                "hits": best_results,
                "strategy_used": "party_fuzzy",
                "strategies_tried": strategies_tried,
            }

    # --- Strategy 3: Single party name ---
    single_name = petitioner or respondent
    if single_name:
        strategies_tried.append("single_party")
        results = search_by_single_party(single_name, "any", year, court, size)
        if _update_best(results):
            log.info("single party match", hits=len(best_results))
            return {
                "hits": best_results,
                "strategy_used": "single_party",
                "strategies_tried": strategies_tried,
            }

    # --- Strategy 4: Case type specialized search ---
    case_type = detect_case_type(query)
    if case_type:
        strategies_tried.append(f"case_type:{case_type}")
        results = search_by_case_type(query, case_type, court, year, size)
        if _update_best(results):
            log.info("case type match", case_type=case_type, hits=len(best_results))
            return {
                "hits": best_results,
                "strategy_used": f"case_type:{case_type}",
                "strategies_tried": strategies_tried,
            }

    # --- Strategy 5: Acts / sections invoked ---
    acts_or_sections = acts_or_sections or []
    if acts_or_sections:
        strategies_tried.append("legal_provision")
        for provision in acts_or_sections:
            results = search_by_legal_provision(provision, query, court, size)
            _update_best(results)
        if _has_good_results(best_results, min_hits):
            log.info("provision match", hits=len(best_results))
            return {
                "hits": best_results,
                "strategy_used": "legal_provision",
                "strategies_tried": strategies_tried,
            }

    # --- Strategy 6: Multi-tier (topics + acts + lexical) ---
    strategies_tried.append("multi_tier")
    should_clauses = []
    topics = topics or []
    lq = lexical_query or query

    for topic in topics:
        should_clauses.append({"match": {"keywords": {
            "query": topic, "boost": 15, "minimum_should_match": "90%",
        }}})
    for section in acts_or_sections:
        should_clauses.append({"match": {"acts_or_sections_invoked": {
            "query": section, "boost": 15, "minimum_should_match": "90%",
        }}})
    if lq:
        should_clauses.append({"match": {"page_content": {
            "query": lq, "boost": 8, "minimum_should_match": "80%",
        }}})
    if not should_clauses:
        should_clauses.append({"match": {"page_content": {
            "query": query, "boost": 3,
        }}})

    filters = []
    if year:
        filters.append({"term": {"year": year}})
    if court:
        filters.append({"term": {"court_name": court.lower()}})

    mt_hits = _es_search({
        "size": size,
        "query": {"bool": {
            "should": should_clauses, "filter": filters,
            "minimum_should_match": 1,
        }},
    }, timeout=50)
    if _update_best(_format_hits(mt_hits)):
        log.info("multi-tier match", hits=len(best_results))
        return {
            "hits": best_results,
            "strategy_used": "multi_tier",
            "strategies_tried": strategies_tried,
        }

    # --- Strategy 7: Relaxed full-text fallback (no year/court filter) ---
    strategies_tried.append("full_text_relaxed")
    relaxed_hits = _es_search({
        "size": size,
        "query": {"bool": {
            "should": [
                {"match": {"page_content": {"query": query, "boost": 5}}},
                {"match": {"keywords": {"query": query, "boost": 10}}},
            ],
            "minimum_should_match": 1,
        }},
    }, timeout=50)
    _update_best(_format_hits(relaxed_hits))

    strategy_used = strategies_tried[-1] if best_results else "none"
    log.info(
        "search completed",
        strategy_used=strategy_used,
        strategies_tried=len(strategies_tried),
        total_hits=len(best_results),
    )

    return {
        "hits": best_results,
        "strategy_used": strategy_used,
        "strategies_tried": strategies_tried,
    }
