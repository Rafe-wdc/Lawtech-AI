"""Tests for `core.statute_citation_check`.

Two production failures are pinned here:

  * **Blank `New Provision:`** — asked for IPC 309, the system answered that
    the counterpart is "Section 224 BNS". BNS 224 is "Threat of injury to a
    public servant". `New Provision:` is blank on 227 of 1,297 old-code rows
    and the layout prompt demanded a number, so the model supplied one.

  * **Cross-pairing** — a bail draft produced "Section 439 BNSS". 439 is the
    CrPC bail provision; the BNSS counterpart is 483. The citation names a
    provision that exists in neither code.

The corpus row format the parser depends on is reproduced verbatim from
`newacts_v1`; if the indexing changes shape these tests fail, which is the
intent.
"""
from __future__ import annotations

import pytest

from core.statute_citation_check import (
    era_mismatch,
    annotate,
    check,
    cited_new_code_sections,
    correct_cross_pairs,
    find_cross_pairs,
    retrieved_new_code_sections,
    statute_pairs,
)

# Verbatim row shape from the `newacts_v1` index.
CTX_BAIL = """- **Source**: `/content/Bharitya New Acts/Code of Criminal Procedure 1973.csv`
  Section Number: Section 439 of The Code of Criminal Procedure 1973
  Section Name: Special powers of High Court or Court of Session regarding bail
  New Provision: Section 483 of Bharatiya Nagarik Suraksha Sanhita, 2023:
  (1) A High Court or Court of Session may direct that any person accused of
  an offence and in custody be released on bail."""

# The 227-row case: the field is present but empty.
CTX_BLANK = """- **Source**: `/content/Bharitya New Acts/Indian Penal Code 1860.csv`
  Section Number: Section 309 of The Indian Penal Code 1860
  Section Name: Attempt to commit suicide
  New Provision:
  Whoever attempts to commit suicide and does any act towards the commission
  of such offence, shall be punished with simple imprisonment."""


class TestExtraction:
    def test_new_code_sections_come_from_the_new_provision_line(self):
        assert retrieved_new_code_sections(CTX_BAIL) == {"483"}

    def test_blank_new_provision_yields_no_new_sections(self):
        assert retrieved_new_code_sections(CTX_BLANK) == set()

    def test_old_section_number_is_not_mistaken_for_a_new_one(self):
        # 439 appears in the row, but as the OLD section.
        assert "439" not in retrieved_new_code_sections(CTX_BAIL)

    @pytest.mark.parametrize("text,expected", [
        ("Section 483 BNSS", ["483"]),
        ("Section 103 of the Bharatiya Nyaya Sanhita, 2023", ["103"]),
        ("s. 63 BSA and Section 103 BNS", ["63", "103"]),
        ("Section 439 CrPC", []),          # old code is not policed
        ("Section 12 of the Contract Act", []),
    ])
    def test_cited_sections(self, text, expected):
        assert [s for s, _ in cited_new_code_sections(text)] == expected

    def test_subsection_is_grounded_by_its_parent(self):
        ctx = CTX_BAIL.replace("Section 483 of Bharatiya", "Section 8 of Bharatiya")
        assert check("Section 8(7) BNS applies.", ctx)["ungrounded"] == []


class TestBlankProvisionFabrication:
    def test_invented_counterpart_is_flagged(self):
        result = check("The counterpart is Section 224 of the Bharatiya "
                       "Nyaya Sanhita, 2023.", CTX_BLANK)
        assert result["ungrounded"] == ["Section 224 Bharatiya Nyaya Sanhita"]

    def test_context_present_but_empty_of_new_sections_still_judges(self):
        # Regression: an earlier version treated "no new-code sections
        # retrieved" as unjudgeable and passed everything — which is exactly
        # the blank-field case this module exists to catch.
        assert check("Section 224 BNS.", CTX_BLANK)["ungrounded"]

    def test_no_context_at_all_fails_open(self):
        result = check("Section 224 BNS.", "")
        assert result["ungrounded"] == []
        assert result["no_context"] is True

    def test_annotate_prepends_a_banner_and_keeps_the_answer(self):
        answer = "The counterpart is Section 224 BNS."
        out, result = annotate(answer, CTX_BLANK)
        assert out.endswith(answer)          # nothing removed
        assert out.startswith(">")
        assert "224" in result["ungrounded"][0]

    def test_grounded_answer_passes_untouched(self):
        answer = "Bail is governed by Section 483 BNSS, 2023."
        out, result = annotate(answer, CTX_BAIL)
        assert out == answer
        assert result["ungrounded"] == []


class TestCrossPairing:
    def test_pairs_are_read_from_the_row(self):
        assert statute_pairs(CTX_BAIL) == {"439": ("483", "CrPC", "BNSS")}

    def test_blank_new_provision_contributes_no_pair(self):
        assert statute_pairs(CTX_BLANK) == {}

    def test_a_blank_row_cannot_borrow_the_next_rows_number(self):
        # Rows are split on "Section Number:" precisely so IPC 309's blank
        # field cannot pick up CrPC 439's counterpart.
        pairs = statute_pairs(CTX_BLANK + "\n\n" + CTX_BAIL)
        assert "309" not in pairs
        assert pairs["439"][0] == "483"

    @pytest.mark.parametrize("text", [
        "The applicant seeks bail under Section 439 BNSS, 2023.",
        "under Section 439 of the Bharatiya Nagarik Suraksha Sanhita",
        "an application u/s. 439 BNSS",
    ])
    def test_hybrid_citation_is_corrected(self, text):
        out, fixes = correct_cross_pairs(text, CTX_BAIL)
        assert "483" in out and "439" not in out
        assert fixes[0]["correct"] == "Section 483 BNSS"

    @pytest.mark.parametrize("text", [
        "Bail under Section 483 BNSS, 2023.",              # already correct
        "Bail under Section 439 CrPC, 1973.",              # correct old code
        "Section 439 CrPC (Section 483 BNSS after 01.07.2024)",  # both, correct
        "Section 439 of the Companies Act",                # not a criminal code
    ])
    def test_correct_citations_are_left_alone(self, text):
        out, fixes = correct_cross_pairs(text, CTX_BAIL)
        assert out == text
        assert fixes == []

    def test_cross_family_pairing_is_reported_but_not_rewritten(self):
        # "439 BNS" puts a CrPC number against the IPC's successor. Real, but
        # not a number substitution — rewriting it would assert a mapping the
        # corpus does not make.
        text = "punishable under Section 439 BNS, 2023."
        out, fixes = correct_cross_pairs(text, CTX_BAIL)
        assert out == text and fixes == []
        flagged = find_cross_pairs(text, CTX_BAIL)
        assert flagged[0]["correctable"] is False

    def test_no_context_means_no_correction(self):
        text = "bail under Section 439 BNSS"
        assert correct_cross_pairs(text, "") == (text, [])

    def test_banner_names_the_counterpart(self):
        out, result = annotate("Bail is governed by Section 439 BNSS.", CTX_BAIL)
        assert "Section 483 BNSS" in out
        assert result["cross_paired"] == ["Section 439 BNSS -> Section 483 BNSS"]


class TestNeverRaises:
    @pytest.mark.parametrize("answer", ["", None, "Section", "s. of BNS"])
    @pytest.mark.parametrize("context", ["", None, CTX_BAIL])
    def test_degenerate_inputs(self, answer, context):
        check(answer or "", context or "")
        correct_cross_pairs(answer or "", context or "")
        annotate(answer or "", context or "")


class TestEraMismatch:
    """New-code charges pleaded under the old procedural code.

    Measured live: a bail application for offences under Sections 316(2) and
    318(4) BNS came back captioned "APPLICATION FOR REGULAR BAIL UNDER
    SECTIONS 437/439 OF THE CODE OF CRIMINAL PROCEDURE, 1973" — while the
    reference draft the pipeline supplied was itself a BNSS format.
    """

    BAD = ("APPLICATION FOR REGULAR BAIL UNDER SECTIONS 437 AND 439 OF THE "
           "CODE OF CRIMINAL PROCEDURE, 1973 for offences under Section "
           "316(2) of the Bharatiya Nyaya Sanhita, 2023 and Section 318(4) "
           "of the Bharatiya Nyaya Sanhita, 2023.")

    def test_the_measured_failure_is_detected(self):
        found = era_mismatch(self.BAD)
        assert found["charging_era"] == "new"
        assert found["stale_procedural"] == ["Section 437 CrPC", "Section 439 CrPC"]

    def test_consistent_new_era_draft_is_clean(self):
        assert era_mismatch(
            "BAIL UNDER SECTION 483 OF THE BHARATIYA NAGARIK SURAKSHA "
            "SANHITA, 2023 for an offence under Section 316(2) of the "
            "Bharatiya Nyaya Sanhita, 2023."
        ) is None

    def test_consistent_old_era_draft_is_clean(self):
        assert era_mismatch(
            "BAIL UNDER SECTION 439 OF THE CODE OF CRIMINAL PROCEDURE, 1973 "
            "for an offence under Section 420 of the Indian Penal Code, 1860."
        ) is None

    def test_mixed_charging_citations_are_not_flagged(self):
        # Naming both codes is the cross-referencing the prompt asks for;
        # either procedural code is defensible alongside it.
        assert era_mismatch(
            "Section 420 of the Indian Penal Code, 1860 [Section 318 of the "
            "Bharatiya Nyaya Sanhita, 2023] — bail under Section 439 CrPC."
        ) is None

    def test_old_charges_under_new_procedure_is_reported(self):
        found = era_mismatch(
            "BAIL UNDER SECTION 483 BNSS for an offence under Section 420 IPC."
        )
        assert found["charging_era"] == "old"
        assert found["stale_procedural"] == ["Section 483 BNSS"]

    def test_no_citations_means_nothing_to_judge(self):
        assert era_mismatch("A plain paragraph with no statute in it.") is None
        assert era_mismatch("") is None

    def test_procedural_only_draft_is_not_flagged(self):
        # No charging section means no era to read the procedure against.
        assert era_mismatch("Bail under Section 439 CrPC.") is None

    def test_log_helper_never_raises_and_returns_the_finding(self):
        from core.statute_citation_check import log_era_mismatch
        assert log_era_mismatch(self.BAD)["charging_era"] == "new"
        assert log_era_mismatch("") is None

    def test_detection_does_not_modify_the_text(self):
        # The repair is the critic's job — this path only measures.
        from core.statute_citation_check import log_era_mismatch
        before = self.BAD
        log_era_mismatch(before)
        assert before == self.BAD


class TestEnumeratedCitations:
    """A citation may list several sections against one act name.

    The first version of the regex matched only a number sitting directly
    against the act, so the caption that exposed the era bug — "SECTIONS
    437/439 OF THE CODE OF CRIMINAL PROCEDURE, 1973" — matched neither 437
    (followed by "/") nor 439 (no lead word of its own). It saw nothing at
    all in the very text it was written for.
    """

    # Verbatim from the live draft, req b01b7bc9.
    LIVE_CAPTION = (
        "**APPLICATION FOR REGULAR BAIL UNDER SECTIONS 437/439 OF THE CODE "
        "OF CRIMINAL PROCEDURE, 1973** for offences under Sections 316(2) "
        "and 318(4) of the Bharatiya Nyaya Sanhita, 2023."
    )

    def test_the_live_caption_is_detected(self):
        found = era_mismatch(self.LIVE_CAPTION)
        assert found["charging_era"] == "new"
        assert found["stale_procedural"] == [
            "Section 437 CrPC", "Section 439 CrPC"]
        assert found["charging"] == ["316(2) BNS", "318(4) BNS"]

    @pytest.mark.parametrize("text,expected", [
        ("Sections 437/439 of the CrPC", ["437", "439"]),
        ("Sections 437, 438 and 439 CrPC", ["437", "438", "439"]),
        ("Sections 316(2) and 318(4) BNS", ["316(2)", "318(4)"]),
        ("Section 439 CrPC", ["439"]),
        ("Sections 103 & 105 of the Bharatiya Nyaya Sanhita", ["103", "105"]),
    ])
    def test_every_number_in_a_list_is_read(self, text, expected):
        from core.statute_citation_check import _all_citations
        assert [s for s, _ in _all_citations(text)] == expected

    def test_enumerated_new_code_citations_are_all_checked(self):
        # Both numbers must be tested for grounding, not just the last.
        result = check("Sections 224 and 226 of the Bharatiya Nyaya "
                       "Sanhita, 2023.", CTX_BLANK)
        assert result["ungrounded"] == [
            "Section 224 Bharatiya Nyaya Sanhita",
            "Section 226 Bharatiya Nyaya Sanhita",
        ]

    def test_only_the_wrong_number_in_a_list_is_rewritten(self):
        # 439 is the cross-paired one; 483 is already correct and the
        # separators, spacing and act name must survive untouched.
        out, fixes = correct_cross_pairs("Sections 439/483 BNSS apply.", CTX_BAIL)
        assert out == "Sections 483/483 BNSS apply."
        assert [f["cited"] for f in fixes] == ["Section 439 BNSS"]

    def test_enumeration_shape_is_preserved(self):
        out, _ = correct_cross_pairs(
            "under Sections 439 and 999 of the Bharatiya Nagarik Suraksha "
            "Sanhita, 2023", CTX_BAIL)
        assert out == ("under Sections 483 and 999 of the Bharatiya Nagarik "
                       "Suraksha Sanhita, 2023")


class TestCorpusSpelling:
    """The corpus spells it "Bhartiya", and drafts inherit that spelling.

    The reference-draft filename in the `drafting` index is "Format For First
    Bail Application Under Section 478 Of Bhartiya Nagarik Suraksha Sanhita,
    2023 (BNSS).csv", and generated captions came back as "SECTION 483 OF THE
    BHARTIYA NAGARIK SURAKSHA SANHITA, 2023". Matching only the "Bharatiya"
    spelling made every check here silently blind to the most common form
    the pipeline actually produces — a guard that reports clean because it
    cannot see.
    """

    @pytest.mark.parametrize("act", ["Bharatiya", "Bhartiya"])
    def test_both_spellings_are_read(self, act):
        from core.statute_citation_check import _all_citations
        text = f"BAIL UNDER SECTION 483 OF THE {act.upper()} NAGARIK SURAKSHA SANHITA, 2023"
        assert _all_citations(text) == [("483", "BNSS")]

    @pytest.mark.parametrize("act", ["Bharatiya", "Bhartiya"])
    def test_era_mismatch_sees_both_spellings(self, act):
        text = (f"BAIL UNDER SECTIONS 437/439 OF THE CODE OF CRIMINAL "
                f"PROCEDURE, 1973 for offences under Section 316(2) of the "
                f"{act} Nyaya Sanhita, 2023.")
        assert era_mismatch(text)["stale_procedural"] == [
            "Section 437 CrPC", "Section 439 CrPC"]

    @pytest.mark.parametrize("act", ["Bharatiya", "Bhartiya"])
    def test_grounding_check_sees_both_spellings(self, act):
        result = check(f"The counterpart is Section 224 of the {act} "
                       f"Nyaya Sanhita, 2023.", CTX_BLANK)
        assert result["ungrounded"]

    def test_retrieved_rows_in_either_spelling_are_parsed(self):
        ctx = CTX_BAIL.replace("Bharatiya", "Bhartiya")
        assert statute_pairs(ctx) == {"439": ("483", "CrPC", "BNSS")}
        assert retrieved_new_code_sections(ctx) == {"483"}
