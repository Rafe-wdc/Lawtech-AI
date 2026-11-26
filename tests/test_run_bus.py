"""Postgres LISTEN/NOTIFY wake-ups for background runs (2026-11-26).

Needs TEST_POSTGRES_URL. Runs on a SelectorEventLoop: psycopg's async mode
does not support Windows' default ProactorEventLoop (on Linux, where prod
runs, the default loop and uvloop both work; the checkpointer already uses
async psycopg there).
"""
from __future__ import annotations

import asyncio
import os
import threading
import time

import pytest

from core.run_bus import CONNECTED_FALLBACK_S, POLL_FALLBACK_S, RunEventBus, listen_for_queued
from core.run_store import RunStore

PG_URL = os.getenv("TEST_POSTGRES_URL", "")


def run_selector(coro):
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)


@pytest.fixture
def pg_store():
    if not PG_URL:
        pytest.skip("TEST_POSTGRES_URL not set")
    import psycopg
    with psycopg.connect(PG_URL, autocommit=True) as c:
        for t in ("run_outbox", "run_watchers", "run_events", "run_workers", "runs"):
            c.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    st = RunStore(postgres_url=PG_URL)
    st.ensure_schema()
    yield st
    st.close()


async def _until(pred, timeout=5.0):
    t0 = time.monotonic()
    while not pred():
        assert time.monotonic() - t0 < timeout, "condition not met"
        await asyncio.sleep(0.02)


def _append_later(store, run_id, token, delay):
    def go():
        time.sleep(delay)
        store.append_events(run_id, token, [{"type": "token", "content": "x"}])
    threading.Thread(target=go, daemon=True).start()


def test_notify_wakes_a_tail_long_before_the_fallback(pg_store):
    r = pg_store.create_run(thread_id="t", endpoint="/pyapi/search/stream", request={"query": "q"})
    claim = pg_store.claim("w")

    async def body():
        bus = RunEventBus(pg_store)
        sub = bus.subscribe(r.run_id)
        await _until(lambda: bus.connected)
        assert bus.fallback_s() == CONNECTED_FALLBACK_S
        _append_later(pg_store, r.run_id, claim.lease_token, 0.2)
        t0 = time.monotonic()
        woke = await sub.wait(CONNECTED_FALLBACK_S)
        latency = time.monotonic() - t0
        # a notification for another run does not wake this tail
        other = bus.subscribe("someone-else")
        _append_later(pg_store, r.run_id, claim.lease_token, 0.0)
        other_woke = await other.wait(0.5)
        await bus.close()
        return woke, latency, other_woke
    woke, latency, other_woke = run_selector(body())
    assert woke is True and latency < 0.6, latency
    assert other_woke is False


def test_listener_recovers_after_its_connection_is_killed(pg_store):
    import psycopg
    r = pg_store.create_run(thread_id="t", endpoint="/pyapi/search/stream", request={"query": "q"})
    claim = pg_store.claim("w")

    async def body():
        bus = RunEventBus(pg_store)
        sub = bus.subscribe(r.run_id)
        await _until(lambda: bus.connected)
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                      "WHERE query LIKE 'LISTEN lawtech_run_events%' AND pid <> pg_backend_pid()")
        await _until(lambda: not bus.connected, 5)
        assert bus.fallback_s() == POLL_FALLBACK_S         # tails poll while it is down
        await _until(lambda: bus.connected, 10)            # reconnects with backoff
        _append_later(pg_store, r.run_id, claim.lease_token, 0.1)
        woke = await sub.wait(CONNECTED_FALLBACK_S)
        await bus.close()
        return woke
    assert run_selector(body()) is True


def test_runner_is_woken_when_a_run_is_queued(pg_store):
    async def body():
        woken = asyncio.Event()
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(listen_for_queued(pg_store, lambda: loop.call_soon(woken.set)))
        await asyncio.sleep(0.5)                           # let LISTEN start
        t0 = time.monotonic()
        threading.Thread(target=lambda: pg_store.create_run(
            thread_id="tq", endpoint="/pyapi/search/stream", request={"query": "q"}),
            daemon=True).start()
        await asyncio.wait_for(woken.wait(), 3)
        latency = time.monotonic() - t0
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return latency
    assert run_selector(body()) < 1.0


def test_sqlite_has_no_listener(tmp_path):
    st = RunStore(sqlite_path=str(tmp_path / "r.db"))

    async def body():
        bus = RunEventBus(st)
        bus.subscribe("x")
        assert bus.connected is False and bus.fallback_s() == POLL_FALLBACK_S
        await asyncio.wait_for(listen_for_queued(st, lambda: None), 1)   # returns at once
    asyncio.run(body())
