"""PR 1 — stop the runaway section pair.

Three changes, three groups of tests:

1. Per-pair total budget: one clock per pair, created in the caller, shared
   by every attempt and failover. The clamp must read strictly decreasing
   values across attempt 1 -> Flash failover -> attempt 2 -> Flash failover,
   and refuse to start a call once the pair budget is gone.
2. Language-gate tolerance: a draft at a raw script ratio just under 0.85
   is NOT off-target (it was being regenerated for 54s), while the measured
   failures (<= 0.60) still are.
3. Unresolved review notes: when self_refine ends with the critic still
   failing, the shipped draft carries a visible banner.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from agents.drafting import (
    PAIR_TOTAL_BUDGET_S,
    _PAIR_RETRY_MIN_REMAINING_S,
    _UNRESOLVED_REVIEW_BANNER_MARKER,
    _make_pair_clock,
    _unresolved_review_banner,
)
from core.language import (
    OFF_TARGET_SCRIPT_THRESHOLD,
    _OFF_TARGET_TOLERANCE,
    is_off_target_language,
    output_script_ratio,
)


# ---------------------------------------------------------------------------
# 1. Pair clock
# ---------------------------------------------------------------------------

def test_pair_clock_counts_down_from_budget_and_floors_at_zero(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    remaining = _make_pair_clock(90)
    assert remaining() == 90
    now[0] += 30
    assert remaining() == 60
    now[0] += 100
    assert remaining() == 0  # never negative


def test_pair_clock_is_strictly_decreasing_across_all_four_calls(monkeypatch):
    """attempt 1 primary -> flash failover -> attempt 2 primary -> flash
    failover on the SAME pair must see the same clock, i.e. strictly
    decreasing readings. A clock created inside the pair function would
    reset on attempt 2 and this sequence would go back up."""
    now = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    remaining = _make_pair_clock(90)
    readings = []
    for stage_cost in (25, 20, 25, 20):  # seconds each call took
        readings.append(remaining())
        now[0] += stage_cost
    assert readings == [90, 65, 45, 20]
    assert all(a > b for a, b in zip(readings, readings[1:]))
    assert remaining() == 0


def test_default_budget_bounds_the_worst_case_below_the_request_budget():
    # Four 60s calls used to cost 240s of a 285s request. The cap must be
    # well under that so a stalled pair cannot starve the sections after it.
    assert PAIR_TOTAL_BUDGET_S <= 120
    assert _PAIR_RETRY_MIN_REMAINING_S < PAIR_TOTAL_BUDGET_S


def test_pair_timeout_clamps_and_refuses_once_budget_is_gone(monkeypatch):
    """Drive `_generate_section_pair`'s clamp through a stubbed LLM: the
    per-call timeout passed to bounded_wait_for must be min(60, remaining),
    and a call must not start at zero remaining."""
    import agents.drafting as d

    seen_timeouts: list[float] = []

    async def fake_bounded_wait_for(coro, *, local_timeout):
        seen_timeouts.append(local_timeout)
        coro.close()
        return SimpleNamespace(text="## Heading\nbody", response_metadata={})

    monkeypatch.setattr("core.deadline.bounded_wait_for", fake_bounded_wait_for)

    class _LLM:
        def invoke(self, msgs):  # never reached — bounded_wait_for is faked
            return SimpleNamespace(text="x", response_metadata={})

    monkeypatch.setattr(d, "get_drafting_llm", lambda **kw: _LLM())
    monkeypatch.setattr(d, "localize_prompt", lambda p, *a, **k: p)
    monkeypatch.setattr("core.token_tracker.record", lambda *a, **k: None)

    now = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    remaining = _make_pair_clock(90)
    now[0] += 50  # 40s left of the pair budget

    sec = SimpleNamespace(id="facts", heading="Facts", summary="facts")

    def _call():
        return d._generate_section_pair(
            sections_to_write=[sec], section_position_start=1, total_sections=3,
            query="q", user_facts="", reference_draft="ref", prior_text="",
            gathered_context=None, user_intent=None, user_language="en",
            pair_remaining=remaining,
        )

    text = asyncio.run(_call())
    assert text.startswith("## Heading")
    assert seen_timeouts == [40]  # clamped below the 60s local timeout

    now[0] += 40  # pair budget exhausted
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(_call())


# ---------------------------------------------------------------------------
# 2. Language-gate tolerance
# ---------------------------------------------------------------------------

def _mixed(native: str, latin_words: int) -> str:
    return native + " " + " ".join(["Section"] * latin_words)


def test_gate_effective_threshold_is_inside_the_calibration_gap():
    effective = OFF_TARGET_SCRIPT_THRESHOLD - _OFF_TARGET_TOLERANCE
    assert 0.60 < effective < 0.85
    assert abs(effective - 0.83) < 1e-9


def test_draft_just_under_085_is_not_off_target():
    # 100 Devanagari letters + 17 latin letters -> ratio 0.855; add a bit of
    # latin to land at ~0.84 (raw), which used to trigger regeneration.
    native = "क" * 100
    draft = native + " " + "Sec" * 6 + " " + "x"  # 100 / (100 + 19) = 0.840
    ratio = output_script_ratio(draft, "mr")
    assert 0.83 <= ratio < 0.85, ratio
    assert not is_off_target_language(draft, "mr")


def test_measured_failures_are_still_caught():
    native = "క" * 60
    draft = native + " " + "x" * 40  # 0.60 — the worst measured failure
    assert output_script_ratio(draft, "te") <= 0.60
    assert is_off_target_language(draft, "te")
    assert is_off_target_language("Entirely English text here", "gu")


# ---------------------------------------------------------------------------
# 3. Unresolved review banner
# ---------------------------------------------------------------------------

def _crit(passes: bool, *violations):
    return SimpleNamespace(passes=passes, violations=list(violations))


def _viol(field: str, issue: str, severity: str = "major"):
    return SimpleNamespace(field=field, issue=issue, severity=severity)


def test_no_banner_when_history_empty_or_final_passes():
    assert _unresolved_review_banner([]) == ""
    assert _unresolved_review_banner([_crit(True)]) == ""
    assert _unresolved_review_banner([_crit(False), _crit(True)]) == ""


def test_banner_lists_unresolved_violations_most_severe_first():
    history = [
        _crit(False, _viol("prayer", "Relief does not match grounds")),
        _crit(False,
              _viol("verification", "Missing deponent paragraph", "minor"),
              _viol("facts", "FIR number differs from source", "critical"),
              _viol("prayer", "Relief does not match grounds")),
    ]
    banner = _unresolved_review_banner(history)
    assert banner.startswith("> ⚠ ")
    assert _UNRESOLVED_REVIEW_BANNER_MARKER in banner
    assert "3 point(s)" in banner
    lines = [l for l in banner.splitlines() if l.startswith("> - ")]
    assert lines[0].startswith("> - facts:")           # critical first
    assert lines[-1].startswith("> - verification:")   # minor last
    assert banner.endswith("\n\n")


def test_banner_caps_at_four_lines_and_counts_the_rest():
    vs = [_viol(f"f{i}", f"issue {i}") for i in range(6)]
    banner = _unresolved_review_banner([_crit(False, *vs)])
    listed = [l for l in banner.splitlines() if l.startswith("> - ")]
    assert len(listed) == 5
    assert listed[-1] == "> - and 2 more"
