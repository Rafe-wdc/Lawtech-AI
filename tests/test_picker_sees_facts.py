"""The drafting template picker sees the uploaded facts.

Run:
    pytest tests/test_picker_sees_facts.py -v

BACKGROUND (2026-10-29)
=======================
`_pick_reference_source` chose a template from the query and the candidate
file names alone. On "Draft a Divorce Petition u/s 13A of HMA ..." over a
wife's statement of her husband's cruelty it returned "none" in 2 of 3 runs
(web-synthesised reference -> a Section 13B mutual-consent JOINT petition),
and with the facts hidden it could not have done better. These tests pin
that the facts excerpt reaches the picker prompt, that the template-pick
cache keys on the facts as well as the query, and that the drafting node
threads `user_facts` through to the acquirer.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.drafting import (  # noqa: E402
    _PICKER_FACTS_PREVIEW_CAP,
    _PickerChoice,
    _TEMPLATE_PICK_CACHE,
    _acquire_reference_draft,
    _pick_reference_source,
    _picker_facts_preview,
)
from config.prompts import DRAFTING_PICKER_PROMPT  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


FACTS = ("मेरा विवाह 28 अप्रैल 2022 को हुआ था। मेरे पति शराब पीकर मारपीट करते थे और "
         "दहेज की माँग करते थे। 11 फरवरी 2024 को मुझे मायके भेज दिया गया।")

CANDIDATES = [
    "/t/Petition for Divorce on the Ground of Cruelty under Section 13(1)(ia).csv",
    "/t/Petition For Divorce By Mutual Consent Under Section 13B(1).csv",
]


class TestFactsPreview:

    def test_empty_facts_say_so(self):
        assert _picker_facts_preview("") == "(no documents uploaded)"
        assert _picker_facts_preview(None) == "(no documents uploaded)"
        assert _picker_facts_preview("   ") == "(no documents uploaded)"

    def test_long_facts_are_capped_with_marker(self):
        out = _picker_facts_preview("x" * (_PICKER_FACTS_PREVIEW_CAP + 500))
        assert out.endswith("[... truncated ...]")
        assert len(out) < _PICKER_FACTS_PREVIEW_CAP + 40

    def test_prompt_declares_the_variable_and_the_rule(self):
        assert "{source_preview}" in DRAFTING_PICKER_PROMPT
        assert "13B" in DRAFTING_PICKER_PROMPT and "13(1)" in DRAFTING_PICKER_PROMPT
        # ChatPromptTemplate must be able to parse it with exactly these inputs.
        from langchain_core.prompts import ChatPromptTemplate
        tpl = ChatPromptTemplate.from_template(DRAFTING_PICKER_PROMPT)
        assert set(tpl.input_variables) == {"query", "file_paths", "source_preview"}


class TestPickerReceivesFacts:

    def _chain(self, picked: str):
        chain = MagicMock()
        chain.ainvoke = AsyncMock(return_value={
            "raw": MagicMock(usage_metadata={"total_tokens": 50}),
            "parsed": _PickerChoice(selected_file=picked, reasoning="facts say cruelty"),
        })
        return chain

    def test_facts_excerpt_is_in_the_chain_input(self):
        chain = self._chain(CANDIDATES[0])
        with patch("core.clients.init_chat_model") as mock_init:
            llm = MagicMock()
            llm.with_structured_output.return_value = llm
            mock_init.return_value = llm
            with patch("agents.drafting.ChatPromptTemplate") as mock_prompt:
                mock_prompt.from_template.return_value.__or__ = MagicMock(return_value=chain)
                result = _run(_pick_reference_source(
                    "Draft a divorce petition", CANDIDATES, user_facts=FACTS,
                ))
        assert result == CANDIDATES[0]
        sent = chain.ainvoke.call_args.args[0]
        assert sent["source_preview"] == FACTS
        assert sent["query"] == "Draft a divorce petition"
        assert CANDIDATES[1] in sent["file_paths"]

    def test_no_facts_sends_the_placeholder(self):
        chain = self._chain(CANDIDATES[1])
        with patch("core.clients.init_chat_model") as mock_init:
            llm = MagicMock()
            llm.with_structured_output.return_value = llm
            mock_init.return_value = llm
            with patch("agents.drafting.ChatPromptTemplate") as mock_prompt:
                mock_prompt.from_template.return_value.__or__ = MagicMock(return_value=chain)
                _run(_pick_reference_source("Draft a divorce petition", CANDIDATES))
        assert chain.ainvoke.call_args.args[0]["source_preview"] == "(no documents uploaded)"


class TestPickCacheKeysOnFacts:

    @staticmethod
    def _silent(*a, **k):
        pass

    def _fake_es(self):
        es = MagicMock()
        es.search = MagicMock(side_effect=lambda index, body: (
            {"hits": {"hits": [{"_source": {"source": c}} for c in CANDIDATES]}}
            if "match" in body["query"] else
            {"hits": {"hits": [{"_source": {"source": CANDIDATES[0],
                                            "page_content": "BODY"}}]}}
        ))
        return es

    def test_different_facts_same_query_do_not_share_a_pick(self):
        _TEMPLATE_PICK_CACHE.clear()
        picker = AsyncMock(side_effect=[CANDIDATES[0], CANDIDATES[0]])
        with patch("agents.drafting.get_es_client", return_value=self._fake_es()), \
             patch("agents.drafting._pick_reference_source", picker):
            _run(_acquire_reference_draft("draft a divorce petition", self._silent,
                                          user_facts=FACTS))
            _run(_acquire_reference_draft("draft a divorce petition", self._silent,
                                          user_facts="Both parties have settled all "
                                                     "claims and jointly seek divorce."))
        assert picker.await_count == 2
        assert picker.await_args_list[0].kwargs["user_facts"] == FACTS

    def test_same_facts_same_query_hit_the_cache(self):
        _TEMPLATE_PICK_CACHE.clear()
        picker = AsyncMock(return_value=CANDIDATES[0])
        with patch("agents.drafting.get_es_client", return_value=self._fake_es()), \
             patch("agents.drafting._pick_reference_source", picker):
            _run(_acquire_reference_draft("draft a divorce petition", self._silent,
                                          user_facts=FACTS))
            _run(_acquire_reference_draft("draft a divorce petition", self._silent,
                                          user_facts=FACTS))
        assert picker.await_count == 1

    def test_no_facts_keeps_the_old_key_shape(self):
        _TEMPLATE_PICK_CACHE.clear()
        picker = AsyncMock(return_value=CANDIDATES[0])
        with patch("agents.drafting.get_es_client", return_value=self._fake_es()), \
             patch("agents.drafting._pick_reference_source", picker):
            _run(_acquire_reference_draft("draft a divorce petition", self._silent))
        assert list(_TEMPLATE_PICK_CACHE.keys()) == ["|draft a divorce petition|"]


class TestDraftingNodeThreadsFacts:

    def test_call_site_passes_user_facts(self):
        """Cheap structural pin: the only production call of
        `_acquire_reference_draft` forwards `user_facts`."""
        import inspect
        import agents.drafting as d
        src = inspect.getsource(d.drafting_node)
        start = src.index("_acquire_reference_draft(")
        call = src[start:src.index("_gather_relevant_context(query)", start)]
        assert "user_facts=user_facts" in call


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
