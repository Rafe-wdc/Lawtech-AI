"""Agent #6 — Newacts Agent

Handles queries about the 6 core Indian codes:
BNS, BNSS, BSA (new) ↔ IPC, CrPC, IEA (old)

Supports section lookup, old↔new law mapping, and hybrid vector search.

Uses: GPT-4o (metadata extraction), Gemini Flash Lite (generation)
Data Source: Elasticsearch "newacts_v1" index
"""

from __future__ import annotations

from datetime import date
from typing import Optional, List

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import (
    get_es_client, get_gpt4o, get_gemini_flash, get_retriever_embeddings,
)
from core.settings import ES_INDICES
from config.prompts import NEWACTS_SYSTEM_PROMPT


# --- Act Name → Source Path Mapping ---
# These paths must match the 'source' field in ES "newacts_v1" index.

ACTS_PATHS = {
    "The Bharatiya Nyaya Sanhita, 2023": "Bharitya New Acts/The Bharatiya Nyaya Sanhita,2023.csv",
    "The Bharatiya Nagarik Suraksha Sanhita, 2023": "Bharitya New Acts/The Bharatiya Nagarik Suraksha Sanhita, 2023.csv",
    "The Bharatiya Sakshya Adhiniyam, 2023": "Bharitya New Acts/The Bharatiya Sakshya Adhiniyam, 2023.csv",
    "The Code of Criminal Procedure 1973": "Bharitya New Acts/Code of Criminal Procedure 1974.csv",
    "The Indian Penal Code, 1860": "Bharitya New Acts/Indian Penal Code 1860.csv",
    "Indian Evidence Act 1872": "Bharitya New Acts/Indian Evidence Act 1872.csv",
}


# --- Metadata Extraction ---

class ActQueryMetadata(BaseModel):
    section_number: Optional[List[str]] = Field(
        None, description="List of section numbers, normalized (remove parentheses contents)"
    )
    act_name: Optional[str] = Field(
        None, description="Exact act name from allowed mapping"
    )
    hybrid_search: Optional[bool] = Field(
        False, description="Whether to use semantic + keyword hybrid search"
    )


METADATA_PROMPT = """You are a legal AI assistant specialized in analyzing user queries to extract act metadata.

## Task
Extract:
1. **section_number** — list of section numbers (remove parentheses contents, e.g., 20(1) → "20")
2. **act_name** — exact name from allowed mapping or null
3. **hybrid_search** — true if query is vague/conceptual, false if exact section + act identified

## Allowed mapping (case-insensitive + fuzzy)
- BNS  → The Bharatiya Nyaya Sanhita, 2023
- BNSS → The Bharatiya Nagarik Suraksha Sanhita, 2023
- BSA  → The Bharatiya Sakshya Adhiniyam, 2023
- CrPC → The Code of Criminal Procedure 1973
- IPC  → The Indian Penal Code, 1860
- IEA  → Indian Evidence Act 1872

## Rules for hybrid_search
1. true if query is vague/conceptual
2. false if query has exact section number(s) AND act name
3. false if query has only act name/acronym
4. else → true

User query: {query}

Return only valid JSON:
{{"section_number": ["<string>", ...] or null, "act_name": "<string or null>", "hybrid_search": true/false}}"""


def _extract_act_metadata(query: str) -> ActQueryMetadata:
    """Use GPT-4o to extract act metadata from a newacts query."""
    llm = get_gpt4o().with_structured_output(ActQueryMetadata)
    prompt = ChatPromptTemplate.from_template(METADATA_PROMPT)
    chain = prompt | llm
    return chain.invoke({"query": query})


# --- ES Query Builder ---

def _build_newacts_query(metadata: ActQueryMetadata, query_text: str) -> dict:
    """Build ES query for newacts — either exact filter or hybrid BM25+vector."""
    filters = []

    if metadata.act_name and metadata.act_name in ACTS_PATHS:
        filters.append({"term": {"source": ACTS_PATHS[metadata.act_name]}})

    if metadata.section_number:
        filters.append({"terms": {"section_number": metadata.section_number}})

    # Hybrid search: BM25 + cosine similarity on embedding field
    if metadata.hybrid_search and query_text:
        embeddings = get_retriever_embeddings()
        query_vector = embeddings.embed_query(query_text)

        should_clauses = [{"match": {"page_content": query_text}}]
        if metadata.act_name and metadata.act_name in ACTS_PATHS:
            should_clauses.append(
                {"term": {"source": ACTS_PATHS[metadata.act_name]}}
            )

        return {
            "size": 20,
            "query": {
                "script_score": {
                    "query": {
                        "bool": {
                            "should": should_clauses,
                            "filter": filters,
                        }
                    },
                    "script": {
                        "source": """
                            double bm25 = _score;
                            double vector_score = cosineSimilarity(params.query_vector, 'embedding');
                            return bm25 + (100 * vector_score);
                        """,
                        "params": {"query_vector": query_vector},
                    },
                }
            },
            "sort": [
                {"_score": {"order": "desc"}},
                {"section_number": {"order": "asc"}},
            ],
        }

    # Non-hybrid: exact filter search
    return {
        "query": {
            "bool": {
                "must": [],
                "filter": filters,
            }
        },
        "size": 10,
        "sort": [{"section_number": {"order": "asc"}}],
    }


# --- Agent Node ---

async def newacts_node(state: LegalAgentState) -> dict:
    """Retrieve new/old act provisions and generate response.

    Flow:
    1. Extract act metadata from query (GPT-4o)
    2. Map act name to ES source path
    3. Build ES query (exact filter or hybrid)
    4. Search ES "newacts_v1" index
    5. Generate response with provisions
    """
    query = state.get("query", state["original_query"])
    chat_history = state.get("chat_history", [])
    print(f"[Newacts] Retrieving provisions for: {query[:80]}...")

    try:
        # Step 1: Extract metadata
        metadata = _extract_act_metadata(query)
        print(f"[Newacts] Metadata: act={metadata.act_name}, "
              f"sections={metadata.section_number}, hybrid={metadata.hybrid_search}")

        # Step 2: Build and execute query
        es_query = _build_newacts_query(metadata, query)
        es = get_es_client()
        response = es.search(index=ES_INDICES["newacts"], body=es_query)

        hits = response["hits"]["hits"]
        if not hits:
            print("[Newacts] No results found")
            return {
                "agent_results": {"Newacts": AgentResult(
                    agent_name="Newacts",
                    content="",
                    sources=[],
                    tokens_consumed=0,
                )},
            }

        # Step 3: Convert hits to documents
        docs_text = "\n\n".join(h["_source"]["page_content"] for h in hits)
        source_file = hits[0]["_source"].get("source", "unknown")

        # Step 4: Generate response
        llm = get_gemini_flash(temperature=0.1)
        prompt = ChatPromptTemplate.from_messages([
            ("system", NEWACTS_SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("user", "Act Provisions:\n{docs}"),
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

        # Determine display name for the act
        act_display = metadata.act_name or source_file

        result = AgentResult(
            agent_name="Newacts",
            content=llm_response.content,
            sources=[SourceMetadata(
                title=act_display,
                content=[h["_source"]["page_content"][:200] for h in hits[:5]],
                file_name=source_file,
            )],
            tokens_consumed=tokens,
        )

    except Exception as e:
        print(f"[Newacts] Error: {e}")
        result = AgentResult(
            agent_name="Newacts",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )

    return {"agent_results": {"Newacts": result}}
