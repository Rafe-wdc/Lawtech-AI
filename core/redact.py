"""Brand-name redactor — strip LLM-provider and model-family tokens from
any string that may reach an external surface (API response, SSE event,
log line, error message).

Single source of truth. Add a new provider or model family by editing
_BRAND_PATTERNS and re-running tests; every caller picks up the change.

The redactor is intentionally lossy — it replaces a matched token with
the literal "[model]" rather than trying to be clever about case or
spacing. The goal is "never let the brand reach the user", not
"preserve sentence grammar".

Safe to apply repeatedly (idempotent: "[model]" contains no brand
tokens, so a second pass is a no-op).
"""

from __future__ import annotations

import re
from typing import Any

# Brand tokens that must not reach the user. Ordered longest-first so
# multi-word phrases match before bare single tokens. Word-boundary
# anchors prevent partial-word damage ("opening" must not match
# "openai", "geminate" must not match "gemini").
#
# The replacement is a single literal "[model]" — never include any of
# these brand tokens inside the replacement, or you create an infinite
# fixpoint problem.
#
# DESIGN NOTE: bare "google" is intentionally NOT redacted. The
# Google Docs / Google Drive integration is a user-facing feature
# whose connect-account UX, content-extraction messages, and shareable
# URLs ("docs.google.com/document/...") must stay readable. The
# narrow SDK identifiers ("google_genai", "vertex_ai") still get
# caught, and the bare model families ("gemini*", "gpt*", "claude*")
# carry the brand signal that actually matters for end users.
_BRAND_TOKENS: tuple[str, ...] = (
    # Provider SDK identifiers (multi-word, longest first).
    # Separator class includes `.` so that Python module paths
    # ("google.genai.errors...", "google.api_core...") get redacted
    # too — the resulting text starts with "[model]" rather than
    # leaking the provider in a stack trace.
    r"google[\s_.\-]?genai(?:[\s_.\-]?errors)?",
    r"google[\s_.\-]?gen[\s_.\-]?ai",
    r"google[\s_.\-]?generativeai",
    r"google[\s_.\-]?ai[\s_.\-]?generativelanguage(?:_v\d+\w*)?",
    r"google[\s_.\-]?api[\s_.\-]?core",
    r"vertex[\s_.\-]?ai",
    r"openai",
    r"anthropic",
    # Model families (with version suffix patterns)
    r"gemini[\s_-]*\d+(?:\.\d+)?[\s_-]*(?:flash|pro|ultra|nano)?(?:[\s_-]*lite)?",
    r"gemini",
    r"gpt[\s_-]?\d+(?:\.\d+)?[\s_-]*(?:o|mini|turbo|nano)?",
    r"gpt",
    r"claude[\s_-]?(?:opus|sonnet|haiku|instant)?[\s_-]?\d+(?:\.\d+)?(?:[\s_-]*(?:opus|sonnet|haiku|instant))?",
    r"claude",
    r"o1[\s_-]?(?:mini|preview)?",
    r"o3[\s_-]?(?:mini|preview)?",
)

# Compile a single alternation so one re.sub call handles everything.
# IGNORECASE so "Gemini", "GEMINI", "gemini-2.5-flash-lite" all match.
_BRAND_RE = re.compile(
    r"\b(?:" + "|".join(_BRAND_TOKENS) + r")\b",
    flags=re.IGNORECASE,
)

# When a model identifier is the WHOLE string (e.g. the `model` field of
# a token-usage call), prefer a cleaner placeholder.
_MODEL_PLACEHOLDER = "[model]"


def redact_brands(text: Any) -> Any:
    """Redact LLM brand/model tokens from a string.

    Non-strings pass through unchanged so this is safe to drop into
    log-extra serializers, dict-value transforms, etc.
    """
    if not isinstance(text, str) or not text:
        return text
    return _BRAND_RE.sub(_MODEL_PLACEHOLDER, text)


def redact_kv(value: Any) -> Any:
    """Redact recursively into dicts and lists; leave other types alone.

    Used by the logger formatter to sanitize structured-kv extras
    without forcing every call site to pre-redact.
    """
    if isinstance(value, str):
        return redact_brands(value)
    if isinstance(value, dict):
        return {k: redact_kv(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        seq_type = type(value)
        return seq_type(redact_kv(v) for v in value)
    return value
