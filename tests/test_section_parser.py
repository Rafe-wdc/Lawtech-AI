"""Tests for tools/inline/section_parser.parse_multi_section_info.

Locks in the year-not-as-section contract. The parser used to treat the
year part of an act name (e.g. "1908" in "Code of Civil Procedure 1908")
as a section number, dragging in unrelated docs and causing the agent
to fall back to web search.
"""

from __future__ import annotations

from tools.inline.section_parser import parse_multi_section_info


class TestYearNotASection:
    """Years like 1908 in act names must NOT be parsed as section numbers."""

    def test_cpc_section_9_with_year_in_act_name(self):
        # The bug case from the live load test.
        info = parse_multi_section_info(
            "What does Section 9 of the Code of Civil Procedure 1908 say?"
        )
        assert info is not None
        assert info["section_numbers"] == ["9"], (
            f"Year-as-section regression: {info['section_numbers']}"
        )

    def test_ipc_section_with_year(self):
        info = parse_multi_section_info(
            "Explain Section 302 of the Indian Penal Code 1860"
        )
        assert info is not None
        assert info["section_numbers"] == ["302"]

    def test_modern_act_with_year(self):
        info = parse_multi_section_info(
            "Section 138 of the Negotiable Instruments Act 1881"
        )
        assert info is not None
        assert info["section_numbers"] == ["138"]

    def test_iea_with_year_2023(self):
        # BSA 2023 case — defensive year filter must catch 2023 too.
        info = parse_multi_section_info(
            "Section 25 of the Bharatiya Sakshya Adhiniyam 2023"
        )
        assert info is not None
        assert "2023" not in info["section_numbers"]
        assert info["section_numbers"] == ["25"]

    def test_under_phrasing(self):
        # "under" should act as a boundary just like "of".
        info = parse_multi_section_info(
            "What punishment is prescribed under Section 138 NI Act 1881?"
        )
        assert info is not None
        assert info["section_numbers"] == ["138"]


class TestStillCorrectOnLegitMultiSection:
    """Multi-section queries that legitimately list several numbers must
    continue to work after the boundary cut-off."""

    def test_two_sections(self):
        info = parse_multi_section_info(
            "Sections 302 and 307 of the IPC"
        )
        assert info is not None
        assert set(info["section_numbers"]) == {"302", "307"}

    def test_three_sections_comma(self):
        info = parse_multi_section_info(
            "Sections 302, 307 and 420 of the Indian Penal Code 1860"
        )
        assert info is not None
        # 1860 must NOT appear.
        assert "1860" not in info["section_numbers"]
        assert set(info["section_numbers"]) == {"302", "307", "420"}

    def test_range(self):
        info = parse_multi_section_info(
            "Sections 10 to 15 of the Companies Act 2013"
        )
        assert info is not None
        assert "2013" not in info["section_numbers"]
        assert info["section_numbers"] == [
            "10", "11", "12", "13", "14", "15",
        ]

    def test_single_section_no_act_name(self):
        # No "of" — falls through to the basic-regex fallback path.
        info = parse_multi_section_info("Section 9 explained")
        assert info is not None
        assert info["section_numbers"] == ["9"]

    def test_section_with_subsection(self):
        info = parse_multi_section_info(
            "Section 302(1) of BNS"
        )
        assert info is not None
        assert info["section_numbers"] == ["302"]
        assert info["subsections"] == {"302": "1"}


class TestYearFilterEdgeCases:

    def test_19th_century_year(self):
        # 1860 is in 1[89]\d{2} range.
        info = parse_multi_section_info(
            "Section 1 of the Indian Penal Code 1860"
        )
        assert "1860" not in (info["section_numbers"] if info else [])

    def test_21st_century_year(self):
        info = parse_multi_section_info(
            "Section 7 of the Companies Act 2013"
        )
        assert "2013" not in (info["section_numbers"] if info else [])

    def test_4digit_non_year_still_works_pre_boundary(self):
        # If for some reason a real section number happens to look like a
        # year (unlikely in practice — no real Indian statute has 1908 sections),
        # the user would NOT include it after an "of" boundary. Test that
        # legitimate large section numbers BEFORE the boundary work.
        info = parse_multi_section_info("Section 5000 explained")
        assert info is not None
        # 5000 doesn't match 1[89]\d{2}|20\d{2} so it survives.
        assert info["section_numbers"] == ["5000"]
