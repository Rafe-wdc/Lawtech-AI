"""Keep every response inside the answer box.

Advocate report 2026-09-10: "a few part of the answer is going beyond the
answer box". A markdown response overflows its container horizontally only
when it contains something the renderer cannot wrap. Scanning 80 saved
responses found two shapes that actually occur:

  * a bare whitelisted PDF URL (api.sci.gov.in / lawttorney S3), 70-110
    unbreakable characters. External URLs are already stripped; these are
    kept on purpose because they are the judgment links. As a markdown
    link they render as a short label and wrap; bare, they render as the
    whole URL and do not.
  * a run of 20-40 underscores used as a fill-in blank in a draft
    ("Bail Application No. ______________________ of 2026").

Both are repaired here mechanically. Wide tables are the third shape; a
table's width is the renderer's problem (overflow-x on the container) and
is left to the frontend, which is where it belongs.
"""
from __future__ import annotations

import re

from core.url_filter import _BARE_URL_RE, _MD_LINK_RE, is_whitelisted_url

# Underscore / dash / dot / equals runs. 15 keeps a visible signature blank
# on a phone-width answer box; 20+ overflows it.
_LONG_RUN_RE = re.compile(r"([_=.─-╿])\1{19,}")
_RUN_KEEP = 15
# A whole line of dashes (or dashes+spaces) is a horizontal rule.
_RULE_LINE_RE = re.compile(r"(?m)^[ \t]*-{4,}[ \t]*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


def _label_for(url: str) -> str:
    u = url.lower()
    if "api.sci.gov.in" in u:
        return "Supreme Court judgment (PDF)"
    if "lawttorney.s3" in u:
        return "Judgment (PDF)"
    return "Document (PDF)"


def shorten_bare_urls(text: str) -> tuple[str, int]:
    """Turn bare whitelisted URLs into `[label](url)` links. URLs already
    inside a markdown link are left alone. Returns (text, count)."""
    if "http" not in text:
        return text, 0
    protected: list[str] = []

    def _keep(m: re.Match) -> str:
        protected.append(m.group(0))
        return f"\x00{len(protected) - 1}\x00"

    tmp = _MD_LINK_RE.sub(_keep, text)
    n = 0

    def _wrap(m: re.Match) -> str:
        nonlocal n
        url = m.group(0)
        tail = ""
        while url and url[-1] in ".,;:)]!?":
            tail = url[-1] + tail
            url = url[:-1]
        if not is_whitelisted_url(url):
            return m.group(0)
        n += 1
        return f"[{_label_for(url)}]({url}){tail}"

    tmp = _BARE_URL_RE.sub(_wrap, tmp)
    out = re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], tmp)
    return out, n


def cap_long_runs(text: str) -> tuple[str, int]:
    """Cap runs of underscores / dots / box characters at 15 and turn a line
    of dashes into a markdown rule. Table separator rows are untouched."""
    n = 0
    out_lines = []
    for line in text.split("\n"):
        if _TABLE_SEP_RE.match(line) and "|" in line:
            out_lines.append(line)
            continue
        if _RULE_LINE_RE.match(line):
            out_lines.append("---")
            n += 1
            continue
        new, k = _LONG_RUN_RE.subn(lambda m: m.group(1) * _RUN_KEEP, line)
        n += k
        out_lines.append(new)
    return "\n".join(out_lines), n


def keep_inside_answer_box(text: str) -> tuple[str, dict]:
    """Apply every overflow repair. Returns (text, {"urls": n, "runs": n})."""
    if not text:
        return text, {"urls": 0, "runs": 0}
    text, urls = shorten_bare_urls(text)
    text, runs = cap_long_runs(text)
    return text, {"urls": urls, "runs": runs}
