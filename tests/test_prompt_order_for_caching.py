"""The draft-modification prompt keeps its fixed rules apart from the inputs.

Claude's prompt cache only reuses the identical PREFIX of a request. The
existing draft sat in the middle of the system prompt, ahead of ~5,800 tokens
of rules, so the rules were never reused, and the whole prompt (draft
included) was written to cache at 1.25x on every call.
"""

import os
import re

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

from config import prompts as p

_VAR = re.compile(r"(?<!\{)\{([a-zA-Z_]+)\}(?!\})")



def test_modification_rules_are_fixed_and_inputs_separate():
    assert not _VAR.findall(p.DRAFTING_MODIFICATION_PROMPT)
    p.DRAFTING_MODIFICATION_PROMPT.format()          # only {{ }} escapes
    assert set(_VAR.findall(p.DRAFTING_MODIFICATION_INPUTS)) == {
        "intent_directives_block", "existing_draft",
        "user_directive", "new_source_documents"}


def test_modification_request_puts_the_draft_in_the_user_message():
    import inspect
    import agents.drafting as d
    src = inspect.getsource(d)
    assert "system_prompt = DRAFTING_MODIFICATION_PROMPT.format()" in src
    assert "HumanMessage(content=user_message)" in src
