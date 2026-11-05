"""Sign-off block (Place / Date / party signature / Advocate) sits after the
prayer, before the verification. Testers' report 2026-10-05 on the
medical-negligence plaint: "Date, Place, Name of Advocate missing in draft".
Six live runs: the block was never absent but in 2 of 6 drafts it opened the
VERIFICATION AND AFFIDAVIT section or trailed the list of documents. Shapes
below are cut from those runs. Pure tests, no LLM.
"""
from __future__ import annotations

from agents.drafting import _relocate_signoff_block, validate_draft

PRAYER = (
    "## PRAYER\n\nWherefore, the Plaintiff prays that this Hon'ble Court may be pleased to:\n\n"
    "(a) Pass a decree for Rs. 25,00,000/- against the Defendants jointly and severally;\n\n"
    "(b) Award costs of the suit;\n\n"
    "(d) Grant such other and further reliefs as this Hon'ble Court may deem fit and proper in the facts and circumstances of the case to meet the ends of justice.\n\n"
)
SIGNOFF = "Place: Pune\nDate: 05 October 2026\n\nMrs. Anjali Deshmukh\nPlaintiff\n\n[Name of Advocate]\nAdvocate for Plaintiff\n\n"
VERIFICATION = (
    "### VERIFICATION\n\nI, Mrs. Anjali Deshmukh, aged 42 years, residing at Pune, the Plaintiff above named, do hereby verify that the contents of paragraphs 1 to 25 are true to my knowledge.\n\n"
    "Verified at Pune on this 05th day of October, 2026.\n\n"
)
AFFIDAVIT = (
    "### AFFIDAVIT IN SUPPORT OF PLAINT\n\nI, Mrs. Anjali Deshmukh, do hereby solemnly affirm and state as under:\n\n"
    "1. I am the Plaintiff and am conversant with the facts of the case.\n\n"
    "Solemnly affirmed at Pune on this 05th day of October, 2026.\n\nDeponent\nMrs. Anjali Deshmukh\n\n"
)
DOCS = "### LIST OF DOCUMENTS\n\n1. Discharge summary dated 15 March 2024 issued by Defendant No. 1.\n\n2. Bills of the corrective surgery.\n\n"
CITED = "---\n\n## Judgments Cited\n\n- *Jacob Mathew v. State of Punjab*, (2005) 6 SCC 1\n"

RUN2_SHAPE = PRAYER + "## VERIFICATION AND AFFIDAVIT\n\n" + SIGNOFF + VERIFICATION + AFFIDAVIT + DOCS + CITED
RUN6_SHAPE = PRAYER + "## VERIFICATION AND SUPPORTING AFFIDAVIT\n\n" + VERIFICATION + AFFIDAVIT + DOCS + SIGNOFF + CITED
CORRECT = PRAYER + SIGNOFF + "## VERIFICATION AND AFFIDAVIT\n\n" + VERIFICATION + AFFIDAVIT + DOCS + CITED


def _after_prayer(text: str) -> bool:
    p, v, s = text.index("## PRAYER"), text.index("VERIFICATION"), text.index("Place: Pune")
    return p < s < v


def test_block_opening_the_verification_section_moves_after_prayer():
    out, st = _relocate_signoff_block(RUN2_SHAPE)
    assert st == "moved" and _after_prayer(out)
    assert out.count("Place: Pune") == 1 and out.count("Advocate for Plaintiff") == 1
    assert "## VERIFICATION AND AFFIDAVIT\n\n### VERIFICATION" in out   # section now opens with its own text


def test_block_trailing_the_documents_list_moves_after_prayer():
    out, st = _relocate_signoff_block(RUN6_SHAPE)
    assert st == "moved" and _after_prayer(out)
    assert out.count("Place: Pune") == 1
    assert out.index("2. Bills of the corrective surgery.") < out.index("## Judgments Cited")
    assert "Place: Pune" not in out[out.index("### LIST OF DOCUMENTS"):]


def test_correct_draft_is_untouched():
    out, st = _relocate_signoff_block(CORRECT)
    assert st == "" and out == CORRECT


def test_affidavit_deponent_block_is_never_moved():
    for shape in (RUN2_SHAPE, RUN6_SHAPE, CORRECT):
        out, _ = _relocate_signoff_block(shape)
        assert out.count("Deponent") == shape.count("Deponent")
        assert out.index("Solemnly affirmed at Pune") > out.index("### AFFIDAVIT")


def test_verification_with_its_own_trailing_place_date_is_untouched():
    # the verification's own place / date at its END belongs to the verification
    text = PRAYER + "## VERIFICATION\n\nI, the Plaintiff, verify that the above is true.\n\nPlace: Pune\nDate: 05 October 2026\n\nPlaintiff\n"
    out, st = _relocate_signoff_block(text)
    assert st == "" and out == text


def test_affidavit_own_place_date_is_untouched():
    text = PRAYER + SIGNOFF + "## VERIFICATION\n\ntext\n\n## AFFIDAVIT\n\nI affirm.\n\nPlace: Pune\nDate: 05 October 2026\n\nDeponent\n"
    out, st = _relocate_signoff_block(text)
    assert st == "" and out == text


def test_missing_block_gets_a_placeholder_after_prayer():
    text = PRAYER + "## VERIFICATION AND AFFIDAVIT\n\n" + VERIFICATION + AFFIDAVIT
    out, st = _relocate_signoff_block(text)
    assert st == "inserted"
    assert text.index("## PRAYER") < out.index("Place: [Place]") < out.index("## VERIFICATION AND AFFIDAVIT")
    assert "[Name of Advocate]" in out and "Advocate for the Plaintiff" in out


def test_documents_without_prayer_or_verification_are_untouched():
    notice = "## LEGAL NOTICE\n\nTAKE NOTICE that ...\n\nPlace: Pune\nDate: 05 October 2026\n\n[Name of Advocate]\nAdvocate\n"
    out, st = _relocate_signoff_block(notice)
    assert st == "" and out == notice
    no_verif = PRAYER + DOCS + SIGNOFF
    out, st = _relocate_signoff_block(no_verif)
    assert st == "" and out == no_verif


def test_validate_draft_applies_the_repair():
    r = validate_draft(RUN2_SHAPE)
    out = r[0] if isinstance(r, tuple) else r
    assert _after_prayer(out) and out.count("Place: Pune") == 1


def test_section_pair_prompt_carries_the_rule():
    from config.prompts import DRAFTING_SECTION_PAIR_PROMPT as P
    assert "IMMEDIATELY after the last prayer clause" in P
