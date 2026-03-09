"""Advanced test suite — 20 prompts testing real-world user expectations.

Unlike test_concurrent.py which tests basic routing and single-agent responses,
this suite focuses on:
  - Multi-agent queries (Bug #1 fix: Constitution + Maxim in same query)
  - Multi-turn conversations with follow-ups (Bug #7 fix: rewritten query saves)
  - Complex scenarios requiring cross-domain reasoning
  - Natural language phrasing (how real users actually type)
  - Response quality validation (not just routing correctness)

Usage:
    python tests/test_advanced.py [--api-url URL] [--timeout N] [--concurrency N]

Outputs:
    tests/test_report_advanced.md   (markdown report)
    tests/test_results_advanced.json (machine-readable, for evaluate_results.py)
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# ── Test Prompts ─────────────────────────────────────────────────────────────
# Format: (id, prompt, category, expected_agents, quality_checks)
#
# expected_agents: list of agents that SHOULD appear (any match = pass)
# quality_checks: dict of keywords/patterns the response MUST contain

PROMPTS = [
    # ── Multi-Agent: Constitution + Maxim (tests Bug #1 fix) ─────────────
    (1,
     "Explain Article 21 of the Constitution and the doctrine of audi alteram partem",
     "MultiAgent-ConstitutionMaxim",
     ["Constitution", "Maxim"],
     {"must_contain": ["Article 21", "audi alteram partem"],
      "must_not_contain": []},
     ),
    (2,
     "What are the fundamental rights under Article 14 and explain the maxim of res judicata",
     "MultiAgent-ConstitutionMaxim",
     ["Constitution", "Maxim"],
     {"must_contain": ["Article 14", "res judicata"],
      "must_not_contain": []},
     ),

    # ── Multi-Agent: Legislation + Judgment ──────────────────────────────
    (3,
     "Explain Section 138 of Negotiable Instruments Act and relevant case laws on cheque bounce",
     "MultiAgent-LegislationJudgment",
     ["Legislation", "Judgment"],
     {"must_contain": ["138", "Negotiable Instruments"],
      "must_not_contain": []},
     ),
    (4,
     "Section 9 of Arbitration Act with important Supreme Court precedents on interim relief",
     "MultiAgent-LegislationSCI",
     ["Legislation", "SCI_Judgment"],
     {"must_contain": ["Section 9", "Arbitration"],
      "must_not_contain": []},
     ),

    # ── Multi-Agent: Drafting + Case Law ─────────────────────────────────
    (5,
     "Draft a bail application for offence under Section 420 IPC with relevant case laws",
     "MultiAgent-DraftingJudgment",
     ["Drafting", "Judgment"],
     {"must_contain": ["bail", "420"],
      "must_not_contain": []},
     ),

    # ── Multi-Agent: Newacts + Judgment ───────────────────────────────────
    (6,
     "Section 438 BNSS anticipatory bail provisions with Supreme Court precedents",
     "MultiAgent-NewactsSCI",
     ["Newacts", "SCI_Judgment"],
     {"must_contain": ["438", "BNSS"],
      "must_not_contain": []},
     ),

    # ── Complex Scenarios (natural language, real user phrasing) ──────────
    (7,
     "I bought a flat 3 years ago and the builder has not given possession yet. He is also not refunding my money. I have paid 45 lakhs. What legal options do I have under RERA and consumer protection laws?",
     "Scenario-RealEstate",
     ["Scenario"],
     {"must_contain": ["RERA|Real Estate"],
      "must_not_contain": []},
     ),
    (8,
     "My husband and his family are demanding 10 lakhs in dowry and threatening to harm me. I want to file a case against them. What sections apply and what is the procedure?",
     "Scenario-DomesticViolence",
     ["Scenario"],
     {"must_contain": ["498"],
      "must_not_contain": []},
     ),
    (9,
     "I am a startup founder and my co-founder is misusing company funds. We have a shareholders agreement. How can I legally remove him and protect the company?",
     "Scenario-Corporate",
     ["Scenario"],
     {"must_contain": [],
      "must_not_contain": []},
     ),
    (10,
     "Someone posted my private photos on Instagram without my consent. What cybercrime provisions apply and how do I file an FIR?",
     "Scenario-Cyber",
     ["Scenario"],
     {"must_contain": ["IT Act"],
      "must_not_contain": []},
     ),

    # ── Cross-Domain: Old vs New Law Comparison ──────────────────────────
    (11,
     "Compare the bail provisions under old CrPC Section 438 with the new BNSS. What has changed?",
     "Newacts-Comparison",
     ["Newacts"],
     {"must_contain": ["438"],
      "must_not_contain": []},
     ),
    (12,
     "What is the difference between IPC Section 302 and BNS Section 103 on murder?",
     "Newacts-Comparison",
     ["Newacts"],
     {"must_contain": ["302", "103"],
      "must_not_contain": []},
     ),

    # ── Deep Legal Concepts ──────────────────────────────────────────────
    (13,
     "Explain the concept of right to privacy as a fundamental right in light of KS Puttaswamy judgment",
     "Constitution-Deep",
     ["Constitution"],
     {"must_contain": ["privacy"],
      "must_not_contain": []},
     ),
    (14,
     "What is the basic structure doctrine and which Supreme Court cases established it?",
     "Constitution-Deep",
     ["Constitution", "SCI_Judgment"],
     {"must_contain": ["basic structure"],
      "must_not_contain": []},
     ),

    # ── Drafting with Specific Requirements ──────────────────────────────
    (15,
     "Draft a legal notice to a landlord for illegal eviction. Include references to Rent Control Act and relevant sections",
     "Drafting-Detailed",
     ["Drafting"],
     {"must_contain": ["legal notice", "landlord"],
      "must_not_contain": []},
     ),
    (16,
     "Draft a consumer complaint for defective product under Consumer Protection Act 2019 for filing in District Commission",
     "Drafting-Detailed",
     ["Drafting"],
     {"must_contain": ["consumer", "complaint"],
      "must_not_contain": []},
     ),

    # ── Judgment Search with Specificity ──────────────────────────────────
    (17,
     "Find Supreme Court judgments on Section 377 IPC and LGBTQ rights in India",
     "SCI-Specific",
     ["SCI_Judgment"],
     {"must_contain": ["377"],
      "must_not_contain": []},
     ),
    (18,
     "What did the Supreme Court hold in Maneka Gandhi vs Union of India regarding Article 21?",
     "SCI-NamedCase",
     ["SCI_Judgment"],
     {"must_contain": ["Maneka Gandhi"],
      "must_not_contain": []},
     ),

    # ── Edge Cases: Ambiguous / Conversational ───────────────────────────
    (19,
     "If someone slaps me in public, what can I do legally?",
     "Edge-Conversational",
     ["Scenario"],
     {"must_contain": [],
      "must_not_contain": []},
     ),
    (20,
     "Can a wife file both 498A and domestic violence case simultaneously? What are the pros and cons?",
     "Edge-Analysis",
     ["Scenario"],
     {"must_contain": ["498"],
      "must_not_contain": []},
     ),
]


# ── Validation ───────────────────────────────────────────────────────────────

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
    "Maxim": {"Maxim", "Legal_Concepts", "Constitution"},
    "Constitution": {"Constitution", "Legal_Concepts"},
    "Legal_Concepts": {"Legal_Concepts", "Maxim", "Constitution"},
    "SCI_Judgment": {"SCI_Judgment", "Judgment"},
    "Legislation": {"Legislation", "Newacts"},
    "Judgment": {"Judgment", "SCI_Judgment"},
}


def is_apologetic(text: str) -> bool:
    if not text or len(text.strip()) < 30:
        return True
    for pat in SORRY_PATTERNS:
        if re.search(pat, text[:500]):
            return True
    return False


def classify_result(resp_json: dict, expected_agents: list[str],
                    quality_checks: dict) -> tuple[str, list[str]]:
    """Classify result as PASS/WEAK/FAIL with reasons.

    For multi-agent queries, checks that ALL expected agents were used.
    Also validates quality_checks (must_contain keywords in response).
    """
    result_text = resp_json.get("result", "")
    agents_used = set(resp_json.get("agents_used", []))
    reasons = []

    # Check agent routing
    if expected_agents:
        for expected in expected_agents:
            acceptable = _AGENT_ALIASES.get(expected, {expected})
            if not acceptable.intersection(agents_used):
                reasons.append(f"Missing agent: {expected} (got: {agents_used})")

    # Check response quality
    if is_apologetic(result_text):
        reasons.append("Response is apologetic/empty")

    # Check must_contain keywords (supports "A|B" for alternatives)
    for keyword in quality_checks.get("must_contain", []):
        alternatives = [k.strip() for k in keyword.split("|")]
        if not any(alt.lower() in result_text.lower() for alt in alternatives):
            reasons.append(f"Missing keyword: '{keyword}'")

    # Check must_not_contain
    for keyword in quality_checks.get("must_not_contain", []):
        if keyword.lower() in result_text.lower():
            reasons.append(f"Contains forbidden: '{keyword}'")

    if not reasons:
        return "PASS", []
    elif any("Missing agent" in r for r in reasons):
        return "FAIL", reasons
    elif any("apologetic" in r for r in reasons):
        return "WEAK", reasons
    else:
        return "WEAK", reasons


# ── Request Handling ─────────────────────────────────────────────────────────

def send_request(prompt_tuple, api_url, timeout):
    """Send a single request and return the result dict."""
    idx, prompt, category, expected_agents, quality_checks = prompt_tuple
    start = time.time()
    try:
        resp = requests.post(
            api_url,
            json={"Promptquery": prompt},
            timeout=timeout,
        )
        elapsed = time.time() - start

        if resp.status_code != 200:
            return {
                "id": idx, "prompt": prompt, "category": category,
                "expected_agents": expected_agents, "status": "ERROR",
                "agents_used": [], "response_len": 0, "elapsed": round(elapsed, 1),
                "tokens": 0, "full_response": "", "fail_reasons": [],
                "error_detail": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }

        data = resp.json()
        status, reasons = classify_result(data, expected_agents, quality_checks)
        return {
            "id": idx, "prompt": prompt, "category": category,
            "expected_agents": expected_agents, "status": status,
            "agents_used": data.get("agents_used", []),
            "response_len": len(data.get("result", "")),
            "elapsed": round(elapsed, 1),
            "tokens": data.get("total_tokens_consumed", 0),
            "full_response": data.get("result", ""),
            "fail_reasons": reasons,
            "error_detail": "",
        }
    except requests.exceptions.Timeout:
        return {
            "id": idx, "prompt": prompt, "category": category,
            "expected_agents": expected_agents, "status": "ERROR",
            "agents_used": [], "response_len": 0, "elapsed": round(timeout, 1),
            "tokens": 0, "full_response": "", "fail_reasons": [],
            "error_detail": "TIMEOUT",
        }
    except Exception as e:
        return {
            "id": idx, "prompt": prompt, "category": category,
            "expected_agents": expected_agents, "status": "ERROR",
            "agents_used": [], "response_len": 0,
            "elapsed": round(time.time() - start, 1),
            "tokens": 0, "full_response": "", "fail_reasons": [],
            "error_detail": str(e)[:200],
        }


# ── Multi-Turn Session Test ─────────────────────────────────────────────────

MULTI_TURN_SESSION = [
    # Simulates a real user session with follow-ups
    ("What is Section 302 of IPC and its equivalent in BNS?",
     {"must_contain": ["302"]}),
    ("What are the Supreme Court cases related to this?",
     {"must_contain": []}),
    ("Can you explain the difference in punishment provisions between old and new law?",
     {"must_contain": []}),
    ("Draft an FIR complaint template for this offence",
     {"must_contain": ["FIR"]}),
]


def run_multi_turn_session(api_url: str, timeout: int) -> list[dict]:
    """Run a 4-turn session testing follow-up query handling."""
    results = []
    thread_id = None

    for i, (query, checks) in enumerate(MULTI_TURN_SESSION, 1):
        start = time.time()
        payload = {"Promptquery": query}
        if thread_id:
            payload["globalThreadId"] = thread_id

        try:
            resp = requests.post(api_url, json=payload, timeout=timeout)
            elapsed = time.time() - start

            if resp.status_code != 200:
                results.append({
                    "turn": i, "query": query, "status": "ERROR",
                    "error_detail": f"HTTP {resp.status_code}",
                    "elapsed": round(elapsed, 1), "agents_used": [],
                    "response_len": 0, "full_response": "",
                    "query_rewritten": False, "effective_query": "",
                })
                continue

            data = resp.json()
            thread_id = data.get("globalThreadId", thread_id)
            result_text = data.get("result", "")

            # Check quality
            status = "PASS"
            if is_apologetic(result_text):
                status = "WEAK"
            for kw in checks.get("must_contain", []):
                alternatives = [k.strip() for k in kw.split("|")]
                if not any(alt.lower() in result_text.lower() for alt in alternatives):
                    status = "WEAK"

            results.append({
                "turn": i, "query": query, "status": status,
                "agents_used": data.get("agents_used", []),
                "response_len": len(result_text),
                "elapsed": round(elapsed, 1),
                "query_rewritten": data.get("query_rewritten", False),
                "effective_query": data.get("effective_query", ""),
                "full_response": result_text,
                "error_detail": "",
            })

        except Exception as e:
            results.append({
                "turn": i, "query": query, "status": "ERROR",
                "error_detail": str(e)[:200],
                "elapsed": round(time.time() - start, 1),
                "agents_used": [], "response_len": 0, "full_response": "",
                "query_rewritten": False, "effective_query": "",
            })

    return results


# ── Report Generation ────────────────────────────────────────────────────────

def generate_markdown(details, session_results, summary, total_elapsed, concurrency):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(details)

    lines = [
        f"# Advanced Test Report — {now}",
        "",
        f"**Concurrency:** {concurrency} parallel requests  ",
        f"**Total Single-Shot Prompts:** {total}  ",
        f"**Multi-Turn Session:** {len(MULTI_TURN_SESSION)} turns  ",
        f"**Wall Clock Time:** {total_elapsed:.1f}s  ",
        "",
        "## Part 1: Single-Shot Prompts",
        "",
        "### Summary",
        "",
        "| Status | Count |",
        "|--------|-------|",
    ]
    for status in ["PASS", "WEAK", "FAIL", "ERROR"]:
        lines.append(f"| {status} | {summary.get(status, 0)} |")

    # Timing
    times = [d["elapsed"] for d in details if d["elapsed"] > 0]
    if times:
        lines.extend([
            "",
            "### Timing",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Avg latency | {sum(times)/len(times):.1f}s |",
            f"| Min latency | {min(times):.1f}s |",
            f"| Max latency | {max(times):.1f}s |",
            f"| Median latency | {sorted(times)[len(times)//2]:.1f}s |",
        ])

    # Results table
    lines.extend([
        "",
        "### Results",
        "",
        "| # | Status | Category | Prompt | Agent(s) | Time | Len |",
        "|---|--------|----------|--------|----------|------|-----|",
    ])
    for d in sorted(details, key=lambda x: x["id"]):
        agents_str = ", ".join(d["agents_used"]) if d["agents_used"] else "—"
        prompt_short = d["prompt"][:55] + ("..." if len(d["prompt"]) > 55 else "")
        lines.append(
            f"| {d['id']} | {d['status']} | {d['category']} | {prompt_short} "
            f"| {agents_str} | {d['elapsed']:.1f}s | {d['response_len']} |"
        )

    # Non-PASS details
    non_pass = [d for d in sorted(details, key=lambda x: x["id"]) if d["status"] != "PASS"]
    if non_pass:
        lines.extend(["", "### Non-PASS Details", ""])
        for d in non_pass:
            lines.extend([
                f"#### #{d['id']} — {d['status']} — {d['category']}",
                "",
                f"**Query:** `{d['prompt']}`  ",
                f"**Expected:** {d['expected_agents']}  ",
                f"**Actual:** {d['agents_used']}  ",
                f"**Time:** {d['elapsed']:.1f}s | **Length:** {d['response_len']}  ",
            ])
            if d.get("fail_reasons"):
                lines.append(f"**Reasons:** {'; '.join(d['fail_reasons'])}  ")
            if d.get("error_detail"):
                lines.append(f"**Error:** `{d['error_detail']}`  ")
            lines.append("")

            if d.get("full_response"):
                lines.extend([
                    "<details>",
                    "<summary>Full Response</summary>",
                    "",
                    d["full_response"][:2000],
                    "",
                    "</details>",
                    "",
                ])

    # Part 2: Multi-turn session
    lines.extend([
        "---",
        "",
        "## Part 2: Multi-Turn Session",
        "",
        "Tests follow-up queries, query rewriting, and conversation memory.",
        "",
        "| Turn | Status | Query | Agent(s) | Rewritten? | Effective Query | Time |",
        "|------|--------|-------|----------|------------|-----------------|------|",
    ])
    for r in session_results:
        eff = r.get("effective_query", "")
        eff_short = (eff[:40] + "...") if eff and len(eff) > 40 else (eff or "—")
        q_short = r["query"][:40] + ("..." if len(r["query"]) > 40 else "")
        agents_str = ", ".join(r["agents_used"]) if r["agents_used"] else "—"
        lines.append(
            f"| {r['turn']} | {r['status']} | {q_short} "
            f"| {agents_str} | {r.get('query_rewritten', False)} "
            f"| {eff_short} | {r['elapsed']:.1f}s |"
        )

    # Session details
    lines.extend(["", "### Turn Details", ""])
    for r in session_results:
        lines.extend([
            f"#### Turn {r['turn']} — {r['status']}",
            "",
            f"**Query:** `{r['query']}`  ",
            f"**Agents:** {r['agents_used']}  ",
        ])
        if r.get("query_rewritten"):
            lines.append(f"**Rewritten to:** `{r['effective_query']}`  ")
        if r.get("error_detail"):
            lines.append(f"**Error:** `{r['error_detail']}`  ")
        lines.append("")

        if r.get("full_response"):
            lines.extend([
                "<details>",
                "<summary>Full Response</summary>",
                "",
                r["full_response"][:2000],
                "",
                "</details>",
                "",
            ])

    lines.extend(["---", "",
                   f"*Generated by test_advanced.py on {now}*", ""])
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Advanced test runner")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="Max parallel requests for single-shot tests")
    parser.add_argument("--api-url", default="http://localhost:5000/pyapi/search",
                        help="API endpoint")
    parser.add_argument("--timeout", type=int, default=300,
                        help="Per-request timeout in seconds")
    args = parser.parse_args()

    print(f"{'='*80}")
    print(f"  Advanced Test Suite")
    print(f"  {len(PROMPTS)} single-shot prompts + {len(MULTI_TURN_SESSION)}-turn session")
    print(f"  API: {args.api_url}  |  Concurrency: {args.concurrency}")
    print(f"{'='*80}")

    total_start = time.time()

    # ── Part 1: Single-shot prompts (concurrent) ────────────────────────
    print(f"\n--- Part 1: Single-Shot Prompts ({len(PROMPTS)}) ---\n")

    results = {"PASS": 0, "WEAK": 0, "FAIL": 0, "ERROR": 0}
    details = []
    completed = 0
    total = len(PROMPTS)

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        future_to_prompt = {
            pool.submit(send_request, p, args.api_url, args.timeout): p
            for p in PROMPTS
        }
        for future in as_completed(future_to_prompt):
            result = future.result()
            completed += 1
            details.append(result)
            results[result["status"]] += 1

            color = {"PASS": "\033[92m", "WEAK": "\033[93m",
                     "FAIL": "\033[91m", "ERROR": "\033[91m"}
            reset = "\033[0m"
            agents_str = ",".join(result["agents_used"])[:25] if result["agents_used"] else "—"
            print(
                f"  [{completed:2d}/{total}] #{result['id']:2d} "
                f"[{result['category']:30s}] "
                f"{color.get(result['status'], '')}{result['status']:5s}{reset} "
                f"agents=[{agents_str}] "
                f"({result['elapsed']:.1f}s)"
            )
            if result.get("fail_reasons"):
                for reason in result["fail_reasons"]:
                    print(f"           -> {reason}")
            sys.stdout.flush()

    print(f"\n  Single-shot: {results['PASS']}/{total} PASS, "
          f"{results['WEAK']} WEAK, {results['FAIL']} FAIL, {results['ERROR']} ERROR")

    # ── Part 2: Multi-turn session (sequential) ─────────────────────────
    print(f"\n--- Part 2: Multi-Turn Session ({len(MULTI_TURN_SESSION)} turns) ---\n")

    session_results = run_multi_turn_session(args.api_url, args.timeout)

    session_pass = sum(1 for r in session_results if r["status"] == "PASS")
    for r in session_results:
        color = {"PASS": "\033[92m", "WEAK": "\033[93m",
                 "FAIL": "\033[91m", "ERROR": "\033[91m"}
        reset = "\033[0m"
        rewrite_flag = " [rewritten]" if r.get("query_rewritten") else ""
        print(
            f"  Turn {r['turn']}: "
            f"{color.get(r['status'], '')}{r['status']:5s}{reset} "
            f"agents={r['agents_used']} "
            f"({r['elapsed']:.1f}s){rewrite_flag}"
        )
        if r.get("query_rewritten"):
            print(f"           -> {r['effective_query'][:80]}")

    print(f"\n  Session: {session_pass}/{len(MULTI_TURN_SESSION)} PASS")

    total_elapsed = time.time() - total_start

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"  TOTAL: {results['PASS'] + session_pass}/"
          f"{total + len(MULTI_TURN_SESSION)} PASS")
    print(f"  Wall clock: {total_elapsed:.1f}s")
    print(f"{'='*80}")

    # ── Save Reports ─────────────────────────────────────────────────────
    script_dir = os.path.dirname(os.path.abspath(__file__))

    md_content = generate_markdown(
        details, session_results, results, total_elapsed, args.concurrency
    )
    md_path = os.path.join(script_dir, "test_report_advanced.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"\n  Markdown report: {md_path}")

    # JSON for evaluate_results.py compatibility
    json_details = []
    for d in sorted(details, key=lambda x: x["id"]):
        json_details.append({
            "id": d["id"],
            "prompt": d["prompt"],
            "category": d["category"],
            "expected_agent": d["expected_agents"][0] if d["expected_agents"] else None,
            "expected_agents": d["expected_agents"],
            "status": d["status"],
            "agents_used": d["agents_used"],
            "response_len": d["response_len"],
            "elapsed": d["elapsed"],
            "tokens": d["tokens"],
            "full_response": d["full_response"],
            "fail_reasons": d.get("fail_reasons", []),
            "error_detail": d.get("error_detail", ""),
        })

    # Add session turns as separate entries (IDs 101-104)
    for r in session_results:
        json_details.append({
            "id": 100 + r["turn"],
            "prompt": r["query"],
            "category": f"Session-Turn{r['turn']}",
            "expected_agent": None,
            "expected_agents": [],
            "status": r["status"],
            "agents_used": r["agents_used"],
            "response_len": r["response_len"],
            "elapsed": r["elapsed"],
            "tokens": 0,
            "full_response": r["full_response"],
            "fail_reasons": [],
            "error_detail": r.get("error_detail", ""),
            "query_rewritten": r.get("query_rewritten", False),
            "effective_query": r.get("effective_query", ""),
        })

    json_path = os.path.join(script_dir, "test_results_advanced.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": results,
            "session_summary": {
                "total": len(MULTI_TURN_SESSION),
                "pass": session_pass,
            },
            "total_elapsed": total_elapsed,
            "concurrency": args.concurrency,
            "details": json_details,
        }, f, indent=2)
    print(f"  JSON results:   {json_path}")

    all_pass = (results["FAIL"] == 0 and results["ERROR"] == 0
                and all(r["status"] == "PASS" for r in session_results))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
