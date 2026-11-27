"""A section keyword that is not followed by an identifier does not start the
section scan.

"Vide order dated 12/11/2024 below Exh. 05 ... Can the bank proceed under
Section 13(4) or Section 14 of the SARFAESI Act?" parsed as Sections 12, 11
and 05: the first keyword in the text was "order" (of "order dated"), the
scan ran from there up to the first "of/in/under", and the date and the
exhibit number became section numbers while the query's own Section 13(4)
was ignored. Seen live on dev and prod on 2026-11-25 (lawyer test report:
the Legislation agent showed "Detected sections: 12, 11, 05").

The act name also stops at the year: "Interest Act 2002 action on undivided
ancestral property ..." used to carry the rest of the sentence into the ES
`source` match.
"""

from __future__ import annotations

from tools.inline.section_parser import parse_multi_section_info, parse_section_info

SARFAESI = (
    "Vide order dated 12/11/2024 below Exh. 05, the Hon'ble Civil Court allowed the "
    "Temporary Injunction application. The brother obtained a loan by pledging the "
    "undivided share. Can the bank proceed with physical or symbolic possession under "
    "Section 13(4) or Section 14 of the SARFAESI Act while a partition suit is pending?"
)


def test_order_dated_does_not_turn_the_date_into_sections():
    parsed = parse_multi_section_info(SARFAESI)
    assert parsed["section_type"] == "section"
    assert parsed["section_numbers"] == ["13", "14"]
    assert parsed["subsections"] == {"13": "4"}


def test_prose_keyword_before_the_reference_is_skipped():
    parsed = parse_multi_section_info(
        "The order was passed on 5 March. Section 438 CrPC anticipatory bail")
    assert parsed["section_type"] == "section"
    assert parsed["section_numbers"] == ["438"]

    parsed = parse_multi_section_info(
        "What is the procedure under Section 14 SARFAESI for taking possession")
    assert parsed["section_type"] == "section"
    assert parsed["section_numbers"] == ["14"]

    parsed = parse_multi_section_info(
        "process server did not serve summons under Order 5 Rule 9 CPC")
    assert parsed["section_type"] == "order"
    assert parsed["section_numbers"] == ["5", "9"]


def test_keyword_only_queries_still_return_none():
    for q in (
        "Which section of IPC applies to cheating?",
        "In 2024 the court passed an order. Which section applies for cheating under BNS?",
        "The order dated 12/11/2024 is under challenge",
    ):
        assert parse_multi_section_info(q) is None, q
        assert parse_section_info(q) is None, q


def test_act_name_stops_after_the_year():
    assert parse_multi_section_info(
        "Section 9 of the Code of Civil Procedure 1908 and section 10"
    )["act_name"] == "code of civil procedure 1908"
    assert parse_multi_section_info(
        "Section 80C of the Income Tax Act 1961 deductions for 2024"
    )["act_name"] == "income tax act 1961"
    assert parse_multi_section_info(
        "Rule 3(2) of the Maharashtra Rent Control Rules 2000 notice period"
    )["act_name"] == "maharashtra rent control rules 2000"
    # The comma form drops the year as before.
    assert parse_multi_section_info(
        "Article 113 of the Limitation Act, 1963"
    )["act_name"] == "limitation act"


def test_act_name_stops_after_the_year_across_line_breaks():
    # The lawyer's question as the Legislation agent saw it (SARFAESI spelled
    # out): the act name ran past the subject line into "Dear Advocate".
    parsed = parse_multi_section_info(
        "Subject: Legal Query Regarding Bank's Securitisation and Reconstruction of "
        "Financial Assets and Enforcement of Securities Interest Act 2002 Action on "
        "Undivided Ancestral Property (Spl. C. S. No. 994/2024)\n"
        "Dear Advocate, the bank wants possession under Section 13(4) or Section 14."
    )
    assert parsed["section_numbers"] == ["13", "14"]
    assert parsed["act_name"] == (
        "financial assets and enforcement of securities interest act 2002")


def test_plain_references_are_unchanged():
    assert parse_multi_section_info("Section 138 of NI Act")["section_numbers"] == ["138"]
    assert parse_multi_section_info(
        "Sections 302, 307 and 420 of IPC")["section_numbers"] == ["302", "307", "420"]
    assert parse_multi_section_info(
        "Sections 10 to 15 of Companies Act 2013")["section_numbers"] == [
        "10", "11", "12", "13", "14", "15"]
    assert parse_multi_section_info("Order XXXIX of CPC")["section_numbers"] == ["xxxix"]
    assert parse_multi_section_info("Schedule A of the Act")["section_numbers"] == ["a"]
    assert parse_section_info("Order VII Rule 11 of CPC")["section_number"] == "vii"
