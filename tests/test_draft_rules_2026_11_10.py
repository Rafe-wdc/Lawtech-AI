"""Guards for two drafting rules added 2026-11-10.

A fresh private complaint about 2016 IPC offences was captioned "under
Section 200 of the Code of Criminal Procedure", and the draft added facts the
client's file never stated ("illiteracy", "filthy abuses", "stalk"). These
tests pin the rule text in both drafting prompts and the reviewer.
"""
from config.prompts import DRAFTING_SECTIONWISE_DISCLOSURES, DRAFTING_SECTION_PAIR_PROMPT, DRAFTING_SYSTEM_PROMPT


def _reviewer_text() -> str:
    import inspect
    import core.self_refine as sr
    return inspect.getsource(sr)


def test_filing_date_picks_the_procedure_in_both_drafting_prompts():
    for prompt in (DRAFTING_SYSTEM_PROMPT, DRAFTING_SECTIONWISE_DISCLOSURES):
        assert "THE FILING DATE" in prompt and "PICKS THE PROCEDURE" in prompt
        assert "Section 223 of the Bharatiya Nagarik Suraksha Sanhita" in prompt
        assert "531(2)(a)" in prompt


def test_reviewer_flags_a_fresh_complaint_captioned_under_the_crpc():
    text = _reviewer_text()
    assert "the filing date picks the" in text
    assert "Section 223 BNSS" in text


def test_no_added_allegations_rule_in_both_drafting_prompts():
    for prompt in (DRAFTING_SYSTEM_PROMPT, DRAFTING_SECTION_PAIR_PROMPT):
        assert "NO ADDED ALLEGATIONS" in prompt
        assert "illiteracy" in prompt


def test_new_rule_text_has_no_format_braces():
    # Drafting prompts go through str.format(); a stray brace in the new
    # paragraphs would raise at request time.
    for prompt, marker in ((DRAFTING_SYSTEM_PROMPT, "NO ADDED ALLEGATIONS"),
                           (DRAFTING_SECTION_PAIR_PROMPT, "NO ADDED ALLEGATIONS"),
                           (DRAFTING_SYSTEM_PROMPT, "TWO CLOCKS"),
                           (DRAFTING_SECTIONWISE_DISCLOSURES, "TWO CLOCKS")):
        i = prompt.index(marker)
        end = prompt.find(chr(10) + chr(10), i)
        paragraph = prompt[i:end if end > 0 else None]
        assert "{" not in paragraph and "}" not in paragraph, marker
