"""Strict citation whitelisting for generated legal drafts — SHADOW BY DEFAULT.

WHAT THIS IS FOR
----------------
A drafting request asking for supporting case law produced 7 case citations,
none of which appeared in what the pipeline retrieved. The root cause was not
model carelessness: the Supreme Court search was failing with
"maxClauseCount is set to 1024" on long queries, and the error string was
formatted into the writer's prompt under a heading reading "## RELEVANT
SUPREME COURT JUDGMENTS". The writer was handed a promise of authority
followed by a stack trace, and filled the gap from memory.

The principle for a court filing is "no citation is better than a wrong
citation". But that switch cannot be flipped blind — deleting a real
authority from a filing is its own serious defect — so this ships in SHADOW
mode: it computes and logs what it WOULD strip, and strips nothing.

MODES
-----
    CITATION_STRIP_MODE=shadow   (default) log only, change nothing
    CITATION_STRIP_MODE=enforce  actually remove unmatched citations
    CITATION_STRIP_MODE=off      disabled entirely

Enforce must not be enabled until shadow data from real production drafts has
been reviewed by an advocate and the would-be strips classified as:
    (a) fabrication            -> correct to strip
    (b) real case from context -> WRONG to strip
    (c) real case that retrieval missed -> a retrieval bug, fix that first

KNOWN LIMITATION, MEASURED
--------------------------
The SCI index returns "No matching judgments found" for most bail-topic
queries — only some phrasings hit. So the whitelist is frequently EMPTY, and
in enforce mode that would strip every citation from those drafts. That is
category (c), and it is why shadow comes first: the shadow logs are the
evidence for whether the retrieval gap or the fabrication is the bigger
problem.
"""
from __future__ import annotations

import os
import re

from core.logger import get_logger

log = get_logger("CitationWhitelist")

SHADOW = "shadow"
ENFORCE = "enforce"
OFF = "off"


def strip_mode() -> str:
    """Current mode. Anything unrecognised falls back to shadow (safe)."""
    raw = (os.getenv("CITATION_STRIP_MODE") or SHADOW).strip().lower()
    return raw if raw in (SHADOW, ENFORCE, OFF) else SHADOW


# "X v. Y", "X vs Y", "X versus Y" — the party-name form an Indian filing uses.
# A period only ENDS a party when followed by whitespace or end-of-line —
# otherwise "State of U.P." truncates to "State of U" and no longer grounds
# against the retrieved "STATE OF UTTAR PRADESH".
_CASE_CITE_RE = re.compile(
    r"([A-Z][A-Za-z.&'\- ]{2,60}?)\s+(?:v\.?|vs\.?|versus)\s+([A-Z][A-Za-z.&'\- ]{2,60}?)"
    r"(?=\s*[,;:\)\n]|\s+\(|\.\s|\.$|\s*$)",
    re.M,
)

# Retrieved SCI hits render as: **PARTIES** (DB ID: 12345)
_SCI_PARTIES_RE = re.compile(r"\*\*([^*\n]{4,140})\*\*\s*\(DB ID:", re.I)

# Role labels a filing uses for ITS OWN parties. "Applicant v. Respondent" is
# a heading, never a case citation.
#
# Deliberately does NOT include "state", "union of india" or "state of X":
# those are the commonest real respondents in Indian criminal citations
# ("Sanjay Chandra v. CBI", "Dataram Singh v. State of Uttar Pradesh"), and
# excluding them would blind the check to most of what it exists to audit.
_ROLE_LABELS = frozenset({
    "applicant", "respondent", "petitioner", "accused", "complainant",
    "opponent", "deponent", "plaintiff", "defendant", "appellant",
    "applicant accused", "respondent state",
})

# A party name longer than this is the regex having run into prose.
_MAX_PARTY_WORDS = 9


# Signal phrases that introduce a citation. The party-name regex starts at a
# capital letter, so "As held in Sanjay Chandra v. CBI" captures party A as
# "As held in Sanjay Chandra" — the lead-in has to come off before matching,
# or a real retrieved case fails to ground and is reported as fabricated.
_LEADIN_RE = re.compile(
    r"^(?:as\s+)?(?:held|laid\s+down|observed|decided|reiterated|settled)\s+in\s+|"
    r"^(?:see\s+also|see|per|following|relied\s+upon\s+in|in\s+the\s+case\s+of|"
    r"in|cf\.?|vide|ref\.?)\s+",
    re.I,
)

# Statutory reference: "Section 316(2) of the Bharatiya Nyaya Sanhita, 2023".
_STATUTE_RE = re.compile(
    r"(?:Sections?|Secs?\.?|S\.)\s*([0-9]+[A-Za-z]*(?:\([0-9a-z]+\))?"
    r"(?:\s*(?:,|and|&)\s*[0-9]+[A-Za-z]*(?:\([0-9a-z]+\))?)*)"
    r"\s+of\s+(?:the\s+)?"
    r"([A-Z][A-Za-z'’\- ]{4,58}?"
    r"(?:Act|Sanhita|Adhiniyam|Code|Constitution|Rules)"
    r"(?:\s*,?\s*(?:19|20)\d{2})?)",
    re.I,
)

# A bare "Section 438" with no Act named — real, but unparseable into an Act.
_BARE_SECTION_RE = re.compile(
    r"(?:Sections?|Secs?\.?|S\.)\s*[0-9]+[A-Za-z]*(?:\([0-9a-z]+\))?(?!\s+of\s)",
    re.I,
)


# Lowercase words that legitimately sit INSIDE a party name. Any other
# lowercase word means the regex has run out of the citation and into prose
# ("CBI held X" -> party is "CBI"). Without this the captured party is
# polluted, dedup breaks, and grounding fails against a correct record.
_PARTY_CONNECTORS = frozenset({
    "of", "and", "the", "for", "de", "da", "van", "von", "bin", "ex", "rel",
})

# Real Indian statutes. An Act absent from RETRIEVAL is not thereby fabricated
# — retrieval covers whatever the query matched, not the statute book. Without
# this list, citing BNSS when only BNS was retrieved reads as a fabricated
# Act, and in enforce mode a correct statutory reference gets deleted from a
# filing. Only an Act in NEITHER the registry NOR this list is reported.
_KNOWN_ACTS = frozenset(_norm for _norm in (
    "bharatiya nyaya sanhita 2023", "bharatiya nagarik suraksha sanhita 2023",
    "bharatiya sakshya adhiniyam 2023", "indian penal code 1860",
    "code of criminal procedure 1973", "indian evidence act 1872",
    "code of civil procedure 1908", "constitution of india",
    "negotiable instruments act 1881", "specific relief act 1963",
    "indian contract act 1872", "transfer of property act 1882",
    "limitation act 1963", "hindu marriage act 1955",
    "protection of women from domestic violence act 2005",
    "juvenile justice care and protection of children act 2015",
    "narcotic drugs and psychotropic substances act 1985",
    "prevention of money laundering act 2002",
    "scheduled castes and scheduled tribes prevention of atrocities act 1989",
    "information technology act 2000", "companies act 2013",
    "arbitration and conciliation act 1996", "consumer protection act 2019",
    "motor vehicles act 1988", "prevention of corruption act 1988",
))


def _trim_party(party: str) -> str:
    """Cut a captured party at the first prose word.

    "CBI held X" -> "CBI";  "State of Uttar Pradesh" -> unchanged.
    """
    words = (party or "").split()
    out: list[str] = []
    for w in words:
        bare = re.sub(r"[^A-Za-z]", "", w)
        if bare and bare.islower() and bare.lower() not in _PARTY_CONNECTORS:
            break
        out.append(w)
    return " ".join(out).strip(" ,.;:&-")


def _strip_leadin(party: str) -> str:
    """Remove an introductory phrase from a captured first party."""
    out = (party or "").strip()
    for _ in range(3):          # "See also, as held in X" — peel repeatedly
        new = _LEADIN_RE.sub("", out).strip(" ,;:")
        if new == out:
            break
        out = new
    return out


def _normalise_party(name: str) -> str:
    """Fold a party name for comparison.

    Case, punctuation, honorifics and corporate suffixes vary constantly
    between a retrieved record and how a draft writes the same case
    ("C.B.I." / "CBI", "State of U.P." / "State of Uttar Pradesh").
    """
    n = (name or "").lower()
    n = re.sub(r"\b(?:m/s|shri|smt|mr|mrs|ms|dr|the)\b", " ", n)
    n = re.sub(r"[^a-z0-9]+", " ", n)
    n = re.sub(r"\b(?:and|ors|anr|others|another|etc)\b", " ", n)
    return " ".join(n.split())


def extract_draft_citations(text: str) -> list[str]:
    """Case citations the draft makes, as "A v. B" strings."""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _CASE_CITE_RE.finditer(text):
        a = _trim_party(_strip_leadin(m.group(1)))
        b = _trim_party(m.group(2))
        na, nb = _normalise_party(a), _normalise_party(b)
        # Either side being a bare role label means this is a heading
        # ("Applicant v. Respondent"), not a citation.
        if na in _ROLE_LABELS or nb in _ROLE_LABELS:
            continue
        if len(na) < 3 or len(nb) < 3:
            continue
        # An over-long "party" means the regex ran into surrounding prose.
        if len(na.split()) > _MAX_PARTY_WORDS or len(nb.split()) > _MAX_PARTY_WORDS:
            continue
        cite = f"{a} v. {b}"
        key = f"{_normalise_party(a)}|{_normalise_party(b)}"
        if key not in seen:
            seen.add(key)
            out.append(cite)
    return out


def extract_retrieved_cases(context_blocks) -> list[str]:
    """Case names the pipeline actually retrieved.

    Reads the SCI block's "**PARTIES** (DB ID: n)" form, plus any judgment
    block text. Accepts a dict of blocks or a single string.
    """
    if not context_blocks:
        return []
    if isinstance(context_blocks, str):
        blob = context_blocks
    else:
        blob = "\n".join(str(v) for v in context_blocks.values())
    names = [m.group(1).strip() for m in _SCI_PARTIES_RE.finditer(blob)]
    # HC blocks carry "A v. B" inline rather than the SCI bold form.
    names += [f"{m.group(1).strip()} v. {m.group(2).strip()}"
              for m in _CASE_CITE_RE.finditer(blob)]
    return names


def _retrieved_party_tokens(retrieved: list[str]) -> set[str]:
    """Every normalised party name appearing anywhere in retrieval."""
    toks: set[str] = set()
    for name in retrieved:
        for part in re.split(r"\s+(?:v\.?|vs\.?|versus)\s+", name, flags=re.I):
            p = _normalise_party(part)
            if len(p) >= 4 and p not in _ROLE_LABELS:
                toks.add(p)
    return toks


def _is_grounded(citation: str, party_tokens: set[str]) -> bool:
    """Does EITHER party of this citation appear in retrieval?

    Deliberately lenient — matching on one party rather than both. A draft
    routinely shortens "State of Uttar Pradesh & Anr." to "State of U.P.",
    and a strict both-parties rule would report those as fabricated. The
    asymmetry is intentional: a false "fabricated" verdict deletes a real
    authority from a filing, which is worse than letting one through.
    """
    for part in re.split(r"\s+(?:v\.?|vs\.?|versus)\s+", citation, flags=re.I):
        p = _normalise_party(part)
        if len(p) < 4:
            continue
        for tok in party_tokens:
            if p == tok or p in tok or tok in p:
                return True
    return False


def _norm_act(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def audit_statutes(draft: str, registry) -> dict:
    """Category (1) — statutory references, checked at ACT level.

    NOT auto-allowed, despite statutes being "already whitelisted". Measured
    counter-example: a draft cited "Sections 316(2) and 318(4) of the Indian
    Penal Code, 2023" four times, including in its operative heading. No such
    Act exists — the IPC is 1860 and the 2023 statute is the Bharatiya Nyaya
    Sanhita. Auto-allowing would wave through a fabricated statute defining
    what the accused is charged under, which is worse than a fabricated case.

    Section NUMBERS are not checked. The registry holds whichever chunk
    matched ("Section 1", "Section 10"), never the section the draft is
    about, so checking numbers would flag the user's own charge section.

    A statutory reference the regex cannot resolve to an Act is reported as
    `unparsed` rather than silently allowed — shadow review should see the
    parser's blind spots, not have them hidden.
    """
    if not draft:
        return {"acts_cited": [], "acts_unknown": [], "unparsed": 0}
    known = {_norm_act(r.canonical_citation) for r in (registry.all() if registry else [])}
    known |= {_norm_act(r.title) for r in (registry.all() if registry else [])}

    cited, unknown = [], []
    for m in _STATUTE_RE.finditer(draft):
        act = " ".join(m.group(2).split()).strip(" ,.")
        if act in cited:
            continue
        cited.append(act)
        na = _norm_act(act)
        if any(na in k or k in na for k in known if k):
            continue                      # retrieved for this request
        if any(na in k or k in na for k in _KNOWN_ACTS):
            continue                      # a real Act, just not retrieved
        unknown.append(act)

    # Bare "Section 438" with no Act named — real reference, unresolvable.
    unparsed = len(_BARE_SECTION_RE.findall(draft))
    return {"acts_cited": cited, "acts_unknown": unknown, "unparsed": unparsed}


def audit_citations(draft: str, context_blocks) -> dict:
    """Compare the draft's citations against what was retrieved.

    Returns a record suitable for logging and later advocate review. Never
    modifies the draft — see `apply_citation_policy` for that.
    """
    cites = extract_draft_citations(draft)
    retrieved = extract_retrieved_cases(context_blocks)
    tokens = _retrieved_party_tokens(retrieved)
    grounded, ungrounded = [], []
    for c in cites:
        (grounded if _is_grounded(c, tokens) else ungrounded).append(c)
    # Occurrence counts. Citations are DEDUPED for review — an advocate
    # classifies a case once, not once per mention — but the count travels
    # with it, because a fabricated authority repeated in eight paragraphs is
    # a different remediation problem from one mentioned in passing.
    occurrences = {}
    for c in cites:
        first_party = re.split(r"\s+(?:v\.?|vs\.?|versus)\s+", c, flags=re.I)[0]
        if len(first_party) >= 4:
            occurrences[c] = len(re.findall(re.escape(first_party), draft))
    return {
        "citations_in_draft": len(cites),
        "retrieved_cases": len(retrieved),
        "grounded": grounded,
        "ungrounded": ungrounded,
        "retrieval_empty": len(retrieved) == 0,
        "occurrences": occurrences,
    }


def apply_citation_policy(draft: str, context_blocks) -> tuple[str, dict]:
    """Audit, and in enforce mode remove ungrounded citations.

    Returns (draft, audit). In shadow and off modes the draft is returned
    unchanged — the whole point is that the decision is logged and reviewed
    before it is ever acted on.
    """
    mode = strip_mode()
    if mode == OFF or not draft:
        return draft, {"mode": OFF, "citations_in_draft": 0}

    audit = audit_citations(draft, context_blocks)
    audit["mode"] = mode

    if audit["ungrounded"]:
        log.warning(
            "CITATION_SHADOW" if mode == SHADOW else "CITATION_STRIP",
            mode=mode,
            would_strip=len(audit["ungrounded"]),
            grounded=len(audit["grounded"]),
            retrieved_cases=audit["retrieved_cases"],
            retrieval_empty=audit["retrieval_empty"],
            citations=audit["ungrounded"][:10],
        )
    else:
        log.info("CITATION_SHADOW: nothing to strip",
                 citations_in_draft=audit["citations_in_draft"],
                 retrieved_cases=audit["retrieved_cases"])

    if mode != ENFORCE:
        return draft, audit

    cleaned = draft
    for cite in audit["ungrounded"]:
        cleaned = cleaned.replace(cite, "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned, audit
