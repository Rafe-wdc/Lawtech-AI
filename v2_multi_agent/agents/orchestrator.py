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

from core.state import LegalAgentState, AgentResult
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
        "SCI_Judgment",
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
        description="List of agent names to invoke: Legislation, Judgment, Newacts, Drafting, Scenario, Constitution, Maxim, Legal_Concepts, SCI_Judgment",
    )
    reasoning: str = Field(..., description="Brief reasoning for agent selection")


PLAN_PROMPT = """You are a legal query planner. Given a query and its primary task type, determine if MULTIPLE agents should handle it.

Available agents:
- Legislation: Central/state law sections and provisions (all acts EXCEPT the 6 below)
- Judgment: Court case laws, citations, precedents (general / High Court / unspecified courts)
- Newacts: ONLY these 6 acts: BNS/IPC, BNSS/CrPC, BSA/IEA
- Drafting: Legal document templates and drafting
- Scenario: Situational analysis with web search
- Constitution: Constitutional provisions, fundamental rights, Articles
- Maxim: Legal maxims and doctrines (Latin phrases like res judicata, audi alteram partem, estoppel)
- Legal_Concepts: General legal explanations (use only when no specific category applies)
- SCI_Judgment: Supreme Court of India case search

Rules:
1. Most queries need only the PRIMARY agent matching the task type.
2. Use MULTIPLE agents when the query explicitly asks for different types:
   - "Draft bail application with relevant case laws" → [Drafting, Judgment]
   - "Section 438 BNSS with SC precedents" → [Newacts, SCI_Judgment]
   - "Arguments on behalf of plaintiff and defendant" → [Scenario, Judgment]
   - "Explain Article 21 and related case laws" → [Constitution, Judgment]
3. When a query mentions BOTH a constitutional concept AND a legal maxim/doctrine → [Constitution, Maxim]
   - "Article 14 and res judicata" → [Constitution, Maxim]
   - "Article 21 and audi alteram partem" → [Constitution, Maxim]
4. When a query references a named SC landmark case (Puttaswamy, Maneka Gandhi, Kesavananda Bharati, Vishaka, etc.) alongside a constitutional topic → include SCI_Judgment with Constitution.
5. For complex scenarios requesting statutes → add Legislation or Newacts alongside Scenario.
6. NEVER include Drafting unless the user explicitly asks to draft/write/prepare a legal document. Asking "what legal options" or "how can I" is NOT a drafting request.
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


def _plan_agents(query: str, task: str) -> list[str]:
    """Determine which domain agents to invoke for this query.

    For simple queries: returns just the primary task agent.
    For complex queries: returns multiple agents to run in parallel.
    For Drafting: always adds citation agents (Judgment + Legislation/Newacts).
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
        agents = agents if agents else [task]
    except Exception as e:
        log.error("Planning failed, falling back to single agent",
                  error=str(e), fallback=task)
        agents = [task]

    # For Drafting: ALWAYS add citation agents for court-filing quality
    if task == "Drafting" or "Drafting" in agents:
        if "Drafting" not in agents:
            agents.insert(0, "Drafting")
        citation_agents = _select_citation_agents(query)
        for ca in citation_agents:
            if ca not in agents:
                agents.append(ca)
        agents = agents[:4]  # allow up to 4 agents for drafting
        log.info("Drafting citation agents added",
                 agents=agents, citation_agents=citation_agents)

    return agents


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
        try:
            task = await asyncio.wait_for(
                asyncio.to_thread(
                    _classify_task, query, chat_summary=summary if summary else None
                ),
                timeout=30,
            )
        except asyncio.TimeoutError:
            task = _classify_task_regex_fallback(query)
            log.warning("Task classification timed out, using regex fallback",
                        task=task, query=query[:80])

    # Step 2: Handle non-legal
    if task == "Non_legal":
        log.warning("Non-legal query blocked", query=query[:80])
        return {
            "task": task,
            "tasks_planned": [],
            "is_blocked": True,
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

    # Step 3: Plan agents
    try:
        tasks_planned = await asyncio.wait_for(
            asyncio.to_thread(_plan_agents, query, task),
            timeout=25,
        )
    except asyncio.TimeoutError:
        log.warning("Agent planning timed out, falling back to single agent", task=task)
        tasks_planned = [task] if task not in ("Non_legal",) else []
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
        }

    # Pass through related_sections from state (populated by newacts agent)
    related_sections = state.get("related_sections", [])

    # Single agent — pass through directly (with auto-citation for Drafting)
    if len(valid_results) == 1:
        name, result = next(iter(valid_results.items()))

        # Drafting solo: auto-enrich with AI-generated citations
        if name == "Drafting" and len(result.content) > 500:
            log.info("Drafting solo — auto-citation enrichment starting",
                     draft_len=len(result.content))
            try:
                enriched = await _auto_cite_draft(query, result.content)
                log.info("Auto-citation enrichment completed",
                         original_len=len(result.content),
                         enriched_len=len(enriched))
                return_dict = {
                    "final_response": enriched,
                    "source_metadata": _serialize_sources(result),
                    "tokens_consumed": result.tokens_consumed,
                }
                if related_sections:
                    return_dict["related_sections"] = related_sections
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
        if related_sections:
            return_dict["related_sections"] = related_sections
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

        try:
            if citations_text.strip():
                enriched = await _inject_citations_into_draft(
                    query, drafting_result.content, citations_text
                )
            else:
                enriched = await _auto_cite_draft(query, drafting_result.content)

            log.info("Draft synthesis completed",
                     enriched_len=len(enriched), total_tokens=total_tokens)

            return_dict = {
                "final_response": enriched,
                "source_metadata": all_serialized_sources,
                "tokens_consumed": total_tokens,
            }
            if related_sections:
                return_dict["related_sections"] = related_sections
            return return_dict

        except Exception as e:
            log.error("Draft synthesis failed, returning raw draft", error=str(e))
            return_dict = {
                "final_response": drafting_result.content,
                "source_metadata": all_serialized_sources,
                "tokens_consumed": total_tokens,
            }
            if related_sections:
                return_dict["related_sections"] = related_sections
            return return_dict

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


# --- Draft Citation Helpers ---

async def _inject_citations_into_draft(
    query: str, draft: str, citations_text: str
) -> str:
    """Preserve full draft and inject real citations from ALL agents.

    Uses Gemini 2.5 Flash for fast citation injection.
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
        })

    tokens = 0
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        tokens = response.usage_metadata.get("total_tokens", 0)
    log.info("Citation injection completed",
             draft_len=len(draft), enriched_len=len(response.content),
             tokens=tokens)
    return response.content


async def _auto_cite_draft(query: str, draft: str) -> str:
    """Add AI-generated citations when no database agents provided results.

    Uses Gemini 2.5 Flash for fast auto-citation.
    """
    with log_time(log, "Draft auto-citation"):
        llm = get_drafting_llm()
        prompt = ChatPromptTemplate.from_template(DRAFT_CITATION_PROMPT)
        chain = prompt | llm

        from core.streaming import stream_chain_response
        response = await stream_chain_response(chain, {
            "query": query,
            "draft": draft,
        })

    tokens = 0
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        tokens = response.usage_metadata.get("total_tokens", 0)
    log.info("Auto-citation completed",
             draft_len=len(draft), enriched_len=len(response.content),
             tokens=tokens)
    return response.content
