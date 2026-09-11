"""Strip pleading furniture from a non-drafting answer.

An arguments / analysis request can still come back shaped as a pleading:
cause title, application number, party block, PRAYER, "Through Counsel"
sign-off, [placeholders] (advocate test 2026-09-11, and reproduced on the
Scenario agent). The prompts now forbid it; this is the guarantee. Only a
response that carries pleading furniture of TWO or more kinds is touched,
and only the furniture is removed - the argument body stays.
"""
from __future__ import annotations

import re

_CAPTION_LINE_RE = re.compile(
    r"^[ \t#*]*(?:IN THE (?:HON'BLE )?(?:SUPREME COURT|HIGH COURT|COURT OF)[^\n]*|"
    r"(?:CRIMINAL |CIVIL )?(?:MISC(?:ELLANEOUS)?\.? )?(?:\(?BAIL\)? )?(?:APPLICATION|PETITION|CASE|SUIT|APPEAL|COMPLAINT)"
    r"(?: \((?:CRL|CIVIL)\.?\))? NO\.?[^\n]*|"
    r"(?:FIR|F\.I\.R\.?) NO\.?[^\n]*|POLICE STATION[ \t]*:[^\n]*|(?:UNDER|U/S)[ \t]+SECTIONS?[^\n]*|"
    r"IN THE MATTER OF[^\n]*|BETWEEN:?[ \t]*|VERSUS[ \t]*|VS\.?[ \t]*|AND[ \t]*|"
    r"MEMORANDUM OF (?:WRITTEN )?ARGUMENTS[^\n]*|WRITTEN (?:ARGUMENTS|SUBMISSIONS)[^\n]*|"
    r"\.{3,}[ \t]*\*{0,2}(?:PETITIONER|RESPONDENT|APPLICANT|ACCUSED|COMPLAINANT|STATE)[^\n]*)[ \t#*]*$",
    re.IGNORECASE | re.MULTILINE,
)
_PRAYER_HEAD_RE = re.compile(r"^[ \t#*]*(?:\d+\.[ \t]*)?PRAYER\b[^\n]*$", re.IGNORECASE | re.MULTILINE)
_HEADING_RE = re.compile(r"^[ \t]*#{1,6}[ \t]+\S", re.MULTILINE)
_SIGNOFF_LINE_RE = re.compile(
    r"^[ \t#*]*(?:(?:ACCUSED|APPLICANT|PETITIONER|RESPONDENT|COMPLAINANT)(?:[ \t]*/[ \t]*\w+)?[ \t]+THROUGH[ \t]+COUNSEL[^\n]*|"
    r"THROUGH[ \t]+COUNSEL[^\n]*|ADVOCATE FOR THE[^\n]*|COUNSEL FOR THE[^\n]*|SD/-[^\n]*|"
    r"PLACE[ \t]*:[^\n]*|DATE[D]?[ \t]*:[^\n]*|VERIFICATION[^\n]*|DEPONENT[^\n]*|"
    r"\[(?:NAME|SIGNATURE|ENROLMENT|ENROLLMENT)[^\]]*\][^\n]*)[ \t#*]*$",
    re.IGNORECASE | re.MULTILINE,
)
_KINDS = {
    "caption": re.compile(r"(?im)^[ \t#*]*IN THE (?:HON'BLE )?(?:SUPREME COURT|HIGH COURT|COURT OF)\b"),
    "number": re.compile(r"(?im)^[ \t#*]*(?:[A-Z .()]*APPLICATION|PETITION|CASE|FIR|F\.I\.R\.?)[A-Z .()]* NO\.?\b"),
    "parties": re.compile(r"(?im)^[ \t#*]*(?:BETWEEN:?|VERSUS|\.{3,}[ \t]*\*{0,2}(?:PETITIONER|RESPONDENT|APPLICANT|ACCUSED))"),
    "prayer": re.compile(r"(?im)^[ \t#*]*(?:\d+\.[ \t]*)?PRAYER\b"),
    "signoff": re.compile(r"(?im)^[ \t#*]*(?:(?:\w+(?:[ \t]*/[ \t]*\w+)?[ \t]+)?THROUGH[ \t]+COUNSEL\b|VERIFICATION\b)"),
}
NON_DRAFTING_TASKS = frozenset({
    "Scenario", "Legal_Concepts", "Newacts", "Legislation", "Constitution", "Maxim",
    "Judgment", "SCI_Judgment", "GST_Judgment",
})


def pleading_kinds(text: str) -> list[str]:
    return [k for k, rx in _KINDS.items() if rx.search(text or "")]


def strip_pleading_furniture(text: str, task: str | None) -> tuple[str, dict]:
    """Remove caption / number / party / PRAYER / sign-off furniture from a
    non-drafting answer that carries two or more kinds of it."""
    stats = {"kinds": [], "lines_removed": 0, "prayer_removed": 0}
    if not text or task not in NON_DRAFTING_TASKS:
        return text, stats
    kinds = pleading_kinds(text)
    stats["kinds"] = kinds
    if len(kinds) < 2:
        return text, stats
    out = text
    # PRAYER section: from its heading to the next heading or end.
    m = _PRAYER_HEAD_RE.search(out)
    while m:
        nxt = _HEADING_RE.search(out, m.end())
        out = out[:m.start()] + (out[nxt.start():] if nxt else "")
        stats["prayer_removed"] += 1
        m = _PRAYER_HEAD_RE.search(out)
    before = out.count("\n")
    out = _CAPTION_LINE_RE.sub("", out)
    out = _SIGNOFF_LINE_RE.sub("", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"
    stats["lines_removed"] = max(0, before - out.count("\n"))
    return out, stats
