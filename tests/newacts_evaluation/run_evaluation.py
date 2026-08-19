"""Run the newacts evaluation suite against a running server.

Hits /pyapi/search/stream for each turn, consumes the SSE stream, scores
each response against the criteria in rounds.py, and writes both a
human-readable markdown report and a machine-readable JSON companion
(so compare_results.py can diff two runs cleanly).

Usage:
    python tests/newacts_evaluation/run_evaluation.py [--label baseline]
                                                       [--url http://127.0.0.1:5000]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests

# Windows console (cp1252) can't encode em-dash / arrow / Devanagari /
# Tamil / Bengali characters that appear in round descriptions and
# prompts. Force UTF-8 for stdout/stderr so the runner doesn't crash
# mid-run when it hits a Hindi turn.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# Local import from same directory
sys.path.insert(0, str(Path(__file__).parent))
from rounds import ROUNDS, total_turns  # noqa: E402

DEFAULT_URL = "http://127.0.0.1:5000/pyapi/search/stream"
DEFAULT_KEY = "ff6c3e959de2bf4f73901db1ff797ea484d326ac6e2622067493352435f23a51"
RESULTS_DIR = Path(__file__).parent / "results"

# Sleep between requests to avoid slamming the server / hitting rate limits.
SLEEP_BETWEEN_TURNS_S = 0.5
SLEEP_BETWEEN_ROUNDS_S = 1.0
REQUEST_TIMEOUT_S = 300


def call_stream(url: str, api_key: str, prompt: str, thread_id: str) -> dict:
    """POST to /pyapi/search/stream and consume the SSE stream.

    Returns a dict with the assembled response + metadata (latency,
    tokens, agents_used, sources_count, errors).
    """
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    payload = {"Promptquery": prompt, "globalThreadId": thread_id}

    token_buf: list[str] = []
    final_response: str | None = None
    agents_used: list[str] = []
    sources_count = 0
    token_usage: dict | None = None
    errors: list[str] = []
    events_seen = 0
    cached = False

    t0 = time.time()
    try:
        with requests.post(url, json=payload, headers=headers,
                           stream=True, timeout=REQUEST_TIMEOUT_S) as r:
            r.raise_for_status()
            for raw in r.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data:"):
                    continue
                data_str = raw[len("data:"):].strip()
                if not data_str:
                    continue
                try:
                    ev = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                events_seen += 1
                t = ev.get("type")
                if t == "token":
                    chunk = ev.get("content") or ev.get("data") or ""
                    if chunk:
                        token_buf.append(chunk)
                elif t == "token_reset":
                    token_buf.clear()
                elif t == "response":
                    final_response = ev.get("content") or ev.get("data")
                elif t == "sources":
                    sources_count = len(ev.get("data") or [])
                elif t == "agents_planned":
                    agents_used = ev.get("agents") or []
                elif t == "error":
                    errors.append(str(ev.get("data")))
                elif t == "status" and "cached" in (ev.get("message") or "").lower():
                    cached = True
                elif t == "done":
                    # Some events (like `done`) may carry token_usage
                    if isinstance(ev.get("token_usage"), dict):
                        token_usage = ev["token_usage"]
                    break
    except requests.exceptions.RequestException as e:
        errors.append(f"http_error: {e}")

    elapsed = time.time() - t0
    response_text = final_response or "".join(token_buf) or ""

    return {
        "response": response_text,
        "response_len": len(response_text),
        "latency_s": round(elapsed, 2),
        "agents_used": agents_used,
        "sources_count": sources_count,
        "token_usage": token_usage,
        "total_tokens": (token_usage or {}).get("total_tokens") if token_usage else None,
        "events_seen": events_seen,
        "errors": errors,
        "cached": cached,
    }


def score_turn(turn: dict, result: dict) -> dict:
    """Score a single turn's response against its expected criteria."""
    resp_lower = (result["response"] or "").lower()

    # contains_expected: % of expected strings present (case-insensitive)
    expected = turn.get("expected_contains") or []
    found = [s for s in expected if s.lower() in resp_lower]
    contains_pct = (len(found) / len(expected)) if expected else 1.0

    # forbidden markers
    forbidden = turn.get("expected_not_contains") or []
    hit_forbidden = [s for s in forbidden if s.lower() in resp_lower]

    # agent routing
    expected_agent = turn.get("expected_agent", "Newacts")
    agent_ok = expected_agent in (result.get("agents_used") or [])

    # mismatch note (for case B / case G rounds)
    mismatch_ok = None
    if turn.get("should_note_mismatch"):
        indicators = turn.get("mismatch_indicators") or []
        mismatch_ok = any(ind.lower() in resp_lower for ind in indicators)

    # Overall pass: 60% contains + no forbidden + correct agent + (mismatch if req'd)
    pass_ = (
        contains_pct >= 0.6
        and not hit_forbidden
        and agent_ok
        and (mismatch_ok is not False)  # None (not required) or True (required-and-met)
    )

    return {
        "pass": pass_,
        "contains_pct": round(contains_pct, 2),
        "contains_found": found,
        "contains_missing": [s for s in expected if s.lower() not in resp_lower],
        "forbidden_hit": hit_forbidden,
        "agent_ok": agent_ok,
        "mismatch_ok": mismatch_ok,
        "response_preview": (result["response"] or "")[:400].replace("\n", " "),
    }


def run_all(url: str, api_key: str) -> dict:
    print(f"[start] {len(ROUNDS)} rounds, {total_turns()} turns total")
    started_at = datetime.now().isoformat(timespec="seconds")

    results: list[dict] = []
    for round_ in ROUNDS:
        thread_id = str(uuid.uuid4())
        print(f"\n[round {round_['id']:>2}] {round_['description']} "
              f"({','.join(round_['tags'])})")
        round_turns: list[dict] = []
        for turn in round_["turns"]:
            print(f"  [turn {turn['id']}] {turn['prompt'][:70]}{'...' if len(turn['prompt']) > 70 else ''}")
            call_result = call_stream(url, api_key, turn["prompt"], thread_id)
            score = score_turn(turn, call_result)
            print(f"    -> pass={score['pass']}  "
                  f"contains={score['contains_pct']*100:.0f}%  "
                  f"agent_ok={score['agent_ok']}  "
                  f"latency={call_result['latency_s']}s")
            if score["forbidden_hit"]:
                print(f"    !! forbidden hit: {score['forbidden_hit']}")
            if score["contains_missing"]:
                print(f"    ~~ missing: {score['contains_missing']}")
            round_turns.append({
                "turn_id": turn["id"],
                "prompt": turn["prompt"],
                "score": score,
                "call": {k: v for k, v in call_result.items() if k != "response"},
                "response": call_result["response"],
            })
            time.sleep(SLEEP_BETWEEN_TURNS_S)

        round_pass = all(t["score"]["pass"] for t in round_turns)
        results.append({
            "round_id": round_["id"],
            "description": round_["description"],
            "tags": round_["tags"],
            "thread_id": thread_id,
            "round_pass": round_pass,
            "turns": round_turns,
        })
        time.sleep(SLEEP_BETWEEN_ROUNDS_S)

    finished_at = datetime.now().isoformat(timespec="seconds")

    # Aggregates
    all_turns = [t for r in results for t in r["turns"]]
    total = len(all_turns)
    passed = sum(1 for t in all_turns if t["score"]["pass"])
    latencies = [t["call"]["latency_s"] for t in all_turns]
    total_tokens = sum(
        (t["call"].get("total_tokens") or 0) for t in all_turns
    )

    summary = {
        "started_at": started_at,
        "finished_at": finished_at,
        "rounds_total": len(results),
        "rounds_passed": sum(1 for r in results if r["round_pass"]),
        "turns_total": total,
        "turns_passed": passed,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "latency_avg_s": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
        "latency_p50_s": round(sorted(latencies)[len(latencies) // 2], 2) if latencies else 0.0,
        "latency_p95_s": round(sorted(latencies)[int(len(latencies) * 0.95)], 2) if latencies else 0.0,
        "latency_total_s": round(sum(latencies), 2),
        "total_tokens_reported": total_tokens,
    }

    return {"summary": summary, "results": results}


def write_reports(run: dict, label: str) -> tuple[Path, Path]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    slug = f"{label}_{stamp}"
    json_path = RESULTS_DIR / f"{slug}.json"
    md_path = RESULTS_DIR / f"{slug}.md"

    # JSON: full detail for machine consumption
    json_path.write_text(json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")

    # Markdown: human-readable
    summary = run["summary"]
    lines: list[str] = []
    lines.append(f"# Newacts evaluation run — {label}\n")
    lines.append(f"- Started: `{summary['started_at']}` → finished: `{summary['finished_at']}`")
    lines.append(f"- Rounds passed: **{summary['rounds_passed']}/{summary['rounds_total']}**")
    lines.append(f"- Turns passed: **{summary['turns_passed']}/{summary['turns_total']}** "
                 f"({summary['pass_rate']*100:.1f}%)")
    lines.append(f"- Latency: avg {summary['latency_avg_s']}s | "
                 f"p50 {summary['latency_p50_s']}s | "
                 f"p95 {summary['latency_p95_s']}s | "
                 f"total {summary['latency_total_s']}s")
    lines.append(f"- Total tokens (reported): {summary['total_tokens_reported']}")
    lines.append("")
    lines.append("## Per-round results\n")

    for r in run["results"]:
        badge = "PASS" if r["round_pass"] else "FAIL"
        lines.append(f"### Round {r['round_id']} — {r['description']}  `[{badge}]`")
        lines.append(f"*tags: {', '.join(r['tags'])}*  |  thread `{r['thread_id'][:8]}`\n")
        for t in r["turns"]:
            sc = t["score"]
            call = t["call"]
            tbadge = "PASS" if sc["pass"] else "FAIL"
            lines.append(f"**Turn {t['turn_id']}** `[{tbadge}]` "
                         f"contains={sc['contains_pct']*100:.0f}%  "
                         f"latency={call['latency_s']}s  "
                         f"agents={call.get('agents_used', [])}")
            lines.append(f"- prompt: `{t['prompt']}`")
            if sc["contains_missing"]:
                lines.append(f"- missing: `{sc['contains_missing']}`")
            if sc["forbidden_hit"]:
                lines.append(f"- forbidden hit: `{sc['forbidden_hit']}`")
            if sc["mismatch_ok"] is not None:
                lines.append(f"- mismatch note: {'yes' if sc['mismatch_ok'] else 'no'}")
            lines.append(f"- response preview: `{sc['response_preview']}`")
            lines.append("")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="baseline",
                    help="Label for the results files (e.g. baseline, postfix)")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--key", default=DEFAULT_KEY)
    args = ap.parse_args()

    run = run_all(args.url, args.key)
    json_path, md_path = write_reports(run, args.label)

    print("\n" + "=" * 60)
    print(f"[done] label={args.label}")
    print(f"  json: {json_path}")
    print(f"  md:   {md_path}")
    s = run["summary"]
    print(f"  rounds:  {s['rounds_passed']}/{s['rounds_total']}")
    print(f"  turns:   {s['turns_passed']}/{s['turns_total']} ({s['pass_rate']*100:.1f}%)")
    print(f"  latency: avg {s['latency_avg_s']}s, total {s['latency_total_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
