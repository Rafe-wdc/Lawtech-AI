"""Targeted follow-up edits of a draft (core/draft_edit.py + agents/drafting.py).

"Change paragraph 3" used to regenerate the whole draft (dev 2026-10-19: a
follow-up on a two-page notice cost 53k tokens through the full pipeline).
The model now returns block edits and code applies them; every other block
must come back byte for byte. No network: the LLM is faked.
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import pytest
from langchain_core.messages import AIMessage

from config.intent import UserIntent
from core.draft_edit import (DraftEdit, DraftEditPlan, EditError, apply_edits,
                             render_numbered, split_blocks)

DRAFT = """IN THE COURT OF THE CIVIL JUDGE, PUNE

Ramesh Kumar ... Plaintiff

FACTS

1. That the plaintiff owns the shop at FC Road, Pune.

2. That the defendant took the shop on rent of Rs 50,000 per month.

3. That the defendant has not paid rent from January to April 2026.

GROUNDS

4. Because the defendant is in breach of the lease.

5. Because the arrears amount to Rs 2,00,000.

PRAYER

1. Decree for Rs 2,00,000 with interest.

2. Costs of the suit.


Place: Pune
"""


def _plan(*edits, whole=False):
    return DraftEditPlan(whole_document=whole, edits=[DraftEdit(**e) for e in edits])


def _blocks(text):
    return split_blocks(text).blocks


# --- splitting and rendering ---------------------------------------------------

def test_split_keeps_every_character():
    s = split_blocks(DRAFT)
    assert s.lead + "".join(b + sep for b, sep in zip(s.blocks, s.seps)) == DRAFT
    assert s.ids[0] == "B1" and s.blocks[0] == "IN THE COURT OF THE CIVIL JUDGE, PUNE"
    assert "[B5]\n2. That the defendant took the shop" in render_numbered(s)


def test_table_and_list_without_blank_lines_stay_one_block():
    s = split_blocks("Intro\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n(a) one\n(b) two")
    assert len(s.blocks) == 3 and s.blocks[1].count("\n") == 2


# --- applying ------------------------------------------------------------------

def test_replace_changes_only_that_block():
    out = apply_edits(DRAFT, _plan({"op": "replace", "block": "B6",
                                    "text": "3. That the defendant has not paid rent from February to April 2026."}))
    before, after = _blocks(DRAFT), _blocks(out.text)
    assert after[5].startswith("3. That the defendant has not paid rent from February")
    assert [b for i, b in enumerate(after) if i != 5] == [b for i, b in enumerate(before) if i != 5]
    assert out.text.endswith("\n\n\nPlace: Pune\n")        # spacing outside the edit kept
    assert out.changed_blocks == ["B6"] and out.inserted == 0


def test_identity_edit_reproduces_the_draft_exactly():
    out = apply_edits(DRAFT, _plan({"op": "replace", "block": "B1",
                                    "text": "IN THE COURT OF THE CIVIL JUDGE, PUNE"}))
    assert out.text == DRAFT


def test_delete_renumbers_the_series_across_headings_but_not_the_prayer():
    out = apply_edits(DRAFT, _plan({"op": "delete", "block": "B4"}))   # facts para 1
    text = out.text
    assert "1. That the defendant took the shop" in text
    assert "2. That the defendant has not paid" in text
    assert "3. Because the defendant is in breach" in text           # grounds continue the series
    assert "4. Because the arrears" in text
    assert "1. Decree for Rs 2,00,000" in text and "2. Costs of the suit." in text
    assert "That the plaintiff owns the shop" not in text


def test_insert_renumbers_the_following_paragraphs():
    out = apply_edits(DRAFT, _plan({"op": "insert_after", "block": "B5",
                                    "text": "3. That the lease deed is dated 01.01.2025."}))
    text = out.text
    assert text.index("3. That the lease deed") < text.index("4. That the defendant has not paid")
    assert "5. Because the defendant is in breach" in text and "6. Because the arrears" in text
    assert "1. Decree for" in text                                      # prayer untouched
    assert out.inserted == 1


def test_insert_before_the_first_block():
    out = apply_edits(DRAFT, _plan({"op": "insert_before", "block": "B1", "text": "WITHOUT PREJUDICE"}))
    assert out.text.startswith("WITHOUT PREJUDICE\n\nIN THE COURT OF")


def test_delete_last_block_keeps_trailing_whitespace():
    out = apply_edits(DRAFT, _plan({"op": "delete", "block": f"B{len(_blocks(DRAFT))}"}))
    assert out.text.endswith("2. Costs of the suit.\n")


@pytest.mark.parametrize("edits, why", [
    ([{"op": "replace", "block": "B99", "text": "x"}], "unknown block"),
    ([{"op": "replace", "block": "B2", "text": "  "}], "without text"),
    ([{"op": "replace", "block": "B2", "text": "a"}, {"op": "delete", "block": "B2"}], "twice"),
    ([], "no edits"),
])
def test_plans_that_cannot_apply_raise(edits, why):
    with pytest.raises(EditError, match=why):
        apply_edits(DRAFT, _plan(*edits))


def test_deleting_everything_is_refused():
    n = len(_blocks(DRAFT))
    with pytest.raises(EditError, match="whole draft"):
        apply_edits(DRAFT, _plan(*[{"op": "delete", "block": f"B{i}"} for i in range(1, n + 1)]))


def test_block_ids_are_read_leniently():
    out = apply_edits(DRAFT, _plan({"op": "replace", "block": "[b2]", "text": "Ramesh Kumar ... Plaintiff No. 1"}))
    assert "Plaintiff No. 1" in out.text


# --- choosing the path ----------------------------------------------------------

from agents.drafting import _followup_edit_scope  # noqa: E402

PRIOR = DRAFT * 3   # comfortably over the 500-char minimum


def _intent(scope="none", confidence=0.9):
    return UserIntent(confidence=confidence, task_intent="draft", draft_edit_scope=scope)


@pytest.mark.parametrize("intent, query, expected", [
    (_intent("partial"), "change paragraph 3 to say February", "partial"),
    (_intent("whole"), "make it more formal throughout", "whole"),
    (_intent("none"), "shorten it", "whole"),                      # verb regex fallback
    (_intent("none"), "now draft the reply notice", None),
    (_intent("partial", confidence=0.2), "change paragraph 3", None),   # unsure intent, no verb match
    (None, "translate into hindi", "whole"),
])
def test_scope_from_intent_with_regex_fallback(intent, query, expected):
    assert _followup_edit_scope(intent, query, "draft", PRIOR, False) == expected


@pytest.mark.parametrize("kind, content, upload", [
    ("", PRIOR, False),               # previous turn was not a draft
    ("draft", "short", False),        # nothing meaningful to edit
    ("draft", PRIOR, True),           # new upload: likely a new document
    ("draft", "**Draft incomplete** " + PRIOR, False),
])
def test_scope_needs_a_usable_prior_draft(kind, content, upload):
    assert _followup_edit_scope(_intent("partial"), "change paragraph 3", kind, content, upload) is None


# --- the LLM call -----------------------------------------------------------------

class _FakeStructured:
    def __init__(self, result):
        self.result, self.messages = result, None

    def invoke(self, messages):
        self.messages = messages
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _FakeLLM:
    def __init__(self, result):
        self.structured = _FakeStructured(result)
        self.kwargs = None

    def with_structured_output(self, schema, include_raw=False):
        assert schema is DraftEditPlan and include_raw
        return self.structured


def _run_edit(monkeypatch, result, directive="change paragraph 3 to say February"):
    import agents.drafting as drafting
    fake = _FakeLLM(result)

    def factory(**kw):
        fake.kwargs = kw
        return fake
    monkeypatch.setattr(drafting, "get_drafting_llm", factory)
    out = asyncio.run(drafting._generate_targeted_edit(
        user_directive=directive, prior_draft=DRAFT, user_intent=_intent("partial"),
        user_language="en", progress_emit=lambda *a, **k: None))
    return out, fake


def test_targeted_edit_applies_the_plan(monkeypatch):
    plan = _plan({"op": "replace", "block": "B6",
                  "text": "3. That the defendant has not paid rent from February to April 2026."})
    out, fake = _run_edit(monkeypatch, {"raw": AIMessage(content=""), "parsed": plan, "parsing_error": None})
    text, meta = out
    assert "from February to April 2026" in text and "from January" not in text
    assert meta == {"edit_ops": 1, "edited_blocks": ["B6"], "inserted_blocks": 0,
                    "total_blocks": len(_blocks(DRAFT))}
    assert fake.kwargs["thinking"] is False                       # structured output needs it off
    user_msg = fake.structured.messages[1].content
    assert "[B6]\n3. That the defendant has not paid" in user_msg
    assert "change paragraph 3 to say February" in user_msg


@pytest.mark.parametrize("result", [
    {"raw": AIMessage(content=""), "parsed": DraftEditPlan(whole_document=True), "parsing_error": None},
    {"raw": AIMessage(content=""), "parsed": None, "parsing_error": ValueError("bad json")},
    {"raw": AIMessage(content=""), "parsed": _plan({"op": "replace", "block": "B99", "text": "x"}),
     "parsing_error": None},
    RuntimeError("api down"),
])
def test_targeted_edit_hands_back_to_the_whole_rewrite(monkeypatch, result):
    out, _ = _run_edit(monkeypatch, result)
    assert out is None


# --- the drafting node --------------------------------------------------------------

def _node_state(scope):
    return {
        "query": "change paragraph 3", "original_query": "change paragraph 3 to say February",
        "user_language": "en", "previous_artifact_kind": "draft",
        "previous_artifact_content": PRIOR, "user_intent": _intent(scope),
    }


def test_node_uses_the_targeted_edit(monkeypatch):
    import agents.drafting as drafting
    monkeypatch.setenv("DRAFTING_FOLLOWUP_FAST_PATH", "1")

    async def fake_edit(**kw):
        return "EDITED DRAFT " + "x" * 600, {"edit_ops": 1, "edited_blocks": ["B7"],
                                             "inserted_blocks": 0, "total_blocks": 40}

    async def no_rewrite(**kw):
        raise AssertionError("whole rewrite must not run")
    monkeypatch.setattr(drafting, "_generate_targeted_edit", fake_edit)
    monkeypatch.setattr(drafting, "_generate_draft_modification", no_rewrite)
    result = asyncio.run(drafting.drafting_node(_node_state("partial")))["agent_results"]["Drafting"]
    assert result.content.startswith("EDITED DRAFT")
    assert result.meta["edit_mode"] == "targeted" and result.meta["edited_blocks"] == ["B7"]


def test_node_falls_back_to_the_whole_rewrite(monkeypatch):
    import agents.drafting as drafting
    monkeypatch.setenv("DRAFTING_FOLLOWUP_FAST_PATH", "1")

    async def failed_edit(**kw):
        return None

    async def rewrite(**kw):
        return "REWRITTEN DRAFT " + "y" * 600
    monkeypatch.setattr(drafting, "_generate_targeted_edit", failed_edit)
    monkeypatch.setattr(drafting, "_generate_draft_modification", rewrite)
    result = asyncio.run(drafting.drafting_node(_node_state("partial")))["agent_results"]["Drafting"]
    assert result.content.startswith("REWRITTEN DRAFT") and result.meta["edit_mode"] == "whole"


def test_node_ignores_edits_when_the_flag_is_off(monkeypatch):
    import agents.drafting as drafting
    monkeypatch.setenv("DRAFTING_FOLLOWUP_FAST_PATH", "0")
    assert drafting._fast_path_enabled() is False
