"""Run store: the queue, event log, presence list and outbox for background runs.

A *run* is one attempt to answer one message. The front door (FastAPI) creates
it, the runner process (workers/run_worker.py) executes it, and both talk only
through the tables here, so closing the app never stops an answer.
Design: Background_Runs_Implementation_Plan.md (sections 4, 5, 7, 8, 9).

Tables (Postgres in prod, SQLite for local development):
  runs          one row per run; status queued|running|done|failed|cancelled
  run_events    every SSE event of a run, in order (seq 1, 2, 3 ...)
  run_watchers  one row per open SSE tail, refreshed every few seconds
  run_outbox    the run-complete message for Node, written in the same
                transaction as the final status (transactional outbox)
  run_workers   liveness of runner processes

Safety rules this module enforces:
  * One active run per thread: a partial unique index on runs(thread_id)
    WHERE status IN ('queued','running'). A second create raises
    ActiveRunExists carrying the run already in progress.
  * Leases with fencing: a claim hands out a fresh lease_token. Every write
    by the holder (append, heartbeat, finalize) matches on that token and on
    status='running'; zero rows means the lease is gone and LeaseLost is
    raised, so a runner whose lease expired cannot write anything.
  * Cancel is immediate: the run row turns terminal at once (the thread is
    free again at once) and the holder's next write hits the fence.
  * The outbox row is inserted in the same transaction as the terminal
    status, so "finished but Node never told" and "Node told about an
    answer that was not recorded" cannot happen.

All timestamps come from one clock (`now_fn`, UTC), the runner and the web
workers run on the same host. Tests inject a fake clock.

Every public method is synchronous (psycopg/sqlite3 are blocking); async
callers use `store.aio.<method>(...)`, which runs it in a worker thread.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator

from core.logger import get_logger

log = get_logger("RunStore")

ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("done", "failed", "cancelled")

# Event types that do not count as output. A run that crashed having stored
# only these can be retried from scratch: the user saw nothing but progress,
# and nothing irreversible (file processing, a saved draft) has happened.
# Everything else (token, response, sources, file_processing,
# drafting_progress, artifact_*, error, done ...) marks the run as having
# output, and an interrupted run with output is failed, never re-run.
SAFE_PRE_OUTPUT_TYPES = frozenset({"status", "progress", "context", "agents_planned"})

# Postgres LISTEN/NOTIFY channels. Payloads: "<run_id>:<seq>" and "<run_id>".
EVENTS_CHANNEL = "lawtech_run_events"
QUEUED_CHANNEL = "lawtech_run_queued"

INTERRUPTED_MESSAGE = (
    "The answer was interrupted before it finished. Please ask again."
)
NO_WORKER_MESSAGE = (
    "The answer could not be started. Please try again in a moment."
)


class ActiveRunExists(Exception):
    """The thread already has a queued or running run."""

    def __init__(self, run: "RunRow"):
        super().__init__(f"thread {run.thread_id[:12]} has active run {run.run_id[:12]}")
        self.run = run


class LeaseLost(Exception):
    """The caller no longer holds the run (lease expired, reclaimed or cancelled)."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(v) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v))


def _loads(v: str | None, default):
    if not v:
        return default
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


@dataclass
class RunRow:
    run_id: str
    thread_id: str
    user_id: str
    identity_source: str
    status: str
    endpoint: str
    request: dict
    attempts: int
    max_attempts: int
    lease_token: str
    lease_until: datetime | None
    worker_id: str
    has_output: bool
    last_seq: int
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    turn_number: int | None
    response_text: str
    error: str
    token_usage: dict

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def public(self) -> dict:
        """The fields an API response may show. No secrets, no request body."""
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        return {
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "status": self.status,
            "last_seq": self.last_seq,
            "attempts": self.attempts,
            "turn_number": self.turn_number,
            "error": self.error,
            "created_at": iso(self.created_at),
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
        }


@dataclass
class RunEvent:
    run_id: str
    seq: int
    type: str
    payload: dict
    created_at: datetime | None

    def sse(self) -> str:
        """The stored event as one SSE message, carrying its seq."""
        body = dict(self.payload)
        body["seq"] = self.seq
        # json.dumps defaults, as core/chat_runner._sse: replayed events are
        # byte-compatible with live ones (non-ASCII escaped)
        return f"data: {json.dumps(body)}\n\n"


@dataclass
class Claim:
    run: RunRow
    lease_token: str
    secrets: dict


@dataclass
class OutboxItem:
    outbox_id: int
    run_id: str
    kind: str
    payload: dict
    attempts: int
    created_at: datetime | None = None


@dataclass
class SweepReport:
    requeued: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    expired_queued: list[str] = field(default_factory=list)
    events_deleted: int = 0
    runs_deleted: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.requeued or self.failed or self.repaired or self.expired_queued
                    or self.events_deleted or self.runs_deleted)


def partial_text(events: Iterable[RunEvent]) -> str:
    """The answer text as a client rendered it: the last `response` if one
    was stored, else the tokens after the last `token_reset`."""
    response = None
    tokens: list[str] = []
    for e in events:
        if e.type == "response":
            response = e.payload.get("content") or ""
        elif e.type == "token":
            tokens.append(e.payload.get("content") or "")
        elif e.type == "token_reset":
            tokens = []
    return response if response is not None else "".join(tokens)


def artifact_ref(events: Iterable[RunEvent]) -> dict | None:
    """The draft panel's reference, as the app stores it in StoreHistory's
    `source.artifact`: {id, version, sectionCount} from `artifact_start`,
    replaced by {id, version, title, sectionCount} from `artifact_done`.
    None when the answer has no draft panel."""
    ref = None
    for e in events:
        p = e.payload
        if e.type == "artifact_start" and p.get("artifact_id"):
            ref = {"id": p["artifact_id"], "version": p.get("version"),
                   "sectionCount": len(p.get("sections") or [])}
        elif e.type == "artifact_done" and isinstance(p.get("artifact"), dict):
            art = p["artifact"]
            ref = {"id": art.get("artifact_id") or p.get("artifact_id"),
                   "version": art["version"] if art.get("version") is not None else p.get("version"),
                   "sectionCount": len(art.get("sections") or [])}
            if art.get("title"):
                ref["title"] = art["title"]
    return ref


def with_history_flag(events: list[dict], saved_by_server: bool) -> list[dict]:
    """Mark the `done` event(s): `history_saved_by_server` true means Python
    saves this answer to the chat history itself, so the app must not call
    StoreHistory for it (Node would keep it twice and charge twice); false
    means the app saves it as before."""
    return [{**ev, "history_saved_by_server": bool(saved_by_server)}
            if ev.get("type") == "done" else ev for ev in events]


def terminal_done_event(run: RunRow, **extra) -> dict:
    """A `done` for a run that did not produce its own (cancel, crash,
    recovery). Carries the keys every `done` carries so clients that read
    them unconditionally keep working."""
    ev = {
        "type": "done",
        "run_id": run.run_id,
        "thread_id": run.thread_id,
        "agents_used": [],
        "token_usage": {},
        "conversation_turn": 0,
        "query_rewritten": False,
        "effective_query": None,
    }
    ev.update(extra)
    return ev


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_PG_DDL = [
    """CREATE TABLE IF NOT EXISTS runs (
        run_id            TEXT PRIMARY KEY,
        thread_id         TEXT NOT NULL,
        user_id           TEXT NOT NULL DEFAULT '',
        identity_source   TEXT NOT NULL DEFAULT '',
        status            TEXT NOT NULL
                          CHECK (status IN ('queued','running','done','failed','cancelled')),
        endpoint          TEXT NOT NULL,
        request_json      TEXT NOT NULL,
        secrets_json      TEXT NOT NULL DEFAULT '',
        attempts          INTEGER NOT NULL DEFAULT 0,
        max_attempts      INTEGER NOT NULL DEFAULT 2,
        lease_token       TEXT NOT NULL DEFAULT '',
        lease_until       TIMESTAMPTZ,
        worker_id         TEXT NOT NULL DEFAULT '',
        has_output        BOOLEAN NOT NULL DEFAULT FALSE,
        last_seq          INTEGER NOT NULL DEFAULT 0,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        started_at        TIMESTAMPTZ,
        finished_at       TIMESTAMPTZ,
        turn_number       INTEGER,
        response_text     TEXT NOT NULL DEFAULT '',
        error             TEXT NOT NULL DEFAULT '',
        token_usage_json  TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_runs_active_thread
        ON runs (thread_id) WHERE status IN ('queued','running')""",
    "CREATE INDEX IF NOT EXISTS idx_runs_queue ON runs (created_at) WHERE status = 'queued'",
    "CREATE INDEX IF NOT EXISTS idx_runs_lease ON runs (lease_until) WHERE status = 'running'",
    "CREATE INDEX IF NOT EXISTS idx_runs_thread ON runs (thread_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_runs_user ON runs (user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_runs_finished ON runs (finished_at)",
    """CREATE TABLE IF NOT EXISTS run_events (
        run_id        TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        seq           INTEGER NOT NULL,
        type          TEXT NOT NULL,
        payload_json  TEXT NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (run_id, seq)
    )""",
    """CREATE TABLE IF NOT EXISTS run_watchers (
        run_id      TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        watcher_id  TEXT NOT NULL,
        last_seen   TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (run_id, watcher_id)
    )""",
    """CREATE TABLE IF NOT EXISTS run_outbox (
        outbox_id        BIGSERIAL PRIMARY KEY,
        run_id           TEXT NOT NULL UNIQUE REFERENCES runs(run_id) ON DELETE CASCADE,
        kind             TEXT NOT NULL DEFAULT 'run_complete',
        payload_json     TEXT NOT NULL,
        status           TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','sent','dead','skipped')),
        attempts         INTEGER NOT NULL DEFAULT 0,
        next_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_error       TEXT NOT NULL DEFAULT '',
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        sent_at          TIMESTAMPTZ
    )""",
    "CREATE INDEX IF NOT EXISTS idx_outbox_due ON run_outbox (next_attempt_at) WHERE status = 'pending'",
    """CREATE TABLE IF NOT EXISTS run_workers (
        worker_id    TEXT PRIMARY KEY,
        last_seen    TIMESTAMPTZ NOT NULL,
        concurrency  INTEGER NOT NULL,
        running      INTEGER NOT NULL DEFAULT 0,
        version      TEXT NOT NULL DEFAULT ''
    )""",
]

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    thread_id         TEXT NOT NULL,
    user_id           TEXT NOT NULL DEFAULT '',
    identity_source   TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL
                      CHECK (status IN ('queued','running','done','failed','cancelled')),
    endpoint          TEXT NOT NULL,
    request_json      TEXT NOT NULL,
    secrets_json      TEXT NOT NULL DEFAULT '',
    attempts          INTEGER NOT NULL DEFAULT 0,
    max_attempts      INTEGER NOT NULL DEFAULT 2,
    lease_token       TEXT NOT NULL DEFAULT '',
    lease_until       TEXT,
    worker_id         TEXT NOT NULL DEFAULT '',
    has_output        INTEGER NOT NULL DEFAULT 0,
    last_seq          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    started_at        TEXT,
    finished_at       TEXT,
    turn_number       INTEGER,
    response_text     TEXT NOT NULL DEFAULT '',
    error             TEXT NOT NULL DEFAULT '',
    token_usage_json  TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_runs_active_thread
    ON runs (thread_id) WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS idx_runs_queue ON runs (created_at) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS idx_runs_lease ON runs (lease_until) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_runs_thread ON runs (thread_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_user ON runs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_finished ON runs (finished_at);
CREATE TABLE IF NOT EXISTS run_events (
    run_id        TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    type          TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS run_watchers (
    run_id      TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    watcher_id  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    PRIMARY KEY (run_id, watcher_id)
);
CREATE TABLE IF NOT EXISTS run_outbox (
    outbox_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL UNIQUE REFERENCES runs(run_id) ON DELETE CASCADE,
    kind             TEXT NOT NULL DEFAULT 'run_complete',
    payload_json     TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','sent','dead','skipped')),
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  TEXT NOT NULL,
    last_error       TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    sent_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_due ON run_outbox (next_attempt_at) WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS run_workers (
    worker_id    TEXT PRIMARY KEY,
    last_seen    TEXT NOT NULL,
    concurrency  INTEGER NOT NULL,
    running      INTEGER NOT NULL DEFAULT 0,
    version      TEXT NOT NULL DEFAULT ''
);
"""


# ---------------------------------------------------------------------------
# Connection wrapper: one SQL text for both backends ("?" placeholders)
# ---------------------------------------------------------------------------

class _Cx:
    def __init__(self, conn, pg: bool):
        self._conn = conn
        self.pg = pg

    def execute(self, sql: str, params: tuple | list = ()):
        if self.pg:
            sql = sql.replace("?", "%s")
        return self._conn.execute(sql, params)

    def one(self, sql: str, params: tuple | list = ()):
        # fetchall, not fetchone: an UPDATE ... RETURNING left half-read keeps
        # the SQLite statement open, and COMMIT then fails.
        rows = self.execute(sql, params).fetchall()
        return rows[0] if rows else None

    def all(self, sql: str, params: tuple | list = ()):
        return self.execute(sql, params).fetchall()


class _AsyncFacade:
    """`store.aio.method(...)` runs `store.method(...)` in a worker thread."""

    def __init__(self, store):
        self._store = store

    def __getattr__(self, name):
        fn = getattr(self._store, name)
        if not callable(fn):
            return fn

        async def call(*args, **kwargs):
            return await asyncio.to_thread(fn, *args, **kwargs)

        call.__name__ = name
        return call


class RunStore:
    """Both backends behind one interface. Use `make_run_store()`."""

    def __init__(
        self,
        *,
        postgres_url: str = "",
        sqlite_path: str = "",
        now_fn: Callable[[], datetime] = utcnow,
        pool_max: int = 5,
        callback_enabled: Callable[[str], bool] | None = None,
    ):
        if not postgres_url and not sqlite_path:
            raise ValueError("RunStore needs postgres_url or sqlite_path")
        self.pg = bool(postgres_url)
        self._postgres_url = postgres_url
        self._sqlite_path = sqlite_path
        self._now = now_fn
        self._pool_max = max(1, pool_max)
        self._pool = None
        self._pool_lock = threading.Lock()
        self._schema_ok = False
        self._schema_lock = threading.Lock()
        self._callback_enabled = callback_enabled or _callback_enabled_from_settings
        self.aio = _AsyncFacade(self)

    # -- plumbing -----------------------------------------------------------

    def _ts(self, dt: datetime | None):
        """A timestamp as the backend stores it (fixed-width ISO on SQLite,
        so string comparison is time comparison)."""
        if dt is None:
            return None
        dt = dt.astimezone(timezone.utc)
        return dt if self.pg else dt.isoformat(timespec="microseconds")

    def _get_pool(self):
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    from psycopg.rows import dict_row
                    from psycopg_pool import ConnectionPool
                    self._pool = ConnectionPool(
                        conninfo=self._postgres_url,
                        min_size=1,
                        max_size=self._pool_max,
                        open=True,
                        timeout=10.0,
                        kwargs={"row_factory": dict_row},
                        check=ConnectionPool.check_connection,
                    )
        return self._pool

    def _sqlite_connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._sqlite_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        # WAL + NORMAL: no corruption risk, one fsync per checkpoint instead of
        # per commit (local development backend only; prod is Postgres).
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def _tx(self) -> Iterator[_Cx]:
        """One write transaction. SQLite takes the write lock up front
        (BEGIN IMMEDIATE), which is what serialises claims across processes
        there; Postgres uses row locks (FOR UPDATE SKIP LOCKED)."""
        self.ensure_schema()
        if self.pg:
            with self._get_pool().connection() as conn:   # commit / rollback on exit
                yield _Cx(conn, pg=True)
            return
        conn = self._sqlite_connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield _Cx(conn, pg=False)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @contextmanager
    def _read(self) -> Iterator[_Cx]:
        self.ensure_schema()
        if self.pg:
            with self._get_pool().connection() as conn:
                yield _Cx(conn, pg=True)
            return
        conn = self._sqlite_connect()
        try:
            yield _Cx(conn, pg=False)
        finally:
            conn.close()

    def _lock(self) -> str:
        return " FOR UPDATE SKIP LOCKED" if self.pg else ""

    def _is_unique_violation(self, e: BaseException) -> bool:
        if self.pg:
            from psycopg.errors import UniqueViolation
            return isinstance(e, UniqueViolation)
        return isinstance(e, sqlite3.IntegrityError) and "UNIQUE" in str(e).upper()

    def _notify(self, cx: _Cx, channel: str, payload: str) -> None:
        # Transactional: delivered to listeners only when the transaction commits.
        if cx.pg:
            cx.execute("SELECT pg_notify(?, ?)", (channel, payload))

    def ensure_schema(self) -> None:
        if self._schema_ok:
            return
        with self._schema_lock:
            if self._schema_ok:
                return
            if self.pg:
                with self._get_pool().connection() as conn:
                    for stmt in _PG_DDL:
                        conn.execute(stmt)
                    conn.commit()
            else:
                d = os.path.dirname(self._sqlite_path)
                if d:
                    os.makedirs(d, exist_ok=True)
                conn = self._sqlite_connect()
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.executescript(_SQLITE_DDL)
                finally:
                    conn.close()
            self._schema_ok = True
            log.info("Run store schema ensured", backend="postgresql" if self.pg else "sqlite")

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def _row(self, r) -> RunRow:
        return RunRow(
            run_id=r["run_id"],
            thread_id=r["thread_id"],
            user_id=r["user_id"] or "",
            identity_source=r["identity_source"] or "",
            status=r["status"],
            endpoint=r["endpoint"],
            request=_loads(r["request_json"], {}),
            attempts=int(r["attempts"] or 0),
            max_attempts=int(r["max_attempts"] or 1),
            lease_token=r["lease_token"] or "",
            lease_until=_parse_ts(r["lease_until"]),
            worker_id=r["worker_id"] or "",
            has_output=bool(r["has_output"]),
            last_seq=int(r["last_seq"] or 0),
            created_at=_parse_ts(r["created_at"]),
            started_at=_parse_ts(r["started_at"]),
            finished_at=_parse_ts(r["finished_at"]),
            turn_number=r["turn_number"],
            response_text=r["response_text"] or "",
            error=r["error"] or "",
            token_usage=_loads(r["token_usage_json"], {}),
        )

    def _event(self, r) -> RunEvent:
        return RunEvent(
            run_id=r["run_id"], seq=int(r["seq"]), type=r["type"],
            payload=_loads(r["payload_json"], {}), created_at=_parse_ts(r["created_at"]),
        )

    def _append_locked(self, cx: _Cx, run: RunRow, events: list[dict]) -> int:
        """Append events for a run whose row this transaction already updated
        (last_seq bumped). `run.last_seq` is the value BEFORE the bump."""
        now = self._ts(self._now())
        seq = run.last_seq
        for ev in events:
            seq += 1
            cx.execute(
                "INSERT INTO run_events (run_id, seq, type, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (run.run_id, seq, str(ev.get("type") or ""), json.dumps(ev, ensure_ascii=False), now),
            )
        if events:
            self._notify(cx, EVENTS_CHANNEL, f"{run.run_id}:{seq}")
        return seq

    # -- create / read ------------------------------------------------------

    def create_run(
        self,
        *,
        thread_id: str,
        endpoint: str,
        request: dict,
        user_id: str = "",
        identity_source: str = "",
        secrets: dict | None = None,
        run_id: str | None = None,
        max_attempts: int | None = None,
    ) -> RunRow:
        """Queue a run. Raises ActiveRunExists when the thread already has one."""
        from core import settings as S
        run_id = run_id or str(uuid.uuid4())
        max_attempts = max_attempts or S.RUN_MAX_ATTEMPTS
        for _attempt in range(2):
            try:
                with self._tx() as cx:
                    r = cx.one(
                        "INSERT INTO runs (run_id, thread_id, user_id, identity_source, status, "
                        "endpoint, request_json, secrets_json, max_attempts, created_at) "
                        "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?) RETURNING *",
                        (run_id, thread_id, user_id or "", identity_source or "", endpoint,
                         json.dumps(request, ensure_ascii=False),
                         json.dumps(secrets) if secrets else "",
                         int(max_attempts), self._ts(self._now())),
                    )
                    self._notify(cx, QUEUED_CHANNEL, run_id)
                    return self._row(r)
            except Exception as e:
                if not self._is_unique_violation(e):
                    raise
                active = self.active_run(thread_id)
                if active is not None:
                    raise ActiveRunExists(active) from None
                # The other run finished between our insert and our read: retry once.
        raise RuntimeError("create_run: thread lock contention did not settle")

    def get_run(self, run_id: str) -> RunRow | None:
        with self._read() as cx:
            r = cx.one("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        return self._row(r) if r else None

    def active_run(self, thread_id: str) -> RunRow | None:
        with self._read() as cx:
            r = cx.one(
                "SELECT * FROM runs WHERE thread_id = ? AND status IN ('queued','running')",
                (thread_id,),
            )
        return self._row(r) if r else None

    def latest_run(self, thread_id: str) -> RunRow | None:
        with self._read() as cx:
            r = cx.one(
                "SELECT * FROM runs WHERE thread_id = ? ORDER BY created_at DESC LIMIT 1",
                (thread_id,),
            )
        return self._row(r) if r else None

    def read_events(self, run_id: str, after_seq: int = 0, limit: int = 500) -> list[RunEvent]:
        with self._read() as cx:
            rows = cx.all(
                "SELECT * FROM run_events WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (run_id, int(after_seq), int(limit)),
            )
        return [self._event(r) for r in rows]

    def counts(self) -> dict:
        with self._read() as cx:
            rows = cx.all("SELECT status, COUNT(*) AS n FROM runs "
                          "WHERE status IN ('queued','running') GROUP BY status")
            ob = cx.all("SELECT status, COUNT(*) AS n FROM run_outbox "
                        "WHERE status IN ('pending','dead') GROUP BY status")
        out = {"queued": 0, "running": 0, "outbox_pending": 0, "outbox_dead": 0}
        for r in rows:
            out[r["status"]] = int(r["n"])
        for r in ob:
            out["outbox_" + r["status"]] = int(r["n"])
        return out

    # -- runner side: claim, heartbeat, append, finalize --------------------

    def claim(self, worker_id: str, lease_s: int | None = None) -> Claim | None:
        """Take the oldest queued run. Each claim gets a fresh lease_token."""
        from core import settings as S
        lease_s = lease_s or S.RUN_LEASE_S
        token = uuid.uuid4().hex
        now = self._now()
        with self._tx() as cx:
            r = cx.one(
                "UPDATE runs SET status = 'running', lease_token = ?, lease_until = ?, "
                "worker_id = ?, attempts = attempts + 1, "
                "started_at = COALESCE(started_at, ?) "
                "WHERE run_id = (SELECT run_id FROM runs WHERE status = 'queued' "
                "ORDER BY created_at LIMIT 1" + self._lock() + ") "
                "RETURNING *",
                (token, self._ts(now + timedelta(seconds=lease_s)), worker_id, self._ts(now)),
            )
            if not r:
                return None
            secrets = _loads(r["secrets_json"], {})
        run = self._row(r)
        log.info("Run claimed", run_id=run.run_id[:12], thread_id=run.thread_id[:12],
                 worker=worker_id, attempt=run.attempts)
        return Claim(run=run, lease_token=token, secrets=secrets)

    def heartbeat(self, run_id: str, lease_token: str, lease_s: int | None = None) -> bool:
        """Extend the lease. False means the lease is gone: stop the run."""
        from core import settings as S
        lease_s = lease_s or S.RUN_LEASE_S
        with self._tx() as cx:
            r = cx.one(
                "UPDATE runs SET lease_until = ? "
                "WHERE run_id = ? AND lease_token = ? AND status = 'running' RETURNING run_id",
                (self._ts(self._now() + timedelta(seconds=lease_s)), run_id, lease_token),
            )
        return r is not None

    def release_lease(self, run_id: str, lease_token: str) -> bool:
        """Expire our lease now (forced shutdown), so the next sweep recovers
        the run instead of waiting for the lease to run out."""
        with self._tx() as cx:
            r = cx.one(
                "UPDATE runs SET lease_until = ? "
                "WHERE run_id = ? AND lease_token = ? AND status = 'running' RETURNING run_id",
                (self._ts(self._now() - timedelta(seconds=1)), run_id, lease_token),
            )
        return r is not None

    def append_events(self, run_id: str, lease_token: str, events: list[dict]) -> int:
        """Store events in order; returns the last seq. Fenced: raises
        LeaseLost if the caller no longer holds the run."""
        if not events:
            raise ValueError("append_events needs at least one event")
        has_output = any(str(e.get("type") or "") not in SAFE_PRE_OUTPUT_TYPES for e in events)
        with self._tx() as cx:
            r = cx.one(
                "UPDATE runs SET last_seq = last_seq + ?, has_output = (has_output OR ?) "
                "WHERE run_id = ? AND lease_token = ? AND status = 'running' "
                "RETURNING *",
                (len(events), has_output, run_id, lease_token),
            )
            if r is None:
                raise LeaseLost(run_id)
            run = self._row(r)
            run.last_seq -= len(events)          # value before the bump
            return self._append_locked(cx, run, events)

    def finalize(
        self,
        run_id: str,
        lease_token: str,
        *,
        status: str,
        response_text: str = "",
        error: str = "",
        token_usage: dict | None = None,
        turn_number: int | None = None,
        final_events: list[dict] | None = None,
        sources: list | None = None,
        agents_used: list | None = None,
    ) -> RunRow:
        """End the run: terminal status, final events and the outbox row, in
        one transaction. Fenced: raises LeaseLost if the lease is gone."""
        if status not in ("done", "failed"):
            raise ValueError(f"finalize status must be done or failed, got {status!r}")
        final_events = final_events or []
        now = self._now()
        with self._tx() as cx:
            r = cx.one(
                "UPDATE runs SET status = ?, finished_at = ?, response_text = ?, error = ?, "
                "token_usage_json = ?, turn_number = ?, secrets_json = '', lease_token = '', "
                "lease_until = NULL, last_seq = last_seq + ?, "
                "has_output = (has_output OR ?) "
                "WHERE run_id = ? AND lease_token = ? AND status = 'running' RETURNING *",
                (status, self._ts(now), response_text or "", error or "",
                 json.dumps(token_usage) if token_usage else "", turn_number,
                 len(final_events), bool(final_events), run_id, lease_token),
            )
            if r is None:
                raise LeaseLost(run_id)
            run = self._row(r)
            before = RunRow(**{**run.__dict__, "last_seq": run.last_seq - len(final_events)})
            saved = self._insert_outbox(cx, run, sources=sources, agents_used=agents_used,
                                        pending=final_events)
            self._append_locked(cx, before, with_history_flag(final_events, saved))
        log.info("Run finished", run_id=run_id[:12], status=status, turn=turn_number,
                 events=run.last_seq)
        return run

    # -- front-door side: cancel, watchers ----------------------------------

    def cancel(self, run_id: str) -> RunRow | None:
        """Stop a queued or running run now. Returns the cancelled run, or
        None when it was already finished. The holder's next fenced write
        fails, which stops its pipeline."""
        now = self._now()
        with self._tx() as cx:
            cur = cx.one(
                "SELECT * FROM runs WHERE run_id = ? AND status IN ('queued','running')"
                + (" FOR UPDATE" if cx.pg else ""),
                (run_id,),
            )
            if cur is None:
                return None
            events = [self._event(e) for e in cx.all(
                "SELECT * FROM run_events WHERE run_id = ? ORDER BY seq", (run_id,))]
            r = cx.one(
                "UPDATE runs SET status = 'cancelled', finished_at = ?, lease_token = '', "
                "lease_until = NULL, secrets_json = '', response_text = ?, last_seq = last_seq + 1 "
                "WHERE run_id = ? AND status IN ('queued','running') RETURNING *",
                (self._ts(now), partial_text(events), run_id),
            )
            run = self._row(r)
            before = RunRow(**{**run.__dict__, "last_seq": run.last_seq - 1})
            final = [terminal_done_event(run, cancelled=True)]
            saved = self._insert_outbox(cx, run, pending=final)
            self._append_locked(cx, before, with_history_flag(final, saved))
        log.info("Run cancelled", run_id=run_id[:12], thread_id=run.thread_id[:12])
        return run

    def touch_watcher(self, run_id: str, watcher_id: str) -> None:
        with self._tx() as cx:
            cx.execute(
                "INSERT INTO run_watchers (run_id, watcher_id, last_seen) VALUES (?, ?, ?) "
                "ON CONFLICT (run_id, watcher_id) DO UPDATE SET last_seen = excluded.last_seen",
                (run_id, watcher_id, self._ts(self._now())),
            )

    def remove_watcher(self, run_id: str, watcher_id: str) -> None:
        with self._tx() as cx:
            cx.execute("DELETE FROM run_watchers WHERE run_id = ? AND watcher_id = ?",
                       (run_id, watcher_id))

    def is_watched(self, run_id: str, ttl_s: int | None = None) -> bool:
        with self._read() as cx:
            return self._watched(cx, run_id, ttl_s)

    def _watched(self, cx: _Cx, run_id: str, ttl_s: int | None = None) -> bool:
        from core import settings as S
        ttl_s = S.RUN_WATCHER_TTL_S if ttl_s is None else ttl_s
        r = cx.one(
            "SELECT 1 AS x FROM run_watchers WHERE run_id = ? AND last_seen >= ? LIMIT 1",
            (run_id, self._ts(self._now() - timedelta(seconds=ttl_s))),
        )
        return r is not None

    # -- runner liveness ----------------------------------------------------

    def worker_heartbeat(self, worker_id: str, *, concurrency: int, running: int,
                         version: str = "") -> None:
        with self._tx() as cx:
            cx.execute(
                "INSERT INTO run_workers (worker_id, last_seen, concurrency, running, version) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (worker_id) DO UPDATE SET "
                "last_seen = excluded.last_seen, concurrency = excluded.concurrency, "
                "running = excluded.running, version = excluded.version",
                (worker_id, self._ts(self._now()), int(concurrency), int(running), version),
            )

    def remove_worker(self, worker_id: str) -> None:
        with self._tx() as cx:
            cx.execute("DELETE FROM run_workers WHERE worker_id = ?", (worker_id,))

    def live_workers(self, stale_s: int | None = None) -> list[dict]:
        from core import settings as S
        stale_s = S.RUN_WORKER_STALE_S if stale_s is None else stale_s
        with self._read() as cx:
            rows = cx.all(
                "SELECT * FROM run_workers WHERE last_seen >= ? ORDER BY worker_id",
                (self._ts(self._now() - timedelta(seconds=stale_s)),),
            )
        return [{"worker_id": r["worker_id"], "last_seen": _parse_ts(r["last_seen"]).isoformat(),
                 "concurrency": int(r["concurrency"]), "running": int(r["running"]),
                 "version": r["version"] or ""} for r in rows]

    # -- outbox ---------------------------------------------------------------

    def _insert_outbox(self, cx: _Cx, run: RunRow, *, sources: list | None = None,
                       agents_used: list | None = None,
                       pending: list[dict] | None = None) -> bool:
        """The run-complete message for Node, inside the caller's transaction.
        `pending`: the run's final events, about to be appended. Returns True
        when Python saves the answer to the chat history itself (Node's
        store-history format), which the caller writes onto the `done` event
        (`with_history_flag`) so the app does not save it a second time."""
        stored = [self._event(e) for e in cx.all(
            "SELECT * FROM run_events WHERE run_id = ? AND type IN "
            "('sources','done','artifact_start','artifact_done') ORDER BY seq", (run.run_id,))]
        stored += [RunEvent(run.run_id, 0, str(ev.get("type") or ""), ev, None)
                   for ev in pending or []]
        if sources is None:
            sources = next((e.payload.get("data") or [] for e in reversed(stored)
                            if e.type == "sources"), [])
        if agents_used is None:
            agents_used = next((e.payload.get("agents_used") or [] for e in reversed(stored)
                                if e.type == "done"), [])
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        payload = {
            "run_id": run.run_id,
            "thread_id": run.thread_id,
            "user_id": run.user_id,
            "identity_source": run.identity_source,
            "turn": run.turn_number or 0,
            "status": run.status,
            "query": (run.request or {}).get("query", ""),
            "response": run.response_text,
            "sources": sources,
            "agents_used": agents_used,
            "token_usage": run.token_usage,
            "connected": self._watched(cx, run.run_id),
            "error": run.error,
            "started_at": iso(run.started_at),
            "finished_at": iso(run.finished_at),
            # What the app also saves with an answer: the draft panel's
            # reference and, for a regenerated answer, the original's id.
            "artifact": artifact_ref(stored),
            "parent_id": (run.request or {}).get("parent_id"),
        }
        from core import settings as S
        from core.run_outbox import plan_delivery
        status = "pending" if self._callback_enabled(run.status) else "skipped"
        if status == "pending":
            send, why = plan_delivery(payload)
            if not send:
                status = "skipped"
                log.info("Run-complete not sent to Node", run_id=run.run_id[:12], reason=why)
        now = self._ts(self._now())
        cx.execute(
            "INSERT INTO run_outbox (run_id, kind, payload_json, status, next_attempt_at, created_at) "
            "VALUES (?, 'run_complete', ?, ?, ?, ?) ON CONFLICT (run_id) DO NOTHING",
            (run.run_id, json.dumps(payload, ensure_ascii=False), status, now, now),
        )
        return status == "pending" and S.RUN_CALLBACK_FORMAT == "store_history"

    def claim_outbox(self, limit: int = 10, hold_s: int = 60) -> list[OutboxItem]:
        """Pending messages that are due. Each is held for `hold_s` (its
        next_attempt_at is pushed out) so a second dispatcher does not send
        it while this one is mid-request. If this process dies mid-send the
        row comes due again and is resent; Node dedupes on run_id."""
        now = self._now()
        with self._tx() as cx:
            rows = cx.all(
                "SELECT * FROM run_outbox WHERE status = 'pending' AND next_attempt_at <= ? "
                "ORDER BY next_attempt_at LIMIT ?" + self._lock(),
                (self._ts(now), int(limit)),
            )
            for r in rows:
                cx.execute("UPDATE run_outbox SET next_attempt_at = ? WHERE outbox_id = ?",
                           (self._ts(now + timedelta(seconds=hold_s)), r["outbox_id"]))
        return [OutboxItem(outbox_id=int(r["outbox_id"]), run_id=r["run_id"], kind=r["kind"],
                           payload=_loads(r["payload_json"], {}), attempts=int(r["attempts"]),
                           created_at=_parse_ts(r["created_at"]))
                for r in rows]

    def mark_outbox_sent(self, outbox_id: int) -> None:
        with self._tx() as cx:
            cx.execute("UPDATE run_outbox SET status = 'sent', sent_at = ?, attempts = attempts + 1, "
                       "last_error = '' WHERE outbox_id = ?", (self._ts(self._now()), outbox_id))

    def now(self) -> datetime:
        """The store's clock (injected in tests)."""
        return self._now()

    def mark_outbox_retry(self, outbox_id: int, error: str, next_attempt_at: datetime) -> None:
        with self._tx() as cx:
            cx.execute("UPDATE run_outbox SET attempts = attempts + 1, last_error = ?, "
                       "next_attempt_at = ? WHERE outbox_id = ? AND status = 'pending'",
                       (error[:500], self._ts(next_attempt_at), outbox_id))

    def mark_outbox_dead(self, outbox_id: int, error: str) -> None:
        with self._tx() as cx:
            cx.execute("UPDATE run_outbox SET status = 'dead', attempts = attempts + 1, "
                       "last_error = ? WHERE outbox_id = ?", (error[:500], outbox_id))

    def retry_outbox(self, outbox_id: int) -> bool:
        """Admin: put a dead (or pending) message back in the queue now."""
        with self._tx() as cx:
            r = cx.one("UPDATE run_outbox SET status = 'pending', next_attempt_at = ? "
                       "WHERE outbox_id = ? AND status IN ('dead','pending') RETURNING outbox_id",
                       (self._ts(self._now()), outbox_id))
        return r is not None

    def get_outbox(self, run_id: str) -> dict | None:
        with self._read() as cx:
            r = cx.one("SELECT * FROM run_outbox WHERE run_id = ?", (run_id,))
        if not r:
            return None
        return {"outbox_id": int(r["outbox_id"]), "status": r["status"],
                "attempts": int(r["attempts"]), "last_error": r["last_error"] or "",
                "payload": _loads(r["payload_json"], {}),
                "next_attempt_at": _parse_ts(r["next_attempt_at"])}

    # -- recovery -------------------------------------------------------------

    def sweep(
        self,
        turn_lookup: Callable[[str], dict | None] | None = None,
        *,
        queue_max_wait_s: int | None = None,
        event_retention_days: int | None = None,
        row_retention_days: int | None = None,
    ) -> SweepReport:
        """Recover runs whose holder disappeared, expire runs nobody picked
        up, and delete old rows. Safe to run from several processes at once.

        For a running run whose lease expired:
          * its turn is already in `messages` (looked up by run_id): the
            pipeline finished and only the finalize was lost -> done
          * nothing but progress was stored and attempts remain -> queued
            again (LangGraph does not resume a run; a retry is a fresh run
            on the same thread, as when a user asks again)
          * otherwise -> failed, keeping the text the user already saw
        """
        from core import settings as S
        queue_max_wait_s = S.RUN_QUEUE_MAX_WAIT_S if queue_max_wait_s is None else queue_max_wait_s
        event_days = S.RUN_EVENT_RETENTION_DAYS if event_retention_days is None else event_retention_days
        row_days = S.RUN_ROW_RETENTION_DAYS if row_retention_days is None else row_retention_days
        report = SweepReport()
        now = self._now()

        with self._read() as cx:
            expired = [r["run_id"] for r in cx.all(
                "SELECT run_id FROM runs WHERE status = 'running' AND lease_until < ? "
                "ORDER BY lease_until LIMIT 100", (self._ts(now),))]
            stale_queued = [r["run_id"] for r in cx.all(
                "SELECT run_id FROM runs WHERE status = 'queued' AND created_at < ? "
                "ORDER BY created_at LIMIT 100",
                (self._ts(now - timedelta(seconds=queue_max_wait_s)),))]

        for run_id in expired:
            saved = None
            if turn_lookup is not None:
                try:
                    saved = turn_lookup(run_id)
                except Exception as e:   # unknown: do not guess, try next sweep
                    log.warning("Sweep: turn lookup failed; run left for next sweep",
                                run_id=run_id[:12], error=str(e)[:200])
                    continue
            self._recover_expired(run_id, saved, report)

        for run_id in stale_queued:
            with self._tx() as cx:
                r = cx.one(
                    "SELECT * FROM runs WHERE run_id = ? AND status = 'queued' AND created_at < ?"
                    + self._lock(),
                    (run_id, self._ts(now - timedelta(seconds=queue_max_wait_s))),
                )
                if r is None:
                    continue
                self._fail_locked(cx, self._row(r), error="no worker",
                                  message=NO_WORKER_MESSAGE, events=[])
                report.expired_queued.append(run_id)
                log.error("Run expired in queue: no runner picked it up",
                          run_id=run_id[:12], waited_s=queue_max_wait_s)

        report.events_deleted, report.runs_deleted = self._retention(now, event_days, row_days)
        if report.changed:
            log.info("Sweep", requeued=len(report.requeued), failed=len(report.failed),
                     repaired=len(report.repaired), expired_queued=len(report.expired_queued),
                     events_deleted=report.events_deleted, runs_deleted=report.runs_deleted)
        return report

    def _recover_expired(self, run_id: str, saved: dict | None, report: SweepReport) -> None:
        now = self._now()
        with self._tx() as cx:
            r = cx.one(
                "SELECT * FROM runs WHERE run_id = ? AND status = 'running' AND lease_until < ?"
                + self._lock(),
                (run_id, self._ts(now)),
            )
            if r is None:      # renewed, finished or handled by another sweeper meanwhile
                return
            run = self._row(r)
            events = [self._event(e) for e in cx.all(
                "SELECT * FROM run_events WHERE run_id = ? ORDER BY seq", (run_id,))]

            if saved is not None:
                has_done = any(e.type == "done" for e in events)
                final = [] if has_done else [terminal_done_event(
                    run, conversation_turn=int(saved.get("turn_number") or 0), recovered=True)]
                r2 = cx.one(
                    "UPDATE runs SET status = 'done', finished_at = ?, response_text = ?, "
                    "turn_number = ?, secrets_json = '', lease_token = '', lease_until = NULL, "
                    "last_seq = last_seq + ? WHERE run_id = ? RETURNING *",
                    (self._ts(now), saved.get("ai_response") or partial_text(events),
                     int(saved.get("turn_number") or 0), len(final), run_id),
                )
                done = self._row(r2)
                saved_by_server = self._insert_outbox(cx, done, pending=final)
                for e in events:          # a done stored before finalize was lost
                    if e.type == "done":
                        cx.execute("UPDATE run_events SET payload_json = ? WHERE run_id = ? AND seq = ?",
                                   (json.dumps(with_history_flag([e.payload], saved_by_server)[0],
                                               ensure_ascii=False), run_id, e.seq))
                self._append_locked(cx, RunRow(**{**done.__dict__,
                                                  "last_seq": done.last_seq - len(final)}),
                                    with_history_flag(final, saved_by_server))
                report.repaired.append(run_id)
                log.warning("Sweep: run had saved its turn, finalize was lost; marked done",
                            run_id=run_id[:12], turn=done.turn_number)
                return

            if not run.has_output and run.attempts < run.max_attempts:
                status_ev = {"type": "status", "agent": "runner",
                             "message": "Resuming after an interruption..."}
                r2 = cx.one(
                    "UPDATE runs SET status = 'queued', lease_token = '', lease_until = NULL, "
                    "worker_id = '', last_seq = last_seq + 1 WHERE run_id = ? RETURNING *",
                    (run_id,),
                )
                q = self._row(r2)
                self._append_locked(cx, RunRow(**{**q.__dict__, "last_seq": q.last_seq - 1}),
                                    [status_ev])
                self._notify(cx, QUEUED_CHANNEL, run_id)
                report.requeued.append(run_id)
                log.warning("Sweep: runner lost before any output; run queued again",
                            run_id=run_id[:12], attempt=run.attempts, worker=run.worker_id)
                return

            self._fail_locked(cx, run, error="interrupted", message=INTERRUPTED_MESSAGE,
                              events=events)
            report.failed.append(run_id)
            log.error("Sweep: runner lost after output; run failed with its partial text",
                      run_id=run_id[:12], attempt=run.attempts, worker=run.worker_id)

    def _fail_locked(self, cx: _Cx, run: RunRow, *, error: str, message: str,
                     events: list[RunEvent]) -> RunRow:
        final = [{"type": "error", "data": message, "run_id": run.run_id},
                 terminal_done_event(run, failed=True)]
        r = cx.one(
            "UPDATE runs SET status = 'failed', finished_at = ?, error = ?, response_text = ?, "
            "secrets_json = '', lease_token = '', lease_until = NULL, last_seq = last_seq + ?, "
            "has_output = (has_output OR ?) WHERE run_id = ? RETURNING *",
            (self._ts(self._now()), error, partial_text(events), len(final), True, run.run_id),
        )
        failed = self._row(r)
        saved = self._insert_outbox(cx, failed, pending=final)
        self._append_locked(cx, RunRow(**{**failed.__dict__,
                                          "last_seq": failed.last_seq - len(final)}),
                            with_history_flag(final, saved))
        return failed

    def _retention(self, now: datetime, event_days: int, row_days: int) -> tuple[int, int]:
        with self._tx() as cx:
            cx.execute("UPDATE runs SET secrets_json = '' WHERE secrets_json <> '' "
                       "AND status IN ('done','failed','cancelled')")
            cx.execute("DELETE FROM run_watchers WHERE last_seen < ?",
                       (self._ts(now - timedelta(hours=1)),))
            cx.execute("DELETE FROM run_workers WHERE last_seen < ?",
                       (self._ts(now - timedelta(days=1)),))
            ev = cx.execute(
                "DELETE FROM run_events WHERE run_id IN (SELECT run_id FROM runs "
                "WHERE status IN ('done','failed','cancelled') AND finished_at < ?)",
                (self._ts(now - timedelta(days=event_days)),),
            ).rowcount
            rn = cx.execute(
                "DELETE FROM runs WHERE status IN ('done','failed','cancelled') AND finished_at < ?",
                (self._ts(now - timedelta(days=row_days)),),
            ).rowcount
        return max(ev or 0, 0), max(rn or 0, 0)


def _callback_enabled_from_settings(status: str) -> bool:
    from core import settings as S
    if not S.RUN_CALLBACK_URL:
        return False
    if status == "cancelled":
        return S.RUN_CALLBACK_SEND_CANCELLED
    return True


def make_run_store(**overrides) -> RunStore:
    """The store for this process: Postgres when POSTGRES_URL is set (as
    chat_store), else the local SQLite chat database."""
    from core import settings as S
    if overrides.get("postgres_url") or overrides.get("sqlite_path"):
        return RunStore(**overrides)
    if S.POSTGRES_URL:
        return RunStore(postgres_url=S.POSTGRES_URL, **overrides)
    return RunStore(sqlite_path=S.CHAT_HISTORY_DB_PATH, **overrides)


_store: RunStore | None = None
_store_lock = threading.Lock()


def get_run_store() -> RunStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = make_run_store()
    return _store
