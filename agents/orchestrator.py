"""Agent #1 — Orchestrator Agent

The brain of the system. Two phases:
1. PLAN: Analyze query → classify task → decide which agents to invoke
2. SYNTHESIZE: Merge results from domain agents into final response

Uses: GPT-4o for task classification, planning, and synthesis.
"""

from __future__ import annotations

import asyncio
from pydantic import BaseModel, Field
from typing import Literal
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from core.state import LegalAgentState, AgentResult, FileContextData
from core.clients import get_gpt4o, get_gemini_flash, get_gemini_pro, get_drafting_llm
from core.logger import get_logger, log_time
from config.prompts import (
    TASK_CLASSIFICATION_PROMPT, SYNTHESIS_PROMPT,
    DRAFT_SYNTHESIS_PROMPT, DRAFT_CITATION_PROMPT,
)

log = get_logger("Orchestrator")


# --- Task Classification (migrated from v1 task_identifer.py) ---

class IdentifyTaskSchema(BaseModel):
    task: Literal[
        "Drafting", "Judgment", "Legislation", "Constitution",
        "Scenario", "Maxim", "Newacts", "Legal_Concepts",
        "SCI_Judgment", "Document",
        "Non_legal", "Other",
    ] = Field(..., description="The primary legal task type")


def _classify_task_regex_fallback(query: str) -> str:
    """Keyword-based task classification fallback when LLM is unavailable."""
    q = query.lower()
    if any(k in q for k in ("draft", "agreement", "notice", "plaint", "petition", "template", "format")):
        return "Drafting"
    if any(k in q for k in ("bns", "bnss", "bsa", "ipc", "crpc", "iea",
                              "bharatiya nyaya", "bharatiya nagarik", "bharatiya sakshya",
                              "penal code", "criminal procedure", "evidence act")):
        return "Newacts"
    if any(k in q for k in ("judgment", "judgement", "case law", "citation", "held that",
                              "vs.", " v. ", "high court", "hc", "bench")):
        return "Judgment"
    if any(k in q for k in ("supreme court", "sc judgment", "puttaswamy", "maneka gandhi",
                              "kesavananda", "navtej", "vishaka")):
        return "SCI_Judgment"
    if any(k in q for k in ("article ", "fundamental right", "directive principle",
                              "constitution", "constitutional")):
        return "Constitution"
    if any(k in q for k in ("maxim", "audi alteram", "res judicata", "estoppel",
                              "nemo judex", "caveat emptor", "actus reus", "mens rea")):
        return "Maxim"
    if any(k in q for k in ("scenario", "situation", "what happens if", "can i",
                              "what should", "legal opinion", "advise", "rights")):
        return "Scenario"
    if any(k in q for k in ("section ", "act ", "rule ", "regulation", "provision",
                              "statute", "law ", " act,", " act.")):
        # Avoid misclassifying constitutional queries that mention "section"
        if not any(k in q for k in ("constitution", "constitutional", "article ",
                                     "fundamental right", "directive principle")):
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
            llm = get_gpt4o().with_structured_output(IdentifyTaskSchema)
            formatted = prompt.format(query=query, chat_summary=chat_summary or "")
            result = llm.invoke(formatted)
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
        description="List of agent names to invoke: Legislation, Judgment, Newacts, Drafting, Scenario, Constitution, Maxim, Legal_Concepts, SCI_Judgment, Document",
    )
    reasoning: str = Field(..., description="Brief reasoning for agent selection")


PLAN_PROMPT = """You are a legal query planner. Given a query and its primary task type, determine which agents should handle it.

Available agents:
- Legislation: Central/state law sections and provisions (all acts EXCEPT the 6 below)
- Judgment: Court case laws, citations, precedents (general / High Court / unspecified courts)
- Newacts: ONLY these 6 acts: BNS/IPC, BNSS/CrPC, BSA/IEA
- Drafting: Legal document templates and drafting
- Scenario: Situational analysis, legal advice, remedies, web search
- Constitution: Constitutional provisions, fundamental rights, Articles
- Maxim: Legal maxims and doctrines (Latin phrases like res judicata, audi alteram partem, estoppel)
- Legal_Concepts: General legal explanations (use only when no specific category applies)
- SCI_Judgment: Supreme Court of India case search
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
6. For drafting requests: ONLY include Drafting when the user explicitly asks to draft/write/prepare a legal document. "What legal options" or "how can I" is NOT a drafting request.
7. Never use more than 3 agents.
8. "Other" always maps to Scenario.

Query: {query}
Primary Task: {task}

Return the list of agents and brief reasoning."""


def _select_citation_agents(query: str) -> list[str]:
    """Select which agents should provide citations for a draft.

    Always returns at least [Judgment, Legislation/Newacts].
    """
    agents = ["Judgment"]  # always include general case laws
    query_lower = query.lower()

    # Check for specific acts in Newacts scope (BNS/BNSS/BSA/IPC/CrPC/IEA)
    newacts_keywords = [
        "bns", "bnss", "bsa", "ipc", "crpc", "iea",
        "penal code", "criminal procedure", "evidence act",
        "bharatiya nyaya", "bharatiya nagarik", "bharatiya sakshya",
    ]
    if any(kw in query_lower for kw in newacts_keywords):
        agents.append("Newacts")
    else:
        agents.append("Legislation")

    # Check for Supreme Court references
    sci_keywords = [
        "supreme court", " sc ", "puttaswamy", "maneka gandhi",
        "kesavananda", "vishaka", "navtej", "mohd. ahmed khan",
    ]
    if any(kw in query_lower for kw in sci_keywords):
        agents.append("SCI_Judgment")

    return agents


def _detect_multi_intent(query: str, task: str) -> list[str]:
    """Keyword-based multi-intent detection as safety net.

    Ensures complex queries that mention citations, arguments, provisions etc.
    get routed to multiple agents even if the LLM planner returns only one.
    Returns additional agents to add (may be empty).
    """
    q = query.lower()
    extra = []

    # Detect requests for case laws / citations / judgments
    wants_cases = any(k in q for k in (
        "case law", "case laws", "citation", "citations", "judgment", "judgement",
        "precedent", "precedents", "court decision", "landmark case",
        "relevant case", "supporting case", "judicial",
    ))

    # Detect requests for statutory provisions / sections / acts
    wants_statutes = any(k in q for k in (
        "section", "provision", "provisions", "statutory", "statute",
        "act ", " act,", " act.", "legal provision", "under which law",
        "applicable law", "relevant law", "penal", "ipc", "bns", "crpc", "bnss",
    ))

    # Detect requests for arguments / defences / remedies (scenario analysis)
    wants_analysis = any(k in q for k in (
        "argument", "arguments", "defence", "defense", "remedy", "remedies",
        "legal option", "legal options", "on behalf of", "what can",
        "how to fight", "how to defend", "legal recourse", "legal action",
        "advice", "advise",
    ))

    # Detect drafting intent
    wants_draft = any(k in q for k in (
        "draft", "prepare", "write a", "template", "format of",
        "application for", "petition for", "notice for",
    ))

    # Add missing agents based on detected intents
    if wants_cases and task not in ("Judgment", "SCI_Judgment"):
        extra.append("Judgment")
    if wants_statutes and task not in ("Legislation", "Newacts"):
        # Decide between Legislation and Newacts
        newacts_acts = ("bns", "bnss", "bsa", "ipc", "crpc", "iea",
                        "penal code", "criminal procedure", "evidence act")
        if any(a in q for a in newacts_acts):
            extra.append("Newacts")
        else:
            extra.append("Legislation")
    if wants_analysis and task not in ("Scenario",):
        extra.append("Scenario")
    if wants_draft and task not in ("Drafting",):
        extra.append("Drafting")

    return extra


def _plan_agents(query: str, task: str) -> list[str]:
    """Determine which domain agents to invoke for this query.

    For simple queries: returns just the primary task agent.
    For complex queries: returns multiple agents to run in parallel.
    For Drafting: always adds citation agents (Judgment + Legislation/Newacts).
    """
    # Short-circuit for simple task types
    if task == "Non_legal":
        log.debug("Simple task mapping used", task=task, agents=[])
        return []

    # "Other" always maps to Scenario (as per PLAN_PROMPT contract)
    if task == "Other":
        log.debug("Mapping 'Other' task to Scenario")
        task = "Scenario"

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
        agents = agents if agents else [task]
    except Exception as e:
        log.error("Planning failed, falling back to single agent",
                  error=str(e), fallback=task)
        agents = [task]

    # Safety net: keyword-based multi-intent detection
    extra = _detect_multi_intent(query, task)
    for agent in extra:
        if agent not in agents:
            agents.append(agent)
    if extra:
        log.info("Multi-intent detection added agents",
                 extra=extra, all_agents=agents)

    # For Drafting: ALWAYS add citation agents for court-filing quality
    has_drafting = task == "Drafting" or "Drafting" in agents
    if has_drafting:
        if "Drafting" not in agents:
            agents.insert(0, "Drafting")
        citation_agents = _select_citation_agents(query)
        for ca in citation_agents:
            if ca not in agents:
                agents.append(ca)
        agents = agents[:4]  # allow up to 4 agents for drafting
        log.info("Drafting citation agents added",
                 agents=agents, citation_agents=citation_agents)
    else:
        agents = agents[:3]  # cap at 3 agents for non-drafting

    return agents


# --- Per-Agent Query Rewriting ---

import json as _json

AGENT_QUERY_REWRITE_PROMPT = """You are a legal query optimizer. Rewrite the user's query into specialized search queries for each assigned agent.

Each agent has a different database and purpose:
- Judgment: Searches court case law database. Query should focus on: legal topic keywords, cause of action, type of case (e.g. "medical negligence", "consumer complaint", "property dispute"). NEVER include user-provided party names (they are fictional and won't match any real cases). Use generic terms like "doctor negligence hospital compensation" instead.
- Legislation: Searches Indian act/statute database by full-text match. IMPORTANT: Focus on the SINGLE most relevant act and its specific sections. Do NOT list multiple acts — the search engine will match the first act name it finds. Example: "Consumer Protection Act 2019 Section 2 definition of consumer deficiency in service medical negligence" (not "Indian Contract Act; IPC; Consumer Protection Act").
- Newacts: Searches BNS/IPC, BNSS/CrPC, BSA/IEA database. Query should mention the specific criminal code sections or topics.
- Drafting: Creates legal documents. Query should specify: document type, parties, key facts, relief sought.
- Scenario: Performs web-grounded legal analysis. Query should include: full factual situation, what analysis is needed (arguments, remedies, forum).
- Constitution: Searches constitutional provisions database. Query should specify: Article numbers, fundamental rights, constitutional principles.
- Maxim: Searches legal maxims database. Query should specify: maxim name, doctrine, Latin phrase.
- Legal_Concepts: General legal explanation. Query should be the legal concept to explain.
- SCI_Judgment: Searches Supreme Court database. Query should focus on: SC-specific case names, constitutional questions, landmark rulings.

Rules:
1. Each rewritten query must be self-contained and optimized for that agent's search database.
2. Keep queries concise (under 100 words each). Scenario can be longer.
3. Extract and include specific legal terms: act names, section numbers, doctrines, party types.
4. For Legislation: pick the SINGLE most relevant act for the user's primary legal issue. Do NOT list multiple acts.
5. For Judgment: NEVER include fictional party names from the user's scenario. Use only legal topics and case type keywords.
6. Return valid JSON object mapping agent name to rewritten query.

User Query: {query}
Agents: {agents}

Return JSON object like: {{"Judgment": "...", "Legislation": "..."}}"""


class AgentQueries(BaseModel):
    queries: dict[str, str] = Field(
        ..., description="Map of agent name to its optimized query"
    )


# --- User Expectation Extraction + Query Normalization ---

QUERY_NORMALIZE_PROMPT = """You are a legal query analyzer. Analyze the user's query and extract two things:

1. **Normalized Query** (in English): Rewrite the user's query into clear, professional English. If the query is in Hindi, Hinglish, or any other language, translate it to English. Preserve all legal details (names, dates, sections, acts). Add explicit mention of what the user is asking for.

2. **Response Instructions**: Extract what FORMAT and TYPE of response the user expects. Look for:
   - Output type: draft/document, explanation, advice/opinion, comparison table, list, summary, step-by-step guide
   - Specific format requests: table format, bullet points, numbered list, formal legal language
   - Language preference: if the user wrote in Hindi/Hinglish, note "User prefers Hindi/bilingual response"
   - Specific expectations: "on behalf of plaintiff", "with case laws", "with sections", "arguments and counter-arguments"
   - Relief/remedy focus: compensation, bail, injunction, etc.

If the user has no special format preference, return "Standard legal response with proper citations and markdown formatting."

User Query: {query}

Return JSON:
{{"normalized_query": "...", "response_instructions": "..."}}"""


class QueryAnalysis(BaseModel):
    normalized_query: str = Field(..., description="Query rewritten in clear English with expectations embedded")
    response_instructions: str = Field(..., description="What format/type of response the user expects")


def _analyze_and_normalize_query(query: str) -> tuple[str, str]:
    """Analyze user query: translate to English + extract response expectations.

    Returns (normalized_query, response_instructions).
    """
    try:
        with log_time(log, "Query analysis & normalization"):
            llm = get_gemini_flash(temperature=0.1).with_structured_output(QueryAnalysis)
            prompt = ChatPromptTemplate.from_template(QUERY_NORMALIZE_PROMPT)
            chain = prompt | llm
            result = chain.invoke({"query": query})

        log.info("Query normalized",
                 original_len=len(query),
                 normalized_len=len(result.normalized_query),
                 instructions_len=len(result.response_instructions))
        return result.normalized_query, result.response_instructions

    except Exception as e:
        log.warning("Query normalization failed, using original",
                    error=str(e))
        return query, ""


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
    summary = state.get("summary_text", "")
    log.info("Plan phase started", query=query[:100],
             has_summary=bool(summary))

    # Step 0: Normalize query + extract user expectations
    response_instructions = ""
    try:
        normalized_query, response_instructions = await asyncio.wait_for(
            asyncio.to_thread(_analyze_and_normalize_query, query),
            timeout=10,
        )
        if normalized_query and normalized_query != query:
            log.info("Query normalized",
                     original=query[:80], normalized=normalized_query[:80])
            query = normalized_query
            # Note: state["original_query"] is preserved unmodified for agents
            # that need verbatim ES search terms (section numbers, case citations).
    except asyncio.TimeoutError:
        log.warning("Query normalization timed out, using original", exc_info=True)

    # Step 1: Classify task (LLM with regex fallback on timeout)
    # If files are attached, hint the classifier about them
    fc = FileContextData.from_state(state)
    classify_query = query
    if fc and fc.has_content:
        file_hint = f" [User has uploaded files: {', '.join(fc.file_names)}. This query is about the uploaded document(s).]"
        classify_query = query + file_hint
        log.info("File context hint added for classification", file_names=fc.file_names)

    try:
        task = await asyncio.wait_for(
            asyncio.to_thread(
                _classify_task, classify_query, chat_summary=summary if summary else None
            ),
            timeout=30,
        )
    except asyncio.TimeoutError:
        task = _classify_task_regex_fallback(classify_query)
        log.warning("Task classification timed out, using regex fallback",
                    task=task, query=query[:80])

    # Note: if files are attached and task isn't Document, we'll add Document
    # to the plan alongside the classified task (see Step 3 below) instead of
    # overriding, to support multi-intent queries (e.g., "find judgments related
    # to this uploaded contract").

    # Step 2: Handle non-legal (but allow if files are attached — user may just
    # be asking about the document content)
    if task == "Non_legal" and not (fc and fc.has_content):
        log.warning("Non-legal query blocked", query=query[:80])
        return {
            "task": task,
            "tasks_planned": [],
            "is_blocked": True,
            "tokens_consumed": 0,
            "block_reason": (
                "👋 Hello! I'm **Lawttorney**, your AI-powered Indian legal assistant.\n\n"
                "I'm here to help you with all your legal queries. Here's what I can do for you:\n\n"
                "1. ⚖️ **Court Judgments** — Search Supreme Court and High Court case laws by party name, citation, or legal issue\n"
                "2. 📜 **Legislation & Acts** — Explain provisions of any Indian Act, section by section (Income Tax, Companies Act, GST, RERA, and more)\n"
                "3. 📋 **New Criminal Codes** — Lookup BNS (IPC), BNSS (CrPC), and BSA (IEA) — old and new law equivalents\n"
                "4. 🏛️ **Constitution & Maxims** — Explain Fundamental Rights, Directive Principles, Articles, and legal maxims like *audi alteram partem*\n"
                "5. 📝 **Legal Drafting** — Generate professional legal documents — agreements, notices, plaints, petitions, and more\n"
                "6. 🔍 **Legal Scenario Analysis** — Analyse your situation, identify applicable laws, and suggest remedies\n\n"
                "Please ask me a legal question and I'll get right on it!"
            ),
        }
    # If Non_legal but has files, treat as Document task
    if task == "Non_legal" and fc and fc.has_content:
        log.info("Non-legal overridden to Document due to file context")
        task = "Document"

    # Step 3: Plan agents
    # For Document task with file context, skip LLM planner — go directly to Document agent
    if task == "Document" and fc and fc.has_content:
        tasks_planned = ["Document"]
        log.info("Document task with file context — using Document agent directly",
                 file_names=fc.file_names)
    else:
        try:
            tasks_planned = await asyncio.wait_for(
                asyncio.to_thread(_plan_agents, query, task),
                timeout=25,
            )
        except asyncio.TimeoutError:
            log.warning("Agent planning timed out, falling back to single agent", task=task)
            tasks_planned = [task] if task not in ("Non_legal",) else []

        # Step 3b: File context — ensure Document agent is planned if files attached
        if fc and fc.has_content and "Document" not in tasks_planned:
            tasks_planned.append("Document")
            log.info("Document agent added for file context (multi-intent support)",
                     file_names=fc.file_names)

    # Step 4: Per-agent query rewriting (only for multi-agent plans)
    agent_queries = {}
    if len(tasks_planned) > 1:
        try:
            agent_queries = await asyncio.wait_for(
                asyncio.to_thread(_rewrite_queries_for_agents, query, tasks_planned),
                timeout=15,
            )
        except asyncio.TimeoutError:
            log.warning("Per-agent query rewriting timed out")

    log.info("Plan phase completed",
             task=task, agents_planned=tasks_planned,
             agent_count=len(tasks_planned),
             agent_queries_generated=len(agent_queries),
             has_response_instructions=bool(response_instructions))

    return {
        "query": query,  # normalized English query replaces original
        "task": task,
        "tasks_planned": tasks_planned,
        "agent_queries": agent_queries,
        "response_instructions": response_instructions,
    }


async def orchestrator_synthesize_node(state: LegalAgentState) -> dict:
    """Phase 2 — Merge results from all domain agents into final response.

    Single agent → pass through directly.
    Multiple agents → LLM synthesis into coherent, unified response.
    """
    agent_results: dict[str, AgentResult] = state.get("agent_results", {})
    query = state.get("query", state["original_query"])
    response_instructions = state.get("response_instructions", "")

    # Inject file context into query for synthesis
    fc = FileContextData.from_state(state)
    if fc and fc.inline_text:
        query = f"{query}\n\n--- Uploaded File Content ---\n{fc.inline_text[:50000]}"
        log.info("File context injected into synthesis",
                 inline_chars=len(fc.inline_text))

    log.info("Synthesize phase started",
             agents_received=list(agent_results.keys()),
             has_response_instructions=bool(response_instructions))

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
    has_unprocessed_file = (fc is not None and fc.inline_text
                           and "Document" not in valid_results)
    if len(valid_results) == 1 and not has_unprocessed_file:
        name, result = next(iter(valid_results.items()))

        # Drafting solo: auto-enrich with AI-generated citations
        if name == "Drafting" and len(result.content) > 500:
            log.info("Drafting solo — auto-citation enrichment starting",
                     draft_len=len(result.content))
            try:
                enriched, cite_tokens = await _auto_cite_draft(
                    query, result.content, response_instructions
                )
                log.info("Auto-citation enrichment completed",
                         original_len=len(result.content),
                         enriched_len=len(enriched))
                return_dict = {
                    "final_response": enriched,
                    "source_metadata": _serialize_sources(result),
                    "tokens_consumed": result.tokens_consumed + cite_tokens,
                }
                return return_dict
            except Exception as e:
                log.warning("Auto-citation failed, passing draft through",
                            error=str(e))

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
    # When Drafting is one of the agents, preserve the full draft
    # and inject citations from the other agents (Judgment, Legislation, Newacts)
    if "Drafting" in valid_results:
        drafting_result = valid_results.pop("Drafting")
        citation_results = valid_results  # remaining: Judgment, Legislation, etc.

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

        # For large drafts (>40K chars), LLM citation injection truncates the draft.
        # Instead, append citations as a separate section at the end.
        LARGE_DRAFT_THRESHOLD = 40_000

        try:
            if len(drafting_result.content) > LARGE_DRAFT_THRESHOLD:
                log.info("Draft too large for LLM injection, appending citations",
                         draft_len=len(drafting_result.content),
                         threshold=LARGE_DRAFT_THRESHOLD)
                enriched = drafting_result.content
                if citations_text.strip():
                    enriched += "\n\n---\n\n## REFERENCES & CITATIONS\n" + citations_text
            elif citations_text.strip():
                enriched, cite_tokens = await _inject_citations_into_draft(
                    query, drafting_result.content, citations_text
                )
                total_tokens += cite_tokens
            else:
                enriched, cite_tokens = await _auto_cite_draft(query, drafting_result.content)
                total_tokens += cite_tokens

            log.info("Draft synthesis completed",
                     enriched_len=len(enriched), total_tokens=total_tokens)

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

    # --- Generic Multi-Agent Synthesis (non-drafting) ---
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

            from core.streaming import stream_chain_response
            response = await stream_chain_response(chain, {
                "query": query,
                "agent_results": agent_results_text,
                "response_instructions": response_instructions or "Standard legal response with proper citations and markdown formatting.",
            }, timeout=180)  # Multi-agent synthesis needs more time

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
    query: str, draft: str, citations_text: str
) -> tuple[str, int]:
    """Preserve full draft and inject real citations from ALL agents.

    Uses Gemini 2.5 Flash for fast citation injection.
    Returns: (enriched_content, tokens_consumed)
    """
    with log_time(log, "Draft citation injection"):
        llm = get_drafting_llm()
        prompt = ChatPromptTemplate.from_template(DRAFT_SYNTHESIS_PROMPT)
        chain = prompt | llm

        from core.streaming import stream_chain_response
        response = await stream_chain_response(chain, {
            "query": query,
            "draft": draft,
            "citations": citations_text,
        }, timeout=180)  # Citation injection on full draft needs more time

    tokens = 0
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        tokens = response.usage_metadata.get("total_tokens", 0)
    log.info("Citation injection completed",
             draft_len=len(draft), enriched_len=len(response.content),
             tokens=tokens)
    return response.content, tokens


async def _auto_cite_draft(
    query: str, draft: str, response_instructions: str = ""
) -> tuple[str, int]:
    """Add AI-generated citations when no database agents provided results.

    Uses Gemini 2.5 Flash for fast auto-citation.
    Returns: (enriched_content, tokens_consumed)
    """
    with log_time(log, "Draft auto-citation"):
        llm = get_drafting_llm()
        prompt = ChatPromptTemplate.from_template(DRAFT_CITATION_PROMPT)
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

    tokens = 0
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        tokens = response.usage_metadata.get("total_tokens", 0)
    log.info("Auto-citation completed",
             draft_len=len(draft), enriched_len=len(response.content),
             tokens=tokens)
    return response.content, tokens
