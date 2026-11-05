"""Parallel section drafting (default since 2026-10-16).

The opening pair is written first; every remaining section is then written
as its own call, concurrently, each seeing the opening. Claude Sonnet 5
writes ~80-115 output tokens/s, so a 7-section petition written strictly in
sequence took ~8 minutes and overran the per-call limit.
"""

import asyncio
import os
import time
from unittest.mock import patch

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import agents.drafting as d
from agents.drafting import _Section, _generate_sectionwise

PLAN = [_Section(id=i, heading=h, summary="") for i, h in [
    ("cause", "CAUSE TITLE"), ("facts", "STATEMENT OF FACTS"), ("grounds", "GROUNDS"),
    ("interim", "INTERIM RELIEF"), ("prayer", "PRAYER"), ("verify", "VERIFICATION"),
    ("annex", "LIST OF DOCUMENTS")]]


def _run(sections, fake, monkeypatch, parallel="1", max_par="4"):
    monkeypatch.setenv("DRAFTING_PARALLEL_SECTIONS", parallel)
    monkeypatch.setenv("DRAFTING_PARALLEL_MAX", max_par)
    with patch("agents.drafting._generate_section_pair", side_effect=fake):
        return asyncio.run(_generate_sectionwise(
            sections=sections, query="Draft a writ petition", user_facts="",
            reference_draft="REF", user_intent=None, user_language="en",
            progress_emit=lambda *a, **k: None, gathered_context=None))


def _recording_fake(calls, delay=0.0, active=None, peak=None):
    async def fake(*, sections_to_write, section_position_start, prior_text, **kw):
        if active is not None:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        calls.append((section_position_start, [s.heading for s in sections_to_write], prior_text))
        await asyncio.sleep(delay)
        if active is not None:
            active[0] -= 1
        return "\n".join(f"## {s.heading}\n1. Body of {s.id}." for s in sections_to_write)
    return fake


def test_opening_pair_first_then_one_call_per_section(monkeypatch):
    calls = []
    out = _run(PLAN, _recording_fake(calls), monkeypatch)
    assert calls[0][0] == 1 and calls[0][1] == ["CAUSE TITLE", "STATEMENT OF FACTS"]
    assert calls[0][2] == ""                                  # opening sees nothing before it
    rest = sorted(calls[1:])
    assert [c[0] for c in rest] == [3, 4, 5, 6, 7]
    assert all(len(c[1]) == 1 for c in rest)                  # one section per call
    assert all("Body of facts." in c[2] for c in rest)        # each sees the opening


def test_sections_are_assembled_in_document_order(monkeypatch):
    calls = []

    async def fake(*, sections_to_write, section_position_start, prior_text, **kw):
        # later sections finish first
        await asyncio.sleep(0.05 * (10 - section_position_start))
        return "\n".join(f"## {s.heading}\nBody of {s.id}." for s in sections_to_write)

    out = _run(PLAN, fake, monkeypatch)
    order = [out.index(f"## {s.heading}") for s in PLAN]
    assert order == sorted(order)


def test_runs_concurrently_and_respects_the_cap(monkeypatch):
    calls, active, peak = [], [0], [0]
    t0 = time.monotonic()
    _run(PLAN, _recording_fake(calls, delay=0.2, active=active, peak=peak), monkeypatch, max_par="2")
    took = time.monotonic() - t0
    assert peak[0] == 2                     # 5 parallel sections, cap 2
    # opening 0.2s + ceil(5/2)=3 waves of 0.2s = ~0.8s; sequential would be ~1.4s
    assert took < 1.15


def test_one_failed_section_does_not_sink_the_others(monkeypatch):
    async def fake(*, sections_to_write, section_position_start, prior_text, **kw):
        if section_position_start == 4:
            raise RuntimeError("provider down")
        return "\n".join(f"## {s.heading}\nBody of {s.id}." for s in sections_to_write)

    monkeypatch.setattr(d, "_make_pair_clock", lambda *a, **k: (lambda: 0.0))  # no retry budget
    out = _run(PLAN, fake, monkeypatch)
    # the sections around the failed one survive (the repair pass may then
    # regenerate the failed mandatory section; that is existing behaviour)
    for sid in ("cause", "facts", "grounds", "prayer", "verify", "annex"):
        assert f"Body of {sid}." in out


def test_switch_back_to_sequential(monkeypatch):
    calls = []
    _run(PLAN, _recording_fake(calls), monkeypatch, parallel="0")
    assert [c[0] for c in calls] == [1, 3, 5, 7]              # pairs, in order
    assert len(calls[1][2]) < len(calls[2][2])               # each sees more than the last


def test_short_plans_stay_sequential(monkeypatch):
    calls = []
    _run(PLAN[:2], _recording_fake(calls), monkeypatch)
    assert [c[0] for c in calls] == [1]


def test_parallel_plan_fits_where_sequential_would_trim():
    from agents.drafting import _parallel_plan_time_s, OBSERVED_PAIR_TIME_P90_S
    # 7 sections: opening + ceil(5/4)=2 waves = 3 x p90 (vs 4 pairs sequentially)
    assert _parallel_plan_time_s(7, OBSERVED_PAIR_TIME_P90_S) == 3 * OBSERVED_PAIR_TIME_P90_S
