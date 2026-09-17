"""Post-deploy smoke test — 3 prompts exercising F1-F12 against prod.

Each prompt sent to https://api.lawttorney.com/pyapi/search (batch mode).
Results saved to _prod_smoke_f1_f12_out/ so we can inspect per-prompt.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

API = "https://api.lawttorney.com/pyapi/search"
OUT = Path(__file__).parent / "_prod_smoke_f1_f12_out"
OUT.mkdir(exist_ok=True)


def load_api_key() -> str:
    env = Path(__file__).parent / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("API_KEYS="):
            return line.split("=", 1)[1].strip().strip('"').split(",")[0]
    raise RuntimeError("API_KEYS not found in .env")


def run(prompt_id: str, prompt: str, language: str | None = None,
        exercises: str = "") -> dict:
    """Send one prompt to prod and save results."""
    print(f"\n{'=' * 72}\n{prompt_id}: exercises {exercises}\n{'=' * 72}")
    print(f"PROMPT ({language or 'auto'}):\n{prompt[:250]}")

    body: dict = {"Promptquery": prompt}
    if language:
        body["preferred_language"] = language

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": load_api_key(),
    }

    t0 = time.perf_counter()
    try:
        r = requests.post(API, json=body, headers=headers, timeout=180)
    except requests.exceptions.RequestException as e:
        print(f"REQUEST FAILED: {e}")
        return {"prompt_id": prompt_id, "error": str(e)}
    latency = time.perf_counter() - t0

    print(f"\nHTTP {r.status_code} in {latency:.1f}s")
    if r.status_code != 200:
        print(f"BODY: {r.text[:500]}")
        return {"prompt_id": prompt_id, "status": r.status_code, "body": r.text[:1000]}

    data = r.json()
    result = data.get("result", "")
    print(f"AGENTS: {data.get('agents_used')}")
    print(f"RESPONSE LEN: {len(result)} chars")
    print(f"\n--- RESPONSE (first 1500 chars) ---\n{result[:1500]}")
    if len(result) > 1500:
        print(f"\n... [{len(result) - 1500} more chars]")

    # Save full response for later inspection
    out_file = OUT / f"{prompt_id}.json"
    out_file.write_text(
        json.dumps({
            "prompt_id": prompt_id,
            "exercises": exercises,
            "prompt": prompt,
            "language": language,
            "latency_s": latency,
            "agents_used": data.get("agents_used"),
            "result_len": len(result),
            "result": result,
            "sources_count": len(data.get("source", [])),
            "effective_query": data.get("effective_query"),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n--> saved to {out_file.name}")
    return {
        "prompt_id": prompt_id,
        "latency_s": latency,
        "agents_used": data.get("agents_used"),
        "result_len": len(result),
        "result": result,
    }


PROMPTS = [
    (
        "P1_marathi_short_draft_detailed",
        # Exercises: F3 (statute names stay English in Marathi), F5 (detailed
        # short doc must not fan-out into timeout), F9 (uses real client name
        # 'Ravindra Menon' + 'Ejipura, Bengaluru' — must not substitute),
        # F12 (Marathi query preserved through rewriter for flexible-backend
        # Drafting agent).
        "BNSS ची कलम 480 अंतर्गत तपशीलवार anticipatory bail application "
        "मराठीत तयार करा. आरोपीचे नाव Ravindra Menon, वय 42 वर्षे, पत्ता "
        "Ejipura, Bengaluru आणि FIR क्र. 445/2024 दिनांक 12 August 2024, "
        "HAL Police Station, कलम 420 IPC. सर्व कारणे, statutory framework, "
        "landmark Supreme Court judgments आणि Prayer clause सह.",
        "mr",
        "F3 (Marathi statute anchors) + F5 (short-doc detailed no-timeout) + "
        "F9 (real facts preserved) + F12 (Marathi query passed to Drafting)",
    ),
    (
        "P2_english_compound_intent",
        # Exercises: F8 (multi-intent — draft + case laws + explain), F1 (no
        # [verify] placeholders), F2 (citation grounding). Structured to
        # force the classifier to fan out multiple agents.
        "Draft a Section 138 NI Act demand notice for a dishonoured cheque "
        "of Rs. 5,00,000 in favour of my client Anand Krishnan (senior "
        "advocate, Chennai) AND cite 3 landmark Supreme Court judgments on "
        "Section 138 AND explain the essential ingredients of the offence "
        "under Section 138.",
        None,
        "F8 (multi-intent all three asks) + F1 (no [verify] placeholders) + "
        "F2 (real citations from corpus)",
    ),
    (
        "P3_hindi_count_directive",
        # Exercises: F12 (count directive '5' preserved through rewriter),
        # F3 (Hindi response keeps English statute anchors), F7 (correct
        # court attribution).
        "मुझे धारा 138 NI Act पर सुप्रीम कोर्ट के 5 landmark judgments "
        "चाहिए। हर judgment के लिए case name, citation, year, और "
        "1-2 lines में ratio decidendi दें।",
        "hi",
        "F12 (count '5' preserved) + F3 (Hindi + English statute anchors) + "
        "F7 (correct court attribution)",
    ),
]


def main() -> None:
    results = []
    for pid, prompt, lang, exercises in PROMPTS:
        r = run(pid, prompt, lang, exercises)
        results.append(r)

    print(f"\n{'=' * 72}\nSUMMARY\n{'=' * 72}")
    for r in results:
        pid = r.get("prompt_id")
        latency = r.get("latency_s", 0)
        agents = r.get("agents_used", [])
        length = r.get("result_len", 0)
        print(f"{pid:<40} {latency:>6.1f}s  {length:>7,}c  {agents}")


if __name__ == "__main__":
    main()
