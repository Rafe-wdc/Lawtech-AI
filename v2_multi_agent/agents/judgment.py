"""Agent #5 — Judgment Agent

Searches court judgments using multi-tier ES queries.
Extracts structured case metadata and generates S3 PDF links.

Handles: "State vs Doe 2020", "SC cases on anticipatory bail", etc.

Uses: GPT-4o (metadata extraction + response generation)
Data Source: Elasticsearch "judgements" index + AWS S3
"""

from __future__ import annotations

import os
from datetime import date
from typing import Optional, List

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import get_es_client, get_gpt4o, get_gemini_flash
from core.settings import ES_INDICES, S3_BUCKET, S3_REGION
from config.prompts import JUDGMENT_SYSTEM_PROMPT


# --- Case Metadata Extraction (migrated from v1 judgement_retriever.py) ---

class CaseMetadata(BaseModel):
    court_name: Optional[str] = Field(
        default=None,
        description="Name of the court (supreme court, [location] high court, tribunal, etc.)",
    )
    petitioner_names: Optional[List[str]] = Field(
        default_factory=list,
        description="List of petitioner names",
    )
    respondent_names: Optional[List[str]] = Field(
        default_factory=list,
        description="List of respondent names",
    )
    year: Optional[int] = Field(None, description="4-digit year if mentioned")
    topics: Optional[List[str]] = Field(
        default_factory=list,
        description="Legal topics (e.g., 'murder', 'bail', 'negligence')",
    )
    acts_or_sections: Optional[List[str]] = Field(
        default_factory=list,
        description="Legal provisions (e.g., 'section 138 ni act')",
    )
    lexical_query: Optional[str] = Field(
        None,
        description="Normalized lexical search string for ES BM25",
    )
    size: Optional[int] = Field(
        3,
        description="Number of results to retrieve (1-50)",
    )


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
    llm = get_gpt4o().with_structured_output(CaseMetadata)
    prompt = ChatPromptTemplate.from_template(METADATA_EXTRACTION_PROMPT)
    chain = prompt | llm
    return chain.invoke({"query": query})


# --- Multi-Tier ES Query Builder ---

def _build_judgment_query(metadata: CaseMetadata, query_text: str) -> dict:
    """Build a multi-tier ES query for judgments.

    Tiers:
    1. Exact party match (petitioner + respondent, boost 20)
    2. Topic-based (keywords field, boost 15)
    3. Statutory reference (acts_or_sections_invoked field, boost 15)
    4. Lexical fallback (page_content, boost 8)
    5. Full-text fallback (page_content, boost 3)
    """
    should_clauses = []
    filters = []

    petitioner_names = metadata.petitioner_names or []
    respondent_names = metadata.respondent_names or []
    topics = metadata.topics or []
    acts_or_sections = metadata.acts_or_sections or []
    lexical_query = metadata.lexical_query or query_text

    # TIER 1: Exact Party Match
    if petitioner_names and respondent_names:
        for petitioner in petitioner_names:
            for respondent in respondent_names:
                should_clauses.append({
                    "bool": {
                        "must": [
                            {"match": {"petitioner_names": {"query": petitioner, "boost": 20, "minimum_should_match": "95%"}}},
                            {"match": {"respondent_names": {"query": respondent, "boost": 20, "minimum_should_match": "95%"}}},
                        ]
                    }
                })

    # TIER 2: Topics
    for topic in topics:
        should_clauses.append(
            {"match": {"keywords": {"query": topic, "boost": 15, "minimum_should_match": "95%"}}}
        )

    # TIER 3: Acts / Sections
    for section in acts_or_sections:
        should_clauses.append(
            {"match": {"acts_or_sections_invoked": {"query": section, "boost": 15, "minimum_should_match": "95%"}}}
        )

    # TIER 4: Lexical Query
    if lexical_query:
        should_clauses.append(
            {"match": {"page_content": {"query": lexical_query, "boost": 8, "minimum_should_match": "95%"}}}
        )

    # TIER 5: Full-text fallback
    if query_text and not lexical_query:
        should_clauses.append(
            {"match": {"page_content": {"query": query_text, "boost": 3}}}
        )

    # Filters
    if metadata.year:
        filters.append({"term": {"year": metadata.year}})
    if metadata.court_name:
        filters.append({"term": {"court_name": metadata.court_name.lower()}})

    return {
        "size": min(metadata.size or 3, 50),
        "query": {
            "bool": {
                "should": should_clauses,
                "filter": filters if filters else [],
                "minimum_should_match": 1,
            }
        },
    }


# --- S3 Link Generation ---

def _generate_s3_link(court: str, file_name: str, title: str) -> str:
    """Generate an S3 public URL for a judgment PDF."""
    s3_root = (court or "").strip().lower()
    file_base = os.path.basename(file_name.replace("\\", "/"))

    if s3_root == "supreme":
        s3_key = f"{s3_root}/{title}.pdf"
    else:
        s3_key = f"{s3_root}/{file_base}"

    return f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{s3_key}"


# --- Agent Node ---

async def judgment_node(state: LegalAgentState) -> dict:
    """Search judgments and generate response with case citations.

    Flow:
    1. Extract structured metadata from query (GPT-4o)
    2. Build multi-tier ES query
    3. Search ES "judgements" index
    4. Generate S3 PDF links
    5. Generate response with citations
    """
    query = state.get("query", state["original_query"])
    chat_history = state.get("chat_history", [])
    print(f"[Judgment] Searching cases for: {query[:80]}...")

    try:
        # Step 1: Extract metadata
        metadata = _extract_case_metadata(query)
        print(f"[Judgment] Metadata: court={metadata.court_name}, "
              f"parties={metadata.petitioner_names} vs {metadata.respondent_names}, "
              f"topics={metadata.topics}")

        # Step 2: Build and execute query
        es_query = _build_judgment_query(metadata, query)
        es = get_es_client()
        response = es.options(request_timeout=50).search(
            index=ES_INDICES["judgments"], body=es_query
        )

        hits = response["hits"]["hits"]
        if not hits:
            print("[Judgment] No results found")
            return {
                "agent_results": {"Judgment": AgentResult(
                    agent_name="Judgment",
                    content="",
                    sources=[],
                    tokens_consumed=0,
                )},
            }

        # Step 3: Convert hits to documents + extract metadata
        docs_text_parts = []
        sources = []
        first_court = ""
        first_file = ""
        first_title = ""

        for hit in hits:
            src = hit["_source"]
            content = src["page_content"]
            court = src.get("court_name", "")
            source_file = src.get("source", "unknown")

            petitioner = src.get("petitioner_names", ["Unknown"])
            respondent = src.get("respondent_names", ["Unknown"])
            pet_name = petitioner[0] if petitioner else "Unknown"
            resp_name = respondent[0] if respondent else "Unknown"
            title = f"{pet_name} vs {resp_name}"

            docs_text_parts.append(content)

            if not first_court:
                first_court = court
                first_file = source_file if isinstance(source_file, str) else str(source_file)
                first_title = title

            sources.append(SourceMetadata(
                title=title,
                content=[content[:200]],
                file_name=str(source_file) if isinstance(source_file, dict) else source_file,
            ))

        # Step 4: Generate S3 link for first result
        doc_link = None
        if first_court and first_file:
            doc_link = _generate_s3_link(first_court, first_file, first_title)
            print(f"[Judgment] S3 Link: {doc_link}")
            if sources:
                sources[0].doc_link = doc_link

        # Step 5: Generate response
        docs_text = "\n\n".join(docs_text_parts)
        llm = get_gemini_flash(temperature=0.1)
        prompt = ChatPromptTemplate.from_messages([
            ("system", JUDGMENT_SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("user", "Court Judgments:\n{docs}"),
            ("user", "Current Date: {date}"),
            ("user", "User Query: {query}"),
        ])
        chain = prompt | llm

        llm_response = chain.invoke({
            "query": query,
            "docs": docs_text,
            "chat_history": chat_history,
            "date": str(date.today()),
        })

        tokens = 0
        if hasattr(llm_response, "usage_metadata") and llm_response.usage_metadata:
            tokens = llm_response.usage_metadata.get("total_tokens", 0)

        result = AgentResult(
            agent_name="Judgment",
            content=llm_response.content,
            sources=sources,
            tokens_consumed=tokens,
        )

    except Exception as e:
        print(f"[Judgment] Error: {e}")
        result = AgentResult(
            agent_name="Judgment",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )

    return {"agent_results": {"Judgment": result}}
