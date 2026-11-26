"""Run-complete notification to Node via the transactional outbox (phase 5).

A stub Node server (a real HTTP server on a thread) answers with scripted
statuses and, like the real Node endpoint must, dedupes on Idempotency-Key:
a second delivery of the same run stores nothing and pushes nothing.

Acceptance tests (plan section 14):
  * Node unavailable when the run finishes: the message is retried later
  * the same callback arrives twice: one stored turn, one notification
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.run_outbox import OutboxDispatcher, backoff_s
from core.run_store import RunStore

SECRET = "node-callback-secret"


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += timedelta(seconds=s)


class StubNode:
    """Stores a turn and sends a push once per run_id, as Node must."""

    def __init__(self):
        self.script: list[int] = []        # statuses to answer, then 200
        self.requests: list[dict] = []
        self.stored: dict[str, dict] = {}
        self.pushes: list[str] = []
        self.delay_s = 0.0
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append({"headers": dict(self.headers), "body": body})
                if stub.delay_s:
                    time.sleep(stub.delay_s)
                status = stub.script.pop(0) if stub.script else 200
                if status == 200 and self.headers.get("X-Service-Secret") != SECRET:
                    status = 401
                if status == 200:
                    key = self.headers["Idempotency-Key"]
                    if key not in stub.stored:              # dedupe: store and push once
                        stub.stored[key] = body
                        if body["status"] == "done" and not body["connected"]:
                            stub.pushes.append(body["user_id"])
                self.send_response(status)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v2/api/runs/complete"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    s = RunStore(sqlite_path=str(tmp_path / "runs.db"), now_fn=clock,
                 callback_enabled=lambda status: status != "cancelled")
    s.ensure_schema()
    return s


@pytest.fixture
def node():
    n = StubNode()
    yield n
    n.close()


def finished_run(store, thread="t1", user="u-1", watched=False):
    r = store.create_run(thread_id=thread, endpoint="/pyapi/search/stream",
                         request={"query": "bail?"}, user_id=user, identity_source="jwt")
    c = store.claim("w")
    if watched:
        store.touch_watcher(r.run_id, "tail")
    store.finalize(r.run_id, c.lease_token, status="done", response_text="Answer.", turn_number=2,
                   final_events=[{"type": "done", "conversation_turn": 2, "agents_used": ["A"]}])
    return r.run_id


def dispatch(d):
    return asyncio.run(d.dispatch_once())


def dispatcher(store, url, **kw):
    return OutboxDispatcher(store, url=url, secret=SECRET, timeout_s=2, **kw)


class TestDelivery:
    def test_payload_and_headers(self, store, node):
        run_id = finished_run(store)
        assert dispatch(dispatcher(store, node.url)) == 1
        req = node.requests[0]
        assert req["headers"]["X-Service-Secret"] == SECRET
        assert req["headers"]["Idempotency-Key"] == run_id
        b = req["body"]
        assert (b["run_id"], b["user_id"], b["identity_source"], b["turn"], b["status"]) == \
               (run_id, "u-1", "jwt", 2, "done")
        assert b["response"] == "Answer." and b["query"] == "bail?" and b["connected"] is False
        assert store.get_outbox(run_id)["status"] == "sent"
        assert node.pushes == ["u-1"]                      # nobody watching -> push

    def test_connected_user_gets_no_push(self, store, node):
        finished_run(store, watched=True)
        dispatch(dispatcher(store, node.url))
        assert node.requests[0]["body"]["connected"] is True and node.pushes == []

    def test_no_callback_url_sends_nothing(self, store, node):
        run_id = finished_run(store)
        assert dispatch(dispatcher(store, "")) == 0
        assert node.requests == [] and store.get_outbox(run_id)["status"] == "pending"


class TestRetries:
    def test_node_unavailable_then_back(self, store, node, clock):
        """Acceptance: Node down when the run finishes; delivered later."""
        node.script = [503, 503]
        run_id = finished_run(store)
        d = dispatcher(store, node.url)
        dispatch(d)
        ob = store.get_outbox(run_id)
        assert ob["status"] == "pending" and ob["attempts"] == 1 and ob["last_error"] == "HTTP 503"
        assert dispatch(d) == 0                            # not due yet
        clock.advance(5)
        dispatch(d)
        assert store.get_outbox(run_id)["attempts"] == 2
        clock.advance(29)
        assert dispatch(d) == 0                            # second backoff is 30 s
        clock.advance(1)
        dispatch(d)
        ob = store.get_outbox(run_id)
        assert ob["status"] == "sent" and ob["attempts"] == 3
        assert len(node.stored) == 1 and node.pushes == ["u-1"]

    def test_connection_refused_is_retried(self, store, clock):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            dead_port = s.getsockname()[1]
        run_id = finished_run(store)
        dispatch(dispatcher(store, f"http://127.0.0.1:{dead_port}/x"))
        ob = store.get_outbox(run_id)
        assert ob["status"] == "pending" and ob["attempts"] == 1
        assert ob["last_error"].startswith("Connect")      # refused (Linux) or timed out (Windows)

    def test_slow_node_times_out_and_is_retried(self, store, node):
        node.delay_s = 3
        run_id = finished_run(store)
        dispatch(dispatcher(store, node.url))
        ob = store.get_outbox(run_id)
        assert ob["status"] == "pending" and "Timeout" in ob["last_error"]

    def test_backoff_schedule(self):
        assert [backoff_s(n) for n in range(1, 9)] == [5, 30, 120, 600, 1800, 3600, 3600, 3600]

    def test_gives_up_after_24_hours(self, store, node, clock):
        node.script = [503] * 100
        run_id = finished_run(store)
        d = dispatcher(store, node.url)
        dispatch(d)
        clock.advance(25 * 3600)
        dispatch(d)
        ob = store.get_outbox(run_id)
        assert ob["status"] == "dead" and "24 h" in ob["last_error"]

    def test_contract_error_is_dead_and_admin_retry_redelivers(self, store, node):
        node.script = [400]
        run_id = finished_run(store)
        d = dispatcher(store, node.url)
        dispatch(d)
        ob = store.get_outbox(run_id)
        assert ob["status"] == "dead" and store.counts()["outbox_dead"] == 1
        assert store.retry_outbox(ob["outbox_id"])
        dispatch(d)
        assert store.get_outbox(run_id)["status"] == "sent"

    def test_wrong_secret_is_rejected_as_contract_error(self, store, node):
        run_id = finished_run(store)
        dispatch(OutboxDispatcher(store, url=node.url, secret="wrong", timeout_s=2))
        assert store.get_outbox(run_id)["status"] == "dead" and node.stored == {}


class TestDuplicates:
    def test_same_callback_twice_is_one_turn_and_one_push(self, store, node, clock):
        """Acceptance: Node accepted the first delivery but we never learned it
        (the response was lost). The retry carries the same Idempotency-Key;
        Node stores and pushes once."""
        run_id = finished_run(store)
        from core.run_outbox import _httpx_post
        calls = {"n": 0}

        async def lossy_post(url, payload, headers, timeout_s):
            status = await _httpx_post(url, payload, headers, timeout_s)
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionResetError("response lost after Node accepted it")
            return status

        d = dispatcher(store, node.url, post=lossy_post)
        dispatch(d)
        assert store.get_outbox(run_id)["status"] == "pending"
        clock.advance(5)
        dispatch(d)
        assert len(node.requests) == 2
        assert {r["headers"]["Idempotency-Key"] for r in node.requests} == {run_id}
        assert len(node.stored) == 1 and node.pushes == ["u-1"]
        assert store.get_outbox(run_id)["status"] == "sent"

    def test_dispatcher_dying_mid_send_resends_after_the_hold(self, store, node, clock):
        run_id = finished_run(store)
        assert len(store.claim_outbox(hold_s=11)) == 1     # claimed, then the process died
        d = dispatcher(store, node.url)
        assert dispatch(d) == 0                            # still held
        clock.advance(11)
        assert dispatch(d) == 1
        assert store.get_outbox(run_id)["status"] == "sent" and len(node.stored) == 1

    def test_one_outbox_row_per_run_even_if_recovery_races(self, store, clock):
        """Finalize and the sweeper's repair cannot both create a message."""
        r = store.create_run(thread_id="t", endpoint="/pyapi/search/stream", request={"query": "q"})
        c = store.claim("w")
        store.finalize(r.run_id, c.lease_token, status="done", response_text="a")
        clock.advance(120)
        store.sweep(turn_lookup=lambda _: {"turn_number": 1, "ai_response": "a"})
        with store._read() as cx:
            n = cx.one("SELECT COUNT(*) AS n FROM run_outbox WHERE run_id = ?", (r.run_id,))["n"]
        assert n == 1


class TestRunnerSendsIt:
    def test_runner_delivers_after_a_run(self, tmp_path, node):
        from workers.run_worker import RunWorker
        st = RunStore(sqlite_path=str(tmp_path / "live.db"), callback_enabled=lambda s: True)
        r = st.create_run(thread_id="t", endpoint="/pyapi/search/stream", request={"query": "q"},
                          user_id="u-7")

        async def pipeline(claim):
            for ev in ({"type": "token", "content": "hi"}, {"type": "response", "content": "hi"},
                       {"type": "done", "conversation_turn": 1, "agents_used": []}):
                yield f"data: {json.dumps(ev)}\n\n"

        async def body():
            w = RunWorker(st, pipeline, concurrency=1, lease_s=2, poll_s=0.05,
                          sweep_every_s=5, liveness_every_s=5, outbox_every_s=0.05,
                          outbox_dispatcher=OutboxDispatcher(st, url=node.url, secret=SECRET,
                                                             timeout_s=2).dispatch_once)
            task = asyncio.create_task(w.run_forever())
            t0 = time.monotonic()
            while not node.stored:
                assert time.monotonic() - t0 < 5
                await asyncio.sleep(0.05)
            w.request_stop()
            await task
        asyncio.run(body())
        assert node.stored[r.run_id]["user_id"] == "u-7" and node.pushes == ["u-7"]
        assert st.get_outbox(r.run_id)["status"] == "sent"
