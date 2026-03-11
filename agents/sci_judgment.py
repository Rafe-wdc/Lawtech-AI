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
from core.clients import get_gpt4o
from core.language import localize_prompt
from core.logger import get_logger, log_time
from config.prompts import SCI_JUDGMENT_SYSTEM_PROMPT
from tools.shared import AGENT_TOOLS, search_by_topic

log = get_logger("SCI_Judgment")


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
    user_language = state.get("user_language", "en")
    log.info("Agent started", query=query[:100],
             using_agent_query="SCI_Judgment" in agent_queries)

    try:
        # Build ReAct agent with SCI tools
        llm = get_gpt4o(temperature=0)
        tools = AGENT_TOOLS["sci_judgment"]
        log.debug("Building ReAct agent", tools_count=len(tools))

        agent = create_react_agent(
            llm,
            tools,
            prompt=SystemMessage(content=localize_prompt(SCI_JUDGMENT_SYSTEM_PROMPT, user_language)),
        )

        # Invoke the ReAct sub-agent
        with log_time(log, "ReAct agent execution"):
            result = await agent.ainvoke(
                {"messages": [("user", query)]}
            )

        # Extract final AI message content and tools used
        messages = result.get("messages", [])
        answer = ""
        tools_used = []
        pdf_links = []
        sources = []

        for msg in messages:
            # Track tool calls
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                    if name:
                        tools_used.append(name)

            # Get the last AI message with content as the answer
            if hasattr(msg, "type") and msg.type == "ai" and msg.content:
                answer = msg.content

        if not answer and messages:
            last_msg = messages[-1]
            if hasattr(last_msg, "content") and last_msg.content:
                answer = last_msg.content

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
                        retry_result = await agent.ainvoke(
                            {"messages": [
                                ("user", query),
                                ("assistant", f"I searched for cases and found these results:\n\n{fallback_result}\n\nLet me present these findings to the user."),
                            ]}
                        )
                    retry_messages = retry_result.get("messages", [])
                    answer = ""
                    tools_used = ["search_by_topic (fallback)"]
                    for msg in retry_messages:
                        if hasattr(msg, "type") and msg.type == "ai" and msg.content:
                            answer = msg.content
                    if not answer:
                        answer = f"Here are relevant Supreme Court cases found:\n\n{fallback_result}"
                    log.info("Fallback search completed",
                             response_len=len(answer))
            except Exception as fb_err:
                log.error("Fallback search failed", error=str(fb_err))

        # Estimate token usage from messages
        tokens = 0
        for msg in messages:
            if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                tokens += msg.usage_metadata.get("total_tokens", 0)

        # Parse tool response messages to extract structured case data
        for msg in messages:
            if hasattr(msg, "type") and msg.type == "tool" and msg.content:
                text = msg.content
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

        log.info("Sources parsed from tool messages",
                 source_count=len(sources))

        result = AgentResult(
            agent_name="SCI_Judgment",
            content=answer,
            sources=sources,
            tokens_consumed=tokens,
        )

    except Exception as e:
        log.error("Agent failed", error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="SCI_Judgment",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )

    return {"agent_results": {"SCI_Judgment": result}}
