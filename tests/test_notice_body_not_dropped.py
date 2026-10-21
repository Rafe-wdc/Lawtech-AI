"""The repeated-section cleanup removes genuine duplicates and nothing else.

Dev 2026-10-19, reproduced locally (1 of 8 identical legal-notice requests):
the model wrote a 4,742-char notice and the user received 396 chars. The log
read "Removed repeated section(s): subject: legal notice demanding payment of
outstanding arrears of rent ...". The title "LEGAL NOTICE" is contained in
the subject line, so the subject was taken for a repeat of the title and
dropped together with every line down to the signature. In other runs the
signature's "**[Advocate Name]**", already printed in the From block, was
dropped as a repeat and the notice ended at "(Signature)".

The duplicate removal itself (a second PRAYER / VERIFICATION / SHOWETH, a
paraphrased repeat) must keep working; see also
test_drafting_no_repeated_sections.py.
"""

from __future__ import annotations

import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import pytest

from agents.drafting import _collapse_repeated_sections, validate_draft

BODY = "\n\n".join(
    f"{i}. That my client states the facts of paragraph {i} in full, with dates and amounts."
    for i in range(1, 9)
)


def _notice(subject_line: str) -> str:
    return f"""**REGISTERED POST A.D. / SPEED POST**

## LEGAL NOTICE

**Date:** 19 October 2026

**To:**

Suresh Patil

Mumbai, Maharashtra

**From:**

Advocate [Advocate's Name]

{subject_line}

Sir,

Under instructions from my client, Ramesh Kumar, I serve you with the following legal notice:

{BODY}

You are therefore called upon to pay Rs. 2,00,000/- within 15 days of receipt of this notice.

**Advocate [Advocate's Name]**

Counsel for Ramesh Kumar
"""


# --- not duplicates: kept ---------------------------------------------------------

@pytest.mark.parametrize("subject_line", [
    "**Subject: Legal Notice demanding payment of outstanding arrears of rent amounting to Rs. 2,00,000/-**",
    "### SUBJECT: LEGAL NOTICE FOR RECOVERY OF ARREARS OF RENT",
    "**Sub: Legal notice for arrears of rent**",
])
def test_subject_line_containing_the_title_keeps_the_body(subject_line):
    notice = _notice(subject_line)
    cleaned, warnings = validate_draft(notice)
    assert "8. That my client states the facts of paragraph 8" in cleaned
    assert "within 15 days of receipt" in cleaned
    assert len(cleaned) > 0.9 * len(notice)
    assert not any("Removed repeated section" in w for w in warnings)


@pytest.mark.parametrize("name", ["[Advocate Name]", "Rahul Sharma"])
def test_signature_name_already_in_the_from_block_is_kept(name):
    notice = (f"## LEGAL NOTICE\n\n**From:**\n\n**{name}**\n\nAdvocate, Pune\n\n{BODY}\n\n"
              f"Yours faithfully,\n\n(Signature)\n\n**{name}**\n\nAdvocate\n\n"
              "Counsel for Ramesh Kumar\n")
    cleaned, dropped = _collapse_repeated_sections(notice)
    assert dropped == [] and cleaned.rstrip().endswith("Counsel for Ramesh Kumar")


def test_a_repeat_far_larger_than_its_original_is_kept():
    draft = ("## LEGAL NOTICE DEMAND\n\nTo: X\n\n"
             "## LEGAL NOTICE DEMAND FOR ARREARS\n\n" + BODY * 2)
    cleaned, dropped = _collapse_repeated_sections(draft)
    assert dropped == [] and BODY in cleaned


# --- genuine duplicates: still removed ----------------------------------------------

def test_a_repeated_prayer_is_removed():
    draft = (
        "## FACTS\n\n" + BODY + "\n\n## PRAYER\n\n(a) Grant bail.\n\n(b) Costs.\n\n"
        "## VERIFICATION\n\nVerified at Pune.\n\n## PRAYER\n\n(a) Grant bail to the applicant.\n"
    )
    cleaned, dropped = _collapse_repeated_sections(draft)
    assert dropped == ["prayer"]
    assert cleaned.count("## PRAYER") == 1 and "Grant bail to the applicant" not in cleaned


def test_a_repeated_verification_with_place_and_date_is_removed():
    draft = ("## FACTS\n\n" + BODY + "\n\n## VERIFICATION\n\nVerified at Pune.\n\nPlace: Pune\n\n"
             "Date: 19.10.2026\n\n## VERIFICATION\n\nVerified again at Pune.\n\nPlace: Pune\n")
    cleaned, dropped = _collapse_repeated_sections(draft)
    assert dropped == ["verification"] and "Verified again" not in cleaned


def test_a_paraphrased_heading_is_still_a_repeat():
    draft = ("## BRIEF FACTS\n\n" + BODY + "\n\n## GROUNDS\n\nGround A.\n\n"
             "## BRIEF FACTS OF THE CASE\n\nA short repeat.\n")
    cleaned, dropped = _collapse_repeated_sections(draft)
    assert dropped == ["brief facts of the case"]


def test_a_title_case_bold_heading_repeat_is_removed():
    draft = ("**Brief Facts of the Case**\n\n" + BODY + "\n\n## GROUNDS\n\nGround A.\n\n"
             "**Brief Facts of the Case**\n\nA short paraphrased repeat.\n")
    cleaned, dropped = _collapse_repeated_sections(draft)
    assert dropped == ["brief facts of the case"] and "paraphrased repeat" not in cleaned
