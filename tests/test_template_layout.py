"""Reading layout out of the drafting templates.

Each fixture reproduces a line shape found in the `drafting` index (the
templates are text extracts whose layout survived only as whitespace).
"""

from __future__ import annotations

from core.draft_blocks import (
    Artifact, HeadingBlock, ParaBlock, RowBlock, SignatureBlock, TextBlock, TitleBlock,
)
from core.template_layout import fix_mojibake, template_to_artifact

T = "\t" * 9

BAIL = (
    "Draft Heading: Draft or Specimen or Format of Bail Application before the High Court:\n"
    "Draft Content: IN THE HIGH COURT OF JUDICATURE AT MUMBAI \n"
    f"{T}  (Criminal Miscellaneous Jurisdiction)\n"
    "In the matter of: An application for Anticipatory Bail Under Section 439 of the\n"
    "Criminal Procedure Code 1973.\n"
    "Misc. Application No.___/20___ . \n"
    "Name:_____, \n"
    "Age:__ years, Occupation:_____, \n"
    f"Address:______.{T}{T}_____ Applicant\n"
    f"{T}Versus\n"
    f"The State of _____{T}{T}_____ Opponent\n"
    "To\n"
    "\tThe Honâ\u0080\u0099ble Mr. Chief Justice and His Companion Justices of the said Honâ\u0080\u0099ble Court. "
    "The Humble application of Shri \n"
    "Mills Fills Gills, the petitioner abovenamed most respectfully submits as under:\n"
    "1. That the applicant is a reputed person being in the service of the ___ , and \n"
    "is an assessee under the Income Tax Act 1961.\n"
    "2. That the applicant is, at present, residing at the withinmentioned address.\n"
    "3. That in the circumstances, the applicant humbly prays that:\n"
    "(a) Your Lordship may be pleased to release him on bail.\n"
    "(b) Your Lordship may pass such any other orders as may be deemed fit and proper.\n"
    "Place:\n"
    f"Date:{T}{T}APPLICANT\n"
    f"{T}{T}Sd/-\n"
    f"{T}{T}ADVOCATE FOR APPLICANT\n"
    "List of Required Documents for Bail Application before the High Court:\n"
    "-Aadhaar Card / Voter ID / Passport (Identity proof).\n"
    "-Copy of FIR / Complaint (Certified copy of the First Information Report).\n"
)

NOTICE = (
    "Draft Heading: format of Notice for Notice under Section 138 of Negotiable Instruments Act\n"
    "Draft Content: NOTICE\n"
    "To, \n"
    "_________\n"
    "Sir, \n"
    "Under the instructions from my client, Mr. _____ residing at __ , on being\n"
    "authorised by him in this behalf, I address you as under:\n"
    "(1) That my client had entered into a contract with you for the supply of cement.\n"
    "(2) That as per the contract, my client supplied to you __ number of cement bags.\n"
    "The delivery of the same has been acknowledged by you by endorsing and\n"
    "signing the delivery challan dated __ .\n"
    "(3)That my client presented the said cheque; however the bank returned it.\n"
)


def _blocks(a: Artifact):
    return [b for s in a.sections for b in s.blocks]


def test_mojibake_is_repaired():
    assert fix_mojibake("Honâ\u0080\u0099ble Rs. â\u0082¹ 5") == "Hon’ble Rs. ₹ 5"
    assert fix_mojibake("café plain") == "café plain"   # real Latin-1 text untouched


def test_cause_title_layout():
    a = template_to_artifact(BAIL)
    b = _blocks(a)
    titles = [x for x in b if isinstance(x, TitleBlock)]
    assert titles[0].text.startswith("IN THE HIGH COURT") and titles[0].align == "center"
    assert any(t.text == "(Criminal Miscellaneous Jurisdiction)" for t in titles)
    assert any(t.text == "Versus" and not t.bold for t in titles)
    assert any(t.text.startswith("Misc. Application No.") for t in titles)

    matter = next(x for x in b if isinstance(x, TextBlock) and x.text.startswith("In the matter of"))
    assert matter.text.endswith("Criminal Procedure Code 1973.")      # wrapped line rejoined

    rows = [x for x in b if isinstance(x, RowBlock)]
    applicant = next(r for r in rows if "Applicant" in r.right[0])
    assert applicant.left[0].startswith("Name:") and len(applicant.left) == 3
    assert any("Opponent" in r.right[0] for r in rows)


def test_body_paragraphs_and_prayer():
    b = _blocks(template_to_artifact(BAIL))
    paras = [x for x in b if isinstance(x, ParaBlock) and x.number]
    assert [p.number for p in paras] == [1, 2, 3]
    assert paras[0].text.endswith("Income Tax Act 1961.")              # wrap joined
    assert [s.marker for s in paras[2].sub] == ["(a)", "(b)"]
    assert "[[blank]]" in paras[0].text and "___" not in paras[0].text


def test_signature_before_an_appended_documents_list():
    a = template_to_artifact(BAIL)
    b = _blocks(a)
    sig = [x for x in b if isinstance(x, SignatureBlock)]
    assert sig and sig[0].lines == ["Sd/-", "ADVOCATE FOR APPLICANT"]
    assert a.sections[-1].heading == "Documents Required"
    assert isinstance(a.sections[-1].blocks[0], HeadingBlock)
    # each bullet stays its own line
    assert sum(1 for x in a.sections[-1].blocks if isinstance(x, TextBlock)) == 2


def test_salutation_and_address_lines_stand_alone():
    b = _blocks(template_to_artifact(NOTICE))
    texts = [x.text for x in b if isinstance(x, TextBlock)]
    assert "To," in texts and "Sir," in texts and "[[blank]]" in texts
    assert any(t.startswith("Under the instructions") for t in texts)


def test_wrapped_sentence_after_a_full_stop_stays_in_its_paragraph():
    paras = [x for x in _blocks(template_to_artifact(NOTICE)) if isinstance(x, ParaBlock)]
    assert len(paras) == 3
    assert "The delivery of the same has been acknowledged" in paras[1].text
    assert paras[2].text.startswith("That my client presented")       # "(3)That" without a space


def test_every_result_is_a_valid_artifact_and_exports():
    from core.draft_docx import artifact_to_docx
    for raw in (BAIL, NOTICE, "", "just one line"):
        a = template_to_artifact(raw)
        Artifact.model_validate(a.model_dump())
        if a.sections:
            assert artifact_to_docx(a).getbuffer().nbytes > 1000
