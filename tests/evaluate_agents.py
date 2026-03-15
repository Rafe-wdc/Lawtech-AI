"""AI-powered evaluator for agent test results — LLM-as-judge.

Reads test_results_agents_full.json (from test_agents.py) and uses GPT-4o-mini
to score each response on Relevance, Completeness, and Accuracy (0-10).

Also evaluates multi-turn conversation coherence.

Usage:
    python tests/evaluate_agents.py [--input tests/test_results_agents_full.json] [--concurrency 10]
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


# ── Structured Output Models ─────────────────────────────────────────────────

class EvaluationScore(BaseModel):
    relevance: int = Field(..., ge=0, le=10, description="Does the response address the user's query?")
    completeness: int = Field(..., ge=0, le=10, description="Are all aspects of the query covered?")
    accuracy: int = Field(..., ge=0, le=10, description="Is the legal information correct and well-cited?")
    verdict: Literal["PASS", "PARTIAL", "FAIL"] = Field(..., description="Overall verdict")
    reasoning: str = Field(..., description="2-3 sentence explanation of the scores")


class MultiTurnScore(BaseModel):
    coherence: int = Field(..., ge=0, le=10, description="Does the system maintain context across turns?")
    follow_up_quality: int = Field(..., ge=0, le=10, description="Are follow-up answers informed by prior turns?")
    verdict: Literal["PASS", "PARTIAL", "FAIL"] = Field(..., description="Overall verdict")
    reasoning: str = Field(..., description="2-3 sentence explanation")


# ── Category-Specific Evaluation Criteria ────────────────────────────────────

CATEGORY_CRITERIA = {
    "Newacts": (
        "This is a query about Indian criminal law statutes (BNS/BNSS/BSA or their "
        "predecessors IPC/CrPC/IEA). The response MUST:\n"
        "- Reference the specific section number(s) asked about\n"
        "- Provide the actual statutory text or an accurate paraphrase\n"
        "- If old↔new mapping is asked, provide the correct equivalent section\n"
        "- Mention the act name clearly\n"
        "- For topic queries, cite relevant sections even if not explicitly asked"
    ),
    "Legislation": (
        "This is a query about Indian civil/commercial legislation (Companies Act, "
        "Contract Act, Arbitration Act, RERA, etc.). The response MUST:\n"
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
        "- Be relevant to the legal topic asked about\n"
        "- For party name searches, find the specific case mentioned\n"
        "- For case type queries (bail, quashing, writ), return relevant case types"
    ),
    "SCI": (
        "This is a query about Supreme Court of India judgments. The response MUST:\n"
        "- Reference specific Supreme Court case names or citations\n"
        "- Summarize the key holdings or legal principles established\n"
        "- Mention the constitutional or statutory provisions involved\n"
        "- Be specific to Supreme Court precedents, not lower courts\n"
        "- For landmark cases, correctly identify the case and its significance"
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
    "Legal_Concepts": (
        "This is a query about a general legal concept. The response MUST:\n"
        "- Explain the concept clearly and accurately\n"
        "- Reference relevant Indian laws, sections, or provisions\n"
        "- Provide context on how the concept applies in practice\n"
        "- Be informative even for vague or short queries"
    ),
    "Scenario": (
        "This is a scenario-based legal query seeking practical advice. The response MUST:\n"
        "- Provide actionable legal steps the person can take\n"
        "- Reference relevant laws, sections, or provisions\n"
        "- Consider the practical aspects of the situation\n"
        "- Be specific to Indian law, not generic advice\n"
        "- For news queries, provide current and accurate information"
    ),
    "Multi": (
        "This is a complex query that should trigger MULTIPLE agents working together. "
        "The response MUST:\n"
        "- Combine information from different legal domains (statutes + case law, or constitution + judgments)\n"
        "- Present a cohesive, well-organized answer that integrates all sources\n"
        "- Reference specific provisions AND relevant case law\n"
        "- Not feel like separate, disconnected sections"
    ),
    "Edge": (
        "This is an edge-case query testing system robustness. The response should:\n"
        "- Provide a relevant and coherent answer despite the unusual query format\n"
        "- Not produce errors or apologies if the query is answerable\n"
        "- Handle vague, minimal, or non-legal queries gracefully\n"
        "- For non-legal queries, politely redirect or decline"
    ),
}


def _get_criteria(category: str) -> str:
    """Get evaluation criteria for a category, with fallback."""
    base = category.split("-")[0]
    return CATEGORY_CRITERIA.get(base, CATEGORY_CRITERIA.get("Edge", "Evaluate the response quality."))


# ── Evaluation Prompts ───────────────────────────────────────────────────────

EVAL_PROMPT = """You are an expert legal AI evaluator specializing in Indian law.

You will be given:
1. A user query
2. The query category
3. The AI system's response
4. Which agents were used (for context)
5. Keyword coverage data (expected vs found keywords)

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
- Consider keyword coverage when scoring: missing expected keywords may indicate gaps

**Accuracy** (0-10): Is the legal information correct and well-cited?
- 9-10: Accurate with proper citations and references
- 7-8: Mostly accurate, minor imprecisions
- 4-6: Some inaccuracies or unsupported claims
- 0-3: Significant errors or fabricated information
- Missing expected keywords may indicate inaccurate or irrelevant content

Category-specific criteria:
{criteria}

{keyword_section}

Verdict rules:
- PASS: All three scores >= 7
- PARTIAL: Any score between 4-6, none below 4
- FAIL: Any score below 4, OR the response is empty/error

Provide a 2-3 sentence reasoning explaining your scores."""

MULTI_TURN_EVAL_PROMPT = """You are an expert evaluator for multi-turn legal AI conversations.

Given a conversation sequence (query + response for each turn), evaluate:

**Coherence** (0-10): Does the system maintain context across turns?
- 9-10: Perfect context retention, each turn builds on previous
- 7-8: Good context, minor gaps
- 4-6: Some context lost, repetition or contradictions
- 0-3: No context awareness, treats each turn independently

**Follow-up Quality** (0-10): Are follow-up answers informed by prior context?
- 9-10: Follow-ups are precise and contextual
- 7-8: Mostly contextual, minor gaps
- 4-6: Partially contextual, could be better
- 0-3: Ignores prior context entirely

Verdict: PASS if both >= 7, PARTIAL if any 4-6, FAIL if any < 4."""


# ── LLM Setup ───────────────────────────────────────────────────────────────

def _get_evaluator_llm():
    """Get Gemini Flash with structured output for evaluation."""
    from langchain.chat_models import init_chat_model
    llm = init_chat_model("google_genai:gemini-2.5-flash", temperature=0.1)
    return llm


def _build_keyword_section(item: dict, response_text: str) -> tuple:
    """Build keyword coverage section for eval prompt. Returns (section_text, coverage_pct)."""
    expected_keywords = item.get("expected_keywords", [])
    if not expected_keywords:
        return "", 1.0
    response_lower = response_text.lower()
    found = [kw for kw in expected_keywords if kw.lower() in response_lower]
    missing = [kw for kw in expected_keywords if kw.lower() not in response_lower]
    coverage = len(found) / len(expected_keywords) if expected_keywords else 1.0
    section = (
        f"## Keyword Coverage\n"
        f"Expected keywords: {', '.join(expected_keywords)}\n"
        f"Found keywords: {', '.join(found) if found else 'NONE'}\n"
        f"Missing keywords: {', '.join(missing) if missing else 'NONE'}\n"
        f"Coverage: {coverage:.0%}\n\n"
        f"Consider keyword coverage when scoring Completeness and Accuracy."
    )
    return section, coverage


def evaluate_single(item: dict, llm) -> dict:
    """Evaluate a single test result. Returns the item enriched with scores."""
    prompt_text = item["prompt"]
    category = item["category"]
    response_text = item.get("full_response", "")
    status = item.get("status", "")
    agents_used = item.get("agents_used", [])

    # Skip ERROR results or empty responses
    if status == "ERROR" or not response_text.strip():
        return {
            **item,
            "eval_relevance": 0,
            "eval_completeness": 0,
            "eval_accuracy": 0,
            "eval_verdict": "FAIL",
            "eval_reasoning": f"No response to evaluate (status: {status})",
            "eval_keyword_coverage": 0.0,
        }

    criteria = _get_criteria(category)
    keyword_section, kw_coverage = _build_keyword_section(item, response_text)
    eval_llm = llm.with_structured_output(EvaluationScore)
    messages = [
        ("system", EVAL_PROMPT.format(criteria=criteria, keyword_section=keyword_section)),
        ("user",
         f"**User Query:** {prompt_text}\n\n"
         f"**Category:** {category}\n\n"
         f"**Agents Used:** {', '.join(agents_used) if agents_used else 'Unknown'}\n\n"
         f"**AI Response:**\n{response_text[:4000]}"),
    ]

    try:
        score: EvaluationScore = eval_llm.invoke(messages)
        return {
            **item,
            "eval_relevance": score.relevance,
            "eval_completeness": score.completeness,
            "eval_accuracy": score.accuracy,
            "eval_verdict": score.verdict,
            "eval_reasoning": score.reasoning,
            "eval_keyword_coverage": round(kw_coverage, 2),
        }
    except Exception as e:
        return {
            **item,
            "eval_relevance": -1,
            "eval_completeness": -1,
            "eval_accuracy": -1,
            "eval_verdict": "ERROR",
            "eval_reasoning": f"Evaluation failed: {str(e)[:200]}",
            "eval_keyword_coverage": round(kw_coverage, 2),
        }


def evaluate_multi_turn(mt_result: dict, llm) -> dict:
    """Evaluate a multi-turn conversation sequence."""
    turns = mt_result.get("turns", [])
    if not turns or mt_result.get("status") == "ERROR":
        return {
            **mt_result,
            "eval_coherence": 0,
            "eval_follow_up": 0,
            "eval_verdict": "FAIL",
            "eval_reasoning": "No turns to evaluate or sequence errored",
        }

    # Build conversation text for evaluation
    conv_text = ""
    for t in turns:
        rw = f" [Rewritten to: {t['effective_query']}]" if t.get("query_rewritten") else ""
        conv_text += (
            f"**Turn {t['turn']}:** {t['prompt']}{rw}\n"
            f"**Agents:** {', '.join(t.get('agents_used', []))}\n"
            f"**Response Length:** {t.get('response_len', 0)} chars\n"
            f"**Status:** {t['status']}\n\n"
        )

    mt_llm = llm.with_structured_output(MultiTurnScore)
    messages = [
        ("system", MULTI_TURN_EVAL_PROMPT),
        ("user",
         f"**Conversation: {mt_result['name']}**\n\n{conv_text}"),
    ]

    try:
        score: MultiTurnScore = mt_llm.invoke(messages)
        return {
            **mt_result,
            "eval_coherence": score.coherence,
            "eval_follow_up": score.follow_up_quality,
            "eval_verdict": score.verdict,
            "eval_reasoning": score.reasoning,
        }
    except Exception as e:
        return {
            **mt_result,
            "eval_coherence": -1,
            "eval_follow_up": -1,
            "eval_verdict": "ERROR",
            "eval_reasoning": f"Evaluation failed: {str(e)[:200]}",
        }


# ── Report Generation ────────────────────────────────────────────────────────

def generate_eval_report(
    evaluated: list[dict],
    mt_evaluated: list[dict],
    total_elapsed: float,
) -> str:
    """Generate a markdown evaluation report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(evaluated)

    verdicts = {"PASS": 0, "PARTIAL": 0, "FAIL": 0, "ERROR": 0}
    for e in evaluated:
        verdicts[e.get("eval_verdict", "ERROR")] += 1

    lines = [
        f"# Agent AI Evaluation Report — {now}",
        "",
        f"**Total Single-Turn:** {total}  ",
        f"**Total Multi-Turn:** {len(mt_evaluated)}  ",
        f"**Evaluation Time:** {total_elapsed:.1f}s  ",
        f"**Model:** Gemini 2.5 Flash (temperature=0.1)  ",
        "",
        "## Overall Verdicts (Single-Turn)",
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
            "## Average Scores (Single-Turn)",
            "",
            "| Dimension | Average |",
            "|-----------|---------|",
            f"| Relevance | {avg_r:.1f}/10 |",
            f"| Completeness | {avg_c:.1f}/10 |",
            f"| Accuracy | {avg_a:.1f}/10 |",
            f"| **Overall** | **{(avg_r + avg_c + avg_a) / 3:.1f}/10** |",
        ])

    # Per-agent category breakdown
    cat_scores: dict[str, list[dict]] = {}
    for e in evaluated:
        base = e["category"].split("-")[0]
        cat_scores.setdefault(base, []).append(e)

    lines.extend([
        "", "## Per-Agent Breakdown", "",
        "| Agent | Count | Avg Rel | Avg Comp | Avg Acc | PASS | PARTIAL | FAIL |",
        "|-------|-------|---------|----------|---------|------|---------|------|",
    ])
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
        vf = sum(1 for i in items if i["eval_verdict"] in ("FAIL", "ERROR"))
        lines.append(f"| {cat} | {len(items)} | {ar:.1f} | {ac:.1f} | {aa:.1f} | {vp} | {vpa} | {vf} |")

    # Multi-turn results
    if mt_evaluated:
        lines.extend([
            "", "## Multi-Turn Evaluation", "",
            "| Sequence | Coherence | Follow-up | Verdict | Reasoning |",
            "|----------|-----------|-----------|---------|-----------|",
        ])
        for mt in mt_evaluated:
            reasoning_short = mt.get("eval_reasoning", "")[:100]
            lines.append(
                f"| {mt['name']} | {mt.get('eval_coherence', 'N/A')} "
                f"| {mt.get('eval_follow_up', 'N/A')} | {mt.get('eval_verdict', 'N/A')} "
                f"| {reasoning_short} |"
            )

    # Keyword coverage summary
    kw_items = [e for e in evaluated if e.get("expected_keywords")]
    if kw_items:
        kw_coverages = [e.get("eval_keyword_coverage", 0) for e in kw_items]
        avg_kw = sum(kw_coverages) / len(kw_coverages)
        full_kw = sum(1 for c in kw_coverages if c >= 1.0)
        lines.extend([
            "", "## Keyword Coverage Summary", "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Tests with keywords | {len(kw_items)} |",
            f"| Avg keyword coverage | {avg_kw:.0%} |",
            f"| Full coverage (100%) | {full_kw}/{len(kw_items)} |",
        ])

    # Per-prompt results table
    lines.extend([
        "", "## Per-Prompt Results", "",
        "| # | Verdict | Rel | Comp | Acc | KW% | Category | Agent(s) | Prompt |",
        "|---|---------|-----|------|-----|-----|----------|----------|--------|",
    ])
    for e in sorted(evaluated, key=lambda x: x["id"]):
        prompt_short = e["prompt"][:40] + ("..." if len(e["prompt"]) > 40 else "")
        agents_str = ", ".join(e.get("agents_used", []))[:25] or "—"
        kw_pct = f"{e.get('eval_keyword_coverage', 0):.0%}" if e.get("expected_keywords") else "—"
        lines.append(
            f"| {e['id']} | {e['eval_verdict']} | {e['eval_relevance']} "
            f"| {e['eval_completeness']} | {e['eval_accuracy']} | {kw_pct} "
            f"| {e['category']} | {agents_str} | {prompt_short} |"
        )

    # Non-PASS details
    non_pass = [e for e in sorted(evaluated, key=lambda x: x["id"]) if e["eval_verdict"] != "PASS"]
    if non_pass:
        lines.extend(["", "## Non-PASS Details", ""])
        for e in non_pass:
            lines.extend([
                f"### #{e['id']} — {e['eval_verdict']} — {e['category']}",
                "",
                f"**Query:** `{e['prompt']}`  ",
                f"**Agents:** {', '.join(e.get('agents_used', [])) or '—'}  ",
                f"**Scores:** Relevance={e['eval_relevance']}, "
                f"Completeness={e['eval_completeness']}, Accuracy={e['eval_accuracy']}  ",
                f"**Reasoning:** {e['eval_reasoning']}  ",
                "",
            ])

    # Bottom 5
    scored_sorted = sorted(scored, key=lambda x: x["eval_relevance"] + x["eval_completeness"] + x["eval_accuracy"])
    if scored_sorted:
        lines.extend(["", "## Lowest Scoring Prompts (Bottom 5)", ""])
        for e in scored_sorted[:5]:
            total_score = e["eval_relevance"] + e["eval_completeness"] + e["eval_accuracy"]
            lines.append(
                f"- **#{e['id']}** ({total_score}/30) [{e['category']}] — "
                f"{e['prompt'][:50]} — {e['eval_reasoning'][:80]}"
            )

    # Top 5
    if scored_sorted:
        lines.extend(["", "## Highest Scoring Prompts (Top 5)", ""])
        for e in scored_sorted[-5:]:
            total_score = e["eval_relevance"] + e["eval_completeness"] + e["eval_accuracy"]
            lines.append(
                f"- **#{e['id']}** ({total_score}/30) [{e['category']}] — "
                f"{e['prompt'][:50]}"
            )

    lines.extend(["", "---", f"*Generated by evaluate_agents.py on {now}*", ""])
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AI-powered agent test evaluator")
    parser.add_argument(
        "--input", default=None,
        help="Path to test_results_agents_full.json (default: auto-detect)",
    )
    parser.add_argument("--concurrency", type=int, default=10, help="Parallel evaluation threads")
    parser.add_argument("--skip-multi-turn", action="store_true", help="Skip multi-turn evaluation")
    args = parser.parse_args()

    # Find input file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if args.input:
        input_path = args.input
    else:
        input_path = os.path.join(script_dir, "test_results_agents_full.json")

    if not os.path.exists(input_path):
        print(f"  ERROR: Input file not found: {input_path}")
        print(f"  Run test_agents.py first to generate test results.")
        return 1

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    single_turn = data.get("single_turn", data.get("details", []))
    multi_turn = data.get("multi_turn", []) if not args.skip_multi_turn else []
    total_st = len(single_turn)
    total_mt = len(multi_turn)

    print(f"\n{'='*80}")
    print(f"  AI AGENT EVALUATOR")
    print(f"  Single-turn: {total_st} prompts | Multi-turn: {total_mt} sequences")
    print(f"  Concurrency: {args.concurrency}")
    print(f"{'='*80}\n")

    # Initialize LLM
    llm = _get_evaluator_llm()

    # ── Phase 1: Single-Turn Evaluation ──────────────────────────────────────
    print("  Phase 1: Evaluating single-turn results...\n")
    evaluated = []
    completed = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        future_to_item = {
            pool.submit(evaluate_single, item, llm): item
            for item in single_turn
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
            scores = f"R={result['eval_relevance']:2d} C={result['eval_completeness']:2d} A={result['eval_accuracy']:2d}"
            print(
                f"  [{completed:3d}/{total_st}] #{result['id']:3d} "
                f"{color.get(v, '')}{v:7s}{reset} "
                f"[{scores}] "
                f"{result['prompt'][:45]}"
            )
            sys.stdout.flush()

    # ── Phase 2: Multi-Turn Evaluation ───────────────────────────────────────
    mt_evaluated = []
    if multi_turn:
        print(f"\n  Phase 2: Evaluating multi-turn sequences...\n")
        for mt in multi_turn:
            print(f"    {mt['name']}...", end=" ")
            sys.stdout.flush()
            mt_result = evaluate_multi_turn(mt, llm)
            mt_evaluated.append(mt_result)
            v = mt_result.get("eval_verdict", "ERROR")
            color = "\033[92m" if v == "PASS" else "\033[93m" if v == "PARTIAL" else "\033[91m"
            reset = "\033[0m"
            print(f"{color}{v}{reset} (C={mt_result.get('eval_coherence', '?')} F={mt_result.get('eval_follow_up', '?')})")

    total_elapsed = time.time() - start_time

    # ── Summary ──────────────────────────────────────────────────────────────
    verdicts = {"PASS": 0, "PARTIAL": 0, "FAIL": 0, "ERROR": 0}
    for e in evaluated:
        verdicts[e.get("eval_verdict", "ERROR")] += 1

    scored = [e for e in evaluated if e["eval_relevance"] >= 0]

    print(f"\n{'='*80}")
    print(f"  SINGLE-TURN: {verdicts['PASS']}/{total_st} PASS, "
          f"{verdicts['PARTIAL']} PARTIAL, {verdicts['FAIL']} FAIL, {verdicts['ERROR']} ERROR")
    if scored:
        avg_r = sum(e["eval_relevance"] for e in scored) / len(scored)
        avg_c = sum(e["eval_completeness"] for e in scored) / len(scored)
        avg_a = sum(e["eval_accuracy"] for e in scored) / len(scored)
        avg_all = (avg_r + avg_c + avg_a) / 3
        print(f"  Avg Scores: R={avg_r:.1f} C={avg_c:.1f} A={avg_a:.1f} | Overall={avg_all:.1f}/10")
    if mt_evaluated:
        mt_pass = sum(1 for m in mt_evaluated if m.get("eval_verdict") == "PASS")
        print(f"  MULTI-TURN: {mt_pass}/{len(mt_evaluated)} PASS")
    print(f"  Evaluation time: {total_elapsed:.1f}s")
    print(f"{'='*80}")

    # Non-PASS details
    non_pass = [e for e in sorted(evaluated, key=lambda x: x["id"]) if e["eval_verdict"] != "PASS"]
    if non_pass:
        print(f"\n--- Non-PASS Details ({len(non_pass)}) ---")
        for e in non_pass:
            total_score = e["eval_relevance"] + e["eval_completeness"] + e["eval_accuracy"]
            print(f"\n  #{e['id']} [{e['eval_verdict']}] ({total_score}/30) {e['prompt'][:55]}")
            print(f"    R={e['eval_relevance']} C={e['eval_completeness']} A={e['eval_accuracy']}")
            print(f"    {e['eval_reasoning'][:120]}")
    else:
        print("\n  All prompts passed AI evaluation!")

    # ── Save Reports ─────────────────────────────────────────────────────────
    md_content = generate_eval_report(evaluated, mt_evaluated, total_elapsed)
    md_path = os.path.join(script_dir, "test_eval_agents_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"\n  Eval report:  {md_path}")

    # JSON results
    json_details = []
    for e in sorted(evaluated, key=lambda x: x["id"]):
        json_details.append({
            "id": e["id"],
            "prompt": e["prompt"],
            "category": e["category"],
            "expected_agents": e.get("expected_agents"),
            "expected_keywords": e.get("expected_keywords", []),
            "agents_used": e.get("agents_used", []),
            "test_status": e.get("status", ""),
            "response_len": e.get("response_len", 0),
            "eval_relevance": e["eval_relevance"],
            "eval_completeness": e["eval_completeness"],
            "eval_accuracy": e["eval_accuracy"],
            "eval_verdict": e["eval_verdict"],
            "eval_reasoning": e["eval_reasoning"],
            "eval_keyword_coverage": e.get("eval_keyword_coverage", 0.0),
        })

    json_path = os.path.join(script_dir, "test_eval_agents_results.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "verdicts": verdicts,
            "evaluation_time": total_elapsed,
            "single_turn": json_details,
            "multi_turn": [
                {
                    "name": mt["name"],
                    "eval_coherence": mt.get("eval_coherence"),
                    "eval_follow_up": mt.get("eval_follow_up"),
                    "eval_verdict": mt.get("eval_verdict"),
                    "eval_reasoning": mt.get("eval_reasoning"),
                }
                for mt in mt_evaluated
            ],
        }, f, indent=2)
    print(f"  Eval JSON:    {json_path}")

    return 0 if verdicts["FAIL"] == 0 and verdicts["ERROR"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
