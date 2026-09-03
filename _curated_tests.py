"""Curated end-to-end tests over real files from test_pdfs/, test_image/,
test_docx/. Hits the in-process FastAPI app via TestClient — no live URL.

Run a single test by passing its number:
    python _curated_tests.py 1
    python _curated_tests.py 2
    ...

Run all sequentially:
    python _curated_tests.py all
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()

API_KEYS = os.environ.get("API_KEYS", "")
USER_KEY = API_KEYS.split(",")[0].strip().strip('"') or "dev"

REPO = Path(__file__).resolve().parent
TEST_PDFS = REPO / "test_pdfs"
TEST_IMAGES = REPO / "test_image"
TEST_DOCX = REPO / "test_docx"


# --- Shared client + SSE helpers ----------------------------------------

PROD_URL = "https://api.lawttorney.com"


class _ProdClient:
    """Minimal subset of TestClient that talks to a live HTTPS server."""

    def __init__(self, base_url: str):
        import httpx
        self._base = base_url.rstrip("/")
        self._http = httpx.Client(timeout=httpx.Timeout(420.0, connect=30.0))

    def get(self, path: str, **kw):
        return self._http.get(self._base + path, **kw)

    def post(self, path: str, **kw):
        return self._http.post(self._base + path, **kw)

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _client():
    if os.environ.get("CURATED_TESTS_TARGET") == "prod":
        return _ProdClient(PROD_URL)
    from fastapi.testclient import TestClient
    from core.gateway import app
    return TestClient(app)


def _extract_final(text: str) -> str:
    """Pull the final assistant response from the SSE stream."""
    out = ""
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            ev = json.loads(line[6:])
        except Exception:
            continue
        if ev.get("type") == "response":
            c = ev.get("content")
            if isinstance(c, str) and len(c) > len(out):
                out = c
    return out


def _print_header(num: int, title: str):
    line = "=" * 70
    print(f"\n{line}\nTEST {num}: {title}\n{line}")


def _print_result(passed: bool, msg: str):
    tag = "PASS" if passed else "FAIL"
    print(f"\n[{tag}] {msg}\n")


def _post_with_file(client, file_path: Path, mime: str, query: str,
                    thread_id: str | None = None) -> dict:
    """POST /pyapi/chat with one file attachment. Returns dict with
    {status, elapsed, final_answer, events_by_type}.
    """
    thread_id = thread_id or f"curated-{uuid.uuid4().hex[:8]}"
    t0 = time.time()
    with file_path.open("rb") as f:
        r = client.post(
            "/pyapi/chat",
            headers={"X-API-Key": USER_KEY},
            data={
                "query": query,
                "globalThreadId": thread_id,
                "preferred_language": "en",
            },
            files={"files": (file_path.name, f, mime)},
            timeout=420.0,
        )
    elapsed = time.time() - t0
    final = _extract_final(r.text) if r.status_code == 200 else ""

    events_by_type: dict[str, int] = {}
    fp_stages: list[str] = []
    for line in r.text.splitlines():
        if line.startswith("data: "):
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            t = ev.get("type", "?")
            events_by_type[t] = events_by_type.get(t, 0) + 1
            if t == "file_processing":
                fp_stages.append(ev.get("stage", "?"))

    return {
        "status": r.status_code,
        "elapsed": elapsed,
        "final_answer": final,
        "events_by_type": events_by_type,
        "file_processing_stages": fp_stages,
        "raw": r.text,
        "thread_id": thread_id,
    }


# --- Test 1: Small text PDF ---------------------------------------------

def test_1_small_pdf() -> int:
    _print_header(1, "Small text PDF (Index.pdf, ~80 KB)")
    pdf = TEST_PDFS / "Index.pdf"
    if not pdf.exists():
        print(f"[SKIP] {pdf} missing")
        return 2
    print(f"file = {pdf.name}  ({pdf.stat().st_size:,} bytes)")
    with _client() as client:
        res = _post_with_file(
            client, pdf, "application/pdf",
            "What is this document about? Give a 2-3 sentence summary.",
        )
    print(f"status={res['status']}  elapsed={res['elapsed']:.1f}s  "
          f"events={res['events_by_type']}")
    print(f"file_processing stages: {res.get('file_processing_stages', [])}")
    print(f"response length: {len(res['final_answer'])}")
    print(f"--- first 400 chars ---\n{res['final_answer'][:400]}")
    ok = res["status"] == 200 and len(res["final_answer"]) > 100
    _print_result(ok, f"small PDF Q&A — answer length {len(res['final_answer'])}")
    return 0 if ok else 1


# --- Test 2: Medium text PDF + drafting ---------------------------------

def test_2_medium_pdf_drafting() -> int:
    _print_header(2, "Medium text PDF + drafting (Adv. P. P. Deshmukh Sec. 9.pdf, ~150 KB)")
    pdf = TEST_PDFS / "Adv. P. P. Deshmukh Sec. 9.pdf"
    if not pdf.exists():
        print(f"[SKIP] {pdf} missing")
        return 2
    print(f"file = {pdf.name}  ({pdf.stat().st_size:,} bytes)")
    with _client() as client:
        res = _post_with_file(
            client, pdf, "application/pdf",
            "Read the attached. Draft a short reply notice on behalf of the "
            "opposite party in formal Indian legal style.",
        )
    print(f"status={res['status']}  elapsed={res['elapsed']:.1f}s  "
          f"events={res['events_by_type']}")
    print(f"file_processing stages: {res.get('file_processing_stages', [])}")
    print(f"response length: {len(res['final_answer'])}")
    print(f"--- first 600 chars ---\n{res['final_answer'][:600]}")
    ok = res["status"] == 200 and len(res["final_answer"]) > 500
    _print_result(ok, f"drafting from PDF — response length {len(res['final_answer'])}")
    return 0 if ok else 1


# --- Test 3: Large text PDF ---------------------------------------------

def test_3_large_pdf_summary() -> int:
    _print_header(3, "Large PDF summary (judgement123.pdf, ~11.8 MB)")
    pdf = TEST_PDFS / "judgement123.pdf"
    if not pdf.exists():
        print(f"[SKIP] {pdf} missing")
        return 2
    print(f"file = {pdf.name}  ({pdf.stat().st_size:,} bytes)")
    with _client() as client:
        res = _post_with_file(
            client, pdf, "application/pdf",
            "Summarize this judgment in 5 bullet points covering: parties, "
            "main legal issue, court's reasoning, decision, and citations.",
        )
    print(f"status={res['status']}  elapsed={res['elapsed']:.1f}s  "
          f"events={res['events_by_type']}")
    print(f"file_processing stages: {res.get('file_processing_stages', [])}")
    print(f"response length: {len(res['final_answer'])}")
    print(f"--- first 800 chars ---\n{res['final_answer'][:800]}")
    ok = res["status"] == 200 and len(res["final_answer"]) > 500
    _print_result(ok, f"large PDF summary — response length {len(res['final_answer'])}")
    return 0 if ok else 1


# --- Test 4: DOCX -------------------------------------------------------

def test_4_docx_fact_extraction() -> int:
    _print_header(4, "DOCX fact extraction (MATTER For FIR 156 3 CRPC.docx)")
    doc = TEST_DOCX / "MATTER For FIR 156 3 CRPC.docx"
    if not doc.exists():
        print(f"[SKIP] {doc} missing")
        return 2
    print(f"file = {doc.name}  ({doc.stat().st_size:,} bytes)")
    with _client() as client:
        res = _post_with_file(
            client, doc,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "Extract the key facts from this matter: parties, dates, alleged "
            "offences, sections invoked, and police station / court.",
        )
    print(f"status={res['status']}  elapsed={res['elapsed']:.1f}s  "
          f"events={res['events_by_type']}")
    print(f"file_processing stages: {res.get('file_processing_stages', [])}")
    print(f"response length: {len(res['final_answer'])}")
    print(f"--- first 600 chars ---\n{res['final_answer'][:600]}")
    ok = res["status"] == 200 and len(res["final_answer"]) > 300
    _print_result(ok, f"DOCX fact extraction — response length {len(res['final_answer'])}")
    return 0 if ok else 1


# --- Test 5: Image vision OCR -------------------------------------------

def test_5_image_vision_ocr() -> int:
    _print_header(5, "Image vision OCR (test_1_legal_notice.jpg)")
    img = TEST_IMAGES / "test_1_legal_notice.jpg"
    if not img.exists():
        print(f"[SKIP] {img} missing")
        return 2
    print(f"file = {img.name}  ({img.stat().st_size:,} bytes)")
    with _client() as client:
        res = _post_with_file(
            client, img, "image/jpeg",
            "What does this notice say? Quote the key sentences verbatim.",
        )
    print(f"status={res['status']}  elapsed={res['elapsed']:.1f}s  "
          f"events={res['events_by_type']}")
    print(f"file_processing stages: {res.get('file_processing_stages', [])}")
    print(f"response length: {len(res['final_answer'])}")
    print(f"--- first 600 chars ---\n{res['final_answer'][:600]}")
    ok = res["status"] == 200 and len(res["final_answer"]) > 100
    _print_result(ok, f"image vision OCR — response length {len(res['final_answer'])}")
    return 0 if ok else 1


# --- Test 6: Long prompt without file -----------------------------------

_LONG_PROMPT_TAIL_SENTINEL = "GOLDEN-ANCHOR-91827"


def _build_long_rental_prompt() -> str:
    """~22 K chars of rental-agreement boilerplate with a unique sentinel
    planted very late in the text (so any truncation upstream drops it)."""
    para = (
        "The Tenant shall pay the monthly rent on or before the 5th day of "
        "each English calendar month without any default. The Landlord "
        "reserves the right to inspect the demised premises with prior "
        "written notice of 48 hours. The Tenant shall not sublet, assign, "
        "or transfer the demised premises in whole or in part without the "
        "prior written consent of the Landlord. "
    )
    body = (para * 50)[:22000]
    return (
        "I have a rental agreement that I want analysed under Indian "
        "tenancy law. Here is the pasted text:\n\n"
        "RENTAL AGREEMENT\n\n"
        + body
        + f"\n\n[CLAUSE 47-X — SECURITY DEPOSIT KEY — marker "
        f"{_LONG_PROMPT_TAIL_SENTINEL}]: The Tenant has paid INR 3,00,000 "
        "as refundable security deposit, refundable on vacating after "
        "deducting any outstanding dues.\n\n"
        "QUESTION: What is the security deposit amount in this agreement "
        f"and which clause covers it (look for marker {_LONG_PROMPT_TAIL_SENTINEL})?"
    )


def test_6_long_prompt_no_file() -> int:
    _print_header(6, "Long prompt without file (~22K chars, sentinel very late)")
    prompt = _build_long_rental_prompt()
    print(f"prompt length: {len(prompt)}")
    print(f"sentinel position: {prompt.find(_LONG_PROMPT_TAIL_SENTINEL)}")

    with _client() as client:
        t0 = time.time()
        r = client.post(
            "/pyapi/search",
            headers={"X-API-Key": USER_KEY},
            json={
                "Promptquery": prompt,
                "globalThreadId": f"curated-long-{uuid.uuid4().hex[:8]}",
                "preferred_language": "en",
            },
            timeout=420.0,
        )
        elapsed = time.time() - t0
    print(f"status={r.status_code}  elapsed={elapsed:.1f}s")
    if r.status_code != 200:
        print(f"body[:600] = {r.text[:600]}")
        _print_result(False, "non-200")
        return 1
    body = r.json()
    final = body.get("result", "") or ""
    print(f"response length: {len(final)}")
    print(f"--- first 400 chars ---\n{final[:400]}")
    has_sentinel = _LONG_PROMPT_TAIL_SENTINEL in final
    has_amount = "3,00,000" in final or "3 lakh" in final.lower()
    ok = r.status_code == 200 and (has_sentinel or has_amount)
    _print_result(ok, f"long prompt — sentinel_in_response={has_sentinel}, "
                       f"amount_in_response={has_amount}")
    return 0 if ok else 1


# --- Test 7: Multi-turn with attachment ---------------------------------

def test_7_multi_turn_with_docx() -> int:
    _print_header(7, "Multi-turn with DOCX (turn 1 upload, turn 2 follow-up no file)")
    doc = TEST_DOCX / "MATTER For FIR 156 3 CRPC.docx"
    if not doc.exists():
        print(f"[SKIP] {doc} missing")
        return 2
    print(f"file = {doc.name}  ({doc.stat().st_size:,} bytes)")
    thread_id = f"curated-mt-{uuid.uuid4().hex[:8]}"

    with _client() as client:
        # Turn 1: upload + question
        r1 = _post_with_file(
            client, doc,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "Summarize the attached matter in 3 lines: who, what, where.",
            thread_id=thread_id,
        )
        print(f"\n[turn 1] status={r1['status']}  elapsed={r1['elapsed']:.1f}s  "
              f"response length: {len(r1['final_answer'])}")
        print(f"[turn 1] first 300:\n{r1['final_answer'][:300]}")

        # Turn 2: same thread, no file
        t0 = time.time()
        r2 = client.post(
            "/pyapi/chat",
            headers={"X-API-Key": USER_KEY},
            data={
                "query": (
                    "Looking at the matter we discussed, list the exact "
                    "sections of law mentioned and the police station "
                    "or court named in the document."
                ),
                "globalThreadId": thread_id,
                "preferred_language": "en",
            },
            files={},  # no file
            timeout=420.0,
        )
        elapsed2 = time.time() - t0
        ans2 = _extract_final(r2.text) if r2.status_code == 200 else ""
        print(f"\n[turn 2] status={r2.status_code}  elapsed={elapsed2:.1f}s  "
              f"response length: {len(ans2)}")
        print(f"[turn 2] first 500:\n{ans2[:500]}")

    ok = (r1["status"] == 200 and len(r1["final_answer"]) > 100
          and r2.status_code == 200 and len(ans2) > 100)
    _print_result(ok, f"multi-turn — turn1={len(r1['final_answer'])}ch, "
                       f"turn2={len(ans2)}ch")
    return 0 if ok else 1


# --- Runner -------------------------------------------------------------

TESTS = {
    1: ("Small text PDF", test_1_small_pdf),
    2: ("Medium PDF + drafting", test_2_medium_pdf_drafting),
    3: ("Large PDF summary", test_3_large_pdf_summary),
    4: ("DOCX fact extraction", test_4_docx_fact_extraction),
    5: ("Image vision OCR", test_5_image_vision_ocr),
    6: ("Long prompt no file", test_6_long_prompt_no_file),
    7: ("Multi-turn DOCX", test_7_multi_turn_with_docx),
}


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python _curated_tests.py <1-7|all>")
        return 1
    arg = sys.argv[1].strip().lower()

    if arg == "all":
        results: list[tuple[int, str, int]] = []
        for n, (name, fn) in TESTS.items():
            rc = fn()
            results.append((n, name, rc))
        print("\n\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        for n, name, rc in results:
            print(f"  Test {n} ({name}): "
                  f"{'PASS' if rc == 0 else 'SKIP' if rc == 2 else 'FAIL'}")
        return 0 if all(rc == 0 for _, _, rc in results) else 1

    try:
        n = int(arg)
    except ValueError:
        print(f"Bad arg: {arg!r}")
        return 1

    if n not in TESTS:
        print(f"No test {n}. Available: {sorted(TESTS.keys())}")
        return 1

    name, fn = TESTS[n]
    return fn()


if __name__ == "__main__":
    raise SystemExit(main())
