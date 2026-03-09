"""Shared tools for Supreme Court of India judgment search.

7 tools for the `supreme_court_judgement` Elasticsearch index:
semantic search, keyword search, case number, party name,
date range, judge search, and case details.

Adapted from: Tool-calling-judgment/r&d/tools.py
Uses v2 centralized ES client and settings.
"""

from typing import Optional

from langchain.tools import tool
from pydantic import BaseModel, Field

from core.clients import get_es_client
from core.settings import ES_INDICES

INDEX_NAME = ES_INDICES["sci_judgments"]


# --- Formatting Helpers ---

def _format_hit(hit, include_excerpt=True):
    """Format a single ES hit into a readable string."""
    src = hit["_source"]
    score = hit.get("_score")
    db_id = hit["_id"]

    pdf_links = src.get("pdf_links", [])
    pdf_url = pdf_links[0]["url"] if pdf_links else "N/A"

    score_line = f"\n- Relevance Score: {score:.2f}" if score is not None else ""

    result = (
        f"**{src.get('parties', 'Unknown')}** (DB ID: {db_id})\n"
        f"- Case No: {src.get('case_no', 'N/A')}\n"
        f"- Date: {src.get('judgment_date', 'N/A')}\n"
        f"- Bench: {(src.get('bench') or 'N/A')[:120]}\n"
        f"- Judgment By: {src.get('judgment_by') or 'N/A'}\n"
        f"- PDF: {pdf_url}"
        f"{score_line}"
    )

    if include_excerpt:
        highlights = hit.get("highlight", {})
        if "full_text" in highlights:
            excerpt = " ... ".join(highlights["full_text"][:3])
        else:
            full_text = src.get("full_text", "")
            excerpt = full_text[:500] + "..." if full_text else "N/A"
        result += f"\n- Excerpt: {excerpt}"

    return result


def _format_hits(hits, label="judgment", include_excerpt=True):
    """Format multiple ES hits."""
    if not hits:
        return f"No matching {label}s found."
    formatted = [_format_hit(h, include_excerpt=include_excerpt) for h in hits]
    return f"Found {len(formatted)} relevant {label}(s):\n\n" + "\n\n".join(formatted)


# ============================================================
# Tool 1: Semantic/Conceptual Search
# ============================================================

class SemanticSearchInput(BaseModel):
    query: str = Field(description="Natural language description of the legal topic, principle, or concept to search for")
    top_k: int = Field(default=5, description="Number of results to return (1-10)")
    year_from: Optional[int] = Field(default=None, description="Filter: minimum judgment year (e.g. 2000)")
    year_to: Optional[int] = Field(default=None, description="Filter: maximum judgment year (e.g. 2023)")


@tool(args_schema=SemanticSearchInput)
def search_by_topic(query: str, top_k: int = 5, year_from: Optional[int] = None, year_to: Optional[int] = None) -> str:
    """Search Supreme Court judgments by topic or legal concept using BM25 keyword matching.

    Use this tool when the user asks about a legal concept, topic, or principle
    in natural language. Examples: 'right to privacy', 'dowry death',
    'environmental protection principles', 'anticipatory bail conditions'.

    IMPORTANT: For best results, phrase the query as precise legal terminology,
    not as a long scenario description.

    This tool searches across the full judgment text, party names, and bench
    to find the most relevant cases using BM25 ranking with phrase boosting.
    NOTE: This uses keyword/BM25 matching, not embedding-based semantic search.
    """
    try:
        es = get_es_client()

        should_clauses = [
            {
                "multi_match": {
                    "query": query,
                    "fields": ["full_text", "parties^3", "bench^2", "judgment_by^2"],
                    "type": "best_fields",
                    "fuzziness": "AUTO",
                    "boost": 1,
                }
            },
            {
                "multi_match": {
                    "query": query,
                    "fields": ["full_text^2", "parties^3"],
                    "type": "phrase",
                    "slop": 3,
                    "boost": 3,
                }
            },
        ]

        filter_clauses = []
        if year_from or year_to:
            date_range = {}
            if year_from:
                date_range["gte"] = f"01-01-{year_from}"
            if year_to:
                date_range["lte"] = f"31-12-{year_to}"
            date_range["format"] = "dd-MM-yyyy"
            filter_clauses.append({"range": {"judgment_date": date_range}})

        body = {
            "size": min(top_k, 10),
            "query": {
                "bool": {
                    "should": should_clauses,
                    "minimum_should_match": 1,
                    "filter": filter_clauses,
                }
            },
            "highlight": {
                "fields": {"full_text": {"fragment_size": 300, "number_of_fragments": 3}},
                "pre_tags": ["**"],
                "post_tags": ["**"],
            },
            "_source": {"excludes": ["full_text"]},
        }

        result = es.search(index=INDEX_NAME, body=body)
        hits = result["hits"]["hits"]

        return _format_hits(hits)

    except Exception as e:
        return f"Semantic search error: {str(e)}"


# ============================================================
# Tool 2: Keyword Search
# ============================================================

class KeywordSearchInput(BaseModel):
    query: str = Field(description="Keywords or legal terms to search for in judgment text. For phrases, use quotes.")
    limit: int = Field(default=10, description="Number of results to return (1-20)")


@tool(args_schema=KeywordSearchInput)
def search_by_keyword(query: str, limit: int = 10) -> str:
    """Search Supreme Court judgments by exact keywords using full-text search.

    Use this tool when the user searches for specific legal terms, section numbers,
    act names, or exact phrases. Examples: 'Section 302 IPC', 'Article 21',
    'writ of mandamus', 'POCSO Act'.

    This uses BM25 full-text search which matches exact terms in judgment text.
    """
    try:
        es = get_es_client()

        body = {
            "size": min(limit, 20),
            "query": {
                "bool": {
                    "should": [
                        {"match_phrase": {"full_text": {"query": query, "boost": 3}}},
                        {"match": {"full_text": {"query": query, "boost": 1}}},
                        {"match_phrase": {"parties": {"query": query, "boost": 2}}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "highlight": {
                "fields": {"full_text": {"fragment_size": 200, "number_of_fragments": 3}},
                "pre_tags": [""],
                "post_tags": [""],
            },
            "_source": {"excludes": ["full_text"]},
        }

        result = es.search(index=INDEX_NAME, body=body)
        hits = result["hits"]["hits"]

        return _format_hits(hits)

    except Exception as e:
        return f"Keyword search error: {str(e)}"


# ============================================================
# Tool 3: Case Number Search
# ============================================================

class CaseNumberInput(BaseModel):
    case_number: str = Field(description="Case number to search for, e.g., 'Crl.A. No.-000883-000883 - 2020' or partial like 'C.A. No.-004085'")


@tool(args_schema=CaseNumberInput)
def search_by_case_number(case_number: str) -> str:
    """Look up a Supreme Court judgment by its case number.

    Use this tool when the user provides a specific case number like
    'Criminal Appeal No. 883 of 2020', 'CA 4085/2020', 'SLP(C) 13636/2020'.
    Handles partial matches.
    """
    try:
        es = get_es_client()

        body = {
            "size": 10,
            "query": {
                "bool": {
                    "should": [
                        {"match_phrase": {"case_no": {"query": case_number, "boost": 3}}},
                        {"match": {"case_no": {"query": case_number, "boost": 1}}},
                        {"wildcard": {"case_no.keyword": {"value": f"*{case_number}*", "boost": 2}}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "_source": {"excludes": ["full_text"]},
        }

        result = es.search(index=INDEX_NAME, body=body)
        hits = result["hits"]["hits"]

        return _format_hits(hits, include_excerpt=False)

    except Exception as e:
        return f"Case number search error: {str(e)}"


# ============================================================
# Tool 4: Party Name Search
# ============================================================

class PartyNameInput(BaseModel):
    party_name: str = Field(description="Name of a party (petitioner or respondent) to search for")
    limit: int = Field(default=10, description="Number of results to return (1-20)")


@tool(args_schema=PartyNameInput)
def search_by_party_name(party_name: str, limit: int = 10) -> str:
    """Search Supreme Court judgments by party name.

    Use this tool when the user mentions a specific person, company, or entity
    involved in a case. Examples: 'cases involving Tata Motors',
    'State of Maharashtra cases', 'Kesavananda Bharati'.
    """
    try:
        es = get_es_client()

        body = {
            "size": min(limit, 20),
            "query": {
                "bool": {
                    "should": [
                        {"match_phrase": {"parties": {"query": party_name, "boost": 3}}},
                        {"match": {"parties": {"query": party_name, "boost": 1}}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "_source": {"excludes": ["full_text"]},
        }

        result = es.search(index=INDEX_NAME, body=body)
        hits = result["hits"]["hits"]

        return _format_hits(hits, include_excerpt=False)

    except Exception as e:
        return f"Party name search error: {str(e)}"


# ============================================================
# Tool 5: Date Range Search
# ============================================================

class DateRangeInput(BaseModel):
    start_date: str = Field(description="Start date in DD-MM-YYYY format, e.g., '01-01-2020'")
    end_date: str = Field(description="End date in DD-MM-YYYY format, e.g., '31-12-2023'")
    limit: int = Field(default=10, description="Number of results to return (1-20)")


@tool(args_schema=DateRangeInput)
def search_by_date_range(start_date: str, end_date: str, limit: int = 10) -> str:
    """Search Supreme Court judgments within a specific date range.

    Use this tool when the user asks for judgments from a specific time period.
    Examples: 'cases from 2020 to 2023', 'judgments delivered in January 2024',
    'recent Supreme Court decisions this year'.
    Dates must be in DD-MM-YYYY format.
    """
    try:
        es = get_es_client()

        body = {
            "size": min(limit, 20),
            "query": {
                "range": {
                    "judgment_date": {
                        "gte": start_date,
                        "lte": end_date,
                        "format": "dd-MM-yyyy",
                    }
                }
            },
            "sort": [{"judgment_date": {"order": "desc"}}],
            "_source": {"excludes": ["full_text"]},
        }

        result = es.search(index=INDEX_NAME, body=body)
        hits = result["hits"]["hits"]

        return _format_hits(hits, include_excerpt=False)

    except Exception as e:
        return f"Date range search error: {str(e)}"


# ============================================================
# Tool 6: Judge Search
# ============================================================

class JudgeSearchInput(BaseModel):
    judge_name: str = Field(description="Name of the judge to search for, e.g., 'Chandrachud', 'Vikram Nath'")
    limit: int = Field(default=10, description="Number of results to return (1-20)")


@tool(args_schema=JudgeSearchInput)
def search_by_judge(judge_name: str, limit: int = 10) -> str:
    """Search Supreme Court judgments by judge or bench member name.

    Use this tool when the user asks for cases decided by a specific judge.
    Examples: 'judgments by Justice DY Chandrachud', 'cases where Justice Nariman was on the bench'.
    Note: Judge information is more complete for cases from 2014 onwards.
    """
    try:
        es = get_es_client()

        body = {
            "size": min(limit, 20),
            "query": {
                "bool": {
                    "should": [
                        {"match_phrase": {"bench": {"query": judge_name, "boost": 2}}},
                        {"match_phrase": {"judgment_by": {"query": judge_name, "boost": 3}}},
                        {"match": {"bench": {"query": judge_name, "boost": 1}}},
                        {"match": {"judgment_by": {"query": judge_name, "boost": 1}}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "sort": [{"judgment_date": {"order": "desc"}}],
            "_source": {"excludes": ["full_text"]},
        }

        result = es.search(index=INDEX_NAME, body=body)
        hits = result["hits"]["hits"]

        return _format_hits(hits, include_excerpt=False)

    except Exception as e:
        return f"Judge search error: {str(e)}"


# ============================================================
# Tool 7: Case Details
# ============================================================

class CaseDetailsInput(BaseModel):
    db_id: int = Field(description="Database ID of the case to retrieve full details for")


@tool(args_schema=CaseDetailsInput)
def get_case_details(db_id: int) -> str:
    """Get complete details of a specific Supreme Court case by its database ID.

    Use this tool to fetch the full judgment text, all PDF links, and complete metadata
    for a specific case. Use this AFTER finding a case through other search tools
    to get more details. The db_id is shown in search results as 'DB ID'.
    """
    try:
        es = get_es_client()

        result = es.get(index=INDEX_NAME, id=str(db_id))
        src = result["_source"]

        pdf_links = src.get("pdf_links", [])
        pdf_section = "\n".join(
            [f"  - {link.get('label', 'PDF')}: {link.get('url', 'N/A')}" for link in pdf_links]
        ) if pdf_links else "  No PDF links available"

        full_text = src.get("full_text", "N/A") or "N/A"
        text_preview = full_text[:3000] + "..." if len(full_text) > 3000 else full_text

        return f"""## Full Case Details

**Parties**: {src.get('parties', 'N/A')}
**Case No**: {src.get('case_no', 'N/A')}
**Diary No**: {src.get('diary_no', 'N/A')}
**Judgment Date**: {src.get('judgment_date', 'N/A')}
**Bench**: {src.get('bench') or 'N/A'}
**Judgment By**: {src.get('judgment_by') or 'N/A'}
**Pages**: {src.get('page_count', 'N/A')}

**PDF Links**:
{pdf_section}

**Judgment Text** (first 3000 characters):
{text_preview}
"""

    except Exception as e:
        return f"Case details error: {str(e)}"
