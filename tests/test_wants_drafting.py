"""Unit tests for `_wants_drafting` (orchestrator routing).

The routing was originally a regex/keyword check on the raw user query (BUG-01
fix, see git log). It now reads `intent.task_intent` from the structured
extractor — so this file tests only the trivial 3-line field check that
remains in `agents.orchestrator._wants_drafting`. The semantic
query → task_intent mapping lives in the intent extractor (Gemini Flash Lite)
and is covered by `tests/test_user_intent_extraction.py`.

Run via pytest, or as a CLI script (preserved for parity with sibling tests):

    pytest tests/test_wants_drafting.py -v
    python tests/test_wants_drafting.py
"""
from __future__ import annotations

import sys

# Make `agents` importable when run from repo root.
sys.path.insert(0, ".")

from agents.orchestrator import _wants_drafting
from config.intent import UserIntent, default_intent


def _intent(task_intent: str = "draft", confidence: float = 0.9) -> UserIntent:
    """Build a UserIntent stub with the two fields _wants_drafting reads."""
    return default_intent().model_copy(update={
        "task_intent": task_intent,
        "confidence": confidence,
    })


def test_returns_true_for_draft_intent():
    assert _wants_drafting(_intent(task_intent="draft")) is True


def test_returns_false_for_analyze_intent():
    assert _wants_drafting(_intent(task_intent="analyze")) is False


def test_returns_false_for_lookup_intent():
    assert _wants_drafting(_intent(task_intent="lookup")) is False


def test_returns_false_when_intent_is_none():
    assert _wants_drafting(None) is False


def test_returns_false_when_confidence_below_threshold():
    """Conservative fallback: a low-confidence 'draft' intent does NOT route
    to Drafting. The LLM planner gets to decide instead."""
    assert _wants_drafting(_intent(task_intent="draft", confidence=0.3)) is False


def test_passes_at_threshold():
    """Confidence ≥ 0.5 is enough to honour task_intent='draft'."""
    assert _wants_drafting(_intent(task_intent="draft", confidence=0.5)) is True


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
