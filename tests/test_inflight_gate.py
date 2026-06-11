"""Unit tests for the per-worker in-flight gate (Phase 3 backpressure).

Strategy: build a tiny FastAPI app that mounts a minimal copy of the gate
middleware, hits it with N concurrent slow requests against capacity K,
and asserts that exactly (N - K) requests get rejected with 503 +
Retry-After.

We do NOT import core.gateway because that pulls the whole agent graph,
which needs API keys, Postgres, OpenSearch, etc. Instead we exercise the
gate logic in isolation. The middleware code we test is a faithful copy
of the production middleware in core/gateway.py.
"""

from __future__ import annotations

import asyncio
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


def _build_app(*, capacity: int, slow_handler_seconds: float):
    """Build a FastAPI app with the gate at the given capacity."""
    app = FastAPI()
    sem = asyncio.Semaphore(capacity)
    rejections: list[str] = []

    @app.middleware("http")
    async def gate(request: Request, call_next):
        if request.url.path in {"/health", "/"}:
            return await call_next(request)
        try:
            await asyncio.wait_for(sem.acquire(), timeout=0.05)
        except asyncio.TimeoutError:
            rejections.append(request.url.path)
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": "5"},
                content={"error": "server_busy"},
            )
        try:
            return await call_next(request)
        finally:
            sem.release()

    @app.get("/slow")
    async def slow():
        await asyncio.sleep(slow_handler_seconds)
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    app.state.rejections = rejections
    return app


async def _fire_concurrent_async(app: FastAPI, path: str, n: int) -> list[int]:
    """Fire n concurrent requests against `path` in a single event loop.

    Uses httpx.AsyncClient + ASGITransport so all requests share the same
    loop the semaphore lives on. This mirrors production behaviour (single
    loop per uvicorn worker). The fastapi.testclient.TestClient pattern
    spawns thread-per-request, each with its own loop — that's incompatible
    with module-level asyncio primitives like Semaphore.
    """
    from httpx import AsyncClient, ASGITransport
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        responses = await asyncio.gather(*[client.get(path) for _ in range(n)])
    return [r.status_code for r in responses]


# -----------------------------------------------------------------------------
# Core behaviour: cap=3, fire 12, expect 9 rejections
# -----------------------------------------------------------------------------

def test_cap_3_fire_12_expect_9_rejections():
    """Plan §7.3: 12 concurrent slow requests against cap=3 → exactly 9× 503."""
    app = _build_app(capacity=3, slow_handler_seconds=0.5)
    statuses = asyncio.run(_fire_concurrent_async(app, "/slow", n=12))
    ok = sum(1 for s in statuses if s == 200)
    rejected = sum(1 for s in statuses if s == 503)
    assert ok == 3, f"expected 3 successful, got {ok}: {statuses}"
    assert rejected == 9, f"expected 9 rejected, got {rejected}: {statuses}"


def test_503_response_has_retry_after_header():
    """503 must carry Retry-After so clients backoff cleanly."""
    async def _body():
        app = _build_app(capacity=1, slow_handler_seconds=0.3)
        from httpx import AsyncClient, ASGITransport
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Two concurrent requests, cap=1 → first wins, second rejected.
            r1_task = asyncio.create_task(client.get("/slow"))
            await asyncio.sleep(0.02)  # let r1 acquire the slot
            r2 = await client.get("/slow")
            r1 = await r1_task
        assert r1.status_code == 200
        assert r2.status_code == 503
        assert r2.headers.get("Retry-After") == "5"
        assert r2.json()["error"] == "server_busy"
    asyncio.run(_body())


# -----------------------------------------------------------------------------
# Exclusion paths bypass the gate
# -----------------------------------------------------------------------------

def test_excluded_paths_bypass_gate():
    """Health endpoint must NOT hold a slot, even under saturation."""
    async def _body():
        app = _build_app(capacity=1, slow_handler_seconds=2.0)
        from httpx import AsyncClient, ASGITransport
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            slow_task = asyncio.create_task(client.get("/slow"))
            await asyncio.sleep(0.02)  # let /slow acquire the only slot
            health = await client.get("/health")
            assert health.status_code == 200
            await slow_task  # let it finish so the test cleans up
    asyncio.run(_body())


# -----------------------------------------------------------------------------
# Semaphore is released even when handler raises
# -----------------------------------------------------------------------------

def test_semaphore_released_on_exception():
    """Handler exception must not leak the slot — try/finally release."""
    app = FastAPI()
    sem = asyncio.Semaphore(1)

    @app.middleware("http")
    async def gate(request: Request, call_next):
        try:
            await asyncio.wait_for(sem.acquire(), timeout=0.05)
        except asyncio.TimeoutError:
            return JSONResponse(status_code=503, content={"error": "busy"})
        try:
            return await call_next(request)
        finally:
            sem.release()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("intentional")

    client = TestClient(app, raise_server_exceptions=False)
    # First request raises; slot should be released.
    r1 = client.get("/boom")
    assert r1.status_code == 500
    # Second request should succeed (slot was released) — not 503.
    r2 = client.get("/boom")
    assert r2.status_code == 500
    assert r2.status_code != 503


# -----------------------------------------------------------------------------
# Slot recycles correctly after a fast handler
# -----------------------------------------------------------------------------

def test_slot_recycles_after_fast_handler():
    """Sequential fast requests should never hit 503 at cap=1."""
    async def _body():
        app = _build_app(capacity=1, slow_handler_seconds=0.0)
        from httpx import AsyncClient, ASGITransport
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(20):
                r = await client.get("/slow")
                assert r.status_code == 200, f"unexpected 503: {r.status_code}"
    asyncio.run(_body())


def test_semaphore_released_on_exception_async():
    """Async variant of release-on-exception — uses the same loop pattern."""
    async def _body():
        app = FastAPI()
        sem = asyncio.Semaphore(1)

        @app.middleware("http")
        async def gate(request: Request, call_next):
            try:
                await asyncio.wait_for(sem.acquire(), timeout=0.05)
            except asyncio.TimeoutError:
                return JSONResponse(status_code=503, content={"error": "busy"})
            try:
                return await call_next(request)
            finally:
                sem.release()

        @app.get("/boom")
        async def boom():
            raise RuntimeError("intentional")

        from httpx import AsyncClient, ASGITransport
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.get("/boom")
            assert r1.status_code == 500
            # If the slot leaked, this would 503. Should be 500 (handler crash again).
            r2 = await client.get("/boom")
            assert r2.status_code == 500
            assert r2.status_code != 503
    asyncio.run(_body())
