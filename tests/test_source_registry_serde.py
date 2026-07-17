"""Regression tests for `SourceRegistry` checkpoint serialization.

Prior to 2026-07-17 `SourceRegistry` was a plain `__slots__` class. When it
rode on `LegalAgentState["source_registry"]` (via the reducer), any Send
that carried the current state as its `arg` would fail LangGraph's msgpack
checkpoint with:

    TypeError: Type is not msgpack serializable: Send

ormsgpack reports the *outermost* packed type, so the message named `Send`
instead of `SourceRegistry`, which sent readers looking in the wrong place.
The user-visible symptom was a mid-stream `error` SSE event followed by the
generic "I was unable to retrieve information..." web-fallback string
replacing the real agent answer — reliably reproducible with a plain
"Hello" against `/pyapi/chat` and `/pyapi/search/stream`.

Fix: convert `SourceRegistry` to a `@dataclass`. LangGraph's
`_msgpack_default` (langgraph.checkpoint.serde.jsonplus) has a dedicated
branch for dataclasses that emits an EXT_CONSTRUCTOR_KW_ARGS ext code and
round-trips via `cls(**fields)`.

These tests lock in that discipline so any future regression (adding
`__slots__`, replacing the dataclass, introducing a non-serializable nested
type) is caught in CI before it reaches prod.
"""
from __future__ import annotations

import pytest

from langgraph.types import Send
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from core.source_registry import (
    RetrievedSource,
    SourceRegistry,
    merge_source_registries,
)


@pytest.fixture
def serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer()


@pytest.fixture
def sample_source() -> RetrievedSource:
    return RetrievedSource(
        id="leg-ir-2d-1",
        agent="Legislation",
        type="legislation",
        canonical_citation="Section 2(d), Industrial Relations Code 2020",
        title="Section 2(d), Industrial Relations Code 2020",
        pdf_urls=[],
        snippet="average pay means...",
        year="2020",
    )


def test_empty_registry_serializes(serializer: JsonPlusSerializer) -> None:
    reg = SourceRegistry()
    typ, blob = serializer.dumps_typed(reg)
    assert typ == "msgpack"
    back = serializer.loads_typed((typ, blob))
    assert isinstance(back, SourceRegistry)
    assert len(back) == 0


def test_registry_with_records_roundtrips(
    serializer: JsonPlusSerializer, sample_source: RetrievedSource
) -> None:
    reg = SourceRegistry({sample_source.id: sample_source})
    typ, blob = serializer.dumps_typed(reg)
    back = serializer.loads_typed((typ, blob))
    assert isinstance(back, SourceRegistry)
    assert len(back) == 1
    got = back.get(sample_source.id)
    assert got is not None
    assert got.canonical_citation == sample_source.canonical_citation
    assert got.agent == sample_source.agent


def test_send_with_registry_in_state_serializes(
    serializer: JsonPlusSerializer, sample_source: RetrievedSource
) -> None:
    """The exact shape that broke prod: a Send whose arg is state carrying a
    SourceRegistry. This is what LangGraph's `route_after_orchestrator`
    emits during the fan-out superstep."""
    reg = SourceRegistry({sample_source.id: sample_source})
    state = {
        "query": "Hello",
        "user_language": "en",
        "source_registry": reg,
    }
    send = Send("non_legal", state)
    typ, blob = serializer.dumps_typed(send)
    assert typ == "msgpack"

    back = serializer.loads_typed((typ, blob))
    # After roundtrip the Send is reconstructed; its arg dict should still
    # contain a SourceRegistry with the same record.
    assert back.node == "non_legal"
    back_reg = back.arg["source_registry"]
    assert isinstance(back_reg, SourceRegistry)
    assert len(back_reg) == 1


def test_reducer_does_not_leak_mutations(
    sample_source: RetrievedSource,
) -> None:
    """`__post_init__` shallow-copies the passed dict so parallel-agent
    reducer writes don't leak back into the upstream registry. Regression
    check for the copy semantics preserved from the pre-dataclass class."""
    a = SourceRegistry({sample_source.id: sample_source})
    b = SourceRegistry(
        {
            "leg-other-1": RetrievedSource(
                id="leg-other-1",
                agent="Legislation",
                type="legislation",
                canonical_citation="Section 3, Foo Act",
            )
        }
    )

    merged = merge_source_registries(a, b)
    assert len(merged) == 2
    assert len(a) == 1  # unchanged
    assert len(b) == 1  # unchanged

    merged.add(
        RetrievedSource(
            id="leg-new-1",
            agent="Legislation",
            type="legislation",
            canonical_citation="Section 99, Bar Act",
        )
    )
    assert len(merged) == 3
    assert len(a) == 1  # still unchanged
    assert len(b) == 1  # still unchanged


def test_positional_and_keyword_construction() -> None:
    """Preserve the two construction call patterns used across the codebase:

    - `SourceRegistry()` — orchestrator fresh-registry path
    - `SourceRegistry(records_dict)` — positional, `merge_source_registries`
    - `SourceRegistry(_records=records_dict)` — keyword, used by LangGraph
      when the checkpoint serializer reconstructs the dataclass on load.
    """
    src = RetrievedSource(
        id="x", agent="a", type="b", canonical_citation="c"
    )
    r0 = SourceRegistry()
    assert len(r0) == 0

    r1 = SourceRegistry({"x": src})
    assert len(r1) == 1

    r2 = SourceRegistry(_records={"x": src})
    assert len(r2) == 1
