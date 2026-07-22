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
    search_constitution,
    search_constitution_by_part,
    search_legal_maxims,
)

# --- VectorDB Tools (PDF uploads only) ---
from .vectordb_tools import (
    search_pdf_collection,
    store_pdf_chunks,
    get_full_attachment,
    retrieve_attachment_context,
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
# validate_pdf / extract_text_pymupdf / extract_text_vision were removed on
# 2026-07-22 — dead code that duplicated (and drifted from) the real OCR
# pipeline in core/file_processor.py. See document_tools.py header comment.
from .document_tools import (
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
    search_by_topic,
    search_by_keyword,
    search_by_case_number,
    search_by_party_name,
    search_by_date_range,
    search_by_judge,
    get_case_details,
)

# --- GST Judgment (AAAR) Tools ---
from .gst_judgment_tools import (
    gst_search_by_topic,
    gst_search_by_keyword,
    gst_search_by_case_number,
    gst_search_by_party_name,
    gst_search_by_date_range,
    gst_search_by_state,
    gst_get_case_details,
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
    # Constitution & Maxim ES (3)
    search_constitution,
    search_constitution_by_part,
    search_legal_maxims,
    # VectorDB — PDF uploads (4)
    search_pdf_collection,
    store_pdf_chunks,
    get_full_attachment,
    retrieve_attachment_context,
    # LLM Extraction (5)
    extract_case_metadata,
    extract_act_metadata,
    extract_match_phrase,
    select_best_template,
    generate_legal_response,
    # Storage (2)
    generate_s3_link,
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
    # Document (3)
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
    search_by_topic,
    search_by_keyword,
    search_by_case_number,
    search_by_party_name,
    search_by_date_range,
    search_by_judge,
    get_case_details,
    # GST Judgment / AAAR (7)
    gst_search_by_topic,
    gst_search_by_keyword,
    gst_search_by_case_number,
    gst_search_by_party_name,
    gst_search_by_date_range,
    gst_search_by_state,
    gst_get_case_details,
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
        save_chat_history,
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
        get_full_attachment,
        retrieve_attachment_context,
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
        search_constitution,
        search_constitution_by_part,
        generate_legal_response,
    ],
    "maxim": [
        search_legal_maxims,
        generate_legal_response,
    ],
    "legal_concepts": [
        generate_legal_response,
    ],
    "document": [
        compress_pdf,
        search_pdf_collection,
        store_pdf_chunks,
        delete_pdf_vectorstore,
        get_collection_metadata,
        get_full_attachment,
        retrieve_attachment_context,
    ],
    "sci_judgment": [
        search_by_topic,
        search_by_keyword,
        search_by_case_number,
        search_by_party_name,
        search_by_date_range,
        search_by_judge,
        get_case_details,
    ],
    "gst_judgment": [
        gst_search_by_topic,
        gst_search_by_keyword,
        gst_search_by_case_number,
        gst_search_by_party_name,
        gst_search_by_date_range,
        gst_search_by_state,
        gst_get_case_details,
    ],
}
