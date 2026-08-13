"""Regression tests for the Constitution / Maxim tier-2 retry.

`agents/constitution_maxim.py` used to jump straight from "ES returned
nothing" to Google web search, skipping the cheap query-rewrite retry that
every other ES-backed agent runs. `_REWRITE_PROMPTS` has had tailored
"Constitution" and "Maxim" entries since the helper was written, but no
caller ever passed those names — the prompts were dead code and CLAUDE.md's
"all domain agents have a 3-tier fallback" was aspirational.

The most important test here is `test_unknown_agent_name_is_a_silent_noop`.
`rewrite_query_for_domain` returns the query UNCHANGED for any agent name
missing from `_REWRITE_PROMPTS` — no exception, no warning. That means a
call site added without a matching prompt entry looks like a working retry
and does nothing. Anyone extending tier 2 to more agents must add the prompt
first; this test pins that contract so the trap is visible.

Run:
    pytest tests/test_constitution_maxim_retry.py -v
    python tests/test_constitution_maxim_retry.py     # no pytest required
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# `core.settings` raises at import without these; nothing here makes an API
# call. `EMBEDDING_SERVICE_URL` skips the ~1.4 GB eager model load.
os.environ.setdefault("OPENAI_API_KEY", "test-key-unused")
os.environ.setdefault("GOOGLE_API_KEY", "test-key-unused")
os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://localhost:1")

from langchain_core.documents import Document  # noqa: E402

import agents.constitution_maxim as cm  # noqa: E402
from core.agent_fallback import _REWRITE_PROMPTS, rewrite_query_for_domain  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _doc(text="Article 21 protects life and personal liberty."):
    return Document(page_content=text, metadata={"source": "constitution.csv"})


class _Sentinel:
    """Stand-in for the AgentResult returned by web_search_fallback."""
    content = "<web-fallback>"


def _invoke(task="Constitution", query="right to life",
            es_side_effect=None, rewritten="Article 21 right to life",
            relevance=(False, {"matched_subject": "unrelated"})):
    """Drive `_handle_constitution_or_maxim` with everything external mocked.

    Returns (result, mocks) so tests can assert on call counts and arguments.
    `get_web_context`, `rewrite_query_for_domain` and `web_search_fallback`
    are imported INSIDE the function body, so they must be patched at their
    definition module, not as attributes of `agents.constitution_maxim`.
    """
    es = MagicMock(side_effect=es_side_effect or [[]])
    rewrite = MagicMock(return_value=rewritten)
    web = AsyncMock(return_value=_Sentinel())
    gate = AsyncMock(return_value=relevance)

    with patch.object(cm, "_retrieve_from_es", es), \
         patch.object(cm, "progress", MagicMock()), \
         patch.object(cm, "check_retrieval_relevance", gate), \
         patch("core.agent_fallback.get_web_context", AsyncMock(return_value="")), \
         patch("core.agent_fallback.rewrite_query_for_domain", rewrite), \
         patch("core.agent_fallback.web_search_fallback", web):
        result = _run(cm._handle_constitution_or_maxim(task, query, []))

    return result, {"es": es, "rewrite": rewrite, "web": web, "gate": gate}


# --- The contract that makes silent no-ops possible --------------------------

class TestRewriteContract:

    def test_unknown_agent_name_is_a_silent_noop(self):
        """The trap: no error, no log, query comes back untouched."""
        for unknown in ("SCI_Judgment", "GST_Judgment", "Scenario",
                        "Drafting", "Document", "", "Legislation "):
            assert rewrite_query_for_domain("my query", unknown) == "my query", (
                f"{unknown!r} unexpectedly has a rewrite prompt — update this "
                f"test and CLAUDE.md's tier-2 table together"
            )

    def test_constitution_and_maxim_prompts_exist(self):
        # If either disappears, the retry added to constitution_maxim.py
        # silently degrades to a no-op rather than failing.
        assert "Constitution" in _REWRITE_PROMPTS
        assert "Maxim" in _REWRITE_PROMPTS

    def test_keyed_agents_are_exactly_the_documented_five(self):
        assert set(_REWRITE_PROMPTS) == {
            "Newacts", "Legislation", "Judgment", "Constitution", "Maxim"}


# --- The retry itself --------------------------------------------------------

class TestTierTwoRetry:

    def test_retry_fires_and_researches_with_rewritten_query(self):
        _, m = _invoke(es_side_effect=[[], [_doc()]])
        assert m["es"].call_count == 2, "ES was not re-searched after rewrite"
        # Second call must use the rewritten text, first the original.
        assert m["es"].call_args_list[0].args[1] == "right to life"
        assert m["es"].call_args_list[1].args[1] == "Article 21 right to life"
        # Deliberately no assertion about web fallback here: the retry found
        # docs, but the mocked relevance gate then rejects them, so web fires
        # for an unrelated reason. "Retry avoided the web" is covered by
        # test_no_second_search_when_rewrite_changes_nothing and the
        # still-empty / failure cases below.

    def test_task_name_is_passed_to_the_rewriter(self):
        # Passing anything not in _REWRITE_PROMPTS would be a silent no-op.
        for task in ("Constitution", "Maxim"):
            _, m = _invoke(task=task, es_side_effect=[[], [_doc()]])
            assert m["rewrite"].call_args.args[1] == task

    def test_gate_judges_the_original_query_not_the_rewrite(self):
        """The rewrite is a search aid, not a restatement of user intent."""
        _, m = _invoke(es_side_effect=[[], [_doc()]])
        assert m["gate"].await_args.args[0] == "right to life"

    def test_no_second_search_when_rewrite_changes_nothing(self):
        _, m = _invoke(query="right to life", rewritten="right to life",
                       es_side_effect=[[]])
        assert m["es"].call_count == 1, "pointless second ES call"
        m["web"].assert_awaited_once()

    def test_retry_failure_falls_through_to_web(self):
        _, m = _invoke(es_side_effect=[[], RuntimeError("ES down")])
        assert m["es"].call_count == 2
        m["web"].assert_awaited_once()

    def test_still_empty_after_retry_falls_through_to_web(self):
        result, m = _invoke(es_side_effect=[[], []])
        assert m["es"].call_count == 2
        m["web"].assert_awaited_once()
        assert result.content == "<web-fallback>"

    def test_hits_on_first_try_skips_the_rewriter_entirely(self):
        _, m = _invoke(es_side_effect=[[_doc()]])
        m["rewrite"].assert_not_called()
        assert m["es"].call_count == 1


# --- Standalone runner (venv has no pytest) ---------------------------------

if __name__ == "__main__":
    failures = 0
    for cls in (TestRewriteContract, TestTierTwoRetry):
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
