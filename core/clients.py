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

import os
import threading
import time
from functools import lru_cache

# Cap BLAS / OpenMP / tokenizer threads BEFORE PyTorch is imported.
# Without this, every gunicorn worker's PyTorch tries to use all 8 vCPUs
# via OpenMP. With 6 workers, that's 48 threads fighting for 8 cores —
# the torch.nn.Linear deadlock Phase 7 observed (workers wedged at
# 110-150% CPU for 15 min after load stopped). True parallelism comes
# from the 6 worker PROCESSES; each worker should do one matmul thread
# at a time. setdefault preserves anything the systemd unit already set.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

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
            # opensearch-py accepts use_ssl/ssl_show_warn; elasticsearch>=8
            # rejects them (use https:// URL scheme + verify_certs only).
            kwargs["verify_certs"] = True
            if _USING_OPENSEARCH:
                kwargs["use_ssl"] = True
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
#
# Output budget caps (max_output_tokens) are set to the Gemini 2.5 hard ceiling
# (65,535) by default to prevent silent truncation. Callers can override per-use.
#
# `thinking_budget` controls Gemini 2.5's hidden reasoning tokens. CRITICAL:
# thinking tokens are charged AGAINST max_output_tokens (Google counts them as
# part of the output budget). For pure-generation tasks (synthesis, drafting,
# OCR), set thinking_budget=0 to disable thinking and reclaim the full budget
# for visible output. For tool-use / analytical tasks (ReAct agents, scenario,
# PDF chat), leave thinking_budget at the default to allow planning.

@lru_cache(maxsize=8)
def get_gemini_flash_lite(temperature: float = 0.3,
                          max_output_tokens: int = 8192,
                          thinking_budget: int = 0):
    """Gemini 2.5 Flash Lite — fastest. For classification, metadata, query rewrite.

    Defaults: 8K tokens cap, no thinking (lite is for fast/cheap, not reasoning).
    """
    return init_chat_model(
        "google_genai:gemini-2.5-flash-lite",
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        thinking_budget=thinking_budget,
        max_retries=2,
        timeout=60,
    )


# Alias: existing callers use get_gemini_flash() — keep pointing to Flash Lite
get_gemini_flash = get_gemini_flash_lite


@lru_cache(maxsize=8)
def get_gemini_flash_full(temperature: float = 0.3,
                          max_output_tokens: int = 65535,
                          thinking_budget: int = 0):
    """Gemini 2.5 Flash — balanced. For response generation, synthesis, ReAct agents.

    Defaults: max output ceiling (65K), thinking disabled (0).

    Pure-generation paths (legislation, judgment, newacts, drafting, synthesis)
    don't need thinking and tolerate latency poorly — keep the default.

    ReAct agents (SCI/GST/Judgment) that benefit from tool-planning reasoning
    should explicitly opt in: `get_gemini_flash_full(temperature=0, thinking_budget=2048)`.
    """
    return init_chat_model(
        "google_genai:gemini-2.5-flash",
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        thinking_budget=thinking_budget,
        max_retries=2,
        timeout=120,
    )


@lru_cache(maxsize=8)
def get_gemini_pro(temperature: float = 0.5,
                   max_output_tokens: int = 65535,
                   thinking_budget: int = 8192):
    """Gemini 2.5 Pro — strongest. For scenario analysis, PDF chat, complex reasoning.

    Defaults: max output ceiling, generous thinking budget — analytical work
    benefits from reasoning.
    """
    return init_chat_model(
        "google_genai:gemini-2.5-pro",
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        thinking_budget=thinking_budget,
        max_retries=2,
        timeout=180,
    )


@lru_cache(maxsize=8)
def get_drafting_llm(max_output_tokens: int = 65535,
                     thinking_budget: int = 0):
    """Gemini 2.5 Flash for legal drafting — fast, high-quality sections.

    Defaults: max output ceiling, no thinking (format-following beats reasoning
    for drafts; the drafting prompt is highly structured).
    """
    return init_chat_model(
        "google_genai:gemini-2.5-flash",
        temperature=0.4,
        max_output_tokens=max_output_tokens,
        thinking_budget=thinking_budget,
        max_retries=2,
        timeout=180,
    )


# --- Google GenAI client (for Gemini with Google Search grounding) ---

@lru_cache(maxsize=1)
def get_genai_client() -> genai.Client:
    """Google GenAI client for web-grounded search (Scenario agent)."""
    return genai.Client(
        http_options={"timeout": 120_000},  # 120s timeout for web-grounded search
    )


# --- ChromaDB client (Phase 5 — multi-process safe via server mode) ---
#
# Production: CHROMA_SERVER_HOST is set, we connect to the local Chroma
# server (started by lawttorney-chroma.service). The server serialises
# writes across all gunicorn workers, eliminating the data-corruption
# window that PersistentClient has under concurrent PDF uploads.
#
# Dev fallback: when CHROMA_SERVER_HOST is unset, PersistentClient still
# works for a single-process developer setup. Production MUST set this.

_chroma_client = None


def get_chroma_client():
    """Singleton ChromaDB client. HTTP in prod, PersistentClient in dev."""
    import chromadb
    global _chroma_client
    if _chroma_client is None:
        host = os.getenv("CHROMA_SERVER_HOST", "").strip()
        port_str = os.getenv("CHROMA_SERVER_PORT", "8000").strip()
        if host:
            try:
                port = int(port_str)
            except ValueError:
                port = 8000
            _chroma_client = chromadb.HttpClient(host=host, port=port)
            _log.info(
                "ChromaDB HTTP client initialized",
                host=host, port=port,
            )
        else:
            from .settings import CHROMA_STORE_ROOT
            _chroma_client = chromadb.PersistentClient(path=CHROMA_STORE_ROOT)
            _log.warning(
                "CHROMA_SERVER_HOST not set — using PersistentClient. "
                "NOT multi-process safe; set CHROMA_SERVER_HOST=localhost in prod."
            )
    return _chroma_client


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


# --- Eager-load MiniLM in local mode ---
# In remote mode the model lives in the embed microservice; nothing to load
# here. In local mode (the prod default since 2026-06-28), we must load
# MiniLM at module import so that gunicorn's preload_app=True puts it in
# the master's address space BEFORE workers fork — each forked worker then
# inherits the ~80 MB resident model via copy-on-write, so MiniLM is
# always hot from request #1 on every worker. Without this, the first
# PDF upload to land on a freshly-forked worker pays a 3-5s model load
# inside the request hot path (file_processor's _store_in_chromadb).
#
# Guarded by EMBEDDING_SERVICE_URL so dev/test environments using the
# remote service still skip the local load.
if not EMBEDDING_SERVICE_URL:
    try:
        _t0 = time.time()
        get_qa_embeddings()
        _log.info("QA embeddings eager-loaded at import",
                  load_s=round(time.time() - _t0, 2))
    except Exception as _e:
        # Fall back to lazy load on first request — never block startup.
        _log.warning("QA embeddings eager-load failed; will lazy-load",
                     error=str(_e)[:200])
