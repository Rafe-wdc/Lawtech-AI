"""Shared Tools: LLM-based extraction and generation.

Reusable @tool functions for structured metadata extraction,
template selection, and legal response generation.

These tools wrap common LLM call patterns used across multiple agents,
enabling consistent prompt handling and structured output parsing.

Uses: GPT-4o, GPT-4o-mini, Gemini Flash Lite via core.clients
"""

from __future__ import annotations

from datetime import date
from typing import Optional, List

from pydantic import BaseModel, Field
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from core.clients import get_gpt4o, get_gpt4o_mini, get_gemini_flash, get_drafting_llm


# ============================================================
# Structured Output Schemas
# ============================================================

class CaseMetadata(BaseModel):
    """Structured metadata extracted from a judgment search query."""
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


class ActQueryMetadata(BaseModel):
    """Structured metadata extracted from a newacts search query."""
    section_number: Optional[List[str]] = Field(
        None, description="List of section numbers, normalized (remove parentheses contents)"
    )
    act_name: Optional[str] = Field(
        None, description="Exact act name from allowed mapping"
    )
    hybrid_search: Optional[bool] = Field(
        False, description="Whether to use semantic + keyword hybrid search"
    )


class QueryMatchPhrase(BaseModel):
    """Match phrase for targeted Elasticsearch sub-search."""
    match_phrase: Optional[str] = Field(None, description="match_phrase clause")
    sub_part: Optional[str] = Field(None, description="Sub part of act with name")


class TemplateSource(BaseModel):
    """Selected template source path."""
    source: str = Field(..., description="The most relevant source file path")


# ============================================================
# Extraction Prompts
# ============================================================

_CASE_METADATA_PROMPT = """You are an expert in legal text parsing and Elasticsearch query formulation.

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


_ACT_METADATA_PROMPT = """You are a legal AI assistant specialized in analyzing user queries to extract act metadata.

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


_MATCH_PHRASE_PROMPT = """You are a legal document search expert.

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


_TEMPLATE_SELECTION_PROMPT = """You are a legal AI assistant tasked with identifying the most relevant legal document template.

User query: {query}

Select the most relevant file path from the list below:
{files_path}

Return only the most relevant file path."""


# ============================================================
# Tool Functions
# ============================================================

@tool
def extract_case_metadata(query: str) -> dict:
    """Extract structured case metadata from a judgment search query using GPT-4o.

    Parses court name, party names, year, topics, acts/sections, and generates
    a normalized lexical search string for Elasticsearch BM25 matching.

    Args:
        query: Natural language judgment search query (e.g. "State vs Doe 2020 SC murder")

    Returns:
        Dict with keys: court_name, petitioner_names, respondent_names, year,
        topics, acts_or_sections, lexical_query, size
    """
    llm = get_gpt4o().with_structured_output(CaseMetadata)
    prompt = ChatPromptTemplate.from_template(_CASE_METADATA_PROMPT)
    chain = prompt | llm
    result = chain.invoke({"query": query})

    return {
        "court_name": result.court_name,
        "petitioner_names": result.petitioner_names or [],
        "respondent_names": result.respondent_names or [],
        "year": result.year,
        "topics": result.topics or [],
        "acts_or_sections": result.acts_or_sections or [],
        "lexical_query": result.lexical_query,
        "size": result.size or 3,
    }


@tool
def extract_act_metadata(query: str) -> dict:
    """Extract act metadata from a newacts search query using GPT-4o.

    Identifies section numbers, act name, and whether hybrid search is needed
    for the 6 core Indian codes (BNS, BNSS, BSA, IPC, CrPC, IEA).

    Args:
        query: Query about Indian legal codes (e.g. "Section 302 BNS")

    Returns:
        Dict with keys: section_number (list), act_name, hybrid_search (bool)
    """
    llm = get_gpt4o().with_structured_output(ActQueryMetadata)
    prompt = ChatPromptTemplate.from_template(_ACT_METADATA_PROMPT)
    chain = prompt | llm
    result = chain.invoke({"query": query})

    return {
        "section_number": result.section_number,
        "act_name": result.act_name,
        "hybrid_search": result.hybrid_search or False,
    }


@tool
def extract_match_phrase(query: str, text: str, title: str) -> dict:
    """Extract a match phrase for targeted legislation sub-search using GPT-4o-mini.

    Combines provision type from context, number from query, and act title
    into a normalized match_phrase for Elasticsearch exact matching.

    Args:
        query: The user's original query
        text: The retrieved provision context text
        title: The act title

    Returns:
        Dict with keys: match_phrase, sub_part
    """
    llm = get_gpt4o_mini().with_structured_output(QueryMatchPhrase)
    prompt = ChatPromptTemplate.from_template(_MATCH_PHRASE_PROMPT)
    chain = prompt | llm
    result = chain.invoke({"query": query, "text": text, "title": title})

    return {
        "match_phrase": result.match_phrase,
        "sub_part": result.sub_part,
    }


@tool
def select_best_template(query: str, file_paths: list[str]) -> str:
    """Select the most relevant legal template from a list of file paths using GPT-4o-mini.

    Used after searching the drafting index to narrow down
    which template best matches the user's drafting request.

    Args:
        query: The user's drafting request (e.g. "bail application for theft")
        file_paths: List of template source file paths from ES search results

    Returns:
        The selected template source file path
    """
    llm = get_gpt4o_mini().with_structured_output(TemplateSource)
    prompt = ChatPromptTemplate.from_template(_TEMPLATE_SELECTION_PROMPT)
    chain = prompt | llm
    result = chain.invoke({"query": query, "files_path": "\n".join(file_paths)})
    selected = result.source.strip()
    # Validate: LLM must select from the provided list, not hallucinate a path
    if selected not in file_paths:
        # Try partial match (LLM may return just the filename)
        matches = [fp for fp in file_paths if selected in fp or fp in selected]
        if matches:
            selected = matches[0]
        elif file_paths:
            selected = file_paths[0]  # Safe fallback to first option
    return selected


@tool
def generate_legal_response(
    system_prompt: str,
    query: str,
    docs: str,
    chat_history_text: str = "",
    temperature: float = 0.1,
    model: str = "gemini_flash",
) -> dict:
    """Generate a legal response from retrieved documents and user query.

    Uses the specified LLM to generate a contextual legal answer based on
    retrieved documents, system prompt, and optional chat history.

    Args:
        system_prompt: The system prompt defining the AI persona and response format
        query: The user's legal query
        docs: Retrieved documents/provisions text to base the response on
        chat_history_text: Formatted previous conversation turns (optional)
        temperature: LLM temperature (default 0.1 for factual responses)
        model: Which LLM to use — "gemini_flash", "drafting" (GPT-4o), or "gpt4o"

    Returns:
        Dict with keys: content (response text), tokens_consumed (int)
    """
    # Select LLM
    if model == "drafting":
        llm = get_drafting_llm()
    elif model == "gpt4o":
        llm = get_gpt4o(temperature=temperature)
    else:
        llm = get_gemini_flash(temperature=temperature)

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("user", "Previous conversation:\n{chat_history}"),
        ("user", "Retrieved content:\n{docs}"),
        ("user", "Current Date: {date}"),
        ("user", "User Query: {query}"),
    ])
    chain = prompt | llm

    response = chain.invoke({
        "query": query,
        "docs": docs,
        "chat_history": chat_history_text,
        "date": str(date.today()),
    })

    tokens = 0
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        tokens = response.usage_metadata.get("total_tokens", 0)
    elif hasattr(response, "response_metadata"):
        token_usage = response.response_metadata.get("token_usage", {})
        tokens = token_usage.get("total_tokens", 0)

    return {
        "content": response.content,
        "tokens_consumed": tokens,
    }
