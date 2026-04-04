"""Automatic Statute Referencing — Inject applicable Indian law references into legal text.

Scans legal drafts/responses and inserts relevant statute citations
(act name + section number) into each clause. Preserves original text
and only ADDS references where missing.

Uses Gemini Flash for intelligent statute identification.
Also available as standalone endpoint for user-triggered enhancement.
"""

from __future__ import annotations

import asyncio
from datetime import date

from core.clients import get_gemini_flash_full
from core.logger import get_logger, log_time

log = get_logger("StatuteRefs")


STATUTE_REF_PROMPT = """You are an expert Indian legal drafting assistant. Your task is to enhance the provided legal text by inserting applicable Indian statute references (Act name + Section number) into each clause or statement where they are missing.

## Rules

1. **PRESERVE** the original text exactly — do not rewrite, rephrase, or remove anything
2. **ONLY ADD** statute references where they are currently missing
3. If a clause already has a statute reference, leave it unchanged
4. Insert references naturally using phrases like:
   - "under Section X of the [Act Name], [Year]"
   - "as per Section X of the [Act Name], [Year]"
   - "in accordance with Section X of the [Act Name], [Year]"
   - "read with Section X of the [Act Name], [Year]"
5. Use the correct and complete official act names with year
6. For new criminal codes, reference BOTH old and new:
   - "Section 302 of the Indian Penal Code, 1860 (now Section 103 of the Bharatiya Nyaya Sanhita, 2023)"
7. If multiple statutes apply to a clause, include all relevant ones
8. Do NOT fabricate or guess sections — only add references you are confident about
9. If unsure about a specific section number, use the general act reference:
   - "under the provisions of the [Act Name], [Year]"

## Common Indian Statutes to Reference

**Civil Law:**
- Indian Contract Act, 1872 (Sections 1-75: General, 124-238: Special contracts)
- Transfer of Property Act, 1882 (Sections 5-53A: Transfer, 54-69: Sale, 105-117: Lease)
- Specific Relief Act, 1963 (Sections 10-25: Specific performance, 34-39: Injunctions)
- Registration Act, 1908
- Indian Stamp Act, 1899
- Limitation Act, 1963

**Criminal Law:**
- Indian Penal Code, 1860 / Bharatiya Nyaya Sanhita, 2023
- Code of Criminal Procedure, 1973 / Bharatiya Nagarik Suraksha Sanhita, 2023
- Indian Evidence Act, 1872 / Bharatiya Sakshya Adhiniyam, 2023

**Family Law:**
- Hindu Marriage Act, 1955
- Hindu Succession Act, 1956
- Muslim Personal Law (Shariat) Application Act, 1937
- Guardians and Wards Act, 1890
- Protection of Women from Domestic Violence Act, 2005
- Dowry Prohibition Act, 1961

**Commercial Law:**
- Negotiable Instruments Act, 1881
- Companies Act, 2013
- Insolvency and Bankruptcy Code, 2016
- Arbitration and Conciliation Act, 1996
- Consumer Protection Act, 2019
- RERA (Real Estate Regulation and Development Act), 2016
- Partnership Act, 1932
- Sale of Goods Act, 1930

**Labour Law:**
- Industrial Disputes Act, 1947
- Payment of Wages Act, 1936
- Employees Provident Fund Act, 1952

**Property Law:**
- Indian Easements Act, 1882
- Land Acquisition Act, 2013

**Constitutional:**
- Constitution of India (Articles 14-32: Fundamental Rights, 226: HC Writs)

**IT & Cyber:**
- Information Technology Act, 2000

## Text to Enhance

{text}

## Output

Return the COMPLETE text with statute references inserted. Do not add any commentary, explanation, or notes outside the document text itself. The output should be the enhanced document ready for use."""


async def add_statute_references(text: str) -> str:
    """Enhance legal text by inserting applicable Indian statute references.

    Args:
        text: Legal draft or response text (markdown)

    Returns:
        Enhanced text with statute references inserted
    """
    if not text or len(text.strip()) < 50:
        return text

    try:
        with log_time(log, "Statute reference injection"):
            llm = get_gemini_flash_full(temperature=0.1)
            response = await asyncio.to_thread(
                llm.invoke,
                STATUTE_REF_PROMPT.format(text=text),
            )

        enhanced = response.content.strip()

        # Sanity check: enhanced text should be at least as long as original
        # (we're only adding, not removing)
        if len(enhanced) < len(text) * 0.8:
            log.warning("Statute ref result suspiciously short, using original",
                        original_len=len(text), enhanced_len=len(enhanced))
            return text

        log.info("Statute references added",
                 original_len=len(text),
                 enhanced_len=len(enhanced),
                 added_chars=len(enhanced) - len(text))

        return enhanced

    except Exception as e:
        log.error("Statute reference injection failed, returning original",
                  error=str(e), exc_info=True)
        return text
