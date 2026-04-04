"""Fix Draft — Revise a legal draft based on compliance report findings.

Takes the original draft + compliance report and produces a revised version
that fixes all Critical Issues, addresses Warnings, and incorporates Suggestions.

Marks changes with [REVISED] tags so the user can see what was changed.
"""

from __future__ import annotations

import asyncio
from datetime import date

from core.clients import get_gemini_flash_full
from core.logger import get_logger, log_time
from core.sanitize import sanitize_output

log = get_logger("FixDraft")


FIX_DRAFT_PROMPT = """You are a senior Indian legal drafting expert. You have been given an original legal draft and a compliance report that identified issues with it. Your task is to produce a REVISED draft that fixes all identified problems.

## Instructions

1. **Fix ALL Critical Issues** — these are mandatory fixes. Every critical issue in the report MUST be addressed.
2. **Address ALL Warnings** — fix these unless doing so would change the legal meaning or is impossible without case-specific information.
3. **Incorporate Suggestions** where practical — these are optional improvements.
4. **Preserve the structure** — keep the same section numbering, headings, and overall organization.
5. **Mark every change** — where you make a significant change, add a brief inline comment: `[REVISED: reason]` so the lawyer can review what changed.
6. **Do NOT fill in placeholders** with made-up information. Keep `[___]` or `[placeholder]` markers but ensure the surrounding legal text is correct.
7. **Fix incorrect section mappings** — if the report says Section X was wrongly mapped to Section Y, use the correct mapping.
8. **Remove premature references** — if the report says new laws (BNS/BNSS/BSA) are not yet in force, remove those references and use only current law (IPC/CrPC/IEA).
9. **Add missing elements** — if the report identifies missing sections (e.g., List of Annexures, advocate details), add them as properly formatted placeholders.
10. **Upgrade citations** — if the report suggests stronger citations (e.g., SC instead of HC), add them where known.

## Original Draft:

{original_draft}

## Compliance Report:

{compliance_report}

## Today's Date: {date}

## Output

Return the COMPLETE revised draft. Include everything from the original — don't skip sections. The output should be the full document, ready for a lawyer to review the [REVISED] markers and finalize.

At the END of the document, add a brief:

---

## REVISION SUMMARY

| # | Change | Location | Reason |
|---|--------|----------|--------|
[List each change made with location and reason]

**Total Changes:** [count]
**Critical Issues Fixed:** [count]/[total from report]
**Warnings Addressed:** [count]/[total from report]
"""


async def fix_draft(
    original_draft: str,
    compliance_report: str,
) -> str:
    """Revise a legal draft based on compliance report findings.

    Args:
        original_draft: The original legal draft (markdown)
        compliance_report: The compliance check report (markdown)

    Returns:
        Revised draft as markdown with [REVISED] markers and revision summary
    """
    if not original_draft.strip():
        return "Error: No original draft provided."
    if not compliance_report.strip():
        return original_draft  # Nothing to fix

    try:
        with log_time(log, "Draft revision"):
            llm = get_gemini_flash_full(temperature=0.1)
            response = await asyncio.to_thread(
                llm.invoke,
                FIX_DRAFT_PROMPT.format(
                    original_draft=original_draft,
                    compliance_report=compliance_report,
                    date=str(date.today()),
                ),
            )

        revised = sanitize_output(response.content.strip())

        # Sanity: revised should be at least 50% of original (we're fixing, not deleting)
        if len(revised) < len(original_draft) * 0.5:
            log.warning("Revised draft suspiciously short, returning original",
                        original_len=len(original_draft), revised_len=len(revised))
            return original_draft

        log.info("Draft revised",
                 original_len=len(original_draft),
                 revised_len=len(revised),
                 report_len=len(compliance_report),
                 changes_marked=revised.count("[REVISED"))

        return revised

    except Exception as e:
        log.error("Draft revision failed", error=str(e), exc_info=True)
        return (
            f"## Draft Revision Failed\n\n"
            f"Error: {e}\n\n"
            f"Please review the compliance report and fix the draft manually.\n\n"
            f"---\n\n{original_draft}"
        )
