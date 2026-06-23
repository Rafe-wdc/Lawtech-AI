"""Unit tests for Phase 1 drafting mechanical fixes (2026-06-23).

Covers six surgical fixes that don't require an LLM to verify:

  1. MAX_FINAL_RESPONSE_CHARS raised from 60K to 250K (matches orchestrator).
  2. _MAX_SECTIONS raised from 16 to 28.
  3. Duplicate heading fix in `_assemble_document` (strip echoed `## TITLE`
     lines and always inject canonical numbering).
  4. Empty numbered paragraph detection in `validate_draft`.
  5. Global paragraph renumber pass walks substantive sections and
     rewrites numbering continuously, skipping procedural sections.
  6. Section prompt no longer instructs the LLM to emit `## {section_title}`.

These run without network / LLM calls — pure regex/string assertions.
"""
from __future__ import annotations

import os
import re
import sys

# Project root on sys.path
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


# --- Fix 1: guardrail char cap raised --------------------------------------


def test_max_final_response_chars_aligned_with_orchestrator():
    """The 60K cap was the binding limit on Drafting output before this fix.
    250K matches the orchestrator's own synthesis ceiling and is the
    smallest change that unbreaks long drafts. The orchestrator's
    `_MAX_SYNTHESIS_LEN` is a function-local literal, not a module-level
    constant, so we verify the guardrail side directly.
    """
    from agents import guardrail
    assert guardrail.MAX_FINAL_RESPONSE_CHARS == 250_000, \
        f"Expected 250K guardrail cap, got {guardrail.MAX_FINAL_RESPONSE_CHARS}"


# --- Fix 2: _MAX_SECTIONS raised -------------------------------------------


def test_max_sections_supports_long_writ():
    """The Avachat writ needed 15 named Parts + GROUNDS + PRAYER + 4
    procedural blocks = 21 sections, well over the old 16 ceiling.
    """
    from agents import drafting
    assert drafting._MAX_SECTIONS >= 21, \
        f"Expected ≥21 to handle a 15-part writ + procedurals, got {drafting._MAX_SECTIONS}"


# --- Fix 3: duplicate heading strip ----------------------------------------


def test_assembler_strips_echoed_section_heading():
    """When section LLM emits its own `## TITLE` line (or `N.` bare line),
    the assembler should strip it before injecting the canonical heading.
    """
    import asyncio
    from agents.drafting import _assemble_document, DraftOutline, SectionPlan

    outline = DraftOutline(
        document_title="WRIT PETITION UNDER ARTICLE 226",
        court_details="**IN THE HON'BLE HIGH COURT**\n\n.....Petitioner\n\nVersus\n\n.....Respondent",
        sections=[
            SectionPlan(title="STATEMENT OF FACTS",
                        description="...", estimated_paragraphs=3, needs_citations=False),
            SectionPlan(title="GROUNDS OF APPEAL",
                        description="...", estimated_paragraphs=3, needs_citations=False),
        ],
    )

    # Section 1: LLM echoed `## STATEMENT OF FACTS` then a stray `4.` line.
    # Section 2: LLM emitted clean body. Assembler should normalise both.
    sections = [
        "## STATEMENT OF FACTS\n\n1. The Petitioner is a citizen of India.\n2. The matter arises from a contract dated 2020.\n",
        "1. The judgment is bad in law.\n2. The trial court ignored evidence.\n",
    ]

    assembled = asyncio.run(_assemble_document(outline, sections, user_language="en"))

    # Exactly one occurrence of each canonical heading.
    s1 = re.findall(r"^##\s+1\.\s+STATEMENT OF FACTS\s*$", assembled, re.MULTILINE)
    s2 = re.findall(r"^##\s+2\.\s+GROUNDS OF APPEAL\s*$", assembled, re.MULTILINE)
    assert len(s1) == 1, f"Expected exactly 1 Section 1 heading, got {len(s1)}: {assembled}"
    assert len(s2) == 1, f"Expected exactly 1 Section 2 heading, got {len(s2)}"
    # Echoed bare `## STATEMENT OF FACTS` (without number) must NOT appear.
    assert not re.search(r"^##\s+STATEMENT OF FACTS\s*$", assembled, re.MULTILINE), \
        f"Echoed un-numbered heading leaked through:\n{assembled}"


# --- Fix 4: empty numbered paragraph detector ------------------------------


def test_validate_draft_strips_empty_numbered_paragraphs():
    """Empty `69.` lines (paragraph number with no body) should be stripped
    and reported as a warning.
    """
    from agents.drafting import validate_draft
    bad_draft = (
        "## 1. STATEMENT OF FACTS\n\n"
        "68. Para sixty-eight content.\n"
        "69.\n"
        "70. Para seventy content.\n"
    )
    cleaned, warnings = validate_draft(bad_draft, stance=None)
    # The empty `69.` line must be gone.
    assert "69." not in [ln.strip() for ln in cleaned.split("\n")], \
        f"Empty `69.` line should be stripped:\n{cleaned}"
    # And we got a warning.
    assert any("empty numbered paragraph" in w.lower() for w in warnings), \
        f"Expected warning about empty paragraph, got: {warnings}"


# --- Fix 5: global paragraph renumber pass ---------------------------------


def test_renumber_pass_fixes_section_jumps():
    """The renumber pass should walk the assembled draft and rewrite the
    global counter continuously across substantive sections.
    """
    from agents.drafting import _renumber_global_paragraphs

    # Simulates the Avachat symptom: Sec 3 restarts at 6 instead of 16,
    # Sec 5 jumps from 15 to 23. After renumbering both must be continuous.
    draft = (
        "## 1. STATEMENT OF FACTS\n\n"
        "1. First para.\n"
        "2. Second para.\n"
        "3. Third para.\n"
        "## 2. GROUNDS OF APPEAL\n\n"
        "6. Bad start (LLM echoed user's literal '6 to 13').\n"
        "7. Continuing wrong sequence.\n"
        "## 3. PRAYER\n\n"
        "(a) Issue a writ of mandamus.\n"
        "(b) Costs.\n"
    )
    out = _renumber_global_paragraphs(draft)
    # Substantive sections should have continuous 1, 2, 3, 4, 5 numbering.
    lines = out.split("\n")
    substantive_nums = []
    for ln in lines:
        m = re.match(r"^(\d+)\.\s+\S", ln)
        if m:
            substantive_nums.append(int(m.group(1)))
    assert substantive_nums == [1, 2, 3, 4, 5], \
        f"Expected continuous 1..5 substantive numbering, got {substantive_nums}\n{out}"
    # Prayer section's `(a)/(b)` should be untouched.
    assert "(a) Issue a writ of mandamus." in out
    assert "(b) Costs." in out


def test_renumber_pass_skips_procedural_sections():
    """Procedural section keywords (Verification, Court Fee, Schedule, etc.)
    should be skipped so their local numbering scheme survives.
    """
    from agents.drafting import _renumber_global_paragraphs

    draft = (
        "## 1. STATEMENT OF FACTS\n\n"
        "1. First.\n"
        "2. Second.\n"
        "## 2. SCHEDULE OF PROPERTIES\n\n"
        "1. Plot A — 1 acre at village X.\n"
        "2. Plot B — 0.5 acre at village Y.\n"
        "## 3. GROUNDS OF APPEAL\n\n"
        "5. Bad start.\n"
        "6. Bad continuation.\n"
    )
    out = _renumber_global_paragraphs(draft)
    # Substantive sections get renumbered continuously: STATEMENT OF FACTS
    # gives us 1, 2; GROUNDS picks up 3, 4. SCHEDULE stays at 1, 2.
    sec_facts = out.split("## 2. SCHEDULE")[0]
    sec_grounds = out.split("## 3. GROUNDS")[1]
    sec_schedule_block = out.split("## 2. SCHEDULE")[1].split("## 3. GROUNDS")[0]

    assert "1. First." in sec_facts and "2. Second." in sec_facts
    assert "3. Bad start." in sec_grounds and "4. Bad continuation." in sec_grounds
    # Schedule kept its local 1, 2.
    assert "1. Plot A" in sec_schedule_block
    assert "2. Plot B" in sec_schedule_block


def test_renumber_pass_is_idempotent_when_already_correct():
    """A well-numbered draft must pass through unchanged."""
    from agents.drafting import _renumber_global_paragraphs

    draft = (
        "## 1. STATEMENT OF FACTS\n\n"
        "1. First.\n"
        "2. Second.\n"
        "## 2. GROUNDS\n\n"
        "3. Third.\n"
        "4. Fourth.\n"
    )
    out = _renumber_global_paragraphs(draft)
    assert out == draft, f"Renumber changed an already-correct draft:\n{out}"


# --- Fix 6: section prompt no longer echoes section heading ----------------


def test_section_prompt_does_not_echo_heading():
    """The user-message template inside `_generate_section` previously
    included `## {section_title}` which the LLM would echo verbatim,
    producing duplicated headings after the assembler added its own. The
    new prompt explicitly forbids emitting the heading.
    """
    from pathlib import Path
    src = Path(_REPO, "agents", "drafting.py").read_text(encoding="utf-8")
    # The OLD literal should be gone.
    assert "## {section_title}\\n{section_desc}" not in src, \
        "Old echoed-heading template still present in section prompt"
    # The NEW guard text should be present.
    assert "DO NOT prefix your output with `## {section_title}`" in src, \
        "Expected explicit DO-NOT-emit-heading instruction in section prompt"
