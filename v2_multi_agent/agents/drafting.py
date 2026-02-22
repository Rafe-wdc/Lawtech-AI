"""Agent #7 — Drafting Agent

Legal document drafting from templates stored in Elasticsearch.
Finds best matching template, then fills it with user's details.

Handles: "Draft bail application", "Legal notice for property dispute", etc.

Uses: GPT-4o (draft generation), GPT-4o-mini (template selection)
Data Source: Elasticsearch "drafting" index
"""

from __future__ import annotations

import os
from datetime import date

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import get_es_client, get_gpt4o_mini, get_drafting_llm
from core.settings import ES_INDICES
from core.logger import get_logger, log_time
from config.prompts import DRAFTING_SYSTEM_PROMPT

log = get_logger("Drafting")


# --- Template Selection ---

class TemplateSource(BaseModel):
    source: str = Field(..., description="The most relevant source file path")


TEMPLATE_SELECTION_PROMPT = """You are a legal AI assistant tasked with identifying the most relevant legal document template.

User query: {query}

Select the most relevant file path from the list below:
{files_path}

Return only the most relevant file path."""


def _select_best_template(query: str, file_paths: list[str]) -> str:
    """Use GPT-4o-mini to select the most relevant template from search results."""
    with log_time(log, "Template selection (LLM)"):
        llm = get_gpt4o_mini().with_structured_output(TemplateSource)
        prompt = ChatPromptTemplate.from_template(TEMPLATE_SELECTION_PROMPT)
        chain = prompt | llm
        result = chain.invoke({"query": query, "files_path": "\n".join(file_paths)})
    return result.source.strip()


# --- Agent Node ---

async def drafting_node(state: LegalAgentState) -> dict:
    """Find template and generate legal draft.

    Flow:
    1. Search ES "drafting" index for matching templates (top 100)
    2. Use GPT-4o-mini to select most relevant template source
    3. Fetch full template document from that source
    4. Use GPT-4o to fill template with user's scenario details
    """
    query = state.get("query", state["original_query"])
    chat_history = state.get("chat_history", [])
    log.info("Agent started", query=query[:100])

    try:
        es = get_es_client()
        index = ES_INDICES["drafting"]

        # Step 1: Search for matching templates
        with log_time(log, "Template search (ES)"):
            search_query = {
                "size": 100,
                "query": {"match": {"page_content": query}},
            }
            response = es.search(index=index, body=search_query)
            hits = response["hits"]["hits"]

        if not hits:
            log.warning("No templates found")
            return {
                "agent_results": {"Drafting": AgentResult(
                    agent_name="Drafting",
                    content="",
                    sources=[],
                    tokens_consumed=0,
                )},
            }

        # Step 2: Get unique file paths and select best one
        file_paths = list(dict.fromkeys(
            h["_source"]["source"] for h in hits
        ))
        log.info("Template candidates found",
                 total_hits=len(hits), unique_templates=len(file_paths))

        selected_source = _select_best_template(query, file_paths)
        log.info("Template selected", template=selected_source)

        # Step 3: Fetch the full template document
        source_query = {
            "size": 1,
            "query": {"term": {"source.keyword": selected_source}},
        }
        source_response = es.search(index=index, body=source_query)
        source_hits = source_response["hits"]["hits"]

        if not source_hits:
            log.error("Selected template not found in ES",
                      template=selected_source)
            return {
                "agent_results": {"Drafting": AgentResult(
                    agent_name="Drafting",
                    content="",
                    sources=[],
                    tokens_consumed=0,
                    error=f"Template source not found: {selected_source}",
                )},
            }

        template_text = source_hits[0]["_source"]["page_content"]
        log.debug("Template loaded",
                  template=selected_source, template_len=len(template_text))

        # Step 4: Generate the draft using GPT-4o
        with log_time(log, "Draft generation (LLM)"):
            llm = get_drafting_llm()
            prompt = ChatPromptTemplate.from_messages([
                ("system", DRAFTING_SYSTEM_PROMPT),
                MessagesPlaceholder(variable_name="chat_history", optional=True),
                ("user", "Original Draft Template:\n{docs}"),
                ("user", "Current Date: {date}"),
                ("user", "User Query:\n{query}"),
            ])
            chain = prompt | llm

            from core.streaming import stream_chain_response
            llm_response = await stream_chain_response(chain, {
                "query": query,
                "docs": template_text,
                "chat_history": chat_history,
                "date": str(date.today()),
            })

        tokens = 0
        if hasattr(llm_response, "response_metadata"):
            token_usage = llm_response.response_metadata.get("token_usage", {})
            tokens = token_usage.get("total_tokens", 0)
        if tokens == 0 and hasattr(llm_response, "usage_metadata") and llm_response.usage_metadata:
            tokens = llm_response.usage_metadata.get("total_tokens", 0)

        log.info("Agent completed",
                 template=selected_source,
                 response_len=len(llm_response.content), tokens=tokens)

        template_display = os.path.splitext(os.path.basename(selected_source))[0]
        result = AgentResult(
            agent_name="Drafting",
            content=llm_response.content,
            sources=[SourceMetadata(
                source_type="drafting",
                title=template_display,
                content=[template_text[:300]],
                file_name=selected_source,
                agent_name="Drafting",
                template_type=template_display,
            )],
            tokens_consumed=tokens,
        )

    except Exception as e:
        log.error("Agent failed", error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="Drafting",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )

    return {"agent_results": {"Drafting": result}}
