"""Agent #13 — Tribunal Order Agent

Searches the `tribunal_judgements` index (~46.5k orders: CESTAT/CEGAT, CAT,
CIC, NGT, consumer commissions, APTEL, CLB, CERC, IPAB, AAR, Copyright Board,
TDSAT) with a ReAct agent over 6 tribunal tools. Every search can be scoped
to one tribunal (config.tribunals).

Handles: "TDSAT orders on carriage fee disputes", "CESTAT rulings on CENVAT
credit for input services", "NGT orders on sand mining", "CIC decisions on
Section 8(1)(j) RTI", etc.

Uses: Gemini Flash (ReAct reasoning + tool calling)
Data Source: OpenSearch "tribunal_judgements" alias
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
from config.prompts import TRIBUNAL_JUDGMENT_SYSTEM_PROMPT
from tools.shared import AGENT_TOOLS, tribunal_search_by_topic

AGENT = "Tribunal_Judgment"
log = get_logger(AGENT)


def _parse_tribunal_blocks(text: str) -> list[SourceMetadata]:
    """Extract SourceMetadata from tribunal tool output.

    Search results look like:
        **<title>** (DB ID: <id>)
        - Tribunal: <name> (<code>)
        - Case No: ...
        - Date: ...
        - Bench: ...            (optional)
        - PDF: <url>
    and tribunal_get_case_details uses "**Title**: <title> (DB ID: <id>)".
    """
    sources: list[SourceMetadata] = []
    if not text:
        return sources

    for block in re.split(r'\n\n(?=\*\*)', text):
        if not block.strip() or "No matching" in block:
            continue
        head = (re.search(r'\*\*Title\*\*:\s*(.+?)\s*\(DB ID:\s*(\S+?)\)', block)
                or re.search(r'\*\*(.+?)\*\*\s*\(DB ID:\s*(\S+?)\)', block))
        if not head:
            continue
        title, db_id = head.group(1).strip(), head.group(2).strip()

        tribunal_m = re.search(r'Tribunal\**:\s*(.+?)\s*\(([A-Z]+)\)', block)
        case_m = re.search(r'Case No\**:\s*(.+)', block)
        date_m = re.search(r'Date\**:\s*(.+)', block)
        bench_m = re.search(r'Bench\**:\s*(.+)', block)
        pdf_m = re.search(r'PDF\**:\s*(https?://\S+)', block)

        pdf_url = pdf_m.group(1).strip() if pdf_m else None
        date = date_m.group(1).strip() if date_m else None
        year_m = re.search(r'\b(19|20)\d{2}\b', date or "")
        tribunal_name = tribunal_m.group(1).strip() if tribunal_m else "Tribunal"
        case_no = case_m.group(1).strip() if case_m else None

        sources.append(SourceMetadata(
            source_type="tribunal_judgment",
            title=title,
            db_id=db_id,
            parties=title,
            court_name=tribunal_name,
            case_no=None if case_no == "N/A" else case_no,
            judgment_date=None if date == "N/A" else date,
            year=int(year_m.group(0)) if year_m else None,
            bench=bench_m.group(1).strip() if bench_m else None,
            doc_link=pdf_url,
            pdf_links=[{"label": "Tribunal Order PDF", "url": pdf_url}] if pdf_url else [],
            agent_name=AGENT,
        ))
    return sources


def _message_text(msg) -> str:
    raw = msg.text
    if isinstance(raw, list):
        return "\n".join(p.get("text", str(p)) if isinstance(p, dict) else str(p) for p in raw)
    return raw or ""


async def tribunal_judgment_node(state: LegalAgentState) -> dict:
    """Search tribunal orders using a ReAct agent."""
    agent_queries = state.get("agent_queries", {})
    query = agent_queries.get(AGENT, state.get("query", state.get("original_query", "")))
    user_context = state.get("user_context", "")
    user_language = state.get("user_language", "en")
    log.info("Agent started", query=query[:100],
             has_user_context=bool(user_context),
             using_agent_query=AGENT in agent_queries)

    if user_context:
        query = f"User's document/context:\n{user_context}\n\nUser's question:\n{query}"

    try:
        progress("tribunal_judgment", "Preparing tribunal order search...", step="prepare")
        llm = get_gemini_flash_full(temperature=0, max_output_tokens=8192)
        tools = AGENT_TOOLS["tribunal_judgment"]

        from core.state import FileContextData as _FileContextData
        from core.file_context import format_file_context_prefix as _fmt_files
        system_prompt = localize_prompt(
            TRIBUNAL_JUDGMENT_SYSTEM_PROMPT, user_language, state.get("user_intent"),
        )
        file_prefix = _fmt_files(_FileContextData.from_state(state))
        if file_prefix:
            system_prompt = system_prompt + "\n\n" + file_prefix
            log.info("Tribunal_Judgment: uploaded file context injected",
                     prefix_len=len(file_prefix))
        agent = create_react_agent(llm, tools, prompt=SystemMessage(content=system_prompt))

        progress("tribunal_judgment", "Running multi-step research (ReAct agent)...", step="react")
        with log_time(log, "ReAct agent execution"):
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
                answer = _message_text(msg)

        if not answer and messages and hasattr(messages[-1], "content") and messages[-1].text:
            answer = _message_text(messages[-1])

        if tools_used:
            progress("tribunal_judgment", f"Completed {len(tools_used)} search steps",
                     found=len(tools_used), substep=True, step="react")
        log.info("ReAct agent completed",
                 tools_used=tools_used, tool_calls=len(tools_used),
                 response_len=len(answer), total_messages=len(messages))

        # The model answered without searching: force one topic search and
        # let it answer again from those results.
        if not tools_used:
            log.warning("ReAct agent returned 0 tool calls, forcing topic search fallback",
                        query=query[:100])
            try:
                fallback_result = await asyncio.to_thread(
                    tribunal_search_by_topic.invoke, {"query": query}
                )
                if fallback_result and "No matching" not in fallback_result:
                    sources.extend(_parse_tribunal_blocks(fallback_result))
                    with log_time(log, "ReAct retry with fallback results"):
                        retry_result = await asyncio.wait_for(
                            agent.ainvoke({"messages": [
                                ("user", query),
                                ("assistant", "I searched the tribunal orders and found these "
                                              f"results:\n\n{fallback_result}\n\nLet me present "
                                              "these findings to the user."),
                            ]}),
                            timeout=60,
                        )
                    retry_messages = retry_result.get("messages", [])
                    # Both trajectories feed the token tracker below.
                    messages = list(messages) + list(retry_messages)
                    answer = ""
                    tools_used = ["tribunal_search_by_topic (fallback)"]
                    for msg in retry_messages:
                        if hasattr(msg, "type") and msg.type == "ai" and msg.text:
                            answer = _message_text(msg)
                    if not answer:
                        answer = f"Here are relevant tribunal orders:\n\n{fallback_result}"
            except Exception as fb_err:
                log.error("Fallback search failed", error=short_err(fb_err))

        from core.token_tracker import record as _record_tokens
        tokens = 0
        for i, msg in enumerate(messages):
            tokens += _record_tokens(AGENT, f"react_msg_{i}", msg)

        for msg in messages:
            if hasattr(msg, "type") and msg.type == "tool" and msg.text:
                sources.extend(_parse_tribunal_blocks(msg.text))

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
            progress("tribunal_judgment", f"Found {len(sources)} tribunal orders",
                     found=len(sources), step="results")
        progress("tribunal_judgment", "Generating response...", step="generate")
        log.info("Sources parsed from tool messages", source_count=len(sources))

        # Nothing from the corpus: answer from web grounding under this
        # agent's prompt, so the orchestrator's generic all-empty branch
        # does not fire instead.
        if (not answer or not answer.strip()) and not sources:
            progress("tribunal_judgment", "No corpus results — falling back to web search...",
                     step="fallback", substep=True)
            try:
                from core.agent_fallback import web_search_fallback
                fb = await web_search_fallback(
                    query, AGENT, TRIBUNAL_JUDGMENT_SYSTEM_PROMPT,
                    user_language=user_language, intent=state.get("user_intent"),
                )
                if fb.content:
                    log.info("Tribunal web fallback produced answer",
                             answer_len=len(fb.content), fb_sources=len(fb.sources or []))
                    return {"agent_results": {AGENT: AgentResult(
                        agent_name=AGENT,
                        content=fb.content,
                        sources=fb.sources or [],
                        tokens_consumed=(tokens or 0) + (fb.tokens_consumed or 0),
                        fallback_used=True,
                    )}}
            except Exception as web_err:
                log.warning("Tribunal web fallback failed", error=short_err(web_err))

        result = AgentResult(agent_name=AGENT, content=answer, sources=sources,
                             tokens_consumed=tokens)

    except Exception as e:
        from core.metrics import record_agent_error
        record_agent_error("Tribunal_Judgment", e)
        log.error("Agent failed", error=short_err(e), exc_info=True)
        result = AgentResult(agent_name=AGENT, content="", sources=[],
                             tokens_consumed=0, error=short_err(e))

    return {"agent_results": {AGENT: result}}
