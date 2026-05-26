"""Validate the legislation relevance gate (coarse BGE floor + LLM judge)
on the same Telangana-vs-CPC failure case from Response 1.

This runs without ES/uvicorn — it instantiates synthetic hits identical
to what BM25 actually retrieved in production, then calls the gate's
public entrypoint and asserts:

  (a) Telangana Municipalities Act-style hits are REJECTED (wrong subject)
  (b) Order XXXIX Rule 1 / Section 151 CPC-style hits are ACCEPTED

Run: python tests/validate_semantic_gate.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.retrieval_relevance import (
    check_retrieval_relevance,
    _COARSE_SEMANTIC_FLOOR,
    _RELEVANCE_CONFIDENCE_MIN,
)


QUERY = (
    "Draft an Application for Interim Injunction under Order XXXIX Rule 1 & 2 "
    "read with Section 151 of the Code of Civil Procedure, 1908. My client is "
    "the lawful owner and possessor of agricultural land situated in Pune. The "
    "defendant is attempting to illegally sell the suit property and create "
    "third-party rights despite having no ownership title. Whether interim "
    "injunction should be granted restraining defendant from alienating the "
    "suit property?"
)

# Exact text-style of what BM25 wrongly retrieved in Response 1 (Telangana
# Municipalities Act). Copied/condensed from tests/1prompt2responses.txt:183.
TELANGANA_HITS = [
    {"_source": {"page_content": (
        "Section 372 of the Telangana Municipalities Act, 1965: "
        "Notwithstanding anything in the Code of Civil Procedure, 1908 "
        "(Central Act 5 of 1908) or in any other law for the time being in "
        "force, no court shall grant any temporary injunction or make any "
        "interim order restraining any proceeding which is being or about to "
        "be taken under this Act, for the preparation or publication of "
        "electoral rolls or for the conduct of any elections."
    )}},
    {"_source": {"page_content": (
        "Section 377 of the Telangana Municipalities Act, 1965: "
        "Notwithstanding anything in the Code of Civil Procedure, 1908 or in "
        "any other law for the time being in force, no court shall grant any "
        "interim or temporary injunction or make any interim orders "
        "restraining any proceeding which is being or about to be taken "
        "under rule 8 in Schedule II to this Act, for the revision or "
        "amendment of the assessment books or restraining such revision or "
        "amendment from taking effect."
    )}},
    {"_source": {"page_content": (
        "Section 192(3) of the Telangana Municipalities Act, 1965: No "
        "decision made or order passed or proceeding taken by the "
        "Commissioner effecting removal of encroachments shall be called in "
        "question before a civil court in any suit, application or other "
        "proceeding and no injunction shall be granted by any court in "
        "respect of any proceeding taken or about to be taken by the "
        "Commissioner."
    )}},
    {"_source": {"page_content": (
        "Section 37(3)(c) of the Telangana Municipalities Act, 1965: The "
        "council shall not alienate such vacant land to any third party "
        "except by way of public auction or in accordance with the rules "
        "prescribed by the Government."
    )}},
]

# What the agent SHOULD have retrieved — Order XXXIX Rule 1 + Section 151 CPC.
# Phrasing closely follows the actual statutory text.
CPC_HITS = [
    {"_source": {"page_content": (
        "Order XXXIX Rule 1 of the Code of Civil Procedure, 1908 — Cases in "
        "which temporary injunction may be granted: Where in any suit it is "
        "proved by affidavit or otherwise — (a) that any property in dispute "
        "in a suit is in danger of being wasted, damaged or alienated by any "
        "party to the suit, or wrongfully sold in execution of a decree, or "
        "(b) that the defendant threatens, or intends, to remove or dispose "
        "of his property with a view to defrauding his creditors, or (c) that "
        "the defendant threatens to dispossess the plaintiff or otherwise "
        "cause injury to the plaintiff in relation to any property in dispute "
        "in the suit, the court may by order grant a temporary injunction to "
        "restrain such act, or make such other order for the purpose of "
        "staying and preventing the wasting, damaging, alienation, sale, "
        "removal or disposition of the property or dispossession of the "
        "plaintiff, or otherwise causing injury to the plaintiff in relation "
        "to any property in dispute in the suit as the court thinks fit, "
        "until the disposal of the suit or until further orders."
    )}},
    {"_source": {"page_content": (
        "Order XXXIX Rule 2 of the Code of Civil Procedure, 1908 — Injunction "
        "to restrain repetition or continuance of breach: In any suit for "
        "restraining the defendant from committing a breach of contract or "
        "other injury of any kind, whether compensation is claimed in the "
        "suit or not, the plaintiff may, at any time after the commencement "
        "of the suit, and either before or after judgment, apply to the "
        "court for a temporary injunction to restrain the defendant from "
        "committing the breach of contract or injury complained of, or any "
        "breach of contract or injury of a like kind arising out of the same "
        "contract or relating to the same property or right."
    )}},
    {"_source": {"page_content": (
        "Section 151 of the Code of Civil Procedure, 1908 — Saving of "
        "inherent powers of court: Nothing in this Code shall be deemed to "
        "limit or otherwise affect the inherent power of the court to make "
        "such orders as may be necessary for the ends of justice, or to "
        "prevent abuse of the process of the court."
    )}},
]


async def show(label: str, hits: list[dict], source: str) -> bool:
    chunks = [(h.get("_source") or {}).get("page_content", "") for h in hits]
    is_relevant, tel = await check_retrieval_relevance(
        QUERY, chunks, source, agent_name="Legislation",
    )
    verdict = "ACCEPT" if is_relevant else "REJECT (web fallback)"
    print(f"{label}")
    print(f"  coarse_sim       : {tel['coarse_sim']:.4f}  "
          f"(floor {_COARSE_SEMANTIC_FLOOR})")
    print(f"  judge_relevant   : {tel['judge_relevant']}")
    print(f"  judge_confidence : {tel['judge_confidence']}  "
          f"(min {_RELEVANCE_CONFIDENCE_MIN})")
    print(f"  matched_subject  : {tel['matched_subject']}")
    print(f"  reason           : {tel['reason']}")
    print(f"  verdict          : -> {verdict}\n")
    return is_relevant


async def amain() -> int:
    print(f"Query: {QUERY[:100]}...\n")
    print("-" * 80)
    telangana_relevant = await show(
        "Telangana Municipalities Act",
        TELANGANA_HITS,
        source="telangana_municipalities_act_1965.pdf",
    )
    cpc_relevant = await show(
        "Order XXXIX + S.151 CPC",
        CPC_HITS,
        source="code_of_civil_procedure_1908.pdf",
    )
    print("-" * 80)

    expected_telangana = False  # gate MUST reject Telangana
    expected_cpc = True         # gate MUST accept CPC
    ok = (telangana_relevant == expected_telangana) and (cpc_relevant == expected_cpc)

    print()
    if ok:
        print("PASS - gate correctly rejects Telangana, accepts CPC.")
        return 0
    else:
        print("FAIL - gate decisions did not match expectations.")
        if telangana_relevant != expected_telangana:
            print(f"  Telangana: expected REJECT, got "
                  f"{'ACCEPT' if telangana_relevant else 'REJECT'}")
        if cpc_relevant != expected_cpc:
            print(f"  CPC: expected ACCEPT, got "
                  f"{'ACCEPT' if cpc_relevant else 'REJECT'}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
