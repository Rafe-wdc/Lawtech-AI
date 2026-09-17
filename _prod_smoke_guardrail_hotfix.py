"""One-off prod smoke — verifies both fixes are live on api.lawttorney.com.

Fires a Drafting request whose text contains phrases that:
  1) The old 14-pattern injection regex would have BLOCKED at guardrail_input
     ("act as complainant", "act as informant") — proves the regex cleanup.
  2) Routes to has_drafting=True in orchestrator_plan_node — proves the
     _tax_appellate_detected UnboundLocalError fix.

Passes if the response is neither the old guardrail message nor a 500.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request


PROD_URL = "https://api.lawttorney.com/pyapi/search"
# From your local .env — same key configured on prod (verify if this fails).
API_KEY = os.environ["PROD_API_KEY"]

TEST_PROMPT = (
    "Draft a legal notice under Section 138 of the Negotiable Instruments Act, "
    "1881 for cheque bounce. Facts: The Complainant received a cheque of "
    "Rs.2,50,000 dated 15th March 2026 from the Accused towards repayment of a "
    "loan. The cheque was presented at HDFC Bank on 30th March 2026 and "
    "returned with the remark 'Account Closed' on 2nd April 2026. Same cheque "
    "was re-presented on 15th April 2026 and returned again with the same "
    "remark. The Complainant shall act as informant under Section 154 CrPC. "
    "The Petitioner may also act as complainant in any consequential summary "
    "trial proceedings. Draft the statutory demand notice with the standard "
    "15-day compliance period and include the standard prayer clauses."
)

# The previous regex would have blocked on 'act as informant' + 'act as complainant'.
OLD_GUARDRAIL_MSG = "your query contains patterns that are not allowed"
# The previous orchestrator crash surfaced this text verbatim.
OLD_ORCH_BUG = "_tax_appellate_detected"


def main() -> int:
    body = json.dumps({
        "Promptquery": TEST_PROMPT,
        "globalThreadId": f"smoke-{int(time.time())}",
    }).encode()

    req = urllib.request.Request(
        PROD_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": API_KEY,
        },
        method="POST",
    )

    print(f"POST {PROD_URL}")
    print(f"prompt (first 200 chars): {TEST_PROMPT[:200]}...")
    print()
    t0 = time.time()

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            elapsed = time.time() - t0
            status = resp.status
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        elapsed = time.time() - t0
        body_text = e.read().decode(errors="replace")[:1000]
        print(f"HTTP {e.code} after {elapsed:.1f}s")
        print(f"body: {body_text}")
        return 2
    except Exception as e:
        print(f"transport error after {time.time() - t0:.1f}s: {e}")
        return 2

    result = payload.get("result", "")
    agents_used = payload.get("agents_used", [])
    tokens = payload.get("token_usage", {})

    print(f"HTTP {status}  ({elapsed:.1f}s)")
    print(f"agents_used: {agents_used}")
    print(f"total_tokens: {tokens.get('total') if isinstance(tokens, dict) else tokens}")
    print(f"response length: {len(result)} chars")
    print()

    print("=" * 60)
    print("RESPONSE (first 1500 chars):")
    print("=" * 60)
    print(result[:1500])
    print()

    verdict = []
    if OLD_GUARDRAIL_MSG in result.lower():
        verdict.append("FAIL: guardrail rejection message still present")
    if OLD_ORCH_BUG in result:
        verdict.append("FAIL: orchestrator UnboundLocalError still surfacing")
    if not result.strip():
        verdict.append("FAIL: empty response")

    print("=" * 60)
    if verdict:
        for v in verdict:
            print(v)
        return 1

    print("VERDICT: both fixes verified live on prod")
    print("  - No 'contains patterns' rejection (guardrail passthrough works)")
    print("  - No '_tax_appellate_detected' error (orchestrator hotfix live)")
    print("  - Drafting agent produced a response")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
