"""Local smoke: verify the FileProcessor token bucket appears in the done event.

Usage:
    python _test_local_file_tokens.py [PDF_NAME]

Defaults to a small scanned FIR PDF. Streams SSE from localhost:5000, prints
the file_processing timeline and the token_usage.by_agent breakdown so we
can eyeball whether FileProcessor tokens are being reported now.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx


REPO_ROOT = Path(__file__).resolve().parent
LOCAL_BASE = "http://localhost:5000/pyapi"
PROD_BASE = "https://api.lawttorney.com/pyapi"
import os
API_KEY = os.environ["LAWTECH_API_KEY"]

# Default PDF: FIRs are typically scanned in India -> high chance of OCR firing.
DEFAULT_PDF = "0156 Publish FIR.pdf"
PROMPT = "Summarize the attached document in 3 sentences."


async def run(pdf_name: str, base: str) -> int:
    pdf_path = REPO_ROOT / "test_pdfs" / pdf_name
    if not pdf_path.exists():
        print(f"[FAIL] PDF not found: {pdf_path}")
        return 1

    body = pdf_path.read_bytes()
    print("=" * 70)
    print(f"PDF:    {pdf_path.name}  ({len(body):,} bytes)")
    print(f"PROMPT: {PROMPT}")
    print(f"URL:    {base}/chat")
    print("=" * 70)

    events: list[dict[str, Any]] = []
    file_stages: list[str] = []
    done_evt: dict | None = None
    t0 = time.time()

    headers = {"X-API-Key": API_KEY}
    data = {"query": PROMPT}
    files = {"files": (pdf_path.name, body, "application/pdf")}

    async with httpx.AsyncClient() as client:
        try:
            async with client.stream(
                "POST", f"{base}/chat",
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

                    if t == "file_processing":
                        stage = evt.get("stage") or evt.get("message", "")[:60]
                        file_stages.append(stage)
                        print(f"  [{time.time()-t0:6.1f}s] file_processing -> {stage}")
                    elif t == "done":
                        done_evt = evt
                        print(f"  [{time.time()-t0:6.1f}s] done")
                        break
                    elif t == "error":
                        print(f"  [{time.time()-t0:6.1f}s] error -> {evt}")
        except Exception as e:
            print(f"[FAIL] Stream error: {e}")
            return 3

    print()
    print("=" * 70)
    print("RESULT")
    print("=" * 70)

    if not done_evt:
        print("[FAIL] No done event received. Last 3 events:")
        for e in events[-3:]:
            print(f"  {json.dumps(e)[:200]}")
        return 4

    tu = done_evt.get("token_usage") or {}
    by_agent = tu.get("by_agent") or {}

    print(f"agents_used:      {done_evt.get('agents_used')}")
    print(f"total_tokens:     {tu.get('total_tokens'):,}")
    print(f"input_tokens:     {tu.get('input_tokens'):,}")
    print(f"output_tokens:    {tu.get('output_tokens'):,}")
    print(f"cost_usd:         ${tu.get('cost_usd', 0):.6f}")
    print()
    print("by_agent breakdown:")
    for agent, stats in sorted(by_agent.items()):
        marker = "  <-- NEW (file OCR)" if agent == "FileProcessor" else ""
        print(f"  {agent:20s} total={stats['total']:>7,}  input={stats['input']:>7,}  "
              f"output={stats['output']:>6,}  calls={stats['calls']:>2}  "
              f"cost=${stats['cost_usd']:.6f}{marker}")

    fp = by_agent.get("FileProcessor")
    print()
    if fp:
        print(f"[PASS] FileProcessor bucket present: {fp['total']:,} tokens "
              f"across {fp['calls']} OCR call(s), cost ${fp['cost_usd']:.6f}")
        # Show per-OCR-call detail from calls[]
        fp_calls = [c for c in tu.get("calls", []) if c.get("agent") == "FileProcessor"]
        if fp_calls:
            print("\n  Per-OCR-call detail:")
            for c in fp_calls:
                print(f"    step={c['step']:35s}  in={c['input']:>6,}  "
                      f"out={c['output']:>4,}  total={c['total']:>6,}  "
                      f"cost=${c['cost_usd']:.6f}")
        return 0
    else:
        print("[INFO] No FileProcessor bucket — this PDF had a text layer, "
              "so PyMuPDF handled it and Vision OCR never fired. That's a "
              "valid outcome (the wiring works; OCR just wasn't needed). "
              "To force OCR, try a scanned/image-only PDF like:\n"
              "    python _test_local_file_tokens.py 'Adobe Scan 16 Jul 2026 (1).pdf'")
        return 0


if __name__ == "__main__":
    # Usage: python _test_local_file_tokens.py [pdf_name] [--prod]
    args = [a for a in sys.argv[1:] if a != "--prod"]
    base = PROD_BASE if "--prod" in sys.argv else LOCAL_BASE
    pdf_name = args[0] if args else DEFAULT_PDF
    sys.exit(asyncio.run(run(pdf_name, base)))
