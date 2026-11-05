"""A party's name must not be glued onto the cause-title line above it.

Dev box 2026-10-06, bail application: the cause title read
    Under Sections: Section 318(4) and Section 316(2) of the Bharatiya Nyaya Sanhita, 2023
    <blank line>
    Mr. Rahul Deshpande
and `_rejoin_blankline_fragments` joined the two, because the first line is
long and does not end a sentence. A short Title Case line is a new
cause-title line, not the tail of a sentence.
"""

from __future__ import annotations

from agents.drafting import _rejoin_blankline_fragments, validate_draft

LONG = "Under Sections: Section 318(4) and Section 316(2) of the Bharatiya Nyaya Sanhita, 2023"


def test_party_name_after_a_blank_line_stays_on_its_own_line():
    for name in ("Mr. Anil Joshi", "M/s Sharma Traders,", "Smt. Anita R. Kulkarni"):
        out, joins = _rejoin_blankline_fragments(f"{LONG}\n\n{name}\nAge: 34 years")
        assert joins == 0 and f"2023\n\n{name}" in out, name


def test_validate_draft_keeps_the_cause_title_lines_apart():
    md = (f"**IN THE COURT OF SESSIONS AT PUNE**\n\nIN THE MATTER OF:\n{LONG}\n\n"
          "Mr. Anil Joshi\nAge: 34 years\n\n.....Applicant / Accused\n")
    out, _ = validate_draft(md)
    assert "2023 Mr. Anil Joshi" not in out
    assert any(l.strip() == "Mr. Anil Joshi" for l in out.splitlines())


def test_the_cases_the_join_was_written_for_still_join():
    # a blank line inside an open bracket
    a = ("I, [Name of Petitioner], aged about 45 years, presently residing at [Address of\n\n"
         "Petitioner], the Petitioner above-named, do hereby solemnly affirm as under:")
    out, joins = _rejoin_blankline_fragments(a)
    assert joins == 1 and "[Address of Petitioner], the Petitioner" in out
    # a sentence that plainly continues
    b = ("The Petitioner states that the entries were carried forward every year between 2020 and\n\n"
         "2025 without any inquiry by the revenue authorities.")
    out, joins = _rejoin_blankline_fragments(b)
    assert joins == 1 and "between 2020 and 2025 without" in out
    # a continuation that starts with a proper noun but is a long line of prose
    c = ("I state that the contents of paragraphs 1 to 12 of the accompanying Special\n\n"
         "Leave Petition are true to my knowledge and the rest are submissions on legal advice.")
    out, joins = _rejoin_blankline_fragments(c)
    assert joins == 1 and "Special Leave Petition are true" in out
