"""Remove Gemini grounding artifacts from web-grounded answers.

Gemini with Google Search grounding sometimes copies its internal search
result objects into the visible text as if they were citations:

    Primary Citation: 1980 All LJ 524 [PerQueryResult(index="4.1.2", snippet="10 1980 ALL L.J. 524.")]

Nothing in this codebase produces `PerQueryResult`; the model writes it.
Nothing rendered or removed it either, so it reached a lawyer verbatim
(prod, 2026-09-22). The objects can be left unclosed, several can share one
bracket, and `[cite: 3]` / `[cite_start]` tags appear the same way. All of
it is removed; the prose around it is kept.
"""

import re

# One PerQueryResult(...) object: keyword=value fields, quoted or bare, and a
# closing paren that is sometimes missing.
_PER_QUERY_RESULT_RE = re.compile(
    r"PerQueryResult\(\s*"
    r"(?:\w+\s*=\s*(?:\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|[\w.:\-]+)\s*,?\s*)*"
    r"\)?"
)
_CITE_TAG_RE = re.compile(r"\[cite(?:_start|_end)?(?:\s*:\s*[\d,\s]+)?\]")
_EMPTY_BRACKETS_RE = re.compile(r"\[\s*[,;\s]*\]")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"[ \t]+([.,;:)\]])")


def strip_grounding_artifacts(text: str) -> tuple[str, int]:
    """Return (cleaned text, number of artifacts removed). Cheap no-op when
    the text carries none."""
    if not text or ("PerQueryResult" not in text and "[cite" not in text):
        return text, 0
    cleaned, n_objects = _PER_QUERY_RESULT_RE.subn("", text)
    cleaned, n_tags = _CITE_TAG_RE.subn("", cleaned)
    cleaned = _EMPTY_BRACKETS_RE.sub("", cleaned)
    cleaned = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned, n_objects + n_tags
