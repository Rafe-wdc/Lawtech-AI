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

from functools import lru_cache
from elasticsearch import Elasticsearch
from langchain.chat_models import init_chat_model
from langchain_community.embeddings import HuggingFaceEmbeddings
from google import genai

from .settings import (
    ELASTICSEARCH_URL,
    EMBEDDING_MODELS,
    EMBEDDING_SERVICE_URL,
)


# --- Elasticsearch ---

_es_client: Elasticsearch | None = None


def get_es_client(max_retries: int = 3, timeout: int = 30) -> Elasticsearch:
    """Get or create singleton Elasticsearch client with connection pooling."""
    global _es_client
    if _es_client is None:
        _es_client = Elasticsearch(
            ELASTICSEARCH_URL,
            request_timeout=timeout,
            max_retries=max_retries,
            retry_on_timeout=True,
        )
    return _es_client


# --- LLM Clients via init_chat_model (provider-agnostic, LangChain 1.0+) ---
# Pattern: init_chat_model("provider:model_name", **kwargs)
# Docs: https://docs.langchain.com/oss/python/langchain/models

@lru_cache(maxsize=1)
def get_gpt4o(temperature: float = 0.3):
    """GPT-4o for orchestrator, judgment metadata, task classification."""
    return init_chat_model("openai:gpt-4o", temperature=temperature, max_retries=2)


@lru_cache(maxsize=1)
def get_gpt4o_mini(temperature: float = 0.3):
    """GPT-4o-mini for draft selection, match phrase extraction."""
    return init_chat_model("openai:gpt-4o-mini", temperature=temperature, max_retries=2)


@lru_cache(maxsize=1)
def get_gemini_pro(temperature: float = 0.5):
    """Gemini 2.5 Pro for scenario analysis, PDF chat, relevance checking."""
    return init_chat_model("google_genai:gemini-2.5-pro", temperature=temperature)


@lru_cache(maxsize=1)
def get_gemini_flash(temperature: float = 0.3):
    """Gemini 2.5 Flash Lite for legal concepts, query rewriting, guardrails."""
    return init_chat_model("google_genai:gemini-2.5-flash-lite", temperature=temperature)


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
