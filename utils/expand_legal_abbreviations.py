import re

# Dictionary of abbreviations
indian_legal_abbreviations = {
    "Sec": "Section",
    "IPC": "Indian Penal Code 1860",
    "CrPC": "Code of Criminal Procedure 1973",
    "CPC": "Code of Civil Procedure",
    "NI": "Negotiable Instruments Act 1881",
    "SC": "Supreme Court",
    "HC": "High Court",
    "FIR": "First Information Report",
    "IO": "Investigating Officer",
    "DGP": "Director General of Police",
    "LPA": "Letters Patent Appeal",
    "NIA": "National Investigation Agency",
    "CBI": "Central Bureau of Investigation",
    "ED": "Enforcement Directorate",
    "RTI": "Right to Information",
    "NGT": "National Green Tribunal",
    "PIL": "Public Interest Litigation",
    "UAPA": "Unlawful Activities (Prevention) Act",
    "POTA": "Prevention of Terrorism Act",
    "TADA": "Terrorist and Disruptive Activities (Prevention) Act",
    "GST": "Goods and Services Tax",
    "CGST": "Central Goods and Services Tax",
    "HMA": "Hindu Marriage Act",
    "HSA": "Hindu Succession Act",
    "MVA": "Motor Vehicles Act",
    "PC Act": "Prevention of Corruption Act",
    "NDPS": "Narcotic Drugs and Psychotropic Substances Act",
    "ADR": "Alternate Dispute Resolution",
    "JJ Act": "Juvenile Justice (Care and Protection of Children) Act",
    "DV Act": "Protection of Women from Domestic Violence Act",
    "BNSS": "Bharatiya Nagarik Suraksha Sanhita 2023",
    "BSA": "Bharatiya Sakshya Adhiniyam 2023",
    "BNS": "Bharatiya Nyaya Sanhita 2023",
    "POCSO": "Protection of Children from Sexual Offences Act 2012",
    "TOPA": "Transfer of Property Act"
}


def expand_legal_abbreviations(text):
    # Replace abbreviations in a case-insensitive way
    for abbr, full_form in indian_legal_abbreviations.items():
        # Use word boundaries to avoid partial replacements
        pattern = r'\b' + re.escape(abbr) + r'\b'
        text = re.sub(pattern, full_form, text, flags=re.IGNORECASE)
    return text