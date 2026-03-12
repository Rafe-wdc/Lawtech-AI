"""Agent Fallback Utilities — Query Rewrite + Web Search Fallback

Shared resilience layer for all domain agents. When an agent's primary
data source (Elasticsearch) returns empty, these utilities provide:

1. rewrite_query_for_domain() — GPT-4o-mini rewrites the query for better search
2. web_search_fallback() — Gemini 2.5 Flash + Google Search grounding

Pattern borrowed from scenario_node() in agents/scenario.py.
"""

from __future__ import annotations

import asyncio

from core.state import AgentResult, SourceMetadata
from core.clients import get_gpt4o_mini, get_genai_client
from core.logger import get_logger, log_time
from core.chat_store import chat_store
from core.metrics import METRICS

log = get_logger("AgentFallback")


# --- Domain-Specific Rewrite Prompts ---

_REWRITE_PROMPTS: dict[str, str] = {
    "Newacts": (
        "You are a legal search expert for Indian criminal law codes. "
        "The user's query returned no results from the database of 6 acts: "
        "BNS (IPC), BNSS (CrPC), BSA (IEA). "
        "Rewrite the query to improve search results. "
        "Expand abbreviations, identify the correct act name and section numbers. "
        "If the query mentions old law names, include both old and new equivalents. "
        "Return ONLY the rewritten query, nothing else."
    ),
    "Legislation": (
        "You are a legal search expert for Indian legislation. "
        "The user's query returned no results from the legislation database. "
        "Rewrite the query to improve search results. "
        "Identify the full official act name, section/rule number, and provision type. "
        "Expand abbreviations (e.g., NI Act → Negotiable Instruments Act 1881). "
        "Return ONLY the rewritten query, nothing else."
    ),
    "Judgment": (
        "You are a legal search expert for Indian court judgments. "
        "The user's query returned no results from the judgment database. "
        "Rewrite the query to improve search results. "
        "Expand to include likely party names, court name, year range, and legal topic. "
        "If a case type is mentioned (bail, quashing, writ), make it explicit. "
        "Return ONLY the rewritten query, nothing else."
    ),
    "Constitution": (
        "You are a legal search expert for the Indian Constitution. "
        "The user's query returned no results from the Constitution database. "
        "Rewrite using standard constitutional terminology. "
        "Map common terms to Article numbers (e.g., right to life → Article 21). "
        "Return ONLY the rewritten query, nothing else."
    ),
    "Maxim": (
        "You are a legal search expert for legal maxims and doctrines. "
        "The user's query returned no results from the legal maxim database. "
        "Rewrite using standard Latin legal terminology and the English equivalent. "
        "E.g., 'hearing both sides' → 'audi alteram partem - principles of natural justice'. "
        "Return ONLY the rewritten query, nothing else."
    ),
}


def rewrite_query_for_domain(query: str, agent_name: str) -> str:
    """Rewrite a query to improve search results for a specific agent domain.

    Uses GPT-4o-mini for fast, cheap query rewriting.
    Returns the original query if rewriting fails or produces no change.
    """
    system_prompt = _REWRITE_PROMPTS.get(agent_name)
    if not system_prompt:
        return query

    try:
        with log_time(log, "Query rewrite", agent=agent_name):
            llm = get_gpt4o_mini(temperature=0.1)
            response = llm.invoke([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Original query: {query}"},
            ])
            rewritten = response.content.strip()

        if rewritten and rewritten != query:
            log.info("Query rewritten",
                     agent=agent_name,
                     original=query[:80],
                     rewritten=rewritten[:80])
            METRICS["fallback_total"].labels(agent=agent_name, tier="query_rewrite").inc()
            return rewritten

        log.debug("Rewrite produced no change", agent=agent_name)
        return query

    except Exception as e:
        log.warning("Query rewrite failed, using original",
                    agent=agent_name, error=str(e))
        return query


async def web_search_fallback(
    query: str,
    agent_name: str,
    system_prompt: str,
    original_query: str = "",
) -> AgentResult:
    """Fall back to Gemini 2.5 Flash with Google Search grounding.

    Same pattern as scenario_node() — uses the native genai.Client
    (NOT LangChain) because LangChain doesn't support the google_search tool.

    Retries once with gemini-2.5-pro if Flash returns 503.
    """
    log.info("Web search fallback started", agent=agent_name, query=query[:100])
    METRICS["fallback_total"].labels(agent=agent_name, tier="web_search").inc()

    try:
        # Override any "use only provided context" instructions since we're
        # using web search grounding — the model should use search results
        fallback_instruction = (
            "\n\nIMPORTANT: You are now using web search grounding. "
            "Use the search results to provide a comprehensive, accurate answer. "
            "Do NOT say you lack context — use the web search results."
        )
        full_prompt = f"{system_prompt}{fallback_instruction}\n\nUser Query: {query}"
        client = get_genai_client()

        # Try gemini-2.5-flash first, fall back to gemini-2.5-pro on 503
        response = None
        for model in ["gemini-2.5-flash", "gemini-2.5-pro"]:
            try:
                with log_time(log, f"{model} + Google Search", agent=agent_name):
                    response = await asyncio.wait_for(
                        asyncio.to_thread(
                            client.models.generate_content,
                            model=model,
                            contents=[full_prompt],
                            config={
                                "tools": [{"google_search": {}}],
                                "max_output_tokens": 8000,
                                "temperature": 0.5,
                                "top_p": 0.95,
                            },
                        ),
                        timeout=120.0,
                    )
                break  # success
            except Exception as model_err:
                if "503" in str(model_err) and model == "gemini-2.5-flash":
                    log.warning("Flash 503, retrying with Pro",
                                agent=agent_name, error=str(model_err)[:100])
                    await asyncio.sleep(1)
                    continue
                raise

        if response is None:
            raise RuntimeError("All models failed")

        if not response.candidates or not response.candidates[0].content.parts:
            log.warning("Gemini web fallback returned empty candidates/parts",
                        agent=agent_name)
            content = ""
        else:
            content = response.candidates[0].content.parts[0].text
        tokens = getattr(response.usage_metadata, "total_token_count", 0)

        # Stream the fallback content as tokens to the frontend
        try:
            from langgraph.config import get_stream_writer
            writer = get_stream_writer()
            # Stream in small chunks for smooth frontend rendering
            chunk_size = 20
            for i in range(0, len(content), chunk_size):
                writer({"type": "token", "content": content[i:i + chunk_size]})
        except RuntimeError:
            pass  # not in streaming context (batch endpoint)

        # Extract web sources from grounding metadata
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
                                source_type=agent_name.lower(),
                                title=title_text or "Web Source",
                                web_url=url,
                                web_title=title_text,
                                agent_name=agent_name,
                            ))
        except Exception as grounding_err:
            log.warning("Failed to extract grounding metadata",
                        error=str(grounding_err))

        if not sources:
            sources.append(SourceMetadata(
                source_type=agent_name.lower(),
                title=f"AI-Generated {agent_name} Analysis",
                content=["Response generated by AI with web search grounding"],
                agent_name=agent_name,
            ))

        log.info("Web search fallback completed",
                 agent=agent_name, response_len=len(content),
                 tokens=tokens, web_sources=len(sources))

        # Fire-and-forget: log to fallback_log for ES backfill tracking
        web_urls = [s.web_url for s in sources if getattr(s, "web_url", None)]
        asyncio.create_task(
            chat_store.log_fallback(
                agent=agent_name,
                query=query,
                original_query=original_query or query,
                response_preview=content[:600],
                web_sources=web_urls,
                tokens=tokens,
                fallback_tier="web",
            )
        )

        return AgentResult(
            agent_name=agent_name,
            content=content,
            sources=sources,
            tokens_consumed=tokens,
            fallback_used=True,
        )

    except Exception as e:
        log.error("Web search fallback failed",
                  agent=agent_name, error=str(e), exc_info=True)
        return AgentResult(
            agent_name=agent_name,
            content="",
            sources=[],
            tokens_consumed=0,
            error=f"Web search fallback failed: {e}",
            fallback_used=True,
        )


async def get_web_context(query: str, agent_name: str) -> str:
    """Fetch supplementary web context to enrich local retrieval results.

    Runs Gemini + Google Search grounding and returns the raw text only —
    no streaming, no AgentResult. Used to augment ES retrieval context with
    examples, case laws, and practical application details.

    Returns empty string on failure (graceful degradation).
    """
    log.debug("Web context enrichment started", agent=agent_name, query=query[:80])
    try:
        client = get_genai_client()
        enrich_prompt = (
            f"Provide supplementary information about: {query}\n"
            "Include: relevant case laws, examples of practical application, "
            "historical context, exceptions, and how it is used in Indian courts."
        )

        response = None
        for model in ["gemini-2.5-flash", "gemini-2.5-pro"]:
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.models.generate_content,
                        model=model,
                        contents=[enrich_prompt],
                        config={
                            "tools": [{"google_search": {}}],
                            "max_output_tokens": 4000,
                            "temperature": 0.3,
                        },
                    ),
                    timeout=45.0,
                )
                break
            except Exception as model_err:
                if "503" in str(model_err) and model == "gemini-2.5-flash":
                    log.debug("Flash 503 during enrichment, retrying with Pro",
                              agent=agent_name)
                    await asyncio.sleep(1)
                    continue
                raise

        if response is None:
            return ""

        if not response.candidates or not response.candidates[0].content.parts:
            log.warning("Web enrichment returned empty candidates/parts",
                        agent=agent_name)
            text = ""
        else:
            text = response.candidates[0].content.parts[0].text or ""
        log.debug("Web context enrichment completed",
                  agent=agent_name, context_len=len(text))
        return text

    except Exception as e:
        log.warning("Web context enrichment failed, continuing without it",
                    agent=agent_name, error=str(e)[:100])
        return ""
