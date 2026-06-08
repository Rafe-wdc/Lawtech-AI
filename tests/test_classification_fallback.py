"""Unit tests for `_classify_task_regex_fallback` (Drafting misrouting fix).

This locks down Phase 1 of the Drafting overfiring fix: the regex fallback now
defers to `_wants_drafting()` instead of doing a naive substring check on
("draft", "agreement", "notice", "plaint", "petition", "template", "format").

The naive substring check used to misroute these Q&A-style queries to Drafting
whenever the LLM classifier had to fall back (timeout / rate-limit / parse
error). `_wants_drafting()` requires intent (verb + document-noun) and has a
Q&A negative-regex short-circuit.

Run:  python tests/test_classification_fallback.py
"""
from __future__ import annotations

import sys

# Make `agents` importable when run from repo root
sys.path.insert(0, ".")

from agents.orchestrator import _classify_task_regex_fallback

# (query, expected_task) — fallback should NEVER pick Drafting for these.
# The expected_task is what we'd accept; anything in the accept-list is fine.
NEGATIVE_DRAFTING_CASES: list[tuple[str, set[str]]] = [
    # --- Q&A about document structure / format / requirements ---
    ("Essential elements of a partnership agreement",
        {"Legal_Concepts", "Legislation"}),
    ("Notice under Section 138 NI Act requirements",
        {"Legislation", "Legal_Concepts"}),
    ("Section 80 CPC notice requirements",
        {"Legislation", "Legal_Concepts"}),
    ("Plaint requirements under Order VII CPC",
        {"Legislation", "Legal_Concepts"}),
    ("Difference between agreement and contract",
        {"Legal_Concepts", "Legislation"}),
    ("Discuss the petition under Article 32 of the Constitution",
        {"Constitution", "Legal_Concepts"}),
    ("What are the ingredients of a valid contract?",
        {"Legal_Concepts", "Legislation"}),

    # --- Q&A about an existing document (substring collisions with "plaint", etc.) ---
    ("Summarize the attached plaint",
        {"Legal_Concepts", "Document"}),
    ("Who is the plaintiff in this case?",
        {"Legal_Concepts", "Document"}),
    ("Explain Order VII Rule 1 referenced in this plaint",
        {"Legislation", "Legal_Concepts", "Document"}),

    # --- Procedural queries ("how to file X") ---
    # These should route to Scenario or Legal_Concepts, NEVER Drafting.
    ("How do I file a writ petition under Article 32?",
        {"Scenario", "Constitution", "Legal_Concepts"}),

    # --- Bare statute-name mentions involving doc-nouns ---
    # "appeal" alone is too generic for Drafting; "notice" in a statute context is not Drafting.
    ("Section 41 CrPC notice provisions",
        {"Newacts", "Legislation"}),
]

# (query, expected_task) — fallback SHOULD pick Drafting for these.
POSITIVE_DRAFTING_CASES: list[tuple[str, str]] = [
    ("Draft a plaint for partition of ancestral property", "Drafting"),
    ("Please prepare a bail application", "Drafting"),
    ("Write me a legal notice for breach of contract", "Drafting"),
    ("Give me a sample affidavit", "Drafting"),
    ("Redraft this written statement", "Drafting"),
    ("Drafting an MOU between two companies", "Drafting"),
]


# --- Phase 3 candidates (intentionally Drafting today, but worth revisiting) ---
# These queries currently route to Drafting because _wants_drafting() treats
# "format of X" / "sample X" / "template X" as inherently drafting intent (see
# _DRAFTING_FORMAT_RE in agents/orchestrator.py). Some users asking
# "what is the format of X?" want a short structural explanation, NOT a 30k-char
# generated draft. Whether to narrow _DRAFTING_FORMAT_RE is a product call
# pending in Phase 3 of the Drafting-misroute investigation.
#
# Keep these here so when Phase 3 is decided, we can flip them into either
# NEGATIVE_DRAFTING_CASES (narrow the regex) or fold them silently away (keep
# current behaviour confirmed).
PHASE_3_REVIEW: list[str] = [
    "What is the format of a written statement under CPC?",
    "Show me the format of a bail application",
    "Standard form of an affidavit",
]


def main() -> int:
    passed = 0
    failed: list[tuple[str, str, str]] = []

    # Negative cases — fallback must NOT pick Drafting.
    for query, accept_set in NEGATIVE_DRAFTING_CASES:
        actual = _classify_task_regex_fallback(query)
        if actual == "Drafting":
            failed.append((query, "NOT Drafting (any of: " + ", ".join(sorted(accept_set)) + ")", actual))
        else:
            passed += 1

    # Positive cases — fallback should still recognize real drafting requests.
    for query, expected in POSITIVE_DRAFTING_CASES:
        actual = _classify_task_regex_fallback(query)
        if actual == expected:
            passed += 1
        else:
            failed.append((query, expected, actual))

    total = len(NEGATIVE_DRAFTING_CASES) + len(POSITIVE_DRAFTING_CASES)
    print(f"\n{'='*70}")
    print(f"_classify_task_regex_fallback Drafting tests: {passed}/{total} passed")
    print('='*70)

    if failed:
        print(f"\n{len(failed)} FAILURES:")
        for query, expected, actual in failed:
            print(f"  [got {actual!r} but wanted {expected!r}]")
            print(f"    query: {query!r}")
        return 1

    print("\nAll cases passed [PASS]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
