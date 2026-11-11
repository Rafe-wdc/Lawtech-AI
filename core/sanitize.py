"""Output Sanitization Layer — Clean all LLM output before delivery.

Single module that ALL features route through before returning content
to the user (chat response, export, compliance report, memo, TOA, etc.).

Fixes common LLM output issues:
- Malformed markdown tables (dash overflow, broken separators)
- Excessive whitespace / blank lines
- Unclosed code blocks
- Broken list formatting
- Excessively long lines without breaks
- Repeated content blocks (LLM loops)
"""

from __future__ import annotations

import re

from core.logger import get_logger

log = get_logger("Sanitize")


def sanitize_output(text: str) -> str:
    """Clean LLM output for safe rendering and export.

    This is the single entry point — apply to ALL LLM-generated content
    before returning to the user or passing to export/PDF generation.

    Args:
        text: Raw LLM output (markdown)

    Returns:
        Cleaned markdown text
    """
    if not text:
        return text

    original_len = len(text)

    # _collapse_runaway_runs FIRST -- it catches the inline-padding pathology
    # (e.g. a 124k-dash table separator row, or 10k spaces in a single cell)
    # that earlier passes don't see because they treat the whole line as
    # opaque. By collapsing those runs upfront, _fix_malformed_tables and
    # _fix_long_lines see normal-sized lines to work on.
    text = _collapse_runaway_runs(text)
    text = _fix_dash_overflow(text)
    text = _fix_malformed_tables(text)
    text = _fix_excessive_whitespace(text)
    text = _fix_unclosed_code_blocks(text)
    text = _fix_long_lines(text)
    text = _fix_repeated_blocks(text)
    text = _strip_stray_cjk(text)

    cleaned_len = len(text)
    if original_len - cleaned_len > 500:
        log.info("Output sanitized",
                 original_len=original_len,
                 cleaned_len=cleaned_len,
                 removed=original_len - cleaned_len)

    return text


# ---------------------------------------------------------------------------
# Individual sanitization passes
# ---------------------------------------------------------------------------

# Chars that LLMs commonly run away on when "aligning" markdown tables.
# Any run of >100 of these in a row is collapsed -- legitimate prose never
# contains 100 identical dashes or spaces in a row.
_RUNAWAY_CHARS = "-_= *"
_RUNAWAY_MIN_RUN = 100   # threshold: 100+ identical chars in a row
_RUNAWAY_KEEP = 8        # what we collapse the run TO (preserves visual hint)


def _collapse_runaway_runs(text: str) -> str:
    """Collapse runs of 100+ identical "padding" characters down to 8.

    Catches LLM markdown-table runaway pathology in two shapes:

      Shape A -- pure separator row:
          "| :----- | :----- <124,860 dashes> ----- |"
      Shape B -- intra-cell padding:
          "| Section 125 | Sub (1)............<6k dots>............ | ..."

    Both are signs the model got stuck "aligning" columns visually and
    streamed thousands of repeated chars before terminating. The fix is
    cheap and idempotent: any run of >100 of [-, _, =, space, *] collapses
    to 8 of the same char so the visual cue remains. Other characters
    (alphanumerics, normal prose) are never touched.

    O(N) over the input string. No regex backtracking risk -- we walk char
    by char with a simple state machine.
    """
    if not text:
        return text

    runaway_chars = set(_RUNAWAY_CHARS)
    out: list[str] = []
    i = 0
    n = len(text)
    total_collapsed = 0
    runs_fixed = 0

    while i < n:
        ch = text[i]
        if ch in runaway_chars:
            # Walk forward while the same char repeats
            j = i + 1
            while j < n and text[j] == ch:
                j += 1
            run_len = j - i
            if run_len >= _RUNAWAY_MIN_RUN:
                out.append(ch * _RUNAWAY_KEEP)
                total_collapsed += run_len - _RUNAWAY_KEEP
                runs_fixed += 1
            else:
                out.append(text[i:j])
            i = j
        else:
            out.append(ch)
            i += 1

    if runs_fixed:
        log.warning("Collapsed runaway padding runs",
                    runs_fixed=runs_fixed,
                    chars_removed=total_collapsed)
    return "".join(out)


def _fix_dash_overflow(text: str) -> str:
    """Remove consecutive dash-only lines (malformed table separators).

    LLMs sometimes generate hundreds of lines like:
    -----------------------------------------------------------
    -----------------------------------------------------------
    These break PDF/DOCX rendering into 50+ blank pages.
    """
    lines = text.split("\n")
    cleaned = []
    consecutive_dashes = 0

    for line in lines:
        stripped = line.strip()
        # Detect lines that are ONLY dashes, pipes, colons, spaces
        is_dash_line = (
            bool(stripped)
            and len(stripped) > 20
            and bool(re.match(r"^[-\s|:=_*]+$", stripped))
        )

        if is_dash_line:
            consecutive_dashes += 1
            if consecutive_dashes == 1:
                # Keep first one as a simple HR, but truncate
                cleaned.append("---")
            # Skip all subsequent consecutive dash lines
            continue

        consecutive_dashes = 0
        cleaned.append(line)

    return "\n".join(cleaned)


def _fix_malformed_tables(text: str) -> str:
    """Fix malformed markdown table separator rows.

    Catches: |:---|:---|:---...| that extends >80 chars
    Rebuilds as proper short separator.
    """
    lines = text.split("\n")
    cleaned = []

    for line in lines:
        stripped = line.strip()
        # Table separator row that's too long
        if re.match(r"^\|[\s\-:|]+\|?$", stripped) and len(stripped) > 80:
            # Count columns by splitting on |
            parts = [p for p in stripped.strip("|").split("|") if p.strip()]
            n_cols = max(min(len(parts), 10), 3)  # reasonable column count
            short_sep = "| " + " | ".join(":---" for _ in range(n_cols)) + " |"
            cleaned.append(short_sep)
        else:
            cleaned.append(line)

    return "\n".join(cleaned)


def _fix_excessive_whitespace(text: str) -> str:
    """Collapse runs of 3+ blank lines down to 2.

    LLMs sometimes output many consecutive blank lines between sections.
    """
    # Replace 3+ consecutive newlines with exactly 2
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    # Remove trailing whitespace on each line
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines)


def _fix_unclosed_code_blocks(text: str) -> str:
    """Close any unclosed code blocks (``` without matching ```)."""
    count = text.count("```")
    if count % 2 != 0:
        # Odd number of ``` — add a closing one at the end
        text = text.rstrip() + "\n```\n"
    return text


_LONG_RUN_RE = re.compile(r"\S{501,}")
_LONG_RUN_CHUNK = 120


def _split_run(m: re.Match) -> str:
    run = m.group(0)
    if "://" in run:
        return run
    return "\n".join(run[i:i + _LONG_RUN_CHUNK] for i in range(0, len(run), _LONG_RUN_CHUNK))


def _fix_long_lines(text: str) -> str:
    """Break extremely long lines (>500 chars) that have no spaces.

    These cause horizontal overflow in PDF rendering.
    Skip code blocks and URLs.
    """
    lines = text.split("\n")
    cleaned = []
    in_code = False

    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
            cleaned.append(line)
            continue

        if in_code:
            cleaned.append(line)
            continue

        # Skip URLs and table rows
        if line.strip().startswith("http") or line.strip().startswith("|"):
            cleaned.append(line)
            continue

        # Only an unbroken run (no spaces) overflows: the renderer wraps
        # prose on its own. Re-wrapping prose here used to hard-break every
        # >500-char paragraph at 120 chars, and a break that left "1971." at
        # the start of a line rendered as an empty numbered paragraph and
        # shifted every later paragraph number by one (advocate screenshot,
        # writ petition, 2026-09-21). Runs are split in place; the line's
        # indentation and every space are kept, and URLs are left whole.
        if len(line) > 500:
            cleaned.append(_LONG_RUN_RE.sub(_split_run, line))
        else:
            cleaned.append(line)

    return "\n".join(cleaned)


def _fix_repeated_blocks(text: str) -> str:
    """Detect and remove large repeated content blocks (LLM loops).

    Sometimes the LLM outputs the same section 2-3 times. If a block
    of 10+ lines repeats identically, keep only the first occurrence.
    """
    lines = text.split("\n")
    if len(lines) < 30:
        return text  # too short for meaningful dedup

    # Look for repeated blocks of 10+ lines
    block_size = 10
    seen_blocks = set()
    cleaned = []
    i = 0

    while i < len(lines):
        if i + block_size <= len(lines):
            block = "\n".join(lines[i:i + block_size])
            block_hash = hash(block)

            if block_hash in seen_blocks:
                # Skip this repeated block
                i += block_size
                continue
            seen_blocks.add(block_hash)

        cleaned.append(lines[i])
        i += 1

    if len(cleaned) < len(lines):
        log.info("Removed repeated blocks",
                 original_lines=len(lines),
                 cleaned_lines=len(cleaned))

    return "\n".join(cleaned)


# ---------------------------------------------------------------------------
# Stray CJK and other foreign-script glyphs
# ---------------------------------------------------------------------------

# Seen 2026-11-06 in a lawyer's draft: "In *Sci尊敬 / Chandra Shashi v. …*" —
# two Chinese characters dropped into an English sentence by the generation
# model. Gemini Flash models are reported to emit stray CJK tokens mid-output
# (Google AI developer forum, several threads 2025-26). Nothing retrieved for
# the request contained the glyphs (the Supreme Court corpus holds no "尊敬"
# at all; its only CJK is OCR debris in 7 pre-2013 scans) and no prompt asked
# for them, so they are a decoding glitch, not content.
#
# An Indian-law answer never legitimately mixes CJK script into otherwise
# non-CJK text, so CJK runs are removed unless the text as a whole is written
# in a CJK language (user asked in that script). Indic scripts (Devanagari,
# Bengali, Tamil, Telugu, ...) are outside these ranges and never affected.
#
# 2026-11-10: the same glitch in Thai, INSIDE a party name: an English
# complaint called a witness "Dine<Thai>hbhai Mehta" (names changed here) and
# spelt him correctly as "Dineshbhai Mehta" further down the same draft. The pass now also covers
# other scripts no Indian legal answer uses (Thai, Lao, Khmer, Myanmar,
# Cyrillic, Hebrew, Armenian, Georgian), and a run sitting inside a word is
# replaced by the missing letters when the document spells that word
# correctly elsewhere. Greek is left alone (alpha, beta, mu appear in
# technical and medical evidence); Arabic script is Urdu, an Indian language.
_STRAY_SCRIPT_CLASS = (
    "["
    "぀-ヿ"      # Hiragana, Katakana
    "㐀-䶿"      # CJK Unified Ideographs Extension A
    "一-鿿"      # CJK Unified Ideographs
    "가-힯"      # Hangul syllables
    "豈-﫿"      # CJK Compatibility Ideographs
    "Ѐ-ӿ"      # Cyrillic
    "԰-֏"      # Armenian
    "֐-׿"      # Hebrew
    "฀-๿"      # Thai
    "຀-໿"      # Lao
    "က-႟"      # Myanmar
    "Ⴀ-ჿ"      # Georgian
    "ក-៿"      # Khmer
    "]"
)
_CJK_RUN_RE = re.compile(f"( ?){_STRAY_SCRIPT_CLASS}+( ?)")
_ANY_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
# Below this share of all letters the text is NOT written in that script, so
# any of it -- two glyphs or a whole sentence that flipped language -- is
# the glitch. A real Chinese / Thai / Russian answer is almost entirely in
# its own script and sits far above it.
_CJK_LANGUAGE_SHARE = 0.5
# A glitch inside a word swallowed at most this many of its letters.
_IN_WORD_MAX_MISSING = 3


def _strip_stray_cjk(text: str) -> str:
    """Remove foreign-script glyphs a response cannot have meant to contain.

    A run between two parts of one word ("Dine" + run + "hbhai") is replaced by
    the letters that word is missing when exactly one word elsewhere in the
    text fits (prefix + 1-3 letters + suffix); otherwise the run is dropped.
    """
    matches = list(_CJK_RUN_RE.finditer(text))
    if not matches:
        return text
    glyphs = sum(len(m.group(0).strip(" ")) for m in matches)
    letters = len(_ANY_LETTER_RE.findall(text))
    if letters and glyphs / letters > _CJK_LANGUAGE_SHARE:
        return text  # genuinely written in that script, not a glitch

    vocabulary = set(re.findall(r"[A-Za-z]{3,}", _CJK_RUN_RE.sub(" \x00 ", text)))
    repaired: list[str] = []

    def _repl(m: re.Match) -> str:
        before, after = m.group(1), m.group(2)
        if not before and not after:
            # Only the letters touching the run matter; slicing the whole
            # text per match would make long answers quadratic.
            left = re.search(r"[A-Za-z]+$", text[max(0, m.start() - 40):m.start()])
            right = re.match(r"[A-Za-z]+", text[m.end():m.end() + 40])
            if left and right:
                lw, rw = left.group(0), right.group(0)
                fits = {w for w in vocabulary
                        if w.startswith(lw) and w.endswith(rw)
                        and 0 < len(w) - len(lw) - len(rw) <= _IN_WORD_MAX_MISSING}
                if len(fits) == 1:
                    word = fits.pop()
                    repaired.append(word)
                    return word[len(lw):len(word) - len(rw)]
        # "Sci<glyphs> /" -> "Sci /", "word <glyphs> word" -> "word word"
        return " " if (before and after) else (before or after)

    cleaned = _CJK_RUN_RE.sub(_repl, text)
    log.warning("Stray foreign-script glyphs removed",
                runs=len(matches), glyphs=glyphs,
                sample=matches[0].group(0).strip(" ")[:12],
                words_repaired=repaired[:5])
    return cleaned
