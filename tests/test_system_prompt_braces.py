"""Braces in text injected into an agent's system prompt must not crash the
agent before its model call.

The domain agents append two pieces of user-derived text to their system
prompt: the uploaded-file block (`core.file_context.format_file_context_prefix`)
and the user-intent directives (`additional_instructions`, copied by the
intent extractor from the user's own words). Until 2026-11-25 the assembled
prompt went into `ChatPromptTemplate.from_messages` as a ("system", text)
tuple, which LangChain parses as an f-string template: "{Party A}" raised
KeyError, a lone "}" raised ValueError, and "$1/5^{\\text{th}}$" raised
ValueError("unexpected '{' in field name"). The agent then returned an
error result and the request fell through to the web-search last resort.
Prod counters over 5 days (20-25 Nov 2026): 362 of 2,171 Scenario runs died
in under 0.1 s this way, plus 31 Judgment, 31 Legislation, 63 Newacts and
8 Constitution runs.

The fix passes the assembled prompt as a `SystemMessage`, which is kept
verbatim. These tests cover the template behaviour, the wiring in every
agent that builds a chat prompt from an assembled system prompt, and the
Scenario agent end to end with a fake model.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

_REPO_ROOT = Path(__file__).resolve().parent.parent

BRACE_TEXTS = [
    "The clause reads {Party A} shall pay {Party B}",
    "Amount } 1,00,000 only",
    "the plaintiff's undivided $1/5^{\\text{th}}$ share",
    '{"name": "FIR 123/2024"}',
]

AGENT_FILES = [
    "agents/scenario.py",
    "agents/judgment.py",
    "agents/legislation.py",
    "agents/newacts.py",
    "agents/constitution_maxim.py",
    "agents/document.py",
]


# ---------------------------------------------------------------------------
# Template behaviour
# ---------------------------------------------------------------------------

def test_system_message_keeps_braces_verbatim():
    for text in BRACE_TEXTS:
        system_text = "SYSTEM PROMPT\n\n## UPLOADED SOURCE DOCUMENTS\n" + text
        template = ChatPromptTemplate.from_messages([
            SystemMessage(content=system_text),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
        ])
        messages = template.format_messages(input="question", chat_history=[])
        assert messages[0].content == system_text
        assert messages[-1].content == "question"


# ---------------------------------------------------------------------------
# Wiring: no agent hands an assembled system prompt to the template parser
# ---------------------------------------------------------------------------

_SYSTEM_TUPLE = re.compile(r'\(\s*"system"\s*,\s*[A-Za-z_]*system_prompt\s*\)')


def test_agents_pass_their_system_prompt_as_a_message():
    for rel in AGENT_FILES:
        src = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "SystemMessage(content=" in src, rel
        assert not _SYSTEM_TUPLE.search(src), (
            f"{rel} still passes its system prompt as a (\"system\", ...) tuple, "
            "which LangChain parses as a template")


# ---------------------------------------------------------------------------
# Scenario agent end to end with a fake model
# ---------------------------------------------------------------------------

def _fake_response(text: str):
    part = SimpleNamespace(text=text)
    candidate = SimpleNamespace(
        content=SimpleNamespace(parts=[part]),
        grounding_metadata=None, finish_reason=None, safety_ratings=None,
    )
    return SimpleNamespace(
        candidates=[candidate],
        usage_metadata=SimpleNamespace(total_token_count=12, prompt_token_count=10,
                                       candidates_token_count=2),
        prompt_feedback=None,
    )


class _FakeModels:
    def __init__(self):
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return _fake_response("scenario analysis")


def _run_scenario(state: dict, file_prefix: str):
    import agents.scenario as scenario_mod

    fake = SimpleNamespace(models=_FakeModels())
    with patch.object(scenario_mod, "get_genai_client", return_value=fake), \
         patch("core.file_context.format_file_context_prefix", return_value=file_prefix), \
         patch("core.token_tracker.record_genai", return_value=0), \
         patch("core.token_tracker.record_grounding", return_value=None):
        result = asyncio.run(scenario_mod.scenario_node(state))["agent_results"]["Scenario"]
    return result, fake.models.calls


def _state(**extra) -> dict:
    base = {
        "original_query": "Can the bank take possession while the partition suit is pending?",
        "query": "Can the bank take possession while the partition suit is pending?",
        "user_language": "en",
        "chat_history": [],
        "agent_queries": {},
    }
    base.update(extra)
    return base


def test_scenario_survives_braces_in_the_uploaded_file_block():
    prefix = "## UPLOADED SOURCE DOCUMENTS\n" + "\n".join(BRACE_TEXTS)
    result, calls = _run_scenario(_state(), prefix)
    assert result.error is None, result.error
    assert result.content == "scenario analysis"
    assert len(calls) == 1
    prompt = calls[0]["contents"][0]
    for text in BRACE_TEXTS:
        assert text in prompt


def test_scenario_survives_braces_in_the_user_directives():
    from config.intent import default_intent

    intent = default_intent()
    intent.additional_instructions = (
        "address the plaintiff's undivided $1/5^{\\text{th}}$ share and {Party A}")
    result, calls = _run_scenario(_state(user_intent=intent), "")
    assert result.error is None, result.error
    assert result.content == "scenario analysis"
    assert "$1/5^{\\text{th}}$" in calls[0]["contents"][0]
