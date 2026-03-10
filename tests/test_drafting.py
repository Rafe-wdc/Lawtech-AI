"""Drafting agent test suite -- 10 prompts targeting the full drafting pipeline.

Tests template search (hybrid BM25+kNN), template selection, outline generation,
parallel section generation, assembly, and citation enrichment.

Covers: bail applications, legal notices, agreements, petitions, suits,
        writ petitions, complaints, appeals, deeds, and affidavits.

Usage:
    python tests/test_drafting.py [--concurrency N] [--timeout N] [--api-url URL]
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import argparse
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import OpenAI

# Load .env from project root so OPENAI_API_KEY is available for AI eval
_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env")

# ============================================================================
#  10 DRAFTING PROMPTS
#  Format: (id, prompt, doc_type, quality_checks)
#  quality_checks: dict with optional keys:
#    - must_contain: list of strings that MUST appear in the response
#    - min_sections: minimum number of ## headings expected
#    - min_length: minimum character length of response
# ============================================================================

DRAFTING_PROMPTS = [
    # 1. Bail Application (most common)
    (1,
     "Draft an anticipatory bail application under Section 483 BNSS for a person accused of cheating under Section 318 BNS",
     "Bail Application",
     {
         "must_contain": ["bail", "483", "318"],
         "min_sections": 3,
         "min_length": 2000,
     }),

    # 2. Legal Notice (short document)
    (2,
     "Draft a legal notice under Section 138 of the Negotiable Instruments Act for cheque dishonour of Rs. 5,00,000",
     "Legal Notice",
     {
         "must_contain": ["138", "negotiable", "cheque"],
         "min_sections": 2,
         "min_length": 1000,
     }),

    # 3. Agreement for Sale
    (3,
     "Draft an agreement for sale of a residential flat in Mumbai for Rs. 1.5 Crore under the Transfer of Property Act",
     "Agreement for Sale",
     {
         "must_contain": ["agreement", "sale", "property"],
         "min_sections": 3,
         "min_length": 2000,
     }),

    # 4. Divorce Petition
    (4,
     "Draft a petition for divorce on the ground of cruelty under Section 13(1)(ia) of the Hindu Marriage Act 1955",
     "Divorce Petition",
     {
         "must_contain": ["divorce", "cruelty", "13"],
         "min_sections": 3,
         "min_length": 2000,
     }),

    # 5. Writ Petition (Mandamus)
    (5,
     "Draft a writ petition under Article 226 of the Constitution for mandamus directing the municipal corporation to provide water supply",
     "Writ Petition",
     {
         "must_contain": ["226", "mandamus"],
         "min_sections": 3,
         "min_length": 2000,
     }),

    # 6. Consumer Complaint
    (6,
     "Draft a consumer complaint for defective goods under the Consumer Protection Act 2019 for a defective washing machine worth Rs. 45,000",
     "Consumer Complaint",
     {
         "must_contain": ["consumer", "defect"],
         "min_sections": 2,
         "min_length": 1500,
     }),

    # 7. Criminal Appeal
    (7,
     "Draft a criminal appeal under Section 374 CrPC against conviction under Section 304 IPC for culpable homicide",
     "Criminal Appeal",
     {
         "must_contain": ["appeal", "conviction"],
         "min_sections": 3,
         "min_length": 2000,
     }),

    # 8. Rent Agreement
    (8,
     "Draft a rent agreement for a commercial shop in Delhi for 3 years at Rs. 50,000 per month with annual escalation clause",
     "Rent Agreement",
     {
         "must_contain": ["rent", "agreement", "tenant"],
         "min_sections": 3,
         "min_length": 1500,
     }),

    # 9. Power of Attorney
    (9,
     "Draft a general power of attorney for managing property affairs including sale, lease, and mortgage of immovable property",
     "Power of Attorney",
     {
         "must_contain": ["power of attorney", "property"],
         "min_sections": 2,
         "min_length": 1000,
     }),

    # 10. Suit for Recovery of Money
    (10,
     "Draft a civil suit for recovery of Rs. 10,00,000 with interest under Order 37 of CPC as a summary suit",
     "Recovery Suit",
     {
         "must_contain": ["recovery", "suit"],
         "min_sections": 3,
         "min_length": 2000,
     }),
]


# ============================================================================
#  EVALUATION
# ============================================================================

SORRY_PATTERNS = [
    r"(?i)i('m| am) sorry",
    r"(?i)i can('|no)t (help|assist|provide|do that|generate|create|draft)",
    r"(?i)i('m| am) unable to",
    r"(?i)i('m| am) not able to",
    r"(?i)as an ai",
    r"(?i)i don'?t have (access|the ability)",
]


def _detect_repeated_points(text: str) -> list[str]:
    """Detect repeated/duplicate points across the document.

    Extracts numbered points and paragraph openings, then checks for
    near-duplicate content using simple similarity.
    """
    duplicates = []

    # Extract numbered points (1. xxx, 2. xxx, etc.) and paragraph openers
    points = re.findall(r"(?:^\d+[\.\)]\s*(.{30,200}))", text, re.MULTILINE)
    # Also extract sentences starting with "That " (common in legal drafts)
    that_clauses = re.findall(r"(?:^That\s+(.{30,200}))", text, re.MULTILINE)
    all_points = points + that_clauses

    # Compare each pair for near-duplication
    seen = []
    for p in all_points:
        p_clean = re.sub(r"\s+", " ", p.lower().strip())
        p_words = set(p_clean.split())
        for prev, prev_words in seen:
            if len(p_words) < 4:
                continue
            overlap = len(p_words & prev_words) / max(len(p_words | prev_words), 1)
            if overlap > 0.70:
                duplicates.append(
                    f"Repeated point: '{p[:80]}...' ~= '{prev[:80]}...'"
                )
                break
        seen.append((p_clean, p_words))

    return duplicates[:5]  # Cap at 5


def _detect_unnecessary_stretch(text: str) -> list[str]:
    """Detect filler/padding content that artificially inflates document length."""
    stretch_issues = []

    # Detect repetitive filler phrases
    filler_patterns = [
        (r"(it is (humbly |most |respectfully )?submitted that)", "filler: 'it is submitted that'"),
        (r"(the hon'?ble court may be pleased to note)", "filler: 'hon'ble court may be pleased'"),
        (r"(in the interest of justice)", "filler: 'in the interest of justice'"),
        (r"(under the facts and circumstances)", "filler: 'under facts and circumstances'"),
    ]

    for pattern, label in filler_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if len(matches) > 5:
            stretch_issues.append(
                f"Overused phrase ({len(matches)}x): {label}"
            )

    # Detect excessively long paragraphs (>1000 chars with no structure)
    paragraphs = text.split("\n\n")
    long_paras = [p for p in paragraphs if len(p) > 1500 and "\n" not in p.strip()]
    if long_paras:
        stretch_issues.append(
            f"{len(long_paras)} paragraph(s) exceed 1500 chars without sub-structure"
        )

    return stretch_issues[:5]


def _check_format_alignment(text: str) -> list[str]:
    """Check formatting consistency and proper markdown structure."""
    format_issues = []

    # Check heading hierarchy (should go # -> ## -> ###, not skip levels)
    headings = re.findall(r"^(#{1,4})\s", text, re.MULTILINE)
    heading_levels = [len(h) for h in headings]
    for i in range(1, len(heading_levels)):
        if heading_levels[i] > heading_levels[i - 1] + 1:
            format_issues.append(
                f"Heading level skipped: h{heading_levels[i-1]} -> h{heading_levels[i]} "
                f"(heading #{i+1})"
            )

    # Check for broken markdown (unclosed bold, mixed formatting)
    bold_opens = len(re.findall(r"\*\*(?!\*)", text))
    if bold_opens % 2 != 0:
        format_issues.append(f"Unclosed bold markers: {bold_opens} '**' found (odd count)")

    # Check for inconsistent numbering (1. 2. 3. 5. -- skipped 4)
    numbered = re.findall(r"^(\d+)\.\s", text, re.MULTILINE)
    if len(numbered) > 3:
        nums = [int(n) for n in numbered]
        for i in range(1, len(nums)):
            if nums[i] == nums[i - 1]:
                format_issues.append(
                    f"Duplicate numbering: point {nums[i]} appears twice"
                )
                break
            # Check for large gaps (skip of 2+)
            if nums[i] > nums[i - 1] + 3 and nums[i - 1] > 0:
                format_issues.append(
                    f"Numbering gap: {nums[i-1]} -> {nums[i]}"
                )
                break

    # Check section ordering (Prayer should come near the end)
    prayer_pos = text.lower().find("prayer")
    if prayer_pos > 0:
        text_after_prayer = text[prayer_pos:]
        # If more than 30% of content comes after "Prayer", it's misplaced
        if len(text_after_prayer) > len(text) * 0.35:
            format_issues.append(
                "Prayer/relief section not near the end of document"
            )

    return format_issues[:5]


def evaluate_draft(response_text: str, checks: dict) -> dict:
    """Deep evaluation of a drafting response.

    Checks:
      - Required keywords present
      - Section count meets minimum
      - Response length meets minimum
      - Court filing structure (footer, prayer, placeholders)
      - Repeated/duplicate points across sections
      - Unnecessary stretching and filler content
      - Format alignment and markdown consistency
    """
    issues = []
    text_lower = response_text.lower()

    # Check for sorry/refusal patterns (only in first 500 chars — drafts may
    # contain "I cannot" in citation/synthesis sections without being a refusal)
    header = response_text[:500]
    for pat in SORRY_PATTERNS:
        if re.search(pat, header):
            issues.append(f"Response contains refusal pattern: {pat}")
            break

    # Check must_contain keywords
    for keyword in checks.get("must_contain", []):
        if keyword.lower() not in text_lower:
            issues.append(f"Missing keyword: '{keyword}'")

    # Count sections (## headings)
    section_count = len(re.findall(r"^#{1,3}\s", response_text, re.MULTILINE))
    min_sections = checks.get("min_sections", 2)
    if section_count < min_sections:
        issues.append(f"Too few sections: {section_count} (expected >= {min_sections})")

    # Check minimum length
    min_length = checks.get("min_length", 1000)
    if len(response_text) < min_length:
        issues.append(f"Response too short: {len(response_text)} chars (expected >= {min_length})")

    # Court filing structure markers
    has_footer = any(kw in text_lower for kw in ("signature", "advocate", "counsel", "verification"))
    has_prayer = any(kw in text_lower for kw in ("prayer", "relief sought", "humbly prayed"))
    has_placeholders = "[" in response_text

    # Deep checks
    repeated_points = _detect_repeated_points(response_text)
    stretch_issues = _detect_unnecessary_stretch(response_text)
    format_issues = _check_format_alignment(response_text)

    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "repeated_points": repeated_points,
        "stretch_issues": stretch_issues,
        "format_issues": format_issues,
        "stats": {
            "section_count": section_count,
            "char_length": len(response_text),
            "has_footer": has_footer,
            "has_prayer": has_prayer,
            "has_placeholders": has_placeholders,
        },
    }


# ============================================================================
#  AI EVALUATOR (GPT-4o-mini deep analysis)
# ============================================================================

AI_EVAL_PROMPT = """You are a senior Indian legal document reviewer. Analyze this AI-generated legal draft.

DOCUMENT TYPE: {doc_type}
USER QUERY: {query}

DRAFT:
{draft}

Score each dimension from 1-10 and provide brief reasoning (1-2 sentences max per dimension).

Dimensions:
1. **Legal Accuracy** - Are statutes, section numbers, and legal procedures correct?
2. **Completeness** - Does it cover all standard sections for this document type?
3. **Repetition** - Are any points/arguments repeated across sections? (10=no repetition, 1=heavy repetition)
4. **Conciseness** - Is it well-written without unnecessary padding/filler? (10=concise, 1=bloated)
5. **Format Quality** - Is the markdown structure clean, headings consistent, numbering proper?
6. **Court-Filing Readiness** - Could a lawyer use this as a starting template with minor edits?

Respond in EXACTLY this JSON format (no extra text):
{{"legal_accuracy": {{"score": N, "reason": "..."}}, "completeness": {{"score": N, "reason": "..."}}, "repetition": {{"score": N, "reason": "..."}}, "conciseness": {{"score": N, "reason": "..."}}, "format_quality": {{"score": N, "reason": "..."}}, "court_filing_readiness": {{"score": N, "reason": "..."}}, "overall_score": N, "top_issues": ["issue1", "issue2"]}}"""


def _run_ai_evaluation(draft: str, doc_type: str, query: str) -> dict | None:
    """Run GPT-4o-mini evaluation on a draft. Returns scores dict or None on failure."""
    try:
        client = OpenAI()
        # Truncate draft to ~12K chars to fit in context
        draft_truncated = draft[:12000]
        if len(draft) > 12000:
            draft_truncated += f"\n\n... [truncated, full draft is {len(draft):,} chars]"

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": AI_EVAL_PROMPT.format(
                    doc_type=doc_type,
                    query=query,
                    draft=draft_truncated,
                ),
            }],
            temperature=0.1,
            max_tokens=800,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        return json.loads(content)

    except Exception as e:
        print(f"    [AI eval failed: {str(e)[:100]}]")
        return None


def run_one_test(test_case: tuple, api_url: str, timeout: int) -> dict:
    """Execute a single drafting test case."""
    test_id, prompt, doc_type, checks = test_case
    start = time.time()

    try:
        resp = requests.post(
            api_url,
            json={"query": prompt, "prompt_query": prompt},
            timeout=timeout,
        )
        elapsed = time.time() - start

        if resp.status_code != 200:
            return {
                "id": test_id,
                "doc_type": doc_type,
                "status": "ERROR",
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                "elapsed": elapsed,
                "response": "",
                "evaluation": None,
                "ai_evaluation": None,
                "agents_used": [],
            }

        data = resp.json()
        response_text = data.get("result", "")
        agents_used = data.get("agents_used", [])

        if not response_text or len(response_text.strip()) < 50:
            return {
                "id": test_id,
                "doc_type": doc_type,
                "status": "ERROR",
                "error": "Empty or near-empty response",
                "elapsed": elapsed,
                "response": response_text,
                "evaluation": None,
                "ai_evaluation": None,
                "agents_used": agents_used,
            }

        evaluation = evaluate_draft(response_text, checks)

        # Run AI evaluation
        ai_eval = _run_ai_evaluation(response_text, doc_type, prompt)

        return {
            "id": test_id,
            "doc_type": doc_type,
            "status": "PASS" if evaluation["passed"] else "FAIL",
            "error": None,
            "elapsed": elapsed,
            "response": response_text,
            "evaluation": evaluation,
            "ai_evaluation": ai_eval,
            "agents_used": agents_used,
        }

    except requests.exceptions.Timeout:
        return {
            "id": test_id,
            "doc_type": doc_type,
            "status": "ERROR",
            "error": f"Timeout after {timeout}s",
            "elapsed": timeout,
            "response": "",
            "evaluation": None,
            "ai_evaluation": None,
            "agents_used": [],
        }
    except Exception as e:
        elapsed = time.time() - start
        return {
            "id": test_id,
            "doc_type": doc_type,
            "status": "ERROR",
            "error": str(e)[:300],
            "elapsed": elapsed,
            "response": "",
            "evaluation": None,
            "ai_evaluation": None,
            "agents_used": [],
        }


def generate_report(results: list[dict], total_elapsed: float) -> str:
    """Generate a markdown report from test results."""
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    errors = sum(1 for r in results if r["status"] == "ERROR")

    lines = [
        "# Drafting Agent Test Report",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Total Time:** {total_elapsed:.1f}s",
        f"**Results:** {passed} PASS / {failed} FAIL / {errors} ERROR out of {len(results)}",
        "",
        "## Summary",
        "",
        "| # | Document Type | Status | Time | Sections | Length | Issues |",
        "|---|--------------|--------|------|----------|--------|--------|",
    ]

    for r in sorted(results, key=lambda x: x["id"]):
        status_icon = {"PASS": "PASS", "FAIL": "FAIL", "ERROR": "ERR"}[r["status"]]
        sections = "-"
        length = "-"
        issues = r.get("error", "") or ""

        if r["evaluation"]:
            stats = r["evaluation"]["stats"]
            sections = str(stats["section_count"])
            length = f"{stats['char_length']:,}"
            if r["evaluation"]["issues"]:
                issues = "; ".join(r["evaluation"]["issues"][:2])

        lines.append(
            f"| {r['id']:2d} | {r['doc_type'][:20]:20s} | {status_icon:4s} | "
            f"{r['elapsed']:5.1f}s | {sections:>8s} | {length:>8s} | {issues[:60]} |"
        )

    lines.append("")

    # Quality stats
    lines.append("## Quality Metrics")
    lines.append("")
    with_eval = [r for r in results if r["evaluation"]]
    if with_eval:
        avg_sections = sum(r["evaluation"]["stats"]["section_count"] for r in with_eval) / len(with_eval)
        avg_length = sum(r["evaluation"]["stats"]["char_length"] for r in with_eval) / len(with_eval)
        footer_pct = sum(1 for r in with_eval if r["evaluation"]["stats"]["has_footer"]) / len(with_eval) * 100
        prayer_pct = sum(1 for r in with_eval if r["evaluation"]["stats"]["has_prayer"]) / len(with_eval) * 100
        placeholder_pct = sum(1 for r in with_eval if r["evaluation"]["stats"]["has_placeholders"]) / len(with_eval) * 100

        lines.append(f"- **Avg sections per draft:** {avg_sections:.1f}")
        lines.append(f"- **Avg response length:** {avg_length:,.0f} chars")
        lines.append(f"- **Has footer/signature:** {footer_pct:.0f}%")
        lines.append(f"- **Has prayer/relief:** {prayer_pct:.0f}%")
        lines.append(f"- **Has placeholders:** {placeholder_pct:.0f}%")

    # --- AI Evaluation Scores ---
    ai_results = [r for r in results if r.get("ai_evaluation")]
    if ai_results:
        lines.append("")
        lines.append("## AI Evaluation Scores (GPT-4o-mini)")
        lines.append("")
        lines.append(
            "| # | Document Type | Legal | Complete | Repetition | Concise | Format | Court-Ready | Overall |"
        )
        lines.append(
            "|---|--------------|-------|----------|------------|---------|--------|-------------|---------|"
        )
        for r in sorted(ai_results, key=lambda x: x["id"]):
            ai = r["ai_evaluation"]
            lines.append(
                f"| {r['id']:2d} | {r['doc_type'][:20]:20s} | "
                f"{ai.get('legal_accuracy', {}).get('score', '-'):>5} | "
                f"{ai.get('completeness', {}).get('score', '-'):>8} | "
                f"{ai.get('repetition', {}).get('score', '-'):>10} | "
                f"{ai.get('conciseness', {}).get('score', '-'):>7} | "
                f"{ai.get('format_quality', {}).get('score', '-'):>6} | "
                f"{ai.get('court_filing_readiness', {}).get('score', '-'):>11} | "
                f"{ai.get('overall_score', '-'):>7} |"
            )

        # Averages
        dims = ["legal_accuracy", "completeness", "repetition", "conciseness",
                "format_quality", "court_filing_readiness"]
        dim_labels = ["Legal", "Complete", "Repetition", "Concise", "Format", "Court-Ready"]
        lines.append("")
        lines.append("**Averages:**")
        for dim, label in zip(dims, dim_labels):
            scores = [r["ai_evaluation"].get(dim, {}).get("score", 0) for r in ai_results]
            avg = sum(scores) / len(scores) if scores else 0
            lines.append(f"- {label}: {avg:.1f}/10")
        overall_scores = [r["ai_evaluation"].get("overall_score", 0) for r in ai_results]
        lines.append(f"- **Overall: {sum(overall_scores)/len(overall_scores):.1f}/10**")

    # --- Deep Quality Analysis (per draft) ---
    lines.append("")
    lines.append("## Deep Quality Analysis")
    for r in sorted(results, key=lambda x: x["id"]):
        if not r.get("evaluation"):
            continue
        ev = r["evaluation"]
        lines.append(f"\n### #{r['id']} {r['doc_type']}")

        # Repeated points
        if ev.get("repeated_points"):
            lines.append("**Repeated Points:**")
            for rp in ev["repeated_points"]:
                lines.append(f"- {rp}")

        # Stretch/filler
        if ev.get("stretch_issues"):
            lines.append("**Unnecessary Stretch:**")
            for si in ev["stretch_issues"]:
                lines.append(f"- {si}")

        # Format issues
        if ev.get("format_issues"):
            lines.append("**Format Issues:**")
            for fi in ev["format_issues"]:
                lines.append(f"- {fi}")

        # AI evaluation details
        ai = r.get("ai_evaluation")
        if ai:
            lines.append("**AI Review:**")
            for dim in dims:
                info = ai.get(dim, {})
                if info and info.get("reason"):
                    lines.append(f"- {dim}: {info['score']}/10 -- {info['reason']}")
            if ai.get("top_issues"):
                lines.append("**Top Issues:**")
                for issue in ai["top_issues"]:
                    lines.append(f"- {issue}")

        if (not ev.get("repeated_points") and not ev.get("stretch_issues")
                and not ev.get("format_issues") and not ai):
            lines.append("No issues detected.")

    # --- Failures ---
    failures = [r for r in results if r["status"] in ("FAIL", "ERROR")]
    if failures:
        lines.append("")
        lines.append("## Failures")
        for r in failures:
            lines.append(f"\n### #{r['id']} {r['doc_type']} ({r['status']})")
            if r.get("error"):
                lines.append(f"**Error:** {r['error'][:500]}")
            if r["evaluation"] and r["evaluation"]["issues"]:
                for issue in r["evaluation"]["issues"]:
                    lines.append(f"- {issue}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Drafting agent test suite (10 prompts)")
    parser.add_argument("--concurrency", type=int, default=2,
                        help="Max concurrent requests (default 2, drafts are heavy)")
    parser.add_argument("--timeout", type=int, default=480,
                        help="Per-request timeout in seconds (default 480)")
    parser.add_argument("--api-url", type=str,
                        default="http://localhost:5000/pyapi/search",
                        help="API endpoint URL")
    args = parser.parse_args()

    print()
    print("=" * 70)
    print(f"  DRAFTING AGENT TEST -- 10 prompts")
    print(f"  Concurrency: {args.concurrency} | Timeout: {args.timeout}s")
    print(f"  API: {args.api_url}")
    print("=" * 70)
    print()

    total_start = time.time()
    results = []

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {
            pool.submit(run_one_test, tc, args.api_url, args.timeout): tc
            for tc in DRAFTING_PROMPTS
        }

        for i, future in enumerate(as_completed(futures), 1):
            tc = futures[future]
            result = future.result()
            results.append(result)

            color = {"PASS": "\033[92m", "FAIL": "\033[93m", "ERROR": "\033[91m"}
            reset = "\033[0m"
            c = color.get(result["status"], "")

            sections = "-"
            length = "-"
            if result["evaluation"]:
                sections = str(result["evaluation"]["stats"]["section_count"])
                length = f"{result['evaluation']['stats']['char_length']:,}"

            ai_score = "-"
            if result.get("ai_evaluation") and result["ai_evaluation"].get("overall_score"):
                ai_score = f"{result['ai_evaluation']['overall_score']}/10"

            print(
                f"  [{i:2d}/10] #{result['id']:2d} {result['doc_type'][:22]:22s} "
                f"{c}{result['status']:5s}{reset}  "
                f"sections={sections:>3s}  len={length:>8s}  "
                f"AI={ai_score:>5s}  "
                f"({result['elapsed']:.1f}s)"
            )
            sys.stdout.flush()

    total_elapsed = time.time() - total_start

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    errors = sum(1 for r in results if r["status"] == "ERROR")

    print()
    print(f"  Results: {passed} PASS / {failed} FAIL / {errors} ERROR")
    print(f"  Total time: {total_elapsed:.1f}s")
    print()

    # Write report
    report_dir = os.path.dirname(os.path.abspath(__file__))
    report_path = os.path.join(report_dir, "test_report_drafting.md")
    json_path = os.path.join(report_dir, "test_results_drafting.json")

    report = generate_report(results, total_elapsed)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"  Report: {report_path}")

    # JSON results (without full response text to keep file small)
    json_results = []
    for r in results:
        jr = {k: v for k, v in r.items() if k != "response"}
        jr["response_length"] = len(r.get("response", ""))
        json_results.append(jr)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_results, f, indent=2, default=str)
    print(f"  JSON:   {json_path}")

    # Full results with response text
    full_json_path = os.path.join(report_dir, "test_results_drafting_full.json")
    with open(full_json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  Full:   {full_json_path}")

    return 0 if errors == 0 and failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
