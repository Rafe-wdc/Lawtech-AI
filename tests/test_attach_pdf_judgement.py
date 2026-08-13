"""Attach-PDF test: summarize judgement123.pdf via /pyapiv2/chat.

Streams SSE, prints timeline + final answer, dumps full event log to JSON.

Run: python tests/test_attach_pdf_judgement.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import os

BASE = "https://tool.lawttorney.com/pyapiv2"
API_KEY = os.environ.get("LAWTECH_TEST_API_KEY", "")
PDF_PATH = Path(__file__).resolve().parents[1] / "test_pdfs" / "judgement123.pdf"
PDF_MIME = "application/pdf"

PROMPT = "summarize the attached file"


async def run() -> int:
    if not PDF_PATH.exists():
        print(f"[FAIL] PDF not found: {PDF_PATH}")
        return 1

    body = PDF_PATH.read_bytes()
    print(f"PDF:    {PDF_PATH.name}  ({len(body):,} bytes)")
    print(f"PROMPT: {PROMPT}")
    print(f"URL:    {BASE}/chat")
    print()

    events: list[dict[str, Any]] = []
    answer_chunks: list[str] = []
    final_answer = ""
    errors: list[str] = []
    file_processing: list[dict] = []
    task_type = None
    t0 = time.time()

    headers = {"X-API-Key": API_KEY}
    data = {"query": PROMPT}
    files = {"files": (PDF_PATH.name, body, PDF_MIME)}

    async with httpx.AsyncClient() as client:
        try:
            async with client.stream(
                "POST", f"{BASE}/chat",
                headers=headers, data=data, files=files, timeout=400.0,
            ) as resp:
                if resp.status_code != 200:
                    text = await resp.aread()
                    print(f"[FAIL] HTTP {resp.status_code}: {text[:500]!r}")
                    return 2

                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    try:
                        evt = json.loads(line[6:])
                    except Exception:
                        continue
                    events.append(evt)
                    t = evt.get("type", "")

                    if t == "token":
                        tok = evt.get("data") or evt.get("content") or evt.get("token") or ""
                        if isinstance(tok, str):
                            answer_chunks.append(tok)
                    elif t in ("final_answer", "answer", "response"):
                        txt = evt.get("data") or evt.get("content") or evt.get("text") or ""
                        if isinstance(txt, str) and len(txt) > len(final_answer):
                            final_answer = txt
                    elif t == "done":
                        txt = evt.get("response") or evt.get("answer") or evt.get("data") or ""
                        if isinstance(txt, str) and len(txt) > len(final_answer):
                            final_answer = txt
                    elif t in ("error", "guardrail_error"):
                        errors.append(json.dumps(evt))
                    elif t == "file_processing":
                        file_processing.append(evt)
                    elif t in ("task_type", "task_classification", "orchestrator_plan", "agents_planned"):
                        task_type = evt
        except Exception as exc:
            print(f"[FAIL] stream error: {exc!r}")
            return 3

    elapsed = time.time() - t0
    if not final_answer and answer_chunks:
        final_answer = "".join(answer_chunks)

    type_counts = Counter(e.get("type", "?") for e in events)
    print(f"--- Stream complete in {elapsed:.1f}s ---")
    print(f"Total events: {len(events)}")
    print("Event types:")
    for t, n in type_counts.most_common():
        print(f"  {n:4d}  {t}")
    print()

    if file_processing:
        print("--- file_processing events ---")
        for evt in file_processing:
            print(json.dumps(evt, indent=2)[:1200])
        print()

    if task_type:
        print("--- task classification ---")
        print(json.dumps(task_type, indent=2)[:600])
        print()

    if errors:
        print("--- errors ---")
        for e in errors:
            print(e[:1000])
        print()

    print("--- final answer (first 5000 chars) ---")
    print(final_answer[:5000] if final_answer else "(empty)")
    print()
    print(f"Final answer length: {len(final_answer)} chars")

    out = Path(__file__).with_name("_attach_pdf_judgement_result.json")
    out.write_text(json.dumps({
        "elapsed_s": elapsed,
        "type_counts": type_counts,
        "final_answer": final_answer,
        "file_processing": file_processing,
        "errors": errors,
        "events": events,
    }, indent=2, default=str), encoding="utf-8")
    print(f"Full dump: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
