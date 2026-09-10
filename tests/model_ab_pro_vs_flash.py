"""A/B: gemini-2.5-pro vs gemini-3.8-flash on the tasks this repo actually runs.

Answers the only question that matters before swapping the model behind
`get_gemini_pro()`: on OUR prompts, does the cheaper model hold quality?

For each task x model:
  1. Calls the model with the SAME prompt, temperature and output ceiling the
     real callsite uses.
  2. Thinking params route through `core.clients._thinking_kwargs`, so 2.5 gets
     `thinking_budget` and 3.x gets `thinking_level` - the swap is invalid
     without that, and this harness exercises it.
  3. Records latency, token usage and cost (`core.token_tracker`).
  4. Grades the output with gpt-4o-mini as judge, blind to which model wrote it.

Tasks mirror the `get_gemini_pro()` callsites the swap touches:
  draft_bail    agents/drafting.py:1659 / 2041 - drafting generation
  entity_check  agents/drafting.py:1016        - facts cross-check
  refine        core/self_refine.py:1518       - the refiner
  doc_qa        agents/document.py:148         - PDF Q&A

Run:
    python tests/model_ab_pro_vs_flash.py
    python tests/model_ab_pro_vs_flash.py --tasks draft_bail,refine --repeat 3

Needs a working GOOGLE_API_KEY (both models) and OPENAI_API_KEY (the judge).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from langchain.chat_models import init_chat_model          # noqa: E402
from core.clients import _thinking_kwargs                   # noqa: E402
from core.token_tracker import _estimate_cost_usd           # noqa: E402

DEFAULT_MODELS = ["gemini-2.5-pro", "gemini-3.8-flash"]
# gpt-4o-mini was measured too weak for this: on a 4-line entity_check answer
# that correctly listed all four planted errors, it reported only three and
# scored accuracy 2/10. The judge has to be at least as careful as the thing it
# grades.
JUDGE = "openai:gpt-4o"


def _text_of(content) -> str:
    """Flatten a message's content to plain text.

    Gemini 3.x returns a LIST of content blocks - `[{"type": "text", "text":
    ..., "extras": {"signature": "<base64 thought signature>"}}]` - where 2.5
    returns a bare string. Passing the raw list to the judge means grading a
    Python repr with a multi-KB signature blob glued to it, which is exactly
    how a genuinely good draft scored 1/10 on instruction-following.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, str):
                out.append(b)
            elif isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text", ""))
        return "\n".join(p for p in out if p)
    return str(content)

# ── Tasks ────────────────────────────────────────────────────────────────────
# Each mirrors a real callsite: same temperature and thinking budget, with an
# output ceiling trimmed enough to keep an A/B affordable.

_FACTS = (
    "FIR No. 412/2026 dated 03-Feb-2026, P.S. Kothrud, Pune. Accused Ramesh Patil, "
    "age 41, businessman, R/o 14 Sahyadri Apts, Kothrud, Pune. Sections 318(4) and "
    "316(2) BNS. Arrested 06-Feb-2026, in judicial custody at Yerwada Central Jail. "
    "Allegation: cheated the complainant Sunil More of Rs.18,00,000 on a promised "
    "land transfer. No prior criminal antecedents. Chargesheet filed 22-Apr-2026. "
    "Co-accused Dilip Shinde granted bail by the same court on 11-Mar-2026."
)

TASKS: dict[str, dict] = {
    "draft_bail": {
        "callsite": "agents/drafting.py:1659 / 2041 (drafting generation)",
        "temperature": 0.0, "thinking_budget": 4096, "max_output_tokens": 6000,
        "system": (
            "You are a senior Indian advocate drafting for filing. Follow the "
            "structural skeleton EXACTLY and USE THE EXACT HEADING TEXT GIVEN. "
            "Headings, in order and verbatim: CAUSE TITLE / MOST RESPECTFULLY "
            "SHOWETH / FACTS OF THE CASE / GROUNDS FOR BAIL / PREVIOUS BAIL "
            "APPLICATIONS / PRAYER / VERIFICATION. Number body paragraphs "
            "continuously from 1. Use short natural placeholders like [Date] for "
            "facts not supplied; never invent a particular."
        ),
        "user": (
            "Draft a regular bail application before the Court of Sessions, Pune, "
            "under the correct BNSS provision.\n\nCASE FACTS:\n" + _FACTS
        ),
        "criteria": (
            "Heading fidelity (all 7 headings present, verbatim, in order); correct "
            "enabling provision - regular bail before a Sessions Court is s.483 BNSS "
            "(corresponding to s.439 CrPC); s.480 BNSS corresponds to s.437 and is "
            "WRONG here; continuous paragraph numbering; no invented particulars; "
            "grounds actually tailored to these facts (no antecedents, period of "
            "custody, co-accused parity)."
        ),
    },
    "entity_check": {
        "callsite": "agents/drafting.py:1016 (entity cross-check vs CASE FACTS)",
        "temperature": 0.0, "thinking_budget": 4096, "max_output_tokens": 2000,
        "system": (
            "You verify a legal draft against its source facts. List EVERY named "
            "entity, date, section or amount in the draft that is NOT supported by "
            "the CASE FACTS block. One finding per line as "
            "`<entity> | <why unsupported>`. If everything checks out, output "
            "exactly OK."
        ),
        "user": (
            "CASE FACTS:\n" + _FACTS +
            "\n\nDRAFT EXTRACT:\n"
            "1. That the Applicant Ramesh Patil was arrested on 06-Feb-2026 in "
            "connection with FIR No. 412/2026 registered at P.S. Kothrud under "
            "Sections 318(4) and 316(2) BNS.\n"
            "2. That the Applicant resides at 14 Sahyadri Apts, Kothrud, Pune, and "
            "has been in custody at Arthur Road Jail since 08-Feb-2026.\n"
            "3. That the alleged sum involved is Rs.24,00,000 and the co-accused "
            "Dilip Shinde was granted bail on 11-Mar-2026.\n"
            "4. That the Applicant has two prior convictions under Section 420 IPC."
        ),
        "criteria": (
            "Must catch ALL FOUR planted errors: Arthur Road Jail (facts say Yerwada "
            "Central Jail); custody since 08-Feb-2026 (facts say arrested 06-Feb); "
            "Rs.24,00,000 (facts say Rs.18,00,000); two prior convictions (facts say "
            "NO prior antecedents). Score down for each missed error and for false "
            "positives raised against entities that ARE supported."
        ),
    },
    "refine": {
        "callsite": "core/self_refine.py:1518 (the refiner)",
        "temperature": 0.3, "thinking_budget": 2048, "max_output_tokens": 4000,
        "system": (
            "You are a legal-draft refiner. Apply ONLY the listed violations. Do not "
            "rewrite anything else, do not shorten the draft, and preserve every "
            "heading verbatim. Return the corrected draft in full."
        ),
        "user": (
            "VIOLATIONS:\n"
            "1. statute_era_mismatch - the application cites Section 480 BNSS as the "
            "enabling provision, but it is addressed to a Court of Session; the "
            "correct provision is Section 483 BNSS (s.439 CrPC).\n"
            "2. paragraph_numbering_break - body paragraphs restart at 1 under "
            "GROUNDS FOR BAIL instead of continuing the global counter.\n\n"
            "DRAFT:\n"
            "IN THE COURT OF SESSIONS JUDGE, PUNE\n\n"
            "APPLICATION FOR REGULAR BAIL UNDER SECTION 480 OF THE BNSS, 2023\n\n"
            "MOST RESPECTFULLY SHOWETH:\n"
            "1. That the Applicant is accused in FIR No. 412/2026, P.S. Kothrud.\n"
            "2. That the Applicant was arrested on 06-Feb-2026.\n\n"
            "GROUNDS FOR BAIL\n"
            "1. That the Applicant has no criminal antecedents.\n"
            "2. That the co-accused has been granted bail by this Hon'ble Court.\n\n"
            "PRAYER\n"
            "3. That bail be granted on such terms as this Hon'ble Court deems fit."
        ),
        "criteria": (
            "Both violations fixed: s.480 BNSS becomes s.483 BNSS everywhere it "
            "appears, and the GROUNDS paragraphs renumber to continue the counter "
            "(3, 4) with PRAYER following (5). Score DOWN heavily for collateral "
            "edits, dropped or reworded headings, or a shortened draft - obedience "
            "matters as much as the fix itself."
        ),
    },
    "doc_qa": {
        "callsite": "agents/document.py:148 (PDF Q&A, large docs)",
        "temperature": 0.3, "thinking_budget": 1024, "max_output_tokens": 2000,
        "system": (
            "Answer strictly from the supplied document. If the document does not "
            "contain the answer, say so plainly instead of inferring it."
        ),
        "user": (
            "DOCUMENT:\n"
            "Civil Suit No. 114 of 2025, Court of Civil Judge (Junior Division), Pune. "
            "Plaintiff Arun Shankar Deshmukh (businessman, Bibwewadi) sues Defendant "
            "Kunal Rajendra Patil (Kothrud) under Order VII Rule 1 CPC to recover "
            "Rs.10,00,000 advanced in cash on 15-Apr-2023 as a friendly loan; "
            "witnesses Rohit Naik and Sandeep Pawar. Repayment fell due 15-Oct-2023. "
            "Legal notice dated 10-Jan-2024 was served on 15-Jan-2024; the defendant "
            "did not reply. Plaintiff seeks Rs.10,00,000 plus 12% p.a. interest from "
            "15-Apr-2023, plus costs. Verified at Pune on 29-Jun-2025 by "
            "Adv. Meera Kulkarni.\n\n"
            "QUESTIONS:\n"
            "1. On what date was the legal notice served, and how long after the debt "
            "fell due was it sent?\n"
            "2. What interest rate is claimed and from which date?\n"
            "3. Who witnessed the loan?\n"
            "4. What is the defendant's stated defence?"
        ),
        "criteria": (
            "Q1: served 15-Jan-2024, roughly 3 months after the 15-Oct-2023 due date. "
            "Q2: 12% p.a. from 15-Apr-2023 - the ADVANCE date, not the due date. "
            "Q3: Rohit Naik and Sandeep Pawar. Q4: MUST refuse - the document records "
            "no defence, only that the defendant did not reply. Inventing a defence "
            "is the critical failure and should score near zero on accuracy."
        ),
    },
}


# ── Deterministic checks ─────────────────────────────────────────────────────
# The LLM judge proved uncalibrated in BOTH directions on these tasks:
# gpt-4o-mini scored a fully correct doc_qa answer 9/10 and a correct
# entity_check answer 2/10 (it miscounted the findings); gpt-4o then scored
# everything 4-5 regardless of content. Every task here has a KNOWN right
# answer, so score it by assertion instead. These are the numbers to merge on;
# the LLM grade is kept only as a soft second opinion.

def _has(t: str, *subs: str) -> bool:
    low = t.lower()
    return all(s.lower() in low for s in subs)


def _none(t: str, *subs: str) -> bool:
    low = t.lower()
    return not any(s.lower() in low for s in subs)


CHECKS = {
    "draft_bail": lambda t: {
        # Regular bail before a Sessions Court is s.483 BNSS. s.480 is s.437
        # CrPC (Magistrate) and s.482 is anticipatory - both wrong here.
        "cites s.483 BNSS": "483" in t,
        "avoids wrong provision (480/482)": _none(t, "section 480", "s.480", "section 482", "s.482"),
        "heading MOST RESPECTFULLY SHOWETH": _has(t, "MOST RESPECTFULLY SHOWETH"),
        "heading GROUNDS FOR BAIL": _has(t, "GROUNDS FOR BAIL"),
        "heading PREVIOUS BAIL APPLICATIONS": _has(t, "PREVIOUS BAIL APPLICATIONS"),
        "heading PRAYER": _has(t, "PRAYER"),
        "heading VERIFICATION": _has(t, "VERIFICATION"),
        "uses the real jail (Yerwada)": "yerwada" in t.lower(),
        "no invented jail": _none(t, "arthur road"),
        "keeps the real amount": _has(t, "18,00,000") or _has(t, "18,00,000/-"),
    },
    "entity_check": lambda t: {
        "flags wrong jail": _has(t, "arthur road"),
        "flags wrong custody date": "08-feb" in t.lower(),
        "flags wrong amount": "24,00,000" in t,
        "flags invented convictions": _has(t, "convict") or _has(t, "420"),
        "no signature blob leaked": _none(t, "extras", "signature"),
    },
    "refine": lambda t: {
        "corrects to s.483": "483" in t,
        "removes s.480": _none(t, "480"),
        "keeps heading GROUNDS FOR BAIL": _has(t, "GROUNDS FOR BAIL"),
        "keeps heading PRAYER": _has(t, "PRAYER"),
        "keeps heading MOST RESPECTFULLY SHOWETH": _has(t, "MOST RESPECTFULLY SHOWETH"),
        "renumbers grounds (3 and 4)": ("3." in t and "4." in t),
        "no collateral shrink": len(t) > 400,
    },
    "doc_qa": lambda t: {
        "notice served 15-Jan-2024": _has(t, "15-jan-2024") or _has(t, "15 january 2024") or _has(t, "15-01-2024"),
        "interest 12%": "12%" in t or "12 %" in t,
        "interest runs from 15-Apr-2023": _has(t, "15-apr-2023") or _has(t, "15 april 2023"),
        "both witnesses named": _has(t, "rohit naik") and _has(t, "sandeep pawar"),
        # The critical one: the document records NO defence, only silence.
        "refuses to invent a defence": _none(t, "denies", "contends", "gift", "disputes the loan"),
    },
}


class Grade(BaseModel):
    quality: int = Field(ge=0, le=10, description="Overall quality against the criteria")
    instruction_following: int = Field(ge=0, le=10, description="Obeyed format/heading/scope instructions")
    factual_accuracy: int = Field(ge=0, le=10, description="No invented or wrong particulars")
    reasoning: str = Field(description="Two sentences citing specific evidence from the output")


def run_one(model: str, task: dict) -> dict:
    """One generation call. Never raises - a failure is a recorded result."""
    kw = _thinking_kwargs(f"google_genai:{model}", task["thinking_budget"])
    llm = init_chat_model(
        f"google_genai:{model}",
        temperature=task["temperature"],
        max_output_tokens=task["max_output_tokens"],
        max_retries=1,
        timeout=300,
        **kw,
    )
    t0 = time.perf_counter()
    try:
        r = llm.invoke([("system", task["system"]), ("human", task["user"])])
    except Exception as e:
        return {"ok": False, "thinking_kwargs": kw,
                "error": f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"}
    dt = time.perf_counter() - t0
    u = getattr(r, "usage_metadata", None) or {}
    tin, tout = u.get("input_tokens", 0), u.get("output_tokens", 0)
    return {
        "ok": True, "text": _text_of(r.content), "latency_s": round(dt, 2),
        "input_tokens": tin, "output_tokens": tout,
        "cost_usd": round(_estimate_cost_usd(model, tin, tout), 6),
        "thinking_kwargs": kw,
    }


def grade(task: dict, text: str) -> dict:
    judge = init_chat_model(JUDGE, temperature=0).with_structured_output(Grade)
    g = judge.invoke(
        "You are grading one model's output on an Indian legal drafting task. You do "
        "NOT know which model produced it; judge only the text.\n\n"
        f"TASK GIVEN TO THE MODEL:\n{task['user'][:3000]}\n\n"
        f"GRADING CRITERIA:\n{task['criteria']}\n\n"
        f"MODEL OUTPUT:\n{text[:12000]}"
    )
    return g.model_dump()


def _median(vals):
    return statistics.median(vals) if vals else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--tasks", default=",".join(TASKS))
    ap.add_argument("--repeat", type=int, default=1,
                    help="runs per model/task; the median is reported")
    ap.add_argument("--out", default="tests/model_ab_results")
    a = ap.parse_args()

    models = [m.strip() for m in a.models.split(",") if m.strip()]
    names = [t.strip() for t in a.tasks.split(",") if t.strip() in TASKS]
    if not os.getenv("GOOGLE_API_KEY"):
        print("GOOGLE_API_KEY not set - nothing to run.")
        return 2

    print(f"A/B  models={models}  tasks={names}  repeat={a.repeat}\n")
    results: dict = {}
    for name in names:
        task = TASKS[name]
        results[name] = {"callsite": task["callsite"], "models": {}}
        print(f"[{name}]  {task['callsite']}")
        for m in models:
            runs = []
            for i in range(a.repeat):
                r = run_one(m, task)
                if r["ok"]:
                    r["checks"] = CHECKS[name](r["text"])
                    r["checks_passed"] = sum(r["checks"].values())
                    r["checks_total"] = len(r["checks"])
                    r["grade"] = grade(task, r["text"])
                runs.append(r)
                detail = (f"{r['checks_passed']}/{r['checks_total']} checks  "
                          f"{r['latency_s']}s  ${r['cost_usd']}"
                          if r["ok"] else r["error"][:70])
                print(f"   {m:18s} run{i + 1} {'ok' if r['ok'] else 'FAIL':4s} {detail}")
            good = [r for r in runs if r["ok"]]
            # A check counts as passed only if it passed on EVERY run - a rule
            # the model obeys half the time is not one you can ship behind.
            stable = {}
            if good:
                for k in good[0]["checks"]:
                    stable[k] = all(r["checks"].get(k) for r in good)
            results[name]["models"][m] = {
                "runs": runs,
                "checks_stable": stable,
                "checks_passed_all_runs": sum(stable.values()),
                "checks_total": len(stable),
                "median_latency_s": _median([r["latency_s"] for r in good]),
                "median_cost_usd": _median([r["cost_usd"] for r in good]),
                "median_quality": _median([r["grade"]["quality"] for r in good]),
            }
            for k, ok in stable.items():
                if not ok:
                    print(f"       FAILED: {k}")
        print()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    (out / f"ab_{stamp}.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    lines = [f"# Model A/B - {stamp}",
             "",
             "`checks` = deterministic assertions passed on EVERY run. "
             "`llm` is a soft second opinion only - the judge was measured "
             "uncalibrated on these tasks.",
             "",
             "| task | model | checks | llm | latency | cost |",
             "|---|---|---|---|---|---|"]
    for name, r in results.items():
        for m, s in r["models"].items():
            lines.append(
                f"| `{name}` | `{m}` | **{s['checks_passed_all_runs']}/{s['checks_total']}** | "
                f"{s['median_quality']} | {s['median_latency_s']:.1f}s | "
                f"${s['median_cost_usd']:.5f} |")
    lines += ["", "## Failed checks", ""]
    for name, r in results.items():
        for m, s in r["models"].items():
            bad = [k for k, ok in s["checks_stable"].items() if not ok]
            lines.append(f"- `{name}` / `{m}`: " + (", ".join(bad) if bad else "none"))
    md = "\n".join(lines)
    (out / f"ab_{stamp}.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"\nWrote {out}/ab_{stamp}.json and .md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
