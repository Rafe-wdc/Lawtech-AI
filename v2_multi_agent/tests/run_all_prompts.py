"""Run all 27 test prompts against the live API and generate a markdown report.

Usage:
    python tests/run_all_prompts.py

Requires the server to be running on localhost:5000.
Outputs: tests/test_report.md  (full markdown report)
         tests/test_results.json (machine-readable results)
"""

import json
import re
import sys
import time
from datetime import datetime

import requests

API_URL = "http://localhost:5000/pyapi/search"
TIMEOUT = 120  # seconds per request

# (id, prompt, category, expected_agent)
# expected_agent=None means we don't check agent routing
PROMPTS = [
    # --- Newacts: Single Section ---
    (1,  "Section 302 of IPC", "Newacts-Single", "Newacts"),
    (2,  "Section 35 of BNS", "Newacts-Single", "Newacts"),
    (3,  "What is Section 438 of CrPC?", "Newacts-Single", "Newacts"),
    # --- Newacts: Multi Section ---
    (4,  "Sections 302 and 307 of IPC", "Newacts-Multi", "Newacts"),
    (5,  "Explain Sections 64, 65 and 66 of BSA", "Newacts-Multi", "Newacts"),
    (6,  "Compare Section 154 and Section 161 of CrPC", "Newacts-Multi", "Newacts"),
    # --- Newacts: Topic ---
    (7,  "punishment for theft in BNS", "Newacts-Topic", "Newacts"),
    (8,  "bail provisions under BNSS", "Newacts-Topic", "Newacts"),
    (9,  "electronic evidence rules in BSA", "Newacts-Topic", "Newacts"),
    # --- Newacts: Nearby / Subsection ---
    (10, "What does Section 100 of BNS say?", "Newacts-Nearby", "Newacts"),
    (11, "Section 528 of BNSS", "Newacts-Nearby", "Newacts"),
    (12, "Section 3(5) of Bharatiya Nyaya Sanhita", "Newacts-Subsection", "Newacts"),
    (13, "IEA Section 65b", "Newacts-Subsection", "Newacts"),
    # --- Newacts: Mapping ---
    (14, "What is the equivalent of Section 498a of IPC in BNS?", "Newacts-Mapping", "Newacts"),
    (15, "Section 125 CrPC new law equivalent", "Newacts-Mapping", "Newacts"),
    # --- Legislation: Single ---
    (16, "Section 138 of Negotiable Instruments Act", "Legislation-Single", "Legislation"),
    (17, "Section 9 of Arbitration Act", "Legislation-Single", "Legislation"),
    # --- Legislation: Multi ---
    (18, "Sections 44 and 45 of Transfer of Property Act", "Legislation-Multi", "Legislation"),
    (19, "Explain Sections 3, 4 and 5 of Consumer Protection Act", "Legislation-Multi", "Legislation"),
    # --- Legislation: Range ---
    (20, "Sections 10 to 15 of Companies Act", "Legislation-Range", "Legislation"),
    # --- Legislation: Topic ---
    (21, "director duties under companies act", "Legislation-Topic", "Legislation"),
    (22, "tenant rights in rent control legislation", "Legislation-Topic", "Legislation"),
    # --- Legislation: Subsection ---
    (23, "Section 138(1) of Negotiable Instruments Act", "Legislation-Subsection", "Legislation"),
    # --- Edge Cases ---
    (24, "What comes after section 35 of BNS?", "Edge-Nearby", "Newacts"),
    (25, "theft", "Edge-Vague", None),
    (26, "Rule 3 of Maharashtra Rent Control Rules", "Edge-NonSection", "Legislation"),
    (27, "Sections 302, 304, 304a, 307 and 376 of IPC", "Edge-ManySection", "Newacts"),
]

# Keywords that indicate an apologetic / empty response
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


def classify_result(resp_json: dict, expected_agent: str | None) -> str:
    result_text = resp_json.get("result", "")
    agents_used = resp_json.get("agents_used", [])
    if expected_agent and expected_agent not in agents_used:
        return "FAIL"
    if is_apologetic(result_text):
        return "WEAK"
    return "PASS"


def generate_markdown(details: list[dict], summary: dict, total_elapsed: float) -> str:
    """Generate a full markdown report from test results."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(details)

    lines = []
    lines.append(f"# Test Report — {now}")
    lines.append("")
    lines.append(f"**Server:** `{API_URL}`  ")
    lines.append(f"**Total Prompts:** {total}  ")
    lines.append(f"**Total Time:** {total_elapsed:.1f}s  ")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Status | Count |")
    lines.append(f"|--------|-------|")
    for status in ["PASS", "WEAK", "FAIL", "ERROR"]:
        count = summary.get(status, 0)
        lines.append(f"| {status} | {count} |")
    lines.append("")

    # Timing stats
    times = [d["elapsed"] for d in details if d["elapsed"] > 0]
    if times:
        lines.append("## Timing")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Avg | {sum(times)/len(times):.1f}s |")
        lines.append(f"| Min | {min(times):.1f}s |")
        lines.append(f"| Max | {max(times):.1f}s |")
        lines.append(f"| Median | {sorted(times)[len(times)//2]:.1f}s |")
        lines.append("")

    # Quick results table
    lines.append("## Results Overview")
    lines.append("")
    lines.append("| # | Status | Category | Prompt | Agent(s) | Time | Resp Len |")
    lines.append("|---|--------|----------|--------|----------|------|----------|")
    for d in details:
        status_icon = {"PASS": "PASS", "WEAK": "WEAK", "FAIL": "FAIL", "ERROR": "ERROR"}[d["status"]]
        agents_str = ", ".join(d["agents_used"]) if d["agents_used"] else "—"
        prompt_short = d["prompt"][:50] + ("..." if len(d["prompt"]) > 50 else "")
        lines.append(
            f"| {d['id']} | {status_icon} | {d['category']} | {prompt_short} "
            f"| {agents_str} | {d['elapsed']:.1f}s | {d['response_len']} |"
        )
    lines.append("")

    # Detailed per-prompt sections
    lines.append("---")
    lines.append("")
    lines.append("## Detailed Results")
    lines.append("")

    for d in details:
        status_icon = {"PASS": "PASS", "WEAK": "WEAK", "FAIL": "FAIL", "ERROR": "ERROR"}[d["status"]]
        lines.append(f"### #{d['id']} — {status_icon} — {d['category']}")
        lines.append("")
        lines.append(f"**Query:** `{d['prompt']}`  ")
        lines.append(f"**Expected Agent:** {d['expected_agent'] or 'Any'}  ")
        lines.append(f"**Actual Agent(s):** {', '.join(d['agents_used']) if d['agents_used'] else '—'}  ")
        lines.append(f"**Time:** {d['elapsed']:.1f}s  ")
        lines.append(f"**Response Length:** {d['response_len']} chars  ")
        if d.get("tokens"):
            lines.append(f"**Tokens:** {d['tokens']}  ")
        lines.append("")

        if d.get("full_response"):
            lines.append("<details>")
            lines.append("<summary>Full Response</summary>")
            lines.append("")
            lines.append(d["full_response"])
            lines.append("")
            lines.append("</details>")
            lines.append("")
        elif d.get("error_detail"):
            lines.append(f"**Error:** `{d['error_detail']}`")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def main():
    print(f"{'='*80}")
    print(f"  Running {len(PROMPTS)} test prompts against {API_URL}")
    print(f"{'='*80}\n")

    results = {"PASS": 0, "WEAK": 0, "FAIL": 0, "ERROR": 0}
    details = []
    total_start = time.time()

    for idx, prompt, category, expected_agent in PROMPTS:
        sys.stdout.write(f"  #{idx:2d} [{category:20s}] {prompt[:55]:55s} ... ")
        sys.stdout.flush()

        start = time.time()
        try:
            resp = requests.post(
                API_URL,
                json={"Promptquery": prompt},
                timeout=TIMEOUT,
            )
            elapsed = time.time() - start

            if resp.status_code != 200:
                status = "ERROR"
                agents = []
                result_text = ""
                tokens = 0
                error_detail = f"HTTP {resp.status_code}: {resp.text[:200]}"
            else:
                data = resp.json()
                status = classify_result(data, expected_agent)
                agents = data.get("agents_used", [])
                result_text = data.get("result", "")
                tokens = data.get("total_tokens_consumed", 0)
                error_detail = ""

        except requests.exceptions.Timeout:
            elapsed = TIMEOUT
            status = "ERROR"
            agents = []
            result_text = ""
            tokens = 0
            error_detail = "TIMEOUT"
        except Exception as e:
            elapsed = time.time() - start
            status = "ERROR"
            agents = []
            result_text = ""
            tokens = 0
            error_detail = str(e)[:200]

        results[status] += 1
        details.append({
            "id": idx,
            "prompt": prompt,
            "category": category,
            "expected_agent": expected_agent,
            "status": status,
            "agents_used": agents,
            "response_len": len(result_text),
            "elapsed": round(elapsed, 1),
            "tokens": tokens,
            "full_response": result_text,
            "error_detail": error_detail,
        })

        color = {"PASS": "\033[92m", "WEAK": "\033[93m", "FAIL": "\033[91m", "ERROR": "\033[91m"}
        reset = "\033[0m"
        print(f"{color.get(status, '')}{status:5s}{reset}  ({elapsed:.1f}s)")

    total_elapsed = time.time() - total_start

    # Summary
    total = len(PROMPTS)
    print(f"\n{'='*80}")
    print(f"  RESULTS: {results['PASS']}/{total} PASS, "
          f"{results['WEAK']} WEAK, {results['FAIL']} FAIL, {results['ERROR']} ERROR")
    print(f"  Total time: {total_elapsed:.1f}s")
    print(f"{'='*80}")

    # Non-PASS details
    non_pass = [d for d in details if d["status"] != "PASS"]
    if non_pass:
        print(f"\n--- Non-PASS Details ---")
        for d in non_pass:
            print(f"\n  #{d['id']} [{d['status']}] {d['prompt']}")
            print(f"    Agents: {d['agents_used']}, Response len: {d['response_len']}")
            if d["full_response"]:
                preview = d["full_response"].replace("\n", " ")[:150]
                print(f"    Preview: {preview}...")
    else:
        print("\n  All prompts passed!")

    # Generate markdown report
    md_content = generate_markdown(details, results, total_elapsed)
    md_path = "tests/test_report.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"\n  Markdown report saved to {md_path}")

    # Save JSON results
    json_details = []
    for d in details:
        jd = dict(d)
        # Truncate full_response in JSON to keep file manageable
        if len(jd.get("full_response", "")) > 500:
            jd["response_preview"] = jd["full_response"][:500] + "..."
        else:
            jd["response_preview"] = jd["full_response"]
        del jd["full_response"]
        json_details.append(jd)

    json_path = "tests/test_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"summary": results, "total_elapsed": total_elapsed, "details": json_details}, f, indent=2)
    print(f"  JSON results saved to {json_path}")

    return 0 if results["FAIL"] == 0 and results["ERROR"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
