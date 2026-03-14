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

# ── Prompt Tuple: (id, prompt, category, expected_agents) ────────────────────
#    expected_agents: str (single) or list[str] (any-of / multi-agent)
#    None = any agent is acceptable

PROMPTS = [
    # ═══════════════════════════════════════════════════════════════════════════
    #  NEWACTS — BNS / BNSS / BSA / IPC / CrPC / IEA  (1–18)
    # ═══════════════════════════════════════════════════════════════════════════

    # Single Section
    (1,  "Section 302 of IPC", "Newacts-Single", "Newacts"),
    (2,  "Section 35 of BNS", "Newacts-Single", "Newacts"),
    (3,  "What is Section 438 of CrPC?", "Newacts-Single", "Newacts"),
    (4,  "Section 173 of BNSS", "Newacts-Single", "Newacts"),

    # Multi Section
    (5,  "Sections 302 and 307 of IPC", "Newacts-Multi", "Newacts"),
    (6,  "Compare Section 154 and Section 161 of CrPC", "Newacts-Multi", "Newacts"),
    (7,  "Explain Sections 64, 65 and 66 of BSA", "Newacts-Multi", "Newacts"),

    # Topic Search
    (8,  "punishment for theft in BNS", "Newacts-Topic", "Newacts"),
    (9,  "bail provisions under BNSS", "Newacts-Topic", "Newacts"),
    (10, "electronic evidence rules in BSA", "Newacts-Topic", "Newacts"),

    # Old↔New Mapping
    (11, "What is the equivalent of Section 498a of IPC in BNS?", "Newacts-Mapping", "Newacts"),
    (12, "Section 125 CrPC new law equivalent", "Newacts-Mapping", "Newacts"),

    # Subsection / Proviso
    (13, "Section 3(5) of Bharatiya Nyaya Sanhita", "Newacts-Subsection", "Newacts"),
    (14, "IEA Section 65b", "Newacts-Subsection", "Newacts"),

    # Nearby / Edge
    (15, "What does Section 100 of BNS say?", "Newacts-Nearby", "Newacts"),
    (16, "What comes after section 35 of BNS?", "Newacts-Nearby", "Newacts"),

    # Range / Many
    (17, "Sections 302, 304, 304a, 307 and 376 of IPC", "Newacts-Range", "Newacts"),
    (18, "Section 420 IPC punishment for cheating", "Newacts-Single", "Newacts"),

    # ═══════════════════════════════════════════════════════════════════════════
    #  LEGISLATION — All other Indian acts  (19–32)
    # ═══════════════════════════════════════════════════════════════════════════

    # Single Section
    (19, "Section 138 of Negotiable Instruments Act", "Legislation-Single", "Legislation"),
    (20, "Section 9 of Arbitration Act", "Legislation-Single", "Legislation"),
    (21, "Section 23 of Indian Contract Act", "Legislation-Single", "Legislation"),
    (22, "Section 34 of Indian Contract Act", "Legislation-Single", "Legislation"),

    # Multi Section
    (23, "Sections 44 and 45 of Transfer of Property Act", "Legislation-Multi", "Legislation"),
    (24, "Explain Sections 3, 4 and 5 of Consumer Protection Act", "Legislation-Multi", "Legislation"),

    # Range
    (25, "Sections 10 to 15 of Companies Act", "Legislation-Range", "Legislation"),

    # Topic Search
    (26, "director duties under companies act", "Legislation-Topic", "Legislation"),
    (27, "tenant rights in rent control legislation", "Legislation-Topic", "Legislation"),
    (28, "minimum wages provisions in labour law", "Legislation-Topic", "Legislation"),

    # Subsection
    (29, "Section 138(1) of Negotiable Instruments Act", "Legislation-Subsection", "Legislation"),

    # Specific Acts
    (30, "Section 18 of RERA Act", "Legislation-Single", "Legislation"),
    (31, "Section 12 of Domestic Violence Act", "Legislation-Single", "Legislation"),
    (32, "Rule 3 of Maharashtra Rent Control Rules", "Legislation-NonSection", "Legislation"),

    # ═══════════════════════════════════════════════════════════════════════════
    #  JUDGMENT — High Court / General court cases  (33–46)
    # ═══════════════════════════════════════════════════════════════════════════

    # Topic-based
    (33, "cases on anticipatory bail", "Judgment-Topic", "Judgment"),
    (34, "dowry harassment case law", "Judgment-Topic", "Judgment"),
    (35, "cheque bounce cases under Section 138", "Judgment-Topic", "Judgment"),
    (36, "property dispute judgments", "Judgment-Topic", "Judgment"),
    (37, "cases on medical negligence", "Judgment-Topic", "Judgment"),
    (38, "land acquisition compensation judgments", "Judgment-Topic", "Judgment"),

    # Party name search
    (39, "Kirloskar vs Kirloskar property dispute", "Judgment-Party", "Judgment"),
    (40, "State of Maharashtra vs Suresh", "Judgment-Party", "Judgment"),

    # Case type search
    (41, "quashing of FIR in cyber crime cases", "Judgment-CaseType", "Judgment"),
    (42, "writ petition cases on fundamental rights", "Judgment-CaseType", "Judgment"),

    # Legal provision-based
    (43, "cases invoking Section 498A IPC", "Judgment-Provision", "Judgment"),
    (44, "judgments under Section 138 NI Act", "Judgment-Provision", "Judgment"),

    # Specific court
    (45, "Bombay High Court cases on rent dispute", "Judgment-Court", "Judgment"),
    (46, "Delhi High Court cybercrime judgments", "Judgment-Court", "Judgment"),

    # ═══════════════════════════════════════════════════════════════════════════
    #  SCI_JUDGMENT — Supreme Court of India  (47–56)
    # ═══════════════════════════════════════════════════════════════════════════

    # Topic-based
    (47, "Supreme Court cases on right to privacy", "SCI-Topic", "SCI_Judgment"),
    (48, "SC judgment on Article 21 right to life", "SCI-Topic", "SCI_Judgment"),
    (49, "Supreme Court ruling on triple talaq", "SCI-Topic", "SCI_Judgment"),
    (50, "SC cases on bail conditions", "SCI-Topic", "SCI_Judgment"),
    (51, "Supreme Court judgments on environmental protection", "SCI-Topic", "SCI_Judgment"),

    # Landmark
    (52, "Supreme Court landmark judgment on Aadhaar privacy", "SCI-Landmark", "SCI_Judgment"),
    (53, "Kesavananda Bharati vs State of Kerala", "SCI-Landmark", "SCI_Judgment"),
    (54, "Maneka Gandhi vs Union of India case", "SCI-Landmark", "SCI_Judgment"),

    # Freedom of speech / constitutional topics
    (55, "SC precedents on freedom of speech Article 19", "SCI-Topic", "SCI_Judgment"),
    (56, "Supreme Court on reservation and equality", "SCI-Topic", "SCI_Judgment"),

    # ═══════════════════════════════════════════════════════════════════════════
    #  CONSTITUTION  (57–64)
    # ═══════════════════════════════════════════════════════════════════════════

    (57, "Article 21 of Indian Constitution", "Constitution", "Constitution"),
    (58, "Fundamental rights under Part III of Constitution", "Constitution", "Constitution"),
    (59, "Article 14 right to equality", "Constitution", "Constitution"),
    (60, "What are fundamental duties under Article 51A?", "Constitution", "Constitution"),
    (61, "Directive principles of state policy", "Constitution", "Constitution"),
    (62, "Article 32 writ jurisdiction of Supreme Court", "Constitution", "Constitution"),
    (63, "Article 19(1)(a) freedom of expression", "Constitution", "Constitution"),
    (64, "What is Article 226 and its scope?", "Constitution", "Constitution"),

    # ═══════════════════════════════════════════════════════════════════════════
    #  MAXIM — Legal maxims & doctrines  (65–72)
    # ═══════════════════════════════════════════════════════════════════════════

    (65, "What is audi alteram partem?", "Maxim", "Maxim"),
    (66, "Explain the doctrine of res judicata", "Maxim", "Maxim"),
    (67, "Meaning of caveat emptor in law", "Maxim", "Maxim"),
    (68, "What is estoppel in legal terms?", "Maxim", "Maxim"),
    (69, "Doctrine of ultra vires", "Maxim", "Maxim"),
    (70, "Explain nemo judex in causa sua", "Maxim", "Maxim"),
    (71, "habeas corpus meaning and legal significance", "Maxim", "Maxim"),
    (72, "What is the doctrine of proportionality?", "Maxim", "Maxim"),

    # ═══════════════════════════════════════════════════════════════════════════
    #  LEGAL_CONCEPTS — Generic legal explanations  (73–76)
    # ═══════════════════════════════════════════════════════════════════════════

    (73, "define murder", "Legal_Concepts", "Legal_Concepts"),
    (74, "What is the difference between bail and anticipatory bail?", "Legal_Concepts", "Legal_Concepts"),
    (75, "Explain FIR and its importance", "Legal_Concepts", "Legal_Concepts"),
    (76, "theft", "Legal_Concepts", None),  # vague — any agent acceptable

    # ═══════════════════════════════════════════════════════════════════════════
    #  SCENARIO — Real-life situations, web-grounded  (77–84)
    # ═══════════════════════════════════════════════════════════════════════════

    (77, "My landlord is refusing to return my security deposit of 50000 rupees after I vacated the flat. What legal steps can I take?",
     "Scenario", "Scenario"),
    (78, "My employer terminated me without any notice or severance pay. What are my rights under Indian labour laws?",
     "Scenario", "Scenario"),
    (79, "I received a legal notice for defamation on social media. How should I respond and what are the possible consequences?",
     "Scenario", "Scenario"),
    (80, "Can police arrest someone without an FIR? What are the rights of an arrested person in India?",
     "Scenario", "Scenario"),
    (81, "What is the procedure to file a consumer complaint online in India and what compensation can I claim?",
     "Scenario", "Scenario"),
    (82, "My neighbour is encroaching on my land and has constructed an illegal wall. I have all the land documents. What remedies are available to me under Indian law? Can I file a civil suit and also approach the municipal corporation? What are the time limits for filing such cases?",
     "Scenario-Long", "Scenario"),
    (83, "What are the latest changes in GST laws in India?",
     "Scenario-News", "Scenario"),
    (84, "Is cryptocurrency legal in India? What are the recent court rulings?",
     "Scenario-News", "Scenario"),

    # ═══════════════════════════════════════════════════════════════════════════
    #  MULTI-AGENT — Queries that should trigger fan-out  (85–94)
    # ═══════════════════════════════════════════════════════════════════════════

    # Constitution + Judgment
    (85, "Explain Article 21 with landmark Supreme Court judgments",
     "Multi-Constitution+Judgment", ["Constitution", "Judgment", "SCI_Judgment"]),
    (86, "Article 14 equality with related case laws",
     "Multi-Constitution+Judgment", ["Constitution", "Judgment", "SCI_Judgment"]),

    # Newacts + SCI_Judgment
    (87, "Section 438 BNSS with Supreme Court precedents on anticipatory bail",
     "Multi-Newacts+SCI", ["Newacts", "SCI_Judgment"]),
    (88, "BNS Section 302 murder with relevant SC judgments",
     "Multi-Newacts+SCI", ["Newacts", "SCI_Judgment", "Judgment"]),

    # Legislation + Judgment
    (89, "Section 138 NI Act with important case laws",
     "Multi-Legislation+Judgment", ["Legislation", "Judgment"]),
    (90, "RERA Section 18 with recent court judgments on delayed possession",
     "Multi-Legislation+Judgment", ["Legislation", "Judgment"]),

    # Constitution + Maxim
    (91, "Article 14 and res judicata",
     "Multi-Constitution+Maxim", ["Constitution", "Maxim"]),

    # Scenario + Newacts
    (92, "I was arrested without a warrant and the police did not inform my family. What are my rights under BNSS?",
     "Multi-Scenario+Newacts", ["Scenario", "Newacts"]),

    # Mixed
    (93, "Compare old Section 302 IPC with new BNS equivalent and cite relevant Supreme Court judgments",
     "Multi-Newacts+SCI", ["Newacts", "SCI_Judgment", "Judgment"]),
    (94, "What are the legal provisions for cybercrime in India and recent judgments?",
     "Multi-Mixed", None),  # any combination is acceptable

    # ═══════════════════════════════════════════════════════════════════════════
    #  EDGE CASES  (95–100)
    # ═══════════════════════════════════════════════════════════════════════════

    (95, "sec 302 ipc", "Edge-Abbreviation", "Newacts"),
    (96, "art 21 constitution", "Edge-Abbreviation", "Constitution"),
    (97, "Anticipatory bail", "Edge-Short", None),
    (98, "What happens if someone commits forgery of a government document and also cheating by personation and criminal breach of trust all in a single transaction?",
     "Edge-Complex", None),  # long enough to maybe trigger Scenario
    (99, "hello", "Edge-NonLegal", None),  # should be blocked or minimal response
    (100, "Tell me about the weather today", "Edge-NonLegal", None),
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


def classify_result(resp_json: dict, expected_agents) -> str:
    """Classify result as PASS / WEAK / FAIL / ERROR.

    expected_agents:
        str        — single expected agent name
        list[str]  — at least one of these must appear in agents_used
        None       — any agent is acceptable (routing not checked)
    """
    result_text = resp_json.get("result", "")
    agents_used = set(resp_json.get("agents_used", []))

    # Check routing
    if expected_agents is not None:
        if isinstance(expected_agents, str):
            # Single expected agent — allow aliases
            acceptable = _AGENT_ALIASES.get(expected_agents, {expected_agents})
            if not acceptable.intersection(agents_used):
                return "FAIL"
        elif isinstance(expected_agents, list):
            # Multi-agent: at least ONE expected must appear
            any_match = False
            for exp in expected_agents:
                acceptable = _AGENT_ALIASES.get(exp, {exp})
                if acceptable.intersection(agents_used):
                    any_match = True
                    break
            if not any_match:
                return "FAIL"

    # Check response quality
    if is_apologetic(result_text):
        return "WEAK"

    return "PASS"


def send_request(prompt_tuple, api_url, timeout, api_key=None):
    """Send a single request and return the result dict."""
    idx, prompt, category, expected_agents = prompt_tuple
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


# ── Multi-Turn Tests ─────────────────────────────────────────────────────────

MULTI_TURN_SEQUENCES = [
    {
        "name": "Newacts Follow-up",
        "turns": [
            "Section 302 of IPC",
            "What is its equivalent in BNS?",
            "What is the punishment for this section?",
        ],
    },
    {
        "name": "Judgment Follow-up",
        "turns": [
            "cases on anticipatory bail in India",
            "What did the Supreme Court say in the most recent case?",
        ],
    },
    {
        "name": "Legislation Follow-up",
        "turns": [
            "Section 138 of Negotiable Instruments Act",
            "What are the defences available to the accused under this section?",
        ],
    },
]


def run_multi_turn(sequence: dict, api_url: str, timeout: int, api_key: str = None) -> dict:
    """Run a multi-turn conversation sequence and return results."""
    thread_id = None
    turn_results = []
    headers = {"X-API-Key": api_key} if api_key else {}

    for i, prompt in enumerate(sequence["turns"]):
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

    # Results table
    lines.extend([
        "## Single-Turn Results",
        "",
        "| # | Status | Category | Prompt | Agent(s) | Time | Resp |",
        "|---|--------|----------|--------|----------|------|------|",
    ])
    for d in details_sorted:
        agents_str = ", ".join(d["agents_used"]) if d["agents_used"] else "—"
        prompt_short = d["prompt"][:50] + ("..." if len(d["prompt"]) > 50 else "")
        lines.append(
            f"| {d['id']} | {d['status']} | {d['category']} | {prompt_short} "
            f"| {agents_str} | {d['elapsed']:.1f}s | {d['response_len']} |"
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
                "| Turn | Prompt | Status | Agents | Time | Rewritten? |",
                "|------|--------|--------|--------|------|------------|",
            ])
            for t in mt["turns"]:
                agents = ", ".join(t["agents_used"]) if t["agents_used"] else "—"
                rw = "Yes" if t.get("query_rewritten") else "No"
                prompt_short = t["prompt"][:50] + ("..." if len(t["prompt"]) > 50 else "")
                lines.append(
                    f"| {t['turn']} | {prompt_short} | {t['status']} | {agents} | {t['elapsed']:.1f}s | {rw} |"
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
            print(
                f"  [{completed:3d}/{total}] #{result['id']:3d} "
                f"{result['category']:28s} "
                f"{color.get(result['status'], '')}{result['status']:5s}{reset}  "
                f"{agents_short:30s}  "
                f"({result['elapsed']:.1f}s)"
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
        print(f"\n--- Non-PASS Details ---")
        for d in non_pass:
            expected = d.get("expected_agents") or "Any"
            if isinstance(expected, list):
                expected = " | ".join(expected)
            print(f"\n  #{d['id']} [{d['status']}] {d['prompt'][:60]}")
            print(f"    Expected: {expected}  |  Actual: {', '.join(d['agents_used']) or '—'}")
            if d.get("error_detail"):
                print(f"    Error: {d['error_detail']}")
            elif d["full_response"]:
                preview = d["full_response"].replace("\n", " ")[:120]
                print(f"    Preview: {preview}...")
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
