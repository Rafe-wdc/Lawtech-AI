"""The runner process (background runs, 2026-11-26), driven by a fake pipeline.

Covers: a run end to end, token batching (by size and by time), failures
keeping partial text, Stop stopping the pipeline within a heartbeat,
concurrency limits, graceful and forced shutdown, recovery by another runner
after a crash (retry before output, fail after output), drain mode, and the
latency the event store adds.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from core.run_store import RunStore
from workers.run_worker import EventSink, RunWorker, parse_sse


def sse(ev: dict) -> str:
    return f"data: {json.dumps(ev)}\n\n"


def answer_events(tokens: list[str], turn: int = 1) -> list[dict]:
    text = "".join(tokens)
    return ([{"type": "status", "agent": "memory", "message": "Loading..."}]
            + [{"type": "token", "content": t} for t in tokens]
            + [{"type": "response", "content": text},
               {"type": "sources", "data": [{"title": "Case A"}]},
               {"type": "done", "agents_used": ["Judgment"], "conversation_turn": turn,
                "token_usage": {"total_tokens": 42}, "thread_id": "t"}])


class Script:
    """A fake pipeline: yields events, can pause, crash, or hang, and records
    whether it was closed (cancelled) and how many ran at once."""

    def __init__(self, events, *, delay=0.0, crash_after=None, hang_after=None,
                 gate: asyncio.Event | None = None, gate_after=None):
        self.events = events
        self.delay = delay
        self.crash_after = crash_after
        self.hang_after = hang_after
        self.gate = gate
        self.gate_after = gate_after
        self.closed: list[str] = []
        self.active = 0
        self.max_active = 0
        self.claims = []

    async def __call__(self, claim):
        self.claims.append(claim)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            for i, ev in enumerate(self.events):
                if self.crash_after is not None and i == self.crash_after:
                    raise RuntimeError("pipeline exploded")
                if self.hang_after is not None and i == self.hang_after:
                    await asyncio.sleep(3600)
                if self.gate is not None and i == self.gate_after:
                    await self.gate.wait()
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield sse(ev)
            if self.hang_after is not None and self.hang_after >= len(self.events):
                await asyncio.sleep(3600)           # hang after the last event
        except (asyncio.CancelledError, GeneratorExit):
            self.closed.append(claim.run.run_id)
            raise
        finally:
            self.active -= 1


@pytest.fixture
def store(tmp_path):
    s = RunStore(sqlite_path=str(tmp_path / "runs.db"), callback_enabled=lambda st: True)
    s.ensure_schema()
    return s


def worker(store, script, **kw):
    opts = dict(concurrency=2, lease_s=1, token_flush_ms=50, token_flush_bytes=2048,
                poll_s=0.05, sweep_every_s=0.2, liveness_every_s=0.2, drain_timeout_s=5)
    opts.update(kw)
    return RunWorker(store, script, **opts)


def queue(store, thread="t1"):
    return store.create_run(thread_id=thread, endpoint="/pyapi/search/stream",
                            request={"query": "q"})


async def until(pred, timeout=5.0, every=0.02):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if await asyncio.to_thread(pred) if not asyncio.iscoroutinefunction(pred) else await pred():
            return True
        await asyncio.sleep(every)
    raise AssertionError("condition not met in time")


async def run_until_finished(w: RunWorker, store, run_ids, timeout=5.0):
    task = asyncio.create_task(w.run_forever())
    await until(lambda: all(store.get_run(r).status in ("done", "failed", "cancelled")
                            for r in run_ids), timeout)
    w.request_stop()
    await asyncio.wait_for(task, 5)


def go(coro):
    return asyncio.run(coro)


async def kill(w: RunWorker, main_task: asyncio.Task) -> None:
    """Simulate kill -9: run tasks die without finalizing or releasing their
    lease, heartbeats stop, the process loop stops."""
    tasks = [st.task for st in list(w._runs.values()) if st.task]
    for t in tasks:
        t.cancel()
    main_task.cancel()
    await asyncio.gather(*tasks, main_task, return_exceptions=True)


# ---------------------------------------------------------------------------

class TestParse:
    def test_messages_comments_and_garbage(self):
        chunk = sse({"type": "a"}) + ": ping\n\n" + "data: {not json}\n\n" + sse({"type": "b"})
        assert [e["type"] for e in parse_sse(chunk)] == ["a", "b"]


class TestEndToEnd:
    def test_run_completes_with_batched_tokens(self, store):
        tokens = [f"w{i} " for i in range(60)]
        r = queue(store)
        s = Script(answer_events(tokens), delay=0.002)
        go(run_until_finished(worker(store, s), store, [r.run_id]))

        run = store.get_run(r.run_id)
        assert run.status == "done" and run.turn_number == 1
        assert run.response_text == "".join(tokens)
        assert run.token_usage == {"total_tokens": 42}
        evs = store.read_events(r.run_id)
        token_rows = [e for e in evs if e.type == "token"]
        assert 1 <= len(token_rows) < len(tokens)          # batched
        assert "".join(e.payload["content"] for e in token_rows) == "".join(tokens)
        assert [e.seq for e in evs] == list(range(1, len(evs) + 1))
        assert evs[-1].type == "done" and evs[-1].payload["run_id"] == r.run_id
        assert [e.type for e in evs if e.type != "token"] == ["status", "response", "sources", "done"]
        ob = store.get_outbox(r.run_id)
        assert ob["status"] == "pending" and ob["payload"]["sources"] == [{"title": "Case A"}]
        assert ob["payload"]["agents_used"] == ["Judgment"]

    def test_thread_id_event_is_not_stored(self, store):
        r = queue(store)
        s = Script([{"type": "thread_id", "data": "t1"}] + answer_events(["a"]))
        go(run_until_finished(worker(store, s), store, [r.run_id]))
        assert "thread_id" not in [e.type for e in store.read_events(r.run_id)]

    def test_size_threshold_flushes_tokens(self, store):
        r = queue(store)
        big = ["x" * 600] * 10
        s = Script(answer_events(big))
        go(run_until_finished(worker(store, s, token_flush_ms=10_000, token_flush_bytes=1500),
                              store, [r.run_id]))
        rows = [e for e in store.read_events(r.run_id) if e.type == "token"]
        assert len(rows) >= 3 and all(len(e.payload["content"]) <= 1800 for e in rows)

    def test_time_threshold_flushes_while_pipeline_stalls(self, store):
        async def body():
            r = queue(store)
            gate = asyncio.Event()
            s = Script(answer_events(["Hel", "lo"]), gate=gate, gate_after=3)   # stall after tokens
            w = worker(store, s, token_flush_ms=50)
            task = asyncio.create_task(w.run_forever())
            t0 = time.monotonic()
            await until(lambda: any(e.type == "token" for e in store.read_events(r.run_id)), 2)
            flushed_after = time.monotonic() - t0
            gate.set()
            await until(lambda: store.get_run(r.run_id).status == "done")
            w.request_stop()
            await task
            return flushed_after
        assert go(body()) < 1.0


class TestFailures:
    def test_executor_crash_keeps_partial_text(self, store):
        r = queue(store)
        s = Script(answer_events(["Half ", "an ", "answer"]), crash_after=4)
        go(run_until_finished(worker(store, s), store, [r.run_id]))
        run = store.get_run(r.run_id)
        assert run.status == "failed" and run.response_text == "Half an answer"
        assert [e.type for e in store.read_events(r.run_id)][-2:] == ["error", "done"]
        assert store.get_outbox(r.run_id)["payload"]["status"] == "failed"

    def test_pipeline_error_event_fails_the_run(self, store):
        r = queue(store)
        evs = [{"type": "token", "content": "par"},
               {"type": "error", "data": "Request timed out."},
               {"type": "done", "agents_used": [], "conversation_turn": 0}]
        go(run_until_finished(worker(store, Script(evs)), store, [r.run_id]))
        run = store.get_run(r.run_id)
        assert run.status == "failed" and run.error == "Request timed out."
        assert run.response_text == "par" and run.turn_number is None
        types = [e.type for e in store.read_events(r.run_id)]
        assert types.count("error") == 1 and types[-1] == "done"

    def test_pipeline_ending_without_done_fails(self, store):
        r = queue(store)
        go(run_until_finished(worker(store, Script([{"type": "token", "content": "x"}])),
                              store, [r.run_id]))
        assert store.get_run(r.run_id).status == "failed"


class TestStopAndLimits:
    def test_cancel_stops_the_pipeline_within_a_heartbeat(self, store):
        async def body():
            r = queue(store)
            s = Script(answer_events(["a", "b"]), hang_after=2)
            w = worker(store, s, lease_s=0.4)
            task = asyncio.create_task(w.run_forever())
            await until(lambda: any(e.type == "token" for e in store.read_events(r.run_id)))
            t0 = time.monotonic()
            store.cancel(r.run_id)
            await until(lambda: r.run_id in s.closed, 2)
            stopped_in = time.monotonic() - t0
            w.request_stop()
            await asyncio.wait_for(task, 5)
            return r, stopped_in
        r, stopped_in = go(body())
        assert stopped_in < 0.5
        run = store.get_run(r.run_id)
        assert run.status == "cancelled"
        evs = store.read_events(r.run_id)
        assert evs[-1].type == "done" and evs[-1].payload.get("cancelled") is True
        assert sum(1 for e in evs if e.type == "done") == 1

    def test_concurrency_limit(self, store):
        runs = [queue(store, f"t{i}") for i in range(5)]
        s = Script(answer_events(["a", "b", "c"]), delay=0.03)
        go(run_until_finished(worker(store, s, concurrency=2), store, [r.run_id for r in runs]))
        assert s.max_active == 2
        assert all(store.get_run(r.run_id).status == "done" for r in runs)

    def test_liveness_row_while_running(self, store):
        async def body():
            gate = asyncio.Event()
            r = queue(store)
            w = worker(store, Script(answer_events(["a"]), gate=gate, gate_after=1))
            task = asyncio.create_task(w.run_forever())
            await until(lambda: store.get_run(r.run_id).status == "running")
            await until(lambda: any(x["running"] == 1 for x in store.live_workers()))
            gate.set()
            await until(lambda: store.get_run(r.run_id).status == "done")
            w.request_stop()
            await task
        go(body())
        assert store.live_workers() == []            # removed on exit


class TestShutdownAndRecovery:
    def test_graceful_stop_finishes_in_flight_and_claims_nothing_new(self, store):
        async def body():
            gate = asyncio.Event()
            first = queue(store, "t1")
            w = worker(store, Script(answer_events(["a"]), gate=gate, gate_after=1), concurrency=1)
            task = asyncio.create_task(w.run_forever())
            await until(lambda: store.get_run(first.run_id).status == "running")
            second = queue(store, "t2")
            w.request_stop()
            gate.set()
            await asyncio.wait_for(task, 5)
            return first, second
        first, second = go(body())
        assert store.get_run(first.run_id).status == "done"
        assert store.get_run(second.run_id).status == "queued"

    def test_forced_stop_releases_lease_and_run_is_retried(self, store):
        async def body():
            r = queue(store)
            hanging = Script([{"type": "status", "message": "working"}], hang_after=1)
            w = worker(store, hanging, drain_timeout_s=0.2)
            task = asyncio.create_task(w.run_forever())
            await until(lambda: store.get_run(r.run_id).status == "running")
            await until(lambda: len(store.read_events(r.run_id)) == 1)
            w.request_stop()
            await asyncio.wait_for(task, 5)        # drain times out, run abandoned
            # released lease -> recovered by the next sweep; the worker's own
            # sweeper may already have done it before it stopped
            store.sweep()
            assert store.get_run(r.run_id).status == "queued"
            w2 = worker(store, Script(answer_events(["ok"])))
            await run_until_finished(w2, store, [r.run_id])
            return r
        r = go(body())
        run = store.get_run(r.run_id)
        assert run.status == "done" and run.attempts == 2

    def test_crash_before_output_is_retried_by_another_runner(self, store):
        """Runner A dies (task killed, no cleanup, lease not released); runner
        B's sweeper requeues after the lease expires and B completes it."""
        async def body():
            r = queue(store)
            a = worker(store, Script([{"type": "status", "message": "planning"}], hang_after=1),
                       lease_s=0.5, sweep_every_s=60)
            ta = asyncio.create_task(a.run_forever())
            await until(lambda: len(store.read_events(r.run_id)) == 1)
            await kill(a, ta)
            b = worker(store, Script(answer_events(["recovered"])), lease_s=0.5, sweep_every_s=0.1)
            await run_until_finished(b, store, [r.run_id], timeout=8)
            return r
        r = go(body())
        run = store.get_run(r.run_id)
        assert run.status == "done" and run.attempts == 2 and run.response_text == "recovered"
        types = [e.type for e in store.read_events(r.run_id)]
        assert "status" in types and types[-1] == "done"

    def test_crash_after_output_fails_with_partial(self, store):
        async def body():
            r = queue(store)
            a = worker(store, Script(answer_events(["seen ", "text"]), hang_after=3),
                       lease_s=0.5, sweep_every_s=60)
            ta = asyncio.create_task(a.run_forever())
            await until(lambda: any(e.type == "token" for e in store.read_events(r.run_id)))
            await asyncio.sleep(0.15)               # let the token batch flush
            await kill(a, ta)
            b = worker(store, Script(answer_events(["never"])), lease_s=0.5, sweep_every_s=0.1)
            await run_until_finished(b, store, [r.run_id], timeout=8)
            return r
        r = go(body())
        run = store.get_run(r.run_id)
        assert run.status == "failed" and run.error == "interrupted"
        assert run.response_text == "seen text"

    def test_drain_mode_finishes_queue_then_exits(self, store):
        r = queue(store)
        w = worker(store, Script(answer_events(["a"])), exit_when_idle=True)
        go(asyncio.wait_for(w.run_forever(), 5))
        assert store.get_run(r.run_id).status == "done"


class TestLatency:
    @pytest.mark.parametrize("backend", ["sqlite", "postgres"])
    def test_persist_latency_of_a_typical_answer(self, backend, tmp_path):
        """400 tokens, as in recorded prod streams. Reports the cost of the
        event store per batch write; the bound is loose so slow disks do not
        flake. Postgres runs only when TEST_POSTGRES_URL is set."""
        import os
        if backend == "postgres":
            url = os.getenv("TEST_POSTGRES_URL", "")
            if not url:
                pytest.skip("TEST_POSTGRES_URL not set")
            import psycopg
            with psycopg.connect(url, autocommit=True) as c:
                for tb in ("run_outbox", "run_watchers", "run_events", "run_workers", "runs"):
                    c.execute(f"DROP TABLE IF EXISTS {tb} CASCADE")
            st = RunStore(postgres_url=url)
        else:
            st = RunStore(sqlite_path=str(tmp_path / "bench.db"))

        async def body():
            r = queue(st)
            claim = st.claim("bench")
            sink = EventSink(st, r.run_id, claim.lease_token, flush_ms=250,
                             flush_bytes=2048, on_lost=lambda: None)
            t0 = time.perf_counter()
            for i in range(400):
                await sink.add({"type": "token", "content": f"tok{i} "})
                if i % 40 == 0:
                    await sink.add({"type": "progress", "message": "x"})
                await asyncio.sleep(0.001)
            await sink.close()
            return sink, time.perf_counter() - t0
        try:
            sink, elapsed = go(body())
        finally:
            st.close()
        ms = sorted(sink.persist_ms)
        p50 = ms[len(ms) // 2]
        p95 = ms[min(len(ms) - 1, int(0.95 * len(ms)))]
        print(f"\n[{backend}] rows={sink.rows} token_rows={sink.token_rows} "
              f"tokens_in={sink.tokens_in} persist_p50_ms={p50:.1f} "
              f"persist_p95_ms={p95:.1f} elapsed_s={elapsed:.2f}")
        assert sink.tokens_in == 400 and sink.token_rows < 100
        assert p95 < 150


class TestStagingCleanup:
    def test_removes_only_old_folders_of_runs_that_will_not_read_them(self, store, tmp_path):
        import os
        root = tmp_path / "staging"
        root.mkdir()
        active = queue(store, "ta")                         # still queued: keep
        gone = queue(store, "tb")
        store.cancel(gone.run_id)                            # cancelled while queued: delete
        for name in (active.run_id, gone.run_id, "no-such-run", "fresh-folder"):
            (root / name).mkdir()
            (root / name / "00.pdf").write_bytes(b"%PDF")
        old = time.time() - 7200
        for name in (active.run_id, gone.run_id, "no-such-run"):
            os.utime(root / name, (old, old))
        w = worker(store, Script([]), staging_root=str(root))
        assert w._clean_staging() == 2
        assert sorted(os.listdir(root)) == sorted([active.run_id, "fresh-folder"])

    def test_missing_root_is_fine(self, store, tmp_path):
        w = worker(store, Script([]), staging_root=str(tmp_path / "nope"))
        assert w._clean_staging() == 0
