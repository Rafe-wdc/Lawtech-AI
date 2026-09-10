"""Remove diagrams and code fences from legal responses.

Advocate report 2026-09-10: a flowchart of remedies appeared in the middle
of a property-law opinion. The model had drawn it as ASCII art inside a
triple-backtick code fence, so it rendered in monospace, could not wrap,
and its columns were misaligned on screen. Legal writing for practitioners
is prose, numbered paragraphs and, on request, a table; a code fence never
carries anything a lawyer needs.

Two mechanical repairs, applied to every response in the output guardrail:

  * a fenced block that is a DRAWING (box-drawing characters, "+---+"
    frames, pipe-and-space column layouts, arrow chains) is removed
    outright. The sections that follow restate the same content in prose,
    as they did in the reported case.
  * any other fenced block is unfenced: its lines become ordinary text, so
    a statutory extract the model wrapped in a fence still reaches the
    reader, in the body font.

The prompt rule in INDIAN_LEGAL_BEHAVIORAL_DISCIPLINE asks the model not to
do this in the first place; this is the guarantee for when it does anyway.
"""
from __future__ import annotations

import re

_FENCE_RE = re.compile(r"(?ms)^[ \t]*```[^\n]*\n(.*?)^[ \t]*```[ \t]*$")
_BOX_CHARS = set("─│┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬━┃┏┓┗┛┣┫┳┻╋")
_FRAME_RE = re.compile(r"\+-{2,}\+|^[ \t]*\|[ \t]*$|-->|<--|==>|-{3,}>|^[ \t]*[|/\\]{1}[ \t]+[|/\\]", re.M)
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$", re.M)


def _is_drawing(body: str) -> bool:
    """A fenced block is a drawing when it uses box characters, ascii
    frames or arrow chains, or when most of its lines are laid out in
    aligned columns of spaces (three or more spaces inside the line)."""
    if any(ch in _BOX_CHARS for ch in body):
        return True
    if _FRAME_RE.search(body):
        return True
    lines = [l for l in body.split("\n") if l.strip()]
    if not lines:
        return False
    # a markdown table inside a fence is not a drawing; unfence it instead
    if sum(1 for l in lines if _TABLE_ROW_RE.match(l)) >= max(2, len(lines) // 2):
        return False
    columnar = sum(1 for l in lines if re.search(r"\S {3,}\S", l))
    return columnar >= max(2, len(lines) // 2)


def strip_diagrams(text: str) -> tuple[str, dict]:
    """Remove drawing blocks and unfence the rest. Returns (text, stats)."""
    stats = {"diagrams_removed": 0, "fences_unwrapped": 0}
    if not text or "```" not in text:
        return text, stats

    def _repl(m: re.Match) -> str:
        body = m.group(1)
        if _is_drawing(body):
            stats["diagrams_removed"] += 1
            return ""
        stats["fences_unwrapped"] += 1
        return body.strip("\n") + "\n"

    out = _FENCE_RE.sub(_repl, text)
    # an unclosed trailing fence: sanitize_markdown closes it before we run,
    # but be safe and drop a lone opener that survived.
    out = re.sub(r"(?m)^[ \t]*```[^\n]*\n?", "", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out, stats
