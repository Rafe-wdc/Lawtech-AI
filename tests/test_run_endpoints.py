"""Background runs through the real front door (phase 4, 2026-11-26).

A real uvicorn server (core.gateway.app) and a real runner (RunWorker +
PipelineExecutor) run on their own threads and event loops and share a
SQLite run store, exactly as two processes share Postgres in production.
Only the pipeline is fake (core.chat_runner.run_chat_pipeline) so no model
is called. Requests go over TCP, so a closed connection is a real disconnect.

Acceptance tests from Background_Runs_Implementation_Plan.md section 14:
  * the user closes the app mid-answer: the answer still completes
  * the web server dies mid-answer (gunicorn restart): the answer completes
    and the app reattaches on the new server
  * reconnect with ?after=N: the missing events, no text twice
  * two requests on one thread: the second gets 409 with the active run
  * BACKGROUND_RUNS off: today's inline path, no run created
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time

import httpx
import jwt
import pytest
import uvicorn

import core.gateway as gw
import core.run_api as run_api
import core.run_store as run_store_mod
from core import settings as S
from core.auth import require_user_key
from core.file_processor import FileContext
from core.run_executor import PipelineExecutor
from core.run_store import RunStore
from workers.run_worker import RunWorker

SECRET = "endpoint-test-secret-long-enough-for-hs256"
CALLS: list[dict] = []
GATES: dict[str, threading.Event] = {}
FILES_SEEN: list[dict] = []


def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev)}\n\n"


async def fake_pipeline(inputs, build_initial_state, followup_suggestions_fn,
                        async_timeout_cm, node_status_map):
    """Stands in for core.chat_runner.run_chat_pipeline."""
    CALLS.append({"thread_id": inputs.thread_id, "run_id": inputs.run_id,
                  "user_id": inputs.user_id, "query": inputs.query,
                  "skip_tid": inputs.skip_thread_id_event,
                  "file_context": inputs.file_context, "endpoint": inputs.endpoint_name})
    if not inputs.skip_thread_id_event:
        yield _sse({"type": "thread_id", "data": inputs.thread_id})
    yield _sse({"type": "status", "agent": "memory", "message": "Loading..."})
    n, delay = (40, 0.05) if inputs.query.startswith("slow") else (20, 0.0)
    gate = GATES.get(inputs.query)
    words = [f"w{i} " for i in range(n)]
    for i, w in enumerate(words):
        if gate is not None and i == 3:
            await asyncio.to_thread(gate.wait, 15)
        if delay:
            await asyncio.sleep(delay)
        yield _sse({"type": "token", "content": w})
    yield _sse({"type": "response", "content": "".join(words)})
    yield _sse({"type": "done", "agents_used": ["Legislation"], "conversation_turn": 1,
                "token_usage": {"total_tokens": 5}, "thread_id": inputs.thread_id})


async def fake_process_files(files, thread_id, writer=None, force_ocr=False, batch_id="",
                             page_counts=None):
    for path, name, size in files:
        with open(path, "rb") as f:
            FILES_SEEN.append({"path": path, "name": name, "size": size, "data": f.read()})
    if writer:
        writer({"type": "file_processing", "stage": "extract", "message": "Reading..."})
    return FileContext(summary=f"Read {len(files)} file(s)", file_names=[n for _, n, _ in files])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """The front door: core.gateway.app under uvicorn, lifespan off."""

    def __init__(self):
        self.port = _free_port()
        self.srv = uvicorn.Server(uvicorn.Config(gw.app, host="127.0.0.1", port=self.port,
                                                 lifespan="off", log_level="warning"))
        self.thread = threading.Thread(target=self.srv.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "Server":
        self.thread.start()
        t0 = time.time()
        while not self.srv.started:
            assert time.time() - t0 < 15, "server did not start"
            time.sleep(0.02)
        return self

    def stop(self, force: bool = False) -> None:
        self.srv.force_exit = force
        self.srv.should_exit = True
        self.thread.join(15)


class Runner:
    """The runner process, on its own thread and loop."""

    def __init__(self, store: RunStore):
        self.store = store
        self.worker: RunWorker | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._main, daemon=True)

    def _main(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        executor = PipelineExecutor("fake-graph")
        self.worker = RunWorker(self.store, executor, concurrency=2, lease_s=2,
                                token_flush_ms=50, poll_s=0.05, sweep_every_s=0.5,
                                liveness_every_s=0.5, on_run_finished=executor.cleanup)
        self.ready.set()
        self.loop.run_until_complete(self.worker.run_forever())

    def start(self) -> "Runner":
        self.thread.start()
        assert self.ready.wait(5)
        t0 = time.time()
        while not self.store.live_workers():
            assert time.time() - t0 < 5, "runner did not report alive"
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        if self.loop and self.worker:
            self.loop.call_soon_threadsafe(self.worker.request_stop)
        self.thread.join(15)


PG_URL = os.getenv("TEST_POSTGRES_URL", "")


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path, monkeypatch):
    if request.param == "postgres":
        if not PG_URL:
            pytest.skip("TEST_POSTGRES_URL not set")
        import psycopg
        with psycopg.connect(PG_URL, autocommit=True) as c:
            for t in ("run_outbox", "run_watchers", "run_events", "run_workers", "runs"):
                c.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        st = RunStore(postgres_url=PG_URL, callback_enabled=lambda s: True)
    else:
        st = RunStore(sqlite_path=str(tmp_path / "runs.db"), callback_enabled=lambda s: True)
    st.ensure_schema()
    monkeypatch.setattr(run_store_mod, "_store", st)
    monkeypatch.setattr(S, "BACKGROUND_RUNS", True)
    monkeypatch.setattr(S, "USER_AUTH_MODE", "off")
    monkeypatch.setattr(S, "USER_JWT_SECRET", SECRET)
    monkeypatch.setattr(S, "USER_JWT_ALGORITHMS", ["HS256"])
    monkeypatch.setattr(S, "RUN_STAGING_ROOT", str(tmp_path / "staging"))
    monkeypatch.setattr(run_api, "_runner_seen", {"at": -1e9, "ok": False})
    monkeypatch.setattr("core.chat_runner.run_chat_pipeline", fake_pipeline)
    monkeypatch.setattr("core.file_processor.process_files", fake_process_files)
    monkeypatch.setattr(gw.limiter, "enabled", False)
    gw.app.dependency_overrides[require_user_key] = lambda: "test"
    gw.app.state.agent_graph = "fake-graph"
    CALLS.clear()
    GATES.clear()
    FILES_SEEN.clear()
    yield st
    gw.app.dependency_overrides.pop(require_user_key, None)
    st.close()


@pytest.fixture
def server(store):
    srv = Server().start()
    yield srv
    srv.stop(force=True)


@pytest.fixture
def runner(store):
    r = Runner(store).start()
    yield r
    r.stop()


def events_of(resp, stop_after=None):
    """Parse SSE `data:` lines; stop early when stop_after(events) is true."""
    out = []
    for line in resp.iter_lines():
        if not line.startswith("data: "):
            continue
        out.append(json.loads(line[6:]))
        if stop_after is not None and stop_after(out):
            break
    return out


def stream(server, body, headers=None, stop_after=None, path="/pyapi/search/stream"):
    with httpx.stream("POST", server.url + path, json=body, headers=headers or {},
                      timeout=30) as r:
        assert r.status_code == 200, r.read()
        return events_of(r, stop_after)


def reattach(server, run_id, after, headers=None, params=None):
    p = {"after": after, **(params or {})}
    with httpx.stream("GET", f"{server.url}/pyapi/runs/{run_id}/stream", params=p,
                      headers=headers or {}, timeout=30) as r:
        assert r.status_code == 200, r.read()
        return events_of(r)


def wait_status(store, run_id, statuses=("done", "failed", "cancelled"), timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        run = store.get_run(run_id)
        if run and run.status in statuses:
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} still {store.get_run(run_id).status}")


def tokens_text(evs):
    return "".join(e.get("content", "") for e in evs if e.get("type") == "token")


def token(user):
    return jwt.encode({"userId": user, "exp": int(time.time()) + 600}, SECRET, algorithm="HS256")


# ---------------------------------------------------------------------------

class TestBackgroundStream:
    def test_end_to_end(self, store, server, runner):
        evs = stream(server, {"Promptquery": "what is bail", "user_id": "u-1"})
        assert evs[0] == {"type": "thread_id", "data": evs[1]["thread_id"]}
        assert evs[1]["type"] == "run_id" and evs[1]["seq"] == 0
        run_id = evs[1]["data"]
        assert evs[-1]["type"] == "done" and evs[-1]["run_id"] == run_id
        assert tokens_text(evs) == "".join(f"w{i} " for i in range(20))
        assert [e["seq"] for e in evs[2:]] == list(range(1, len(evs) - 1))
        run = wait_status(store, run_id)
        assert run.status == "done" and run.user_id == "u-1"
        call = CALLS[-1]
        assert call["run_id"] == run_id and call["skip_tid"] is True and call["user_id"] == "u-1"
        assert sum(1 for e in evs if e["type"] == "thread_id") == 1

    def test_closing_the_app_does_not_stop_the_answer(self, store, server, runner):
        evs = stream(server, {"Promptquery": "slow answer"},
                     stop_after=lambda es: sum(1 for e in es if e["type"] == "token") >= 3)
        run_id = evs[1]["data"]
        assert store.get_run(run_id).status == "running"      # connection gone, run alive
        run = wait_status(store, run_id)
        assert run.status == "done" and run.response_text == "".join(f"w{i} " for i in range(40))
        assert store.get_outbox(run_id)["payload"]["connected"] is False

    def test_reconnect_receives_missing_events_without_duplicates(self, store, server, runner):
        first = stream(server, {"Promptquery": "slow reconnect"},
                       stop_after=lambda es: sum(1 for e in es if e["type"] == "token") >= 4)
        run_id = first[1]["data"]
        last_seq = first[-1]["seq"]
        time.sleep(0.4)                                        # the run moves on meanwhile
        rest = reattach(server, run_id, after=last_seq)
        seqs = [e["seq"] for e in first[2:]] + [e["seq"] for e in rest]
        assert seqs == list(range(1, seqs[-1] + 1))            # no gap, no repeat
        assert tokens_text(first) + tokens_text(rest) == "".join(f"w{i} " for i in range(40))
        assert rest[-1]["type"] == "done"

    def test_full_replay_of_a_finished_run(self, store, server, runner):
        evs = stream(server, {"Promptquery": "quick"})
        run_id = evs[1]["data"]
        replay = reattach(server, run_id, after=0)
        assert replay == evs[2:]

    def test_web_server_restart_mid_answer(self, store, runner):
        """gunicorn recycling a worker = the front door process dying."""
        a = Server().start()
        try:
            first = stream(a, {"Promptquery": "slow restart"},
                           stop_after=lambda es: sum(1 for e in es if e["type"] == "token") >= 3)
        finally:
            a.stop(force=True)
        run_id = first[1]["data"]
        b = Server().start()
        try:
            rest = reattach(b, run_id, after=first[-1]["seq"])
        finally:
            b.stop(force=True)
        assert tokens_text(first) + tokens_text(rest) == "".join(f"w{i} " for i in range(40))
        assert wait_status(store, run_id).status == "done"


class TestThreadLock:
    def test_second_request_on_a_busy_thread_gets_409(self, store, server, runner):
        GATES["hold"] = threading.Event()
        first = stream(server, {"Promptquery": "hold", "globalThreadId": "T-busy"},
                       stop_after=lambda es: any(e["type"] == "token" for e in es))
        run_id = first[1]["data"]
        r = httpx.post(server.url + "/pyapi/search/stream",
                       json={"Promptquery": "again", "globalThreadId": "T-busy"}, timeout=10)
        assert r.status_code == 409
        body = r.json()
        assert body["active_run_id"] == run_id and body["status"] == "running"
        rb = httpx.post(server.url + "/pyapi/search",
                        json={"Promptquery": "batch", "globalThreadId": "T-busy"}, timeout=10)
        assert rb.status_code == 409 and rb.json()["active_run_id"] == run_id
        rc = httpx.post(server.url + "/pyapi/chat",
                        data={"query": "chat", "globalThreadId": "T-busy"}, timeout=10)
        assert rc.status_code == 409
        GATES["hold"].set()
        assert wait_status(store, run_id).status == "done"
        after = stream(server, {"Promptquery": "now free", "globalThreadId": "T-busy"})
        assert after[-1]["type"] == "done"
        assert [c["query"] for c in CALLS] == ["hold", "now free"]   # the 409s never ran

    def test_active_run_and_status_endpoints(self, store, server, runner):
        GATES["hold2"] = threading.Event()
        first = stream(server, {"Promptquery": "hold2", "globalThreadId": "T-2"},
                       stop_after=lambda es: any(e["type"] == "token" for e in es))
        run_id = first[1]["data"]
        a = httpx.get(server.url + "/pyapi/threads/T-2/active-run", timeout=10).json()
        assert a["run_id"] == run_id and a["status"] == "running"
        s = httpx.get(f"{server.url}/pyapi/runs/{run_id}", timeout=10).json()
        assert s["status"] == "running" and "secrets" not in s and "request" not in s
        GATES["hold2"].set()
        wait_status(store, run_id)
        assert httpx.get(server.url + "/pyapi/threads/T-2/active-run",
                         timeout=10).json()["status"] == "done"
        assert httpx.get(server.url + "/pyapi/threads/none/active-run",
                         timeout=10).status_code == 404
        assert httpx.get(server.url + "/pyapi/runs/nope", timeout=10).status_code == 404


class TestInlinePaths:
    def test_flag_off_keeps_todays_behaviour(self, store, server, monkeypatch):
        monkeypatch.setattr(S, "BACKGROUND_RUNS", False)
        evs = stream(server, {"Promptquery": "inline", "globalThreadId": "T-in"})
        assert evs[0] == {"type": "thread_id", "data": "T-in"}
        assert "run_id" not in [e["type"] for e in evs] and "seq" not in evs[-1]
        assert CALLS[-1]["run_id"] is None and CALLS[-1]["skip_tid"] is False
        assert store.latest_run("T-in") is None

    def test_no_runner_alive_falls_back_inline(self, store, server):
        evs = stream(server, {"Promptquery": "fallback", "globalThreadId": "T-fb"})
        assert evs[-1]["type"] == "done" and "run_id" not in evs[-1]
        assert CALLS[-1]["run_id"] is None
        assert store.latest_run("T-fb") is None


class TestChatUploads:
    def test_upload_is_staged_and_processed_by_the_runner(self, store, server, runner, tmp_path):
        with httpx.stream("POST", server.url + "/pyapi/chat",
                          data={"query": "read my note", "user_id": "u-9"},
                          files={"files": ("note.txt", b"the facts of the case", "text/plain")},
                          timeout=30) as r:
            assert r.status_code == 200, r.read()
            evs = events_of(r)
        run_id = evs[1]["data"]
        types = [e["type"] for e in evs]
        assert types[:2] == ["thread_id", "run_id"] and "file_processing" in types
        assert evs[-1]["type"] == "done"
        assert FILES_SEEN and FILES_SEEN[0]["data"] == b"the facts of the case"
        assert FILES_SEEN[0]["name"] == "note.txt"
        assert os.path.dirname(FILES_SEEN[0]["path"]) == os.path.join(S.RUN_STAGING_ROOT, run_id)
        call = CALLS[-1]
        assert call["endpoint"] == "/pyapi/chat" and call["run_id"] == run_id
        assert call["file_context"]["summary"] == "Read 1 file(s)"
        wait_status(store, run_id)
        t0 = time.time()
        while os.path.exists(os.path.join(S.RUN_STAGING_ROOT, run_id)):
            assert time.time() - t0 < 5, "staging folder not removed"
            time.sleep(0.05)


class TestOwnership:
    def test_runs_are_visible_to_their_owner_only(self, store, server, runner, monkeypatch):
        monkeypatch.setattr(S, "USER_AUTH_MODE", "optional")
        a, b = {"Authorization": f"Bearer {token('alice')}"}, {"Authorization": f"Bearer {token('bob')}"}
        evs = stream(server, {"Promptquery": "mine"}, headers=a)
        run_id = evs[1]["data"]
        run = wait_status(store, run_id)
        assert run.user_id == "alice" and run.identity_source == "jwt"
        assert CALLS[-1]["user_id"] == "alice"
        url = f"{server.url}/pyapi/runs/{run_id}"
        assert httpx.get(url, headers=a, timeout=10).status_code == 200
        assert httpx.get(url, headers=b, timeout=10).status_code == 404
        assert httpx.get(url, timeout=10).status_code == 404
        assert httpx.get(url + "/stream", headers=b, timeout=10).status_code == 404
        assert len(reattach(server, run_id, 0, headers=a)) > 3

    def test_payload_user_id_cannot_override_the_token(self, store, server, runner, monkeypatch):
        monkeypatch.setattr(S, "USER_AUTH_MODE", "optional")
        r = httpx.post(server.url + "/pyapi/search/stream",
                       json={"Promptquery": "spoof", "user_id": "bob"},
                       headers={"Authorization": f"Bearer {token('alice')}"}, timeout=10)
        assert r.status_code == 400

    def test_required_mode_rejects_anonymous_requests(self, store, server, runner, monkeypatch):
        monkeypatch.setattr(S, "USER_AUTH_MODE", "required")
        r = httpx.post(server.url + "/pyapi/search/stream",
                       json={"Promptquery": "anon", "user_id": "u"}, timeout=10)
        assert r.status_code == 401
        assert CALLS == []


class TestCancelAndHealth:
    def test_stop_ends_the_open_stream_and_frees_the_thread(self, store, server, runner):
        GATES["stopme"] = threading.Event()
        got: list[dict] = []
        opened = threading.Event()

        def watch():
            with httpx.stream("POST", server.url + "/pyapi/search/stream",
                              json={"Promptquery": "stopme", "globalThreadId": "T-stop"},
                              timeout=30) as r:
                for line in r.iter_lines():
                    if line.startswith("data: "):
                        got.append(json.loads(line[6:]))
                        if any(e["type"] == "token" for e in got):
                            opened.set()

        t = threading.Thread(target=watch, daemon=True)
        t.start()
        assert opened.wait(10)
        run_id = got[1]["data"]
        t0 = time.time()
        r = httpx.post(f"{server.url}/pyapi/runs/{run_id}/cancel", timeout=10)
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        t.join(10)
        assert not t.is_alive(), "open stream did not end after Stop"
        assert time.time() - t0 < 3
        assert got[-1]["type"] == "done" and got[-1].get("cancelled") is True
        # the thread is free at once: no 409 for the next question
        nxt = stream(server, {"Promptquery": "next one", "globalThreadId": "T-stop"})
        assert nxt[-1]["type"] == "done"
        run = store.get_run(run_id)
        assert run.status == "cancelled" and run.response_text == "w0 w1 w2 "
        GATES["stopme"].set()                    # release the parked fake-pipeline thread
        time.sleep(0.3)
        assert store.get_run(run_id).status == "cancelled"   # the runner never overwrote it

    def test_stop_after_finish_is_409(self, store, server, runner):
        evs = stream(server, {"Promptquery": "done already"})
        run_id = evs[1]["data"]
        wait_status(store, run_id)
        r = httpx.post(f"{server.url}/pyapi/runs/{run_id}/cancel", timeout=10)
        assert r.status_code == 409 and r.json()["status"] == "done"

    def test_cannot_stop_someone_elses_run(self, store, server, runner, monkeypatch):
        monkeypatch.setattr(S, "USER_AUTH_MODE", "optional")
        GATES["alice-run"] = threading.Event()
        a = {"Authorization": f"Bearer {token('alice')}"}
        first = stream(server, {"Promptquery": "alice-run"}, headers=a,
                       stop_after=lambda es: any(e["type"] == "token" for e in es))
        run_id = first[1]["data"]
        r = httpx.post(f"{server.url}/pyapi/runs/{run_id}/cancel",
                       headers={"Authorization": f"Bearer {token('bob')}"}, timeout=10)
        assert r.status_code == 404 and store.get_run(run_id).status == "running"
        assert httpx.post(f"{server.url}/pyapi/runs/{run_id}/cancel", headers=a,
                          timeout=10).status_code == 200
        GATES["alice-run"].set()

    def test_health_shows_runner_and_queue(self, store, server, runner):
        h = httpx.get(server.url + "/pyapi/health/runs", timeout=10).json()
        assert h["status"] == "ok" and h["enabled"] is True and h["runners"] == 1
        assert h["capacity"] == 2 and h["queued"] == 0 and h["outbox_dead"] == 0

    def test_health_reports_missing_runner(self, store, server):
        h = httpx.get(server.url + "/pyapi/health/runs", timeout=10).json()
        assert h["status"] == "no_runner" and h["runners"] == 0


class TestMetrics:
    def test_counters_and_queue_gauges(self, store, server, runner):
        from core.auth import require_admin_key
        from core.metrics import METRICS
        started = METRICS["background_runs_started_total"].labels(endpoint="/pyapi/search/stream")
        before = started._value.get()
        evs = stream(server, {"Promptquery": "count me"})
        assert evs[-1]["type"] == "done"
        assert started._value.get() == before + 1
        gw.app.dependency_overrides[require_admin_key] = lambda: "admin"
        try:
            text = httpx.get(server.url + "/pyapi/metrics", timeout=10).text
        finally:
            gw.app.dependency_overrides.pop(require_admin_key, None)
        assert "lawtech_background_runners_alive 1" in text
        assert "lawtech_background_runs_queued 0" in text
        assert "lawtech_background_run_event_delay_seconds_count" in text


class TestRunnerDownWithStuckRun:
    def test_a_stuck_run_does_not_lock_the_thread_while_no_runner_serves(self, store, server):
        """With no runner alive nothing clears a stuck run (the sweeper lives
        in the runner), so the busy check must not apply: answer inline."""
        store.create_run(thread_id="T-stuck", endpoint="/pyapi/search/stream",
                         request={"query": "never claimed"})
        evs = stream(server, {"Promptquery": "still answered", "globalThreadId": "T-stuck"})
        assert evs[-1]["type"] == "done" and CALLS[-1]["run_id"] is None
        with httpx.stream("POST", server.url + "/pyapi/chat",
                          data={"query": "chat too", "globalThreadId": "T-stuck"}, timeout=30) as r:
            assert r.status_code == 200
            chat_evs = events_of(r)
        assert chat_evs[-1]["type"] == "done" and CALLS[-1]["endpoint"] == "/pyapi/chat"
        assert not os.listdir(S.RUN_STAGING_ROOT) if os.path.isdir(S.RUN_STAGING_ROOT) else True

    def test_chat_without_files_creates_no_staging_folder(self, store, server, runner):
        evs = stream(server, {"Promptquery": "x"})          # warm the runner check
        with httpx.stream("POST", server.url + "/pyapi/chat", data={"query": "no files"},
                          timeout=30) as r:
            assert r.status_code == 200
            chat_evs = events_of(r)
        run_id = chat_evs[1]["data"]
        assert chat_evs[-1]["type"] == "done"
        assert not os.path.exists(os.path.join(S.RUN_STAGING_ROOT, run_id))


class TestNodeHistoryFields:
    def test_parent_id_never_fails_a_request(self):
        for value, want in [(5521, "5521"), ("5521", "5521"), (" 77 ", "77"), ("", None),
                            (None, None), (True, None)]:
            assert gw.SearchRequest(Promptquery="q", parent_id=value).parent_id == want

    def test_parent_id_is_stored_and_done_says_the_app_saves_a_watched_answer(
            self, store, server, runner):
        evs = stream(server, {"Promptquery": "regenerate this", "user_id": "101",
                              "parent_id": 5521})
        run = wait_status(store, evs[1]["data"])
        assert run.request["parent_id"] == "5521"
        assert evs[-1]["type"] == "done" and evs[-1]["history_saved_by_server"] is False

    def test_chat_form_accepts_parent_id(self, store, server, runner):
        for sent, want in [("77", "77"), ("", None)]:
            with httpx.stream("POST", server.url + "/pyapi/chat",
                              data={"query": "q", "parent_id": sent, "user_id": "101"},
                              timeout=30) as r:
                assert r.status_code == 200
                evs = events_of(r)
            assert wait_status(store, evs[1]["data"]).request["parent_id"] == want
