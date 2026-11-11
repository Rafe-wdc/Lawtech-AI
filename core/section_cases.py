"""Judgments that actually mention the section a user asks case law for.

2026-11-10: "Provide me 50 Case laws or Citation u/s 154B-29 of Maharashtra
Co-operative Society Act 1960" returned 33 cases on prod. One was about
Section 154B-29. Five were older Section 101 recovery cases, ten were
housing-society cases on other points, and the rest were unrelated Supreme
Court judgments (an assassination appeal, an MTP petition, an Arms Act
prosecution) each given an invented line on why it applied. A note then
said all 33 "bear directly upon Section 154B-29".

Two things drove it. The High Court search treated "154B-29" as loose words
and returned general co-operative cases; and the writer, asked for 50, filled
the count from whatever the Supreme Court search returned. Nothing checked
that a listed judgment is about the section at all.

This module answers that check from the judgments' own text: which judgments
in our High Court and Supreme Court indices mention the asked section, with
the passage that mentions it. The orchestrator hands the list to the writer
as the only judgments it may present as authority on that section.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.logger import get_logger
from core.settings import ES_INDICES

log = get_logger("SectionCases")

# The request must ask for case law ...
_CASE_LAW_ASK_RE = re.compile(
    r"case\s*-?\s*laws?|judg(?:e)?ments?|citations?|precedents?|rulings?|"
    r"authorit(?:y|ies)|decided\s+cases|\bcases?\b",
    re.I,
)
# ... under a named section: "u/s 154B-29", "Section 13(1)(ia)", "sec. 498A".
_SECTION_RE = re.compile(
    r"(?:\bu\s*/\s*s\.?|\bunder\s+sec(?:tion|\.)?|\bsec(?:tion|\.)?|\bs\.)\s*"
    r"(?P<sec>\d{1,4}[A-Z]{0,2}(?:\s*-\s*[A-Z]?\d{1,3}[A-Z]?)?(?:\s*\(\s*[0-9a-z]{1,4}\s*\))*)",
    re.I,
)
_ACT_AFTER_RE = re.compile(r"^\s*(?:of\s+(?:the\s+)?)?(?P<act>[^.,;?\n]{3,90}?(?:act|code|sanhita|adhiniyam)(?:\s*,?\s*\d{4})?)",
                           re.I)
_COUNT_RE = re.compile(r"\b(\d{1,3})\s+(?:case|judg|citation|precedent|ruling|authorit|decision)", re.I)

MAX_PER_INDEX = 40
PER_QUERY_TIMEOUT_S = 8
SNIPPET_CHARS = 240


@dataclass(frozen=True)
class SectionAsk:
    section: str          # as asked, normalised: "154B-29", "13(1)(ia)"
    act: str              # the act named after it, "" when none
    count: int | None     # "50 case laws" -> 50

    @property
    def distinctive(self) -> bool:
        """A section number unlikely to be some other act's section: it has a
        letter, a sub-section or a hyphen ("154B-29", "498A", "13(1)(ia)").
        A bare number ("138") is shared by many acts and is checked only when
        the act is named."""
        return bool(re.search(r"[A-Za-z(\-]", self.section))


@dataclass(frozen=True)
class SectionJudgment:
    title: str
    court: str
    date: str
    pdf_url: str | None
    snippet: str


def asked_section(query: str) -> SectionAsk | None:
    """The section a case-law request names, or None."""
    if not query or not _CASE_LAW_ASK_RE.search(query):
        return None
    m = _SECTION_RE.search(query)
    if not m:
        return None
    section = re.sub(r"\s+", "", m.group("sec"))
    act_m = _ACT_AFTER_RE.match(query[m.end():])
    act = act_m.group("act").strip() if act_m else ""
    count_m = _COUNT_RE.search(query)
    count = int(count_m.group(1)) if count_m else None
    ask = SectionAsk(section=section, act=act, count=count)
    if not ask.distinctive and not ask.act:
        return None
    return ask


def phrase_variants(section: str) -> list[str]:
    """Spellings of the section a judgment may use, as ES phrases.

    The standard analyzer splits "154B-29" into [154b, 29] and "154-B-29"
    into [154, b, 29], so both spellings are searched. A bare number is
    searched as "section N" / "s N" to avoid page and paragraph numbers.
    """
    base = section.replace(" ", "")
    m = re.match(r"^(\d+)([A-Za-z]{1,2})(.*)$", base)
    forms = {base}
    if m:
        num, letters, rest = m.groups()
        forms.add(f"{num}-{letters}{rest}")
        forms.add(f"{num} {letters}{rest}")
    if not re.search(r"[A-Za-z(\-]", base):
        forms = {f"section {base}", f"sec {base}", f"s {base}"}
    # "154-B-29" and "154 B-29" reach the index as the same tokens; one is enough.
    by_tokens: dict[tuple, str] = {}
    for f in sorted(forms):
        by_tokens.setdefault(tuple(re.findall(r"[0-9a-z]+", f.lower())), f)
    return sorted(by_tokens.values())


def _mention_re(section: str) -> re.Pattern:
    """Regex finding the section in raw judgment text, any spacing/hyphens."""
    parts = re.findall(r"\d+|[A-Za-z]+|\([0-9a-zA-Z]+\)", section)
    sep = r"[\s\-.]*"
    body = sep.join(re.escape(p) if not p.startswith("(") else r"\(\s*" + re.escape(p[1:-1]) + r"\s*\)"
                    for p in parts)
    if not re.search(r"[A-Za-z(\-]", section):
        body = r"(?:section|sec\.?|s\.|u/s\.?)\s*" + body
    return re.compile(r"(?<![\dA-Za-z])" + body + r"(?![\dA-Za-z])", re.I)


def _query(ask: SectionAsk, text_field: str) -> dict:
    should = []
    if ask.act:
        should.append({"match": {text_field: {"query": ask.act, "boost": 2}}})
    # The phrase decides membership (cached filter); the act name only ranks.
    return {"bool": {"filter": [_section_phrases(ask, text_field)], "should": should}}


def _section_phrases(ask: SectionAsk, text_field: str) -> dict:
    return {"bool": {"should": [{"match_phrase": {text_field: p}} for p in phrase_variants(ask.section)],
                     "minimum_should_match": 1}}


def _highlight(ask: SectionAsk, field: str) -> dict:
    # The passage comes back from the search server: fetching each judgment's
    # full text to find it took 30-80 s for 40 judgments. Passages are picked
    # by the section phrases only; with the act name in the highlight query
    # the server preferred passages full of "Maharashtra ... Act" and missed
    # the one naming the section.
    return {"fields": {field: {"fragment_size": SNIPPET_CHARS, "number_of_fragments": 3,
                               "pre_tags": [""], "post_tags": [""],
                               "highlight_query": _section_phrases(ask, field)}}}


def _passage(h: dict, field: str, rx: re.Pattern) -> str:
    """A passage naming the section: the first highlighted fragment that shows
    it in full, else the first fragment (the phrase filter already proved the
    judgment names the section; a fragment can split "154B" from "29")."""
    frags = [" ".join(f.split()) for f in ((h.get("highlight") or {}).get(field) or [])]
    good = [f for f in frags if rx.search(f)]
    return (good or frags or [""])[0]


def _hc_judgments(es, ask: SectionAsk, rx: re.Pattern, size: int) -> tuple[list[SectionJudgment], bool]:
    from tools.shared.storage_tools import generate_s3_link
    probe = es.search(index=ES_INDICES["judgments"], request_timeout=PER_QUERY_TIMEOUT_S, body={
        "size": 0, "track_total_hits": size + 1,
        "query": {"bool": {"filter": [_section_phrases(ask, "page_content")]}}})
    total = (probe["hits"].get("total") or {}).get("value", 0)
    if total > size:
        return [], False              # more than one page: not the complete set
    res = es.search(index=ES_INDICES["judgments"], request_timeout=PER_QUERY_TIMEOUT_S, body={
        "size": size, "query": _query(ask, "page_content"),
        "_source": ["petitioner_names", "respondent_names", "court_name", "judgment_date",
                    "source", "case_number"],
        "highlight": _highlight(ask, "page_content")})
    out, seen = [], set()
    for h in res["hits"]["hits"]:
        s = h["_source"]
        pet = (s.get("petitioner_names") or ["?"])
        resp = (s.get("respondent_names") or ["?"])
        pet = pet[0] if isinstance(pet, list) else pet
        resp = resp[0] if isinstance(resp, list) else resp
        # The index holds exact copies of some judgments under different ids.
        key = (str(s.get("court_name")).lower(), str(s.get("case_number")), s.get("judgment_date"),
               str(pet).lower())
        if key in seen:
            continue
        seen.add(key)
        snippet = _passage(h, "page_content", rx)
        title = f"{pet} v. {resp}"
        court = (s.get("court_name") or "").title()
        link = None
        try:
            if s.get("court_name") and s.get("source"):
                link = generate_s3_link.invoke({"court": s["court_name"], "file_name": s["source"],
                                                "title": title})
        except Exception:
            link = None
        out.append(SectionJudgment(title, court, s.get("judgment_date") or "", link, snippet))
    return out, total <= size


def _sc_judgments(es, ask: SectionAsk, rx: re.Pattern, size: int) -> tuple[list[SectionJudgment], bool]:
    probe = es.search(index=ES_INDICES["sci_judgments"], request_timeout=PER_QUERY_TIMEOUT_S, body={
        "size": 0, "track_total_hits": size + 1,
        "query": {"bool": {"filter": [_section_phrases(ask, "full_text")]}}})
    total = (probe["hits"].get("total") or {}).get("value", 0)
    if total > size:
        return [], False              # more than one page: not the complete set
    res = es.search(index=ES_INDICES["sci_judgments"], request_timeout=PER_QUERY_TIMEOUT_S, body={
        "size": size, "query": _query(ask, "full_text"),
        "_source": ["parties", "judgment_date", "pdf_links"],
        "highlight": _highlight(ask, "full_text")})
    out, seen = [], set()
    for h in res["hits"]["hits"]:
        s = h["_source"]
        key = ((s.get("parties") or "").lower(), s.get("judgment_date"))
        if key in seen:
            continue
        seen.add(key)
        snippet = _passage(h, "full_text", rx)
        links = s.get("pdf_links") or []
        link = links[0].get("url") if links and isinstance(links[0], dict) else None
        title = re.sub(r"\s+VS\.?\s+", " v. ", (s.get("parties") or "?").strip(), flags=re.I)
        out.append(SectionJudgment(title, "Supreme Court of India", s.get("judgment_date") or "",
                                   link, snippet))
    return out, total <= size


def find_section_judgments(ask: SectionAsk) -> tuple[list[SectionJudgment], bool]:
    """Judgments in our HC and SC indices whose text names the section.

    Returns (judgments, complete). `complete` is True only when both searches
    finished and each returned every matching judgment, i.e. the list is the
    whole of what the database holds on that section. Only then is it safe to
    tell the writer to cite nothing else; for a common section (138, 498A)
    the database holds more than can be fetched and the caller leaves the
    answer to today's pipeline. Supreme Court first, then High Courts. Never
    raises: on any failure the result is incomplete and logged.
    """
    from core.clients import get_es_client
    rx = _mention_re(ask.section)
    size = min(MAX_PER_INDEX, max(20, ask.count or 0))
    out: list[SectionJudgment] = []
    complete = True
    try:
        es = get_es_client()
    except Exception as e:
        log.warning("Section judgments skipped: no search client", error=str(e)[:160])
        return [], False
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=2)
    futures = {name: pool.submit(fn, es, ask, rx, size)
               for name, fn in (("sc", _sc_judgments), ("hc", _hc_judgments))}
    for name, fut in futures.items():
        try:
            found, whole = fut.result(timeout=PER_QUERY_TIMEOUT_S + 2)
            out.extend(found)
            complete = complete and whole
        except Exception as e:
            complete = False
            log.warning("Section judgments: index search failed", index=name,
                        error=f"{type(e).__name__}: {e}"[:160])
    # A search still running is abandoned, not waited for.
    pool.shutdown(wait=False, cancel_futures=True)
    log.info("Section judgments found", section=ask.section, act=ask.act[:60],
             asked=ask.count, found=len(out), complete=complete)
    return out, complete


def format_block(ask: SectionAsk, found: list[SectionJudgment]) -> str:
    """The checked list and its rules, appended to the writer's agent results."""
    label = f"Section {ask.section}" + (f" of the {ask.act}" if ask.act else "")
    asked = f" The user asked for {ask.count}." if ask.count else ""
    lines = [f"### JUDGMENTS IN OUR DATABASE THAT MENTION {label.upper()}",
             f"(checked against each judgment's own text; found: {len(found)}.{asked})"]
    for i, j in enumerate(found, 1):
        meta = ", ".join(x for x in (j.court, j.date) if x)
        link = f" [PDF]({j.pdf_url})" if j.pdf_url else ""
        lines.append(f"{i}. {j.title} — {meta}{link}\n   Passage: \"{j.snippet}\"")
    if not found:
        lines.append("None. No judgment in our database mentions this section.")
    lines.append(
        "\nRULES FOR THIS ANSWER (override any wish to reach the requested number):\n"
        f"- Present as case law under {label} ONLY judgments from the list above. "
        "Use each one's passage to say what it decided about the section; if the passage "
        "only quotes the section in passing (for example in a pre-deposit or revision "
        "clause), say so instead of overstating it. If the section belongs to another act "
        "in a passage, leave that judgment out.\n"
        f"- State plainly how many judgments in our database apply {label}"
        + (f", and that this is fewer than the {ask.count} asked for" if ask.count and ask.count > len(found) else "")
        + ". Never pad the list and never write that other judgments interpret this section.\n"
        "- Other judgments from the agent results may appear only under a separate heading "
        f"that says they do not apply {label}, each described only by what its retrieved text "
        "shows, and only when they help the user (for example an earlier provision the section "
        "replaced). Leave out judgments whose retrieved text has nothing to do with the request."
    )
    return "\n".join(lines)


def section_in(text: str) -> SectionAsk | None:
    """A distinctive section named anywhere in `text` (no case-law wording
    required). Used by the High Court search to try the exact phrase first."""
    m = _SECTION_RE.search(text or "")
    if not m:
        return None
    ask = SectionAsk(section=re.sub(r"\s+", "", m.group("sec")), act="", count=None)
    return ask if ask.distinctive else None


def to_sources(found: list[SectionJudgment]) -> list:
    """Registry records for the checked judgments, so the writer's
    citation-fidelity rule (cite only retrieved sources) admits them."""
    from core.source_registry import RetrievedSource
    out = []
    for i, j in enumerate(found):
        sc = j.court.lower().startswith("supreme")
        year = (re.search(r"(\d{4})", j.date) or [None, ""])[1] if j.date else ""
        citation = f"{j.title} ({year}) {j.court}".strip()
        out.append(RetrievedSource(
            id=f"sec-{'sci' if sc else 'hc'}-{i}-{abs(hash(citation)) % 100000}",
            agent="SectionCases",
            type="sci_judgment" if sc else "judgment",
            canonical_citation=citation,
            title=j.title,
            pdf_urls=[j.pdf_url] if j.pdf_url else [],
            snippet=j.snippet,
            year=year,
        ))
    return out
