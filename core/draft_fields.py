"""Blanks in a draft as form fields: the "fill the blanks" form beside the panel.

A draft marks every blank as [[label]] (core.draft_blocks). The writer spells
the same blank differently in different places, [DISTRICT] in the cause title
and [District] in an address, so blanks are grouped by a key that ignores case,
spacing, quote style and trailing punctuation. One value fills every blank
with that key; a single place can still be typed over in the panel.

A value takes the case of the words next to its blank: capitals in the cause
title ("SESSIONS AT PUNE", "STATE OF MAHARASHTRA"), as typed inside a sentence
("District Pune"). A blank alone on its line follows its label, so
[NAME OF APPLICANT] in the party block gives "RAHUL SHARMA".
"""

from __future__ import annotations

import re
import unicodedata

from core.draft_blocks import (
    _PLACEHOLDER_RE, Artifact, HeadingBlock, ParaBlock, RowBlock, SignatureBlock,
    TableBlock, TextBlock, TitleBlock, _block_texts,
)

MAX_VALUE_CHARS = 500
_CONTEXT_CHARS = 50


def field_key(label: str) -> str:
    """"[DISTRICT]", "[District]" and "[district ]" share one key."""
    s = unicodedata.normalize("NFKC", label or "")
    s = s.replace("’", "'").replace("‘", "'").replace("'", "")
    s = re.sub(r"\s+", " ", s).strip(" .:;,-").casefold()
    return s


# A hint for the form's input: a date picker, a number pad, a ₹ prefix, a
# multi-line box. "text" otherwise. The value is always sent back as text.
_KINDS = (
    ("date", re.compile(r"\bdated?\b|दिनांक|तारीख", re.I)),
    ("amount", re.compile(r"\bamount\b|\brs\b|₹|\brupees\b|\bfees?\b|\brent\b|\bcompensation\b|राशि", re.I)),
    ("number", re.compile(r"\bage\b|\baged\b|\byear\b|उम्र|आयु|वर्ष", re.I)),
    ("address", re.compile(r"\baddress\b|\bresidence\b|पता", re.I)),
)


def _kind(label: str) -> str:
    for kind, rx in _KINDS:
        if rx.search(label):
            return kind
    return "text"


def _display_label(spellings: list[str]) -> str:
    """The first spelling that is not all capitals, else the first one."""
    for s in spellings:
        if not s.isupper():
            return s
    return spellings[0]


def _context(text: str, start: int, end: int) -> str:
    """The words around one blank, as plain text with blanks shown as [x],
    for the form's hint ("Sessions Court, [District]")."""
    a, b = max(0, start - _CONTEXT_CHARS), min(len(text), end + _CONTEXT_CHARS)
    if a > 0:                                   # start on a whole word
        sp = text.find(" ", a, start)
        a = sp + 1 if sp != -1 else a
    if b < len(text):                           # end on a whole word
        sp = text.rfind(" ", end, b)
        b = sp if sp != -1 else b
    s = _PLACEHOLDER_RE.sub(r"[\1]", text[a:b].strip())
    s = re.sub(r"\*\*|__|\*", "", s).replace("[[", "[").replace("]]", "]")
    return ("…" if a > 0 else "") + s + ("…" if b < len(text) else "")


def fields(artifact: Artifact) -> list[dict]:
    """One entry per distinct blank, in the order the blanks first appear:
    {key, label, kind, count, places: [{section_id, block_id, context}]}."""
    out: dict[str, dict] = {}
    spellings: dict[str, list[str]] = {}
    for sec in artifact.sections:
        for b in sec.blocks:
            for t in _block_texts(b):
                for m in _PLACEHOLDER_RE.finditer(t):
                    label = m.group(1).strip()
                    key = field_key(label)
                    if not key:
                        continue
                    f = out.setdefault(key, {"key": key, "label": label, "kind": "text",
                                             "count": 0, "places": []})
                    f["count"] += 1
                    f["places"].append({"section_id": sec.id, "block_id": b.id,
                                        "context": _context(t, m.start(), m.end())})
                    spellings.setdefault(key, []).append(label)
    for key, f in out.items():
        f["label"] = _display_label(spellings[key])
        f["kind"] = _kind(f["label"])
    return list(out.values())


# Keys the API sends next to a draft but never stores; a client may send a
# whole draft back (Word export), so they are dropped before validating.
RESPONSE_ONLY_KEYS = frozenset({"fields", "blanks", "filled", "unknown"})


def fields_payload(artifact: Artifact) -> dict:
    """`fields` and `blanks` (every place still to fill), sent next to the
    draft in responses and events."""
    found = fields(artifact)
    return {"fields": found, "blanks": sum(f["count"] for f in found)}


def clean_value(value) -> str:
    """A form value as inline text: one line, no blank markers, bounded."""
    s = str(value if value is not None else "")
    s = re.sub(r"\s*[\r\n]+\s*", ", ", s.strip())
    s = s.replace("[[", "[").replace("]]", "]")
    return re.sub(r"[ \t]+", " ", s).strip(" ,")[:MAX_VALUE_CHARS]


_WORD_RE = re.compile(r"[^\W\d_]+")


def _cased(words: list[str]) -> bool:
    return any(c.isupper() or c.islower() for w in words for c in w)


def _capitals_here(text: str, start: int, end: int, label: str) -> bool:
    """Whether the words next to a blank are in capitals: the three before it,
    else the three after it, else the blank's own label."""
    words = _WORD_RE.findall(_PLACEHOLDER_RE.sub(" ", text[:start]))[-3:]
    if not _cased(words):
        words = _WORD_RE.findall(_PLACEHOLDER_RE.sub(" ", text[end:]))[:3]
    if _cased(words):
        return "".join(words).isupper()
    return label.isupper()


def _map_texts(b, fn) -> None:
    if isinstance(b, (TitleBlock, HeadingBlock, TextBlock)):
        b.text = fn(b.text)
    elif isinstance(b, ParaBlock):
        b.text = fn(b.text)
        for s in b.sub:
            s.text = fn(s.text)
    elif isinstance(b, RowBlock):
        b.left = [fn(t) for t in b.left]
        b.right = [fn(t) for t in b.right]
    elif isinstance(b, SignatureBlock):
        b.lines = [fn(t) for t in b.lines]
    elif isinstance(b, TableBlock):
        b.header = [fn(t) for t in b.header]
        b.rows = [[fn(c) for c in r] for r in b.rows]


def fill(artifact: Artifact, values: dict) -> tuple[Artifact, list[str], list[str]]:
    """Fill blanks from the form. `values` maps a field key (or a label) to its
    value; empty values are skipped. Returns (new artifact, keys filled, keys
    that match no blank in this draft). The input artifact is not changed."""
    wanted: dict[str, str] = {}
    for k, v in (values or {}).items():
        key, val = field_key(str(k)), clean_value(v)
        if key and val:
            wanted[key] = val
    present = {f["key"] for f in fields(artifact)}
    unknown = sorted(k for k in wanted if k not in present)
    filled: dict[str, None] = {}

    def _fill_text(t: str) -> str:
        def _sub(m: re.Match) -> str:
            label = m.group(1).strip()
            key = field_key(label)
            if key not in wanted:
                return m.group(0)
            filled.setdefault(key, None)
            caps = _capitals_here(t, m.start(), m.end(), label)
            return wanted[key].upper() if caps else wanted[key]

        return _PLACEHOLDER_RE.sub(_sub, t)

    new = artifact.model_copy(deep=True)
    for sec in new.sections:
        for b in sec.blocks:
            _map_texts(b, _fill_text)
    return new, list(filled), unknown
