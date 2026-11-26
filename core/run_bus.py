"""Wake-ups for background runs: Postgres LISTEN/NOTIFY, polling elsewhere.

The run store sends `pg_notify` when events are appended (EVENTS_CHANNEL,
payload "<run_id>:<seq>") and when a run is queued (QUEUED_CHANNEL). Each
process holds ONE listening connection and fans notifications out in memory:

  * web workers: every open SSE tail subscribes to its run and re-reads
    run_events when woken, instead of polling the table;
  * the runner: wakes its claim loop the moment a run is queued.

A notification is only a hint. Tails always re-read from their last seq and
also wake on a fallback timer (2 s while the listener is connected, 0.25 s
when it is not, and always 0.25 s on SQLite), so a lost notification or a
dropped listener costs latency, never events.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from core.logger import get_logger
from core.run_store import EVENTS_CHANNEL, QUEUED_CHANNEL, RunStore

log = get_logger("RunBus")

CONNECTED_FALLBACK_S = 2.0
POLL_FALLBACK_S = 0.25


class Subscription:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def notify(self) -> None:
        self._event.set()

    async def wait(self, timeout: float) -> bool:
        """True when notified (cleared again), False on timeout."""
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        self._event.clear()
        return True


async def _listen(store: RunStore, channel: str, on_payload: Callable[[str], None],
                  on_state: Callable[[bool], None]) -> None:
    """Hold a LISTEN connection, reconnecting with backoff, until cancelled."""
    import psycopg
    backoff = 1.0
    warned = False
    while True:
        try:
            conn = await psycopg.AsyncConnection.connect(store._postgres_url, autocommit=True)
            async with conn:
                await conn.execute(f"LISTEN {channel}")
                on_state(True)
                if warned:
                    log.info("Run listener reconnected", channel=channel)
                backoff, warned = 1.0, False
                async for n in conn.notifies():
                    on_payload(n.payload or "")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if not warned:
                log.warning("Run listener unavailable; falling back to polling",
                            channel=channel, error=str(e)[:200])
                warned = True
        finally:
            on_state(False)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30.0)


class RunEventBus:
    """One per process. Subscriptions are per open SSE tail."""

    def __init__(self, store: RunStore):
        self._store = store
        self._subs: dict[str, set[Subscription]] = {}
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = False

    def subscribe(self, run_id: str) -> Subscription:
        self._ensure_listening()
        sub = Subscription()
        self._subs.setdefault(run_id, set()).add(sub)
        return sub

    def unsubscribe(self, run_id: str, sub: Subscription) -> None:
        subs = self._subs.get(run_id)
        if subs is not None:
            subs.discard(sub)
            if not subs:
                self._subs.pop(run_id, None)

    def fallback_s(self) -> float:
        return CONNECTED_FALLBACK_S if self._connected else POLL_FALLBACK_S

    @property
    def connected(self) -> bool:
        return self._connected

    def _deliver(self, payload: str) -> None:
        run_id = payload.split(":", 1)[0]
        for sub in list(self._subs.get(run_id, ())):
            sub.notify()

    def _set_state(self, ok: bool) -> None:
        self._connected = ok

    def _ensure_listening(self) -> None:
        if not self._store.pg:
            return
        loop = asyncio.get_running_loop()
        if self._task is not None and self._loop is loop and not self._task.done():
            return
        self._loop = loop
        self._connected = False
        self._task = loop.create_task(
            _listen(self._store, EVENTS_CHANNEL, self._deliver, self._set_state),
            name="run-events-listener",
        )

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._connected = False


_buses: dict[int, RunEventBus] = {}


def get_bus(store: RunStore) -> RunEventBus:
    bus = _buses.get(id(store))
    if bus is None:
        bus = _buses[id(store)] = RunEventBus(store)
    return bus


async def shutdown() -> None:
    for bus in list(_buses.values()):
        await bus.close()


async def listen_for_queued(store: RunStore, wake: Callable[[], None]) -> None:
    """Runner side: call `wake()` whenever a run is queued. Returns at once on
    SQLite (the runner polls)."""
    if not store.pg:
        return
    await _listen(store, QUEUED_CHANNEL, lambda _p: wake(), lambda _ok: None)
