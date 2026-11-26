"""The runner: executes background runs, independent of any HTTP request.

    python -m workers.run_worker          (systemd: lawttorney-runner.service)

The web tier (FastAPI under gunicorn) only queues runs and streams their
stored events; gunicorn recycles its workers, which is why generation lives
here instead. Design: Background_Runs_Implementation_Plan.md section 5.

One process runs up to RUNNER_CONCURRENCY runs at once. For each run:
  claim (lease + fencing token) -> execute the unchanged chat pipeline ->
  store every event as it is produced (tokens batched per RUN_TOKEN_FLUSH_MS
  or RUN_TOKEN_FLUSH_BYTES) -> finalize (status + final events + outbox row
  in one transaction).
A heartbeat renews each lease every quarter of RUN_LEASE_S. If a renewal or
any write finds the lease gone (expired, reclaimed, or the user pressed
Stop), the run's pipeline is cancelled at once and nothing more is written.

Alongside: a sweeper (recovers runs whose runner died, expires stale queue
entries, deletes old rows), a liveness row for this process, and the outbox
dispatcher that tells Node a run finished.

SIGTERM: stop claiming, let in-flight runs finish (systemd allows 540 s,
above the 480 s pipeline deadline), then exit 0. A second SIGTERM, or the
drain timeout, cancels what is left; those runs' leases are released so the
next sweep recovers them.

With BACKGROUND_RUNS off the process drains: it finishes any run still
queued or running, then exits 0 (systemd Restart=on-failure leaves it down).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable

from core.logger import get_logger, set_request_id
from core.run_store import Claim, LeaseLost, RunRow, RunStore, terminal_done_event

log = get_logger("RunWorker")

Executor = Callable[[Claim], AsyncIterator[str]]

CRASH_MESSAGE = "Something went wrong while preparing the answer. Please try again."
NO_ANSWER_MESSAGE = "No answer was produced. Please try again."


def parse_sse(chunk: str) -> list[dict]:
    """The JSON events in one or more SSE messages. Comments (': ping') and
    malformed lines are skipped."""
    out: list[dict] = []
    for block in chunk.split("\n\n"):
        data = [ln[5:].lstrip() for ln in block.split("\n") if ln.startswith("data:")]
        if not data:
            continue
        try:
            ev = json.loads("\n".join(data))
        except ValueError:
            log.warning("Unparseable SSE event skipped", preview=block[:120])
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(len(s) - 1, int(0.95 * len(s)))], 1)


class EventSink:
    """Writes a run's events to the store in order, batching tokens.

    Consecutive `token` events are joined into one stored `token` event and
    written when RUN_TOKEN_FLUSH_MS has passed since the first of them, when
    RUN_TOKEN_FLUSH_BYTES have accumulated, or when any other event arrives
    (which is written right after, keeping order). A `token_reset` is "any
    other event", so tokens before a reset never merge with tokens after it.
    """

    def __init__(self, store: RunStore, run_id: str, lease_token: str, *,
                 flush_ms: int, flush_bytes: int, on_lost: Callable[[], None]):
        self._store = store
        self._run_id = run_id
        self._token = lease_token
        self._flush_s = max(0.0, flush_ms / 1000.0)
        self._flush_bytes = max(1, flush_bytes)
        self._on_lost = on_lost
        self._buf: list[str] = []
        self._buf_bytes = 0
        self._buf_t0: float | None = None
        self._lock = asyncio.Lock()
        self._timer: asyncio.Task | None = None
        self.rows = 0
        self.token_rows = 0
        self.tokens_in = 0
        self.persist_ms: list[float] = []

    async def add(self, ev: dict) -> None:
        if ev.get("type") == "token":
            content = ev.get("content") or ""
            async with self._lock:
                self.tokens_in += 1
                self._buf.append(content)
                self._buf_bytes += len(content.encode("utf-8"))
                if self._buf_t0 is None:
                    self._buf_t0 = time.monotonic()
                    self._ensure_timer()
                if self._buf_bytes >= self._flush_bytes:
                    await self._flush_locked([])
            return
        async with self._lock:
            await self._flush_locked([ev])

    async def close(self) -> None:
        self._stop_timer()
        async with self._lock:
            await self._flush_locked([])

    def abort(self) -> None:
        self._stop_timer()

    def _ensure_timer(self) -> None:
        if self._timer is None or self._timer.done():
            self._timer = asyncio.create_task(self._timer_loop(), name=f"flush-{self._run_id[:8]}")

    def _stop_timer(self) -> None:
        if self._timer is not None and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    async def _timer_loop(self) -> None:
        tick = max(0.01, self._flush_s / 2)
        try:
            while True:
                await asyncio.sleep(tick)
                async with self._lock:
                    if self._buf_t0 is not None and time.monotonic() - self._buf_t0 >= self._flush_s:
                        await self._flush_locked([])
        except LeaseLost:
            self._on_lost()
        except asyncio.CancelledError:
            pass
        except Exception as e:     # a transient DB error: the next add() retries the write
            log.warning("Token flush failed; will retry on next event",
                        run_id=self._run_id[:12], error=str(e)[:200])

    async def _flush_locked(self, extra: list[dict]) -> None:
        events: list[dict] = []
        if self._buf:
            events.append({"type": "token", "content": "".join(self._buf)})
        events.extend(extra)
        if not events:
            return
        t0 = time.perf_counter()
        await self._store.aio.append_events(self._run_id, self._token, events)
        self.persist_ms.append((time.perf_counter() - t0) * 1000)
        if self._buf:
            self.token_rows += 1
            self._buf, self._buf_bytes, self._buf_t0 = [], 0, None
        self.rows += len(events)


@dataclass
class Outcome:
    """What the pipeline produced, read off its events."""
    response: str | None = None
    tokens: list[str] = field(default_factory=list)
    sources: list | None = None
    errors: list[str] = field(default_factory=list)
    done: dict | None = None
    crashed: bool = False

    def observe(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "token":
            self.tokens.append(ev.get("content") or "")
        elif t == "token_reset":
            self.tokens = []
        elif t == "response":
            self.response = ev.get("content") or ""
        elif t == "sources":
            self.sources = ev.get("data") or []
        elif t == "error":
            self.errors.append(str(ev.get("data") or ev.get("message") or "error"))
        elif t == "done":
            self.done = ev

    def finish(self, run: RunRow) -> dict:
        """finalize() arguments. A run is done when the pipeline delivered a
        response without an error; otherwise failed, keeping any text the
        user already saw (partial answers are kept, not discarded)."""
        text = self.response if self.response is not None else "".join(self.tokens)
        if self.crashed or self.done is None:
            msg = self.errors[-1] if self.errors else (CRASH_MESSAGE if self.crashed else NO_ANSWER_MESSAGE)
            events = [] if self.errors else [{"type": "error", "data": msg, "run_id": run.run_id}]
            events.append(terminal_done_event(run, failed=True))
            return {"status": "failed", "response_text": text, "error": msg[:500],
                    "final_events": events, "sources": self.sources}
        done = dict(self.done)
        done["run_id"] = run.run_id
        ok = (self.response is not None and not self.errors and not done.get("upload_failed"))
        turn = done.get("conversation_turn")
        return {
            "status": "done" if ok else "failed",
            "response_text": text,
            "error": "" if ok else (self.errors[-1] if self.errors else NO_ANSWER_MESSAGE)[:500],
            "token_usage": done.get("token_usage") or None,
            "turn_number": int(turn) if isinstance(turn, int) and turn > 0 else None,
            "final_events": [done],
            "sources": self.sources,
            "agents_used": done.get("agents_used"),
        }


@dataclass
class _RunState:
    claim: Claim
    task: asyncio.Task | None = None
    lost: bool = False
    forced: bool = False
    finishing: bool = False        # past the pipeline: lease signals no longer cancel


class RunWorker:
    def __init__(
        self,
        store: RunStore,
        executor: Executor,
        *,
        concurrency: int,
        lease_s: int,
        token_flush_ms: int = 250,
        token_flush_bytes: int = 2048,
        poll_s: float = 1.0,
        sweep_every_s: float = 15.0,
        liveness_every_s: float = 10.0,
        outbox_every_s: float = 2.0,
        drain_timeout_s: float = 520.0,
        turn_lookup: Callable[[str], dict | None] | None = None,
        outbox_dispatcher: Callable[[], Awaitable[None]] | None = None,
        on_run_finished: Callable[[Claim], Awaitable[None] | None] | None = None,
        exit_when_idle: bool = False,
        worker_id: str | None = None,
        staging_root: str | None = None,
        staging_max_age_s: float = 3600.0,
    ):
        self.store = store
        self.executor = executor
        self.concurrency = max(1, concurrency)
        self.lease_s = lease_s
        self.flush_ms = token_flush_ms
        self.flush_bytes = token_flush_bytes
        self.poll_s = poll_s
        self.sweep_every_s = sweep_every_s
        self.liveness_every_s = liveness_every_s
        self.outbox_every_s = outbox_every_s
        self.drain_timeout_s = drain_timeout_s
        self.turn_lookup = turn_lookup
        self.outbox_dispatcher = outbox_dispatcher
        self.on_run_finished = on_run_finished
        self.exit_when_idle = exit_when_idle
        self.staging_root = staging_root
        self.staging_max_age_s = staging_max_age_s
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self._slots = asyncio.Semaphore(self.concurrency)
        self._runs: dict[str, _RunState] = {}
        self._stopping = asyncio.Event()
        self._wake = asyncio.Event()
        self.finished: list[str] = []          # run ids, for tests and the summary log

    # -- control --------------------------------------------------------------

    def wake(self) -> None:
        """A run was queued (LISTEN/NOTIFY): claim now instead of at the next poll."""
        self._wake.set()

    def request_stop(self) -> None:
        if not self._stopping.is_set():
            log.info("Runner stopping: no new claims; finishing in-flight runs",
                     worker=self.worker_id, in_flight=len(self._runs))
        self._stopping.set()
        self._wake.set()

    def force_stop(self) -> None:
        self.request_stop()
        for st in list(self._runs.values()):
            st.forced = True
            if st.task and not st.task.done():
                st.task.cancel()

    @property
    def in_flight(self) -> int:
        return len(self._runs)

    # -- main loop --------------------------------------------------------------

    async def run_forever(self) -> None:
        log.info("Runner started", worker=self.worker_id, concurrency=self.concurrency,
                 lease_s=self.lease_s, drain_mode=self.exit_when_idle)
        loops = [asyncio.create_task(self._every(self.sweep_every_s, self._sweep_once), name="sweeper"),
                 asyncio.create_task(self._every(self.liveness_every_s, self._liveness), name="liveness")]
        if self.outbox_dispatcher is not None:
            loops.append(asyncio.create_task(self._every(self.outbox_every_s, self.outbox_dispatcher),
                                             name="outbox"))
        try:
            await self._liveness()
            await self._claim_loop()
            await self._drain()
        finally:
            for t in loops:
                t.cancel()
            await asyncio.gather(*loops, return_exceptions=True)
            try:
                await self.store.aio.remove_worker(self.worker_id)
            except Exception as e:
                log.warning("Could not remove runner liveness row", error=str(e)[:200])
            log.info("Runner stopped", worker=self.worker_id, finished=len(self.finished))

    async def _claim_loop(self) -> None:
        while not self._stopping.is_set():
            await self._slots.acquire()
            if self._stopping.is_set():
                self._slots.release()
                break
            try:
                claim = await self.store.aio.claim(self.worker_id, self.lease_s)
            except Exception as e:
                self._slots.release()
                log.error("Claim failed", error=str(e)[:200])
                await self._idle()
                continue
            if claim is None:
                self._slots.release()
                if self.exit_when_idle and not self._runs and await self._queue_empty():
                    log.info("Drain mode: nothing left to run; exiting", worker=self.worker_id)
                    self._stopping.set()
                    break
                await self._idle()
                continue
            st = _RunState(claim=claim)
            self._runs[claim.run.run_id] = st
            st.task = asyncio.create_task(self._run_one(st), name=f"run-{claim.run.run_id[:8]}")

    async def _idle(self) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=self.poll_s)
        except asyncio.TimeoutError:
            pass

    async def _queue_empty(self) -> bool:
        try:
            c = await self.store.aio.counts()
            return c["queued"] == 0 and c["running"] == 0
        except Exception:
            return False

    async def _drain(self) -> None:
        # Stop advertising this runner first: while it drains, the front door
        # either uses another runner or answers inline, instead of queueing
        # work nobody here will claim.
        try:
            await self.store.aio.remove_worker(self.worker_id)
        except Exception as e:
            log.warning("Could not remove runner liveness row", error=str(e)[:200])
        tasks = [st.task for st in self._runs.values() if st.task]
        if not tasks:
            return
        log.info("Waiting for in-flight runs", count=len(tasks), timeout_s=self.drain_timeout_s)
        _, pending = await asyncio.wait(tasks, timeout=self.drain_timeout_s)
        if pending:
            log.error("Drain timeout: cancelling runs still in flight; they will be recovered",
                      count=len(pending))
            self.force_stop()
            await asyncio.gather(*pending, return_exceptions=True)

    async def _every(self, interval_s: float, fn: Callable[[], Awaitable[None]]) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("Runner periodic task failed", task=getattr(fn, "__name__", "?"),
                          error=str(e)[:200])

    async def _sweep_once(self) -> None:
        await self.store.aio.sweep(self.turn_lookup)
        if self.staging_root:
            await asyncio.to_thread(self._clean_staging)

    def _clean_staging(self) -> int:
        """Delete staged uploads of runs that will never read them (finished,
        cancelled while queued, expired, or gone) once they are an hour old.
        A run that executes deletes its own folder; this catches the rest."""
        import shutil
        try:
            names = os.listdir(self.staging_root)
        except FileNotFoundError:
            return 0
        removed = 0
        cutoff = time.time() - self.staging_max_age_s
        for name in names:
            path = os.path.join(self.staging_root, name)
            try:
                if not os.path.isdir(path) or os.path.getmtime(path) > cutoff:
                    continue
                run = self.store.get_run(name)
                if run is not None and run.is_active:
                    continue
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
            except Exception as e:
                log.warning("Staging cleanup skipped a folder", folder=name, error=str(e)[:160])
        if removed:
            log.info("Staging cleanup", removed=removed)
        return removed

    async def _liveness(self) -> None:
        if self._stopping.is_set():
            return                      # draining: no longer taking runs
        await self.store.aio.worker_heartbeat(self.worker_id, concurrency=self.concurrency,
                                              running=len(self._runs))

    # -- one run ----------------------------------------------------------------

    def _mark_lost(self, st: _RunState) -> None:
        if st.finishing:
            return
        if not st.lost:
            st.lost = True
            if st.task and not st.task.done():
                st.task.cancel()

    async def _lease_heartbeat(self, st: _RunState) -> None:
        run_id, token = st.claim.run.run_id, st.claim.lease_token
        every = max(0.05, self.lease_s / 4)
        while True:
            await asyncio.sleep(every)
            try:
                ok = await self.store.aio.heartbeat(run_id, token, self.lease_s)
            except Exception as e:
                log.warning("Lease heartbeat failed; retrying", run_id=run_id[:12], error=str(e)[:200])
                continue
            if not ok:
                log.warning("Lease lost (cancelled, expired or reclaimed); stopping run",
                            run_id=run_id[:12])
                self._mark_lost(st)
                return

    async def _run_one(self, st: _RunState) -> None:
        claim = st.claim
        run = claim.run
        set_request_id(run.thread_id[:8])
        t_start = time.perf_counter()
        queue_wait_ms = 0
        if run.created_at and run.started_at:
            queue_wait_ms = int((run.started_at - run.created_at).total_seconds() * 1000)
        hb = asyncio.create_task(self._lease_heartbeat(st), name=f"lease-{run.run_id[:8]}")
        sink = EventSink(self.store, run.run_id, claim.lease_token, flush_ms=self.flush_ms,
                         flush_bytes=self.flush_bytes, on_lost=lambda: self._mark_lost(st))
        outcome = Outcome()
        status = "lost"
        agen = None

        async def stop_heartbeat() -> None:
            # Before finalize or release_lease: a renewal landing after either
            # would extend a lease we just gave up, or see the run finished
            # and cancel our own cleanup.
            if not hb.done():
                hb.cancel()
            await asyncio.gather(hb, return_exceptions=True)

        try:
            try:
                agen = self.executor(claim)
                async for chunk in agen:
                    for ev in parse_sse(chunk):
                        if ev.get("type") == "thread_id":
                            continue                  # the front door already sent it
                        outcome.observe(ev)
                        if ev.get("type") == "done":
                            continue                  # written by finalize, atomically
                        await sink.add(ev)
            except (LeaseLost, asyncio.CancelledError):
                raise
            except Exception as e:
                outcome.crashed = True
                log.error("Run executor raised", run_id=run.run_id[:12], error=str(e)[:300],
                          exc_info=True)
            await sink.close()
            st.finishing = True
            await stop_heartbeat()
            fin = outcome.finish(run)
            await self.store.aio.finalize(run.run_id, claim.lease_token, **fin)
            status = fin["status"]
        except LeaseLost:
            log.warning("Run stopped: lease lost", run_id=run.run_id[:12])
        except asyncio.CancelledError:
            st.finishing = True
            await stop_heartbeat()
            if not st.lost:
                if st.forced:
                    status = "abandoned"
                    try:
                        await self.store.aio.release_lease(run.run_id, claim.lease_token)
                    except Exception:
                        pass
                    log.error("Run abandoned at shutdown; lease released for recovery",
                              run_id=run.run_id[:12])
                raise
            # Cancelled by our own lease check: the run is over, not the worker.
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
            log.warning("Run stopped: lease lost", run_id=run.run_id[:12])
        finally:
            st.finishing = True
            sink.abort()
            hb.cancel()
            if agen is not None:
                try:
                    await agen.aclose()       # runs the pipeline's own cleanup
                except BaseException:
                    pass
            self._runs.pop(run.run_id, None)
            self._slots.release()
            self.finished.append(run.run_id)
            if self.on_run_finished is not None:
                try:
                    res = self.on_run_finished(claim)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as e:
                    log.warning("Run cleanup hook failed", run_id=run.run_id[:12], error=str(e)[:200])
            log.info(
                "run_completed",
                run_id=run.run_id[:12], thread_id=run.thread_id[:12], status=status,
                attempt=run.attempts, events=sink.rows, token_rows=sink.token_rows,
                tokens_in=sink.tokens_in, queue_wait_ms=queue_wait_ms,
                persist_p95_ms=_p95(sink.persist_ms),
                duration_ms=int((time.perf_counter() - t_start) * 1000),
            )


# ---------------------------------------------------------------------------
# Process entry point
# ---------------------------------------------------------------------------

def _install_signals(loop: asyncio.AbstractEventLoop, worker: RunWorker) -> None:
    hits = {"n": 0}

    def on_signal(*_):
        hits["n"] += 1
        if hits["n"] == 1:
            loop.call_soon_threadsafe(worker.request_stop)
        else:
            log.error("Second stop signal: cancelling in-flight runs")
            loop.call_soon_threadsafe(worker.force_stop)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, on_signal)
        except (NotImplementedError, RuntimeError):   # Windows
            signal.signal(sig, on_signal)


async def _amain() -> None:
    from core import settings as S
    from core.chat_store import chat_store
    from core.run_store import get_run_store

    store = get_run_store()
    store.ensure_schema()

    if not S.BACKGROUND_RUNS:
        counts = store.counts()
        if counts["queued"] == 0 and counts["running"] == 0:
            log.info("BACKGROUND_RUNS off and no run left to finish; exiting")
            store.close()
            return

    from core.run_executor import PipelineExecutor
    executor = await PipelineExecutor.create(warm_up=S.RUNNER_WARMUP)

    from core.run_outbox import OutboxDispatcher
    dispatcher = OutboxDispatcher(store)

    worker = RunWorker(
        store, executor,
        concurrency=S.RUNNER_CONCURRENCY,
        lease_s=S.RUN_LEASE_S,
        token_flush_ms=S.RUN_TOKEN_FLUSH_MS,
        token_flush_bytes=S.RUN_TOKEN_FLUSH_BYTES,
        turn_lookup=chat_store._get_turn_by_run_id_sync,
        outbox_dispatcher=dispatcher.dispatch_once,
        on_run_finished=executor.cleanup,
        exit_when_idle=not S.BACKGROUND_RUNS,
        staging_root=S.RUN_STAGING_ROOT,
    )
    _install_signals(asyncio.get_running_loop(), worker)

    from core.run_bus import listen_for_queued
    listener = asyncio.create_task(listen_for_queued(store, worker.wake), name="queued-listener")
    try:
        await worker.run_forever()
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)
        await executor.close()
        store.close()


def main() -> None:
    import sys
    # psycopg's async mode (LISTEN for queued runs) cannot use Windows'
    # default ProactorEventLoop; Linux keeps the default loop.
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(_amain(), loop_factory=factory)


if __name__ == "__main__":
    main()
