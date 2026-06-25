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
from core.logger import get_logger, log_time
from core.progress import progress
from config.prompts import (
    TASK_CLASSIFICATION_PROMPT, SYNTHESIS_PROMPT, SYNTHESIS_TABLE_PROMPT,
    USER_INTENT_EXTRACTION_PROMPT,
    wrap_untrusted,
    DRAFT_SYNTHESIS_PROMPT, DRAFT_CITATION_PROMPT,
)
from config.intent import LegalArtifact, UserIntent, default_intent

log = get_logger("Orchestrator")

import re

# --- Response Instructions Sanitization ---

_INJECTION_PATTERNS = re.compile(
    r"(?i)(ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|rules?|prompts?)"
    r"|disregard\s+(everything|all|the)\b"
    r"|you\s+are\s+now\b"
    r"|system\s*:\s*"
    r"|new\s+instructions?\s*:"
    r"|override\s+(all|previous|the)\b"
    r"|do\s+not\s+follow\b"
    r"|forget\s+(all|your|previous)\b)",
)

_MAX_INSTRUCTIONS_LEN = 500


# Tax / quasi-judicial appellate triggers — when any of these appears in
# the user's drafting query, the orchestrator auto-enables cite_appendix
# (fans Judgment / SCI_Judgment / Legislation alongside Drafting) so the
# resulting written submission cites real case laws from the corpus,
# not the section-LLM's parametric memory. Sagar feedback 2026-06-18 —
# CIT(A) written submissions specifically need "Add valid and correct
# relevant case laws/citations with Case Number and Case Year" plus
# AO-citation rebuttal, both of which require corpus retrieval.
#
# NB: bracketed forms like "CIT(A)" don't play nicely with \b boundaries
# (\b doesn't match between `)` and a following non-word char). The
# patterns below avoid trailing \b for those forms.
_TAX_APPELLATE_PATTERNS = (
    re.compile(r"\bCIT\s*\(\s*A(?:ppeals?)?\s*\)", re.IGNORECASE),
    re.compile(r"\bCIT\s+Appeals?\b", re.IGNORECASE),
    re.compile(
        r"\bCommissioner\s+of\s+Income[\s-]*Tax\s*\(\s*Appeals?\s*\)",
        re.IGNORECASE,
    ),
    re.compile(r"\bITAT\b", re.IGNORECASE),
    re.compile(r"\bIncome[\s-]*Tax\s+Appellate\s+Tribunal\b", re.IGNORECASE),
    re.compile(r"\bNFAC\b", re.IGNORECASE),
    re.compile(r"\bNational\s+Faceless\s+Appeal\s+Centre\b", re.IGNORECASE),
    re.compile(r"\bForm\s*35\b", re.IGNORECASE),
    re.compile(r"\bSection\s*143\s*\(\s*3\s*\)", re.IGNORECASE),
    re.compile(r"\bSection\s*144\s*B\b", re.IGNORECASE),
    re.compile(
        r"\bSection\s*250\s+(?:of\s+the\s+)?Income[\s-]*Tax\s+Act\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bGST\s+Appellate\b", re.IGNORECASE),
    re.compile(r"\bAAAR\b", re.IGNORECASE),
    re.compile(
        r"\bAppellate\s+Authority\s+for\s+Advance\s+Ruling\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bCESTAT\b", re.IGNORECASE),
    re.compile(r"\bNCLT\b", re.IGNORECASE),
    re.compile(r"\bNCLAT\b", re.IGNORECASE),
    re.compile(
        r"\bNational\s+Company\s+Law\s+(?:Appellate\s+)?Tribunal\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bSAT\s+(?:Mumbai|Delhi)\b", re.IGNORECASE),
    re.compile(r"\bSecurities\s+Appellate\s+Tribunal\b", re.IGNORECASE),
    re.compile(r"\bDRT\b|\bDRAT\b", re.IGNORECASE),
    re.compile(
        r"\bDebt(?:s)?\s+Recovery\s+(?:Appellate\s+)?Tribunal\b",
        re.IGNORECASE,
    ),
)


def _is_tax_appellate_query(query: str) -> bool:
    """True iff the query looks like a tax / quasi-judicial appellate
    written submission. Used to auto-enable cite_appendix for these
    drafts so case laws come from the corpus retrieval pipeline rather
    than the section LLM's parametric memory.
    """
    if not query:
        return False
    return any(p.search(query) for p in _TAX_APPELLATE_PATTERNS)


def _sanitize_response_instructions(instructions: str) -> str:
    """Strip prompt-injection patterns and cap length of LLM-extracted instructions."""
    if not instructions:
        return ""
    cleaned = _INJECTION_PATTERNS.sub("", instructions).strip()
    if len(cleaned) > _MAX_INSTRUCTIONS_LEN:
        cleaned = cleaned[:_MAX_INSTRUCTIONS_LEN]
    return cleaned


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


# --- Long Query Extraction ---
# When users paste 20-30K chars (e.g. a contract + question), we separate
# the concise question from the pasted context. Classification/routing uses
# only the question; the generating agent gets the full text as user_context.

_LONG_QUERY_THRESHOLD = 5000  # chars — below this, treat as normal query

_QUERY_EXTRACT_PROMPT = """You are a legal AI assistant. The user has sent a very long message that likely contains a pasted document (contract, notice, agreement, judgment) along with their actual question.

Your task: Extract the user's actual QUESTION or INSTRUCTION from the text. The question is usually at the beginning or end of the message.

If the entire text IS the document with no explicit question, infer the most likely intent (e.g., "Review this document and identify key legal issues").

User message (first 3000 chars):
{text_start}

---
User message (last 2000 chars):
{text_end}

Return JSON:
{{"question": "<the user's actual question/instruction in 1-3 sentences>", "document_type": "<contract|notice|agreement|judgment|legislation|petition|affidavit|other>"}}"""


class QueryExtraction(BaseModel):
    question: str = Field(..., description="The user's actual question or instruction")
    document_type: str = Field("other", description="Type of document pasted")


def _extract_question_from_long_query(query: str) -> tuple[str, str]:
    """Extract concise question from a long query containing pasted content.

    Returns (concise_question, document_type).
    Falls back to first 500 chars if extraction fails.
    """
    try:
        with log_time(log, "Long query extraction"):
            llm = get_gemini_flash(temperature=0.1).with_structured_output(
                QueryExtraction, include_raw=True,
            )
            prompt = ChatPromptTemplate.from_template(_QUERY_EXTRACT_PROMPT)
            chain = prompt | llm
            raw_and_parsed = chain.invoke({
                "text_start": query[:3000],
                "text_end": query[-2000:],
            })
        from core.token_tracker import record as _record_tokens
        _record_tokens("Orchestrator", "extract_long_query", raw_and_parsed.get("raw"))
        result = raw_and_parsed["parsed"]

        log.info("Question extracted from long query",
                 question_len=len(result.question),
                 doc_type=result.document_type,
                 original_len=len(query))
        return result.question, result.document_type

    except Exception as e:
        log.warning("Long query extraction failed, using truncated query",
                    error=str(e))
        return query[:500], "other"


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


PLAN_PROMPT = """You are a legal query planner. Given a query and its primary task type, determine which agents should handle it.

Available agents:
- Legislation: Central/state law sections and provisions (all acts EXCEPT the 6 below)
- Judgment: Court case laws, citations, precedents (general / High Court / unspecified courts)
- Newacts: ONLY these 6 acts: BNS/IPC, BNSS/CrPC, BSA/IEA
- Drafting: ONLY when the user explicitly asks the AI to CREATE / WRITE / PREPARE / DRAFT a legal document (verbs: draft, write, prepare, create, generate, compose, draw up, "give me a [doc]", "I need a [doc]"). NEVER for questions ABOUT documents (format, structure, essential elements, requirements, how to file, when to use, difference between X and Y) — those go to Legal_Concepts / Legislation / Scenario.
- Scenario: Situational analysis, legal advice, remedies, web search
- Constitution: Constitutional provisions, fundamental rights, Articles
- Maxim: Legal maxims and doctrines (Latin phrases like res judicata, audi alteram partem, estoppel)
- Legal_Concepts: General legal explanations (use only when no specific category applies)
- SCI_Judgment: Supreme Court of India case search
- GST_Judgment: GST Appellate Authority for Advance Ruling (AAAR) orders. Use for any query about GST/CGST/SGST/IGST advance rulings, AAR, AAAR, GST classification appeals, GST ITC disputes, GST valuation rulings, or state-level GST appellate orders.
- Document: Answers questions about user-uploaded documents (PDFs, images, DOCX). Use when user has uploaded files.

Rules:
1. Most queries need only the PRIMARY agent matching the task type.
2. Use MULTIPLE agents when the query explicitly or implicitly asks for different types of information:
   - "Draft bail application with relevant case laws" → [Drafting, Judgment]
   - "Section 438 BNSS with SC precedents" → [Newacts, SCI_Judgment]
   - "Arguments on behalf of plaintiff and defendant" → [Scenario, Judgment]
   - "Explain Article 21 and related case laws" → [Constitution, Judgment]
3. COMPLEX SCENARIO QUERIES: When a query describes a factual situation AND asks for arguments, defences, legal provisions, citations, or remedies, use MULTIPLE agents:
   - Scenario (for analysis/arguments/remedies) + Judgment (for case laws) + Legislation/Newacts (for statutory provisions)
   - Example: "A doctor operated on wrong patient. What are the legal arguments and relevant case laws and statutory provisions?" → [Scenario, Judgment, Legislation]
   - Example: "My landlord locked me out. Arguments with citations and relevant IPC sections" → [Scenario, Judgment, Newacts]
4. When a query mentions BOTH a constitutional concept AND a legal maxim/doctrine → [Constitution, Maxim]
5. When a query references a named SC landmark case alongside a constitutional topic → include SCI_Judgment.
6. For drafting requests: ONLY include Drafting when the user explicitly asks the AI to CREATE / WRITE / PREPARE / DRAFT a legal document. Questions ABOUT documents (format, structure, essential elements, ingredients, requirements, how to file, when to use, difference between X and Y) are NOT drafting requests — route those to Legal_Concepts, Legislation, or Scenario. "What legal options" or "how can I" is NOT drafting.
7. Never use more than 3 agents.
8. "Other" always maps to Scenario.

Query: {query}
Primary Task: {task}

Return the list of agents and brief reasoning."""


CLASSIFY_AND_PLAN_PROMPT = """You are an expert AI assistant specialized in Indian legal domain analysis.

Perform TWO tasks in one step:

## Task 1: Classify the query
Identify the PRIMARY legal task type. Choose EXACTLY ONE:

- **Newacts** → ONLY for these 6 acts: BNS/IPC, BNSS/CrPC, BSA/IEA (and their old/new equivalents). NOT for any other acts.
  - This includes ALL variants and full names: "IPC" / "Indian Penal Code" / "Penal Code"; "CrPC" / "Cr.P.C" / "Code of Criminal Procedure" / "Criminal Procedure Code"; "IEA" / "Indian Evidence Act" / "Evidence Act"; "BNS" / "Bharatiya Nyaya Sanhita"; "BNSS" / "Bharatiya Nagarik Suraksha Sanhita"; "BSA" / "Bharatiya Sakshya Adhiniyam".
  - Examples: "Section 125 of CrPC" → [Newacts]; "Section 125 of Code of Criminal Procedure 1973" → [Newacts]; "Section 302 IPC" → [Newacts]; "Section 65B Indian Evidence Act" → [Newacts]; "Section 438 BNSS" → [Newacts]. NEVER pair these queries with Legislation -- Newacts already covers both the old and new statute text.
- **Legislation** → ALL other central/state acts and statutes NOT listed under Newacts.
  - Examples: "Section 138 NI Act" → [Legislation]; "Section 7 Hindu Marriage Act" → [Legislation]; "Section 482 Companies Act" → [Legislation].
- **Drafting** → User explicitly asks the AI to **CREATE a standalone legal document** that could be filed in court or signed by parties: plaints, petitions, written statements, bail applications, affidavits, legal notices, agreements, contracts, deeds, MOUs, wills, divorce petitions, etc. Trigger only on explicit production verbs: "draft", "write", "prepare", "create", "generate", "compose", "draw up", "redraft", "give me a [document]", "I need a [document]" — **paired with a court-filing-ready document noun**. DO NOT trigger on questions ABOUT documents (format, structure, essential elements, ingredients, requirements, how to file, when to use, difference between X and Y). DO NOT trigger on tactical / strategic outputs like cross-examination questions, arguments, defences, strategies, analyses, opinions, briefs of advice — those go to **Scenario** (situational legal analysis), even when the user uses the words "draft" or "prepare".
  - DO Drafting: "Draft a plaint for partition", "Prepare a bail application", "Give me a sample MOU", "Write a legal notice for property dispute", "Generate a divorce petition".
  - DO NOT route to Drafting (route elsewhere):
    - "Essential elements of a partnership agreement" → Legal_Concepts (theory)
    - "What is the format of a bail application?" → Legal_Concepts (structural explanation)
    - "How to file a writ petition under Article 32?" → Scenario (procedure)
    - "Discuss petition under Article 32" → Constitution (concept)
    - "Notice under Section 138 NI Act — requirements" → Legislation (statutory rule)
    - "Section 80 CPC notice requirements" → Legislation
    - "Plaint requirements under Order VII CPC" → Legislation
    - "Difference between agreement and contract" → Legal_Concepts
    - "What is a written statement?" → Legal_Concepts
    - "Prepare a cross-examination strategy for an NDPS case" → Scenario (tactical output)
    - "Draft arguments for the accused / for the prosecution" → Scenario (advocacy strategy)
    - "Give me cross-examination questions for the IO" → Scenario (litigation prep)
    - "Prepare a brief on bail under Section 37 NDPS" → Scenario (legal analysis)
    - "What defences are available against Section 498A IPC" → Scenario (situational advice)
- **Constitution** → Constitutional provisions, fundamental rights/duties, Articles of Constitution.
- **Scenario** → Situational legal query, real-life legal situation analysis, legal advice.
- **Judgment** → Case law, court decisions, precedents (general / High Court / unspecified courts).
- **SCI_Judgment** → Supreme Court of India cases. Use when user explicitly mentions "Supreme Court" or "SC", or names a landmark SC case.
- **GST_Judgment** → GST Appellate Authority for Advance Ruling (AAAR) orders. Use when the query is about: GST/CGST/SGST/IGST advance rulings, AAR or AAAR orders, GST classification appeals, GST input tax credit (ITC) disputes, GST valuation rulings, HSN classification under GST, or state-level GST appellate decisions.
- **Maxim** → Legal maxims, Latin phrases, legal doctrines (res judicata, estoppel, etc.).
- **Legal_Concepts** → General legal explanations that don't fit above categories.
- **Document** → Questions about uploaded files/documents.
- **Non_legal** → Non-legal queries: greetings, casual chat, non-legal topics, bot identity questions.
- **Other** → Legal-adjacent queries that don't fit other categories.

## Task 2: Plan which agents to invoke
Available agents: Legislation, Judgment, Newacts, Drafting, Scenario, Constitution, Maxim, Legal_Concepts, SCI_Judgment, GST_Judgment, Document

Rules:
1. Most queries need only the PRIMARY agent matching the task type.
2. Use MULTIPLE agents when the query explicitly asks for different types of information:
   - "Draft bail application with relevant case laws" → [Drafting, Judgment]
   - "Section 438 BNSS with SC precedents" → [Newacts, SCI_Judgment]
   - "Explain Article 21 and related case laws" → [Constitution, Judgment]
3. COMPLEX SCENARIO QUERIES: When a query describes a factual situation AND asks for arguments, defences, provisions, or citations, use MULTIPLE agents:
   - Scenario + Judgment + Legislation/Newacts as appropriate
4. When a query mentions BOTH constitutional concept AND legal maxim → [Constitution, Maxim]
5. When a query references a named SC landmark case alongside a constitutional topic → include SCI_Judgment.
6. For drafting requests: ONLY include Drafting when the user explicitly asks the AI to CREATE / WRITE / PREPARE / DRAFT a legal document. Questions ABOUT documents (format, structure, essential elements, requirements, how to file, difference between X and Y) are NOT drafting — route to Legal_Concepts / Legislation / Scenario.
7. Never use more than 3 agents.
8. "Other" task always maps to Scenario agent.
9. For Non_legal: agents should be ["Non_legal"].

User Query: {query}
Chat Summary (Optional): {chat_summary}

Return the task type, list of agents, and brief reasoning."""


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
        agents = result.agents[:3]  # cap at 3
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
    extra: list[str] = []
    if intent.include_case_law and task not in ("Judgment", "SCI_Judgment"):
        # SCI takes priority when the user names the apex court.
        extra.append("SCI_Judgment" if intent.wants_supreme_court else "Judgment")
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
                    error=str(e).splitlines()[0][:200], exc_info=True)
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
    """
    if len(agents) <= 1:
        return {}

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

        # Parse JSON from response
        text = response.content.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        parsed = _json.loads(text)
        # Validate: only keep queries for planned agents
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
    refined = response.content
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
    user_context = ""  # long-form pasted content, empty for normal queries
    log.info("Plan phase started", query=query[:100],
             has_summary=bool(summary), query_len=len(query))

    progress("orchestrator", "Understanding your question...", step="classify")

    # --- Long query extraction: separate question from pasted content ---
    if len(query) > _LONG_QUERY_THRESHOLD:
        log.info("Long query detected, extracting question",
                 query_len=len(query), threshold=_LONG_QUERY_THRESHOLD)
        progress("orchestrator", "Analyzing your document...", step="extract")
        try:
            extracted_question, doc_type = await asyncio.wait_for(
                asyncio.to_thread(_extract_question_from_long_query, query),
                timeout=15,
            )
            user_context = query  # full text preserved for generating agent
            query = extracted_question  # route/classify on concise question
            log.info("Long query split",
                     question=extracted_question[:100],
                     doc_type=doc_type,
                     context_len=len(user_context))
        except asyncio.TimeoutError:
            log.warning("Long query extraction timed out, using first 500 chars for routing")
            user_context = query
            query = query[:500]
        except Exception as extract_err:
            log.warning("Long query extraction failed", error=str(extract_err))
            user_context = query
            query = query[:500]

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

    _SCI_STRONG_KEYWORDS = (
        "supreme court judgment", "supreme court case", "supreme court ruling",
        "supreme court ruled", "supreme court held", "supreme court order",
        "sc judgment", "sc case", "sc ruling", "hon'ble sc",
        "article 136", "supreme court of india", "apex court judgment",
        "apex court ruling", "supreme court bench",
    )
    _orig_lower = _original_query.lower()
    is_sci = any(k in _orig_lower for k in _SCI_STRONG_KEYWORDS)

    fc = FileContextData.from_state(state)

    # Intent extractor result (Phase 1, telemetry-only). Stays None on the
    # short-circuit paths (greeting / SCI / skip-normalize) and on extractor
    # failures. Populated only inside the parallel-gather path below when
    # INTENT_EXTRACTOR_V2 is on.
    extracted_intent: UserIntent | None = None

    # --- Short-circuit: greeting or SCI pre-check resolved ---
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

    elif is_sci:
        log.info("SCI pre-check triggered, skipping LLM classification",
                 query=query[:80])
        task = "SCI_Judgment"
        tasks_planned = ["SCI_Judgment"]
        response_instructions = ""

    else:
        # --- Change 3: Skip normalization for English queries ---
        _FORMAT_KEYWORDS = (
            "table format", "bullet", "in hindi", "in marathi", "in tamil",
            "in telugu", "in bengali", "in kannada", "in malayalam",
            "in gujarati", "in punjabi", "in urdu", "in odia",
            "hindi mein", "hindi me", "batao", "samjhao", "kaise",
        )
        skip_normalize = (
            user_language == "en"
            and len(query) < 500
            and not any(k in _orig_lower for k in _FORMAT_KEYWORDS)
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
            # step (memory rewrite, long-query extraction, abbreviation
            # expansion) already changed `query`. The rewriter's expanded
            # form is a better retrieval target than re-normalizing the
            # raw 2-word follow-up that the extractor just received.
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

    # Drafting false-positive strip (post multi-intent): if the primary task
    # is not Drafting but Drafting ended up in tasks_planned, drop it unless
    # the user explicitly requested a draft (task_intent="draft") with a
    # file context (file-attached drafting flow). The cite-appendix-OFF
    # logic below otherwise strips ALL non-drafting agents the moment
    # Drafting is in the plan, which silently hijacks primary execution.
    if (
        task != "Drafting"
        and "Drafting" in tasks_planned
        and not (fc and fc.has_content and _wants_drafting(extracted_intent))
    ):
        tasks_planned = [a for a in tasks_planned if a != "Drafting"]
        if not tasks_planned:
            tasks_planned = [task]
        log.info("Drafting stripped (post multi-intent) — primary task is non-drafting",
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
        # Tax / quasi-judicial appellate written submissions (CIT(A) /
        # ITAT / GST appellate / NCLT / SAT / DRT) inherently require
        # detailed case-law citations groundwise. The frontend may not
        # know to send cite_appendix=true for these drafts, so detect
        # them server-side via keyword triggers and force fan-out to
        # Judgment / SCI_Judgment / Legislation. Sagar bug feedback
        # 2026-06-18 — user explicitly wanted "Add valid and correct
        # relevant case laws/citations with Case Number and Case Year"
        # plus "All the Citations mentioned in the assessment order by
        # AO, need to be explained, how the same is not applicable to
        # the assessee with detailed explanation".
        _tax_appellate_detected = _is_tax_appellate_query(query)
        if not cite_appendix_on and _tax_appellate_detected:
            cite_appendix_on = True
            log.info("cite_appendix force-enabled — tax appellate query detected",
                     trigger="tax_appellate_keyword")
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
        tasks_planned = tasks_planned[:3]
        _tax_appellate_detected = False

    # Intent-driven plan validation (Newacts/Legislation veto + SCI/GST enrich)
    tasks_planned = _validate_and_enrich_plan(
        tasks_planned, extracted_intent, log,
    )

    # Tax appellate guardrail: when this is a CIT(A)/ITAT/etc. written
    # submission, the orchestrator's intent-driven enrichment often adds
    # Scenario / Legal_Concepts based on phrases like "explain how AO's
    # case laws are not applicable" — but those agents DON'T produce
    # case-law citations, they produce their OWN full draft of the
    # submission, which then gets appended to the user's Drafting output
    # as a confusing duplicate cause-title block. Restrict the fan-out
    # to the corpus agents (Judgment / SCI_Judgment / Legislation /
    # Newacts) plus Drafting itself — those are the agents that supply
    # citations / statute text without re-drafting.
    if _tax_appellate_detected and has_drafting:
        _TAX_APPELLATE_ALLOWED = {
            "Drafting", "Document",
            "Judgment", "SCI_Judgment",
            "Legislation", "Newacts",
        }
        _before = list(tasks_planned)
        tasks_planned = [t for t in tasks_planned if t in _TAX_APPELLATE_ALLOWED]
        dropped = [t for t in _before if t not in _TAX_APPELLATE_ALLOWED]
        if dropped:
            log.info("Tax appellate guardrail: dropped non-citation agents",
                     dropped=dropped, kept=tasks_planned)

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
    if "Drafting" in tasks_planned and fc and fc.has_content and fc.inline_text:
        log.info("Drafting will receive uploaded document via file_context",
                 file_text_len=len(fc.inline_text),
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
    if user_context:
        result["user_context"] = user_context
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

    # Inject file context into query for synthesis
    fc = FileContextData.from_state(state)
    if fc and fc.inline_text:
        query = f"{query}\n\n--- Uploaded File Content ---\n{fc.inline_text[:50000]}"
        log.info("File context injected into synthesis",
                 inline_chars=len(fc.inline_text))

    log.info("Synthesize phase started",
             agents_received=list(agent_results.keys()),
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
    has_unprocessed_file = (fc is not None and fc.inline_text
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
            }

        log.info("Single agent pass-through",
                 agent=name, content_len=len(result.content),
                 tokens=result.tokens_consumed)
        return_dict = {
            "final_response": result.content,
            "source_metadata": _serialize_sources(result),
            "tokens_consumed": result.tokens_consumed,
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
        # consumed the file via case_facts + raw text). Including it in the
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
        appendix_parts: list[str] = []
        skipped_redundant: list[str] = []

        # Unique-token dedup: keep a supporting agent's appendix iff it
        # contributes a meaningful number of UNIQUE informational tokens
        # beyond the primary. Replaces the prior overlap-ratio threshold
        # which dropped genuinely complementary content (Maxim explaining
        # audi alteram partem alongside Constitution Article 14 share
        # 50%+ tokens through common legal vocabulary — "natural", "justice",
        # "principles", "court" — even when Maxim adds substantive new
        # material). Measuring NEW tokens directly catches the actual
        # signal we care about: does the supporting agent add information?
        #
        # Thresholds:
        #   - Supporting must add >= MIN_UNIQUE new informational tokens.
        #     30 is conservative — a 200-word response paraphrasing the
        #     primary typically has 10-15 unique informational tokens
        #     after stop-word filtering.
        #   - Skip the gate entirely for very short supporting responses
        #     (< 50 tokens total): nothing to dedup, just keep.
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
                # Still merge the sources — the supporting agent's
                # citations are valuable even when its prose is redundant.
                all_serialized_sources.extend(_serialize_sources(result))
                total_tokens += result.tokens_consumed
                continue
            heading = _SUPPORTING_HEADINGS.get(name, f"## {name} Notes")
            appendix_parts.append(f"\n\n---\n\n{heading}\n\n{result.content.strip()}")
            total_tokens += result.tokens_consumed
            all_serialized_sources.extend(_serialize_sources(result))

        if skipped_redundant:
            log.info("Dropped redundant supporting agents from synthesis",
                     skipped=skipped_redundant,
                     reason=f"unique tokens < {MIN_UNIQUE_TOKENS_TO_KEEP}")

        final_response = primary + "".join(appendix_parts)

        log.info("Primary-task-aware synthesis completed (append-only)",
                 task=primary_task_state, primary_agent=primary_agent_name,
                 final_len=len(final_response),
                 supporting_agents=list(supporting_results.keys()),
                 total_tokens=total_tokens)

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
            response = await stream_chain_response(chain, {
                "query": query,
                "agent_results": agent_results_text,
                "response_instructions": response_instructions or "Standard legal response with proper citations and markdown formatting.",
            }, timeout=180)  # Multi-agent synthesis needs more time

        synthesized = response.content
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
             draft_len=len(draft), enriched_len=len(response.content),
             tokens=tokens)
    return response.content, tokens


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
             draft_len=len(draft), enriched_len=len(response.content),
             tokens=tokens)
    return response.content, tokens
