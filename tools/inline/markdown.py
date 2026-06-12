"""Inline Tool: Markdown sanitization.

Pure function — no I/O, runs in-process with zero latency.
Fixes common broken markdown issues in LLM output.
Migrated from: v1 utils/guardrails.py (sanitize_markdown)
"""

import re


def sanitize_markdown(content: str) -> str:
    """Fix broken markdown in LLM output for clean frontend rendering.

    Fixes:
    1. Unclosed fenced code blocks
    2. Unclosed inline backticks
    3. Unclosed bold markers (**)
    4. Headings missing space after #
    5. Excessive blank lines
    6. Mixed bullet markers → normalized to -
    7. Missing table header separator rows
    8. Trailing whitespace
    """
    if not content:
        return content

    # Skip expensive line-by-line processing for very large responses
    # (observed: 349K response blocked event loop for 14 minutes)
    if len(content) > 100000:
        return content.rstrip() + '\n'

    text = content

    # 1. Fix unclosed fenced code blocks
    fence_count = len(re.findall(r'^```', text, re.MULTILINE))
    if fence_count % 2 != 0:
        text = text.rstrip() + "\n```"

    # 2-3. Fix unclosed inline backticks and bold markers
    lines = text.split('\n')
    fixed_lines = []
    in_fence = False
    for line in lines:
        if line.strip().startswith('```'):
            in_fence = not in_fence
            fixed_lines.append(line)
            continue
        if not in_fence:
            # Fix unclosed backticks
            single_backticks = len(re.findall(r'(?<!`)`(?!`)', line))
            if single_backticks % 2 != 0:
                line = line + '`'
            # Fix unclosed bold
            bold_count = len(re.findall(r'\*\*', line))
            if bold_count % 2 != 0:
                line = line + '**'
        fixed_lines.append(line)
    text = '\n'.join(fixed_lines)

    # 4. Fix headings missing space after #
    text = re.sub(r'^(#{1,6})([^\s#])', r'\1 \2', text, flags=re.MULTILINE)

    # 5. Normalize excessive blank lines
    text = re.sub(r'\n{4,}', '\n\n\n', text)

    # 6. Normalize bullet markers
    lines = text.split('\n')
    fixed_lines = []
    in_fence = False
    for line in lines:
        if line.strip().startswith('```'):
            in_fence = not in_fence
            fixed_lines.append(line)
            continue
        if not in_fence:
            line = re.sub(r'^(\s*)[•●▪](\s)', r'\1-\2', line)
            line = re.sub(r'^(\s*)\*(\s+)', r'\1-\2', line)
        fixed_lines.append(line)
    text = '\n'.join(fixed_lines)

    # 7. Fix broken tables — insert a separator row after the HEADER row only
    # if missing. A table is a contiguous block of pipe-containing lines. The
    # header is the FIRST such line. If line 2 of the block isn't a separator
    # row, insert one. Subsequent data rows must NOT trigger insertion (earlier
    # versions of this sanitizer inserted `|---|---|---|` between every pair of
    # data rows because they always have pipes too).
    _SEP_RE = re.compile(r'^[\s|:\-]+$')
    lines = text.split('\n')
    fixed_lines = []
    in_fence = False
    prev_was_pipe = False  # was the previous emitted line part of a pipe block?
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith('```'):
            in_fence = not in_fence
            fixed_lines.append(line)
            prev_was_pipe = False
            i += 1
            continue
        is_pipe_line = (not in_fence) and ('|' in line)
        if is_pipe_line:
            is_separator = bool(stripped) and bool(_SEP_RE.match(stripped))
            cells = [c.strip() for c in line.split('|')]
            cell_count = len([c for c in cells if c])
            # A markdown table header MUST start with `|` (after stripping). This
            # rejects prose like "Either | this | or | that." which happens to
            # contain pipes but isn't a table.
            line_starts_with_pipe = stripped.startswith('|')
            # A header also requires a following pipe row (data or separator).
            # A single isolated pipe line is not a table.
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            next_is_pipe = bool(next_line) and ('|' in next_line)
            is_header_candidate = (
                (not prev_was_pipe)
                and (cell_count >= 2)
                and not is_separator
                and line_starts_with_pipe
                and next_is_pipe
            )
            fixed_lines.append(line)
            if is_header_candidate:
                already_has_sep = next_is_pipe and bool(_SEP_RE.match(next_line))
                if not already_has_sep:
                    sep = '|'.join(['---' if c else '' for c in cells])
                    fixed_lines.append(sep)
            # Track "previous was pipe" only when this line is unambiguously part
            # of a table (starts with `|`); pipe-containing prose resets the chain.
            prev_was_pipe = line_starts_with_pipe
            i += 1
            continue
        fixed_lines.append(line)
        prev_was_pipe = False
        i += 1
    text = '\n'.join(fixed_lines)

    # 8. Remove trailing whitespace
    text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)

    # 9. Ensure single trailing newline
    text = text.rstrip() + '\n'

    return text
