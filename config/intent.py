"""User intent schema — structured representation of "how the user wants the answer".

Replaces the regex + free-form `response_instructions: str` pattern with typed
Pydantic fields. One LLM call (the intent extractor) populates this object early
in the orchestrator pipeline; every downstream consumer (synthesis, domain
agents, language localization) reads typed fields instead of pattern-matching
on natural-language text.

See docs/intent_layer_implementation_plan.md for the full design + rollout plan.

Design contract:
    1. Adding a new format = add an enum value + one branch in the consumer.
       No regex changes. No prompt rewrites.
    2. Adding a new language = already supported. Map ISO code → display name in
       LANG_NAMES. The LLM extractor understands intent regardless of input
       language (the schema is provider-agnostic).
    3. Failure mode = `confidence=0.0` + safe defaults. Downstream consumers
       MUST handle `default_intent()` gracefully and may consult the legacy
       regex as a fallback during the migration window (Phase 1 → Phase 4).
    4. Schema versioning via `schema_version: int`. When we add fields, we bump
       the version. Log-replay tooling reads the version to pick the right
       reconstruction logic.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Response format enum — what shape does the user want?
# ---------------------------------------------------------------------------

class ResponseFormat(str, Enum):
    """The shape of the response surface.

    The default is PROSE — paragraphs with optional `## headings`. Every other
    value is a user-explicit deviation from prose. Downstream consumers should
    treat `format_explicit=False` + PROSE as "no opinion expressed" and apply
    their own per-agent defaults (e.g. Newacts single-section uses PROSE,
    Newacts multi-section uses TABLE — see NEWACTS_SYSTEM_PROMPT).
    """

    PROSE = "prose"                    # paragraphs + optional ## headings (default)
    BULLET_LIST = "bullet_list"        # explicit "- " list, one item per line
    NUMBERED_LIST = "numbered_list"    # explicit "1. " list
    TABLE = "table"                    # single markdown table
    COMPARISON = "comparison_table"    # multi-column side-by-side comparison
    OUTLINE = "outline"                # ## / ### nested structure, no body prose
    DRAFT = "draft"                    # full legal document (handled by Drafting agent)
    JSON = "json"                      # machine-readable / API-style payload


# ---------------------------------------------------------------------------
# Language registry — ISO 639-1 → display name
#
# Kept in sync with core/language.SUPPORTED_LANGUAGES. Import-time check below
# guards against drift between the two registries.
# ---------------------------------------------------------------------------

LANG_NAMES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "bn": "Bengali",
    "te": "Telugu",
    "mr": "Marathi",
    "ta": "Tamil",
    "kn": "Kannada",
    "ml": "Malayalam",
    "gu": "Gujarati",
    "pa": "Punjabi",
    "ur": "Urdu",
    "or": "Odia",
    "as": "Assamese",
    "sa": "Sanskrit",
}


def _check_lang_registry_sync() -> None:
    """Fail fast at import time if LANG_NAMES drifts from core.language.

    Drift between these two registries causes hard-to-diagnose bugs where the
    extractor produces an ISO code that downstream code can't render. Catch
    it on the first import after a registry change.
    """
    try:
        from core.language import SUPPORTED_LANGUAGES
    except ImportError:
        return  # core.language not importable at this moment; skip the check
    extra_here = set(LANG_NAMES) - set(SUPPORTED_LANGUAGES)
    missing_here = set(SUPPORTED_LANGUAGES) - set(LANG_NAMES)
    if extra_here or missing_here:
        raise RuntimeError(
            "LANG_NAMES drifted from core.language.SUPPORTED_LANGUAGES. "
            f"In intent only: {sorted(extra_here)}. "
            f"In language only: {sorted(missing_here)}. "
            "Sync the two registries."
        )


_check_lang_registry_sync()


# ---------------------------------------------------------------------------
# The schema itself
# ---------------------------------------------------------------------------

class UserIntent(BaseModel):
    """All user directives extracted from the query in one shot.

    Populated by the intent extractor (agents/orchestrator._extract_user_intent
    once that lands in Phase 1). Read by:
      - the synthesis-template picker (orchestrator.orchestrator_synthesize_node)
      - the single-agent pass-through guard (orchestrator)
      - the per-agent system-prompt builder (each agents/*.py via
        config/prompts._format_intent_directives)
      - localize_prompt() — for language directives

    Field defaults are deliberately conservative — an empty UserIntent must
    behave identically to the legacy "no opinion expressed" path so the new
    field can land without touching downstream logic.
    """

    model_config = ConfigDict(use_enum_values=False, validate_assignment=True)

    # — Format
    response_format: ResponseFormat = Field(
        ResponseFormat.PROSE,
        description="The user's preferred response shape. Default PROSE.",
    )
    format_explicit: bool = Field(
        False,
        description="True iff the user explicitly named the format. False when "
                    "the extractor defaulted to PROSE because no directive was "
                    "given.",
    )
    table_columns: list[str] | None = Field(
        None,
        description="If response_format is TABLE or COMPARISON, the column "
                    "headers the user suggested (or None to let the consumer "
                    "decide). Capped to 8 columns to bound prompt size.",
        max_length=8,
    )

    # — Language
    language: str = Field(
        "en",
        description="ISO 639-1 code for the language the user wants the answer "
                    "in. NOT necessarily the language they typed the query in. "
                    "Must be in LANG_NAMES.",
        pattern=r"^[a-z]{2}$",
    )
    language_explicit: bool = Field(
        False,
        description="True iff the user explicitly named a target language "
                    "('in Hindi', 'मराठीत', 'Marathi mein'). False when the "
                    "language was inferred from the script the query was typed "
                    "in (auto-detect). Used to decide whether the language "
                    "directive overrides per-server defaults.",
    )

    # — Depth / length
    response_depth: Literal["brief", "standard", "detailed"] = Field(
        "standard",
        description="brief: target <200 words. standard: agent default. "
                    "detailed: target comprehensive coverage of all relevant "
                    "subsections / explanations.",
    )

    # — Content directives
    include_citations: bool = Field(
        True,
        description="Default True (legal AI; citations are expected). Only set "
                    "False if the user explicitly says 'no citations' / "
                    "'without references'.",
    )
    include_examples: bool = Field(
        False,
        description="True iff user explicitly asked for examples / illustrations / "
                    "practical scenarios.",
    )
    include_case_law: bool = Field(
        False,
        description="True iff user asked for case laws / precedents / judgments. "
                    "When True, the orchestrator should consider adding the "
                    "Judgment or SCI_Judgment agent to the plan.",
    )
    arguments_for_party: Literal["none", "plaintiff", "defendant", "both"] = Field(
        "none",
        description="If user asked to draft/argue 'on behalf of <party>', who? "
                    "Used by the Drafting agent's stance generator.",
    )

    # — Catchall for things outside the typed schema
    additional_instructions: str = Field(
        "",
        description="Free-text catchall for user directives that don't fit the "
                    "typed fields. Capped at 300 chars to bound prompt injection "
                    "attack surface — anything longer is truncated by the "
                    "extractor. Injected verbatim into downstream prompts after "
                    "wrapping with the injection guard from Round 4.",
        max_length=300,
    )

    # — Extraction metadata
    confidence: float = Field(
        1.0,
        ge=0.0,
        le=1.0,
        description="Extractor's self-reported confidence in the typed fields. "
                    "Below 0.7, downstream consumers SHOULD consult the legacy "
                    "regex fallback as a defense-in-depth check.",
    )

    schema_version: int = Field(
        1,
        description="Bumped when fields are added/changed in a way that affects "
                    "log replay. Read by tooling to pick the right reconstruction "
                    "logic.",
    )

    # — Convenience properties for the most common consumer checks

    @property
    def wants_table(self) -> bool:
        return self.response_format in (
            ResponseFormat.TABLE,
            ResponseFormat.COMPARISON,
        )

    @property
    def wants_list(self) -> bool:
        return self.response_format in (
            ResponseFormat.BULLET_LIST,
            ResponseFormat.NUMBERED_LIST,
        )

    @property
    def language_display_name(self) -> str:
        """Human-readable name of the response language, e.g. 'Hindi'."""
        return LANG_NAMES.get(self.language, "English")


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def default_intent() -> UserIntent:
    """A safe, opinionless intent — equivalent to the legacy "no directive" path.

    Use this when the extractor fails or is bypassed. Downstream consumers MUST
    treat this object identically to receiving no intent at all.
    """
    return UserIntent(confidence=0.0)
