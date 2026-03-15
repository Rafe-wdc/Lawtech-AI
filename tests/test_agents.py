"""Comprehensive agent test suite — all agents EXCEPT Drafting.

Tests routing correctness, response quality, multi-agent fan-out,
and multi-turn conversation across 9 domain agents.

Usage:
    python tests/test_agents.py [--concurrency N] [--timeout N] [--api-url URL]

Agents Tested:
    Newacts, Legislation, Judgment, SCI_Judgment, Constitution,
    Maxim, Legal_Concepts, Scenario, (Multi-Agent combos)

NOT Tested:
    Drafting, Document (requires PDF upload)
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

# ── Prompt Tuple: (id, prompt, category, expected_agents, expected_keywords) ──
#    expected_agents: str (single) or list[str] (any-of / multi-agent)
#    None = any agent is acceptable
#    expected_keywords: list[str] — keywords that MUST appear in response (case-insensitive)
#    Empty list = no keyword check (e.g., Non_legal, edge cases)

PROMPTS = [
    # ═══════════════════════════════════════════════════════════════════════════
    #  NEWACTS — BNS / BNSS / BSA / IPC / CrPC / IEA  (1–18)
    # ═══════════════════════════════════════════════════════════════════════════

    (1,  "Section 302 of IPC and its equivalent in BNS", "Newacts-Mapping", "Newacts",
     ["302", "IPC", "BNS", "103", "murder"]),
    (2,  "Section 420 IPC mapped to new BNS provision", "Newacts-Mapping", "Newacts",
     ["420", "IPC", "BNS", "318", "cheating"]),
    (3,  "What replaced Section 498A IPC in BNS?", "Newacts-Mapping", "Newacts",
     ["498A", "BNS", "85"]),
    (4,  "Section 376 IPC equivalent in Bharatiya Nyaya Sanhita", "Newacts-Mapping", "Newacts",
     ["376", "IPC", "BNS", "64", "rape"]),
    (5,  "Section 304A IPC in new criminal code", "Newacts-Mapping", "Newacts",
     ["304A", "IPC", "BNS", "106", "negligence"]),
    (6,  "Section 34 IPC common intention in BNS", "Newacts-Mapping", "Newacts",
     ["34", "IPC", "BNS", "common intention"]),
    (7,  "CrPC Section 154 FIR equivalent in BNSS", "Newacts-Mapping", "Newacts",
     ["154", "CrPC", "BNSS", "173", "FIR"]),
    (8,  "Section 41 CrPC arrest without warrant in BNSS", "Newacts-Mapping", "Newacts",
     ["41", "BNSS"]),
    (9,  "Section 125 CrPC maintenance in BNSS", "Newacts-Mapping", "Newacts",
     ["125", "CrPC", "BNSS", "maintenance"]),
    (10, "CrPC Section 482 inherent powers in BNSS", "Newacts-Mapping", "Newacts",
     ["482", "CrPC", "BNSS", "inherent", "High Court"]),
    (11, "Section 438 CrPC anticipatory bail in BNSS", "Newacts-Mapping", "Newacts",
     ["438", "CrPC", "BNSS", "anticipatory", "bail"]),
    (12, "Indian Evidence Act Section 65B in BSA", "Newacts-Mapping", "Newacts",
     ["65B", "Evidence", "BSA", "electronic"]),
    (13, "Section 27 Indian Evidence Act in BSA", "Newacts-Mapping", "Newacts",
     ["27", "Evidence", "BSA", "accused"]),
    (14, "IEA Section 45 expert opinion in Bharatiya Sakshya Adhiniyam", "Newacts-Mapping", "Newacts",
     ["45", "Evidence", "BSA", "expert", "opinion"]),
    (15, "Section 32 dying declaration IEA to BSA mapping", "Newacts-Mapping", "Newacts",
     ["32", "Evidence", "BSA", "dying declaration"]),
    (16, "All changes from IPC to BNS overview", "Newacts-Topic", "Newacts",
     ["IPC", "BNS", "Bharatiya Nyaya Sanhita"]),
    (17, "Section 307 IPC attempt to murder in BNS", "Newacts-Mapping", "Newacts",
     ["307", "IPC", "BNS", "109", "attempt", "murder"]),
    (18, "Section 379 IPC theft provision in BNS", "Newacts-Mapping", "Newacts",
     ["379", "IPC", "BNS", "303", "theft"]),

    # ═══════════════════════════════════════════════════════════════════════════
    #  LEGISLATION — All other Indian acts  (19–32)
    # ═══════════════════════════════════════════════════════════════════════════

    (19, "Section 138 of Negotiable Instruments Act", "Legislation-Single", "Legislation",
     ["138", "Negotiable Instruments", "cheque", "dishonour"]),
    (20, "Section 73 of Indian Contract Act 1872", "Legislation-Single", "Legislation",
     ["73", "Contract Act", "damages", "breach"]),
    (21, "Section 9 of Arbitration and Conciliation Act 1996", "Legislation-Single", "Legislation",
     ["9", "Arbitration", "interim"]),
    (22, "Section 12 of POCSO Act", "Legislation-Single", ["Legislation", "Newacts"],
     ["12", "POCSO", "child"]),
    (23, "Section 66A of Information Technology Act", "Legislation-Single", "Legislation",
     ["66A", "Information Technology"]),
    (24, "Section 7 of IBC insolvency proceedings", "Legislation-Single", "Legislation",
     ["7", "IBC", "Insolvency", "financial creditor"]),
    (25, "Section 34 of Arbitration Act award", "Legislation-Single", "Legislation",
     ["34", "Arbitration", "award", "set aside"]),
    (26, "Section 14 of Hindu Succession Act", "Legislation-Single", "Legislation",
     ["14", "Hindu Succession", "property", "woman"]),
    (27, "Section 13 of Hindu Marriage Act grounds for divorce", "Legislation-Single", "Legislation",
     ["13", "Hindu Marriage", "divorce"]),
    (28, "Section 125 Motor Vehicles Act third party insurance", "Legislation-Single", "Legislation",
     ["125", "Motor Vehicles", "insurance"]),
    (29, "Section 24 of RERA Act", "Legislation-Single", "Legislation",
     ["24", "RERA", "Real Estate"]),
    (30, "Section 3 of Dowry Prohibition Act 1961", "Legislation-Single", "Legislation",
     ["3", "Dowry Prohibition", "dowry", "penalty"]),
    (31, "Section 498A Indian Penal Code cruelty by husband", "Legislation-Single", ["Legislation", "Newacts"],
     ["498A", "cruelty", "husband"]),
    (32, "Section 302 IPC punishment for murder", "Legislation-Single", ["Legislation", "Newacts"],
     ["302", "IPC", "murder", "punishment"]),

    # ═══════════════════════════════════════════════════════════════════════════
    #  JUDGMENT — High Court / General court cases  (33–46)
    # ═══════════════════════════════════════════════════════════════════════════

    (33, "Landmark judgments on Section 498A IPC cruelty", "Judgment-Provision", "Judgment",
     ["498A", "cruelty"]),
    (34, "Court decisions on Section 138 NI Act cheque bounce", "Judgment-Provision", "Judgment",
     ["138", "cheque"]),
    (35, "Judgments on anticipatory bail under CrPC", "Judgment-Topic", "Judgment",
     ["anticipatory bail", "438"]),
    (36, "High Court judgments on dowry death Section 304B", "Judgment-Provision", "Judgment",
     ["304B", "dowry death"]),
    (37, "Bail jurisprudence under Section 439 CrPC", "Judgment-Provision", "Judgment",
     ["439", "bail"]),
    (38, "Motor accident compensation tribunal judgments", "Judgment-Topic", "Judgment",
     ["accident", "compensation"]),
    (39, "Judgments on land acquisition and compensation", "Judgment-Topic", "Judgment",
     ["land acquisition", "compensation"]),
    (40, "Court orders on domestic violence Protection of Women Act", "Judgment-Topic", "Judgment",
     ["domestic violence", "protection"]),
    (41, "Cybercrime judgments under IT Act Section 66", "Judgment-Provision", "Judgment",
     ["66", "IT Act"]),
    (42, "Judgments on medical negligence by doctors", "Judgment-Topic", "Judgment",
     ["medical negligence"]),
    (43, "High Court decisions on writ of habeas corpus", "Judgment-CaseType", "Judgment",
     ["habeas corpus", "writ"]),
    (44, "Consumer protection judgments on deficiency of service", "Judgment-Topic", "Judgment",
     ["consumer", "deficiency", "service"]),
    (45, "Court rulings on specific performance of contract", "Judgment-Topic", "Judgment",
     ["specific performance", "contract"]),
    (46, "Judgments on quashing of FIR under Section 482 CrPC", "Judgment-Provision", "Judgment",
     ["482", "quash", "FIR"]),

    # ═══════════════════════════════════════════════════════════════════════════
    #  SCI_JUDGMENT — Supreme Court of India  (47–56)
    # ═══════════════════════════════════════════════════════════════════════════

    (47, "Kesavananda Bharati vs State of Kerala Supreme Court", "SCI-Landmark", "SCI_Judgment",
     ["Kesavananda", "basic structure"]),
    (48, "Maneka Gandhi vs Union of India judgment", "SCI-Landmark", "SCI_Judgment",
     ["Maneka Gandhi", "Article 21", "personal liberty"]),
    (49, "Vishaka vs State of Rajasthan sexual harassment", "SCI-Landmark", "SCI_Judgment",
     ["Vishaka", "sexual harassment", "guidelines"]),
    (50, "K.S. Puttaswamy vs Union of India privacy", "SCI-Landmark", "SCI_Judgment",
     ["Puttaswamy", "privacy", "fundamental right"]),
    (51, "Navtej Singh Johar vs Union of India Section 377", "SCI-Landmark", "SCI_Judgment",
     ["Navtej", "377"]),
    (52, "MC Mehta vs Union of India environmental law", "SCI-Landmark", "SCI_Judgment",
     ["MC Mehta", "environment"]),
    (53, "Shreya Singhal vs Union of India Section 66A", "SCI-Landmark", "SCI_Judgment",
     ["Shreya Singhal", "66A"]),
    (54, "DK Basu vs State of West Bengal custodial death", "SCI-Landmark", "SCI_Judgment",
     ["DK Basu", "custodial", "arrest"]),
    (55, "Indian Young Lawyers Association vs State of Kerala Sabarimala", "SCI-Landmark", "SCI_Judgment",
     ["Sabarimala", "women"]),
    (56, "Arnesh Kumar vs State of Bihar arrest guidelines", "SCI-Landmark", "SCI_Judgment",
     ["Arnesh Kumar", "arrest"]),

    # ═══════════════════════════════════════════════════════════════════════════
    #  CONSTITUTION  (57–64)
    # ═══════════════════════════════════════════════════════════════════════════

    (57, "Article 14 of Indian Constitution right to equality", "Constitution", "Constitution",
     ["Article 14", "equality", "law"]),
    (58, "Article 19 freedom of speech and expression", "Constitution", "Constitution",
     ["Article 19", "freedom", "speech", "expression"]),
    (59, "Article 21 right to life and personal liberty", "Constitution", "Constitution",
     ["Article 21", "life", "personal liberty"]),
    (60, "Article 32 right to constitutional remedies", "Constitution", "Constitution",
     ["Article 32", "constitutional remedies", "Supreme Court"]),
    (61, "Article 226 power of High Courts to issue writs", "Constitution", "Constitution",
     ["Article 226", "High Court", "writ"]),
    (62, "Fundamental Duties under Article 51A", "Constitution", "Constitution",
     ["Article 51A", "fundamental duties"]),
    (63, "Directive Principles of State Policy Article 39", "Constitution", "Constitution",
     ["Article 39", "directive principles"]),
    (64, "Article 370 special status of Jammu and Kashmir", "Constitution", "Constitution",
     ["Article 370", "Jammu", "Kashmir"]),

    # ═══════════════════════════════════════════════════════════════════════════
    #  MAXIM — Legal maxims & doctrines  (65–72)
    # ═══════════════════════════════════════════════════════════════════════════

    (65, "Explain the legal maxim Res Judicata", "Maxim", "Maxim",
     ["Res Judicata", "adjudicated"]),
    (66, "Audi alteram partem meaning and application", "Maxim", "Maxim",
     ["Audi alteram partem", "hear", "natural justice"]),
    (67, "Nemo judex in causa sua maxim", "Maxim", "Maxim",
     ["Nemo judex", "judge", "bias"]),
    (68, "Actus reus non facit reum nisi mens sit rea", "Maxim", "Maxim",
     ["actus reus", "mens rea", "guilty mind"]),
    (69, "Ignorantia juris non excusat legal principle", "Maxim", "Maxim",
     ["Ignorantia", "ignorance", "law", "excuse"]),
    (70, "Ubi jus ibi remedium meaning", "Maxim", "Maxim",
     ["Ubi jus", "right", "remedy"]),
    (71, "Salus populi suprema lex meaning in Indian law", "Maxim", "Maxim",
     ["Salus populi", "welfare", "people"]),
    (72, "Ratio decidendi and obiter dicta distinction", "Maxim", "Maxim",
     ["ratio decidendi", "obiter dicta", "binding"]),

    # ═══════════════════════════════════════════════════════════════════════════
    #  LEGAL_CONCEPTS — Generic legal explanations  (73–76)
    # ═══════════════════════════════════════════════════════════════════════════

    (73, "What is the doctrine of basic structure in Indian Constitution", "Legal_Concepts", "Legal_Concepts",
     ["basic structure", "Kesavananda", "amendment"]),
    (74, "Explain doctrine of separation of powers in India", "Legal_Concepts", "Legal_Concepts",
     ["separation of powers", "legislature", "executive", "judiciary"]),
    (75, "What is judicial review in Indian legal system", "Legal_Concepts", "Legal_Concepts",
     ["judicial review", "constitution"]),
    (76, "Explain the concept of natural justice principles", "Legal_Concepts", None,
     ["natural justice", "fair"]),

    # ═══════════════════════════════════════════════════════════════════════════
    #  SCENARIO — Real-life situations, web-grounded  (77–84)
    # ═══════════════════════════════════════════════════════════════════════════

    (77, "A tenant has not paid rent for 6 months. What legal steps can the landlord take?",
     "Scenario", "Scenario",
     ["eviction", "rent", "notice", "tenant"]),
    (78, "An employee was terminated without notice. What are the legal remedies?",
     "Scenario", "Scenario",
     ["termination", "notice", "compensation"]),
    (79, "A doctor operated on the wrong patient. What are the legal consequences?",
     "Scenario", "Scenario",
     ["medical negligence", "compensation", "consumer"]),
    (80, "A person was arrested without a warrant for a bailable offence. Is it legal?",
     "Scenario", "Scenario",
     ["arrest", "warrant", "bailable", "bail"]),
    (81, "My neighbour constructed a wall blocking my sunlight. Legal options?",
     "Scenario", "Scenario",
     ["easement", "injunction"]),
    (82, "A company is unable to pay its debts. What is the insolvency process?",
     "Scenario", "Scenario",
     ["insolvency", "IBC", "NCLT"]),
    (83, "A minor signed a contract. Is it enforceable?",
     "Scenario", "Scenario",
     ["minor", "contract", "void"]),
    (84, "Someone posted defamatory content about me on social media. Legal remedies?",
     "Scenario", "Scenario",
     ["defamation", "damages"]),

    # ═══════════════════════════════════════════════════════════════════════════
    #  MULTI-AGENT — Queries that should trigger fan-out  (85–94)
    # ═══════════════════════════════════════════════════════════════════════════

    (85, "Section 302 IPC with Supreme Court landmark judgments on murder",
     "Multi-Newacts+SCI", ["Newacts", "SCI_Judgment", "Judgment"],
     ["302", "murder", "Supreme Court"]),
    (86, "Section 438 BNSS with Supreme Court precedents on anticipatory bail",
     "Multi-Newacts+SCI", ["Newacts", "SCI_Judgment"],
     ["438", "BNSS", "anticipatory bail", "Supreme Court"]),
    (87, "Article 21 Constitution with landmark Supreme Court interpretations",
     "Multi-Constitution+SCI", ["Constitution", "Judgment", "SCI_Judgment"],
     ["Article 21", "life", "liberty", "Supreme Court"]),
    (88, "Section 498A IPC equivalent in BNS with High Court judgments",
     "Multi-Newacts+Judgment", ["Newacts", "Judgment"],
     ["498A", "BNS", "cruelty"]),
    (89, "Cheque bounce Section 138 NI Act with court judgments",
     "Multi-Legislation+Judgment", ["Legislation", "Judgment"],
     ["138", "cheque", "NI Act"]),
    (90, "POCSO Act provisions with Supreme Court guidelines",
     "Multi-Legislation+SCI", ["Legislation", "SCI_Judgment", "Judgment"],
     ["POCSO", "child", "Supreme Court"]),
    (91, "Dowry Prohibition Act with Section 304B IPC and court rulings",
     "Multi-Legislation+Judgment", ["Legislation", "Judgment"],
     ["dowry", "304B"]),
    (92, "Right to privacy Article 21 with Puttaswamy judgment analysis",
     "Multi-Constitution+SCI", ["Constitution", "SCI_Judgment"],
     ["privacy", "Article 21", "Puttaswamy"]),
    (93, "Arbitration Act Section 11 appointment of arbitrator with SC decisions",
     "Multi-Legislation+SCI", ["Legislation", "SCI_Judgment", "Judgment"],
     ["11", "Arbitration", "arbitrator"]),
    (94, "Consumer Protection Act 2019 with landmark consumer court decisions",
     "Multi-Legislation+Judgment", None,
     ["Consumer Protection", "2019", "consumer"]),

    # ═══════════════════════════════════════════════════════════════════════════
    #  EDGE CASES  (95–100)
    # ═══════════════════════════════════════════════════════════════════════════

    (95, "Hello", "Edge-NonLegal", None, []),
    (96, "Thank you for the information", "Edge-NonLegal", None, []),
    (97, "What is the weather today?", "Edge-NonLegal", None, []),
    (98, "धारा 302 आईपीसी में सजा का प्रावधान", "Edge-Hindi", None,
     ["302", "IPC"]),
    (99, "Section 302 IPC aur BNS mein kya badlav hai", "Edge-Hinglish", None,
     ["302", "IPC", "BNS"]),
    (100, "Draft a legal notice for non-payment of dues", "Edge-Drafting", "Drafting",
     ["legal notice", "non-payment"]),
]


# ── Evaluation Helpers ────────────────────────────────────────────────────────

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

# Agents the orchestrator may reasonably swap between
_AGENT_ALIASES = {
    "Maxim":          {"Maxim", "Legal_Concepts", "Constitution"},
    "Constitution":   {"Constitution", "Legal_Concepts"},
    "Legal_Concepts": {"Legal_Concepts", "Maxim", "Constitution", "Scenario"},
    "SCI_Judgment":   {"SCI_Judgment", "Judgment"},
    "Judgment":       {"Judgment", "SCI_Judgment"},
    "Scenario":       {"Scenario", "Legal_Concepts"},
}


def is_apologetic(text: str) -> bool:
    """Check if response is effectively empty or apologetic."""
    if not text or len(text.strip()) < 30:
        return True
    for pat in SORRY_PATTERNS:
        if re.search(pat, text[:500]):
            return True
    return False


def check_keywords(response_text: str, expected_keywords: list) -> tuple:
    """Check keyword coverage in response. Returns (coverage_pct, found, missing)."""
    if not expected_keywords:
        return 1.0, [], []
    response_lower = response_text.lower()
    found = [kw for kw in expected_keywords if kw.lower() in response_lower]
    missing = [kw for kw in expected_keywords if kw.lower() not in response_lower]
    coverage = len(found) / len(expected_keywords)
    return coverage, found, missing


def classify_result(resp_json: dict, expected_agents, expected_keywords=None) -> tuple:
    """Classify result as PASS / WEAK / FAIL / ERROR.

    expected_agents:
        str        — single expected agent name
        list[str]  — at least one of these must appear in agents_used
        None       — any agent is acceptable (routing not checked)
    expected_keywords:
        list[str]  — keywords that must appear in response (case-insensitive)
        None/[]    — no keyword check

    Returns (status, keyword_coverage, keyword_missing).
    """
    result_text = resp_json.get("result", "")
    agents_used = set(resp_json.get("agents_used", []))

    # Check routing
    if expected_agents is not None:
        if isinstance(expected_agents, str):
            # Single expected agent — allow aliases
            acceptable = _AGENT_ALIASES.get(expected_agents, {expected_agents})
            if not acceptable.intersection(agents_used):
                return "FAIL", 0.0, expected_keywords or []
        elif isinstance(expected_agents, list):
            # Multi-agent: at least ONE expected must appear
            any_match = False
            for exp in expected_agents:
                acceptable = _AGENT_ALIASES.get(exp, {exp})
                if acceptable.intersection(agents_used):
                    any_match = True
                    break
            if not any_match:
                return "FAIL", 0.0, expected_keywords or []

    # Check response quality
    if is_apologetic(result_text):
        return "WEAK", 0.0, expected_keywords or []

    # Check keyword coverage
    kw_coverage, kw_found, kw_missing = check_keywords(result_text, expected_keywords or [])
    if expected_keywords:
        if kw_coverage < 0.3:
            return "FAIL", kw_coverage, kw_missing
        elif kw_coverage < 0.6:
            return "WEAK", kw_coverage, kw_missing

    return "PASS", kw_coverage, kw_missing


def send_request(prompt_tuple, api_url, timeout, api_key=None):
    """Send a single request and return the result dict."""
    idx, prompt, category, expected_agents, expected_keywords = prompt_tuple
    headers = {"X-API-Key": api_key} if api_key else {}
    start = time.time()
    try:
        resp = requests.post(
            api_url,
            json={"Promptquery": prompt},
            headers=headers,
            timeout=timeout,
        )
        elapsed = time.time() - start

        if resp.status_code != 200:
            return {
                "id": idx, "prompt": prompt, "category": category,
                "expected_agents": expected_agents,
                "expected_keywords": expected_keywords,
                "status": "ERROR",
                "agents_used": [], "response_len": 0, "elapsed": round(elapsed, 1),
                "tokens": 0, "full_response": "",
                "keyword_coverage": 0.0, "keyword_missing": expected_keywords,
                "error_detail": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }

        data = resp.json()
        status, kw_coverage, kw_missing = classify_result(
            data, expected_agents, expected_keywords
        )
        return {
            "id": idx, "prompt": prompt, "category": category,
            "expected_agents": expected_agents,
            "expected_keywords": expected_keywords,
            "status": status,
            "agents_used": data.get("agents_used", []),
            "response_len": len(data.get("result", "")),
            "elapsed": round(elapsed, 1),
            "tokens": data.get("total_tokens_consumed", 0),
            "full_response": data.get("result", ""),
            "keyword_coverage": round(kw_coverage, 2),
            "keyword_missing": kw_missing,
            "error_detail": "",
        }
    except requests.exceptions.Timeout:
        return {
            "id": idx, "prompt": prompt, "category": category,
            "expected_agents": expected_agents,
            "expected_keywords": expected_keywords,
            "status": "ERROR",
            "agents_used": [], "response_len": 0, "elapsed": round(timeout, 1),
            "tokens": 0, "full_response": "", "error_detail": "TIMEOUT",
            "keyword_coverage": 0.0, "keyword_missing": expected_keywords,
        }
    except Exception as e:
        return {
            "id": idx, "prompt": prompt, "category": category,
            "expected_agents": expected_agents,
            "expected_keywords": expected_keywords,
            "status": "ERROR",
            "agents_used": [], "response_len": 0,
            "elapsed": round(time.time() - start, 1),
            "tokens": 0, "full_response": "", "error_detail": str(e)[:200],
            "keyword_coverage": 0.0, "keyword_missing": expected_keywords,
        }


# ── Multi-Turn Tests ─────────────────────────────────────────────────────────

MULTI_TURN_SEQUENCES = [
    {
        "name": "Newacts Follow-up",
        "turns": [
            ("Section 302 IPC and its BNS equivalent", ["302", "IPC", "BNS", "103"]),
            ("What about the punishment difference between old and new?", ["punishment", "imprisonment"]),
            ("How does Section 307 IPC map to BNS?", ["307", "109", "attempt", "murder"]),
        ],
    },
    {
        "name": "Legislation Drill-down",
        "turns": [
            ("Section 138 of Negotiable Instruments Act", ["138", "cheque", "dishonour"]),
            ("What is the limitation period for filing under this section?", ["limitation"]),
        ],
    },
    {
        "name": "Constitution to Judgment",
        "turns": [
            ("Article 21 right to life and personal liberty", ["Article 21", "life", "liberty"]),
            ("What are the landmark Supreme Court cases expanding Article 21?", ["Supreme Court", "Maneka Gandhi"]),
        ],
    },
    {
        "name": "Scenario to Legislation",
        "turns": [
            ("A person was cheated online by a fake website. What legal action can be taken?", ["fraud", "FIR"]),
            ("Which specific sections of IT Act apply here?", ["IT Act", "section"]),
        ],
    },
    {
        "name": "Maxim to Application",
        "turns": [
            ("Explain the maxim Res Judicata", ["Res Judicata"]),
            ("How has the Supreme Court applied this principle?", ["Supreme Court"]),
        ],
    },
]


def run_multi_turn(sequence: dict, api_url: str, timeout: int, api_key: str = None) -> dict:
    """Run a multi-turn conversation sequence and return results.

    Each turn is a tuple: (prompt, expected_keywords).
    """
    thread_id = None
    turn_results = []
    headers = {"X-API-Key": api_key} if api_key else {}

    for i, turn_data in enumerate(sequence["turns"]):
        prompt, expected_keywords = turn_data
        start = time.time()
        try:
            payload = {"Promptquery": prompt}
            if thread_id:
                payload["globalThreadId"] = thread_id

            resp = requests.post(api_url, json=payload, headers=headers, timeout=timeout)
            elapsed = time.time() - start

            if resp.status_code != 200:
                turn_results.append({
                    "turn": i + 1, "prompt": prompt, "status": "ERROR",
                    "expected_keywords": expected_keywords,
                    "agents_used": [], "response_len": 0,
                    "elapsed": round(elapsed, 1), "tokens": 0,
                    "keyword_coverage": 0.0, "keyword_missing": expected_keywords,
                    "error_detail": f"HTTP {resp.status_code}",
                })
                break

            data = resp.json()
            if not thread_id:
                thread_id = data.get("globalThreadId")

            response_text = data.get("result", "")
            kw_coverage, kw_found, kw_missing = check_keywords(
                response_text, expected_keywords
            )

            if is_apologetic(response_text):
                status = "WEAK"
            elif expected_keywords and kw_coverage < 0.3:
                status = "FAIL"
            elif expected_keywords and kw_coverage < 0.6:
                status = "WEAK"
            else:
                status = "PASS"

            turn_results.append({
                "turn": i + 1, "prompt": prompt, "status": status,
                "expected_keywords": expected_keywords,
                "agents_used": data.get("agents_used", []),
                "response_len": len(response_text),
                "elapsed": round(elapsed, 1),
                "tokens": data.get("total_tokens_consumed", 0),
                "keyword_coverage": round(kw_coverage, 2),
                "keyword_missing": kw_missing,
                "query_rewritten": data.get("query_rewritten", False),
                "effective_query": data.get("effective_query"),
            })
        except Exception as e:
            turn_results.append({
                "turn": i + 1, "prompt": prompt, "status": "ERROR",
                "expected_keywords": expected_keywords,
                "agents_used": [], "response_len": 0,
                "elapsed": round(time.time() - start, 1), "tokens": 0,
                "keyword_coverage": 0.0, "keyword_missing": expected_keywords,
                "error_detail": str(e)[:200],
            })
            break

    all_pass = all(t["status"] == "PASS" for t in turn_results)
    return {
        "name": sequence["name"],
        "thread_id": thread_id,
        "status": "PASS" if all_pass else "FAIL",
        "turns": turn_results,
    }


# ── Report Generation ────────────────────────────────────────────────────────

def _agent_category(category: str) -> str:
    """Extract agent group from category for per-agent summary."""
    return category.split("-")[0].replace("Multi", "Multi-Agent").replace("Edge", "Edge Case")


def generate_markdown(details, summary, total_elapsed, concurrency, multi_turn_results):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(details)
    details_sorted = sorted(details, key=lambda d: d["id"])

    lines = [
        f"# Agent Test Report — {now}",
        "",
        f"**Concurrency:** {concurrency} parallel requests  ",
        f"**Total Prompts:** {total} single + {len(multi_turn_results)} multi-turn sequences  ",
        f"**Wall Clock Time:** {total_elapsed:.1f}s  ",
        f"**Agents Tested:** Newacts, Legislation, Judgment, SCI_Judgment, Constitution, Maxim, Legal_Concepts, Scenario  ",
        f"**Excluded:** Drafting, Document  ",
        "",
        "## Summary",
        "",
        "| Status | Count | Pct |",
        "|--------|-------|-----|",
    ]
    for status in ["PASS", "WEAK", "FAIL", "ERROR"]:
        count = summary.get(status, 0)
        pct = (count / total * 100) if total > 0 else 0
        lines.append(f"| {status} | {count} | {pct:.0f}% |")
    lines.append("")

    # Per-agent breakdown
    agent_stats = {}
    for d in details_sorted:
        group = _agent_category(d["category"])
        if group not in agent_stats:
            agent_stats[group] = {"total": 0, "PASS": 0, "WEAK": 0, "FAIL": 0, "ERROR": 0, "times": []}
        agent_stats[group]["total"] += 1
        agent_stats[group][d["status"]] += 1
        agent_stats[group]["times"].append(d["elapsed"])

    lines.extend([
        "## Per-Agent Breakdown",
        "",
        "| Agent Group | Total | PASS | WEAK | FAIL | ERROR | Avg Time |",
        "|-------------|-------|------|------|------|-------|----------|",
    ])
    for group, stats in sorted(agent_stats.items()):
        avg_t = sum(stats["times"]) / len(stats["times"]) if stats["times"] else 0
        lines.append(
            f"| {group} | {stats['total']} | {stats['PASS']} | "
            f"{stats['WEAK']} | {stats['FAIL']} | {stats['ERROR']} | {avg_t:.1f}s |"
        )
    lines.append("")

    # Timing stats
    times = [d["elapsed"] for d in details_sorted if d["elapsed"] > 0]
    if times:
        lines.extend([
            "## Timing",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Wall clock | {total_elapsed:.1f}s |",
            f"| Sum of all requests | {sum(times):.1f}s |",
            f"| Speedup vs sequential | {sum(times)/total_elapsed:.1f}x |",
            f"| Avg latency | {sum(times)/len(times):.1f}s |",
            f"| Min latency | {min(times):.1f}s |",
            f"| Max latency | {max(times):.1f}s |",
            f"| Median latency | {sorted(times)[len(times)//2]:.1f}s |",
            "",
        ])

    # Keyword coverage stats
    kw_tests = [d for d in details_sorted if d.get("expected_keywords")]
    if kw_tests:
        kw_coverages = [d.get("keyword_coverage", 0) for d in kw_tests]
        avg_kw = sum(kw_coverages) / len(kw_coverages) if kw_coverages else 0
        full_kw = sum(1 for c in kw_coverages if c >= 1.0)
        lines.extend([
            "## Keyword Coverage",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Tests with keywords | {len(kw_tests)} |",
            f"| Avg keyword coverage | {avg_kw:.0%} |",
            f"| Full coverage (100%) | {full_kw}/{len(kw_tests)} |",
            "",
        ])

    # Results table
    lines.extend([
        "## Single-Turn Results",
        "",
        "| # | Status | Category | Prompt | Agent(s) | Time | KW% | Resp |",
        "|---|--------|----------|--------|----------|------|-----|------|",
    ])
    for d in details_sorted:
        agents_str = ", ".join(d["agents_used"]) if d["agents_used"] else "—"
        prompt_short = d["prompt"][:50] + ("..." if len(d["prompt"]) > 50 else "")
        kw_pct = f"{d.get('keyword_coverage', 0):.0%}" if d.get("expected_keywords") else "—"
        lines.append(
            f"| {d['id']} | {d['status']} | {d['category']} | {prompt_short} "
            f"| {agents_str} | {d['elapsed']:.1f}s | {kw_pct} | {d['response_len']} |"
        )
    lines.append("")

    # Multi-turn results
    if multi_turn_results:
        lines.extend(["## Multi-Turn Results", ""])
        for mt in multi_turn_results:
            status_icon = "PASS" if mt["status"] == "PASS" else "FAIL"
            lines.extend([
                f"### {mt['name']} — {status_icon}",
                f"Thread: `{(mt['thread_id'] or 'N/A')[:12]}...`",
                "",
                "| Turn | Prompt | Status | Agents | KW% | Time | Rewritten? |",
                "|------|--------|--------|--------|-----|------|------------|",
            ])
            for t in mt["turns"]:
                agents = ", ".join(t["agents_used"]) if t["agents_used"] else "—"
                rw = "Yes" if t.get("query_rewritten") else "No"
                prompt_short = t["prompt"][:50] + ("..." if len(t["prompt"]) > 50 else "")
                kw_pct = f"{t.get('keyword_coverage', 0):.0%}" if t.get("expected_keywords") else "—"
                lines.append(
                    f"| {t['turn']} | {prompt_short} | {t['status']} | {agents} | {kw_pct} | {t['elapsed']:.1f}s | {rw} |"
                )
            lines.append("")

    # Non-PASS details
    non_pass = [d for d in details_sorted if d["status"] != "PASS"]
    if non_pass:
        lines.extend(["## Non-PASS Details", ""])
        for d in non_pass:
            expected = d.get("expected_agents") or "Any"
            if isinstance(expected, list):
                expected = " | ".join(expected)
            lines.extend([
                f"### #{d['id']} — {d['status']} — {d['category']}",
                "",
                f"**Query:** `{d['prompt']}`  ",
                f"**Expected:** {expected}  ",
                f"**Actual:** {', '.join(d['agents_used']) if d['agents_used'] else '—'}  ",
                f"**Time:** {d['elapsed']:.1f}s | **Response:** {d['response_len']} chars  ",
            ])
            if d.get("expected_keywords"):
                kw_pct = d.get("keyword_coverage", 0)
                kw_miss = d.get("keyword_missing", [])
                lines.append(f"**Keyword Coverage:** {kw_pct:.0%} | **Missing:** {', '.join(kw_miss) if kw_miss else '—'}  ")
            if d.get("error_detail"):
                lines.append(f"**Error:** `{d['error_detail']}`  ")
            if d.get("full_response"):
                preview = d["full_response"].replace("\n", " ")[:300]
                lines.append(f"\n> {preview}...")
            lines.extend(["", "---", ""])

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Comprehensive agent test (all except Drafting)")
    parser.add_argument("--concurrency", type=int, default=5, help="Max parallel requests")
    parser.add_argument("--api-url", default="http://localhost:5000/pyapi/search", help="API endpoint")
    parser.add_argument("--api-key", default=None, help="X-API-Key header value")
    parser.add_argument("--timeout", type=int, default=180, help="Per-request timeout (seconds)")
    parser.add_argument("--skip-multi-turn", action="store_true", help="Skip multi-turn sequences")
    args = parser.parse_args()

    total = len(PROMPTS)
    print(f"\n{'='*80}")
    print(f"  AGENT TEST SUITE — All Agents (except Drafting)")
    print(f"  {total} single-turn prompts + {len(MULTI_TURN_SEQUENCES)} multi-turn sequences")
    print(f"  Concurrency: {args.concurrency} | Timeout: {args.timeout}s")
    print(f"  API: {args.api_url}")
    print(f"{'='*80}\n")

    # ── Phase 1: Single-Turn Concurrent Tests ─────────────────────────────────
    print("  Phase 1: Single-Turn Tests\n")
    results = {"PASS": 0, "WEAK": 0, "FAIL": 0, "ERROR": 0}
    details = []
    completed = 0

    total_start = time.time()

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        future_to_prompt = {
            pool.submit(send_request, p, args.api_url, args.timeout, args.api_key): p
            for p in PROMPTS
        }

        for future in as_completed(future_to_prompt):
            result = future.result()
            completed += 1
            details.append(result)
            results[result["status"]] += 1

            color = {"PASS": "\033[92m", "WEAK": "\033[93m", "FAIL": "\033[91m", "ERROR": "\033[91m"}
            reset = "\033[0m"
            agents_short = ",".join(result["agents_used"])[:30] if result["agents_used"] else "—"
            kw_str = f"KW:{result.get('keyword_coverage', 0):.0%}" if result.get("expected_keywords") else ""
            print(
                f"  [{completed:3d}/{total}] #{result['id']:3d} "
                f"{result['category']:28s} "
                f"{color.get(result['status'], '')}{result['status']:5s}{reset}  "
                f"{agents_short:30s}  "
                f"{kw_str:6s} ({result['elapsed']:.1f}s)"
            )
            sys.stdout.flush()

    single_elapsed = time.time() - total_start

    # ── Phase 2: Multi-Turn Sequential Tests ──────────────────────────────────
    multi_turn_results = []
    if not args.skip_multi_turn:
        print(f"\n  Phase 2: Multi-Turn Tests\n")
        for seq in MULTI_TURN_SEQUENCES:
            print(f"    Running: {seq['name']} ({len(seq['turns'])} turns)...", end=" ")
            sys.stdout.flush()
            mt_result = run_multi_turn(seq, args.api_url, args.timeout, args.api_key)
            multi_turn_results.append(mt_result)
            color = "\033[92m" if mt_result["status"] == "PASS" else "\033[91m"
            reset = "\033[0m"
            total_time = sum(t["elapsed"] for t in mt_result["turns"])
            print(f"{color}{mt_result['status']}{reset} ({total_time:.1f}s)")

    total_elapsed = time.time() - total_start

    # ── Summary ──────────────────────────────────────────────────────────────
    mt_pass = sum(1 for m in multi_turn_results if m["status"] == "PASS")
    mt_total = len(multi_turn_results)

    print(f"\n{'='*80}")
    print(f"  SINGLE-TURN: {results['PASS']}/{total} PASS, "
          f"{results['WEAK']} WEAK, {results['FAIL']} FAIL, {results['ERROR']} ERROR")
    if multi_turn_results:
        print(f"  MULTI-TURN:  {mt_pass}/{mt_total} PASS")
    print(f"  Wall clock: {total_elapsed:.1f}s")
    times = [d["elapsed"] for d in details if d["status"] != "ERROR"]
    if times:
        print(f"  Avg latency: {sum(times)/len(times):.1f}s | Speedup: {sum(times)/single_elapsed:.1f}x")
    print(f"{'='*80}")

    # Non-PASS details
    non_pass = [d for d in sorted(details, key=lambda x: x["id"]) if d["status"] != "PASS"]
    if non_pass:
        def _safe(text):
            return text.encode("ascii", "replace").decode()
        print(f"\n--- Non-PASS Details ---")
        for d in non_pass:
            expected = d.get("expected_agents") or "Any"
            if isinstance(expected, list):
                expected = " | ".join(expected)
            print(f"\n  #{d['id']} [{d['status']}] {_safe(d['prompt'][:60])}")
            print(f"    Expected: {expected}  |  Actual: {', '.join(d['agents_used']) or '—'}")
            if d.get("expected_keywords") and d.get("keyword_missing"):
                print(f"    KW Coverage: {d.get('keyword_coverage', 0):.0%} | Missing: {', '.join(d['keyword_missing'])}")
            if d.get("error_detail"):
                print(f"    Error: {d['error_detail']}")
            elif d["full_response"]:
                preview = d["full_response"].replace("\n", " ")[:120]
                print(f"    Preview: {_safe(preview)}...")
    else:
        print("\n  All single-turn prompts passed!")

    # ── Save Reports ─────────────────────────────────────────────────────────
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Markdown report
    md_content = generate_markdown(details, results, total_elapsed, args.concurrency, multi_turn_results)
    md_path = os.path.join(script_dir, "test_report_agents.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"\n  Markdown report: {md_path}")

    # JSON results (truncated)
    json_details = []
    for d in sorted(details, key=lambda x: x["id"]):
        jd = dict(d)
        if len(jd.get("full_response", "")) > 500:
            jd["response_preview"] = jd["full_response"][:500] + "..."
        else:
            jd["response_preview"] = jd["full_response"]
        del jd["full_response"]
        # Include keyword stats
        jd["keyword_coverage"] = d.get("keyword_coverage", 0.0)
        jd["keyword_missing"] = d.get("keyword_missing", [])
        json_details.append(jd)

    json_path = os.path.join(script_dir, "test_results_agents.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": results,
            "total_elapsed": total_elapsed,
            "concurrency": args.concurrency,
            "single_turn": json_details,
            "multi_turn": multi_turn_results,
        }, f, indent=2)
    print(f"  JSON results:   {json_path}")

    # Full-response JSON (for AI evaluator)
    full_json_path = os.path.join(script_dir, "test_results_agents_full.json")
    with open(full_json_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": results,
            "total_elapsed": total_elapsed,
            "concurrency": args.concurrency,
            "single_turn": sorted(details, key=lambda x: x["id"]),
            "multi_turn": multi_turn_results,
        }, f, indent=2)
    print(f"  Full results:   {full_json_path}")

    has_failures = results["FAIL"] > 0 or results["ERROR"] > 0
    return 1 if has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
