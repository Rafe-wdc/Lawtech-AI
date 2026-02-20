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

from langchain_core.messages import SystemMessage
from langgraph.prebuilt import create_react_agent

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import get_gpt4o
from core.logger import get_logger, log_time
from config.prompts import SCI_JUDGMENT_SYSTEM_PROMPT
from tools.shared import AGENT_TOOLS

log = get_logger("SCI_Judgment")


async def sci_judgment_node(state: LegalAgentState) -> dict:
    """Search Supreme Court of India judgments using ReAct agent.

    Flow:
    1. Build ReAct sub-agent with 7 SCI tools + system prompt
    2. Invoke with user query (agent decides which tools to call)
    3. Agent performs multi-step research (2-4 searches + case reads)
    4. Extract final response and wrap in AgentResult
    """
    query = state.get("query", state["original_query"])
    log.info("Agent started", query=query[:100])

    try:
        # Build ReAct agent with SCI tools
        llm = get_gpt4o(temperature=0)
        tools = AGENT_TOOLS["sci_judgment"]
        log.debug("Building ReAct agent", tools_count=len(tools))

        agent = create_react_agent(
            llm,
            tools,
            prompt=SystemMessage(content=SCI_JUDGMENT_SYSTEM_PROMPT),
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

        # Estimate token usage from messages
        tokens = 0
        for msg in messages:
            if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                tokens += msg.usage_metadata.get("total_tokens", 0)

        # Build source metadata from answer (extract PDF links if present)
        sources = []
        if answer:
            sources.append(SourceMetadata(
                title="Supreme Court of India Judgments",
                content=[f"Tools used: {', '.join(tools_used)}"] if tools_used else [],
            ))

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
