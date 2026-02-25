"""Shared tools — @tool decorated functions callable by agents.

These tools encapsulate reusable operations (ES search, ChromaDB retrieval,
LLM extraction, storage, guardrails, memory, orchestration, scenario analysis,
document processing) that are shared across the 10-agent system.

Import individual tools or use ALL_TOOLS for agent tool bindings.
Agent-specific tool lists are available via AGENT_TOOLS dict.
"""

# --- Elasticsearch & Domain Tools ---
from .elasticsearch_tools import (
    search_legislation,
    search_legislation_by_topic,
    search_legislation_multi_section,
    search_judgments,
    search_newacts,
    search_newacts_by_topic,
    get_nearby_sections,
    search_drafts,
    get_docs_by_source,
    create_search_variations,
    search_high_court,
    map_act_to_source,
    map_old_to_new_law,
    validate_draft_format,
    translate_draft,
)

# --- VectorDB Tools ---
from .vectordb_tools import (
    search_chromadb_ensemble,
    search_pdf_collection,
    store_pdf_chunks,
)

# --- LLM Extraction Tools ---
from .llm_tools import (
    extract_case_metadata,
    extract_act_metadata,
    extract_match_phrase,
    select_best_template,
    generate_legal_response,
    # Schemas (for direct use by agents if needed)
    CaseMetadata,
    ActQueryMetadata,
    QueryMatchPhrase,
    TemplateSource,
)

# --- Storage Tools ---
from .storage_tools import (
    generate_s3_link,
    load_pdf_chat_history,
    save_pdf_chat_history,
    load_chat_history_from_api,
)

# --- Guardrail Tools ---
from .guardrail_tools import (
    validate_input,
    detect_injection_regex,
    detect_injection_llm,
    detect_pii,
    redact_pii,
    flag_hallucination,
)

# --- Memory Tools ---
from .memory_tools import (
    rewrite_query,
    summarize_conversation,
    is_followup_query,
    save_chat_history,
)

# --- Orchestrator Tools ---
from .orchestrator_tools import (
    get_execution_plan,
    delegate_to_agent,
    delegate_parallel,
    merge_results,
    request_clarification,
)

# --- Scenario Tools ---
from .scenario_tools import (
    web_search_grounded,
    analyze_scenario,
    cite_provisions,
    find_similar_cases,
    suggest_remedies,
    get_legal_news,
)

# --- Document Tools ---
from .document_tools import (
    validate_pdf,
    extract_text_pymupdf,
    extract_text_vision,
    compress_pdf,
    delete_pdf_vectorstore,
    get_collection_metadata,
)

# --- Judgment Specialized Search ---
from .judgment_search import (
    smart_judgment_search,
    search_by_party_names as search_judgment_by_party,
    search_by_party_fuzzy as search_judgment_fuzzy,
    search_by_single_party as search_judgment_single_party,
    search_by_citation as search_judgment_by_citation,
    search_by_case_type as search_judgment_by_case_type,
    search_by_judge as search_judgment_by_judge,
    search_by_date_range as search_judgment_by_date_range,
    search_by_legal_provision as search_judgment_by_provision,
    detect_citation,
    detect_case_type,
)

# --- SCI Judgment Tools ---
from .sci_judgment_tools import (
    search_by_semantic,
    search_by_keyword,
    search_by_case_number,
    search_by_party_name,
    search_by_date_range,
    search_by_judge,
    get_case_details,
)


# ============================================================
# ALL_TOOLS — flat list of every tool for convenience
# ============================================================

ALL_TOOLS = [
    # Elasticsearch (5 + 4 new)
    search_legislation,
    search_legislation_by_topic,
    search_legislation_multi_section,
    search_judgments,
    search_newacts,
    search_newacts_by_topic,
    get_nearby_sections,
    search_drafts,
    get_docs_by_source,
    # Domain-specific ES helpers (6)
    create_search_variations,
    search_high_court,
    map_act_to_source,
    map_old_to_new_law,
    validate_draft_format,
    translate_draft,
    # VectorDB (3)
    search_chromadb_ensemble,
    search_pdf_collection,
    store_pdf_chunks,
    # LLM Extraction (5)
    extract_case_metadata,
    extract_act_metadata,
    extract_match_phrase,
    select_best_template,
    generate_legal_response,
    # Storage (4)
    generate_s3_link,
    load_pdf_chat_history,
    save_pdf_chat_history,
    load_chat_history_from_api,
    # Guardrail (6)
    validate_input,
    detect_injection_regex,
    detect_injection_llm,
    detect_pii,
    redact_pii,
    flag_hallucination,
    # Memory (4)
    rewrite_query,
    summarize_conversation,
    is_followup_query,
    save_chat_history,
    # Orchestrator (5)
    get_execution_plan,
    delegate_to_agent,
    delegate_parallel,
    merge_results,
    request_clarification,
    # Scenario (6)
    web_search_grounded,
    analyze_scenario,
    cite_provisions,
    find_similar_cases,
    suggest_remedies,
    get_legal_news,
    # Document (6)
    validate_pdf,
    extract_text_pymupdf,
    extract_text_vision,
    compress_pdf,
    delete_pdf_vectorstore,
    get_collection_metadata,
    # Judgment Specialized Search (11)
    smart_judgment_search,
    search_judgment_by_party,
    search_judgment_fuzzy,
    search_judgment_single_party,
    search_judgment_by_citation,
    search_judgment_by_case_type,
    search_judgment_by_judge,
    search_judgment_by_date_range,
    search_judgment_by_provision,
    detect_citation,
    detect_case_type,
    # SCI Judgment (7)
    search_by_semantic,
    search_by_keyword,
    search_by_case_number,
    search_by_party_name,
    search_by_date_range,
    search_by_judge,
    get_case_details,
]


# ============================================================
# AGENT_TOOLS — per-agent tool bindings
# ============================================================

AGENT_TOOLS = {
    "orchestrator": [
        get_execution_plan,
        delegate_to_agent,
        delegate_parallel,
        merge_results,
        request_clarification,
    ],
    "guardrail": [
        validate_input,
        detect_injection_regex,
        detect_injection_llm,
        detect_pii,
        redact_pii,
        flag_hallucination,
    ],
    "memory": [
        load_chat_history_from_api,
        load_pdf_chat_history,
        save_chat_history,
        save_pdf_chat_history,
        rewrite_query,
        summarize_conversation,
        is_followup_query,
    ],
    "legislation": [
        search_legislation,
        search_legislation_by_topic,
        search_legislation_multi_section,
        create_search_variations,
        get_docs_by_source,
        extract_match_phrase,
        generate_legal_response,
    ],
    "judgment": [
        extract_case_metadata,
        search_judgments,
        search_high_court,
        smart_judgment_search,
        search_judgment_by_party,
        search_judgment_fuzzy,
        search_judgment_single_party,
        search_judgment_by_citation,
        search_judgment_by_case_type,
        search_judgment_by_judge,
        search_judgment_by_date_range,
        search_judgment_by_provision,
        generate_s3_link,
        generate_legal_response,
    ],
    "newacts": [
        extract_act_metadata,
        search_newacts,
        search_newacts_by_topic,
        get_nearby_sections,
        map_act_to_source,
        map_old_to_new_law,
        generate_legal_response,
    ],
    "drafting": [
        search_drafts,
        select_best_template,
        get_docs_by_source,
        validate_draft_format,
        translate_draft,
        generate_legal_response,
    ],
    "scenario": [
        web_search_grounded,
        analyze_scenario,
        cite_provisions,
        find_similar_cases,
        suggest_remedies,
        get_legal_news,
    ],
    "constitution": [
        search_chromadb_ensemble,
        generate_legal_response,
    ],
    "maxim": [
        search_chromadb_ensemble,
        generate_legal_response,
    ],
    "legal_concepts": [
        generate_legal_response,
    ],
    "document": [
        validate_pdf,
        extract_text_pymupdf,
        extract_text_vision,
        compress_pdf,
        search_pdf_collection,
        store_pdf_chunks,
        delete_pdf_vectorstore,
        get_collection_metadata,
        load_pdf_chat_history,
        save_pdf_chat_history,
    ],
    "sci_judgment": [
        search_by_semantic,
        search_by_keyword,
        search_by_case_number,
        search_by_party_name,
        search_by_date_range,
        search_by_judge,
        get_case_details,
    ],
}
