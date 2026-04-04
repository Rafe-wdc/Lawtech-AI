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

    text = _fix_dash_overflow(text)
    text = _fix_malformed_tables(text)
    text = _fix_excessive_whitespace(text)
    text = _fix_unclosed_code_blocks(text)
    text = _fix_long_lines(text)
    text = _fix_repeated_blocks(text)

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

        # Break lines >500 chars at word boundaries
        if len(line) > 500:
            words = line.split(" ")
            current = ""
            for word in words:
                if len(current) + len(word) + 1 > 120:
                    cleaned.append(current)
                    current = word
                else:
                    current = current + " " + word if current else word
            if current:
                cleaned.append(current)
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
