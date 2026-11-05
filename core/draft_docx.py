"""Word (.docx) from a block artifact — the download behind the draft panel.

Every layout feature is a native Word construct, not spaces or pipes, so the
file stays aligned when the advocate edits it:

  - centred / right / justified lines   -> paragraph alignment
  - party label on the right ("...Applicant") -> a right tab stop at the text
    edge, one line per paragraph line, right side bottom-aligned
  - numbered paragraph                  -> hanging indent + tab, number typed
    as text (never a Word auto-list, which renumbers on edit)
  - page size, margins, font, spacing   -> section and Normal style
  - [[blank]]                           -> "[blank]" highlighted yellow

Input is a validated core.draft_blocks.Artifact; output is a BytesIO.
"""

from __future__ import annotations

import re
from io import BytesIO

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_COLOR_INDEX, WD_TAB_ALIGNMENT
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from core.draft_blocks import (
    Artifact, HeadingBlock, PageBreakBlock, ParaBlock, RowBlock,
    SignatureBlock, TableBlock, TextBlock, TitleBlock,
)

_ALIGN = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}

_PAGE_CM = {"A4": (21.0, 29.7), "Legal": (21.59, 35.56)}

# Fonts that carry Devanagari and the other Indic scripts the app writes in.
_COMPLEX_SCRIPT_FONT = "Nirmala UI"

# Hanging indents (cm).
_PARA_GUTTER = 1.25
_SUB_GUTTER = 1.0

# **bold**  *italic*  __underline__  [[blank]] — longest markers first.
_INLINE_RE = re.compile(r"(\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|\[\[[^\[\]\n]{1,80}\]\])")


def _add_runs(p, text: str, *, bold: bool = False, underline: bool = False) -> None:
    """Write inline-formatted text into paragraph `p`. "\\n" is a line break."""
    for li, line in enumerate(text.split("\n")):
        if li:
            p.add_run().add_break(WD_BREAK.LINE)
        for part in _INLINE_RE.split(line):
            if not part:
                continue
            r_bold, r_ital, r_under, hl = bold, False, underline, False
            if part.startswith("**") and part.endswith("**") and len(part) > 4:
                part, r_bold = part[2:-2], True
            elif part.startswith("__") and part.endswith("__") and len(part) > 4:
                part, r_under = part[2:-2], True
            elif part.startswith("[[") and part.endswith("]]"):
                part, hl = f"[{part[2:-2]}]", True
            elif part.startswith("*") and part.endswith("*") and len(part) > 2:
                part, r_ital = part[1:-1], True
            run = p.add_run(part)
            run.bold = r_bold or None
            run.italic = r_ital or None
            run.underline = r_under or None
            if hl:
                run.font.highlight_color = WD_COLOR_INDEX.YELLOW


def _para(doc, align: str = "justify", *, before: float = 0, after: float = 6):
    p = doc.add_paragraph()
    p.alignment = _ALIGN.get(align, WD_ALIGN_PARAGRAPH.JUSTIFY)
    pf = p.paragraph_format
    pf.space_before, pf.space_after = Pt(before), Pt(after)
    return p


def _setup(doc, artifact: Artifact) -> float:
    """Page, margins and base font. Returns the text width in cm."""
    page = artifact.page
    w, h = _PAGE_CM.get(page.size, _PAGE_CM["A4"])
    m = {"top": 2.5, "bottom": 2.5, "left": 4.0, "right": 2.5, **(page.margins_cm or {})}
    sec = doc.sections[0]
    sec.start_type = WD_SECTION.NEW_PAGE
    sec.page_width, sec.page_height = Cm(w), Cm(h)
    sec.top_margin, sec.bottom_margin = Cm(m["top"]), Cm(m["bottom"])
    sec.left_margin, sec.right_margin = Cm(m["left"]), Cm(m["right"])

    normal = doc.styles["Normal"]
    normal.font.name = page.font
    normal.font.size = Pt(page.font_size_pt)
    rfonts = normal.element.get_or_add_rPr().get_or_add_rFonts()
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia"):
        rfonts.set(qn(attr), page.font)
    rfonts.set(qn("w:cs"), _COMPLEX_SCRIPT_FONT)
    normal.paragraph_format.line_spacing = page.line_spacing
    normal.paragraph_format.space_after = Pt(6)
    return w - m["left"] - m["right"]


def _row(doc, b: RowBlock, text_width_cm: float) -> None:
    """Left stack and right stack on shared lines, right side bottom-aligned."""
    n = max(len(b.left), len(b.right), 1)
    left = list(b.left) + [""] * (n - len(b.left))
    right = [""] * (n - len(b.right)) + list(b.right)
    p = _para(doc, "left", before=6, after=8)
    p.paragraph_format.tab_stops.add_tab_stop(Cm(text_width_cm), WD_TAB_ALIGNMENT.RIGHT)
    for i, (l, r) in enumerate(zip(left, right)):
        if i:
            p.add_run().add_break(WD_BREAK.LINE)
        _add_runs(p, l)
        if r:
            p.add_run("\t")
            _add_runs(p, r)


def _numbered(doc, label: str, text: str, indent_cm: float, gutter_cm: float) -> None:
    p = _para(doc, "justify")
    pf = p.paragraph_format
    pf.left_indent = Cm(indent_cm + gutter_cm)
    pf.first_line_indent = Cm(-gutter_cm)
    pf.tab_stops.add_tab_stop(Cm(indent_cm + gutter_cm))
    if label:
        p.add_run(label)
    p.add_run("\t")
    _add_runs(p, text)


def _table(doc, b: TableBlock) -> None:
    rows = ([b.header] if b.header else []) + list(b.rows)
    if not rows:
        return
    cols = max(len(r) for r in rows)
    t = doc.add_table(rows=len(rows), cols=cols)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for ri, r in enumerate(rows):
        for ci in range(cols):
            cell = t.cell(ri, ci)
            cell.paragraphs[0].paragraph_format.space_after = Pt(0)
            cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.LEFT
            _add_runs(cell.paragraphs[0], r[ci] if ci < len(r) else "",
                      bold=bool(b.header) and ri == 0)
    doc.add_paragraph()


def artifact_to_docx(artifact: Artifact) -> BytesIO:
    doc = Document()
    text_width = _setup(doc, artifact)
    doc.core_properties.title = artifact.title[:250]

    for sec in artifact.sections:
        for b in sec.blocks:
            if isinstance(b, TitleBlock):
                p = _para(doc, b.align, after=3)
                _add_runs(p, b.text, bold=b.bold, underline=b.underline)
            elif isinstance(b, HeadingBlock):
                p = _para(doc, b.align, before=12, after=6)
                p.paragraph_format.keep_with_next = True
                _add_runs(p, b.text, bold=True, underline=b.underline)
            elif isinstance(b, TextBlock):
                p = _para(doc, b.align)
                if b.indent_cm:
                    p.paragraph_format.left_indent = Cm(b.indent_cm)
                _add_runs(p, b.text)
            elif isinstance(b, RowBlock):
                _row(doc, b, text_width)
            elif isinstance(b, ParaBlock):
                label = b.label or (f"{b.number}." if b.number else "")
                _numbered(doc, label, b.text, 0.0, _PARA_GUTTER)
                for s in b.sub:
                    _numbered(doc, s.marker, s.text, _PARA_GUTTER, _SUB_GUTTER)
            elif isinstance(b, SignatureBlock):
                p = _para(doc, b.align, before=24)
                p.paragraph_format.keep_together = True
                _add_runs(p, "\n".join(b.lines))
            elif isinstance(b, TableBlock):
                _table(doc, b)
            elif isinstance(b, PageBreakBlock):
                doc.add_page_break()

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def docx_filename(artifact: Artifact) -> str:
    stem = re.sub(r"[^A-Za-z0-9]+", "_", artifact.title).strip("_")[:60] or "Draft"
    return f"{stem}_v{artifact.version}.docx"
