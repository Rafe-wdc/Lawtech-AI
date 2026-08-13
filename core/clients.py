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

# Guards CONSTRUCTION of `_es_client` only. Deliberately not `_es_lock` below —
# that one guards the circuit-breaker counters, and holding it across client
# construction would block every `is_es_available()` check.
_es_client_lock = threading.Lock()


def get_es_client(max_retries: int = 3, timeout: int = 30) -> _SearchClient:
    """Get or create singleton search client (OpenSearch or Elasticsearch).

    Double-checked locking: agents call this from `asyncio.to_thread`, so two
    concurrent first requests can genuinely both observe `None` and each build
    a client. The loser's client is then silently dropped, leaking its
    connection pool, and "Search client initialized" logs twice.
    """
    global _es_client
    # Fast path — no lock once initialised (the common case, every request).
    if _es_client is not None:
        return _es_client

    with _es_client_lock:
        # Re-check: another thread may have built it while we waited.
        if _es_client is not None:
            return _es_client

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


# --- Gemini Circuit Breaker ---
# Mirrors the ES pattern above. Prevents cascading Gemini calls during a
# regional outage / quota trip. When Gemini returns 5xx or a 429 or hits
# `ResourceExhausted`, agents that guard critical paths with
# `is_gemini_available()` can skip the Gemini call and fall back to
# cached / no-op behaviour. This targets self_refine, the drafting
# judge/router, and web_search_fallback — the paths where a hung Gemini
# holds a semaphore slot until gunicorn kills the worker.
#
# Two breakers because Flash quota is separately rate-limited from Pro
# quota — Pro can be down while Flash is fine.

_gemini_flash_failure_count: int = 0
_gemini_flash_open_until: float = 0.0
_gemini_pro_failure_count: int = 0
_gemini_pro_open_until: float = 0.0
_GEMINI_FAILURE_THRESHOLD: int = 5
_GEMINI_OPEN_DURATION_SEC: float = 60.0
_gemini_lock = threading.Lock()


def is_gemini_flash_available() -> bool:
    """Return False if the Gemini Flash circuit is open (fast-fail active)."""
    with _gemini_lock:
        return time.time() >= _gemini_flash_open_until


def is_gemini_pro_available() -> bool:
    """Return False if the Gemini Pro circuit is open (fast-fail active)."""
    with _gemini_lock:
        return time.time() >= _gemini_pro_open_until


def record_gemini_flash_failure() -> None:
    """Increment Flash failure count; open the circuit after 5 hits."""
    global _gemini_flash_failure_count, _gemini_flash_open_until
    with _gemini_lock:
        _gemini_flash_failure_count += 1
        if _gemini_flash_failure_count >= _GEMINI_FAILURE_THRESHOLD:
            _gemini_flash_open_until = time.time() + _GEMINI_OPEN_DURATION_SEC
            _log.warning(
                "Gemini Flash circuit OPEN — fast-failing for 60s",
                failure_count=_gemini_flash_failure_count,
            )


def record_gemini_flash_success() -> None:
    """Reset Flash breaker after a successful call."""
    global _gemini_flash_failure_count, _gemini_flash_open_until
    with _gemini_lock:
        if _gemini_flash_failure_count > 0:
            _log.info(
                "Gemini Flash circuit RESET after successful call",
                previous_failures=_gemini_flash_failure_count,
            )
        _gemini_flash_failure_count = 0
        _gemini_flash_open_until = 0.0


def record_gemini_pro_failure() -> None:
    """Increment Pro failure count; open the circuit after 5 hits."""
    global _gemini_pro_failure_count, _gemini_pro_open_until
    with _gemini_lock:
        _gemini_pro_failure_count += 1
        if _gemini_pro_failure_count >= _GEMINI_FAILURE_THRESHOLD:
            _gemini_pro_open_until = time.time() + _GEMINI_OPEN_DURATION_SEC
            _log.warning(
                "Gemini Pro circuit OPEN — fast-failing for 60s",
                failure_count=_gemini_pro_failure_count,
            )


def record_gemini_pro_success() -> None:
    """Reset Pro breaker after a successful call."""
    global _gemini_pro_failure_count, _gemini_pro_open_until
    with _gemini_lock:
        if _gemini_pro_failure_count > 0:
            _log.info(
                "Gemini Pro circuit RESET after successful call",
                previous_failures=_gemini_pro_failure_count,
            )
        _gemini_pro_failure_count = 0
        _gemini_pro_open_until = 0.0


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


# --- ChromaDB client (PersistentClient — v1 behaviour restored 2026-07-01) ---
#
# Every process/worker uses its own PersistentClient view of the on-disk
# chroma_store. Matches how v1 shipped for a year without incident and
# how huge PDFs were handled without timing out.
#
# History: an intermediate "chroma-server" mode (lawttorney-chroma.service
# HttpClient) was tried during the scale-50 work under the assumption that
# multi-worker writes to the same SQLite file would race. In practice it
# introduced a shared SQLAlchemy pool (~5 connections) that gunicorn's
# workers can exhaust in seconds, producing the misleading
# "vector embedding timed out" event and leaving collections empty.
# The theoretical corruption never surfaced; the pool exhaustion did,
# repeatedly. We're reverting to PersistentClient permanently. If real
# corruption is ever observed, we'll fix it locally (SQLite WAL mode,
# per-collection locks) rather than reintroducing a shared server.
#
# CHROMA_SERVER_HOST is intentionally ignored — the mode is baked in
# so it can't be re-enabled by a stale env var.

_chroma_client = None


def get_chroma_client():
    """Singleton ChromaDB client — always PersistentClient (v1 mode)."""
    import chromadb
    global _chroma_client
    if _chroma_client is None:
        from .settings import CHROMA_STORE_ROOT
        _chroma_client = chromadb.PersistentClient(path=CHROMA_STORE_ROOT)
        _log.info(
            "ChromaDB PersistentClient initialized",
            path=CHROMA_STORE_ROOT,
        )
    return _chroma_client


# --- Embedding Models ---
# When EMBEDDING_SERVICE_URL is set, embeddings are computed by a remote
# microservice (services/embedding_service.py) instead of loading models
# locally. This allows multi-worker deployments without OOM crashes.

_retriever_embeddings = None
_qa_embeddings = None

# Guards construction of the two embedding singletons. Same double-checked
# pattern as `_es_client_lock`, but the stakes are higher here: on the local
# path each of these loads a sentence-transformers model into memory
# (BGE-large ≈ 1.3 GB, MiniLM ≈ 90 MB), single-threaded under
# OMP_NUM_THREADS=1. Two concurrent first callers would each load a full copy
# and one would then be discarded.
#
# Normally the eager block below preloads both at import, which hides the
# race. It is reachable whenever that preload does NOT run or does not
# succeed — i.e. when EMBEDDING_SERVICE_URL is set (preload skipped), or when
# the eager load raised and was swallowed as a warning, leaving these None for
# the first concurrent requests to fight over.
_embeddings_lock = threading.Lock()


def get_retriever_embeddings():
    """BGE-large-en-v1.5 for legal document retrieval (ES hybrid search, ChromaDB).

    Returns a LangChain-compatible Embeddings object — either a remote HTTP
    client (production) or a local HuggingFaceEmbeddings (development).
    """
    global _retriever_embeddings
    if _retriever_embeddings is not None:
        return _retriever_embeddings

    with _embeddings_lock:
        if _retriever_embeddings is not None:
            return _retriever_embeddings
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
    if _qa_embeddings is not None:
        return _qa_embeddings

    with _embeddings_lock:
        if _qa_embeddings is not None:
            return _qa_embeddings
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


# --- Eager-load both embedding models in local mode ---
# In remote mode the models live in the embed microservice; nothing to load
# here. In local mode (the prod default since 2026-06-28), we must load
# both BGE-large (retriever) and MiniLM (QA) at module import so that
# gunicorn's preload_app=True puts them in the master's address space
# BEFORE workers fork — each forked worker then inherits the resident
# models via copy-on-write. Resident memory: ~1.3 GB BGE + ~80 MB MiniLM
# = ~1.4 GB shared across master + all 6 workers (single copy, not 7
# copies). Both models are hot from request #1 on every worker.
#
# Without this, the first request that triggers an embed path pays the
# 3-15s model load inside the request hot path — counting against
# CHROMA_STORE_TIMEOUT_S (180s) for PDFs and against the relevance-gate
# budget for ES queries.
#
# Guarded by EMBEDDING_SERVICE_URL so remote-mode deployments skip
# the local load. Per-model try/except so a BGE failure doesn't block
# MiniLM (or vice versa). Eager-load failure falls back to lazy on
# first call — never blocks startup.
if not EMBEDDING_SERVICE_URL:
    for _name, _loader in (
        ("retriever (BGE-large)", get_retriever_embeddings),
        ("QA (MiniLM)", get_qa_embeddings),
    ):
        try:
            _t0 = time.time()
            _loader()
            _log.info("Embedding model eager-loaded at import",
                      model=_name, load_s=round(time.time() - _t0, 2))
        except Exception as _e:
            _log.warning("Embedding model eager-load failed; will lazy-load",
                         model=_name, error=str(_e)[:200])
