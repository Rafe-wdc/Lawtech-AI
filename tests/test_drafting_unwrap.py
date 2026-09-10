"""Hard-wrapped prose is rejoined into paragraphs; layout lines are not.

Advocate screenshot 2026-09-10: numbered paragraphs wrapped at ~100 columns
with continuation lines at the left margin rendered as one fragment per
line, while unwrapped paragraphs in the same draft rendered correctly. The
prompt rule "one paragraph = one line" does not hold, so validate_draft now
repairs it mechanically. These tests pin what is joined and, more
importantly, what is never joined.
"""
from __future__ import annotations

from agents.drafting import _unwrap_hard_wrapped_lines, validate_draft

LONG = ("1. That the present application is preferred on behalf of the Applicant/Accused above-named, "
        "seeking regular bail in")


def test_wrapped_numbered_paragraph_is_joined_column_zero():
    src = (LONG + "\n"
           "connection with FIR No. [FIR No.] of [Year], registered at Police Station [Police Station], for\n"
           "offences punishable under Sections [Sections Charged] of the Bharatiya Nyaya Sanhita, 2023.\n")
    out, n = _unwrap_hard_wrapped_lines(src)
    assert n == 2
    assert out.count("\n") == 1
    assert "seeking regular bail in connection with FIR" in out


def test_wrapped_paragraph_with_indented_continuation_is_joined():
    src = (LONG + "\n"
           "   connection with FIR No. 12 of 2024 registered at Police Station Wakad, District Pune, for\n"
           "   offences under Section 420.\n")
    out, n = _unwrap_hard_wrapped_lines(src)
    assert n == 2 and "bail in connection with FIR No. 12" in out


def test_stranded_year_is_rejoined_not_escaped():
    src = ("12. The accused was arrested on the night of 12 March at the residence of the complainant in\n"
           "2023. He has been in custody since that date without any bail application being moved.\n")
    r = validate_draft(src)
    out = r[0] if isinstance(r, tuple) else r
    assert "complainant in 2023. He has been" in out
    assert "\\." not in out


def test_second_numbered_item_is_never_joined_onto_the_first():
    src = (LONG + " connection with FIR No. 12 of 2024 for offences punishable under Section 420 IPC.\n"
           "2. That this application is being moved before this Hon'ble Court under Section 483.\n")
    out, n = _unwrap_hard_wrapped_lines(src)
    assert n == 0 and out == src


def test_cause_title_and_party_block_untouched():
    src = ("**IN THE COURT OF THE SESSIONS JUDGE, PUNE**\n"
           "Bail Application No. [Case No.] of 2026\n"
           "Suresh Patil,\n"
           "Age: [Age] years, Occupation: [Occupation], presently confined at Yerwada Central Jail, Pune, Maharashtra\n"
           ".....Applicant/Accused\n"
           "**vs**\n"
           "State of Maharashtra\n"
           ".....Respondent\n")
    out, n = _unwrap_hard_wrapped_lines(src)
    assert n == 0 and out == src


def test_signature_and_verification_furniture_untouched():
    src = ("I, [Name of Applicant], the Applicant above-named, do hereby verify that the contents of the present\n"
           "application are true and correct to my personal knowledge, and no part thereof is false.\n"
           "Verified at [District] on this 10th day of September, 2026.\n"
           "Sd/-\n"
           "Applicant/Accused\n"
           "[Name of Applicant]\n"
           "Place: Pune\n"
           "Date: 10 September 2026\n")
    out, n = _unwrap_hard_wrapped_lines(src)
    lines = out.split("\n")
    assert n == 1                                   # only the wrapped verification sentence
    assert "personal knowledge, and no part" in lines[0]
    assert lines[1].startswith("Verified at")
    assert lines[2] == "Sd/-" and lines[3] == "Applicant/Accused"
    assert lines[4] == "[Name of Applicant]"
    assert lines[5].startswith("Place:") and lines[6].startswith("Date:")


def test_short_capitalised_line_after_sentence_end_is_not_a_wrapped_tail():
    src = ("The Applicant undertakes to abide by every condition this Hon'ble Court may impose while granting bail.\n"
           "DEPONENT\n")
    out, n = _unwrap_hard_wrapped_lines(src)
    assert n == 0


def test_short_lowercase_tail_is_joined():
    src = ("The Applicant undertakes to abide by every condition this Hon'ble Court may impose while granting\n"
           "bail to him.\n")
    out, n = _unwrap_hard_wrapped_lines(src)
    assert n == 1 and "while granting bail to him." in out


def test_nested_bullets_headings_and_tables_untouched():
    src = ("2. The Court clarified that the following rights remain open for adjudication before the civil court:\n"
           "   - easementary rights over the access road\n"
           "     - vehicular access including water supply lines\n"
           "## GROUNDS\n"
           "| Head | Detail |\n"
           "|---|---|\n"
           "| Penalty | up to 7 years imprisonment and fine under Section 318(4) of the Bharatiya Nyaya Sanhita |\n"
           "| Trial | by Magistrate of the first class in the ordinary course of criminal procedure under BNSS |\n")
    out, n = _unwrap_hard_wrapped_lines(src)
    assert n == 0 and out == src


def test_label_ending_in_colon_is_not_joined_with_its_value():
    src = ("The following documents are annexed hereto and relied upon by the Applicant in support of this application:\n"
           "Annexure A: copy of the FIR\n")
    out, n = _unwrap_hard_wrapped_lines(src)
    assert n == 0


def test_blank_lines_and_paragraph_breaks_preserved():
    src = LONG + "\ncustody since March.\n\n2. Second paragraph.\n"
    out, n = _unwrap_hard_wrapped_lines(src)
    assert n == 1 and "\n\n2. Second paragraph." in out


def test_validate_draft_end_to_end_on_screenshot_shape():
    src = ("**MOST RESPECTFULLY SHOWETH:**\n\n"
           + LONG + "\n"
           "connection with FIR No. [FIR No.] of [Year], registered at Police Station [Police Station], District\n"
           "[District], for offences punishable under Sections [Sections Charged] of the Bharatiya Nyaya Sanhita.\n\n"
           "2. That this application is being moved before this Hon'ble Court under Section 483 of the Bharatiya Nagarik\n"
           "Suraksha Sanhita, 2023, this Hon'ble Sessions Court being competent to entertain the same.\n\n"
           "## VERIFICATION\n\n"
           "Verified at [District] on this 10th day of September, 2026.\n\n"
           "Sd/-\n\nApplicant/Accused\n")
    r = validate_draft(src)
    out = r[0] if isinstance(r, tuple) else r
    body = [l for l in out.split("\n") if l.strip()]
    assert sum(1 for l in body if l.startswith("1. ")) == 1
    assert sum(1 for l in body if l.startswith("2. ")) == 1
    assert not any(l.startswith(("connection with", "[District]", "Suraksha Sanhita")) for l in body)
    assert "Sd/-" in body and "Applicant/Accused" in body
