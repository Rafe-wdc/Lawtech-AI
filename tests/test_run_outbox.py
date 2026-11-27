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


@pytest.fixture(autouse=True)
def _generic_callback_format(monkeypatch):
    """These tests check delivery mechanics on the generic run-complete
    payload. Node's store-history format has its own tests in
    test_run_outbox.py (TestStoreHistoryFormat)."""
    from core import settings as S
    monkeypatch.setattr(S, "RUN_CALLBACK_FORMAT", "run_complete")

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


# ---------------------------------------------------------------------------
# Node's store-history format (RUN_CALLBACK_FORMAT=store_history, the default)
# ---------------------------------------------------------------------------

class StoreHistoryNode:
    """Node's POST /v2/api/chats/cases/ai/store-history as built: no secret,
    every call a new record with its tokens deducted, push when notify."""

    def __init__(self):
        self.script: list[int] = []
        self.requests: list[dict] = []
        self.records: list[dict] = []
        self.tokens_charged = 0
        self.pushes: list[int] = []
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append({"headers": dict(self.headers), "body": body})
                status = stub.script.pop(0) if stub.script else 200
                out = {}
                if status == 200:
                    stub.records.append({**body, "id": 5000 + len(stub.records)})
                    stub.tokens_charged += int(body.get("Token") or 0)
                    if body.get("notify"):
                        stub.pushes.append(body["UserId"])
                    out = {"status": True, "data": stub.records[-1]}
                data = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = (f"http://127.0.0.1:{self.httpd.server_address[1]}"
                    "/v2/api/chats/cases/ai/store-history")
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


SOURCES = [
    {"title": "Act", "doc_link": "https://s3/act.pdf"},
    {"title": "Web", "web_url": "https://example.org/a"},
    {"title": "Judgment", "pdf_links": [{"name": "x"}, {"url": "https://s3/j.pdf"}]},
    {"title": "No link"},
]


class TestStoreHistoryFormat:
    @pytest.fixture(autouse=True)
    def _store_history(self, monkeypatch):
        from core import settings as S
        monkeypatch.setattr(S, "RUN_CALLBACK_FORMAT", "store_history")

    @pytest.fixture
    def hist(self):
        n = StoreHistoryNode()
        yield n
        n.close()

    def run(self, store, *, user="101", watched=False, status="done", request=None,
            events=None, response="Answer.", tokens=350):
        r = store.create_run(thread_id="t-9", endpoint="/pyapi/search/stream",
                             request=request or {"query": "What is Section 303 BNS?"},
                             user_id=user, identity_source="payload")
        c = store.claim("w")
        if watched:
            store.touch_watcher(r.run_id, "tail")
        store.finalize(r.run_id, c.lease_token, status=status, response_text=response,
                       turn_number=1, token_usage={"total_tokens": tokens} if tokens else None,
                       sources=SOURCES,
                       final_events=(events or []) + [{"type": "done", "agents_used": ["Newacts"]}])
        return r.run_id

    def done_event(self, store, run_id):
        return [e for e in store.read_events(run_id, 0, 100) if e.type == "done"][-1].payload

    def test_body_is_what_the_app_sends_plus_notify(self, store, hist):
        run_id = self.run(store)
        assert dispatch(dispatcher(store, hist.url)) == 1
        req = hist.requests[0]
        assert req["headers"]["Idempotency-Key"] == run_id
        assert req["body"] == {
            "UserId": 101,
            "SearchTitle": "What is Section 303 BNS?",
            "SearchResult": "Answer.",
            "ThreadId": "t-9",
            "ParentId": None,
            "Token": 350,
            "source": {"type": "sources",
                       "urls": ["https://s3/act.pdf", "https://example.org/a", "https://s3/j.pdf"],
                       "sources": SOURCES},
            "summary": "What is Section 303 BNS?",
            "notify": True,
        }
        assert store.get_outbox(run_id)["status"] == "sent"
        assert hist.pushes == [101] and hist.tokens_charged == 350
        assert self.done_event(store, run_id)["history_saved_by_server"] is True

    def test_no_secret_configured_sends_no_secret_header(self, store, hist):
        self.run(store)
        dispatch(OutboxDispatcher(store, url=hist.url, secret="", timeout_s=2))
        assert "X-Service-Secret" not in hist.requests[0]["headers"]
        assert len(hist.records) == 1

    def test_missing_token_count_is_sent_as_1_like_the_app(self, store, hist):
        self.run(store, tokens=0)
        dispatch(dispatcher(store, hist.url))
        assert hist.requests[0]["body"]["Token"] == 1

    def test_answer_an_app_showed_is_left_to_the_app(self, store, hist):
        run_id = self.run(store, watched=True)
        assert store.get_outbox(run_id)["status"] == "skipped"
        assert dispatch(dispatcher(store, hist.url)) == 0 and hist.requests == []
        assert self.done_event(store, run_id)["history_saved_by_server"] is False

    def test_without_a_callback_url_the_app_saves_everything(self, tmp_path, clock, hist):
        st = RunStore(sqlite_path=str(tmp_path / "off.db"), now_fn=clock,
                      callback_enabled=lambda status: False)
        st.ensure_schema()
        run_id = self.run(st)                                       # nobody watching
        assert st.get_outbox(run_id)["status"] == "skipped"
        assert self.done_event(st, run_id)["history_saved_by_server"] is False

    def test_node_down_then_back_stores_it_once(self, store, hist, clock):
        self.run(store)
        hist.script = [503]
        dispatch(dispatcher(store, hist.url))
        clock.advance(6)
        dispatch(dispatcher(store, hist.url))
        assert len(hist.requests) == 2
        assert len(hist.records) == 1 and hist.tokens_charged == 350 and hist.pushes == [101]

    def test_stop_and_failure_are_left_to_the_app(self, store, hist):
        r = store.create_run(thread_id="t-c", endpoint="/pyapi/search/stream",
                             request={"query": "q"}, user_id="101")
        store.claim("w")
        store.cancel(r.run_id)
        assert self.done_event(store, r.run_id)["history_saved_by_server"] is False
        run_id = self.run(store, status="failed")
        assert self.done_event(store, run_id)["history_saved_by_server"] is False
        assert dispatch(dispatcher(store, hist.url)) == 0

    @pytest.mark.parametrize("kw", [{"user": "u-abc"}, {"user": ""}, {"status": "failed"},
                                    {"response": "  "}],
                             ids=["non-numeric user", "no user", "failed", "empty answer"])
    def test_what_node_cannot_store_is_not_sent(self, store, hist, kw):
        run_id = self.run(store, **kw)
        assert store.get_outbox(run_id)["status"] == "skipped"
        assert dispatch(dispatcher(store, hist.url)) == 0

    def test_out_of_prompts_is_not_retried(self, store, hist, clock):
        run_id = self.run(store)
        hist.script = [403]
        dispatch(dispatcher(store, hist.url))
        clock.advance(3600)
        dispatch(dispatcher(store, hist.url))
        assert len(hist.requests) == 1 and store.get_outbox(run_id)["status"] == "dead"

    def test_draft_reference_and_parent_id_as_the_app_saves_them(self, store, hist):
        art = {"artifact_id": "art-t9", "version": 2, "title": "Legal Notice",
               "sections": [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]}
        self.run(store, request={"query": "Draft a legal notice", "parent_id": "5521"},
                 events=[{"type": "artifact_start", "artifact_id": "art-t9", "version": 2,
                          "sections": [{"id": "p1"}, {"id": "p3"}]},
                         {"type": "artifact_done", "artifact_id": "art-t9", "version": 2,
                          "artifact": art}])
        dispatch(dispatcher(store, hist.url))
        body = hist.requests[0]["body"]
        assert body["ParentId"] == 5521
        assert body["source"]["artifact"] == {"id": "art-t9", "version": 2,
                                              "title": "Legal Notice", "sectionCount": 3}

    def test_draft_that_never_finished_keeps_the_start_reference(self, store, hist):
        self.run(store, events=[{"type": "artifact_start", "artifact_id": "art-t9", "version": 1,
                                 "sections": [{"id": "p1"}, {"id": "p3"}]}])
        dispatch(dispatcher(store, hist.url))
        assert hist.requests[0]["body"]["source"]["artifact"] == {
            "id": "art-t9", "version": 1, "sectionCount": 2}

    def test_row_queued_in_the_generic_format_is_not_sent_to_store_history(self, store, hist,
                                                                           monkeypatch):
        from core import settings as S
        monkeypatch.setattr(S, "RUN_CALLBACK_FORMAT", "run_complete")
        run_id = self.run(store, user="u-abc")                    # queued: generic sends all
        monkeypatch.setattr(S, "RUN_CALLBACK_FORMAT", "store_history")
        dispatch(dispatcher(store, hist.url))                     # Node could not store it
        assert hist.requests == [] and store.get_outbox(run_id)["status"] == "dead"
