"""AI-powered test evaluator — LLM-as-judge for response quality.

Reads test_results_full.json (from test_concurrent.py) and uses GPT-4o-mini
to score each response on Relevance, Completeness, and Accuracy (0-10).

Usage (from v2_multi_agent/):
    python tests/evaluate_results.py [--input tests/test_results_full.json] [--concurrency 10]
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load .env from project root
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# ── Structured Output Model ──────────────────────────────────────────────────

class EvaluationScore(BaseModel):
    relevance: int = Field(..., ge=0, le=10, description="Does the response address the user's query?")
    completeness: int = Field(..., ge=0, le=10, description="Are all aspects of the query covered?")
    accuracy: int = Field(..., ge=0, le=10, description="Is the legal information correct and well-cited?")
    verdict: Literal["PASS", "PARTIAL", "FAIL"] = Field(..., description="Overall verdict")
    reasoning: str = Field(..., description="2-3 sentence explanation of the scores")


# ── Category-Specific Criteria ───────────────────────────────────────────────

CATEGORY_CRITERIA = {
    "Newacts": (
        "This is a query about Indian criminal law statutes (BNS/BNSS/BSA or their "
        "predecessors IPC/CrPC/IEA). The response MUST:\n"
        "- Reference the specific section number(s) asked about\n"
        "- Provide the actual statutory text or an accurate paraphrase\n"
        "- If old↔new mapping is asked, provide the correct equivalent section\n"
        "- Mention the act name clearly"
    ),
    "Legislation": (
        "This is a query about Indian civil/commercial legislation (Companies Act, "
        "Contract Act, Arbitration Act, etc.). The response MUST:\n"
        "- Reference the specific section number(s) asked about\n"
        "- Provide the actual statutory text or an accurate summary\n"
        "- Explain the legal provision in context\n"
        "- Be specific to the act mentioned, not generic"
    ),
    "Judgment": (
        "This is a query about Indian court judgments/case law. The response MUST:\n"
        "- Reference specific case names or citations\n"
        "- Mention the court (High Court, District Court, etc.)\n"
        "- Summarize the key legal principles or holdings\n"
        "- Be relevant to the legal topic asked about"
    ),
    "SCI_Judgment": (
        "This is a query about Supreme Court of India judgments. The response MUST:\n"
        "- Reference specific Supreme Court case names or citations\n"
        "- Summarize the key holdings or legal principles established\n"
        "- Mention the constitutional or statutory provisions involved\n"
        "- Be specific to Supreme Court precedents, not lower courts"
    ),
    "Constitution": (
        "This is a query about the Indian Constitution. The response MUST:\n"
        "- Reference the specific Article(s) asked about\n"
        "- Explain the scope and application of the provision\n"
        "- Mention relevant case law or interpretations if applicable\n"
        "- Distinguish between fundamental rights, DPSPs, and duties"
    ),
    "Maxim": (
        "This is a query about a legal maxim, doctrine, or concept. The response MUST:\n"
        "- Explain the meaning of the maxim/doctrine clearly\n"
        "- Provide the legal context and application\n"
        "- Reference relevant case law or statutory provisions\n"
        "- Give practical examples of how the doctrine applies"
    ),
    "Drafting": (
        "This is a request to draft a legal document. The response MUST:\n"
        "- Produce a usable legal document with proper structure\n"
        "- Include appropriate legal formatting (title, parties, clauses)\n"
        "- Use correct legal terminology and language\n"
        "- Cover all essential elements for the document type"
    ),
    "Scenario": (
        "This is a scenario-based legal query seeking practical advice. The response MUST:\n"
        "- Provide actionable legal steps the person can take\n"
        "- Reference relevant laws, sections, or provisions\n"
        "- Consider the practical aspects of the situation\n"
        "- Be specific to Indian law, not generic advice"
    ),
    "Edge": (
        "This is an edge-case query testing system robustness. The response should:\n"
        "- Provide a relevant and coherent answer despite the unusual query format\n"
        "- Not produce errors or apologies if the query is answerable\n"
        "- Handle vague or minimal queries gracefully"
    ),
}


def _get_criteria(category: str) -> str:
    """Get evaluation criteria for a category, with fallback."""
    # Strip sub-category suffixes: "Newacts-Single" → "Newacts"
    base = category.split("-")[0]
    return CATEGORY_CRITERIA.get(base, CATEGORY_CRITERIA.get("Edge", "Evaluate the response quality."))


# ── Evaluation Prompt ────────────────────────────────────────────────────────

EVAL_PROMPT = """You are an expert legal AI evaluator specializing in Indian law.

You will be given:
1. A user query
2. The query category
3. The AI system's response

Score the response on three dimensions (0-10 each):

**Relevance** (0-10): Does the response directly address what was asked?
- 9-10: Perfectly on-topic, addresses every aspect of the query
- 7-8: Mostly relevant, minor tangents
- 4-6: Partially relevant, misses key aspects
- 0-3: Off-topic or doesn't address the query

**Completeness** (0-10): Are all parts of the query covered?
- 9-10: Comprehensive, covers all aspects with depth
- 7-8: Covers main points, minor gaps
- 4-6: Covers some aspects, notable gaps
- 0-3: Superficial or missing major parts

**Accuracy** (0-10): Is the legal information correct and well-cited?
- 9-10: Accurate with proper citations and references
- 7-8: Mostly accurate, minor imprecisions
- 4-6: Some inaccuracies or unsupported claims
- 0-3: Significant errors or fabricated information

Category-specific criteria:
{criteria}

Verdict rules:
- PASS: All three scores >= 7
- PARTIAL: Any score between 4-6, none below 4
- FAIL: Any score below 4, OR the response is empty/error

Provide a 2-3 sentence reasoning explaining your scores."""


# ── LLM Evaluation ──────────────────────────────────────────────────────────

def _get_evaluator_llm():
    """Get GPT-4o-mini with structured output for evaluation."""
    from langchain.chat_models import init_chat_model
    llm = init_chat_model("openai:gpt-4o-mini", temperature=0.1)
    return llm.with_structured_output(EvaluationScore)


def evaluate_single(item: dict, llm) -> dict:
    """Evaluate a single test result. Returns the item enriched with scores."""
    prompt_text = item["prompt"]
    category = item["category"]
    response_text = item.get("full_response", "")
    status = item.get("status", "")

    # Skip ERROR results
    if status == "ERROR" or not response_text.strip():
        return {
            **item,
            "eval_relevance": 0,
            "eval_completeness": 0,
            "eval_accuracy": 0,
            "eval_verdict": "FAIL",
            "eval_reasoning": f"No response to evaluate (status: {status})",
        }

    criteria = _get_criteria(category)
    messages = [
        ("system", EVAL_PROMPT.format(criteria=criteria)),
        ("user",
         f"**User Query:** {prompt_text}\n\n"
         f"**Category:** {category}\n\n"
         f"**AI Response:**\n{response_text[:4000]}"),
    ]

    try:
        score: EvaluationScore = llm.invoke(messages)
        return {
            **item,
            "eval_relevance": score.relevance,
            "eval_completeness": score.completeness,
            "eval_accuracy": score.accuracy,
            "eval_verdict": score.verdict,
            "eval_reasoning": score.reasoning,
        }
    except Exception as e:
        return {
            **item,
            "eval_relevance": -1,
            "eval_completeness": -1,
            "eval_accuracy": -1,
            "eval_verdict": "ERROR",
            "eval_reasoning": f"Evaluation failed: {str(e)[:200]}",
        }


# ── Report Generation ────────────────────────────────────────────────────────

def generate_eval_report(evaluated: list[dict], total_elapsed: float) -> str:
    """Generate a markdown evaluation report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(evaluated)

    verdicts = {"PASS": 0, "PARTIAL": 0, "FAIL": 0, "ERROR": 0}
    for e in evaluated:
        verdicts[e.get("eval_verdict", "ERROR")] += 1

    # Category averages
    cat_scores: dict[str, list[dict]] = {}
    for e in evaluated:
        base = e["category"].split("-")[0]
        cat_scores.setdefault(base, []).append(e)

    lines = [
        f"# AI Evaluation Report — {now}",
        "",
        f"**Total Prompts:** {total}  ",
        f"**Evaluation Time:** {total_elapsed:.1f}s  ",
        f"**Model:** GPT-4o-mini (temperature=0.1)  ",
        "",
        "## Overall Verdicts",
        "",
        "| Verdict | Count | % |",
        "|---------|-------|---|",
    ]
    for v in ["PASS", "PARTIAL", "FAIL", "ERROR"]:
        pct = (verdicts[v] / total * 100) if total else 0
        lines.append(f"| {v} | {verdicts[v]} | {pct:.0f}% |")

    # Overall average scores
    scored = [e for e in evaluated if e["eval_relevance"] >= 0]
    if scored:
        avg_r = sum(e["eval_relevance"] for e in scored) / len(scored)
        avg_c = sum(e["eval_completeness"] for e in scored) / len(scored)
        avg_a = sum(e["eval_accuracy"] for e in scored) / len(scored)
        lines.extend([
            "",
            "## Average Scores",
            "",
            "| Dimension | Average |",
            "|-----------|---------|",
            f"| Relevance | {avg_r:.1f}/10 |",
            f"| Completeness | {avg_c:.1f}/10 |",
            f"| Accuracy | {avg_a:.1f}/10 |",
            f"| **Overall** | **{(avg_r + avg_c + avg_a) / 3:.1f}/10** |",
        ])

    # Category breakdown
    lines.extend(["", "## Category Breakdown", "",
                   "| Category | Count | Avg Rel | Avg Comp | Avg Acc | PASS | PARTIAL | FAIL |",
                   "|----------|-------|---------|----------|---------|------|---------|------|"])
    for cat in sorted(cat_scores.keys()):
        items = cat_scores[cat]
        valid = [i for i in items if i["eval_relevance"] >= 0]
        if not valid:
            continue
        n = len(valid)
        ar = sum(i["eval_relevance"] for i in valid) / n
        ac = sum(i["eval_completeness"] for i in valid) / n
        aa = sum(i["eval_accuracy"] for i in valid) / n
        vp = sum(1 for i in items if i["eval_verdict"] == "PASS")
        vpa = sum(1 for i in items if i["eval_verdict"] == "PARTIAL")
        vf = sum(1 for i in items if i["eval_verdict"] == "FAIL")
        lines.append(f"| {cat} | {len(items)} | {ar:.1f} | {ac:.1f} | {aa:.1f} | {vp} | {vpa} | {vf} |")

    # Per-prompt results
    lines.extend([
        "", "## Per-Prompt Results", "",
        "| # | Verdict | Rel | Comp | Acc | Category | Prompt |",
        "|---|---------|-----|------|-----|----------|--------|",
    ])
    for e in sorted(evaluated, key=lambda x: x["id"]):
        prompt_short = e["prompt"][:50] + ("..." if len(e["prompt"]) > 50 else "")
        lines.append(
            f"| {e['id']} | {e['eval_verdict']} | {e['eval_relevance']} "
            f"| {e['eval_completeness']} | {e['eval_accuracy']} "
            f"| {e['category']} | {prompt_short} |"
        )

    # Detailed reasoning for non-PASS
    non_pass = [e for e in sorted(evaluated, key=lambda x: x["id"]) if e["eval_verdict"] != "PASS"]
    if non_pass:
        lines.extend(["", "## Non-PASS Details", ""])
        for e in non_pass:
            lines.extend([
                f"### #{e['id']} — {e['eval_verdict']} — {e['category']}",
                "",
                f"**Query:** `{e['prompt']}`  ",
                f"**Scores:** Relevance={e['eval_relevance']}, "
                f"Completeness={e['eval_completeness']}, Accuracy={e['eval_accuracy']}  ",
                f"**Reasoning:** {e['eval_reasoning']}  ",
                "",
            ])

    # Lowest scoring prompts
    scored_sorted = sorted(scored, key=lambda x: x["eval_relevance"] + x["eval_completeness"] + x["eval_accuracy"])
    if scored_sorted:
        lines.extend(["", "## Lowest Scoring Prompts (Bottom 5)", ""])
        for e in scored_sorted[:5]:
            total_score = e["eval_relevance"] + e["eval_completeness"] + e["eval_accuracy"]
            lines.append(
                f"- **#{e['id']}** ({total_score}/30) — {e['prompt'][:60]} — {e['eval_reasoning'][:100]}"
            )

    lines.extend(["", "---", f"*Generated by evaluate_results.py on {now}*", ""])
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AI-powered test evaluator")
    parser.add_argument(
        "--input", default=None,
        help="Path to test_results_full.json (default: auto-detect in tests/)",
    )
    parser.add_argument("--concurrency", type=int, default=10, help="Parallel evaluation threads")
    args = parser.parse_args()

    # Find input file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if args.input:
        input_path = args.input
    else:
        input_path = os.path.join(script_dir, "test_results_full.json")

    if not os.path.exists(input_path):
        print(f"  ERROR: Input file not found: {input_path}")
        print(f"  Run test_concurrent.py first to generate test results.")
        return 1

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    details = data["details"]
    total = len(details)
    print(f"{'='*80}")
    print(f"  AI Evaluator — scoring {total} test results")
    print(f"  Input: {input_path}")
    print(f"  Concurrency: {args.concurrency}")
    print(f"{'='*80}\n")

    # Initialize LLM once (shared across threads — langchain handles thread safety)
    llm = _get_evaluator_llm()

    evaluated = []
    completed = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        future_to_item = {
            pool.submit(evaluate_single, item, llm): item
            for item in details
        }

        for future in as_completed(future_to_item):
            result = future.result()
            completed += 1
            evaluated.append(result)

            color = {
                "PASS": "\033[92m", "PARTIAL": "\033[93m",
                "FAIL": "\033[91m", "ERROR": "\033[91m",
            }
            reset = "\033[0m"
            v = result.get("eval_verdict", "ERROR")
            scores = f"R={result['eval_relevance']} C={result['eval_completeness']} A={result['eval_accuracy']}"
            print(
                f"  [{completed:2d}/{total}] #{result['id']:2d} "
                f"{color.get(v, '')}{v:7s}{reset} "
                f"[{scores}] "
                f"{result['prompt'][:45]}"
            )
            sys.stdout.flush()

    total_elapsed = time.time() - start_time

    # Summary
    verdicts = {"PASS": 0, "PARTIAL": 0, "FAIL": 0, "ERROR": 0}
    for e in evaluated:
        verdicts[e.get("eval_verdict", "ERROR")] += 1

    print(f"\n{'='*80}")
    print(f"  VERDICTS: {verdicts['PASS']}/{total} PASS, "
          f"{verdicts['PARTIAL']} PARTIAL, {verdicts['FAIL']} FAIL, {verdicts['ERROR']} ERROR")
    scored = [e for e in evaluated if e["eval_relevance"] >= 0]
    if scored:
        avg_r = sum(e["eval_relevance"] for e in scored) / len(scored)
        avg_c = sum(e["eval_completeness"] for e in scored) / len(scored)
        avg_a = sum(e["eval_accuracy"] for e in scored) / len(scored)
        print(f"  Avg Scores: Relevance={avg_r:.1f}, Completeness={avg_c:.1f}, Accuracy={avg_a:.1f}")
    print(f"  Evaluation time: {total_elapsed:.1f}s")
    print(f"{'='*80}")

    # Non-PASS details
    non_pass = [e for e in sorted(evaluated, key=lambda x: x["id"]) if e["eval_verdict"] != "PASS"]
    if non_pass:
        print(f"\n--- Non-PASS Details ---")
        for e in non_pass:
            print(f"\n  #{e['id']} [{e['eval_verdict']}] {e['prompt'][:60]}")
            print(f"    Scores: R={e['eval_relevance']} C={e['eval_completeness']} A={e['eval_accuracy']}")
            print(f"    Reason: {e['eval_reasoning'][:150]}")
    else:
        print("\n  All prompts passed AI evaluation!")

    # Save reports
    md_content = generate_eval_report(evaluated, total_elapsed)
    md_path = os.path.join(script_dir, "test_eval_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"\n  Eval report:  {md_path}")

    json_path = os.path.join(script_dir, "test_eval_results.json")
    eval_details = []
    for e in sorted(evaluated, key=lambda x: x["id"]):
        eval_details.append({
            "id": e["id"],
            "prompt": e["prompt"],
            "category": e["category"],
            "expected_agent": e.get("expected_agent"),
            "agents_used": e.get("agents_used", []),
            "test_status": e.get("status", ""),
            "response_len": e.get("response_len", 0),
            "eval_relevance": e["eval_relevance"],
            "eval_completeness": e["eval_completeness"],
            "eval_accuracy": e["eval_accuracy"],
            "eval_verdict": e["eval_verdict"],
            "eval_reasoning": e["eval_reasoning"],
        })
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "verdicts": verdicts,
            "evaluation_time": total_elapsed,
            "details": eval_details,
        }, f, indent=2)
    print(f"  Eval JSON:    {json_path}")

    return 0 if verdicts["FAIL"] == 0 and verdicts["ERROR"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
