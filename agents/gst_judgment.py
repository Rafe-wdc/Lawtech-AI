"""Agent #12 — GST Appellate Authority for Advance Ruling (AAAR) Agent

Searches 533 GST AAAR appellate orders using a ReAct agent with 7
GST-specific tools. AAARs are state-level administrative appellate bodies
(not courts) — there is no bench / judge metadata.

Handles: "GST classification of solar panels", "Maharashtra AAAR rulings on ITC",
"Section 17(5) advance ruling appeals", etc.

Uses: Gemini Flash (ReAct reasoning + tool calling)
Data Source: OpenSearch "gst_judgements" index
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
from config.prompts import GST_JUDGMENT_SYSTEM_PROMPT
from tools.shared import AGENT_TOOLS, gst_search_by_topic

log = get_logger("GST_Judgment")


def _parse_gst_blocks(text: str) -> list[SourceMetadata]:
    """Extract SourceMetadata from a GST-formatted tool output string.

    Tool output blocks look like:
        **<applicant>** (DB ID: <sha1>)
        - Appeal Order No: ...
        - Date: ...
        - State/UT: ...
        - Brief: ...
        - Underlying AR Order: ...
        - PDF: <url>
    """
    sources: list[SourceMetadata] = []
    if not text:
        return sources

    case_blocks = re.split(r'\n\n(?=\*\*)', text)
    for block in case_blocks:
        if not block.strip() or "No matching" in block:
            continue
        parties_m = re.search(r'\*\*(.+?)\*\*\s*\(DB ID:\s*(\S+)\)', block)
        if not parties_m:
            continue

        case_no_m = re.search(r'Appeal Order No:\s*(.+)', block)
        date_m = re.search(r'Date:\s*(.+)', block)
        state_m = re.search(r'State/UT:\s*(.+)', block)
        brief_m = re.search(r'Brief:\s*(.+)', block)
        ar_m = re.search(r'Underlying AR Order:\s*(.+)', block)
        pdf_m = re.search(r'PDF:\s*(https?://\S+)', block)

        p_title = parties_m.group(1).strip()
        p_dbid = parties_m.group(2).strip()
        pdf_url = pdf_m.group(1).strip() if pdf_m else None

        sources.append(SourceMetadata(
            source_type="gst_judgment",
            title=p_title,
            db_id=p_dbid,
            parties=p_title,
            case_no=case_no_m.group(1).strip() if case_no_m else None,
            judgment_date=date_m.group(1).strip() if date_m else None,
            state_ut=state_m.group(1).strip() if state_m else None,
            brief_of_order=brief_m.group(1).strip() if brief_m else None,
            ar_order_no_date=ar_m.group(1).strip() if ar_m else None,
            doc_link=pdf_url,
            pdf_links=[{"label": "AAAR Order PDF", "url": pdf_url}] if pdf_url and pdf_url != "N/A" else [],
            agent_name="GST_Judgment",
        ))
    return sources


async def gst_judgment_node(state: LegalAgentState) -> dict:
    """Search GST AAAR appellate orders using a ReAct agent."""
    agent_queries = state.get("agent_queries", {})
    query = agent_queries.get("GST_Judgment", state.get("query", state.get("original_query", "")))
    user_context = state.get("user_context", "")
    user_language = state.get("user_language", "en")
    log.info("Agent started", query=query[:100],
             has_user_context=bool(user_context),
             using_agent_query="GST_Judgment" in agent_queries)

    if user_context:
        query = f"User's document/context:\n{user_context}\n\nUser's question:\n{query}"

    try:
        progress("gst_judgment", "Preparing GST AAAR search...", step="prepare")
        # max_output_tokens capped at 8192 (~32k chars) to bound responses
        # and prevent runaway markdown-table padding loops.
        llm = get_gemini_flash_full(temperature=0, max_output_tokens=8192)
        tools = AGENT_TOOLS["gst_judgment"]
        log.debug("Building ReAct agent", tools_count=len(tools))

        agent = create_react_agent(
            llm,
            tools,
            prompt=SystemMessage(content=localize_prompt(
                GST_JUDGMENT_SYSTEM_PROMPT,
                user_language,
                state.get("user_intent"),
            )),
        )

        progress("gst_judgment", "Running multi-step research (ReAct agent)...", step="react")
        with log_time(log, "ReAct agent execution"):
            # Bound the ReAct trajectory — a hung tool loop can otherwise
            # consume the whole 180-300s gateway budget with no local cap.
            result = await asyncio.wait_for(
                agent.ainvoke({"messages": [("user", query)]}),
                timeout=90,
            )

        messages = result.get("messages", [])
        answer = ""
        tools_used: list[str] = []
        sources: list[SourceMetadata] = []

        for msg in messages:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                    if name:
                        tools_used.append(name)

            if hasattr(msg, "type") and msg.type == "ai" and msg.text:
                raw = msg.text
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
            progress("gst_judgment", f"Completed {len(tools_used)} search steps",
                     found=len(tools_used), substep=True, step="react")
        log.info("ReAct agent completed",
                 tools_used=tools_used, tool_calls=len(tools_used),
                 response_len=len(answer), total_messages=len(messages))

        # Fallback: if ReAct returned 0 tool calls, force a topic search
        if not tools_used:
            log.warning("ReAct agent returned 0 tool calls, forcing topic search fallback",
                        query=query[:100])
            try:
                fallback_result = await asyncio.to_thread(
                    gst_search_by_topic.invoke, {"query": query}
                )
                if fallback_result and "No matching" not in fallback_result:
                    sources.extend(_parse_gst_blocks(fallback_result))
                    log.info("Fallback sources parsed", source_count=len(sources))

                    with log_time(log, "ReAct retry with fallback results"):
                        retry_result = await asyncio.wait_for(
                            agent.ainvoke(
                                {"messages": [
                                    ("user", query),
                                    ("assistant", f"I searched for GST AAAR orders and found these results:\n\n{fallback_result}\n\nLet me present these findings to the user."),
                                ]}
                            ),
                            timeout=60,
                        )
                    retry_messages = retry_result.get("messages", [])
                    answer = ""
                    tools_used = ["gst_search_by_topic (fallback)"]
                    for msg in retry_messages:
                        if hasattr(msg, "type") and msg.type == "ai" and msg.text:
                            answer = msg.text
                    if not answer:
                        answer = f"Here are relevant GST AAAR orders:\n\n{fallback_result}"
                    log.info("Fallback search completed", response_len=len(answer))
            except Exception as fb_err:
                log.error("Fallback search failed", error=short_err(fb_err))

        from core.token_tracker import record as _record_tokens
        tokens = 0
        for i, msg in enumerate(messages):
            tokens += _record_tokens("GST_Judgment", f"react_msg_{i}", msg)

        # Parse tool response messages for structured order data
        for msg in messages:
            if hasattr(msg, "type") and msg.type == "tool" and msg.text:
                sources.extend(_parse_gst_blocks(msg.text))

        # Deduplicate by db_id
        seen_ids: set[str] = set()
        unique_sources: list[SourceMetadata] = []
        for s in sources:
            if s.db_id and s.db_id in seen_ids:
                continue
            if s.db_id:
                seen_ids.add(s.db_id)
            unique_sources.append(s)
        sources = unique_sources

        if sources:
            progress("gst_judgment", f"Found {len(sources)} GST AAAR orders",
                     found=len(sources), step="results")
        progress("gst_judgment", "Generating response...", step="generate")
        log.info("Sources parsed from tool messages", source_count=len(sources))

        # Web-search last-resort — the GST AAAR corpus is only ~533
        # orders, so ReAct + topic-search misses are common. Fall to
        # Gemini + Google Search grounding when nothing landed, so the
        # orchestrator's all-empty branch doesn't fire the generic web
        # essay that ignores the GST-specific system prompt.
        if (not answer or not answer.strip()) and not sources:
            progress("gst_judgment",
                     "No corpus results — falling back to web search...",
                     step="fallback", substep=True)
            try:
                from core.agent_fallback import web_search_fallback
                # RAW prompt — web_search_fallback localizes the assembled
                # prompt itself, so localizing here too just duplicates the
                # (long) directive block.
                fb = await web_search_fallback(
                    query,
                    "GST_Judgment",
                    GST_JUDGMENT_SYSTEM_PROMPT,
                    user_language=user_language,
                    intent=state.get("user_intent"),
                )
                if fb.content:
                    log.info("GST web fallback produced answer",
                             answer_len=len(fb.content),
                             fb_sources=len(fb.sources or []))
                    result = AgentResult(
                        agent_name="GST_Judgment",
                        content=fb.content,
                        sources=fb.sources or [],
                        tokens_consumed=(tokens or 0) + (fb.tokens_consumed or 0),
                        fallback_used=True,
                    )
                    return {"agent_results": {"GST_Judgment": result}}
            except Exception as web_err:
                log.warning("GST web fallback failed",
                            error=short_err(web_err))

        result = AgentResult(
            agent_name="GST_Judgment",
            content=answer,
            sources=sources,
            tokens_consumed=tokens,
        )

    except Exception as e:
        from core.metrics import record_agent_error
        record_agent_error("GST_Judgment", e)
        log.error("Agent failed", error=short_err(e), exc_info=True)
        result = AgentResult(
            agent_name="GST_Judgment",
            content="",
            sources=[],
            tokens_consumed=0,
            error=short_err(e),
        )

    return {"agent_results": {"GST_Judgment": result}}
