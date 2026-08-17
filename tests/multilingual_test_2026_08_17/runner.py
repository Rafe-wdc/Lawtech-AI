"""Sequential same-thread test runner for `test prompt.txt`.

The source file (project root `test prompt.txt`) has TWO turns:
    Turn 1 — the entire body under "prompt 1:" (drafting + citations +
             arguments + consumer laws + PDF review, concatenated verbatim).
    Turn 2 — the literal string "Convert above text into marathi" — the
             directive follow-up that exercises the language-switch path.

We send them sequentially on ONE globalThreadId so the memory / rewriter /
intent extractor sees turn 2 as a real follow-up on turn 1.

Turn bodies are parsed FROM the file (not hard-coded) so the Marathi
Devanagari and unicode characters remain byte-verbatim with the source.

Usage:
    python tests/multilingual_test_2026_08_17/runner.py --endpoint prod
    python tests/multilingual_test_2026_08_17/runner.py --endpoint local

Writes: tests/multilingual_test_2026_08_17/{endpoint}_results.json
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

API_KEY = "ff6c3e959de2bf4f73901db1ff797ea484d326ac6e2622067493352435f23a51"
TIMEOUT = 600  # 10 min per request (turn 1 is a mega-prompt; drafting can take 3-5 min)

ENDPOINTS = {
    "prod": "https://api.lawttorney.com/pyapi/search",
    "local": "http://localhost:5000/pyapi/search",
}

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_FILE = REPO_ROOT / "test prompt.txt"


def load_turns() -> list[dict]:
    """Split `test prompt.txt` at the literal 'prompt 2:' marker.

    Returns two turns:
      1: everything from the start through the last line before 'prompt 2:'
         (verbatim body of prompt 1 including all sub-scenarios).
      2: the text after the 'prompt 2:' label on that line (== "Convert
         above text into marathi").
    """
    text = SOURCE_FILE.read_text(encoding="utf-8")
    marker = "\nprompt 2:"
    idx = text.find(marker)
    if idx < 0:
        raise SystemExit("Could not find 'prompt 2:' marker in test prompt.txt")

    turn1 = text[:idx].strip()
    # Extract everything after the 'prompt 2:' label on that line, then trim
    after = text[idx + len(marker):].strip()
    # `after` starts with " Convert above text into marathi" — take the first
    # non-empty line as the second turn (there is nothing after it in the
    # source file today, but this is defensive).
    turn2 = after.split("\n", 1)[0].strip()

    return [
        {"id": 1, "label": "prompt-1-mega", "prompt": turn1},
        {"id": 2, "label": "prompt-2-convert-to-marathi", "prompt": turn2},
    ]


def run(endpoint: str) -> None:
    url = ENDPOINTS[endpoint]
    out_dir = Path(__file__).parent
    thread_id = str(uuid.uuid4())
    turns = load_turns()

    print(f"[{endpoint}] URL: {url}")
    print(f"[{endpoint}] thread_id: {thread_id}")
    print(f"[{endpoint}] turns: {len(turns)}")
    for t in turns:
        print(f"[{endpoint}]   turn {t['id']} chars={len(t['prompt'])} preview={t['prompt'][:60]!r}")

    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "X-API-Key": API_KEY,
    })

    run_started = datetime.now(timezone.utc).isoformat()
    results: list[dict] = []
    for turn in turns:
        payload = {
            "Promptquery": turn["prompt"],
            "globalThreadId": thread_id,
        }
        t0 = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        prompt_chars = len(turn["prompt"])
        print(f"\n[{endpoint}] Turn {turn['id']} {turn['label']} chars={prompt_chars} sending...")
        try:
            r = session.post(url, json=payload, timeout=TIMEOUT)
            elapsed_s = time.perf_counter() - t0
            body: dict = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"_raw": r.text[:5000]}
            status = r.status_code
        except requests.exceptions.RequestException as e:
            elapsed_s = time.perf_counter() - t0
            body = {"_error": type(e).__name__ + ": " + str(e)[:400]}
            status = 0

        result_preview = str(body.get("result", ""))[:200].replace("\n", " ")
        agents = body.get("agents_used", [])
        eff_q = str(body.get("effective_query", ""))[:160]
        response_chars = len(str(body.get("result", "")))
        print(f"[{endpoint}] Turn {turn['id']} status={status} time={elapsed_s:6.1f}s agents={agents} resp_chars={response_chars}")
        print(f"[{endpoint}] Turn {turn['id']} effective_query={eff_q!r}")
        print(f"[{endpoint}] Turn {turn['id']} preview={result_preview!r}")

        results.append({
            "id": turn["id"],
            "label": turn["label"],
            "started_at": started_at,
            "elapsed_s": round(elapsed_s, 2),
            "status": status,
            "request": {
                "url": url,
                "method": "POST",
                "headers": {"X-API-Key": "***", "Content-Type": "application/json"},
                "body": payload,
            },
            "response": body,
        })

        out = {
            "endpoint": endpoint,
            "url": url,
            "thread_id": thread_id,
            "run_started_at": run_started,
            "turns": results,
        }
        (out_dir / f"{endpoint}_results.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"\n[{endpoint}] DONE. Wrote {out_dir / f'{endpoint}_results.json'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", choices=list(ENDPOINTS), required=True)
    args = ap.parse_args()
    run(args.endpoint)