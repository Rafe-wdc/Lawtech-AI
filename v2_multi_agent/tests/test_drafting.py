"""Advanced Drafting Agent test suite — 15 prompts testing real-world drafting requests.

Tests the full drafting pipeline:
  - Template retrieval from Elasticsearch (497 templates)
  - Template selection via GPT-4o-mini
  - Draft generation via GPT-4o
  - Natural language phrasing (how real users ask)
  - Edge cases: vague requests, specific section references, multi-party docs

Usage (from v2_multi_agent/):
    python tests/test_drafting.py [--api-url URL] [--timeout N] [--concurrency N]

Outputs:
    tests/test_report_drafting.md   (markdown report)
    tests/test_results_drafting.json (machine-readable, for evaluate_results.py)
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


# -- Test Prompts ---------------------------------------------------------------
# Format: (id, prompt, subcategory, must_contain_keywords, must_not_contain)
#
# Designed from actual ES index templates — each maps to a real template in the
# "drafting" index. Tests natural user phrasing, not exact template names.

PROMPTS = [
    # -- Bail & Criminal --
    (1,
     "Draft an anticipatory bail application under Section 483 BNSS",
     "Criminal-Bail",
     ["bail", "anticipatory"],
     [],
     ),
    (2,
     "I need a criminal complaint format for hurt and assault under BNS",
     "Criminal-Complaint",
     ["complaint"],
     [],
     ),

    # -- Civil Suits --
    (3,
     "Draft a suit for recovery of possession and arrears of rent against a tenant who has not paid rent for 6 months",
     "Civil-Recovery",
     ["possession", "rent"],
     [],
     ),
    (4,
     "Prepare a suit for specific performance of contract for sale of immovable property",
     "Civil-SpecificPerformance",
     ["specific performance"],
     [],
     ),
    (5,
     "Draft a suit for partition of ancestral property between three brothers",
     "Civil-Partition",
     ["partition"],
     [],
     ),

    # -- Notices --
    (6,
     "Draft a legal notice for dishonour of cheque under Section 138 of Negotiable Instruments Act",
     "Notice-Cheque",
     ["notice", "cheque", "138"],
     [],
     ),
    (7,
     "Prepare a notice to landlord on behalf of tenant for illegal eviction and harassment",
     "Notice-Landlord",
     ["notice", "landlord"],
     [],
     ),
    (8,
     "Draft a notice claiming damages for breach of contract for supply of defective goods",
     "Notice-Breach",
     ["notice", "breach", "damages"],
     [],
     ),

    # -- Agreements & Deeds --
    (9,
     "Draft a leave and licence agreement for residential flat in Mumbai for 11 months",
     "Agreement-LeaveAndLicence",
     ["leave", "licence"],
     [],
     ),
    (10,
     "Prepare a general power of attorney for a person going abroad to authorize all acts on his behalf",
     "Agreement-POA",
     ["power of attorney"],
     [],
     ),

    # -- Family Law --
    (11,
     "Draft a petition for divorce under Section 13 of Hindu Marriage Act 1955 on the ground of cruelty",
     "Family-Divorce",
     ["divorce", "cruelty"],
     [],
     ),
    (12,
     "Prepare an application for maintenance under Section 125 CrPC on behalf of wife",
     "Family-Maintenance",
     ["maintenance"],
     [],
     ),

    # -- Wills & Succession --
    (13,
     "Draft a will under Indian Succession Act 1925 for a person who wants to bequeath property to his two sons and daughter equally",
     "Succession-Will",
     ["will"],
     [],
     ),

    # -- Applications & Miscellaneous --
    (14,
     "Draft an application for condonation of delay in filing an appeal in the High Court",
     "Application-Condonation",
     ["condonation", "delay"],
     [],
     ),
    (15,
     "Prepare a written statement on behalf of defendant in a suit for partition",
     "WrittenStatement-Partition",
     ["written statement", "defendant"],
     [],
     ),
]


# -- Validation -----------------------------------------------------------------

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

# Minimum response length for a real draft (multi-step pipeline generates 30+ pages)
MIN_DRAFT_LENGTH = 5000


def is_apologetic(text: str) -> bool:
    if not text or len(text.strip()) < 30:
        return True
    for pat in SORRY_PATTERNS:
        if re.search(pat, text[:500]):
            return True
    return False


def classify_result(resp_json: dict, must_contain: list[str],
                    must_not_contain: list[str]) -> tuple[str, list[str]]:
    """Classify result as PASS/WEAK/FAIL with reasons."""
    result_text = resp_json.get("result", "")
    agents_used = set(resp_json.get("agents_used", []))
    reasons = []

    # Must route to Drafting agent
    if "Drafting" not in agents_used:
        reasons.append(f"Wrong routing: expected Drafting, got {agents_used}")

    # Check response quality
    if is_apologetic(result_text):
        reasons.append("Response is apologetic/empty")

    # Check minimum length (drafts should be substantial)
    if len(result_text.strip()) < MIN_DRAFT_LENGTH and not is_apologetic(result_text):
        reasons.append(f"Draft too short: {len(result_text)} chars (min {MIN_DRAFT_LENGTH})")

    # Check must_contain keywords
    for keyword in must_contain:
        if keyword.lower() not in result_text.lower():
            reasons.append(f"Missing keyword: '{keyword}'")

    # Check must_not_contain
    for keyword in must_not_contain:
        if keyword.lower() in result_text.lower():
            reasons.append(f"Contains forbidden: '{keyword}'")

    if not reasons:
        return "PASS", []
    elif any("Wrong routing" in r for r in reasons) or any("apologetic" in r for r in reasons):
        return "FAIL", reasons
    else:
        return "WEAK", reasons


# -- Request Handling -----------------------------------------------------------

def send_request(prompt_tuple, api_url, timeout):
    """Send a single request and return the result dict."""
    idx, prompt, category, must_contain, must_not_contain = prompt_tuple
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
                "status": "ERROR",
                "agents_used": [], "response_len": 0, "elapsed": round(elapsed, 1),
                "tokens": 0, "full_response": "", "fail_reasons": [],
                "error_detail": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }

        data = resp.json()
        status, reasons = classify_result(data, must_contain, must_not_contain)
        return {
            "id": idx, "prompt": prompt, "category": category,
            "status": status,
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
            "status": "ERROR",
            "agents_used": [], "response_len": 0, "elapsed": round(timeout, 1),
            "tokens": 0, "full_response": "", "fail_reasons": [],
            "error_detail": "TIMEOUT",
        }
    except Exception as e:
        return {
            "id": idx, "prompt": prompt, "category": category,
            "status": "ERROR",
            "agents_used": [], "response_len": 0,
            "elapsed": round(time.time() - start, 1),
            "tokens": 0, "full_response": "", "fail_reasons": [],
            "error_detail": str(e)[:200],
        }


# -- Report Generation ----------------------------------------------------------

STATUS_COLORS = {
    "PASS": "\033[92m",
    "WEAK": "\033[93m",
    "FAIL": "\033[91m",
    "ERROR": "\033[91m",
}
RESET = "\033[0m"


def print_result(i, total, r):
    color = STATUS_COLORS.get(r["status"], "")
    agents_str = ",".join(r["agents_used"])[:30] if r["agents_used"] else "-"
    print(f"  [{i:2d}/{total}] #{r['id']:2d} [{r['category']:<30s}] "
          f"{color}{r['status']:<5s}{RESET} "
          f"agents=[{agents_str}] ({r['elapsed']}s)")
    if r["fail_reasons"]:
        for reason in r["fail_reasons"]:
            print(f"           -> {reason}")
    if r["error_detail"]:
        print(f"           -> {r['error_detail']}")


def generate_report(results: list[dict], elapsed: float, args) -> str:
    lines = [
        "# Drafting Agent Test Report",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**API:** `{args.api_url}`",
        f"**Timeout:** {args.timeout}s | **Concurrency:** {args.concurrency}",
        f"**Wall clock:** {elapsed:.0f}s",
        "",
    ]

    # Summary
    counts = {"PASS": 0, "WEAK": 0, "FAIL": 0, "ERROR": 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    total = len(results)
    lines.append(f"## Summary: {counts['PASS']}/{total} PASS, "
                 f"{counts['WEAK']} WEAK, {counts['FAIL']} FAIL, "
                 f"{counts['ERROR']} ERROR")
    lines.append("")

    # Detail table
    lines.append("## Results")
    lines.append("")
    lines.append("| # | Category | Status | Agents | Time | Len | Issue |")
    lines.append("|---|----------|--------|--------|------|-----|-------|")

    for r in results:
        agents = ", ".join(r["agents_used"]) if r["agents_used"] else "-"
        issue = "; ".join(r["fail_reasons"]) if r["fail_reasons"] else r.get("error_detail", "")
        lines.append(
            f"| {r['id']} | {r['category']} | **{r['status']}** | "
            f"{agents} | {r['elapsed']}s | {r['response_len']} | {issue[:80]} |"
        )

    lines.append("")

    # Timing stats
    times = [r["elapsed"] for r in results if r["status"] != "ERROR"]
    if times:
        lines.append("## Timing")
        lines.append(f"- **Avg:** {sum(times)/len(times):.1f}s")
        lines.append(f"- **Min:** {min(times):.1f}s")
        lines.append(f"- **Max:** {max(times):.1f}s")
        lines.append("")

    return "\n".join(lines)


# -- Main -----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Drafting Agent Advanced Test Suite")
    parser.add_argument("--api-url", default="http://localhost:5055/pyapi/search",
                        help="API endpoint")
    parser.add_argument("--timeout", type=int, default=300,
                        help="Per-request timeout in seconds")
    parser.add_argument("--concurrency", type=int, default=3,
                        help="Max concurrent requests")
    args = parser.parse_args()

    total = len(PROMPTS)
    print("=" * 80)
    print(f"  Drafting Agent Test Suite")
    print(f"  {total} prompts  |  API: {args.api_url}  |  Concurrency: {args.concurrency}")
    print("=" * 80)
    print()

    # Health check
    health_url = args.api_url.rsplit("/", 1)[0] + "/health"
    try:
        h = requests.get(health_url, timeout=5)
        if h.status_code != 200:
            print(f"  WARNING: Health check returned {h.status_code}")
    except Exception as e:
        print(f"  WARNING: Health check failed: {e}")
        print(f"  Proceeding anyway...")
    print()

    # Run tests
    results = []
    wall_start = time.time()

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {
            pool.submit(send_request, p, args.api_url, args.timeout): p
            for p in PROMPTS
        }
        i = 0
        for future in as_completed(futures):
            i += 1
            r = future.result()
            results.append(r)
            print_result(i, total, r)

    wall_elapsed = time.time() - wall_start

    # Sort by ID
    results.sort(key=lambda r: r["id"])

    # Summary
    counts = {"PASS": 0, "WEAK": 0, "FAIL": 0, "ERROR": 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    print()
    print(f"  Result: {counts['PASS']}/{total} PASS, "
          f"{counts['WEAK']} WEAK, {counts['FAIL']} FAIL, "
          f"{counts['ERROR']} ERROR")
    print(f"  Wall clock: {wall_elapsed:.0f}s")
    print()

    # Save outputs
    out_dir = os.path.dirname(os.path.abspath(__file__))

    json_path = os.path.join(out_dir, "test_results_drafting.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": counts,
            "total_elapsed": wall_elapsed,
            "concurrency": args.concurrency,
            "details": results,
        }, f, indent=2, ensure_ascii=False)

    md_path = os.path.join(out_dir, "test_report_drafting.md")
    report = generate_report(results, wall_elapsed, args)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"  Markdown report: {os.path.abspath(md_path)}")
    print(f"  JSON results:    {os.path.abspath(json_path)}")
    print()

    # Exit code
    sys.exit(0 if counts["FAIL"] == 0 and counts["ERROR"] == 0 else 1)


if __name__ == "__main__":
    main()
