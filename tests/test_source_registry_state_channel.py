"""Regression tests for the `source_registry` LangGraph state channel.

`LegalAgentState.source_registry` (core/state.py) has been declared as
`Annotated[SourceRegistry, merge_source_registries]` for some time, but until
now **no production code ever wrote or read it**. The reducer had zero
callers: the channel was documented, typed, reduced — and dead.

The concrete symptom: `agents/drafting.py` reports only its reference
TEMPLATE in `AgentResult.sources`. The BNS sections, legislation and HC/SC
judgments it pulls via `_gather_relevant_context` were visible to drafting's
own critic and to nothing else, so a multi-agent synthesis could flag
drafting's genuinely-retrieved citations as unsourced.

This wires it: drafting writes the channel, the orchestrator merges it with
the registry it derives from `AgentResult.sources`.

The wiring tests use source introspection — the same guard style as
`tests/test_judgment_relevance_gate.py::TestGateSkipIsWired` — because
exercising the real LangGraph fan-out needs a compiled graph, ES and live
LLMs. Introspection catches the regression that matters: someone deleting
the write or reverting the merge.

Run:
    pytest tests/test_source_registry_state_channel.py -v
    python tests/test_source_registry_state_channel.py     # no pytest required
"""

from __future__ import annotations

import inspect
import os
import sys
import typing

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("OPENAI_API_KEY", "test-key-unused")
os.environ.setdefault("GOOGLE_API_KEY", "test-key-unused")
os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://localhost:1")

from core.source_registry import (  # noqa: E402
    RetrievedSource, SourceRegistry, merge_source_registries,
)
from core.state import LegalAgentState  # noqa: E402


def _rec(rid, citation="X v. Y"):
    return RetrievedSource(
        id=rid, agent="Test", type="judgment", canonical_citation=citation)


# --- The reducer's contract --------------------------------------------------

class TestReducerContract:
    """LangGraph calls this with whatever is currently in the channel, which
    is None on the first write. All four combinations must be safe."""

    def test_both_none(self):
        assert len(merge_source_registries(None, None)) == 0

    def test_existing_none_is_first_write(self):
        new = SourceRegistry(); new.add(_rec("a"))
        assert len(merge_source_registries(None, new)) == 1

    def test_new_none_preserves_existing(self):
        existing = SourceRegistry(); existing.add(_rec("a"))
        assert len(merge_source_registries(existing, None)) == 1

    def test_union_of_distinct_ids(self):
        a = SourceRegistry(); a.add(_rec("a"))
        b = SourceRegistry(); b.add(_rec("b"))
        assert {r.id for r in merge_source_registries(a, b).all()} == {"a", "b"}

    def test_same_id_later_write_wins(self):
        a = SourceRegistry(); a.add(_rec("dup", "OLD"))
        b = SourceRegistry(); b.add(_rec("dup", "NEW"))
        merged = merge_source_registries(a, b)
        assert len(merged) == 1
        assert merged.all()[0].canonical_citation == "NEW"

    def test_merge_does_not_mutate_inputs(self):
        # Parallel fan-out means the same `existing` may be reduced against
        # several partials; mutating it would corrupt the others.
        a = SourceRegistry(); a.add(_rec("a"))
        b = SourceRegistry(); b.add(_rec("b"))
        merge_source_registries(a, b)
        assert len(a) == 1 and len(b) == 1


# --- The channel declaration -------------------------------------------------

class TestChannelDeclared:

    def test_reducer_is_attached_to_the_state_key(self):
        hints = typing.get_type_hints(LegalAgentState, include_extras=True)
        assert "source_registry" in hints, "state channel was removed"
        metadata = typing.get_args(hints["source_registry"])[1:]
        assert merge_source_registries in metadata, (
            "source_registry lost its reducer — parallel agent writes would "
            "then overwrite each other instead of merging"
        )


# --- The wiring --------------------------------------------------------------

class TestWiring:

    def test_drafting_writes_the_channel(self):
        import agents.drafting as d
        src = inspect.getsource(d.drafting_node)
        assert '"source_registry": gathered_registry' in src, (
            "drafting_node no longer publishes its retrieved sources to state; "
            "its ES statutes/judgments become invisible to synthesis again"
        )

    def test_orchestrator_merges_state_with_agent_results(self):
        import agents.orchestrator as o
        src = inspect.getsource(o.orchestrator_synthesize_node)
        assert "merge_source_registries(" in src
        assert 'state.get("source_registry")' in src, (
            "synthesis stopped reading the state channel — agent-written "
            "sources would be silently dropped"
        )

    def test_pass_through_persists_instead_of_discarding(self):
        import agents.orchestrator as o
        src = inspect.getsource(o.orchestrator_synthesize_node)
        # Two single-agent exits: Drafting-solo and the generic one.
        assert src.count('"source_registry": source_registry') >= 2, (
            "a single-agent exit is discarding the registry it just built"
        )


# --- The value case ----------------------------------------------------------

class TestDraftingSourcesSurvive:
    """The reason this channel exists at all."""

    def test_agent_written_sources_survive_the_merge(self):
        # What drafting retrieves but does NOT report in AgentResult.sources.
        from_drafting = SourceRegistry()
        from_drafting.add(_rec("leg-bns-103", "Section 103, Bharatiya Nyaya Sanhita 2023"))
        from_drafting.add(_rec("sci-44015", "Union of India v. Rajeev Bansal"))

        # What the orchestrator derives from AgentResult.sources — for
        # Drafting that is only the reference template.
        from_agent_results = SourceRegistry()
        from_agent_results.add(_rec("draft-tmpl", "Bail Application Template"))

        merged = merge_source_registries(from_drafting, from_agent_results)
        cites = {r.canonical_citation for r in merged.all()}

        assert "Section 103, Bharatiya Nyaya Sanhita 2023" in cites
        assert "Union of India v. Rajeev Bansal" in cites
        assert "Bail Application Template" in cites
        # Before this wiring the critic saw only the template, so the two real
        # citations would have been flagged as unretrieved.
        assert len(merged) == 3

    def test_whitelist_includes_the_agent_written_sources(self):
        reg = SourceRegistry()
        reg.add(_rec("leg-bns-103", "Section 103, Bharatiya Nyaya Sanhita 2023"))
        merged = merge_source_registries(None, reg)
        assert "Section 103" in merged.serialize_for_critic()


# --- Standalone runner (venv has no pytest) ---------------------------------

if __name__ == "__main__":
    failures = 0
    for cls in (TestReducerContract, TestChannelDeclared,
                TestWiring, TestDraftingSourcesSurvive):
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
