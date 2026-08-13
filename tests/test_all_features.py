"""Test ALL features #1-6 + sanitization layer.

Tests: Export, Memo, TOA, Statute Refs, Compliance, Fix Draft, Sanitization.
"""

import json
import requests
import time
import os

API = "http://localhost:5000/pyapi"
KEY = os.environ.get("LAWTECH_TEST_API_KEY", "")
HEADERS = {"Content-Type": "application/json", "X-API-Key": KEY}

results = []


def test(name, endpoint, payload, checks, timeout=120):
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"{'='*60}")
    t0 = time.time()
    try:
        resp = requests.post(f"{API}/{endpoint}", json=payload, headers=HEADERS, timeout=timeout)
        elapsed = time.time() - t0
        print(f"HTTP {resp.status_code} ({elapsed:.1f}s)")

        if resp.status_code != 200:
            print(f"FAIL: {resp.text[:300]}")
            results.append({"name": name, "status": "FAIL", "time": elapsed})
            return None

        ct = resp.headers.get("content-type", "")
        if "markdown" in ct or "text" in ct:
            content = resp.text
        elif "json" in ct:
            content = json.dumps(resp.json())
        else:
            content = f"[binary {len(resp.content)} bytes]"

        print(f"Content: {len(content):,} chars")

        passed = failed = 0
        for check_name, check_fn in checks:
            try:
                ok = check_fn(content, resp)
            except Exception:
                ok = False
            status = "PASS" if ok else "FAIL"
            if ok: passed += 1
            else: failed += 1
            print(f"  [{status}] {check_name}")

        results.append({"name": name, "status": "PASS" if failed == 0 else "PARTIAL",
                        "passed": passed, "failed": failed, "time": elapsed})
        return content
    except Exception as e:
        elapsed = time.time() - t0
        print(f"ERROR: {e}")
        results.append({"name": name, "status": "ERROR", "time": elapsed})
        return None


# ================================================================
# FEATURE #1: EXPORT
# ================================================================

test("Export: DOCX", "export", {
    "raw_text": "# Test\n\nThis is a **test** document.\n\n- Item 1\n- Item 2",
    "title": "Test Export", "format": "docx",
}, [
    ("Returns DOCX", lambda c, r: "vnd" in r.headers.get("content-type", "")),
    ("Has Content-Disposition", lambda c, r: "attachment" in r.headers.get("content-disposition", "")),
    ("File > 5KB", lambda c, r: len(r.content) > 5000),
])

test("Export: PDF", "export", {
    "raw_text": "# Test PDF\n\nSection 302 IPC.\n\n| Col1 | Col2 |\n|---|---|\n| A | B |",
    "title": "Test PDF", "format": "pdf",
}, [
    ("Returns PDF", lambda c, r: "pdf" in r.headers.get("content-type", "")),
    ("File > 5KB", lambda c, r: len(r.content) > 5000),
])

# ================================================================
# FEATURE #2: MEMO
# ================================================================

test("Memo: Research Memo (md)", "memo", {
    "query": "Section 302 IPC",
    "response_text": "Section 302 IPC deals with murder. Punishment: death or life imprisonment. Equivalent in BNS: Section 103. Case: Bachan Singh v State of Punjab (1980).",
    "format": "md",
}, [
    ("Contains ISSUE", lambda c, r: "ISSUE" in c.upper()),
    ("Contains BRIEF ANSWER", lambda c, r: "BRIEF" in c.upper() or "ANSWER" in c.upper()),
    ("Contains DISCUSSION", lambda c, r: "DISCUSSION" in c.upper() or "ANALYSIS" in c.upper()),
    ("Contains CONCLUSION", lambda c, r: "CONCLUSION" in c.upper()),
    ("Contains DISCLAIMER", lambda c, r: "DISCLAIMER" in c.upper()),
    ("Mentions Section 302", lambda c, r: "302" in c),
    ("No dash overflow", lambda c, r: c.count("---\n") < 5),
])

# ================================================================
# FEATURE #3: TOA
# ================================================================

test("TOA: Table of Authorities", "toa", {
    "query": "Section 498A and domestic violence",
    "response_text": "Section 498A IPC deals with cruelty. Arnesh Kumar v State of Bihar (2014) 8 SCC 273. Article 21 Constitution. DV Act 2005 Section 18.",
    "format": "md",
}, [
    ("Contains CASES section", lambda c, r: "CASE" in c.upper()),
    ("Contains STATUTES section", lambda c, r: "STATUT" in c.upper() or "LEGISLATION" in c.upper()),
    ("Mentions Arnesh Kumar", lambda c, r: "Arnesh Kumar" in c),
    ("Mentions 498A", lambda c, r: "498A" in c),
    ("No dash overflow", lambda c, r: c.count("---\n") < 5),
])

# ================================================================
# FEATURE #4: STATUTE REFS
# ================================================================

test("Statute Refs: Rental Agreement", "statute-refs", {
    "text": "## RENT\nTenant pays Rs 25,000 monthly.\n\n## TERMINATION\nTwo months notice.\n\n## DISPUTE\nArbitration.",
    "format": "md",
}, [
    ("Contains Act reference", lambda c, r: "Act" in c),
    ("Enhanced text longer", lambda c, r: len(c) > 100),
    ("Preserves RENT heading", lambda c, r: "RENT" in c),
    ("No dash overflow", lambda c, r: c.count("---\n") < 5),
])

# ================================================================
# FEATURE #5: COMPLIANCE CHECK
# ================================================================

DRAFT = """# BAIL APPLICATION

IN THE COURT OF SESSIONS COURT _____, AT _____

The accused has been charged under Section 379 IPC (now Section 304 BNS) for theft.

PRAYER: The applicant prays for bail under Section 439 CrPC (now Section 482 BNSS)."""

compliance_result = test("Compliance: Bail Application", "compliance-check", {
    "text": DRAFT, "format": "md",
}, [
    ("Contains CRITICAL or ISSUES", lambda c, r: "CRITICAL" in c.upper() or "ISSUE" in c.upper()),
    ("Identifies wrong mapping", lambda c, r: "304" in c or "482" in c or "mapping" in c.lower()),
    ("Contains Score", lambda c, r: "Score" in c or "score" in c),
    ("No dash overflow (< 10 lines of ---)", lambda c, r: c.count("---\n") < 10),
    ("Report < 10000 chars (no bloat)", lambda c, r: len(c) < 10000),
])

# ================================================================
# FEATURE #6: FIX DRAFT
# ================================================================

if compliance_result:
    test("Fix Draft: From compliance", "fix-draft", {
        "original_draft": DRAFT,
        "compliance_report": compliance_result,
        "format": "md",
    }, [
        ("Contains revised content", lambda c, r: len(c) > len(DRAFT)),
        ("Contains REVISED marker", lambda c, r: "REVISED" in c or "revised" in c.lower()),
        ("Removes wrong section 304", lambda c, r: "Section 304 BNS" not in c or "303" in c),
        ("No dash overflow", lambda c, r: c.count("---\n") < 10),
    ])
else:
    print("\nSKIPPED: Fix Draft (compliance check failed)")
    results.append({"name": "Fix Draft: From compliance", "status": "SKIP", "time": 0})

# ================================================================
# SANITIZATION: Test with intentionally bad content
# ================================================================

test("Sanitize: Dash overflow in export", "export", {
    "raw_text": "# Report\n\nGood content.\n\n" + ("---" * 40 + "\n") * 200 + "\nEnd.",
    "title": "Dash Test", "format": "docx",
}, [
    ("DOCX generated", lambda c, r: "vnd" in r.headers.get("content-type", "")),
    ("File < 100KB (no bloat)", lambda c, r: len(r.content) < 100000),
])

test("Sanitize: Dash overflow in compliance", "compliance-check", {
    "text": DRAFT, "format": "docx",
}, [
    ("DOCX generated", lambda c, r: "vnd" in r.headers.get("content-type", "")),
    ("File < 200KB (no 75-page PDF)", lambda c, r: len(r.content) < 200000),
])

# ================================================================
# PERSISTENCE: Save + Restore
# ================================================================

print(f"\n{'='*60}")
print("TEST: Persistence (save-turn + restore)")
print(f"{'='*60}")

try:
    # Create a thread first via search
    search_resp = requests.post(f"{API}/search", json={"Promptquery": "What is Section 302 IPC?"}, headers=HEADERS, timeout=60)
    if search_resp.ok:
        thread_id = search_resp.json().get("globalThreadId", "")
        print(f"Thread created: {thread_id[:12]}...")

        # Save a compliance turn
        save_resp = requests.post(f"{API}/save-turn", json={
            "thread_id": thread_id,
            "user_query": "[COMPLIANCE_CHECK]original draft text",
            "ai_response": "# COMPLIANCE REPORT\n\nScore: 7/10",
        }, headers=HEADERS, timeout=10)
        print(f"Save turn: HTTP {save_resp.status_code}")

        # Load and verify
        load_resp = requests.get(f"{API}/threads/{thread_id}/messages", headers=HEADERS, timeout=10)
        if load_resp.ok:
            msgs = load_resp.json().get("messages", [])
            has_compliance = any(m.get("user_query", "").startswith("[COMPLIANCE_CHECK]") for m in msgs)
            print(f"  Messages: {len(msgs)}, Has compliance: {has_compliance}")
            results.append({"name": "Persistence", "status": "PASS" if has_compliance else "FAIL",
                            "passed": 1 if has_compliance else 0, "failed": 0 if has_compliance else 1, "time": 0})
        else:
            results.append({"name": "Persistence", "status": "FAIL", "time": 0})
    else:
        print(f"Search failed: HTTP {search_resp.status_code}")
        results.append({"name": "Persistence", "status": "FAIL", "time": 0})
except Exception as e:
    print(f"ERROR: {e}")
    results.append({"name": "Persistence", "status": "ERROR", "time": 0})

# ================================================================
# SUMMARY
# ================================================================

print(f"\n{'='*60}")
print("FINAL SUMMARY")
print(f"{'='*60}")
print(f"{'#':<4} {'Test':<45} {'Status':<10} {'Time':<8} {'Details'}")
print(f"{'-'*60}")

total_pass = 0
total_tests = len(results)
for i, r in enumerate(results, 1):
    details = ""
    if "passed" in r:
        details = f"{r['passed']}/{r['passed']+r['failed']} checks"
    print(f"{i:<4} {r['name']:<45} {r['status']:<10} {r.get('time',0):.1f}s    {details}")
    if r["status"] == "PASS":
        total_pass += 1

print(f"{'-'*60}")
print(f"RESULT: {total_pass}/{total_tests} tests passed")

if total_pass < total_tests:
    exit(1)
