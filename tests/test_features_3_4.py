"""Test Features #3 (Table of Authorities) and #4 (Statute Referencing)

Tests both endpoints with realistic legal content and validates:
- Correct citation extraction (TOA)
- Proper statute injection (Statute Refs)
- Format correctness (md/docx)
- Content preservation
- Quality checks via AI evaluator
"""

import json
import requests
import time
import sys

API = "http://localhost:5000/pyapi"
import os
KEY = os.environ["LAWTECH_API_KEY"]
HEADERS = {"Content-Type": "application/json", "X-API-Key": KEY}

results = []


def test(name, endpoint, payload, checks):
    print(f"\n{'='*70}")
    print(f"TEST: {name}")
    print(f"{'='*70}")
    t0 = time.time()
    try:
        resp = requests.post(f"{API}/{endpoint}", json=payload, headers=HEADERS, timeout=120)
        elapsed = time.time() - t0
        print(f"HTTP {resp.status_code} ({elapsed:.1f}s)")

        if resp.status_code != 200:
            print(f"FAIL: {resp.text[:300]}")
            results.append({"name": name, "status": "FAIL", "error": resp.text[:200], "time": elapsed})
            return None

        ct = resp.headers.get("content-type", "")
        if "markdown" in ct or "text" in ct:
            content = resp.text
        elif "json" in ct:
            content = json.dumps(resp.json())
        else:
            content = f"[binary {len(resp.content)} bytes]"

        print(f"Content: {len(content):,} chars")

        passed = 0
        failed = 0
        for check_name, check_fn in checks:
            try:
                ok = check_fn(content, resp)
            except Exception:
                ok = False
            status = "PASS" if ok else "FAIL"
            if ok:
                passed += 1
            else:
                failed += 1
            print(f"  [{status}] {check_name}")

        results.append({
            "name": name,
            "status": "PASS" if failed == 0 else "PARTIAL",
            "passed": passed,
            "failed": failed,
            "content_len": len(content),
            "time": elapsed,
        })
        return content
    except Exception as e:
        elapsed = time.time() - t0
        print(f"ERROR: {e}")
        results.append({"name": name, "status": "ERROR", "error": str(e), "time": elapsed})
        return None


# ================================================================
# FEATURE #3: TABLE OF AUTHORITIES
# ================================================================

RICH_LEGAL_RESPONSE = """# Analysis of Section 498A IPC and Domestic Violence

## Statutory Framework

Section 498A of the Indian Penal Code, 1860 deals with cruelty by husband or relatives of husband. The equivalent provision in BNS is Section 85.

The Protection of Women from Domestic Violence Act, 2005 provides civil remedies including protection orders under Section 18, residence orders under Section 19, and monetary relief under Section 20.

## Key Case Law

1. **Arnesh Kumar v. State of Bihar** (2014) 8 SCC 273 - Supreme Court held that police must follow the checklist before arresting under Section 498A.

2. **Rajesh Sharma v. State of U.P.** (2017) 10 SCC 817 - Supreme Court constituted Family Welfare Committees.

3. **Social Action Forum for Manav Adhikar v. Union of India** (2018) - Supreme Court upheld the constitutional validity of Section 498A.

## Constitutional Provisions

Article 14 (Right to Equality) and Article 21 (Right to Life and Personal Liberty) of the Constitution of India are relevant.

## Additional Statutes

Under Section 12 of the DV Act, an aggrieved person can file an application before the Magistrate. The Dowry Prohibition Act, 1961 under Section 3 prohibits giving or taking dowry. Section 304B IPC (Section 80 BNS) deals with dowry death.
"""

test(
    "TOA: Rich Legal Response (md)",
    "toa",
    {
        "query": "Section 498A IPC and domestic violence remedies",
        "response_text": RICH_LEGAL_RESPONSE,
        "format": "md",
    },
    [
        ("Contains CASES section", lambda c, r: "CASES" in c.upper() or "CASE LAW" in c.upper()),
        ("Contains STATUTES section", lambda c, r: "STATUT" in c.upper() or "LEGISLATION" in c.upper()),
        ("Contains CONSTITUTIONAL section", lambda c, r: "CONSTITUTION" in c.upper()),
        ("Mentions Arnesh Kumar", lambda c, r: "Arnesh Kumar" in c),
        ("Mentions Rajesh Sharma", lambda c, r: "Rajesh Sharma" in c),
        ("Mentions Section 498A", lambda c, r: "498A" in c),
        ("Mentions Article 14", lambda c, r: "Article 14" in c),
        ("Mentions Article 21", lambda c, r: "Article 21" in c),
        ("Mentions DV Act", lambda c, r: "Domestic Violence" in c or "DV Act" in c),
        ("Mentions Dowry Prohibition", lambda c, r: "Dowry" in c),
        ("Response > 500 chars", lambda c, r: len(c) > 500),
    ],
)


test(
    "TOA: DOCX Format",
    "toa",
    {
        "query": "Section 302 IPC",
        "response_text": "Section 302 IPC prescribes death or life imprisonment. Bachan Singh v. State of Punjab (1980) 2 SCC 684 established rarest of rare. Article 21 protects right to life.",
        "format": "docx",
    },
    [
        ("Returns DOCX content-type", lambda c, r: "vnd" in r.headers.get("content-type", "")),
        ("Has Content-Disposition", lambda c, r: "attachment" in r.headers.get("content-disposition", "")),
        ("File > 10KB", lambda c, r: len(r.content) > 10000),
    ],
)


# ================================================================
# FEATURE #4: STATUTE REFERENCING
# ================================================================

BARE_RENTAL = """# RENTAL AGREEMENT

## 1. RENT
The Tenant shall pay monthly rent of Rs. 25,000 on or before the 5th of each month.

## 2. SECURITY DEPOSIT
The Tenant has deposited Rs. 1,50,000 as security deposit, refundable upon vacating.

## 3. TERMINATION
Either party may terminate by giving two months written notice.

## 4. MAINTENANCE
Minor repairs up to Rs. 3,000 shall be borne by the Tenant. Major structural repairs are the Landlord's responsibility.

## 5. DISPUTE RESOLUTION
Any dispute shall be resolved through arbitration.

## 6. SUBLETTING
The Tenant shall not sublet without prior written consent of the Landlord."""

test(
    "Statute Refs: Rental Agreement",
    "statute-refs",
    {"text": BARE_RENTAL, "format": "md"},
    [
        ("Contains Transfer of Property Act", lambda c, r: "Transfer of Property" in c),
        ("Contains Indian Contract Act", lambda c, r: "Contract Act" in c),
        ("Contains Arbitration Act", lambda c, r: "Arbitration" in c and "1996" in c),
        ("More Section refs than original", lambda c, r: c.count("Section ") > BARE_RENTAL.count("Section ")),
        ("Preserves headings", lambda c, r: "RENT" in c and "TERMINATION" in c and "MAINTENANCE" in c),
        ("Preserves amounts", lambda c, r: "25,000" in c and "1,50,000" in c),
        ("Enhanced text is longer", lambda c, r: len(c) > len(BARE_RENTAL)),
    ],
)


BARE_CRIMINAL = """# BAIL APPLICATION

The accused has been charged with causing hurt to the complainant. The investigation is complete and the charge sheet has been filed. The accused has no prior criminal record and is willing to furnish surety.

GROUNDS:
1. The offense is bailable in nature
2. The accused has deep roots in the community
3. There is no flight risk
4. The accused is the sole breadwinner"""

test(
    "Statute Refs: Bail Application",
    "statute-refs",
    {"text": BARE_CRIMINAL, "format": "md"},
    [
        ("Contains CrPC/BNSS ref", lambda c, r: "Criminal Procedure" in c or "BNSS" in c or "CrPC" in c or "Nagarik Suraksha" in c),
        ("Contains IPC/BNS ref", lambda c, r: "Penal Code" in c or "BNS" in c or "IPC" in c or "Nyaya Sanhita" in c),
        ("Contains bail section", lambda c, r: any(s in c for s in ["436", "437", "438", "439", "480", "481", "482", "483"])),
        ("Preserves grounds", lambda c, r: "bailable" in c.lower() and "flight risk" in c.lower()),
        ("Enhanced text is longer", lambda c, r: len(c) > len(BARE_CRIMINAL)),
    ],
)


BARE_EMPLOYMENT = """# EMPLOYMENT AGREEMENT

## NON-COMPETE
The Employee agrees not to work for any competitor within 50 km for 2 years after termination.

## TERMINATION
The Employer may terminate with 30 days notice. The Employee may resign with 60 days notice.

## INTELLECTUAL PROPERTY
All inventions created during employment shall belong to the Employer.

## GRATUITY
The Employee shall be entitled to gratuity after 5 years of continuous service."""

test(
    "Statute Refs: Employment Agreement",
    "statute-refs",
    {"text": BARE_EMPLOYMENT, "format": "md"},
    [
        ("Contains Contract Act", lambda c, r: "Contract Act" in c),
        ("Contains Section 27 or restraint of trade", lambda c, r: "Section 27" in c or "restraint of trade" in c.lower()),
        ("Contains Gratuity Act", lambda c, r: "Gratuity" in c and ("1972" in c or "Act" in c)),
        ("Preserves structure", lambda c, r: "NON-COMPETE" in c and "TERMINATION" in c),
        ("Enhanced text is longer", lambda c, r: len(c) > len(BARE_EMPLOYMENT)),
    ],
)


# ================================================================
# AI EVALUATOR: Quality assessment using the API itself
# ================================================================

print(f"\n{'='*70}")
print("AI QUALITY EVALUATION")
print(f"{'='*70}")

# Use the Lawttorney API itself as evaluator
eval_queries = []

# Evaluate TOA quality
if results[0].get("status") in ("PASS", "PARTIAL"):
    eval_queries.append({
        "name": "TOA Quality",
        "query": (
            "As a senior Indian lawyer, evaluate this Table of Authorities. "
            "Rate 1-10 on: (a) completeness of citation extraction, "
            "(b) correct categorization, (c) proper Indian citation format, "
            "(d) usefulness for court filing. Be strict. "
            "Reply with just the scores and a 2-line verdict.\n\n"
            "TABLE OF AUTHORITIES TO EVALUATE:\n" +
            (results[0].get("_content", "")[:3000] if "_content" in results[0] else "Content not available")
        ),
    })

# Evaluate Statute Refs quality
for r in results:
    if "Statute Refs: Rental" in r.get("name", "") and r.get("status") in ("PASS", "PARTIAL"):
        eval_queries.append({
            "name": "Statute Refs Quality (Rental)",
            "query": (
                "As a senior Indian property lawyer, evaluate these statute references "
                "added to a rental agreement. Rate 1-10 on: (a) correctness of statutes cited, "
                "(b) relevance to each clause, (c) completeness (any missing?), "
                "(d) proper section numbers. Be strict. "
                "Reply with just the scores and a 2-line verdict."
            ),
        })
        break

for eq in eval_queries:
    print(f"\nEvaluating: {eq['name']}...")
    try:
        resp = requests.post(
            f"{API}/search",
            json={"Promptquery": eq["query"]},
            headers=HEADERS,
            timeout=60,
        )
        if resp.ok:
            data = resp.json()
            eval_result = data.get("result", "No evaluation")
            print(f"  {eval_result[:500]}")
        else:
            print(f"  Eval failed: HTTP {resp.status_code}")
    except Exception as e:
        print(f"  Eval error: {e}")


# ================================================================
# FINAL SUMMARY
# ================================================================

print(f"\n{'='*70}")
print("FINAL SUMMARY")
print(f"{'='*70}")
print(f"{'#':<4} {'Test':<45} {'Status':<10} {'Time':<8} {'Details'}")
print(f"{'-'*70}")

total_checks_passed = 0
total_checks = 0
for i, r in enumerate(results, 1):
    status = r["status"]
    details = ""
    if "passed" in r:
        details = f"{r['passed']}/{r['passed']+r['failed']} checks"
        total_checks_passed += r["passed"]
        total_checks += r["passed"] + r["failed"]
    elif "error" in r:
        details = r["error"][:40]
    print(f"{i:<4} {r['name']:<45} {status:<10} {r['time']:.1f}s    {details}")

tests_passed = sum(1 for r in results if r["status"] == "PASS")
print(f"{'-'*70}")
print(f"Tests: {tests_passed}/{len(results)} fully passed")
print(f"Checks: {total_checks_passed}/{total_checks} passed")
