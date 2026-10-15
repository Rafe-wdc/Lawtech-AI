"""Shared tools for tribunal order search (`tribunal_judgements` index).

Six tools over the tribunal index built by scripts/tribunal/: the tribunal
documents migrated from judgements_v2 (CESTAT/CEGAT, CAT, CIC, NGT, consumer
commissions, APTEL, CLB, CERC, IPAB, AAR, Copyright Board) plus the scraped
TDSAT orders. Every search takes an optional `tribunal` filter (code or name,
see config.tribunals), because tribunals differ in jurisdiction and an NGT
order says nothing about a CESTAT question.

Forked from gst_judgment_tools.py. Differences:
  - tribunal filter on every search; tribunal_name / bench in the output
  - a member (bench) search instead of a state search
  - titles come from the parties or, for migrated documents that have
    none, from the source file name
"""

from __future__ import annotations

import re
from pathlib import PureWindowsPath
from typing import Optional

from langchain.tools import tool
from pydantic import BaseModel, Field

from config.tribunals import TRIBUNAL_BY_CODE, TRIBUNALS, Tribunal
from core.clients import get_es_client
from core.settings import ES_INDICES

INDEX_NAME = ES_INDICES["tribunal_judgments"]

_TEXT_FIELDS = [
    "full_text", "title^3", "parties^3", "petitioner_names^3",
    "respondent_names^3", "keywords^2", "acts_or_sections_invoked^2",
]

TRIBUNAL_CHOICES = ", ".join(f"{t.code} ({t.name})" for t in TRIBUNALS)


# --- Tribunal filter ---------------------------------------------------------

def resolve_tribunal(value: Optional[str]) -> Optional[Tribunal]:
    """Tribunal for a code ("NGT") or a name ("National Green Tribunal"),
    case-insensitive; a name may be partial ("green tribunal"). None when
    nothing matches, so the search runs across all tribunals."""
    if not value or not value.strip():
        return None
    v = value.strip()
    t = TRIBUNAL_BY_CODE.get(v.upper())
    if t:
        return t
    low = re.sub(r"[^a-z ]", "", v.lower())
    for t in TRIBUNALS:
        if low and (low in t.name.lower() or t.name.lower() in low):
            return t
    # Former names / folder labels, e.g. "CEGAT", "Customs, Excise and Gold Tribunal"
    for t in TRIBUNALS:
        if any(low and low in re.sub(r"[^a-z ]", "", f.lower()) for f in t.folders):
            return t
    if v.upper() == "CEGAT":
        return TRIBUNAL_BY_CODE["CESTAT"]
    return None


def _filters(tribunal: Optional[str], year_from=None, year_to=None) -> list[dict]:
    out: list[dict] = []
    t = resolve_tribunal(tribunal)
    if t:
        out.append({"term": {"tribunal_code": t.code}})
    if year_from or year_to:
        rng = {}
        if year_from:
            rng["gte"] = int(year_from)
        if year_to:
            rng["lte"] = int(year_to)
        out.append({"range": {"year": rng}})
    return out


# --- Formatting --------------------------------------------------------------

def _as_list(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x]
    return [str(v)]


def _side(names: list[str]) -> str:
    """One side of the title; NGT orders can list twenty respondent states."""
    return ", ".join(names) if len(names) <= 2 else f"{names[0]} & Ors."


def order_title(src: dict) -> str:
    """Display title: stored title / parties, else petitioner v. respondent,
    else the source file name ("X_vs_Y_on_7_May_2014.PDF" -> "X vs Y")."""
    for key in ("title", "parties"):
        if src.get(key):
            return str(src[key]).strip()
    pet, res = _as_list(src.get("petitioner_names")), _as_list(src.get("respondent_names"))
    if pet and res:
        return f"{_side(pet)} vs {_side(res)}"
    stem = PureWindowsPath(str(src.get("source") or "")).stem
    stem = re.sub(r"_on_\d{1,2}_[A-Za-z]+_\d{4}$", "", stem)
    stem = stem.replace("_", " ").strip()
    if stem:
        return stem
    return ", ".join(pet) or "Unknown"


def _pdf_url(src: dict) -> str:
    links = src.get("pdf_links") or []
    if isinstance(links, list) and links and isinstance(links[0], dict):
        return links[0].get("url") or "N/A"
    return "N/A"


def _format_hit(hit, include_excerpt=True) -> str:
    src = hit["_source"]
    score = hit.get("_score")
    bench = ", ".join(_as_list(src.get("bench_judge_names")) or _as_list(src.get("judgment_by")))
    lines = [
        f"**{order_title(src)}** (DB ID: {hit['_id']})",
        f"- Tribunal: {src.get('tribunal_name') or 'Tribunal'} ({src.get('tribunal_code') or 'N/A'})",
        f"- Case No: {src.get('case_no') or 'N/A'}",
        f"- Date: {src.get('judgment_date') or src.get('year') or 'N/A'}",
    ]
    if bench:
        lines.append(f"- Bench: {bench}")
    lines.append(f"- PDF: {_pdf_url(src)}")
    if score is not None:
        lines.append(f"- Relevance Score: {score:.2f}")
    result = "\n".join(lines)
    if include_excerpt:
        hl = hit.get("highlight", {}).get("full_text")
        if hl:
            result += "\n- Excerpt: " + " ... ".join(hl[:3])
    return result


def _format_hits(hits, include_excerpt=True) -> str:
    if not hits:
        return "No matching tribunal orders found."
    return (f"Found {len(hits)} relevant tribunal order(s):\n\n"
            + "\n\n".join(_format_hit(h, include_excerpt) for h in hits))


_HIGHLIGHT = {
    "fields": {"full_text": {"fragment_size": 300, "number_of_fragments": 3}},
    "pre_tags": ["**"], "post_tags": ["**"],
}
_NO_TEXT = {"excludes": ["full_text"]}


def _search(body: dict, label: str, include_excerpt=True) -> str:
    try:
        hits = get_es_client().search(index=INDEX_NAME, body=body)["hits"]["hits"]
        return _format_hits(hits, include_excerpt)
    except Exception as e:
        return f"Tribunal {label} error: {e}"


# --- Tool 1: topic -----------------------------------------------------------

class TopicSearchInput(BaseModel):
    query: str = Field(description="Natural-language description of the legal issue")
    tribunal: Optional[str] = Field(default=None, description=f"Optional tribunal filter: {TRIBUNAL_CHOICES}")
    top_k: int = Field(default=5, description="Number of results (1-10)")
    year_from: Optional[int] = Field(default=None, description="Filter: earliest order year")
    year_to: Optional[int] = Field(default=None, description="Filter: latest order year")


@tool(args_schema=TopicSearchInput)
def tribunal_search_by_topic(query: str, tribunal: Optional[str] = None, top_k: int = 5,
                             year_from: Optional[int] = None, year_to: Optional[int] = None) -> str:
    """Search tribunal orders by legal issue (BM25 over the full text, parties and keywords).

    Use for natural-language questions: 'carriage fee dispute between broadcaster
    and MSO', 'CENVAT credit on input services', 'environmental clearance
    violation', 'RTI personal information exemption'. Pass `tribunal` when the
    user names one (TDSAT, CESTAT, NGT, CAT, CIC, APTEL, ...)."""
    return _search({
        "size": min(max(top_k, 1), 10),
        "query": {"bool": {
            "should": [
                {"multi_match": {"query": query, "fields": _TEXT_FIELDS,
                                 "type": "best_fields", "fuzziness": "AUTO"}},
                {"multi_match": {"query": query, "fields": _TEXT_FIELDS,
                                 "type": "phrase", "slop": 3, "boost": 3}},
            ],
            "minimum_should_match": 1,
            "filter": _filters(tribunal, year_from, year_to),
        }},
        "highlight": _HIGHLIGHT,
        "_source": _NO_TEXT,
    }, "topic search")


# --- Tool 2: keyword ---------------------------------------------------------

class KeywordSearchInput(BaseModel):
    query: str = Field(description="Exact keywords or phrase, e.g. a section or regulation number")
    tribunal: Optional[str] = Field(default=None, description="Optional tribunal filter (code or name)")
    limit: int = Field(default=10, description="Number of results (1-20)")


@tool(args_schema=KeywordSearchInput)
def tribunal_search_by_keyword(query: str, tribunal: Optional[str] = None, limit: int = 10) -> str:
    """Search tribunal orders for exact words or phrases (section numbers,
    regulation / notification numbers, defined terms). Examples:
    'Section 14 of the TRAI Act', 'Rule 2(l) of CENVAT Credit Rules',
    'Section 8(1)(j)', 'Interconnection Regulations 2017'."""
    return _search({
        "size": min(max(limit, 1), 20),
        "query": {"bool": {
            "should": [
                {"match_phrase": {"full_text": {"query": query, "boost": 3}}},
                {"match": {"full_text": {"query": query}}},
                {"match_phrase": {"acts_or_sections_invoked": {"query": query, "boost": 2}}},
            ],
            "minimum_should_match": 1,
            "filter": _filters(tribunal),
        }},
        "highlight": _HIGHLIGHT,
        "_source": _NO_TEXT,
    }, "keyword search")


# --- Tool 3: case number -----------------------------------------------------

class CaseNumberInput(BaseModel):
    case_number: str = Field(description="Case / petition / appeal number, full or partial, "
                                         "e.g. 'BROADCASTING PETITION/252/2017' or '252/2017'")
    tribunal: Optional[str] = Field(default=None, description="Optional tribunal filter (code or name)")


@tool(args_schema=CaseNumberInput)
def tribunal_search_by_case_number(case_number: str, tribunal: Optional[str] = None) -> str:
    """Look up tribunal orders by case number (partial numbers match)."""
    cleaned = case_number.replace("*", "").replace("?", "").strip()
    return _search({
        "size": 10,
        "query": {"bool": {
            "should": [
                {"term": {"case_no": {"value": cleaned, "boost": 5}}},
                {"wildcard": {"case_no": {"value": f"*{cleaned.upper()}*", "boost": 2}}},
                {"wildcard": {"case_no": {"value": f"*{cleaned}*"}}},
                {"match_phrase": {"full_text": {"query": cleaned}}},
            ],
            "minimum_should_match": 1,
            "filter": _filters(tribunal),
        }},
        "_source": _NO_TEXT,
    }, "case number search", include_excerpt=False)


# --- Tool 4: party -----------------------------------------------------------

class PartyNameInput(BaseModel):
    party_name: str = Field(description="Name of a party (company, department, person)")
    tribunal: Optional[str] = Field(default=None, description="Optional tribunal filter (code or name)")
    limit: int = Field(default=10, description="Number of results (1-20)")


@tool(args_schema=PartyNameInput)
def tribunal_search_by_party_name(party_name: str, tribunal: Optional[str] = None, limit: int = 10) -> str:
    """Search tribunal orders by party name, e.g. 'Bharti Airtel', 'Star India',
    'Commissioner of Central Excise', 'Union of India'."""
    fields = ["title", "parties", "petitioner_names", "respondent_names"]
    return _search({
        "size": min(max(limit, 1), 20),
        "query": {"bool": {
            "should": [
                {"multi_match": {"query": party_name, "fields": fields, "type": "phrase", "boost": 3}},
                {"multi_match": {"query": party_name, "fields": fields, "operator": "and"}},
            ],
            "minimum_should_match": 1,
            "filter": _filters(tribunal),
        }},
        "sort": ["_score", {"judgment_date": {"order": "desc", "unmapped_type": "date"}}],
        "_source": _NO_TEXT,
    }, "party search", include_excerpt=False)


# --- Tool 5: member / bench --------------------------------------------------

class MemberInput(BaseModel):
    member_name: str = Field(description="Name of a tribunal member / chairperson, e.g. 'S.B. Sinha'")
    query: Optional[str] = Field(default=None, description="Optional issue to narrow the results")
    tribunal: Optional[str] = Field(default=None, description="Optional tribunal filter (code or name)")
    limit: int = Field(default=10, description="Number of results (1-20)")


@tool(args_schema=MemberInput)
def tribunal_search_by_member(member_name: str, query: Optional[str] = None,
                              tribunal: Optional[str] = None, limit: int = 10) -> str:
    """Find orders by a tribunal member or chairperson on the bench, optionally
    narrowed to an issue."""
    must: list[dict] = [{"multi_match": {
        "query": member_name, "fields": ["bench_judge_names", "judgment_by", "bench"],
        "operator": "and"}}]
    if query:
        must.append({"multi_match": {"query": query, "fields": _TEXT_FIELDS}})
    return _search({
        "size": min(max(limit, 1), 20),
        "query": {"bool": {"must": must, "filter": _filters(tribunal)}},
        "highlight": _HIGHLIGHT if query else {},
        "_source": _NO_TEXT,
    }, "member search", include_excerpt=bool(query))


# --- Tool 6: details ---------------------------------------------------------

class CaseDetailsInput(BaseModel):
    db_id: str = Field(description="DB ID shown in a search result")


@tool(args_schema=CaseDetailsInput)
def tribunal_get_case_details(db_id: str) -> str:
    """Read one tribunal order in full (first 6000 characters of the text) by
    its DB ID. Use after a search, on the 2-3 most relevant orders, before
    describing what the tribunal held."""
    try:
        src = get_es_client().get(index=INDEX_NAME, id=str(db_id))["_source"]
    except Exception as e:
        return f"Tribunal case details error: {e}"
    text = src.get("full_text") or ""
    preview = text[:6000] + ("..." if len(text) > 6000 else "")
    bench = ", ".join(_as_list(src.get("bench_judge_names")) or _as_list(src.get("judgment_by"))) or "N/A"
    acts = ", ".join(_as_list(src.get("acts_or_sections_invoked"))[:10]) or "N/A"
    return f"""## Tribunal Order Details

**Title**: {order_title(src)} (DB ID: {db_id})
**Tribunal**: {src.get('tribunal_name') or 'Tribunal'} ({src.get('tribunal_code') or 'N/A'})
**Case No**: {src.get('case_no') or 'N/A'}
**Date**: {src.get('judgment_date') or src.get('year') or 'N/A'}
**Bench**: {bench}
**Provisions invoked**: {acts}
**PDF**: {_pdf_url(src)}

**Order Text** (first 6000 characters):
{preview or 'N/A'}
"""
