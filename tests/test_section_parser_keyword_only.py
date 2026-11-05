"""A section keyword followed by an ordinary word is not a section reference.

"Which section of IPC applies to cheating?" parsed as Section "o" (the first
letter of "of"), so the legislation search looked for a section that does not
exist and fell back to the web (seen live 2026-09-17, still on dev 2026-10-06).
"""

from __future__ import annotations

from tools.inline.section_parser import parse_multi_section_info, parse_section_info


def test_keyword_followed_by_an_ordinary_word_is_not_a_reference():
    for q in (
        "Which section of IPC applies to cheating?",
        "Explain the Section on anticipatory bail",
        "Cite the Article of the Limitation Act, 1963",
        "which section i should use for theft",
        "under which section a person can be arrested",
    ):
        assert parse_multi_section_info(q) is None, q
        assert parse_section_info(q) is None, q


def test_numeric_references_are_unchanged():
    assert parse_multi_section_info("Section 138 of NI Act")["section_numbers"] == ["138"]
    assert parse_multi_section_info(
        "Sections 302, 307 and 420 of IPC")["section_numbers"] == ["302", "307", "420"]
    assert parse_multi_section_info("Section 318(4) BNS")["section_numbers"] == ["318"]
    assert parse_multi_section_info(
        "Article 113 of the Limitation Act, 1963")["section_numbers"] == ["113"]
    assert parse_section_info("Section 138 of NI Act")["section_number"] == "138"


def test_letter_and_roman_identifiers_still_parse():
    assert parse_multi_section_info("Section 65B of Evidence Act")["section_numbers"] == ["65b"]
    assert parse_multi_section_info("Article 21A of the Constitution")["section_numbers"] == ["21a"]
    assert parse_multi_section_info("Schedule A of the Act")["section_numbers"] == ["a"]
    assert parse_multi_section_info("Schedule I to the Act")["section_numbers"] == ["i"]
    assert parse_multi_section_info("Order XXXIX of CPC")["section_numbers"] == ["xxxix"]
    assert parse_section_info("Order VII Rule 11 of CPC")["section_number"] == "vii"
