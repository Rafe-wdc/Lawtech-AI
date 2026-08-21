"""Shared state schema for the LangGraph multi-agent system.

LangGraph requires TypedDict for state schemas (not dataclass).
Agents return partial dicts — LangGraph merges them into the full state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Annotated
from langchain_core.messages import BaseMessage
from langgraph.graph import MessagesState

from core.source_registry import SourceRegistry, merge_source_registries


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


# Sentinel key: when a state update contains it, the reducer discards
# `existing` and starts fresh from just the other keys in `new`. Turn-
# boundary nodes (memory_node) emit this to reset agent_results across
# graph invocations without breaking within-turn parallel fan-out.
# Without the reset, the checkpointer persists agent_results across turns
# and the synth layer sees stale prior-turn results as if fresh
# (Break #2 in tests/multilingual_test_2026_08_17/pipeline_investigation.md).
_RESET_AGENT_RESULTS = "__RESET_TURN__"


def _merge_agent_results(existing: dict, new: dict) -> dict:
    """Merge new agent results into existing dict without overwriting.

    Special case: when `new` contains the reset sentinel `__RESET_TURN__`,
    discard `existing` and keep only the OTHER keys from `new` (the sentinel
    itself is stripped). This lets the memory node clear stale prior-turn
    results at each turn boundary while preserving within-turn parallel-
    fan-out merge semantics that every domain agent depends on.
    """
    if _RESET_AGENT_RESULTS in new:
        return {k: v for k, v in new.items() if k != _RESET_AGENT_RESULTS}
    return {**existing, **new}


# Reset sentinel object for source_metadata. A list-of-dicts field can't use
# a key-based sentinel like `_RESET_AGENT_RESULTS`; a single-element list
# containing exactly this sentinel is treated as "reset" by the reducer.
_RESET_SOURCE_METADATA = {"__RESET_SOURCE_METADATA__": True}


def _cap_source_metadata(existing: list, new: list) -> list:
    """Custom reducer: append new sources but cap total to last 100 entries.

    Prevents unbounded state growth in multi-turn threads where each turn
    adds sources from multiple agents (typically 20-50 per turn).

    Special case: when `new` is exactly `[_RESET_SOURCE_METADATA]`, discard
    `existing` and return an empty list. Turn-boundary nodes emit this to
    prevent prior-turn sources from leaking into the current turn's payload
    (companion fix to `_RESET_AGENT_RESULTS`).
    """
    if new == [_RESET_SOURCE_METADATA]:
        return []
    combined = existing + new
    return combined[-100:] if len(combined) > 100 else combined


def _sum_tokens(existing: int, new: int) -> int:
    """Custom reducer: accumulate token counts across agents."""
    return existing + new


# Cap on the `messages` list. MessagesState uses add_messages as its
# built-in reducer, which appends unbounded — multi-turn threads that
# never checkpoint-out will grow the list forever, bloating both the
# in-request state and every serialised checkpoint. Cap after the
# add_messages semantics so we always keep the freshest N.
_MESSAGES_CAP = 40   # ~20 turns of user+AI exchanges

def _capped_add_messages(existing, new):
    """Compose add_messages semantics with a hard cap on total messages."""
    # Lazy import — langgraph is imported at graph.py time, avoiding
    # a circular hit at state.py import.
    from langgraph.graph.message import add_messages
    merged = add_messages(existing, new)
    if isinstance(merged, list) and len(merged) > _MESSAGES_CAP:
        return merged[-_MESSAGES_CAP:]
    return merged


class LegalAgentState(MessagesState):
    """Full shared state for the legal multi-agent graph.

    MessagesState provides: messages (with add_messages reducer).
    We override the reducer with `_capped_add_messages` so long threads
    don't accumulate unbounded checkpointer bloat.

    Each agent returns a partial dict with only the fields it updates.
    Fields with Annotated reducers (agent_results, tokens_consumed)
    are merged/accumulated instead of overwritten.
    """
    # Override the base MessagesState reducer with a capped variant.
    messages: Annotated[list[BaseMessage], _capped_add_messages]

    # Query lifecycle
    original_query: str
    query: str
    user_context: str           # Long-form context extracted from queries >5K chars (pasted docs/contracts)
    thread_id: str | None
    unique_string: str | None
    user_language: str          # ISO 639-1 code detected from original_query, default "en"
    user_language_source: str   # "client" (explicit preferred_language on the
                                # request) | "detected" (langdetect + script
                                # markers). Consumers that want to second-guess
                                # the language MUST NOT override a client
                                # preference — check this first.

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

    # Previous-turn typed state (Level 1 of the follow-up simplification —
    # docs/followup_pipeline_simplification_plan.md). Populated by memory_node
    # from ChatHistoryResult; consumers (intent extractor, rewriter, classifier,
    # drafting fast-path) read these to inherit the LAST turn's decisions
    # instead of re-deriving them from chat-history text. All default to
    # empty/None on fresh threads and on rows that predate the migration.
    previous_intent: Any        # config.intent.UserIntent | None (typed as Any
                                # to avoid an import cycle, matching user_intent above)
    previous_task: str          # e.g. "Drafting"; "" when no prior turn
    previous_artifact_kind: str # "draft" when the prior turn produced a
                                # modifiable draft artefact; "" otherwise
    previous_artifact_content: str  # the prior turn's ai_response (raw
                                    # content of the modifiable artefact)

    # Agent results — uses custom merge so parallel agents don't overwrite each other
    agent_results: Annotated[dict[str, AgentResult], _merge_agent_results]

    # Guardrail state
    is_blocked: bool
    block_reason: str | None

    # File attachments (inline chat uploads)
    file_context: dict | None

    # Third-party integration content (Google Docs, Notion)
    integration_context: dict | None

    # Per-request drafting flag: include the REFERENCES & CITATIONS appendix
    # (fans out Scenario/Legislation/Judgment alongside Drafting). None means
    # "use DRAFTING_CITE_APPENDIX_DEFAULT from settings".
    cite_appendix: bool | None

    # Regenerate (Sagar bug #5, 2026-06-16): when set, the orchestrator
    # short-circuits the full agent pipeline and runs ONE refinement call
    # over `regenerate_of` so "regenerate" produces a polished version of
    # the previous answer instead of a completely new one.
    regenerate_of: str | None

    # Final output
    final_response: str
    source_metadata: Annotated[list[dict[str, Any]], _cap_source_metadata]
    tokens_consumed: Annotated[int, _sum_tokens]

    # Citation grounding — one registry per request, populated by every
    # retrieval-side agent before it calls its generator. Consumed by
    # generator prompts, orchestrator merge, and self_refine critic/refiner
    # to ensure every citation, quoted statute, and PDF URL in the final
    # answer is traceable to an actual retrieval — never invented from
    # training memory. See core/source_registry.py.
    source_registry: Annotated[SourceRegistry, merge_source_registries]


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
    """Helper for agents to access file context from state.

    Phase F (RAG attachment routing plan, 2026-06-28): single text pipeline.
    File content lives in ChromaDB; agents read via get_full_attachment.
    The legacy inline_text + gemini_file_parts + image_data channels were
    removed in this phase.

    2026-06-29: ``extracted_texts`` carries the raw per-file text the file
    processor already extracted (before chunking + Chroma embedding). It is
    the Chroma-independent path agents should prefer for full-document use
    cases like Drafting — and the only path that survives when the Chroma
    embed step times out under pool exhaustion.
    """
    chromadb_collections: list[str] = field(default_factory=list)
    file_names: list[str] = field(default_factory=list)
    summary: str = ""
    extracted_texts: list[dict] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        """True if any file content is available (via Chroma or raw text)."""
        return bool(self.chromadb_collections) or bool(self.extracted_texts)

    @property
    def has_extracted_text(self) -> bool:
        """True if the raw per-file extracted text is available in state."""
        return any((e.get("text") or "").strip() for e in self.extracted_texts)

    @classmethod
    def from_state(cls, state: dict) -> FileContextData | None:
        """Deserialize file_context dict from state, or None if absent.

        Tolerates legacy keys (inline_text, gemini_file_parts, image_data)
        in persisted state by silently dropping them — no migration needed.
        """
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
