"""Inline Tool: Section/provision parser for legal queries.

Pure function — no I/O, runs in-process with zero latency.
Extracts section type, number, and act name from natural language queries.
Migrated from: v1 retrievers/legislation_retriever.py (extract_section_info_from_query)
"""

import re

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

    # --- Step 2: Extract all section numbers ---
    section_numbers = []
    subsections: dict[str, str] = {}

    # Check for range pattern: "10 to 15" or "10-15"
    range_match = re.search(r'(\d+)\s+to\s+(\d+)', after_type)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        if end > start and (end - start) <= 50:  # safety limit
            section_numbers = [str(n) for n in range(start, end + 1)]
    else:
        # Check for comma/and list: "302, 307 and 420" or "302, 307, 420"
        # First capture all number+optional-subsection patterns
        num_with_sub = re.findall(
            r'(\d+[a-z]*)\s*(?:\((\w+)\))?',
            after_type
        )

        for num, sub in num_with_sub:
            if num and num not in section_numbers:
                section_numbers.append(num)
                if sub:
                    subsections[num] = sub

    # If no numbers found via list/range, fall back to basic regex
    if not section_numbers:
        basic_matches = re.findall(
            rf'({_SECTION_PATTERN})\s+{number_pattern}', query_lower
        )
        for _, num in basic_matches:
            if num not in section_numbers:
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
