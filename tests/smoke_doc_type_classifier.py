"""Live smoke test for the doc-type classifier.

Hits the real Gemini Flash-Lite endpoint with a battery of queries and
prints what the classifier returns. No assertions — this is an
operator-readable spot check for the new gate.

Run:
    python tests/smoke_doc_type_classifier.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.drafting import _classify_doc_type


# Each tuple: (query, expected_doc_type).
# "expected" is what a competent human Indian-law drafter would pick. The
# classifier should match this; we PRINT the actual vs expected for review.
CASES = [
    # Court-filed applications (should stay court_filing)
    ("draft a bail application under Section 483 BNSS", "court_filing"),
    ("anticipatory bail application for offences under Section 420 IPC", "court_filing"),
    ("draft an application under Order XXXIX Rules 1 & 2 CPC for temporary injunction", "court_filing"),
    ("application for grant of probate before District Judge", "court_filing"),
    ("succession certificate application", "court_filing"),
    ("application for transfer of case under Section 24 CPC", "court_filing"),
    ("Section 200 CrPC magistrate complaint for cheating", "court_filing"),

    # Office letters (the bug the user reported)
    ("draft an RTI application for water bill records", "office_letter"),
    ("application for income certificate to the Tahsildar", "office_letter"),
    ("application for caste certificate to SDM", "office_letter"),
    ("draft a leave of absence application to my employer", "office_letter"),
    ("NOC application to the housing society", "office_letter"),
    ("application to the bank for closure of loan account", "office_letter"),

    # Police complaints
    ("draft a police complaint for chain snatching", "police_complaint"),
    ("FIR registration application to SHO", "police_complaint"),

    # Legal notices
    ("draft a Section 138 NI Act notice for cheque bounce", "legal_notice"),
    ("reply to legal notice dated 2026-01-15", "legal_notice"),
    ("demand notice for unpaid rent", "legal_notice"),

    # Agreements
    ("draft a lease deed for commercial premises", "agreement_deed"),
    ("agreement to sell for residential plot", "agreement_deed"),

    # Tribunal appellate
    ("written submission before CIT(A) for AY 2022-23", "tribunal_appellate"),
    ("ITAT appeal memorandum", "tribunal_appellate"),
]


async def main() -> None:
    print(f"{'EXPECTED':<22} {'ACTUAL':<22} {'QUERY':<70}")
    print("-" * 116)
    correct = 0
    for query, expected in CASES:
        actual = await _classify_doc_type(query, "")
        mark = "OK " if actual == expected else "!! "
        if actual == expected:
            correct += 1
        print(f"{mark}{expected:<19} {actual:<22} {query[:70]}")
    print("-" * 116)
    print(f"Accuracy: {correct}/{len(CASES)} ({100 * correct // len(CASES)}%)")


if __name__ == "__main__":
    asyncio.run(main())
