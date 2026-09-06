"""Same 2-turn same-thread test — against /pyapi/search/stream (SSE).

Verifies that the 6 pipeline fixes behave identically on the streaming
endpoint (the user's primary path) as they did on /pyapi/search.

Usage:
    python tests/multilingual_test_2026_08_17/stream_runner.py --endpoint prod
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

from tests.multilingual_test_2026_08_17.runner import load_turns

API_KEY = "ff6c3e959de2bf4f73901db1ff797ea484d326ac6e2622067493352435f23a51"
TIMEOUT = 600

ENDPOINTS = {
    "prod": "https://api.lawttorney.com/pyapi/search/stream",
    "local": "http://localhost:5000/pyapi/search/stream",
}


def _consume_sse(response: requests.Response) -> dict:
    """Read an SSE stream to completion, aggregate final signals.

    Returns a dict with the accumulated final_response text, agents_used,
    thread_id, and the raw event list (for debugging).
    """
    agents_planned: list[str] = []
    agents_used: list[str] = []
    final_response = ""
    thread_id = ""
    effective_query = ""
    events: list[dict] = []

    for raw in response.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data:"):
            continue
        payload = raw[5:].strip()
        if not payload:
            continue
        try:
            evt = json.loads(payload)
        except json.JSONDecodeError:
            continue
        events.append(evt)
        t = evt.get("type", "")
        if t == "response" and "content" in evt:
            final_response = evt["content"]
        elif t == "agents_planned":
            agents_planned = evt.get("agents", [])
        elif t == "agents_used":
            agents_used = evt.get("agents", [])
        elif t == "thread_id":
            thread_id = evt.get("thread_id", "")
        elif t == "effective_query":
            effective_query = evt.get("query", "")
        elif t == "done":
            # Terminal event carries agents_used + token_usage in some builds
            if not agents_used:
                agents_used = evt.get("agents_used", agents_used)
            if not effective_query:
                effective_query = evt.get("effective_query", effective_query)

    return {
        "final_response": final_response,
        "agents_planned": agents_planned,
        "agents_used": agents_used,
        "thread_id_from_stream": thread_id,
        "effective_query": effective_query,
        "event_count": len(events),
        "event_types": sorted({e.get("type", "?") for e in events}),
    }


def run(endpoint: str) -> None:
    url = ENDPOINTS[endpoint]
    out_dir = Path(__file__).parent
    thread_id = str(uuid.uuid4())
    turns = load_turns()

    print(f"[{endpoint}] URL: {url}")
    print(f"[{endpoint}] thread_id: {thread_id}")
    print(f"[{endpoint}] turns: {len(turns)}")

    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "X-API-Key": API_KEY,
        "Accept": "text/event-stream",
    })

    run_started = datetime.now(timezone.utc).isoformat()
    results: list[dict] = []
    for turn in turns:
        payload = {"Promptquery": turn["prompt"], "globalThreadId": thread_id}
        t0 = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        print(f"\n[{endpoint}] Turn {turn['id']} {turn['label']} chars={len(turn['prompt'])} sending (SSE)...")
        try:
            r = session.post(url, json=payload, timeout=TIMEOUT, stream=True)
            summary = _consume_sse(r)
            elapsed_s = time.perf_counter() - t0
            status = r.status_code
        except requests.exceptions.RequestException as e:
            elapsed_s = time.perf_counter() - t0
            summary = {"_error": type(e).__name__ + ": " + str(e)[:400]}
            status = 0

        result_text = summary.get("final_response", "")
        deva = sum(1 for c in result_text if 0x0900 <= ord(c) <= 0x097F)
        latin = sum(1 for c in result_text if c.isascii() and c.isalpha())

        print(f"[{endpoint}] Turn {turn['id']} status={status} time={elapsed_s:6.1f}s")
        print(f"[{endpoint}] Turn {turn['id']} events={summary.get('event_count')} event_types={summary.get('event_types')}")
        print(f"[{endpoint}] Turn {turn['id']} agents_planned={summary.get('agents_planned')} agents_used={summary.get('agents_used')}")
        print(f"[{endpoint}] Turn {turn['id']} chars={len(result_text)} devanagari={deva} latin={latin}")
        print(f"[{endpoint}] Turn {turn['id']} effective_query={summary.get('effective_query','')[:150]!r}")
        print(f"[{endpoint}] Turn {turn['id']} preview={result_text[:200].replace(chr(10), ' ')!r}")

        results.append({
            "id": turn["id"], "label": turn["label"],
            "started_at": started_at, "elapsed_s": round(elapsed_s, 2),
            "status": status,
            "request": {
                "url": url, "method": "POST",
                "headers": {"X-API-Key": "***", "Content-Type": "application/json", "Accept": "text/event-stream"},
                "body": payload,
            },
            "sse_summary": summary,
        })

        out = {
            "endpoint": endpoint + "_stream",
            "url": url,
            "thread_id": thread_id,
            "run_started_at": run_started,
            "turns": results,
        }
        (out_dir / f"{endpoint}_stream_afterfix_results.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(f"\n[{endpoint}] DONE. Wrote {out_dir / f'{endpoint}_stream_afterfix_results.json'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", choices=list(ENDPOINTS), required=True)
    args = ap.parse_args()
    run(args.endpoint)
