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

    # 7. Fix broken tables
    lines = text.split('\n')
    fixed_lines = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith('```'):
            in_fence = not in_fence
            fixed_lines.append(line)
            i += 1
            continue
        if not in_fence and '|' in line:
            cells = [c.strip() for c in line.split('|')]
            cell_count = len([c for c in cells if c])
            if cell_count >= 2:
                fixed_lines.append(line)
                next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
                if next_line and '|' in next_line and re.match(r'^[\s|:\-]+$', next_line):
                    pass
                elif next_line and '|' in next_line:
                    sep = '|'.join(['---' if c else '' for c in cells])
                    fixed_lines.append(sep)
                i += 1
                continue
        fixed_lines.append(line)
        i += 1
    text = '\n'.join(fixed_lines)

    # 8. Remove trailing whitespace
    text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)

    # 9. Ensure single trailing newline
    text = text.rstrip() + '\n'

    return text
