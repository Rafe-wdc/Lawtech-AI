"""Unit tests for RemoteEmbeddings.aembed_query / aembed_documents.

Strategy: spin up a tiny in-process stub server that mimics the embed
service's /embed contract, then verify:
  1. aembed_query returns a vector of the right shape
  2. aembed_documents handles batching correctly (50/batch, multi-batch)
  3. The async path doesn't block — we can fan out N concurrent
     aembed_query calls and finish in less time than N × single-call
     latency (proves we're truly async, not silently thread-pooled)

No network. No event loop assumptions across threads. Vanilla pytest +
asyncio.run.
"""

from __future__ import annotations

import asyncio
import time
from typing import List

from fastapi import FastAPI, Request


# ─────────────────────────────────────────────────────────────────────────────
# Stub embed server (deterministic, with a small per-call latency to make
# concurrency measurable). Reads body manually so we don't fight FastAPI's
# Pydantic auto-inference (a BaseModel defined inside a function gets
# interpreted as query params — gotcha).

def _build_stub_app(per_call_sleep_s: float = 0.0) -> FastAPI:
    app = FastAPI()
    state = {"calls": 0, "max_concurrent": 0, "in_flight": 0}
    lock = asyncio.Lock()

    @app.post("/embed")
    async def embed(request: Request):
        data = await request.json()
        texts: List[str] = data.get("texts", [])
        model: str = data.get("model", "retriever")
        async with lock:
            state["in_flight"] += 1
            state["max_concurrent"] = max(state["max_concurrent"], state["in_flight"])
        if per_call_sleep_s > 0:
            await asyncio.sleep(per_call_sleep_s)
        async with lock:
            state["in_flight"] -= 1
            state["calls"] += 1
        # Deterministic fake vector — first dim = len(text) so tests can
        # assert response shape easily.
        vectors = [[float(len(t)), 0.0, 0.0] for t in texts]
        return {"vectors": vectors, "model": model, "count": len(vectors)}

    app.state.stats = state
    return app


def _client_against(app: FastAPI):
    """Build a RemoteEmbeddings that talks to the in-process FastAPI app."""
    import httpx
    from core import embedding_client

    # Patch the module-level async client to use ASGI transport against `app`.
    # This avoids needing a real network listener and works in any test runner.
    transport = httpx.ASGITransport(app=app)
    embedding_client._async_client = httpx.AsyncClient(
        transport=transport,
        base_url="http://stub",
        timeout=httpx.Timeout(connect=2.0, read=10.0, write=10.0, pool=10.0),
    )
    emb = embedding_client.RemoteEmbeddings(base_url="http://stub", model_name="retriever")
    return emb


def _reset_client():
    from core import embedding_client
    if embedding_client._async_client is not None:
        # Don't bother awaiting aclose — test scope is short-lived
        embedding_client._async_client = None


# ─────────────────────────────────────────────────────────────────────────────
# Tests

def test_aembed_query_returns_vector():
    """Basic happy path: single query → single vector."""
    async def _body():
        app = _build_stub_app()
        emb = _client_against(app)
        try:
            vec = await emb.aembed_query("hello world")
            assert isinstance(vec, list)
            assert len(vec) == 3
            assert vec[0] == 11.0  # len("hello world")
        finally:
            _reset_client()
    asyncio.run(_body())


def test_aembed_documents_batching():
    """120 texts → should fan out as 3 batches of 50/50/20."""
    async def _body():
        app = _build_stub_app()
        emb = _client_against(app)
        try:
            texts = [f"doc{i}" for i in range(120)]
            vecs = await emb.aembed_documents(texts)
            assert len(vecs) == 120
            # Stub records call count
            stats = app.state.stats
            assert stats["calls"] == 3, f"expected 3 batches, got {stats['calls']}"
        finally:
            _reset_client()
    asyncio.run(_body())


def test_aembed_query_truly_async_concurrency():
    """20 concurrent aembed_query calls should overlap in time.

    If aembed_query secretly blocks (e.g. thread-pool fallback with limited
    pool), max_concurrent on the server side will stay low. Real async means
    most/all 20 hit the server at once. Stub adds 0.2s per call.
    """
    async def _body():
        app = _build_stub_app(per_call_sleep_s=0.2)
        emb = _client_against(app)
        try:
            t0 = time.perf_counter()
            results = await asyncio.gather(*[
                emb.aembed_query(f"q{i}") for i in range(20)
            ])
            elapsed = time.perf_counter() - t0
            assert len(results) == 20
            # Sequential cost would be 20 × 0.2 = 4.0 s. True async should
            # finish in just over 0.2 s. Allow generous slack for asyncio
            # scheduling and ASGITransport overhead.
            assert elapsed < 1.5, f"too slow ({elapsed:.2f}s) — looks sequential"
            stats = app.state.stats
            # All 20 should have arrived at the stub roughly at once.
            assert stats["max_concurrent"] >= 15, (
                f"expected ~20 concurrent, got max={stats['max_concurrent']}"
            )
        finally:
            _reset_client()
    asyncio.run(_body())


def test_sync_embed_query_still_works():
    """Phase 7 must NOT regress the sync path used by ChromaDB integration."""
    async def _noop():
        return None
    # We can't easily test sync against the in-process stub without spinning
    # up a real server (requests.Session can't talk ASGI). The bar here is
    # simpler: verify the sync class methods exist and have the expected
    # signature so callers like ChromaDB don't crash on a missing attr.
    from core.embedding_client import RemoteEmbeddings
    emb = RemoteEmbeddings(base_url="http://nowhere", model_name="retriever")
    assert callable(emb.embed_query)
    assert callable(emb.embed_documents)
    assert callable(emb.aembed_query)
    assert callable(emb.aembed_documents)
