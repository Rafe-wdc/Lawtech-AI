"""Shared state schema for the LangGraph multi-agent system.

LangGraph requires TypedDict for state schemas (not dataclass).
Agents return partial dicts — LangGraph merges them into the full state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Annotated
from langchain_core.messages import BaseMessage
from langgraph.graph import MessagesState


TaskType = Literal[
    "Drafting", "Judgment", "Legislation", "Constitution",
    "Scenario", "Maxim", "Newacts", "Legal_Concepts",
    "SCI_Judgment", "GST_Judgment", "Document",
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

    # GST Judgment fields (AAAR appellate orders)
    state_ut: str | None = None
    brief_of_order: str | None = None
    ar_order_no_date: str | None = None

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
    retry_attempted: bool = False
    fallback_used: bool = False
    # Diagnostic side-channel surfaced to the response payload — drafting validator
    # warnings (mojibake fixes, statute traps), search-fallback notes, etc. Free-form.
    meta: dict = field(default_factory=dict)


def _merge_agent_results(existing: dict, new: dict) -> dict:
    """Custom reducer: merge new agent results into existing dict without overwriting."""
    return {**existing, **new}


def _cap_source_metadata(existing: list, new: list) -> list:
    """Custom reducer: append new sources but cap total to last 100 entries.

    Prevents unbounded state growth in multi-turn threads where each turn
    adds sources from multiple agents (typically 20-50 per turn).
    """
    combined = existing + new
    return combined[-100:] if len(combined) > 100 else combined


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
    user_context: str           # Long-form context extracted from queries >5K chars (pasted docs/contracts)
    thread_id: str | None
    unique_string: str | None
    user_language: str          # ISO 639-1 code detected from original_query, default "en"

    # Task routing
    task: TaskType | None
    tasks_planned: list[str]
    agent_queries: dict[str, str]   # per-agent rewritten queries
    response_instructions: str      # user's expected output format/language/style
                                    # (legacy free-form text — superseded by
                                    # user_intent in Phase 2; kept until Phase 4)

    # Structured user-intent extracted by the intent extractor (Phase 1+).
    # When INTENT_EXTRACTOR_V2 is False, this stays None and downstream
    # consumers fall back to response_instructions + the legacy regex
    # heuristics. See docs/intent_layer_implementation_plan.md.
    user_intent: Any  # config.intent.UserIntent | None — typed as Any to avoid
                     # an import cycle (state.py is imported very early)

    # Conversation context
    chat_history: list[BaseMessage]
    summary_text: str

    # Agent results — uses custom merge so parallel agents don't overwrite each other
    agent_results: Annotated[dict[str, AgentResult], _merge_agent_results]

    # Guardrail state
    is_blocked: bool
    block_reason: str | None

    # File attachments (inline chat uploads)
    file_context: dict | None

    # Third-party integration content (Google Docs, Notion)
    integration_context: dict | None

    # Draft continuation (for incomplete drafts that need retry)
    draft_continuation: dict[str, Any] | None

    # Per-request drafting flag: include the REFERENCES & CITATIONS appendix
    # (fans out Scenario/Legislation/Judgment alongside Drafting). None means
    # "use DRAFTING_CITE_APPENDIX_DEFAULT from settings".
    cite_appendix: bool | None

    # Final output
    final_response: str
    source_metadata: Annotated[list[dict[str, Any]], _cap_source_metadata]
    tokens_consumed: Annotated[int, _sum_tokens]


def get_query_with_context(state: dict) -> tuple[str, str]:
    """Get the routing query and full user context for LLM generation.

    For normal queries (<5K chars): returns (query, "")
    For long queries (>5K chars): returns (concise_question, full_pasted_text)

    Agents should:
    - Use query for search/retrieval
    - Include user_context in the LLM generation prompt if non-empty
    """
    query = state.get("query", state.get("original_query", ""))
    user_context = state.get("user_context", "")
    return query, user_context


@dataclass
class FileContextData:
    """Helper for agents to access file context from state."""
    inline_text: str = ""
    # Gemini Files API parts — [{file_data: {file_uri, mime_type}, name}]
    gemini_file_parts: list[dict] = field(default_factory=list)
    # Legacy base64 image_data (backward compat with old persisted state)
    image_data: list[dict] = field(default_factory=list)
    chromadb_collections: list[str] = field(default_factory=list)
    file_names: list[str] = field(default_factory=list)
    summary: str = ""

    @property
    def has_content(self) -> bool:
        """True if any file content is available."""
        return bool(
            self.inline_text
            or self.gemini_file_parts
            or self.image_data
            or self.chromadb_collections
        )

    @property
    def all_gemini_parts(self) -> list[dict]:
        """All Gemini-compatible content parts (URI-based + legacy base64).

        Returns gemini_file_parts first, then any legacy image_data converted
        to inline_data format for backward compatibility.
        """
        parts: list[dict] = list(self.gemini_file_parts)
        for img in self.image_data:  # backward compat
            if img.get("base64"):
                parts.append({
                    "inline_data": {
                        "data": img["base64"],
                        "mime_type": img.get("mime", "image/jpeg"),
                    },
                    "name": img.get("name", "image"),
                })
        return parts

    @classmethod
    def from_state(cls, state: dict) -> FileContextData | None:
        """Deserialize file_context dict from state, or None if absent."""
        fc = state.get("file_context")
        if not fc:
            return None
        return cls(**{k: v for k, v in fc.items() if k in cls.__dataclass_fields__})


@dataclass
class IntegrationContextData:
    """Helper for agents to access third-party integration content
    (Google Docs, Notion pages) fetched via the FSD chat service.
    """
    provider: str = ""               # "google" | "notion"
    title: str = ""
    content: str = ""                # Combined text (may span multiple docs)
    url: str = ""
    metadata: dict = field(default_factory=dict)
    documents: list[dict] = field(default_factory=list)  # Per-doc summary when multiple URLs

    @property
    def has_content(self) -> bool:
        return bool(self.content)

    def as_prompt_prefix(self) -> str:
        """Format the integration content as a prompt prefix for LLM injection."""
        if not self.content:
            return ""
        label = f"{self.provider.title()} document ({self.title})" if self.provider else self.title
        return f"Reference content from user's {label}:\n{self.content}\n\n"

    @classmethod
    def from_state(cls, state: dict) -> IntegrationContextData | None:
        """Deserialize integration_context dict from state, or None if absent."""
        ic = state.get("integration_context")
        if not ic:
            return None
        return cls(**{k: v for k, v in ic.items() if k in cls.__dataclass_fields__})
