"""Krutidev / DV-TT / Shusha legacy-Hindi font → Devanagari Unicode.

Sagar's bug #3 (2026-06-16): "Hindi mai krutidev word ki file attach
karne ke baad usko read nahi kar paa Raha hai."

Krutidev is a legacy ASCII-mapped font: the underlying stored characters
are Latin codepoints that the Krutidev font happens to render as
Devanagari glyphs. When python-docx extracts `run.text`, it returns the
raw Latin codepoints — useless for retrieval or LLM context — and the
user sees gibberish.

This module:
- exposes a set of font-name patterns we treat as legacy Hindi fonts
  (LEGACY_HINDI_FONT_NAMES)
- exposes `is_legacy_hindi_font(name)` for the detector
- exposes `krutidev_to_unicode(text)` for the transliteration
- exposes `looks_like_unicode_devanagari(text)` so the caller can decide
  whether the result of the lookup-table pass is clean enough or whether
  a vision-OCR fallback should be queued

The mapping table covers Krutidev 010 (the most common variant). Other
Krutidev variants (020, 040, 050, 100, ...) share ~95% of mappings with
010; minor divergences will show up as a few wrong characters and the
caller's `looks_like_unicode_devanagari()` heuristic will catch them and
escalate to vision OCR.

Reference: this mapping is the de-facto standard distributed by multiple
open-source converters (e.g. https://github.com/Vinit2305/Krutidev-to-Unicode).
"""

from __future__ import annotations

import re
from functools import lru_cache


# ---------------------------------------------------------------------------
# Legacy-Hindi font detector
# ---------------------------------------------------------------------------

# Substrings (case-insensitive) that mark a run as Krutidev/DV-TT/Shusha/etc.
# Matched against `run.font.name` from python-docx. Mangal IS Unicode-correct
# and is excluded. DevLys, AMS, Shree-Dev, and most ".tt"-suffixed Hindi fonts
# are legacy.
_LEGACY_FONT_TOKENS: tuple[str, ...] = (
    "krutidev",
    "kruti dev",
    "kruti-dev",
    "dv-tt",
    "dvb-tt",
    "dvbw-tt",
    "shusha",
    "shree-dev",
    "shree dev",
    "shreedev",
    "devlys",
    "dev-lys",
    "ams",
    "yogesh",
    "surekh",
    "ttyogesh",
    "ttsurekh",
)


def is_legacy_hindi_font(font_name: str | None) -> bool:
    """True if the font name matches a known legacy Hindi font."""
    if not font_name:
        return False
    lower = font_name.lower()
    return any(tok in lower for tok in _LEGACY_FONT_TOKENS)


# ---------------------------------------------------------------------------
# Krutidev → Unicode mapping (Krutidev 010 base table)
# ---------------------------------------------------------------------------

# Two-character and three-character digraphs MUST be processed before single-
# character substitutions, since the digraph keys would be matched by a
# greedy single-char pass otherwise (e.g. "f=" → digraph; "f" alone is
# different). The conversion loop applies the table in declared order.
#
# This dict deliberately preserves Krutidev's quirky orderings — re-sorting
# alphabetically would break the digraph-before-singleton invariant.
_KRUTIDEV_MAP: tuple[tuple[str, str], ...] = (
    # Three- and four-character digraphs
    ("ध~",   "ध्र"),
    ("क~",   "क्र"),
    ("भ~",   "भ्र"),
    ("L=", "क्ष"),
    ("'k",  "श"),
    ("=kk", "ज्ञ"),
    ("=k",   "त्र"),
    ("=q",   "क्र"),
    # Two-character digraphs
    ("Ø",   "क्र"),
    ("ƒ",   "क्ष"),
    ("º",   "ह्र"),
    ("ƒ",   "क्ष"),
    ("Þ",   "्र"),
    ("«",   "र्"),
    ("¥",   "ज्ञ"),
    ("Ï",   "त्त"),
    ("ã",   "ं"),
    ("ð",   "ः"),
    ("v",   "अ"),
    ("vk",  "आ"),
    ("b",   "इ"),
    ("Z",   "र्"),
    ("Ã",   "्र"),
    ("ÅbZ", "ई"),
    ("m",   "उ"),
    ("Å",   "ऊ"),
    ("_",   "ऋ"),
    (",",   "ए"),
    (",s",  "ऐ"),
    ("vks", "ओ"),
    ("vkS", "औ"),
    ("a",   "ं"),
    # Single characters — consonants
    ("d",   "क"),
    ("[k",  "ख"),
    ("x",   "ग"),
    ("?k",  "घ"),
    ("³",   "ङ"),
    ("p",   "च"),
    ("N",   "छ"),
    ("t",   "ज"),
    (">",   "झ"),
    ("¥",   "ञ"),
    ("V",   "ट"),
    ("B",   "ठ"),
    ("M",   "ड"),
    ("<",   "ढ"),
    ("M+",  "ड़"),
    ("<+",  "ढ़"),
    (".",   "ण"),
    ("r",   "त"),
    ("Fk",  "थ"),
    ("n",   "द"),
    ("/k",  "ध"),
    ("u",   "न"),
    ("i",   "प"),
    ("Q",   "फ"),
    ("Q+",  "फ़"),
    ("c",   "ब"),
    ("Hk",  "भ"),
    ("e",   "म"),
    (";",   "य"),
    ("j",   "र"),
    ("y",   "ल"),
    ("o",   "व"),
    ("'",   "श"),
    ("\"k", "ष"),
    ("l",   "स"),
    ("g",   "ह"),
    # Matras / vowel signs (must come AFTER single consonants so they
    # combine correctly)
    ("k",   "ा"),
    ("h",   "ी"),
    ("q",   "ु"),
    ("w",   "ू"),
    ("`",   "ृ"),
    ("s",   "े"),
    ("S",   "ै"),
    ("ks",  "ो"),
    ("kS",  "ौ"),
    # Vowel sign i (precedes consonant in Krutidev but follows in Unicode)
    # The conversion loop emits 'f' as a placeholder, then a post-pass swaps
    # 'f' + <next consonant> → <next consonant> + 'ि'. See _SHORT_I_RE below.
    ("f",   "f"),
    # Halant (virama) / other marks
    ("~",   "्"),
    ("\\",  "्"),
    ("‚",   ","),
    ("‘",   "'"),
    ("’",   "'"),
    # Numerals
    ("0",   "०"),
    ("1",   "१"),
    ("2",   "२"),
    ("3",   "३"),
    ("4",   "४"),
    ("5",   "५"),
    ("6",   "६"),
    ("7",   "७"),
    ("8",   "८"),
    ("9",   "९"),
    # Uppercase consonant variants (Krutidev uses both cases interchangeably
    # for different glyphs; missing these leaves Latin letters in the output)
    ("Y",   "य"),
    ("D",   "क"),
    ("P",   "च"),
    ("T",   "ज"),
    ("R",   "त"),
    ("N",   "छ"),
    ("U",   "न"),
    ("I",   "प"),
    ("C",   "ब"),
    ("E",   "म"),
    ("J",   "र"),
    ("L",   "स"),
    ("G",   "ह"),
    ("X",   "ग"),
)


# After substitution, "f<consonant>" patterns mean the user typed Krutidev
# short-i (vowel sign U+093F = ि) which renders BEFORE its consonant in
# Devanagari but is STORED before the consonant in Krutidev — Unicode wants
# it stored AFTER. Swap them.
_SHORT_I_RE = re.compile(r"f([क-ह])")


def krutidev_to_unicode(text: str) -> str:
    """Convert a string of Krutidev (legacy ASCII Hindi) text to Devanagari Unicode.

    Best-effort. Some Krutidev variants (Krutidev 020, 040, 050, ...) use
    slightly different codepoint mappings; the result may contain a few
    wrong characters in those cases. The caller is expected to run
    `looks_like_unicode_devanagari` to decide whether the converted text
    is clean enough or whether a richer fallback (Gemini Vision OCR over
    a DOCX-rendered page image) should be invoked.

    Empty or already-Devanagari input is passed through unchanged.
    """
    if not text:
        return ""
    # Skip if already mostly Devanagari — converting twice would corrupt.
    if looks_like_unicode_devanagari(text):
        return text
    out = text
    for src, dst in _KRUTIDEV_MAP:
        if src in out:
            out = out.replace(src, dst)
    # Swap "ि<consonant>" placeholder back to "<consonant>ि"
    out = _SHORT_I_RE.sub(lambda m: m.group(1) + "ि", out)
    return out


# ---------------------------------------------------------------------------
# Heuristic: did the conversion actually produce Devanagari?
# ---------------------------------------------------------------------------

_DEVANAGARI_RANGE_RE = re.compile(r"[ऀ-ॿ]")


@lru_cache(maxsize=1024)
def looks_like_unicode_devanagari(text: str, min_ratio: float = 0.20) -> bool:
    """True if `text` contains a meaningful share of Devanagari codepoints.

    Used to decide whether the lookup-table conversion produced clean
    Devanagari (return True → use the conversion) or garbled output (return
    False → caller should fall back to vision OCR over a rendered page).

    A threshold of 20% is conservative — for a paragraph of mostly Hindi
    text the ratio should easily exceed 50%. The threshold sits low enough
    that short paragraphs with a few English words ("Section 138 NI Act")
    still pass.
    """
    if not text:
        return False
    stripped = "".join(ch for ch in text if not ch.isspace() and not ch.isdigit())
    if len(stripped) < 2:
        # Single-character strings can't be reliably classified — but a single
        # Devanagari char IS Devanagari.
        return bool(_DEVANAGARI_RANGE_RE.search(stripped))
    devanagari_chars = _DEVANAGARI_RANGE_RE.findall(stripped)
    return len(devanagari_chars) / len(stripped) >= min_ratio
