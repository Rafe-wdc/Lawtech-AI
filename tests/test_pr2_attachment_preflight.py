"""PR 2 — kill the attachment failure class, adapt self-refine to the clock.

4. Attachment preflight in the section-pair prompt builder: estimate the
   assembled prompt, route the upload through retrieval when it will not
   fit, fail fast with AttachmentTooLarge when it still cannot — never let
   Gemini 400 twice per pair per provider.
5. Adaptive self-refine budget: < 120s left -> one critique+refine pass;
   < 60s left -> critique only, no rewrite, violations still returned.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import agents.drafting as d
from agents.drafting import (
    PAIR_INPUT_TOKEN_CEILING,
    AttachmentTooLarge,
    _chars_per_token,
    _estimate_tokens,
    _retrieve_source_for_pair,
)
import core.self_refine as sr


# ---------------------------------------------------------------------------
# Token estimate
# ---------------------------------------------------------------------------

def test_estimate_is_script_aware():
    latin = "word " * 1000            # 5000 chars
    indic = "शब्द " * 1000             # 5000 chars, Devanagari-dominant
    assert _chars_per_token(latin) == 4.0
    assert _chars_per_token(indic) == 2.5
    assert _estimate_tokens(latin) == 1251
    assert _estimate_tokens(indic) == 2001
    assert _estimate_tokens("") == 0


# ---------------------------------------------------------------------------
# Retrieval over the upload
# ---------------------------------------------------------------------------

class _BagEmbed:
    """Deterministic stand-in for the QA embeddings: bag-of-words over a
    fixed vocabulary, so cosine similarity rewards shared words."""
    vocab = ["bail", "fir", "custody", "prayer", "verification", "lease",
             "rent", "tenant", "notice", "cheque", "bank", "dishonour"]

    def _vec(self, text):
        t = text.lower()
        return [float(t.count(w)) for w in self.vocab]

    def embed_documents(self, docs):
        return [self._vec(x) for x in docs]

    def embed_query(self, q):
        return self._vec(q)


def _paragraph(topic_words, i, size=600):
    body = " ".join(topic_words) + " "
    return f"Para {i}. " + (body * (size // max(len(body), 1) + 1))[:size]


def test_retrieval_keeps_relevant_paragraphs_in_order_and_marks_gaps():
    d._CHUNK_EMBED_CACHE.clear()
    paras = [
        _paragraph(["lease", "rent", "tenant"], 1),
        _paragraph(["bail", "fir", "custody"], 2),
        _paragraph(["cheque", "bank", "dishonour"], 3),
        _paragraph(["bail", "custody"], 4),
        _paragraph(["notice", "lease"], 5),
        _paragraph(["prayer", "bail"], 6),
    ]
    facts = "\n\n".join(paras)
    reduced, info = _retrieve_source_for_pair(
        facts, "Grounds for bail. custody fir bail", target_chars=2000,
        embed=_BagEmbed(),
    )
    assert info["mode"] == "retrieval"
    assert info["chunks_total"] == 6
    assert 0 < info["chunks_kept"] < 6
    assert info["chars_after"] <= 2000
    # bail paragraphs win; the lease paragraph does not make the cut
    assert "Para 2." in reduced and "Para 4." in reduced
    assert "Para 1." not in reduced
    # original order preserved, gaps marked
    assert reduced.index("Para 2.") < reduced.index("Para 4.")
    assert "paragraph(s) of the source omitted" in reduced
    assert info["embed_cache"] == "miss"
    # second pair on the same upload hits the cache
    _, info2 = _retrieve_source_for_pair(facts, "prayer", 2000, embed=_BagEmbed())
    assert info2["embed_cache"] == "hit"


def test_retrieval_falls_back_to_head_when_no_paragraph_structure():
    blob = "x" * 50_000  # one chunk, nothing to route over
    reduced, info = _retrieve_source_for_pair(blob, "anything", 10_000, embed=_BagEmbed())
    assert info["mode"] == "head_truncate"
    assert len(reduced) == 10_000


# ---------------------------------------------------------------------------
# Preflight inside _generate_section_pair
# ---------------------------------------------------------------------------

def _stub_pair_env(monkeypatch, captured):
    async def fake_bounded_wait_for(coro, *, local_timeout):
        coro.close()
        return SimpleNamespace(text="## Facts\nbody", response_metadata={})

    class _LLM:
        def invoke(self, msgs):
            return SimpleNamespace(text="x", response_metadata={})

    monkeypatch.setattr("core.deadline.bounded_wait_for", fake_bounded_wait_for)
    monkeypatch.setattr(d, "get_drafting_llm", lambda **kw: _LLM())
    monkeypatch.setattr(d, "localize_prompt", lambda p, *a, **k: p)
    monkeypatch.setattr("core.token_tracker.record", lambda *a, **k: None)
    monkeypatch.setattr(d, "get_drafting_llm", lambda **kw: _LLM())

    # Capture what the retrieval path is asked for, and use the bag embedder.
    real = d._retrieve_source_for_pair

    def spy(user_facts, query_text, target_chars, embed=None):
        captured["target_chars"] = target_chars
        return real(user_facts, query_text, target_chars, embed=_BagEmbed())

    monkeypatch.setattr(d, "_retrieve_source_for_pair", spy)


def _call_pair(user_facts, ref="ref"):
    sec = SimpleNamespace(id="facts", heading="Facts", summary="bail custody fir")
    return d._generate_section_pair(
        sections_to_write=[sec], section_position_start=1, total_sections=3,
        query="Draft a bail application", user_facts=user_facts,
        reference_draft=ref, prior_text="", gathered_context=None,
        user_intent=None, user_language="en",
    )


def test_small_upload_is_untouched(monkeypatch):
    captured = {}
    _stub_pair_env(monkeypatch, captured)
    text = asyncio.run(_call_pair("Para 1. bail custody fir\n\nPara 2. prayer"))
    assert text.startswith("## Facts")
    assert "target_chars" not in captured  # retrieval never ran


def test_oversized_upload_is_routed_through_retrieval(monkeypatch):
    captured = {}
    _stub_pair_env(monkeypatch, captured)
    d._CHUNK_EMBED_CACHE.clear()
    # ~4.4M chars of Latin text ≈ 1.1M tokens: over the 900K ceiling.
    paras = [_paragraph(["bail", "custody"] if i % 3 == 0 else ["lease", "rent"], i, 1500)
             for i in range(3000)]
    huge = "\n\n".join(paras)
    assert _estimate_tokens(huge) > PAIR_INPUT_TOKEN_CEILING
    text = asyncio.run(_call_pair(huge))
    assert text.startswith("## Facts")
    # retrieval was asked for a budget that fits under the ceiling
    assert 0 < captured["target_chars"] < len(huge)
    assert captured["target_chars"] <= PAIR_INPUT_TOKEN_CEILING * 4 * 0.9


def test_unfittable_prompt_fails_fast_without_an_llm_call(monkeypatch):
    captured = {}
    calls = []

    async def counting_bounded_wait_for(coro, *, local_timeout):
        calls.append(1)
        coro.close()
        return SimpleNamespace(text="x", response_metadata={})

    _stub_pair_env(monkeypatch, captured)
    monkeypatch.setattr("core.deadline.bounded_wait_for", counting_bounded_wait_for)
    # The reference draft alone blows the ceiling; there is no upload to trim.
    big_ref = "r" * (PAIR_INPUT_TOKEN_CEILING * 4 + 100_000)
    with pytest.raises(AttachmentTooLarge) as ei:
        asyncio.run(_call_pair("", ref=big_ref))
    assert "attachment_too_large" in str(ei.value)
    assert calls == []  # never reached the model


# ---------------------------------------------------------------------------
# Adaptive self-refine budget
# ---------------------------------------------------------------------------

def _failing_critique():
    return sr.Critique(
        passes=False, confidence=0.95,
        violations=[sr.Violation(field="prayer", issue="Relief mismatch",
                                 severity="major", suggested_fix="fix")],
    )


def _passing_critique():
    return sr.Critique(passes=True, confidence=0.95, violations=[])


def _run_refine(monkeypatch, remaining, critiques, max_iterations=2):
    refine_calls = []
    it = iter(critiques)

    async def fake_critique(*a, **k):
        return next(it)

    async def fake_refine(user_query, intent, current, critique, llm, **k):
        refine_calls.append(1)
        return current + " refined"

    monkeypatch.setattr(sr, "_critique", fake_critique)
    monkeypatch.setattr(sr, "_refine", fake_refine)
    monkeypatch.setattr(sr, "_deadline_remaining", lambda: remaining)
    # An intent with an explicit directive, otherwise self_refine's
    # skip-when-trivial path returns before the loop.
    from config.intent import UserIntent
    intent = UserIntent(format_explicit=True, confidence=0.9)
    out, history = asyncio.run(sr.self_refine(
        "x" * 3000, user_query="q", intent=intent, max_iterations=max_iterations,
        critic_llm=object(), refiner_llm=object(),
    ))
    return out, history, refine_calls


def test_under_60s_runs_critique_only_and_returns_violations(monkeypatch):
    out, history, refine_calls = _run_refine(
        monkeypatch, remaining=45.0,
        critiques=[_failing_critique(), _passing_critique()])
    assert refine_calls == []
    assert len(history) == 1 and not history[-1].passes
    assert out == "x" * 3000


def test_under_120s_allows_exactly_one_refine_pass(monkeypatch):
    out, history, refine_calls = _run_refine(
        monkeypatch, remaining=100.0,
        critiques=[_failing_critique(), _failing_critique(), _failing_critique()])
    assert len(refine_calls) == 1
    assert out.endswith(" refined")
    assert len(history) == 2  # critique, refine, closing critique


def test_healthy_budget_keeps_the_full_loop(monkeypatch):
    out, history, refine_calls = _run_refine(
        monkeypatch, remaining=250.0,
        critiques=[_failing_critique(), _failing_critique(), _failing_critique()])
    assert len(refine_calls) == 2
    assert len(history) == 3


def test_no_deadline_means_no_adaptation(monkeypatch):
    out, history, refine_calls = _run_refine(
        monkeypatch, remaining=None,
        critiques=[_failing_critique(), _failing_critique(), _failing_critique()])
    assert len(refine_calls) == 2
