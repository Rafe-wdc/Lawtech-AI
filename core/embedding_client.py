"""HTTP-backed LangChain Embeddings — calls the embedding microservice.

Drop-in replacement for HuggingFaceEmbeddings. Works with both:
- Direct vector computation: embeddings.embed_query(text) → List[float]
- ChromaDB integration: Chroma(embedding_function=embeddings)

Phase 7 added async methods (`aembed_query`/`aembed_documents`) backed by
`httpx.AsyncClient`. Use these from async handlers — they let the v2
worker's asyncio loop run other tasks while the embed RPC is in flight,
which is required to sustain 50+ concurrent users without saturating the
worker's thread pool.
"""

from __future__ import annotations

import os
from typing import List

import httpx
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from langchain_core.embeddings import Embeddings

from core.logger import get_logger

log = get_logger("EmbeddingClient")


# Module-level session with a connection pool + retry policy so a transient
# embedding-service blip doesn't fail the whole request. The remote service
# is CPU-bound and single-process by default, so the right pattern is:
#   - per-request timeouts (split connect + read so we fail fast on dead service)
#   - bounded retries with backoff on 5xx / connection errors
#   - pooled connections so we don't pay TCP handshake on every embed call
def _build_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=int(os.getenv("EMBEDDING_CLIENT_RETRIES", "2")),
        backoff_factor=0.5,                      # 0.5s, 1s
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(["POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        pool_connections=8, pool_maxsize=32, max_retries=retries,
    )
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


_session = _build_session()


# ── Async client (Phase 7) ────────────────────────────────────────────────
# One long-lived httpx.AsyncClient per worker process. Created lazily inside
# the first async embed call so we don't bind to an event loop at module
# import time (gunicorn master imports, then forks workers — the master's
# loop and the worker's loop are different objects).
#
# Pool sizing (per worker):
#   max_connections=100        plenty of slack vs the 10 in-flight gate cap
#   max_keepalive_connections=20 covers the steady-state without churn
# Timeouts mirror the sync client; env-tunable via EMBEDDING_CLIENT_*.

_async_client: httpx.AsyncClient | None = None


def _async_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=float(os.getenv("EMBEDDING_CLIENT_CONNECT_TIMEOUT", "3")),
        read=float(os.getenv("EMBEDDING_CLIENT_READ_TIMEOUT", "30")),
        write=10.0,
        pool=10.0,
    )


def _get_async_client() -> httpx.AsyncClient:
    """Lazy singleton, per-process. Safe to call from any async code path."""
    global _async_client
    if _async_client is None:
        _async_client = httpx.AsyncClient(
            timeout=_async_timeout(),
            limits=httpx.Limits(
                max_connections=int(os.getenv("EMBEDDING_CLIENT_POOL_MAX", "100")),
                max_keepalive_connections=int(
                    os.getenv("EMBEDDING_CLIENT_POOL_KEEPALIVE", "20"),
                ),
            ),
        )
    return _async_client


class RemoteEmbeddings(Embeddings):
    """LangChain-compatible embeddings that delegate to an HTTP service."""

    def __init__(
        self,
        base_url: str,
        model_name: str = "retriever",
        connect_timeout: float | None = None,
        read_timeout: float | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        # Split timeouts so a dead service fails fast (connect) but a slow
        # cold-start embed still completes (read). Both env-tunable.
        self._connect_timeout = (
            connect_timeout
            if connect_timeout is not None
            else float(os.getenv("EMBEDDING_CLIENT_CONNECT_TIMEOUT", "3"))
        )
        self._read_timeout = (
            read_timeout
            if read_timeout is not None
            else float(os.getenv("EMBEDDING_CLIENT_READ_TIMEOUT", "30"))
        )

    @property
    def timeout(self) -> tuple[float, float]:
        return (self._connect_timeout, self._read_timeout)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of texts. Called by ChromaDB for document storage."""
        # Batch in chunks of 50 to avoid oversized requests
        all_vectors = []
        for i in range(0, len(texts), 50):
            batch = texts[i : i + 50]
            try:
                resp = _session.post(
                    f"{self.base_url}/embed",
                    json={"texts": batch, "model": self.model_name},
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                all_vectors.extend(resp.json()["vectors"])
            except requests.RequestException as e:
                # Surface as a single, sanitized error — no stack trace leakage
                # to the caller (which often logs into user-facing pipelines).
                log.error("Embedding service call failed",
                          batch_size=len(batch), base_url=self.base_url,
                          error=str(e).splitlines()[0][:200])
                raise
        return all_vectors

    def embed_query(self, text: str) -> List[float]:
        """Embed a single query text. Called by ES hybrid search and ChromaDB retrieval."""
        return self.embed_documents([text])[0]

    # ── Async methods (Phase 7) ──────────────────────────────────────────
    # LangChain Embeddings ABC defines aembed_query/aembed_documents that
    # default to running the sync versions in a thread pool. Overriding
    # them with real async HTTP avoids the asyncio.to_thread hop, which
    # was the throughput cliff seen at 50+ concurrent users — the executor
    # saturates and queues, slowing every dependent agent step.

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        """Async batch embed. Used by ChromaDB async paths + agent code."""
        all_vectors: List[List[float]] = []
        client = _get_async_client()
        for i in range(0, len(texts), 50):
            batch = texts[i : i + 50]
            try:
                resp = await client.post(
                    f"{self.base_url}/embed",
                    json={"texts": batch, "model": self.model_name},
                )
                resp.raise_for_status()
                all_vectors.extend(resp.json()["vectors"])
            except httpx.HTTPError as e:
                log.error("Async embedding call failed",
                          batch_size=len(batch), base_url=self.base_url,
                          error=str(e).splitlines()[0][:200])
                raise
        return all_vectors

    async def aembed_query(self, text: str) -> List[float]:
        """Async single-query embed. Use from async handlers."""
        vecs = await self.aembed_documents([text])
        return vecs[0]
