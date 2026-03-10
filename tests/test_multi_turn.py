"""Multi-turn conversation test suite -- 200 total test sequences.

Tests conversation continuity, context preservation, query rewriting,
and agent routing across 1, 2, 3, and 4-turn conversations.

Structure:
    - 50 x 1-turn prompts  (baseline single-shot quality)
    - 50 x 2-turn sequences (follow-up handling)
    - 50 x 3-turn sequences (deep conversation)
    - 50 x 4-turn sequences (extended context)

Usage:
    python tests/test_multi_turn.py [--concurrency N] [--timeout N] [--api-url URL]
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLE-TURN PROMPTS (50)
# ═══════════════════════════════════════════════════════════════════════════════

SINGLE_TURN_PROMPTS = [
    # Newacts (10)
    (1,   "Section 302 of BNS",                         "Newacts",      "Newacts"),
    (2,   "Section 376 IPC punishment",                  "Newacts",      "Newacts"),
    (3,   "bail provisions under BNSS",                  "Newacts",      "Newacts"),
    (4,   "What is the equivalent of Section 420 IPC in BNS?", "Newacts", "Newacts"),
    (5,   "Section 65B of Indian Evidence Act",          "Newacts",      "Newacts"),
    (6,   "Sections 299 and 300 of IPC difference",      "Newacts",      "Newacts"),
    (7,   "Section 41 BNSS arrest without warrant",      "Newacts",      "Newacts"),
    (8,   "punishment for kidnapping under BNS",         "Newacts",      "Newacts"),
    (9,   "Section 3(5) Bharatiya Nyaya Sanhita",        "Newacts",      "Newacts"),
    (10,  "electronic evidence under BSA",               "Newacts",      "Newacts"),

    # Legislation (10)
    (11,  "Section 138 NI Act cheque dishonour",         "Legislation",  "Legislation"),
    (12,  "Section 9 of Arbitration and Conciliation Act", "Legislation", "Legislation"),
    (13,  "Section 34 of Specific Relief Act",           "Legislation",  "Legislation"),
    (14,  "Section 24 of Hindu Marriage Act",            "Legislation",  "Legislation"),
    (15,  "Section 11 of POCSO Act",                     "Legislation",  "Legislation"),
    (16,  "RERA Section 18 delayed possession",          "Legislation",  "Legislation"),
    (17,  "Section 125 of Code of Civil Procedure",      "Legislation",  "Legislation"),
    (18,  "Section 3 Domestic Violence Act",             "Legislation",  "Legislation"),
    (19,  "Section 80 CPC mandatory notice",             "Legislation",  "Legislation"),
    (20,  "director duties under Companies Act 2013",    "Legislation",  "Legislation"),

    # Judgment (8)
    (21,  "cases on anticipatory bail conditions",       "Judgment",     "Judgment"),
    (22,  "dowry death case law in India",               "Judgment",     "Judgment"),
    (23,  "Bombay High Court rent dispute cases",        "Judgment",     "Judgment"),
    (24,  "property partition suit judgments",            "Judgment",     "Judgment"),
    (25,  "medical negligence court cases India",        "Judgment",     "Judgment"),
    (26,  "Delhi High Court on injunction in property matters", "Judgment", "Judgment"),
    (27,  "cases on quashing of FIR under Section 482",  "Judgment",     "Judgment"),
    (28,  "cyber crime defamation case law",             "Judgment",     "Judgment"),

    # SCI (5)
    (29,  "Supreme Court on right to privacy Puttaswamy", "SCI",         "SCI_Judgment"),
    (30,  "Kesavananda Bharati basic structure",         "SCI",          "SCI_Judgment"),
    (31,  "SC judgment on triple talaq",                 "SCI",          "SCI_Judgment"),
    (32,  "Supreme Court on environmental protection",   "SCI",          "SCI_Judgment"),
    (33,  "Maneka Gandhi vs Union of India",             "SCI",          "SCI_Judgment"),

    # Constitution (5)
    (34,  "Article 21 right to life and personal liberty", "Constitution", "Constitution"),
    (35,  "Fundamental duties under Article 51A",        "Constitution",  "Constitution"),
    (36,  "Article 32 writ jurisdiction",                "Constitution",  "Constitution"),
    (37,  "Directive principles Part IV",                "Constitution",  "Constitution"),
    (38,  "Article 19(1)(a) freedom of speech",          "Constitution",  "Constitution"),

    # Maxim (4)
    (39,  "Explain audi alteram partem",                 "Maxim",        "Maxim"),
    (40,  "doctrine of res judicata",                    "Maxim",        "Maxim"),
    (41,  "meaning of caveat emptor",                    "Maxim",        "Maxim"),
    (42,  "nemo judex in causa sua principle",           "Maxim",        "Maxim"),

    # Scenario (5)
    (43,  "My landlord won't return security deposit. What legal steps?", "Scenario", "Scenario"),
    (44,  "I was wrongfully terminated without notice. What are my rights?", "Scenario", "Scenario"),
    (45,  "How to file consumer complaint online in India?", "Scenario",  "Scenario"),
    (46,  "Someone filed false FIR against me. What can I do?", "Scenario", "Scenario"),
    (47,  "My neighbour encroached on my land. Legal remedies?", "Scenario", "Scenario"),

    # Multi-Agent (3)
    (48,  "Article 21 with landmark Supreme Court judgments", "Multi", ["Constitution", "Judgment", "SCI_Judgment"]),
    (49,  "Section 302 IPC old vs new BNS with SC judgments", "Multi", ["Newacts", "SCI_Judgment", "Judgment"]),
    (50,  "Section 138 NI Act with important case laws", "Multi", ["Legislation", "Judgment"]),
]


# ═══════════════════════════════════════════════════════════════════════════════
#  2-TURN SEQUENCES (50)
# ═══════════════════════════════════════════════════════════════════════════════

TWO_TURN_SEQUENCES = [
    # Newacts follow-ups (10)
    {"id": 101, "name": "IPC to BNS mapping", "category": "Newacts-2T", "turns": [
        "Section 302 of IPC",
        "What is its BNS equivalent?"
    ]},
    {"id": 102, "name": "CrPC bail detail", "category": "Newacts-2T", "turns": [
        "Section 438 of CrPC anticipatory bail",
        "What conditions can the court impose while granting anticipatory bail?"
    ]},
    {"id": 103, "name": "BNS punishment follow", "category": "Newacts-2T", "turns": [
        "Section 376 BNS",
        "What is the minimum punishment for this offence?"
    ]},
    {"id": 104, "name": "BNSS arrest detail", "category": "Newacts-2T", "turns": [
        "Section 41 of BNSS",
        "When can the police arrest without a warrant under this section?"
    ]},
    {"id": 105, "name": "BSA evidence follow", "category": "Newacts-2T", "turns": [
        "electronic evidence rules in BSA",
        "What is the procedure to certify electronic evidence?"
    ]},
    {"id": 106, "name": "IPC cheating detail", "category": "Newacts-2T", "turns": [
        "Section 420 IPC cheating and dishonestly inducing delivery of property",
        "What is the difference between cheating under 415 and 420?"
    ]},
    {"id": 107, "name": "BNS murder vs culpable", "category": "Newacts-2T", "turns": [
        "difference between Section 299 and 300 IPC",
        "Can you give examples of each?"
    ]},
    {"id": 108, "name": "BNSS FIR process", "category": "Newacts-2T", "turns": [
        "How to file an FIR under BNSS?",
        "What if the police refuses to register my FIR?"
    ]},
    {"id": 109, "name": "BNS theft section", "category": "Newacts-2T", "turns": [
        "Section 303 BNS theft",
        "What is the punishment and is it bailable or non-bailable?"
    ]},
    {"id": 110, "name": "Old new mapping series", "category": "Newacts-2T", "turns": [
        "What is the equivalent of Section 498A IPC in BNS?",
        "What are the recent amendments to this section?"
    ]},

    # Legislation follow-ups (10)
    {"id": 111, "name": "NI Act deep dive", "category": "Legislation-2T", "turns": [
        "Section 138 of Negotiable Instruments Act",
        "What are the defences available to the accused?"
    ]},
    {"id": 112, "name": "Arbitration interim", "category": "Legislation-2T", "turns": [
        "Section 9 of Arbitration Act interim measures",
        "Can the court grant interim relief after the arbitral tribunal is constituted?"
    ]},
    {"id": 113, "name": "RERA builder delay", "category": "Legislation-2T", "turns": [
        "RERA Section 18 remedy for delayed possession",
        "What interest rate is applicable under RERA for delayed projects?"
    ]},
    {"id": 114, "name": "Hindu marriage alimony", "category": "Legislation-2T", "turns": [
        "Section 24 of Hindu Marriage Act",
        "How is the amount of maintenance calculated?"
    ]},
    {"id": 115, "name": "DV Act scope", "category": "Legislation-2T", "turns": [
        "What is domestic violence under Section 3 of DV Act?",
        "Can a husband file complaint under DV Act?"
    ]},
    {"id": 116, "name": "Consumer Protection", "category": "Legislation-2T", "turns": [
        "Explain the Consumer Protection Act 2019",
        "What is the pecuniary jurisdiction of District, State and National Commission?"
    ]},
    {"id": 117, "name": "Contract Act void", "category": "Legislation-2T", "turns": [
        "Section 23 of Indian Contract Act",
        "Give examples of agreements that are void for being opposed to public policy"
    ]},
    {"id": 118, "name": "Companies Act director", "category": "Legislation-2T", "turns": [
        "duties of directors under Companies Act 2013",
        "What happens if a director violates fiduciary duty?"
    ]},
    {"id": 119, "name": "POCSO mandatory", "category": "Legislation-2T", "turns": [
        "Section 19 of POCSO Act mandatory reporting",
        "What is the punishment for failure to report?"
    ]},
    {"id": 120, "name": "CPC notice", "category": "Legislation-2T", "turns": [
        "Section 80 CPC notice before suit",
        "What is the effect of filing a suit without serving notice under Section 80?"
    ]},

    # Judgment follow-ups (8)
    {"id": 121, "name": "Bail conditions recent", "category": "Judgment-2T", "turns": [
        "cases on anticipatory bail in India",
        "What conditions did the court impose in the most significant case?"
    ]},
    {"id": 122, "name": "498A case precedent", "category": "Judgment-2T", "turns": [
        "cases on Section 498A IPC cruelty",
        "Has the Supreme Court issued guidelines to prevent misuse of 498A?"
    ]},
    {"id": 123, "name": "Property dispute follow", "category": "Judgment-2T", "turns": [
        "property partition suit judgments",
        "What is the difference between partition by metes and bounds vs by sale?"
    ]},
    {"id": 124, "name": "FIR quashing deep", "category": "Judgment-2T", "turns": [
        "High Court cases on quashing of FIR under 482 CrPC",
        "What are the grounds on which an FIR can be quashed?"
    ]},
    {"id": 125, "name": "Cheque bounce case", "category": "Judgment-2T", "turns": [
        "important cheque bounce cases under Section 138",
        "What is the limitation period for filing a case?"
    ]},
    {"id": 126, "name": "Land acquisition", "category": "Judgment-2T", "turns": [
        "land acquisition compensation judgments",
        "How is the market value determined?"
    ]},
    {"id": 127, "name": "Cyber crime case", "category": "Judgment-2T", "turns": [
        "Delhi High Court cybercrime judgments",
        "What constitutes cyber harassment under IT Act?"
    ]},
    {"id": 128, "name": "Medical negligence", "category": "Judgment-2T", "turns": [
        "medical negligence case law in India",
        "What is the standard of care expected from doctors?"
    ]},

    # SCI follow-ups (5)
    {"id": 129, "name": "Privacy right detail", "category": "SCI-2T", "turns": [
        "Supreme Court judgment on right to privacy Puttaswamy",
        "How does this judgment apply to Aadhaar data collection?"
    ]},
    {"id": 130, "name": "Basic structure test", "category": "SCI-2T", "turns": [
        "Kesavananda Bharati case basic structure",
        "What are the features of the basic structure?"
    ]},
    {"id": 131, "name": "Art 21 expansion", "category": "SCI-2T", "turns": [
        "SC expansion of Article 21 right to life",
        "Does right to life include right to livelihood?"
    ]},
    {"id": 132, "name": "Triple talaq effect", "category": "SCI-2T", "turns": [
        "Supreme Court ruling on triple talaq",
        "What legislation was passed after this judgment?"
    ]},
    {"id": 133, "name": "Environment PIL", "category": "SCI-2T", "turns": [
        "Supreme Court on environmental protection",
        "What is the precautionary principle as applied by the SC?"
    ]},

    # Constitution follow-ups (4)
    {"id": 134, "name": "Art 21 scope", "category": "Constitution-2T", "turns": [
        "Article 21 of Indian Constitution",
        "What rights have been read into Article 21 by the Supreme Court?"
    ]},
    {"id": 135, "name": "Art 14 test", "category": "Constitution-2T", "turns": [
        "Article 14 right to equality",
        "What is the test of reasonable classification?"
    ]},
    {"id": 136, "name": "Art 32 vs 226", "category": "Constitution-2T", "turns": [
        "Article 32 of Indian Constitution",
        "What is the difference between Article 32 and Article 226?"
    ]},
    {"id": 137, "name": "DPSP enforceability", "category": "Constitution-2T", "turns": [
        "Directive Principles of State Policy",
        "Are DPSPs enforceable in court?"
    ]},

    # Scenario follow-ups (8)
    {"id": 138, "name": "Deposit recovery steps", "category": "Scenario-2T", "turns": [
        "My landlord won't return my security deposit. What legal steps?",
        "Can I file a complaint in consumer court for this?"
    ]},
    {"id": 139, "name": "Wrongful termination", "category": "Scenario-2T", "turns": [
        "I was terminated without notice or severance. What are my rights?",
        "How do I calculate the compensation I am entitled to?"
    ]},
    {"id": 140, "name": "False FIR quash", "category": "Scenario-2T", "turns": [
        "Someone filed a false FIR against me. What can I do?",
        "How long does it take to quash an FIR in High Court?"
    ]},
    {"id": 141, "name": "Consumer complaint process", "category": "Scenario-2T", "turns": [
        "How to file consumer complaint online?",
        "What documents do I need to file the complaint?"
    ]},
    {"id": 142, "name": "Land encroachment suit", "category": "Scenario-2T", "turns": [
        "My neighbour built a wall on my land. Legal remedies?",
        "Can I get an injunction to stop further construction?"
    ]},
    {"id": 143, "name": "Rent agreement dispute", "category": "Scenario-2T", "turns": [
        "My tenant is not paying rent for 6 months. How to evict?",
        "What is the notice period required before filing an eviction suit?"
    ]},
    {"id": 144, "name": "Cheque bounce scenario", "category": "Scenario-2T", "turns": [
        "I received a bounced cheque from a business partner. What should I do?",
        "What is the time limit to send the legal notice?"
    ]},
    {"id": 145, "name": "Marriage divorce process", "category": "Scenario-2T", "turns": [
        "What is the procedure for mutual consent divorce in India?",
        "How long does it take and what is the cooling off period?"
    ]},

    # Multi-agent follow-ups (5)
    {"id": 146, "name": "Art 21 + SC cases", "category": "Multi-2T", "turns": [
        "Article 21 right to life with Supreme Court interpretations",
        "Which SC case first expanded Article 21 beyond just physical life?"
    ]},
    {"id": 147, "name": "138 NI + judgments", "category": "Multi-2T", "turns": [
        "Section 138 NI Act with important case laws",
        "What did the Supreme Court say about dishonour of cheque in Dashrath Rupsingh?"
    ]},
    {"id": 148, "name": "BNS 302 + SC", "category": "Multi-2T", "turns": [
        "Section 302 IPC murder with relevant Supreme Court judgments",
        "What is the test for distinguishing murder from culpable homicide as held by SC?"
    ]},
    {"id": 149, "name": "BNSS arrest + SC", "category": "Multi-2T", "turns": [
        "What are the rights of arrested person under BNSS with SC judgments?",
        "Which SC case laid down the DK Basu guidelines?"
    ]},
    {"id": 150, "name": "Environment law + SC", "category": "Multi-2T", "turns": [
        "environmental protection legislation in India with SC precedents",
        "What did MC Mehta vs Union of India decide?"
    ]},
]


# ═══════════════════════════════════════════════════════════════════════════════
#  3-TURN SEQUENCES (50)
# ═══════════════════════════════════════════════════════════════════════════════

THREE_TURN_SEQUENCES = [
    # Newacts deep dives (10)
    {"id": 201, "name": "IPC 302 full journey", "category": "Newacts-3T", "turns": [
        "Section 302 of IPC",
        "What is its BNS equivalent?",
        "What are the recent amendments to this section?"
    ]},
    {"id": 202, "name": "Bail process complete", "category": "Newacts-3T", "turns": [
        "Section 438 of CrPC anticipatory bail",
        "What conditions can the court impose?",
        "Can anticipatory bail be cancelled? On what grounds?"
    ]},
    {"id": 203, "name": "Dowry complete path", "category": "Newacts-3T", "turns": [
        "Section 498A IPC",
        "What is its BNS equivalent and changes?",
        "Is this section bailable or non-bailable under the new code?"
    ]},
    {"id": 204, "name": "Theft to robbery", "category": "Newacts-3T", "turns": [
        "What is theft under BNS?",
        "How does theft become robbery?",
        "What is the punishment for robbery vs theft?"
    ]},
    {"id": 205, "name": "FIR to chargesheet", "category": "Newacts-3T", "turns": [
        "How to file an FIR under BNSS?",
        "What is the time limit for police to file chargesheet?",
        "What happens if the chargesheet is not filed within the time limit?"
    ]},
    {"id": 206, "name": "Evidence chain BSA", "category": "Newacts-3T", "turns": [
        "electronic evidence under BSA",
        "What is the procedure to produce electronic evidence in court?",
        "Can WhatsApp messages be used as evidence?"
    ]},
    {"id": 207, "name": "BNS self defence", "category": "Newacts-3T", "turns": [
        "right of private defence under BNS",
        "When does the right extend to causing death?",
        "What are the limitations of this right?"
    ]},
    {"id": 208, "name": "Arrest rights full", "category": "Newacts-3T", "turns": [
        "Section 41 BNSS arrest without warrant",
        "What are the rights of the arrested person?",
        "Can the arrested person get bail at the police station itself?"
    ]},
    {"id": 209, "name": "Defamation IPC vs BNS", "category": "Newacts-3T", "turns": [
        "Section 499 IPC defamation",
        "What is the equivalent in BNS?",
        "Is criminal defamation still valid after the SC judgment?"
    ]},
    {"id": 210, "name": "Confession admissibility", "category": "Newacts-3T", "turns": [
        "confession to police under BNSS",
        "Is a confession to police admissible in court?",
        "What about confession to a magistrate?"
    ]},

    # Legislation deep dives (10)
    {"id": 211, "name": "NI Act complete", "category": "Legislation-3T", "turns": [
        "Section 138 of Negotiable Instruments Act",
        "What are the defences available?",
        "What is the procedure to file a complaint under Section 138?"
    ]},
    {"id": 212, "name": "RERA full journey", "category": "Legislation-3T", "turns": [
        "RERA Section 18 delayed possession",
        "What interest rate applies for delayed projects?",
        "Can the buyer withdraw from the project and claim full refund?"
    ]},
    {"id": 213, "name": "Arbitration complete", "category": "Legislation-3T", "turns": [
        "Section 9 Arbitration Act interim measures",
        "When can court intervene after tribunal is formed?",
        "How to challenge an arbitral award under Section 34?"
    ]},
    {"id": 214, "name": "DV Act remedies", "category": "Legislation-3T", "turns": [
        "What constitutes domestic violence under the DV Act?",
        "What orders can the magistrate pass under the Act?",
        "Can a live-in partner file complaint under DV Act?"
    ]},
    {"id": 215, "name": "Hindu marriage divorce", "category": "Legislation-3T", "turns": [
        "Grounds for divorce under Hindu Marriage Act",
        "What is the difference between judicial separation and divorce?",
        "How is alimony calculated in Hindu marriage?"
    ]},
    {"id": 216, "name": "Companies Act compliance", "category": "Legislation-3T", "turns": [
        "director appointment under Companies Act 2013",
        "What are the qualifications and disqualifications of a director?",
        "What is the liability of a director for fraud?"
    ]},
    {"id": 217, "name": "Labour law rights", "category": "Legislation-3T", "turns": [
        "minimum wages under labour law in India",
        "Which authority fixes minimum wages?",
        "What is the remedy if an employer pays less than minimum wages?"
    ]},
    {"id": 218, "name": "Transfer of Property", "category": "Legislation-3T", "turns": [
        "Section 54 of Transfer of Property Act sale",
        "What are the essentials of a valid sale?",
        "Is registration mandatory for sale of immovable property?"
    ]},
    {"id": 219, "name": "Contract formation", "category": "Legislation-3T", "turns": [
        "essentials of a valid contract under Indian Contract Act",
        "What is free consent? When is consent not free?",
        "What are the consequences of a contract made under coercion?"
    ]},
    {"id": 220, "name": "IT Act cybercrime", "category": "Legislation-3T", "turns": [
        "cybercrime provisions under IT Act 2000",
        "What is the punishment for hacking under Section 66?",
        "How to file a cyber crime complaint?"
    ]},

    # Judgment explorations (8)
    {"id": 221, "name": "Bail law evolution", "category": "Judgment-3T", "turns": [
        "landmark cases on bail in India",
        "What did the Supreme Court say about bail as rule and jail as exception?",
        "Are there recent cases that changed bail conditions?"
    ]},
    {"id": 222, "name": "Property dispute chain", "category": "Judgment-3T", "turns": [
        "property dispute judgments on partition",
        "How do courts handle ancestral property disputes?",
        "What is the right of daughters in ancestral property after 2005 amendment?"
    ]},
    {"id": 223, "name": "Medical negligence law", "category": "Judgment-3T", "turns": [
        "medical negligence case law India",
        "What is the Bolam test as applied in India?",
        "Can a doctor be criminally prosecuted for negligence?"
    ]},
    {"id": 224, "name": "FIR quashing grounds", "category": "Judgment-3T", "turns": [
        "cases on quashing of FIR",
        "What are the specific grounds for quashing under 482?",
        "Can the High Court quash an FIR in non-compoundable offences?"
    ]},
    {"id": 225, "name": "Consumer protection", "category": "Judgment-3T", "turns": [
        "important consumer court judgments in India",
        "What is the maximum compensation awarded in a consumer case?",
        "Can services by government departments be challenged in consumer court?"
    ]},
    {"id": 226, "name": "Rent dispute HC", "category": "Judgment-3T", "turns": [
        "Bombay High Court rent dispute judgments",
        "What are the grounds for eviction of a tenant?",
        "Can a tenant claim ownership after long occupation?"
    ]},
    {"id": 227, "name": "Land acquisition comp", "category": "Judgment-3T", "turns": [
        "land acquisition compensation judgments",
        "How is the market value of acquired land determined?",
        "Is solatium still payable under the new Land Acquisition Act 2013?"
    ]},
    {"id": 228, "name": "Matrimonial cases", "category": "Judgment-3T", "turns": [
        "judgments on maintenance under Section 125 CrPC",
        "Can a working wife claim maintenance?",
        "What is the quantum of maintenance usually awarded?"
    ]},

    # SCI deep dives (5)
    {"id": 229, "name": "Privacy right full", "category": "SCI-3T", "turns": [
        "Puttaswamy judgment on right to privacy",
        "How does privacy relate to Aadhaar?",
        "Has any subsequent case modified the Puttaswamy ratio?"
    ]},
    {"id": 230, "name": "Basic structure doctrine", "category": "SCI-3T", "turns": [
        "Kesavananda Bharati case",
        "What constitutes the basic structure?",
        "Has Parliament tried to override the basic structure doctrine?"
    ]},
    {"id": 231, "name": "Art 21 evolution", "category": "SCI-3T", "turns": [
        "evolution of Article 21 in Supreme Court",
        "What rights have been added through judicial interpretation?",
        "Does Article 21 protect the right to die?"
    ]},
    {"id": 232, "name": "Free speech limits", "category": "SCI-3T", "turns": [
        "SC on freedom of speech Article 19(1)(a)",
        "What are the reasonable restrictions under 19(2)?",
        "Can the government ban a book or movie under 19(2)?"
    ]},
    {"id": 233, "name": "Reservation law", "category": "SCI-3T", "turns": [
        "Supreme Court on reservation policy",
        "What is the 50% ceiling on reservation?",
        "Can economically weaker sections get reservation beyond 50%?"
    ]},

    # Constitution deep dives (4)
    {"id": 234, "name": "Fundamental rights full", "category": "Constitution-3T", "turns": [
        "Fundamental rights under Part III",
        "Can fundamental rights be suspended during emergency?",
        "What happened during the 1975 emergency regarding Article 21?"
    ]},
    {"id": 235, "name": "Art 14 complete", "category": "Constitution-3T", "turns": [
        "Article 14 right to equality",
        "What is reasonable classification?",
        "Can there be affirmative action under Article 14?"
    ]},
    {"id": 236, "name": "Writ jurisdiction", "category": "Constitution-3T", "turns": [
        "What are the five types of writs?",
        "When can habeas corpus be filed?",
        "Can a writ be filed against a private person?"
    ]},
    {"id": 237, "name": "Amendment power", "category": "Constitution-3T", "turns": [
        "Article 368 amendment of Constitution",
        "What is the procedure for amending the Constitution?",
        "Which amendments are considered most significant?"
    ]},

    # Scenario conversations (8)
    {"id": 238, "name": "Tenant eviction full", "category": "Scenario-3T", "turns": [
        "My tenant hasn't paid rent for 8 months. How to evict?",
        "What is the legal notice format for eviction?",
        "How long does the eviction process take in court?"
    ]},
    {"id": 239, "name": "Business dispute", "category": "Scenario-3T", "turns": [
        "My business partner ran away with Rs 50 lakh. What legal action?",
        "Should I file a civil suit or criminal case?",
        "Can I attach his property before the trial?"
    ]},
    {"id": 240, "name": "Accident compensation", "category": "Scenario-3T", "turns": [
        "I was in a road accident caused by another driver. What compensation can I claim?",
        "Where do I file the claim — civil court or motor tribunal?",
        "How is the compensation amount calculated?"
    ]},
    {"id": 241, "name": "Workplace harassment", "category": "Scenario-3T", "turns": [
        "I am being sexually harassed at workplace. What should I do?",
        "What is the role of the Internal Complaints Committee?",
        "Can I also file a police complaint along with ICC?"
    ]},
    {"id": 242, "name": "Property fraud", "category": "Scenario-3T", "turns": [
        "I bought a flat but the builder is not giving possession after 3 years",
        "Can I file complaint under RERA?",
        "What compensation and interest can I claim?"
    ]},
    {"id": 243, "name": "Inheritance dispute", "category": "Scenario-3T", "turns": [
        "My father died without a will. How is property distributed?",
        "What if some siblings don't agree to the partition?",
        "Can I file a partition suit in civil court?"
    ]},
    {"id": 244, "name": "Defamation case", "category": "Scenario-3T", "turns": [
        "Someone posted defamatory content about me on social media",
        "Should I file criminal or civil defamation case?",
        "What damages can I claim in civil defamation?"
    ]},
    {"id": 245, "name": "Insurance claim dispute", "category": "Scenario-3T", "turns": [
        "My insurance company rejected my health claim. What can I do?",
        "Can I approach the insurance ombudsman?",
        "Should I file a consumer complaint instead?"
    ]},

    # Multi-agent 3-turn (5)
    {"id": 246, "name": "498A complete journey", "category": "Multi-3T", "turns": [
        "Section 498A IPC cruelty against wife",
        "What are the SC guidelines to prevent misuse?",
        "What is the new BNS equivalent and any changes?"
    ]},
    {"id": 247, "name": "Art 21 + SC + law", "category": "Multi-3T", "turns": [
        "Article 21 right to life",
        "Which Supreme Court case first expanded this right?",
        "What legislation protects right to life in environmental context?"
    ]},
    {"id": 248, "name": "Bail full legal picture", "category": "Multi-3T", "turns": [
        "bail provisions under BNSS",
        "What did the Supreme Court say about bail being the rule?",
        "What are the conditions courts usually impose for bail?"
    ]},
    {"id": 249, "name": "Property law complete", "category": "Multi-3T", "turns": [
        "Section 54 Transfer of Property Act",
        "What are the landmark cases on sale deed vs agreement to sell?",
        "Can a sale be challenged for inadequate consideration?"
    ]},
    {"id": 250, "name": "Consumer law full", "category": "Multi-3T", "turns": [
        "Consumer Protection Act 2019 overview",
        "What are the important cases on deficiency of service?",
        "Can I file a consumer complaint against a government hospital?"
    ]},
]


# ═══════════════════════════════════════════════════════════════════════════════
#  4-TURN SEQUENCES (50)
# ═══════════════════════════════════════════════════════════════════════════════

FOUR_TURN_SEQUENCES = [
    # Newacts deep conversations (10)
    {"id": 301, "name": "Murder law complete", "category": "Newacts-4T", "turns": [
        "Section 302 IPC murder",
        "What is the BNS equivalent?",
        "What is the difference between murder and culpable homicide?",
        "What are the landmark SC cases on Section 302?"
    ]},
    {"id": 302, "name": "Bail law full journey", "category": "Newacts-4T", "turns": [
        "anticipatory bail under Section 438 CrPC",
        "What is the BNSS equivalent?",
        "What conditions can the court impose?",
        "Can anticipatory bail be cancelled and on what grounds?"
    ]},
    {"id": 303, "name": "Dowry law evolution", "category": "Newacts-4T", "turns": [
        "Section 498A IPC dowry cruelty",
        "What is the punishment and is it bailable?",
        "What is the BNS equivalent?",
        "What SC guidelines exist to prevent misuse of 498A?"
    ]},
    {"id": 304, "name": "FIR to trial", "category": "Newacts-4T", "turns": [
        "How to file an FIR under BNSS?",
        "What if police refuses to register FIR?",
        "What is the time limit for chargesheet?",
        "What happens if chargesheet is not filed in time?"
    ]},
    {"id": 305, "name": "Evidence law complete", "category": "Newacts-4T", "turns": [
        "electronic evidence under BSA",
        "How to certify electronic evidence for court?",
        "Can WhatsApp messages be admitted as evidence?",
        "What was the SC ruling on electronic evidence admissibility?"
    ]},
    {"id": 306, "name": "Cheating law deep", "category": "Newacts-4T", "turns": [
        "Section 420 IPC cheating",
        "What is the BNS equivalent?",
        "What is the difference between cheating and criminal breach of trust?",
        "Is cheating under 420 bailable?"
    ]},
    {"id": 307, "name": "Self defence full", "category": "Newacts-4T", "turns": [
        "right of private defence under IPC",
        "When does it extend to causing death?",
        "What is the BNS position on self defence?",
        "What are the leading cases on the right of private defence?"
    ]},
    {"id": 308, "name": "Confession law chain", "category": "Newacts-4T", "turns": [
        "Is confession to police admissible under BSA?",
        "What about confession to a magistrate?",
        "Can a retracted confession be used?",
        "What SC cases deal with admissibility of confessions?"
    ]},
    {"id": 309, "name": "Cyber crime codes", "category": "Newacts-4T", "turns": [
        "cyber crime sections in BNS",
        "What is the punishment for cyber stalking?",
        "How does IT Act interact with BNS provisions?",
        "What are recent cases on social media crimes?"
    ]},
    {"id": 310, "name": "Juvenile law path", "category": "Newacts-4T", "turns": [
        "What age defines a juvenile under Indian law?",
        "What happens if a juvenile commits murder?",
        "Can a juvenile be tried as an adult?",
        "What did the SC say in the Nirbhaya case about the juvenile?"
    ]},

    # Legislation deep conversations (10)
    {"id": 311, "name": "NI Act full process", "category": "Legislation-4T", "turns": [
        "Section 138 of NI Act cheque dishonour",
        "What is the procedure to file a complaint?",
        "What are the defences available to the accused?",
        "What is the punishment and can it be compounded?"
    ]},
    {"id": 312, "name": "RERA complete", "category": "Legislation-4T", "turns": [
        "Section 18 RERA delayed possession",
        "What interest rate applies?",
        "Can buyer withdraw and claim full refund?",
        "What are the recent RERA tribunal orders on delayed projects?"
    ]},
    {"id": 313, "name": "Arbitration full", "category": "Legislation-4T", "turns": [
        "Section 9 Arbitration Act interim measures",
        "Can court intervene after tribunal is formed?",
        "How to challenge arbitral award under Section 34?",
        "What is the time limit for filing challenge?"
    ]},
    {"id": 314, "name": "Hindu succession complete", "category": "Legislation-4T", "turns": [
        "Hindu Succession Act inheritance rules",
        "What are the rights of daughters in ancestral property?",
        "What is the difference between separate and ancestral property?",
        "What did SC say in Vineeta Sharma case about daughter's rights?"
    ]},
    {"id": 315, "name": "Labour law employment", "category": "Legislation-4T", "turns": [
        "Employee rights under labour law in India",
        "What is the legal notice period for termination?",
        "Can an employee claim wrongful termination compensation?",
        "What are the new labour codes and when do they apply?"
    ]},
    {"id": 316, "name": "DV Act remedies full", "category": "Legislation-4T", "turns": [
        "domestic violence under DV Act Section 3",
        "What orders can the magistrate pass?",
        "Can a live-in partner file complaint?",
        "What is the punishment for breach of protection order?"
    ]},
    {"id": 317, "name": "Contract law chain", "category": "Legislation-4T", "turns": [
        "essentials of valid contract under Indian Contract Act",
        "What makes consent not free?",
        "What are the consequences of void and voidable contracts?",
        "Can a minor enter into a contract?"
    ]},
    {"id": 318, "name": "IP law patents", "category": "Legislation-4T", "turns": [
        "patent registration under Indian Patent Act",
        "What can and cannot be patented in India?",
        "What is the procedure for filing a patent?",
        "How long does patent protection last?"
    ]},
    {"id": 319, "name": "Tax law GST", "category": "Legislation-4T", "turns": [
        "GST registration requirements",
        "What are the penalties for non-registration?",
        "How to file GST returns?",
        "What are the recent changes in GST rates?"
    ]},
    {"id": 320, "name": "Banking law NPA", "category": "Legislation-4T", "turns": [
        "SARFAESI Act provisions for NPA recovery",
        "What is the procedure under SARFAESI?",
        "Can the borrower challenge SARFAESI action?",
        "What are the rights of the guarantor under SARFAESI?"
    ]},

    # Judgment conversations (8)
    {"id": 321, "name": "Bail jurisprudence", "category": "Judgment-4T", "turns": [
        "landmark bail cases in India",
        "What did SC say about bail as rule and jail as exception?",
        "What conditions are usually imposed?",
        "Has bail law changed after the recent SC directions?"
    ]},
    {"id": 322, "name": "Property law cases", "category": "Judgment-4T", "turns": [
        "property dispute cases in India",
        "How do courts handle ancestral property partition?",
        "What is adverse possession and its requirements?",
        "Any recent Supreme Court cases on property disputes?"
    ]},
    {"id": 323, "name": "Consumer cases deep", "category": "Judgment-4T", "turns": [
        "landmark consumer court judgments",
        "What is the highest compensation ever awarded?",
        "Can a consumer complaint be filed against a bank?",
        "What is the limitation period for consumer complaints?"
    ]},
    {"id": 324, "name": "Matrimonial law cases", "category": "Judgment-4T", "turns": [
        "maintenance under Section 125 CrPC cases",
        "Can a working wife claim maintenance?",
        "How is the quantum of maintenance determined?",
        "What about maintenance for elderly parents?"
    ]},
    {"id": 325, "name": "Environmental cases", "category": "Judgment-4T", "turns": [
        "environmental law cases in India",
        "What is the polluter pays principle in Indian law?",
        "What is the role of the National Green Tribunal?",
        "Any recent landmark NGT orders?"
    ]},
    {"id": 326, "name": "Cybercrime cases", "category": "Judgment-4T", "turns": [
        "cybercrime case law in India",
        "What constitutes online defamation?",
        "What are the intermediary liability provisions?",
        "Can social media platforms be held liable?"
    ]},
    {"id": 327, "name": "Labour dispute cases", "category": "Judgment-4T", "turns": [
        "wrongful termination case law India",
        "What is the reinstatement remedy?",
        "Can contractual employees claim permanent status?",
        "What recent SC cases changed labour law?"
    ]},
    {"id": 328, "name": "Medical negligence full", "category": "Judgment-4T", "turns": [
        "medical negligence cases in India",
        "What is the standard of care test?",
        "Can a doctor be criminally prosecuted?",
        "What compensation has been awarded in major cases?"
    ]},

    # SCI conversations (4)
    {"id": 329, "name": "Privacy law evolution", "category": "SCI-4T", "turns": [
        "Puttaswamy judgment on privacy",
        "How does it affect Aadhaar?",
        "What is the data protection framework post-Puttaswamy?",
        "Has the Digital Personal Data Protection Act addressed the concerns?"
    ]},
    {"id": 330, "name": "Basic structure full", "category": "SCI-4T", "turns": [
        "Kesavananda Bharati case basic structure",
        "What are the features of basic structure?",
        "Can Parliament override basic structure?",
        "What amendments were struck down as violating basic structure?"
    ]},
    {"id": 331, "name": "Equality law SC", "category": "SCI-4T", "turns": [
        "SC on right to equality Article 14",
        "What is the reasonable classification test?",
        "How does Article 14 interact with reservations?",
        "What did SC say in EWS reservation case?"
    ]},
    {"id": 332, "name": "Free speech SC", "category": "SCI-4T", "turns": [
        "SC on freedom of speech Article 19(1)(a)",
        "What are the reasonable restrictions?",
        "Can the government ban a movie or book?",
        "What is the SC position on hate speech?"
    ]},

    # Scenario extended conversations (10)
    {"id": 333, "name": "Full eviction process", "category": "Scenario-4T", "turns": [
        "My tenant is not paying rent for 1 year. How to evict?",
        "What notice do I need to send?",
        "How long does the court eviction process take?",
        "Can I cut electricity or water to force eviction?"
    ]},
    {"id": 334, "name": "Business fraud complete", "category": "Scenario-4T", "turns": [
        "My business partner cheated me of Rs 1 crore",
        "Should I file civil or criminal case?",
        "Can I get his property attached before trial?",
        "What is the typical timeline for resolution?"
    ]},
    {"id": 335, "name": "Accident claim full", "category": "Scenario-4T", "turns": [
        "I was in a road accident. The other driver was drunk.",
        "What compensation can I claim?",
        "Where should I file the claim?",
        "Can I also file a criminal case against the drunk driver?"
    ]},
    {"id": 336, "name": "Workplace harassment full", "category": "Scenario-4T", "turns": [
        "I am being harassed at work by my manager",
        "What should I do first?",
        "What if the ICC does not act?",
        "Can I file a police complaint for sexual harassment?"
    ]},
    {"id": 337, "name": "Property purchase fraud", "category": "Scenario-4T", "turns": [
        "I bought property and found out the seller had fake documents",
        "What criminal case can I file?",
        "Can I get my money back through civil court?",
        "What if the seller is absconding?"
    ]},
    {"id": 338, "name": "Divorce settlement", "category": "Scenario-4T", "turns": [
        "I want to file for divorce. My wife is not working.",
        "How much alimony will I have to pay?",
        "Can I get custody of my children?",
        "What is the process for mutual consent divorce?"
    ]},
    {"id": 339, "name": "Insurance dispute full", "category": "Scenario-4T", "turns": [
        "My car insurance claim was rejected as they say it was a pre-existing damage",
        "What can I do to challenge this?",
        "Should I approach ombudsman or consumer court?",
        "What evidence do I need to prove my case?"
    ]},
    {"id": 340, "name": "Will contest", "category": "Scenario-4T", "turns": [
        "My father's will gives everything to my brother. Is this legal?",
        "Can I challenge the will?",
        "On what grounds can a will be challenged?",
        "What if the will was made when my father had dementia?"
    ]},
    {"id": 341, "name": "Noise pollution case", "category": "Scenario-4T", "turns": [
        "My neighbour plays loud music every night disturbing my sleep",
        "What legal action can I take?",
        "Can I file a police complaint for noise pollution?",
        "What are the Supreme Court guidelines on noise pollution?"
    ]},
    {"id": 342, "name": "Online fraud recovery", "category": "Scenario-4T", "turns": [
        "I was scammed online and lost Rs 5 lakh through UPI",
        "How do I file a cyber crime complaint?",
        "Can I get my money back?",
        "What are the chances of recovery in online fraud cases?"
    ]},

    # Multi-agent 4-turn (8)
    {"id": 343, "name": "498A complete picture", "category": "Multi-4T", "turns": [
        "Section 498A IPC cruelty",
        "What is the BNS equivalent?",
        "What did the Supreme Court say about misuse?",
        "What is the procedure for filing complaint and getting bail?"
    ]},
    {"id": 344, "name": "Art 21 full exploration", "category": "Multi-4T", "turns": [
        "Article 21 of Indian Constitution",
        "Which Supreme Court cases expanded Article 21?",
        "What legislation protects these rights?",
        "How does Article 21 apply to prisoners?"
    ]},
    {"id": 345, "name": "Property complete legal", "category": "Multi-4T", "turns": [
        "Transfer of Property Act sale provisions",
        "What are the landmark cases on sale deed?",
        "What is the registration process?",
        "What stamp duty applies in Maharashtra?"
    ]},
    {"id": 346, "name": "Consumer full legal", "category": "Multi-4T", "turns": [
        "Consumer Protection Act 2019",
        "What are the important consumer court judgments?",
        "How to file a complaint online?",
        "What is the maximum compensation for deficiency of service?"
    ]},
    {"id": 347, "name": "Environment complete", "category": "Multi-4T", "turns": [
        "Environmental Protection Act provisions",
        "What are the landmark SC cases on environment?",
        "What is the role of the NGT?",
        "How to file PIL for environmental protection?"
    ]},
    {"id": 348, "name": "Bail complete picture", "category": "Multi-4T", "turns": [
        "bail provisions under BNSS",
        "What are the SC guidelines on bail?",
        "What conditions can courts impose?",
        "What are the recent cases on default bail?"
    ]},
    {"id": 349, "name": "Cyber law complete", "category": "Multi-4T", "turns": [
        "Cyber crime provisions in IT Act and BNS",
        "What are the landmark cases on cyber crime?",
        "How to file FIR for cyber crime?",
        "What is the role of CERT-In and cyber police?"
    ]},
    {"id": 350, "name": "Labour full picture", "category": "Multi-4T", "turns": [
        "Employee rights under labour law",
        "What are the important SC cases on wrongful termination?",
        "What are the new labour codes?",
        "How to file complaint at labour commissioner?"
    ]},
]


# ═══════════════════════════════════════════════════════════════════════════════
#  EVALUATION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

SORRY_PATTERNS = [
    r"(?i)i\s+(am\s+)?sorry",
    r"(?i)cannot\s+find",
    r"(?i)could\s+not\s+find",
    r"(?i)no\s+(relevant\s+)?information\s+found",
    r"(?i)unable\s+to\s+(find|locate|retrieve)",
    r"(?i)don'?t\s+have\s+(any\s+)?information",
    r"(?i)no\s+results?\s+found",
    r"(?i)not\s+available\s+in",
]

_AGENT_ALIASES = {
    "Maxim":          {"Maxim", "Legal_Concepts", "Constitution"},
    "Constitution":   {"Constitution", "Legal_Concepts"},
    "Legal_Concepts": {"Legal_Concepts", "Maxim", "Constitution", "Scenario"},
    "SCI_Judgment":   {"SCI_Judgment", "Judgment"},
    "Judgment":       {"Judgment", "SCI_Judgment"},
    "Scenario":       {"Scenario", "Legal_Concepts"},
}


def is_apologetic(text: str) -> bool:
    if not text or len(text.strip()) < 30:
        return True
    for pat in SORRY_PATTERNS:
        if re.search(pat, text[:500]):
            return True
    return False


def classify_result(resp_json: dict, expected_agents) -> str:
    result_text = resp_json.get("result", "")
    agents_used = set(resp_json.get("agents_used", []))

    if expected_agents is not None:
        if isinstance(expected_agents, str):
            acceptable = _AGENT_ALIASES.get(expected_agents, {expected_agents})
            if not acceptable.intersection(agents_used):
                return "FAIL"
        elif isinstance(expected_agents, list):
            any_match = False
            for exp in expected_agents:
                acceptable = _AGENT_ALIASES.get(exp, {exp})
                if acceptable.intersection(agents_used):
                    any_match = True
                    break
            if not any_match:
                return "FAIL"

    if is_apologetic(result_text):
        return "WEAK"
    return "PASS"


def send_request(prompt_tuple, api_url, timeout):
    idx, prompt, category, expected_agents = prompt_tuple
    start = time.time()
    try:
        resp = requests.post(api_url, json={"Promptquery": prompt}, timeout=timeout)
        elapsed = time.time() - start

        if resp.status_code != 200:
            return {
                "id": idx, "prompt": prompt, "category": category,
                "expected_agents": expected_agents, "status": "ERROR",
                "agents_used": [], "response_len": 0, "elapsed": round(elapsed, 1),
                "tokens": 0, "full_response": "",
                "error_detail": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }

        data = resp.json()
        status = classify_result(data, expected_agents)
        return {
            "id": idx, "prompt": prompt, "category": category,
            "expected_agents": expected_agents, "status": status,
            "agents_used": data.get("agents_used", []),
            "response_len": len(data.get("result", "")),
            "elapsed": round(elapsed, 1),
            "tokens": data.get("total_tokens_consumed", 0),
            "full_response": data.get("result", ""),
            "error_detail": "",
        }
    except requests.exceptions.Timeout:
        return {
            "id": idx, "prompt": prompt, "category": category,
            "expected_agents": expected_agents, "status": "ERROR",
            "agents_used": [], "response_len": 0, "elapsed": round(timeout, 1),
            "tokens": 0, "full_response": "", "error_detail": "TIMEOUT",
        }
    except Exception as e:
        return {
            "id": idx, "prompt": prompt, "category": category,
            "expected_agents": expected_agents, "status": "ERROR",
            "agents_used": [], "response_len": 0,
            "elapsed": round(time.time() - start, 1),
            "tokens": 0, "full_response": "", "error_detail": str(e)[:200],
        }


def run_multi_turn(sequence: dict, api_url: str, timeout: int) -> dict:
    thread_id = None
    turn_results = []

    for i, prompt in enumerate(sequence["turns"]):
        start = time.time()
        try:
            payload = {"Promptquery": prompt}
            if thread_id:
                payload["globalThreadId"] = thread_id

            resp = requests.post(api_url, json=payload, timeout=timeout)
            elapsed = time.time() - start

            if resp.status_code != 200:
                turn_results.append({
                    "turn": i + 1, "prompt": prompt, "status": "ERROR",
                    "agents_used": [], "response_len": 0,
                    "elapsed": round(elapsed, 1), "tokens": 0,
                    "error_detail": f"HTTP {resp.status_code}",
                })
                break

            data = resp.json()
            if not thread_id:
                thread_id = data.get("globalThreadId")

            response_text = data.get("result", "")
            status = "WEAK" if is_apologetic(response_text) else "PASS"

            turn_results.append({
                "turn": i + 1, "prompt": prompt, "status": status,
                "agents_used": data.get("agents_used", []),
                "response_len": len(response_text),
                "elapsed": round(elapsed, 1),
                "tokens": data.get("total_tokens_consumed", 0),
                "query_rewritten": data.get("query_rewritten", False),
                "effective_query": data.get("effective_query"),
                "full_response": response_text,
            })
        except Exception as e:
            turn_results.append({
                "turn": i + 1, "prompt": prompt, "status": "ERROR",
                "agents_used": [], "response_len": 0,
                "elapsed": round(time.time() - start, 1), "tokens": 0,
                "error_detail": str(e)[:200],
            })
            break

    all_pass = all(t["status"] == "PASS" for t in turn_results)
    any_error = any(t["status"] == "ERROR" for t in turn_results)
    return {
        "id": sequence["id"],
        "name": sequence["name"],
        "category": sequence["category"],
        "thread_id": thread_id,
        "num_turns": len(sequence["turns"]),
        "turns_completed": len(turn_results),
        "status": "PASS" if all_pass else ("ERROR" if any_error else "PARTIAL"),
        "turns": turn_results,
        "total_elapsed": sum(t["elapsed"] for t in turn_results),
        "total_tokens": sum(t.get("tokens", 0) for t in turn_results),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  REPORT GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def generate_markdown(single_results, multi_results_by_turn, total_elapsed, concurrency):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    all_multi = []
    for turn_count, results in multi_results_by_turn.items():
        all_multi.extend(results)

    single_summary = {"PASS": 0, "WEAK": 0, "FAIL": 0, "ERROR": 0}
    for d in single_results:
        single_summary[d["status"]] += 1

    multi_summary = {"PASS": 0, "PARTIAL": 0, "ERROR": 0}
    for m in all_multi:
        multi_summary[m["status"]] = multi_summary.get(m["status"], 0) + 1

    lines = [
        f"# Multi-Turn Test Report — {now}",
        "",
        f"**Concurrency:** {concurrency} parallel requests  ",
        f"**Total:** 50 single-turn + 150 multi-turn sequences (50×2 + 50×3 + 50×4)  ",
        f"**Wall Clock Time:** {total_elapsed:.1f}s  ",
        "",
        "## Single-Turn Summary (50 prompts)",
        "",
        "| Status | Count | Pct |",
        "|--------|-------|-----|",
    ]
    for status in ["PASS", "WEAK", "FAIL", "ERROR"]:
        count = single_summary.get(status, 0)
        pct = (count / 50 * 100) if 50 > 0 else 0
        lines.append(f"| {status} | {count} | {pct:.0f}% |")

    lines.extend(["", "## Multi-Turn Summary", ""])
    for turn_count in [2, 3, 4]:
        results = multi_results_by_turn.get(turn_count, [])
        total = len(results)
        passed = sum(1 for r in results if r["status"] == "PASS")
        partial = sum(1 for r in results if r["status"] == "PARTIAL")
        errors = sum(1 for r in results if r["status"] == "ERROR")
        avg_time = sum(r["total_elapsed"] for r in results) / total if total else 0
        lines.append(f"### {turn_count}-Turn Sequences ({total} total)")
        lines.append(f"- PASS: {passed} | PARTIAL: {partial} | ERROR: {errors}")
        lines.append(f"- Avg total time: {avg_time:.1f}s")
        lines.append("")

    # Single-turn results table
    lines.extend([
        "## Single-Turn Results",
        "",
        "| # | Status | Category | Prompt | Agent(s) | Time |",
        "|---|--------|----------|--------|----------|------|",
    ])
    for d in sorted(single_results, key=lambda x: x["id"]):
        agents_str = ", ".join(d["agents_used"]) if d["agents_used"] else "—"
        prompt_short = d["prompt"][:50] + ("..." if len(d["prompt"]) > 50 else "")
        lines.append(f"| {d['id']} | {d['status']} | {d['category']} | {prompt_short} | {agents_str} | {d['elapsed']:.1f}s |")
    lines.append("")

    # Multi-turn details
    for turn_count in [2, 3, 4]:
        results = multi_results_by_turn.get(turn_count, [])
        non_pass = [r for r in results if r["status"] != "PASS"]
        if non_pass:
            lines.extend([f"## {turn_count}-Turn Non-PASS Details", ""])
            for r in non_pass:
                lines.extend([
                    f"### #{r['id']} — {r['name']} ({r['status']})",
                    f"Thread: `{(r['thread_id'] or 'N/A')[:12]}...`",
                    "",
                    "| Turn | Prompt | Status | Agents | Time | Rewritten? |",
                    "|------|--------|--------|--------|------|------------|",
                ])
                for t in r["turns"]:
                    agents = ", ".join(t["agents_used"]) if t["agents_used"] else "—"
                    rw = "Yes" if t.get("query_rewritten") else "No"
                    prompt_short = t["prompt"][:50] + ("..." if len(t["prompt"]) > 50 else "")
                    lines.append(f"| {t['turn']} | {prompt_short} | {t['status']} | {agents} | {t['elapsed']:.1f}s | {rw} |")
                lines.extend(["", "---", ""])

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Multi-turn conversation test suite (200 sequences)")
    parser.add_argument("--concurrency", type=int, default=5, help="Max parallel requests")
    parser.add_argument("--api-url", default="http://localhost:5000/pyapi/search", help="API endpoint")
    parser.add_argument("--timeout", type=int, default=180, help="Per-request timeout (seconds)")
    parser.add_argument("--phase", type=str, default="all",
                        choices=["all", "single", "2turn", "3turn", "4turn"],
                        help="Run specific phase only")
    args = parser.parse_args()

    print(f"\n{'='*80}")
    print(f"  MULTI-TURN TEST SUITE")
    print(f"  50 × 1-turn | 50 × 2-turn | 50 × 3-turn | 50 × 4-turn = 200 sequences")
    print(f"  Concurrency: {args.concurrency} | Timeout: {args.timeout}s")
    print(f"  API: {args.api_url}")
    print(f"{'='*80}\n")

    total_start = time.time()
    single_results = []
    multi_results_by_turn = {2: [], 3: [], 4: []}

    # ── Phase 1: Single-Turn ──────────────────────────────────────────────────
    if args.phase in ("all", "single"):
        print("  Phase 1: Single-Turn (50 prompts)\n")
        completed = 0
        total = len(SINGLE_TURN_PROMPTS)

        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            future_to_prompt = {
                pool.submit(send_request, p, args.api_url, args.timeout): p
                for p in SINGLE_TURN_PROMPTS
            }
            for future in as_completed(future_to_prompt):
                result = future.result()
                completed += 1
                single_results.append(result)
                color = {"PASS": "\033[92m", "WEAK": "\033[93m", "FAIL": "\033[91m", "ERROR": "\033[91m"}
                reset = "\033[0m"
                agents_short = ",".join(result["agents_used"])[:30] if result["agents_used"] else "—"
                print(f"  [{completed:3d}/{total}] #{result['id']:3d} "
                      f"{result['category']:20s} "
                      f"{color.get(result['status'], '')}{result['status']:5s}{reset}  "
                      f"{agents_short:30s}  ({result['elapsed']:.1f}s)")
                sys.stdout.flush()

        pass_count = sum(1 for r in single_results if r["status"] == "PASS")
        print(f"\n  Single-Turn: {pass_count}/{total} PASS\n")

    # ── Phase 2-4: Multi-Turn ─────────────────────────────────────────────────
    turn_configs = [
        (2, "2-Turn", TWO_TURN_SEQUENCES, "2turn"),
        (3, "3-Turn", THREE_TURN_SEQUENCES, "3turn"),
        (4, "4-Turn", FOUR_TURN_SEQUENCES, "4turn"),
    ]

    for turn_count, phase_name, sequences, phase_key in turn_configs:
        if args.phase not in ("all", phase_key):
            continue

        print(f"  Phase {turn_count}: {phase_name} Sequences ({len(sequences)} sequences)\n")
        for i, seq in enumerate(sequences, 1):
            print(f"    [{i:2d}/{len(sequences)}] {seq['name'][:40]:40s} ", end="")
            sys.stdout.flush()
            mt_result = run_multi_turn(seq, args.api_url, args.timeout)
            multi_results_by_turn[turn_count].append(mt_result)
            color = {"PASS": "\033[92m", "PARTIAL": "\033[93m", "ERROR": "\033[91m"}
            reset = "\033[0m"
            c = color.get(mt_result["status"], "")
            turns_ok = sum(1 for t in mt_result["turns"] if t["status"] == "PASS")
            print(f"{c}{mt_result['status']:7s}{reset} "
                  f"({turns_ok}/{mt_result['num_turns']} turns ok) "
                  f"{mt_result['total_elapsed']:.1f}s")
            sys.stdout.flush()

        passed = sum(1 for r in multi_results_by_turn[turn_count] if r["status"] == "PASS")
        print(f"\n  {phase_name}: {passed}/{len(sequences)} PASS\n")

    total_elapsed = time.time() - total_start

    # ── Summary ───────────────────────────────────────────────────────────────
    all_multi = []
    for results in multi_results_by_turn.values():
        all_multi.extend(results)

    single_pass = sum(1 for r in single_results if r["status"] == "PASS")
    multi_pass = sum(1 for r in all_multi if r["status"] == "PASS")
    multi_partial = sum(1 for r in all_multi if r["status"] == "PARTIAL")
    multi_error = sum(1 for r in all_multi if r["status"] == "ERROR")

    print(f"\n{'='*80}")
    print(f"  RESULTS")
    print(f"  Single-Turn:  {single_pass}/{len(single_results)} PASS")
    print(f"  Multi-Turn:   {multi_pass}/{len(all_multi)} PASS, {multi_partial} PARTIAL, {multi_error} ERROR")
    for tc in [2, 3, 4]:
        results = multi_results_by_turn[tc]
        if results:
            p = sum(1 for r in results if r["status"] == "PASS")
            print(f"    {tc}-Turn: {p}/{len(results)} PASS")
    print(f"  Wall clock: {total_elapsed:.1f}s")
    print(f"{'='*80}")

    # ── Save Reports ──────────────────────────────────────────────────────────
    script_dir = os.path.dirname(os.path.abspath(__file__))

    md = generate_markdown(single_results, multi_results_by_turn, total_elapsed, args.concurrency)
    md_path = os.path.join(script_dir, "test_report_multi_turn.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"\n  Report: {md_path}")

    json_path = os.path.join(script_dir, "test_results_multi_turn.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                "single_turn": {"total": len(single_results),
                                "pass": single_pass},
                "multi_turn": {
                    "2_turn": {"total": len(multi_results_by_turn[2]),
                               "pass": sum(1 for r in multi_results_by_turn[2] if r["status"] == "PASS")},
                    "3_turn": {"total": len(multi_results_by_turn[3]),
                               "pass": sum(1 for r in multi_results_by_turn[3] if r["status"] == "PASS")},
                    "4_turn": {"total": len(multi_results_by_turn[4]),
                               "pass": sum(1 for r in multi_results_by_turn[4] if r["status"] == "PASS")},
                },
            },
            "total_elapsed": total_elapsed,
            "concurrency": args.concurrency,
            "single_turn": sorted(single_results, key=lambda x: x["id"]),
            "multi_turn_2": multi_results_by_turn[2],
            "multi_turn_3": multi_results_by_turn[3],
            "multi_turn_4": multi_results_by_turn[4],
        }, f, indent=2)
    print(f"  Results: {json_path}")

    has_failures = any(r["status"] in ("FAIL", "ERROR") for r in single_results)
    has_failures = has_failures or any(r["status"] == "ERROR" for r in all_multi)
    return 1 if has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
