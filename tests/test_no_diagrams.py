"""A legal response never carries a diagram or a code fence.

Advocate report 2026-09-10: an ASCII flowchart of remedies appeared in the
middle of a property-law opinion, inside a code fence, rendering as
misaligned monospace columns. core.diagrams removes drawings and unfences
everything else; the output guardrail applies it to every response.
"""
from __future__ import annotations

import asyncio

from core.diagrams import strip_diagrams

# The exact block from the reported response.
FLOWCHART = (
    "```\n"
    "                          COMPREHENSIVE CIVIL SUIT\n"
    "                                      |\n"
    "       +------------------------------+-------------------------------+\n"
    "---\n"
    "Declaratory & Cancellation Partition & Possession Preventive & Injunctive\n"
    "Reliefs Reliefs Reliefs\n"
    "---\n"
    "• Section 34, Specific • Preliminary & Final Decree • Order XXXIX Rules 1 & 2 CPC\n"
    "  Relief Act (Co-ownership) (Order XX Rule 18 CPC) (Temporary Injunction)\n"
    "• Section 31, Specific • Section 5, Specific Relief • Section 38, Specific Relief Act\n"
    "  Relief Act (Cancellation) Act (Title-based Possession) (Perpetual Injunction)\n"
    "```\n"
)
OPINION = (
    "## 5. Comprehensive Remedies Available to the Aggrieved Brother\n\n"
    "The aggrieved brother should institute a comprehensive civil suit praying for the following reliefs:\n\n"
    + FLOWCHART +
    "\n### A. Declaration of Title (Section 34, Specific Relief Act, 1963)\n\n"
    "- The plaintiff must seek a judicial decree declaring that he remains a lawful joint owner.\n"
)


def test_reported_flowchart_is_removed_and_prose_kept():
    out, stats = strip_diagrams(OPINION)
    assert stats["diagrams_removed"] == 1
    assert "COMPREHENSIVE CIVIL SUIT" not in out
    assert "+---" not in out and "```" not in out
    assert "### A. Declaration of Title" in out
    assert "praying for the following reliefs:" in out


def test_box_drawing_characters_count_as_a_drawing():
    src = "text\n```\n┌──┐\n│ A │\n└──┘\n```\nmore\n"
    out, stats = strip_diagrams(src)
    assert stats["diagrams_removed"] == 1 and out.strip() == "text\n\nmore".strip()


def test_arrow_chain_is_a_drawing():
    src = "```\nFIR --> Charge sheet --> Framing of charge --> Trial\n```\n"
    _, stats = strip_diagrams(src)
    assert stats["diagrams_removed"] == 1


def test_statutory_extract_in_a_fence_is_unfenced_not_lost():
    src = ("Section 49 reads:\n\n```\nNo document required by section 17 to be registered shall affect any "
           "immovable property comprised therein.\n```\n\nTherefore the deed is inadmissible.\n")
    out, stats = strip_diagrams(src)
    assert stats == {"diagrams_removed": 0, "fences_unwrapped": 1}
    assert "```" not in out
    assert "No document required by section 17" in out
    assert "Therefore the deed is inadmissible." in out


def test_markdown_table_inside_a_fence_is_unfenced_not_removed():
    src = "```\n| Old | New |\n|---|---|\n| 420 IPC | 318 BNS |\n```\n"
    out, stats = strip_diagrams(src)
    assert stats["diagrams_removed"] == 0
    assert "| 420 IPC | 318 BNS |" in out and "```" not in out


def test_response_without_fences_is_untouched():
    src = "1. First.\n\n   - sub\n\n2. Second with a | pipe in prose.\n"
    out, stats = strip_diagrams(src)
    assert out == src and stats == {"diagrams_removed": 0, "fences_unwrapped": 0}


def test_guardrail_removes_the_flowchart_end_to_end():
    from agents.guardrail import guardrail_output_node
    out = asyncio.run(guardrail_output_node({"final_response": OPINION, "task": "Scenario"}))["final_response"]
    assert "COMPREHENSIVE CIVIL SUIT" not in out and "```" not in out
    assert "### A. Declaration of Title" in out


def test_prompt_rule_is_in_the_shared_discipline_block():
    from config.prompts import INDIAN_LEGAL_BEHAVIORAL_DISCIPLINE as D
    assert "NO diagrams" in D and "flowcharts" in D and "code fence" in D
