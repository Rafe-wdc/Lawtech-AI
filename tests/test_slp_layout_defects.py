"""Five layout defects from an advocate's SLP on 2026-09-11, pinned.

A. a hard-wrapped year alone on a line is never deleted as an empty paragraph
B. fragments split by a blank line are rejoined when the first ends mid-sentence
C. a single-pipe two-column line renders with a slash, never a raw pipe
D. a heading that merely restates the caption after it is dropped
E. a caps ground heading glued to its paragraph number is split
Pure tests, no LLM.
"""
from __future__ import annotations

from agents.drafting import (
    _drop_heading_that_restates_next_line,
    _rejoin_blankline_fragments,
    _split_ground_heading_from_paragraph,
    _split_single_pipe_lines,
    validate_draft,
)


def _vd(text: str) -> str:
    r = validate_draft(text)
    return r[0] if isinstance(r, tuple) else r


# A + B: the reported content loss
def test_year_stranded_after_blank_line_is_rejoined_not_deleted():
    src = ("Following the discharge order, the investigating authorities lapsed into total slumber and institutional inertia for more than five years between 2020 and\n\n"
           "2025.\n\nNo application was ever moved by the prosecution seeking extension of time.\n")
    out = _vd(src)
    assert "between 2020 and 2025." in out
    assert "No application was ever moved" in out


def test_genuine_empty_paragraph_number_is_still_removed():
    out = _vd("1. First averment about the facts of the matter in enough detail to be a real paragraph.\n\n2.\n\n3. Third averment.\n")
    assert "\n2.\n" not in out and "3. Third averment." in out


def test_verification_fragments_with_open_bracket_are_rejoined():
    src = ("I, [Name of Petitioner], son of [Father's Name], aged about [Age] years, presently residing at [Address of\n\n"
           "Petitioner], the Petitioner above-named, do hereby state and verify as under:\n\n"
           "That the contents of paragraphs 1 to 15 and the accompanying Synopsis and List of Dates of the accompanying Special\n\n"
           "Leave Petition are true and correct to my personal knowledge.\n")
    out, n = _rejoin_blankline_fragments(src)
    assert n == 2
    assert "residing at [Address of Petitioner], the Petitioner" in out
    assert "accompanying Special Leave Petition are true" in out


def test_blankline_rejoin_never_crosses_a_real_paragraph_boundary():
    src = ("The prosecution stems from an alleged sting operation staged in the year 2007, resulting in FIR No. 114/2008.\n\n"
           "Following a protracted investigation, the Special Judge discharged the Petitioner on the date of the discharge order.\n\n"
           "DEPONENT\n\nAFFIDAVIT\n\nI, the Petitioner, do hereby solemnly affirm and state as under:\n\n"
           "That I am the Petitioner in the accompanying Special Leave Petition and am fully conversant with the facts.\n")
    out, n = _rejoin_blankline_fragments(src)
    assert n == 0 and out == src


def test_blankline_rejoin_skips_headings_lists_and_caps_lines():
    src = ("## GROUNDS FOR SPECIAL LEAVE TO APPEAL ON THE FOLLOWING AMONGST OTHER GROUNDS URGED\n\n"
           "because the High Court erred.\n\n"
           "1. First numbered ground with enough words to be a long line for the purposes of this test here\n\n"
           "- a bullet\n")
    out, n = _rejoin_blankline_fragments(src)
    assert n == 0 and out == src


# C
def test_position_of_parties_pipe_becomes_a_slash():
    src = ("IN THE MATTER OF: POSITION OF PARTIES: In the High Court | In this Court\n\n"
           "[Name of Petitioner] [Address of Petitioner] .....Petitioner |.....Petitioner\n\n"
           "State (NCT of Delhi) .....Respondent |.....Respondent\n")
    out, n = _split_single_pipe_lines(src)
    assert n == 3 and "|" not in out
    assert ".....Petitioner / .....Petitioner" in out
    assert "In the High Court / In this Court" in out


def test_real_table_rows_are_not_touched_by_the_pipe_rule():
    src = "| Head A | Head B |\n|---|---|\n| a | b |\n"
    out, n = _split_single_pipe_lines(src)
    assert n == 0 and out == src


# D
def test_heading_restating_the_caption_is_dropped():
    src = ("## IN THE SUPREME COURT OF INDIA (CRIMINAL APPELLATE JURISDICTION) - SPECIAL LEAVE PETITION (CRL.)\n\n"
           "IN THE SUPREME COURT OF INDIA (CRIMINAL APPELLATE JURISDICTION) SPECIAL LEAVE PETITION (CRIMINAL) NO. [SLP No.] OF 2026\n\n"
           "(Under Article 136 of the Constitution of India)\n")
    out, n = _drop_heading_that_restates_next_line(src)
    assert n == 1
    assert out.count("IN THE SUPREME COURT OF INDIA") == 1
    assert out.lstrip().startswith("IN THE SUPREME COURT OF INDIA (CRIMINAL APPELLATE JURISDICTION) SPECIAL LEAVE PETITION (CRIMINAL) NO.")


def test_ordinary_heading_before_body_text_is_kept():
    src = "## GROUNDS FOR DISCHARGE\n\nThe applicant submits the following grounds for discharge from the case.\n"
    out, n = _drop_heading_that_restates_next_line(src)
    assert n == 0 and out == src


# E
def test_ground_heading_is_separated_from_its_paragraph_number():
    src = ("A. BECAUSE THE HON'BLE HIGH COURT ERRED IN REFUSING AD-INTERIM STAY OF TRIAL PROCEEDINGS UNDER SECTION 528 OF THE "
           "BHARATIYA NAGARIK SURAKSHA SANHITA, 2023. 17. The Hon'ble High Court of Delhi failed to exercise its discretion.\n")
    out, n = _split_ground_heading_from_paragraph(src)
    assert n == 1
    assert "SANHITA, 2023.\n\n17. The Hon'ble High Court" in out


def test_ground_heading_without_inline_number_is_untouched():
    src = "A. BECAUSE THE HIGH COURT ERRED IN REFUSING INTERIM PROTECTION TO THE PETITIONER.\n\n17. The Hon'ble High Court failed.\n"
    out, n = _split_ground_heading_from_paragraph(src)
    assert n == 0 and out == src
