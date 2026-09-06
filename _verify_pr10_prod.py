"""Functional verification that PR #10 is running on prod.

Runs the exact Q-18 incident prompt against api.lawttorney.com and
scores the response using `validate_draft_grounding` (the same regex
the fix ships with). If PR #10 is live, the draft should be full of
bracketed placeholders and zero invented particulars. If PR #10 is
NOT live, the audit says 2 of 3 runs invent case facts.
"""
import json
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agents.drafting import validate_draft_grounding  # noqa: E402

URL = "https://api.lawttorney.com/pyapi/search/stream"
API_KEY = "ff6c3e959de2bf4f73901db1ff797ea484d326ac6e2622067493352435f23a51"
PROMPT = "Draft a bail application for cheating under Section 420 IPC"

payload = {"Promptquery": PROMPT}
headers = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json",
    "Accept": "text/event-stream",
}

# Bare (unbracketed) assertions PR #10's bracketing rule targets.
# Match only if NOT preceded by "[IF APPLICABLE" within ~60 chars.
_ASSERTION_PATTERNS = [
    ("no_antecedents", r"has no criminal antecedents?"),
    ("sole_breadwinner", r"sole (?:bread ?winner|earning member)"),
    ("permanent_resident", r"permanent resident"),
    ("investigation_complete", r"investigation is (?:substantially )?complete"),
    ("judicial_custody", r"(?:is )?in judicial custody"),
]


def _bare_assertions(text: str) -> list[tuple[str, str]]:
    """Find assertions that appear OUTSIDE `[IF APPLICABLE: ...]` brackets."""
    hits = []
    for name, pat in _ASSERTION_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            # Look back ~80 chars for an unclosed `[IF APPLICABLE:`
            snippet = text[max(0, m.start() - 80):m.start()]
            if "[IF APPLICABLE" in snippet and snippet.rfind("]") < snippet.rfind("[IF APPLICABLE"):
                continue
            hits.append((name, m.group(0)))
    return hits


def _statutory_currency_present(text: str) -> bool:
    """4A rule: IPC §420 should be paired with BNS §318 (or similar) counterpart."""
    has_420 = re.search(r"\b(?:Section\s*)?420\b", text)
    has_bns_318 = re.search(r"\b318\b", text) and re.search(
        r"Bharatiya Nyaya Sanhita", text, re.IGNORECASE
    )
    return bool(has_420 and has_bns_318)


def _prior_bail_disclosure_present(text: str) -> bool:
    """4B rule: bail draft should include a prior-bail-application disclosure."""
    patterns = [
        r"previous bail application",
        r"prior bail",
        r"no previous application (?:for bail )?has been (?:made|filed)",
        r"earlier bail application",
        r"any (?:earlier|previous) bail",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def call_prod() -> tuple[str, float]:
    """POST and consume the SSE stream. Return (final_response, elapsed_s)."""
    t0 = time.time()
    token_buf, final = [], None
    with requests.post(URL, json=payload, headers=headers, stream=True, timeout=300) as r:
        r.raise_for_status()
        for raw in r.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            try:
                ev = json.loads(raw[5:].strip())
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "token":
                chunk = ev.get("content") or ev.get("data") or ""
                if chunk:
                    token_buf.append(chunk)
            elif t == "response":
                final = ev.get("content") or ev.get("data")
            elif t == "done":
                break
    return final or "".join(token_buf), time.time() - t0


def main() -> int:
    print(f"POST {URL}")
    print(f'Prompt: "{PROMPT}"')
    print("Running…")
    text, elapsed = call_prod()

    if not text:
        print("[FAIL] empty response")
        return 1

    Path("_verify_pr10_prod_response.md").write_text(text, encoding="utf-8")

    # Validator (same regex PR #10 uses to decide regeneration)
    report = validate_draft_grounding(text, PROMPT)
    invented = report["unsupported"]
    placeholder_count = report["placeholder_count"]

    bare = _bare_assertions(text)
    stat_currency = _statutory_currency_present(text)
    prior_bail = _prior_bail_disclosure_present(text)

    print()
    print("=" * 70)
    print(f"Response: {len(text)} chars, {elapsed:.1f}s")
    print("=" * 70)
    print()
    print("PR #10 markers:")
    print(f"  [Q-18/Q-19] placeholder count            : {placeholder_count}")
    print(f"  [Q-18]     invented particulars          : {len(invented)}  (expect 0)")
    if invented:
        for kind, val in invented[:8]:
            print(f"               - {kind}: {val!r}")
    print(f"  [542cdc6]  bare (unbracketed) assertions : {len(bare)}  (expect 0)")
    for kind, val in bare[:8]:
        print(f"               - {kind}: {val!r}")
    print(f"  [4A/4B]    statutory currency (IPC↔BNS)  : {stat_currency}")
    print(f"  [4A/4B]    prior-bail disclosure         : {prior_bail}")
    print()

    verdict_lines = []
    # Primary Q-18 test: zero invented particulars + many placeholders
    if len(invented) == 0 and placeholder_count >= 5:
        verdict_lines.append(f"  [OK] Q-18/Q-19 placeholder mode active on prod ({placeholder_count} placeholders, 0 invented)")
    else:
        verdict_lines.append(f"  [FAIL] Q-18 fix NOT active: {len(invented)} invented, {placeholder_count} placeholders")
    # Bracket-the-claim
    if len(bare) == 0:
        verdict_lines.append("  [OK] 542cdc6 bracketing rule active — no bare assertions")
    else:
        verdict_lines.append(f"  [WARN] 542cdc6 bracketing rule partial — {len(bare)} bare assertions found")
    # 4A/4B parity
    if stat_currency:
        verdict_lines.append("  [OK] 4A statutory currency present (BNS §318 counterpart to IPC §420)")
    else:
        verdict_lines.append("  [WARN] 4A statutory currency NOT surfaced")
    if prior_bail:
        verdict_lines.append("  [OK] 4B prior-bail disclosure present")
    else:
        verdict_lines.append("  [WARN] 4B prior-bail disclosure NOT surfaced")

    print("VERDICT:")
    for line in verdict_lines:
        print(line)

    any_fail = "[FAIL]" in " ".join(verdict_lines)
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
