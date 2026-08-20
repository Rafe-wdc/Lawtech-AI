"""Turn the raw JSON from run_thread_prompt_suite.py into the two markdown reports.

  1. API report      — user prompt + the exact HTTP request the API received +
                       the exact response, per turn. One file per environment.
  2. Flow analysis   — step-by-step pipeline behaviour for a run, reconstructed
                       from the SSE status timeline, the per-LLM-call token
                       ledger, and (when captured) the server log slices.

Usage:
    python tests/gen_thread_report.py --raw docs/test-runs/prod_raw.json \
        --api-report docs/test-runs/API_THREAD_TEST_REPORT_PROD.md

    python tests/gen_thread_report.py --raw docs/test-runs/local_raw.json \
        --api-report docs/test-runs/API_THREAD_TEST_REPORT_LOCAL.md \
        --flow-report docs/test-runs/LOCAL_FLOW_ANALYSIS.md \
        --log-dir docs/test-runs/local_log
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

# Log lines worth surfacing in the flow narrative — everything else is noise.
FLOW_PATTERNS = [
    r"Routing to", r"Fan-out routing", r"Graph ", r"classif", r"Classified",
    r"Tasks planned", r"[Ii]ntent", r"language", r"Language", r"rewrit", r"Rewrit",
    r"ES search", r"Elasticsearch", r"search returned", r"hits", r"Reference draft",
    r"Fan-out judge", r"Section pair", r"self.refine", r"Self.refine", r"[Cc]ritique",
    r"fallback", r"Fallback", r"[Ww]eb search", r"relevance", r"Relevance",
    r"Cache hit", r"Cache ", r"blocked", r"Blocked", r"guardrail", r"Guardrail",
    r"[Ss]ynthes", r"Vision", r"OCR", r"[Cc]hunk", r"Chroma", r"embedding",
    r"[Tt]imeout", r"ERR", r"WRN", r"[Ee]rror", r"[Ff]ailed", r"[Rr]etry",
    r"File ", r"[Uu]pload", r"[Dd]ocument", r"turn", r"Turn", r"history",
]
_FLOW_RE = re.compile("|".join(FLOW_PATTERNS))

LOG_LINE_RE = re.compile(
    r"^(?P<ts>[\d\-]+ [\d:.]+) \| (?P<lvl>\w+) \| \[(?P<comp>[^\]]+)\] "
    r"(?:req=(?P<req>\w+) \| )?(?P<msg>.*)$"
)


def fmt_int(n) -> str:
    return f"{n:,}" if isinstance(n, (int, float)) else "—"


def load(raw_path: Path) -> dict:
    return json.loads(raw_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Report 1 — user prompt + exact API request + exact response
# ---------------------------------------------------------------------------

def gen_api_report(run: dict, out: Path) -> None:
    L: list[str] = []
    turns = [t for t in run["turns"] if "record" in t]
    ok = [t for t in turns if not t["record"].get("error") and not t["summary"]["errors"]]

    L += [
        f"# Lawttorney API — single-thread prompt suite ({run['label']})",
        "",
        "Every prompt in `docs/test prompt.txt`, sent **in order, in one thread**, so",
        "each turn sees the previous turns' history (memory agent, query rewriting,",
        "per-turn typed state). Prompts are verbatim from the source file, typos included.",
        "",
        "| | |",
        "|---|---|",
        f"| Environment | `{run['base_url']}` |",
        f"| Endpoint | `POST /pyapi/chat` (multipart/form-data → SSE) |",
        f"| `globalThreadId` | `{run['thread_id']}` |",
        f"| Started (UTC) | {run.get('started_at', '—')} |",
        f"| Finished (UTC) | {run.get('finished_at', '—')} |",
        f"| Turns | {len(turns)} sent, {len(ok)} completed without error |",
        "",
    ]

    # --- Overview table ---
    L += [
        "## Turn overview",
        "",
        "| # | Section | Prompt (short) | Agents used | Latency | Response chars | Tokens | Cost |",
        "|---|---------|----------------|-------------|---------|----------------|--------|------|",
    ]
    tot_tokens = tot_cost = 0.0
    for t in turns:
        s, r, meta = t["summary"], t["record"], t["turn"]
        tu = s.get("token_usage") or {}
        tot_tokens += tu.get("total_tokens", 0) or 0
        tot_cost += tu.get("cost_usd", 0) or 0
        short = meta["label"][:42]
        agents = ", ".join(s["agents_used"]) or ("**ERROR**" if r.get("error") else "—")
        L.append(
            f"| {meta['n']} | {meta['section']} | {short} | {agents} | "
            f"{r.get('elapsed_s', 0):.1f}s | {fmt_int(len(s['response']))} | "
            f"{fmt_int(tu.get('total_tokens', 0))} | ${tu.get('cost_usd', 0):.4f} |"
        )
    L += ["", f"**Run totals:** {fmt_int(int(tot_tokens))} tokens, ${tot_cost:.4f}.", ""]

    # --- Per-turn detail ---
    L += ["---", "", "## Per-turn detail", ""]
    for t in turns:
        meta, rec, s = t["turn"], t["record"], t["summary"]
        req = rec["request"]
        L += [
            f"### Turn {meta['n']} — {meta['label']}",
            "",
            f"*Source section in `docs/test prompt.txt`: **{meta['section']}***",
            "",
            "#### 1. User prompt (verbatim)",
            "",
            "```text",
            meta["query"],
            "```",
            "",
            "#### 2. Exact API request",
            "",
            "```http",
            f"POST {req['url']} HTTP/1.1",
            "Content-Type: multipart/form-data",
            "X-API-Key: <redacted>",
            "Accept: text/event-stream",
            "```",
            "",
            "Form fields:",
            "",
            "| Field | Value |",
            "|-------|-------|",
            f"| `query` | *(the user prompt above, {len(meta['query'])} chars, unmodified)* |",
            f"| `globalThreadId` | `{req['form_fields']['globalThreadId']}` |",
        ]
        for f in req.get("files", []):
            L.append(
                f"| `{f['field']}` | `{f['filename']}` — {fmt_int(f['size_bytes'])} bytes, "
                f"`{f['content_type']}` |"
            )
        if not req.get("files"):
            L.append("| `files` | *(none)* |")

        # curl equivalent
        curl = [
            f"curl -N -X POST {req['url']} \\",
            "  -H 'X-API-Key: <key>' \\",
            f"  -F 'globalThreadId={req['form_fields']['globalThreadId']}' \\",
        ]
        for f in req.get("files", []):
            curl.append(f"  -F 'files=@{f['filename']};type=application/pdf' \\")
        curl.append("  -F 'query=<prompt above>'")
        L += ["", "Reproduce with:", "", "```bash", *curl, "```", ""]

        # Server-side derived request state
        ctx = s.get("context") or {}
        L += [
            "#### 3. What the server made of it",
            "",
            "| Signal | Value |",
            "|--------|-------|",
            f"| HTTP status | `{rec.get('http_status')}` |",
            f"| Conversation turn | {s.get('conversation_turn')} |",
            f"| History turns loaded | {ctx.get('history_turns', '—')} |",
            f"| Query rewritten by memory agent | {s.get('query_rewritten')} |",
            f"| Effective query after rewrite | {('`' + str(s['effective_query'])[:200] + '`') if s.get('effective_query') else '*(unchanged)*'} |",
            f"| Agents planned | {', '.join(s['agents_planned']) or '—'} |",
            f"| Agents used | {', '.join(s['agents_used']) or '—'} |",
            f"| Sources returned | {len(s['sources'])} |",
            f"| Wall clock | {rec.get('elapsed_s', 0):.1f}s |",
            f"| First streamed token | {rec.get('first_token_s') if rec.get('first_token_s') is not None else '—'}s |",
            "",
        ]
        if s.get("file_processing"):
            L += ["File processing events:", ""]
            for fp in s["file_processing"]:
                L.append(f"- `t+{fp.get('t')}s` — {fp.get('message') or fp.get('stage')}")
            L.append("")
        if rec.get("error") or s.get("errors"):
            L += ["> **Error:** " + str(rec.get("error") or s["errors"]), ""]

        # Sources
        if s["sources"]:
            L += ["<details>", "<summary>Sources cited back to the client "
                  f"({len(s['sources'])})</summary>", "",
                  "| # | Type | Title | Agent | Score |",
                  "|---|------|-------|-------|-------|"]
            for k, src in enumerate(s["sources"], 1):
                title = str(src.get("title") or src.get("file_name") or "—")[:70]
                L.append(
                    f"| {k} | {src.get('source_type', '—')} | {title} | "
                    f"{src.get('agent_name', '—')} | {src.get('relevance_score', '—')} |"
                )
            L += ["", "</details>", ""]

        # Response
        L += [
            "#### 4. Exact API response",
            "",
            "<details>",
            f"<summary>Full response text ({fmt_int(len(s['response']))} chars) — click to expand</summary>",
            "",
            s["response"] or "*(empty)*",
            "",
            "</details>",
            "",
        ]
        if s.get("followups"):
            L += ["Follow-up suggestions returned: "
                  + ", ".join(f"`{x}`" for x in s["followups"]), ""]
        L += ["---", ""]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"  api report  -> {out}")


# ---------------------------------------------------------------------------
# Report 2 — step-by-step flow analysis
# ---------------------------------------------------------------------------

def _log_lines(log_dir: Path | None, n: int, req_id: str | None = None) -> list[dict]:
    """Parse a turn's log slice.

    `logs/agent.log` is shared by every process rooted at this repo (a
    `--reload` dev server on :5050 was also writing to it during the run), so
    lines are filtered down to this request's id — `thread_id[:8]`, set by
    `set_request_id` in the /pyapi/chat handler — before anything is reported.
    """
    if not log_dir:
        return []
    p = log_dir / f"turn{n:02d}.log"
    if not p.exists():
        return []
    out = []
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LOG_LINE_RE.match(raw)
        if not m:
            continue
        d = m.groupdict()
        if req_id and (d.get("req") or "") != req_id:
            continue
        out.append(d)
    return out


def gen_flow_report(run: dict, out: Path, log_dir: Path | None) -> None:
    L: list[str] = []
    turns = [t for t in run["turns"] if "record" in t]

    L += [
        f"# Local run — step-by-step pipeline behaviour",
        "",
        f"Same suite, same single thread, run against `{run['base_url']}`.",
        "Every step below is reconstructed from three independent sources captured",
        "during the run:",
        "",
        "1. **SSE status timeline** — the graph node the runner was told about, with",
        "   the wall-clock offset at which the event arrived.",
        "2. **Per-LLM-call token ledger** — the `done` event's `token_usage.calls[]`,",
        "   which records every model call as `(agent, step, in, out, cache_read)`.",
        "3. **Server log slice** — `logs/agent.log`, sliced by byte offset around each",
        "   turn, so the internal decisions (routing, ES hits, fan-out judge,",
        "   self-refine, fallbacks) are attributable to the exact turn that caused them.",
        "",
        f"| | |",
        "|---|---|",
        f"| Thread | `{run['thread_id']}` |",
        f"| Started (UTC) | {run.get('started_at', '—')} |",
        f"| Finished (UTC) | {run.get('finished_at', '—')} |",
        "",
        "## The pipeline being exercised",
        "",
        "```",
        "POST /pyapi/chat",
        "  ├─ file staging          (only when files[] present: extract → Chroma → Gemini Files)",
        "  └─ core.chat_runner.run_chat_pipeline",
        "       └─ LangGraph  (core/graph.py)",
        "            START → guardrail_input ──[blocked?]──→ blocked_response ─┐",
        "                         │                                            │",
        "                         └─→ memory → orchestrator_plan               │",
        "                                          │                           │",
        "                              ┌───────────┴─── Send() fan-out ───┐    │",
        "                          domain agent            domain agent   …    │",
        "                              └───────────┬──────────────────────┘    │",
        "                                orchestrator_synthesize               │",
        "                                          │                           │",
        "                                   guardrail_output ←─────────────────┘",
        "                                          │",
        "                                         END",
        "```",
        "",
        "SSE status strings map 1:1 onto those nodes via `_NODE_STATUS` in",
        "`core/gateway.py`, which is what makes the timeline below a real node trace",
        "rather than cosmetic progress text.",
        "",
        "---",
        "",
    ]

    for t in turns:
        meta, rec, s = t["turn"], t["record"], t["summary"]
        tu = s.get("token_usage") or {}
        calls = tu.get("calls", []) or []
        logs = _log_lines(log_dir, meta["n"], run["thread_id"][:8])

        L += [
            f"## Turn {meta['n']} — {meta['label']}",
            "",
            f"**Prompt ({meta['section']}):** {meta['query'][:300]}"
            + ("…" if len(meta["query"]) > 300 else ""),
            "",
            f"**Outcome:** {rec.get('elapsed_s', 0):.1f}s · agents "
            f"`{', '.join(s['agents_used']) or '—'}` · "
            f"{fmt_int(len(s['response']))} chars · "
            f"{fmt_int(tu.get('total_tokens', 0))} tokens · "
            f"${tu.get('cost_usd', 0):.4f} · {len(calls)} LLM calls",
            "",
            "### Step 1 — node timeline (from SSE)",
            "",
            "| t+ | Event | Node it maps to |",
            "|----|-------|-----------------|",
        ]
        node_for = {
            "Validating query...": "`guardrail_input`",
            "Loading context...": "`memory`",
            "Planning search strategy...": "`orchestrator_plan`",
            "Generating legal draft...": "`drafting`",
            "Searching legislation...": "`legislation`",
            "Searching court judgments...": "`judgment`",
            "Searching Supreme Court judgments...": "`sci_judgment`",
            "Searching legal provisions...": "`newacts`",
            "Analyzing legal scenario...": "`scenario`",
            "Searching constitutional provisions...": "`constitution`",
            "Searching legal maxims...": "`maxim`",
            "Explaining legal concepts...": "`legal_concepts`",
            "Searching uploaded documents...": "`document`",
            "Injecting citations into draft...": "`orchestrator_synthesize`",
            "Finalizing...": "`guardrail_output`",
            "Query blocked.": "`blocked_response`",
        }
        for fp in s.get("file_processing", []):
            L.append(f"| {fp.get('t', '—')}s | file_processing — "
                     f"{str(fp.get('message') or fp.get('stage'))[:70]} | *(pre-graph)* |")
        for st in s["statuses"]:
            msg = st["message"] or ""
            L.append(f"| {st['t']}s | {msg} | {node_for.get(msg, '—')} |")
        L += ["", f"Planned by orchestrator: `{', '.join(s['agents_planned']) or '—'}` → "
              f"actually reported used: `{', '.join(s['agents_used']) or '—'}`.", ""]

        # Step 2 — LLM call ledger
        L += [
            "### Step 2 — every LLM call, in order",
            "",
            "| # | Agent | Step | In | Out | Cache read | Cost |",
            "|---|-------|------|----|-----|------------|------|",
        ]
        for k, c in enumerate(calls, 1):
            L.append(
                f"| {k} | {c.get('agent')} | `{c.get('step')}` | {fmt_int(c.get('input'))} | "
                f"{fmt_int(c.get('output'))} | {fmt_int(c.get('cache_read'))} | "
                f"${c.get('cost_usd', 0):.4f} |"
            )
        if not calls:
            L.append("| — | *(no calls recorded)* | | | | | |")
        L.append("")

        by_agent = tu.get("by_agent") or {}
        if by_agent:
            L += ["Roll-up by agent:", "",
                  "| Agent | Calls | In | Out | Cache read | Cost |",
                  "|-------|-------|----|-----|------------|------|"]
            for a, v in sorted(by_agent.items(), key=lambda kv: -kv[1].get("total", 0)):
                L.append(
                    f"| {a} | {v.get('calls')} | {fmt_int(v.get('input'))} | "
                    f"{fmt_int(v.get('output'))} | {fmt_int(v.get('cache_read'))} | "
                    f"${v.get('cost_usd', 0):.4f} |"
                )
            L.append("")

        # Step 3 — server-side decisions from the log
        L += ["### Step 3 — internal decisions (server log)", ""]
        interesting = [l for l in logs if _FLOW_RE.search(l["msg"])]
        warns = [l for l in logs if l["lvl"] in ("WRN", "ERR")]
        if interesting:
            L += ["```log"]
            for l in interesting[:70]:
                L.append(f"{l['ts'][11:]} {l['lvl']} [{l['comp']}] {l['msg'][:230]}")
            if len(interesting) > 70:
                L.append(f"... {len(interesting) - 70} more matching lines")
            L += ["```", ""]
        else:
            L += ["*(no server log slice captured for this turn)*", ""]

        if warns:
            L += [f"**Warnings/errors in this turn ({len(warns)}):**", ""]
            seen = Counter()
            for l in warns:
                key = f"{l['comp']}: {l['msg'][:150]}"
                seen[key] += 1
            for k, v in seen.most_common(12):
                L.append(f"- `{k}`" + (f" ×{v}" if v > 1 else ""))
            L.append("")

        # Step 4 — what came back
        L += [
            "### Step 4 — what the client got back",
            "",
            f"- Sources: **{len(s['sources'])}**"
            + (f" ({', '.join(sorted({str(x.get('source_type')) for x in s['sources']}))})"
               if s["sources"] else ""),
            f"- Response: **{fmt_int(len(s['response']))} chars**, first 400:",
            "",
            "```text",
            (s["response"][:400] + ("…" if len(s["response"]) > 400 else "")) or "(empty)",
            "```",
            "",
        ]
        if s.get("followups"):
            L.append("- Follow-ups: " + ", ".join(f"`{x}`" for x in s["followups"]))
            L.append("")
        L += ["---", ""]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"  flow report -> {out}")


# ---------------------------------------------------------------------------
# Report 3 — prod vs local, turn by turn
# ---------------------------------------------------------------------------

def gen_compare_report(prod: dict, local: dict, out: Path) -> None:
    L: list[str] = [
        "# Prod vs local — same thread, same prompts, same order",
        "",
        f"- **Prod:** `{prod['base_url']}` · thread `{prod['thread_id']}`",
        f"- **Local:** `{local['base_url']}` · thread `{local['thread_id']}`",
        "",
        "Both runs sent byte-identical prompts in the same order inside a single",
        "thread each. Divergences below are therefore attributable to environment",
        "and to model non-determinism, not to differing inputs.",
        "",
        "## Routing agreement",
        "",
        "| # | Turn | Prod agents | Local agents | Same route? |",
        "|---|------|-------------|--------------|-------------|",
    ]
    pmap = {t["turn"]["n"]: t for t in prod["turns"] if "record" in t}
    lmap = {t["turn"]["n"]: t for t in local["turns"] if "record" in t}
    agree = total = 0
    for n in sorted(set(pmap) | set(lmap)):
        p, l = pmap.get(n), lmap.get(n)
        pa = ", ".join(p["summary"]["agents_used"]) if p else "—"
        la = ", ".join(l["summary"]["agents_used"]) if l else "—"
        label = (p or l)["turn"]["label"][:40]
        same = "yes" if (p and l and set(p["summary"]["agents_used"])
                         == set(l["summary"]["agents_used"])) else "**no**"
        if p and l:
            total += 1
            agree += same == "yes"
        L.append(f"| {n} | {label} | {pa} | {la} | {same} |")
    L += ["", f"Routing agreed on **{agree}/{total}** comparable turns.", ""]

    L += [
        "## Cost, latency and output size",
        "",
        "| # | Prod time | Local time | Prod tokens | Local tokens | Prod $ | Local $ | Prod chars | Local chars |",
        "|---|-----------|------------|-------------|--------------|--------|---------|------------|-------------|",
    ]
    sums = {"pt": 0.0, "lt": 0.0, "ptok": 0, "ltok": 0, "pc": 0.0, "lc": 0.0}
    for n in sorted(set(pmap) | set(lmap)):
        p, l = pmap.get(n), lmap.get(n)
        ptu = (p["summary"].get("token_usage") or {}) if p else {}
        ltu = (l["summary"].get("token_usage") or {}) if l else {}
        sums["pt"] += (p["record"].get("elapsed_s", 0) if p else 0)
        sums["lt"] += (l["record"].get("elapsed_s", 0) if l else 0)
        sums["ptok"] += ptu.get("total_tokens", 0) or 0
        sums["ltok"] += ltu.get("total_tokens", 0) or 0
        sums["pc"] += ptu.get("cost_usd", 0) or 0
        sums["lc"] += ltu.get("cost_usd", 0) or 0
        cells = [
            f"{p['record'].get('elapsed_s', 0):.1f}s" if p else "—",
            f"{l['record'].get('elapsed_s', 0):.1f}s" if l else "—",
            fmt_int(ptu.get("total_tokens", 0)) if p else "—",
            fmt_int(ltu.get("total_tokens", 0)) if l else "—",
            f"${ptu.get('cost_usd', 0):.4f}" if p else "—",
            f"${ltu.get('cost_usd', 0):.4f}" if l else "—",
            fmt_int(len(p["summary"]["response"])) if p else "—",
            fmt_int(len(l["summary"]["response"])) if l else "—",
        ]
        L.append(f"| {n} | " + " | ".join(cells) + " |")
    L += [
        f"| **Total** | **{sums['pt']:.0f}s** | **{sums['lt']:.0f}s** | "
        f"**{fmt_int(sums['ptok'])}** | **{fmt_int(sums['ltok'])}** | "
        f"**${sums['pc']:.2f}** | **${sums['lc']:.2f}** | | |",
        "",
    ]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"  compare     -> {out}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="")
    ap.add_argument("--api-report", default="")
    ap.add_argument("--flow-report", default="")
    ap.add_argument("--log-dir", default="")
    ap.add_argument("--compare", nargs=3, metavar=("PROD_RAW", "LOCAL_RAW", "OUT"),
                    default=None)
    args = ap.parse_args()

    if args.raw:
        run = load(Path(args.raw))
        if args.api_report:
            gen_api_report(run, Path(args.api_report))
        if args.flow_report:
            gen_flow_report(run, Path(args.flow_report),
                            Path(args.log_dir) if args.log_dir else None)
    if args.compare:
        gen_compare_report(load(Path(args.compare[0])), load(Path(args.compare[1])),
                           Path(args.compare[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
