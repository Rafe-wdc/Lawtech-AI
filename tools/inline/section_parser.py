"""Inline Tool: Section/provision parser for legal queries.

Pure function — no I/O, runs in-process with zero latency.
Extracts section type, number, and act name from natural language queries.
Migrated from: v1 retrievers/legislation_retriever.py (extract_section_info_from_query)
"""

import re

# Year-like 4-digit numbers (1800-2099). The parser used to read "Section 9 of
# the Code of Civil Procedure 1908" and extract sections=['9', '1908'], which
# caused ES to retrieve docs about "Section 1908" (nonexistent) and dragged
# in unrelated Section 7 docs that cross-reference Section 9. Filter these
# out as a defensive layer even after the "of <act>" cut-off below.
_YEAR_RE = re.compile(r"^(?:1[89]\d{2}|20\d{2})$")

# When extracting section numbers from a query, stop scanning at the first
# act-name indicator ("of", "in", "under") -- numbers AFTER that are almost
# always part of the act name (year of enactment) or unrelated context, not
# additional section numbers.
_SECTION_LIST_BOUNDARY_RE = re.compile(r"\b(?:of|in|under)\b", flags=re.IGNORECASE)

SECTION_TYPES = [
    'section', 'rule', 'rules', 'order', 'regulation', 'scheme', 'procedure',
    'policy', 'article', 'condition', 'statute', 'ordinance', 'standing',
    'rule and regulation', 'bye-law', 'bye-laws', 'bye law', 'bye laws',
    'by-law', 'plan', 'niyam', 'adhiniyam', 'code', 'notification',
    'instruction', 'manual', 'licence', 'roster', 'process', 'tariff',
    'regulatory', 'schedule', 'function', 'byelaw', 'byelaws', 'direction',
    'guideline', 'criteria', 'law', 'clause', 'board standing order',
]

# Regex pattern for section types (longest first to avoid partial matches)
_SECTION_PATTERN = '|'.join(
    re.escape(st) for st in sorted(SECTION_TYPES, key=len, reverse=True)
)

# Act name indicators for extraction
_ACT_INDICATORS = r'(?:act|rule|law|code|regulation|ordinance|statute|scheme|policy|manual|notification)'


def _extract_act_name(query_lower: str, section_type: str) -> str:
    """Extract act/law name from a lowercased query string."""
    act_patterns = [
        rf'of\s+(?:the\s+)?(.+?{_ACT_INDICATORS}[^,]*)',
        rf'(.+?{_ACT_INDICATORS}[^,]*?).*?{re.escape(section_type)}',
        rf'([\w\s]+{_ACT_INDICATORS}[\w\s]*)',
    ]

    for pattern in act_patterns:
        match = re.search(pattern, query_lower)
        if match:
            act_name = match.group(1).strip()
            act_name = re.sub(r'^\s*(of\s+(?:the\s+)?|the\s+)', '', act_name)
            act_name = re.sub(r'\s*,\s*\d{4}.*', '', act_name)
            return act_name
    return ''


def parse_section_info(query: str) -> dict | None:
    """Extract section type, number, and act name from a legal query.

    Examples:
        "Section 44 of Transfer of Property Act" → {type: "section", number: "44", act: "transfer of property act"}
        "Rule 3 of Maharashtra Rent Control Rules" → {type: "rule", number: "3", act: "maharashtra rent control rules"}

    Returns None if no section/provision pattern is found.
    """
    query_lower = query.lower().strip()
    number_pattern = r'(\d+[a-z]*|\b[ivx]+\b|[a-z]\d*|\d+[a-z]\d*)'

    section_matches = re.findall(rf'({_SECTION_PATTERN})\s+{number_pattern}', query_lower)

    if not section_matches:
        return None

    section_type, section_number = section_matches[0]
    act_name = _extract_act_name(query_lower, section_type)

    return {
        'section_type': section_type,
        'section_number': section_number,
        'act_name': act_name,
    }


def parse_multi_section_info(query: str) -> dict | None:
    """Extract section type, multiple section numbers, subsections, and act name.

    Handles:
    - Single sections: "Section 44 of TPA" → numbers: ["44"]
    - Multiple sections: "Sections 44 and 45 of TPA" → numbers: ["44", "45"]
    - Comma-separated: "Sections 302, 307 and 420 of IPC" → numbers: ["302", "307", "420"]
    - Ranges: "Sections 10 to 15 of Companies Act" → numbers: ["10", "11", "12", "13", "14", "15"]
    - Subsections: "Section 302(1) of BNS" → numbers: ["302"], subsections: {"302": "1"}

    Returns None if no section/provision pattern is found.
    Returns dict with keys: section_type, section_numbers, subsections, act_name
    """
    query_lower = query.lower().strip()
    number_pattern = r'(\d+[a-z]*|\b[ivx]+\b|[a-z]\d*|\d+[a-z]\d*)'

    # --- Step 1: Find section type keyword ---
    type_match = re.search(rf'({_SECTION_PATTERN})s?\s', query_lower)
    if not type_match:
        return None

    section_type = type_match.group(1)
    # Get the text after the section type keyword for number extraction
    after_type = query_lower[type_match.end() - 1:]  # include trailing space

    # Truncate `after_type` at the first "of/in/under" boundary so that the
    # act name (and its year) cannot leak into the section-number scan. This
    # is the primary fix for the "1908 parsed as Section 1908" bug. We still
    # apply a defensive year filter below as a safety net.
    boundary = _SECTION_LIST_BOUNDARY_RE.search(after_type)
    scan_zone = after_type[:boundary.start()] if boundary else after_type

    # --- Step 2: Extract all section numbers ---
    section_numbers = []
    subsections: dict[str, str] = {}

    # Check for range pattern: "10 to 15" or "10-15"
    range_match = re.search(r'(\d+)\s+to\s+(\d+)', scan_zone)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        # Hard cap at 20 — prevents DoS via "Sections 1 to 10000" expanding
        # into thousands of ES terms-filter values.  Callers that receive
        # exactly 20 numbers should treat it as a capped range and switch to
        # a topic/overview search strategy.
        _RANGE_CAP = 20
        if end > start:
            section_numbers = [str(n) for n in range(start, min(end, start + _RANGE_CAP - 1) + 1)]
    else:
        # Check for comma/and list: "302, 307 and 420" or "302, 307, 420"
        # First capture all number+optional-subsection patterns
        num_with_sub = re.findall(
            r'(\d+[a-z]*)\s*(?:\((\w+)\))?',
            scan_zone,
        )

        for num, sub in num_with_sub:
            # Defensive: reject 4-digit years even if the boundary cut-off
            # somehow let them through (e.g. weird query phrasing without
            # an "of/in/under").
            if num and num not in section_numbers and not _YEAR_RE.match(num):
                section_numbers.append(num)
                if sub:
                    subsections[num] = sub

    # If no numbers found via list/range, fall back to basic regex over the
    # FULL query (not scan_zone) to maintain backward compat. Year filter
    # still applies.
    if not section_numbers:
        basic_matches = re.findall(
            rf'({_SECTION_PATTERN})\s+{number_pattern}', query_lower
        )
        for _, num in basic_matches:
            if num not in section_numbers and not _YEAR_RE.match(num):
                section_numbers.append(num)

    if not section_numbers:
        return None

    # --- Step 3: Extract act name ---
    act_name = _extract_act_name(query_lower, section_type)

    return {
        'section_type': section_type,
        'section_numbers': section_numbers,
        'subsections': subsections,
        'act_name': act_name,
    }
