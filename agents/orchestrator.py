"""Agent #1 — Orchestrator Agent

The brain of the system. Two phases:
1. PLAN: Analyze query → classify task → decide which agents to invoke
2. SYNTHESIZE: Merge results from domain agents into final response

Uses: Gemini 2.5 Flash Lite for task classification, planning, and synthesis.
"""

from __future__ import annotations

import asyncio
from pydantic import BaseModel, Field
from typing import Literal
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from core.state import LegalAgentState, AgentResult, FileContextData
from core.clients import get_gemini_flash, get_gemini_flash_full, get_gemini_pro, get_drafting_llm
from core.language import localize_prompt
from core.logger import get_logger, log_time, short_err
from core.progress import progress
from core.source_registry import (
    SourceRegistry,
    merge_source_registries,
    source_from_sci,
    source_from_hc,
    source_from_legislation,
    source_from_web,
)
from config.prompts import (
    TASK_CLASSIFICATION_PROMPT, SYNTHESIS_PROMPT, SYNTHESIS_TABLE_PROMPT,
    USER_INTENT_EXTRACTION_PROMPT,
    wrap_untrusted,
    DRAFT_SYNTHESIS_PROMPT, DRAFT_CITATION_PROMPT,
)
from config.intent import LegalArtifact, UserIntent, default_intent

log = get_logger("Orchestrator")

import re

# Prompt-injection sanitizer and the tax-appellate keyword detector were
# removed on 2026-07-25. The former was dead code (no live callers) that
# duplicated the guardrail regex; the latter was a hardcoded keyword bank
# that auto-enabled cite_appendix on CIT(A)/ITAT/GST/NCLT queries and
# violated the project's no-mechanical-patterns policy. Callers that want
# the citation appendix should pass `cite_appendix=true` explicitly, or
# rely on the env-level DRAFTING_CITE_APPENDIX_DEFAULT.


# --- Internal cite-marker stripper (BUG-09) ---
# The drafting agent emits [CITE: brief description] markers as placeholders
# for case law that the citation-injection step is supposed to fill in. After
# we switched to append-only synthesis (BUG-03), unfilled markers leaked to
# the user. This stripper removes any unfilled marker before the draft is
# returned. Keep markers that look like real legal citations alone.
_INTERNAL_CITE_MARKER_RE = re.compile(
    # [CITE: anything that's not a closing bracket]
    r"\s*\[CITE:[^\]\n]*\]\s*",
    re.IGNORECASE,
)


# Table-format intent detection and the QUERY_NORMALIZE_PROMPT legacy
# pipeline that produced free-form `response_instructions: str` were removed
# in Phase 4 of the intent layer rollout. The structured user-intent
# extractor (`_extract_user_intent`) is now the single source of truth —
# downstream consumers read `state["user_intent"].wants_table` via
# `_resolve_wants_table`. See docs/intent_layer_implementation_plan.md.


def _strip_internal_cite_markers(text: str) -> str:
    """Remove unfilled `[CITE: ...]` placeholder markers from the final draft.

    These are internal scaffolding emitted by the section-generation prompt
    rule #9. After synthesis, any marker that survived was never matched to
    a real citation — surfacing it to users is noise.
    """
    if not text:
        return text
    cleaned = _INTERNAL_CITE_MARKER_RE.sub(" ", text)
    # Tidy stray whitespace/punctuation introduced by the substitution
    cleaned = re.sub(r" +([.,;:!?])", r"\1", cleaned)  # " ." -> "."
    cleaned = re.sub(r"  +", " ", cleaned)              # double spaces
    cleaned = re.sub(r"\n +", "\n", cleaned)            # leading line spaces
    return cleaned.strip()


# --- Drafting Intent Detection (intent-driven, no regex) ---
# The original verb+noun regex bank is gone. Drafting intent now reads
# `UserIntent.task_intent == "draft"` populated by the always-on extractor
# (`_extract_user_intent`). The extractor handles native-language verbs,
# polite/passive phrasings, and conservative defaults (when ambiguous between
# "draft" and "explain", it picks "explain") — exactly the cases the regex
# bank could not cover without continuous keyword maintenance.


def _wants_drafting(intent: UserIntent | None) -> bool:
    """True iff the user wants the AI to PRODUCE a legal document.

    Reads the typed task_intent from the structured extractor. Conservative
    fallback when intent is missing or low-confidence: False (don't
    spuriously add Drafting; rely on the LLM planner).
    """
    if intent is None or intent.confidence < 0.5:
        return False
    return intent.task_intent == "draft"


# --- Task Classification (migrated from v1 task_identifer.py) ---

class IdentifyTaskSchema(BaseModel):
    task: Literal[
        "Drafting", "Judgment", "Legislation", "Constitution",
        "Scenario", "Maxim", "Newacts", "Legal_Concepts",
        "SCI_Judgment", "GST_Judgment", "Document",
        "Non_legal", "Other",
    ] = Field(..., description="The primary legal task type")


class ClassifyAndPlan(BaseModel):
    """Merged classification + planning result — single LLM call."""
    task: Literal[
        "Drafting", "Judgment", "Legislation", "Constitution",
        "Scenario", "Maxim", "Newacts", "Legal_Concepts",
        "SCI_Judgment", "GST_Judgment", "Document",
        "Non_legal", "Other",
    ] = Field(..., description="The primary legal task type")
    agents: list[str] = Field(
        ...,
        description="List of agent names to invoke (1-3): Legislation, Judgment, Newacts, Drafting, Scenario, Constitution, Maxim, Legal_Concepts, SCI_Judgment, GST_Judgment, Document",
    )
    reasoning: str = Field(..., description="Brief reasoning for task type and agent selection")


def _classify_task_regex_fallback(query: str) -> str:
    """Minimal task-classification fallback when the LLM call fails.

    The 50-line keyword cascade that used to live here was retired. Any
    keyword-based routing is now derived from the typed UserIntent (read
    via `_classify_task_from_intent`). On a hard LLM failure with no
    intent available, default to Legal_Concepts — the safest catch-all,
    since its agent runs a web-search-grounded explanation that gives
    the user *something* useful regardless of topic.

    Kept for back-compat with the call site in `_classify_task` where
    no intent has been extracted yet (the extractor runs in parallel
    with classification).
    """
    return "Legal_Concepts"


def _classify_task_from_intent(intent: UserIntent | None) -> str | None:
    """Derive a primary task from a typed UserIntent, when available.

    Used as the failure-time mapping inside `orchestrator_plan_node`: when
    classify+plan times out or errors AND we did extract an intent, we
    project the intent's typed fields into a single task name. Returns
    None when intent is missing or no field strongly identifies a task —
    caller falls back to `_classify_task_regex_fallback`.
    """
    if intent is None or intent.confidence < 0.5:
        return None
    ti = intent.task_intent
    if ti == "chat":
        return "Non_legal"
    if ti == "ask_about_file":
        return "Document"
    if ti == "draft":
        return "Drafting"
    if ti == "analyze":
        return "Scenario"
    if intent.wants_supreme_court:
        return "SCI_Judgment"
    if intent.wants_gst_rulings:
        return "GST_Judgment"
    if intent.wants_constitution:
        return "Constitution"
    if intent.wants_maxim:
        return "Maxim"
    if intent.include_case_law:
        return "Judgment"
    if intent.wants_statute_text:
        return "Legislation"
    return "Legal_Concepts"


def _classify_task(query: str, chat_summary: str | None = None) -> str:
    """Classify query into a task type using GPT-4o structured output.

    Falls back to keyword-based classification if the LLM call fails
    (rate-limit, timeout, structured-output parse error, etc.).
    """
    try:
        with log_time(log, "Task classification"):
            prompt = PromptTemplate.from_template(TASK_CLASSIFICATION_PROMPT)
            llm = get_gemini_flash(temperature=0.1).with_structured_output(
                IdentifyTaskSchema, include_raw=True,
            )
            # Wrap untrusted inputs in spotlighting delimiters — defense against
            # prompt injection. Combined with the preamble baked into the prompt
            # template, this is the OWASP-recommended layered defense.
            formatted = prompt.format(
                query=wrap_untrusted(query),
                chat_summary=wrap_untrusted(chat_summary or ""),
            )
            raw_and_parsed = llm.invoke(formatted)
        from core.token_tracker import record as _record_tokens
        _record_tokens("Orchestrator", "classify_task", raw_and_parsed.get("raw"))
        result = raw_and_parsed["parsed"]
        log.info("Task classified", task=result.task, query=query[:80])
        return result.task
    except Exception as e:
        fallback = _classify_task_regex_fallback(query)
        log.warning("Task classification LLM failed, using regex fallback",
                    error=str(e), fallback=fallback, query=query[:80])
        return fallback


# --- Multi-Agent Planning ---

class AgentPlan(BaseModel):
    agents: list[str] = Field(
        ...,
        description="List of agent names to invoke: Legislation, Judgment, Newacts, Drafting, Scenario, Constitution, Maxim, Legal_Concepts, SCI_Judgment, GST_Judgment, Document",
    )
    reasoning: str = Field(..., description="Brief reasoning for agent selection")


CLASSIFY_AND_PLAN_PROMPT = """You are an Indian legal query router. Output:
- `task`: the ONE primary task type (from the ordered list below)
- `agents`: 1-4 agents to invoke (see fan-out rules)
- `reasoning`: one sentence explaining the choice

## Task types — evaluate TOP-DOWN; FIRST match wins

1. **Non_legal** — greetings ("hi", "hello", "namaste"), casual chat, non-legal
   topics (weather, sports, math), or bot-identity questions ("who are you",
   "what can you do").
2. **Document** — the user attached files AND is asking about their contents
   (summary, extraction, "what does this say", "who is the plaintiff here").
3. **Drafting** — explicit production verb (draft / write / prepare / create /
   generate / compose / draw up / redraft / "give me a" / "I need a") PLUS a
   filing-ready document noun (plaint, petition, written statement, bail
   application, affidavit, legal notice, agreement, contract, deed, MOU, will,
   divorce petition, reply, rejoinder, etc.).
   NOT Drafting:
   - Tactical outputs (arguments, cross-examination questions, strategy,
     defences, briefs of advice) → **Scenario**, even if the user says "draft".
   - Questions ABOUT documents (format, essential elements, requirements,
     "how to file", "difference between X and Y", "what is a written
     statement") → **Legal_Concepts** or **Legislation**.
4. **Newacts** — the query references any of the 6 codes: IPC, BNS, CrPC,
   BNSS, IEA, BSA (any spelling / any language / full-name variants like
   "Indian Penal Code", "Code of Criminal Procedure", "Bharatiya Nyaya
   Sanhita"). Includes section references inside them ("Section 302 IPC",
   "Section 438 BNSS", "Section 65B Evidence Act").
   HARD RULE: NEVER also add Legislation to the agents list. Newacts already
   covers both old (IPC / CrPC / IEA) and new (BNS / BNSS / BSA) statute text.
5. **Legislation** — any OTHER Indian central or state act, or a specific
   section within one: NI Act (Sec 138), Companies Act, Hindu Marriage Act,
   GST Act, Specific Relief Act, Consumer Protection Act, POCSO, JJ Act,
   IBC, Motor Vehicles Act, etc.
6. **Constitution** — an Article of the Constitution ("Article 21",
   "Article 32"), a fundamental right, a directive principle, or the Preamble.
7. **SCI_Judgment** — user explicitly names the Supreme Court ("SC",
   "Hon'ble Supreme Court", "apex court") OR names a famous SC landmark case
   (Kesavananda Bharati, Puttaswamy, Maneka Gandhi, Vishaka, Navtej, D.K. Basu,
   Arnesh Kumar, Hussainara Khatoon, etc.).
8. **GST_Judgment** — GST AAR / AAAR / advance ruling / classification appeal
   under GST / GST ITC dispute / HSN code ruling / state GST appellate order.
9. **Judgment** — the user names a specific case OR asks for court decisions
   / precedents from High Courts or unspecified courts.
10. **Maxim** — a Latin legal maxim or doctrine (res judicata, audi alteram
    partem, estoppel, nemo judex, actus reus, mens rea, ubi jus ibi remedium,
    caveat emptor, etc.).
11. **Scenario** — the query describes a SPECIFIC FACT PATTERN (the user's
    situation, a client's situation, or a hypothetical with concrete facts)
    AND asks for arguments / defences / remedies / options / strategy.
    REQUIRES facts. If the query is abstract ("how does bail work", "when to
    hire a lawyer"), it is NOT Scenario — see #12.
12. **Legal_Concepts** — educational / procedural / definitional query with
    NO specific act, NO specific section, NO specific case, NO fact pattern.
    Patterns: "how does X work", "when should I Y", "what is Z", "difference
    between A and B", "procedure for W", "how to deal with a criminal case",
    "when to hire a lawyer", "what are my rights when arrested". This is a
    first-class target — reach for it whenever the query is educational
    rather than fact- or reference-specific.
13. **Other** — legal-adjacent but nothing above matched → route to Scenario.

## Fan-out rules

### HARD RULES (apply first, override everything else)
- If task is **Non_legal**, **Document**, or **Legal_Concepts** → agents MUST
  be `[task]` alone. No fan-out under any circumstance.
- Never pair **Newacts + Legislation** in the same agents list.
- Maximum 4 agents.

### Default
- Single agent equal to the primary task.

### Additive rules (only when the user's own words trigger them)
- Asks for case laws / precedents / citations / rulings / supporting
  judgments (verbatim): add BOTH **Judgment** AND **SCI_Judgment**. Skip
  SCI_Judgment if the user scoped to "High Court only"; skip Judgment if
  they scoped to "Supreme Court only" or named a specific SC case.
- Asks for statutory text alongside another primary: add **Legislation**
  (or **Newacts** if it's one of the 6 codes — never both).
- Invokes a fundamental right alongside another primary: add **Constitution**.
- Invokes a Latin maxim alongside another primary: add **Maxim**.
- Drafting + explicit "with case laws / citations / precedents" →
  add **Judgment** (also **SCI_Judgment** if SC scope named).

## Inputs
User query: {query}
Chat summary (optional, may be stale): {chat_summary}

Return `task`, `agents`, and one-sentence `reasoning`."""


def _classify_and_plan(query: str, chat_summary: str | None = None) -> tuple[str, list[str]]:
    """Classify query AND plan agents in a single LLM call.

    Returns (task, agents_list).
    Falls back to regex classification + single-agent plan on failure.
    """
    try:
        with log_time(log, "Classify + plan (merged)"):
            llm = get_gemini_flash(temperature=0.1).with_structured_output(
                ClassifyAndPlan, include_raw=True,
            )
            prompt = ChatPromptTemplate.from_template(CLASSIFY_AND_PLAN_PROMPT)
            chain = prompt | llm
            raw_and_parsed = chain.invoke({
                "query": query,
                "chat_summary": chat_summary or "",
            })
        from core.token_tracker import record as _record_tokens
        _record_tokens("Orchestrator", "classify_and_plan", raw_and_parsed.get("raw"))
        result = raw_and_parsed["parsed"]

        task = result.task
        agents = result.agents[:4]  # cap at 4 (bumped from 3 on 2026-06-30 to allow Scenario + Legislation + Judgment + SCI_Judgment fan-out)
        if not agents:
            agents = [task]

        # Telemetry cross-check (Drafting agreement) was retired with the
        # regex bank. The planner's Drafting decision is now validated
        # downstream against `extracted_intent.task_intent` in
        # `orchestrator_plan_node`.

        log.info("Classify+plan completed",
                 task=task, agents=agents,
                 source="llm",
                 reasoning=result.reasoning[:120])
        return task, agents

    except Exception as e:
        # Propagate to caller (orchestrator_plan_node) so it can apply the
        # intent-aware fallback path (`_classify_task_from_intent`). Returning
        # a Legal_Concepts-as-fallback tuple here would silently swallow
        # the LLM failure AND prevent the intent fallback from running —
        # the user's "draft a bail application" gets misrouted to a
        # web-grounded explanation.
        log.warning("Classify+plan LLM failed; propagating to caller",
                    error=str(e), source="raised")
        raise


def _select_citation_agents(
    intent: UserIntent | None,
    existing_plan: list[str] | None = None,
) -> list[str]:
    """Select which agents should produce the citation appendix for a draft.

    Reads typed UserIntent and (when available) the already-planned agents
    to make a corpus-aware choice:

    - Judgment: always included as the case-law source.
    - SCI_Judgment: when `intent.wants_supreme_court` (user named SC /
      famous SC landmark case).
    - GST_Judgment: when `intent.wants_gst_rulings`.
    - Newacts vs Legislation: if the planner LLM already chose Newacts
      for this draft (i.e. the query is about BNS/IPC, BNSS/CrPC,
      BSA/IEA), the citation appendix uses Newacts too — Legislation
      would re-fetch the same content from a less-curated index AND
      pollute the appendix with wrong-corpus hits. Otherwise default
      to Legislation.

    Previously the function unconditionally appended Legislation even
    for criminal-code queries, leaking wrong-corpus statute hits into
    the citation appendix.
    """
    existing = set(existing_plan or [])
    agents = ["Judgment"]
    if intent is not None and intent.wants_supreme_court:
        agents.append("SCI_Judgment")
    if intent is not None and intent.wants_gst_rulings:
        agents.append("GST_Judgment")
    # Corpus-aware statute pick. Newacts indexes BNS/IPC, BNSS/CrPC,
    # BSA/IEA; Legislation indexes everything else. Prefer Newacts when
    # the planner already routed there.
    if "Newacts" in existing:
        agents.append("Newacts")
    else:
        agents.append("Legislation")
    # Dedup while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for a in agents:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def _detect_multi_intent(intent: UserIntent | None, task: str) -> list[str]:
    """Intent-driven multi-agent enrichment.

    Reads typed UserIntent fields populated by the extractor and surfaces
    additional agents the planner should add. Replaces the previous
    keyword-scan safety net.
    """
    if intent is None or intent.confidence < 0.5:
        return []
    # Legal_Concepts primary is STRICTLY single-agent (see the HARD RULES
    # block in CLASSIFY_AND_PLAN_PROMPT). The web-grounded legal_concepts
    # agent produces a self-contained answer; any fan-out here reintroduces
    # the concat-of-agent-outputs artifact seen in the 2026-07-23 same-thread
    # smoke ("How to deal with a criminal case?" → 4-agent plan → duplicate
    # PDF blocks + self-contradiction in turn 2). If the user genuinely
    # references a specific act / case / Article / maxim, the top-down
    # decision procedure in CLASSIFY_AND_PLAN_PROMPT will have already
    # routed to that more specific task (Newacts / SCI_Judgment / etc.)
    # before falling through to Legal_Concepts — so reaching here with
    # task == "Legal_Concepts" means the query is genuinely educational
    # and no enrichment is warranted.
    if task == "Legal_Concepts":
        return []
    extra: list[str] = []
    if intent.include_case_law:
        # Fan out to BOTH High Court (Judgment) AND Supreme Court (SCI_Judgment)
        # when case law is requested, regardless of whether the user explicitly
        # named the apex court.
        #
        # Why both, not either: substantive Indian-law topics (medical
        # negligence, consumer protection, fundamental rights, family law,
        # service matters) have their landmark precedents at the Supreme
        # Court — Jacob Mathew, Indian Medical Association v V.P. Shantha,
        # Kesavananda Bharati, etc. The HC index has the regional / recent
        # case law that supplements them. The previous "SCI OR Judgment"
        # gate, keyed only on `wants_supreme_court`, silently dropped SC
        # cases whenever the user phrased the request generically ("with
        # supporting case laws"), and the HC agent's relevance gate would
        # then fall through to web search when the HC corpus didn't have
        # on-point hits — surfacing "AI-Generated" placeholders instead of
        # the real SC PDFs the SCI corpus had ready.
        #
        # The 4-agent cap downstream still bounds fan-out cost.
        if task != "Judgment":
            extra.append("Judgment")
        if task != "SCI_Judgment":
            extra.append("SCI_Judgment")
    if intent.wants_statute_text and task not in ("Legislation", "Newacts"):
        extra.append("Legislation")
    if intent.wants_constitution and task != "Constitution":
        extra.append("Constitution")
    if intent.wants_maxim and task != "Maxim":
        extra.append("Maxim")
    if intent.wants_scenario_analysis and task != "Scenario":
        extra.append("Scenario")
    if intent.wants_supreme_court and task != "SCI_Judgment" and "SCI_Judgment" not in extra:
        extra.append("SCI_Judgment")
    if intent.wants_gst_rulings and task != "GST_Judgment":
        extra.append("GST_Judgment")
    if intent.task_intent == "draft" and task != "Drafting":
        extra.append("Drafting")
    return extra


# --- Per-Agent Query Rewriting ---

import json as _json

AGENT_QUERY_REWRITE_PROMPT = """You are a legal query optimizer. Rewrite the user's query into specialized search queries for each assigned agent.

Each agent has a different database and purpose:
- Judgment: Searches court case law database. Query should focus on: legal topic keywords, cause of action, type of case (e.g. "medical negligence", "consumer complaint", "property dispute"). PARTY NAMES — use judgment: when the user names parties in case-citation style ("Kesavananda Bharati v. State of Kerala", "Vishaka v. State of Rajasthan", "Maneka Gandhi", "Puttaswamy") or a famous landmark case, KEEP the names in the rewritten query — those are real cases and the name is the strongest match signal. When the user names parties in factual-scenario style ("Mr. Sharma defrauded Mrs. Verma", "ABC Corp sued XYZ Ltd", or any fictional/placeholder names like "Plaintiff X", "John Doe"), STRIP them and use generic legal topic terms instead — those names won't match real case law. Apply common sense: capitalized proper nouns paired with "v." or "vs" are case citations; lowercase first names in narrative prose are factual parties.
- Legislation: Searches Indian act/statute database by full-text match. IMPORTANT: Focus on the SINGLE most relevant act and its specific sections. Do NOT list multiple acts — the search engine will match the first act name it finds. Example: "Consumer Protection Act 2019 Section 2 definition of consumer deficiency in service medical negligence" (not "Indian Contract Act; IPC; Consumer Protection Act").
- Newacts: Searches BNS/IPC, BNSS/CrPC, BSA/IEA database. Query should mention the specific criminal code sections or topics.
- Drafting: Creates legal documents. Query should specify: document type, parties, key facts, relief sought.
- Scenario: Performs web-grounded legal analysis. Query should include: full factual situation, what analysis is needed (arguments, remedies, forum).
- Constitution: Searches constitutional provisions database. Query should specify: Article numbers, fundamental rights, constitutional principles.
- Maxim: Searches legal maxims database. Query should specify: maxim name, doctrine, Latin phrase.
- Legal_Concepts: General legal explanation. Query should be the legal concept to explain.
- SCI_Judgment: Searches Supreme Court database. Query should focus on: SC-specific case names, constitutional questions, landmark rulings.
- GST_Judgment: Searches GST AAAR (Appellate Authority for Advance Ruling) order database. Query should focus on: GST issue (classification / ITC / valuation / exemption), HSN code, GST section/notification, applicant name, state. Do NOT include unrelated tax topics (income tax, customs).

Rules:
1. Each rewritten query must be self-contained and optimized for that agent's search database.
2. Keep queries concise (under 100 words each). Scenario can be longer.
3. Extract and include specific legal terms: act names, section numbers, doctrines, party types.
4. For Legislation: pick the SINGLE most relevant act for the user's primary legal issue. Do NOT list multiple acts.
5. For Judgment: NEVER include fictional party names from the user's scenario. Use only legal topics and case type keywords.
6. Return valid JSON object mapping agent name to rewritten query.
7. CRITICAL: All rewritten queries MUST be in English, regardless of the input language. If the user query is in Hindi, Tamil, or any other language, translate the intent to English for every agent query.

User Query: {query}
Agents: {agents}

Return JSON object like: {{"Judgment": "...", "Legislation": "..."}}"""


class AgentQueries(BaseModel):
    queries: dict[str, str] = Field(
        ..., description="Map of agent name to its optimized query"
    )


# =============================================================================
# Structured UserIntent extractor (intent layer rollout — Phase 4 final state).
# See docs/intent_layer_implementation_plan.md.
#
# Single LLM call (Gemini Flash Lite) parses the user's query + chat summary
# into a typed UserIntent + normalized English query. Always-on as of Phase 4.
# Replaces the legacy QUERY_NORMALIZE_PROMPT + free-form
# `response_instructions: str` + regex picker pipeline that was retired in
# Phase 4 — see git history for the deleted code.
# =============================================================================

class QueryAnalysisV2(BaseModel):
    """Combined output of the intent extractor — normalized query + typed intent.

    Both fields populated by a SINGLE LLM call (Gemini Flash Lite). Returning
    them together saves a round-trip vs. extracting them sequentially.
    """
    normalized_query: str = Field(
        ...,
        description="Query rewritten in clear English with proper-noun preservation. "
                    "Identical to QueryAnalysis.normalized_query — kept for migration.",
    )
    intent: UserIntent = Field(
        default_factory=default_intent,
        description="Structured user-intent object. See config.intent.UserIntent.",
    )


def _extract_user_intent(
    query: str, chat_summary: str = ""
) -> tuple[str, UserIntent]:
    """Extract structured user intent from query + chat history in one LLM call.

    This is the Phase 1 replacement for `_analyze_and_normalize_query`. During
    Phase 1 both functions run in parallel so we can compare via telemetry;
    Phase 2 will cut over the downstream consumers (synthesis template picker,
    pass-through guard) to read `intent.response_format` instead of the regex.

    Returns:
        (normalized_query, UserIntent). On any failure returns
        (original_query, default_intent()) so callers have a safe fallback —
        an empty intent behaves identically to "no directive expressed".

    Token cost: ~200 output tokens × Gemini Flash Lite = roughly $0.0001/req.
    """
    try:
        with log_time(log, "Intent extraction (v2)"):
            llm = get_gemini_flash(temperature=0.0).with_structured_output(
                QueryAnalysisV2, include_raw=True,
            )
            prompt = ChatPromptTemplate.from_template(USER_INTENT_EXTRACTION_PROMPT)
            chain = prompt | llm
            raw_and_parsed = chain.invoke({
                # Both inputs flow into an LLM call, so they MUST be wrapped
                # with the injection-guard delimiters from Round 4.
                "query":        wrap_untrusted(query),
                "chat_summary": wrap_untrusted(chat_summary or ""),
            })
        from core.token_tracker import record as _record_tokens
        _record_tokens("Orchestrator", "extract_user_intent",
                       raw_and_parsed.get("raw"))
        result = raw_and_parsed["parsed"]
        log.info("User intent extracted",
                 format=result.intent.response_format.value,
                 format_explicit=result.intent.format_explicit,
                 language=result.intent.language,
                 language_explicit=result.intent.language_explicit,
                 depth=result.intent.response_depth,
                 confidence=result.intent.confidence)
        return result.normalized_query, result.intent

    except Exception as e:
        # Graceful degradation: an empty intent (confidence=0) tells downstream
        # consumers to fall back to the legacy heuristics. Never raise — the
        # extractor is a soft enhancement layer, not a hard dependency.
        log.warning("Intent extraction failed; falling back to default_intent()",
                    error=short_err(e), exc_info=True)
        return query, default_intent()


def _legacy_response_instructions(intent: UserIntent) -> str:
    """Project a structured UserIntent into the natural-language string that
    the SYNTHESIS prompts inject as `{response_instructions}`.

    Phase 2 status: the orchestrator's PICKER (table-vs-prose) no longer reads
    this — see `_resolve_wants_table`, which reads `intent.wants_table`
    directly. But the synthesis prompt templates (`SYNTHESIS_PROMPT`,
    `SYNTHESIS_TABLE_PROMPT`) still interpolate `{response_instructions}`
    to give the LLM a human-readable summary of the user's directives. So
    when the legacy normalizer was skipped (skip_normalize path), we still
    project the intent here so the prompt template is populated.

    Phase 4 will delete this once `{response_instructions}` is replaced with
    a structured directive block read directly from `state["user_intent"]`.

    The output vocabulary intentionally overlaps with `_TABLE_INTENT_RE` so
    that the legacy regex fallback at low confidence ALSO recognises the
    user's intent. Defense in depth during migration.
    """
    parts: list[str] = []
    if intent.format_explicit:
        if intent.response_format.value == "table":
            parts.append("table format")
        elif intent.response_format.value == "comparison_table":
            parts.append("comparison table")
        elif intent.response_format.value == "bullet_list":
            parts.append("bullet list")
        elif intent.response_format.value == "numbered_list":
            parts.append("numbered list")
        elif intent.response_format.value == "draft":
            parts.append("draft a document")
        elif intent.response_format.value == "outline":
            parts.append("outline format")
        elif intent.response_format.value == "json":
            parts.append("json output")
    if intent.language_explicit and intent.language != "en":
        parts.append(f"respond in {intent.language_display_name}")
    if intent.response_depth == "brief":
        parts.append("brief")
    elif intent.response_depth == "detailed":
        parts.append("detailed")
    if intent.include_case_law:
        parts.append("with case laws")
    if intent.additional_instructions:
        parts.append(intent.additional_instructions[:120])
    if not parts:
        return "Standard legal response with proper citations and markdown formatting."
    return ". ".join(p.strip() for p in parts) + "."


# =============================================================================
# Phase 2-4 of intent layer rollout — synthesis-side resolvers.
#
# These resolvers read state["user_intent"] which is populated by the
# orchestrator's intent extractor (always-on as of Phase 4). The legacy
# regex fallback was removed in Phase 4 — when extraction fails the
# fallback is `default_intent()`, which expresses "no preference".
# =============================================================================

_INTENT_CONFIDENCE_THRESHOLD = 0.7


def _resolve_wants_table(state: LegalAgentState) -> bool:
    """True iff the user wants a markdown table (TABLE or COMPARISON format).

    Reads `state["user_intent"]`. Below the confidence threshold OR when
    intent is missing, returns False (no special table routing) — the
    LLM still receives the typed intent via the agent prompt directives
    when present.
    """
    intent: UserIntent | None = state.get("user_intent")
    if intent is None or intent.confidence < _INTENT_CONFIDENCE_THRESHOLD:
        return False
    return intent.wants_table


def _resolve_explicit_non_english(state: LegalAgentState) -> bool:
    """True iff the user explicitly asked for a non-English response language.

    Used by the single-agent pass-through guard: when the user said "answer
    in Hindi" but only one agent ran, pass-through skips the synthesis stage
    that applies `localize_prompt(...)`. Force synthesis in that case so the
    language directive is honoured.
    """
    intent: UserIntent | None = state.get("user_intent")
    if intent is None or intent.confidence < _INTENT_CONFIDENCE_THRESHOLD:
        return False
    return intent.language_explicit and intent.language != "en"


def _resolve_user_language(state: LegalAgentState) -> str:
    """Return the ISO 639-1 language code to use for downstream localization.

    Priority:
      1. `state["user_intent"].language` when the intent extractor said the
         user EXPLICITLY named a target language and confidence is high.
         Catches the case where a Hindi-speaking user types in English script
         ("Section 131 in Hindi") — langdetect would say "en", but the intent
         extractor catches the explicit "in Hindi" directive.
      2. `state["user_language"]` — populated by the memory agent's
         `detect_language()` (langdetect + Romanized heuristic). The
         default path when the query has no explicit language directive.
    """
    intent: UserIntent | None = state.get("user_intent")
    if (
        intent is not None
        and intent.confidence >= _INTENT_CONFIDENCE_THRESHOLD
        and intent.language_explicit
    ):
        return intent.language
    return state.get("user_language", "en")


def _rewrite_queries_for_agents(query: str, agents: list[str]) -> dict[str, str]:
    """Generate per-agent optimized queries using LLM.

    For single-agent plans, returns empty dict (agent uses original query).
    For multi-agent plans, rewrites the query for each agent's domain.

    Skips when the user's original query is already long enough to be
    standalone (>=500 chars). The AGENT_QUERY_REWRITE_PROMPT was designed
    to optimise SHORT user prompts for per-agent retrieval, and it
    compresses fact-rich drafting prompts (party names, dates, itemised
    Stridhan lists) into short search queries — which then propagates a
    facts-less query to drafting_node via agent_queries["Drafting"], and
    drafting falls back to template example values. Per
    feedback_preserve_user_query: never lose information from the user's
    prompt anywhere in the pipeline.

    Exception — Drafting + citation agents on a long query. When Drafting is
    co-planned with a citation agent (SCI_Judgment / Judgment / Legislation /
    Newacts / Constitution / Maxim) and the raw query is a full drafting
    template (>=500 chars, e.g. a party-labelled writ-petition scaffold), the
    unrewrote-everyone skip propagates the whole template to the citation
    agent's ReAct step too. The citation agent's LLM then treats the template
    as a "draft this" instruction and produces a SECOND full drafted
    document, which the orchestrator's Drafting-primary append-only synth
    glues under `### <AGENT> CITATIONS:` — the "double draft" the user sees.
    In this case we rewrite ONLY for the non-Drafting agents; Drafting is
    NOT emitted in the returned dict, so drafting_node's own fallback to
    state["query"] preserves the raw prompt verbatim (invariant intact).
    """
    if len(agents) <= 1:
        return {}

    if len(query) >= 500:
        # Long drafting-scenario: rewrite ONLY for the non-Drafting agents.
        # Drafting is intentionally omitted from the target list so the
        # drafting_node falls back to state["query"] (raw, untouched).
        if "Drafting" in agents:
            citation_agents = [a for a in agents if a != "Drafting"]
            if not citation_agents:
                return {}
            log.info(
                "Per-agent query rewrite — Drafting co-planned with citation "
                "agents; rewriting citation agents only (Drafting reads raw)",
                query_chars=len(query),
                citation_agents=citation_agents,
            )
            return _run_rewrite_llm(query, citation_agents)

        log.debug("Skipping per-agent query rewrite — query already standalone-length",
                  query_chars=len(query), agents=agents)
        return {}

    return _run_rewrite_llm(query, agents)


def _run_rewrite_llm(query: str, agents: list[str]) -> dict[str, str]:
    """Invoke the LLM query rewriter for the given agent list.

    Returns {} on any failure — callers fall back to state["query"] in that
    case, so a rewriter failure never truncates the user's prompt.
    """
    try:
        with log_time(log, "Per-agent query rewriting"):
            llm = get_gemini_flash(temperature=0.1)
            prompt = ChatPromptTemplate.from_template(AGENT_QUERY_REWRITE_PROMPT)
            chain = prompt | llm

            response = chain.invoke({
                "query": query,
                "agents": ", ".join(agents),
            })
        from core.token_tracker import record as _record_tokens
        _record_tokens("Orchestrator", "rewrite_per_agent_queries", response)

        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        parsed = _json.loads(text)
        agent_queries = {k: v for k, v in parsed.items() if k in agents and isinstance(v, str)}

        log.info("Per-agent queries generated",
                 agents=list(agent_queries.keys()),
                 query_lengths={k: len(v) for k, v in agent_queries.items()})
        return agent_queries

    except Exception as e:
        log.warning("Per-agent query rewriting failed, agents will use original query",
                    error=str(e))
        return {}


# --- Intent-Driven Plan Validation ---
# The keyword-table safety net (_PLAN_SIGNALS + _validate_and_enrich_plan)
# was retired. Plan validation now reads typed UserIntent fields
# (`include_case_law`, `wants_statute_text`, `wants_constitution`,
# `wants_maxim`, `wants_supreme_court`, `wants_gst_rulings`,
# `wants_scenario_analysis`) via `_detect_multi_intent`, and the Newacts
# veto over Legislation is a typed-field decision instead of a keyword
# scan. Adding a new corpus = add a field to UserIntent + a branch in
# _detect_multi_intent. No keyword maintenance.


def _validate_and_enrich_plan(
    tasks_planned: list[str],
    intent: UserIntent | None,
    log,
) -> list[str]:
    """Apply intent-driven enrichment + Newacts/Legislation veto.

    Replaces the prior keyword-scan validator. Reasons over typed
    UserIntent fields populated by `_extract_user_intent`. When intent
    is missing or low-confidence, leaves the plan as-is.
    """
    if intent is None or intent.confidence < 0.5:
        return tasks_planned

    # Newacts covers BOTH old and new criminal-code texts. When the user is
    # clearly asking about one of those codes, Legislation is redundant.
    # The extractor flags the act/code in `wants_statute_text` and the
    # planner LLM is asked to route IPC/BNS/CrPC/etc. queries to Newacts.
    # If the planner picked both, prefer Newacts and veto Legislation.
    if "Newacts" in tasks_planned and "Legislation" in tasks_planned:
        tasks_planned = [a for a in tasks_planned if a != "Legislation"]
        log.info("Plan veto: Newacts covers Legislation content",
                 plan=tasks_planned)

    # When the user named the Supreme Court / a famous landmark case, prefer
    # SCI_Judgment over general Judgment (or in addition, if both are valid).
    if intent.wants_supreme_court and "SCI_Judgment" not in tasks_planned:
        if len(tasks_planned) < 3:
            tasks_planned.append("SCI_Judgment")
            log.info("Plan enriched: wants_supreme_court → SCI_Judgment",
                     plan=tasks_planned)

    # GST routing trumps general statute lookups.
    if intent.wants_gst_rulings and "GST_Judgment" not in tasks_planned:
        if len(tasks_planned) < 3:
            tasks_planned.append("GST_Judgment")
            log.info("Plan enriched: wants_gst_rulings → GST_Judgment",
                     plan=tasks_planned)

    return tasks_planned


# ---------------------------------------------------------------------------
# Review-and-Redraft citation-agent strip
#
# When the user uploaded a document AND asked to review/redraft it, the
# Drafting agent is the right place to retrieve supporting precedents — it
# already runs `_gather_relevant_context` (SC/HC/Legislation/Newacts top
# hits) against the user's ACTUAL matter. Co-planning SCI_Judgment /
# Judgment / GST_Judgment as separate agents on a topic-loose "landmark
# case laws" rewrite fires each of those agents' generation LLMs with a
# vague query; the LLM returns fully-shaped prose (sometimes a full second
# drafted application, sometimes 3 unrelated case summaries), and the
# Draft-Aware append-only synth path appends that prose verbatim under a
# `### <AGENT> CITATIONS:` header. This is the "highly hallucination"
# failure mode the client reported.
#
# The verb detection mirrors `_REVIEW_REDRAFT_VERBS_RE` in
# agents/drafting.py — keep both in sync. Long-term the check should move
# to a typed `UserIntent.document_analysis_mode` field.
# ---------------------------------------------------------------------------
_REVIEW_REDRAFT_VERBS_RE = re.compile(
    r"\b("
    r"review|redraft|revise|revised|revising|revision|"
    r"correct|corrected|correcting|"
    r"fix|fixing|"
    r"audit|auditing|"
    r"rectif|"
    r"amend|amending|amendment|"
    r"error|errors|mistake|mistakes"
    r")\b",
    re.IGNORECASE,
)

_CITATION_AGENTS_TO_STRIP_ON_REVIEW = ("SCI_Judgment", "Judgment", "GST_Judgment")


def _has_review_redraft_verbs(query: str) -> bool:
    """True when `query` contains any review/redraft/revise/audit verb."""
    return bool(query and _REVIEW_REDRAFT_VERBS_RE.search(query))


# --- Regenerate helper (Sagar bug #5, 2026-06-16) ---

_REFINE_PROMPT = """You are refining a previous legal AI response. The user
clicked "regenerate" — they want a POLISHED version of the previous answer,
NOT a completely different new response.

Rules:
- Preserve the previous response's STRUCTURE (section headings, table layouts,
  paragraph numbering, cause-title block, etc.) EXACTLY.
- Preserve the substantive content — every fact, citation, statute reference,
  party name, amount, date, and address stays the same.
- IMPROVE clarity, polish phrasing, fix typos, fix grammar, repair any markdown
  formatting issues (broken tables, missing blank lines between cause-title
  elements, code-fenced "vs", etc.).
- Do NOT add new information the user didn't ask for.
- Do NOT shorten or summarise the response.
- Do NOT change the language (if the previous response was in Marathi, the
  refined response stays in Marathi).
- Do NOT prefix with "Here is the refined version" or any other preamble.
  Start directly with the substantive content.

Original user query:
{query}

Previous response (this is what you are refining):
{previous_response}

Refined response (same structure, same content, polished phrasing):"""


async def _refine_existing_response(
    query: str,
    previous_response: str,
    user_language: str = "en",
) -> tuple[str, int]:
    """Refinement pass over a previous AI response.

    Used by the regenerate short-circuit in orchestrator_plan_node when the
    request carries `regenerate_of`. Single Gemini Flash call with low
    temperature so the refined response stays close to the original
    structure but gets a quality pass.

    Returns (refined_text, tokens_consumed).
    """
    progress("orchestrator", "Refining previous response...", step="regenerate")
    with log_time(log, "Refine previous response"):
        # Use temperature=0 so the refinement is as deterministic as possible.
        # Same input + same prompt → essentially same output, matching the
        # user's "regeneration should align with previous output" expectation.
        llm = get_gemini_flash_full(temperature=0.0, max_output_tokens=12288)
        prompt = ChatPromptTemplate.from_template(
            localize_prompt(_REFINE_PROMPT, user_language)
        )
        chain = prompt | llm
        from core.streaming import stream_chain_response
        response = await stream_chain_response(
            chain,
            {"query": query, "previous_response": previous_response},
            timeout=180,
        )
    refined = response.text
    from core.token_tracker import record as _record_tokens
    tokens = _record_tokens("Orchestrator", "refine", response)
    return refined, tokens


# --- Agent Nodes ---

async def orchestrator_plan_node(state: LegalAgentState) -> dict:
    """Phase 1 — Classify task and create execution plan.

    Steps:
    0. Normalize query (translate to English + extract user expectations)
    1. Classify query into task type
    2. Handle Non_legal rejection
    3. Plan which agents to invoke (single or multi-agent)
    4. Rewrite query per-agent for multi-agent plans
    5. Return task + tasks_planned + agent_queries + response_instructions
    """
    query = state.get("query", state["original_query"])

    # Sagar bug #5 (2026-06-16): regenerate short-circuit. When the frontend
    # passes `regenerate_of`, the user clicked "regenerate" — they want a
    # polished version of the previous response, NOT a completely different
    # new answer. Run ONE Gemini Flash refinement call here and skip the
    # full agent pipeline.
    regenerate_of = state.get("regenerate_of") or ""
    if regenerate_of.strip():
        refined, refine_tokens = await _refine_existing_response(
            query=query,
            previous_response=regenerate_of,
            user_language=state.get("user_language", "en"),
        )
        # Stream the refined content to any active SSE writer so the UX
        # matches a normal generation.
        try:
            from langgraph.config import get_stream_writer
            writer = get_stream_writer()
            _chunk = 40
            for i in range(0, len(refined), _chunk):
                writer({"type": "token", "content": refined[i:i + _chunk]})
        except RuntimeError:
            pass  # batch endpoint
        log.info("Regenerate short-circuit: refined previous response",
                 prev_len=len(regenerate_of), refined_len=len(refined),
                 tokens=refine_tokens)
        # Build a synthetic agent result so downstream nodes (guardrail
        # output, source handling, etc.) see a normal-looking payload.
        from core.state import AgentResult
        refine_result = AgentResult(
            agent_name="Refiner",
            content=refined,
            sources=[],
            tokens_consumed=refine_tokens,
        )
        return {
            "task": "Refine",
            "tasks_planned": ["Refine"],
            "agent_queries": {},
            "response_instructions": "",
            "agent_results": {"Refiner": refine_result},
            "final_response": refined,
            "tokens_consumed": refine_tokens,
        }

    _original_query = state.get("original_query", query)
    summary = state.get("summary_text", "")
    user_language = state.get("user_language", "en")
    log.info("Plan phase started", query=query[:100],
             has_summary=bool(summary), query_len=len(query))

    progress("orchestrator", "Understanding your question...", step="classify")

    # --- Fast pre-checks (no LLM calls, uses original query) ---
    _GREETING_PREFIXES = (
        "hello", "hey", "hii", "helo", "hola", "namaste", "namaskar",
        "good morning", "good afternoon", "good evening", "good night",
        "how are you", "how r u", "what's up", "whats up",
        "who are you", "what are you", "how's it going", "hows it going",
    )
    _GREETING_EXACT = ("hi", "sup")
    _orig_stripped = _original_query.lower().strip().rstrip("!?,.")
    is_greeting = (
        _orig_stripped in _GREETING_PREFIXES
        or _orig_stripped in _GREETING_EXACT
        or any(_orig_stripped.startswith(g + " ") for g in _GREETING_PREFIXES)
        or any(_orig_stripped.startswith(g + " ") and len(_orig_stripped) < 30 for g in _GREETING_EXACT)
    )

    _orig_lower = _original_query.lower()

    fc = FileContextData.from_state(state)

    # Intent extractor result (Phase 1, telemetry-only). Stays None on the
    # short-circuit paths (greeting / skip-normalize) and on extractor
    # failures. Populated only inside the parallel-gather path below when
    # INTENT_EXTRACTOR_V2 is on.
    extracted_intent: UserIntent | None = None

    # The SCI keyword pre-check (removed 2026-07-26) substring-matched
    # "supreme court judgment" anywhere in the query and hardcoded
    # tasks_planned=["SCI_Judgment"], collapsing multi-task case-pack prompts
    # ("draft FIR + charge sheet + cite Supreme Court judgments") to one
    # agent. Rule 7 of CLASSIFY_AND_PLAN_PROMPT already routes pure SCI
    # lookups correctly; the ~2s latency saving was not worth the collapse.
    # --- Short-circuit: greeting resolved ---
    if is_greeting:
        log.info("Greeting detected, short-circuiting classification",
                 original=_original_query[:60])
        task = "Non_legal"
        tasks_planned = ["Non_legal"]
        response_instructions = ""

        # Override to Document if files attached
        if fc and fc.has_content:
            log.info("Non-legal overridden to Document due to file context")
            task = "Document"
            tasks_planned = ["Document"]

    else:
        # --- Change 3: Skip normalization for English queries ---
        _FORMAT_KEYWORDS = (
            "table format", "bullet", "in hindi", "in marathi", "in tamil",
            "in telugu", "in bengali", "in kannada", "in malayalam",
            "in gujarati", "in punjabi", "in urdu", "in odia",
            "hindi mein", "hindi me", "batao", "samjhao", "kaise",
        )
        # Skip query normalization when:
        #   - SHORT English query (< 500 chars) with no format keyword —
        #     nothing to normalize, original behaviour.
        #   - LONG English query (>= 1500 chars) — already standalone
        #     prose; the intent extractor's `normalized_query` field
        #     compresses fact-rich drafting prompts (party names, dates,
        #     amounts, itemised lists) into a shorter "clean English"
        #     summary, and the line below (`query = normalized_query`)
        #     then propagates that loss-y summary to every downstream
        #     agent. Per feedback_preserve_user_query: never lose
        #     information from the user's prompt anywhere in the pipeline.
        # Mid-length (500-1499) still normalizes as before — that range
        # is mostly conversational queries where normalization helps
        # retrieval without dropping facts.
        skip_normalize = (
            user_language == "en"
            and not any(k in _orig_lower for k in _FORMAT_KEYWORDS)
            and (len(query) < 500 or len(query) >= 1500)
        )

        if skip_normalize:
            log.info("Skipping normalization (English, short query)")
            response_instructions = ""
            normalized_query = query
        else:
            normalized_query = None  # will be set by parallel normalization

        # --- Change 2: Parallelize normalization + classify-plan ---
        # Build classify query with file hint
        classify_query = query
        if fc and fc.has_content:
            file_hint = f" [User has uploaded files: {', '.join(fc.file_names)}. This query is about the uploaded document(s).]"
            classify_query = query + file_hint
            log.info("File context hint added for classification", file_names=fc.file_names)

        # Phase 4: structured intent extractor is the canonical path. The
        # legacy _analyze_and_normalize_query and the regex-based
        # _wants_table_format are gone. The extractor produces both the
        # normalized query and the typed UserIntent in one LLM call.
        #
        # Feed the user's ORIGINAL message (pre-rewrite) so explicit
        # directives like "In marathi" survive. The memory node's
        # follow-up rewriter expands short messages into standalone
        # retrieval queries using chat history, which strips
        # user-facing directives (language / format / depth) that
        # aren't legal anchors. Reading directives from the raw input
        # keeps `intent.language_explicit` correct on follow-up turns.
        intent_coro = asyncio.wait_for(
            asyncio.to_thread(_extract_user_intent, _original_query, summary or ""),
            timeout=10,
        )
        classify_coro = asyncio.wait_for(
            asyncio.to_thread(
                _classify_and_plan, classify_query,
                chat_summary=summary if summary else None,
            ),
            timeout=30,
        )
        results = await asyncio.gather(
            intent_coro, classify_coro, return_exceptions=True,
        )

        # Process intent result first — it gives us normalized_query +
        # typed UserIntent + the natural-language projection injected into
        # SYNTHESIS_PROMPT as {response_instructions}.
        if isinstance(results[0], Exception):
            log.warning("Intent extraction failed; using default_intent",
                        error=str(results[0])[:200])
            extracted_intent = default_intent()
            response_instructions = ""
            normalized_query = query
        else:
            normalized_query, extracted_intent = results[0]
            # Only adopt the extractor's normalized form when no upstream
            # step (memory rewrite, abbreviation expansion) already
            # changed `query`. The rewriter's expanded form is a better
            # retrieval target than re-normalizing the raw 2-word
            # follow-up that the extractor just received.
            if (
                normalized_query
                and normalized_query != query
                and _original_query == query
                and not skip_normalize
            ):
                log.info("Query normalized",
                         original=query[:80], normalized=normalized_query[:80])
                query = normalized_query
            response_instructions = _legacy_response_instructions(extracted_intent)

        # Process classify+plan result
        if isinstance(results[1], Exception):
            # LLM-failure fallback: derive task from the typed intent when
            # available; otherwise the safest catch-all (Legal_Concepts).
            task = (
                _classify_task_from_intent(extracted_intent)
                or _classify_task_regex_fallback(classify_query)
            )
            tasks_planned = [task]
            log.warning("Classify+plan failed/timed out, using intent fallback",
                        error=str(results[1])[:200], task=task,
                        source="intent" if extracted_intent and extracted_intent.confidence >= 0.5 else "default")
        else:
            task, tasks_planned = results[1]
            # NOTE: Drafting false-positive strip lives downstream — after
            # _detect_multi_intent runs — so both the LLM-added and
            # safety-net-added Drafting cases are caught in one spot.

        # Handle non-legal with file context
        if task == "Non_legal" and fc and fc.has_content:
            log.info("Non-legal overridden to Document due to file context")
            task = "Document"
            tasks_planned = ["Document"]

    # For Document task with file context, ensure Document agent is primary
    if task == "Document" and fc and fc.has_content and tasks_planned != ["Document"]:
        tasks_planned = ["Document"]
        log.info("Document task with file context — using Document agent directly",
                 file_names=fc.file_names)

    # File context: ensure Document agent is in plan if files attached
    if fc and fc.has_content and "Document" not in tasks_planned:
        tasks_planned.append("Document")
        log.info("Document agent added for file context (multi-intent support)",
                 file_names=fc.file_names)

    # Draft + file detection: if user wants to DRAFT from an uploaded document,
    # route through Drafting pipeline (not just Document Q&A).
    # Reads typed UserIntent.task_intent="draft" (handles native-language
    # verbs, polite phrasings, conservative defaults) instead of regex.
    if fc and fc.has_content and _wants_drafting(extracted_intent):
        if "Drafting" not in tasks_planned:
            tasks_planned.insert(0, "Drafting")
            log.info("Draft-from-file detected — adding Drafting agent",
                     file_names=fc.file_names, plan=tasks_planned)

    progress("orchestrator", f"Identified: {', '.join(tasks_planned)}", substep=True, detail=task, step="classify")

    # Multi-intent enrichment driven by typed UserIntent. Replaces the
    # keyword-scan safety net. When the extractor is confident, the
    # plan reflects exactly what the user asked for; when confidence
    # is low, nothing is added (we trust the planner LLM).
    if task not in ("Non_legal", "Document"):
        extra = _detect_multi_intent(extracted_intent, task)
        for agent in extra:
            if agent not in tasks_planned:
                tasks_planned.append(agent)
        if extra:
            log.info("Multi-intent enrichment via UserIntent",
                     extra=extra, all_agents=tasks_planned)

    # Drafting reconciliation (post multi-intent): when Drafting appears in
    # the plan but wasn't the primary task, the typed UserIntent from the
    # extractor is the authoritative signal on whether the user actually
    # wants a draft. Two branches:
    #  (a) `_wants_drafting(intent)` True — the intent extractor confidently
    #      says task_intent="draft". The initial classifier misclassified
    #      (e.g. as Scenario). Promote Drafting to primary and let the
    #      dedicated Drafting pipeline (ES reference lookup + Gemini 2.5 Pro
    #      + self_refine) run instead of the multi-agent fan-out. This
    #      restores the previously-broken flow where a "draft a plaint for
    #      partition" request routed to Scenario+Legislation+Judgment and
    #      never touched the Drafting agent.
    #  (b) `_wants_drafting(intent)` False — Drafting arrived from the LLM
    #      planner without intent-extractor backing (classifier
    #      hallucination). Strip it so the cite-appendix-OFF branch below
    #      doesn't silently drop the true primary agents.
    if task != "Drafting" and "Drafting" in tasks_planned:
        if _wants_drafting(extracted_intent):
            log.info("Drafting promoted to primary via typed intent",
                     prior_task=task, agents=tasks_planned)
            task = "Drafting"
            tasks_planned = ["Drafting"] + [a for a in tasks_planned if a != "Drafting"]
        else:
            tasks_planned = [a for a in tasks_planned if a != "Drafting"]
            if not tasks_planned:
                tasks_planned = [task]
            log.info("Drafting stripped (post multi-intent) — typed intent does not confirm draft",
                     task=task, agents=tasks_planned)

    # Drafting citation agents
    # Phase 1: gated behind cite_appendix flag (per-request) + env default.
    # Default OFF — citations doubled response length and ~50% of token spend
    # without making the draft itself more file-ready. Callers that want the
    # appendix pass `cite_appendix=true`.
    has_drafting = task == "Drafting" or "Drafting" in tasks_planned
    if has_drafting:
        if "Drafting" not in tasks_planned:
            tasks_planned.insert(0, "Drafting")
        from core.settings import DRAFTING_CITE_APPENDIX_DEFAULT
        _flag = state.get("cite_appendix")
        cite_appendix_on = _flag if _flag is not None else DRAFTING_CITE_APPENDIX_DEFAULT
        # The server-side tax-appellate keyword auto-enable was removed
        # 2026-07-25. Callers must pass cite_appendix=true when they want
        # citation fanout on CIT(A)/ITAT/GST/NCLT written submissions.
        # Client feedback 2026-06-19: legal notices need NO judgments and
        # office applications (RTI / department / employer / bank / etc.)
        # need NO judgments. These are correspondence, not pleadings. If
        # the intent extractor identified the request as one of these,
        # FORCE the citation appendix OFF even when the caller passed
        # cite_appendix=true. The drafting agent's stance + section prompt
        # is the second layer of defence (it strips case-law from the body).
        _non_pleading_artifacts = {
            LegalArtifact.LEGAL_NOTICE_DRAFT,
            LegalArtifact.OFFICE_APPLICATION,
        }
        _non_pleading_draft = (
            extracted_intent is not None
            and extracted_intent.legal_artifact in _non_pleading_artifacts
        )
        if cite_appendix_on and _non_pleading_draft:
            cite_appendix_on = False
            log.info(
                "cite_appendix force-disabled — non-pleading draft (notice / "
                "office application); judgments / statute appendix would be "
                "noise on a correspondence-style document",
                artifact=extracted_intent.legal_artifact.value,
            )
        if cite_appendix_on:
            citation_agents = _select_citation_agents(extracted_intent, tasks_planned)
            for ca in citation_agents:
                if ca not in tasks_planned:
                    tasks_planned.append(ca)
            tasks_planned = tasks_planned[:4]
            log.info("Drafting citation appendix enabled",
                     citation_agents=citation_agents,
                     source="request" if _flag is not None else "env_default")
        else:
            tasks_planned = [t for t in tasks_planned if t == "Drafting" or t == "Document"][:3]
            log.info("Drafting citation appendix skipped",
                     source="request" if _flag is not None else "env_default")
    else:
        # Bumped from [:3] to [:4] on 2026-06-30 so multi-intent enrichment
        # can fan out to BOTH SCI_Judgment AND Judgment alongside the
        # primary task (e.g. Scenario+Legislation+Judgment+SCI_Judgment for
        # an "arguments with case laws" prompt). The previous 3-cap silently
        # dropped SCI_Judgment whenever 4 agents were planned, which is why
        # generic case-law requests on substantive Indian-law topics never
        # surfaced real Supreme Court precedents from the SCI corpus.
        tasks_planned = tasks_planned[:4]

    # Intent-driven plan validation (Newacts/Legislation veto + SCI/GST enrich)
    tasks_planned = _validate_and_enrich_plan(
        tasks_planned, extracted_intent, log,
    )

    # Review-and-Redraft with upload: strip citation agents.
    #
    # When the user uploaded a document AND asked to review/redraft it, the
    # Drafting agent is authoritative for both the corrected draft AND the
    # supporting precedents (via `_gather_relevant_context` — SC/HC/Legis/
    # Newacts top hits retrieved against the user's actual matter). Keeping
    # SCI_Judgment / Judgment / GST_Judgment co-planned on a topic-loose
    # "landmark case laws" rewrite fires each of those agents' generation
    # LLMs with a vague query, and the Draft-Aware append-only synth path
    # (orchestrator_synthesize_node) then appends their prose verbatim
    # under `### <AGENT> CITATIONS:` — the exact "highly hallucination"
    # failure mode the client reported on 2026-07-26 (Section 290 BNSS
    # plea-bargaining smoke).
    if (
        fc and fc.has_content
        and "Drafting" in tasks_planned
        and _has_review_redraft_verbs(_original_query)
    ):
        _stripped = [
            a for a in tasks_planned
            if a not in _CITATION_AGENTS_TO_STRIP_ON_REVIEW
        ]
        _removed = [a for a in tasks_planned if a in _CITATION_AGENTS_TO_STRIP_ON_REVIEW]
        if _removed:
            log.info(
                "Review-and-redraft with upload: citation agents stripped "
                "(Drafting retrieves precedents via _gather_relevant_context)",
                removed=_removed, kept=_stripped,
            )
            tasks_planned = _stripped

    # The tax-appellate fan-out guardrail was removed on 2026-07-25 along
    # with the _is_tax_appellate_query keyword detector it depended on.
    # Callers explicitly opt in to citation fanout via cite_appendix=true;
    # _select_citation_agents already chooses corpus-only agents.

    # Step 4: Per-agent query rewriting (only for multi-agent plans)
    agent_queries = {}
    if len(tasks_planned) > 1:
        progress("orchestrator", f"Preparing search queries for {len(tasks_planned)} agents...", found=len(tasks_planned), step="plan")
        try:
            agent_queries = await asyncio.wait_for(
                asyncio.to_thread(_rewrite_queries_for_agents, query, tasks_planned),
                timeout=15,
            )
        except asyncio.TimeoutError:
            log.warning("Per-agent query rewriting timed out")

    # NOTE: Previously this block concatenated 30K chars of PDF text into
    # agent_queries["Drafting"] so the drafting agent would receive the
    # uploaded-document content. That approach had three flaws (BUG-02, BUG-05,
    # BUG-16): (a) it bloated the BM25 template-search query and biased
    # selection toward irrelevant templates, (b) it buried the facts at the
    # end of the prompt where the LLM ignored them, and (c) it forced the
    # entire pipeline through one giant string. The drafting agent now reads
    # file_context directly via FileContextData.from_state(state) and passes
    # the document text as an explicit FACTS field to outline + section
    # generation. The agent_queries["Drafting"] entry now holds only the
    # user's clean question (used for template search and selection).
    if "Drafting" in tasks_planned and fc and fc.has_content and fc.chromadb_collections:
        log.info("Drafting will receive uploaded document via file_context",
                 collections=len(fc.chromadb_collections),
                 file_names=fc.file_names)

    log.info("Plan phase completed",
             task=task, agents_planned=tasks_planned,
             agent_count=len(tasks_planned),
             agent_queries_generated=len(agent_queries),
             has_response_instructions=bool(response_instructions))

    result = {
        "query": query,  # normalized English query replaces original
        "task": task,
        "tasks_planned": tasks_planned,
        "agent_queries": agent_queries,
        "response_instructions": response_instructions,
    }
    # Phase 1: surface the structured intent on state when it was successfully
    # extracted. Downstream consumers can opt in to reading it ahead of Phase 2
    # by checking state.get("user_intent"). Stays None otherwise.
    if extracted_intent is not None:
        result["user_intent"] = extracted_intent
        # Phase 2.3 follow-through: when the user EXPLICITLY named a target
        # language (e.g. "Section 131 in Hindi" — typed in Latin script so
        # langdetect says "en", but the extractor catches the directive),
        # override state["user_language"] so EVERY downstream consumer
        # picks up the right language: the domain agents (Legislation,
        # Newacts, etc.) localize their own prompts via this field, the
        # synthesizer's localize_prompt reads it, and the final guardrail
        # respects it. Without this override the user gets English back even
        # though every layer of the pipeline had the right signal available.
        if (
            extracted_intent.confidence >= 0.7
            and extracted_intent.language_explicit
        ):
            result["user_language"] = extracted_intent.language
    return result


# ---------------------------------------------------------------------------
# Multi-agent dedup helpers — used by the primary-task-aware synthesis to
# skip supporting agents whose content substantially overlaps the primary
# (e.g. Legislation duplicating Constitution's Article 21 explanation).
# Cheap O(N) token-set comparison; no LLM call.
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset((
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "must", "shall",
    "to", "of", "in", "on", "at", "by", "for", "with", "from", "as",
    "it", "its", "this", "that", "these", "those", "such", "which",
    "who", "whom", "what", "when", "where", "how", "why", "than", "then",
    "if", "any", "all", "no", "not", "also", "into", "under", "over",
    "between", "among", "where", "while", "however", "therefore", "thus",
    "i", "we", "you", "he", "she", "they", "them", "us", "our", "your",
    "their", "his", "her", "my", "its",
))


def _content_token_set(text: str) -> set[str]:
    """Token bag for overlap comparison. Lowercase, drop stop words, keep
    legal-meaningful tokens (act names, section numbers, doctrine names).
    """
    if not text:
        return set()
    tokens = re.findall(r"[A-Za-z]{3,}|\d{3,}", text.lower())
    return {t for t in tokens if t not in _STOP_WORDS}


def _token_overlap_ratio(primary: set[str], supporting: set[str]) -> float:
    """Fraction of supporting tokens that appear in primary. 0.0-1.0.

    Asymmetric: measures how much of `supporting` is redundant given
    `primary`. A supporting response that's 50%+ contained in primary
    is treated as redundant and skipped.
    """
    if not supporting:
        return 0.0
    overlap = len(supporting & primary)
    return overlap / len(supporting)


def _build_source_registry(agent_results: dict[str, AgentResult]) -> SourceRegistry:
    """Auto-lift every agent's retrieved SourceMetadata into a unified
    SourceRegistry.

    Called once at the top of synthesis. Downstream consumers (synthesis
    prompt, self_refine critic/refiner, per-agent primary-picker) read from
    the returned registry so citations, quoted statutes, and PDF URLs in the
    final answer are grounded to what was actually retrieved — not
    fabricated from training memory.

    Adapters live in `core.source_registry`. Records with no id / no title
    are silently dropped (see `source_from_*` adapters).
    """
    registry = SourceRegistry()
    for agent_name, result in agent_results.items():
        if not result or not getattr(result, "sources", None):
            continue
        for meta in result.sources:
            st = getattr(meta, "source_type", "")
            record = None
            if st == "sci_judgment":
                record = source_from_sci(meta)
            elif st in ("judgment", "gst_judgment"):
                record = source_from_hc(meta)
            elif st in ("legislation", "newacts", "constitution", "maxim"):
                record = source_from_legislation(meta)
            elif st in ("scenario", "scenario_web", "legal_concepts", "document"):
                record = source_from_web(meta)
            else:
                # Unknown source_type — try each adapter in order; the
                # first that returns non-None wins. Keeps the registry
                # tolerant of new source types added downstream.
                for adapter in (source_from_sci, source_from_hc,
                                source_from_legislation, source_from_web):
                    record = adapter(meta)
                    if record:
                        break
            if record:
                registry.add(record)
    return registry


async def orchestrator_synthesize_node(state: LegalAgentState) -> dict:
    """Phase 2 — Merge results from all domain agents into final response.

    Single agent → pass through directly.
    Multiple agents → LLM synthesis into coherent, unified response.
    """
    agent_results: dict[str, AgentResult] = state.get("agent_results", {})
    query = state.get("query", state["original_query"])
    response_instructions = state.get("response_instructions", "")
    # Phase 2: prefer extractor's explicit language over langdetect when the
    # user named a target language (e.g. "Section 131 in Hindi" — langdetect
    # says "en" because the query is in Latin script, but the extractor
    # caught the explicit "in Hindi" directive).
    user_language = _resolve_user_language(state)

    # Phase D (RAG attachment routing plan, 2026-06-28): no longer
    # pre-pending uploaded file text into the synthesis query. Agents that
    # need the attachment (Drafting, Document) read it themselves via
    # get_full_attachment(collection_id). Synthesis only merges results.
    fc = FileContextData.from_state(state)

    # Phase 2 (source-registry pipeline): auto-lift every retrieved
    # SourceMetadata into a single SourceRegistry. Downstream prompt
    # interpolation (SYNTHESIS_PROMPT `{retrieved_sources}` slot) and
    # self_refine critic/refiner read from this registry so citations,
    # quoted statutes, and PDF URLs in the final answer are traceable to
    # what was actually retrieved — never fabricated from training memory.
    #
    # Two inputs, merged:
    #   1. `state["source_registry"]` — written directly by agents during
    #      fan-out and merged by the `merge_source_registries` reducer
    #      (core/state.py). This carries sources an agent retrieved but does
    #      NOT report in `AgentResult.sources` — e.g. Drafting reports only
    #      its reference template there, while the BNS sections, legislation
    #      and judgments it pulled via `_gather_relevant_context` would
    #      otherwise be invisible to synthesis and to the critic below.
    #   2. `_build_source_registry(agent_results)` — derived from every
    #      agent's reported `AgentResult.sources`.
    #
    # Deriving from agent_results alone was the previous behaviour; the state
    # channel is additive, so agents that write nothing lose nothing.
    source_registry = merge_source_registries(
        state.get("source_registry"),
        _build_source_registry(agent_results),
    )

    log.info("Synthesize phase started",
             agents_received=list(agent_results.keys()),
             registry_size=len(source_registry),
             has_response_instructions=bool(response_instructions))

    progress("orchestrator", f"Merging results from {len(agent_results)} agents...", found=len(agent_results), step="synthesize")

    if not agent_results:
        log.warning("No agent results to synthesize")
        return {
            "final_response": "No results were found for your query. Please try rephrasing.",
            "source_metadata": [],
            "tokens_consumed": 0,
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
        # Drafting + uploaded files: DO NOT fire the generic web-search
        # last resort. Web search knows nothing about the user's PDFs and
        # would produce a generic essay that ignores their case documents,
        # while looking legitimate. Return a specific temporary-failure
        # message instead so the user knows to retry.
        # See Buglist/prod_bug_inventory_2026-07-29.md Bug #5.
        planned = state.get("tasks_planned") or []
        task = state.get("task")
        is_drafting_request = (
            task == "Drafting" or "Drafting" in planned
        )
        if is_drafting_request and fc and fc.has_content:
            file_hint = (
                f" (attached: {', '.join(fc.file_names[:3])})"
                if fc.file_names else ""
            )
            log.warning(
                "All agents empty on Drafting-with-files — suppressing web fallback",
                planned=planned, file_count=len(fc.file_names),
            )
            return {
                "final_response": (
                    "I couldn't complete this draft right now — the AI "
                    "backend is experiencing high load. Please try again "
                    f"in about 30 seconds. Your uploaded documents{file_hint} "
                    "are still attached to this thread."
                ),
                "source_metadata": [],
                "tokens_consumed": 0,
            }

        log.warning("All agents empty — invoking web search last resort")
        from core.agent_fallback import web_search_fallback
        fallback = await web_search_fallback(
            query, "Orchestrator",
            "You are Lawttorney, an Indian legal AI assistant. "
            "Answer the user's legal question comprehensively with citations.",
            user_language=_resolve_user_language(state),
            intent=state.get("user_intent"),
        )
        if fallback.content:
            return {
                "final_response": fallback.content,
                "source_metadata": _serialize_sources(fallback),
                "tokens_consumed": fallback.tokens_consumed,
            }
        return {
            "final_response": "The agents could not find relevant information. Please try a different query.",
            "source_metadata": [],
            "tokens_consumed": 0,
        }

    # Single agent — pass through directly (with auto-citation for Drafting)
    # BUT if file context has inline text, always synthesize so file content is used
    # If file context has inline text but single result is NOT from Document agent,
    # force synthesis so file content gets incorporated. Otherwise pass through.
    #
    # Also force synthesis when the user explicitly asked for a comparison table —
    # pass-through would deliver the raw agent prose, bypassing SYNTHESIS_TABLE_PROMPT
    # and leaving the user without the table they requested.
    has_unprocessed_file = (fc is not None and fc.chromadb_collections
                           and "Document" not in valid_results)
    # Phase 2: prefer structured intent (state["user_intent"]) over the legacy
    # regex when extractor confidence is high. Also force synthesis when the
    # user explicitly named a non-English target language — pass-through skips
    # the localize_prompt step that the synthesis path applies.
    _wants_table_single = _resolve_wants_table(state)
    _explicit_lang_override = _resolve_explicit_non_english(state)
    if (
        len(valid_results) == 1
        and not has_unprocessed_file
        and not _wants_table_single
        and not _explicit_lang_override
    ):
        name, result = next(iter(valid_results.items()))

        # Drafting solo: previously this called _auto_cite_draft which used an
        # LLM to rewrite the draft and add citations. That LLM rewrite would
        # routinely strip facts the drafting agent had carefully extracted
        # from the user's uploaded file (BUG-03). Pass the draft through
        # unmodified, just stripping internal [CITE: No matching case] markers.
        if name == "Drafting":
            cleaned = _strip_internal_cite_markers(result.content)
            log.info("Drafting solo — passing through unmodified (BUG-03 fix)",
                     draft_len=len(result.content), cleaned_len=len(cleaned))
            return {
                "final_response": cleaned,
                "source_metadata": _serialize_sources(result),
                "tokens_consumed": result.tokens_consumed,
                # Persist rather than discard — see the pass-through note below.
                "source_registry": source_registry,
            }

        log.info("Single agent pass-through",
                 agent=name, content_len=len(result.content),
                 tokens=result.tokens_consumed,
                 registry_size=len(source_registry))
        return_dict = {
            "final_response": result.content,
            "source_metadata": _serialize_sources(result),
            "tokens_consumed": result.tokens_consumed,
            # The registry was built above and, until now, thrown away on this
            # path — the cost was paid and the result discarded. Persisting it
            # costs nothing and is the prerequisite for running the critic on
            # single-agent answers, which today return with no citation
            # grounding at all.
            "source_registry": source_registry,
        }
        return return_dict

    # --- Draft-Aware Synthesis ---
    # When Drafting is one of the agents, the drafting agent's output is the
    # primary content. Other agents' results become citation references that
    # we APPEND in a separate block — we MUST NOT rewrite the draft via an LLM
    # (BUG-03: an earlier _inject_citations_into_draft LLM call routinely
    # stripped real names/dates/amounts and replaced them with template
    # placeholders, losing all the file-grounded facts the drafting agent
    # had carefully extracted).
    if "Drafting" in valid_results:
        drafting_result = valid_results.pop("Drafting")
        citation_results = valid_results  # Judgment, Legislation, Newacts, Document, ...

        # When the user attached a file, the Document agent's analysis is
        # redundant with the drafting agent's content (drafting already
        # consumed the file's raw text directly). Including it in the
        # citation block can dilute the response with template-style language.
        fc_check = FileContextData.from_state(state)
        if fc_check and fc_check.has_content and "Document" in citation_results:
            log.info("Drafting+file: dropping Document from citation block "
                     "(content already incorporated into the draft)")
            citation_results = {k: v for k, v in citation_results.items() if k != "Document"}

        log.info("Draft-aware synthesis starting",
                 draft_len=len(drafting_result.content),
                 citation_agents=list(citation_results.keys()))

        all_serialized_sources = _serialize_sources(drafting_result)
        total_tokens = drafting_result.tokens_consumed

        citations_text = ""
        for name, result in citation_results.items():
            if result.content:
                citations_text += f"\n\n### {name.upper()} CITATIONS:\n{result.content}"
                total_tokens += result.tokens_consumed
                all_serialized_sources.extend(_serialize_sources(result))

        # APPEND-ONLY synthesis: preserve the draft verbatim, strip internal
        # [CITE: ...] placeholder markers (BUG-09), append a References section.
        try:
            enriched = _strip_internal_cite_markers(drafting_result.content)
            if citations_text.strip():
                enriched += "\n\n---\n\n## REFERENCES & CITATIONS\n" + citations_text

            log.info("Draft synthesis completed (append-only)",
                     enriched_len=len(enriched),
                     citation_agents=list(citation_results.keys()),
                     total_tokens=total_tokens)

            return {
                "final_response": enriched,
                "source_metadata": all_serialized_sources,
                "tokens_consumed": total_tokens,
            }

        except Exception as e:
            log.error("Draft synthesis failed, returning raw draft", error=str(e))
            return {
                "final_response": drafting_result.content,
                "source_metadata": all_serialized_sources,
                "tokens_consumed": total_tokens,
            }

    # --- Primary-Task-Aware Synthesis ---
    # The agent matching the LLM's primary task is the PRIMARY content; other
    # agents' content is appended under labelled headings. This replaces the
    # earlier judgment-only special case, which hijacked Scenario-primary
    # queries (e.g. "Prepare a cross-examination strategy for NDPS case")
    # by making Judgment's 5-case-summary template the main response — 80%
    # of the output was bail-case narratives, ~10% was the tactical content
    # the user actually asked for. Each agent's system prompt shapes its own
    # response (JUDGMENT_SYSTEM_PROMPT, SCI_JUDGMENT_SYSTEM_PROMPT,
    # SCENARIO_SYSTEM_PROMPT, NEWACTS_SYSTEM_PROMPT, etc), so letting the
    # primary task's agent own the layout preserves the structure the user
    # expects for THAT kind of query.
    #
    # Append-only: emit primary verbatim, then append each supporting agent's
    # content. No LLM rewrite, no token cost.
    #
    # Skip when the user asked for a comparison table (SYNTHESIS_TABLE_PROMPT
    # owns layout) or when unprocessed file context needs the LLM synthesizer.
    _PRIMARY_AGENT_FOR_TASK: dict[str, str] = {
        "Scenario":       "Scenario",
        "Judgment":       "Judgment",
        "SCI_Judgment":   "SCI_Judgment",
        "Newacts":        "Newacts",
        "Legislation":    "Legislation",
        "Constitution":   "Constitution",
        "Maxim":          "Maxim",
        "GST_Judgment":   "GST_Judgment",
        "Legal_Concepts": "Legal_Concepts",
        "Other":          "Scenario",   # fallback per planner contract
    }
    _SUPPORTING_HEADINGS: dict[str, str] = {
        "Newacts":      "## Statutory Provisions Referenced",
        "Legislation":  "## Statutory Provisions Referenced",
        "Judgment":     "## Supporting Case Authority",
        "SCI_Judgment": "## Related Supreme Court Authority",
        "Constitution": "## Constitutional Provisions Referenced",
        "Maxim":        "## Legal Maxims & Doctrines Referenced",
        "Scenario":     "## Additional Analysis",
        "Document":     "## From Your Uploaded Document",
        "GST_Judgment": "## Related GST/AAAR Authority",
    }

    primary_task_state = state.get("task")
    primary_agent_name = _PRIMARY_AGENT_FOR_TASK.get(primary_task_state or "")
    # Phase 2: intent-first resolution; regex fallback at low confidence.
    _wants_table_primary = _resolve_wants_table(state)

    if (
        primary_agent_name
        and primary_agent_name in valid_results
        and not has_unprocessed_file
        and not _wants_table_primary
    ):
        primary_result = valid_results.pop(primary_agent_name)
        supporting_results = valid_results

        log.info("Primary-task-aware synthesis starting",
                 task=primary_task_state, primary_agent=primary_agent_name,
                 primary_len=len(primary_result.content),
                 supporting_agents=list(supporting_results.keys()))

        primary = primary_result.content.rstrip()
        all_serialized_sources = _serialize_sources(primary_result)
        total_tokens = primary_result.tokens_consumed
        kept_supporting: dict[str, AgentResult] = {}
        skipped_redundant: list[str] = []

        # Unique-token dedup: keep a supporting agent's content iff it
        # contributes a meaningful number of UNIQUE informational tokens
        # beyond the primary. Measuring NEW tokens directly catches the
        # signal we care about: does the supporting agent add information?
        # Even when we drop the supporter's prose, we still merge its
        # sources — citations are valuable even when the surrounding prose
        # is redundant.
        primary_tokens = _content_token_set(primary)
        MIN_UNIQUE_TOKENS_TO_KEEP = 30

        for name, result in supporting_results.items():
            if not result.content:
                continue
            supporting_tokens = _content_token_set(result.content)
            unique_to_supporting = supporting_tokens - primary_tokens
            overlap_ratio = _token_overlap_ratio(primary_tokens, supporting_tokens)

            if (
                len(supporting_tokens) >= 50
                and len(unique_to_supporting) < MIN_UNIQUE_TOKENS_TO_KEEP
            ):
                skipped_redundant.append(
                    f"{name}(unique={len(unique_to_supporting)}, "
                    f"overlap={overlap_ratio:.0%})"
                )
                all_serialized_sources.extend(_serialize_sources(result))
                total_tokens += result.tokens_consumed
                continue
            kept_supporting[name] = result
            all_serialized_sources.extend(_serialize_sources(result))
            total_tokens += result.tokens_consumed

        if skipped_redundant:
            log.info("Dropped redundant supporting agents from synthesis",
                     skipped=skipped_redundant,
                     reason=f"unique tokens < {MIN_UNIQUE_TOKENS_TO_KEEP}")

        # Fast path — no supporting content survived dedup. Return primary
        # as-is; the primary agent already streamed its tokens to the
        # frontend, no merge cost, no token_reset.
        if not kept_supporting:
            log.info("Primary-only response — no non-redundant supporters",
                     task=primary_task_state, primary_agent=primary_agent_name,
                     final_len=len(primary), total_tokens=total_tokens)
            return {
                "final_response": primary,
                "source_metadata": all_serialized_sources,
                "tokens_consumed": total_tokens,
            }

        # Case-lookup short-circuit: SCI_Judgment and Judgment primaries
        # ship a mandated structural template (### Detailed Narrative,
        # ### Court Observations, **PDF Links:** block with clickable URLs
        # — see SCI_JUDGMENT_SYSTEM_PROMPT). The LLM merge path below
        # rewrites via SYNTHESIS_PROMPT, which has no rule to preserve
        # URLs or those exact headings — the merged output loses every
        # PDF link and renames the mandatory sections. Append-only concat
        # keeps primary verbatim so the user still gets clickable links
        # and the mandated structure, while supporting content lands
        # under its own labelled heading.
        if primary_agent_name in ("SCI_Judgment", "Judgment"):
            appendix_parts = []
            for name, result in kept_supporting.items():
                heading = _SUPPORTING_HEADINGS.get(name, f"## {name} Notes")
                appendix_parts.append(
                    f"\n\n---\n\n{heading}\n\n{result.content.strip()}"
                )
            final_response = primary + "".join(appendix_parts)
            log.info("Primary-task-aware append-only (case-lookup primary)",
                     task=primary_task_state, primary_agent=primary_agent_name,
                     primary_len=len(primary),
                     supporting_agents=list(kept_supporting.keys()),
                     final_len=len(final_response), total_tokens=total_tokens)
            return {
                "final_response": final_response,
                "source_metadata": all_serialized_sources,
                "tokens_consumed": total_tokens,
            }

        # LLM merge path — replaces append-only concat. Primary is anchor
        # for layout/shape; supporters contribute case law, statutes, or
        # complementary analysis. Merger produces ONE coherent response
        # (no `## Supporting X` / `## Related Y` cliff-edges, no
        # cross-agent duplication of the same case treatment, no language
        # leak when supporters write in English and primary is Gujarati).
        # SYNTHESIS_PROMPT already carries all the merge rules — organize
        # by legal argument (not by agent source), don't repeat, don't
        # mention "agents", respond in target language, preserve citations.
        log.info("Primary-task-aware merge (LLM) starting",
                 task=primary_task_state, primary_agent=primary_agent_name,
                 primary_len=len(primary),
                 supporting_agents=list(kept_supporting.keys()))

        # Primary's tokens have already streamed to the frontend during
        # its own generation. The merger will restream; emit token_reset
        # so the frontend clears its buffer before we push merged tokens.
        try:
            from langgraph.config import get_stream_writer
            _writer = get_stream_writer()
            _writer({"type": "token_reset"})
        except RuntimeError:
            pass

        try:
            # Build merge input — flag primary as anchor so the LLM
            # recognises which agent owns layout/shape for THIS task.
            _MAX_AGENT_CONTENT = 12000
            _primary_clip = primary if len(primary) <= _MAX_AGENT_CONTENT else \
                primary[:_MAX_AGENT_CONTENT] + "\n\n[... truncated for merge]"
            merge_input = (
                f"\n\n### PRIMARY AGENT ({primary_agent_name}) "
                f"— anchor for output shape and layout:\n{_primary_clip}"
            )
            for name, result in kept_supporting.items():
                content = result.content
                if len(content) > _MAX_AGENT_CONTENT:
                    content = content[:_MAX_AGENT_CONTENT] + "\n\n[... truncated for merge]"
                merge_input += f"\n\n### SUPPORTING AGENT ({name}):\n{content}"

            llm = get_gemini_flash_full(
                temperature=0.2,
                max_output_tokens=12288,
                thinking_budget=0,
            )
            prompt = ChatPromptTemplate.from_template(
                localize_prompt(SYNTHESIS_PROMPT, user_language, state.get("user_intent"))
            )
            chain = prompt | llm
            from core.streaming import stream_chain_response
            response = await stream_chain_response(chain, {
                "query": query,
                "agent_results": merge_input,
                "response_instructions": response_instructions or
                    "Standard legal response with proper citations and markdown formatting.",
                # Phase 3 (citation-grounding pipeline): the registry is the
                # authoritative allowed-citation whitelist for the merge.
                # SYNTHESIS_PROMPT rules 12/13 forbid citations outside this
                # pool and mandate inline PDF-URL preservation.
                "retrieved_sources": source_registry.serialize_for_prompt(),
            }, timeout=180)

            merged = response.content
            from core.token_tracker import record as _record_tokens
            merge_tokens = _record_tokens("Orchestrator", "merge_synthesis", response)
            total_tokens += merge_tokens

            log.info("Primary-task-aware merge (LLM) completed",
                     task=primary_task_state, primary_agent=primary_agent_name,
                     final_len=len(merged),
                     supporting_agents=list(kept_supporting.keys()),
                     total_tokens=total_tokens)

            # Phase 5 (citation-grounding pipeline): audit the merged
            # response against the source registry. self_refine is a no-op
            # when the intent has no directives AND the registry is empty,
            # so this is safe to call unconditionally. When the registry
            # carries retrievals, the critic checks for hallucinated
            # citations / bracket placeholders / invented PDF URLs via the
            # `unretrieved_citation` category, and the refiner rewrites on
            # violations using the registry as the allowed-citation pool.
            try:
                from core.self_refine import self_refine as _self_refine
                refined, _crit_history = await _self_refine(
                    merged,
                    query,
                    state.get("user_intent"),
                    source_registry=source_registry,
                )
                if refined and refined != merged:
                    log.info("Merge output refined by self_refine (citation grounding)",
                             pre_len=len(merged), post_len=len(refined),
                             iterations=len(_crit_history))
                    merged = refined
            except Exception as _refine_err:
                log.warning("self_refine failed after merge — using unrefined merged output",
                            error=short_err(_refine_err))

            return {
                "final_response": merged,
                "source_metadata": all_serialized_sources,
                "tokens_consumed": total_tokens,
            }

        except Exception as e:
            # Merge failure — fall back to append-only so the user still
            # gets a response. Primary's already-streamed tokens remain
            # visible to the frontend; the final response event carries
            # the append-only concatenation.
            log.error("Merge synthesis failed — falling back to append-only",
                      error=str(e), primary_agent=primary_agent_name,
                      supporting_agents=list(kept_supporting.keys()))
            appendix_parts = []
            for name, result in kept_supporting.items():
                heading = _SUPPORTING_HEADINGS.get(name, f"## {name} Notes")
                appendix_parts.append(f"\n\n---\n\n{heading}\n\n{result.content.strip()}")
            final_response = primary + "".join(appendix_parts)
            return {
                "final_response": final_response,
                "source_metadata": all_serialized_sources,
                "tokens_consumed": total_tokens,
            }

    # --- Generic Multi-Agent Synthesis (non-drafting) ---
    progress("orchestrator", "Composing final response...", step="synthesize")
    log.info("Multi-agent synthesis starting",
             agents=list(valid_results.keys()),
             content_lengths={n: len(r.content) for n, r in valid_results.items()})

    agent_results_text = ""
    total_tokens = 0
    all_serialized_sources = []

    _MAX_AGENT_CONTENT = 8000  # per-agent cap to prevent token explosion
    for name, result in valid_results.items():
        content = result.content
        if len(content) > _MAX_AGENT_CONTENT:
            content = content[:_MAX_AGENT_CONTENT] + "\n\n[... truncated for synthesis]"
        agent_results_text += f"\n\n### {name.upper()} AGENT RESULTS:\n{content}"
        total_tokens += result.tokens_consumed
        all_serialized_sources.extend(_serialize_sources(result))

    # Pick prompt template: table-mode if user asked for a comparison table,
    # else the general synthesis prompt. SYNTHESIS_TABLE_PROMPT constrains output
    # to a single markdown table with no preamble/postamble.
    # Phase 2: intent-first; regex falls back at low confidence or no intent.
    wants_table = _resolve_wants_table(state)
    synth_template = SYNTHESIS_TABLE_PROMPT if wants_table else SYNTHESIS_PROMPT
    _intent_for_log = state.get("user_intent")
    log.info("Synthesis prompt selected",
             template="SYNTHESIS_TABLE_PROMPT" if wants_table else "SYNTHESIS_PROMPT",
             source=("intent" if (
                 _intent_for_log is not None
                 and _intent_for_log.confidence >= _INTENT_CONFIDENCE_THRESHOLD
             ) else "regex_fallback"),
             response_instructions=response_instructions[:120] if response_instructions else "")

    try:
        with log_time(log, "LLM synthesis"):
            # Synthesis is pure generation (merge → format), not analysis. Disable
            # thinking_budget to reclaim the full 65K-token output budget for visible
            # content. Without this, Gemini 2.5 Flash can silently spend thousands
            # of tokens on hidden reasoning while emitting only a handful of visible
            # chars (observed in production logs).
            # Use temperature=0 for table mode (deterministic formatting).
            # max_output_tokens capped at 12288 (~48k chars) to bound the
            # synthesis output. Default 65535 allowed runaway markdown-table
            # column-padding loops that produced 140k-char responses with a
            # single 125k-char dash-only table separator row (see incident
            # thread 73a59cc4-fbfa-4d7b-b493-948a974c1496). 12k tokens is
            # comfortably above any legitimate synthesis of 3 agent outputs.
            llm = get_gemini_flash_full(
                temperature=0.0 if wants_table else 0.2,
                max_output_tokens=12288,
                thinking_budget=0,
            )
            prompt = ChatPromptTemplate.from_template(
                localize_prompt(synth_template, user_language, state.get("user_intent"))
            )
            chain = prompt | llm

            from core.streaming import stream_chain_response
            _chain_input = {
                "query": query,
                "agent_results": agent_results_text,
                "response_instructions": response_instructions or "Standard legal response with proper citations and markdown formatting.",
            }
            # Phase 3 (citation-grounding pipeline): SYNTHESIS_PROMPT carries
            # the {retrieved_sources} slot for rules 12/13 (fidelity + PDF
            # link preservation). SYNTHESIS_TABLE_PROMPT does not, so only
            # inject when the general prompt is selected.
            if not wants_table:
                _chain_input["retrieved_sources"] = source_registry.serialize_for_prompt()
            response = await stream_chain_response(chain, _chain_input, timeout=180)  # Multi-agent synthesis needs more time

        synthesized = response.text
        from core.token_tracker import record as _record_tokens
        synth_tokens = _record_tokens("Orchestrator", "synthesize", response)

        # Defense-in-depth cap: at 65K-token output ceiling (~260K chars at
        # ~4 chars/token) the LLM cannot legitimately exceed ~260K chars. We
        # cap at 250K to allow full-budget responses but still catch upstream
        # streaming bugs (e.g. cumulative-content chunk double-counting).
        _MAX_SYNTHESIS_LEN = 250_000
        if len(synthesized) > _MAX_SYNTHESIS_LEN:
            log.warning("Synthesis output too large, truncating",
                        original_len=len(synthesized), cap=_MAX_SYNTHESIS_LEN)
            synthesized = synthesized[:_MAX_SYNTHESIS_LEN] + "\n\n*[Response truncated for length]*"

        total_tokens += synth_tokens
        log.info("Synthesis completed",
                 synthesized_len=len(synthesized),
                 synthesis_tokens=synth_tokens, total_tokens=total_tokens)

        # Phase 5 (citation-grounding pipeline): audit the synthesized
        # response against the source registry. Skipped when wants_table
        # (tables shouldn't carry case-law citations to hallucinate) or
        # when the response was truncated (refiner might chop legitimate
        # content). Otherwise the critic looks for hallucinated citations,
        # bracket placeholders, and invented PDF URLs, and the refiner
        # rewrites on violations using the registry as the whitelist.
        if not wants_table and len(source_registry) > 0:
            try:
                from core.self_refine import self_refine as _self_refine_pkg
                refined_synth, _crit_hist = await _self_refine_pkg(
                    synthesized,
                    query,
                    state.get("user_intent"),
                    source_registry=source_registry,
                )
                if refined_synth and refined_synth != synthesized:
                    log.info("Synthesis refined by self_refine (citation grounding)",
                             pre_len=len(synthesized), post_len=len(refined_synth),
                             iterations=len(_crit_hist))
                    synthesized = refined_synth
            except Exception as _refine_err:
                log.warning("self_refine failed after synthesis — using unrefined output",
                            error=short_err(_refine_err))

    except Exception as e:
        log.error("LLM synthesis failed, concatenating results", error=str(e))
        parts = []
        for name, result in valid_results.items():
            parts.append(result.content)
        synthesized = "\n\n---\n\n".join(parts)

    return {
        "final_response": synthesized,
        "source_metadata": all_serialized_sources,
        "tokens_consumed": total_tokens,
    }


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
            "state_ut", "brief_of_order", "ar_order_no_date",
            "section_number", "act_name",
            "template_type",
            "web_url", "web_title",
        ]:
            val = getattr(s, field_name, None)
            if val is not None and val != "" and val != []:
                d[field_name] = val
        serialized.append(d)
    return serialized


# --- Draft Citation Helpers ---

async def _inject_citations_into_draft(
    query: str, draft: str, citations_text: str, user_language: str = "en",
) -> tuple[str, int]:
    """Preserve full draft and inject real citations from ALL agents.

    Uses Gemini 2.5 Flash for fast citation injection.
    Returns: (enriched_content, tokens_consumed)
    """
    with log_time(log, "Draft citation injection"):
        llm = get_drafting_llm()
        prompt = ChatPromptTemplate.from_template(
            localize_prompt(DRAFT_SYNTHESIS_PROMPT, user_language)
        )
        chain = prompt | llm

        from core.streaming import stream_chain_response
        response = await stream_chain_response(chain, {
            "query": query,
            "draft": draft,
            "citations": citations_text,
        }, timeout=180)  # Citation injection on full draft needs more time

    from core.token_tracker import record as _record_tokens
    tokens = _record_tokens("Orchestrator", "inject_citations", response)
    log.info("Citation injection completed",
             draft_len=len(draft), enriched_len=len(response.text),
             tokens=tokens)
    return response.text, tokens


async def _auto_cite_draft(
    query: str, draft: str, response_instructions: str = "", user_language: str = "en",
) -> tuple[str, int]:
    """Add AI-generated citations when no database agents provided results.

    Uses Gemini 2.5 Flash for fast auto-citation.
    Returns: (enriched_content, tokens_consumed)
    """
    with log_time(log, "Draft auto-citation"):
        llm = get_drafting_llm()
        prompt = ChatPromptTemplate.from_template(
            localize_prompt(DRAFT_CITATION_PROMPT, user_language)
        )
        chain = prompt | llm

        instructions_text = ""
        if response_instructions:
            instructions_text = f"User's format preferences: {response_instructions}"

        from core.streaming import stream_chain_response
        response = await stream_chain_response(chain, {
            "query": query,
            "draft": draft,
            "response_instructions": instructions_text,
        }, timeout=180)  # Auto-citation on full draft needs more time

    from core.token_tracker import record as _record_tokens
    tokens = _record_tokens("Orchestrator", "auto_cite", response)
    log.info("Auto-citation completed",
             draft_len=len(draft), enriched_len=len(response.text),
             tokens=tokens)
    return response.text, tokens
