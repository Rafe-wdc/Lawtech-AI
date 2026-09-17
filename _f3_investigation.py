"""F3 Marathi investigation — 2 controlled prompts."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

API = "https://api.lawttorney.com/pyapi/search"
OUT = Path(__file__).parent / "_f3_investigation_out"
OUT.mkdir(exist_ok=True)


def load_key() -> str:
    env = Path(__file__).parent / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("API_KEYS="):
            return line.split("=", 1)[1].strip().strip('"').split(",")[0]
    raise RuntimeError("API_KEYS missing")


PROMPTS = [
    (
        # Q1 — SHORT Marathi Q&A. Explain a single statute in Marathi.
        # Response should be ~1-2K chars — well below shrink-guard threshold.
        # If it still has Devanagari digits + native कलम, the critic is failing
        # systemically (not the shrink-guard).
        "Q1_marathi_short_qa",
        "BNSS ची कलम 480 काय आहे? २-३ परिच्छेदांत मराठीत सांगा.",
        "mr",
    ),
    (
        # Q2 — MEDIUM Marathi draft. RTI application — should be ~2-4K chars.
        # Between short (Q1) and long (P1). Isolates whether the issue scales
        # with length.
        "Q2_marathi_medium_draft",
        "माहितीचा अधिकार अधिनियम, 2005 च्या कलम 6 अंतर्गत Pune Municipal "
        "Corporation च्या PIO ला उद्देशून एक RTI application मराठीत तयार करा. "
        "अर्जदार: Sushmita Kulkarni, वय 35 वर्षे, पत्ता Kothrud, Pune. "
        "माहिती: 2024 मध्ये मंजूर झालेल्या building plans यादी.",
        "mr",
    ),
]


def audit(pid: str, result: str, latency: float, agents: list) -> dict:
    dev_digits = len(re.findall(r"[०-९]", result))
    kalam = result.count("कलम")
    dhara = result.count("धारा")
    dev_acts = (
        result.count("भारतीय दंड संहिता")
        + result.count("भारतीय नागरिक सुरक्षा संहिता")
        + result.count("परक्राम्य लिखत अधिनियम")
        + result.count("माहितीचा अधिकार अधिनियम")
    )
    latin_section = len(re.findall(r"Section \d+", result))

    print(f"\n{'=' * 72}\n{pid} — audit\n{'=' * 72}")
    print(f"  len={len(result):,}c    latency={latency:.1f}s    agents={agents}")
    print(f"  [F3] Devanagari digits ०-९: {dev_digits}   (target: 0)")
    print(f"  [F3] native 'कलम': {kalam}   (target: 0)")
    print(f"  [F3] native 'धारा': {dhara}   (target: 0)")
    print(f"  [F3] native act titles: {dev_acts}   (target: 0)")
    print(f"  [F3] English 'Section N': {latin_section}")

    print("  --- Response first 800 chars ---")
    print("  " + result[:800].replace("\n", "\n  "))

    return {
        "pid": pid,
        "result_len": len(result),
        "latency_s": latency,
        "agents": agents,
        "dev_digits": dev_digits,
        "native_kalam": kalam,
        "native_dhara": dhara,
        "native_act_titles": dev_acts,
        "latin_section_anchors": latin_section,
        "result": result,
    }


def main() -> None:
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": load_key(),
    }
    findings = []
    for pid, prompt, lang in PROMPTS:
        print(f"\nSending {pid}: {prompt[:80]}...")
        body = {"Promptquery": prompt, "preferred_language": lang}
        t0 = time.perf_counter()
        r = requests.post(API, json=body, headers=headers, timeout=200)
        latency = time.perf_counter() - t0
        if r.status_code != 200:
            print(f"HTTP {r.status_code}: {r.text[:200]}")
            continue
        data = r.json()
        result = data.get("result", "")
        agents = data.get("agents_used", [])

        finding = audit(pid, result, latency, agents)
        findings.append(finding)

        (OUT / f"{pid}.json").write_text(
            json.dumps(finding, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"\n{'=' * 72}\nCOMPARISON\n{'=' * 72}")
    for f in findings:
        pid = f["pid"]
        print(f"{pid:<30}  len={f['result_len']:>7,}  dev_digits={f['dev_digits']:>3}  "
              f"kalam={f['native_kalam']:>3}  section={f['latin_section_anchors']:>3}")


if __name__ == "__main__":
    main()
