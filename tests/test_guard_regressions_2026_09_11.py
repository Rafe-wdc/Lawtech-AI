"""Counter-examples the 2026-09-11 audit reproduced against this week's guards.

Each test is the exact input the audit used, pinned so the guard can never
regress into damaging legitimate legal text again. Pure tests.
"""
from __future__ import annotations

import asyncio

from agents.drafting import (
    _collapse_repeated_sections,
    _drop_reemitted_sections,
    _escape_stranded_numbers,
    _nest_subpoints_under_numbered_items,
    _unwrap_hard_wrapped_lines,
    validate_draft,
)


def _vd(text: str) -> str:
    r = validate_draft(text)
    return r[0] if isinstance(r, tuple) else r


# 1. a pair's own section is never dropped because it shares words with the title
def test_pairs_own_section_survives_title_overlap():
    already = ["application for anticipatory bail under section 482 bnss", "brief facts"]
    pair = "## GROUNDS FOR ANTICIPATORY BAIL\n\n1. The applicant apprehends arrest.\n\n## PRAYER\n\n(a) relief.\n"
    out, dropped = _drop_reemitted_sections(pair, planned=["Legal Grounds", "Prayer"], already=already)
    assert dropped == []
    assert "GROUNDS FOR ANTICIPATORY BAIL" in out and "apprehends arrest" in out


def test_genuine_reemission_is_still_dropped():
    already = ["brief facts of the case", "most respectfully showeth"]
    pair = "**MOST RESPECTFULLY SHOWETH:**\n\n1. repeated intro.\n\n## GROUNDS\n\n2. new.\n"
    out, dropped = _drop_reemitted_sections(pair, planned=["Grounds"], already=already)
    assert dropped == ["most respectfully showeth"] and "## GROUNDS" in out


# 2. the affidavit's caption and case number ahead of its heading are kept
def test_affidavit_caption_before_its_heading_is_kept():
    text = ("**IN THE COURT OF SESSIONS JUDGE, PUNE**\n\n**BAIL APPLICATION NO. 12 OF 2026**\n\n"
            "## GROUNDS\n\n1. ground.\n\n## PRAYER\n\nprayed.\n\n## VERIFICATION\n\nverified.\n\n"
            "**IN THE COURT OF SESSIONS JUDGE, PUNE**\n\n**BAIL APPLICATION NO. 12 OF 2026**\n\n"
            "Suresh Patil\n\n.....Applicant\n\n**vs**\n\nState\n\n.....Respondent\n\n"
            "## AFFIDAVIT IN SUPPORT\n\nI, Suresh Patil, affirm.\n\n## VERIFICATION\n\nverified again.\n")
    out, dropped = _collapse_repeated_sections(text)
    assert dropped == []
    assert out.count("**IN THE COURT OF SESSIONS JUDGE, PUNE**") == 2
    assert out.count("**BAIL APPLICATION NO. 12 OF 2026**") == 2


# 3. unwrap never merges regional-script furniture, labels, headings or Title Case lines
def test_unwrap_leaves_marathi_signature_block_alone():
    src = ("वरील सर्व बाबी माझ्या माहितीप्रमाणे खऱ्या असून त्या मी माझ्या मुवक्किलाच्या सूचनेच्या आधारे दिली आहे.\n"
           "ठिकाण: पुणे\nदिनांक: 11 सप्टेंबर 2026\nअर्जदार\n")
    out, n = _unwrap_hard_wrapped_lines(src)
    assert n == 0 and out == src


def test_unwrap_leaves_title_case_signatory_line_alone():
    src = ("For and on behalf of the Company, through its duly authorised representative and Authorised Signatory\n"
           "Authorised Signatory\n")
    out, n = _unwrap_hard_wrapped_lines(src)
    assert n == 0 and out == src


def test_unwrap_leaves_plain_caps_heading_alone():
    src = ("The applicant undertakes not to tamper with evidence or influence witnesses in any manner whatsoever.\n"
           "GROUNDS ON WHICH THE APPLICANT SEEKS ANTICIPATORY BAIL\n1. first ground.\n")
    out, n = _unwrap_hard_wrapped_lines(src)
    assert n == 0 and out == src


def test_unwrap_still_joins_a_real_wrapped_tail():
    src = ("The applicant undertakes not to tamper with evidence or influence witnesses in any manner\n"
           "whatsoever during the pendency of the trial.\n")
    out, n = _unwrap_hard_wrapped_lines(src)
    assert n == 1 and "any manner whatsoever during" in out


# 4. a spaced dash between numbers is a range, not an enumeration
def test_numeric_range_dash_becomes_hyphen_not_comma():
    out = _vd("The offences under Sections 138 – 142 of the Negotiable Instruments Act, 1881 for the period 2019 – 2023.\n")
    assert "Sections 138-142" in out and "2019-2023" in out
    assert "138, 142" not in out


def test_parenthetical_dash_still_becomes_comma():
    out = _vd("The accused – a first-time offender – deserves bail.\n")
    assert "accused, a first-time offender, deserves" in out


# 5. angle-bracket placeholders survive the HTML strip
def test_angle_bracket_placeholders_survive():
    out = _vd("IN THE COURT OF <Name of Court> AT <Place>, complainant <insert name>\n\n<br>Signed<b>x</b>\n")
    assert "<Name of Court>" in out and "<Place>" in out and "<insert name>" in out
    assert "<br>" not in out and "<b>" not in out


def test_terminal_strip_keeps_placeholders_too():
    from core.chat_runner import _strip_html_from_response
    out = _strip_html_from_response("IN THE COURT OF <Name of Court><br>next")
    assert "<Name of Court>" in out and "<br>" not in out


# 6. three-digit paragraph numbers in a long pleading are not escaped
def test_continuing_hundred_series_paragraphs_are_not_escaped():
    src = "99. That the defendant denies.\n100. That the defendant further denies.\n101. That further.\n"
    out, n = _escape_stranded_numbers(src)
    assert n == 0 and out == src


def test_stranded_year_is_still_escaped():
    src = "12. Arrested on 12 March\n2023. In custody since.\n"
    out, n = _escape_stranded_numbers(src)
    assert n == 1 and "2023\\. In custody" in out


# 7. a plain-caps heading between two clauses is a boundary, not a sub-point
def test_caps_heading_between_clauses_is_not_nested():
    src = "8. The applicant has no antecedents.\n\nGROUNDS FOR BAIL\n\n9. First ground.\n"
    out, n = _nest_subpoints_under_numbered_items(src)
    assert n == 0 and out == src


def test_bullets_between_clauses_are_still_nested():
    src = "10. Contributions:\n\n- First Partner: Rs. 1\n- Second Partner: Rs. 2\n\n11. Next.\n"
    out, n = _nest_subpoints_under_numbered_items(src)
    assert n == 2 and "\n    - First Partner" in out


# 8. an acknowledgement on a thread with restored files must not add Document
def test_conversational_followup_never_adds_document_agent():
    import inspect
    import agents.orchestrator as orch
    src = inspect.getsource(orch)
    assert 'and task != "Non_legal" and not state.get("conversational_followup")' in src
