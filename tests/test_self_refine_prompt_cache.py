"""Self-refine critic/refiner prompts are prompt-cached on Claude.

CRITIQUE_PROMPT carries ~16k tokens of fixed instructions; sent as one human
message it was billed at full input price on every critique (two per draft).
On Claude the fixed part now goes in a system block with cache_control.
"""

import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import core.self_refine as sr

VARS = dict(query="Q", intent_json="{}", retrieved_sources_whitelist="W",
            response="R", violations_block="V", language_directive="")


class ChatAnthropic:            # matched by class name, like the real one
    pass


class _WithFallbacks:
    def __init__(self, runnable):
        self.runnable = runnable


class ChatGoogleGenerativeAI:
    pass


def _flat(msgs):
    out = []
    for m in msgs:
        c = m.content
        out.append(c if isinstance(c, str) else "".join(b["text"] for b in c))
    return "\n".join(out)


def test_claude_gets_a_cached_fixed_block_with_the_same_text():
    for tpl in (sr.CRITIQUE_PROMPT, sr.REFINE_PROMPT):
        for llm in (ChatAnthropic(), _WithFallbacks(ChatAnthropic())):
            msgs = sr._prompt_for(tpl, llm).format_messages(**VARS)
            assert len(msgs) == 2 and msgs[0].type == "system"
            assert msgs[0].content[0]["cache_control"] == {"type": "ephemeral"}
            assert "{{" not in msgs[0].content[0]["text"]
            assert msgs[1].content.startswith("## Inputs")
            original = sr.ChatPromptTemplate.from_template(tpl).format_messages(**VARS)
            assert _flat(msgs) == original[0].content


def test_fixed_block_is_large_enough_to_cache():
    msgs = sr._prompt_for(sr.CRITIQUE_PROMPT, ChatAnthropic()).format_messages(**VARS)
    assert len(msgs[0].content[0]["text"]) > 40_000        # ~16k tokens


def test_gemini_and_default_keep_the_single_message():
    for llm in (None, ChatGoogleGenerativeAI()):
        msgs = sr._prompt_for(sr.CRITIQUE_PROMPT, llm).format_messages(**VARS)
        assert len(msgs) == 1 and msgs[0].type == "human"
