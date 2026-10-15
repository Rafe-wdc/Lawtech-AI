"""Tribunals in the `tribunal_judgements` index.

One table shared by the ingest scripts (scripts/tribunal/) and the Tribunal
agent's search filter. `folders` are the second segment of the `source` path
of the tribunal documents migrated from `judgements_v2`
("tribunal\\<folder>\\<year>\\<file>"). CEGAT was renamed CESTAT in 2003, so
both folders share one code: a search for CESTAT finds the older orders too.
"""

from __future__ import annotations

from typing import NamedTuple


class Tribunal(NamedTuple):
    code: str
    name: str
    folders: tuple[str, ...] = ()


TRIBUNALS: tuple[Tribunal, ...] = (
    Tribunal("CESTAT", "Customs, Excise and Service Tax Appellate Tribunal",
             ("cestat", "Customs, Excise and Gold Tribunal")),
    Tribunal("CAT", "Central Administrative Tribunal", ("Central Administrative Tribunal",)),
    Tribunal("CIC", "Central Information Commission", ("Central Information Commission",)),
    Tribunal("NGT", "National Green Tribunal", ("National Green Tribunal",)),
    Tribunal("CDRC", "Consumer Disputes Redressal Commission", ("Consumer Disputes Redressal",)),
    Tribunal("APTEL", "Appellate Tribunal for Electricity", ("Appellate Tribunal For Electricity",)),
    Tribunal("CLB", "Company Law Board", ("Company Law Board",)),
    Tribunal("CERC", "Central Electricity Regulatory Commission",
             ("Central Electricity Regulatory Commission",)),
    Tribunal("IPAB", "Intellectual Property Appellate Board", ("Intellectual Property Appellate Board",)),
    Tribunal("AAR", "Authority for Advance Rulings", ("Authority Tribunal",)),
    Tribunal("CB", "Copyright Board", ("Copyright Board",)),
    Tribunal("TDSAT", "Telecom Disputes Settlement and Appellate Tribunal"),
)

UNKNOWN = Tribunal("OTHER", "Tribunal (unspecified)")

TRIBUNAL_BY_CODE: dict[str, Tribunal] = {t.code: t for t in TRIBUNALS}
FOLDER_TO_TRIBUNAL: dict[str, Tribunal] = {
    f.lower(): t for t in TRIBUNALS for f in t.folders
}

TDSAT = TRIBUNAL_BY_CODE["TDSAT"]


def tribunal_for_source(source: str) -> Tribunal:
    """Tribunal for a migrated document's `source` path."""
    parts = (source or "").replace("/", "\\").split("\\")
    folder = parts[1].strip().lower() if len(parts) > 2 else ""
    return FOLDER_TO_TRIBUNAL.get(folder, UNKNOWN)
