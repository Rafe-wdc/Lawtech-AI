"""One-off local smoke: quashing of criminal complaint (Bombay HC).

Streams /pyapi/chat, captures every SSE event, and writes a single
markdown report with:
  1. Prompt + run metadata
  2. Event-type counts
  3. Chronological internal steps (progress / agents_planned / etc.)
  4. Full final response
Raw JSONL events are also dumped alongside for auditing.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "http://localhost:5000/pyapi"
KEY = os.environ["LAWTECH_API_KEY"]

PROMPT = (
    "Complete details on quashing of criminal complaint at Bombay high court "
    "and step by step guide"
)

OUT_DIR = Path(__file__).parent
LABEL = "bhc_quash_smoke"
OUT_MD = OUT_DIR / f"_{LABEL}.md"
OUT_RAW = OUT_DIR / f"_{LABEL}_events.jsonl"


def _fmt_event_row(seq: int, elapsed_s: float, e: dict) -> str:
    t = e.get("type") or "<no-type>"
    # Prefer a short summary of the interesting fields per event type.
    summary_keys = [
        "step", "stage", "message", "agents", "agent",
        "task", "files", "rejected", "error", "reason",
        "data",
    ]
    parts: list[str] = []
    for k in summary_keys:
        if k in e and e[k] not in (None, "", [], {}):
            v = e[k]
            if isinstance(v, str):
                v_short = v.replace("\n", " ")
                if len(v_short) > 220:
                    v_short = v_short[:217] + "..."
                parts.append(f"{k}={v_short!r}")
            else:
                s = json.dumps(v, ensure_ascii=False, default=str)
                if len(s) > 220:
                    s = s[:217] + "..."
                parts.append(f"{k}={s}")
    tail = " ".join(parts) if parts else ""
    return f"| {seq:>4} | {elapsed_s:>7.2f}s | `{t}` | {tail} |"


async def main() -> int:
    print(f"BASE: {BASE}")
    print(f"PROMPT ({len(PROMPT)} chars): {PROMPT}")

    t0 = time.time()
    headers = {"X-API-Key": KEY}
    data = {"query": PROMPT}

    answer = ""
    events_seen: dict[str, int] = {}
    agents_planned: list = []
    progress_steps: list[str] = []
    errors: list[str] = []
    done_event: dict | None = None
    thread_id = None
    ttfb_ms: int | None = None
    raw_lines = 0
    event_rows: list[str] = []
    seq = 0

    with OUT_RAW.open("w", encoding="utf-8") as raw_fh:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST", f"{BASE}/chat",
                headers=headers, data=data, timeout=1500.0,
            ) as resp:
                if ttfb_ms is None:
                    ttfb_ms = round((time.time() - t0) * 1000)
                if resp.status_code != 200:
                    err_body = await resp.aread()
                    msg = err_body.decode(errors="replace")[:1000]
                    print(f"HTTP {resp.status_code}: {msg}")
                    return 1
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        e = json.loads(line[6:])
                    except Exception:
                        continue
                    raw_fh.write(line[6:] + "\n")
                    raw_lines += 1
                    seq += 1
                    elapsed = time.time() - t0
                    t = e.get("type") or "<no-type>"
                    events_seen[t] = events_seen.get(t, 0) + 1

                    # Skip logging individual token events one-by-one — collapse to a marker only.
                    if t != "token":
                        event_rows.append(_fmt_event_row(seq, elapsed, e))

                    if t == "thread_id":
                        thread_id = e.get("data")
                    elif t == "token":
                        tok = e.get("data") or e.get("content") or e.get("token") or ""
                        if isinstance(tok, str):
                            answer += tok
                    elif t in ("response", "final_answer", "answer"):
                        txt = e.get("content") or e.get("data") or e.get("text") or ""
                        if isinstance(txt, str) and len(txt) > len(answer):
                            answer = txt
                    elif t == "agents_planned":
                        agents_planned = e.get("agents") or e.get("data") or []
                    elif t == "progress":
                        step = e.get("step") or e.get("stage")
                        if step:
                            progress_steps.append(step)
                    elif t in ("error", "guardrail_error"):
                        errors.append(json.dumps(e, ensure_ascii=False)[:500])
                    elif t == "done":
                        done_event = e
                        for k in ("final_response", "response", "final_answer"):
                            v = e.get(k)
                            if isinstance(v, str) and len(v) > len(answer):
                                answer = v

    elapsed = round(time.time() - t0, 2)

    # Build markdown report
    lines: list[str] = []
    lines.append(f"# {LABEL}")
    lines.append("")
    lines.append(f"**Endpoint:** `{BASE}/chat`  ")
    lines.append(f"**Thread ID:** `{thread_id}`  ")
    lines.append(f"**Elapsed:** {elapsed}s  ")
    lines.append(f"**TTFB:** {ttfb_ms} ms  ")
    lines.append(f"**Answer length:** {len(answer):,} chars ({len(answer.split()):,} words)  ")
    lines.append(f"**Raw SSE events:** {raw_lines}  ")
    lines.append("")
    lines.append("## Prompt")
    lines.append("")
    lines.append("```")
    lines.append(PROMPT)
    lines.append("```")
    lines.append("")
    lines.append("## Event-type counts")
    lines.append("")
    lines.append("| Event type | Count |")
    lines.append("|---|---:|")
    for k, v in sorted(events_seen.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| `{k}` | {v} |")
    lines.append("")
    lines.append(f"**Agents planned:** `{agents_planned}`  ")
    lines.append(f"**Unique progress steps ({len(set(progress_steps))}):** {sorted(set(progress_steps))}  ")
    if errors:
        lines.append("")
        lines.append("### Errors")
        for err in errors:
            lines.append(f"- `{err}`")
    lines.append("")
    lines.append("## Internal steps (chronological, token events collapsed)")
    lines.append("")
    lines.append("| # | Elapsed | Type | Summary |")
    lines.append("|---:|---:|---|---|")
    lines.extend(event_rows)
    lines.append("")
    lines.append("## Final response")
    lines.append("")
    lines.append(answer or "_(empty)_")
    lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    print()
    print(f"elapsed: {elapsed}s  answer_len: {len(answer):,}  agents: {agents_planned}")
    print(f"progress steps: {len(progress_steps)}   raw events: {raw_lines}")
    print(f"Report saved: {OUT_MD}")
    print(f"Raw SSE:      {OUT_RAW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
