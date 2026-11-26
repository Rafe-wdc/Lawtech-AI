"""Run store (background runs, 2026-11-26): queue, leases and fencing,
one active run per thread, immediate cancel, outbox, recovery, retention.

Runs on SQLite always, and on Postgres too when TEST_POSTGRES_URL points at a
scratch database (its run_* tables are dropped and recreated per test).
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone

import pytest

from core.run_store import (
    ActiveRunExists, LeaseLost, RunStore, SAFE_PRE_OUTPUT_TYPES, partial_text,
)

PG_URL = os.getenv("TEST_POSTGRES_URL", "")
_RUN_TABLES = ("run_outbox", "run_watchers", "run_events", "run_workers", "runs")


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 26, 10, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


def _reset_pg(url: str) -> None:
    import psycopg
    with psycopg.connect(url, autocommit=True) as c:
        for t in _RUN_TABLES:
            c.execute(f"DROP TABLE IF EXISTS {t} CASCADE")


@pytest.fixture(params=["sqlite", "postgres"])
def backend(request, tmp_path):
    if request.param == "postgres":
        if not PG_URL:
            pytest.skip("TEST_POSTGRES_URL not set")
        _reset_pg(PG_URL)
        return {"postgres_url": PG_URL}
    return {"sqlite_path": str(tmp_path / "runs.db")}


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def callbacks():
    """Which statuses get a 'pending' (sendable) outbox row."""
    return {"enabled": True}


@pytest.fixture
def store(backend, clock, callbacks):
    s = RunStore(now_fn=clock, callback_enabled=lambda status: callbacks["enabled"], **backend)
    yield s
    s.close()


def _new(store, thread="t1", **kw):
    return store.create_run(thread_id=thread, endpoint="/pyapi/search/stream",
                            request={"query": "what is bail?"}, **kw)


def _claimed(store, thread="t1", worker="w1"):
    _new(store, thread)
    c = store.claim(worker)
    assert c is not None
    return c


# ---------------------------------------------------------------------------

class TestCreateAndThreadLock:
    def test_create_and_read_back(self, store):
        r = _new(store, user_id="u-1", identity_source="jwt", secrets={"integration_token": "x"})
        got = store.get_run(r.run_id)
        assert got.status == "queued" and got.user_id == "u-1" and got.identity_source == "jwt"
        assert got.request == {"query": "what is bail?"}
        assert "secrets" not in got.public() and "request" not in got.public()

    def test_second_active_run_on_same_thread_rejected(self, store):
        first = _new(store)
        with pytest.raises(ActiveRunExists) as ei:
            _new(store)
        assert ei.value.run.run_id == first.run_id

    def test_other_threads_are_independent(self, store):
        _new(store, "t1")
        _new(store, "t2")
        assert store.counts()["queued"] == 2

    def test_new_run_allowed_once_previous_finished(self, store):
        c = _claimed(store)
        store.finalize(c.run.run_id, c.lease_token, status="done", response_text="a")
        assert _new(store).status == "queued"

    def test_running_run_also_holds_the_lock(self, store):
        _claimed(store)
        with pytest.raises(ActiveRunExists):
            _new(store)


class TestClaimAndLease:
    def test_claim_takes_oldest_and_counts_attempt(self, store, clock):
        a = _new(store, "t1")
        clock.advance(1)
        _new(store, "t2")
        c = store.claim("w1")
        assert c.run.run_id == a.run_id and c.run.status == "running"
        assert c.run.attempts == 1 and c.lease_token and c.run.worker_id == "w1"

    def test_claim_returns_secrets_to_the_holder_only(self, store):
        r = _new(store, secrets={"integration_token": "jwt-x"})
        c = store.claim("w1")
        assert c.secrets == {"integration_token": "jwt-x"}
        assert store.get_run(r.run_id).public().get("secrets") is None

    def test_nothing_to_claim(self, store):
        assert store.claim("w1") is None

    def test_concurrent_claims_never_share_a_run(self, store):
        for i in range(6):
            _new(store, f"t{i}")
        got, lock = [], threading.Lock()

        def take():
            while True:
                c = store.claim(threading.current_thread().name)
                if c is None:
                    return
                with lock:
                    got.append(c.run.run_id)

        threads = [threading.Thread(target=take, name=f"w{i}") for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(got) == 6 and len(set(got)) == 6

    def test_heartbeat_extends_lease(self, store, clock):
        c = _claimed(store)
        before = store.get_run(c.run.run_id).lease_until
        clock.advance(20)
        assert store.heartbeat(c.run.run_id, c.lease_token)
        assert store.get_run(c.run.run_id).lease_until > before

    def test_heartbeat_with_wrong_token_fails(self, store):
        c = _claimed(store)
        assert store.heartbeat(c.run.run_id, "not-the-token") is False


class TestEvents:
    def test_seq_is_contiguous_and_read_after_is_exact(self, store):
        c = _claimed(store)
        rid, tok = c.run.run_id, c.lease_token
        assert store.append_events(rid, tok, [{"type": "status", "message": "a"}]) == 1
        assert store.append_events(rid, tok, [{"type": "token", "content": "Hel"},
                                              {"type": "token", "content": "lo"}]) == 3
        evs = store.read_events(rid)
        assert [e.seq for e in evs] == [1, 2, 3]
        assert [e.seq for e in store.read_events(rid, after_seq=1)] == [2, 3]
        assert store.read_events(rid, after_seq=3) == []
        assert evs[1].sse().startswith("data: ") and '"seq": 2' in evs[1].sse()

    def test_only_output_events_mark_has_output(self, store):
        c = _claimed(store)
        rid, tok = c.run.run_id, c.lease_token
        for t in SAFE_PRE_OUTPUT_TYPES:
            store.append_events(rid, tok, [{"type": t}])
        assert store.get_run(rid).has_output is False
        store.append_events(rid, tok, [{"type": "file_processing", "message": "x"}])
        assert store.get_run(rid).has_output is True

    def test_append_by_non_holder_raises(self, store):
        c = _claimed(store)
        with pytest.raises(LeaseLost):
            store.append_events(c.run.run_id, "other", [{"type": "token", "content": "x"}])

    def test_partial_text_follows_reset_and_response(self, store):
        c = _claimed(store)
        rid, tok = c.run.run_id, c.lease_token
        store.append_events(rid, tok, [{"type": "token", "content": "draft "},
                                       {"type": "token_reset"},
                                       {"type": "token", "content": "final"}])
        assert partial_text(store.read_events(rid)) == "final"
        store.append_events(rid, tok, [{"type": "response", "content": "Final."}])
        assert partial_text(store.read_events(rid)) == "Final."


class TestFencing:
    def test_expired_holder_cannot_write_after_reclaim(self, store, clock):
        """The core fencing guarantee: runner A freezes, its lease expires, the
        run is requeued and claimed by B; A's late writes all bounce."""
        a = _claimed(store, worker="A")
        rid = a.run.run_id
        clock.advance(61)
        rep = store.sweep()
        assert rep.requeued == [rid]
        b = store.claim("B")
        assert b.run.run_id == rid and b.lease_token != a.lease_token and b.run.attempts == 2
        with pytest.raises(LeaseLost):
            store.append_events(rid, a.lease_token, [{"type": "token", "content": "late"}])
        assert store.heartbeat(rid, a.lease_token) is False
        with pytest.raises(LeaseLost):
            store.finalize(rid, a.lease_token, status="done", response_text="stale")
        store.append_events(rid, b.lease_token, [{"type": "token", "content": "ok"}])
        assert store.get_run(rid).worker_id == "B"

    def test_finalize_twice_second_is_fenced(self, store):
        c = _claimed(store)
        store.finalize(c.run.run_id, c.lease_token, status="done", response_text="a")
        with pytest.raises(LeaseLost):
            store.finalize(c.run.run_id, c.lease_token, status="done", response_text="b")


class TestFinalizeAndOutbox:
    def test_done_writes_final_events_status_and_one_outbox_row(self, store):
        c = _claimed(store, )
        rid, tok = c.run.run_id, c.lease_token
        store.append_events(rid, tok, [{"type": "sources", "data": [{"title": "S1"}]}])
        run = store.finalize(rid, tok, status="done", response_text="Answer.",
                             token_usage={"total_tokens": 9}, turn_number=3,
                             final_events=[{"type": "done", "conversation_turn": 3,
                                            "agents_used": ["Judgment"]}],
                             agents_used=["Judgment"])
        assert run.status == "done" and run.turn_number == 3 and run.last_seq == 2
        assert store.read_events(rid)[-1].type == "done"
        ob = store.get_outbox(rid)
        assert ob["status"] == "pending"
        p = ob["payload"]
        assert p["status"] == "done" and p["response"] == "Answer." and p["turn"] == 3
        assert p["sources"] == [{"title": "S1"}] and p["agents_used"] == ["Judgment"]
        assert p["query"] == "what is bail?" and p["token_usage"] == {"total_tokens": 9}

    def test_secrets_are_blanked_at_finish(self, store, backend):
        r = _new(store, secrets={"integration_token": "jwt"})
        c = store.claim("w")
        store.finalize(r.run_id, c.lease_token, status="done", response_text="a")
        with store._read() as cx:
            row = cx.one("SELECT secrets_json FROM runs WHERE run_id = ?", (r.run_id,))
        assert row["secrets_json"] == ""

    def test_connected_reflects_a_fresh_watcher(self, store, clock):
        c = _claimed(store)
        store.touch_watcher(c.run.run_id, "tail-1")
        clock.advance(5)
        store.finalize(c.run.run_id, c.lease_token, status="done", response_text="a")
        assert store.get_outbox(c.run.run_id)["payload"]["connected"] is True

    def test_connected_false_once_watcher_is_stale(self, store, clock):
        c = _claimed(store)
        store.touch_watcher(c.run.run_id, "tail-1")
        clock.advance(16)
        store.finalize(c.run.run_id, c.lease_token, status="done", response_text="a")
        assert store.get_outbox(c.run.run_id)["payload"]["connected"] is False

    def test_callback_not_configured_writes_skipped(self, store, callbacks):
        callbacks["enabled"] = False
        c = _claimed(store)
        store.finalize(c.run.run_id, c.lease_token, status="done", response_text="a")
        assert store.get_outbox(c.run.run_id)["status"] == "skipped"
        assert store.claim_outbox() == []

    def test_outbox_claim_holds_then_retry_and_sent(self, store, clock):
        c = _claimed(store)
        store.finalize(c.run.run_id, c.lease_token, status="done", response_text="a")
        items = store.claim_outbox(hold_s=60)
        assert len(items) == 1 and items[0].payload["run_id"] == c.run.run_id
        assert store.claim_outbox() == []                     # held while in flight
        store.mark_outbox_retry(items[0].outbox_id, "503", clock() + timedelta(seconds=5))
        assert store.claim_outbox() == []
        clock.advance(6)
        again = store.claim_outbox()
        assert len(again) == 1 and again[0].attempts == 1
        store.mark_outbox_sent(again[0].outbox_id)
        ob = store.get_outbox(c.run.run_id)
        assert ob["status"] == "sent" and ob["attempts"] == 2
        assert store.counts()["outbox_pending"] == 0

    def test_dead_outbox_can_be_retried_by_admin(self, store):
        c = _claimed(store)
        store.finalize(c.run.run_id, c.lease_token, status="done", response_text="a")
        item = store.claim_outbox()[0]
        store.mark_outbox_dead(item.outbox_id, "400 bad payload")
        assert store.counts()["outbox_dead"] == 1
        assert store.retry_outbox(item.outbox_id)
        assert store.get_outbox(c.run.run_id)["status"] == "pending"


class TestCancel:
    def test_cancel_running_is_immediate_and_frees_the_thread(self, store):
        c = _claimed(store)
        rid = c.run.run_id
        store.append_events(rid, c.lease_token, [{"type": "token", "content": "part"}])
        run = store.cancel(rid)
        assert run.status == "cancelled" and run.response_text == "part"
        last = store.read_events(rid)[-1]
        assert last.type == "done" and last.payload["cancelled"] is True
        with pytest.raises(LeaseLost):                       # the runner stops on its next write
            store.append_events(rid, c.lease_token, [{"type": "token", "content": "x"}])
        assert store.heartbeat(rid, c.lease_token) is False
        assert _new(store).status == "queued"                # thread free at once

    def test_cancel_queued(self, store):
        r = _new(store)
        assert store.cancel(r.run_id).status == "cancelled"
        assert store.claim("w") is None

    def test_cancel_finished_returns_none(self, store):
        c = _claimed(store)
        store.finalize(c.run.run_id, c.lease_token, status="done", response_text="a")
        assert store.cancel(c.run.run_id) is None

    def test_cancel_outbox_is_skipped_by_default(self, store, callbacks):
        callbacks["enabled"] = False
        r = _new(store)
        store.cancel(r.run_id)
        assert store.get_outbox(r.run_id)["status"] == "skipped"


class TestSweepRecovery:
    def test_requeue_when_no_output(self, store, clock):
        c = _claimed(store)
        store.append_events(c.run.run_id, c.lease_token, [{"type": "status", "message": "x"}])
        clock.advance(61)
        assert store.sweep().requeued == [c.run.run_id]
        run = store.get_run(c.run.run_id)
        assert run.status == "queued" and run.lease_token == ""
        assert store.read_events(c.run.run_id)[-1].payload["message"].startswith("Resuming")

    def test_fail_when_output_was_shown(self, store, clock):
        c = _claimed(store)
        store.append_events(c.run.run_id, c.lease_token, [{"type": "token", "content": "Half "},
                                                         {"type": "token", "content": "answer"}])
        clock.advance(61)
        assert store.sweep().failed == [c.run.run_id]
        run = store.get_run(c.run.run_id)
        assert run.status == "failed" and run.error == "interrupted"
        assert run.response_text == "Half answer"
        types = [e.type for e in store.read_events(c.run.run_id)]
        assert types[-2:] == ["error", "done"]
        assert store.get_outbox(c.run.run_id)["payload"]["status"] == "failed"

    def test_repair_to_done_when_turn_already_saved(self, store, clock):
        c = _claimed(store)
        rid = c.run.run_id
        store.append_events(rid, c.lease_token, [{"type": "token", "content": "x"}])
        clock.advance(61)
        rep = store.sweep(turn_lookup=lambda r: {"turn_number": 4, "ai_response": "Saved."}
                          if r == rid else None)
        assert rep.repaired == [rid]
        run = store.get_run(rid)
        assert run.status == "done" and run.turn_number == 4 and run.response_text == "Saved."
        last = store.read_events(rid)[-1]
        assert last.type == "done" and last.payload["recovered"] is True
        assert store.get_outbox(rid)["payload"]["turn"] == 4

    def test_no_retry_after_max_attempts(self, store, clock):
        c = _claimed(store)
        clock.advance(61)
        store.sweep()
        c2 = store.claim("w2")
        assert c2.run.attempts == 2
        clock.advance(61)
        assert store.sweep().failed == [c.run.run_id]

    def test_live_lease_is_left_alone(self, store, clock):
        c = _claimed(store)
        clock.advance(30)
        assert not store.sweep().changed
        assert store.get_run(c.run.run_id).status == "running"

    def test_turn_lookup_error_leaves_run_for_next_sweep(self, store, clock):
        c = _claimed(store)
        clock.advance(61)

        def boom(_):
            raise RuntimeError("db down")
        rep = store.sweep(turn_lookup=boom)
        assert not rep.failed and not rep.requeued
        assert store.get_run(c.run.run_id).status == "running"

    def test_queued_too_long_fails_with_no_worker(self, store, clock):
        r = _new(store)
        clock.advance(901)
        assert store.sweep().expired_queued == [r.run_id]
        run = store.get_run(r.run_id)
        assert run.status == "failed" and run.error == "no worker"

    def test_retention_deletes_old_events_and_rows(self, store, clock):
        c = _claimed(store)
        store.append_events(c.run.run_id, c.lease_token, [{"type": "token", "content": "x"}])
        store.finalize(c.run.run_id, c.lease_token, status="done", response_text="a")
        clock.advance(8 * 86400)
        rep = store.sweep()
        assert rep.events_deleted == 1 and store.read_events(c.run.run_id) == []
        assert store.get_run(c.run.run_id) is not None
        clock.advance(90 * 86400)
        assert store.sweep().runs_deleted == 1
        assert store.get_run(c.run.run_id) is None


class TestWorkersAndWatchers:
    def test_worker_liveness(self, store, clock):
        store.worker_heartbeat("w1", concurrency=2, running=1)
        assert [w["worker_id"] for w in store.live_workers()] == ["w1"]
        clock.advance(46)
        assert store.live_workers() == []
        store.worker_heartbeat("w1", concurrency=2, running=0)
        store.remove_worker("w1")
        assert store.live_workers() == []

    def test_watchers(self, store, clock):
        c = _claimed(store)
        rid = c.run.run_id
        assert store.is_watched(rid) is False
        store.touch_watcher(rid, "a")
        assert store.is_watched(rid) is True
        store.remove_watcher(rid, "a")
        assert store.is_watched(rid) is False

    def test_latest_and_active_run(self, store):
        c = _claimed(store)
        assert store.active_run("t1").run_id == c.run.run_id
        store.finalize(c.run.run_id, c.lease_token, status="done", response_text="a")
        assert store.active_run("t1") is None
        assert store.latest_run("t1").run_id == c.run.run_id
