"""Notes in a draft that are for the lawyer, not the court.

A draft's answer carries short notes for the advocate: what the added-facts
check took out, what the review could not fix, a section that failed, a
case-law warning, a court search that failed. They belong on screen (chat
and draft panel). They must not be printed into the Word or PDF file the
advocate files, where they would sit before the title or after the
signature. The /pyapi/export endpoint removes them on both download paths:
the Markdown one (core/export.py) and the draft panel's block one
(core/draft_docx.py).

Five notes are written only into drafts, so they are removed wherever they
appear. The failed-court-search line is also written into research
answers, where a saved memo should keep it, so it is removed only when the
export is known to be a draft.
"""
from __future__ import annotations

import re

# Each string is the bold lead the note starts with. The tests check them
# against the code that writes the note.
DRAFT_NOTE_MARKERS = (
    "**Details taken out**",                  # core/added_facts.removal_note
    "**Please check before filing**",         # agents/drafting._unresolved_review_banner
    "**Draft incomplete**",                   # agents/drafting, a section failed
    "**This draft came back in English",      # agents/drafting, language safety net
    "**Verify the case law before filing.**",  # agents/drafting, unverifiable citation
)
# agents/orchestrator._missing_sources_banner
_SEARCH_FAILED_RE = re.compile(r"The .{1,80}? judgment search could not be completed in this run")
_LEAD_RE = re.compile(r"^(?:\s*>)*\s*(?:⚠\s*)?")
_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")


def _lead(line: str) -> str:
    """The line without quote markers and a leading warning sign."""
    return _LEAD_RE.sub("", line or "", count=1)


def _starts_note(line: str, is_draft: bool) -> bool:
    lead = _lead(line)
    if lead.startswith(DRAFT_NOTE_MARKERS):
        return True
    return is_draft and "⚠" in line[:8] and bool(_SEARCH_FAILED_RE.match(lead))


def _continues_note(line: str) -> bool:
    """A quoted line or a list item: the rest of a note's block."""
    s = line.lstrip()
    return s.startswith(">") or s.startswith("- ")


_BULLET_LABELS = {"•", "-", "–", "*", "·"}


def _continues_note_block(block) -> bool:
    """The panel's form of a note's list item: a quoted or "- " text block,
    or an unnumbered bullet paragraph (draft_to_blocks turns "- item" into
    a para block labelled "•")."""
    text = getattr(block, "text", None)
    if not isinstance(text, str):
        return False
    if _continues_note(text):
        return True
    return (getattr(block, "type", "") == "para" and getattr(block, "number", None) is None
            and (getattr(block, "label", None) or "").strip() in _BULLET_LABELS)


def has_draft_note(md: str) -> bool:
    """True when the text carries a note only drafts get, so it is a draft."""
    return any(_lead(line).startswith(DRAFT_NOTE_MARKERS) for line in (md or "").splitlines())


def strip_from_markdown(md: str, is_draft: bool = False) -> str:
    """The text without the lawyer's notes, for a Word or PDF download.

    A note is its first line plus the quoted lines and list items after it,
    across blank lines. A "---" rule left right before a removed note goes
    too. Returns the text unchanged when there is no note.
    """
    lines = (md or "").split("\n")
    if not any(_starts_note(line, is_draft) for line in lines):
        return md
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not _starts_note(lines[i], is_draft):
            out.append(lines[i])
            i += 1
            continue
        while out and (not out[-1].strip() or _RULE_RE.match(out[-1])):
            out.pop()
        i += 1
        while i < len(lines) and (not lines[i].strip() or _continues_note(lines[i])) \
                and not _starts_note(lines[i], is_draft):
            i += 1
        if out and i < len(lines):
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def strip_from_artifact(artifact):
    """The draft panel's document without the lawyer's notes.

    The panel stores each line of a note as its own text block, so a note is
    the block it starts in plus the quoted and list blocks right after it.
    Sections left empty are dropped. A draft that would be left with nothing
    is returned unchanged.
    """
    sections, changed = [], False
    for sec in artifact.sections:
        kept, skipping = [], False
        for block in sec.blocks:
            text = getattr(block, "text", None)
            if isinstance(text, str) and _starts_note(text, True):
                skipping = changed = True
                continue
            if skipping and _continues_note_block(block):
                continue
            skipping = False
            kept.append(block)
        if kept:
            sections.append(sec.model_copy(update={"blocks": kept}))
        elif sec.blocks:
            changed = True
    if not changed or not sections:
        return artifact
    return artifact.model_copy(update={"sections": sections})
