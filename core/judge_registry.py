"""Judge registry: which judges we profile and how each is recorded in our indexes.

The registry (config/judge_registry.json) is curated by a person. Judge names
are spelled inconsistently in the indexes ("Dr. S. Muralidhar", "S. MURALIDHAR",
"S.MURALIDHAR"), and matching them automatically would credit one judge with
another's decisions, so every index value is listed explicitly.
scripts/build_judge_registry.py lists the candidate values to choose from and
checks the file against the live index.

Two facts about the data shape everything here (checked live, 2026-11-12):
- Supreme Court `judgment_by` often names the presiding judge, not the author.
  In recent judgments it disagreed with the "<NAME>, J." line of the text in
  about half of Vikram Nath's and 15-35% of Nagarathna's, Oka's and
  Pardiwala's, and it is empty before 2016. Authorship is therefore read from
  the text (detect_author), never from the field.
- Supreme Court `bench` is free text. Before about 2015 the names are run
  together ("R.M. LODHAMADAN B. LOKURKURIAN JOSEPH"), which a phrase query
  misses; bench_filter() adds a query that tolerates the join.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from core.settings import ES_INDICES

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "judge_registry.json"

STATUSES = ("sitting", "retired", "unknown")


# ================================================================
# Index layouts
# ================================================================

@dataclass(frozen=True)
class IndexLayout:
    """How one judgment index stores the fields we need."""
    key: str                        # ES_INDICES key
    text_field: str
    date_field: str
    date_format: str                # OpenSearch date format of date_field
    bench_kind: Literal["text", "keyword"]
    bench_field: str
    author_field: str | None = None  # keyword field naming the claimed author (see module doc)
    court_field: str | None = None   # keyword field for court filters

    @property
    def index(self) -> str:
        return ES_INDICES[self.key]


LAYOUTS: dict[str, IndexLayout] = {
    "sci_judgments": IndexLayout(
        key="sci_judgments", text_field="full_text",
        date_field="judgment_date", date_format="dd-MM-yyyy",
        bench_kind="text", bench_field="bench", author_field="judgment_by.keyword",
    ),
    "judgments": IndexLayout(
        key="judgments", text_field="page_content",
        date_field="judgment_date", date_format="yyyy-MM-dd",
        bench_kind="keyword", bench_field="bench_judge_names.keyword",
        court_field="court_name.keyword",
    ),
}


# ================================================================
# Registry model
# ================================================================

@dataclass(frozen=True)
class JudgeSource:
    """Where one judge's judgments sit in one index, as exact index values."""
    index_key: str
    bench_phrases: tuple[str, ...] = ()   # text bench (SC): phrases naming the judge
    bench_values: tuple[str, ...] = ()    # keyword bench (HC): exact name values
    court_names: tuple[str, ...] = ()     # exact court values (HC)

    @property
    def layout(self) -> IndexLayout:
        return LAYOUTS[self.index_key]


@dataclass(frozen=True)
class Judge:
    judge_id: str
    name: str
    courts: tuple[str, ...]
    status: str
    verified: bool
    aliases: tuple[str, ...]
    sources: tuple[JudgeSource, ...]

    def name_tokens(self) -> list[list[str]]:
        """Normalised tokens of the name and every alias."""
        out, seen = [], set()
        for n in (self.name, *self.aliases):
            toks = normalize_name(n).split()
            if toks and tuple(toks) not in seen:
                seen.add(tuple(toks))
                out.append(toks)
        return out

    def matches_name(self, text: str) -> bool:
        """True if `text` names this judge, e.g. an author line "Mehta, J.".
        A joint line ("J. B. PARDIWALA AND R. MAHADEVAN") names each of them."""
        aliases = self.name_tokens()
        for part in re.split(r"\s+(?:AND|&)\s+", text or "", flags=re.I):
            q = normalize_name(part).split()
            if any(_compatible(q, a) for a in aliases):
                return True
        return False


@dataclass(frozen=True)
class Resolution:
    status: Literal["resolved", "ambiguous", "unresolved"]
    judge: Judge | None = None
    candidates: tuple[Judge, ...] = ()


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class Registry:
    judges: tuple[Judge, ...]

    def get(self, judge_id: str) -> Judge | None:
        return next((j for j in self.judges if j.judge_id == judge_id), None)

    def resolve(self, text: str, court_hint: str | None = None) -> Resolution:
        """Map a judge name as a user writes it to one registry judge.

        An exact name or alias match wins. Otherwise every word the user gave
        must fit the name (initials may stand for words) and the surname must
        be among them, so "Pardiwala", "JB Pardiwala" and "Justice J.B.
        Pardiwala" all resolve, while "Justice Vikram" does not.
        """
        q = normalize_name(text).split()
        if not q:
            return Resolution("unresolved")
        pool = self.judges
        if court_hint:
            hint = court_hint.lower()
            pool = tuple(j for j in pool if any(hint in c.lower() for c in j.courts)) or pool
        exact = tuple(j for j in pool if any(q == a for a in j.name_tokens()))
        hits = exact or tuple(j for j in pool if any(_compatible(q, a) for a in j.name_tokens()))
        if len(hits) == 1:
            return Resolution("resolved", judge=hits[0], candidates=hits)
        if hits:
            return Resolution("ambiguous", candidates=hits)
        return Resolution("unresolved")


def _source_from_json(d: dict, where: str) -> JudgeSource:
    key = d.get("index")
    if key not in LAYOUTS:
        raise RegistryError(f"{where}: unknown index {key!r}; use one of {sorted(LAYOUTS)}")
    unknown = set(d) - {"index", "bench_phrases", "bench_values", "court_names"}
    if unknown:
        raise RegistryError(f"{where}: unknown source fields {sorted(unknown)}")
    src = JudgeSource(
        index_key=key,
        bench_phrases=tuple(d.get("bench_phrases") or ()),
        bench_values=tuple(d.get("bench_values") or ()),
        court_names=tuple(d.get("court_names") or ()),
    )
    if src.layout.bench_kind == "text" and not src.bench_phrases:
        raise RegistryError(f"{where}: {key} needs bench_phrases")
    if src.layout.bench_kind == "keyword" and not (src.bench_values and src.court_names):
        raise RegistryError(f"{where}: {key} needs bench_values and court_names")
    return src


def parse_registry(data: dict) -> Registry:
    judges, seen = [], set()
    for i, d in enumerate(data.get("judges") or ()):
        jid = d.get("judge_id") or ""
        where = f"judges[{i}] ({jid or 'no id'})"
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", jid):
            raise RegistryError(f"{where}: judge_id must be lower-case words joined by '-'")
        if jid in seen:
            raise RegistryError(f"{where}: duplicate judge_id")
        seen.add(jid)
        status = d.get("status", "unknown")
        if status not in STATUSES:
            raise RegistryError(f"{where}: status must be one of {STATUSES}")
        sources = tuple(_source_from_json(s, where) for s in d.get("sources") or ())
        if not sources:
            raise RegistryError(f"{where}: no sources")
        if not d.get("name") or not d.get("courts"):
            raise RegistryError(f"{where}: name and courts are required")
        judges.append(Judge(
            judge_id=jid, name=d["name"], courts=tuple(d["courts"]), status=status,
            verified=bool(d.get("verified", False)), aliases=tuple(d.get("aliases") or ()),
            sources=sources,
        ))
    return Registry(tuple(judges))


@lru_cache(maxsize=4)
def load_registry(path: Path | str = REGISTRY_PATH) -> Registry:
    with open(path, encoding="utf-8") as f:
        return parse_registry(json.load(f))


# ================================================================
# Names
# ================================================================

_HONORIFICS = {
    "HON", "HONBLE", "HONOURABLE", "HONORABLE", "THE", "MR", "MRS", "MS", "MISS",
    "SMT", "SHRI", "SRI", "DR", "JUSTICE", "JUSTICES", "JUDGE", "LORD", "CHIEF",
}
# ", J.", ", JJ", ", CJI", ", ACJ", ", Ag.C.J." after a name. Stripped before the
# words are split, so that the initial J of "J.B. Pardiwala" survives.
_JUDGE_SUFFIX = r"(?:(?:Ag|Acting)\.?[ \t]*)?(?:A\.?[ \t]*)?(?:C\.?[ \t]*)?J\.?(?:[ \t]*J\.?)?(?:[ \t]*I\.?)?"
_TITLE_SUFFIX_RE = re.compile(r",\s*" + _JUDGE_SUFFIX + r"\s*[.:]*\s*$", re.I)


def normalize_name(text: str) -> str:
    """Upper-case name words with titles, punctuation and ", J." removed.

    "HON'BLE MR. JUSTICE M.R. SHAH" and "M.R. Shah, J." both become "M R SHAH".
    """
    s = _TITLE_SUFFIX_RE.sub("", (text or "").strip())
    s = s.upper().replace("’", "'")
    s = re.sub(r"HON'?BLE", " ", s)
    s = re.sub(r"[^A-Z\s]", " ", s)
    return " ".join(t for t in s.split() if t not in _HONORIFICS)


def _token_fits(q: str, a: str) -> bool:
    return q == a or (len(q) == 1 and a.startswith(q)) or (len(a) == 1 and q.startswith(a))


def _compatible(query: list[str], alias: list[str]) -> bool:
    """Every query word fits a word of the alias, and the alias surname is given."""
    if not query or not alias or alias[-1] not in query:
        return False
    words = []
    for t in query:
        # "JB" / "KV" written for J.B. / K.V.: split short unknown words into initials
        words.extend(list(t) if len(t) <= 3 and t not in alias else [t])
    return all(any(_token_fits(w, a) for a in alias) for w in words)


# The author line of an Indian judgment: "J.B. PARDIWALA, J.:", "Mehta, J.",
# "SANJIV KHANNA, CJI.", "SANJAY KAROL J." on a line of its own near the top.
# Signature blocks ("....................J." with the name below) never match,
# nor do advocates' names that end in J ("SRI.E.S.SANEEJ"): without a comma the
# J must stand apart.
_AUTHOR_LINE_RE = re.compile(
    r"^[ \t]*([A-Z][A-Za-z.'\- ]{1,60}?)(?:[ \t]*,[ \t]*|[ \t]+)" + _JUDGE_SUFFIX + r"[ \t]*[.:]*[ \t]*$", re.M,
)
# The JUDGMENT / ORDER heading the author line follows. Parties above it can
# look like author lines ("NIKSON J.", "JOHNSON C.J." in Kerala cause titles).
_HEADING_RE = re.compile(
    r"^[ \t]*(?:COMMON[ \t]+)?(?:J[ \t]*U[ \t]*D[ \t]*G[ \t]*E?[ \t]*M[ \t]*E[ \t]*N[ \t]*T|O[ \t]*R[ \t]*D[ \t]*E[ \t]*R)[ \t]*$",
    re.M,
)
AUTHOR_SCAN_CHARS = 12000


def detect_author(text: str | None) -> str | None:
    """Name on the judgment's first "<NAME>, J." line, as written; None if absent.

    Read from the JUDGMENT / ORDER heading on when there is one. Separate
    opinions repeat the pattern further down; the first is the lead author.
    """
    if not text:
        return None
    # Many SC texts space every word with U+00A0 ("M.R.\xa0SHAH,\xa0J.").
    window = re.sub(r"[^\S\n]", " ", text[:AUTHOR_SCAN_CHARS])
    heading = _HEADING_RE.search(window)
    if heading:
        window = window[heading.end():]
    for m in _AUTHOR_LINE_RE.finditer(window):
        name = m.group(1).strip(" .")
        if len(normalize_name(name).replace(" ", "")) >= 3:
            return name
    return None


# ================================================================
# Query filters
# ================================================================

def _phrase_tokens(phrase: str) -> list[str]:
    """Approximate the standard analyzer: lower-case, "M.R." -> "m.r"."""
    return re.findall(r"[a-z0-9]+(?:\.[a-z0-9]+)*", phrase.lower())


def _joined_phrase_query(field: str, tokens: list[str]) -> dict:
    """Phrase query that still matches when the first or last word is fused
    to a neighbouring judge's name, as in old SC benches ("LOKURKURIAN JOSEPH")."""
    clauses = [{"span_multi": {"match": {"wildcard": {field: {"value": f"*{tokens[0]}"}}}}}]
    clauses += [{"span_term": {field: t}} for t in tokens[1:-1]]
    clauses.append({"span_multi": {"match": {"wildcard": {field: {"value": f"{tokens[-1]}*"}}}}})
    return {"span_near": {"clauses": clauses, "slop": 0, "in_order": True}}


def bench_filter(source: JudgeSource) -> dict:
    """Query clause: judgments where this judge sat on the bench."""
    lay = source.layout
    if lay.bench_kind == "keyword":
        return {"bool": {"filter": [
            {"terms": {lay.bench_field: list(source.bench_values)}},
            {"terms": {lay.court_field: list(source.court_names)}},
        ]}}
    should = []
    for p in source.bench_phrases:
        should.append({"match_phrase": {lay.bench_field: p}})
        toks = _phrase_tokens(p)
        if len(toks) >= 2:
            should.append(_joined_phrase_query(lay.bench_field, toks))
    return {"bool": {"should": should, "minimum_should_match": 1}}


def date_range_filter(layout: IndexLayout, year_from: int | None, year_to: int | None) -> dict | None:
    if year_from is None and year_to is None:
        return None
    rng: dict = {"format": layout.date_format}
    if year_from is not None:
        rng["gte"] = "01-01-%d" % year_from if layout.date_format == "dd-MM-yyyy" else "%d-01-01" % year_from
    if year_to is not None:
        rng["lte"] = "31-12-%d" % year_to if layout.date_format == "dd-MM-yyyy" else "%d-12-31" % year_to
    return {"range": {layout.date_field: rng}}
