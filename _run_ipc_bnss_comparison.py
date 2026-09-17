"""Ad-hoc smoke: hit /pyapi/search/stream with an IPC 299/300 vs BNSS
comparison prompt and dump the full request/response conversation to a
markdown file for inspection.
"""

import json
import os
import sys
import time
from pathlib import Path

import requests

API_URL = "http://127.0.0.1:5000/pyapi/search/stream"
API_KEY = "ff6c3e959de2bf4f73901db1ff797ea484d326ac6e2622067493352435f23a51"
OUT_PATH = Path(__file__).parent / "_ipc_bnss_comparison_run_postfix_md_v2.md"

PROMPT = (
    "Give me the comparison between section 299 and section 300 of IPC "
    "along with the new sections of Bharatiya Nagarik Suraksha Sanhita 2023"
)

payload = {
    "Promptquery": PROMPT,
}

headers = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json",
    "Accept": "text/event-stream",
}

def main() -> int:
    events: list[dict] = []
    token_buf: list[str] = []
    final_response: str | None = None
    sources: list[dict] = []
    agents_used: list[str] = []
    followups: list[str] = []
    thread_id: str | None = None
    errors: list[str] = []

    t0 = time.time()
    with requests.post(API_URL, json=payload, headers=headers,
                       stream=True, timeout=600) as r:
        r.raise_for_status()
        for raw in r.iter_lines(decode_unicode=True):
            if not raw:
                continue
            if not raw.startswith("data:"):
                continue
            data_str = raw[len("data:"):].strip()
            if not data_str:
                continue
            try:
                ev = json.loads(data_str)
            except json.JSONDecodeError:
                events.append({"type": "raw", "data": data_str})
                continue
            events.append(ev)
            t = ev.get("type")
            if t == "thread_id":
                thread_id = ev.get("data")
            elif t == "token":
                chunk = ev.get("content") or ev.get("data") or ""
                if chunk:
                    token_buf.append(chunk)
            elif t == "token_reset":
                token_buf.clear()
            elif t == "response":
                final_response = ev.get("content") or ev.get("data")
            elif t == "sources":
                sources = ev.get("data") or []
            elif t == "agents_planned":
                agents_used = ev.get("agents") or []
            elif t == "followup_suggestions":
                followups = ev.get("data") or []
            elif t == "error":
                errors.append(str(ev.get("data")))
            elif t == "done":
                break
    elapsed = time.time() - t0

    streamed_text = "".join(token_buf)
    answer = final_response or streamed_text or "(no response captured)"

    def _fence(lang: str, body: str) -> str:
        return f"```{lang}\n{body}\n```"

    md = []
    md.append("# IPC 299/300 vs BNSS 2023 — search/stream run\n")
    md.append(f"- **Endpoint**: `POST {API_URL}`")
    md.append(f"- **Elapsed**: {elapsed:.2f}s")
    md.append(f"- **Thread ID**: `{thread_id or '(none)'}`")
    md.append(f"- **Agents planned**: {', '.join(agents_used) or '(none reported)'}")
    md.append(f"- **Sources returned**: {len(sources)}")
    md.append(f"- **SSE events received**: {len(events)}")
    if errors:
        md.append(f"- **Errors**: {len(errors)}")
    md.append("")

    md.append("## Request payload\n")
    md.append(_fence("json", json.dumps(payload, indent=2, ensure_ascii=False)))
    md.append("")

    md.append("## Request headers\n")
    safe_headers = {k: ("<redacted>" if k.lower() == "x-api-key" else v)
                    for k, v in headers.items()}
    md.append(_fence("json", json.dumps(safe_headers, indent=2)))
    md.append("")

    md.append("## Final response\n")
    md.append(answer)
    md.append("")

    if followups:
        md.append("## Follow-up suggestions\n")
        for s in followups:
            md.append(f"- {s}")
        md.append("")

    if sources:
        md.append("## Sources\n")
        md.append(_fence("json", json.dumps(sources, indent=2, ensure_ascii=False)))
        md.append("")

    if errors:
        md.append("## Errors\n")
        for e in errors:
            md.append(f"- {e}")
        md.append("")

    md.append("## Raw SSE event stream (all events)\n")
    md.append(_fence("json", json.dumps(events, indent=2, ensure_ascii=False)))
    md.append("")

    md.append("## Final API response (repeated at end)\n")
    md.append(answer)
    md.append("")

    OUT_PATH.write_text("\n".join(md), encoding="utf-8")
    print(f"[ok] wrote {OUT_PATH} ({OUT_PATH.stat().st_size} bytes, "
          f"{elapsed:.1f}s, {len(events)} events)")
    return 0


if __name__ == "__main__":
    sys.exit(main())