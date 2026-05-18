"""Shared tools for GST Appellate Authority for Advance Ruling (AAAR) search.

6 tools for the `gst_judgements` OpenSearch index — forked from
sci_judgment_tools.py. Differences from SCI:
  - INDEX_NAME points at gst_judgements
  - No judge / bench fields (AAARs are administrative bodies, not courts)
    -> dropped search_by_judge; dropped bench/judgment_by from boosts
  - Added gst_search_by_state for state/UT filtering
  - _format_hit + get_case_details surface state_ut, brief_of_order,
    ar_order_no_date instead of bench/judgment_by
  - db_id is a SHA1 string, not an int

Index size: 533 docs. 527 have full_text; 5 have no PDF URL; 1 had OCR failure.
"""

from typing import Optional

from langchain.tools import tool
from pydantic import BaseModel, Field

from core.clients import get_es_client
from core.settings import ES_INDICES

INDEX_NAME = ES_INDICES["gst_judgments"]


# --- Formatting Helpers ---

def _format_hit(hit, include_excerpt=True):
    """Format a single ES hit into a readable string."""
    src = hit["_source"]
    score = hit.get("_score")
    db_id = hit["_id"]

    pdf_links = src.get("pdf_links", [])
    pdf_url = pdf_links[0]["url"] if pdf_links else "N/A"

    score_line = f"\n- Relevance Score: {score:.2f}" if score is not None else ""
    brief = (src.get("brief_of_order") or "").strip()
    brief_line = f"\n- Brief: {brief[:200]}" if brief else ""
    ar_order = (src.get("ar_order_no_date") or "").strip()
    ar_line = f"\n- Underlying AR Order: {ar_order}" if ar_order else ""

    result = (
        f"**{src.get('parties', 'Unknown')}** (DB ID: {db_id})\n"
        f"- Appeal Order No: {src.get('case_no', 'N/A')}\n"
        f"- Date: {src.get('judgment_date', 'N/A')}\n"
        f"- State/UT: {src.get('state_ut', 'N/A')}"
        f"{brief_line}"
        f"{ar_line}"
        f"\n- PDF: {pdf_url}"
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


def _format_hits(hits, label="GST appellate order", include_excerpt=True):
    if not hits:
        return f"No matching {label}s found."
    formatted = [_format_hit(h, include_excerpt=include_excerpt) for h in hits]
    return f"Found {len(formatted)} relevant {label}(s):\n\n" + "\n\n".join(formatted)


# ============================================================
# Tool 1: Topic / Conceptual Search
# ============================================================

class TopicSearchInput(BaseModel):
    query: str = Field(description="Natural language description of the GST legal topic or issue")
    top_k: int = Field(default=5, description="Number of results to return (1-10)")
    year_from: Optional[int] = Field(default=None, description="Filter: minimum order year")
    year_to: Optional[int] = Field(default=None, description="Filter: maximum order year")


@tool(args_schema=TopicSearchInput)
def gst_search_by_topic(query: str, top_k: int = 5, year_from: Optional[int] = None, year_to: Optional[int] = None) -> str:
    """Search GST AAAR appellate orders by topic or issue using BM25 keyword matching.

    Use when the user describes a GST issue or concept in natural language.
    Examples: 'classification of solar panels', 'ITC on input services',
    'valuation of related-party supply', 'GST on healthcare services'.

    Searches across full_text, parties (applicant), and brief_of_order.
    """
    try:
        es = get_es_client()

        should_clauses = [
            {
                "multi_match": {
                    "query": query,
                    "fields": ["full_text", "parties^3", "brief_of_order^2"],
                    "type": "best_fields",
                    "fuzziness": "AUTO",
                    "boost": 1,
                }
            },
            {
                "multi_match": {
                    "query": query,
                    "fields": ["full_text^2", "parties^3", "brief_of_order^2"],
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
        return f"GST topic search error: {str(e)}"


# ============================================================
# Tool 2: Keyword Search
# ============================================================

class KeywordSearchInput(BaseModel):
    query: str = Field(description="Keywords or exact phrases to search in order text")
    limit: int = Field(default=10, description="Number of results to return (1-20)")


@tool(args_schema=KeywordSearchInput)
def gst_search_by_keyword(query: str, limit: int = 10) -> str:
    """Search GST AAAR orders by exact keywords / phrases (BM25 full-text search).

    Use for specific section numbers, notification numbers, HSN codes, or exact terms.
    Examples: 'Section 17(5)', 'Notification 11/2017', 'HSN 8541', 'composite supply'.
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
        return f"GST keyword search error: {str(e)}"


# ============================================================
# Tool 3: Case Number Search
# ============================================================

class CaseNumberInput(BaseModel):
    case_number: str = Field(description="Appeal order number, e.g. 'GUJ/GAAAR/APPEAL/2020/04' or partial")


@tool(args_schema=CaseNumberInput)
def gst_search_by_case_number(case_number: str) -> str:
    """Look up a GST AAAR order by its appeal order number.

    Handles partial matches via wildcard on case_no.keyword.
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
                        {"wildcard": {"case_no.keyword": {"value": f"*{case_number.replace('*', '').replace('?', '')}*", "boost": 2}}},
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
        return f"GST case number search error: {str(e)}"


# ============================================================
# Tool 4: Party (Applicant) Name Search
# ============================================================

class PartyNameInput(BaseModel):
    party_name: str = Field(description="Name of the applicant entity to search for")
    limit: int = Field(default=10, description="Number of results to return (1-20)")


@tool(args_schema=PartyNameInput)
def gst_search_by_party_name(party_name: str, limit: int = 10) -> str:
    """Search GST AAAR orders by applicant name.

    Examples: 'Tata Motors', 'M/S Jay Jalaram Enterprises'.
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
        return f"GST party name search error: {str(e)}"


# ============================================================
# Tool 5: Date Range Search
# ============================================================

class DateRangeInput(BaseModel):
    start_date: str = Field(description="Start date in DD-MM-YYYY format")
    end_date: str = Field(description="End date in DD-MM-YYYY format")
    limit: int = Field(default=10, description="Number of results to return (1-20)")


@tool(args_schema=DateRangeInput)
def gst_search_by_date_range(start_date: str, end_date: str, limit: int = 10) -> str:
    """Search GST AAAR orders within a date range. Dates must be DD-MM-YYYY."""
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
        return f"GST date range search error: {str(e)}"


# ============================================================
# Tool 6: State / UT Filter (GST-specific)
# ============================================================

class StateSearchInput(BaseModel):
    state_ut: str = Field(description="State or UT name, e.g. 'Gujarat', 'Maharashtra', 'Karnataka'")
    query: Optional[str] = Field(default=None, description="Optional text query to narrow within the state")
    limit: int = Field(default=10, description="Number of results to return (1-20)")


@tool(args_schema=StateSearchInput)
def gst_search_by_state(state_ut: str, query: Optional[str] = None, limit: int = 10) -> str:
    """Filter GST AAAR orders by state / UT, optionally combined with a text query.

    Each state has its own AAAR; rulings are persuasive (not binding) across states,
    so state filtering matters when comparing positions.
    """
    try:
        es = get_es_client()
        must_clauses: list[dict] = [{"term": {"state_ut": state_ut}}]
        if query:
            must_clauses.append({
                "multi_match": {
                    "query": query,
                    "fields": ["full_text", "parties^2", "brief_of_order^2"],
                    "type": "best_fields",
                    "fuzziness": "AUTO",
                }
            })

        body = {
            "size": min(limit, 20),
            "query": {"bool": {"must": must_clauses}},
            "sort": [{"judgment_date": {"order": "desc"}}],
            "highlight": {
                "fields": {"full_text": {"fragment_size": 200, "number_of_fragments": 2}},
                "pre_tags": ["**"],
                "post_tags": ["**"],
            } if query else {},
            "_source": {"excludes": ["full_text"]},
        }
        result = es.search(index=INDEX_NAME, body=body)
        hits = result["hits"]["hits"]
        return _format_hits(hits, include_excerpt=bool(query))

    except Exception as e:
        return f"GST state search error: {str(e)}"


# ============================================================
# Tool 7: Case Details
# ============================================================

class CaseDetailsInput(BaseModel):
    db_id: str = Field(description="Database ID (SHA1 hash string) of the order to retrieve")


@tool(args_schema=CaseDetailsInput)
def gst_get_case_details(db_id: str) -> str:
    """Get full details of a specific GST AAAR order by its db_id.

    Use AFTER finding an order through search tools. db_id is the SHA1 string
    shown as "DB ID" in search results.
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

        return f"""## Full Order Details

**Applicant**: {src.get('parties', 'N/A')}
**Appeal Order No**: {src.get('case_no', 'N/A')}
**Order Date**: {src.get('judgment_date', 'N/A')}
**State/UT**: {src.get('state_ut', 'N/A')}
**Brief of Order**: {src.get('brief_of_order') or 'N/A'}
**Underlying AR Order**: {src.get('ar_order_no_date') or 'N/A'}
**Pages**: {src.get('page_count', 'N/A')}
**Text Status**: {src.get('text_status', 'N/A')} (extracted via {src.get('extraction_method', 'N/A')})

**PDF Links**:
{pdf_section}

**Order Text** (first 3000 characters):
{text_preview}
"""

    except Exception as e:
        return f"GST case details error: {str(e)}"
