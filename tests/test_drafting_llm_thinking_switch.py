"""get_drafting_llm(thinking=False) turns Claude's thinking off.

The self-refine critic uses with_structured_output (forced tool calling),
which Claude does not honour while thinking is on: measured 2026-10-16, the
critic ran ~89 s and raised OutputParserException on every Claude draft.
"""

import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import core.clients as clients


def _captured_kwargs(monkeypatch, **call):
    seen = []

    class _Fake:
        def with_fallbacks(self, fbs):
            return self

    def fake_init(model, **kw):
        seen.append((model, kw))
        return _Fake()

    monkeypatch.setitem(clients.GEMINI_MODELS, "generation", "anthropic:claude-sonnet-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(clients, "init_chat_model", fake_init)
    clients.get_drafting_llm(max_output_tokens=8192, **call)
    return next(kw for m, kw in seen if m.startswith("anthropic:"))


def test_default_keeps_adaptive_thinking(monkeypatch):
    assert _captured_kwargs(monkeypatch)["thinking"] == {"type": "adaptive"}


def test_thinking_false_disables_it(monkeypatch):
    kw = _captured_kwargs(monkeypatch, thinking=False)
    assert kw["thinking"] == {"type": "disabled"}
    assert "output_config" not in kw


def test_drafting_self_refine_uses_thinking_off():
    import inspect
    import agents.drafting as d
    src = inspect.getsource(d)
    assert "critic_llm=get_drafting_llm(max_output_tokens=8192, thinking=False)" in src
    assert "refiner_llm=get_drafting_llm(max_output_tokens=24000, thinking=False)" in src
    assert "max_iterations=1," in src     # one rewrite round fits the budget
