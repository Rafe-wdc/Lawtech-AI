r"""Find and check the index values for config/judge_registry.json.

The registry is written by a person: this script only lists what the indexes
contain and checks the file against them. Read-only; the app's search account
is enough.

Usage (from the repo root, project venv):
    python scripts/build_judge_registry.py top --index sci_judgments --n 40
    python scripts/build_judge_registry.py candidates Muralidhar "Devan Ramachandran" --out cands.json
    python scripts/build_judge_registry.py check
    python scripts/build_judge_registry.py check --registry path\to\other.json

candidates: for each name, the Supreme Court bench count for the name as a
phrase, and every High Court bench value containing it (case-insensitive),
with judgment counts, courts and first/last dates. Supreme Court author-field
values are listed for reference only (the field often names the presiding
judge, so the registry doesn't use it). Pick the values that are this judge,
never another with a similar name, and copy them into the registry.

check: for every judge and source, how many judgments match as bench member,
the date span, and any listed value that matches nothing (exit code 1).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# Importing core.clients loads the local embedding models unless this is set;
# this script never embeds.
os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

from core.clients import get_es_client  # noqa: E402
from core.judge_registry import (  # noqa: E402
    LAYOUTS, REGISTRY_PATH, JudgeSource, RegistryError, bench_filter, load_registry,
)

TIMEOUT = 120


def _ci_regex(fragment: str) -> str:
    """Case-insensitive Lucene regexp matching values that contain `fragment`."""
    out = []
    for ch in fragment:
        if ch.isalpha():
            out.append(f"[{ch.upper()}{ch.lower()}]")
        elif ch.isalnum() or ch == " ":
            out.append(ch)
        else:
            out.append("\\" + ch)
    return ".*" + "".join(out) + ".*"


def _span(es, index: str, query: dict, date_field: str) -> dict:
    body = {"size": 0, "query": query, "track_total_hits": True, "aggs": {
        "first": {"min": {"field": date_field, "format": "yyyy-MM-dd"}},
        "last": {"max": {"field": date_field, "format": "yyyy-MM-dd"}},
    }}
    r = es.search(index=index, body=body, request_timeout=TIMEOUT)
    a = r["aggregations"]
    return {"count": r["hits"]["total"]["value"],
            "first": a["first"].get("value_as_string"), "last": a["last"].get("value_as_string")}


def _value_rows(es, layout, field: str, include: str, extra_filter: dict | None = None, size: int = 60) -> list[dict]:
    aggs = {"v": {"terms": {"field": field, "include": include, "size": size}, "aggs": {
        "first": {"min": {"field": layout.date_field, "format": "yyyy-MM-dd"}},
        "last": {"max": {"field": layout.date_field, "format": "yyyy-MM-dd"}},
    }}}
    if layout.court_field:
        aggs["v"]["aggs"]["courts"] = {"terms": {"field": layout.court_field, "size": 6}}
    body = {"size": 0, "aggs": aggs, "track_total_hits": False}
    if extra_filter:
        body["query"] = extra_filter
    r = es.search(index=layout.index, body=body, request_timeout=TIMEOUT)
    rows = []
    for b in r["aggregations"]["v"]["buckets"]:
        rows.append({
            "value": b["key"], "judgments": b["doc_count"],
            "first": b["first"].get("value_as_string"), "last": b["last"].get("value_as_string"),
            "courts": {c["key"]: c["doc_count"] for c in b.get("courts", {}).get("buckets", [])},
        })
    return rows


def cmd_top(es, args) -> None:
    lay = LAYOUTS[args.index]
    field = lay.author_field if lay.bench_kind == "text" else lay.bench_field
    body = {"size": 0, "aggs": {"v": {"terms": {"field": field, "size": args.n}}}}
    r = es.search(index=lay.index, body=body, request_timeout=TIMEOUT)
    print(f"Most frequent {field} values in {lay.index}:")
    for b in r["aggregations"]["v"]["buckets"]:
        print(f"  {b['doc_count']:7}  {b['key']!r}")


def cmd_candidates(es, args) -> None:
    sc, hc = LAYOUTS["sci_judgments"], LAYOUTS["judgments"]
    report = {}
    for name in args.names:
        inc = _ci_regex(name)
        entry = {
            "sci_judgments": {
                "bench_phrase": {"phrase": name.upper(), **_span(es, sc.index, bench_filter(
                    JudgeSource(index_key="sci_judgments", bench_phrases=(name.upper(),))), sc.date_field)},
                "author_field_values": _value_rows(es, sc, sc.author_field, inc),
            },
            "judgments": {"bench_values": _value_rows(es, hc, hc.bench_field, inc)},
        }
        report[name] = entry
        print(f"\n=== {name}")
        bp = entry["sci_judgments"]["bench_phrase"]
        print(f"  Supreme Court bench, phrase {bp['phrase']!r}: {bp['count']} judgments ({bp['first']} .. {bp['last']})")
        for row in entry["sci_judgments"]["author_field_values"]:
            print(f"  SC author field  {row['judgments']:6}  {row['first']} .. {row['last']}  {row['value']!r}  (reference only)")
        for row in entry["judgments"]["bench_values"]:
            courts = ", ".join(f"{c} {n}" for c, n in row["courts"].items())
            print(f"  HC bench value   {row['judgments']:6}  {row['first']} .. {row['last']}  {row['value']!r}  [{courts}]")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWritten: {args.out}")


def cmd_check(es, args) -> int:
    try:
        reg = load_registry(args.registry)
    except (RegistryError, OSError, json.JSONDecodeError) as e:
        print(f"Registry invalid: {e}")
        return 1
    problems = 0
    for j in reg.judges:
        flag = "" if j.verified else "   [not yet verified by a person]"
        print(f"\n{j.judge_id}  {j.name}  ({', '.join(j.courts)}; {j.status}){flag}")
        for s in j.sources:
            lay = s.layout
            span = _span(es, lay.index, bench_filter(s), lay.date_field)
            print(f"  {lay.index}: sat on {span['count']} judgments, {span['first']} .. {span['last']}")
            if span["count"] == 0:
                problems += 1
                print("    PROBLEM: no judgments match this source")
            singles = [(f"bench phrase {p!r}", bench_filter(JudgeSource(s.index_key, bench_phrases=(p,))))
                       for p in s.bench_phrases]
            singles += [(f"bench value {v!r}", {"bool": {"filter": [
                {"term": {lay.bench_field: v}}, {"terms": {lay.court_field: list(s.court_names)}}]}})
                for v in s.bench_values]
            for label, q in singles:
                n = es.count(index=lay.index, body={"query": q}, request_timeout=TIMEOUT)["count"]
                print(f"    {label}: {n}")
                if n == 0:
                    problems += 1
                    print("    PROBLEM: matches nothing (typo, or wrong court)")
            for c in s.court_names:
                n = es.count(index=lay.index, body={"query": {"term": {lay.court_field: c}}},
                             request_timeout=TIMEOUT)["count"]
                if n == 0:
                    problems += 1
                    print(f"    PROBLEM: court {c!r} matches nothing")
    print(f"\n{len(reg.judges)} judges, {problems} problem(s)")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("top", help="most frequent author / bench values")
    p.add_argument("--index", choices=sorted(LAYOUTS), default="sci_judgments")
    p.add_argument("--n", type=int, default=40)
    p = sub.add_parser("candidates", help="index values containing each name")
    p.add_argument("names", nargs="+")
    p.add_argument("--out")
    p = sub.add_parser("check", help="check the registry against the index")
    p.add_argument("--registry", default=str(REGISTRY_PATH))
    args = ap.parse_args()
    for name in getattr(args, "names", []) or []:
        if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", name):
            ap.error(f"name {name!r}: letters, spaces, dots, hyphens and apostrophes only")
    es = get_es_client(timeout=TIMEOUT)
    if args.cmd == "top":
        cmd_top(es, args)
    elif args.cmd == "candidates":
        cmd_candidates(es, args)
    else:
        return cmd_check(es, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
