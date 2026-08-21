"""Shared Tools: Orchestrator operations.

Reusable @tool functions for task classification, execution planning,
agent delegation, result merging, and user clarification.

Used by the Orchestrator Agent (#1) for multi-agent coordination.

Uses: GPT-4o for classification/planning, Gemini Flash Lite for synthesis
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field
from langchain.tools import tool
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate

from core.clients import get_gemini_flash
from core.logger import get_logger
from config.prompts import SYNTHESIS_PROMPT

log = get_logger("Orchestrator")


# --- Structured Output Schemas ---

class IdentifyTaskSchema(BaseModel):
    task: Literal[
        "Drafting", "Judgment", "Legislation", "Constitution",
        "Scenario", "Maxim", "Newacts", "Legal_Concepts",
        "Non_legal", "Other",
    ] = Field(..., description="The primary legal task type")


class AgentPlan(BaseModel):
    agents: list[str] = Field(
        ...,
        description="List of agent names to invoke: Legislation, Judgment, Newacts, Drafting, Scenario, Constitution, Maxim, Legal_Concepts",
    )
    reasoning: str = Field(..., description="Brief reasoning for agent selection")


class ClarificationRequest(BaseModel):
    question: str = Field(..., description="The clarification question to ask the user")
    options: list[str] = Field(default_factory=list, description="Suggested options for the user")


# --- Prompts ---
#
# NOTE: The legacy `_PLAN_PROMPT` and `get_execution_plan` tool were
# retired 2026-08-19 (audit finding F8). They wrapped the older
# TASK_CLASSIFICATION_PROMPT and were never bound to any live agent —
# the orchestrator node calls `_classify_and_plan` / `_dynamic_plan`
# directly, not via tools. The `_CLARIFICATION_PROMPT` below is still
# used by `request_clarification`.


_CLARIFICATION_PROMPT = """You are a legal AI assistant. The user's query is ambiguous and needs clarification before proceeding.

Analyze the query and generate a clear, specific clarification question with suggested options.

Query: {query}
Ambiguity: {ambiguity}

Generate a clarification question and 2-4 options."""


# --- Tool Functions ---


@tool
def delegate_to_agent(agent_name: str, query: str, context: str = "") -> dict:
    """Create a delegation instruction for a single domain agent.

    Prepares the delegation payload that the graph will use to
    route the query to a specific domain agent.

    Args:
        agent_name: The agent to delegate to (Legislation, Judgment, Newacts, Drafting, Scenario, Constitution, Maxim, Legal_Concepts)
        query: The query to send to the agent
        context: Additional context from other agents or conversation history

    Returns:
        Dict with keys: agent (str), query (str), context (str), status (str)
    """
    valid_agents = {
        "Legislation", "Judgment", "Newacts", "Drafting",
        "Scenario", "Constitution", "Maxim", "Legal_Concepts",
    }

    if agent_name not in valid_agents:
        return {
            "agent": agent_name,
            "query": query,
            "context": context,
            "status": f"error: unknown agent '{agent_name}'",
        }

    return {
        "agent": agent_name,
        "query": query,
        "context": context,
        "status": "delegated",
    }


@tool
def delegate_parallel(agents_with_queries: list[dict]) -> dict:
    """Create parallel delegation instructions for multiple domain agents.

    Prepares delegation payloads for running multiple agents simultaneously.
    Each entry should have 'agent' and 'query' keys.

    Args:
        agents_with_queries: List of dicts with 'agent' and 'query' keys,
            e.g. [{"agent": "Newacts", "query": "..."}, {"agent": "Judgment", "query": "..."}]

    Returns:
        Dict with keys: delegations (list of delegation dicts), count (int)
    """
    delegations = []

    for entry in agents_with_queries[:3]:  # max 3 agents
        agent_name = entry.get("agent", "")
        query = entry.get("query", "")
        context = entry.get("context", "")

        delegations.append({
            "agent": agent_name,
            "query": query,
            "context": context,
            "status": "delegated",
        })

    return {
        "delegations": delegations,
        "count": len(delegations),
    }


@tool
def merge_results(
    agent_outputs: list[dict],
    query: str,
    strategy: str = "llm",
) -> dict:
    """Merge results from multiple domain agents into a single coherent response.

    Strategies:
    - "llm": Use LLM to synthesize results into unified response
    - "concatenate": Simple concatenation with section dividers

    Args:
        agent_outputs: List of agent result dicts with 'agent_name' and 'content' keys
        query: The original user query for context
        strategy: Merge strategy — "llm" (default) or "concatenate"

    Returns:
        Dict with keys: merged_content (str), agents_merged (list), tokens_consumed (int)
    """
    if not agent_outputs:
        return {
            "merged_content": "No results were found for your query.",
            "agents_merged": [],
            "tokens_consumed": 0,
        }

    # Filter out empty results
    valid = [r for r in agent_outputs if r.get("content")]
    if not valid:
        return {
            "merged_content": "The agents could not find relevant information.",
            "agents_merged": [],
            "tokens_consumed": 0,
        }

    # Single result — pass through
    if len(valid) == 1:
        return {
            "merged_content": valid[0]["content"],
            "agents_merged": [valid[0].get("agent_name", "unknown")],
            "tokens_consumed": 0,
        }

    agent_names = [r.get("agent_name", "unknown") for r in valid]

    if strategy == "concatenate":
        parts = [r["content"] for r in valid]
        return {
            "merged_content": "\n\n---\n\n".join(parts),
            "agents_merged": agent_names,
            "tokens_consumed": 0,
        }

    # LLM synthesis
    agent_results_text = ""
    for r in valid:
        name = r.get("agent_name", "unknown")
        agent_results_text += f"\n\n### {name.upper()} AGENT RESULTS:\n{r['content']}"

    try:
        llm = get_gemini_flash(temperature=0.2)
        prompt = ChatPromptTemplate.from_template(SYNTHESIS_PROMPT)
        chain = prompt | llm

        response = chain.invoke({
            "query": query,
            "agent_results": agent_results_text,
        })

        tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            tokens = response.usage_metadata.get("total_tokens", 0)

        return {
            "merged_content": response.text,
            "agents_merged": agent_names,
            "tokens_consumed": tokens,
        }
    except Exception as e:
        log.error(f"LLM synthesis failed: {e}")
        parts = [r["content"] for r in valid]
        return {
            "merged_content": "\n\n---\n\n".join(parts),
            "agents_merged": agent_names,
            "tokens_consumed": 0,
        }


@tool
def request_clarification(query: str, ambiguity: str = "") -> dict:
    """Generate a clarification question when the user's query is ambiguous.

    Creates a specific question with suggested options to help resolve
    query ambiguity before routing to domain agents.

    Args:
        query: The ambiguous user query
        ambiguity: Description of what's ambiguous (optional)

    Returns:
        Dict with keys: question (str), options (list of str)
    """
    if not ambiguity:
        ambiguity = "The query could be interpreted in multiple ways"

    try:
        llm = get_gemini_flash(temperature=0.3).with_structured_output(ClarificationRequest)
        prompt = ChatPromptTemplate.from_template(_CLARIFICATION_PROMPT)
        chain = prompt | llm
        result = chain.invoke({"query": query, "ambiguity": ambiguity})
        return {
            "question": result.question,
            "options": result.options,
        }
    except Exception as e:
        log.error(f"Clarification generation failed: {e}")
        return {
            "question": f"Could you please clarify your query? {ambiguity}",
            "options": [],
        }
