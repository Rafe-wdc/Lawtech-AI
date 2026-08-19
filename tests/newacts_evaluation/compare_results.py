"""Compare two evaluation runs (baseline vs postfix) side-by-side.

Reads the JSON files produced by run_evaluation.py, produces a markdown
diff showing per-turn pass/fail transitions, latency deltas, and
aggregate improvements.

Usage:
    python tests/newacts_evaluation/compare_results.py \
        --baseline results/baseline_2026-08-18_120000.json \
        --after    results/postfix_2026-08-18_140000.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def index_by_turn(run: dict) -> dict[str, dict]:
    """Flatten a run into {turn_id: turn_dict} for easy lookup."""
    return {
        t["turn_id"]: {**t, "round_id": r["round_id"], "round_desc": r["description"]}
        for r in run["results"]
        for t in r["turns"]
    }


def transition(before_pass: bool, after_pass: bool) -> str:
    if before_pass and after_pass:
        return "PASS→PASS  (unchanged)"
    if not before_pass and after_pass:
        return "FAIL→PASS  (fixed)"
    if before_pass and not after_pass:
        return "PASS→FAIL  (regression)"
    return "FAIL→FAIL  (still broken)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--out", default=None,
                    help="Output markdown path (default: results/compare_<stamp>.md)")
    args = ap.parse_args()

    b = load(args.baseline)
    a = load(args.after)

    b_by_turn = index_by_turn(b)
    a_by_turn = index_by_turn(a)

    all_ids = sorted(set(b_by_turn) | set(a_by_turn),
                     key=lambda x: [int(p) for p in x.split(".")])

    fixed: list[str] = []
    regressed: list[str] = []
    unchanged_pass: list[str] = []
    unchanged_fail: list[str] = []
    rows: list[dict] = []

    for tid in all_ids:
        bt = b_by_turn.get(tid)
        at = a_by_turn.get(tid)
        bp = bool(bt and bt["score"]["pass"])
        ap_ = bool(at and at["score"]["pass"])
        trans = transition(bp, ap_)
        if trans.startswith("FAIL→PASS"):
            fixed.append(tid)
        elif trans.startswith("PASS→FAIL"):
            regressed.append(tid)
        elif trans.startswith("PASS→PASS"):
            unchanged_pass.append(tid)
        else:
            unchanged_fail.append(tid)

        b_lat = bt["call"]["latency_s"] if bt else None
        a_lat = at["call"]["latency_s"] if at else None
        lat_delta = (a_lat - b_lat) if (b_lat is not None and a_lat is not None) else None

        rows.append({
            "turn_id": tid,
            "round_id": (bt or at)["round_id"],
            "round_desc": (bt or at)["round_desc"],
            "prompt": (bt or at)["prompt"],
            "transition": trans,
            "b_pass": bp, "a_pass": ap_,
            "b_contains": bt["score"]["contains_pct"] if bt else None,
            "a_contains": at["score"]["contains_pct"] if at else None,
            "b_latency": b_lat, "a_latency": a_lat, "lat_delta": lat_delta,
            "b_forbidden": bt["score"]["forbidden_hit"] if bt else None,
            "a_forbidden": at["score"]["forbidden_hit"] if at else None,
        })

    # Aggregates
    bs = b["summary"]; asum = a["summary"]
    lines: list[str] = []
    lines.append(f"# Evaluation comparison — baseline vs after-fix\n")
    lines.append(f"- Baseline: `{args.baseline}` — {bs['turns_passed']}/{bs['turns_total']} "
                 f"({bs['pass_rate']*100:.1f}%), avg latency {bs['latency_avg_s']}s")
    lines.append(f"- After:    `{args.after}` — {asum['turns_passed']}/{asum['turns_total']} "
                 f"({asum['pass_rate']*100:.1f}%), avg latency {asum['latency_avg_s']}s")

    dp = asum["pass_rate"] - bs["pass_rate"]
    dl = asum["latency_avg_s"] - bs["latency_avg_s"]
    dt = (asum.get("total_tokens_reported") or 0) - (bs.get("total_tokens_reported") or 0)
    lines.append(f"- **Δ pass rate: {dp*100:+.1f}pp**, "
                 f"Δ avg latency: {dl:+.2f}s, "
                 f"Δ total tokens: {dt:+}")
    lines.append("")

    lines.append("## Headline\n")
    lines.append(f"- Fixed (FAIL→PASS): **{len(fixed)}** turns — `{fixed}`")
    lines.append(f"- Regressed (PASS→FAIL): **{len(regressed)}** turns — `{regressed}`")
    lines.append(f"- Still passing: {len(unchanged_pass)} turns")
    lines.append(f"- Still failing: {len(unchanged_fail)} turns — `{unchanged_fail}`")
    lines.append("")

    # Per-turn table
    lines.append("## Per-turn detail\n")
    lines.append("| Turn | Round | Transition | Contains (b→a) | Latency (b→a, Δ) |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        bc = f"{r['b_contains']*100:.0f}%" if r['b_contains'] is not None else "-"
        ac = f"{r['a_contains']*100:.0f}%" if r['a_contains'] is not None else "-"
        bl = f"{r['b_latency']}s" if r['b_latency'] is not None else "-"
        al = f"{r['a_latency']}s" if r['a_latency'] is not None else "-"
        dl_ = f" ({r['lat_delta']:+.1f}s)" if r['lat_delta'] is not None else ""
        lines.append(f"| {r['turn_id']} | {r['round_id']} — {r['round_desc'][:40]} | "
                     f"{r['transition']} | {bc} → {ac} | {bl} → {al}{dl_} |")
    lines.append("")

    # Detail on fixes and regressions
    if fixed:
        lines.append("## Fixed turns (details)\n")
        for tid in fixed:
            bt = b_by_turn[tid]; at = a_by_turn[tid]
            lines.append(f"### {tid} — {at['prompt']}")
            lines.append(f"- baseline: contains {bt['score']['contains_pct']*100:.0f}% "
                         f"| missing `{bt['score']['contains_missing']}` "
                         f"| forbidden `{bt['score']['forbidden_hit']}`")
            lines.append(f"- after: contains {at['score']['contains_pct']*100:.0f}% "
                         f"| missing `{at['score']['contains_missing']}`")
            lines.append(f"- baseline preview: `{bt['score']['response_preview'][:200]}`")
            lines.append(f"- after preview: `{at['score']['response_preview'][:200]}`")
            lines.append("")

    if regressed:
        lines.append("## Regressions (details)\n")
        for tid in regressed:
            bt = b_by_turn[tid]; at = a_by_turn[tid]
            lines.append(f"### {tid} — {at['prompt']}")
            lines.append(f"- baseline: contains {bt['score']['contains_pct']*100:.0f}% "
                         f"| preview: `{bt['score']['response_preview'][:200]}`")
            lines.append(f"- after: contains {at['score']['contains_pct']*100:.0f}% "
                         f"| missing `{at['score']['contains_missing']}` "
                         f"| forbidden `{at['score']['forbidden_hit']}` "
                         f"| preview: `{at['score']['response_preview'][:200]}`")
            lines.append("")

    out_path = Path(args.out) if args.out else (
        RESULTS_DIR / f"compare_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.md"
    )
    out_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"[done] comparison written to {out_path}")
    print(f"  fixed:     {len(fixed)}")
    print(f"  regressed: {len(regressed)}")
    print(f"  unchanged pass: {len(unchanged_pass)}")
    print(f"  unchanged fail: {len(unchanged_fail)}")
    print(f"  Δ pass rate:   {dp*100:+.1f}pp")
    print(f"  Δ avg latency: {dl:+.2f}s")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
