"""Agent #5 — Judgment Agent

Searches court judgments using smart multi-strategy ES queries.
Extracts structured case metadata and generates S3 PDF links.

Handles: "State vs Doe 2020", "SC cases on anticipatory bail",
         "bail cases under section 438", "Kirloskar vs Kirloskar", etc.

Uses: GPT-4o (metadata extraction), Gemini Flash (response generation)
Data Source: Elasticsearch "judgements" index + AWS S3

Search Strategies (tried in order of specificity):
1. Citation / case number direct lookup
2. Both party names (exact → no-year → swapped → fuzzy)
3. Single party name
4. Case type specialized (bail, quashing, writ, appeal, etc.)
5. Legal provision (acts/sections invoked)
6. Multi-tier (topics + acts + lexical BM25)
7. Relaxed full-text fallback
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import get_gpt4o, get_gemini_flash
from core.language import localize_prompt
from core.logger import get_logger, log_time
from core.settings import TIMEOUT_ES_PARALLEL_SEC, TIMEOUT_METADATA_SEC
from config.prompts import JUDGMENT_SYSTEM_PROMPT
from tools.shared.judgment_search import (
    smart_judgment_search,
    detect_citation,
    detect_case_type,
)
from tools.shared.storage_tools import generate_s3_link
from tools.shared.llm_tools import CaseMetadata

log = get_logger("Judgment")


# --- Case Metadata Extraction ---

METADATA_EXTRACTION_PROMPT = """You are an expert in legal text parsing and Elasticsearch query formulation.

Extract structured metadata from the user query and generate a normalized lexical search string.

Return valid JSON with these keys:
- court_name: string (lowercase, e.g., "supreme court", "bombay high court")
- petitioner_names: list of strings
- respondent_names: list of strings
- year: integer or null
- topics: list of legal topics
- acts_or_sections: list of provisions (e.g., "section 138 ni act")
- lexical_query: normalized BM25 search string
- size: integer (1-50)

Rules:
1. Normalize court names to lowercase, remove "of India", "at"
2. Party before "vs"/"versus" = petitioner, after = respondent
3. Normalize provisions: lowercase, remove "of"/"under", join with spaces
4. lexical_query: combine petitioner vs respondent year, or acts + topics
5. Remove filler words: "give me", "find", "show", "tell", "latest"
6. size: both petitioner & respondent → 1, acts_or_sections → 5, topics only → 10, default → 3

User query: {query}

Output only valid JSON."""


def _extract_case_metadata(query: str) -> CaseMetadata:
    """Use GPT-4o to extract structured metadata from a judgment query."""
    with log_time(log, "Case metadata extraction"):
        llm = get_gpt4o().with_structured_output(CaseMetadata)
        prompt = ChatPromptTemplate.from_template(METADATA_EXTRACTION_PROMPT)
        chain = prompt | llm
        result = chain.invoke({"query": query})

    log.info("Metadata extracted",
             court=result.court_name,
             petitioners=result.petitioner_names,
             respondents=result.respondent_names,
             year=result.year,
             topics=result.topics,
             size=result.size)
    return result


_LEGAL_TOPICS = [
    "bail", "anticipatory bail", "murder", "rape", "cheating", "fraud",
    "negligence", "contract", "property", "divorce", "custody", "contempt",
    "quashing", "injunction", "defamation", "accident", "compensation",
    "writ", "habeas corpus", "mandamus", "eviction", "arbitration",
]


def _judgment_regex_fallback(query: str) -> CaseMetadata:
    """Regex-based fallback when GPT-4o metadata extraction fails or times out.

    Extracts party names, year, court, and legal topics without any LLM call.
    Deliberately conservative — returns broad search params to avoid missing results.
    """
    query_lower = query.lower()

    # Party names: "X vs Y" or "X versus Y"
    petitioners: list[str] = []
    respondents: list[str] = []
    vs_match = re.search(
        r'(.+?)\s+(?:vs?\.?|versus)\s+(.+?)(?:\s+\d{4}|\s*$)',
        query, re.IGNORECASE,
    )
    if vs_match:
        petitioners = [vs_match.group(1).strip()[:80]]
        respondents = [vs_match.group(2).strip()[:80]]

    # Year: 4-digit number 1900-2099
    year: int | None = None
    yr_match = re.search(r'\b(19|20)\d{2}\b', query)
    if yr_match:
        year = int(yr_match.group(0))

    # Court name
    court_name: str | None = None
    if "supreme court" in query_lower or re.search(r'\bsc\b', query_lower):
        court_name = "supreme court"
    elif "high court" in query_lower:
        hc = re.search(r'(\w+)\s+high court', query_lower)
        court_name = f"{hc.group(1)} high court" if hc else "high court"

    # Legal topics
    topics = [t for t in _LEGAL_TOPICS if t in query_lower]

    # Lexical query: combine key tokens
    lexical_parts = []
    if petitioners:
        lexical_parts.append(petitioners[0])
    if respondents:
        lexical_parts.append("vs")
        lexical_parts.append(respondents[0])
    if year:
        lexical_parts.append(str(year))
    lexical = " ".join(lexical_parts) if lexical_parts else query[:100]

    # size: narrow if both parties known, else broader
    size = 1 if (petitioners and respondents) else (5 if topics else 10)

    log.info("Regex fallback metadata",
             petitioners=petitioners, respondents=respondents,
             year=year, court=court_name, topics=topics, size=size)

    return CaseMetadata(
        petitioner_names=petitioners,
        respondent_names=respondents,
        year=year,
        court_name=court_name,
        topics=topics,
        acts_or_sections=[],
        lexical_query=lexical,
        size=size,
    )


# --- Hit Processing ---

def _process_hits(hits: list[dict]) -> tuple[list[str], list[SourceMetadata]]:
    """Convert smart search hits to doc texts and SourceMetadata list."""
    docs_text_parts = []
    sources = []

    for hit in hits:
        content = hit["content"]
        court = hit.get("court_name", "")
        source_file = hit.get("source", "unknown")
        year = hit.get("year")
        keywords = hit.get("keywords", [])
        acts_sections = hit.get("acts_or_sections_invoked", [])
        score = hit.get("score")

        petitioner = hit.get("petitioner_names", ["Unknown"])
        respondent = hit.get("respondent_names", ["Unknown"])
        # Handle string, list, or other types from ES
        if isinstance(petitioner, str):
            pet_name = petitioner or "Unknown"
        elif isinstance(petitioner, list):
            pet_name = petitioner[0] if petitioner else "Unknown"
        else:
            pet_name = str(petitioner) if petitioner else "Unknown"
        if isinstance(respondent, str):
            resp_name = respondent or "Unknown"
        elif isinstance(respondent, list):
            resp_name = respondent[0] if respondent else "Unknown"
        else:
            resp_name = str(respondent) if respondent else "Unknown"
        title = f"{pet_name} vs {resp_name}"

        docs_text_parts.append(content)

        # Generate S3 link
        file_str = str(source_file) if isinstance(source_file, dict) else source_file
        s3_link = generate_s3_link(court, file_str, title) if court and file_str else None

        sources.append(SourceMetadata(
            source_type="judgment",
            title=title,
            content=[content[:300]],
            file_name=file_str,
            doc_link=s3_link,
            agent_name="Judgment",
            relevance_score=score,
            court_name=court,
            year=year,
            petitioner_names=petitioner if isinstance(petitioner, list) else [petitioner],
            respondent_names=respondent if isinstance(respondent, list) else [respondent],
            keywords=keywords if isinstance(keywords, list) else [],
            acts_or_sections_invoked=acts_sections if isinstance(acts_sections, list) else [],
        ))

    return docs_text_parts, sources


# --- Agent Node ---

async def judgment_node(state: LegalAgentState) -> dict:
    """Search judgments and generate response with case citations.

    Flow:
    1. Extract structured metadata from query (GPT-4o)
    2. Run smart multi-strategy ES search (citation → party → case type → topic → fallback)
    3. Generate S3 PDF links for each hit
    4. Generate response with citations (Gemini Flash, streaming)
    """
    agent_queries = state.get("agent_queries", {})
    # Prefer agent-specific query > normalized English query > original (for multilingual support)
    query = agent_queries.get("Judgment", state.get("query", state.get("original_query", "")))
    original_query = state.get("original_query", query)
    chat_history = state.get("chat_history", [])
    _system_prompt = localize_prompt(JUDGMENT_SYSTEM_PROMPT, state.get("user_language", "en"))
    log.info("Agent started", query=query[:100],
             using_agent_query="Judgment" in agent_queries)

    try:
        # Steps 1 + 2: Run metadata extraction and preliminary ES search in parallel.
        # Preliminary search uses regex-fallback metadata (fast) while GPT-4o extracts
        # richer metadata. If preliminary hits are found, we skip the refined search.
        def _preliminary_search():
            prelim_meta = _judgment_regex_fallback(query)
            return smart_judgment_search(
                query=query,
                petitioner=(prelim_meta.petitioner_names or [""])[0],
                respondent=(prelim_meta.respondent_names or [""])[0],
                year=prelim_meta.year,
                court=prelim_meta.court_name,
                topics=prelim_meta.topics,
                acts_or_sections=prelim_meta.acts_or_sections,
                lexical_query=prelim_meta.lexical_query or "",
                size=min(prelim_meta.size or 10, 50),
            )

        with log_time(log, "Parallel metadata + ES search"):
            metadata_task = asyncio.create_task(
                asyncio.to_thread(_extract_case_metadata, query)
            )
            es_task = asyncio.create_task(asyncio.to_thread(_preliminary_search))

            done, _ = await asyncio.wait(
                [metadata_task, es_task],
                timeout=TIMEOUT_ES_PARALLEL_SEC,
                return_when=asyncio.ALL_COMPLETED,
            )

        # Resolve metadata (prefer GPT-4o result, fall back to regex)
        try:
            metadata = metadata_task.result() if metadata_task in done else _judgment_regex_fallback(query)
        except Exception as meta_err:
            log.warning("Metadata extraction failed, using regex fallback", error=str(meta_err))
            metadata = _judgment_regex_fallback(query)

        # Resolve preliminary ES result
        try:
            prelim_result = es_task.result() if es_task in done else {"hits": [], "strategy_used": "none", "strategies_tried": []}
        except Exception:
            prelim_result = {"hits": [], "strategy_used": "none", "strategies_tried": []}

        # If preliminary search found hits, use them directly
        if prelim_result["hits"]:
            search_result = prelim_result
            log.info("Preliminary ES search hit — skipping refined search",
                     strategy=search_result["strategy_used"])
        else:
            # Preliminary missed — do refined search with full GPT-4o metadata
            with log_time(log, "Refined ES search with metadata"):
                search_result = await asyncio.to_thread(
                    smart_judgment_search,
                    query=query,
                    petitioner=(metadata.petitioner_names or [""])[0],
                    respondent=(metadata.respondent_names or [""])[0],
                    year=metadata.year,
                    court=metadata.court_name,
                    topics=metadata.topics,
                    acts_or_sections=metadata.acts_or_sections,
                    lexical_query=metadata.lexical_query or "",
                    size=min(metadata.size or 10, 50),
                )

        hits = search_result["hits"]
        strategy = search_result["strategy_used"]
        strategies_tried = search_result["strategies_tried"]

        if not hits:
            # Phase 1: Query rewrite + retry
            from core.agent_fallback import rewrite_query_for_domain, web_search_fallback
            rewritten = await asyncio.to_thread(rewrite_query_for_domain, query, "Judgment")
            if rewritten != query:
                log.info("Retrying with rewritten query", rewritten=rewritten[:100])
                try:
                    retry_result = await asyncio.to_thread(
                        smart_judgment_search,
                        query=rewritten,
                        petitioner=(metadata.petitioner_names or [""])[0],
                        respondent=(metadata.respondent_names or [""])[0],
                        year=metadata.year,
                        court=metadata.court_name,
                        topics=metadata.topics,
                        acts_or_sections=metadata.acts_or_sections,
                        lexical_query=rewritten,
                        size=min(metadata.size or 10, 50),
                    )
                    hits = retry_result["hits"]
                    if hits:
                        strategy = retry_result["strategy_used"]
                        log.info("Retry search succeeded", hit_count=len(hits))
                except Exception as retry_err:
                    log.warning("Retry search failed", error=str(retry_err))

            # Phase 2: Web search fallback if still empty
            if not hits:
                log.warning("All searches exhausted, using web fallback",
                            strategies_tried=strategies_tried)
                fallback_result = await web_search_fallback(
                    query, "Judgment", _system_prompt, original_query=original_query)
                fallback_result.retry_attempted = True
                return {"agent_results": {"Judgment": fallback_result}}

        log.info("ES results found",
                 hit_count=len(hits),
                 top_score=hits[0].get("score", 0),
                 strategy=strategy,
                 strategies_tried=len(strategies_tried))

        # Step 3: Process hits into docs + source metadata
        docs_text_parts, sources = _process_hits(hits)
        first_title = sources[0].title if sources else ""

        # Step 4: Generate response
        docs_text = "\n\n".join(docs_text_parts)

        with log_time(log, "LLM generation"):
            llm = get_gemini_flash(temperature=0.1)
            prompt = ChatPromptTemplate.from_messages([
                ("system", _system_prompt),
                MessagesPlaceholder(variable_name="chat_history", optional=True),
                ("user", "Court Judgments:\n{docs}"),
                ("user", "Current Date: {date}"),
                ("user", "User Query: {query}"),
            ])
            chain = prompt | llm

            from core.streaming import stream_chain_response
            llm_response = await stream_chain_response(chain, {
                "query": query,
                "docs": docs_text,
                "chat_history": chat_history,
                "date": str(date.today()),
            })

        tokens = 0
        if hasattr(llm_response, "usage_metadata") and llm_response.usage_metadata:
            tokens = llm_response.usage_metadata.get("total_tokens", 0)

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
        content_lower = llm_response.content.lower()[:300]
        if any(p in content_lower for p in _sorry_patterns) or len(llm_response.content) < 50:
            log.warning("LLM response is an apology or too short, using web fallback",
                        response_preview=llm_response.content[:100], strategy=strategy)
            try:
                from langgraph.config import get_stream_writer
                writer = get_stream_writer()
                writer({"type": "token_reset"})
            except RuntimeError:
                pass
            from core.agent_fallback import web_search_fallback
            fallback_result = await web_search_fallback(query, "Judgment", _system_prompt)
            fallback_result.tokens_consumed += tokens
            return {"agent_results": {"Judgment": fallback_result}}

        log.info("Agent completed",
                 cases=len(hits), response_len=len(llm_response.content),
                 tokens=tokens, strategy=strategy,
                 first_case=first_title[:60])

        result = AgentResult(
            agent_name="Judgment",
            content=llm_response.content,
            sources=sources,
            tokens_consumed=tokens,
        )

    except Exception as e:
        log.error("Agent failed", error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="Judgment",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )

    return {"agent_results": {"Judgment": result}}
