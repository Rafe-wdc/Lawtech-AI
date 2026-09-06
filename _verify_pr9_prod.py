"""Live probe: upload a genuinely-blurry image to prod and verify PR #9's
OCR readability/blur gate fires the `unreadable_warning` SSE event.

PR #9 promise: unreadable/blurry/low-res images should be detected, their
OCR text withheld from the pipeline (so it can't poison the answer), and
a quality warning surfaced via the API. If PR #9 is not live, the blurry
image's garbage OCR text flows through as if it were readable content.
"""
import json
import sys
import time
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

URL = "https://api.lawttorney.com/pyapi/chat"
API_KEY = "ff6c3e959de2bf4f73901db1ff797ea484d326ac6e2622067493352435f23a51"
FIXTURE = Path("real_blur.png")   # shipped by PR #9
QUERY = "What does this document say?"


def main() -> int:
    if not FIXTURE.exists():
        print(f"[FAIL] fixture missing: {FIXTURE}")
        return 1

    print(f"POST {URL}")
    print(f"Uploading fixture: {FIXTURE} ({FIXTURE.stat().st_size} bytes)")
    print(f'Query: "{QUERY}"')
    print("Streaming...")

    files = {"files": (FIXTURE.name, FIXTURE.read_bytes(), "image/png")}
    data = {"query": QUERY}
    headers = {"X-API-Key": API_KEY}

    events = []
    unreadable_events = []
    file_processing_stages = set()
    final_response = None
    t0 = time.time()

    with requests.post(URL, headers=headers, data=data, files=files,
                       stream=True, timeout=300) as r:
        r.raise_for_status()
        for raw in r.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            try:
                ev = json.loads(raw[5:].strip())
            except json.JSONDecodeError:
                continue
            events.append(ev)
            t = ev.get("type")
            stage = ev.get("stage")
            if t == "file_processing":
                if stage:
                    file_processing_stages.add(stage)
                if stage == "unreadable_warning":
                    unreadable_events.append(ev)
            elif t == "response":
                final_response = ev.get("content") or ev.get("data")
            elif t == "done":
                break

    elapsed = time.time() - t0

    print()
    print("=" * 70)
    print(f"Received {len(events)} SSE events, elapsed {elapsed:.1f}s")
    print("=" * 70)
    print()
    print("File-processing stages observed:")
    for s in sorted(file_processing_stages):
        marker = " <-- PR #9 EVENT" if s == "unreadable_warning" else ""
        print(f"  - {s}{marker}")
    print()

    if unreadable_events:
        print("Unreadable-warning events found:")
        for ev in unreadable_events:
            print(f"  file:    {ev.get('file')}")
            print(f"  reason:  {ev.get('reason')}")
            print(f"  message: {ev.get('message')}")
        print()

    print(f"Final response (first 350 chars):")
    if final_response:
        print(f"  {final_response[:350].replace(chr(10), ' | ')}")
    else:
        print("  (none)")

    print()
    if unreadable_events:
        print("VERDICT: [OK] PR #9 IS LIVE on prod")
        print("  - readability gate detected the blurry image")
        print("  - unreadable_warning SSE event was surfaced to the client")
        return 0
    else:
        print("VERDICT: [FAIL/UNCLEAR] no `unreadable_warning` event")
        print("  - either PR #9 code isn't running,")
        print("  - or this specific fixture didn't trigger the gate,")
        print(f"  - or the image was accepted as readable (stages: {file_processing_stages}).")
        return 1


if __name__ == "__main__":
    sys.exit(main())
