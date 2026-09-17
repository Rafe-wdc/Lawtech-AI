"""PR 3 — budget-aware planning.

After template acquisition and the fan-out judge, before the section-pair
loop, the plan is checked against the remaining request budget (minus the
self-refine and language-gate reserves). Plans that cannot be written in
time are collapsed (middle sections merged; first and last kept) or
switched to single-pass. Every trim is logged with the numbers.
"""

from __future__ import annotations

from types import SimpleNamespace

from agents.drafting import (
    LANG_GATE_RESERVE_S,
    OBSERVED_PAIR_TIME_P90_S,
    SELF_REFINE_RESERVE_S,
    _Section,
    _fit_plan_to_budget,
)


def _plan(n):
    names = ["Cause Title", "Facts", "Grounds", "Parity", "Medical Grounds",
             "Undertakings", "Prayer", "Verification", "Affidavit", "Schedule"]
    return [_Section(id=f"s{i}", heading=names[i % len(names)], summary=f"brief {i}")
            for i in range(n)]


def test_plan_that_fits_is_kept_unchanged():
    secs, feasible, action = _fit_plan_to_budget(_plan(7), budget_left_s=200)
    assert action == "kept"
    assert feasible == 8          # 200 // 45 = 4 pairs = 8 sections
    assert [s.heading for s in secs] == [s.heading for s in _plan(7)]


def test_plan_is_collapsed_keeping_first_and_last():
    plan = _plan(8)
    secs, feasible, action = _fit_plan_to_budget(plan, budget_left_s=140)
    assert action == "collapsed"
    assert feasible == 6          # 140 // 45 = 3 pairs
    assert len(secs) == 6
    assert secs[0].heading == plan[0].heading          # cause title kept
    assert secs[-1].heading == plan[-1].heading        # closing block kept
    merged = [s for s in secs if " and " in s.heading]
    assert merged, "middle sections were merged, not dropped"
    # nothing was silently dropped: every original heading survives somewhere
    joined = " | ".join(s.heading for s in secs)
    for s in plan:
        assert s.heading in joined
    # merged sections carry both briefs
    for m in merged:
        assert "brief" in m.summary and m.summary.count("brief") >= 2


def test_below_one_pair_goes_single_pass():
    secs, feasible, action = _fit_plan_to_budget(_plan(5), budget_left_s=40)
    assert action == "single_pass"
    assert feasible == 0
    assert secs == []


def test_negative_budget_goes_single_pass():
    secs, feasible, action = _fit_plan_to_budget(_plan(3), budget_left_s=-30)
    assert action == "single_pass"


def test_three_section_plan_into_two_merges_into_the_opening():
    plan = _plan(3)
    secs, feasible, action = _fit_plan_to_budget(plan, budget_left_s=50)
    assert feasible == 2 and action == "collapsed"
    assert len(secs) == 2
    assert secs[-1].heading == plan[-1].heading
    assert plan[1].heading in secs[0].heading


def test_reserves_and_p90_are_sane():
    # The reserves must leave room for at least one pair on a healthy
    # request: 285s budget - reserves must exceed one p90 pair.
    assert 285 - SELF_REFINE_RESERVE_S - LANG_GATE_RESERVE_S > OBSERVED_PAIR_TIME_P90_S * 2
    assert 30 <= OBSERVED_PAIR_TIME_P90_S <= 60


def test_dispatch_hook_trims_when_clock_is_short(monkeypatch):
    """Drive the real hook through _generate_draft's dispatch by stubbing
    the judge, the clock and both generators."""
    import asyncio
    import agents.drafting as d

    plan = _plan(8)
    strategy = SimpleNamespace(should_fanout=True, sections=list(plan),
                               reasoning="fan out — 8 sections")

    async def fake_judge(**kw):
        return strategy

    seen = {}

    async def fake_sectionwise(*, sections, **kw):
        seen["sections"] = list(sections)
        return "## draft"

    async def fake_single(**kw):
        seen["single"] = True
        return "## single"

    async def fake_acquire(*a, **kw):
        return ("REF", "es", "/content/x.csv")

    monkeypatch.setattr(d, "_judge_fanout", fake_judge)
    monkeypatch.setattr(d, "_generate_sectionwise", fake_sectionwise)
    monkeypatch.setattr(d, "_generate_single_pass", fake_single)
    monkeypatch.setattr("core.deadline.remaining", lambda: 200.0)  # 200-65 = 135 -> 3 pairs

    # Niche selector and language gate are network / model calls; neutralise.
    async def fake_niche(*a, **k):
        return None
    monkeypatch.setattr("agents.drafting_niche.pick_drafting_niche", fake_niche)
    monkeypatch.setattr(d, "is_off_target_language", lambda *a, **k: False)

    out = asyncio.run(d._generate_draft(
        query="Draft a bail application", user_facts="", reference_draft="REF",
        user_intent=None, user_language="en", progress_emit=lambda *a, **k: None,
        gathered_context=None, english_query="Draft a bail application",
    ))
    assert out == "## draft"
    assert "single" not in seen
    assert len(seen["sections"]) == 6
    assert seen["sections"][0].heading == plan[0].heading
    assert seen["sections"][-1].heading == plan[-1].heading
