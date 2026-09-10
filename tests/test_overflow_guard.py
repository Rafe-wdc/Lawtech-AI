"""Nothing in a response may be wider than the answer box.

Advocate report 2026-09-10. Two shapes were found in real responses: bare
whitelisted PDF URLs (unbreakable, 70-110 chars) and 20-40 character
underscore runs used as blanks. core.overflow repairs both; the output
guardrail applies it to every response. Pure tests, no LLM.
"""
from __future__ import annotations

from core.overflow import cap_long_runs, keep_inside_answer_box, shorten_bare_urls

SCI = "https://api.sci.gov.in/supremecourt/2023/7943/7943_2023_14_1501_48299_Judgement_08-Aug-2024.pdf"
S3 = "https://lawttorney.s3.ap-south-1.amazonaws.com/bombay%20high%20court/2024/abc.pdf"


def test_bare_sci_url_becomes_short_labelled_link():
    out, n = shorten_bare_urls(f"See the judgment at {SCI} for the ratio.")
    assert n == 1
    assert f"[Supreme Court judgment (PDF)]({SCI})" in out
    assert out.count(SCI) == 1


def test_trailing_punctuation_stays_outside_the_link():
    out, _ = shorten_bare_urls(f"Reported at {SCI}.")
    assert out.endswith(f"]({SCI}).")


def test_existing_markdown_link_is_left_alone():
    src = f"[api.sci.gov.in]({SCI}) and [Judgment]({S3})"
    out, n = shorten_bare_urls(src)
    assert n == 0 and out == src


def test_non_whitelisted_url_is_not_touched_here():
    # external URLs are the url_filter's job (they are stripped there)
    src = "see https://example.com/some/very/long/path/that/goes/on/and/on/forever"
    out, n = shorten_bare_urls(src)
    assert n == 0 and out == src


def test_underscore_blank_is_capped_at_fifteen():
    out, n = cap_long_runs("Bail Application No. ______________________________ of 2026")
    assert n == 1
    assert "_" * 15 in out and "_" * 16 not in out


def test_short_blank_is_untouched():
    src = "Verified at Delhi on this ____ day of September, 2026."
    out, n = cap_long_runs(src)
    assert n == 0 and out == src


def test_line_of_dashes_becomes_a_rule():
    out, n = cap_long_runs("text\n----------------------------------------\nmore")
    assert n == 1 and out == "text\n---\nmore"


def test_table_separator_row_is_never_touched():
    src = "| Head A | Head B |\n|------------------------|------------------------|\n| a | b |"
    out, n = cap_long_runs(src)
    assert n == 0 and out == src


def test_dot_leader_party_label_is_untouched():
    src = ".....Applicant/Accused"
    out, n = cap_long_runs(src)
    assert n == 0 and out == src


def test_keep_inside_answer_box_applies_both():
    src = f"Order at {SCI}\n\nCase No. ________________________________ of 2026"
    out, stats = keep_inside_answer_box(src)
    assert stats == {"urls": 1, "runs": 1}
    assert "[Supreme Court judgment (PDF)]" in out and "_" * 16 not in out


def test_guardrail_applies_the_overflow_repair():
    import asyncio
    from agents.guardrail import guardrail_output_node
    src = f"Held in {SCI} that bail is the rule.\n\nSigned: ______________________________"
    out = asyncio.run(guardrail_output_node({"final_response": src, "task": "SCI_Judgment"}))["final_response"]
    assert "[Supreme Court judgment (PDF)]" in out
    assert "_" * 16 not in out
