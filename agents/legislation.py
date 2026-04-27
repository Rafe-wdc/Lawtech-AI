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
from core.settings import ES_INDICES
from core.language import localize_prompt
from core.logger import get_logger, log_time
from config.prompts import LEGISLATION_SYSTEM_PROMPT

from tools.inline.section_parser import parse_section_info, parse_multi_section_info
from tools.shared.elasticsearch_tools import (
    _create_search_variations,
    _search_legislation_by_topic,
    _search_legislation_multi_section,
)
from core.progress import progress

log = get_logger("Legislation")


# --- Match Phrase Extraction ---

class QueryMetadata(BaseModel):
    match_phrase: Optional[str] = Field(None, description="match_phrase clause")
    sub_part: Optional[str] = Field(None, description="Sub part of act with name")


MATCH_PHRASE_PROMPT = """You are a legal document search expert.

Given:
- An act title,
- A legal provision context,
- A user query,

You must:
- Use the provision type (e.g., Rule, Section) from the context.
- Use the provision number from the user query.
- Combine them with the act title to create a normalized match_phrase value.
- Also output the subpart (e.g., "Rule 1").

Title: "{title}"
Context: {text}
User Query: "{query}"

Return JSON only:
{{"match_phrase": "<context type + user number + title>", "sub_part": "<context type + user number>"}}"""


def _extract_match_phrase(query: str, text: str, title: str) -> QueryMetadata:
    """Use Gemini Flash Lite to extract a match phrase for targeted ES search."""
    llm = get_gemini_flash(temperature=0.1).with_structured_output(
        QueryMetadata, include_raw=True,
    )
    prompt = ChatPromptTemplate.from_template(MATCH_PHRASE_PROMPT)
    chain = prompt | llm
    raw_and_parsed = chain.invoke({'query': query, 'text': text, 'title': title})
    from core.token_tracker import record as _record_tokens
    _record_tokens("Legislation", "extract_match_phrase", raw_and_parsed.get("raw"))
    return raw_and_parsed["parsed"]


# --- Act Name Extraction from Query ---

import re as _re

# Common Indian act names to look for in queries
_ACT_PATTERNS = [
    r"(Consumer Protection Act[,\s]*\d*)",
    r"(Income Tax Act[,\s]*\d*)",
    r"(Companies Act[,\s]*\d*)",
    r"(Negotiable Instruments Act[,\s]*\d*)",
    r"(Motor Vehicles Act[,\s]*\d*)",
    r"(RERA|Real Estate[^\.\n]*Act[,\s]*\d*)",
    r"(Arbitration[^\.\n]*Act[,\s]*\d*)",
    r"(POCSO[^\.\n]*Act[,\s]*\d*)",
    r"(Information Technology Act[,\s]*\d*)",
    r"(GST Act[,\s]*\d*|Goods and Services Tax[^\.\n]*Act[,\s]*\d*)",
    r"(Hindu Marriage Act[,\s]*\d*)",
    r"(Hindu Succession Act[,\s]*\d*)",
    r"(Transfer of Property Act[,\s]*\d*)",
    r"(Indian Contract Act[,\s]*\d*)",
    r"(Specific Relief Act[,\s]*\d*)",
    r"(Limitation Act[,\s]*\d*)",
    r"(Registration Act[,\s]*\d*)",
    r"(Rent Control Act[,\s]*\d*)",
    r"(Domestic Violence[^\.\n]*Act[,\s]*\d*)",
    r"(Insolvency[^\.\n]*Act[,\s]*\d*|IBC[,\s]*\d*)",
]


def _extract_act_name_from_query(query: str) -> str | None:
    """Extract a specific act name from the query if one is mentioned.

    Returns the first act name found, or None if no specific act is mentioned.
    """
    for pattern in _ACT_PATTERNS:
        match = _re.search(pattern, query, _re.IGNORECASE)
        if match:
            act_name = match.group(1).strip().rstrip(",")
            return act_name
    return None


# --- Elasticsearch Search (single-section path) ---

def _search_legislation(query: str) -> tuple[list[dict], str | None]:
    """Multi-query ES search for legislation. Returns (hits, most_common_source)."""
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
            response = es.search(index=index, body=es_query)
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

    # When the query explicitly names an act, prefer sources matching that act name
    # This prevents "Consumer Protection Act 2019" matching "Medical Service Personnel" act
    act_keywords_in_query = _extract_act_name_from_query(query)
    if act_keywords_in_query:
        matching_sources = [
            (src, score) for src, score in sources_counter.most_common()
            if act_keywords_in_query.lower() in src.lower()
        ]
        if matching_sources:
            most_common_source = matching_sources[0][0]
            log.info("Act-name matched source preferred",
                     act_hint=act_keywords_in_query, source=most_common_source)
        else:
            most_common_source = sources_counter.most_common(1)[0][0]
    else:
        most_common_source = sources_counter.most_common(1)[0][0]

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

        source_query = {
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

    source_response = es.search(index=index, body=source_query)
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
    _system_prompt = localize_prompt(LEGISLATION_SYSTEM_PROMPT, state.get("user_language", "en"))
    log.info("Agent started", query=query[:100],
             using_agent_query="Legislation" in agent_queries)

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
                hits, source_name = await asyncio.to_thread(_search_legislation, clean_query)

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
                    retry_hits, retry_source = await asyncio.to_thread(_search_legislation, rewritten)
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
                    query, "Legislation", _system_prompt)
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
            llm = get_gemini_flash_full(temperature=0.1)
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

        # Check if LLM apologized (ES hits were irrelevant) — fall back to web search
        _sorry_patterns = [
            "i am sorry", "i'm sorry", "does not contain", "no information",
            "not contain information", "cannot find", "no relevant",
            "cannot provide information", "no direct information",
            "no specific information", "no information available",
            "unable to find", "could not find", "not available in",
            "there is no", "does not have information",
            "based on the provided agent",
        ]
        content_lower = response.content.lower()[:300]
        if any(p in content_lower for p in _sorry_patterns) or len(response.content) < 50:
            log.warning("LLM response is an apology or too short, using web fallback",
                        response_preview=response.content[:100], search_mode=search_mode)
            try:
                from langgraph.config import get_stream_writer
                writer = get_stream_writer()
                writer({"type": "token_reset"})
            except RuntimeError:
                pass
            from core.agent_fallback import web_search_fallback
            fallback_result = await web_search_fallback(query, "Legislation", LEGISLATION_SYSTEM_PROMPT)
            fallback_result.tokens_consumed += tokens
            return {"agent_results": {"Legislation": fallback_result}}

        log.info("Agent completed",
                 source=source_display, response_len=len(response.content),
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
            content=response.content,
            sources=sources,
            tokens_consumed=tokens,
        )

    except Exception as e:
        log.error("Agent failed", error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="Legislation",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )

    return {"agent_results": {"Legislation": result}}
