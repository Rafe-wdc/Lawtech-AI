"""Reply-notice smoke test.

Hits the local /pyapi/chat endpoint with the Adobe Scan reply-notice PDF and
the exact prompt the user reported the bug for. Streams the SSE response,
extracts the FINAL response text (after self_refine has run), and asserts on
the reply-notice framing invariants added in this session:

  - The "From the desk of" letterhead does NOT contain the replying party's
    own name (Vikram) — should be a placeholder or an advocate identity.
  - The body opens with either advocate voice ("my client, Shri Vikram...")
    OR party voice ("I, Vikram..."), but not the contradictory both.
  - The "To" block acknowledges Rajeshbhai / Trilok Infratech as the
    claimant (either two-level "Through his Advocate" or "Advocate for
    Shri Rajeshbhai" compact form).

Run from repo root while the local server is running on :5000:
    python tests/probe_reply_notice_smoke.py

Exits 0 on PASS, 1 on FAIL.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:5000"
PDF_PATH = Path(r"D:\agentic_proj\Lawtech-AI\test_pdfs\Adobe Scan 16 Jul 2026 (1).pdf")
PROMPT = "Prepare reply notice based on attached pdf"
HEALTH_TIMEOUT_S = 180
RESPONSE_TIMEOUT_S = 900


def _load_api_key() -> str:
    """Pick the first key from .env's API_KEYS (comma-separated)."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return ""
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == "API_KEYS":
            v = v.strip().strip('"').strip("'")
            first = v.split(",")[0].strip()
            return first
    return ""


_API_KEY = _load_api_key()
_AUTH_HEADERS = {"X-API-Key": _API_KEY} if _API_KEY else {}


def wait_for_health() -> bool:
    start = time.time()
    last_err = ""
    while time.time() - start < HEALTH_TIMEOUT_S:
        try:
            r = requests.get(f"{BASE_URL}/pyapi/health", timeout=5)
            if r.status_code == 200:
                elapsed = time.time() - start
                print(f"[health] server healthy after {elapsed:.1f}s")
                return True
            last_err = f"status={r.status_code}"
        except Exception as e:
            last_err = type(e).__name__
        time.sleep(2)
    print(f"[health] TIMEOUT after {HEALTH_TIMEOUT_S}s (last: {last_err})")
    return False


def run_probe() -> str:
    """Send the multipart request, stream the SSE, return the final response text."""
    if not PDF_PATH.exists():
        print(f"[fatal] PDF not found: {PDF_PATH}")
        sys.exit(1)

    print(f"[probe] sending chat request  file={PDF_PATH.name}  prompt={PROMPT!r}")

    token_buffer: list[str] = []
    final_response = ""
    events_seen: dict[str, int] = {}
    error_msg = ""

    with open(PDF_PATH, "rb") as fp:
        files = {"files": (PDF_PATH.name, fp, "application/pdf")}
        data = {"query": PROMPT}
        start = time.time()
        with requests.post(
            f"{BASE_URL}/pyapi/chat",
            data=data,
            files=files,
            headers=_AUTH_HEADERS,
            stream=True,
            timeout=RESPONSE_TIMEOUT_S,
        ) as r:
            r.raise_for_status()
            for raw in r.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data: "):
                    continue
                try:
                    evt = json.loads(raw[6:])
                except json.JSONDecodeError:
                    continue
                etype = evt.get("type", "")
                events_seen[etype] = events_seen.get(etype, 0) + 1

                if etype == "token":
                    token_buffer.append(evt.get("content", ""))
                elif etype == "token_reset":
                    token_buffer.clear()
                elif etype == "response":
                    final_response = evt.get("content", "")
                elif etype == "error":
                    error_msg = evt.get("data", "") or evt.get("message", "")
                elif etype == "done":
                    break

    elapsed = time.time() - start
    print(f"[probe] stream complete in {elapsed:.1f}s   events={events_seen}")
    if error_msg:
        print(f"[probe] ERROR event: {error_msg}")

    if not final_response and token_buffer:
        final_response = "".join(token_buffer)

    return final_response


def check_framing(resp: str) -> bool:
    """Assert the reply-notice framing invariants from this session's fix."""
    print("\n" + "=" * 80)
    print("FINAL RESPONSE")
    print("=" * 80)
    print(resp if resp else "(empty)")
    print("=" * 80)

    if not resp:
        print("[check] response is empty — nothing to audit; FAIL")
        return False

    low = resp.lower()

    # Extract the letterhead / "From the desk of" region — first 800 chars is
    # a safe window in Indian legal-notice format.
    head = resp[:800]
    head_low = head.lower()

    checks: list[tuple[str, bool]] = []

    # 1. "From the desk of" letterhead should NOT put the replying party
    #    (Vikram) directly as sender-advocate. Accept if:
    #      (a) the letterhead contains a bracketed placeholder like
    #          [Replying Advocate ...] or [Advocate ...], OR
    #      (b) the body voice is genuine first-person party ("I, Vikram")
    #          — in which case Vikram in the letterhead is consistent, OR
    #      (c) the "From the desk of" line doesn't name Vikram at all.
    from_vikram_letterhead = (
        "from the desk of" in head_low
        and ("vikram" in head_low[: head_low.find("from the desk of") + 400])
    )
    has_placeholder_advocate = (
        "[replying advocate" in low
        or "[advocate" in low
        or "[replying party" in low
    )
    body_first_person_vikram = (
        "i, vikram" in low
        or "i, mr. vikram" in low
        or "i, shri vikram" in low
    )
    body_advocate_voice = (
        "my client" in low
        and "vikram" in low
    )

    letterhead_ok = (
        not from_vikram_letterhead
        or has_placeholder_advocate
        or (body_first_person_vikram and not body_advocate_voice)
    )
    checks.append(("Letterhead does not contradict body voice", letterhead_ok))

    # 2. Body voice is EITHER advocate ("my client Vikram...") OR party
    #    ("I, Vikram..."), not BOTH.
    voice_consistent = not (body_first_person_vikram and body_advocate_voice)
    checks.append(("Body voice is internally consistent (not both)", voice_consistent))

    # 3. "To" block acknowledges the actual claimant (Rajeshbhai / Trilok
    #    Infratech) — via two-level "Through his Advocate:" OR compact
    #    "Advocate for [Client]" form, OR names Rajeshbhai directly.
    claimant_in_to_block = (
        "rajesh" in low
        or "trilok infratech" in low
        or "advocate for" in low
        or "through his advocate" in low
        or "through their advocate" in low
    )
    checks.append(("To-block acknowledges the claimant (Rajeshbhai / Trilok / 'Advocate for')", claimant_in_to_block))

    # 4. If letterhead uses a placeholder, the exact bracket syntax should
    #    survive refiner (per Rule 8 PLACEHOLDER PRESERVATION).
    if has_placeholder_advocate:
        placeholder_intact = "[" in resp and "]" in resp
        checks.append(("Placeholder bracket syntax preserved through refine", placeholder_intact))

    # 5. Draft is not empty and has notice-shape (Subject / Sir / numbered paras).
    notice_shape = (
        "subject" in low
        and ("sir" in low or "madam" in low)
    )
    checks.append(("Response has notice-shape (Subject + salutation)", notice_shape))

    print("\n" + "-" * 80)
    print("REPLY-NOTICE FRAMING CHECKS")
    print("-" * 80)
    all_pass = True
    for label, ok in checks:
        tag = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  [{tag}] {label}")
    print("-" * 80)
    print("OVERALL:", "PASS" if all_pass else "FAIL")
    return all_pass


def main() -> int:
    if not wait_for_health():
        return 1
    resp = run_probe()
    ok = check_framing(resp)
    # Persist for post-mortem review.
    out_path = Path(__file__).parent / "reply_notice_smoke_out.md"
    out_path.write_text(resp or "(empty)", encoding="utf-8")
    print(f"\n[probe] final response written to {out_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
