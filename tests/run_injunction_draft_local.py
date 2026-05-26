"""Run the same injunction-draft prompt against the LOCAL server (which has
the new LLM-judge relevance gate) to compare against the production result.

Saves to *_local_* filenames so the prod baseline in injunction_draft_*
isn't overwritten.

Run: python tests/run_injunction_draft_local.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from tests.run_injunction_draft import PROMPT, send_prompt, count_section_headers

BASE = os.environ.get("LAWTECH_TEST_BASE", "http://127.0.0.1:5000/pyapi")
API_KEY = os.environ.get("LAWTECH_TEST_API_KEY", "dev-mode-no-auth")


async def main() -> int:
    # Monkey-patch the BASE that send_prompt uses
    import tests.run_injunction_draft as runner
    runner.BASE = BASE
    runner.API_KEY = API_KEY

    print(f"Sending to LOCAL {BASE}/chat")
    print(f"Prompt: {len(PROMPT):,} chars\n")

    async with httpx.AsyncClient() as client:
        r = await send_prompt(client, PROMPT)

    tu = r.get("token_usage") or {}
    out_md = Path(__file__).parent / "injunction_draft_local_report.md"
    out_json = Path(__file__).parent / "injunction_draft_local_results.json"
    out_draft = Path(__file__).parent / "injunction_draft_local_answer.md"

    if not r.get("ok"):
        print(f"FAILED: HTTP {r['http_status']} - {r.get('error')}")
        out_md.write_text(
            f"# Injunction draft LOCAL - FAILED\n\nHTTP {r['http_status']}\n\n{r.get('error')}\n",
            encoding="utf-8",
        )
        return 1

    print(f"HTTP {r['http_status']}  {r['elapsed_s']}s  "
          f"ttfb={r['ttfb_s']}s  ttft={r['ttft_s']}s")
    print(f"  agents_planned: {r.get('agents_planned')}")
    print(f"  agents_used   : {r.get('agents_used')}")
    print(f"  classify      : {r.get('classify_status')}")
    print(f"  answer        : {r['answer_chars']:,} chars ({r['answer_words']:,} words)")
    print(f"  tokens        : {tu.get('total_tokens', 0):,}")
    print(f"  cost          : ${tu.get('cost_usd', 0):.4f}")

    answer = r.get("answer") or ""

    # Check for Telangana contamination (the prior failure mode)
    tel_hits = []
    for needle in ["telangana municipalities", "telangana act"]:
        if needle in answer.lower():
            # Find first occurrence with context
            idx = answer.lower().index(needle)
            tel_hits.append((needle, idx, answer[max(0, idx-50):idx+150]))

    print()
    if tel_hits:
        print(f"!!! Telangana contamination still present ({len(tel_hits)} occurrences):")
        for needle, idx, ctx in tel_hits[:3]:
            print(f"  - '{needle}' at char {idx}: ...{ctx}...")
    else:
        print(">> Clean: no Telangana Municipalities Act reference in the answer.")

    md = [
        f"# Injunction draft LOCAL test - {BASE}",
        "",
        f"- HTTP: **{r['http_status']}**",
        f"- Latency: **{r['elapsed_s']}s**",
        f"- agents_planned: `{r.get('agents_planned')}`",
        f"- agents_used: `{r.get('agents_used')}`",
        f"- classify: {r.get('classify_status')}",
        f"- answer: {r['answer_chars']:,} chars",
        f"- tokens: {tu.get('total_tokens', 0):,}",
        f"- cost: ${tu.get('cost_usd', 0):.4f}",
        f"- Telangana contamination: {'YES (' + str(len(tel_hits)) + ' hits)' if tel_hits else 'NO'}",
        "",
        "## Drafted Application (full)",
        "",
        answer,
    ]

    out_md.write_text("\n".join(md), encoding="utf-8")
    out_draft.write_text(answer, encoding="utf-8")
    out_json.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"Saved report -> {out_md}")
    print(f"Saved draft  -> {out_draft}")
    print(f"Saved JSON   -> {out_json}")
    return 0 if not tel_hits else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
