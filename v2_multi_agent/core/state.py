"""Shared state schema for the LangGraph multi-agent system.

LangGraph requires TypedDict for state schemas (not dataclass).
Agents return partial dicts — LangGraph merges them into the full state.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Any, Literal, Annotated
from langchain_core.messages import BaseMessage
from langgraph.graph import MessagesState


TaskType = Literal[
    "Drafting", "Judgment", "Legislation", "Constitution",
    "Scenario", "Maxim", "Newacts", "Legal_Concepts",
    "SCI_Judgment",
    "Non_legal", "Other",
]


@dataclass
class SourceMetadata:
    """Metadata about a retrieved source document."""
    # Common fields (all agents)
    source_type: str = ""              # "judgment", "legislation", "newacts", "drafting",
                                       # "sci_judgment", "scenario", "constitution",
                                       # "maxim", "legal_concepts", "document"
    title: str | None = None
    content: list[str] = field(default_factory=list)
    doc_link: str | None = None
    file_name: str | None = None
    agent_name: str = ""
    relevance_score: float | None = None

    # Judgment fields
    court_name: str | None = None
    year: int | None = None
    petitioner_names: list[str] = field(default_factory=list)
    respondent_names: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    acts_or_sections_invoked: list[str] = field(default_factory=list)

    # SCI Judgment fields
    case_no: str | None = None
    judgment_date: str | None = None
    bench: str | None = None
    judgment_by: str | None = None
    pdf_links: list[dict] = field(default_factory=list)
    parties: str | None = None
    db_id: str | None = None

    # Legislation / Newacts fields
    section_number: str | None = None
    act_name: str | None = None

    # Drafting fields
    template_type: str | None = None

    # Scenario fields (web search grounding)
    web_url: str | None = None
    web_title: str | None = None


@dataclass
class AgentResult:
    """Result returned by a domain agent."""
    agent_name: str = ""
    content: str = ""
    sources: list[SourceMetadata] = field(default_factory=list)
    tokens_consumed: int = 0
    error: str | None = None


def _merge_agent_results(existing: dict, new: dict) -> dict:
    """Custom reducer: merge new agent results into existing dict without overwriting."""
    return {**existing, **new}


def _sum_tokens(existing: int, new: int) -> int:
    """Custom reducer: accumulate token counts across agents."""
    return existing + new


class LegalAgentState(MessagesState):
    """Full shared state for the legal multi-agent graph.

    MessagesState provides: messages (with add_messages reducer)

    Each agent returns a partial dict with only the fields it updates.
    Fields with Annotated reducers (agent_results, tokens_consumed)
    are merged/accumulated instead of overwritten.
    """
    # Query lifecycle
    original_query: str
    query: str
    thread_id: str | None
    unique_string: str | None

    # Task routing
    task: TaskType | None
    tasks_planned: list[str]

    # Conversation context
    chat_history: list[BaseMessage]
    summary_text: str

    # Agent results — uses custom merge so parallel agents don't overwrite each other
    agent_results: Annotated[dict[str, AgentResult], _merge_agent_results]

    # Guardrail state
    is_blocked: bool
    block_reason: str | None

    # Final output
    final_response: str
    source_metadata: Annotated[list[dict[str, Any]], operator.add]
    related_sections: Annotated[list[dict[str, Any]], operator.add]
    tokens_consumed: Annotated[int, _sum_tokens]
