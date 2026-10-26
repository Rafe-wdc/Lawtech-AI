"""Append a "## Judgments Cited" block with PDF links to a finished draft.

WHY THIS EXISTS
---------------
Lawyer feedback (2026-09-17) on a medical-negligence plaint: draft quality
excellent, but none of the six judgments it relied on had a PDF link. Drafts
run without the Judgment / SCI agents unless the caller sends
`cite_appendix=true` (deliberately off since July — the appendix doubled the
response), and the drafting writer cites most cases from model memory, so a
link only appeared for a Supreme Court case that happened to be retrieved.

WHAT IT DOES
------------
After the draft is final, a deterministic pass with no LLM call:

  1. extracts every "X v. Y" case name the draft cites;
  2. looks each one up by party name in the Supreme Court index, then the
     High Court index, accepting a hit only when both parties' distinctive
     words match (and the year, when both sides carry one);
  3. appends one bullet per case — with the PDF link when found, or
     "not verified in Lawttorney database" when not.

The draft itself is never modified. Any lookup failure leaves the draft
exactly as it was; a single case that errors is reported as not verified.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from core.logger import get_logger

log = get_logger("JudgmentsCited")

HEADING = "## Judgments Cited"
NOT_VERIFIED = "not verified in Lawttorney database"

# Bound the pass: a plaint cites a handful of cases, and the whole lookup
# must never hold up the response.
MAX_CASES = 20
TOTAL_TIMEOUT_S = 15.0
PER_QUERY_TIMEOUT_S = 5

# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

# A party is a run of capitalised words, initials ("V.", "P.B."), honorifics
# and a few lower-case connectors ("State of Punjab", "Director, National
# Heart Institute"). Connectors may not start or end a party.
_WORD = r"(?:M/s\.?|\(?[A-Z][\w'&.-]*\)?)"
_CONNECTOR = r"(?:of|and|the|for|&|de|du|la)"
# No comma between words: a comma after a party name starts the citation
# ("..., AIR 1969 SC 128"), not more of the name.
# Whitespace inside a case name: a hard-wrapped line break is fine, a blank
# line (a new paragraph or a heading) is not.
_SP = r"(?:[ \t]*\n[ \t]*|[ \t]+)"
_PARTY = rf"{_WORD}(?:{_SP}(?:{_CONNECTOR}{_SP})*{_WORD}){{0,9}}"
_CASE_RE = re.compile(
    rf"(?P<a>{_PARTY}){_SP}(?:v\.|vs\.?|versus){_SP}(?P<b>{_PARTY})"
)
# A reporter citation directly after the name, kept for display and year.
_CITATION_TAIL_RE = re.compile(
    r"^[*_]*,?\s*(?P<cit>"
    r"\(\d{4}\)\s*\d+\s*SCC(?:\s*OnLine\s*\w+)?\s*\d+"
    r"|\[\d{4}\]\s*\d+\s*[A-Z][\w.]*(?:\s*[A-Z][\w.]*)?\s*\d+"
    r"|AIR\s*\d{4}\s*[A-Z][\w.]*\s*\d+"
    r"|\d{4}\s*INSC\s*\d+"
    r"|\d{4}\s*SCC\s*OnLine\s*\w+\s*\d+"
    r"|[IVX]+\s*\(\d{4}\)\s*CPJ\s*\d+(?:\s*\([A-Z]+\))?"
    r")"
)
# Sentence openers the name regex can swallow ("In Jacob Mathew v. ...").
_LEADING_NOISE = {
    "in", "see", "also", "as", "the", "and", "per", "cf", "cf.", "vide",
    "relying", "following", "held", "court", "supreme", "high", "hon'ble",
    "judgment", "case", "decision", "ruling", "where", "while", "whereas",
    "similarly", "further", "moreover", "thus", "however", "therefore",
    "that", "this", "under", "reliance", "placed", "on", "again", "likewise",
    "accordingly", "notably", "recently", "earlier", "later", "hence",
    "reiterated", "affirmed", "applied", "followed", "observed",
}
# Words that carry no identity when matching parties.
_STOPWORDS = {
    "of", "and", "the", "for", "vs", "v", "versus", "anr", "ors", "another",
    "others", "dr", "mr", "mrs", "ms", "smt", "shri", "sri", "m/s", "ms.",
    "ltd", "pvt", "private", "limited", "through", "thr", "its", "retd",
}


@dataclass(frozen=True)
class CitedCase:
    petitioner: str
    respondent: str
    citation: str = ""
    # The "X v. Y" is a list / heading label ("1. **Signed Delivery Note vs.
    # Pre-Delivery Defects:**"), not a sentence citing a case. Reported only
    # when the lookup confirms a real judgment by those parties.
    label: bool = False

    @property
    def display(self) -> str:
        name = f"*{self.petitioner} v. {self.respondent}*"
        return f"{name}, {self.citation}" if self.citation else name

    @property
    def year(self) -> int | None:
        m = re.search(r"(?:\(|\[|AIR\s*|^)(\d{4})", self.citation)
        return int(m.group(1)) if m else None


# Parties written as an acronym in a draft and in full in the index, or the
# reverse ("Satender Kumar Antil v. CBI" is stored as "... VS CENTRAL BUREAU
# OF INVESTIGATION").
_ALIASES: dict[str, tuple[str, ...]] = {
    "cbi": ("central", "bureau", "investigation"),
    "ed": ("directorate", "enforcement"),
    "nia": ("national", "investigation", "agency"),
    "ncb": ("narcotics", "control", "bureau"),
    "uoi": ("union", "india"),
    "rbi": ("reserve", "bank", "india"),
    "sebi": ("securities", "exchange", "board", "india"),
    "lic": ("life", "insurance", "corporation", "india"),
    "dda": ("delhi", "development", "authority"),
}


def _raw_tokens(text: str) -> list[str]:
    """Words of `text`, with runs of single letters joined ("C.B.I." -> "cbi").

    Dots split words, so a joined initial ("PRASANTH S.DHANANKA") still yields
    "dhananka"; lone initials ("V.", "P.B.") drop out as too short.
    """
    out: list[str] = []
    run = ""
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(w) == 1 and w.isalpha():
            run += w
            continue
        if run:
            out.append(run)
            run = ""
        out.append(w)
    if run:
        out.append(run)
    return out


def _tokens(text: str) -> set[str]:
    """Distinctive words of a party name, with acronyms and their expansions
    both present so either spelling matches the other."""
    words = {w for w in _raw_tokens(text)
             if (len(w) > 2 or w in _ALIASES) and w not in _STOPWORDS}
    for acr, expansion in _ALIASES.items():
        if acr in words or set(expansion) <= words:
            words.add(acr)
            words.update(expansion)
    return words


def _query_forms(text: str) -> list[str]:
    """Query strings for the index: the name as written, and with acronyms
    swapped for their expansions (and vice versa)."""
    base = {w for w in _raw_tokens(text)
            if (len(w) > 2 or w in _ALIASES) and w not in _STOPWORDS}
    expanded, contracted = set(base), set(base)
    for acr, expansion in _ALIASES.items():
        if acr in base:
            expanded.discard(acr)
            expanded.update(expansion)
        if set(expansion) <= base:
            contracted.difference_update(expansion)
            contracted.add(acr)
    forms = []
    for f in (base, expanded, contracted):
        q = " ".join(sorted(f))
        if q and q not in forms:
            forms.append(q)
    return forms


# Abbreviations that end in a full stop inside a party name.
_ABBREVIATIONS = {"ltd", "pvt", "anr", "ors", "co", "corpn", "inc", "retd",
                  "dr", "mr", "mrs", "smt", "st", "bros", "govt", "m/s", "no"}


def _trim_party(party: str, leading: bool) -> str:
    words = party.replace("*", "").replace("_", "").split()
    # A full stop after an ordinary word ends the sentence, so the party ends
    # there ("State of Punjab. Section 138 ..."). Initials ("P.B.", "V.") and
    # abbreviations ("Pvt. Ltd.") are part of the name.
    for i, w in enumerate(words):
        bare = w.rstrip(".").strip("()").lower()
        if w.endswith(".") and len(bare) > 2 and "." not in bare and bare not in _ABBREVIATIONS:
            words = words[:i + 1]
            words[-1] = words[-1].rstrip(".")
            break
    if leading:
        while words and words[0].lower().strip(",.") in _LEADING_NOISE:
            words.pop(0)
    opened = sum(w.count("(") - w.count(")") for w in words)
    if opened > 0:
        words[-1] += ")" * opened
    while words and words[-1].lower().strip(",") in {"of", "and", "the", "for", "&"}:
        words.pop()
    return " ".join(words).strip(" ,;:")


# A draft's own cause title ("Mrs. Anjali Deshmukh .....Plaintiff v. XYZ
# Hospital & Anr. .....Defendants") has the same "X v. Y" shape as a citation.
# Role labels and dot leaders only ever appear in a cause title.
_ROLE_OR_LEADER_RE = re.compile(
    r"\.{2,}|\b(?:plaintiffs?|defendants?|petitioners?|respondents?|applicants?|"
    r"appellants?|complainants?|accused|opposite\s+part(?:y|ies)|deponent)\b",
    re.IGNORECASE,
)


# A label line: only list / heading markers and emphasis before the "X v. Y",
# and a colon within a few words after it.
_LABEL_BEFORE_RE = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+)?(?:(?:\d+|[a-z]|[ivx]+)[.)][ \t]+|[-*+][ \t]+)?[*_]*[ \t]*$",
                              re.IGNORECASE)
_LABEL_AFTER_RE = re.compile(r"^[^\n:]{0,60}?[*_]*:")


def _is_label(text: str, start: int, end: int) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    return bool(_LABEL_BEFORE_RE.match(text[line_start:start])
                and _LABEL_AFTER_RE.match(text[end:end + 70]))


# Is an "X v. Y" label a comparison of two concepts ("Pleadings v. Evidence",
# "Factum v. Validity") rather than a case? Any party marker, an all-caps
# name, initials or "(Dead)" means a case; otherwise a concept needs an
# abstract-noun word. Checked on 94 hand-classified prod labels (32/35
# concepts, 0/58 cases wrong) and 54 unseen ones (31/34, 0/20).
_PARTY_MARKERS = {
    "state", "union", "india", "govt", "government", "commissioner", "corporation",
    "association", "authority", "ltd", "limited", "pvt", "co", "company", "bank", "board",
    "bureau", "cbi", "society", "trust", "council", "university", "ors", "anr", "smt",
    "shri", "sri", "dr", "justice", "municipal", "collector", "director", "police",
    "inspector", "secretary", "industries", "enterprises", "motors", "services",
    "development", "welfare", "corpn", "inc", "llp", "bros", "trading", "nct", "uoi",
    "officer", "tribunal", "registrar", "panchayat", "hospital", "insurance", "railway",
}
_CONCEPT_SUFFIXES = ("tion", "sion", "ity", "ment", "ence", "ance", "ness", "ship")
_GENERIC_CONCEPTS = {
    "scope", "form", "test", "evidence", "bar", "law", "fact", "status", "right",
    "title", "property", "intent", "strategy", "capacity", "defect", "consent", "demand",
    "refund", "custody", "standard", "contract", "pleading", "document", "record", "memo",
    "ground", "source", "matter", "factum", "validity", "income", "search", "project",
    "member", "buyer", "name", "case", "remedy", "liability", "duty", "burden", "proof",
    "procedure", "jurisdiction", "limitation", "presumption",
}


def _concept_label(petitioner: str, respondent: str) -> bool:
    sides = (petitioner, respondent)
    for side in sides:
        letters = re.sub(r"[^A-Za-z]", "", side)
        if letters and letters.isupper() and len(letters) > 3:
            return False
        if re.search(r"\b[A-Z]\.\s*[A-Z]\.|\((?:dead|d|retd\.?)\)", side, re.I):
            return False
        if {w.lower() for w in re.findall(r"[A-Za-z]+", side)} & _PARTY_MARKERS:
            return False
    for w in (w.lower() for s in sides for w in re.findall(r"[A-Za-z]{3,}", s)):
        stem = w[:-1] if w.endswith("s") and len(w) > 4 else w
        if stem.endswith("ing"):
            if len(stem) >= 8:                  # "Encouraging"; not the name "Narsing"
                return True
        elif stem.endswith(_CONCEPT_SUFFIXES) or stem in _GENERIC_CONCEPTS:
            return True
    return False


def extract_cited_cases(text: str, own_parties_text: str = "") -> list[CitedCase]:
    """Every distinct "X v. Y" case cited in `text`, in order of first mention.

    `own_parties_text` is the user's request: a "case" whose two parties both
    come from it is the draft's own matter, not an authority.
    """
    out: list[CitedCase] = []
    seen: list[tuple[set[str], set[str]]] = []
    own = _tokens(own_parties_text)
    for m in _CASE_RE.finditer(text or ""):
        if _ROLE_OR_LEADER_RE.search(m.group(0)):
            continue
        a = _trim_party(m.group("a"), leading=True)
        b = _trim_party(m.group("b"), leading=False)
        ta, tb = _tokens(a), _tokens(b)
        if not ta or not tb:
            continue
        if own and ta <= own and tb <= own:
            continue
        tail = _CITATION_TAIL_RE.match(text[m.end():m.end() + 80])
        citation = re.sub(r"\s+", " ", tail.group("cit")).strip() if tail else ""
        label = not citation and _is_label(text, m.start(), m.end())
        # Same case mentioned again, possibly with a stray sentence word in
        # front ("Again Jacob Mathew v. ...") or a shorter name. A citing
        # sentence anywhere makes it a citation, even if a label came first.
        dup = next((i for i, (sa, sb) in enumerate(seen)
                    if tb == sb and (ta <= sa or sa <= ta)), None)
        if dup is not None:
            if not label and out[dup].label:
                out[dup] = CitedCase(out[dup].petitioner, out[dup].respondent,
                                     out[dup].citation or citation, label=False)
            continue
        seen.append((ta, tb))
        out.append(CitedCase(a, b, citation, label=label))
        if len(out) >= MAX_CASES:
            break
    return out


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FoundJudgment:
    court: str
    date: str
    pdf_url: str | None


def _parties_match(case: CitedCase, pet_text: str, resp_text: str) -> bool:
    """Both parties' distinctive words appear on the matching side of the hit.

    Petitioner words must all appear; respondent words at least two thirds.
    Guards against look-alikes the full-text match returns, e.g.
    "MATHEW JACOB vs PUNJAB NATIONAL BANK" for Jacob Mathew v. State of Punjab.
    """
    pet, resp = _tokens(pet_text), _tokens(resp_text)
    want_a, want_b = _tokens(case.petitioner), _tokens(case.respondent)
    if not want_a or not want_b or not want_a <= pet:
        return False
    return len(want_b & resp) * 3 >= len(want_b) * 2


def _year_ok(case: CitedCase, hit_year: int | None) -> bool:
    # Reported citations can trail the decision by a year.
    return case.year is None or hit_year is None or abs(case.year - hit_year) <= 1


# Common surnames ("Desai v. State of Maharashtra") match thousands of rows;
# rank on the full cited name and the cited year so the right one is in reach.
_CANDIDATES = 20


def _party_query(case: CitedCase, pet_field: str, resp_field: str) -> dict:
    should = [
        {"match_phrase": {pet_field: {"query": case.petitioner, "boost": 5}}},
        {"match_phrase": {resp_field: {"query": case.respondent, "boost": 3}}},
    ]
    def _any_form(field: str, text: str, **opts) -> dict:
        return {"bool": {"minimum_should_match": 1, "should": [
            {"match": {field: {"query": q, **opts}}} for q in _query_forms(text)]}}

    return {"bool": {
        "must": [
            _any_form(pet_field, case.petitioner, operator="and"),
            _any_form(resp_field, case.respondent, minimum_should_match="60%"),
        ],
        "should": should,
    }}


def _lookup_sci(es, case: CitedCase) -> FoundJudgment | None:
    from core.settings import ES_INDICES
    body = {
        "size": _CANDIDATES,
        "_source": ["parties", "judgment_date", "pdf_links"],
        "query": _party_query(case, "parties", "parties"),
    }
    res = es.search(index=ES_INDICES["sci_judgments"], body=body,
                    request_timeout=PER_QUERY_TIMEOUT_S)
    for hit in res["hits"]["hits"]:
        src = hit["_source"]
        parts = re.split(r"\s+(?:VS\.?|V\.|VERSUS)\s+", src.get("parties") or "",
                         maxsplit=1, flags=re.I)
        if len(parts) != 2 or not _parties_match(case, parts[0], parts[1]):
            continue
        date = src.get("judgment_date") or ""
        m = re.search(r"(\d{4})$", date)
        if not _year_ok(case, int(m.group(1)) if m else None):
            continue
        links = src.get("pdf_links") or []
        url = links[0].get("url") if links and isinstance(links[0], dict) else None
        return FoundJudgment("Supreme Court of India", date, url)
    return None


def _lookup_hc(es, case: CitedCase) -> FoundJudgment | None:
    from core.settings import ES_INDICES
    from tools.shared.storage_tools import generate_s3_link
    body = {
        "size": _CANDIDATES,
        "_source": ["petitioner_names", "respondent_names", "court_name", "year",
                    "judgment_date", "source"],
        "query": _party_query(case, "petitioner_names", "respondent_names"),
    }
    res = es.search(index=ES_INDICES["judgments"], body=body,
                    request_timeout=PER_QUERY_TIMEOUT_S)
    candidates = []
    for hit in res["hits"]["hits"]:
        src = hit["_source"]
        pet = src.get("petitioner_names") or []
        resp = src.get("respondent_names") or []
        pet = " ".join(pet) if isinstance(pet, list) else str(pet)
        resp = " ".join(resp) if isinstance(resp, list) else str(resp)
        if not _parties_match(case, pet, resp):
            continue
        year = src.get("year")
        year = int(year) if str(year or "").isdigit() else None
        if not _year_ok(case, year):
            continue
        candidates.append((year is not None and case.year is not None, src, pet, resp, year))
    # Prefer a hit whose year was actually checked over one with no year.
    candidates.sort(key=lambda c: not c[0])
    for _, src, pet, resp, year in candidates:
        court = str(src.get("court_name") or "").strip()
        source_file = str(src.get("source") or "")
        url = generate_s3_link.invoke({
            "court": court, "file_name": source_file, "title": f"{pet} vs {resp}",
        }) if court and source_file else None
        date = str(src.get("judgment_date") or year or "")
        court_label = ("Supreme Court of India" if court.lower() == "supreme court"
                       else court.title())
        return FoundJudgment(court_label, date, url)
    return None


def _lookup_one(case: CitedCase) -> FoundJudgment | None:
    from core.clients import get_es_client
    es = get_es_client()
    return _lookup_sci(es, case) or _lookup_hc(es, case)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_block(results: list[tuple[CitedCase, FoundJudgment | None]]) -> str:
    lines = []
    for case, found in results:
        if found is None:
            lines.append(f"- {case.display} — {NOT_VERIFIED}")
            continue
        meta = ", ".join(x for x in (found.court, found.date) if x)
        line = f"- {case.display}" + (f" — {meta}" if meta else "")
        if found.pdf_url:
            line += f" ([Judgment PDF]({found.pdf_url}))"
        else:
            line += " (in Lawttorney database; PDF not available)"
        lines.append(line)
    return f"{HEADING}\n\n" + "\n".join(lines)


async def append_judgments_cited(draft: str, lookup=None, user_query: str = "") -> str:
    """Return `draft` with a Judgments Cited block, or `draft` unchanged.

    `user_query` lets the draft's own parties be told apart from authorities.
    `lookup` is injectable for tests; production uses the ES indices.
    """
    lookup = lookup or _lookup_one
    if not draft or HEADING in draft:
        return draft
    cases = extract_cited_cases(draft, own_parties_text=user_query)
    if not cases:
        return draft

    async def _one(case: CitedCase):
        try:
            return case, await asyncio.to_thread(lookup, case)
        except Exception as e:  # one bad lookup must not sink the block
            log.warning("Judgment lookup failed — reporting as not verified",
                        case=case.display[:80], error=str(e)[:200])
            return case, None

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(_one(c) for c in cases)), timeout=TOTAL_TIMEOUT_S)
    except Exception as e:
        log.warning("Judgments Cited pass skipped — draft returned unchanged",
                    cases=len(cases), error=f"{type(e).__name__}: {e}"[:200])
        return draft

    # A heading label that reads as two concepts and is not a judgment in
    # either index is the answer's own heading ("Consent for Part Replacement
    # vs. Demand for Complete Replacement:"), not an authority - leave it out
    # rather than list it as "not verified" (prod 2026-10-25, thread 1f08702).
    # Real cases used as labels ("Vikas v. Bhagwati:") stay, verified or not.
    def _heading_not_case(c: CitedCase, f) -> bool:
        return c.label and f is None and _concept_label(c.petitioner, c.respondent)
    dropped = [c.display for c, f in results if _heading_not_case(c, f)]
    results = [(c, f) for c, f in results if not _heading_not_case(c, f)]
    if dropped:
        log.info("Judgments Cited: unverified heading labels left out", labels=dropped[:5])
    if not results:
        return draft
    cases = [c for c, _ in results]
    found = sum(1 for _, f in results if f is not None)
    linked = sum(1 for _, f in results if f is not None and f.pdf_url)
    log.info("Judgments Cited appended", cases=len(cases), found=found,
             with_pdf=linked, not_verified=len(cases) - found)
    return draft.rstrip() + "\n\n---\n\n" + render_block(results)
