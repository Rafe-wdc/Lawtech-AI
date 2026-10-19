"""validate_draft must not delete a notice's body as a "repeated section".

Dev 2026-10-19, reproduced locally (1 of 8 identical requests): a 4,742-char
legal notice was cut to 396 chars after generation. The log read "Removed
repeated section(s): subject: legal notice demanding payment of outstanding
arrears of rent ...". The title "## LEGAL NOTICE" was contained in the bold
subject line, so the subject was taken for a repeat of the title and dropped
together with every line down to the signature.
"""

from __future__ import annotations

import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

from agents.drafting import _collapse_repeated_sections, validate_draft

BODY = "\n\n".join(
    f"{i}. That my client states the facts of paragraph {i} in full, with dates and amounts."
    for i in range(1, 9)
)

NOTICE = f"""**REGISTERED POST A.D. / SPEED POST**

## LEGAL NOTICE

**Date:** 19 October 2026

**To:**

Suresh Patil

Mumbai, Maharashtra

**From:**

Advocate [Advocate's Name]

**Subject: Legal Notice demanding payment of outstanding arrears of rent amounting to Rs. 2,00,000/- for the shop at FC Road, Pune**

Sir,

Under instructions from my client, Ramesh Kumar, I serve you with the following legal notice:

{BODY}

You are therefore called upon to pay Rs. 2,00,000/- within 15 days of receipt of this notice.

**Advocate [Advocate's Name]**

Counsel for Ramesh Kumar
"""


def test_subject_line_containing_the_title_keeps_the_body():
    cleaned, warnings = validate_draft(NOTICE)
    assert "8. That my client states the facts of paragraph 8" in cleaned
    assert "within 15 days of receipt" in cleaned
    assert len(cleaned) > 0.9 * len(NOTICE)
    assert not any("Removed repeated section" in w for w in warnings)


def test_signature_name_already_in_the_from_block_is_kept():
    """Local repro 2026-10-19: 'Removed repeated section(s): [advocate name]'
    and the notice ended at '(Signature)'."""
    for name in ("[Advocate Name]", "Rahul Sharma"):
        notice = (f"## LEGAL NOTICE\n\n**From:**\n\n**{name}**\n\nAdvocate, Pune\n\n{BODY}\n\n"
                  f"Yours faithfully,\n\n(Signature)\n\n**{name}**\n\nAdvocate\n\n"
                  "Counsel for Ramesh Kumar\n")
        cleaned, dropped = _collapse_repeated_sections(notice)
        assert dropped == [] and cleaned.rstrip().endswith("Counsel for Ramesh Kumar")


def test_hindi_caption_still_counts_as_a_heading():
    draft = ("**सादर निवेदन है कि**\n\nपहला\n\n" + BODY + "\n\n**सादर निवेदन है कि**\n\nदूसरा\n")
    cleaned, dropped = _collapse_repeated_sections(draft)
    assert dropped and "दूसरा" not in cleaned


def test_a_genuinely_repeated_prayer_is_still_removed():
    draft = (
        "## FACTS\n\n" + BODY + "\n\n## PRAYER\n\n(a) Grant bail.\n\n(b) Costs.\n\n"
        "## VERIFICATION\n\nVerified at Pune.\n\n## PRAYER\n\n(a) Grant bail to the applicant.\n"
    )
    cleaned, dropped = _collapse_repeated_sections(draft)
    assert dropped == ["prayer"]
    assert cleaned.count("## PRAYER") == 1 and "Grant bail to the applicant" not in cleaned


def test_paraphrased_heading_still_counts_as_the_same_section():
    draft = ("## BRIEF FACTS\n\n" + BODY + "\n\n## GROUNDS\n\nGround A.\n\n"
             "## BRIEF FACTS OF THE CASE\n\nA short repeat.\n")
    cleaned, dropped = _collapse_repeated_sections(draft)
    assert dropped == ["brief facts of the case"]


def test_a_repeat_far_larger_than_its_original_is_kept():
    # The matched heading is short; the block behind the second one is the
    # body of the document. Dropping it would ship a skeleton.
    draft = "## PRAYER\n\nGrant relief.\n\n## PRAYER FOR INTERIM RELIEF\n\n" + BODY * 3
    cleaned, dropped = _collapse_repeated_sections(draft)
    assert cleaned == draft and dropped == []


def test_the_size_check_alone_saves_the_body():
    # Even if a short heading matched a long one, the body survives.
    draft = ("## LEGAL NOTICE DEMAND\n\nTo: X\n\n"
             "## LEGAL NOTICE DEMAND FOR ARREARS\n\n" + BODY * 2)
    cleaned, dropped = _collapse_repeated_sections(draft)
    assert dropped == [] and BODY in cleaned
