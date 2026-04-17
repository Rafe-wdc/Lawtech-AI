"""Investigate the full SSE behavior for a complex writ-petition drafting prompt.

Stress test for the drafting agent + orchestrator on a heavy civil/
constitutional litigation query. Mirrors tests/investigate_partnership_draft.py
but with a much longer, more-demanding prompt (HPCL/Article 226 writ).
"""

import asyncio
import json
import sys
import time

import httpx

BASE = "https://tool.lawttorney.com/pyapiv2"
API_KEY = "ff6c3e959de2bf4f73901db1ff797ea484d326ac6e2622067493352435f23a51"

PROMPT = (
    "Act as a civil and constitutional litigation lawyer and draft a legally sound "
    "writ petition along with supporting pleadings based on the following facts: "
    "Hindustan Petroleum Corporation Limited entered into a tender-based agreement "
    "with my client for deployment of 9 petrol tankers, subject to certain technical "
    "compliance conditions regarding the upper and bottom portions of the tankers; "
    "out of the said fleet, 6 tankers were utilized by HPCL for a period of "
    "approximately 4-5 months despite alleged technical issues, however, during the "
    "subsistence of the arrangement, HPCL entered into a fresh contract with another "
    "company and discontinued operations with my client without settling dues; my "
    "client has suffered substantial financial losses for a period of 6-7 months as "
    "no payment has been made by HPCL for the utilization of the tankers, and "
    "further, HPCL has arbitrarily blacklisted my client's company without due "
    "process; the tankers are presently in my client's possession, and despite "
    "repeated communications and follow-ups, HPCL has failed to release the "
    "outstanding amount of Rs. 8,00,000/- along with the bank guarantee amount; "
    "therefore, draft a writ petition under Article 226 of the Constitution of "
    "India before the jurisdictional High Court of Himachal Pradesh seeking "
    "(i) quashing of the arbitrary blacklisting order, (ii) direction for release "
    "of pending dues along with interest, (iii) refund of bank guarantee, and "
    "(iv) any other appropriate relief; include grounds such as violation of "
    "principles of natural justice, arbitrariness under Article 14, legitimate "
    "expectation, and unfair trade practice, along with prayers for interim relief, "
    "maintainability arguments, and supporting legal strategy."
)


async def main():
    events = []
    token_chars = 0
    start = None

    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST", f"{BASE}/search/stream",
            headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
            json={"Promptquery": PROMPT},
            timeout=360.0,  # 6 minutes — this is a heavy query
        ) as resp:
            print(f"HTTP {resp.status_code}  (prompt length: {len(PROMPT)} chars)")
            if resp.status_code != 200:
                print(await resp.aread())
                return 1
            start = time.perf_counter()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                t = (time.perf_counter() - start) * 1000
                try:
                    evt = json.loads(line[6:])
                except Exception:
                    continue
                events.append((t, evt))
                if evt.get("type") == "token":
                    token_chars += len(evt.get("content", ""))

    # ----- SUMMARY -----
    print()
    print("=" * 78)
    print(f"TOTAL EVENTS: {len(events)}")
    print(f"TOTAL WALL TIME: {events[-1][0] / 1000:.2f}s" if events else "N/A")
    print("=" * 78)

    # Event breakdown
    type_counts: dict[str, int] = {}
    for _, e in events:
        t = e.get("type", "?")
        type_counts[t] = type_counts.get(t, 0) + 1
    print()
    print("EVENT-TYPE BREAKDOWN")
    print("-" * 78)
    for k in sorted(type_counts, key=lambda x: -type_counts[x]):
        print(f"  {type_counts[k]:4d}  {k}")

    # Final response
    final_resp = next((e for _, e in events if e.get("type") == "response"), None)
    if final_resp:
        content = final_resp.get("content", "")
        print()
        print("FINAL RESPONSE")
        print("-" * 78)
        print(f"  total chars: {len(content):,}")
        print(f"  preview (first 500 chars):")
        print("    " + content[:500].replace("\n", "\n    "))
        print(f"  ...last 400 chars:")
        print("    " + content[-400:].replace("\n", "\n    "))

    # Drafting section progression
    dp = [(t, e) for t, e in events if e.get("type") == "drafting_progress"]
    if dp:
        print()
        print("DRAFTING SECTION PROGRESSION (in_progress + completed/failed pairs)")
        print("-" * 78)
        for t, e in dp:
            status = e.get("status", "—")
            extra = ""
            if e.get("char_count"):
                extra = f" char_count={e['char_count']}"
            elif e.get("error"):
                extra = f" error={e['error'][:40]}"
            print(f"  t={t:>7.0f}ms  section {e['section']}/{e['total']}  [{status}]  "
                  f"{e.get('title', '?')[:45]}{extra}")

    # Key milestones (non-token timeline)
    print()
    print("KEY MILESTONES (non-token events)")
    print("-" * 78)
    for t, e in events:
        etype = e.get("type")
        if etype in ("token", "progress"):
            continue
        detail = ""
        if etype == "status":
            detail = f"agent={e.get('agent')} | {e.get('message', '')[:55]}"
        elif etype == "agents_planned":
            detail = str(e.get("agents"))
        elif etype == "response":
            detail = f"content length={len(e.get('content', ''))} chars"
        elif etype == "sources":
            detail = f"{len(e.get('data', []))} sources"
        elif etype == "followup_suggestions":
            detail = f"{len(e.get('data', []))} suggestions"
        elif etype == "done":
            detail = (f"agents_used={e.get('agents_used')} "
                      f"tokens={e.get('total_tokens')} "
                      f"cached={e.get('cached')} "
                      f"draft_cont={e.get('has_draft_continuation')}")
        elif etype == "context":
            detail = f"rewritten={e.get('query_rewritten')} turns={e.get('history_turns')}"
        elif etype == "thread_id":
            detail = f"id={e.get('data', '')[:16]}..."
        elif etype == "token_reset":
            detail = "<<< reset token buffer >>>"
        elif etype == "error":
            detail = f"ERROR: {e.get('data', '')[:80]}"
        elif etype.startswith("integration_"):
            detail = f"provider={e.get('provider')} | {e.get('message', '')[:55]}"
        else:
            detail = str(e)[:80]
        print(f"  t={t:>7.0f}ms  {etype:<22}  {detail}")

    # Sources
    sources = next((e for _, e in events if e.get("type") == "sources"), None)
    if sources:
        print()
        print(f"SOURCES ({len(sources.get('data', []))} total, first 10)")
        print("-" * 78)
        for i, s in enumerate(sources.get("data", [])[:10]):
            print(f"  [{i}] {s.get('source_type'):<12} {s.get('title', '')[:55]}")

    # Followups
    fs = next((e for _, e in events if e.get("type") == "followup_suggestions"), None)
    if fs:
        print()
        print("FOLLOW-UP SUGGESTIONS")
        print("-" * 78)
        for s in fs.get("data", []):
            print(f"  - {s}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
