"""Agent #8 — Supreme Court of India Judgment Agent

Searches 44,000+ SC judgments using a ReAct agent with 7 specialized tools.
Unlike other domain agents that use deterministic pipelines, this agent
uses multi-step tool calling (2-4 searches + case detail reads) via
create_react_agent from langgraph.prebuilt.

Handles: "SC cases on right to privacy", "Find Supreme Court judgment by
Justice Chandrachud", "Kesavananda Bharati case details", etc.

Uses: GPT-4o (ReAct reasoning + tool calling)
Data Source: Elasticsearch "supreme_court_judgement" index
"""

from __future__ import annotations

import asyncio
import re

from langchain_core.messages import SystemMessage
from langgraph.prebuilt import create_react_agent

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import get_gemini_flash_full
from core.language import localize_prompt
from core.logger import get_logger, log_time, short_err
from core.progress import progress
from config.prompts import SCI_JUDGMENT_SYSTEM_PROMPT
from tools.shared import AGENT_TOOLS, search_by_topic

log = get_logger("SCI_Judgment")


# --- case-name lookup gate ----------------------------------------------
# Cost audit 2026-10-08: a case-name lookup ("Laxman B. Kamble vs MHADA")
# whose parties are not in the Supreme Court index still ran the full ReAct
# research loop, 10 to 14 calls and $0.10 to $0.28, and produced surname
# matches. One AND-match on the `parties` field decides it for free.
_CASE_NAME_RE = re.compile(
    r"^\s*(?P<pet>[^\n]{3,120}?)\s+(?:vs?\.?|versus|v\.)\s+(?P<resp>[^\n]{3,120}?)\s*(?:\(?\d{4}\)?)?\s*$",
    re.IGNORECASE,
)
_PARTY_STOP = frozenset((
    "and", "ors", "anr", "others", "another", "the", "of", "vs", "v", "versus", "shri", "smt",
    "mr", "mrs", "ms", "dr", "m/s", "ltd", "pvt", "co", "sh", "state", "union", "india", "govt",
    "government", "through", "thr", "its", "etc", "anor", "another", "sri", "kum", "ku",
))


_GOVT_PARTY_RE = re.compile(r"(?i)^\s*(?:the\s+)?(?:state|union|govt|government|commissioner|collector|secretary|cbi|central bureau|u\.?o\.?i)\b")


def _significant_party_tokens(name: str) -> list[str]:
    toks = re.findall(r"[a-z]{3,}", (name or "").lower())
    return [t for t in toks if t not in _PARTY_STOP]


def _case_name_lookup(query: str) -> tuple[str, str] | None:
    """Return (petitioner, respondent) when the query is just a case name."""
    if not query or "\n" in query or len(query.split()) > 14:
        return None
    m = _CASE_NAME_RE.match(query)
    if not m:
        return None
    return m.group("pet").strip(" .,"), m.group("resp").strip(" .,")


def _party_in_sci_index(petitioner: str, respondent: str) -> bool | None:
    """True / False when the index can answer, None when it cannot (fail open)."""
    # A government party (State of X, Union of India, CBI) matches thousands of
    # cases; gate on the private party instead.
    if _GOVT_PARTY_RE.match(petitioner or "") and not _GOVT_PARTY_RE.match(respondent or ""):
        tokens = _significant_party_tokens(respondent)
    else:
        tokens = _significant_party_tokens(petitioner) or _significant_party_tokens(respondent)
    if not tokens:
        return None
    try:
        from core.clients import get_es_client
        from core.settings import ES_INDICES
        es = get_es_client()
        r = es.search(index=ES_INDICES["sci_judgments"], body={
            "size": 1, "_source": False,
            "query": {"match": {"parties": {"query": " ".join(tokens), "operator": "and"}}},
        }, request_timeout=15)
        total = r["hits"]["total"]
        return (total["value"] if isinstance(total, dict) else total) > 0
    except Exception as e:  # noqa: BLE001
        log.warning("Case-name party gate skipped (ES error)", error=short_err(e))
        return None


def _not_found_result(petitioner: str, respondent: str) -> AgentResult:
    return AgentResult(
        agent_name="SCI_Judgment",
        content=(
            f"No Supreme Court judgment titled \"{petitioner} vs {respondent}\" was found in the "
            "Lawttorney database. The party names may be spelt differently on the record, or the "
            "matter may be a High Court or unreported order. A case number, citation or date would "
            "allow an exact lookup."
        ),
        sources=[],
        tokens_consumed=0,
    )


async def sci_judgment_node(state: LegalAgentState) -> dict:
    """Search Supreme Court of India judgments using ReAct agent.

    Flow:
    1. Build ReAct sub-agent with 7 SCI tools + system prompt
    2. Invoke with user query (agent decides which tools to call)
    3. Agent performs multi-step research (2-4 searches + case reads)
    4. Extract final response and wrap in AgentResult
    """
    agent_queries = state.get("agent_queries", {})
    # Prefer agent-specific query > normalized English query > original (for multilingual support)
    query = agent_queries.get("SCI_Judgment", state.get("query", state.get("original_query", "")))
    user_context = state.get("user_context", "")
    user_language = state.get("user_language", "en")
    log.info("Agent started", query=query[:100],
             has_user_context=bool(user_context),
             using_agent_query="SCI_Judgment" in agent_queries)
    # For long queries: include pasted content for the ReAct agent
    if user_context:
        query = f"User's document/context:\n{user_context}\n\nUser's question:\n{query}"

    try:
        # Build ReAct agent with SCI tools
        progress("sci_judgment", "Preparing Supreme Court search...", step="prepare")
        _lookup = None if user_context else _case_name_lookup(query)
        if _lookup:
            _present = await asyncio.to_thread(_party_in_sci_index, *_lookup)
            if _present is False:
                log.info("Case-name lookup: parties absent from SCI index, skipping research",
                         petitioner=_lookup[0][:60], respondent=_lookup[1][:60])
                progress("sci_judgment", "No matching Supreme Court case found", step="results")
                return {"agent_results": {"SCI_Judgment": _not_found_result(*_lookup)}}
        # max_output_tokens capped at 8192 (~32k chars) to bound the ReAct
        # agent's final response and prevent runaway markdown-table padding.
        llm = get_gemini_flash_full(temperature=0, max_output_tokens=8192)
        tools = AGENT_TOOLS["sci_judgment"]
        log.debug("Building ReAct agent", tools_count=len(tools))

        # Gap #1: append uploaded-document text to the ReAct agent's system
        # prompt so SCI_Judgment sees the user's matter (upload SCI order +
        # ask "cases citing this?" — the order text now influences retrieval
        # tool selection). Empty when no files attached — flow unchanged.
        from core.state import FileContextData as _FileContextData
        from core.file_context import format_file_context_prefix as _fmt_files
        _sci_system_prompt = localize_prompt(
            SCI_JUDGMENT_SYSTEM_PROMPT,
            user_language,
            state.get("user_intent"),
        )
        _file_prefix = _fmt_files(_FileContextData.from_state(state))
        if _file_prefix:
            _sci_system_prompt = _sci_system_prompt + "\n\n" + _file_prefix
            log.info("SCI_Judgment: uploaded file context injected",
                     prefix_len=len(_file_prefix))
        agent = create_react_agent(
            llm,
            tools,
            prompt=SystemMessage(content=_sci_system_prompt),
        )

        # Invoke the ReAct sub-agent. Bounded because a hung ReAct
        # trajectory can otherwise consume the whole 180-300s gateway
        # budget with no local ceiling.
        progress("sci_judgment", "Running multi-step research (ReAct agent)...", step="react")
        with log_time(log, "ReAct agent execution"):
            result = await asyncio.wait_for(
                agent.ainvoke({"messages": [("user", query)]}),
                timeout=90,
            )

        # Extract final AI message content and tools used
        messages = result.get("messages", [])
        answer = ""
        tools_used = []
        sources = []

        for msg in messages:
            # Track tool calls
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                    if name:
                        tools_used.append(name)

            # Get the last AI message with content as the answer
            if hasattr(msg, "type") and msg.type == "ai" and msg.text:
                raw = msg.text
                # Gemini may return content as list of parts; normalize to string
                if isinstance(raw, list):
                    answer = "\n".join(
                        p.get("text", str(p)) if isinstance(p, dict) else str(p)
                        for p in raw
                    )
                else:
                    answer = raw

        if not answer and messages:
            last_msg = messages[-1]
            if hasattr(last_msg, "content") and last_msg.text:
                raw = last_msg.text
                if isinstance(raw, list):
                    answer = "\n".join(
                        p.get("text", str(p)) if isinstance(p, dict) else str(p)
                        for p in raw
                    )
                else:
                    answer = raw

        if tools_used:
            progress("sci_judgment", f"Completed {len(tools_used)} search steps", found=len(tools_used), substep=True, step="react")
        log.info("ReAct agent completed",
                 tools_used=tools_used, tool_calls=len(tools_used),
                 response_len=len(answer),
                 total_messages=len(messages))

        # Fallback: if ReAct agent didn't call any tools, force a semantic search
        if not tools_used:
            log.warning("ReAct agent returned 0 tool calls, forcing semantic search fallback",
                        query=query[:100])
            try:
                fallback_result = await asyncio.to_thread(
                    search_by_topic.invoke, {"query": query}
                )
                if fallback_result and "No matching" not in fallback_result:
                    # Parse sources from the direct fallback result
                    fallback_text = fallback_result
                    case_blocks = re.split(r'\n\n(?=\*\*)', fallback_text)
                    for block in case_blocks:
                        if not block.strip() or "No matching" in block:
                            continue
                        parties_m = re.search(r'\*\*(.+?)\*\*\s*\(DB ID:\s*(\S+)\)', block)
                        case_no_m = re.search(r'Case No:\s*(.+)', block)
                        date_m = re.search(r'Date:\s*(.+)', block)
                        bench_m = re.search(r'Bench:\s*(.+)', block)
                        judge_m = re.search(r'Judgment By:\s*(.+)', block)
                        pdf_m = re.search(r'PDF:\s*(https?://\S+)', block)
                        if parties_m:
                            p_title = parties_m.group(1).strip()
                            p_dbid = parties_m.group(2).strip()
                            pdf_url = pdf_m.group(1).strip() if pdf_m else None
                            sources.append(SourceMetadata(
                                source_type="sci_judgment",
                                title=p_title,
                                db_id=p_dbid,
                                parties=p_title,
                                case_no=case_no_m.group(1).strip() if case_no_m else None,
                                judgment_date=date_m.group(1).strip() if date_m else None,
                                bench=bench_m.group(1).strip() if bench_m else None,
                                judgment_by=judge_m.group(1).strip() if judge_m else None,
                                doc_link=pdf_url,
                                pdf_links=[{"label": "Judgment PDF", "url": pdf_url}] if pdf_url and pdf_url != "N/A" else [],
                                agent_name="SCI_Judgment",
                            ))
                    log.info("Fallback sources parsed", source_count=len(sources))

                    # Re-invoke ReAct with the search results as context
                    with log_time(log, "ReAct retry with fallback results"):
                        retry_result = await asyncio.wait_for(
                            agent.ainvoke(
                                {"messages": [
                                    ("user", query),
                                    ("assistant", f"I searched for cases and found these results:\n\n{fallback_result}\n\nLet me present these findings to the user."),
                                ]}
                            ),
                            timeout=60,
                        )
                    retry_messages = retry_result.get("messages", [])
                    # Fold the retry trajectory into `messages` so the token-
                    # summing loop below feeds BOTH ReAct calls into the
                    # tracker. Without this, retry LLM calls were silently
                    # dropped from `token_usage.total_tokens`, under-charging
                    # the user's wallet on every fallback-triggered request.
                    messages = list(messages) + list(retry_messages)
                    answer = ""
                    tools_used = ["search_by_topic (fallback)"]
                    for msg in retry_messages:
                        if hasattr(msg, "type") and msg.type == "ai" and msg.text:
                            answer = msg.text
                    if not answer:
                        answer = f"Here are relevant Supreme Court cases found:\n\n{fallback_result}"
                    log.info("Fallback search completed",
                             response_len=len(answer))
            except Exception as fb_err:
                log.error("Fallback search failed", error=short_err(fb_err))

        # Sum token usage across the ReAct trajectory's AI messages
        from core.token_tracker import record as _record_tokens
        tokens = 0
        for i, msg in enumerate(messages):
            tokens += _record_tokens("SCI_Judgment", f"react_msg_{i}", msg)

        # Parse tool response messages to extract structured case data
        for msg in messages:
            if hasattr(msg, "type") and msg.type == "tool" and msg.text:
                text = msg.text
                # Split on case headers: **Parties** (DB ID: 123)
                case_blocks = re.split(r'\n\n(?=\*\*)', text)
                for block in case_blocks:
                    if not block.strip() or "No matching" in block:
                        continue
                    parties_m = re.search(r'\*\*(.+?)\*\*\s*\(DB ID:\s*(\S+)\)', block)
                    case_no_m = re.search(r'Case No:\s*(.+)', block)
                    date_m = re.search(r'Date:\s*(.+)', block)
                    bench_m = re.search(r'Bench:\s*(.+)', block)
                    judge_m = re.search(r'Judgment By:\s*(.+)', block)
                    pdf_m = re.search(r'PDF:\s*(https?://\S+)', block)

                    if parties_m:
                        p_title = parties_m.group(1).strip()
                        p_dbid = parties_m.group(2).strip()
                        pdf_url = pdf_m.group(1).strip() if pdf_m else None
                        sources.append(SourceMetadata(
                            source_type="sci_judgment",
                            title=p_title,
                            db_id=p_dbid,
                            parties=p_title,
                            case_no=case_no_m.group(1).strip() if case_no_m else None,
                            judgment_date=date_m.group(1).strip() if date_m else None,
                            bench=bench_m.group(1).strip() if bench_m else None,
                            judgment_by=judge_m.group(1).strip() if judge_m else None,
                            doc_link=pdf_url,
                            pdf_links=[{"label": "Judgment PDF", "url": pdf_url}] if pdf_url and pdf_url != "N/A" else [],
                            agent_name="SCI_Judgment",
                        ))

        # Deduplicate by db_id
        seen_ids = set()
        unique_sources = []
        for s in sources:
            if s.db_id and s.db_id in seen_ids:
                continue
            if s.db_id:
                seen_ids.add(s.db_id)
            unique_sources.append(s)
        sources = unique_sources

        if sources:
            progress("sci_judgment", f"Found {len(sources)} Supreme Court judgments", found=len(sources), step="results")
        progress("sci_judgment", "Generating response...", step="generate")
        log.info("Sources parsed from tool messages",
                 source_count=len(sources))

        # Web-search last-resort. The SC corpus is broad but not
        # exhaustive; when the ReAct trajectory returns empty content
        # and topic-search also produced nothing, fall to Gemini +
        # Google Search grounding so the user sees a scoped answer with
        # citations instead of a blank content field (which then trips
        # the orchestrator's all-empty branch → generic web fallback).
        if (not answer or not answer.strip()) and not sources:
            progress("sci_judgment",
                     "No corpus results — falling back to web search...",
                     step="fallback", substep=True)
            try:
                from core.agent_fallback import web_search_fallback
                # RAW prompt — web_search_fallback localizes the assembled
                # prompt itself, so localizing here too just duplicates the
                # (long) directive block.
                fb = await web_search_fallback(
                    query,
                    "SCI_Judgment",
                    SCI_JUDGMENT_SYSTEM_PROMPT,
                    user_language=user_language,
                    intent=state.get("user_intent"),
                )
                if fb.content:
                    log.info("SCI web fallback produced answer",
                             answer_len=len(fb.content),
                             fb_sources=len(fb.sources or []))
                    result = AgentResult(
                        agent_name="SCI_Judgment",
                        content=fb.content,
                        sources=fb.sources or [],
                        tokens_consumed=(tokens or 0) + (fb.tokens_consumed or 0),
                        fallback_used=True,
                    )
                    return {"agent_results": {"SCI_Judgment": result}}
            except Exception as web_err:
                log.warning("SCI web fallback failed",
                            error=short_err(web_err))

        result = AgentResult(
            agent_name="SCI_Judgment",
            content=answer,
            sources=sources,
            tokens_consumed=tokens,
        )

    except Exception as e:
        from core.metrics import record_agent_error
        record_agent_error("SCI_Judgment", e)
        log.error("Agent failed", error=short_err(e), exc_info=True)
        result = AgentResult(
            agent_name="SCI_Judgment",
            content="",
            sources=[],
            tokens_consumed=0,
            error=short_err(e),
        )

    return {"agent_results": {"SCI_Judgment": result}}
