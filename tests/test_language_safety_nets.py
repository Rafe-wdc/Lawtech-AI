"""Forced-failure tests for the two language safety nets.

Neither net fired in any live run — which is a good sign, but a safety net
nobody has seen catch anything is not a safety net. These force the exact
failure each was built for and assert it is caught.

Net 1 — revert after self-refine.
    Real incident (Odia, req 65f6dfd1): the generator produced 9,740 chars in
    Odia and the refiner returned 12,411 chars of English. Root cause was
    REFINE_PROMPT carrying no instruction to write in the user's language,
    only a long rule about what to keep in English. That is fixed at source;
    this net catches any recurrence.

Net 2 — generation-time script gate.
    Catches the generator itself returning the wrong language, with a
    budget-bounded single retry and a warning banner when the budget is gone.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from config.intent import default_intent
from core.language import is_off_target_language, output_script_ratio

ODIA_DRAFT = (
    "## ମାମଲାର ଶୀର୍ଷକ\n"
    "ମାନ୍ୟବର ସେସନ୍ସ ଅଦାଲତରେ, [ସ୍ଥାନ]\n\n"
    "## ମାମଲାର ତଥ୍ୟ\n"
    "ଆବେଦନକାରୀ ନିର୍ଦ୍ଦୋଷ ଅଟନ୍ତି ଏବଂ ତାଙ୍କୁ ମିଥ୍ୟା ଭାବରେ ଜଡିତ କରାଯାଇଛି।\n\n"
    "## ଜାମିନ ପାଇଁ ଆଧାର\n"
    "ଆବେଦନକାରୀଙ୍କର କୌଣସି ଅପରାଧିକ ପୃଷ୍ଠଭୂମି ନାହିଁ।\n\n"
    "## ପ୍ରାର୍ଥନା\n"
    "ଆବେଦନକାରୀଙ୍କୁ ଜାମିନ ପ୍ରଦାନ କରାଯାଉ।\n\n"
    "## ଚକାସଣୀ\n"
    "ଉପରୋକ୍ତ ବିବରଣୀ ସତ୍ୟ ଅଟେ।\n"
)

ENGLISH_DRAFT = (
    "## CAUSE TITLE\n"
    "IN THE COURT OF THE HON'BLE SESSIONS JUDGE AT [PLACE]\n\n"
    "## FACTS OF THE CASE\n"
    "The applicant is innocent and has been falsely implicated in this matter.\n\n"
    "## GROUNDS FOR BAIL\n"
    "The applicant has no criminal antecedents whatsoever.\n\n"
    "## PRAYER\n"
    "It is prayed that the applicant be released on bail.\n\n"
    "## VERIFICATION\n"
    "The contents stated above are true to the best of my knowledge.\n"
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _state(query: str, lang: str) -> dict:
    intent = default_intent()
    intent.language = lang
    return {
        "query": query, "original_query": query,
        "agent_queries": {"Drafting": query},
        "user_context": "", "user_language": lang,
        "user_intent": intent,
        "chat_history": [], "agent_results": {},
    }


# --- sanity: the fixtures are what the tests assume ------------------------

def test_fixtures_are_what_they_claim():
    assert output_script_ratio(ODIA_DRAFT, "or") > 0.9
    assert not is_off_target_language(ODIA_DRAFT, "or")
    assert output_script_ratio(ENGLISH_DRAFT, "or") == 0.0
    assert is_off_target_language(ENGLISH_DRAFT, "or")


# --- Net 1: revert after self-refine ---------------------------------------

class TestRevertAfterRefine:
    """Force the refiner to return English and confirm the draft is reverted."""

    def test_refiner_returning_english_is_reverted(self):

        async def fake_generate(**kwargs):
            return ODIA_DRAFT

        async def fake_acquire(*a, **kw):
            return ("reference template text", "/corpus/bail.csv", "es", "english query")

        # The exact incident: refiner hands back a fully English document.
        async def fake_refine(response, user_query, intent, **kw):
            return ENGLISH_DRAFT, []

        # The project logger writes structured lines straight to a stream it
        # binds at import, so neither caplog nor capfd sees them. Record the
        # calls instead.
        import agents.drafting as drafting_mod
        warnings: list[str] = []
        real_warning = drafting_mod.log.warning

        def record(msg, *a, **kw):
            warnings.append(str(msg))
            return real_warning(msg, *a, **kw)

        with patch("agents.drafting._acquire_reference_draft", fake_acquire), \
             patch("agents.drafting._generate_draft", fake_generate), \
             patch("agents.drafting.self_refine", fake_refine), \
             patch.object(drafting_mod.log, "warning", record), \
             patch("agents.drafting._gather_relevant_context",
                   new=lambda *a, **kw: _completed({})):
            from agents.drafting import drafting_node
            out = _run(drafting_node(_state("ଜାମିନ ଆବେଦନ ପ୍ରସ୍ତୁତ କରନ୍ତୁ", "or")))

        content = out["agent_results"]["Drafting"].content
        # The Odia draft survived; the English refinement was discarded.
        assert not is_off_target_language(content, "or"), (
            f"expected the pre-refine Odia draft, got script ratio "
            f"{output_script_ratio(content, 'or'):.2f}"
        )
        assert "ପ୍ରାର୍ଥନା" in content
        assert "IN THE COURT OF THE HON'BLE SESSIONS JUDGE" not in content
        assert any("changed the draft language" in w for w in warnings), \
            f"expected the revert warning to be logged, saw: {warnings}"

    def test_legitimate_refinement_is_kept(self):
        """The net must not fire when the refiner behaves — otherwise every
        refinement is silently thrown away."""
        improved = ODIA_DRAFT + "\n## ଅତିରିକ୍ତ ଆଧାର\nଅଧିକ ବିବରଣୀ ଏଠାରେ ଅଛି।\n"

        async def fake_generate(**kwargs):
            return ODIA_DRAFT

        async def fake_acquire(*a, **kw):
            return ("reference", "/corpus/bail.csv", "es", "english query")

        async def fake_refine(response, user_query, intent, **kw):
            return improved, []

        with patch("agents.drafting._acquire_reference_draft", fake_acquire), \
             patch("agents.drafting._generate_draft", fake_generate), \
             patch("agents.drafting.self_refine", fake_refine), \
             patch("agents.drafting._gather_relevant_context",
                   new=lambda *a, **kw: _completed({})):
            from agents.drafting import drafting_node
            out = _run(drafting_node(_state("ଜାମିନ ଆବେଦନ", "or")))

        content = out["agent_results"]["Drafting"].content
        assert "ଅତିରିକ୍ତ ଆଧାର" in content, "a valid refinement must be kept"

    def test_english_requests_are_untouched(self):
        """An English request must never trip a language revert."""
        async def fake_generate(**kwargs):
            return ENGLISH_DRAFT

        async def fake_acquire(*a, **kw):
            return ("reference", "/corpus/bail.csv", "es", "english query")

        refined = ENGLISH_DRAFT + "\n## ADDITIONAL GROUNDS\nMore detail here.\n"

        async def fake_refine(response, user_query, intent, **kw):
            return refined, []

        with patch("agents.drafting._acquire_reference_draft", fake_acquire), \
             patch("agents.drafting._generate_draft", fake_generate), \
             patch("agents.drafting.self_refine", fake_refine), \
             patch("agents.drafting._gather_relevant_context",
                   new=lambda *a, **kw: _completed({})):
            from agents.drafting import drafting_node
            out = _run(drafting_node(_state("Draft a bail application", "en")))

        assert "ADDITIONAL GROUNDS" in out["agent_results"]["Drafting"].content


def _completed(value):
    fut = asyncio.Future()
    fut.set_result(value)
    return fut


# --- Net 2: generation-time script gate ------------------------------------

KANNADA_DRAFT = (
    "## ಮಾನ್ಯ ಸೆಷನ್ಸ್ ನ್ಯಾಯಾಲಯದ ಮುಂದೆ\n"
    "ಅರ್ಜಿದಾರರು ನಿರಪರಾಧಿಯಾಗಿದ್ದಾರೆ ಎಂದು ವಿನಮ್ರವಾಗಿ ಸಲ್ಲಿಸಲಾಗಿದೆ.\n\n"
    "## ಜಾಮೀನಿಗಾಗಿ ಆಧಾರಗಳು\n"
    "ಅರ್ಜಿದಾರರಿಗೆ ಯಾವುದೇ ಕ್ರಿಮಿನಲ್ ಹಿನ್ನೆಲೆ ಇಲ್ಲ.\n\n"
    "## ಪ್ರಾರ್ಥನೆ\n"
    "ಅರ್ಜಿದಾರರನ್ನು ಜಾಮೀನಿನ ಮೇಲೆ ಬಿಡುಗಡೆ ಮಾಡಬೇಕು.\n"
)


class TestGenerationScriptGate:
    """Force the GENERATOR to return English and confirm the gate reacts."""

    def _strategy(self):
        from agents.drafting import _FanoutStrategy, _Section
        return _FanoutStrategy(
            should_fanout=True,
            sections=[_Section(id="facts", heading="ಪ್ರಕರಣದ ಸಂಗತಿಗಳು", summary=""),
                      _Section(id="prayer", heading="ಪ್ರಾರ್ಥನೆ", summary="")],
            reasoning="test",
        )

    def _run_gate(self, sectionwise_returns, remaining_budget):
        """Drive _generate_draft with a scripted sequence of writer outputs."""
        import agents.drafting as drafting_mod
        calls = {"n": 0}
        warnings: list[str] = []
        real_warning = drafting_mod.log.warning

        def record(msg, *a, **kw):
            warnings.append(str(msg))
            return real_warning(msg, *a, **kw)

        async def fake_sectionwise(**kwargs):
            i = min(calls["n"], len(sectionwise_returns) - 1)
            calls["n"] += 1
            return sectionwise_returns[i]

        async def fake_judge(**kwargs):
            return self._strategy()

        with patch("agents.drafting._judge_fanout", fake_judge), \
             patch("agents.drafting._generate_sectionwise", fake_sectionwise), \
             patch("core.deadline.remaining", lambda: remaining_budget), \
             patch.object(drafting_mod.log, "warning", record):
            out = _run(drafting_mod._generate_draft(
                query="ಜಾಮೀನು ಅರ್ಜಿ ಸಿದ್ಧಪಡಿಸಿ",
                user_facts="",
                reference_draft="reference",
                user_intent=None,
                user_language="kn",
                progress_emit=lambda *a, **kw: None,
                gathered_context=None,
            ))
        return out, calls["n"], warnings

    def test_gate_fires_and_regeneration_recovers(self):
        """English first, Kannada on retry — the retry must be accepted."""
        out, n_calls, warnings = self._run_gate(
            [ENGLISH_DRAFT, KANNADA_DRAFT], remaining_budget=300.0,
        )
        assert n_calls == 2, "expected exactly one regeneration attempt"
        assert not is_off_target_language(out, "kn"), "retry should have been kept"
        assert "ಪ್ರಾರ್ಥನೆ" in out
        assert any("off-target language" in w for w in warnings), \
            f"expected the gate warning, saw: {warnings}"

    def test_budget_exhausted_skips_retry_and_ships_banner(self):
        """No budget: do not retry, ship the draft behind a warning banner."""
        out, n_calls, warnings = self._run_gate(
            [ENGLISH_DRAFT], remaining_budget=20.0,
        )
        assert n_calls == 1, "must NOT regenerate without budget"
        assert out.lstrip().startswith(">"), "expected a warning banner"
        assert "Kannada" in out.split("\n")[0]

    def test_retry_also_english_still_banners(self):
        """Retry fails too — the user must still be told."""
        out, n_calls, warnings = self._run_gate(
            [ENGLISH_DRAFT, ENGLISH_DRAFT], remaining_budget=300.0,
        )
        assert n_calls == 2
        assert out.lstrip().startswith(">"), "expected a warning banner"

    def test_on_target_draft_is_untouched(self):
        """The gate must not fire on a correct draft."""
        out, n_calls, warnings = self._run_gate(
            [KANNADA_DRAFT], remaining_budget=300.0,
        )
        assert n_calls == 1, "must not regenerate a correct draft"
        assert not out.lstrip().startswith(">")
        assert out == KANNADA_DRAFT
