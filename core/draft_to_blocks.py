"""The drafting agent's Markdown draft, as a block Artifact.

The writer is unchanged: it still produces Markdown, so draft content,
quality and cost are exactly what they were. This module reads the draft's
own structure and gives it real layout for the side panel and the Word
download:

  cause title (before the body starts)
    **COURT** / CAPS lines            -> centred title
    "IN THE MATTER OF ..." / "AND"     -> centred, not bold
    party details then ".....Plaintiff" -> row, label on the right
    **vs**                             -> Versus
  body
    ## Heading                         -> centred heading (VERIFICATION and
                                          AFFIDAVIT start a new page)
    N. text                            -> numbered paragraph, number kept
    (a) / (i) text                     -> sub-item of the paragraph above
    - item                             -> bullet
    | table |                          -> table
    Place: / Date:                     -> left
    Advocate for / Deponent / Through  -> signature block, right
  inline
    [Placeholder]                      -> [[Placeholder]] (highlighted blank)

Deterministic, no LLM call. Paragraph numbers are kept as the drafting
validator left them (it already repairs backward jumps), so a list that
legitimately restarts at 1 is not renumbered.
"""

from __future__ import annotations

import re
import uuid

from core.draft_blocks import (
    Artifact, HeadingBlock, PageBreakBlock, ParaBlock, RowBlock, Section,
    SignatureBlock, SubItem, TableBlock, TextBlock, TitleBlock,
)
from core.template_layout import _LABEL_WORDS

_H_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BOLD_LINE_RE = re.compile(r"^\*\*([^*].*?)\*\*:?\s*$")
_NUM_RE = re.compile(r"^(\d{1,3})\.\s+(\S.*)$")
_SUB_RE = re.compile(r"^\s*(\((?:[a-zA-Z]|[ivxlc]{1,6})\)|(?:[a-z]|[ivx]{1,5})\))\s+(\S.*)$")
_BULLET_RE = re.compile(r"^\s*[-*•]\s+(\S.*)$")
_VERSUS_RE = re.compile(r"^\**\s*(?:versus|vs\.?|v/s\.?)\s*\**$", re.I)
_LABEL_LINE_RE = re.compile(r"^[.…_\s]{2,}(\S.*)$")      # ".....Petitioner"
# "Petitioner", "Applicant / Accused", "Respondents / State", "Respondent No. 1"
_PARTY_LABEL_RE = re.compile(rf"^{_LABEL_WORDS}\b.{{0,40}}$", re.I)
_MATTER_RE = re.compile(r"^\**\s*(?:in\s+the\s+matter\s+of\b|and\s*$)", re.I)
_BODY_START_RE = re.compile(
    r"^(?:#{1,3}\s|to,?\s*$|\**\s*(?:most\s+)?respectfully\s+show|.*\b(?:showeth|sheweth)\b"
    r"|the\s+\w+(?:\s+\w+)?\s+(?:above\s*named|abovenamed)\b|dear\s+sir|sir,?\s*$|madam,?\s*$)", re.I)
_PAGE_BREAK_HEADINGS = re.compile(r"^(?:verification|affidavit)\b", re.I)
_PLACE_DATE_RE = re.compile(r"^\**(?:place|date|dated)\**\s*:", re.I)
_SIGN_RE = re.compile(
    r"^(?:advocates?\s+for\b|counsel\s+for\b|through\b|deponent\b|sd/-|signature\b|before\s+me\b"
    r"|for\s+m/s\b|verified\s+by\b|yours\s+(?:faithfully|sincerely)\b|"
    r"(?:the\s+)?(?:petitioners?|applicants?|appellants?|plaintiffs?|defendants?|respondents?"
    r"|accused|complainants?|opponents?|proprietor)(?:\s*/\s*[\w ]+)?\s*$)", re.I)
_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]{3,}\|\s*$")
# "[Name of Applicant]" -> blank; never a citation "[2024] 3 SCC", a footnote "[1]"
# or a markdown link "[label](url)".
_PLACEHOLDER_RE = re.compile(r"(?<!\[)\[(?!\[)([^\[\]\n]{2,80})\](?![\](])")
_CITATION_LIKE = re.compile(r"^\d{4}$|\b(?:SCC|INSC|AIR|SCR|SCALE|CrLJ|MhLJ|BomCR)\b|^\d+$")
# A bracket that annotates rather than leaves a blank: "[Section 121 of the
# Bharatiya Sakshya Adhiniyam, 2023]", "[cheating and dishonestly inducing ...]".
_ANNOTATION_LIKE = re.compile(
    r"\b(?:sections?|articles?|rules?|order)\s+\d"            # "Section 121", "Order VII" is not
    r"|\b(?:act|code|sanhita|adhiniyam)\s*,\s*\d{4}"          # "Adhiniyam, 2023" ("Pin Code" is not)
    r"|\b(?:corresponding|erstwhile|formerly|read\s+with|r/w)\b", re.I)


def _inline(text: str) -> str:
    def _sub(m: re.Match) -> str:
        inner = m.group(1).strip()
        if (_CITATION_LIKE.search(inner) or _ANNOTATION_LIKE.search(inner)
                or len(inner.split()) > 10 or inner[:1].islower()):
            return m.group(0)
        return f"[[{inner}]]"
    return _PLACEHOLDER_RE.sub(_sub, text.strip())


def _caps(s: str) -> bool:
    s = re.sub(r"\[[^\]]*\]", "", s)          # a blank inside a caps line does not count
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 3 and all(c.isupper() for c in letters)


def _unbold(s: str) -> str:
    m = _BOLD_LINE_RE.match(s.strip())
    return m.group(1).strip() if m else s.strip()


class _Builder:
    def __init__(self, title: str, doc_type: str, artifact_id: str, language: str):
        self.sections: list[Section] = [Section(id="s1", heading="Cause title")]
        self.meta = dict(title=title, doc_type=doc_type, artifact_id=artifact_id, language=language)
        self.last_para: ParaBlock | None = None
        self.table: list[list[str]] = []

    def _id(self) -> str:
        sec = self.sections[-1]
        return f"{sec.id}.b{len(sec.blocks) + 1}"

    def add(self, block_cls, **kw):
        self.flush_table()
        b = block_cls(id=self._id(), **kw)
        self.sections[-1].blocks.append(b)
        if not isinstance(b, ParaBlock):
            self.last_para = None
        return b

    def section(self, heading: str):
        self.flush_table()
        if self.sections[-1].blocks:
            self.sections.append(Section(id=f"s{len(self.sections) + 1}",
                                         heading=heading[:60] or "Section"))
        else:
            self.sections[-1].heading = heading[:60] or self.sections[-1].heading
        self.last_para = None

    def flush_table(self):
        if self.table:
            rows, self.table = self.table, []
            header, body = rows[0], rows[1:]
            sec = self.sections[-1]
            sec.blocks.append(TableBlock(id=f"{sec.id}.b{len(sec.blocks) + 1}",
                                         header=header, rows=body or [[""] * len(header)]))

    @property
    def last(self):
        return self.sections[-1].blocks[-1] if self.sections[-1].blocks else None

    def build(self) -> Artifact:
        self.flush_table()
        return Artifact(sections=[s for s in self.sections if s.blocks], **self.meta)


_DOC_NOUN_RE = re.compile(r"\b(?:application|petition|statement|notice|plaint|appeal|affidavit"
                          r"|reply|complaint|agreement|deed|suit|memorandum)\b", re.I)
_CASE_NUMBER_RE = re.compile(r"\bno\.?\s*(?:\[|\d|_)", re.I)
_TITLE_MAX = 90
_ROMAN_RE = re.compile(r"(?=[IVXLC])C{0,3}(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})")


def _title_from(md: str) -> str:
    """The document's own title line ("APPLICATION FOR GRANT OF REGULAR BAIL
    ...", "WRITTEN STATEMENT ON BEHALF OF ..."), not its case-number line.
    Falls back to the case-number line, then "Draft"."""
    fallback = ""
    for line in md.splitlines()[:60]:
        raw = line.strip()
        t = _unbold(raw).strip("# *").strip()
        if not (raw.startswith(("**", "#")) or _caps(t)) or not _DOC_NOUN_RE.search(t):
            continue
        t = re.sub(r"^(?:subject|sub|re)\s*:\s*", "", t, flags=re.I)
        t = re.sub(r"\s*\[[^\]]*\]\s*$", "", t)            # trailing "[CORRESPONDING TO ...]"
        if _CASE_NUMBER_RE.search(t):
            fallback = fallback or t
            continue
        return _shorten(t)
    return _shorten(fallback) if fallback else "Draft"


def _shorten(t: str) -> str:
    t = t.title() if _caps(t) else t
    t = re.sub(r"\b(Of|On|The|And|For|Under|In|To|By|With|At)\b", lambda m: m.group(1).lower(), t)
    t = re.sub(r"\b[IVXLC][ivxlc]+\b",
               lambda m: m.group(0).upper() if _ROMAN_RE.fullmatch(m.group(0).upper()) else m.group(0), t)
    t = t[:1].upper() + t[1:]
    if len(t) <= _TITLE_MAX:
        return t
    cut = t[:_TITLE_MAX].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def markdown_to_artifact(md: str, *, doc_type: str = "draft", title: str | None = None,
                         artifact_id: str | None = None, language: str = "en") -> Artifact:
    b = _Builder(title=(title or _title_from(md or ""))[:200], doc_type=doc_type or "draft",
                 artifact_id=artifact_id or f"art_{uuid.uuid4().hex[:8]}", language=language or "en")
    lines = (md or "").replace("\r", "").split("\n")
    in_body = False
    party: list[str] = []

    def flush_party():
        nonlocal party
        for p in party:
            b.add(TextBlock, text=_inline(p), align="left")
        party = []

    for raw in lines:
        s = raw.strip()
        if not s or re.match(r"^-{3,}$|^\*{3,}$|^_{3,}$", s):
            if b.table and not s:
                b.flush_table()
            continue

        if not in_body and _BODY_START_RE.match(s):
            flush_party()
            in_body = True

        # --- cause title -------------------------------------------------
        if not in_body:
            if _VERSUS_RE.match(s):
                flush_party()
                b.add(TitleBlock, text="Versus", bold=False)
                continue
            m = _LABEL_LINE_RE.match(s)
            if m and _PARTY_LABEL_RE.match(m.group(1).strip().rstrip(".")):
                b.add(RowBlock, left=[_inline(p) for p in party], right=[s.strip()])
                party = []
                continue
            plain = _unbold(s)
            if _MATTER_RE.match(s):
                flush_party()
                b.add(TitleBlock, text=_inline(plain), bold=False)
                continue
            if _BOLD_LINE_RE.match(s) or (_caps(plain) and not party):
                flush_party()
                b.add(TitleBlock, text=_inline(plain), bold=True)
                continue
            party.append(s)
            continue

        # --- body --------------------------------------------------------
        if _TABLE_RE.match(s):
            if not _TABLE_SEP_RE.match(s):
                b.table.append([_inline(c) for c in s.strip().strip("|").split("|")])
            continue
        b.flush_table()

        m = _H_RE.match(s)
        if m or (_BOLD_LINE_RE.match(s) and len(_unbold(s)) <= 120
                 and (_caps(_unbold(s)) or len(_unbold(s).split()) <= 10)
                 and not _PLACE_DATE_RE.match(s)):
            text = (m.group(2) if m else _unbold(s)).strip("* ").rstrip(":")
            if _PAGE_BREAK_HEADINGS.match(text) and any(blk for blk in b.sections[-1].blocks):
                b.section(text.title() if _caps(text) else text)
                b.add(PageBreakBlock)
            else:
                b.section(text.title() if _caps(text) else text)
            level = 2 if m and len(m.group(1)) >= 3 else 1
            b.add(HeadingBlock, text=_inline(text.upper() if level == 1 else text), level=level,
                  underline=level == 1)
            continue

        m = _NUM_RE.match(s)
        if m:
            b.last_para = b.add(ParaBlock, number=int(m.group(1)), text=_inline(m.group(2)))
            continue
        m = _SUB_RE.match(raw)
        if m:
            if b.last_para is not None:
                b.last_para.sub.append(SubItem(marker=m.group(1)[:8], text=_inline(m.group(2))))
            else:
                b.last_para = b.add(ParaBlock, label=m.group(1)[:12], text=_inline(m.group(2)))
            continue
        m = _BULLET_RE.match(raw)
        if m:
            b.add(ParaBlock, label="•", text=_inline(m.group(1)))
            continue

        if _PLACE_DATE_RE.match(s):
            b.add(TextBlock, text=_inline(s), align="left")
            continue
        short = len(s) <= 90 and not s.endswith((".", ";")) or _SIGN_RE.match(s)
        placeholder_only = bool(re.fullmatch(r"\[[^\]]{2,80}\]\s*,?", s))
        if short and (_SIGN_RE.match(_unbold(s)) or
                      (placeholder_only and isinstance(b.last, SignatureBlock))):
            if isinstance(b.last, SignatureBlock):
                b.last.lines.append(_inline(s))
            else:
                prev = b.last
                # "[Name of Defendant]" written just above "Defendant" belongs to the signature
                if (isinstance(prev, TextBlock) and len(prev.text) <= 80
                        and re.fullmatch(r"\[\[[^\]]+\]\],?", prev.text)):
                    b.sections[-1].blocks.pop()
                    b.add(SignatureBlock, lines=[prev.text, _inline(s)])
                else:
                    b.add(SignatureBlock, lines=[_inline(s)])
            continue
        if isinstance(b.last, SignatureBlock) and len(s) <= 90 and not s.endswith("."):
            b.last.lines.append(_inline(s))
            continue

        align = "justify" if len(s) > 80 else "left"
        b.add(TextBlock, text=_inline(s), align=align)

    flush_party()
    return b.build()
