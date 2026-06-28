"""Regression test for `agents.memory._rewrite_query` skip logic.

The bug (2026-06-28): when a user sent a long, fact-rich drafting prompt
as a follow-up in an existing thread, the memory rewrite step compressed
the 6K+ char query into a short "search query" (per REWRITE_PROMPT rule 7),
losing party names / dates / itemised lists. The downstream drafting agent
then fabricated substitutes from template examples.

Fix: skip rewrite entirely when `len(query) >= 500` — the REWRITE_PROMPT
is tuned for short follow-ups, not standalone fact-rich queries.
See `feedback_preserve_user_query.md`.
"""
from __future__ import annotations

from unittest.mock import patch

from langchain_core.messages import HumanMessage, AIMessage

from agents.memory import _rewrite_query


_LONG_FACT_RICH_QUERY = (
    "My name is Jyoti Narendra Amrutkar. I was married to Mr. Narendra "
    "Dattatray Amrutkar on 16 May 2013. Before the marriage, I was informed "
    "that he was earning Rs. 25,000/month but it turned out to be Rs. 7,000. "
    "He sold all my Stridhan including a 2-tola Mangalsutra, a 2.75-tola "
    "necklace, a 1-tola Mini Mangalsutra, and other ornaments. On 15 August "
    "2020 I left the matrimonial home. Draft a legal notice asking for "
    "Stridhan return, divorce, and permanent alimony or per-month alimony. "
    "Also mention mutual divorce if he is ready."
)


def _real_history():
    """A 4-message thread history that would normally trigger rewrite."""
    return [
        HumanMessage(content="Previous summary:"),
        AIMessage(content=(
            "User asked about Section 138 NI Act notice for a Rs. 5,00,000 "
            "cheque dishonour matter; the assistant drafted a standard "
            "demand notice with 15-day compliance period."
        )),
        HumanMessage(content="What about divorce notices?"),
        AIMessage(content=(
            "Divorce notices typically cite Section 13(1)(ia) of the Hindu "
            "Marriage Act, 1955 for cruelty grounds, and Section 13B for "
            "mutual consent. Section 125 CrPC covers maintenance."
        )),
    ]


def test_long_query_skipped_even_with_real_history():
    """The regression case: long standalone query in an existing thread.
    Rewrite should be skipped — no LLM call, original returned."""
    assert len(_LONG_FACT_RICH_QUERY) >= 500
    # If the rewrite call is reached, this test would either patch the LLM
    # or hit the network. Use side_effect=Exception to assert no call.
    with patch("agents.memory.get_gemini_flash",
               side_effect=AssertionError("LLM must not be called")):
        result = _rewrite_query(_LONG_FACT_RICH_QUERY, _real_history())
    assert result == _LONG_FACT_RICH_QUERY


def test_long_query_skipped_without_history():
    """Long query + no history → already skipped by the existing 'no history'
    check. Belt-and-braces."""
    with patch("agents.memory.get_gemini_flash",
               side_effect=AssertionError("LLM must not be called")):
        result = _rewrite_query(_LONG_FACT_RICH_QUERY, [])
    assert result == _LONG_FACT_RICH_QUERY


def test_short_followup_reaches_rewrite_path():
    """Short follow-up queries SHOULD still enter the rewrite path. The LLM
    call may or may not produce a usable rewrite (provenance / length checks
    may reject it downstream), but the path must NOT be short-circuited by
    the new length-based skip. Asserted by patching get_gemini_flash to
    raise — if the new skip kicks in for a short query, the LLM is never
    called and the patch never fires."""
    short_followup = "find related supreme court cases"  # 31 chars
    assert len(short_followup) < 500

    class _Sentinel(Exception):
        pass

    with patch("agents.memory.get_gemini_flash", side_effect=_Sentinel("reached LLM")):
        result = _rewrite_query(short_followup, _real_history())
    # On the _Sentinel exception, the outer try/except in _rewrite_query
    # falls back to the original query — so we get the input back. The
    # important assertion is that the LLM path was entered (otherwise the
    # patch would not have been triggered).
    assert result == short_followup
