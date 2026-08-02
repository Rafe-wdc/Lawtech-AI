"""LangGraph agent graph definition.

This is the heart of the multi-agent system. It defines:
- Which agents exist (nodes)
- How they connect (edges)
- Conditional routing logic (conditional edges)
- Parallel fan-out for domain agents via Send API
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Send

from .state import LegalAgentState
from .logger import get_logger, short_err
from .metrics import observe_node_duration

log = get_logger("Graph")


def _timed_node(name: str, node_fn):
    """Wrap a node function to record `node_duration_seconds{node=name}`
    on every execution — success or failure.

    LangGraph calls node functions once per invocation with the shared
    state. Timing here gives us per-node latency for every graph run
    without touching each agent file.
    """
    import time
    import inspect

    if inspect.iscoroutinefunction(node_fn):
        async def _awrap(state):  # type: ignore[misc]
            start = time.perf_counter()
            try:
                return await node_fn(state)
            finally:
                observe_node_duration(name, time.perf_counter() - start)
        _awrap.__name__ = f"timed_{name}"
        _awrap.__wrapped__ = node_fn  # type: ignore[attr-defined]
        return _awrap

    def _swrap(state):
        start = time.perf_counter()
        try:
            return node_fn(state)
        finally:
            observe_node_duration(name, time.perf_counter() - start)
    _swrap.__name__ = f"timed_{name}"
    _swrap.__wrapped__ = node_fn  # type: ignore[attr-defined]
    return _swrap

# Agent imports
from agents.guardrail import guardrail_input_node, guardrail_output_node
from agents.memory import memory_node
from agents.orchestrator import orchestrator_plan_node, orchestrator_synthesize_node
from agents.legislation import legislation_node
from agents.judgment import judgment_node
from agents.newacts import newacts_node
from agents.drafting import drafting_node
from agents.scenario import scenario_node
from agents.constitution_maxim import constitution_node, maxim_node, legal_concepts_node
from agents.document import document_node
from agents.sci_judgment import sci_judgment_node
from agents.gst_judgment import gst_judgment_node
from agents.non_legal import non_legal_node


# --- Task → Node Mapping ---

AGENT_NODE_MAP = {
    "Legislation": "legislation",
    "Judgment": "judgment",
    "Newacts": "newacts",
    "Drafting": "drafting",
    "Scenario": "scenario",
    "Other": "scenario",
    "Constitution": "constitution",
    "Maxim": "maxim",
    "Legal_Concepts": "legal_concepts",
    "SCI_Judgment": "sci_judgment",
    "GST_Judgment": "gst_judgment",
    "Document": "document",
    "Non_legal": "non_legal",
}


# --- Simple Utility Node ---

async def blocked_response_node(state: LegalAgentState) -> dict:
    """Convert block_reason into final_response for blocked queries."""
    log.info("Blocked response generated",
             reason=state.get("block_reason", "unknown")[:80])
    return {
        "final_response": state.get("block_reason", "Your query was blocked."),
    }


# --- Routing Functions ---

def route_after_guardrail(state: LegalAgentState) -> str:
    """After input guardrail: block or continue to memory."""
    if state.get("is_blocked"):
        log.info("Routing to blocked_response (guardrail blocked)")
        return "blocked_response"
    log.debug("Routing to memory (guardrail passed)")
    return "memory"


def route_after_orchestrator(state: LegalAgentState) -> list[Send]:
    """After orchestrator planning: fan out to domain agents in parallel.

    Uses LangGraph Send API to invoke multiple agents simultaneously.
    Each agent receives the full shared state and writes its results
    to agent_results[agent_name] — the custom reducer merges them.
    """
    # Handle guardrail-blocked queries (PII, injection, etc.)
    if state.get("is_blocked"):
        log.info("Routing to blocked_response (guardrail blocked)")
        return [Send("blocked_response", state)]

    # Sagar bug #5 (2026-06-16): regenerate short-circuit. When
    # orchestrator_plan_node refined a previous response (task='Refine',
    # final_response already populated), bypass ALL domain agents AND the
    # synthesis step — go straight to guardrail_output. Otherwise the
    # unknown task "Refine" falls through to AGENT_NODE_MAP's default
    # ("scenario") and Scenario runs a second time over the already-
    # refined answer.
    if (state.get("task") == "Refine"
            and (state.get("final_response") or "").strip()):
        log.info("Regenerate short-circuit: skipping agent fan-out and synthesis",
                 final_len=len(state.get("final_response") or ""))
        return [Send("guardrail_output", state)]

    planned = state.get("tasks_planned", [])

    if not planned:
        log.warning("No tasks planned, falling back to scenario")
        return [Send("scenario", state)]

    # Map task types to node names, deduplicating
    sends = []
    seen_nodes: set[str] = set()
    for task in planned:
        node = AGENT_NODE_MAP.get(task, "scenario")
        if node not in seen_nodes:
            seen_nodes.add(node)
            sends.append(Send(node, state))

    node_names = list(seen_nodes)
    log.info("Fan-out routing",
             tasks_planned=planned, nodes=node_names,
             parallel_count=len(sends))

    return sends if sends else [Send("scenario", state)]


# --- Graph Construction ---

def _validate_agent_map():
    """Ensure all TaskType values (except Non_legal) have an AGENT_NODE_MAP entry."""
    from core.state import TaskType
    task_types = set(TaskType.__args__)
    unmapped = task_types - set(AGENT_NODE_MAP.keys())
    if unmapped:
        log.warning("TaskTypes missing from AGENT_NODE_MAP (will fallback to scenario)",
                    unmapped=list(unmapped))

_validate_agent_map()


def build_graph() -> StateGraph:
    """Build the complete LangGraph agent graph.

    Flow:
        START → guardrail_input
                    │
                [blocked?] → blocked_response → guardrail_output → END
                    │
                [safe] → memory → orchestrator_plan
                                       │
                                  [blocked?] → blocked_response → guardrail_output → END
                                       │
                    ┌─────────────────[fan-out]─────────────────┐
                    │                  │                         │
              [domain_agent_1]   [domain_agent_2]   [domain_agent_3]
                    │                  │                         │
                    └──────────────────┼─────────────────────────┘
                                       │
                            orchestrator_synthesize
                                       │
                                guardrail_output
                                       │
                                      END
    """
    log.info("Building agent graph")
    graph = StateGraph(LegalAgentState)

    # --- Add Nodes ---

    # Infrastructure agents — wrapped in _timed_node so
    # `lawtech_node_duration_seconds{node=<name>}` gets observed on
    # every run, success or failure.
    graph.add_node("guardrail_input", _timed_node("guardrail_input", guardrail_input_node))
    graph.add_node("memory", _timed_node("memory", memory_node))
    graph.add_node("orchestrator_plan", _timed_node("orchestrator_plan", orchestrator_plan_node))
    graph.add_node("orchestrator_synthesize", _timed_node("orchestrator_synthesize", orchestrator_synthesize_node))
    graph.add_node("guardrail_output", _timed_node("guardrail_output", guardrail_output_node))
    graph.add_node("blocked_response", _timed_node("blocked_response", blocked_response_node))

    # Domain agents
    graph.add_node("legislation", _timed_node("legislation", legislation_node))
    graph.add_node("judgment", _timed_node("judgment", judgment_node))
    graph.add_node("newacts", _timed_node("newacts", newacts_node))
    graph.add_node("drafting", _timed_node("drafting", drafting_node))
    graph.add_node("scenario", _timed_node("scenario", scenario_node))
    graph.add_node("constitution", _timed_node("constitution", constitution_node))
    graph.add_node("maxim", _timed_node("maxim", maxim_node))
    graph.add_node("legal_concepts", _timed_node("legal_concepts", legal_concepts_node))
    graph.add_node("document", _timed_node("document", document_node))
    graph.add_node("sci_judgment", _timed_node("sci_judgment", sci_judgment_node))
    graph.add_node("gst_judgment", _timed_node("gst_judgment", gst_judgment_node))
    graph.add_node("non_legal", _timed_node("non_legal", non_legal_node))

    # --- Add Edges ---

    # Entry point
    graph.add_edge(START, "guardrail_input")

    # After guardrail: block or continue
    graph.add_conditional_edges(
        "guardrail_input",
        route_after_guardrail,
        {
            "blocked_response": "blocked_response",
            "memory": "memory",
        },
    )

    # Memory → Orchestrator planning
    graph.add_edge("memory", "orchestrator_plan")

    # Orchestrator → Fan out to domain agents (parallel via Send)
    # Send API handles routing — no mapping dict needed
    graph.add_conditional_edges("orchestrator_plan", route_after_orchestrator)

    # Blocked queries → output guardrail
    graph.add_edge("blocked_response", "guardrail_output")

    # All domain agents converge → orchestrator_synthesize
    domain_agents = [
        "legislation", "judgment", "newacts", "drafting",
        "scenario", "constitution", "maxim", "legal_concepts",
        "document", "sci_judgment", "gst_judgment", "non_legal",
    ]
    for agent_name in domain_agents:
        graph.add_edge(agent_name, "orchestrator_synthesize")

    # Synthesize → Output guardrail → END
    graph.add_edge("orchestrator_synthesize", "guardrail_output")
    graph.add_edge("guardrail_output", END)

    total_nodes = 6 + len(domain_agents)  # infra + domain
    log.info("Graph built",
             infrastructure_nodes=6, domain_agents=len(domain_agents),
             total_nodes=total_nodes)

    return graph


def compile_graph(checkpointer=None):
    """Compile the graph with optional checkpointer for persistence.

    Args:
        checkpointer: LangGraph checkpointer (MemorySaver, RedisSaver, etc.)
                      If None, uses in-memory checkpointer.
    """
    if checkpointer is None:
        checkpointer = MemorySaver()

    graph = build_graph()
    compiled = graph.compile(checkpointer=checkpointer)
    log.info("Graph compiled successfully",
             nodes=len(compiled.nodes))
    return compiled
