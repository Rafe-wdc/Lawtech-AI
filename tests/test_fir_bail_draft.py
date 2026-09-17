"""Test: Upload FIR PDF + Generate Bail Application Draft

Tests the full pipeline:
1. Upload PDF (0156 Publish FIR.pdf) with drafting prompt
2. Verify response extracts names, dates, sections from the PDF
3. Verify draft is in courtroom language with proper structure
4. Run on both local and test server
5. AI evaluator scores the output

Usage:
    python tests/test_fir_bail_draft.py [--local] [--server] [--both]
"""

import argparse
import json
import re
import requests
import sys
import time


PROMPT = (
    "Draft a bail application from the attached PDF. "
    "Take the names, the dates and all relevant information from the PDF "
    "and prepare a accurate draft according to the courtroom language"
)

PDF_PATH = "test_pdfs/0156 Publish FIR.pdf"

LOCAL_URL = "http://localhost:5000/pyapi"
SERVER_URL = "https://tool.lawttorney.com/pyapiv2"
import os
API_KEY = os.environ["LAWTECH_API_KEY"]


def send_request(api_url, prompt, pdf_path, timeout=300):
    """Send chat request with PDF attachment. Returns (response_data, elapsed, error)."""
    headers = {"X-API-Key": API_KEY}
    data = {"query": prompt}

    try:
        with open(pdf_path, "rb") as f:
            files_payload = [("files", ("0156_Publish_FIR.pdf", f, "application/pdf"))]
            t0 = time.time()
            resp = requests.post(
                f"{api_url}/chat",
                data=data,
                files=files_payload,
                headers=headers,
                timeout=timeout,
                stream=True,
            )
            elapsed = time.time() - t0
    except Exception as e:
        return None, 0, str(e)

    if resp.status_code != 200:
        return None, elapsed, f"HTTP {resp.status_code}: {resp.text[:300]}"

    # Parse SSE stream
    response_text = ""
    sources = []
    agents_used = []
    total_tokens = 0
    thread_id = ""

    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        try:
            evt = json.loads(line[6:])
            t = evt.get("type")
            if t == "response":
                response_text = evt.get("content", "")
            elif t == "sources":
                sources = evt.get("data", [])
            elif t == "done":
                agents_used = evt.get("agents_used", [])
                total_tokens = evt.get("total_tokens", 0)
                thread_id = evt.get("thread_id", "")
            elif t == "thread_id":
                thread_id = evt.get("data", "")
        except Exception:
            pass

    elapsed = time.time() - t0

    return {
        "result": response_text,
        "sources": sources,
        "agents_used": agents_used,
        "total_tokens": total_tokens,
        "thread_id": thread_id,
    }, elapsed, None


def quality_checks(result_text):
    """Run automated quality checks on the draft. Returns list of (name, passed, detail)."""
    checks = []
    text = result_text
    text_lower = text.lower()

    # --- Extraction checks: did it pull info from the PDF? ---
    # FIR details
    checks.append(("Extracts FIR number", bool(re.search(r"(?:FIR|F\.I\.R|CR)\s*(?:No|Number)?\.?\s*\d+", text, re.I)), ""))
    checks.append(("Extracts police station name", bool(re.search(r"police\s*station", text_lower)), ""))
    checks.append(("Contains date(s)", bool(re.search(r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}", text) or re.search(r"\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", text_lower)), ""))
    checks.append(("Contains person name(s)", bool(re.search(r"(?:accused|applicant|complainant|informant)[\s:]+\w", text_lower)), ""))

    # --- Legal sections ---
    has_sections = bool(re.search(r"section\s+\d+", text_lower))
    checks.append(("References IPC/BNS sections", has_sections, ""))
    checks.append(("References bail section (437/438/439)", bool(re.search(r"(?:437|438|439|480|481|482|483)", text)), ""))

    # --- Structure checks ---
    checks.append(("Has FACTS section", bool(re.search(r"##.*(?:fact|synopsis|brief)", text_lower)), ""))
    checks.append(("Has GROUNDS section", bool(re.search(r"##.*(?:ground|argument|submission)", text_lower)), ""))
    checks.append(("Has PRAYER section", bool(re.search(r"##.*(?:prayer|relief)", text_lower)), ""))
    checks.append(("Has VERIFICATION", bool(re.search(r"verif", text_lower)), ""))

    # --- Quality checks ---
    stock_phrases = ["it is humbly submitted", "hon'ble court may be pleased to note", "in the interest of justice"]
    stock_count = sum(text_lower.count(p) for p in stock_phrases)
    checks.append(("Stock phrases <= 3", stock_count <= 3, f"Found {stock_count}"))
    checks.append(("No filler intro section", not bool(re.search(r"##.*(?:introduction|preliminary|definitions)", text_lower)), ""))
    checks.append(("Has placeholders where needed", "[" in text and "]" in text, ""))
    checks.append(("Draft length > 3000 chars", len(text) > 3000, f"{len(text)} chars"))
    checks.append(("Draft length < 20000 chars", len(text) < 20000, f"{len(text)} chars"))

    # --- Document agent used ---
    # (checked separately in the caller)

    return checks


def ai_evaluate(api_url, draft_text, elapsed):
    """Use the API itself as an AI evaluator. Returns evaluation text."""
    eval_prompt = f"""You are a senior Indian criminal lawyer evaluating an AI-generated bail application.

This bail application was drafted from an uploaded FIR PDF. The AI was supposed to extract all relevant information (names, dates, FIR number, sections, police station) from the PDF and produce a court-ready bail application.

Score STRICTLY on these criteria (1-10 each):

1. **Information Extraction** — Did the AI correctly extract accused name(s), complainant name, FIR number, date, police station, and sections from the PDF?
2. **Legal Accuracy** — Are the correct CrPC/BNSS bail sections cited? Are the IPC/BNS sections from the FIR correctly referenced?
3. **Court-Ready Format** — Proper bail application structure? (Facts, Grounds, Prayer, Verification, Affidavit)
4. **Courtroom Language** — Professional legal language? Free of stock phrases and filler?
5. **Completeness** — All necessary elements present? (Court details, parties, case details, legal arguments, conditions)

Give each score (1-10), then:
- **OVERALL SCORE** (1-10)
- **3-line verdict**
- **Top 3 issues** (if any)

BAIL APPLICATION TO EVALUATE:
{draft_text[:10000]}
"""

    try:
        resp = requests.post(
            f"{api_url}/search",
            json={"Promptquery": eval_prompt},
            headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
            timeout=120,
        )
        if resp.ok:
            return resp.json().get("result", "Evaluation failed")
        return f"Evaluation failed: HTTP {resp.status_code}"
    except Exception as e:
        return f"Evaluation failed: {e}"


def run_test(label, api_url):
    """Run the full test against one server. Returns results dict."""
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"  API: {api_url}")
    print(f"{'='*70}")

    # Step 1: Send request
    print(f"\n  Step 1: Uploading FIR PDF + generating bail draft...")
    data, elapsed, error = send_request(api_url, PROMPT, PDF_PATH)

    if error:
        print(f"  FAILED: {error}")
        return {"label": label, "status": "FAIL", "error": error}

    result_text = data["result"]
    agents = data["agents_used"]
    tokens = data["total_tokens"]
    sources = data["sources"]

    print(f"  HTTP 200 ({elapsed:.1f}s)")
    print(f"  Response: {len(result_text):,} chars")
    print(f"  Agents: {agents}")
    print(f"  Tokens: {tokens:,}")
    print(f"  Sources: {len(sources)}")

    # Step 2: Quality checks
    print(f"\n  Step 2: Automated quality checks...")
    checks = quality_checks(result_text)

    # Add agent check
    has_document = "Document" in agents
    checks.append(("Document agent used", has_document, f"Agents: {agents}"))

    passed = failed = 0
    for name, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        if ok: passed += 1
        else: failed += 1
        suffix = f" ({detail})" if detail else ""
        print(f"    [{status}] {name}{suffix}")

    print(f"\n  Checks: {passed}/{passed+failed} passed")

    # Step 3: Response preview
    print(f"\n  Step 3: Response preview (first 1500 chars)...")
    preview = result_text[:1500].replace("\n", "\n    ")
    print(f"    {preview}")

    # Step 4: AI evaluation
    print(f"\n  Step 4: AI Quality Evaluation...")
    eval_result = ai_evaluate(api_url, result_text, elapsed)
    # Safely print (handle encoding)
    try:
        print(f"    {eval_result[:2000]}")
    except UnicodeEncodeError:
        print(f"    [Evaluation returned {len(eval_result)} chars - encoding issue, saved to report]")

    return {
        "label": label,
        "status": "PASS" if failed == 0 else "PARTIAL",
        "elapsed": elapsed,
        "response_len": len(result_text),
        "agents": agents,
        "tokens": tokens,
        "sources_count": len(sources),
        "checks_passed": passed,
        "checks_total": passed + failed,
        "response_preview": result_text[:3000],
        "evaluation": eval_result[:3000] if eval_result else "",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="Test local server only")
    parser.add_argument("--server", action="store_true", help="Test production server only")
    parser.add_argument("--both", action="store_true", help="Test both (default)")
    args = parser.parse_args()

    if not args.local and not args.server:
        args.both = True

    results = []

    if args.local or args.both:
        results.append(run_test("LOCAL SERVER", LOCAL_URL))

    if args.server or args.both:
        results.append(run_test("PRODUCTION SERVER", SERVER_URL))

    # Final Summary
    print(f"\n{'='*70}")
    print("  FINAL REPORT")
    print(f"{'='*70}")
    print(f"  {'Server':<25} {'Status':<10} {'Time':<10} {'Chars':<10} {'Checks':<12} {'Agents'}")
    print(f"  {'-'*70}")
    for r in results:
        if r["status"] == "FAIL":
            print(f"  {r['label']:<25} {'FAIL':<10} {'N/A':<10} {'N/A':<10} {'N/A':<12} {r.get('error','')[:30]}")
        else:
            checks_str = f"{r['checks_passed']}/{r['checks_total']}"
            print(f"  {r['label']:<25} {r['status']:<10} {r['elapsed']:.1f}s     {r['response_len']:,}     {checks_str:<12} {r['agents']}")

    # Save detailed report
    report_path = "tests/test_fir_bail_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n  Detailed report saved to: {report_path}")


if __name__ == "__main__":
    main()