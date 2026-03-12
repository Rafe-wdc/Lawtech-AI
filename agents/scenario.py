"""Agent #8 — Scenario Agent

Handles complex situational queries using Gemini 2.5 Pro with
Google Search grounding for real-time legal analysis.

Also serves as the fallback for "Other" task types and when
other agents return empty results.

Handles: "My landlord refuses to return deposit...", legal news, etc.

Uses: Gemini 2.5 Pro (with Google Search tool)
Data Source: Google Search (real-time web)
"""

from __future__ import annotations

import asyncio

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import get_genai_client
from core.language import localize_prompt
from core.logger import get_logger, log_time
from core.settings import MODELS, TIMEOUT_WEB_SEARCH_SEC
from config.prompts import SCENARIO_SYSTEM_PROMPT

log = get_logger("Scenario")

# Limit concurrent Scenario executions per worker process (Gemini rate-limit protection)
_AGENT_SEMAPHORE = asyncio.Semaphore(3)


# --- Agent Node ---

async def scenario_node(state: LegalAgentState) -> dict:
    """Analyze legal scenario with web-grounded AI.

    Flow:
    1. Build prompt with system instructions (Lawttorney persona)
    2. Include chat history for multi-turn context
    3. Invoke Gemini 2.5 Pro with Google Search grounding
    4. Extract response text and token usage
    5. Return with "Disclaimer" source metadata
    """
    agent_queries = state.get("agent_queries", {})
    query = agent_queries.get("Scenario", state.get("query", state["original_query"]))
    chat_history = state.get("chat_history", [])
    user_language = state.get("user_language", "en")
    log.info("Agent started", query=query[:100],
             has_history=len(chat_history) > 0,
             using_agent_query="Scenario" in agent_queries)

    # Acquire concurrency slot; emit queue_status SSE event if at capacity
    try:
        from langgraph.config import get_stream_writer
        _writer = get_stream_writer()
    except (RuntimeError, ImportError):
        _writer = None

    if _AGENT_SEMAPHORE.locked():
        log.warning("Concurrency limit reached, queuing Scenario request")
        if _writer:
            _writer({"type": "queue_status", "status": "queued",
                     "message": "Scenario agent is busy, queuing your request..."})

    try:
        async with _AGENT_SEMAPHORE:
            # Build prompt dynamically so we can inject language instruction
            system_prompt = localize_prompt(SCENARIO_SYSTEM_PROMPT, user_language)
            template = ChatPromptTemplate.from_messages([
                ("system", system_prompt),
                MessagesPlaceholder("chat_history", optional=True),
                ("human", "{input}"),
            ])
            prompt_messages = template.format_messages(input=query, chat_history=chat_history)
            full_prompt = "\n".join(msg.content for msg in prompt_messages)

            log.debug("Prompt built", prompt_len=len(full_prompt))

            # Invoke Gemini 2.5 Flash with Google Search grounding
            # Uses asyncio.to_thread to avoid blocking the event loop
            # 120s timeout to prevent indefinite hangs on complex searches
            with log_time(log, "Gemini Flash + Google Search"):
                client = get_genai_client()
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.models.generate_content,
                        model=MODELS["scenario_web_grounded"],
                        contents=[full_prompt],
                        config={
                            "tools": [{"google_search": {}}],
                            "max_output_tokens": 8000,
                            "temperature": 0.5,
                            "top_p": 0.95,
                        },
                    ),
                    timeout=TIMEOUT_WEB_SEARCH_SEC,
                )

            # Extract response text (guard against empty candidates/parts/null text)
            if not response.candidates or not response.candidates[0].content.parts:
                log.warning("Gemini returned empty candidates/parts")
                content = ""
            else:
                content = getattr(response.candidates[0].content.parts[0], "text", None) or ""
            tokens = getattr(response.usage_metadata, "total_token_count", 0)

            # Extract web sources from Gemini grounding metadata
            sources = []
            try:
                candidate = response.candidates[0]
                grounding_meta = getattr(candidate, "grounding_metadata", None)
                if grounding_meta:
                    chunks = getattr(grounding_meta, "grounding_chunks", None) or []
                    seen_urls = set()
                    for chunk in chunks:
                        web = getattr(chunk, "web", None)
                        if web:
                            url = getattr(web, "uri", None)
                            title_text = getattr(web, "title", None)
                            if url and url not in seen_urls:
                                seen_urls.add(url)
                                sources.append(SourceMetadata(
                                    source_type="scenario",
                                    title=title_text or "Web Source",
                                    web_url=url,
                                    web_title=title_text,
                                    agent_name="Scenario",
                                ))
            except Exception as grounding_err:
                log.warning("Failed to extract grounding metadata",
                            error=str(grounding_err))

            if not sources:
                sources.append(SourceMetadata(
                    source_type="scenario",
                    title="AI-Generated Legal Analysis",
                    content=["Response generated by AI with web search grounding"],
                    agent_name="Scenario",
                ))

            log.info("Agent completed",
                     response_len=len(content), tokens=tokens,
                     web_sources=len(sources))

            result = AgentResult(
                agent_name="Scenario",
                content=content,
                sources=sources,
                tokens_consumed=tokens,
            )

    except Exception as e:
        log.error("Agent failed", error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="Scenario",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )

    return {"agent_results": {"Scenario": result}}
