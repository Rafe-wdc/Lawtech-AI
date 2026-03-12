"""ES Gap Report — Weekly Backfill Intelligence

Reads fallback_log (queries that hit web search fallback) and clusters
them by semantic similarity to surface the top topics missing from ES.

Usage:
    python scripts/gap_report.py              # last 7 days
    python scripts/gap_report.py --days 30    # last 30 days
    python scripts/gap_report.py --agent Judgment  # filter by agent

Output: console report + optional markdown file

This script is meant to run offline (not in the request path).
Run manually or schedule as a weekly cron job.
"""

from __future__ import annotations

import argparse
import sys
import os
from collections import defaultdict
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3
import json
from datetime import datetime


def load_fallback_logs(db_path: str, days: int, agent: str | None) -> list[dict]:
    """Load unbackfilled fallback logs from SQLite."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    conditions = ["backfilled = 0",
                  f"timestamp >= datetime('now', '-{days} days')"]
    params = []
    if agent:
        conditions.append("agent = ?")
        params.append(agent)

    where = " AND ".join(conditions)
    rows = conn.execute(f"""
        SELECT id, agent, query, original_query, tokens, timestamp, web_sources_json
        FROM fallback_log
        WHERE {where}
        ORDER BY timestamp DESC
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def group_by_agent(logs: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in logs:
        groups[row["agent"]].append(row)
    return dict(groups)


def simple_cluster(queries: list[str], min_word_overlap: int = 2) -> list[list[str]]:
    """Very simple word-overlap clustering (no ML needed for gap report).

    Groups queries that share ≥ min_word_overlap meaningful words.
    Returns list of clusters (each cluster is a list of query strings).
    """
    stopwords = {
        "what", "is", "the", "of", "in", "a", "an", "for", "to", "and",
        "or", "how", "does", "do", "can", "with", "under", "section", "act",
        "india", "indian", "court", "case", "law", "legal"
    }

    def keywords(q: str) -> set[str]:
        return {w.lower() for w in q.split() if len(w) > 3 and w.lower() not in stopwords}

    clusters: list[list[str]] = []
    used = set()

    for i, q in enumerate(queries):
        if i in used:
            continue
        cluster = [q]
        kw_i = keywords(q)
        for j, q2 in enumerate(queries):
            if j <= i or j in used:
                continue
            kw_j = keywords(q2)
            if len(kw_i & kw_j) >= min_word_overlap:
                cluster.append(q2)
                used.add(j)
        used.add(i)
        clusters.append(cluster)

    return sorted(clusters, key=len, reverse=True)


def generate_report(logs: list[dict], days: int, agent_filter: str | None) -> str:
    """Generate a markdown gap report."""
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"# Lawtech-AI ES Gap Report")
    lines.append(f"Generated: {now} | Period: last {days} days"
                 + (f" | Agent: {agent_filter}" if agent_filter else ""))
    lines.append("")

    if not logs:
        lines.append("No unbackfilled fallback entries found.")
        return "\n".join(lines)

    lines.append(f"**Total web fallbacks (not yet backfilled): {len(logs)}**")
    lines.append("")

    by_agent = group_by_agent(logs)

    for agent, agent_logs in sorted(by_agent.items(), key=lambda x: -len(x[1])):
        lines.append(f"## {agent} — {len(agent_logs)} fallbacks")
        lines.append("")

        queries = [r["original_query"] or r["query"] for r in agent_logs]
        clusters = simple_cluster(queries)

        lines.append(f"### Top Topic Clusters (ES gaps to backfill)")
        lines.append("")
        for i, cluster in enumerate(clusters[:10], 1):
            rep = cluster[0][:100]
            lines.append(f"{i}. **{rep}** ({'+ ' + str(len(cluster)-1) + ' similar' if len(cluster) > 1 else 'unique'})")

        lines.append("")
        lines.append("### All Queries")
        lines.append("| # | Query | Tokens | Date |")
        lines.append("|---|-------|--------|------|")
        for i, row in enumerate(agent_logs[:50], 1):
            q = (row["original_query"] or row["query"])[:80]
            ts = row["timestamp"][:10]
            lines.append(f"| {i} | {q} | {row['tokens']} | {ts} |")
        if len(agent_logs) > 50:
            lines.append(f"| ... | _{len(agent_logs) - 50} more_ | | |")

        lines.append("")

    lines.append("---")
    lines.append("*Run `python scripts/gap_report.py` to refresh.*")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="ES Gap Report")
    parser.add_argument("--days", type=int, default=7, help="Days to look back")
    parser.add_argument("--agent", type=str, default=None, help="Filter by agent name")
    parser.add_argument("--output", type=str, default=None,
                        help="Save report to this markdown file path")
    args = parser.parse_args()

    # Locate DB
    project_root = Path(__file__).resolve().parent.parent
    db_candidates = [
        project_root / "data" / "chat_history.db",
        project_root / "chat_history.db",
    ]
    db_path = next((str(p) for p in db_candidates if p.exists()), None)
    if not db_path:
        print("ERROR: chat_history.db not found. Run the app first to initialize the DB.")
        sys.exit(1)

    print(f"Reading from: {db_path}")
    logs = load_fallback_logs(db_path, args.days, args.agent)
    report = generate_report(logs, args.days, args.agent)

    print(report)

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"\nReport saved to: {args.output}")
    else:
        # Auto-save to docs/
        out_path = project_root / "docs" / "gap_report.md"
        out_path.write_text(report, encoding="utf-8")
        print(f"\nReport auto-saved to: {out_path}")


if __name__ == "__main__":
    main()
