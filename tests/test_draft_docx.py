"""Word export from a block artifact keeps real layout.

Reads the generated .docx back with python-docx and checks the native Word
constructs — alignment, right tab stops, hanging indents, highlight, page
setup — rather than only that a file was produced.
"""

from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX, WD_TAB_ALIGNMENT
from docx.oxml.ns import qn
from docx.shared import Cm

from core.draft_blocks import Artifact
from core.draft_docx import artifact_to_docx, docx_filename

SAMPLE = json.loads((Path(__file__).resolve().parent.parent / "docs" /
                     "draft_panel_sample.json").read_text(encoding="utf-8"))


def _doc(data=SAMPLE):
    return Document(artifact_to_docx(Artifact.model_validate(data)))


def _find(doc, starts):
    return next(p for p in doc.paragraphs if p.text.startswith(starts))


def _cm(v):
    return round(v / Cm(1), 2)


def test_page_setup_follows_the_artifact():
    s = _doc().sections[0]
    assert (_cm(s.page_width), _cm(s.page_height)) == (21.0, 29.7)
    assert (_cm(s.left_margin), _cm(s.right_margin), _cm(s.top_margin)) == (4.0, 2.5, 2.5)


def test_titles_are_centred_and_bold():
    p = _find(_doc(), "IN THE HIGH COURT")
    assert p.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert all(r.bold for r in p.runs if r.text.strip())


def test_party_label_sits_on_a_right_tab_at_the_text_edge():
    doc = _doc()
    p = next(p for p in doc.paragraphs if "...Applicant" in p.text)
    stops = list(p.paragraph_format.tab_stops)
    assert stops and stops[0].alignment == WD_TAB_ALIGNMENT.RIGHT
    assert _cm(stops[0].position) == 21.0 - 4.0 - 2.5
    # bottom-aligned: the label shares the LAST line of the party details
    assert p.text.splitlines()[-1].endswith("\t...Applicant")
    assert "\t" not in p.text.splitlines()[0]


def test_numbered_paragraph_has_a_hanging_number_not_a_word_list():
    p = _find(_doc(), "1.\tThe Applicant is a law-abiding")
    pf = p.paragraph_format
    assert pf.first_line_indent < 0 and pf.left_indent == -pf.first_line_indent
    assert p._p.pPr.find(qn("w:numPr")) is None   # typed number, never auto-renumbered


def test_sub_items_nest_deeper_than_their_paragraph():
    doc = _doc()
    parent = _find(doc, "C.\tBECAUSE")
    sub = _find(doc, "(a)\treporting")
    assert sub.paragraph_format.left_indent > parent.paragraph_format.left_indent


def test_blanks_are_highlighted():
    p = _find(_doc(), "BAIL APPLICATION NO.")
    blank = next(r for r in p.runs if r.text == "[number]")
    assert blank.font.highlight_color == WD_COLOR_INDEX.YELLOW
    assert "[[" not in "\n".join(x.text for x in _doc().paragraphs)


def test_verification_starts_on_a_new_page():
    doc = _doc()
    body = list(doc.paragraphs)
    i = next(k for k, p in enumerate(body) if p.text == "VERIFICATION")
    breaks = [p for p in body[:i] if p._p.xpath(".//w:br[@w:type='page']")]
    assert breaks


def test_inline_bold_inside_text():
    p = _find(_doc(), "THE HUMBLE APPLICATION")
    assert any(r.bold and "SHEWETH" in r.text for r in p.runs)


def test_regional_script_font_is_set():
    rfonts = _doc().styles["Normal"].element.rPr.rFonts
    assert rfonts.get(qn("w:cs"))


def test_filename():
    assert docx_filename(Artifact.model_validate(SAMPLE)) == "Bail_Application_under_Section_483_BNSS_v1.docx"


def test_export_returns_docx_and_rejects_bad_artifacts():
    # The endpoint body delegates to _export_artifact when `artifact` is set;
    # call it directly (the rate limiter on the route needs a real Request).
    from core.gateway import ExportRequest, _export_artifact
    assert ExportRequest(artifact=SAMPLE).artifact == SAMPLE

    ok = _export_artifact(SAMPLE, "docx", None)
    assert ok.media_type.endswith("wordprocessingml.document")
    assert "Bail_Application" in ok.headers["content-disposition"]

    pdf = _export_artifact(SAMPLE, "pdf", None)
    assert pdf.media_type == "application/pdf"

    bad = json.loads(json.dumps(SAMPLE))
    bad["sections"][0]["blocks"][0]["type"] = "marquee"
    assert _export_artifact(bad, "docx", None).status_code == 400
