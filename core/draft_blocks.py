"""A draft as layout blocks — the format behind the draft side panel.

Markdown cannot centre a line, put a party label on the right, or hang a
paragraph number, so a court draft written as Markdown loses its layout in the
chat, in the panel and in Word. A draft is instead a list of sections, each a
list of typed blocks that carry their own alignment. The panel, the Word export
and the chat Markdown are all rendered from the same blocks.

The contract with the frontend is docs/draft_panel_frontend_contract.md. This
module is the source of truth for that contract; tests pin the sample in
docs/draft_panel_sample.json against it.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

ARTIFACT_SCHEMA_VERSION = 1

Align = Literal["left", "center", "right", "justify"]

# Inline text supports only **bold**, *italic*, __underline__ and [[placeholder]]
# (a blank the advocate must fill). Nothing else is interpreted.
_PLACEHOLDER_RE = re.compile(r"\[\[([^\[\]\n]{1,80})\]\]")


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., pattern=r"^[a-z0-9_.-]{1,40}$")


class TitleBlock(_Block):
    """Court name, document title. Centred, bold, upper case by convention."""
    type: Literal["title"] = "title"
    text: str
    align: Align = "center"
    bold: bool = True
    underline: bool = False


class HeadingBlock(_Block):
    """A section heading inside the body: SYNOPSIS, GROUNDS, PRAYER."""
    type: Literal["heading"] = "heading"
    text: str
    level: Literal[1, 2] = 1
    align: Align = "center"
    underline: bool = True


class TextBlock(_Block):
    """A plain, unnumbered paragraph (addressee, preamble, closing line)."""
    type: Literal["text"] = "text"
    text: str
    align: Align = "justify"
    indent_cm: float = Field(0.0, ge=0, le=10)


class RowBlock(_Block):
    """Two columns on one line group: left stack, right stack.

    Parties ("Name, Age, Address ... | ...Applicant"), place/date against the
    advocate's signature. Each side is a list of lines; the right side is
    right-aligned and bottom-aligned against the left.
    """
    type: Literal["row"] = "row"
    left: list[str] = Field(default_factory=list)
    right: list[str] = Field(default_factory=list)


class SubItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    marker: str = Field(..., max_length=8)      # "(a)", "(i)", "•"
    text: str


class ParaBlock(_Block):
    """A numbered paragraph with a hanging number.

    `number` is assigned by the backend in document order; renderers display it
    and never compute their own. Sub-items nest under the paragraph.
    """
    type: Literal["para"] = "para"
    number: int | None = Field(None, ge=1, le=999)
    label: str | None = Field(None, max_length=12)   # e.g. "A." for grounds
    text: str
    sub: list[SubItem] = Field(default_factory=list)


class SignatureBlock(_Block):
    """Signature / advocate block. Right-aligned lines."""
    type: Literal["signature"] = "signature"
    lines: list[str]
    align: Align = "right"


class TableBlock(_Block):
    type: Literal["table"] = "table"
    header: list[str] = Field(default_factory=list)
    rows: list[list[str]]


class PageBreakBlock(_Block):
    type: Literal["page_break"] = "page_break"


Block = Annotated[
    Union[TitleBlock, HeadingBlock, TextBlock, RowBlock, ParaBlock,
          SignatureBlock, TableBlock, PageBreakBlock],
    Field(discriminator="type"),
]


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., pattern=r"^[a-z0-9_.-]{1,40}$")
    heading: str                                 # label for the panel outline
    blocks: list[Block] = Field(default_factory=list)


class Page(BaseModel):
    model_config = ConfigDict(extra="forbid")
    size: Literal["A4", "Legal"] = "A4"
    margins_cm: dict[str, float] = Field(
        default_factory=lambda: {"top": 2.5, "bottom": 2.5, "left": 4.0, "right": 2.5})
    font: str = "Times New Roman"
    font_size_pt: float = 14
    line_spacing: float = 1.5


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = ARTIFACT_SCHEMA_VERSION
    artifact_id: str
    version: int = Field(1, ge=1)
    title: str
    doc_type: str                                # niche key, e.g. "bail_application_regular"
    language: str = "en"
    page: Page = Field(default_factory=Page)
    sections: list[Section]


# ---------------------------------------------------------------------------
# Helpers the backend uses on every artifact
# ---------------------------------------------------------------------------

def number_paragraphs(artifact: Artifact) -> Artifact:
    """Assign para numbers 1..N in document order. Paras with a `label`
    (grounds "A.", "B.") keep the label and take no number. The model never
    numbers paragraphs, so a repeated or blank number cannot reach the user."""
    n = 0
    for sec in artifact.sections:
        for b in sec.blocks:
            if isinstance(b, ParaBlock):
                if b.label:
                    b.number = None
                else:
                    n += 1
                    b.number = n
    return artifact


def placeholders(artifact: Artifact) -> list[str]:
    """Every [[blank]] in the draft, in order, de-duplicated."""
    seen: dict[str, None] = {}
    for sec in artifact.sections:
        for b in sec.blocks:
            for t in _block_texts(b):
                for m in _PLACEHOLDER_RE.finditer(t):
                    seen.setdefault(m.group(1).strip(), None)
    return list(seen)


def _block_texts(b) -> list[str]:
    if isinstance(b, (TitleBlock, HeadingBlock, TextBlock)):
        return [b.text]
    if isinstance(b, ParaBlock):
        return [b.text, *[s.text for s in b.sub]]
    if isinstance(b, RowBlock):
        return [*b.left, *b.right]
    if isinstance(b, SignatureBlock):
        return list(b.lines)
    if isinstance(b, TableBlock):
        return [*b.header, *[c for r in b.rows for c in r]]
    return []


def to_markdown(artifact: Artifact) -> str:
    """Chat-safe Markdown of the artifact, for clients without the panel.

    Layout that Markdown cannot express degrades predictably: centred lines
    become bold lines, a row becomes "left — right". Paragraph numbers are
    written as "N\\." so no renderer re-numbers them.
    """
    out: list[str] = []
    for sec in artifact.sections:
        for b in sec.blocks:
            if isinstance(b, TitleBlock):
                out.append(f"**{b.text}**")
            elif isinstance(b, HeadingBlock):
                out.append(("## " if b.level == 1 else "### ") + b.text)
            elif isinstance(b, TextBlock):
                out.append(b.text)
            elif isinstance(b, RowBlock):
                out.append(f"{' '.join(b.left)} — {' '.join(b.right)}".strip(" —"))
            elif isinstance(b, ParaBlock):
                head = f"{b.label}" if b.label else (f"{b.number}\\." if b.number else "")
                out.append(f"{head} {b.text}".strip())
                out.extend(f"   {s.marker} {s.text}" for s in b.sub)
            elif isinstance(b, SignatureBlock):
                out.append("\n".join(b.lines))
            elif isinstance(b, TableBlock):
                rows = ([b.header] if b.header else []) + b.rows
                if rows:
                    width = max(len(r) for r in rows)
                    out.append("| " + " | ".join(rows[0]) + " |")
                    out.append("|" + "---|" * width)
                    out.extend("| " + " | ".join(r) + " |" for r in rows[1:])
            elif isinstance(b, PageBreakBlock):
                out.append("---")
    return "\n\n".join(_PLACEHOLDER_RE.sub(r"[\1]", x) for x in out).strip()
