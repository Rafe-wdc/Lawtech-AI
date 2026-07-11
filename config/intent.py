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
# Legal artifact type — specialized output kinds that need dedicated prompts.
#
# When `legal_artifact != NONE` the consuming agent should:
#   1. Use a tailored system prompt (CROSS_EXAMINATION_PROMPT, etc.) instead of
#      the generic Document/Drafting prompt.
#   2. Upgrade the model to Gemini 2.5 Pro with a sensible thinking budget for
#      strategic legal output.
#   3. Apply a post-generation quality gate (minimum word/question counts) and
#      retry once on under-quality outputs.
#
# Adding a new artifact = add an enum value + an entry in the agent's
# specialized-prompt picker + an entry in the quality-gate threshold table.
# No regex changes, no orchestrator changes.
# ---------------------------------------------------------------------------

class LegalArtifact(str, Enum):
    """Specialized legal-output kinds that warrant a dedicated handling path.

    NONE is the default — the agent produces generic Q&A or draft output
    based on its standard system prompt. Any other value tells the agent to
    pick a tailored prompt designed for that specific legal artifact, use
    an artifact-tuned model config, and enforce a post-generation quality
    gate with retry-on-fail.

    Phase A shipped CROSS_EXAMINATION; Phase B adds the rest below.
    Adding a new artifact = add an enum value + update the extraction prompt
    to detect it + add a specialized system prompt + add a row in
    _QUALITY_THRESHOLDS and _RETRY_PREAMBLE in agents/document.py +
    parametrized test cases.
    """

    NONE                = "none"                # generic — no specialized handling
    CROSS_EXAMINATION   = "cross_examination"   # cross-exam questions for a witness/document
    DEPOSITION_SUMMARY  = "deposition_summary"  # structured summary of a deposition / examination-in-chief
    CONTRACT_ANALYSIS   = "contract_analysis"   # risks + clauses + compliance review of a contract
    LEGAL_NOTICE_DRAFT  = "legal_notice"        # formal Indian-style legal notice draft
    OFFICE_APPLICATION  = "office_application"  # letter-format application to a non-court authority (RTI, govt dept, employer, bank, regulator) — NOT a court filing
    COMPLAINT_DRAFT     = "complaint_draft"     # complaint / petition draft based on attached facts
    WITNESS_PREP        = "witness_prep"        # prep YOUR witness for direct + anticipated cross
    OPENING_STATEMENT   = "opening_statement"   # opening statement for trial
    CLOSING_ARGUMENT    = "closing_argument"    # closing argument for trial


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

    strict_language: bool = Field(
        False,
        description="True iff the user demanded a strict / pure language mode "
                    "('only in Marathi', 'purely in Hindi', 'fakta marathit', "
                    "'मराठीतच', 'सिर्फ हिंदी में'). When True AND language is "
                    "non-English, downstream localize_prompt emits a stronger "
                    "instruction: no English NARRATIVE clauses in body prose "
                    "(no 'It is submitted that', 'as per', 'in accordance "
                    "with'), no English ceremonial-block labels, and no "
                    "English placeholder brackets — the target language must "
                    "carry the whole narrative. NUMERALS (Latin digits) and "
                    "FULL STATUTORY REFERENCES ('Section 138 of the Negotiable "
                    "Instruments Act, 1881') and CASE-LAW CITATIONS stay "
                    "English regardless of strict_language — those are the "
                    "fixed English anchors that apply in both strict and "
                    "non-strict mode. Default False permits English narrative "
                    "clauses to appear alongside the target-language body.",
    )

    # — Depth / length
    response_depth: Literal["brief", "standard", "detailed"] = Field(
        "standard",
        description="brief: target <200 words. standard: agent default. "
                    "detailed: target comprehensive coverage of all relevant "
                    "subsections / explanations.",
    )

    target_word_count: int | None = Field(
        None,
        description="Specific word count the user named (e.g. 'in 100 words', "
                    "'in 50 words', '500-word summary'). When set, the self-"
                    "refine critic checks if the response is within ±25% of "
                    "this target and flags as MAJOR if out of range. NULL when "
                    "no specific number was given (response_depth alone "
                    "governs length). Capped at 5000 to bound prompt-injection "
                    "attacks asking for a 1M-word response.",
        ge=10, le=5000,
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

    # — Specialized legal artifact request (drives prompt selection in
    #   Document / Drafting agents). Default NONE = generic Q&A.
    legal_artifact: LegalArtifact = Field(
        LegalArtifact.NONE,
        description="When the user requests a specific legal artifact (e.g. "
                    "cross-examination questions), the consuming agent picks "
                    "a tailored system prompt + upgrades the model + runs a "
                    "post-generation quality gate. Default NONE = generic "
                    "Q&A or draft output.",
    )

    # — Routing intent: what the user wants the system TO DO with the query.
    #   Read by the orchestrator's planner to decide which agents to invoke.
    #   Replaces the regex-based `_wants_drafting`, the keyword-based
    #   `_detect_multi_intent` scans, and the `_PLAN_SIGNALS` veto table.
    task_intent: Literal[
        "draft",            # produce a court-filing-ready document (plaint,
                            # affidavit, notice, agreement, deed, ...) — verbs
                            # like "draft / prepare / write / give me a <doc>".
                            # NOT for tactical outputs (cross-exam questions,
                            # arguments, briefs of advice — those are "analyze").
        "analyze",          # situational legal analysis, arguments, defences,
                            # remedies, advice, "what should I do", tactical
                            # output. Maps to Scenario.
        "lookup",           # the user wants the text / explanation of a
                            # specific statute, section, article, judgment,
                            # maxim, or constitutional provision. The corpus
                            # is decided by other typed fields (wants_statute_text,
                            # wants_case_law, wants_constitution, wants_maxim).
        "explain",          # conceptual / definitional / theoretical question
                            # ("what is X", "format of Y", "essential elements
                            # of Z", "difference between A and B"). Maps to
                            # Legal_Concepts.
        "ask_about_file",   # the user uploaded a file and is asking about its
                            # contents (extract, summarize, identify parties,
                            # cite sections in the file). Maps to Document.
        "chat",             # greetings, identity questions, casual
                            # acknowledgments. Maps to Non_legal.
        "other",            # default when nothing clearly applies.
    ] = Field(
        "other",
        description="Primary routing intent — what the user wants done with "
                    "the query. The orchestrator reads this to decide which "
                    "agents to invoke. CONSERVATIVE defaults: when in doubt "
                    "between 'draft' and 'explain', pick 'explain'. When the "
                    "user attaches a file and asks any question that's not a "
                    "draft request, prefer 'ask_about_file'.",
    )

    # — Content corpus directives. Each is TRUE iff the user explicitly asked
    #   for that kind of supporting content alongside the primary task.
    #   Replace the keyword-list dispatch in `_detect_multi_intent` /
    #   `_select_citation_agents` / `_PLAN_SIGNALS`.

    wants_statute_text: bool = Field(
        False,
        description="TRUE iff the user wants statutory text / specific section "
                    "/ specific act provisions surfaced. Triggers: 'section X', "
                    "'provision under Y', 'what does <act> say about', "
                    "'applicable law', 'relevant statute', explicit act/code "
                    "names (IPC, BNS, CrPC, BNSS, IEA, BSA, NI Act, Companies "
                    "Act, etc. in ANY language/script). Drives Legislation / "
                    "Newacts routing.",
    )
    wants_scenario_analysis: bool = Field(
        False,
        description="TRUE iff the user wants a situational analysis on TOP of "
                    "their primary ask — arguments, defences, remedies, "
                    "tactical recommendations, 'on behalf of <party>', 'what "
                    "are my options'. Drives Scenario agent inclusion. "
                    "Different from task_intent='analyze' (that is the "
                    "primary task); this field is the secondary signal that "
                    "Scenario should ALSO run for a non-analyze primary task.",
    )
    wants_constitution: bool = Field(
        False,
        description="TRUE iff the user references constitutional provisions / "
                    "Articles / fundamental rights / directive principles. "
                    "Drives Constitution agent inclusion.",
    )
    wants_maxim: bool = Field(
        False,
        description="TRUE iff the user references a legal maxim / Latin "
                    "doctrine (res judicata, audi alteram partem, estoppel, "
                    "nemo judex, caveat emptor, etc.). Drives Maxim agent "
                    "inclusion.",
    )
    wants_supreme_court: bool = Field(
        False,
        description="TRUE iff the user explicitly names the Supreme Court / "
                    "SC / apex court / a famous SC landmark case "
                    "(Kesavananda Bharati, Maneka Gandhi, Puttaswamy, "
                    "Vishaka, Navtej, etc.) or asks for SC-only precedents. "
                    "Drives SCI_Judgment routing instead of (or in addition "
                    "to) general Judgment.",
    )
    wants_gst_rulings: bool = Field(
        False,
        description="TRUE iff the query is about GST/CGST/SGST/IGST advance "
                    "rulings (AAR/AAAR), GST classification appeals, GST "
                    "ITC disputes, GST valuation rulings, HSN classification. "
                    "Drives GST_Judgment routing.",
    )

    named_acts: list[str] = Field(
        default_factory=list,
        description="Specific Indian act / code / statute names the user "
                    "explicitly references in the query. Each entry is the "
                    "canonical English act name as the user wrote it OR a "
                    "close paraphrase (e.g. 'Consumer Protection Act, 2019', "
                    "'Companies Act, 2013', 'Negotiable Instruments Act, "
                    "1881', 'Hindu Marriage Act, 1955'). Include the year "
                    "when the user named it. Capped at 5 entries to bound "
                    "prompt-injection attack surface. Used by the Legislation "
                    "agent for act-name-aware source preference — replaces "
                    "the hardcoded regex bank that only knew 19 acts.",
        max_length=5,
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
