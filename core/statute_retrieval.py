"""Statute retrieval for drafting: the sections a document turns on, from every Act it names.

WHY THIS EXISTS
---------------
Drafting used to call `search_legislation(<whole drafting instruction>)` and
keep the top 3 hits at 800 characters each. Measured on 2026-11-24 with
"Draft a Recovery application with respect to Industrial Relations Code as
well as industrial dispute act under Indian laws":

- it returned Code sections 49, 54, 48, 60 and 63 — and both live drafts
  cited exactly s.49 and s.54 as their recovery provisions. Section 59
  ("Recovery of money due from employer") never reached the writer;
- `search_legislation` picks ONE Act (the source with the highest summed
  score) and then runs a plain match inside it, so a request about two laws
  can never get both, and short rows win on length (s.1, s.39, s.102);
- explicit requests fail too: "Section 59 of the Industrial Relations Code,
  2020" returned s.102/39/1 (the Code is stored as "code number: Code 59",
  so the "section number: Section 59" boost never matches), and "Section
  33C of the Industrial Disputes Act, 1947" returned the Kerala Casual,
  Temporary and Badli Workers (Wages) Act, whose rows cite 33C;
- s.59 is 2,746 characters; the first 800 stop before its one-year
  limitation proviso and before sub-sections (2)-(5).

WHAT IT DOES
------------
For each statute the user named (UserIntent.named_acts, plus an explicit
"Section X of the Y Act" in the query):

1. resolves the Act to its corpus file by name — the central Act, not a
   State amendment or rules, and the fuller copy where the index holds two;
2. adds the Code that replaced it when it is one of the repealed labour Acts
   (core.statute_status), so "the Industrial Disputes Act" also brings the
   Industrial Relations Code;
3. fetches an explicitly named section by its `section_number`;
4. asks Flash Lite to pick, from the Acts' section headings, the provisions
   the document actually relies on (filing provision, forum, limitation,
   savings) — the same dispatcher-pick pattern as `_pick_reference_source`;
5. returns those sections in full (capped per section) with the
   law-in-force note.

When the user named no statute, one Flash Lite call asks which of the 4
Labour Codes and 29 replaced Acts (if any) the request involves; when none
does, it returns None and drafting keeps its previous retrieval unchanged.

Behind DRAFTING_STATUTE_RETRIEVAL: labour when unset; a server sets "off"
to keep the previous behaviour (see `mode`).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from functools import lru_cache

from pydantic import BaseModel, Field

from core import statute_status as ss
from core.logger import get_logger, log_time, short_err
from core.settings import ES_INDICES

log = get_logger("StatuteRetrieval")

MAX_ACTS = 4
MAX_SECTIONS = 6
SECTION_CHARS = 6000
# A definitions section is one long row (IR Code s.2 is 20,228 characters, and
# "worker" is clause (zr), near the end). Cut at 6,000 the drafter never sees
# the definition the document turns on.
DEFINITIONS_CHARS = 22000
_HEADING_CHARS = 110
_PICK_TIMEOUT_S = 15
_TOTAL_TIMEOUT_S = 30

# Words that mark a corpus file as something other than the Act itself.
_NOT_THE_ACT = {"rule", "regulation", "amendment", "scheme", "bye", "byelaw",
                "form", "notification", "ordinance", "draft", "bill"}

_NAME_RE = re.compile(
    r"(?:section|code)?\s*name:\s*(.{2,300}?)\s*(?:section provisions|code provisions|provisions|content)\s*:",
    re.IGNORECASE)


@dataclass
class ResolvedAct:
    asked: str            # the name as the user / intent gave it
    source: str           # corpus file (source.keyword)
    alternates: list[str] = field(default_factory=list)  # other copies of the same Act
    added_as_successor_of: str | None = None


@dataclass
class StatuteContext:
    hits: list[dict]      # {"content", "source", "score", "act", "section", "heading"}
    note: str
    acts: list[ResolvedAct]


def _es():
    from core.clients import get_es_client
    return get_es_client()


def _index() -> str:
    return ES_INDICES["legislation"]


def _basename(source: str) -> str:
    name = (source or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"\.csv$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"(?:_\d+|\s*\(\d+\)|\s+cleaned)+$", "", name, flags=re.IGNORECASE)
    return name.strip()


def act_display_name(source: str) -> str:
    return re.sub(r",(?=\d)", ", ", _basename(source))


def _match_score(asked: str, source: str) -> int:
    """100 = same statute; 60 = the asked name plus one qualifying word
    ("Gratuity Act" -> "Payment of Gratuity Act"); 0 = not this Act."""
    want, want_year = ss.act_tokens(ss.canonical(asked))
    have, have_year = ss.act_tokens(_basename(source))
    if not want or not have:
        return 0
    if want_year and have_year and want_year != have_year:
        return 0
    if have == want:
        return 100
    extra = have - want
    if want <= have and len(extra) == 1 and not (extra & _NOT_THE_ACT):
        return 60
    return 0


def _count(source: str) -> int:
    try:
        return _es().count(index=_index(), body={"query": {"term": {"source.keyword": source}}})["count"]
    except Exception:
        return 0


@lru_cache(maxsize=256)
def resolve_act(asked: str) -> ResolvedAct | None:
    """The corpus file holding `asked`, or None. Cached: the corpus is static."""
    name = ss.canonical(asked)
    words = " ".join(w for w in re.findall(r"[A-Za-z]+", name)
                     if w.lower() not in {"the", "of", "and", "act", "on", "in", "for"})
    if not words:
        return None
    body = {
        "size": 40,
        "_source": ["source"],
        "query": {"match": {"source": {"query": words, "operator": "and", "fuzziness": "AUTO"}}},
        "collapse": {"field": "source.keyword"},
    }
    try:
        hits = _es().search(index=_index(), body=body, request_timeout=10)["hits"]["hits"]
    except Exception as e:
        log.warning("Act resolution search failed", act=asked, error=short_err(e))
        return None
    scored = [(s, h["_source"]["source"]) for h in hits
              if (s := _match_score(asked, h["_source"]["source"]))]
    if not scored:
        log.info("Named act not found in the legislation index", act=asked)
        return None
    best = max(s for s, _ in scored)
    tied = [src for s, src in scored if s == best]
    tied.sort(key=_count, reverse=True)          # the fuller copy first
    return ResolvedAct(asked=asked, source=tied[0], alternates=tied[1:])


def _natural_key(n: str):
    m = re.match(r"(\d+)(.*)", n or "")
    return (int(m.group(1)), m.group(2).lower()) if m else (10**6, (n or "").lower())


@lru_cache(maxsize=128)
def section_headings(source: str) -> tuple[tuple[str, str], ...]:
    """((section_number, heading), ...) for one Act, in section order."""
    try:
        res = _es().search(index=_index(), request_timeout=20, body={
            "size": 1000, "_source": ["section_number", "page_content"],
            "query": {"term": {"source.keyword": source}}})
    except Exception as e:
        log.warning("Heading fetch failed", source=source[-80:], error=short_err(e))
        return ()
    seen: dict[str, str] = {}
    for h in res["hits"]["hits"]:
        sn = str(h["_source"].get("section_number") or "").strip()
        if not sn or sn in seen:
            continue
        text = " ".join((h["_source"].get("page_content") or "").split())
        m = _NAME_RE.search(text[:600])
        seen[sn] = (m.group(1) if m else text[:_HEADING_CHARS]).strip()[:_HEADING_CHARS]
    return tuple(sorted(seen.items(), key=lambda kv: _natural_key(kv[0])))


def _display_text(text: str) -> str:
    """The IR and OSH Codes are stored as "code number: Code 59 ... code name:
    ... code provisions:". A drafter shown "Code 59" may cite it that way;
    the statute calls it section 59. Display only — the index is unchanged."""
    t = " ".join((text or "").split())
    t = re.sub(r"^code number:\s*Code\s+", "section number: Section ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bcode name:", "section name:", t, count=1, flags=re.IGNORECASE)
    t = re.sub(r"\bcode provisions:", "section provisions:", t, count=1, flags=re.IGNORECASE)
    return t


def fetch_section(act: ResolvedAct, number: str) -> dict | None:
    variants = sorted({number, number.upper(), number.lower()})
    for source in [act.source, *act.alternates]:
        try:
            res = _es().search(index=_index(), request_timeout=10, body={
                "size": 1, "_source": ["section_number", "page_content", "source"],
                "query": {"bool": {"filter": [
                    {"term": {"source.keyword": source}},
                    {"terms": {"section_number.keyword": variants}}]}}})
        except Exception as e:
            log.warning("Section fetch failed", source=source[-80:], section=number,
                        error=short_err(e))
            continue
        hits = res["hits"]["hits"]
        if hits:
            s = hits[0]["_source"]
            content = _display_text(s.get("page_content") or "")
            m = _NAME_RE.search(content[:600])
            return {"content": content, "source": s.get("source") or source, "score": 1.0,
                    "act": act_display_name(act.source), "section": str(s.get("section_number")),
                    "heading": (m.group(1).strip() if m else "")}
    return None


class _Pick(BaseModel):
    key: str = Field(description="the statute key from the listing, e.g. 'A1'")
    section: str = Field(description="the section number exactly as listed")


class _Picks(BaseModel):
    picks: list[_Pick] = Field(description="most important first")


async def pick_sections(query: str, acts: list[ResolvedAct], note: str,
                        already: list[tuple[int, str]], limit: int) -> list[tuple[int, str]]:
    """[(act index, section number)] Flash Lite picks from the Acts' headings."""
    if limit <= 0:
        return []
    listing, valid = [], {}
    for i, act in enumerate(acts):
        heads = await asyncio.to_thread(section_headings, act.source)
        if not heads:
            continue
        key = f"A{i + 1}"
        valid[key] = (i, {n for n, _ in heads})
        status = ""
        if act.added_as_successor_of:
            status = f" — in force; replaced the {act.added_as_successor_of}"
        elif ss.replacement_for(act_display_name(act.source)):
            status = " — REPEALED (see LAW IN FORCE)"
        listing.append(f"### {key}: {act_display_name(act.source)}{status}")
        listing.extend(f"{key} | {n} | {h}" for n, h in heads)
    if not listing:
        return []
    from config.prompts import DRAFTING_STATUTE_PICKER_PROMPT
    from core.clients import get_gemini_flash_lite
    from langchain_core.prompts import ChatPromptTemplate
    chain = (ChatPromptTemplate.from_template(DRAFTING_STATUTE_PICKER_PROMPT)
             | get_gemini_flash_lite(temperature=0.0).with_structured_output(_Picks, include_raw=True))
    taken = "\n".join(f"- {act_display_name(acts[i].source)} section {n}" for i, n in already) or "(none)"
    with log_time(log, "Statute section picker"):
        out = await asyncio.wait_for(chain.ainvoke({
            "query": query, "law_note": note or "(no status note)", "listing": "\n".join(listing),
            "already": taken, "limit": str(limit)}), timeout=_PICK_TIMEOUT_S)
    try:
        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "pick_statute_sections", out.get("raw"))
    except Exception:
        pass
    return _valid_picks(out.get("parsed").picks if out.get("parsed") else [], valid, already, limit)


def _valid_picks(picks, valid: dict, already: list[tuple[int, str]], limit: int) -> list[tuple[int, str]]:
    """Keep picks that name a listed statute and section, once each, up to `limit`.
    Case-blind: the parser yields "33c", the index stores "33C"."""
    picked: list[tuple[int, str]] = []
    taken = {(i, n.lower()) for i, n in already}
    for p in picks:
        key = (getattr(p, "key", "") or "").strip().upper()
        if key not in valid:
            continue
        i, numbers = valid[key]
        sec = (getattr(p, "section", "") or "").strip()
        match = next((n for n in numbers if n.lower() == sec.lower()), None)
        if match and (i, match.lower()) not in taken:
            taken.add((i, match.lower()))
            picked.append((i, match))
        if len(picked) >= limit:
            break
    return picked


class _Statutes(BaseModel):
    names: list[str] = Field(description="statute names copied exactly from the list; empty if none")


async def infer_labour_statutes(query: str) -> list[str]:
    """The labour statutes a request involves when it names none.

    "Draft a claim for unpaid gratuity of a retired employee" names no Act,
    and the old retrieval returned the Khadi and Village Industries
    Commission Employees (Pension) Regulations — so neither the Code that
    governs gratuity nor the law-in-force note reached the writer. One Flash
    Lite call against the fixed list of the 4 Codes and the 29 Acts they
    replaced; [] on any failure, which leaves drafting as it was."""
    from config.prompts import DRAFTING_LABOUR_STATUTE_PROMPT
    from core.clients import get_gemini_flash_lite
    from langchain_core.prompts import ChatPromptTemplate
    allowed = [c.name for c in ss.LABOUR_CODES] + [a for c in ss.LABOUR_CODES for a in c.repealed]
    chain = (ChatPromptTemplate.from_template(DRAFTING_LABOUR_STATUTE_PROMPT)
             | get_gemini_flash_lite(temperature=0.0).with_structured_output(_Statutes, include_raw=True))
    try:
        with log_time(log, "Labour statute inference"):
            out = await asyncio.wait_for(chain.ainvoke({
                "query": query, "statutes": "\n".join(f"- {n}" for n in allowed)}),
                timeout=_PICK_TIMEOUT_S)
    except Exception as e:
        log.warning("Labour statute inference failed", error=short_err(e))
        return []
    try:
        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "infer_labour_statutes", out.get("raw"))
    except Exception:
        pass
    names = out.get("parsed").names if out.get("parsed") else []
    return [n for n in allowed if any(ss.same_statute(n, x) for x in names)][:MAX_ACTS]


def _explicit_refs(query: str) -> tuple[str | None, list[str]]:
    """("act as written", [section numbers]) for "Section X of the Y Act", else (None, [])."""
    try:
        from tools.inline.section_parser import parse_multi_section_info
        info = parse_multi_section_info(query) or {}
    except Exception:
        return None, []
    if info.get("section_type") not in {"section", "article", "rule"}:
        return None, []
    nums = [n for n in (info.get("section_numbers") or [])
            if not re.fullmatch(r"(?:1[89]\d{2}|20\d{2})", str(n))]
    return (info.get("act_name") or None), nums


async def _gather(query: str, named_acts: list[str]) -> StatuteContext | None:
    explicit_act, explicit_nums = _explicit_refs(query)
    asked: list[str] = []
    for name in [*(named_acts or []), explicit_act]:
        if name and not any(ss.same_statute(name, a) for a in asked):
            asked.append(name)
    if not asked:
        return None

    acts: list[ResolvedAct] = []
    for name in asked:
        act = await asyncio.to_thread(resolve_act, name)
        if act and all(a.source != act.source for a in acts):
            acts.append(act)
        repl = ss.replacement_for(name) or (act and ss.replacement_for(act_display_name(act.source)))
        if repl:
            code = await asyncio.to_thread(resolve_act, repl.code.name)
            if code and all(a.source != code.source for a in acts):
                acts.append(ResolvedAct(code.asked, code.source, code.alternates,
                                        added_as_successor_of=repl.old_act))
    acts = acts[:MAX_ACTS]
    if not acts:
        return None

    note = ss.law_in_force_note([*asked, *(act_display_name(a.source) for a in acts)])

    chosen: list[tuple[int, str]] = []
    if explicit_nums:
        target = next((i for i, a in enumerate(acts)
                       if explicit_act and _match_score(explicit_act, a.source)), 0)
        chosen += [(target, n) for n in explicit_nums[:MAX_SECTIONS]]
    try:
        chosen += await pick_sections(query, acts, note, chosen, MAX_SECTIONS - len(chosen))
    except Exception as e:
        log.warning("Statute section picker failed; using explicit sections only",
                    error=short_err(e))

    hits = []
    for i, n in chosen:
        hit = await asyncio.to_thread(fetch_section, acts[i], n)
        if hit:
            hits.append(hit)
    log.info("Statute retrieval for drafting",
             asked=asked, acts=[act_display_name(a.source) for a in acts],
             sections=[f"{h['act']} s.{h['section']}" for h in hits], note=bool(note))
    return StatuteContext(hits=hits, note=note, acts=acts)


def mode() -> str:
    """DRAFTING_STATUTE_RETRIEVAL: 'labour' (default) | 'off' | 'all'.

    'labour' is the default when the variable is unset, so dev runs it
    without a server .env change (user's decision, 2026-11-24). A server
    that must keep the previous behaviour sets DRAFTING_STATUTE_RETRIEVAL=off
    in its .env — production does, until the lawyers confirm the
    commencement and repeal facts in core/statute_status.py. The switch also
    selects the *_LABOUR_CODES writer prompts in config/prompts.py.

    'labour' runs this retrieval only when a statute involved is one of the
    Labour Codes or the Acts they replaced — the failure that was measured.
    'all' extends it to every named Act; it changes most drafts that name a
    statute, so it stays unused until a before/after evaluation says so.
    An unknown value is treated as 'off'."""
    import os
    m = (os.getenv("DRAFTING_STATUTE_RETRIEVAL") or "labour").strip().lower()
    return m if m in {"labour", "all", "off"} else "off"


def applies_to(names: list[str]) -> bool:
    m = mode()
    if m == "off" or not any(names):
        return False
    return m == "all" or ss.involves_labour_law(names)


def labour_acts_in(result: dict | None) -> list[str]:
    """Labour Acts / Codes among an old-path `search_legislation` result's
    sources — used when the user named no statute ("a claim for unpaid
    gratuity") but the retrieval landed on a repealed labour Act."""
    out: list[str] = []
    for h in (result or {}).get("hits", []) or []:
        name = act_display_name(h.get("source") or "")
        if name and ss.involves_labour_law([name]) and name not in out:
            out.append(name)
    return out


async def gather_statutes(query: str, named_acts: list[str] | None) -> StatuteContext | None:
    """Never raises; None means "use the previous retrieval"."""
    names = [n for n in (named_acts or []) if n]
    explicit_act, _ = _explicit_refs(query)
    if not names and not explicit_act and mode() != "off":
        names = await infer_labour_statutes(query)
    if not applies_to([*names, explicit_act or ""]):
        return None
    try:
        return await asyncio.wait_for(_gather(query, names),
                                      timeout=_TOTAL_TIMEOUT_S)
    except Exception as e:
        log.warning("Statute retrieval failed; drafting keeps its previous retrieval",
                    error=short_err(e))
        return None


def format_block(ctx: StatuteContext) -> str:
    """The "legislation" prompt block: law-in-force note, then each section in full."""
    parts = []
    if ctx.note:
        parts.append(ctx.note)
    if ctx.hits:
        parts.append("## RELEVANT LEGISLATION SECTIONS (full text, for you to read and apply; "
                     "cite them as 'Section N of the <Act>' and do not reproduce their text in "
                     "the document)")
        for h in ctx.hits:
            body = h["content"]
            cap = DEFINITIONS_CHARS if "definition" in (h.get("heading") or "").lower() else SECTION_CHARS
            if len(body) > cap:
                body = body[:cap].rstrip() + " … [section continues]"
            parts.append(f"- **Source**: `{h['act']}` — Section {h['section']}"
                         f"{(': ' + h['heading']) if h.get('heading') else ''}\n  {body}")
    return ("\n\n".join(parts) + "\n") if parts else ""
