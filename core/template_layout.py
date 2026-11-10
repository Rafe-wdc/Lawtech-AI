"""Read the layout out of a drafting template.

The 498 templates in the `drafting` index are text extracts of court formats.
Their layout survived as whitespace: a line indented by tabs or a long run of
spaces was centred; "name<many tabs>...Applicant" put the party label on the
right. This module turns one template into a core.draft_blocks.Artifact — the
same block format the drafting panel and the Word export use — so a draft
can follow the template's layout instead of the writer's guess at it.

Deterministic, no LLM. Repairs the encoding damage in the corpus (UTF-8
quotes and dashes read as Latin-1: "Honâ\\x80\\x99ble" -> "Hon’ble") and
rejoins hard-wrapped paragraphs.
"""

from __future__ import annotations

import re

from core.draft_blocks import (
    Artifact, HeadingBlock, ParaBlock, RowBlock, Section, SignatureBlock,
    SubItem, TextBlock, TitleBlock, number_paragraphs,
)

# --- encoding repair --------------------------------------------------------

# A UTF-8 multi-byte sequence decoded as Latin-1: a lead byte (U+00C2-U+00F4)
# followed by 1-3 continuation bytes (U+0080-U+00BF).
_MOJIBAKE_RE = re.compile(r"[Â-ô][\u0080-¿]{1,3}")


def fix_mojibake(text: str) -> str:
    def _fix(m: re.Match) -> str:
        s = m.group(0)
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s
    return _MOJIBAKE_RE.sub(_fix, text).replace(" ", " ").replace("­", "")


# --- line classification ----------------------------------------------------

_LABEL_WORDS = (r"(?:Petitioners?|Applicants?|Respondents?|Opponents?|Accused|Complainants?"
                r"|Plaintiffs?|Defendants?|Appellants?|Deponent|Claimants?|Opposite\s+Part(?:y|ies)"
                r"|Non-?Applicants?|Objectors?|Decree[- ]Holders?|Judgment[- ]Debtors?)")
# "_____ Applicant", "...Petitioner", "Applicant/Accused", "…Respondents"
_LABEL_RE = re.compile(rf"^[_.…\s]*{_LABEL_WORDS}(?:\s*(?:/|and)\s*{_LABEL_WORDS})*"
                       r"(?:\s+No\.?\s*\d+)?\s*[.:]?$", re.I)
_GAP_RE = re.compile(r"\S(?:[ ]*\t[ \t]*){2,}(?=\S)|\S[ ]{8,}(?=\S)")
_VERSUS_RE = re.compile(r"^(?:versus|vs\.?|v/s\.?|v\.)$", re.I)
_BLANK_RUN_RE = re.compile(r"\(?_{2,}\)?")

# Marker then text; the space is optional when the text starts with a capital
# or a quote ("(1)The Petitioners ...", common in the corpus).
_PARA_RE = re.compile(r"^(?:(\d{1,3})[.)]|\((\d{1,3})\))(?:\s+|(?=[A-Z\"“‘'(\[]))(\S.*)$")
_NESTED_RE = re.compile(r"^(\d{1,3}\.\d{1,3}(?:\.\d{1,3})?)\.?\s+(\S.*)$")
_SUB_RE = re.compile(r"^(\((?:[a-zA-Z]|[ivx]{1,5})\)|(?:[a-z]|[ivx]{1,5})\))(?:\s+|(?=[A-Z\"“‘'(\[]))(\S.*)$")
# A "documents required" list appended after the draft (378 of 498 templates).
_DOCS_LIST_RE = re.compile(
    r"^\s*(?:list of (?:required )?documents|(?:required|necessary|supporting) documents"
    r"|documents (?:required|to be (?:attached|annexed|filed))|checklist)\b", re.I)
_GROUND_RE = re.compile(r"^([A-Z])[.)]\s+(\S.*)$")

_BODY_START_RE = re.compile(
    r"^(?:to,?$|sir,?$|madam,?$|respected\b|the\s+humble\b|the\s+.*\b(?:submits?|states?)\b"
    r"|.*\b(?:showeth|sheweth)\b|.*\bmost\s+respectfully\b)", re.I)
_SIGN_HINT_RE = re.compile(
    r"^(?:advocate\b|counsel\b|deponent\b|signature\b|sd/-|yours\b|through\b|"
    rf"{_LABEL_WORDS}$)", re.I)
_PLACE_DATE_RE = re.compile(r"^(?:place|date|dated)\s*[:\-]", re.I)
_MATTER_RE = re.compile(r"^in\s+the\s+matter\b", re.I)
_CASE_NO_RE = re.compile(
    r"^(?:[A-Za-z.()/ ]{0,60}\b(?:application|petition|appeal|case|suit|complaint|revision|"
    r"reference|bail|writ)\b[A-Za-z.()/ ]{0,30}\bno\b\.?|[A-Z][A-Za-z.()/ ]{0,40}\bno\.\s*\[\[)", re.I)
_SIGN_STRONG_RE = re.compile(r"^(?:sd\s*[/.]|advocate\s+for\b|counsel\s+for\b|deponent\b|signature\b)", re.I)
_TERMINAL = ".:;?!"
_STANDALONE_RE = re.compile(r"^(?:(?:to|sir|madam|dear\s+sir|respected\s+sir)\s*,?|[-•*]\s*\S.*|\d{1,2}\.\S.*)$", re.I)
_BLANK_ONLY_RE = re.compile(r"^(?:\[\[blank\]\][\s,.]*)+$")


def _lead_width(line: str) -> int:
    w = 0
    for ch in line:
        if ch == "\t":
            w += 8
        elif ch == " ":
            w += 1
        else:
            break
    return w


def _is_caps_heading(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    return (len(s) <= 90 and len(s.split()) >= 1 and len(letters) >= 4
            and all(c.isupper() for c in letters) and not _PARA_RE.match(s))


def _blankify(s: str) -> str:
    return _BLANK_RUN_RE.sub("[[blank]]", s)


class _Line:
    __slots__ = ("raw", "text", "lead", "left", "right", "centered")

    def __init__(self, raw: str):
        self.raw = raw.rstrip()
        stripped = self.raw.strip()
        self.lead = _lead_width(self.raw)
        self.left, self.right = stripped, ""
        m = _GAP_RE.search(stripped)
        if m:
            # Split at the LAST wide gap: "Address: ...<tabs>_____ Applicant".
            parts = re.split(r"(?:[ ]*\t[ \t]*){2,}|[ ]{8,}", stripped)
            parts = [p.strip() for p in parts if p.strip()]
            if len(parts) >= 2:
                self.left, self.right = " ".join(parts[:-1]), parts[-1]
        if not self.right and self.lead >= 24 and _LABEL_RE.match(stripped):
            self.left, self.right = "", stripped
        self.text = stripped
        self.centered = bool(stripped) and not self.right and self.lead >= 24

    @property
    def blank(self) -> bool:
        return not self.text


def _split_header(raw: str) -> tuple[str, str]:
    """('title', 'body') from 'Draft Heading: ...\\nDraft Content: ...'."""
    title = ""
    m = re.match(r"\s*Draft Heading:\s*(.*)\n", raw)
    if m:
        title = m.group(1).strip()
        raw = raw[m.end():]
    raw = re.sub(r"^\s*Draft Content:\s*", "", raw)
    title = re.sub(r"^(?:Draft|Specimen|Format|or|of|for|\s)+", "", title, flags=re.I).rstrip(": ")
    return title or "Draft", raw


# --- assembly ----------------------------------------------------------------

def template_to_artifact(raw_text: str, *, doc_type: str = "", artifact_id: str = "tpl",
                         title: str | None = None) -> Artifact:
    """Turn one template's text into a block Artifact.

    Blanks ("_____", "(_____)") become [[blank]] placeholders. Paragraph
    numbers are re-assigned by number_paragraphs; the template's own nested
    numbering (1.1, (a)) is kept as sub-item markers.
    """
    tpl_title, body = _split_header(fix_mojibake(raw_text or ""))
    lines = [_Line(l) for l in body.replace("\r", "").split("\n")]

    sections: list[Section] = [Section(id="s1", heading="Cause title", blocks=[])]
    in_body = False
    party_left: list[str] = []      # plain lines waiting to join a party row
    pending_row: RowBlock | None = None
    last_para: ParaBlock | None = None
    last_text: TextBlock | None = None
    total = len(lines)
    # The draft ends where an appended documents list begins; signatures and
    # place/date are looked for just before that point, not at the file end.
    docs_idx = next((i for i, l in enumerate(lines) if _DOCS_LIST_RE.match(l.text)), total)

    def new_id() -> str:
        sec = sections[-1]
        return f"{sec.id}.b{len(sec.blocks) + 1}"

    def add(block):
        sections[-1].blocks.append(block)
        return block

    def flush_party():
        nonlocal party_left, pending_row
        if pending_row is not None:
            pending_row.left.extend(_blankify(x) for x in party_left)
            pending_row = None
        else:
            for x in party_left:
                add(TextBlock(id=new_id(), text=_blankify(x), align="left"))
        party_left = []

    def start_section(heading: str):
        nonlocal last_para, last_text
        flush_party()
        if sections[-1].blocks or len(sections) > 1:
            sections.append(Section(id=f"s{len(sections) + 1}",
                                    heading=heading.title()[:60], blocks=[]))
        last_para = last_text = None

    prev_len = 0        # length of the previous non-blank line, 0 after a blank
    for idx, ln in enumerate(lines):
        if ln.blank:
            flush_party()
            last_text = last_para = None
            prev_len = 0
            continue
        t = ln.text
        # The previous line ran to the template's wrap width, so this line
        # continues it even when the previous one ended a sentence.
        wrapped = prev_len >= 55
        prev_len = len(t)
        near_end = docs_idx - 14 <= idx < docs_idx
        if idx == docs_idx:
            in_body = True
            guidance = bool(re.search(r"required|generally|to be|checklist", t, re.I))
            start_section("Documents required" if guidance else "List of documents")
            add(HeadingBlock(id=new_id(), text=_blankify(t.rstrip(":")), underline=True))
            continue

        if not in_body and _BODY_START_RE.match(t):
            flush_party()
            in_body = True

        # Versus / Vs between the parties.
        if _VERSUS_RE.match(t):
            flush_party()
            add(TitleBlock(id=new_id(), text=t.strip().title() if t.isupper() else t,
                           bold=False))
            continue

        # A party / signature row: left text with a right-hand label.
        if ln.right:
            if in_body and near_end and not _LABEL_RE.match(ln.right):
                flush_party()
                add(RowBlock(id=new_id(), left=[_blankify(ln.left)] if ln.left else [],
                             right=[_blankify(ln.right)]))
                last_para = last_text = None
                continue
            if not in_body or _LABEL_RE.match(ln.right):
                if pending_row is not None:
                    flush_party()
                left = party_left + ([ln.left] if ln.left else [])
                party_left = []
                pending_row = add(RowBlock(id=new_id(), left=[_blankify(x) for x in left],
                                           right=[_blankify(ln.right)]))
                last_para = last_text = None
                continue

        # Centred line: title in the cause title, heading in the body.
        if ln.centered:
            flush_party()
            if in_body and (_SIGN_STRONG_RE.match(t) or (near_end and _SIGN_HINT_RE.match(t))):
                prev = sections[-1].blocks[-1] if sections[-1].blocks else None
                if isinstance(prev, SignatureBlock):
                    prev.lines.append(_blankify(t))
                else:
                    add(SignatureBlock(id=new_id(), lines=[_blankify(t)]))
            elif in_body:
                start_section(t)
                add(HeadingBlock(id=new_id(), text=_blankify(t), underline=_is_caps_heading(t)))
            else:
                add(TitleBlock(id=new_id(), text=_blankify(t)))
            continue

        if not in_body:
            # Cause title: court name on the first line, otherwise party details.
            prev = sections[-1].blocks[-1] if sections[-1].blocks else None
            if _MATTER_RE.match(t):
                flush_party()
                add(TextBlock(id=new_id(), text=_blankify(t), align="justify"))
                continue
            if (isinstance(prev, TextBlock) and _MATTER_RE.match(prev.text) and not party_left
                    and pending_row is None and prev.text[-1:] not in _TERMINAL
                    and not _CASE_NO_RE.match(t)):
                prev.text = f"{prev.text} {_blankify(t)}"
                continue
            if _CASE_NO_RE.match(t) and pending_row is None and not party_left:
                add(TitleBlock(id=new_id(), text=_blankify(t)))
                continue
            if not sections[0].blocks and not party_left and _is_caps_heading(t):
                add(TitleBlock(id=new_id(), text=_blankify(t)))
            elif pending_row is not None:
                pending_row.left.append(_blankify(t))
            else:
                party_left.append(t)
            continue

        # --- body ---
        if _is_caps_heading(t) and len(t) <= 70 and not t.endswith(","):
            start_section(t)
            add(HeadingBlock(id=new_id(), text=_blankify(t.rstrip(":")),
                             underline=True))
            continue
        if near_end and (_SIGN_HINT_RE.match(t) or ln.lead >= 24):
            flush_party()
            prev = sections[-1].blocks[-1] if sections[-1].blocks else None
            if isinstance(prev, SignatureBlock):
                prev.lines.append(_blankify(t))
            else:
                add(SignatureBlock(id=new_id(), lines=[_blankify(t)]))
            last_para = last_text = None
            continue
        if near_end and _PLACE_DATE_RE.match(t):
            add(TextBlock(id=new_id(), text=_blankify(t), align="left"))
            last_para = last_text = None
            continue

        m = _PARA_RE.match(t)
        if m:
            last_para = add(ParaBlock(id=new_id(), text=_blankify(m.group(3))))
            last_text = None
            continue
        m = _NESTED_RE.match(t) or _SUB_RE.match(t)
        if m and last_para is not None:
            last_para.sub.append(SubItem(marker=m.group(1)[:8], text=_blankify(m.group(2))))
            continue
        if m:
            last_para = add(ParaBlock(id=new_id(), label=m.group(1)[:12],
                                      text=_blankify(m.group(2))))
            continue
        m = _GROUND_RE.match(t)
        if m:
            last_para = add(ParaBlock(id=new_id(), label=f"{m.group(1)}.",
                                      text=_blankify(m.group(2))))
            last_text = None
            continue

        # Continuation of a hard-wrapped line, or a new plain paragraph.
        cont = _blankify(t)
        opens_block = bool(_BODY_START_RE.match(t)) and (
            last_text is None or last_text.text[-1:] in _TERMINAL + ",")
        # Salutations, bullets, "1.Heading" without a space, and blank-only
        # lines ("_____" address lines) stand alone, as does what follows them.
        standalone = bool(_STANDALONE_RE.match(t) or _BLANK_ONLY_RE.match(cont))
        after_standalone = last_text is not None and bool(
            _STANDALONE_RE.match(last_text.text) or _BLANK_ONLY_RE.match(last_text.text))
        if opens_block or standalone or after_standalone:
            last_para = None
            last_text = add(TextBlock(id=new_id(), text=cont,
                                      align="left" if len(t) < 60 else "justify"))
            continue
        if last_para is not None and (last_text is None):
            tgt = last_para.sub[-1] if last_para.sub else last_para
            if (tgt.text and tgt.text[-1] not in _TERMINAL) or wrapped or t[:1].islower() or t[:1].isdigit():
                tgt.text = f"{tgt.text} {cont}".strip()
                continue
        if last_text is not None and (last_text.text[-1:] not in _TERMINAL or wrapped
                                      or t[:1].islower()):
            last_text.text = f"{last_text.text} {cont}".strip()
            continue
        last_para = None
        last_text = add(TextBlock(id=new_id(), text=cont,
                                  align="left" if len(t) < 60 else "justify"))

    flush_party()
    sections = [s for s in sections if s.blocks]
    art = Artifact(artifact_id=artifact_id, title=(title or tpl_title)[:200],
                   doc_type=doc_type or "template", sections=sections)
    return number_paragraphs(art)


def layout_summary(artifact: Artifact) -> dict:
    """Counts used to judge how much layout a template carried."""
    counts: dict[str, int] = {}
    centred = 0
    for s in artifact.sections:
        for b in s.blocks:
            counts[b.type] = counts.get(b.type, 0) + 1
            if getattr(b, "align", None) == "center":
                centred += 1
    return {"sections": len(artifact.sections), "blocks": counts, "centred": centred}
