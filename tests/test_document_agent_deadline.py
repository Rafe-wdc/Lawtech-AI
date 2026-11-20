"""Document agent: generation budget follows the request deadline, and a
Document-only question with files attached never falls to web search.

2026-11-20: a 390-page scanned writ petition (29.7 MB) with "Provide me this
in short". File processing succeeded; the one Gemini call that writes the
summary needs 60-120 s for that document and was cut by the fixed 90 s
`TIMEOUT_CHROMADB_SEC`. The finished summary was billed and discarded, the
orchestrator ran `web_search_fallback` on the bare prompt, and the user read
"No document, text, statutory provision, or factual background was provided
with the request." Same code on main, dev and the prod build.

No LLM calls: the generation function and the web fallback are stubbed.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

import agents.document as doc_mod
import agents.orchestrator as orch
from agents.document import document_node, generation_timeout_s
from core import deadline
from core.settings import TIMEOUT_CHROMADB_SEC
from core.state import AgentResult


# ---------------------------------------------------------------- budget ---

def test_no_deadline_keeps_the_historical_floor():
    deadline.clear_deadline()
    assert generation_timeout_s() == TIMEOUT_CHROMADB_SEC == 90.0


def test_budget_is_the_remaining_deadline_minus_the_reserve():
    with deadline.deadline_scope(400):
        t = generation_timeout_s()
    # 400 s left minus the 30 s reserve, give or take scheduling jitter.
    assert 360 < t <= 370
    assert t > TIMEOUT_CHROMADB_SEC


def test_budget_never_drops_below_the_floor_when_little_time_is_left():
    with deadline.deadline_scope(50):
        assert generation_timeout_s() == TIMEOUT_CHROMADB_SEC
    with deadline.deadline_scope(1):
        assert generation_timeout_s() == TIMEOUT_CHROMADB_SEC


def test_floor_and_reserve_are_parameters():
    with deadline.deadline_scope(200):
        assert 139 < generation_timeout_s(floor=10, reserve=60) <= 140
        assert generation_timeout_s(floor=500) == 500


def test_flash_client_accepts_a_timeout():
    from core.clients import get_gemini_flash_full
    params = inspect.signature(get_gemini_flash_full).parameters
    assert "timeout" in params and params["timeout"].default == 120


# ------------------------------------------------- document_node wiring ---

_FILE_STATE = {
    "query": "Provide me this in short",
    "original_query": "Provide me this in short",
    "user_language": "en",
    "chat_history": [],
    "file_context": {
        "chromadb_collections": [],
        "file_names": ["WP_KAMTAPRASAD_R_DUBEY.pdf"],
        "extracted_texts": [{"name": "WP_KAMTAPRASAD_R_DUBEY.pdf",
                             "text": "IN THE HIGH COURT OF JUDICATURE AT BOMBAY " * 50}],
    },
}


def _run_document_node_capturing_timeout(monkeypatch, seconds_left):
    seen: dict = {}
    real_wait_for = doc_mod.asyncio.wait_for

    async def spy_wait_for(aw, timeout=None, **kw):
        seen.setdefault("timeouts", []).append(timeout)
        return await real_wait_for(aw, timeout=timeout, **kw)

    monkeypatch.setattr(doc_mod.asyncio, "wait_for", spy_wait_for)
    monkeypatch.setattr(doc_mod, "_generate_from_docs",
                        lambda *a, **k: ("A short summary.", 42))

    async def go():
        if seconds_left is None:
            deadline.clear_deadline()
            return await document_node(dict(_FILE_STATE))
        with deadline.deadline_scope(seconds_left):
            return await document_node(dict(_FILE_STATE))

    out = asyncio.run(go())
    return out, seen


def test_document_node_waits_for_the_remaining_deadline(monkeypatch):
    out, seen = _run_document_node_capturing_timeout(monkeypatch, 400)
    gen_timeout = seen["timeouts"][-1]           # the generation call is the last wait
    # 400 s deadline minus the 30 s tail reserve minus the 75 s kept for the
    # compact retry minus whatever the node spent before the call (first-call
    # imports can take seconds in a big suite): well above the old 90 s cap,
    # never above what the retry split allows.
    assert TIMEOUT_CHROMADB_SEC < gen_timeout <= 295
    assert out["agent_results"]["Document"].content == "A short summary."
    assert not out["agent_results"]["Document"].error


def test_document_node_keeps_ninety_seconds_without_a_deadline(monkeypatch):
    _, seen = _run_document_node_capturing_timeout(monkeypatch, None)
    assert seen["timeouts"][-1] == TIMEOUT_CHROMADB_SEC


def test_generation_timeout_is_still_reported_as_an_error(monkeypatch):
    """The honest message in synthesis relies on the error text naming the
    timeout; keep that contract."""
    async def instant_timeout(aw, timeout=None, **kw):
        aw.close() if inspect.iscoroutine(aw) else None
        raise asyncio.TimeoutError()

    monkeypatch.setattr(doc_mod.asyncio, "wait_for", instant_timeout)
    deadline.clear_deadline()
    out = asyncio.run(document_node(dict(_FILE_STATE)))
    r = out["agent_results"]["Document"]
    assert not r.content
    assert "timeout" in (r.error or "").lower()


# ------------------------------------------------ synthesis: no web search ---

def _synth_state(agent_results, *, with_file=True, planned=("Document",)):
    st = {
        "original_query": "Provide me this in short",
        "query": "Provide me this in short",
        "task": planned[0],
        "tasks_planned": list(planned),
        "previous_artifact_content": "",
        "agent_results": agent_results,
    }
    if with_file:
        st["file_context"] = dict(_FILE_STATE["file_context"])
    return st


def _forbid_web_fallback(monkeypatch):
    import core.agent_fallback as af

    async def boom(*a, **k):
        raise AssertionError("web_search_fallback must not run for a Document-only "
                             "question with files attached")

    monkeypatch.setattr(af, "web_search_fallback", boom)


def test_timed_out_document_with_files_gets_an_honest_message(monkeypatch):
    _forbid_web_fallback(monkeypatch)
    results = {"Document": AgentResult(agent_name="Document", content="",
                                       error="TimeoutError")}
    out = asyncio.run(orch.orchestrator_synthesize_node(_synth_state(results)))
    msg = out["final_response"]
    assert "could not finish reading your uploaded document" in msg
    assert "WP_KAMTAPRASAD_R_DUBEY.pdf" in msg
    assert "have not answered yet" in msg
    assert "No document" not in msg
    assert out["tokens_consumed"] == 0


def test_other_document_failure_with_files_also_stays_honest(monkeypatch):
    _forbid_web_fallback(monkeypatch)
    results = {"Document": AgentResult(agent_name="Document", content="",
                                       error="ResourceExhausted: 429")}
    msg = asyncio.run(orch.orchestrator_synthesize_node(_synth_state(results)))["final_response"]
    assert "could not read your uploaded document" in msg
    assert "in time" not in msg


def test_empty_document_result_with_files_stays_honest(monkeypatch):
    _forbid_web_fallback(monkeypatch)
    results = {"Document": AgentResult(agent_name="Document", content="")}
    msg = asyncio.run(orch.orchestrator_synthesize_node(_synth_state(results)))["final_response"]
    assert "have not answered yet" in msg


def test_without_files_the_web_fallback_still_runs(monkeypatch):
    import core.agent_fallback as af

    async def fake_fallback(*a, **k):
        return AgentResult(agent_name="Orchestrator", content="web answer", sources=[])

    monkeypatch.setattr(af, "web_search_fallback", fake_fallback)
    results = {"Document": AgentResult(agent_name="Document", content="", error="TimeoutError")}
    out = asyncio.run(orch.orchestrator_synthesize_node(_synth_state(results, with_file=False)))
    assert out["final_response"] == "web answer"


def test_mixed_plan_with_files_keeps_the_web_fallback(monkeypatch):
    """A case-law question with an FIR attached: Judgment came back empty and
    Document errored. The web fallback may still answer the case-law part."""
    import core.agent_fallback as af

    async def fake_fallback(*a, **k):
        return AgentResult(agent_name="Orchestrator", content="web answer", sources=[])

    monkeypatch.setattr(af, "web_search_fallback", fake_fallback)
    results = {
        "Judgment": AgentResult(agent_name="Judgment", content=""),
        "Document": AgentResult(agent_name="Document", content="", error="TimeoutError"),
    }
    out = asyncio.run(orch.orchestrator_synthesize_node(
        _synth_state(results, planned=("Judgment", "Document"))))
    assert out["final_response"] == "web answer"


# ------------------------------------------- compact retry after a timeout ---

def test_first_attempt_leaves_room_for_the_compact_retry():
    from agents.document import first_attempt_timeout_s
    assert first_attempt_timeout_s(425) == 350
    assert first_attempt_timeout_s(100) == TIMEOUT_CHROMADB_SEC     # floor wins
    assert first_attempt_timeout_s(90) == TIMEOUT_CHROMADB_SEC



def test_brief_size_note_overrides_the_length_floor():
    from agents.document import brief_size_note
    note = brief_size_note()
    assert "COMPACT" in note and "overrides any minimum-length rule" in note


def test_timed_out_first_attempt_falls_back_to_a_compact_summary(monkeypatch):
    waits: list = []
    real_wait_for = doc_mod.asyncio.wait_for

    async def first_call_times_out(aw, timeout=None, **kw):
        waits.append(timeout)
        if len(waits) == 1:
            aw.close()
            raise asyncio.TimeoutError()
        return await real_wait_for(aw, timeout=timeout, **kw)

    seen_brief: list = []

    def fake_generate(*a, brief=False, **k):
        seen_brief.append(brief)
        return ("Compact summary.", 7)

    monkeypatch.setattr(doc_mod.asyncio, "wait_for", first_call_times_out)
    monkeypatch.setattr(doc_mod, "_generate_from_docs", fake_generate)

    async def go():
        with deadline.deadline_scope(400):
            return await document_node(dict(_FILE_STATE))

    r = asyncio.run(go())["agent_results"]["Document"]
    assert seen_brief == [True]          # the first attempt never ran its body; the retry was brief
    assert r.content == "Compact summary." and not r.error
    assert len(waits) == 2
    assert waits[0] <= 295               # first attempt left room for the retry
    assert 45 <= waits[1] <= 370         # retry got what remained minus the tail reserve


def test_no_compact_retry_when_the_deadline_is_nearly_spent(monkeypatch):
    waits: list = []

    async def always_times_out(aw, timeout=None, **kw):
        waits.append(timeout)
        aw.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(doc_mod.asyncio, "wait_for", always_times_out)
    monkeypatch.setattr(doc_mod, "_generate_from_docs", lambda *a, **k: ("x", 1))

    async def go():
        with deadline.deadline_scope(40):   # 40 s left: retry budget 10 s < 45 s minimum
            return await document_node(dict(_FILE_STATE))

    r = asyncio.run(go())["agent_results"]["Document"]
    assert len(waits) == 1               # no second attempt
    assert not r.content and "timeout" in (r.error or "").lower()
