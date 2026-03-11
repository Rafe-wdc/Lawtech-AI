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
    except Exception:
        pass  # langdetect not installed or failed — use heuristic only

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


def localize_prompt(base_prompt: str, lang: str) -> str:
    """Append a language instruction to any system prompt.

    If lang == "en" the prompt is returned unchanged.
    Otherwise appends:
      "Respond in <Language>. All legal citations — case names, section
       numbers, act titles, court names — must remain in English."

    This single line reliably controls Gemini / GPT-4o output language
    without altering the structure or rules of the base prompt.
    """
    if lang == "en" or lang not in SUPPORTED_LANGUAGES:
        return base_prompt

    lang_name = SUPPORTED_LANGUAGES[lang]
    instruction = (
        f"\n\nLANGUAGE INSTRUCTION: Respond entirely in {lang_name}. "
        "All legal citations must remain in English — this includes case names, "
        "section numbers, act titles (e.g. IPC, BNS, CrPC), court names, "
        "and party names. Do NOT translate these."
    )
    return base_prompt + instruction


@lru_cache(maxsize=None)
def language_name(lang: str) -> str:
    """Return human-readable name for an ISO code."""
    return SUPPORTED_LANGUAGES.get(lang, "English")
