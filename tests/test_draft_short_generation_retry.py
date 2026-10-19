"""Single-pass generation retries a draft that came back as a skeleton.

Dev 2026-10-19: 1 of 9 identical legal-notice requests returned 419 chars —
letterhead, To / From, then the signature, no body — and self_refine (which
skips anything under 500 chars) let it ship. No network: the LLM is faked.
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

from langchain_core.messages import AIMessage

import agents.drafting as drafting

SKELETON = ("**REGISTERED POST A.D.**\n\n## LEGAL NOTICE\n\n**To:**\n\nSuresh Patil\n\n"
            "**From:**\n\nAdvocate [Advocate's Name]\n\n---\n\n**Advocate [Advocate's Name]**")
FULL = SKELETON.replace("---", "1. That my client is the owner of the shop.\n\n" * 40)


class _FakeLLM:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def invoke(self, messages):
        self.calls.append(messages)
        return self.replies.pop(0)


def _msg(text):
    return AIMessage(
        content=[{"type": "thinking", "thinking": "x" * 3000}, {"type": "text", "text": text}],
        response_metadata={"model_name": "claude-sonnet-5", "stop_reason": "end_turn"},
        usage_metadata={"input_tokens": 100, "output_tokens": 5800, "total_tokens": 5900},
    )


def _generate(monkeypatch, replies):
    fake = _FakeLLM(replies)
    monkeypatch.setattr(drafting, "get_drafting_llm", lambda **kw: fake)
    text = asyncio.run(drafting._generate_single_pass(
        query="Draft a legal notice for unpaid rent", user_facts="", reference_draft="",
        user_intent=None, user_language="en", progress_emit=lambda *a, **k: None,
    ))
    return text, fake


def test_skeleton_draft_is_retried_and_the_full_one_kept(monkeypatch):
    text, fake = _generate(monkeypatch, [_msg(SKELETON), _msg(FULL)])
    assert len(fake.calls) == 2
    assert text == FULL
    assert "Return the COMPLETE document" in fake.calls[1][1].content


def test_a_worse_retry_does_not_replace_the_first_answer(monkeypatch):
    text, fake = _generate(monkeypatch, [_msg(SKELETON), _msg("short")])
    assert len(fake.calls) == 2 and text == SKELETON


def test_a_full_draft_is_not_retried(monkeypatch):
    text, fake = _generate(monkeypatch, [_msg(FULL)])
    assert len(fake.calls) == 1 and text == FULL


def test_response_shape_names_the_model_and_blocks():
    shape = drafting._response_shape(_msg(SKELETON))
    assert shape["model"] == "claude-sonnet-5"
    assert shape["finish_reason"] == "end_turn"
    assert shape["output_tokens"] == 5800
    assert shape["blocks"] == ["thinking:3000", f"text:{len(SKELETON)}"]
