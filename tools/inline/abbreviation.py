"""Inline Tool: Legal abbreviation expansion.

Pure function — no I/O, runs in-process with zero latency.
Migrated from: v1 utils/expand_legal_abbreviations.py
"""

import re

LEGAL_ABBREVIATIONS = {
    "IPC": "Indian Penal Code 1860",
    "CrPC": "Code of Criminal Procedure 1973",
    "CPC": "Code of Civil Procedure 1908",
    "IEA": "Indian Evidence Act 1872",
    "BNS": "Bharatiya Nyaya Sanhita 2023",
    "BNSS": "Bharatiya Nagarik Suraksha Sanhita 2023",
    "BSA": "Bharatiya Sakshya Adhiniyam 2023",
    "TPA": "Transfer of Property Act 1882",
    "NI Act": "Negotiable Instruments Act 1881",
    "IT Act": "Information Technology Act 2000",
    "SARFAESI": "Securitisation and Reconstruction of Financial Assets and Enforcement of Securities Interest Act 2002",
    "POCSO": "Protection of Children from Sexual Offences Act 2012",
    "DV Act": "Protection of Women from Domestic Violence Act 2005",
    "RERA": "Real Estate Regulation and Development Act 2016",
    "GST": "Goods and Services Tax Act 2017",
    "PMLA": "Prevention of Money Laundering Act 2002",
    "NDPS": "Narcotic Drugs and Psychotropic Substances Act 1985",
    "SC/ST Act": "Scheduled Castes and Scheduled Tribes Prevention of Atrocities Act 1989",
    "RTI": "Right to Information Act 2005",
    "SEBI": "Securities and Exchange Board of India Act 1992",
    "NIA": "National Investigation Agency Act 2008",
    "UAPA": "Unlawful Activities Prevention Act 1967",
    "HMA": "Hindu Marriage Act 1955",
    "HSA": "Hindu Succession Act 1956",
    "MV Act": "Motor Vehicles Act 1988",
    "CPA": "Consumer Protection Act 2019",
    "RBI Act": "Reserve Bank of India Act 1934",
    "Companies Act": "Companies Act 2013",
    "Arbitration Act": "Arbitration and Conciliation Act 1996",
}


def expand_abbreviations(text: str) -> str:
    """Expand legal abbreviations in text to full act names.

    Examples:
        "Section 35 of BNS" → "Section 35 of Bharatiya Nyaya Sanhita 2023"
        "Under IPC and CrPC" → "Under Indian Penal Code 1860 and Code of Criminal Procedure 1973"
    """
    for abbr, full in LEGAL_ABBREVIATIONS.items():
        text = re.sub(
            r'\b' + re.escape(abbr) + r'\b',
            full,
            text,
            flags=re.IGNORECASE,
        )
    return text
