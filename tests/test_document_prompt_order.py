"""Document chat prompt: the (unchanging) document precedes the (changing)
conversation history so Gemini's implicit context cache can match the prefix
on follow-up turns (cost audit 2026-10-08). Pure test.
"""
from __future__ import annotations

import inspect

import agents.document as doc


def test_document_precedes_history_and_question_is_last():
    src = inspect.getsource(doc._generate_from_docs)
    i_doc = src.index('"Document content:\\n{docs}"')
    i_size = src.index('"Source size and required length: {size_note}"')
    i_hist = src.index('"Previous conversation:\\n{history}"')
    i_date = src.index('"Current Date: {date}"')
    i_q = src.index('"Question: {query}"')
    assert i_doc < i_size < i_hist < i_date < i_q
