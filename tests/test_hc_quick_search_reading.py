"""The High Court agent's quick first search answers only when it searched
the court and year the question asks for (2026-11-18).

The agent runs a quick search on a regex reading of the question while the
model reads it, and used the quick search's hits whenever there were any.
"Bombay HC 2026 ... divorce" was read as no court, so every court was
searched and a Kerala judgment came back among the Bombay ones; an Act's
year ("Arbitration and Conciliation Act, 1996") is read as the decision
year. Now the quick search's hits are set aside when its court or year
differs from the model's reading, and the regex reads "X HC" and "High
Court of X" as the model is told to.
"""
from __future__ import annotations

import pytest

from agents.judgment import _judgment_regex_fallback, _searched_as_asked


@pytest.mark.parametrize("question, court", [
    ("Bombay HC 2026 judgment on divorce on the ground of cruelty", "bombay high court"),
    ("Delhi HC judgment on interim injunction in trademark infringement", "delhi high court"),
    ("Punjab and Haryana HC on regularisation", "punjab and haryana high court"),
    ("High Court of Judicature at Bombay judgments on bail", "bombay high court"),
    ("judgments of the High Court of Madhya Pradesh on 498A", "madhya pradesh high court"),
    ("Madhya Pradesh High Court quashing a 498A FIR", "madhya pradesh high court"),
    ("High Court judgment on a Motor Accident Claims Tribunal award", "high court"),  # not "tribunal"
    ("Bombay High Court on a Motor Accident Claims Tribunal award", "bombay high court"),
    ("HC judgments on Section 9 of the Arbitration Act", "high court"),
    ("Supreme Court judgments on Section 498A", "supreme court"),
    ("Kerala judgments on bail", None),
])
def test_the_quick_reading_names_the_court_asked(question, court):
    assert _judgment_regex_fallback(question).court_name == court


def test_quick_search_hits_are_used_only_when_court_and_year_match_the_question():
    assert _searched_as_asked("bombay high court", 2026, "bombay high court", 2026)
    assert _searched_as_asked(None, None, None, None)
    # looser: the court asked was missed ("Bombay HC") -> search again
    assert not _searched_as_asked(None, 2026, "bombay high court", 2026)
    # stricter: an Act's year taken as the decision year -> search again
    assert not _searched_as_asked("bombay high court", 1996, "bombay high court", None)
    # another year altogether
    assert not _searched_as_asked("bombay high court", 2025, "bombay high court", 2026)
