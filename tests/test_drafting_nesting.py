"""Sub-points left at the margin between two numbered clauses are nested
under the first clause; everything after the last clause is left alone.

Advocate screenshot 2026-09-10, partnership deed: clause 10 -> three
left-margin bullets -> left-margin continuation paragraph -> clause 11.
Markdown ended clause 10 at the first bullet, so the bullets rendered as
their own list and the paragraph as loose text. Pure tests.
"""
from __future__ import annotations

from agents.drafting import _nest_subpoints_under_numbered_items as nest, validate_draft

DEED = (
    "10. **Initial Capital Contribution.** The Partners shall contribute in the following amounts and proportions:\n"
    "\n"
    "- First Partner: Rs. [Capital Contribution of First Partner]\n"
    "- Second Partner: Rs. [Capital Contribution of Second Partner]\n"
    "- Third Partner: Rs. [Capital Contribution of Third Partner]\n"
    "\n"
    "The aforesaid capital shall be brought in by the respective Partners in cash or by cheque on or before [Date].\n"
    "\n"
    "11. **Further Contribution.** Where the said Firm requires further capital, the Partners shall contribute.\n"
)


def test_screenshot_shape_is_nested_under_clause_ten():
    out, n = nest(DEED)
    lines = out.split("\n")
    assert n == 4
    assert lines[2] == "    - First Partner: Rs. [Capital Contribution of First Partner]"
    assert lines[4].startswith("    - Third Partner")
    assert lines[6].startswith("    The aforesaid capital")
    assert lines[8].startswith("11. ")                      # next clause untouched


def test_renders_as_one_list_item_with_a_nested_list():
    from markdown_it import MarkdownIt
    out, _ = nest(DEED)
    html = MarkdownIt("commonmark").render(out)
    assert html.count("<ol") == 1                           # one ordered list, not two
    assert '<ol start="11"' not in html                     # 11 continues the same list
    assert "<ul>" in html and html.index("<ul>") < html.index("Further Contribution")


def test_content_after_the_last_clause_is_never_pulled_in():
    src = ("14. **Dissolution.** The Firm may be dissolved by mutual consent of all the Partners.\n"
           "\n"
           "IN WITNESS WHEREOF the Partners have set their hands on the day and year first above written.\n"
           "\n"
           "Signed by the First Partner\n")
    out, n = nest(src)
    assert n == 0 and out == src


def test_gap_with_heading_is_left_alone():
    src = ("3. Third clause.\n\n## SCHEDULE\n\nProperty description at the margin.\n\n4. Fourth clause.\n")
    out, n = nest(src)
    assert n == 0 and out == src


def test_gap_with_dot_leader_or_bold_label_is_left_alone():
    src = "1. First.\n\n.....Applicant\n\n**vs**\n\n2. Second.\n"
    out, n = nest(src)
    assert n == 0 and out == src


def test_already_indented_gap_is_untouched():
    src = "1. First:\n   - a\n   - b\n2. Second.\n"
    out, n = nest(src)
    assert n == 0 and out == src


def test_marker_width_drives_the_indent():
    out, _ = nest("9. Nine:\n\n- x\n\n10. Ten.\n")
    assert "\n   - x\n" in out                              # "9. " -> 3 spaces
    out, _ = nest("10. Ten:\n\n- x\n\n11. Eleven.\n")
    assert "\n    - x\n" in out                             # "10. " -> 4 spaces


def test_validate_draft_end_to_end_keeps_one_ordered_list():
    from markdown_it import MarkdownIt
    r = validate_draft(DEED)
    out = r[0] if isinstance(r, tuple) else r
    html = MarkdownIt("commonmark").render(out)
    assert html.count("<ol") == 1
    assert "<ul>" in html
