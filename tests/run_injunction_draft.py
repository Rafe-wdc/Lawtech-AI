"""Run the civil-injunction drafting prompt through production /pyapiv2/chat
and write a markdown + JSON report (routing, tokens, cost, latency, answer).

Run: python tests/run_injunction_draft.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("LAWTECH_TEST_BASE", "https://tool.lawttorney.com/pyapiv2")
API_KEY = os.environ["LAWTECH_TEST_API_KEY"]

PROMPT = (
    "You are a Senior Civil Advocate having expertise in Civil Litigation, CPC, "
    "Specific Relief Act, Property Law, Commercial Disputes, Injunction Matters, "
    "and latest judicial precedents. Analyze the following civil dispute and draft "
    "a comprehensive Application for Interim Injunction under Order XXXIX Rule 1 & 2 "
    "read with Section 151 of the Code of Civil Procedure, 1908. The draft must be "
    "detailed, legally sound, court-ready, and should not miss any important legal "
    "or factual point. "
    "Case Facts: My client is the lawful owner and possessor of agricultural land "
    "situated in Pune. The defendant is attempting to illegally sell the suit "
    "property and create third-party rights despite having no ownership title. "
    "Legal Issue: Whether interim injunction should be granted restraining "
    "defendant from alienating the suit property? "
    "Relief Sought: Temporary injunction restraining defendant from creating "
    "third-party rights or interfering with possession till disposal of suit."
)


async def send_prompt(client: httpx.AsyncClient, prompt: str) -> dict:
    """Send one /chat turn and capture all metrics + events."""
    data = {"query": prompt}

    t0 = time.perf_counter()
    final_answer = ""
    chunks: list[str] = []
    done_evt: dict | None = None
    agents_planned: list[str] | None = None
    agents_used: list[str] = []
    classify_status: str | None = None
    progress_events: list[dict] = []
    section_events: list[dict] = []
    ttfb: float | None = None
    ttft: float | None = None

    async with client.stream(
        "POST", f"{BASE}/chat",
        headers={"X-API-Key": API_KEY},
        data=data,
        timeout=900.0,
    ) as resp:
        if ttfb is None:
            ttfb = time.perf_counter() - t0
        if resp.status_code != 200:
            body = await resp.aread()
            return {
                "ok": False,
                "http_status": resp.status_code,
                "error": body.decode(errors="replace")[:500],
                "elapsed_s": round(time.perf_counter() - t0, 2),
            }
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            try:
                evt = json.loads(line[6:])
            except Exception:
                continue
            t = evt.get("type")
            if t == "agents_planned":
                agents_planned = evt.get("agents")
            elif t == "progress":
                progress_events.append({
                    "agent": evt.get("agent"),
                    "step": evt.get("step"),
                    "substep": evt.get("substep"),
                    "message": evt.get("message"),
                })
                if (
                    evt.get("agent") == "orchestrator"
                    and evt.get("step") == "classify"
                    and evt.get("substep")
                ):
                    classify_status = evt.get("message")
            elif t in ("section_start", "section_end", "section_status"):
                section_events.append(evt)
            elif t == "token":
                if ttft is None:
                    ttft = time.perf_counter() - t0
                tok = evt.get("data") or evt.get("content") or evt.get("token") or ""
                if isinstance(tok, str):
                    chunks.append(tok)
            elif t in ("response", "final_answer", "answer"):
                txt = evt.get("content") or evt.get("data") or evt.get("text") or ""
                if isinstance(txt, str) and len(txt) > len(final_answer):
                    final_answer = txt
            elif t == "done":
                done_evt = evt
                if done_evt.get("agents_used"):
                    agents_used = done_evt["agents_used"]

    if not final_answer and chunks:
        final_answer = "".join(chunks)
    elapsed = round(time.perf_counter() - t0, 2)

    tu = (done_evt or {}).get("token_usage") or {}
    return {
        "ok": True,
        "http_status": 200,
        "elapsed_s": elapsed,
        "ttfb_s": round(ttfb or 0, 3),
        "ttft_s": round(ttft or 0, 3) if ttft else None,
        "thread_id_returned": (done_evt or {}).get("thread_id"),
        "agents_planned": agents_planned,
        "agents_used": agents_used,
        "classify_status": classify_status,
        "answer_chars": len(final_answer),
        "answer_words": len(final_answer.split()),
        "answer": final_answer,
        "token_usage": tu,
        "progress_count": len(progress_events),
        "section_events": section_events,
        "done_event": done_evt,
    }


def count_section_headers(text: str) -> int:
    """Rough count of section/paragraph headers (1., 2., I., II., A., B., etc.)."""
    import re
    return len(re.findall(r"(?m)^\s*(?:\d+\.|\(\d+\)|[IVX]+\.|[A-Z]\.)\s+\S", text))


async def main() -> int:
    print(f"Sending injunction-draft prompt to {BASE}/chat")
    print(f"Prompt: {len(PROMPT):,} chars\n")

    async with httpx.AsyncClient() as client:
        r = await send_prompt(client, PROMPT)

    tu = r.get("token_usage") or {}
    out_md = Path(__file__).parent / "injunction_draft_report.md"
    out_json = Path(__file__).parent / "injunction_draft_results.json"
    out_draft = Path(__file__).parent / "injunction_draft_answer.md"

    if not r.get("ok"):
        print(f"FAILED: HTTP {r['http_status']} — {r.get('error')}")
        out_md.write_text(
            f"# Injunction draft — FAILED\n\nHTTP {r['http_status']}\n\n{r.get('error')}\n",
            encoding="utf-8",
        )
        return 1

    print(f"HTTP {r['http_status']}  {r['elapsed_s']}s  "
          f"ttfb={r['ttfb_s']}s  ttft={r['ttft_s']}s")
    print(f"  agents_planned: {r.get('agents_planned')}")
    print(f"  agents_used   : {r.get('agents_used')}")
    print(f"  classify      : {r.get('classify_status')}")
    print(f"  answer        : {r['answer_chars']:,} chars ({r['answer_words']:,} words)")
    print(f"  tokens        : {tu.get('total_tokens', 0):,}  "
          f"(in={tu.get('input_tokens', 0):,}, out={tu.get('output_tokens', 0):,})")
    print(f"  cost          : ${tu.get('cost_usd', 0):.4f}")
    print(f"  section events: {len(r.get('section_events') or [])}")

    answer = r.get("answer") or ""
    headers = count_section_headers(answer)

    # ── markdown report ────────────────────────────────────────────────
    md = [
        f"# Injunction draft test — {BASE}",
        "",
        f"- Endpoint: `{BASE}/chat`",
        f"- HTTP: **{r['http_status']}**",
        f"- Latency: **{r['elapsed_s']}s** (ttfb {r['ttfb_s']}s, ttft {r['ttft_s']}s)",
        f"- Thread ID: `{r.get('thread_id_returned')}`",
        "",
        "## Routing",
        "",
        f"- agents_planned: `{r.get('agents_planned')}`",
        f"- agents_used: `{r.get('agents_used')}`",
        f"- classify status: {r.get('classify_status')}",
        f"- progress events: {r.get('progress_count')}",
        f"- section events: {len(r.get('section_events') or [])}",
        "",
        "## Tokens & cost",
        "",
        f"- input_tokens: **{tu.get('input_tokens', 0):,}**",
        f"- output_tokens: **{tu.get('output_tokens', 0):,}**",
        f"- total_tokens: **{tu.get('total_tokens', 0):,}**",
        f"- cost_usd: **${tu.get('cost_usd', 0):.4f}**",
        "",
        "## Answer stats",
        "",
        f"- chars: **{r['answer_chars']:,}**",
        f"- words: **{r['answer_words']:,}**",
        f"- numbered/section headers detected: **{headers}**",
        "",
        "## Prompt",
        "",
        "```",
        PROMPT,
        "```",
        "",
        "## Drafted Application (full)",
        "",
        answer,
        "",
    ]

    if r.get("section_events"):
        md += ["## Section events", "", "```json"]
        md.append(json.dumps(r["section_events"][:20], indent=2, ensure_ascii=False))
        md += ["```", ""]

    out_md.write_text("\n".join(md), encoding="utf-8")
    out_draft.write_text(answer, encoding="utf-8")
    out_json.write_text(
        json.dumps(r, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print()
    print(f"Saved report -> {out_md}")
    print(f"Saved draft  -> {out_draft}")
    print(f"Saved JSON   -> {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
