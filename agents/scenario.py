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

from core.state import LegalAgentState, AgentResult, SourceMetadata, IntegrationContextData
from core.clients import get_genai_client
from core.language import localize_prompt
from core.logger import get_logger, log_time
from core.progress import progress
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
    user_context = state.get("user_context", "")
    chat_history = state.get("chat_history", [])
    user_language = state.get("user_language", "en")
    integration_ctx = IntegrationContextData.from_state(state)
    log.info("Agent started", query=query[:100],
             has_history=len(chat_history) > 0,
             has_user_context=bool(user_context),
             has_integration_context=bool(integration_ctx and integration_ctx.has_content),
             using_agent_query="Scenario" in agent_queries)
    # For long queries: prepend pasted content to the query for the LLM
    if user_context:
        query = f"User's document/context:\n{user_context}\n\nUser's question:\n{query}"
    # Prepend content fetched from third-party integrations (Google Docs, Notion)
    if integration_ctx and integration_ctx.has_content:
        query = integration_ctx.as_prompt_prefix() + f"User's question:\n{query}"

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
            progress("scenario", "Analyzing legal scenario...", step="analyze")
            # Build prompt dynamically so we can inject language + intent directives
            system_prompt = localize_prompt(
                SCENARIO_SYSTEM_PROMPT,
                user_language,
                state.get("user_intent"),
            )
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
            progress("scenario", "Searching the web for current information...", step="web_search")
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
                # Diagnose why the grounded call returned nothing. Common
                # causes: safety filter on the prompt or response, Google
                # Search finding no verifiable sources on Indic-script
                # queries, or the search step exhausting the token budget.
                _finish_reason = None
                _safety_ratings = None
                _block_reason = None
                try:
                    if response.candidates:
                        _finish_reason = getattr(response.candidates[0], "finish_reason", None)
                        _safety_ratings = getattr(response.candidates[0], "safety_ratings", None)
                    _prompt_feedback = getattr(response, "prompt_feedback", None)
                    if _prompt_feedback is not None:
                        _block_reason = getattr(_prompt_feedback, "block_reason", None)
                except Exception:
                    pass
                log.warning("Gemini + Google Search returned empty candidates/parts — "
                            "will retry once WITHOUT grounding as a rescue",
                            finish_reason=str(_finish_reason) if _finish_reason else None,
                            block_reason=str(_block_reason) if _block_reason else None,
                            safety_ratings=[
                                f"{getattr(r, 'category', '?')}={getattr(r, 'probability', '?')}"
                                for r in (_safety_ratings or [])
                            ] if _safety_ratings else None,
                            user_language=user_language)
                # Rescue: retry once without Google Search grounding. The
                # grounded call sometimes returns empty on Indic-script
                # queries because Google Search struggles to verify
                # legal-domain claims with Indic sources. Falling back to
                # plain Gemini preserves the response (with the caveat
                # that it isn't web-grounded).
                try:
                    with log_time(log, "Gemini Flash rescue (no grounding)"):
                        rescue_resp = await asyncio.wait_for(
                            asyncio.to_thread(
                                client.models.generate_content,
                                model=MODELS["scenario_web_grounded"],
                                contents=[full_prompt],
                                config={
                                    "max_output_tokens": 8000,
                                    "temperature": 0.5,
                                    "top_p": 0.95,
                                },
                            ),
                            timeout=TIMEOUT_WEB_SEARCH_SEC,
                        )
                    if (rescue_resp.candidates
                            and rescue_resp.candidates[0].content.parts):
                        content = getattr(
                            rescue_resp.candidates[0].content.parts[0],
                            "text", None,
                        ) or ""
                        # Preserve the grounded call's usage; add rescue tokens
                        response = rescue_resp  # rebind for grounding-metadata below
                        log.info("Rescue succeeded — non-grounded response",
                                 rescue_len=len(content))
                    else:
                        content = ""
                        log.warning("Rescue also returned empty candidates — "
                                    "returning empty scenario content")
                except Exception as rescue_err:
                    content = ""
                    log.warning("Rescue call failed — returning empty scenario content",
                                error=str(rescue_err)[:200])
            else:
                content = getattr(response.candidates[0].content.parts[0], "text", None) or ""
            tokens = getattr(response.usage_metadata, "total_token_count", 0)

            progress("scenario", "Generating scenario analysis...", step="generate")

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
