"""External-web-link redaction for API responses.

Policy (set 2026-08-22): the API must not surface external web URLs to the
end user, whether they arrive via Scenario Google-Search grounding, the
tier-3 ``web_search_fallback``, or any prose the LLM emits. Only two URL
hosts are considered "our own" and allowed to appear in the response:

- ``lawttorney.s3.<region>.amazonaws.com``  — High Court judgment PDFs
- ``api.sci.gov.in``                         — Supreme Court PDFs (canonical)

Three surfaces are scrubbed:

1. ``source_metadata`` list in the final API payload — records whose only
   link is external are dropped; records that carry a whitelisted link
   have any *external* URL field nulled.
2. ``final_response`` prose — inline URLs, markdown links, and labelled
   wrappers (``See:``, ``Source:``, ...) are stripped when the URL is
   not whitelisted.
3. SSE token stream — a rolling-buffer scrubber holds tokens until the
   trailing region is guaranteed not to be mid-URL, then emits the safe
   prefix.

Whitelist check is prefix-based, so URLs with spaces in the S3 key path
(``bombay high court/``, ``gujarat high court/``, ...) are still
recognised as safe on their scheme+host segment.
"""
from __future__ import annotations

import re
from typing import Iterable


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

# Any URL whose lowercased form starts with one of these prefixes is kept.
# Prefix-match (not urlparse) so S3 keys containing spaces
# (``.../bombay high court/xxx.pdf``) don't confuse the check.
_WHITELIST_PREFIXES: tuple[str, ...] = (
    "https://lawttorney.s3.",       # any region: lawttorney.s3.ap-south-1.amazonaws.com, etc.
    "http://lawttorney.s3.",
    "https://api.sci.gov.in/",
    "http://api.sci.gov.in/",
)


def is_whitelisted_url(url: str | None) -> bool:
    """True when ``url`` points at a Lawttorney-owned S3 bucket or api.sci.gov.in."""
    if not url:
        return False
    u = url.strip().lower()
    return any(u.startswith(p) for p in _WHITELIST_PREFIXES)


# ---------------------------------------------------------------------------
# source_metadata sanitiser
# ---------------------------------------------------------------------------

def sanitize_source_records(records: list | None) -> list:
    """Keep ONLY source records that carry at least one whitelisted PDF URL.

    Post-2026-08-22 policy: the API `source_metadata` payload should surface
    ONLY sources the user can click through to an authoritative PDF. Every
    other record — Legal_Concepts markers, Scenario web-grounding records,
    tier-3 web_search_fallback records, Legislation/Newacts/Constitution/
    Maxim records that carry only citation text and no PDF, Document/PDF-
    chat records — is dropped from the outgoing payload.

    A record survives iff at least one of the following is a whitelisted URL:
      - ``doc_link`` field
      - ``web_url`` field
      - any entry's ``url`` in the ``pdf_links`` list

    Surviving records also have any *non-whitelisted* URL fields nulled
    (defensive — should not happen because the survival rule already
    requires a whitelisted URL, but keeps the payload strictly clean).
    """
    if not records:
        return records or []

    cleaned: list = []
    for rec in records:
        if not isinstance(rec, dict):
            # Defensive — non-dict entries have no URL to whitelist; drop.
            continue

        web_url = (rec.get("web_url") or "").strip() if isinstance(rec.get("web_url"), str) else ""
        doc_link = (rec.get("doc_link") or "").strip() if isinstance(rec.get("doc_link"), str) else ""
        pdf_links = rec.get("pdf_links") or []
        if not isinstance(pdf_links, list):
            pdf_links = []

        has_whitelisted_doc = doc_link and is_whitelisted_url(doc_link)
        has_whitelisted_web = web_url and is_whitelisted_url(web_url)
        whitelisted_pdf_links = [
            p for p in pdf_links
            if isinstance(p, dict) and is_whitelisted_url(p.get("url"))
        ]

        if not (has_whitelisted_doc or has_whitelisted_web or whitelisted_pdf_links):
            # No whitelisted PDF anywhere on this record — drop.
            continue

        # Record survives. Strip any non-whitelisted URL fields defensively.
        mutated = dict(rec)
        if web_url and not is_whitelisted_url(web_url):
            mutated["web_url"] = None
            mutated["web_title"] = None
        if doc_link and not is_whitelisted_url(doc_link):
            mutated["doc_link"] = None
        if len(whitelisted_pdf_links) != len(pdf_links):
            mutated["pdf_links"] = whitelisted_pdf_links

        cleaned.append(mutated)

    return cleaned


# ---------------------------------------------------------------------------
# Prose sanitiser
# ---------------------------------------------------------------------------

# Markdown link: [label](url). Terminates at `)`, so spaces in S3 URL paths
# are consumed correctly.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")

# Bare URL — terminates at whitespace. S3 URLs with literal spaces will only
# match the first segment, but the whitelist check is prefix-based so the
# scheme+host prefix decision still works.
_BARE_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# Labelled wrapper introducing a URL. When the URL is stripped, the wrapper
# becomes dead prose ("See:") so we remove them together.
_WRAPPED_URL_RE = re.compile(
    r"(?:\*\*)?(?P<label>See|Source|Ref|Reference|Read more|More info|Available at)"
    r"(?:\*\*)?\s*:?\s*(?P<url>https?://\S+)",
    re.IGNORECASE,
)


def _strip_trailing_punct(url: str) -> str:
    """Trim trailing sentence punctuation the URL never legitimately ends on."""
    return url.rstrip(".,;:!?)>\"'")


# Trailing footer heading + block that leaks the input `## Retrieved Sources`
# marker back into the response. Belt-and-braces safety net alongside the
# prompt directive in `INDIAN_LEGAL_AUTHORIZED_SOURCES` — if the LLM still
# emits it we strip the whole block. Matches a heading (`##`, `###`, or
# bold `**...**`) whose label is one of the source-block synonyms, then
# swallows everything after it until end-of-string.
_TRAILING_SOURCES_FOOTER_RE = re.compile(
    r"(?is)\n+\s*(?:#{2,4}\s*|\*\*)?"
    r"(?:retrieved\s+sources|sources\s*&\s*citations|citations|"
    r"references|sources)\s*:?\s*(?:\*\*)?\s*\n[\s\S]*\Z"
)

# Internal source-registry ids (grounding tokens). Should never appear in
# visible output; strip any line whose only content is one of these ids
# (or an id + short label — the LLM sometimes echoes them inline).
_INTERNAL_ID_LINE_RE = re.compile(
    r"(?im)^\s*[-*\d.]*\s*\**\s*"
    r"(?:sci|sci_judgment|hc|leg|web|newacts|constitution|maxim|gst)"
    r"[-_][a-z0-9._-]+\**\s*.*$\n?"
)


def sanitize_prose(text: str | None) -> str:
    """Strip non-whitelisted URLs, labelled wrappers, and leaked source
    footers / internal ids from the final response prose."""
    if not text:
        return text or ""

    # Pass 0 — drop any trailing `## Retrieved Sources` / `## Sources` /
    # `## Citations` / `## References` block. LLM sometimes echoes the input
    # marker back as a footer despite the prompt directive.
    text = _TRAILING_SOURCES_FOOTER_RE.sub("", text)

    # Pass 0b — strip any residual line that's just an internal grounding
    # id (e.g. `1. **sci-44015** Suraj Lamp...`). Catches mid-response echoes.
    text = _INTERNAL_ID_LINE_RE.sub("", text)

    # Pass 1 — labelled wrappers ("See: http://…"). Do this before bare-URL
    # stripping so we also remove the wrapper word.
    def _wrap_sub(m: re.Match) -> str:
        url = _strip_trailing_punct(m.group("url"))
        return m.group(0) if is_whitelisted_url(url) else ""
    text = _WRAPPED_URL_RE.sub(_wrap_sub, text)

    # Pass 2 — markdown links [label](url). Keep the label, drop the URL
    # when not whitelisted.
    def _md_sub(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        return m.group(0) if is_whitelisted_url(url) else label
    text = _MD_LINK_RE.sub(_md_sub, text)

    # Pass 3 — bare URLs left in prose.
    def _bare_sub(m: re.Match) -> str:
        url = m.group(0)
        return url if is_whitelisted_url(_strip_trailing_punct(url)) else ""
    text = _BARE_URL_RE.sub(_bare_sub, text)

    # Tidy the orphan whitespace left behind.
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


# ---------------------------------------------------------------------------
# Streaming scrubber
# ---------------------------------------------------------------------------

# Any suffix of "https://" can be the start of an in-flight URL. We include
# short partials ("h", "ht") too so the filter is safe against arbitrarily
# small chunking. Cost: a 1-token hold on any buffer that happens to end in
# "h" or "ht" (words like "with", "which") — the tail flushes on the next
# push, so no data is lost and the delay is invisible at production chunk
# sizes (>=20 chars).
_SCHEME_PARTIAL_TAILS: tuple[str, ...] = (
    "https:/", "https:", "https", "http:/", "http:", "http", "htt", "ht", "h",
)

_URL_MATCH_ANYWHERE = re.compile(r"https?://\S+", re.IGNORECASE)


class StreamingUrlFilter:
    """Rolling-buffer URL scrubber for SSE token streams.

    Emits token content only up to the earliest position where an unfinished
    URL could not yet exist. Everything from a live ``http`` prefix onward
    is held until either the URL terminates (whitespace, EOL, quote, etc.)
    or ``flush()`` is called on stream close.

    Usage::

        filt = StreamingUrlFilter()
        for chunk in stream:
            safe = filt.push(chunk)
            if safe:
                writer({"type": "token", "content": safe})
        tail = filt.flush()
        if tail:
            writer({"type": "token", "content": tail})
    """

    # Hard cap on how much a single in-flight URL region may grow before we
    # give up holding it and force a flush. No legitimate URL should get
    # anywhere near this — the cap only exists so a pathological stream
    # cannot pin unbounded memory in the buffer.
    _MAX_BUFFER = 4096

    def __init__(self) -> None:
        self._buffer = ""

    def push(self, token: str) -> str:
        """Feed one streamed token in; return the safe-to-emit prefix out."""
        if not token:
            return ""
        self._buffer += token
        return self._drain()

    def flush(self) -> str:
        """Drain the held buffer through the prose sanitiser on stream close."""
        if not self._buffer:
            return ""
        tail = self._buffer
        self._buffer = ""
        return sanitize_prose(tail)

    # -- internals -----------------------------------------------------

    def _drain(self) -> str:
        """Emit as much of the current buffer as is guaranteed safe."""
        out_parts: list[str] = []
        while True:
            idx = self._find_first_url_start(self._buffer)
            if idx == -1:
                # No URL anywhere and no scheme-in-flight at the tail — emit all.
                out_parts.append(self._buffer)
                self._buffer = ""
                break

            safe_prefix = self._buffer[:idx]
            remainder = self._buffer[idx:]

            m = _URL_MATCH_ANYWHERE.match(remainder)
            # A URL match is only "complete" when a terminator character
            # follows it (i.e. the match doesn't consume all the way to
            # end-of-buffer). If it does consume to end, the URL might
            # still extend in the next chunk — hold it.
            if m and m.end() < len(remainder):
                url = m.group(0)
                keep = url if is_whitelisted_url(_strip_trailing_punct(url)) else ""
                out_parts.append(safe_prefix + keep)
                self._buffer = remainder[m.end():]
                continue

            # Either no match (scheme partial still building) or match
            # consumed to end (URL still extending). Hold everything from
            # idx onwards until the next chunk / flush.
            if len(remainder) > self._MAX_BUFFER:
                # Safety valve — treat the runaway as complete-and-invalid.
                out_parts.append(safe_prefix + sanitize_prose(remainder))
                self._buffer = ""
            else:
                out_parts.append(safe_prefix)
                self._buffer = remainder
            break

        return "".join(out_parts)

    @staticmethod
    def _find_first_url_start(text: str) -> int:
        """Earliest position that could begin a URL, or -1 if none.

        Two cases:
          (a) a complete ``http://`` or ``https://`` scheme anywhere in the
              buffer → return that index.
          (b) the buffer *ends* with a plausible scheme partial
              (``htt``, ``http``, ``https:``, ...) → return the position
              where the partial starts, so we hold it for the next chunk.
        """
        idx = -1
        for scheme in ("https://", "http://"):
            i = text.find(scheme)
            if i != -1 and (idx == -1 or i < idx):
                idx = i
        if idx != -1:
            return idx
        for partial in _SCHEME_PARTIAL_TAILS:
            if text.endswith(partial):
                return len(text) - len(partial)
        return -1