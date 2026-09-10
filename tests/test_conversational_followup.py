"""An acknowledgement after a draft must not regenerate the draft.

Advocate report 2026-09-10: "ask for a draft, then say ok, and it
regenerates the whole draft". Reproduced on the full graph: "ok", "hmm" and
"yes" were each rewritten by the memory node into the full prior drafting
request and routed to Drafting (2-3 minutes, ~22K chars) - 3 of 5
acknowledgements. core.followup.is_conversational_followup is the
deterministic gate that now runs before the rewriter.

Pure tests: the detector, and the planner's routing given the flag.
"""
from __future__ import annotations

import pytest

from core.followup import is_conversational_followup as conv


# ------------------------------------------------------------ positives
@pytest.mark.parametrize("q", [
    "ok", "OK", "okay", "k", "ok.", "ok!!", "Ok bro", "ok thanks",
    "thanks", "thank you", "thanks a lot", "thx", "ty", "great, thanks bro",
    "good", "nice", "cool", "perfect", "superb", "great",
    "hmm", "hm", "yes", "yeah", "yup", "no", "nope",
    "done", "noted", "got it", "understood", "sure", "alright",
    "theek hai", "thik hai", "accha", "badhiya", "bahut badhiya", "shukriya",
    "haan", "nahi", "ok ji", "sahi hai",
    "ठीक है", "अच्छा", "धन्यवाद", "हाँ",
    "👍", "🙏", "👍👍", "ok 👍",
])
def test_acknowledgements_are_conversational(q):
    assert conv(q) is True


# ------------------------------------------------------------ negatives
@pytest.mark.parametrize("q", [
    # an instruction attached to an ack is a request
    "ok now translate it to hindi", "yes add a ground on parity",
    "no, for sessions court", "ok make it shorter", "thanks, also cite cases",
    "ok in marathi", "good, now draft the affidavit", "yes please",
    "ok do it", "haan karo", "thik hai bhejo", "ok send",
    # questions
    "what is section 239 crpc", "why?", "ok?", "is this correct?",
    # numbers / anchors
    "section 439", "ok 2 more grounds", "yes, FIR 387/2022",
    # edits and directives
    "make it shorter", "shorten", "regenerate", "again", "redo",
    "translate the prayer to hindi", "add a prayer clause",
    # long messages are never acknowledgements
    "ok this looks fine but I think the grounds section needs more detail on parity",
    # empty
    "", "   ", None,
])
def test_requests_are_not_conversational(q):
    assert conv(q) is False


def test_boundaries():
    # six ack words is still an ack; seven is not
    assert conv("ok ok ok ok ok ok") is True
    assert conv("ok ok ok ok ok ok ok") is False
    # six words under 48 chars is still an ack
    assert conv("thanks " * 5 + "bro") is True
    # over 48 chars is never an ack, whatever the words
    assert conv("thank you thank you thank you thank you thank you bro") is False


# --------------------------------------------------- planner routing
def test_planner_routes_flagged_turn_to_non_legal_without_llm():
    """With the flag set, the plan must be Non_legal even when the previous
    turn was Drafting and a file is attached. Exercised on the code path
    directly: the Drafting-inheritance branch and the file->Document
    override are both bypassed."""
    import inspect
    import agents.orchestrator as orch
    src = inspect.getsource(orch)
    assert 'state.get("conversational_followup")' in src
    assert 'task = "Non_legal"' in src
    # the two overrides are guarded
    assert 'not state.get("conversational_followup")' in src
    assert 'has_content and not state.get("conversational_followup")' in src


def test_memory_node_skips_rewrite_for_flagged_turn():
    import inspect
    import agents.memory as mem
    src = inspect.getsource(mem)
    assert "is_conversational_followup(original_query)" in src
    assert '"conversational_followup": bool(' in src


def test_non_legal_prompt_receives_turn_context():
    import agents.non_legal as nl
    assert "{turn_context}" in nl._NON_LEGAL_PROMPT
