"""Regression tests for drafting fact-grounding when no case facts are supplied.

THE BUG
=======
Three identical requests for

    "Draft a bail application for cheating under Section 420 IPC"

produced materially different drafts. Two of the three invented case
particulars — a party name, a C.R. number, a police station — plus factual
assertions about the applicant (in judicial custody, no criminal antecedents,
sole breadwinner, elderly dependants). The third correctly emitted
[ACCUSED'S NAME]-style placeholders.

Temperature was already 0.0 on both generation paths, and Elasticsearch
returned the same template with the same score. The variance came from an
ambiguous instruction, not from sampling.

Root cause: every fact-grounding rule in the drafting prompts is conditioned
on a case source existing — "when the source names different real parties",
"MUST come VERBATIM from UPLOADED SOURCE DOCUMENTS or USER QUERY". A query
naming only a document type satisfies none of those conditions, so the guards
became no-ops while the emphatic "USE REAL FACTS ... NOT PLACEHOLDERS"
heading still applied. The reference template's `____` blanks did the rest.

Verified against the actual templates in Elasticsearch: the invented
particulars are NOT in them, but the boilerplate sentences ARE — e.g. the
Section 439 template literally contains "the sole breadwinner of his family,
and his aged parents are dependent on him". The model kept the sentence and
filled the blanks with fiction.

WHAT THESE TESTS ASSERT
=======================
Invariants, never text equality — wording is expected to vary:
  * placeholder mode engages when, and only when, no case facts are supplied
  * bracketed placeholders are treated as correct output, not violations
  * bare invented particulars ARE flagged
  * anything the user actually stated is NOT flagged
  * the directive reaches the prompt on both generation paths

Run:
    pytest tests/test_drafting_grounding.py -v
    python tests/test_drafting_grounding.py      # no pytest required
"""

from __future__ import annotations

import inspect
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("OPENAI_API_KEY", "test-key-unused")
os.environ.setdefault("GOOGLE_API_KEY", "test-key-unused")
os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://localhost:1")

import agents.drafting as d  # noqa: E402
from agents.drafting import (  # noqa: E402
    _case_facts_present,
    _NO_CASE_FACTS_DIRECTIVE,
    validate_draft_grounding,
)

QUERY = "Draft a bail application for cheating under Section 420 IPC"

# Condensed from an actual bad run — the particulars the user flagged.
INVENTED_DRAFT = """
IN THE COURT OF HON'BLE SESSIONS COURT AT NASHIK
Cri. Bail Application No. 445 / 2024
In C.R. No. 271 of 2021 registered with Mumbai Naka Police Station

Mr. Sandip Ramkisan Funde, Age: 34 Years
                                        ... Accused/Applicant

1. The applicant is in judicial custody since 12/03/2024.
2. The applicant has no criminal antecedents whatsoever.
3. The investigation is substantially complete and no recovery is pending.
4. The applicant is the sole breadwinner of his family and his aged parents
   are dependent on him.
5. The applicant is a permanent resident and is working at Nashik.
6. The applicant undertakes to deposit Rs. 50,000 as surety.
"""

# The shape run 3 produced — correct behaviour.
PLACEHOLDER_DRAFT = """
IN THE COURT OF HON'BLE SESSIONS COURT AT [COURT], [DISTRICT/STATE]
Cri. Bail Application No. [CASE NUMBER] / [YEAR]
In C.R. No. [FIR / C.R. NUMBER] registered with [POLICE STATION]

[ACCUSED'S NAME], Age: [AGE] Years
                                        ... Accused/Applicant

1. [IF APPLICABLE: The applicant is in judicial custody since [DATE OF ARREST].]
2. [IF APPLICABLE: The applicant has no criminal antecedents.]
3. [IF APPLICABLE: The investigation is complete and no recovery is pending.]
4. [IF APPLICABLE: The applicant is the sole breadwinner and [DEPENDANTS] are
   dependent on him.]
5. The applicant undertakes to abide by all conditions imposed by this Court.
"""


class TestPlaceholderModeTrigger:
    """Engages when there is nothing to ground against — and only then."""

    def test_no_facts_triggers_placeholder_mode(self):
        assert _case_facts_present("") is False
        assert _case_facts_present(None) is False
        assert _case_facts_present("   \n  ") is False

    def test_uploaded_facts_suppress_placeholder_mode(self):
        assert _case_facts_present("[Uploaded document — fir.pdf]\nFIR 123...") is True

    def test_directive_names_the_categories_that_were_invented(self):
        text = _NO_CASE_FACTS_DIRECTIVE.lower()
        for category in ("judicial custody", "criminal antecedent",
                         "sole breadwinner", "permanent resident",
                         "police station", "fir"):
            assert category in text, f"directive does not cover {category!r}"


class TestValidatorCatchesInventedFacts:

    def test_flags_the_particulars_from_the_bad_run(self):
        report = validate_draft_grounding(INVENTED_DRAFT, QUERY)
        kinds = {k for k, _ in report["unsupported"]}
        for expected in ("fir_number", "police_station", "person_name",
                         "judicial_custody", "no_antecedents",
                         "breadwinner", "dependants"):
            assert expected in kinds, f"missed {expected}; got {sorted(kinds)}"

    def test_correct_draft_produces_no_violations(self):
        report = validate_draft_grounding(PLACEHOLDER_DRAFT, QUERY)
        assert report["unsupported"] == [], (
            f"placeholders wrongly flagged: {report['unsupported']}")
        assert report["placeholder_count"] > 5

    def test_bracketed_assertions_are_not_violations(self):
        """`[IF APPLICABLE: ... sole breadwinner ...]` is the fix, not the bug."""
        bracketed = "1. [IF APPLICABLE: The applicant is the sole breadwinner.]"
        bare = "1. The applicant is the sole breadwinner."
        assert validate_draft_grounding(bracketed, QUERY)["unsupported"] == []
        assert validate_draft_grounding(bare, QUERY)["unsupported"] != []

    def test_facts_the_user_supplied_are_not_flagged(self):
        """Grounded in the query = allowed through."""
        q = "Draft a bail application for Mr. Ramesh Kumar under Section 420 IPC"
        draft = "Mr. Ramesh Kumar, the applicant, seeks bail."
        names = [v for k, v in validate_draft_grounding(draft, q)["unsupported"]
                 if k == "person_name"]
        assert names == [], f"flagged a name the user gave: {names}"

    def test_empty_draft_is_safe(self):
        assert validate_draft_grounding("", QUERY)["unsupported"] == []
        assert validate_draft_grounding(None, QUERY)["unsupported"] == []


class TestDirectiveIsWiredIntoBothPaths:
    """Source introspection — the same guard style used elsewhere in tests/."""

    def test_single_pass_appends_the_directive(self):
        src = inspect.getsource(d._generate_single_pass)
        assert "_NO_CASE_FACTS_DIRECTIVE" in src
        assert "_case_facts_present" in src

    def test_sectionwise_appends_the_directive(self):
        """Section-wise builds its prompt in `_generate_section_pair`, which
        `_generate_sectionwise` calls once per pair — so the guard lives
        there, not in the outer walker. This is the path the bail
        application actually took (a 9-section fan-out), so it matters most.
        """
        pair_src = inspect.getsource(d._generate_section_pair)
        assert "_NO_CASE_FACTS_DIRECTIVE" in pair_src
        assert "_case_facts_present" in pair_src

        walker_src = inspect.getsource(d._generate_sectionwise)
        assert "_generate_section_pair(" in walker_src, (
            "the walker no longer routes through the guarded pair builder")

    def test_dispatcher_validates_both_paths(self):
        src = inspect.getsource(d._generate_draft)
        assert src.count("_enforce_grounding") >= 2, (
            "both single-pass and section-wise must be validated")

    def test_regeneration_is_bounded_to_one_attempt(self):
        """A clean draft must cost nothing; a bad one at most one retry."""
        src = inspect.getsource(d._enforce_grounding)
        assert "if not unsupported:" in src, "must return early when clean"
        assert src.count("await regenerate(") == 1, "at most one retry"


# --- Standalone runner (venv has no pytest) ---------------------------------

if __name__ == "__main__":
    failures = 0
    for cls in (TestPlaceholderModeTrigger, TestValidatorCatchesInventedFacts,
                TestDirectiveIsWiredIntoBothPaths):
        inst = cls()
        for name in sorted(n for n in dir(inst) if n.startswith("test_")):
            try:
                getattr(inst, name)()
                print(f"  PASS  {cls.__name__}.{name}")
            except Exception as e:
                failures += 1
                print(f"  FAIL  {cls.__name__}.{name}: {type(e).__name__}: {e}")
    print("\nALL PASSED" if not failures else f"\n{failures} FAILURE(S)")
    sys.exit(1 if failures else 0)
