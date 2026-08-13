"""Regression test: an unparseable critique must not be reported as a pass.

THE BUG (observed in production, 2026-08-13)
============================================
`_critique` builds its LLM with `with_structured_output(Critique,
include_raw=True)`, which returns a dict:

    {"raw": AIMessage, "parsed": Critique | None, "parsing_error": Exception | None}

`parsed` is **None** when the model's output does not validate against the
schema. The code did:

    result: Critique = raw_and_parsed["parsed"]
    ...
    passes=result.passes            # ← AttributeError on None

The surrounding `except Exception` caught that and logged the generic
"Critique LLM call failed", so:

  * the real cause — a schema violation, described in `parsing_error` —
    was never logged, and
  * the fail-open Critique(passes=True) was indistinguishable from a real
    pass, so the refine loop logged **"Self-refine passed"**.

What that hid, in the actual incident: the critic found 8 violations
(1 critical), the refiner grew the draft by 1,288 characters, the
*verification* critique then failed to parse — and the unverified draft
shipped under a success message.

WHAT IS ASSERTED HERE
=====================
  * `parsed=None` returns a Critique instead of raising
  * `parsing_error` is surfaced, not swallowed
  * every fail-open path is marked so callers can tell it apart
  * the refine loop logs UNVERIFIED rather than "passed"

Fail-open itself is preserved deliberately — a critic outage must not block
a user's response. The fix makes it *visible*, not fatal.

Run:
    pytest tests/test_self_refine_unparseable.py -v
    python tests/test_self_refine_unparseable.py     # no pytest required
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
from unittest.mock import patch

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("OPENAI_API_KEY", "test-key-unused")
os.environ.setdefault("GOOGLE_API_KEY", "test-key-unused")
os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://localhost:1")

import core.self_refine as sr  # noqa: E402
from core.self_refine import Critique, _UNVERIFIED_NOTE  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class _FakeChain:
    """Stands in for `prompt | llm`, returning what LangChain really returns."""

    def __init__(self, payload):
        self._payload = payload

    def __or__(self, other):
        return self

    def invoke(self, _inputs):
        return self._payload


def _critique_with(payload) -> Critique:
    """Drive `_critique` with a canned structured-output payload."""
    from config.intent import UserIntent

    fake = _FakeChain(payload)

    class _FakeLLM:
        def with_structured_output(self, *a, **k):
            return fake

    # The circuit-breaker helpers are imported *inside* `_critique`
    # (`from core.clients import ...`), so they must be patched at their
    # definition module — patching them as attributes of `core.self_refine`
    # silently misses.
    import core.clients as clients

    with patch.object(sr, "get_gemini_flash_full", lambda **k: _FakeLLM()), \
         patch.object(sr, "ChatPromptTemplate") as tmpl, \
         patch.object(clients, "record_gemini_flash_success", lambda: None), \
         patch.object(clients, "record_gemini_flash_failure", lambda: None), \
         patch.object(clients, "is_gemini_flash_available", lambda: True), \
         patch.object(sr, "_record_tokens", lambda *a, **k: None):
        tmpl.from_template.return_value = fake
        return _run(sr._critique("q", UserIntent(), "a response", None))


class TestUnparseableCritique:

    def test_none_parsed_does_not_raise(self):
        """The exact production payload: parse failed, `parsed` is None."""
        result = _critique_with({
            "raw": type("M", (), {"content": "not valid json"})(),
            "parsed": None,
            "parsing_error": ValueError("field 'passes' required"),
        })
        assert isinstance(result, Critique), "must return a Critique, not raise"

    def test_unparseable_is_marked_unverified(self):
        result = _critique_with({
            "raw": type("M", (), {"content": "garbage"})(),
            "parsed": None,
            "parsing_error": ValueError("schema mismatch"),
        })
        # Fail-open is intentional — but it must be labelled.
        assert result.passes is True
        assert result.confidence == 0.0
        assert result.overall_quality_notes == _UNVERIFIED_NOTE

    def test_valid_parse_passes_through_untouched(self):
        good = Critique(passes=True, confidence=0.91,
                        overall_quality_notes="looks fine")
        result = _critique_with({"raw": None, "parsed": good,
                                 "parsing_error": None})
        assert result.confidence == 0.91
        assert result.overall_quality_notes != _UNVERIFIED_NOTE

    def test_parsing_error_is_surfaced_not_swallowed(self):
        src = inspect.getsource(sr._critique)
        assert "parsing_error" in src, (
            "the reason for the schema failure must reach the logs — it was "
            "invisible before this fix")


class TestFailOpenPathsAreLabelled:
    """All three fail-open exits must be distinguishable from a real pass."""

    def test_every_fail_open_uses_the_marker(self):
        src = inspect.getsource(sr._critique)
        # circuit-open, unparseable, and generic-exception returns
        assert src.count("_UNVERIFIED_NOTE") >= 3, (
            "a fail-open path is still returning an unlabelled pass")

    def test_loop_distinguishes_unverified_from_passed(self):
        src = inspect.getsource(sr.self_refine)
        assert "_UNVERIFIED_NOTE" in src, "the loop cannot tell them apart"
        assert "UNVERIFIED" in src, (
            "an unverified result must not log as 'Self-refine passed'")

    def test_fail_open_behaviour_is_preserved(self):
        """A critic outage must still not block the user's response."""
        result = _critique_with({
            "raw": None, "parsed": None, "parsing_error": ValueError("x"),
        })
        assert result.passes is True, (
            "fail-open is deliberate — the fix makes it visible, not fatal")


# --- Standalone runner (venv has no pytest) ---------------------------------

if __name__ == "__main__":
    failures = 0
    for cls in (TestUnparseableCritique, TestFailOpenPathsAreLabelled):
        inst = cls()
        for name in sorted(n for n in dir(inst) if n.startswith("test_")):
            try:
                getattr(inst, name)()
                print(f"  PASS  {cls.__name__}.{name}")
            except Exception as e:
                failures += 1
                print(f"  FAIL  {cls.__name__}.{name}: {type(e).__name__}: {e}")
    print("\nALL PASSED" if not failures else f"\n{failures} FAILURE(S)")
    sys.exit(1 if failures else 0)
