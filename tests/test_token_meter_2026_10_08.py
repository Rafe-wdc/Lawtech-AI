"""Cost-meter fixes from the September spend audit (2026-10-08).

Google billed about $7,460 for September; the in-app meter saw $4,450.
Three causes, each pinned here. Pure tests, no network.

1. Streamed answers were metered at in=0 / out=last-chunk: Gemini streams
   usage as per-chunk deltas and core/streaming.py kept only the last one.
2. Raw google-genai calls (Scenario, web fallbacks) never priced the model's
   thinking tokens or counted cached input.
3. Google Search grounding is a per-query charge the meter did not model.
Also: cached input is now billed at the provider's cache-read factor.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.streaming import merge_usage, _stream_with_writer
from core import token_tracker as tt


# ------------------------------------------------------------ 1. streaming
def _chunk(text, **usage):
    return SimpleNamespace(text=text, content=text, usage_metadata=usage or None,
                           response_metadata={"model_name": "gemini-3.8-flash"})


class _FakeChain:
    def __init__(self, chunks):
        self._chunks = chunks

    async def astream(self, inputs):
        for c in self._chunks:
            yield c


def test_merge_usage_sums_flat_and_nested_counters():
    a = {"input_tokens": 22, "output_tokens": 17, "total_tokens": 39, "input_token_details": {"cache_read": 0}}
    b = {"input_tokens": 0, "output_tokens": 26, "total_tokens": 26, "input_token_details": {"cache_read": 5},
         "output_token_details": {"reasoning": 7}}
    m = merge_usage(a, b)
    assert m["input_tokens"] == 22 and m["output_tokens"] == 43 and m["total_tokens"] == 65
    assert m["input_token_details"]["cache_read"] == 5 and m["output_token_details"]["reasoning"] == 7


def test_streamed_usage_is_the_sum_of_chunk_deltas():
    # the exact shape observed live on gemini-3.8-flash on 2026-10-08
    chunks = [
        _chunk("Section 138 ", input_tokens=22, output_tokens=17, total_tokens=39),
        _chunk("of the NI Act ", input_tokens=0, output_tokens=26, total_tokens=26),
        _chunk("makes dishonour ", input_tokens=0, output_tokens=29, total_tokens=29),
        _chunk("an offence.", input_tokens=0, output_tokens=0, total_tokens=0),
        _chunk("", input_tokens=0, output_tokens=3, total_tokens=3),     # usage-only final chunk, no text
    ]
    out = asyncio.run(_stream_with_writer(_FakeChain(chunks), {}, lambda ev: None))
    assert out.usage_metadata["input_tokens"] == 22
    assert out.usage_metadata["output_tokens"] == 75
    assert out.usage_metadata["total_tokens"] == 97
    assert out.content == "Section 138 of the NI Act makes dishonour an offence."
    assert out.response_metadata["model_name"] == "gemini-3.8-flash"


def test_streamed_response_is_priced_by_the_meter():
    tracker = tt.start_request()
    chunks = [_chunk("a", input_tokens=5000, output_tokens=10, total_tokens=5010), _chunk("b", input_tokens=0, output_tokens=390, total_tokens=390)]
    out = asyncio.run(_stream_with_writer(_FakeChain(chunks), {}, lambda ev: None))
    tt.record("Legislation", "generate", out)
    call = tracker.calls[-1]
    assert call.input_tokens == 5000 and call.output_tokens == 400
    assert call.cost_usd > 0, "streamed generation must not be priced at $0"


# ------------------------------------------------------- 2. raw genai shim
def test_record_genai_prices_thinking_and_counts_cache():
    tracker = tt.start_request()
    um = SimpleNamespace(prompt_token_count=6000, candidates_token_count=1500, total_token_count=9500,
                         thoughts_token_count=2000, cached_content_token_count=4000)
    tt.record_genai("Scenario", "web_grounded", SimpleNamespace(usage_metadata=um), model="gemini-3.8-flash")
    assert tracker.reasoning_tokens == 2000 and tracker.cache_read_tokens == 4000
    call = tracker.calls[-1]
    # 2000 non-cached input at $0.75 + 4000 cached at 25% + 3500 output (incl. thinking) at $3.75
    expected = (2000 * 0.75 + 4000 * 0.75 * 0.25 + 3500 * 3.75) / 1_000_000
    assert abs(call.cost_usd - expected) < 1e-9


def test_record_genai_without_thinking_fields_still_works():
    tt.start_request()
    um = SimpleNamespace(prompt_token_count=100, candidates_token_count=50, total_token_count=150)
    assert tt.record_genai("X", "y", SimpleNamespace(usage_metadata=um), model="gemini-3.5-flash-lite") == 150


# ------------------------------------------------------ 3. cache discount
def test_cached_input_is_discounted_per_provider():
    full = tt._estimate_cost_usd("gemini-3.8-flash", 10_000, 0)
    cached = tt._estimate_cost_usd("gemini-3.8-flash", 10_000, 0, cache_read_tokens=10_000)
    assert abs(cached - full * 0.25) < 1e-12
    claude_full = tt._estimate_cost_usd("anthropic:claude-sonnet-5", 10_000, 0)
    claude_cached = tt._estimate_cost_usd("anthropic:claude-sonnet-5", 10_000, 0, cache_read_tokens=10_000)
    assert abs(claude_cached - claude_full * 0.10) < 1e-12
    # cache_read can never exceed input
    assert tt._estimate_cost_usd("gemini-3.8-flash", 100, 0, cache_read_tokens=5_000) == tt._estimate_cost_usd("gemini-3.8-flash", 100, 0, cache_read_tokens=100)


# ---------------------------------------------------------- 4. grounding
def _grounded(queries=None, chunks=1):
    gm = SimpleNamespace(web_search_queries=queries, grounding_chunks=[object()] * chunks)
    return SimpleNamespace(candidates=[SimpleNamespace(grounding_metadata=gm)])


def test_grounding_queries_are_counted_and_charged():
    tracker = tt.start_request()
    resp = _grounded(queries=["anticipatory bail 438", "sushila aggarwal"])
    assert tt.grounding_queries_of(resp) == 2
    added = tt.record_grounding("Scenario", "web_grounded", 2, "gemini-3.8-flash")
    assert abs(added - 0.028) < 1e-9
    assert tracker.grounded_queries == 2 and abs(tracker.cost_usd - 0.028) < 1e-9
    assert tracker.by_agent["Scenario"]["grounded"] == 2
    assert tracker.to_dict()["grounded_queries"] == 2
    assert tracker.calls[-1].step == "web_grounded_grounding"


def test_grounding_rate_by_model_family_and_fallback_count():
    assert tt.grounding_cost_usd("gemini-2.5-flash", 1) == 0.035
    assert tt.grounding_cost_usd("gemini-3.8-flash", 1) == 0.014
    assert tt.grounding_queries_of(_grounded(queries=None, chunks=3)) == 1     # chunks but no query list
    assert tt.grounding_queries_of(_grounded(queries=None, chunks=0)) == 0
    assert tt.grounding_queries_of(SimpleNamespace(candidates=[])) == 0


def test_no_tracker_means_no_charge():
    tt._tracker_var.set(None)
    assert tt.record_grounding("Scenario", "web_grounded", 3, "gemini-3.8-flash") == 0.0


def test_call_sites_charge_grounding():
    import inspect
    import agents.scenario as sc
    import core.agent_fallback as af
    assert "record_grounding(" in inspect.getsource(sc)
    src = inspect.getsource(af)
    assert src.count("_record_grounding(") >= 2
