"""Agent #4 — Legislation Agent

Retrieves and explains central/state legislation from Elasticsearch.

Handles: "Section 15 of Companies Act", "Consumer Protection Act refunds", etc.

Uses: Gemini Flash Lite (generation) + GPT-4o-mini (match phrase extraction)
Data Source: Elasticsearch "legislation" index
"""

from __future__ import annotations

import os
import re
from datetime import date
from collections import Counter
from typing import Optional

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import get_es_client, get_gpt4o_mini, get_gemini_flash
from core.settings import ES_INDICES
from core.logger import get_logger, log_time
from config.prompts import LEGISLATION_SYSTEM_PROMPT

log = get_logger("Legislation")


# --- Section Parsing (migrated from v1 legislation_retriever.py) ---

SECTION_TYPES = [
    'section', 'rule', 'rules', 'order', 'regulation', 'scheme', 'procedure',
    'policy', 'article', 'condition', 'statute', 'ordinance', 'standing',
    'rule and regulation', 'bye-law', 'bye-laws', 'bye law', 'bye laws',
    'by-law', 'plan', 'niyam', 'adhiniyam', 'code', 'notification',
    'instruction', 'manual', 'licence', 'roster', 'process', 'tariff',
    'regulatory', 'schedule', 'function', 'byelaw', 'byelaws', 'direction',
    'guideline', 'criteria', 'law', 'clause', 'board standing order',
]


def _extract_section_info(query: str) -> dict | None:
    """Extract section type, number, and act name from natural language query."""
    query_lower = query.lower().strip()

    # Create regex pattern (longest first to avoid partial matches)
    section_pattern = '|'.join(
        re.escape(st) for st in sorted(SECTION_TYPES, key=len, reverse=True)
    )
    number_pattern = r'(\d+[a-z]*|\b[ivx]+\b|[a-z]\d*|\d+[a-z]\d*)'

    section_matches = re.findall(
        rf'({section_pattern})\s+{number_pattern}', query_lower
    )

    if not section_matches:
        return None

    section_type, section_number = section_matches[0]

    # Extract act/law name
    act_indicators = r'(?:act|rule|law|code|regulation|ordinance|statute|scheme|policy|manual|notification)'
    act_patterns = [
        rf'of\s+(?:the\s+)?(.+?{act_indicators}[^,]*)',
        rf'(.+?{act_indicators}[^,]*?).*?{section_type}',
        rf'([\w\s]+{act_indicators}[\w\s]*)',
    ]

    act_name = None
    for pattern in act_patterns:
        match = re.search(pattern, query_lower)
        if match:
            act_name = match.group(1).strip()
            act_name = re.sub(r'^\s*(of\s+(?:the\s+)?|the\s+)', '', act_name)
            act_name = re.sub(r'\s*,\s*\d{4}.*', '', act_name)
            break

    parsed = {
        'section_type': section_type,
        'section_number': section_number,
        'act_name': act_name or '',
    }
    log.debug("Section info extracted", **parsed)
    return parsed


def _create_search_variations(original_query: str, parsed_info: dict | None) -> list[str]:
    """Create multiple search query variations."""
    queries = [original_query]

    if parsed_info:
        st = parsed_info['section_type']
        sn = parsed_info['section_number']
        act = parsed_info['act_name']

        queries.extend([
            f"{st} {sn} of {act}",
            f"{st} {sn} of the {act}",
            f"{st.title()} {sn} of {act.title()}",
            f"{st.title()} {sn} of the {act.title()}",
        ])

        if st == 'rule':
            queries.append(f"rules {sn} of {act}")
        elif st == 'rules':
            queries.append(f"rule {sn} of {act}")

    # Deduplicate preserving order
    seen = set()
    unique = []
    for q in queries:
        q_clean = re.sub(r'\s+', ' ', q.strip().lower())
        if q_clean not in seen:
            seen.add(q_clean)
            unique.append(q)

    log.debug("Search variations created", count=len(unique))
    return unique


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
    """Use GPT-4o-mini to extract a match phrase for targeted ES search."""
    llm = get_gpt4o_mini().with_structured_output(QueryMetadata)
    prompt = ChatPromptTemplate.from_template(MATCH_PHRASE_PROMPT)
    chain = prompt | llm
    return chain.invoke({'query': query, 'text': text, 'title': title})


# --- Elasticsearch Search ---

def _search_legislation(query: str) -> tuple[list[dict], str | None]:
    """Multi-query ES search for legislation. Returns (hits, most_common_source)."""
    es = get_es_client()
    index = ES_INDICES["legislation"]

    parsed_info = _extract_section_info(query)
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
    1. Parse section type/number/act name from query
    2. Multi-query search in ES "legislation" index
    3. Find most relevant source document
    4. Targeted sub-search within that source
    5. Generate response using retrieved statute text
    """
    query = state.get("query", state["original_query"])
    chat_history = state.get("chat_history", [])
    log.info("Agent started", query=query[:100])

    try:
        with log_time(log, "ES retrieval"):
            hits, source_name = _search_legislation(query)

        if not hits:
            log.warning("No results found")
            return {
                "agent_results": {"Legislation": AgentResult(
                    agent_name="Legislation",
                    content="",
                    sources=[],
                    tokens_consumed=0,
                )},
            }

        # Convert hits to documents
        docs_text = "\n\n".join(h["_source"]["page_content"] for h in hits)
        source_file = hits[0]["_source"].get("source", "unknown")
        source_display = os.path.splitext(os.path.basename(source_file))[0]

        log.debug("Generating response",
                  source=source_display, docs_count=len(hits),
                  docs_text_len=len(docs_text))

        # Generate response
        with log_time(log, "LLM generation"):
            llm = get_gemini_flash(temperature=0.1)
            prompt = ChatPromptTemplate.from_messages([
                ("system", LEGISLATION_SYSTEM_PROMPT),
                MessagesPlaceholder(variable_name="chat_history", optional=True),
                ("user", "Legislation provisions:\n{docs}"),
                ("user", "Current Date: {date}"),
                ("user", "User Query: {query}"),
            ])
            chain = prompt | llm

            response = chain.invoke({
                "query": query,
                "docs": docs_text,
                "chat_history": chat_history,
                "date": str(date.today()),
            })

        tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            tokens = response.usage_metadata.get("total_tokens", 0)

        log.info("Agent completed",
                 source=source_display, response_len=len(response.content),
                 tokens=tokens)

        result = AgentResult(
            agent_name="Legislation",
            content=response.content,
            sources=[SourceMetadata(
                title=source_display,
                content=[h["_source"]["page_content"][:200] for h in hits],
                file_name=source_file,
            )],
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
