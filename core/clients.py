"""Shared async clients — singleton instances reused across all agents.

Instead of each agent creating its own ES/LLM/embedding connections,
all agents import from here. This gives us:
- Connection pooling (one ES pool, not one per agent)
- Model reuse (load embeddings once, not per-request)
- Easy swap to MCP later (change imports here, agents untouched)

Uses: init_chat_model() for provider-agnostic LLM initialization (LangChain 1.0+)
Ref: https://docs.langchain.com/oss/python/langchain/models
"""

from __future__ import annotations

import threading
import time
from functools import lru_cache

# Try opensearch-py first (for AWS OpenSearch), fall back to elasticsearch
try:
    from opensearchpy import OpenSearch as _SearchClient
    _USING_OPENSEARCH = True
except ImportError:
    from elasticsearch import Elasticsearch as _SearchClient
    _USING_OPENSEARCH = False

from langchain.chat_models import init_chat_model
from langchain_community.embeddings import HuggingFaceEmbeddings
from google import genai

from .settings import (
    ELASTICSEARCH_URL,
    ES_USER,
    ES_PASSWORD,
    ES_USE_SSL,
    EMBEDDING_MODELS,
    EMBEDDING_SERVICE_URL,
)
from .logger import get_logger

_log = get_logger("Clients")


# --- Elasticsearch / OpenSearch ---

_es_client: _SearchClient | None = None


def get_es_client(max_retries: int = 3, timeout: int = 30) -> _SearchClient:
    """Get or create singleton search client (OpenSearch or Elasticsearch)."""
    global _es_client
    if _es_client is None:
        kwargs: dict = {
            "request_timeout": timeout,
            "max_retries": max_retries,
            "retry_on_timeout": True,
        }

        if ES_USER and ES_PASSWORD:
            kwargs["http_auth"] = (ES_USER, ES_PASSWORD)

        if ES_USE_SSL:
            kwargs["use_ssl"] = True
            kwargs["verify_certs"] = True
            kwargs["ssl_show_warn"] = True

        _es_client = _SearchClient(
            ELASTICSEARCH_URL,
            **kwargs,
        )

        _log.info(
            "Search client initialized",
            backend="opensearch" if _USING_OPENSEARCH else "elasticsearch",
            url=ELASTICSEARCH_URL[:40] + "...",
            ssl=ES_USE_SSL,
            auth=bool(ES_USER),
        )
    return _es_client


# --- Elasticsearch Circuit Breaker ---
# Prevents cascading hangs when ES is unavailable.
# After 3 consecutive failures, opens for 60s (fast-fail period).
# Auto-resets after 60s to allow ES to recover.

_es_failure_count: int = 0
_es_open_until: float = 0.0   # epoch time when circuit opens until
_ES_FAILURE_THRESHOLD: int = 3
_ES_OPEN_DURATION_SEC: float = 60.0
_es_lock = threading.Lock()   # guards _es_failure_count and _es_open_until


def is_es_available() -> bool:
    """Return False if the ES circuit is open (fast-fail period active)."""
    with _es_lock:
        return time.time() >= _es_open_until


def record_es_failure() -> None:
    """Record an ES failure. Opens the circuit after 3 consecutive failures."""
    global _es_failure_count, _es_open_until
    with _es_lock:
        _es_failure_count += 1
        if _es_failure_count >= _ES_FAILURE_THRESHOLD:
            _es_open_until = time.time() + _ES_OPEN_DURATION_SEC
            _log.warning("ES circuit OPEN — fast-failing for 60s",
                         failure_count=_es_failure_count)


def record_es_success() -> None:
    """Reset the circuit breaker after a successful ES call."""
    global _es_failure_count, _es_open_until
    with _es_lock:
        if _es_failure_count > 0:
            _log.info("ES circuit RESET after successful call",
                      previous_failures=_es_failure_count)
        _es_failure_count = 0
        _es_open_until = 0.0


# --- LLM Clients via init_chat_model (provider-agnostic, LangChain 1.0+) ---
# Pattern: init_chat_model("provider:model_name", **kwargs)
# Docs: https://docs.langchain.com/oss/python/langchain/models

@lru_cache(maxsize=1)
def get_gpt4o(temperature: float = 0.3):
    """GPT-4o — kept for backward compat, prefer Gemini models."""
    return init_chat_model("openai:gpt-4o", temperature=temperature, max_retries=2)


@lru_cache(maxsize=1)
def get_gpt4o_mini(temperature: float = 0.3):
    """GPT-4o-mini — kept for backward compat, prefer Gemini models."""
    return init_chat_model("openai:gpt-4o-mini", temperature=temperature, max_retries=2)


# --- Gemini Model Tier ---
# Flash Lite: classification, metadata extraction, query rewrite (fastest, cheapest)
# Flash:      response generation, synthesis, ReAct agents (balanced quality + speed)
# Pro:        scenario analysis, complex reasoning (highest quality)

@lru_cache(maxsize=1)
def get_gemini_flash_lite(temperature: float = 0.3):
    """Gemini 2.5 Flash Lite — fastest. For classification, metadata, query rewrite."""
    return init_chat_model("google_genai:gemini-2.5-flash-lite", temperature=temperature)


# Alias: existing callers use get_gemini_flash() — keep pointing to Flash Lite
get_gemini_flash = get_gemini_flash_lite


@lru_cache(maxsize=1)
def get_gemini_flash_full(temperature: float = 0.3):
    """Gemini 2.5 Flash — balanced. For response generation, synthesis, ReAct agents."""
    return init_chat_model("google_genai:gemini-2.5-flash", temperature=temperature)


@lru_cache(maxsize=1)
def get_gemini_pro(temperature: float = 0.5):
    """Gemini 2.5 Pro — strongest. For scenario analysis, PDF chat, complex reasoning."""
    return init_chat_model("google_genai:gemini-2.5-pro", temperature=temperature)


@lru_cache(maxsize=1)
def get_drafting_llm():
    """Gemini 2.5 Flash for legal drafting — fast, high-quality sections."""
    return init_chat_model("google_genai:gemini-2.5-flash", temperature=0.4)


# --- Google GenAI client (for Gemini with Google Search grounding) ---

@lru_cache(maxsize=1)
def get_genai_client() -> genai.Client:
    """Google GenAI client for web-grounded search (Scenario agent)."""
    return genai.Client(
        http_options={"timeout": 120_000},  # 120s timeout for web-grounded search
    )


# --- Embedding Models ---
# When EMBEDDING_SERVICE_URL is set, embeddings are computed by a remote
# microservice (services/embedding_service.py) instead of loading models
# locally. This allows multi-worker deployments without OOM crashes.

_retriever_embeddings = None
_qa_embeddings = None


def get_retriever_embeddings():
    """BGE-large-en-v1.5 for legal document retrieval (ES hybrid search, ChromaDB).

    Returns a LangChain-compatible Embeddings object — either a remote HTTP
    client (production) or a local HuggingFaceEmbeddings (development).
    """
    global _retriever_embeddings
    if _retriever_embeddings is None:
        if EMBEDDING_SERVICE_URL:
            from .embedding_client import RemoteEmbeddings
            _retriever_embeddings = RemoteEmbeddings(
                EMBEDDING_SERVICE_URL, model_name="retriever",
            )
        else:
            _retriever_embeddings = HuggingFaceEmbeddings(
                model_name=EMBEDDING_MODELS["retriever"],
                model_kwargs={"device": "cpu"},
                encode_kwargs={"normalize_embeddings": True},
            )
    return _retriever_embeddings


def get_qa_embeddings():
    """all-MiniLM-L6-v2 for PDF document Q&A.

    Returns a LangChain-compatible Embeddings object — either a remote HTTP
    client (production) or a local HuggingFaceEmbeddings (development).
    """
    global _qa_embeddings
    if _qa_embeddings is None:
        if EMBEDDING_SERVICE_URL:
            from .embedding_client import RemoteEmbeddings
            _qa_embeddings = RemoteEmbeddings(
                EMBEDDING_SERVICE_URL, model_name="qa",
            )
        else:
            _qa_embeddings = HuggingFaceEmbeddings(
                model_name=EMBEDDING_MODELS["pdf_qa"],
                model_kwargs={"device": "cpu"},
            )
    return _qa_embeddings
