"""Agent #4 — Legislation Agent

Retrieves and explains central/state legislation from Elasticsearch.

Handles: "Section 15 of Companies Act", "Consumer Protection Act refunds", etc.

Uses: Gemini Flash Lite (generation) + GPT-4o-mini (match phrase extraction)
Data Source: Elasticsearch "legislation" index

Search strategy:
1. Parse query for section type/number(s)/act name
2. Multi-section path: if multiple sections detected, search each in parallel
3. Single-section path: multi-query ES search with source aggregation
4. Topic fallback: if no section parsed and no hits, use BM25 topic search
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import get_es_client, get_gemini_flash, get_gemini_flash_full
from core.retrieval_relevance import (
    check_retrieval_relevance, head_tail_apology_detected,
)
from core.settings import ES_INDICES
from core.language import localize_prompt
from core.logger import get_logger, log_time, short_err
from config.prompts import LEGISLATION_SYSTEM_PROMPT

from tools.inline.section_parser import parse_section_info, parse_multi_section_info
from tools.shared.elasticsearch_tools import (
    _create_search_variations,
    _search_legislation_by_topic,
    _search_legislation_multi_section,
)
from core.progress import progress

log = get_logger("Legislation")


# _extract_match_phrase / QueryMetadata / MATCH_PHRASE_PROMPT were
# removed 2026-08-02 (P2 dead-code sweep). None of them was called from
# anywhere — the current pipeline uses UserIntent.named_acts + the
# _search_legislation → _pick_best_source chain instead.


# --- Act-name preference for source ranking ---
#
# The 19-entry regex bank that used to live here (_ACT_PATTERNS +
# _extract_act_name_from_query) was retired. Act names are now extracted
# by the orchestrator's intent extractor and exposed as
# `UserIntent.named_acts: list[str]` — an LLM-driven extraction that
# correctly recognises Insolvency and Bankruptcy Code, POCSO, JJ Act,
# the Sale of Goods Act, the Industrial Disputes Act, and the hundred+
# other Indian acts the regex didn't cover. The Legislation agent now
# threads named_acts into `_search_legislation` and uses it for the
# act-aware source preference.


# Relevance gate is in core.retrieval_relevance — shared across agents.
# See that module for the full design rationale.


# --- Elasticsearch Search (single-section path) ---

def _search_legislation(
    query: str,
    named_acts: list[str] | None = None,
) -> tuple[list[dict], str | None]:
    """Multi-query ES search for legislation. Returns (hits, most_common_source).

    When `named_acts` is non-empty, the source ranker prefers sources whose
    file name contains one of the named acts — this prevents queries like
    "Consumer Protection Act 2019" matching unrelated acts that share
    keywords. Replaces the prior 19-entry hardcoded regex bank.
    """
    from collections import Counter

    es = get_es_client()
    index = ES_INDICES["legislation"]

    parsed_info = parse_section_info(query)
    search_queries = _create_search_variations(query, parsed_info)

    all_hits = []
    sources_counter: Counter = Counter()

    for i, query_text in enumerate(search_queries):
        es_query = {
            "size": 20,
            "query": {
                "bool": {
                    "should": [
                        {"match": {
                            "page_content": {
                                "query": query_text,
                                "boost": 2.0 if query_text == query else 1.0,
                            }
                        }},
                        {"match_phrase": {
                            "page_content": {
                                "query": query_text,
                                "boost": 1.5,
                            }
                        }},
                    ]
                }
            },
        }

        try:
            response = es.search(index=index, body=es_query, request_timeout=8)
            current_hits = response["hits"]["hits"]

            for hit in current_hits:
                sources_counter[hit["_source"]["source"]] += hit["_score"]
            all_hits.extend(current_hits)

            log.debug("ES search iteration",
                      iteration=i + 1, variation=query_text[:60],
                      hits=len(current_hits))

            # Early exit on strong matches
            if len(current_hits) > 5 and any(h["_score"] > 5.0 for h in current_hits):
                log.debug("Strong match found, early exit", iteration=i + 1)
                break
        except Exception as e:
            log.error("ES search failed for variation",
                      variation=query_text[:60], error=str(e))
            continue

    if not sources_counter:
        return [], None

    # When the query names a specific act (extracted by the intent
    # extractor into UserIntent.named_acts), prefer ES sources whose
    # filename matches one of those acts. Prevents queries like
    # "Consumer Protection Act 2019" matching unrelated acts that
    # share keywords. Falls back to most-common when no named act
    # matches any source.
    most_common_source = sources_counter.most_common(1)[0][0]
    if named_acts:
        # Try each named act in order — first match wins. Compare by
        # significant tokens (drop "Act", "of", year digits) to be
        # robust to formatting differences between user query and
        # ES source filenames.
        def _significant_tokens(s: str) -> set[str]:
            return {
                t for t in re.findall(r"[a-z]{3,}", s.lower())
                if t not in {"act", "the", "and", "for", "code", "rules"}
            }

        for act_name in named_acts:
            act_tokens = _significant_tokens(act_name)
            if not act_tokens:
                continue
            matching_sources = [
                (src, score) for src, score in sources_counter.most_common()
                if act_tokens & _significant_tokens(src)
            ]
            if matching_sources:
                most_common_source = matching_sources[0][0]
                log.info("Named act matched source preferred",
                         act=act_name, source=most_common_source)
                break

    log.info("Most relevant source identified",
             source=most_common_source,
             total_hits=len(all_hits),
             unique_sources=len(sources_counter))

    # Targeted search within the most common source
    if parsed_info:
        st = parsed_info['section_type']
        sn = parsed_info['section_number']

        should_clauses = []
        for qt in search_queries[:5]:
            should_clauses.extend([
                {"match_phrase": {"page_content": {"query": qt, "boost": 3.0}}},
                {"match": {"page_content": {"query": qt, "boost": 1.5}}},
            ])

        for variation in [f"{st} {sn}", f"{st.title()} {sn}", f"{st.upper()} {sn}"]:
            should_clauses.append(
                {"match_phrase": {"page_content": {"query": variation, "boost": 4.0}}}
            )

        # Boost section header pattern to prioritize the actual section
        # over cross-referencing sections (e.g., "section number: Section 138")
        for header_var in [
            f"section number: {st.title()} {sn}",
            f"section number: {st} {sn}",
        ]:
            should_clauses.append(
                {"match_phrase": {"page_content": {"query": header_var, "boost": 8.0}}}
            )

        # Case-tolerant exact filter on section_number.keyword. The parser
        # lowercases letter suffixes (e.g. "498a", "65b"), while ES stores
        # them uppercased ("498A", "65B") — a plain `term` filter would
        # miss. `terms` with both casings covers all observed acts and
        # works on both ES and OpenSearch (no case_insensitive dependency).
        # Anchors retrieval to the exact section the parser identified,
        # so definition-heavy sections (like Section 2 of IR Code 2020,
        # whose 20-KB definitions block loses BM25 to short cross-
        # referencing sections) still surface.
        sn_variants = list({sn, sn.upper(), sn.lower()})
        source_query = {
            "query": {
                "bool": {
                    "must": [{"term": {"source.keyword": most_common_source}}],
                    "filter": [{"terms": {"section_number.keyword": sn_variants}}],
                    "should": should_clauses,
                    "minimum_should_match": 0,
                }
            },
            "size": 5,
            "sort": [{"_score": {"order": "desc"}}],
        }
    else:
        source_query = {
            "query": {
                "bool": {
                    "must": [{"match": {"page_content": query}}],
                    "filter": [{"term": {"source.keyword": most_common_source}}],
                }
            },
            "size": 5,
        }

    source_response = es.search(index=index, body=source_query, request_timeout=8)
    final_hits = source_response["hits"]["hits"]

    # Safety net: if the exact-section filter returned nothing, retry the
    # legacy boost-only query. Protects against parser mis-extractions
    # (e.g. "wages under Industrial Relations Code 2020" → sn="2020"),
    # sections stored with unusual section_number formatting, and any
    # other edge case where the identified section number simply isn't
    # present as a keyword under the picked source.
    if parsed_info and not final_hits:
        log.info("Section-filter returned 0 hits, retrying without filter",
                 source=most_common_source, section=parsed_info["section_number"])
        source_query_nofilter = {
            "query": {
                "bool": {
                    "must": [{"term": {"source.keyword": most_common_source}}],
                    "should": should_clauses,
                    "minimum_should_match": 1,
                }
            },
            "size": 5,
            "sort": [{"_score": {"order": "desc"}}],
        }
        source_response = es.search(index=index, body=source_query_nofilter, request_timeout=8)
        final_hits = source_response["hits"]["hits"]

    log.debug("Targeted source search done",
              source=most_common_source, hits=len(final_hits))
    return final_hits, most_common_source


# --- Agent Node ---

async def legislation_node(state: LegalAgentState) -> dict:
    """Retrieve legislation and generate response.

    Flow:
    1. Parse query with multi-section parser
    2. If multiple sections: use multi-section search path
    3. If single section: use standard multi-query search
    4. If no section and no hits: fall back to topic-based search
    5. Generate response using retrieved statute text
    """
    agent_queries = state.get("agent_queries", {})
    # Prefer agent-specific query > normalized English query > original (for multilingual support)
    query = agent_queries.get("Legislation", state.get("query", state.get("original_query", "")))
    user_context = state.get("user_context", "")
    chat_history = state.get("chat_history", [])
    intent = state.get("user_intent")
    named_acts = getattr(intent, "named_acts", None) or []
    _user_language = state.get("user_language", "en")
    # `_system_prompt` is localized for the direct ES-grounded generation
    # path. The web fallback below gets the RAW prompt + `_user_language` /
    # `intent` so IT applies the directive last (recency); pre-localizing for
    # that path let the fallback's default English directive override it.
    _system_prompt = localize_prompt(
        LEGISLATION_SYSTEM_PROMPT,
        _user_language,
        intent,
    )
    log.info("Agent started", query=query[:100],
             using_agent_query="Legislation" in agent_queries,
             named_acts_count=len(named_acts))

    try:
        progress("legislation", "Parsing query for section references...", step="parse")

        # Strip subsection parentheticals for cleaner ES matching
        clean_query = re.sub(r'\(\w+\)', '', query).strip()
        clean_query = re.sub(r'\s+', ' ', clean_query)
        if clean_query != query:
            log.info("Stripped subsections for ES search",
                     original=query[:80], clean=clean_query[:80])

        # Step 1: Parse query for section info (use original for full parsing)
        multi_parsed = parse_multi_section_info(query)
        search_mode = "standard"

        if multi_parsed and multi_parsed.get("section_numbers"):
            sections_str = ", ".join(str(s) for s in multi_parsed["section_numbers"])
            act_str = multi_parsed.get("act_name") or "legislation"
            progress("legislation", f"Detected sections: {sections_str}",
                     detail=act_str, substep=True, step="parse")

        # Step 2: Choose search strategy
        if multi_parsed and len(multi_parsed["section_numbers"]) > 1:
            # --- Multi-section path ---
            search_mode = "multi_section"
            log.info("Multi-section query detected",
                     sections=multi_parsed["section_numbers"],
                     act=multi_parsed["act_name"])
            progress("legislation",
                     f"Searching {len(multi_parsed['section_numbers'])} sections in parallel...",
                     step="search")

            with log_time(log, "Multi-section ES retrieval"):
                result = await asyncio.to_thread(
                    _search_legislation_multi_section,
                    clean_query,
                    multi_parsed["section_numbers"],
                    multi_parsed["act_name"],
                )

            # Convert tool result format to raw ES hits format for downstream
            hits = []
            source_name = None
            for h in result["hits"]:
                hits.append({
                    "_source": {"page_content": h["content"], "source": h["source"]},
                    "_score": h["score"],
                })
                if not source_name:
                    source_name = h["source"]

        else:
            # --- Standard single-section path (use clean_query) ---
            progress("legislation", "Searching legislation database...", step="search")
            with log_time(log, "ES retrieval"):
                try:
                    hits, source_name = await asyncio.to_thread(_search_legislation, clean_query, named_acts)
                except Exception as es_err:
                    err_msg = str(es_err).lower()
                    # Query-shape failures ES can throw on long / complex
                    # queries: `too_many_clauses`, `maxClauseCount`,
                    # `compile error`, `class_cast_exception`. Any of these
                    # mean "this query is not answerable by ES as built" —
                    # let the empty-hits path below trigger topic-search
                    # and then web fallback instead of failing the whole
                    # agent with an opaque 400.
                    if any(k in err_msg for k in (
                        "maxclausecount", "too_many_clauses",
                        "compile error", "class_cast",
                    )):
                        log.warning("Legislation ES retrieval failed on query shape — "
                                    "falling through to topic/web fallback",
                                    error=str(es_err)[:200])
                        hits, source_name = [], None
                    else:
                        raise

        # Step 3: Topic fallback if no hits and no section was identified
        if not hits and not multi_parsed:
            search_mode = "topic"
            log.info("No section hits, trying topic-based search")
            progress("legislation", "No exact match — trying topic search...",
                     substep=True, step="search")
            with log_time(log, "Topic ES retrieval"):
                topic_result = await asyncio.to_thread(_search_legislation_by_topic, clean_query)

            if topic_result["hits"]:
                source_name = topic_result["source"]
                hits = []
                for h in topic_result["hits"]:
                    hits.append({
                        "_source": {"page_content": h["content"], "source": h["source"]},
                        "_score": h["score"],
                    })

        if hits:
            progress("legislation", f"Found {len(hits)} matching provisions",
                     found=len(hits), step="search")

        if not hits:
            # Phase 1: Query rewrite + retry
            progress("legislation", "No results — rewriting query...",
                     step="fallback")
            from core.agent_fallback import rewrite_query_for_domain, web_search_fallback
            rewritten = await asyncio.to_thread(rewrite_query_for_domain, query, "Legislation")
            if rewritten != query:
                progress("legislation", "Retrying with refined query...",
                         detail=rewritten[:80], substep=True, step="fallback")
                log.info("Retrying with rewritten query", rewritten=rewritten[:100])
                try:
                    retry_hits, retry_source = await asyncio.to_thread(_search_legislation, rewritten, named_acts)
                    if retry_hits:
                        hits, source_name = retry_hits, retry_source
                        log.info("Retry search succeeded", hit_count=len(hits))
                except Exception as retry_err:
                    log.warning("Retry search failed", error=str(retry_err))

            # Phase 2: Web search fallback if still empty
            if not hits:
                progress("legislation", "Searching the web for latest information...",
                         step="fallback")
                log.warning("All searches exhausted, using web fallback",
                            search_mode=search_mode)
                fallback_result = await web_search_fallback(
                    query, "Legislation", LEGISLATION_SYSTEM_PROMPT,
                    user_language=_user_language, intent=intent)
                fallback_result.retry_attempted = True
                return {"agent_results": {"Legislation": fallback_result}}

        # Step 3.5: Relevance gate — LLM-as-judge. See core/retrieval_relevance.py
        # for full rationale (LexRAG 2025 pattern). On reject, fall back to web.
        progress("legislation", "Verifying retrieval relevance...",
                 step="relevance_check")
        chunks_for_judge = [
            (h.get("_source") or {}).get("page_content", "") for h in hits
        ]
        is_relevant, judge_telemetry = await check_retrieval_relevance(
            query, chunks_for_judge, source_name, agent_name="Legislation",
        )
        log.info("Relevance judge verdict",
                 passed=is_relevant,
                 source=source_name,
                 search_mode=search_mode,
                 **judge_telemetry)

        if not is_relevant:
            log.warning("Retrieved hits failed relevance gate — "
                        "falling back to web search",
                        rejected_source=source_name,
                        **judge_telemetry)
            mismatch = judge_telemetry.get("matched_subject") or "different subject"
            progress("legislation",
                     f"Retrieved provisions don't match the query "
                     f"({mismatch[:60]}) — searching the web...",
                     step="fallback", substep=True)
            from core.agent_fallback import web_search_fallback
            fallback_result = await web_search_fallback(
                query, "Legislation", LEGISLATION_SYSTEM_PROMPT,
                user_language=_user_language, intent=intent)
            fallback_result.retry_attempted = True
            return {"agent_results": {"Legislation": fallback_result}}

        # Step 4: Convert hits to documents (cap each hit to 3000 chars, total to 30KB)
        _MAX_DOC_CHARS = 3000
        _MAX_TOTAL_CHARS = 30_000
        doc_parts = []
        total_len = 0
        for h in hits:
            chunk = h.get("_source", {}).get("page_content", "")
            if len(chunk) > _MAX_DOC_CHARS:
                chunk = chunk[:_MAX_DOC_CHARS] + "..."
            if total_len + len(chunk) > _MAX_TOTAL_CHARS:
                break
            doc_parts.append(chunk)
            total_len += len(chunk)
        docs_text = "\n\n".join(doc_parts)
        source_file = hits[0].get("_source", {}).get("source", "unknown") if hits else "unknown"
        source_display = os.path.splitext(os.path.basename(source_file))[0]

        log.debug("Generating response",
                  source=source_display, docs_count=len(hits),
                  docs_text_len=len(docs_text), search_mode=search_mode)

        # Step 5: Generate response (with 1 retry on disconnect)
        source_display_progress = os.path.splitext(os.path.basename(
            hits[0].get("_source", {}).get("source", "")
        ))[0] if hits else ""
        if source_display_progress:
            progress("legislation", f"Reading: {source_display_progress}",
                     substep=True, step="search")
        progress("legislation", "Generating response...", step="generate")
        with log_time(log, "LLM generation"):
            # max_output_tokens capped at 8192 (~32k chars) to bound responses
            # and prevent runaway markdown-table padding loops.
            llm = get_gemini_flash_full(temperature=0.1, max_output_tokens=8192)
            prompt = ChatPromptTemplate.from_messages([
                ("system", _system_prompt),
                MessagesPlaceholder(variable_name="chat_history", optional=True),
                ("user", "Legislation provisions:\n{docs}"),
                ("user", "Current Date: {date}"),
                ("user", "User Query: {query}"),
            ])
            chain = prompt | llm
            # For long queries: include user's pasted document in LLM generation
            gen_query = f"User's document/context:\n{user_context}\n\nUser's question:\n{query}" if user_context else query
            invoke_kwargs = {
                "query": gen_query,
                "docs": docs_text,
                "chat_history": chat_history,
                "date": str(date.today()),
            }

            from core.streaming import stream_chain_response
            response = await stream_chain_response(chain, invoke_kwargs)

        from core.token_tracker import record as _record_tokens
        tokens = _record_tokens("Legislation", "generate", response)

        # Check if LLM apologized (ES hits were irrelevant) — fall back to web search.
        # Uses the shared head+tail scanner so end-of-response hedges (after a
        # dutiful write-up of irrelevant retrievals) don't slip through.
        if head_tail_apology_detected(response.text):
            log.warning("LLM response is an apology or too short, using web fallback",
                        response_preview=response.text[:100], search_mode=search_mode)
            try:
                from langgraph.config import get_stream_writer
                writer = get_stream_writer()
                writer({"type": "token_reset"})
            except RuntimeError:
                pass
            from core.agent_fallback import web_search_fallback
            fallback_result = await web_search_fallback(
                query, "Legislation", LEGISLATION_SYSTEM_PROMPT,
                user_language=_user_language, intent=intent)
            fallback_result.tokens_consumed += tokens
            return {"agent_results": {"Legislation": fallback_result}}

        log.info("Agent completed",
                 source=source_display, response_len=len(response.text),
                 tokens=tokens, search_mode=search_mode)

        # Step 6: Build source metadata
        parsed_info = parse_section_info(query)
        sources = []
        for h in hits:
            hit_source = h["_source"].get("source", "unknown")
            hit_display = os.path.splitext(os.path.basename(hit_source))[0]
            sources.append(SourceMetadata(
                source_type="legislation",
                title=hit_display,
                content=[h["_source"]["page_content"][:300]],
                file_name=hit_source,
                agent_name="Legislation",
                relevance_score=h.get("_score"),
                section_number=parsed_info["section_number"] if parsed_info else None,
                act_name=parsed_info["act_name"] if parsed_info else hit_display,
            ))

        result = AgentResult(
            agent_name="Legislation",
            content=response.text,
            sources=sources,
            tokens_consumed=tokens,
        )

    except Exception as e:
        from core.metrics import record_agent_error
        record_agent_error("Legislation", e)
        log.error("Agent failed", error=short_err(e), exc_info=True)
        result = AgentResult(
            agent_name="Legislation",
            content="",
            sources=[],
            tokens_consumed=0,
            error=short_err(e),
        )

    return {"agent_results": {"Legislation": result}}
