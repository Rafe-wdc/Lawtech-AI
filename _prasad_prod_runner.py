"""Prod smoke: upload prasad.pdf + prasad comm 258.pdf, Marathi Section 258 review petition.

Target: https://api.lawttorney.com/pyapi/chat (multipart + SSE).
Drafting task in Marathi, so expect Drafting agent + per-section fan-out.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "https://api.lawttorney.com/pyapi"
import os
KEY = os.environ["LAWTECH_API_KEY"]

PDF_DIR = Path(r"D:\agentic_proj\Lawtech-AI\test_pdfs")
PDF_PATHS = [
    PDF_DIR / "prasad.pdf",
    PDF_DIR / "prasad comm 258.pdf",
]

PROMPT = (
    "सोबतचे सर्व कागदपत्रे वाचून महाराष्ट्र जमीन महसूल अधिनियम १९६६ अंतर्गत कलम २५८ चा अर्ज गट क्रमांक चुकला आहे. "
    "स्थगिती असताना दावा चालविला असल्याने बेकायदेशीर आदेशा कायम केला आहे असा निकाला या न्यायालयाने पारित केला आहे. "
    "तसेच प्रतीग्यापात्र माननीय उच्च न्यायाल्याआचा विचार करा नाहीं. या सर्व माहितीच्या आधारे मला review अर्ज माननीय अतिरिक्त "
    "विभागीय आयुक्त यांच्या न्यायालयात करून दे. विलंब माफी सहित करून दे. २ तुरी २०२६ ता तलाठी पत्र मिळाले आहे. "
    "हा अर्ज प्रसाद नंदकुमार अवचट दोघेही राहणार- फ्लॅट नंबर 405 सिल्व्ह सेंन्द्र अपार्टमेंट विंग 'डी', होटेल जिल्व्हर स्पून च्या मागे, "
    "वॉर्ड नं. 7, रामजी बोरावकेनगर, श्रीरामपूर, जिल्हा अहमदनगर, यांच्च्या वतीने तयार करून दे. योग्य महसुली नोंदीचे व स्थगिती आदेश "
    "असताना दिलेला निकाल व माहिती अधिकारात दिलेली माहिती उच्च न्यायालयाच्या आदेशाने देतेरे प्रतीष्यापात्र गाबतचे सर्वोच्च न्यायाश्व "
    "व मुंबई उच्च न्यायालय यांच्या निकालांचे संदर्भ दे. अतिरि विभागीय आयुक्त पुणे विभाग पुणे खंडेकडील आर.टी.पुर. रिव्हिजन/पुणे/632/२०२२ "
    "दिनांक ०५/०३/२०२६ या आदेशाच्या अनुषंगाने मला महाराष्ट्र जमीन महसूल अधिनियम १९५६ अंतर्गत कलम २५८ चा अर्ज करून दे. "
    "तहसीलदार दौंड यांच्याकडील केस रद्द करण्यात यावी त्या अनुषंगाने पारित केलेले मह‍सुली आदेश रद्द करण्यात यावेत. "
    "मराठी मध्ये करून हे. सर्व क्रमांक मराठी मध्ये टाकून दे. प्रसाद अवचट यन्च्य७अ वतीने अर्ज सविस्तर विस्तृत न्यायालयीन निकालांचा "
    "संदर्भ मेलामा सर्व वस्तुस्थिती टाकून अर्ज करून दे. मा. अतिरिक्त विभागीय आयुक्त, पुणे विभाग, पुणे यांनी आरटीएस रिव्हिजन "
    "पुणे/632/2022 या प्रकरणात दिनांक 05/03/2026 मा. अपर जिल्हाधिकारी, पुणे यांनी आर.टी.एस./23/164/2017 या प्रकरणात्त "
    "दिनांक 11/11/2022 उपविभागीय अधिकारी, दौड पुरंदर यांचेकडे आर.टी.एस. अपील क्र. 78/2016 दाखल केले. "
    "सदर अपील दिनांक 09/01/2017 व वहिवाट/एसआर/05/2015 या प्रकरणात दिनांक 01/02/2016 हे सर्व मूळ तहसील दावा, "
    "प्रथम व द्वितीय अपिलाचे शादेश रद्दबातल करते, या दाव्यात माहिती अधिकारातील उच्च न्यायल्याच्या अदेशासः सर्व कागदपत्रे "
    "दाखल केली होती त्याकडे न्याप्त्याच्या गजरचुकीने पहिले नरेल म्हणून आदेश व गड कमांक चुकलेले आहेत. मूळ दावा रद्द करावा "
    "म्हणून सामानेवाला आतचे अर्जदार यांनी नोटरी केलेले सर्व कागलपने दाखल केली होती. तसेच दिनांक. १०/११/२०२५ च्या लेखी "
    "युक्तिवादात तशीच मागणी केली होती त्याकडे दुर्लक्ष झाले असावे. सर्व कागदपत्रे चोडलती आहेत. त्यानुसार सविस्तर व विस्तृत "
    "सर्व तथ्यांची दाखल घेऊन योग्य त्या ठिकाणी निकालांचे संदर्भ घेऊन अर्ज तयार करून दे. पुनर्विलोकन अर्ज अतिरिक्त विभागीय आयुक्त, "
    "पुणे विभाग"
)

OUT_DIR = Path(__file__).parent
OUT_MD = OUT_DIR / "_prasad_prod.md"
OUT_META = OUT_DIR / "_prasad_prod_meta.json"
OUT_RAW = OUT_DIR / "_prasad_prod_events.jsonl"


async def main() -> int:
    files_bytes: list[tuple[str, bytes]] = []
    total_bytes = 0
    for p in PDF_PATHS:
        if not p.exists():
            print(f"MISSING: {p}")
            return 2
        b = p.read_bytes()
        files_bytes.append((p.name, b))
        total_bytes += len(b)
        print(f"PDF: {p.name} ({len(b):,} bytes)")

    print(f"BASE: {BASE}")
    print(f"PROMPT ({len(PROMPT)} chars, first 120): {PROMPT[:120]}...")
    print(f"Total upload: {total_bytes:,} bytes\n")

    t0 = time.time()
    headers = {"X-API-Key": KEY}
    data = {"query": PROMPT, "preferred_language": "mr"}
    files = [
        ("files", (name, body, "application/pdf")) for name, body in files_bytes
    ]

    answer = ""
    events_seen: dict[str, int] = {}
    agents_planned: list[str] = []
    progress_steps: list[str] = []
    file_processing_events: list[dict] = []
    errors: list[str] = []
    done_event: dict | None = None
    ttfb_ms: int | None = None
    raw_lines = 0

    with OUT_RAW.open("w", encoding="utf-8") as raw_fh:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST", f"{BASE}/chat",
                headers=headers, data=data, files=files, timeout=1500.0,
            ) as resp:
                if ttfb_ms is None:
                    ttfb_ms = round((time.time() - t0) * 1000)
                if resp.status_code != 200:
                    err = await resp.aread()
                    msg = err.decode(errors="replace")[:1000]
                    print(f"HTTP {resp.status_code}: {msg}")
                    return 1
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        e = json.loads(line[6:])
                    except Exception:
                        continue
                    raw_fh.write(line[6:] + "\n")
                    raw_lines += 1
                    t = e.get("type") or "<no-type>"
                    events_seen[t] = events_seen.get(t, 0) + 1
                    if t == "token":
                        tok = e.get("data") or e.get("content") or e.get("token") or ""
                        if isinstance(tok, str):
                            answer += tok
                    elif t in ("response", "final_answer", "answer"):
                        txt = e.get("content") or e.get("data") or e.get("text") or ""
                        if isinstance(txt, str) and len(txt) > len(answer):
                            answer = txt
                    elif t == "agents_planned":
                        agents_planned = e.get("agents") or e.get("data") or []
                    elif t == "progress":
                        step = e.get("step") or e.get("stage")
                        if step:
                            progress_steps.append(step)
                    elif t == "file_processing":
                        file_processing_events.append(e)
                    elif t in ("error", "guardrail_error"):
                        errors.append(json.dumps(e, ensure_ascii=False)[:500])
                    elif t == "done":
                        done_event = e
                        for k in ("final_response", "response", "final_answer"):
                            v = e.get(k)
                            if isinstance(v, str) and len(v) > len(answer):
                                answer = v

    elapsed = round(time.time() - t0, 2)
    OUT_MD.write_text(answer or "(empty)", encoding="utf-8")

    def devanagari_pct(s: str) -> float:
        if not s:
            return 0.0
        letters = [c for c in s if c.isalpha()]
        if not letters:
            return 0.0
        dev = sum(1 for c in letters if "ऀ" <= c <= "ॿ")
        return round(dev * 100 / len(letters), 1)

    meta = {
        "elapsed_s": elapsed,
        "ttfb_ms": ttfb_ms,
        "http_ok": True,
        "raw_events_written": raw_lines,
        "answer_len": len(answer),
        "answer_devanagari_pct": devanagari_pct(answer),
        "agents_planned": agents_planned,
        "event_type_counts": events_seen,
        "progress_step_count": len(progress_steps),
        "unique_progress_steps": sorted(set(progress_steps)),
        "file_processing_summary": [
            {
                "message": e.get("message"),
                "files": e.get("files"),
                "stage": e.get("stage"),
                "rejected": e.get("rejected"),
            }
            for e in file_processing_events
        ],
        "errors": errors,
        "done_event_keys": list((done_event or {}).keys()),
    }
    OUT_META.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(meta, indent=2, ensure_ascii=False))
    print()
    print(f"Response saved: {OUT_MD}")
    print(f"Meta saved:     {OUT_META}")
    print(f"Raw SSE saved:  {OUT_RAW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
