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


def parse_section_info(query: str) -> dict | None:
    """Extract section type, number, and act name from a legal query.

    Examples:
        "Section 44 of Transfer of Property Act" → {type: "section", number: "44", act: "transfer of property act"}
        "Rule 3 of Maharashtra Rent Control Rules" → {type: "rule", number: "3", act: "maharashtra rent control rules"}

    Returns None if no section/provision pattern is found.
    """
    query_lower = query.lower().strip()

    section_pattern = '|'.join(
        re.escape(st) for st in sorted(SECTION_TYPES, key=len, reverse=True)
    )
    number_pattern = r'(\d+[a-z]*|\b[ivx]+\b|[a-z]\d*|\d+[a-z]\d*)'

    section_matches = re.findall(rf'({section_pattern})\s+{number_pattern}', query_lower)

    if not section_matches:
        return None

    section_type, section_number = section_matches[0]

    act_indicators = r'(?:act|rule|law|code|regulation|ordinance|statute|scheme|policy|manual|notification)'
    act_patterns = [
        rf'of\s+(?:the\s+)?(.+?{act_indicators}[^,]*)',
        rf'(.+?{act_indicators}[^,]*?).*?{section_type}',
        rf'([\w\s]+{act_indicators}[\w\s]*)',
    ]

    act_name = None
    for pattern in act_patterns:
        match = re.search(pattern, query_lower)
        if match:
            act_name = match.group(1).strip()
            act_name = re.sub(r'^\s*(of\s+(?:the\s+)?|the\s+)', '', act_name)
            act_name = re.sub(r'\s*,\s*\d{4}.*', '', act_name)
            break

    return {
        'section_type': section_type,
        'section_number': section_number,
        'act_name': act_name or '',
    }
