"""Fetch and search one judge's judgments, for judge profiles and predictions.

Judges come from core.judge_registry. Two roles:
- "bench":  every judgment the judge sat on. This is what a prediction needs:
            the judge's bench decided it, whoever wrote it.
- "author": only those the judge wrote: the "<NAME>, J." line of the text
            names them, or the judge sat alone (High Court). The SC author
            field is recorded when the text has no such line, but it names
            the presiding judge too often to count as proof.

The read-only search account cannot scroll, so results are paged with
search_after on _id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, Literal

from core.judge_registry import (
    Judge, JudgeSource, bench_filter, date_range_filter, detect_author, normalize_name,
)
from core.logger import get_logger

log = get_logger("JudgeTools")

Role = Literal["bench", "author"]

MAX_DOCS = 20000
PAGE_SIZE = 500
TEXT_PAGE_SIZE = 100            # pages carrying full text stay a few MB
REQUEST_TIMEOUT_S = 60
_META_FIELDS = {
    "sci_judgments": ["parties", "case_no", "judgment_date", "bench", "judgment_by", "pdf_links", "page_count"],
    "judgments": ["petitioner_names", "respondent_names", "case_number", "judgment_date", "bench_judge_names",
                  "court_name", "source"],
}


@dataclass
class JudgeJudgment:
    index_key: str
    db_id: str
    date: str | None                 # ISO yyyy-mm-dd
    court: str
    title: str
    case_no: str
    bench: tuple[str, ...]           # judge names as recorded; () if they can't be split
    bench_size: int | None
    pdf_url: str | None = None       # SC; HC links are built from `source` when shown
    source: str = ""                 # HC file path
    text: str | None = None
    author: str | None = None        # author as written in the text, or the stand-in
    author_source: str | None = None  # "text" | "field" | "sole_bench"
    is_author: bool | None = None    # did this judge write it; None if unknown
    highlights: tuple[str, ...] = field(default=())

    @property
    def text_chars(self) -> int | None:
        return len(self.text) if self.text is not None else None


def _client(es):
    if es is not None:
        return es
    from core.clients import get_es_client
    return get_es_client()


# ================================================================
# Hit normalisation
# ================================================================

def _iso_date(value, layout_key: str) -> str | None:
    if not value:
        return None
    s = str(value)[:10]
    if layout_key == "sci_judgments" and len(s) == 10 and s[2] == "-" and s[5] == "-":
        return f"{s[6:]}-{s[3:5]}-{s[:2]}"
    return s


def _split_sc_bench(raw: str) -> tuple[str, ...]:
    """"HON'BLE MR. JUSTICE A HON'BLE MRS. JUSTICE B" -> ("A", "B"); old
    run-together benches have no separator and give ()."""
    if not raw or "HON" not in raw.upper():
        return ()
    parts = [p.strip() for p in re.split(r"HON'?BLE", raw, flags=re.I) if p.strip()]
    return tuple(n for n in (normalize_name(p) for p in parts) if n)


def _first(v):
    if isinstance(v, list):
        return v[0] if v else ""
    return v or ""


def _from_hit(hit: dict, source: JudgeSource) -> JudgeJudgment:
    src = hit.get("_source") or {}
    key = source.index_key
    text = src.get(source.layout.text_field)
    if key == "sci_judgments":
        bench = _split_sc_bench(src.get("bench") or "")
        links = src.get("pdf_links") or []
        return JudgeJudgment(
            index_key=key, db_id=str(hit["_id"]), date=_iso_date(src.get("judgment_date"), key),
            court="Supreme Court of India", title=src.get("parties") or "",
            case_no=src.get("case_no") or "", bench=bench, bench_size=len(bench) or None,
            pdf_url=(links[0].get("url") if links else None), text=text,
        )
    bench = tuple(src.get("bench_judge_names") or ())
    pet, res = _first(src.get("petitioner_names")), _first(src.get("respondent_names"))
    return JudgeJudgment(
        index_key=key, db_id=str(hit["_id"]), date=_iso_date(src.get("judgment_date"), key),
        court=src.get("court_name") or "", title=f"{pet} v. {res}" if pet or res else "",
        case_no=str(src.get("case_number") or ""), bench=bench, bench_size=len(bench) or None,
        source=src.get("source") or "", text=text,
    )


def _dedupe_key(j: JudgeJudgment) -> tuple:
    """The High Court index holds exact copies of some judgments under
    different ids (same rule as judgment_search._dedupe_key)."""
    if j.index_key != "judgments":
        return (j.index_key, j.db_id)
    parts = (j.court.lower(), j.case_no, j.date or "", j.title.split(" v. ")[0].lower())
    return parts if any(parts) else (j.index_key, j.db_id)


def _set_authorship(j: JudgeJudgment, judge: Judge, author_field: str | None) -> None:
    if j.index_key == "judgments" and j.bench_size == 1:
        # High Court judge sitting alone: theirs whatever the text seems to say
        j.author, j.author_source, j.is_author = j.bench[0], "sole_bench", True
        return
    named = detect_author(j.text)
    if named:
        j.author, j.author_source = named, "text"
        j.is_author = judge.matches_name(named)
    elif author_field:
        j.author, j.author_source = author_field, "field"   # a claim only; is_author stays None


# ================================================================
# Fetch
# ================================================================

def _iter_hits(es, source: JudgeSource, query: dict, fields: list[str],
               page_size: int, limit: int) -> Iterator[dict]:
    after, seen = None, 0
    while seen < limit:
        body = {"size": min(page_size, limit - seen), "query": query,
                "sort": [{"_id": "asc"}], "_source": fields}
        if after is not None:
            body["search_after"] = after
        hits = es.search(index=source.layout.index, body=body,
                         request_timeout=REQUEST_TIMEOUT_S)["hits"]["hits"]
        if not hits:
            return
        for h in hits:
            yield h
        seen += len(hits)
        after = hits[-1]["sort"]


def fetch_judge_judgments(
    judge: Judge,
    role: Role = "bench",
    *,
    include_text: bool = False,
    min_chars: int = 0,
    year_from: int | None = None,
    year_to: int | None = None,
    max_docs: int = MAX_DOCS,
    es=None,
) -> list[JudgeJudgment]:
    """All of a judge's judgments across the registry's sources, one per judgment.

    role="author" and min_chars need the text, so they fetch it whatever
    include_text says; with include_text=False it is dropped again before
    returning. At most max_docs are scanned per source.
    """
    es = _client(es)
    need_text = include_text or role == "author" or min_chars > 0
    out: list[JudgeJudgment] = []
    seen_keys: set[tuple] = set()
    for source in judge.sources:
        lay = source.layout
        filters = [bench_filter(source)]
        rng = date_range_filter(lay, year_from, year_to)
        if rng:
            filters.append(rng)
        fields = list(_META_FIELDS[source.index_key]) + ([lay.text_field] if need_text else [])
        n = 0
        for hit in _iter_hits(es, source, {"bool": {"filter": filters}}, fields,
                              TEXT_PAGE_SIZE if need_text else PAGE_SIZE, max_docs):
            n += 1
            j = _from_hit(hit, source)
            key = _dedupe_key(j)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            _set_authorship(j, judge, (hit.get("_source") or {}).get("judgment_by"))
            if min_chars and len(j.text or "") < min_chars:
                continue
            if role == "author" and not j.is_author:
                continue
            if not include_text:
                j.text = None
            out.append(j)
        if n >= max_docs:
            log.warning("Judge judgment scan hit the cap", judge=judge.judge_id,
                        index=lay.index, max_docs=max_docs)
    return out


def search_judge_judgments(
    judge: Judge,
    query: str,
    *,
    size: int = 10,
    year_from: int | None = None,
    year_to: int | None = None,
    es=None,
) -> list[JudgeJudgment]:
    """Keyword search inside the judgments a judge sat on, best first, with
    highlighted passages. For finding the judge's cases closest to a matter."""
    es = _client(es)
    scored: list[tuple[float, JudgeJudgment]] = []
    seen_keys: set[tuple] = set()
    for source in judge.sources:
        lay = source.layout
        filters = [bench_filter(source)]
        rng = date_range_filter(lay, year_from, year_to)
        if rng:
            filters.append(rng)
        body = {
            "size": size,
            "query": {"bool": {"must": [{"match": {lay.text_field: query}}], "filter": filters}},
            "_source": _META_FIELDS[source.index_key],
            "highlight": {"fields": {lay.text_field: {"fragment_size": 300, "number_of_fragments": 3}}},
        }
        hits = es.search(index=lay.index, body=body, request_timeout=REQUEST_TIMEOUT_S)["hits"]["hits"]
        for h in hits:
            j = _from_hit(h, source)
            key = _dedupe_key(j)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            j.highlights = tuple((h.get("highlight") or {}).get(lay.text_field) or ())
            scored.append((float(h.get("_score") or 0.0), j))
    scored.sort(key=lambda p: p[0], reverse=True)
    return [j for _, j in scored[:size]]
