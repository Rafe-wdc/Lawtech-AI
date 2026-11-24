"""Which labour statutes are in force: the four Labour Codes and the 29 Acts they replaced.

WHY THIS EXISTS
---------------
Asked on 2026-11-24 to "draft a recovery application with respect to the
Industrial Relations Code as well as the Industrial Disputes Act", dev and
prod both drafted under Section 33C(2) of the Industrial Disputes Act, 1947
"read with Sections 49 and 54 of the Code", and cited the Payment of Bonus
Act, the Payment of Gratuity Act and the Factories Act as current law. All
four Labour Codes came into force on 21 November 2025 and repealed those
Acts; the recovery provision of the Code is Section 59.

The legislation index cannot tell the drafter this. Its rows carry only
`page_content`, `section_number` and `source` — no in-force or repealed
field — and no row records the commencement date. Each Code's own repeal
section is in the index, but it says the Acts are repealed "with effect
from the date appointed in the notification", not what that date was.

So this module states it. The Act lists below are copied from the repeal
sections as stored in the index (Code on Wages s.69, Industrial Relations
Code s.104, Code on Social Security s.164, OSH Code s.143); the
commencement date is the Central Government's notification of 21 November
2025. The text is legal data for the drafter to read, not a rule applied to
the draft: nothing here edits a document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable

LABOUR_CODES_IN_FORCE_FROM = date(2025, 11, 21)


@dataclass(frozen=True)
class Code:
    name: str
    repeal_section: str
    repealed: tuple[str, ...]


LABOUR_CODES: tuple[Code, ...] = (
    Code("Industrial Relations Code, 2020", "104", (
        "Trade Unions Act, 1926",
        "Industrial Employment (Standing Orders) Act, 1946",
        "Industrial Disputes Act, 1947",
    )),
    Code("Code on Wages, 2019", "69", (
        "Payment of Wages Act, 1936",
        "Minimum Wages Act, 1948",
        "Payment of Bonus Act, 1965",
        "Equal Remuneration Act, 1976",
    )),
    Code("Code on Social Security, 2020", "164", (
        "Employee's Compensation Act, 1923",
        "Employees' State Insurance Act, 1948",
        "Employees' Provident Funds and Miscellaneous Provisions Act, 1952",
        "Employment Exchanges (Compulsory Notification of Vacancies) Act, 1959",
        "Maternity Benefit Act, 1961",
        "Payment of Gratuity Act, 1972",
        "Cine-Workers Welfare Fund Act, 1981",
        "Building and Other Construction Workers' Welfare Cess Act, 1996",
        "Unorganised Workers' Social Security Act, 2008",
    )),
    Code("Occupational Safety, Health and Working Conditions Code, 2020", "143", (
        "Factories Act, 1948",
        "Plantations Labour Act, 1951",
        "Mines Act, 1952",
        "Working Journalists and other Newspaper Employees (Conditions of Service) "
        "and Miscellaneous Provisions Act, 1955",
        "Working Journalists (Fixation of Rates of Wages) Act, 1958",
        "Motor Transport Workers Act, 1961",
        "Beedi and Cigar Workers (Conditions of Employment) Act, 1966",
        "Contract Labour (Regulation and Abolition) Act, 1970",
        "Sales Promotion Employees (Conditions of Service) Act, 1976",
        "Inter-State Migrant Workmen (Regulation of Employment and Conditions of "
        "Service) Act, 1979",
        "Cine-Workers and Cinema Theatre Workers (Regulation of Employment) Act, 1981",
        "Dock Workers (Safety, Health and Welfare) Act, 1986",
        "Building and Other Construction Workers (Regulation of Employment and "
        "Conditions of Service) Act, 1996",
    )),
)

# Short forms lawyers and users write. Only names that unambiguously mean one
# statute; anything else goes through the token comparison below.
_ALIASES = {
    "id act": "Industrial Disputes Act, 1947",
    "ida": "Industrial Disputes Act, 1947",
    "ir code": "Industrial Relations Code, 2020",
    "irc": "Industrial Relations Code, 2020",
    "wage code": "Code on Wages, 2019",
    "wages code": "Code on Wages, 2019",
    "ss code": "Code on Social Security, 2020",
    "social security code": "Code on Social Security, 2020",
    "osh code": "Occupational Safety, Health and Working Conditions Code, 2020",
    "oshwc code": "Occupational Safety, Health and Working Conditions Code, 2020",
}

_FILLER = {"the", "of", "and", "act", "a", "an", "on", "in", "for"}
_YEAR = re.compile(r"^(?:1[89]\d{2}|20\d{2})$")


def act_tokens(name: str) -> tuple[frozenset[str], str | None]:
    """(significant words, year) of a statute name, ignoring case, punctuation,
    filler words and a trailing plural — so "industrial dispute act",
    "The Industrial Disputes Act, 1947" and "Industrial Disputes Act 1947.csv"
    compare equal on words. "Act" is filler because the corpus itself is
    inconsistent ("Code on Wages Act 2019.csv")."""
    s = (name or "").lower()
    s = re.sub(r"\.csv$", "", s)
    words = re.findall(r"[a-z]+|\d{4}", s)
    year = next((w for w in words if _YEAR.match(w)), None)
    out = set()
    for w in words:
        if w in _FILLER or _YEAR.match(w) or w.isdigit():
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return frozenset(out), year


def canonical(name: str) -> str:
    """Expand a known short form; otherwise return the name unchanged."""
    key = re.sub(r"[^a-z ]", "", (name or "").lower()).strip()
    key = re.sub(r"\s+\d{4}$", "", key)
    return _ALIASES.get(key, name)


def same_statute(a: str, b: str) -> bool:
    wa, ya = act_tokens(canonical(a))
    wb, yb = act_tokens(canonical(b))
    if not wa or wa != wb:
        return False
    return ya is None or yb is None or ya == yb


@dataclass(frozen=True)
class Replacement:
    old_act: str
    code: Code


def replacement_for(act_name: str) -> Replacement | None:
    """The Code that replaced `act_name`, if it is one of the 29 repealed Acts."""
    for code in LABOUR_CODES:
        for old in code.repealed:
            if same_statute(act_name, old):
                return Replacement(old, code)
    return None


def labour_code_named(act_name: str) -> Code | None:
    for code in LABOUR_CODES:
        if same_statute(act_name, code.name):
            return code
    return None


def involves_labour_law(names: Iterable[str]) -> bool:
    return any(replacement_for(n) or labour_code_named(n) for n in names if n)


def law_in_force_note(names: Iterable[str]) -> str:
    """A block for the drafter when the matter touches labour law, else "".

    `names` are the statutes the user named or the retrieval returned. The
    note names the repealed Acts among them first, then lists every Code with
    the Acts it replaced, because a draft on one labour Act routinely cites
    others nobody named (bonus, gratuity, overtime under the Factories Act).
    """
    names = [n for n in names if n]
    if not involves_labour_law(names):
        return ""
    when = LABOUR_CODES_IN_FORCE_FROM.strftime("%d %B %Y").lstrip("0")
    named = []
    for n in names:
        r = replacement_for(n)
        if r and r.old_act not in [x.old_act for x in named]:
            named.append(r)
    lines = [
        "## LAW IN FORCE — read before citing any labour statute",
        f"The four Labour Codes came into force on {when} and repealed the Acts "
        "listed below, each under the Code's own repeal-and-savings section.",
    ]
    for r in named:
        lines.append(
            f"- The {r.old_act} named in this matter is REPEALED; it was replaced by "
            f"the {r.code.name} (section {r.code.repeal_section}).")
    lines += [
        "For a document drafted today:",
        "- Ground the document on the Code in force, citing the Code's own section "
        "numbers. Do not present a repealed Act as current law, and do not cite a "
        "Code section unless its text, as retrieved, supports the point.",
        "- The repealed Acts still govern only what the Code's savings clause and "
        "section 6 of the General Clauses Act, 1897 preserve: rights and liabilities "
        f"that accrued, and proceedings that began, before {when}. Where the user's "
        "facts place the matter in that period, say so and name the old provision "
        "alongside the Code's corresponding provision. Where they do not say when "
        "the right accrued, do not state either way as a fact: ground the document "
        "on the Code and give the saved old provision as one bracketed alternative "
        f"(for example \"[where the dues accrued before {when}: read with ...]\").",
        "Acts replaced:",
    ]
    for code in LABOUR_CODES:
        lines.append(f"- {code.name} (s.{code.repeal_section}): " + "; ".join(code.repealed) + ".")
    return "\n".join(lines) + "\n"
