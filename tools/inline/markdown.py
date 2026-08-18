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

    # 7. Fix broken tables.
    #
    # A markdown table is a header row starting with `|`, followed by a
    # separator row (`|---|---|`), followed by data rows. This step
    # normalizes three LLM-emitted breakages that all show up in the
    # same responses:
    #
    #   (a) MISSING separator after the header row (existing case).
    #   (b) BARE-RULE separator after the header row: LLM emits a plain
    #       `---` (horizontal rule) instead of a proper pipe separator.
    #       Renderer sees an <hr>, then the first data row becomes the
    #       de-facto header, so the real headers are lost.
    #   (c) LITERAL NEWLINES inside cell content: numbered lists inside
    #       cells break the row across lines. Renderer terminates the
    #       table at the first non-`|` line.
    #
    # Also drops REDUNDANT extra separators inside the body of the same
    # table block, which can appear after (b) is fixed (LLM sometimes
    # emits BOTH a bare rule after the header AND a proper separator
    # after the first data row — after we promote the bare rule, the
    # second separator becomes redundant).
    _SEP_RE = re.compile(r'^[\s|:\-]+$')          # any pipe-form separator
    _BARE_RULE_RE = re.compile(r'^[\s\-=_]+$')    # plain horizontal rule (no pipes)

    def _is_sep_row(stripped: str) -> bool:
        return bool(stripped) and '|' in stripped and bool(_SEP_RE.match(stripped))

    def _is_bare_rule(stripped: str) -> bool:
        # e.g. "---", "----", "====", "___" — no pipes, only rule chars
        if not stripped or '|' in stripped:
            return False
        if not _BARE_RULE_RE.match(stripped):
            return False
        return len(re.findall(r'[-=_]', stripped)) >= 3

    def _collect_row(lines: list, start: int) -> tuple:
        """Return (merged_row, next_index).

        Starting at a pipe-row line, absorb continuation lines that don't
        start with `|` (numbered items, prose spilled into cell) until
        either the row's stripped form ends with `|` (properly closed)
        or we hit a blank line / new pipe row / EOF. Continuation lines
        join via ``<br>`` so the frontend renderer preserves the visual
        line break inside the cell.
        """
        row = lines[start]
        j = start + 1
        while j < len(lines) and not row.rstrip().endswith('|'):
            nxt = lines[j]
            nxt_stripped = nxt.strip()
            if not nxt_stripped:
                break  # blank line ends the row
            if nxt_stripped.startswith('|'):
                break  # a new row starts
            if nxt_stripped.startswith('```'):
                break  # code fence
            # fold this continuation line into the row via <br>
            row = row.rstrip() + '<br>' + nxt_stripped
            j += 1
        return row, j

    lines = text.split('\n')
    fixed_lines: list = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith('```'):
            in_fence = not in_fence
            fixed_lines.append(line)
            i += 1
            continue

        if in_fence:
            fixed_lines.append(line)
            i += 1
            continue

        # Is this the START of a table? Must begin with `|`, have >=2
        # cells, NOT itself be a separator, and be followed by another
        # pipe/rule/bare-rule line (proves it isn't isolated prose that
        # happens to contain a `|`).
        line_starts_pipe = stripped.startswith('|')
        cells_count = len([c for c in stripped.split('|') if c.strip()])
        is_separator_line = _is_sep_row(stripped)
        next_stripped = lines[i + 1].strip() if i + 1 < len(lines) else ""
        next_looks_tabley = (
            next_stripped.startswith('|')
            or _is_bare_rule(next_stripped)
        )

        is_header_candidate = (
            line_starts_pipe
            and cells_count >= 2
            and not is_separator_line
            and next_looks_tabley
        )

        if not is_header_candidate:
            fixed_lines.append(line)
            i += 1
            continue

        # ---- Table block starts here ----
        header_line, i = _collect_row(lines, i)
        fixed_lines.append(header_line)
        # Rebuild cells from the (possibly folded) header so the synthetic
        # separator, if we need to insert one, matches the real cell count.
        header_cells = [
            c for c in header_line.strip().split('|')
            if c.strip()
        ]
        synthetic_sep = '|' + '|'.join(['---'] * len(header_cells)) + '|'

        # Consume the next line — decide whether to keep, replace, or
        # synthesize a separator.
        if i < len(lines):
            after_stripped = lines[i].strip()
            if _is_sep_row(after_stripped):
                # Proper `|---|---|` separator already present — keep it.
                fixed_lines.append(lines[i])
                i += 1
            elif _is_bare_rule(after_stripped):
                # Bare `---` HR — promote to a proper separator.
                fixed_lines.append(synthetic_sep)
                i += 1
            else:
                # Data row follows immediately — inject a separator.
                fixed_lines.append(synthetic_sep)
        else:
            fixed_lines.append(synthetic_sep)

        # ---- Process data rows inside this table block ----
        while i < len(lines):
            d_line = lines[i]
            d_stripped = d_line.strip()
            if not d_stripped:
                fixed_lines.append(d_line)
                i += 1
                break
            if d_stripped.startswith('```'):
                break
            if not d_stripped.startswith('|'):
                break
            if _is_sep_row(d_stripped):
                # Redundant separator inside table body (common after we
                # promoted a bare rule earlier) — drop it silently.
                i += 1
                continue
            row_line, i = _collect_row(lines, i)
            fixed_lines.append(row_line)

    text = '\n'.join(fixed_lines)

    # 8. Remove trailing whitespace
    text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)

    # 9. Ensure single trailing newline
    text = text.rstrip() + '\n'

    return text
