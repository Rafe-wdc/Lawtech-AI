"""Advocate mark-up on a medical-negligence plaint, 2026-09-13, pinned.

1. Planner section labels are never printed as document headings.
2. The court name in the caption is bold.
3. The suit valuation is stated once, in the court-fee paragraph, not also
   in the title block.
Pure tests, no LLM.
"""
from __future__ import annotations

from agents.drafting import (
    _bold_court_caption,
    _drop_duplicate_valuation_block,
    _strip_planner_label_headings,
    validate_draft,
)

LAWYER_SHAPE = (
    "## Court Heading, Cause Title, and Description of Parties\n\n"
    "IN THE COURT OF THE CIVIL JUDGE SENIOR DIVISION, PUNE\n\nAT PUNE\n\n"
    "SPECIAL CIVIL SUIT NO. [Suit No.] OF 2024\n\n"
    "Mrs. Anjali Deshmukh, Age: 42 years, Occupation: [Occupation], Residing at: [Residential Address], Pune, Maharashtra.\n\n"
    ".....Plaintiff\n\n**vs**\n\n"
    "Dr. Rakesh Nair, Age: [Age] years, Occupation: Medical Practitioner, XYZ Multispecialty Hospital, Pune.\n\n.....Defendant No. 1\n\n"
    "SUIT UNDER SECTION 9 AND ORDER VII RULES 1 AND 2 OF THE CODE OF CIVIL PROCEDURE, 1908 READ WITH THE LAW OF TORTS FOR RECOVERY OF DAMAGES AND COMPENSATION AMOUNTING TO RS. 25,00,000/- ON ACCOUNT OF GROSS MEDICAL NEGLIGENCE\n\n"
    "SUIT VALUATION FOR THE PURPOSES OF JURISDICTION AND COURT FEES: Valuation for Jurisdiction: Rs. 25,00,000/- Valuation for Court Fees: Rs. 25,00,000/- Court fee paid under the Maharashtra Court Fees Act, 1959: Rs. [Court Fee Paid]/-\n\n"
    "The Plaintiff above-named most respectfully submits as under:\n\n"
    "## Statement of Facts and Chronology of Medical Negligence\n\n"
    "1. The Plaintiff above-named is a respectable citizen residing at Pune within the jurisdiction of this Hon'ble Court.\n\n"
    "## Grounds of Negligence, Breach of Duty of Care, and Legal Precedents\n\n"
    "(a) Existence of an absolute legal duty of care owed by both Defendants to the Plaintiff.\n\n"
    "## Cause of Action, Limitation, Court Fees, and Territorial Jurisdiction\n\n"
    "36. The suit has been valued at Rs. 25,00,000/- for the purposes of court fees and jurisdiction. Court fee of Rs. [Court Fee Paid]/- has been paid under the Maharashtra Court Fees Act, 1959.\n\n"
    "## Reliefs Claimed (Prayer), Verification, and List of Documents\n\n"
    "### Prayer\n\n(a) pass a decree for Rs. 25,00,000/-;\n\n"
    "### Verification\n\nI, Mrs. Anjali Deshmukh, verify that the contents of paragraphs 1 to 29 are true.\n\n"
    "### List of Documents Relied Upon\n\n1. Case papers issued by Defendant No. 2.\n"
)


def _vd(text: str) -> str:
    r = validate_draft(text)
    return r[0] if isinstance(r, tuple) else r


# 1. planner labels
def test_opening_block_label_is_removed():
    out, n = _strip_planner_label_headings(LAWYER_SHAPE)
    assert "Court Heading, Cause Title, and Description of Parties" not in out
    assert out.lstrip().startswith("IN THE COURT OF THE CIVIL JUDGE SENIOR DIVISION, PUNE")


def test_composite_labels_are_removed_and_conventional_headings_kept():
    out, n = _strip_planner_label_headings(LAWYER_SHAPE)
    for gone in ("Reliefs Claimed (Prayer), Verification, and List of Documents",
                 "Cause of Action, Limitation, Court Fees, and Territorial Jurisdiction",
                 "Grounds of Negligence, Breach of Duty of Care, and Legal Precedents",
                 "Statement of Facts and Chronology of Medical Negligence"):
        assert gone not in out, gone
    for kept in ("### Prayer", "### Verification", "### List of Documents Relied Upon"):
        assert kept in out, kept
    assert n == 5


def test_conventional_pleading_headings_are_never_removed():
    src = ("## SYNOPSIS AND LIST OF DATES\n\ntext\n\n## QUESTIONS OF LAW\n\ntext\n\n## GROUNDS FOR BAIL\n\ntext\n\n"
           "## PRAYER\n\ntext\n\n## VERIFICATION\n\ntext\n\n## AFFIDAVIT IN SUPPORT\n\ntext\n")
    out, n = _strip_planner_label_headings(src)
    assert n == 0 and out == src


def test_memo_of_parties_label_is_removed():
    out, n = _strip_planner_label_headings("## Cause Title and Memo of Parties\n\nIN THE HIGH COURT OF DELHI\n")
    assert n == 1 and out.lstrip().startswith("IN THE HIGH COURT")


# 2. caption bold
def test_court_name_is_bolded():
    out, n = _bold_court_caption("IN THE COURT OF THE CIVIL JUDGE SENIOR DIVISION, PUNE\n\nAT PUNE\n")
    assert n == 1 and out.startswith("**IN THE COURT OF THE CIVIL JUDGE SENIOR DIVISION, PUNE**")


def test_already_bold_or_heading_caption_is_untouched():
    for src in ("**IN THE HIGH COURT OF DELHI AT NEW DELHI**\n", "## IN THE SUPREME COURT OF INDIA\n"):
        out, n = _bold_court_caption(src)
        assert n == 0 and out == src


# 3. duplicate valuation
def test_title_block_valuation_removed_when_court_fee_paragraph_exists():
    out, n = _drop_duplicate_valuation_block(LAWYER_SHAPE)
    assert n == 1
    assert "SUIT VALUATION FOR THE PURPOSES OF JURISDICTION AND COURT FEES" not in out
    assert "The suit has been valued at Rs. 25,00,000/-" in out
    assert "SUIT UNDER SECTION 9 AND ORDER VII" in out          # the suit description stays


def test_valuation_block_kept_when_it_is_the_only_valuation():
    src = ("IN THE COURT OF THE CIVIL JUDGE, PUNE\n\nSUIT VALUATION FOR THE PURPOSES OF JURISDICTION AND COURT FEES: Rs. 5,00,000/-\n\n"
           "1. Facts.\n\n## PRAYER\n\ndecree.\n")
    out, n = _drop_duplicate_valuation_block(src)
    assert n == 0 and out == src


# end to end
def test_validate_draft_applies_all_three_on_the_lawyer_shape():
    out = _vd(LAWYER_SHAPE)
    assert "Court Heading, Cause Title" not in out
    assert "**IN THE COURT OF THE CIVIL JUDGE SENIOR DIVISION, PUNE**" in out
    assert out.count("Valuation for Jurisdiction") == 0
    assert "The suit has been valued at Rs. 25,00,000/-" in out
    assert "### Prayer" in out and "### Verification" in out


def test_section_pair_rule_and_plaint_overlay_carry_the_guidance():
    from config.prompts import DRAFTING_SECTION_PAIR_PROMPT as P
    from config.drafting_niches import PLAINT_CIVIL_SUIT as O
    assert "do NOT print the planner's label" in P
    assert "State the valuation ONCE" in O
