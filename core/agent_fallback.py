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
from core.clients import get_gemini_flash, get_genai_client
from core.logger import get_logger, log_time, short_err
from core.chat_store import chat_store
from core.metrics import METRICS
from core.settings import MODELS, TIMEOUT_WEB_SEARCH_SEC
from core.token_tracker import record as _record_tokens
from core.token_tracker import record_genai as _record_genai_tokens

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
            llm = get_gemini_flash(temperature=0.1)
            response = llm.invoke([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Original query: {query}"},
            ])
            rewritten = response.text.strip()

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
    *,
    user_language: str = "en",
    intent=None,
) -> AgentResult:
    """Fall back to Gemini 2.5 Flash with Google Search grounding.

    Same pattern as scenario_node() — uses the native genai.Client
    (NOT LangChain) because LangChain doesn't support the google_search tool.

    Retries the same model on transient errors (429 rate-limit, 503
    unavailability) with 2s / 4s / 8s exponential backoff, capped at 3
    attempts. No Pro escalation — Pro and Flash share the same Google
    backend, so escalating for a transient 503 was 4x cost for zero
    reliability gain (removed 2026-08-10).

    Language consistency:
        Some call sites pass an already-localized `system_prompt`, but
        several (e.g. orchestrator's last-resort fallback, legacy
        constitution_maxim paths) pass a raw English prompt. To make the
        layer self-correcting, this function ALSO runs `localize_prompt`
        on the assembled `full_prompt` using the supplied `user_language`
        and `intent`. Re-localizing an already-localized prompt is
        idempotent in effect — the directive simply gets reinforced.
        Default args (`user_language="en"`, `intent=None`) preserve the
        legacy English behaviour for callers that haven't been updated.
    """
    log.info("Web search fallback started", agent=agent_name, query=query[:100])
    METRICS["fallback_total"].labels(agent=agent_name, tier="web_search").inc()

    try:
        # Override any "use only provided context" instructions since we're
        # using web search grounding — the model should use search results.
        # Also inject the Indian authorized-sources allowlist here so EVERY
        # web fallback caller (Newacts / Judgment / Legislation / Drafting /
        # Scenario) gets the discipline — not just the ones whose system
        # prompts wire it in directly. Restores V1's allowlist that was
        # lost in the V2 rewrite; would have prevented the testbook.com /
        # ipleaders.in pollution seen in 2026-06-15 cross-act fallback.
        from config.prompts import (
            INDIAN_LEGAL_AUTHORIZED_SOURCES,
            INDIAN_LEGAL_CITATION_GROUNDING,
        )
        from core.language import localize_prompt
        fallback_instruction = (
            "\n\nIMPORTANT: You are now using web search grounding. "
            "Use the search results to REASON about a comprehensive, accurate "
            "answer. Do NOT say you lack context — synthesise from the search "
            "results. HOWEVER, treat every web result as HIDDEN reasoning "
            "context: the domain name, URL, retrieval id, or search-chunk "
            "identifier from any web result MUST NOT appear anywhere in the "
            "response body. Do NOT write '[leg-<domain>-<digits>]', "
            "'[web-<digits>]', 'Source: <domain>', 'According to <site>', "
            "'per <blog>', 'as noted by <news outlet>', or any external "
            "hyperlink to a web page. Do NOT emit any bracketed marker whose "
            "content is a domain name. When a proposition is grounded only "
            "in a web result and no verified Lawttorney source backs it up, "
            "restate the proposition as general legal background without any "
            "citation, or omit it — never attribute it to the web page."
            "\n\n" + INDIAN_LEGAL_AUTHORIZED_SOURCES
            + "\n\n" + INDIAN_LEGAL_CITATION_GROUNDING
        )
        # Apply the language directive at the fallback layer so callers that
        # passed a raw English prompt also get language consistency.
        localized_system = localize_prompt(
            system_prompt + fallback_instruction, user_language, intent,
        )
        full_prompt = f"{localized_system}\n\nUser Query: {query}"
        client = get_genai_client()

        # Call scenario_web_grounded (Flash) with capped exponential backoff
        # on transient errors (429 rate-limit, 503 unavailability). 2s / 4s / 8s
        # for up to 3 retries — never more than 14s cumulative wait per call.
        #
        # 2026-08-10: dropped the Flash→Pro escalation on 503. Pro and Flash
        # are served from the same Google backend, so escalating to Pro for a
        # transient 503 was paying ~4x per token for zero reliability gain.
        # If Flash is truly down for >14s, so is Pro — the escalation was cost
        # theatre. Retry-same-model on 503 matches the pattern already proven
        # for 429.
        _primary = MODELS["scenario_web_grounded"]
        response = None
        transient_retries = 0
        while True:
            try:
                with log_time(log, f"{_primary} + Google Search", agent=agent_name):
                    response = await asyncio.wait_for(
                        asyncio.to_thread(
                            client.models.generate_content,
                            model=_primary,
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
                break  # success
            except Exception as model_err:
                err_text = str(model_err)
                is_429 = (
                    "429" in err_text
                    or "RESOURCE_EXHAUSTED" in err_text
                    or "rate limit" in err_text.lower()
                )
                is_503 = (
                    "503" in err_text
                    or "UNAVAILABLE" in err_text
                    or "overloaded" in err_text.lower()
                )
                if (is_429 or is_503) and transient_retries < 3:
                    wait_s = 2 ** (transient_retries + 1)  # 2s, 4s, 8s
                    transient_retries += 1
                    log.warning(
                        "Gemini transient error — backing off",
                        model=_primary, wait_s=wait_s,
                        attempt=transient_retries,
                        error_class=("429" if is_429 else "503"),
                        agent=agent_name,
                    )
                    await asyncio.sleep(wait_s)
                    continue
                raise

        if response is None:
            raise RuntimeError("Web fallback failed after transient-error retries")

        if not response.candidates or not response.candidates[0].content.parts:
            log.warning("Gemini web fallback returned empty candidates/parts",
                        agent=agent_name)
            content = "I was unable to retrieve information on this topic at the moment. Please try rephrasing your question."
        else:
            content = getattr(response.candidates[0].content.parts[0], "text", None) or "I was unable to retrieve information on this topic at the moment. Please try rephrasing your question."
        tokens = getattr(response.usage_metadata, "total_token_count", 0)
        # Feed the raw genai response into the per-request TokenUsage tracker
        # so this call shows up in by_agent / by_model / cost_usd summaries.
        # Closes the observability gap where web-grounded fallback tokens
        # (often the priciest calls in the pipeline) were invisible.
        _record_genai_tokens(agent_name, "web_grounded", response, _primary)

        # Stream the fallback content as tokens to the frontend. Run through
        # the URL scrubber so external Google-Search-grounded links don't
        # flash into the SSE stream (they're also stripped from final_response
        # by guardrail_output_node).
        try:
            from langgraph.config import get_stream_writer
            from core.url_filter import StreamingUrlFilter
            writer = get_stream_writer()
            url_filter = StreamingUrlFilter()
            chunk_size = 20
            for i in range(0, len(content), chunk_size):
                safe = url_filter.push(content[i:i + chunk_size])
                if safe:
                    writer({"type": "token", "content": safe})
            tail = url_filter.flush()
            if tail:
                writer({"type": "token", "content": tail})
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
                  agent=agent_name, error=short_err(e), exc_info=True)
        # Sanitize: short_err prepends the exception type (so an empty
        # TimeoutError still surfaces as "TimeoutError"), then redact
        # brand tokens so a "google.genai.errors..." SDK message can't
        # leak the provider to the user.
        from core.redact import redact_brands
        sanitized = redact_brands(short_err(e))
        return AgentResult(
            agent_name=agent_name,
            content="I was unable to retrieve information on this topic at the moment. Please try rephrasing your question.",
            sources=[],
            tokens_consumed=0,
            error=f"Web search fallback failed: {sanitized}",
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

        # Same policy as web_search_fallback: Flash only, 2/4/8s exponential
        # backoff on 429/503, no Pro escalation. (2026-08-10)
        _primary = MODELS["scenario_web_grounded"]
        response = None
        transient_retries = 0
        while True:
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.models.generate_content,
                        model=_primary,
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
                err_text = str(model_err)
                is_429 = (
                    "429" in err_text
                    or "RESOURCE_EXHAUSTED" in err_text
                    or "rate limit" in err_text.lower()
                )
                is_503 = (
                    "503" in err_text
                    or "UNAVAILABLE" in err_text
                    or "overloaded" in err_text.lower()
                )
                if (is_429 or is_503) and transient_retries < 3:
                    wait_s = 2 ** (transient_retries + 1)  # 2s, 4s, 8s
                    transient_retries += 1
                    log.debug(
                        "Gemini transient error during enrichment — backing off",
                        model=_primary, wait_s=wait_s,
                        attempt=transient_retries,
                        error_class=("429" if is_429 else "503"),
                        agent=agent_name,
                    )
                    await asyncio.sleep(wait_s)
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
        # Record tokens for cost attribution.
        _record_genai_tokens(agent_name, "web_enrich", response, _primary)
        log.debug("Web context enrichment completed",
                  agent=agent_name, context_len=len(text))
        return text

    except Exception as e:
        log.warning("Web context enrichment failed, continuing without it",
                    agent=agent_name, error=str(e)[:100])
        return ""
