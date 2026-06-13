"""Language detection and prompt localization utilities.

Supports auto-detection of 13+ Indian languages via langdetect,
with a Romanized-script heuristic for Hinglish and other Roman-script inputs.

Used by:
  agents/memory.py  — detect user_language from original_query
  config/prompts.py — localize_prompt() injected at each agent call site
"""

from __future__ import annotations

import re
from functools import lru_cache

from core.logger import get_logger

log = get_logger("Language")

# ---------------------------------------------------------------------------
# Language registry
# ---------------------------------------------------------------------------

SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "bn": "Bengali",
    "te": "Telugu",
    "mr": "Marathi",
    "ta": "Tamil",
    "kn": "Kannada",
    "ml": "Malayalam",
    "gu": "Gujarati",
    "pa": "Punjabi",
    "ur": "Urdu",
    "or": "Odia",
    "as": "Assamese",
    "sa": "Sanskrit",
}

# Languages where legal citations must clearly stay in English
_CITATION_LANGUAGES = SUPPORTED_LANGUAGES.keys() - {"en"}

# ---------------------------------------------------------------------------
# Romanized (Roman-script) keyword heuristics
# Catches "Hinglish" and other Indian-language queries typed in Latin script.
# ---------------------------------------------------------------------------

_ROMANIZED_HINTS: dict[str, list[str]] = {
    "hi": [
        "kaise", "kya", "karo", "karein", "batao", "dijiye", "chahiye",
        "lagta", "hoga", "wala", "mujhe", "humko", "mere", "hamare",
        "iska", "uska", "yeh", "woh", "bhi", "aur", "lekin", "kyunki",
        "dhara", "mukadma", "adalat", "vakil", "nyay", "jameen",
        "ipc", "crpc", "bns", "bnss", "bsa",          # common in Hinglish legal
    ],
    "mr": ["kasa", "aahe", "sangaa", "karayche", "mhanje", "tyala", "aapan"],
    "ta": ["eppadi", "enna", "sollunga", "pannum", "irukku", "seiyya"],
    "te": ["ela", "cheyali", "cheppandi", "undi", "kavali", "chesukonu"],
    "kn": ["hege", "enu", "helri", "madbekhu", "ide", "alla"],
    "ml": ["engane", "enna", "cheyyuka", "aanu", "undo"],
    "gu": ["kem", "shu", "karo", "che", "nathi", "tame"],
    "pa": ["kiwe", "ki", "karo", "hai", "nahi", "tenu", "saanu"],
}

# Minimum keyword hits to trigger Romanized detection
_ROMANIZED_MIN_HITS = 2


def _detect_romanized(text: str) -> str | None:
    """Check if a Roman-script text is likely a transliterated Indian language.

    Returns ISO code of the best match, or None.
    """
    lower = text.lower()
    tokens = set(re.findall(r"\b[a-z]+\b", lower))
    best_lang: str | None = None
    best_count = 0
    for lang, keywords in _ROMANIZED_HINTS.items():
        count = sum(1 for kw in keywords if kw in tokens)
        if count >= _ROMANIZED_MIN_HITS and count > best_count:
            best_count = count
            best_lang = lang
    return best_lang


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_language(text: str) -> str:
    """Detect the language of *text*.

    Strategy:
      1. If text is very short (< 10 chars) → default "en"
      2. Try langdetect on the full text
      3. If langdetect returns "en" but text is Roman script → check
         Romanized-hint heuristic (catches Hinglish / transliterated queries)
      4. If detected language not in SUPPORTED_LANGUAGES → fall back to "en"

    Returns ISO 639-1 code (e.g. "hi", "ta", "en").
    """
    if not text or len(text.strip()) < 10:
        return "en"

    detected = "en"
    try:
        from langdetect import detect, LangDetectException  # lazy import
        detected = detect(text)
    except Exception as e:
        # langdetect not installed or detection failed — heuristic still runs.
        # Debug-level: this happens on very short or ambiguous inputs and is normal.
        log.debug("langdetect failed; using Romanized heuristic",
                  error=str(e)[:120])

    # langdetect sometimes returns "zh-cn", "pt" etc. for short Indian texts
    # Normalise: strip region suffix ("zh-cn" → "zh"), keep only known codes
    detected = detected.split("-")[0].lower()

    if detected not in SUPPORTED_LANGUAGES:
        detected = "en"

    # If langdetect said English, double-check with Romanized heuristic
    if detected == "en":
        roman_lang = _detect_romanized(text)
        if roman_lang:
            log.debug("Romanized script override", roman_lang=roman_lang,
                      snippet=text[:60])
            detected = roman_lang

    log.debug("Language detected", lang=detected,
              lang_name=SUPPORTED_LANGUAGES.get(detected, "Unknown"),
              snippet=text[:80])
    return detected


def _format_intent_directives(intent) -> str:
    """Render a UserIntent's typed fields into a directive block for agent prompts.

    Empty string when the intent expresses no explicit preferences — that case
    must produce a no-op append so backwards-compatible behaviour is preserved
    for callers that don't pass an intent at all.

    Typed as `intent: Any` (not `UserIntent`) to avoid a config↔core import
    cycle; we duck-type on the expected attributes.

    Currently surfaces:
      - response_depth ("brief" / "detailed") — drives length
      - additional_instructions (free-text catchall)

    Format and language live in their own layers: synthesis-template picker
    handles wants_table, localize_prompt itself handles the language block.
    Adding format here would duplicate the synth layer's directive without
    benefit.
    """
    if intent is None:
        return ""
    parts: list[str] = []
    depth = getattr(intent, "response_depth", "standard")
    if depth == "brief":
        parts.append(
            "USER DEPTH: keep the response under 200 words. Prefer the most "
            "load-bearing facts. Skip background and elaboration."
        )
    elif depth == "detailed":
        parts.append(
            "USER DEPTH: comprehensive coverage. Include all relevant "
            "sub-sections, provisos, explanations, and material context."
        )
    extra = getattr(intent, "additional_instructions", "") or ""
    if extra.strip():
        # Already capped at 300 chars at extraction; safe to inject verbatim.
        parts.append(f"USER ADDITIONAL INSTRUCTIONS: {extra.strip()}")
    if not parts:
        return ""
    return "\n\n## USER DIRECTIVES (must be honoured)\n" + "\n".join(
        f"- {p}" for p in parts
    )


def localize_prompt(base_prompt: str, lang: str, intent=None) -> str:
    """Append a language instruction (and optionally a user-intent directive
    block) to any agent system prompt.

    - If lang == "en" or unsupported, no language line is appended.
    - If intent is provided AND has non-default depth or additional_instructions,
      a "USER DIRECTIVES" block is appended.

    Returning the unchanged prompt when both signals are empty preserves the
    pre-Phase-3 behaviour for callers that don't pass an intent.
    """
    out = base_prompt

    if lang != "en" and lang in SUPPORTED_LANGUAGES:
        lang_name = SUPPORTED_LANGUAGES[lang]
        out += (
            f"\n\nLANGUAGE INSTRUCTION: Respond entirely in {lang_name}. "
            "All legal citations must remain in English — this includes case names, "
            "section numbers, act titles (e.g. IPC, BNS, CrPC), court names, "
            "and party names. Do NOT translate these."
        )

    out += _format_intent_directives(intent)
    return out


@lru_cache(maxsize=None)
def language_name(lang: str) -> str:
    """Return human-readable name for an ISO code."""
    return SUPPORTED_LANGUAGES.get(lang, "English")
