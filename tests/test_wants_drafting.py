"""Unit tests for `_wants_drafting` (BUG-01 fix).

Run:  python tests/test_wants_drafting.py
"""
from __future__ import annotations

import sys

# Make `agents` importable when run from repo root
sys.path.insert(0, ".")

from agents.orchestrator import _wants_drafting

# (query, expected) — True means "should route to Drafting"
CASES: list[tuple[str, bool]] = [
    # --- TRUE: explicit drafting verb ---
    ("Draft a plaint for recovery of money", True),
    ("Please draft a written statement", True),
    ("Draft an affidavit", True),
    ("Drafting a bail application", True),
    ("Could you draft a notice for me?", True),
    ("I want to draft a contract", True),
    ("Redraft this petition", True),
    ("Draft", True),
    ("Help me draft a divorce petition", True),

    # --- TRUE: verb + document-noun ---
    ("Write a plaint based on these facts", True),
    ("Please prepare a bail application", True),
    ("Create a written statement on behalf of defendant", True),
    ("Generate a legal notice for property dispute", True),
    ("Compose an MOU between two parties", True),
    ("Draw up a partnership deed", True),
    ("Give me a sample plaint", True),  # caught by format-re ("sample" + doc-noun)
    ("I need a notice for breach of contract", True),
    ("Want a written statement template", True),
    ("Help me write a petition", True),

    # --- TRUE: format / sample / template requests ---
    ("Format of bail application under BNSS 483", True),
    ("Sample affidavit for income proof", True),
    ("Template for written statement", True),
    ("Specimen of partnership deed", True),
    ("Standard form of NDA", True),
    ("Show me a sample plaint", True),  # caught by format-re

    # --- TRUE: "drafting / preparation of X" ---
    ("Drafting of a petition under Article 226", True),
    ("Preparation of plaint under Order VII", True),

    # --- FALSE: pure Q&A about an existing document ---
    ("Summarize the attached plaint", False),
    ("Who is the plaintiff in this case?", False),  # 'plaintiff' contains 'plaint'
    ("What is the loan amount claimed in this plaint?", False),
    ("Explain Order VII Rule 1 referenced in this plaint", False),
    ("What documentary evidence should the plaintiff produce?", False),
    ("Is the suit filed within the limitation period?", False),
    ("What is the cause of action in the attached document?", False),
    ("Who wrote this complaint?", False),  # 'complaint' contains 'plaint'
    ("Did the defendant file a written statement?", False),
    ("Tell me about written statements in CPC", False),
    ("Has the agreement been signed?", False),
    ("When was the legal notice served?", False),
    ("Analyze the petition I uploaded", False),
    ("What are the key clauses in this contract?", False),
    ("Review my affidavit and tell me what's wrong", False),

    # --- FALSE: substring collisions / unrelated mentions ---
    ("Explaining the difference between IPC and BNS", False),  # 'explain' contains 'plain'
    ("This is a creative legal argument", False),  # 'creative' contains 'create'
    ("Maker of the cheque is liable under NI Act", False),  # 'Maker' contains 'make'
    ("She wrote a brilliant judgment last week", False),  # 'wrote' contains 'rote'
    ("Noticed the defendant did not appear", False),  # 'Noticed' contains 'notice'
    ("Reply must be filed within 30 days", False),  # 'reply' alone is not drafting

    # --- "How do I draft" / "what's the format of" — accepted as drafting ---
    # Reasonable interpretation: the user wants to see a draft/format.
    ("How do I draft a plaint?", True),
    ("What is the format of a bail application?", True),

    # --- FALSE: greetings / non-legal ---
    ("Hello", False),
    ("How are you", False),

    # --- FALSE: scenario analysis ---
    ("My landlord locked me out, what are my options?", False),
    ("Is selling my organ legal in India?", False),
]


def main() -> int:
    passed = 0
    failed: list[tuple[str, bool, bool]] = []
    for query, expected in CASES:
        actual = _wants_drafting(query)
        if actual == expected:
            passed += 1
        else:
            failed.append((query, expected, actual))

    total = len(CASES)
    print(f"\n{'='*70}")
    print(f"_wants_drafting unit tests: {passed}/{total} passed")
    print('='*70)

    if failed:
        print(f"\n{len(failed)} FAILURES:")
        for query, expected, actual in failed:
            mark = "drafting" if actual else "non-drafting"
            want = "drafting" if expected else "non-drafting"
            print(f"  [got {mark} but wanted {want}]  {query!r}")
        return 1

    print("\nAll cases passed [PASS]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
