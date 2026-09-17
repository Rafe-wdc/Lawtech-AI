"""Verify new-code statute citations against what was actually retrieved.

THE FAILURE THIS CATCHES
------------------------
Asked "Section 309 of the Indian Penal Code 1860", the system answered:

    "The corresponding provision in the Bharatiya Nyaya Sanhita, 2023, is
     Section 224 ... Section 224 retains the offence of attempt to commit
     suicide"

BNS 224 is "Threat of injury to public servant" — the counterpart of IPC 189,
nothing to do with suicide. The correct position is that BNS has NO general
counterpart to IPC 309 (attempted suicide was decriminalised); BNS 226 covers
only the narrow case of attempting suicide to compel a public servant.

The answer was internally contradictory: it correctly explained that the
Mental Healthcare Act 2017 decriminalised attempted suicide, then asserted
the new code retains it as an offence.

WHY IT HAPPENS
--------------
`New Provision:` is blank on 227 of 1,297 old-code rows (17.5%), worst in the
Evidence Act. The layout prompt MANDATED a "### New Provision: Section Y"
heading with text. Given a blank field and a format demanding a number, the
model supplies one from memory.

The prompt now has an explicit "none recorded" branch. This module is the
deterministic backstop for when the prompt is not enough — the same lesson as
the drafting doc-type flip, where a prompt rule alone did not hold.

WHAT IT DOES NOT DO
-------------------
It does not decide whether a section number is *correct in law*. It checks
one thing: does this new-code section appear anywhere in the retrieved
context? A number the pipeline never retrieved is a number the pipeline
cannot vouch for.

Old-code citations (IPC / CrPC / IEA) are NOT checked — a user asking about
IPC 309 will see 309 throughout, and the risk being addressed is invented
NEW-code counterparts.
"""
from __future__ import annotations

import re

from core.logger import get_logger

log = get_logger("StatuteCitationCheck")

# "Section 224 of the Bharatiya Nyaya Sanhita, 2023", "Section 483 BNSS",
# "s. 226 BNS". Only the three new codes — see the module docstring.
# A citation may enumerate: "Sections 437/439 of the CrPC", "Sections 316(2)
# and 318(4) of the BNS". An earlier single-number form matched only the
# number sitting directly against the act name, so it saw neither 437 nor 439
# in the caption that exposed the era bug. Every consumer below reads the
# whole list.
_SEC_ONE = r"\d+[A-Za-z]?(?:\(\d+[a-z]?\))?"
_SEC_LIST = rf"{_SEC_ONE}(?:\s*(?:,|/|&|and)\s*{_SEC_ONE})*"
_SEC_LEAD = r"(?:Sections?|Secs?\.?|S\.)"
_SEC_TOKEN_RE = re.compile(_SEC_ONE, re.I)

_NEW_CODE_CITE_RE = re.compile(
    rf"{_SEC_LEAD}\s*({_SEC_LIST})"
    r"\s*(?:of\s+)?(?:the\s+)?"
    r"(BNSS|BNS|BSA|Bhara?tiya\s+Nyaya\s+Sanhita|"
    r"Bhara?tiya\s+Nagarik\s+Suraksha\s+Sanhita|Bhara?tiya\s+Sakshya\s+Adhiniyam)",
    re.I,
)

# Section numbers as they appear in a retrieved row's header or its
# "New Provision: Section X of <new act>" cross-reference.
_RETRIEVED_SEC_RE = re.compile(
    r"Section\s+(\d+[A-Za-z]?(?:\(\d+[a-z]?\))?)\s+of\s+(?:The\s+)?"
    r"(?:Bhara?tiya|BNS|BNSS|BSA)",
    re.I,
)


def _norm(sec: str) -> str:
    """'316(2)' and '316' compare as distinct; whitespace is not significant."""
    return re.sub(r"\s+", "", (sec or "")).strip().lower()


def _base(sec: str) -> str:
    """'316(2)' -> '316'. A sub-section is grounded by its parent section."""
    return re.sub(r"\(.*", "", _norm(sec))


def retrieved_new_code_sections(context: str) -> set[str]:
    """Every new-code section number present in the retrieved context."""
    if not context:
        return set()
    out: set[str] = set()
    for m in _RETRIEVED_SEC_RE.finditer(context):
        out.add(_norm(m.group(1)))
        out.add(_base(m.group(1)))
    return out


def cited_new_code_sections(answer: str) -> list[tuple[str, str]]:
    """(section, act) pairs the answer cites from BNS / BNSS / BSA."""
    if not answer:
        return []
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for m in _NEW_CODE_CITE_RE.finditer(answer):
        act = " ".join(m.group(2).split())
        for sec in _SEC_TOKEN_RE.findall(m.group(1)):
            key = _norm(sec)
            if key not in seen:
                seen.add(key)
                out.append((sec, act))
    return out


def check(answer: str, context: str) -> dict:
    """Which cited new-code sections are absent from retrieval.

    Returns {"cited": [...], "ungrounded": [...], "retrieved": n}. Pure —
    never modifies the answer, never raises.
    """
    cited = cited_new_code_sections(answer)
    available = retrieved_new_code_sections(context)

    # Fail open ONLY when there is no context to judge against. A context
    # that IS present but contains zero new-code sections is exactly the
    # case being policed: the retrieved old-act row had a blank
    # `New Provision:` field, so any new-code section in the answer came
    # from somewhere other than the source material.
    #
    # An earlier version treated "no new-code sections retrieved" as
    # unjudgeable and passed everything — which let the Section 224 case
    # through untouched, the precise failure this module exists for.
    if not (context or "").strip():
        return {"cited": [s for s, _ in cited], "ungrounded": [],
                "retrieved": 0, "no_context": True}

    ungrounded = [
        (sec, act) for sec, act in cited
        if _norm(sec) not in available and _base(sec) not in available
    ]
    return {
        "cited": [s for s, _ in cited],
        "ungrounded": [f"Section {s} {a}" for s, a in ungrounded],
        "retrieved": len(available),
    }


def annotate(answer: str, context: str) -> tuple[str, dict]:
    """Append a caution when the answer cites a section retrieval never saw.

    Annotates rather than strips. A section number is load-bearing inside a
    sentence — deleting it leaves prose asserting a legal position with the
    provision removed, which reads as authoritative and is worse than a
    flagged citation. The reader needs to know the number is unverified, not
    to have it silently vanish.

    No retrieved sections at all means nothing to check against, so the
    answer passes untouched — the same fail-open rule the citation whitelist
    uses, for the same reason: an empty whitelist must not condemn
    everything.
    """
    if not answer:
        return answer, {"cited": [], "ungrounded": [], "retrieved": 0}
    result = check(answer, context)
    if not result["ungrounded"]:
        return answer, result

    log.warning(
        "Answer cites new-code sections absent from retrieval",
        ungrounded=result["ungrounded"][:5],
        cited=result["cited"][:8],
        retrieved_sections=result["retrieved"],
    )
    names = ", ".join(result["ungrounded"][:3])
    banner = (
        f"> ⚠ **Verify before relying on this.** {names} "
        f"{'was' if len(result['ungrounded']) == 1 else 'were'} not found in the "
        "retrieved statutory material, so the section number could not be "
        "confirmed against the source text."
    )

    # Where the retrieved rows name the counterpart outright, say so. A
    # caution the reader can act on beats one they can only worry about.
    fixes = [c for c in find_cross_pairs(answer, context) if c["correctable"]]
    if fixes:
        pairs = "; ".join(
            f"{c['cited']} appears to be {c['old_act']} "
            f"{c['cited_section']}, whose counterpart is {c['correct']}"
            for c in fixes[:3]
        )
        banner += f" The retrieved rows indicate: {pairs}."
        result["cross_paired"] = [f"{c['cited']} -> {c['correct']}" for c in fixes]
    return banner + "\n\n" + answer, result


# ---------------------------------------------------------------------------
# Cross-pairing: an OLD section number bolted to a NEW act name.
#
# THE FAILURE THIS CATCHES
# ------------------------
# A bail draft produced "Section 439 BNSS". Section 439 is the CrPC bail
# provision; the BNSS counterpart is 483. "439 BNSS" is a provision that
# does not exist in either code — the number from one, the act from the
# other. Filed as-is, it cites nothing.
#
# This is mechanical, not a question of law: the corpus row for CrPC 439
# states its own counterpart. Where retrieval supplies the mapping and the
# act families line up (IPC/BNS, CrPC/BNSS, IEA/BSA), the fix is determined
# by the source material and needs no judgement.
# ---------------------------------------------------------------------------

# Canonical abbreviation, and which era the act belongs to.
_ACTS: list[tuple[str, str, str]] = [
    # (regex alternation, canonical abbrev, era)
    (r"Bhara?tiya\s+Nagarik\s+Suraksha\s+Sanhita|BNSS", "BNSS", "new"),
    (r"Bhara?tiya\s+Nyaya\s+Sanhita|BNS", "BNS", "new"),
    (r"Bhara?tiya\s+Sakshya\s+Adhiniyam|BSA", "BSA", "new"),
    (r"Code\s+of\s+Criminal\s+Procedure|Cr\.?\s?P\.?\s?C\.?", "CrPC", "old"),
    (r"Indian\s+Penal\s+Code|IPC", "IPC", "old"),
    (r"Indian\s+Evidence\s+Act|IEA", "IEA", "old"),
]

# Which new code succeeds which old one. A number may only be rewritten
# within its own family — "439 BNS" crosses families and is a different
# mistake, so it is reported but never auto-corrected.
_SUCCESSOR = {"IPC": "BNS", "CrPC": "BNSS", "IEA": "BSA"}

_SEC = _SEC_ONE
_ACT_ALT = "|".join(pat for pat, _, _ in _ACTS)

# BNSS before BNS in _ACTS, so the longer name wins the alternation.
_ANY_CODE_CITE_RE = re.compile(
    rf"({_SEC_LEAD}\s*)({_SEC_LIST})"
    rf"(\s*(?:of\s+)?(?:the\s+)?)({_ACT_ALT})",
    re.I,
)

_ROW_OLD_RE = re.compile(
    rf"Section\s+Number:\s*Section\s+({_SEC})\s+of\s+(?:The\s+)?({_ACT_ALT})",
    re.I,
)
_ROW_NEW_RE = re.compile(
    rf"New\s+Provision:\s*Section\s+({_SEC})\s+of\s+(?:The\s+)?({_ACT_ALT})",
    re.I,
)


def _abbrev(raw: str) -> tuple[str, str]:
    """Free-text act name -> (canonical abbrev, era). ('', '') if unknown."""
    for pat, abbr, era in _ACTS:
        if re.fullmatch(pat, " ".join((raw or "").split()), re.I):
            return abbr, era
    return "", ""


def statute_pairs(context: str) -> dict[str, tuple[str, str, str]]:
    """old section -> (new section, old act, new act), from retrieved rows.

    Each newacts row opens "Section Number: Section N of <old act>" and
    carries "New Provision: Section M of <new act>". Rows are split on the
    former so a blank `New Provision:` cannot borrow the next row's number.
    """
    if not context:
        return {}
    out: dict[str, tuple[str, str, str]] = {}
    # Split so each segment holds at most one row's New Provision line.
    segments = re.split(r"(?=Section\s+Number:)", context, flags=re.I)
    for seg in segments:
        m_old = _ROW_OLD_RE.search(seg)
        if not m_old:
            continue
        m_new = _ROW_NEW_RE.search(seg)
        if not m_new:
            continue  # blank New Provision — the 227-row case, nothing to map
        old_abbr, old_era = _abbrev(m_old.group(2))
        new_abbr, new_era = _abbrev(m_new.group(2))
        if old_era != "old" or new_era != "new":
            continue
        out[_norm(m_old.group(1))] = (m_new.group(1), old_abbr, new_abbr)
    return out


def find_cross_pairs(answer: str, context: str) -> list[dict]:
    """Citations pairing an old section number with a new act name.

    A hit requires all three: retrieval saw the number as an OLD section,
    the answer attaches it to a NEW act, and that number is not itself a
    retrieved new-code section (so a number valid in both codes is left
    alone). `correctable` is True only when the act families match.
    """
    pairs = statute_pairs(context)
    if not pairs or not answer:
        return []
    new_secs = retrieved_new_code_sections(context)
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for m in _ANY_CODE_CITE_RE.finditer(answer):
        abbr, era = _abbrev(m.group(4))
        if era != "new":
            continue
        for sec in _SEC_TOKEN_RE.findall(m.group(2)):
            entry = pairs.get(_norm(sec)) or pairs.get(_base(sec))
            if not entry:
                continue
            if _norm(sec) in new_secs or _base(sec) in new_secs:
                continue  # the number is legitimately a new-code section too
            new_sec, old_abbr, new_abbr = entry
            key = (_norm(sec), abbr)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "cited": f"Section {sec} {abbr}",
                "old_act": old_abbr,
                "correct": f"Section {new_sec} {new_abbr}",
                "correct_section": new_sec,
                "cited_section": sec,
                "cited_act": abbr,
                # Same family only. "439 BNS" pairs a CrPC number with the
                # IPC's successor — real, but not a number substitution.
                "correctable": _SUCCESSOR.get(old_abbr) == abbr,
            })
    return out


def correct_cross_pairs(text: str, context: str) -> tuple[str, list[dict]]:
    """Rewrite the section number in same-family cross-paired citations.

    Used where a banner is the wrong instrument — a court draft is copied
    into a filing, so a caution at the top travels badly while a wrong
    number is a defect in the document itself. Only the digits change; the
    act name the writer chose is left as written, because which era applies
    depends on the offence date and that is not ours to decide.
    """
    if not text:
        return text, []
    found = [c for c in find_cross_pairs(text, context) if c["correctable"]]
    if not found:
        return text, []
    by_act: dict[str, dict[str, str]] = {}
    for c in found:
        by_act.setdefault(c["cited_act"], {})[_norm(c["cited_section"])] =             c["correct_section"]

    def _span(m: re.Match) -> str:
        abbr, _ = _abbrev(m.group(4))
        table = by_act.get(abbr)
        if not table:
            return m.group(0)
        # Only the numbers move; the separators, spacing and act name the
        # writer chose are preserved exactly.
        secs = _SEC_TOKEN_RE.sub(
            lambda t: table.get(_norm(t.group(0)), t.group(0)), m.group(2)
        )
        return f"{m.group(1)}{secs}{m.group(3)}{m.group(4)}"

    fixed = _ANY_CODE_CITE_RE.sub(_span, text)
    log.warning(
        "Corrected old-section-number-with-new-act citations",
        corrections=[f"{c['cited']} -> {c['correct']}" for c in found][:5],
    )
    return fixed, found


# ---------------------------------------------------------------------------
# Era mismatch: charged under the new codes, proceeding under the old one.
#
# Measured live: a bail application for offences under Sections 316(2) and
# 318(4) BNS was captioned "APPLICATION FOR REGULAR BAIL UNDER SECTIONS
# 437/439 OF THE CODE OF CRIMINAL PROCEDURE, 1973". The reference draft the
# pipeline supplied was itself a BNSS format, so the corpus was right and the
# writer overrode it from memory — CrPC numbers are far more frequent in
# training data than their BNSS counterparts.
#
# This is NOT auto-corrected. Unlike a cross-pair, "439 CrPC" is a real
# provision and a draft may name it legitimately — quoting a pre-2024
# judgment, or describing a proceeding that began before commencement.
# Telling those apart needs the surrounding argument, so the repair belongs
# to the critic (`statute_era_mismatch` in CRITIQUE_PROMPT), which reads it.
# What lives here is the measurement: a deterministic signal on every draft,
# so whether the prompt rule and the critic actually hold is a number rather
# than an impression.
# ---------------------------------------------------------------------------

# Which codes charge an offence, and which govern procedure.
_CHARGING = {"IPC": "old", "BNS": "new", "IEA": "old", "BSA": "new"}
_PROCEDURAL = {"CrPC": "old", "BNSS": "new"}


def _all_citations(text: str) -> list[tuple[str, str]]:
    """(section, canonical act abbrev) for every code citation in the text."""
    out: list[tuple[str, str]] = []
    for m in _ANY_CODE_CITE_RE.finditer(text or ""):
        abbr, _ = _abbrev(m.group(4))
        if not abbr:
            continue
        for sec in _SEC_TOKEN_RE.findall(m.group(2)):
            out.append((sec, abbr))
    return out


def era_mismatch(text: str) -> dict | None:
    """New-code charges pleaded under the old procedural code, or vice versa.

    Reported only when the charging citations are unanimous — a draft that
    names both IPC and BNS is doing the cross-referencing the prompt asks
    for, and either procedural code can be defended there.
    """
    cites = _all_citations(text)
    if not cites:
        return None
    charging = {_CHARGING[a] for _, a in cites if a in _CHARGING}
    if len(charging) != 1:
        return None
    era = charging.pop()
    stale = [(s, a) for s, a in cites
             if a in _PROCEDURAL and _PROCEDURAL[a] != era]
    if not stale:
        return None
    seen: set[tuple[str, str]] = set()
    unique = [c for c in stale if not (c in seen or seen.add(c))]
    return {
        "charging_era": era,
        "procedural_era": "old" if era == "new" else "new",
        "charging": sorted({f"{s} {a}" for s, a in cites if a in _CHARGING}),
        "stale_procedural": [f"Section {s} {a}" for s, a in unique],
    }


def log_era_mismatch(text: str, where: str = "draft") -> dict | None:
    """Measure and log; never modifies the text. Returns the finding or None."""
    try:
        found = era_mismatch(text)
    except Exception as e:  # a metric must never break a draft
        log.warning("Era-mismatch check failed", error=str(e)[:200])
        return None
    if found:
        log.warning(
            "Statute era mismatch — new-code charges pleaded under the old "
            "procedural code" if found["charging_era"] == "new" else
            "Statute era mismatch — old-code charges pleaded under the new "
            "procedural code",
            where=where,
            charging=found["charging"][:6],
            stale_procedural=found["stale_procedural"][:6],
        )
    return found
