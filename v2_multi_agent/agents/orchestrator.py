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
from core.logger import get_logger, log_time
from config.prompts import TASK_CLASSIFICATION_PROMPT, SYNTHESIS_PROMPT

log = get_logger("Orchestrator")


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
    with log_time(log, "Task classification"):
        prompt = PromptTemplate.from_template(TASK_CLASSIFICATION_PROMPT)
        llm = get_gpt4o().with_structured_output(IdentifyTaskSchema)

        formatted = prompt.format(query=query, chat_summary=chat_summary or "")
        result = llm.invoke(formatted)
    log.info("Task classified", task=result.task, query=query[:80])
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
        log.debug("Simple task mapping used", task=task,
                  agents=simple_mapping[task])
        return simple_mapping[task]

    # For queries with 100+ words, always go to Scenario (complex scenario)
    word_count = len(query.split())
    if word_count >= 100:
        log.info("Long query detected, routing to Scenario",
                 word_count=word_count)
        return ["Scenario"]

    # Try multi-agent planning with LLM
    try:
        with log_time(log, "Multi-agent planning"):
            llm = get_gpt4o().with_structured_output(AgentPlan)
            prompt = ChatPromptTemplate.from_template(PLAN_PROMPT)
            chain = prompt | llm
            plan = chain.invoke({"query": query, "task": task})
        agents = plan.agents[:3]  # max 3 agents
        log.info("Agent plan created",
                 agents=agents, reasoning=plan.reasoning[:120])
        return agents if agents else [task]
    except Exception as e:
        log.error("Planning failed, falling back to single agent",
                  error=str(e), fallback=task)
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
    log.info("Plan phase started", query=query[:100],
             has_summary=bool(summary))

    # Step 1: Classify task
    word_count = len(query.split())
    if word_count >= 100:
        task = "Scenario"
        log.info("Long query bypass", word_count=word_count, task=task)
    else:
        task = _classify_task(query, chat_summary=summary if summary else None)

    # Step 2: Handle non-legal
    if task == "Non_legal":
        log.warning("Non-legal query blocked", query=query[:80])
        return {
            "task": task,
            "tasks_planned": [],
            "is_blocked": True,
            "block_reason": "This query is not related to legal matters. Please ask a legal question.",
        }

    # Step 3: Plan agents
    tasks_planned = _plan_agents(query, task)
    log.info("Plan phase completed",
             task=task, agents_planned=tasks_planned,
             agent_count=len(tasks_planned))

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

    log.info("Synthesize phase started",
             agents_received=list(agent_results.keys()))

    if not agent_results:
        log.warning("No agent results to synthesize")
        return {
            "final_response": "No results were found for your query. Please try rephrasing.",
            "source_metadata": [],
        }

    # Filter out empty/errored results
    valid_results = {
        name: r for name, r in agent_results.items()
        if r.content and not r.error
    }
    errored = {
        name: r.error for name, r in agent_results.items()
        if r.error
    }
    empty = [
        name for name, r in agent_results.items()
        if not r.content and not r.error
    ]

    if errored:
        log.warning("Some agents returned errors", errored_agents=errored)
    if empty:
        log.debug("Some agents returned empty results", empty_agents=empty)

    if not valid_results:
        log.warning("All agent results were empty or errored")
        return {
            "final_response": "The agents could not find relevant information. Please try a different query.",
            "source_metadata": [],
        }

    # Pass through related_sections from state (populated by newacts agent)
    related_sections = state.get("related_sections", [])

    # Single agent — pass through directly (no synthesis overhead)
    if len(valid_results) == 1:
        name, result = next(iter(valid_results.items()))
        log.info("Single agent pass-through",
                 agent=name, content_len=len(result.content),
                 tokens=result.tokens_consumed)
        return_dict = {
            "final_response": result.content,
            "source_metadata": _serialize_sources(result),
            "tokens_consumed": result.tokens_consumed,
        }
        if related_sections:
            return_dict["related_sections"] = related_sections
        return return_dict

    # Multiple agents — synthesize with LLM
    log.info("Multi-agent synthesis starting",
             agents=list(valid_results.keys()),
             content_lengths={n: len(r.content) for n, r in valid_results.items()})

    agent_results_text = ""
    total_tokens = 0
    all_serialized_sources = []

    for name, result in valid_results.items():
        agent_results_text += f"\n\n### {name.upper()} AGENT RESULTS:\n{result.content}"
        total_tokens += result.tokens_consumed
        all_serialized_sources.extend(_serialize_sources(result))

    try:
        with log_time(log, "LLM synthesis"):
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
        log.info("Synthesis completed",
                 synthesized_len=len(synthesized),
                 synthesis_tokens=synth_tokens, total_tokens=total_tokens)

    except Exception as e:
        log.error("LLM synthesis failed, concatenating results", error=str(e))
        parts = []
        for name, result in valid_results.items():
            parts.append(result.content)
        synthesized = "\n\n---\n\n".join(parts)

    return_dict = {
        "final_response": synthesized,
        "source_metadata": all_serialized_sources,
        "tokens_consumed": total_tokens,
    }
    if related_sections:
        return_dict["related_sections"] = related_sections
    return return_dict


def _serialize_sources(result: AgentResult) -> list[dict]:
    """Serialize all SourceMetadata objects from an agent result into dicts."""
    if not result.sources:
        return []

    serialized = []
    for s in result.sources:
        d = {
            "source_type": s.source_type,
            "title": s.title,
            "content": s.content,
            "doc_link": s.doc_link,
            "file_name": s.file_name,
            "agent_name": s.agent_name or result.agent_name,
            "relevance_score": s.relevance_score,
        }
        # Add type-specific fields only when populated
        for field_name in [
            "court_name", "year", "petitioner_names", "respondent_names",
            "keywords", "acts_or_sections_invoked",
            "case_no", "judgment_date", "bench", "judgment_by",
            "pdf_links", "parties", "db_id",
            "section_number", "act_name",
            "template_type",
            "web_url", "web_title",
        ]:
            val = getattr(s, field_name, None)
            if val is not None and val != "" and val != []:
                d[field_name] = val
        serialized.append(d)
    return serialized
