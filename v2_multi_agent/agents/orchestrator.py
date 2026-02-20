"""Agent #1 — Orchestrator Agent

The brain of the system. Two phases:
1. PLAN: Analyze query → classify task → decide which agents to invoke
2. SYNTHESIZE: Merge results from domain agents into final response

Uses: GPT-4o for task classification, planning, and synthesis.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from core.state import LegalAgentState, AgentResult
from core.clients import get_gpt4o, get_gemini_flash
from config.prompts import TASK_CLASSIFICATION_PROMPT, SYNTHESIS_PROMPT


# --- Task Classification (migrated from v1 task_identifer.py) ---

class IdentifyTaskSchema(BaseModel):
    task: Literal[
        "Drafting", "Judgment", "Legislation", "Constitution",
        "Scenario", "Maxim", "Newacts", "Legal_Concepts",
        "SCI_Judgment",
        "Non_legal", "Other",
    ] = Field(..., description="The primary legal task type")


def _classify_task(query: str, chat_summary: str | None = None) -> str:
    """Classify query into a task type using GPT-4o structured output."""
    prompt = PromptTemplate.from_template(TASK_CLASSIFICATION_PROMPT)
    llm = get_gpt4o().with_structured_output(IdentifyTaskSchema)

    formatted = prompt.format(query=query, chat_summary=chat_summary or "")
    result = llm.invoke(formatted)
    print(f"[Orchestrator] Task classified: {result.task}")
    return result.task


# --- Multi-Agent Planning ---

class AgentPlan(BaseModel):
    agents: list[str] = Field(
        ...,
        description="List of agent names to invoke: Legislation, Judgment, Newacts, Drafting, Scenario, Constitution, Maxim, Legal_Concepts, SCI_Judgment",
    )
    reasoning: str = Field(..., description="Brief reasoning for agent selection")


PLAN_PROMPT = """You are a legal query planner. Given a query and its primary task type, determine if MULTIPLE agents should handle it.

Available agents:
- Legislation: Central/state law sections and provisions
- Judgment: Court case laws, citations, precedents (general / High Court / unspecified courts)
- Newacts: BNS, BNSS, BSA, IPC, CrPC, IEA provisions
- Drafting: Legal document templates and drafting
- Scenario: Situational analysis with web search
- Constitution: Constitutional provisions
- Maxim: Legal maxims and doctrines
- Legal_Concepts: General legal explanations
- SCI_Judgment: Supreme Court of India specific case search (use ONLY when SC/Supreme Court is explicitly mentioned)

Rules:
1. Most queries need only the PRIMARY agent matching the task type.
2. Use MULTIPLE agents when the query explicitly asks for different types:
   - "Draft bail application with relevant case laws" → [Drafting, Judgment]
   - "Section 438 BNSS with SC precedents" → [Newacts, SCI_Judgment]
   - "Arguments on behalf of plaintiff and defendant" (scenario with case laws) → [Scenario, Judgment]
   - "Explain Article 21 and related case laws" → [Constitution, Judgment]
3. For complex scenarios requesting statutes → add Legislation or Newacts alongside Scenario.
4. Never use more than 3 agents.
5. "Other" always maps to Scenario.

Query: {query}
Primary Task: {task}

Return the list of agents and brief reasoning."""


def _plan_agents(query: str, task: str) -> list[str]:
    """Determine which domain agents to invoke for this query.

    For simple queries: returns just the primary task agent.
    For complex queries: returns multiple agents to run in parallel.
    """
    # Short-circuit for simple task types
    simple_mapping = {
        "Non_legal": [],
        "Legal_Concepts": ["Legal_Concepts"],
    }
    if task in simple_mapping:
        return simple_mapping[task]

    # For queries with 100+ words, always go to Scenario (complex scenario)
    if len(query.split()) >= 100:
        return ["Scenario"]

    # Try multi-agent planning with LLM
    try:
        llm = get_gpt4o().with_structured_output(AgentPlan)
        prompt = ChatPromptTemplate.from_template(PLAN_PROMPT)
        chain = prompt | llm
        plan = chain.invoke({"query": query, "task": task})
        agents = plan.agents[:3]  # max 3 agents
        print(f"[Orchestrator] Plan: {agents} — {plan.reasoning}")
        return agents if agents else [task]
    except Exception as e:
        print(f"[Orchestrator] Planning failed, using single agent: {e}")
        return [task]


# --- Agent Nodes ---

async def orchestrator_plan_node(state: LegalAgentState) -> dict:
    """Phase 1 — Classify task and create execution plan.

    Steps:
    1. Classify query into task type
    2. Handle Non_legal rejection
    3. Plan which agents to invoke (single or multi-agent)
    4. Return task + tasks_planned for graph routing
    """
    query = state.get("query", state["original_query"])
    summary = state.get("summary_text", "")
    print(f"[Orchestrator Plan] Analyzing: {query[:80]}...")

    # Step 1: Classify task
    if len(query.split()) >= 100:
        task = "Scenario"
        print("[Orchestrator] Long query (100+ words) -> Scenario")
    else:
        task = _classify_task(query, chat_summary=summary if summary else None)

    # Step 2: Handle non-legal
    if task == "Non_legal":
        return {
            "task": task,
            "tasks_planned": [],
            "is_blocked": True,
            "block_reason": "This query is not related to legal matters. Please ask a legal question.",
        }

    # Step 3: Plan agents
    tasks_planned = _plan_agents(query, task)
    print(f"[Orchestrator Plan] Task={task}, Agents={tasks_planned}")

    return {
        "task": task,
        "tasks_planned": tasks_planned,
    }


async def orchestrator_synthesize_node(state: LegalAgentState) -> dict:
    """Phase 2 — Merge results from all domain agents into final response.

    Single agent → pass through directly.
    Multiple agents → LLM synthesis into coherent, unified response.
    """
    agent_results: dict[str, AgentResult] = state.get("agent_results", {})
    query = state.get("query", state["original_query"])

    if not agent_results:
        return {
            "final_response": "No results were found for your query. Please try rephrasing.",
            "source_metadata": {"title": "No Results", "content": [], "docLink": None},
        }

    # Filter out empty/errored results
    valid_results = {
        name: r for name, r in agent_results.items()
        if r.content and not r.error
    }

    if not valid_results:
        return {
            "final_response": "The agents could not find relevant information. Please try a different query.",
            "source_metadata": {"title": "No Results", "content": [], "docLink": None},
        }

    # Single agent — pass through directly (no synthesis overhead)
    if len(valid_results) == 1:
        result = next(iter(valid_results.values()))
        source_meta = _build_source_metadata(result)
        return {
            "final_response": result.content,
            "source_metadata": source_meta,
            "tokens_consumed": result.tokens_consumed,
        }

    # Multiple agents — synthesize with LLM
    print(f"[Orchestrator Synthesize] Merging {len(valid_results)} agent results")

    agent_results_text = ""
    total_tokens = 0
    all_sources = []

    for name, result in valid_results.items():
        agent_results_text += f"\n\n### {name.upper()} AGENT RESULTS:\n{result.content}"
        total_tokens += result.tokens_consumed
        all_sources.extend(result.sources)

    try:
        llm = get_gemini_flash(temperature=0.2)
        prompt = ChatPromptTemplate.from_template(SYNTHESIS_PROMPT)
        chain = prompt | llm

        response = chain.invoke({
            "query": query,
            "agent_results": agent_results_text,
        })

        synthesized = response.content
        synth_tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            synth_tokens = response.usage_metadata.get("total_tokens", 0)

        total_tokens += synth_tokens

    except Exception as e:
        print(f"[Orchestrator Synthesize] LLM synthesis failed, concatenating: {e}")
        parts = []
        for name, result in valid_results.items():
            parts.append(result.content)
        synthesized = "\n\n---\n\n".join(parts)

    # Build merged source metadata
    source_meta = {
        "title": all_sources[0].title if all_sources else "Legal Analysis",
        "content": [s.title for s in all_sources if s.title],
        "docLink": next((s.doc_link for s in all_sources if s.doc_link), None),
    }

    return {
        "final_response": synthesized,
        "source_metadata": source_meta,
        "tokens_consumed": total_tokens,
    }


def _build_source_metadata(result: AgentResult) -> dict:
    """Build source metadata dict from a single agent result."""
    if not result.sources:
        return {
            "title": "Disclaimer",
            "content": ["AI-generated response based on Legal Intelligence"],
            "docLink": None,
        }

    primary = result.sources[0]
    return {
        "title": primary.title or "Legal Analysis",
        "content": [s.title for s in result.sources if s.title] or primary.content,
        "docLink": primary.doc_link,
    }
