"""Strip fabricated citation provenance from a generated legal draft.

WHY THIS EXISTS
---------------
A drafting request that asked for supporting case law produced 7 case
citations. NONE of them appeared anywhere in what the pipeline retrieved —
the Supreme Court tool had returned "No matching judgments found" — so every
one came from model memory. Two carried invented retrieval identifiers:

    Parag Kishore Satoskar v. State of Jharkhand,
    Crl.A. No.-003803-003803 - 2026, decided on 12-08-2026 (DB ID: 46429)

That is worse than a bare wrong citation. A `DB ID` is the system's own
internal identifier: its presence tells a reader the case was looked up and
verified. Here it was manufactured, and the judgment date is in the future.
In a document filed in court that is unverifiable provenance presented as
verified.

SCOPE — deliberately narrow
---------------------------
This module ONLY removes signals with no legitimate use in a filing:

  * retrieval identifiers ("DB ID: 46429") — internal plumbing, never
    part of a citation
  * TODO / placeholder markers ("[citation needed]", "[verify]")
  * links to non-court domains — a filing cites the reporter or the court,
    not a blog

and REPORTS, without deleting:

  * judgments dated in the future — the citation is almost certainly
    fabricated, but deleting only the date would leave a suspect authority
    looking clean. Surfacing it is what lets an advocate catch it.

It deliberately does NOT decide whether a case is real. That needs the
retrieved-source whitelist, which requires the SCI output format to be
pinned first; enabling it against an incomplete whitelist would strip
CORRECT authorities from filings, which is a worse failure than the one
being fixed. Tracked separately.
"""
from __future__ import annotations

import re
from datetime import date, datetime

# "(DB ID: 46429)", "DB ID 46429", "[db_id: 12]" — the system's own row ids.
_RETRIEVAL_ID_RE = re.compile(
    r"[\(\[\{]?\s*\b(?:DB[\s_-]?ID|DOC[\s_-]?ID|RECORD[\s_-]?ID|"
    r"RETRIEVAL[\s_-]?ID|SOURCE[\s_-]?ID)\b\s*[:=]?\s*\d+\s*[\)\]\}]?",
    re.I,
)

# "[citation needed]", "[verify]", "[TBD]", "(citation to be verified)"
_TODO_MARKER_RE = re.compile(
    r"[\(\[]\s*(?:citation\s+(?:needed|to\s+be\s+(?:verified|confirmed))|"
    r"verify|verification\s+needed|TBD|TODO|FILL\s*IN|to\s+be\s+confirmed)"
    r"\s*[\)\]]",
    re.I,
)

# Court / official sources an Indian filing may legitimately link to.
_ALLOWED_URL_HOSTS = (
    "sci.gov.in", "api.sci.gov.in", "indiacode.nic.in", "egazette.gov.in",
    "legislative.gov.in", "ecourts.gov.in", "judgments.ecourts.gov.in",
    "highcourt", "hcservices.ecourts.gov.in", "lawttorney",
    "prsindia.org", "meity.gov.in", "nic.in", "gov.in",
)
_URL_RE = re.compile(r"https?://([^\s/)>\]]+)[^\s)>\]]*", re.I)

# "decided on 12-08-2026", "dated 2026-08-12", "decided on 12/08/2026"
_JUDGMENT_DATE_RE = re.compile(
    r"(?:decided\s+on|dated|judgment\s+dated|order\s+dated)\s*[:\-]?\s*"
    r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})",
    re.I,
)

_DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
                 "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d")


def _parse_date(raw: str) -> date | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def find_future_dated_judgments(text: str, today: date | None = None) -> list[str]:
    """Judgment dates later than today — a citation that cannot exist yet."""
    if not text:
        return []
    today = today or date.today()
    out: list[str] = []
    for m in _JUDGMENT_DATE_RE.finditer(text):
        d = _parse_date(m.group(1))
        if d and d > today:
            out.append(m.group(0).strip())
    return out


def find_non_court_urls(text: str) -> list[str]:
    """Links to domains a filing has no business citing."""
    if not text:
        return []
    bad: list[str] = []
    for m in _URL_RE.finditer(text):
        host = m.group(1).lower()
        if not any(a in host for a in _ALLOWED_URL_HOSTS):
            bad.append(m.group(0))
    return bad


def strip_fabricated_provenance(
    text: str, today: date | None = None,
) -> tuple[str, list[str]]:
    """Remove fabricated-provenance signals. Returns (cleaned, warnings).

    Never raises — a cleanup pass must not be able to fail a request that
    already produced a draft.
    """
    if not text:
        return text, []
    warnings: list[str] = []
    cleaned = text

    ids = _RETRIEVAL_ID_RE.findall(cleaned)
    n_ids = len(_RETRIEVAL_ID_RE.findall(cleaned))
    if n_ids:
        cleaned = _RETRIEVAL_ID_RE.sub("", cleaned)
        warnings.append(
            f"Removed {n_ids} internal retrieval identifier(s) (e.g. 'DB ID: …') "
            "from citations — these are system plumbing, not part of a citation."
        )

    n_todo = len(_TODO_MARKER_RE.findall(cleaned))
    if n_todo:
        cleaned = _TODO_MARKER_RE.sub("", cleaned)
        warnings.append(
            f"Removed {n_todo} unresolved placeholder marker(s) "
            "(e.g. '[citation needed]')."
        )

    bad_urls = find_non_court_urls(cleaned)
    if bad_urls:
        for u in bad_urls:
            cleaned = cleaned.replace(u, "")
        warnings.append(
            f"Removed {len(bad_urls)} link(s) to non-court domains: "
            + ", ".join(sorted({u[:60] for u in bad_urls})[:3])
        )

    # Reported, NOT deleted — see the module docstring.
    future = find_future_dated_judgments(cleaned, today=today)
    if future:
        warnings.append(
            "UNVERIFIABLE CITATION: judgment date(s) in the future — "
            + "; ".join(sorted(set(future))[:3])
            + ". A judgment cannot post-date the draft; verify these "
              "authorities before filing."
        )

    # Tidy punctuation left behind by removals: " ,", " )", doubled spaces.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\(\s*\)|\[\s*\]", "", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r",\s*\)", ")", cleaned)

    return cleaned, warnings
