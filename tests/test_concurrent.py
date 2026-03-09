"""Run all 70 test prompts CONCURRENTLY against the live API.

Usage:
    python tests/test_concurrent.py [--concurrency N] [--timeout N] [--api-url URL]

Defaults:
    --concurrency 6
    --api-url http://localhost:5050/pyapi/search
    --timeout 180
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

# ── Test Prompts ─────────────────────────────────────────────────────────────

PROMPTS = [
    # ── Newacts: Single Section (1-3) ────────────────────────────────────────
    (1,  "Section 302 of IPC", "Newacts-Single", "Newacts"),
    (2,  "Section 35 of BNS", "Newacts-Single", "Newacts"),
    (3,  "What is Section 438 of CrPC?", "Newacts-Single", "Newacts"),

    # ── Newacts: Multi Section (4-6) ─────────────────────────────────────────
    (4,  "Sections 302 and 307 of IPC", "Newacts-Multi", "Newacts"),
    (5,  "Explain Sections 64, 65 and 66 of BSA", "Newacts-Multi", "Newacts"),
    (6,  "Compare Section 154 and Section 161 of CrPC", "Newacts-Multi", "Newacts"),

    # ── Newacts: Topic Search (7-9) ──────────────────────────────────────────
    (7,  "punishment for theft in BNS", "Newacts-Topic", "Newacts"),
    (8,  "bail provisions under BNSS", "Newacts-Topic", "Newacts"),
    (9,  "electronic evidence rules in BSA", "Newacts-Topic", "Newacts"),

    # ── Newacts: Nearby Sections (10-11) ─────────────────────────────────────
    (10, "What does Section 100 of BNS say?", "Newacts-Nearby", "Newacts"),
    (11, "Section 528 of BNSS", "Newacts-Nearby", "Newacts"),

    # ── Newacts: Subsection (12-13) ──────────────────────────────────────────
    (12, "Section 3(5) of Bharatiya Nyaya Sanhita", "Newacts-Subsection", "Newacts"),
    (13, "IEA Section 65b", "Newacts-Subsection", "Newacts"),

    # ── Newacts: Old↔New Mapping (14-15) ─────────────────────────────────────
    (14, "What is the equivalent of Section 498a of IPC in BNS?", "Newacts-Mapping", "Newacts"),
    (15, "Section 125 CrPC new law equivalent", "Newacts-Mapping", "Newacts"),

    # ── Legislation: Single Section (16-18) ──────────────────────────────────
    (16, "Section 138 of Negotiable Instruments Act", "Legislation-Single", "Legislation"),
    (17, "Section 9 of Arbitration Act", "Legislation-Single", "Legislation"),
    (18, "Section 23 of Indian Contract Act", "Legislation-Single", "Legislation"),

    # ── Legislation: Multi Section (19-20) ───────────────────────────────────
    (19, "Sections 44 and 45 of Transfer of Property Act", "Legislation-Multi", "Legislation"),
    (20, "Explain Sections 3, 4 and 5 of Consumer Protection Act", "Legislation-Multi", "Legislation"),

    # ── Legislation: Range (21) ──────────────────────────────────────────────
    (21, "Sections 10 to 15 of Companies Act", "Legislation-Range", "Legislation"),

    # ── Legislation: Topic Search (22-24) ────────────────────────────────────
    (22, "director duties under companies act", "Legislation-Topic", "Legislation"),
    (23, "tenant rights in rent control legislation", "Legislation-Topic", "Legislation"),
    (24, "minimum wages provisions in labour law", "Legislation-Topic", "Legislation"),

    # ── Legislation: Subsection (25) ─────────────────────────────────────────
    (25, "Section 138(1) of Negotiable Instruments Act", "Legislation-Subsection", "Legislation"),

    # ── Judgment: Case Search (26-31) ────────────────────────────────────────
    (26, "cases on anticipatory bail", "Judgment-Topic", "Judgment"),
    (27, "dowry harassment case law", "Judgment-Topic", "Judgment"),
    (28, "cheque bounce cases under Section 138", "Judgment-Topic", "Judgment"),
    (29, "property dispute judgments", "Judgment-Topic", "Judgment"),
    (30, "cases on medical negligence", "Judgment-Topic", "Judgment"),
    (31, "land acquisition compensation judgments", "Judgment-Topic", "Judgment"),

    # ── Supreme Court Judgments (32-37) ──────────────────────────────────────
    (32, "Supreme Court cases on right to privacy", "SCI-Topic", "SCI_Judgment"),
    (33, "SC judgment on Article 21 right to life", "SCI-Topic", "SCI_Judgment"),
    (34, "Supreme Court ruling on triple talaq", "SCI-Topic", "SCI_Judgment"),
    (35, "SC cases on bail conditions", "SCI-Topic", "SCI_Judgment"),
    (36, "Supreme Court judgments on environmental protection", "SCI-Topic", "SCI_Judgment"),
    (37, "SC precedents on freedom of speech Article 19", "SCI-Topic", "SCI_Judgment"),

    # ── Constitution (38-43) ─────────────────────────────────────────────────
    (38, "Article 21 of Indian Constitution", "Constitution", "Constitution"),
    (39, "Fundamental rights under Part III of Constitution", "Constitution", "Constitution"),
    (40, "Article 14 right to equality", "Constitution", "Constitution"),
    (41, "What are fundamental duties under Article 51A?", "Constitution", "Constitution"),
    (42, "Directive principles of state policy", "Constitution", "Constitution"),
    (43, "Article 32 writ jurisdiction of Supreme Court", "Constitution", "Constitution"),

    # ── Legal Maxims (44-49) ─────────────────────────────────────────────────
    (44, "What is audi alteram partem?", "Maxim", "Maxim"),
    (45, "Explain the doctrine of res judicata", "Maxim", "Maxim"),
    (46, "Meaning of caveat emptor in law", "Maxim", "Maxim"),
    (47, "What is estoppel in legal terms?", "Maxim", "Maxim"),
    (48, "Doctrine of ultra vires", "Maxim", "Maxim"),
    (49, "Explain nemo judex in causa sua", "Maxim", "Maxim"),

    # ── Drafting (50-55) ─────────────────────────────────────────────────────
    (50, "Draft a bail application", "Drafting", "Drafting"),
    (51, "Draft a legal notice for recovery of money", "Drafting", "Drafting"),
    (52, "Create a power of attorney document", "Drafting", "Drafting"),
    (53, "Draft an eviction notice to tenant", "Drafting", "Drafting"),
    (54, "Draft a complaint for cheating and fraud", "Drafting", "Drafting"),
    (55, "Draft a rental agreement", "Drafting", "Drafting"),

    # ── Scenario / Web-Grounded (56-60) ──────────────────────────────────────
    (56, "My landlord is refusing to return my security deposit of 50000 rupees after I vacated the flat. What legal steps can I take?", "Scenario", "Scenario"),
    (57, "My employer terminated me without any notice or severance pay. What are my rights under Indian labour laws?", "Scenario", "Scenario"),
    (58, "I received a legal notice for defamation on social media. How should I respond and what are the possible consequences?",  "Scenario", "Scenario"),
    (59, "Can police arrest someone without an FIR? What are the rights of an arrested person in India?", "Scenario", "Scenario"),
    (60, "What is the procedure to file a consumer complaint online in India and what compensation can I claim?", "Scenario", "Scenario"),

    # ── Edge Cases (61-65) ───────────────────────────────────────────────────
    (61, "What comes after section 35 of BNS?", "Edge-Nearby", "Newacts"),
    (62, "theft", "Edge-Vague", None),
    (63, "Rule 3 of Maharashtra Rent Control Rules", "Edge-NonSection", "Legislation"),
    (64, "Sections 302, 304, 304a, 307 and 376 of IPC", "Edge-ManySection", "Newacts"),
    (65, "define murder", "Edge-Concept", None),

    # ── Cross-Domain / Mixed (66-70) ─────────────────────────────────────────
    (66, "Section 420 IPC punishment for cheating", "Newacts-Single", "Newacts"),
    (67, "What is Section 34 of Indian Contract Act?", "Legislation-Single", "Legislation"),
    (68, "Article 19(1)(a) freedom of expression", "Constitution", "Constitution"),
    (69, "habeas corpus meaning and legal significance", "Maxim", "Maxim"),
    (70, "Supreme Court landmark judgment on Aadhaar privacy", "SCI-Landmark", "SCI_Judgment"),
]

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


def is_apologetic(text: str) -> bool:
    if not text or len(text.strip()) < 30:
        return True
    for pat in SORRY_PATTERNS:
        if re.search(pat, text[:500]):
            return True
    return False


# Agents the orchestrator may reasonably swap between
_AGENT_ALIASES = {
    "Maxim": {"Maxim", "Legal_Concepts", "Constitution"},
    "Constitution": {"Constitution", "Legal_Concepts"},
    "Legal_Concepts": {"Legal_Concepts", "Maxim", "Constitution"},
}


def classify_result(resp_json: dict, expected_agent: str | None) -> str:
    result_text = resp_json.get("result", "")
    agents_used = resp_json.get("agents_used", [])
    if expected_agent:
        acceptable = _AGENT_ALIASES.get(expected_agent, {expected_agent})
        if not acceptable.intersection(agents_used):
            return "FAIL"
    if is_apologetic(result_text):
        return "WEAK"
    return "PASS"


def send_request(prompt_tuple, api_url, timeout):
    """Send a single request and return the result dict."""
    idx, prompt, category, expected_agent = prompt_tuple
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
                "expected_agent": expected_agent, "status": "ERROR",
                "agents_used": [], "response_len": 0, "elapsed": round(elapsed, 1),
                "tokens": 0, "full_response": "",
                "error_detail": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }

        data = resp.json()
        status = classify_result(data, expected_agent)
        return {
            "id": idx, "prompt": prompt, "category": category,
            "expected_agent": expected_agent, "status": status,
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
            "expected_agent": expected_agent, "status": "ERROR",
            "agents_used": [], "response_len": 0, "elapsed": round(timeout, 1),
            "tokens": 0, "full_response": "", "error_detail": "TIMEOUT",
        }
    except Exception as e:
        return {
            "id": idx, "prompt": prompt, "category": category,
            "expected_agent": expected_agent, "status": "ERROR",
            "agents_used": [], "response_len": 0,
            "elapsed": round(time.time() - start, 1),
            "tokens": 0, "full_response": "", "error_detail": str(e)[:200],
        }


def generate_markdown(details, summary, total_elapsed, concurrency):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(details)
    details_sorted = sorted(details, key=lambda d: d["id"])

    lines = [
        f"# Concurrent Test Report — {now}",
        "",
        f"**Concurrency:** {concurrency} parallel requests  ",
        f"**Total Prompts:** {total}  ",
        f"**Wall Clock Time:** {total_elapsed:.1f}s  ",
        "",
        "## Summary",
        "",
        "| Status | Count |",
        "|--------|-------|",
    ]
    for status in ["PASS", "WEAK", "FAIL", "ERROR"]:
        lines.append(f"| {status} | {summary.get(status, 0)} |")
    lines.append("")

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

    lines.extend([
        "## Results Overview",
        "",
        "| # | Status | Category | Prompt | Agent(s) | Time | Resp Len |",
        "|---|--------|----------|--------|----------|------|----------|",
    ])
    for d in details_sorted:
        agents_str = ", ".join(d["agents_used"]) if d["agents_used"] else "—"
        prompt_short = d["prompt"][:50] + ("..." if len(d["prompt"]) > 50 else "")
        lines.append(
            f"| {d['id']} | {d['status']} | {d['category']} | {prompt_short} "
            f"| {agents_str} | {d['elapsed']:.1f}s | {d['response_len']} |"
        )
    lines.extend(["", "---", "", "## Detailed Results", ""])

    for d in details_sorted:
        lines.extend([
            f"### #{d['id']} — {d['status']} — {d['category']}",
            "",
            f"**Query:** `{d['prompt']}`  ",
            f"**Expected Agent:** {d['expected_agent'] or 'Any'}  ",
            f"**Actual Agent(s):** {', '.join(d['agents_used']) if d['agents_used'] else '—'}  ",
            f"**Time:** {d['elapsed']:.1f}s  ",
            f"**Response Length:** {d['response_len']} chars  ",
        ])
        if d.get("tokens"):
            lines.append(f"**Tokens:** {d['tokens']}  ")
        lines.append("")

        if d.get("full_response"):
            lines.extend([
                "<details>",
                "<summary>Full Response</summary>",
                "",
                d["full_response"],
                "",
                "</details>",
                "",
            ])
        elif d.get("error_detail"):
            lines.append(f"**Error:** `{d['error_detail']}`")
            lines.append("")
        lines.extend(["---", ""])

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Concurrent test runner")
    parser.add_argument("--concurrency", type=int, default=6, help="Max parallel requests")
    parser.add_argument("--api-url", default="http://localhost:5050/pyapi/search", help="API endpoint")
    parser.add_argument("--timeout", type=int, default=180, help="Per-request timeout in seconds")
    args = parser.parse_args()

    print(f"{'='*80}")
    print(f"  Running {len(PROMPTS)} prompts with concurrency={args.concurrency}")
    print(f"  API: {args.api_url}  |  Timeout: {args.timeout}s")
    print(f"{'='*80}\n")

    results = {"PASS": 0, "WEAK": 0, "FAIL": 0, "ERROR": 0}
    details = []
    completed = 0
    total = len(PROMPTS)

    total_start = time.time()

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

            color = {"PASS": "\033[92m", "WEAK": "\033[93m", "FAIL": "\033[91m", "ERROR": "\033[91m"}
            reset = "\033[0m"
            print(
                f"  [{completed:2d}/{total}] #{result['id']:2d} "
                f"[{result['category']:20s}] "
                f"{result['prompt'][:45]:45s} "
                f"{color.get(result['status'], '')}{result['status']:5s}{reset}  "
                f"({result['elapsed']:.1f}s)"
            )
            sys.stdout.flush()

    total_elapsed = time.time() - total_start

    # Summary
    print(f"\n{'='*80}")
    print(f"  RESULTS: {results['PASS']}/{total} PASS, "
          f"{results['WEAK']} WEAK, {results['FAIL']} FAIL, {results['ERROR']} ERROR")
    print(f"  Wall clock: {total_elapsed:.1f}s  |  Concurrency: {args.concurrency}")
    times = [d["elapsed"] for d in details if d["status"] != "ERROR"]
    if times:
        print(f"  Avg latency: {sum(times)/len(times):.1f}s  |  Speedup: {sum(times)/total_elapsed:.1f}x")
    print(f"{'='*80}")

    # Non-PASS details
    non_pass = [d for d in sorted(details, key=lambda x: x["id"]) if d["status"] != "PASS"]
    if non_pass:
        print(f"\n--- Non-PASS Details ---")
        for d in non_pass:
            print(f"\n  #{d['id']} [{d['status']}] {d['prompt']}")
            print(f"    Agents: {d['agents_used']}, Response len: {d['response_len']}")
            if d.get("error_detail"):
                print(f"    Error: {d['error_detail']}")
            elif d["full_response"]:
                preview = d["full_response"].replace("\n", " ")[:150]
                print(f"    Preview: {preview}...")
    else:
        print("\n  All prompts passed!")

    # Generate reports
    script_dir = os.path.dirname(os.path.abspath(__file__))

    md_content = generate_markdown(details, results, total_elapsed, args.concurrency)
    md_path = os.path.join(script_dir, "test_report_concurrent.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"\n  Markdown report: {md_path}")

    json_details = []
    for d in sorted(details, key=lambda x: x["id"]):
        jd = dict(d)
        if len(jd.get("full_response", "")) > 500:
            jd["response_preview"] = jd["full_response"][:500] + "..."
        else:
            jd["response_preview"] = jd["full_response"]
        del jd["full_response"]
        json_details.append(jd)

    json_path = os.path.join(script_dir, "test_results_concurrent.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": results,
            "total_elapsed": total_elapsed,
            "concurrency": args.concurrency,
            "details": json_details,
        }, f, indent=2)
    print(f"  JSON results:   {json_path}")

    # Save full-response JSON for AI evaluator (no truncation)
    full_json_path = os.path.join(script_dir, "test_results_full.json")
    with open(full_json_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": results,
            "total_elapsed": total_elapsed,
            "concurrency": args.concurrency,
            "details": sorted(details, key=lambda x: x["id"]),
        }, f, indent=2)
    print(f"  Full results:   {full_json_path}")

    return 0 if results["FAIL"] == 0 and results["ERROR"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
