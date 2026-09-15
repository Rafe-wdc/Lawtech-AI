"""PR 4 — resumable continuation.

7. When section pairs fail, the completed sections are kept, a checkpoint
   (plan + completed texts + first failed index + prefix hash) is filled
   for the caller, and the banner offers "Continue drafting". A follow-up
   request carrying `continue_draft_of=<hash>` short-circuits the
   orchestrator to Drafting with the checkpoint on state, and generation
   resumes at the failed pair with the completed prefix as prior text.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import agents.drafting as d
from agents.drafting import _Section, _checkpoint_hash, _generate_sectionwise
from agents.orchestrator import _continuation_plan


def _plan(n=5):
    names = ["Cause Title", "Facts", "Grounds", "Prayer", "Verification", "Affidavit"]
    return [_Section(id=f"s{i}", heading=names[i], summary=f"brief {i}") for i in range(n)]


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Sectionwise: failure fills the checkpoint; resume starts at the failed pair
# ---------------------------------------------------------------------------

def test_failed_pair_fills_checkpoint_and_banner_offers_continue():
    calls = []

    async def fake_pair(*, sections_to_write, section_position_start, prior_text, **kw):
        calls.append(section_position_start)
        if section_position_start == 3:
            raise RuntimeError("provider down")
        return "\n\n".join(f"## {s.heading}\nbody {s.id}" for s in sections_to_write)

    out: dict = {}
    with patch("agents.drafting._generate_section_pair", side_effect=fake_pair):
        draft = _run(_generate_sectionwise(
            sections=_plan(5), query="Draft", user_facts="", reference_draft="REF",
            user_intent=None, user_language="en", progress_emit=lambda *a, **k: None,
            gathered_context=None, checkpoint_out=out,
        ))
    # pair 1-2 ok, pair 3-4 failed (attempt + retry), pair 5 ok
    assert calls[:1] == [1] and 3 in calls and 5 in calls
    assert out["failed_at_section_index"] == 3
    assert out["total_sections"] == 5
    assert out["completed_sections"] == 3
    assert [s["heading"] for s in out["sections"]] == [s.heading for s in _plan(5)]
    assert len(out["completed_texts"]) == 2          # two successful pair texts
    assert out["completed_sections_hash"] == _checkpoint_hash(out["completed_texts"])
    assert "Continue drafting" in draft
    assert "resume from section 3" in draft
    assert "re-send your prompt" not in draft


def test_resume_starts_at_failed_pair_and_keeps_completed_prefix():
    calls = []

    async def fake_pair(*, sections_to_write, section_position_start, prior_text, **kw):
        calls.append((section_position_start, len(prior_text)))
        return "\n\n".join(f"## {s.heading}\nbody {s.id}" for s in sections_to_write)

    plan = _plan(5)
    prefix = ["## Cause Title\nbody s0\n\n## Facts\nbody s1"]
    ckpt = {
        "sections": [s.model_dump() for s in plan],
        "completed_texts": prefix,
        "failed_at_section_index": 3,
    }
    out: dict = {}
    with patch("agents.drafting._generate_section_pair", side_effect=fake_pair):
        draft = _run(_generate_sectionwise(
            sections=plan, query="Draft", user_facts="", reference_draft="REF",
            user_intent=None, user_language="en", progress_emit=lambda *a, **k: None,
            gathered_context=None, resume_from=ckpt, checkpoint_out=out,
        ))
    # only pairs 3-4 and 5 were written; the first pair saw the prefix as prior text
    assert [c[0] for c in calls] == [3, 5]
    assert calls[0][1] > 0
    assert draft.startswith("## Cause Title")
    assert "## Grounds" in draft and "## Verification" in draft
    assert "Draft incomplete" not in draft
    assert "failed_at_section_index" not in out      # nothing failed this time


def test_checkpoint_hash_is_stable_and_order_sensitive():
    assert _checkpoint_hash(["a", "b"]) == _checkpoint_hash(["a", "b"])
    assert _checkpoint_hash(["a", "b"]) != _checkpoint_hash(["b", "a"])
    assert len(_checkpoint_hash(["x"])) == 16


# ---------------------------------------------------------------------------
# Orchestrator short-circuit
# ---------------------------------------------------------------------------

def _ckpt(h="abc123", thread="t-1"):
    return {
        "thread_id": thread, "completed_sections_hash": h,
        "sections": [s.model_dump() for s in _plan(3)], "query": "Draft a plaint",
        "user_language": "mr", "intent": {"format_explicit": True, "confidence": 0.9},
    }


def test_continuation_plan_matches_hash_and_thread():
    plan = _continuation_plan(_ckpt(), "abc123", "t-1")
    assert plan is not None
    assert plan["task"] == "Drafting" and plan["tasks_planned"] == ["Drafting"]
    assert plan["agent_queries"] == {"Drafting": "Draft a plaint"}
    assert plan["user_language"] == "mr"
    assert plan["draft_continuation"]["completed_sections_hash"] == "abc123"
    assert plan["user_intent"] is not None and plan["user_intent"].format_explicit


def test_continuation_plan_rejects_mismatches():
    assert _continuation_plan(None, "abc123", "t-1") is None
    assert _continuation_plan(_ckpt(), "", "t-1") is None
    assert _continuation_plan(_ckpt(), "other", "t-1") is None          # different draft
    assert _continuation_plan(_ckpt(thread="t-2"), "abc123", "t-1") is None  # other thread
    bad = _ckpt(); bad["sections"] = []
    assert _continuation_plan(bad, "abc123", "t-1") is None


# ---------------------------------------------------------------------------
# Chat store round trip (SQLite backend, temp file)
# ---------------------------------------------------------------------------

def test_sqlite_checkpoint_save_load_clear():
    from core.chat_store import _SqliteChatHistoryStore
    with tempfile.TemporaryDirectory() as tmp:
        store = _SqliteChatHistoryStore(os.path.join(tmp, "t.db"))
        assert _run(store.load_draft_checkpoint("t-9")) is None
        _run(store.save_draft_checkpoint("t-9", _ckpt(thread="t-9")))
        got = _run(store.load_draft_checkpoint("t-9"))
        assert got["completed_sections_hash"] == "abc123"
        assert got["user_language"] == "mr"
        _run(store.clear_draft_checkpoint("t-9"))
        assert _run(store.load_draft_checkpoint("t-9")) is None


# ---------------------------------------------------------------------------
# Dispatcher: resume skips the judge and the budget trim
# ---------------------------------------------------------------------------

def test_generate_draft_resume_skips_judge_and_uses_stored_plan(monkeypatch):
    seen = {}

    async def fake_sectionwise(*, sections, resume_from, checkpoint_out, **kw):
        seen["sections"] = [s.heading for s in sections]
        seen["resume_from"] = resume_from
        return "## resumed"

    async def fail_judge(**kw):
        raise AssertionError("judge must not run on resume")

    async def fail_niche(*a, **k):
        raise AssertionError("niche selector must not run on resume")

    monkeypatch.setattr(d, "_generate_sectionwise", fake_sectionwise)
    monkeypatch.setattr(d, "_judge_fanout", fail_judge)
    monkeypatch.setattr("agents.drafting_niche.pick_drafting_niche", fail_niche)
    monkeypatch.setattr(d, "is_off_target_language", lambda *a, **k: False)

    ckpt = {"sections": [s.model_dump() for s in _plan(4)], "completed_texts": ["x"],
            "failed_at_section_index": 3, "niche_overlay": "## NICHE"}
    out = _run(d._generate_draft(
        query="Draft", user_facts="", reference_draft="REF", user_intent=None,
        user_language="en", progress_emit=lambda *a, **k: None, resume_from=ckpt,
        checkpoint_out={},
    ))
    assert out == "## resumed"
    assert seen["sections"] == [s.heading for s in _plan(4)]
    assert seen["resume_from"] is ckpt
