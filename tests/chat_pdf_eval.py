"""End-to-end test: /pyapiv2/chat with plaint.pdf attachment + 10 prompts + AI evaluator.

For each prompt:
 1. Sends multipart POST to /pyapiv2/chat with plaint.pdf attached.
 2. Streams SSE, captures the final answer text and event timeline.
 3. Sends prompt + answer (+ doc summary) to gpt-4o-mini for grading on
    relevance, accuracy, document-grounding, completeness.
 4. Writes a JSON dump and a short markdown report.

Run:  python tests/chat_pdf_eval.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE = "https://tool.lawttorney.com/pyapiv2"
API_KEY = "ff6c3e959de2bf4f73901db1ff797ea484d326ac6e2622067493352435f23a51"
PDF_PATH = Path(r"D:\agentic_proj\Lawtech-AI\test_pdfs\plaint.pdf")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EVAL_MODEL = "gpt-4o-mini"

# Short, neutral summary of plaint.pdf used so the evaluator can judge grounding
# without re-reading the PDF on every call.
DOC_SUMMARY = (
    "Civil Suit No. 114 of 2025 in the Court of Civil Judge (Junior Division), Pune. "
    "Plaintiff Arun Shankar Deshmukh (businessman, Bibwewadi) filed under Order VII Rule 1 CPC "
    "against Defendant Kunal Rajendra Patil (Kothrud) for recovery of Rs.10,00,000/- friendly "
    "loan advanced in cash on 15-Apr-2023 (witnesses: Rohit Naik, Sandeep Pawar). Repayment "
    "was due 15-Oct-2023. Legal notice dated 10-Jan-2024 was served 15-Jan-2024; defendant did "
    "not reply. Plaintiff seeks decree for Rs.10,00,000 + 12% p.a. interest from 15-Apr-2023, "
    "plus costs. Verified at Pune on 29-Jun-2025 by Adv. Meera Kulkarni."
)

PROMPTS = [
    "Summarize the attached plaint in 8-10 lines, including parties, claim and reliefs.",
    "Who is the plaintiff, who is the defendant, and what is the cause of action in the attached document?",
    "What is the principal loan amount claimed and the rate of interest sought in this plaint?",
    "Is the suit filed within the limitation period under the Limitation Act, 1963 for recovery of money? Explain.",
    "What documentary and oral evidence should the plaintiff produce to strengthen his case in this suit?",
    "Draft a written statement on behalf of the defendant Kunal Rajendra Patil denying the friendly loan alleged in the plaint.",
    "What court fee is payable on a money-recovery suit valued at Rs.10,00,000 under the Maharashtra Court Fees Act?",
    "Explain Order VII Rule 1 of the Code of Civil Procedure, 1908, which is referenced in this plaint.",
    "Cite leading Indian case law on recovery of unsecured friendly loans advanced in cash without written documentation.",
    "What jurisdictional and limitation defences could the defendant raise against this suit, and how strong are they?",
]


# ----------------------------- HTTP helpers -----------------------------


async def stream_chat(
    client: httpx.AsyncClient, query: str, pdf_bytes: bytes, pdf_name: str,
) -> tuple[list[dict[str, Any]], str, float]:
    """POST multipart to /pyapiv2/chat; collect SSE; return (events, final_answer, elapsed_s)."""
    url = f"{BASE}/chat"
    headers = {"X-API-Key": API_KEY}
    data = {"query": query}
    files = {"files": (pdf_name, pdf_bytes, "application/pdf")}

    events: list[dict[str, Any]] = []
    answer_chunks: list[str] = []
    final_answer = ""
    t0 = time.time()

    async with client.stream(
        "POST", url, headers=headers, data=data, files=files, timeout=300.0,
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                evt = json.loads(line[6:])
            except Exception:
                continue
            events.append(evt)

            t = evt.get("type", "")
            # Token streaming
            if t == "token":
                tok = evt.get("data") or evt.get("content") or evt.get("token") or ""
                if isinstance(tok, str):
                    answer_chunks.append(tok)
            # Final answer (some pipelines emit one of these)
            elif t in ("final_answer", "answer", "response"):
                txt = evt.get("data") or evt.get("content") or evt.get("text") or ""
                if isinstance(txt, str) and len(txt) > len(final_answer):
                    final_answer = txt
            elif t == "done":
                txt = evt.get("response") or evt.get("answer") or evt.get("data") or ""
                if isinstance(txt, str) and len(txt) > len(final_answer):
                    final_answer = txt

    elapsed = time.time() - t0
    if not final_answer and answer_chunks:
        final_answer = "".join(answer_chunks)
    return events, final_answer, elapsed


# ----------------------------- Evaluator -----------------------------


EVAL_SYSTEM = (
    "You are an evaluator for a legal AI. Score the assistant's response on the four "
    "criteria below from 1 to 5 (5 = excellent, 1 = unusable). Be strict. Reply with "
    "VALID JSON ONLY, no prose, matching this schema:\n"
    "{\n"
    "  \"relevance\": int,        // does it address the user's prompt?\n"
    "  \"document_grounding\": int, // does it use facts from the attached document where applicable?\n"
    "  \"legal_accuracy\": int,   // is the legal substance correct for Indian law?\n"
    "  \"completeness\": int,     // depth/coverage given the prompt\n"
    "  \"overall\": int,          // overall quality 1-5\n"
    "  \"verdict\": \"PASS\" | \"WEAK\" | \"FAIL\",\n"
    "  \"comment\": \"<=2 sentences explaining the score and any major issue\"\n"
    "}"
)


async def evaluate_one(
    client: httpx.AsyncClient, prompt: str, answer: str,
) -> dict[str, Any]:
    if not OPENAI_API_KEY:
        return {"error": "OPENAI_API_KEY missing"}
    if not answer.strip():
        return {
            "relevance": 1, "document_grounding": 1, "legal_accuracy": 1,
            "completeness": 1, "overall": 1, "verdict": "FAIL",
            "comment": "Empty response from /chat.",
        }

    user_msg = (
        f"DOCUMENT SUMMARY (the user attached this PDF):\n{DOC_SUMMARY}\n\n"
        f"USER PROMPT:\n{prompt}\n\n"
        f"ASSISTANT RESPONSE:\n{answer[:8000]}"
    )

    body = {
        "model": EVAL_MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": EVAL_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    r = await client.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers, json=body, timeout=60.0,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except Exception:
        return {"error": "evaluator returned non-JSON", "raw": content[:500]}


# ----------------------------- Main -----------------------------


async def main() -> int:
    if not PDF_PATH.exists():
        print(f"PDF not found: {PDF_PATH}", file=sys.stderr)
        return 2

    pdf_bytes = PDF_PATH.read_bytes()
    pdf_name = PDF_PATH.name
    print(f"Loaded {pdf_name} ({len(pdf_bytes)/1024:.1f} KB)")
    print(f"Target: {BASE}/chat")
    print(f"Evaluator: {EVAL_MODEL}\n")

    results: list[dict[str, Any]] = []

    # Run prompts sequentially to avoid hammering the test server.
    async with httpx.AsyncClient() as client:
        for i, prompt in enumerate(PROMPTS, start=1):
            print(f"[{i:02d}/{len(PROMPTS)}] {prompt[:90]}")
            entry: dict[str, Any] = {"idx": i, "prompt": prompt}
            try:
                events, answer, elapsed = await stream_chat(
                    client, prompt, pdf_bytes, pdf_name,
                )
                entry["elapsed_s"] = round(elapsed, 2)
                entry["event_count"] = len(events)
                entry["event_types"] = sorted({e.get("type", "?") for e in events})
                entry["answer_len"] = len(answer)
                entry["answer_preview"] = answer[:1500]
                entry["answer_full"] = answer
                entry["error"] = None
            except Exception as e:
                entry["error"] = repr(e)
                entry["elapsed_s"] = None
                entry["answer_full"] = ""
                print(f"   request failed: {e!r}")
                results.append(entry)
                continue

            print(
                f"   {entry['elapsed_s']}s, {entry['event_count']} events, "
                f"answer={entry['answer_len']} chars",
            )

            # Evaluate
            try:
                eval_result = await evaluate_one(client, prompt, entry["answer_full"])
            except Exception as e:
                eval_result = {"error": f"evaluator exception: {e!r}"}
            entry["evaluation"] = eval_result
            verdict = eval_result.get("verdict", "?")
            overall = eval_result.get("overall", "?")
            print(f"   eval: overall={overall} verdict={verdict}")
            results.append(entry)

    # ------------------------- Reports -------------------------
    out_dir = Path(__file__).parent
    json_path = out_dir / "chat_pdf_eval_results.json"
    md_path = out_dir / "chat_pdf_eval_report.md"

    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    def avg(key: str) -> str:
        vals = [
            r["evaluation"].get(key) for r in results
            if isinstance(r.get("evaluation"), dict)
            and isinstance(r["evaluation"].get(key), (int, float))
        ]
        return f"{sum(vals)/len(vals):.2f}" if vals else "n/a"

    n_pass = sum(
        1 for r in results
        if isinstance(r.get("evaluation"), dict)
        and r["evaluation"].get("verdict") == "PASS"
    )
    n_weak = sum(
        1 for r in results
        if isinstance(r.get("evaluation"), dict)
        and r["evaluation"].get("verdict") == "WEAK"
    )
    n_fail = sum(
        1 for r in results
        if (isinstance(r.get("evaluation"), dict)
            and r["evaluation"].get("verdict") == "FAIL")
        or r.get("error")
    )

    lines: list[str] = [
        f"# /pyapiv2/chat eval — plaint.pdf x {len(PROMPTS)} prompts",
        "",
        f"- Endpoint: `{BASE}/chat`",
        f"- Attachment: `{pdf_name}` ({len(pdf_bytes)/1024:.1f} KB)",
        f"- Evaluator: `{EVAL_MODEL}`",
        "",
        "## Aggregate scores (1-5)",
        f"- Relevance: **{avg('relevance')}**",
        f"- Document grounding: **{avg('document_grounding')}**",
        f"- Legal accuracy: **{avg('legal_accuracy')}**",
        f"- Completeness: **{avg('completeness')}**",
        f"- Overall: **{avg('overall')}**",
        "",
        f"**Verdicts:** PASS={n_pass}  WEAK={n_weak}  FAIL={n_fail}",
        "",
        "## Per-prompt results",
        "",
        "| # | Prompt | Time(s) | Events | Ans chars | Overall | Verdict | Comment |",
        "|---|--------|--------:|-------:|----------:|--------:|---------|---------|",
    ]
    for r in results:
        ev = r.get("evaluation") or {}
        prompt_short = r["prompt"][:60].replace("|", "/")
        comment = (ev.get("comment") or r.get("error") or "").replace("|", "/")[:120]
        lines.append(
            f"| {r['idx']} | {prompt_short} | {r.get('elapsed_s', '-')} | "
            f"{r.get('event_count', '-')} | {r.get('answer_len', '-')} | "
            f"{ev.get('overall', '-')} | {ev.get('verdict', 'ERR')} | {comment} |",
        )

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSaved: {json_path}")
    print(f"Saved: {md_path}")
    print(
        f"\nVerdicts -> PASS={n_pass} WEAK={n_weak} FAIL={n_fail} "
        f"(of {len(results)})",
    )
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
