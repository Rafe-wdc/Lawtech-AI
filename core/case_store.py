"""Case workspace store — PostgreSQL only.

Why Postgres only: production and dev both run Postgres (see
docs/case_workspace_foundation.md), and the ingestion queue (slice 3) claims
jobs with ``SELECT … FOR UPDATE SKIP LOCKED``, which SQLite does not have.
Tests run against a real Postgres named by ``TEST_POSTGRES_URL`` (a throwaway
schema per run) and skip locally when it is absent; with ``CI=true`` they fail.

Conventions copied from ``core/chat_store._PostgresChatHistoryStore``:
psycopg3 with a ``ConnectionPool``, ``dict_row``, sync ``_x_sync`` methods
wrapped by async ``x`` via ``asyncio.to_thread``, schema created in code.
What is new: the schema is **versioned** (``case_schema_version``) and
applied under an advisory lock, so several API and worker processes can
start at once without racing on DDL.

Logging rule for this module: ids, counts and codes only. Never a title,
client name, filename or any text a lawyer typed.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from core.logger import get_logger, short_err

log = get_logger("CaseStore")

# ---------------------------------------------------------------------------
# Schema, versioned. Append a new (version, [statements]) tuple to migrate.
# Statements must be idempotent where possible; the version row guards the rest.
# ---------------------------------------------------------------------------

_SCHEMA_V1: list[str] = [
    """CREATE TABLE IF NOT EXISTS cases (
        case_id        UUID PRIMARY KEY,
        owner_user_id  TEXT NOT NULL,
        title          TEXT NOT NULL,
        client_name    TEXT NOT NULL DEFAULT '',
        our_side       TEXT NOT NULL DEFAULT '',
        matter_type    TEXT NOT NULL DEFAULT '',
        stage          TEXT NOT NULL DEFAULT '',
        forum          TEXT NOT NULL DEFAULT '',
        case_number    TEXT NOT NULL DEFAULT '',
        cnr            TEXT NOT NULL DEFAULT '',
        objective      TEXT NOT NULL DEFAULT '',
        status         TEXT NOT NULL DEFAULT 'active',
        doc_count      INTEGER NOT NULL DEFAULT 0,
        bytes_total    BIGINT  NOT NULL DEFAULT 0,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        deleted_at     TIMESTAMPTZ
    )""",
    """CREATE INDEX IF NOT EXISTS idx_cases_owner_updated
         ON cases (owner_user_id, updated_at DESC) WHERE status <> 'deleted'""",
    """CREATE TABLE IF NOT EXISTS case_documents (
        doc_id          UUID PRIMARY KEY,
        case_id         UUID NOT NULL REFERENCES cases(case_id),
        owner_user_id   TEXT NOT NULL,
        filename        TEXT NOT NULL,
        ext             TEXT NOT NULL,
        declared_mime   TEXT NOT NULL DEFAULT '',
        sniffed_mime    TEXT NOT NULL DEFAULT '',
        size_bytes      BIGINT NOT NULL,
        sha256          TEXT,
        client_sha256   TEXT,
        s3_key          TEXT NOT NULL,
        s3_version_id   TEXT,
        sse             TEXT,
        source          TEXT NOT NULL DEFAULT 'lawyer',
        status          TEXT NOT NULL,
        error_code      TEXT,
        error_detail    TEXT,
        duplicate_of    UUID REFERENCES case_documents(doc_id),
        kind            TEXT NOT NULL DEFAULT '',
        kind_confidence REAL NOT NULL DEFAULT 0,
        page_count      INTEGER,
        chars_total     INTEGER,
        ocr_pages       INTEGER NOT NULL DEFAULT 0,
        extractor       TEXT,
        text_key        TEXT,
        meta_key        TEXT,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        ready_at        TIMESTAMPTZ,
        deleted_at      TIMESTAMPTZ
    )""",
    """CREATE INDEX IF NOT EXISTS idx_case_documents_case
         ON case_documents (case_id, created_at)""",
    """CREATE INDEX IF NOT EXISTS idx_case_documents_sha
         ON case_documents (case_id, sha256) WHERE sha256 IS NOT NULL""",
    """CREATE INDEX IF NOT EXISTS idx_case_documents_pending
         ON case_documents (status, created_at)
         WHERE status IN ('uploading','queued','processing')""",
    """CREATE TABLE IF NOT EXISTS case_jobs (
        job_id           UUID PRIMARY KEY,
        case_id          UUID NOT NULL REFERENCES cases(case_id),
        doc_id           UUID REFERENCES case_documents(doc_id),
        type             TEXT NOT NULL,
        status           TEXT NOT NULL,
        priority         SMALLINT NOT NULL DEFAULT 100,
        attempts         INTEGER NOT NULL DEFAULT 0,
        max_attempts     INTEGER NOT NULL DEFAULT 4,
        run_after        TIMESTAMPTZ NOT NULL DEFAULT now(),
        locked_by        TEXT,
        locked_at        TIMESTAMPTZ,
        lock_expires_at  TIMESTAMPTZ,
        last_error_code  TEXT,
        last_error       TEXT,
        history          JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at      TIMESTAMPTZ
    )""",
    """CREATE INDEX IF NOT EXISTS idx_case_jobs_claim
         ON case_jobs (priority, run_after) WHERE status IN ('queued','running')""",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_case_jobs_active_per_doc
         ON case_jobs (doc_id, type) WHERE status IN ('queued','running')""",
    """CREATE INDEX IF NOT EXISTS idx_case_jobs_case
         ON case_jobs (case_id, created_at DESC)""",
    """CREATE TABLE IF NOT EXISTS case_audit (
        audit_id    BIGSERIAL PRIMARY KEY,
        case_id     UUID NOT NULL,
        user_id     TEXT NOT NULL,
        action      TEXT NOT NULL,
        object_id   TEXT,
        request_id  TEXT,
        at          TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE INDEX IF NOT EXISTS idx_case_audit_case ON case_audit (case_id, at DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_case_audit_view_window
         ON case_audit (case_id, user_id, at DESC) WHERE action = 'case_view'""",
    """CREATE TABLE IF NOT EXISTS case_workers (
        worker_id     TEXT PRIMARY KEY,
        started_at    TIMESTAMPTZ NOT NULL,
        last_seen_at  TIMESTAMPTZ NOT NULL,
        jobs_done     INTEGER NOT NULL DEFAULT 0,
        jobs_failed   INTEGER NOT NULL DEFAULT 0
    )""",
]

MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, _SCHEMA_V1),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]

# Columns a client may set on a case (create or update). Everything else is server-owned.
CASE_EDITABLE = (
    "title", "client_name", "our_side", "matter_type", "stage",
    "forum", "case_number", "cnr", "objective",
)
CASE_COLUMNS = (
    "case_id", "owner_user_id", *CASE_EDITABLE, "status", "doc_count", "bytes_total",
    "created_at", "updated_at",
)

ACTION_CASE_CREATE = "case_create"
ACTION_CASE_UPDATE = "case_update"
ACTION_CASE_VIEW = "case_view"


class CaseStoreProtocol(Protocol):
    """What the routes need. ``tests/case_support.py`` has an in-memory fake."""

    async def create_case(self, owner_user_id: str, data: dict) -> dict: ...
    async def list_cases(self, owner_user_id: str, limit: int, offset: int) -> tuple[list[dict], int]: ...
    async def get_case(self, case_id: str, owner_user_id: str) -> Optional[dict]: ...
    async def update_case(self, case_id: str, owner_user_id: str, fields: dict) -> Optional[dict]: ...
    async def record_audit(self, case_id: str, user_id: str, action: str,
                           object_id: Optional[str] = None, request_id: Optional[str] = None) -> None: ...
    async def record_view(self, case_id: str, user_id: str, request_id: Optional[str],
                          window_sec: int) -> bool: ...


def _as_uuid(value: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _row_to_case(row: dict) -> dict:
    out = {k: row.get(k) for k in CASE_COLUMNS}
    out["case_id"] = str(out["case_id"])
    for k in ("created_at", "updated_at"):
        v = out.get(k)
        if isinstance(v, datetime):
            out[k] = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return out


class CaseStore:
    """PostgreSQL-backed store. One instance per process (see ``get_case_store``)."""

    def __init__(self, postgres_url: str, schema: Optional[str] = None,
                 min_size: int = 1, max_size: int = 8):
        if not postgres_url:
            raise RuntimeError("CaseStore requires POSTGRES_URL (the case workspace is Postgres-only)")
        self._url = postgres_url
        self._schema = schema          # tests: isolate in a throwaway schema
        self._min_size = min_size
        self._max_size = max_size
        self._pool = None
        self._pool_lock = threading.Lock()
        self._schema_ok = False
        self._schema_lock = threading.Lock()

    # ------------------------------------------------------------------ pool

    def _configure_conn(self, conn) -> None:
        from psycopg.rows import dict_row
        conn.row_factory = dict_row
        if self._schema:
            conn.execute(f'SET search_path TO "{self._schema}", public')
            conn.commit()

    def _get_pool(self):
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    from psycopg_pool import ConnectionPool
                    self._pool = ConnectionPool(
                        conninfo=self._url, min_size=self._min_size, max_size=self._max_size,
                        open=True, timeout=10.0, configure=self._configure_conn,
                    )
                    log.info("Case-store pool initialised", schema=self._schema or "public")
        return self._pool

    def close(self) -> None:
        if self._pool is not None:
            try:
                self._pool.close()
            except Exception as e:  # pragma: no cover - shutdown noise
                log.warning("Case-store pool close failed", error=short_err(e))
            self._pool = None

    # ---------------------------------------------------------------- schema

    def ensure_schema(self) -> None:
        """Apply any migration newer than the recorded version. Safe to call
        from many processes: a transaction-scoped advisory lock serialises it."""
        if self._schema_ok:
            return
        with self._schema_lock:
            if self._schema_ok:
                return
            with self._get_pool().connection() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(hashtext('lawtech.case_schema'))")
                conn.execute("""CREATE TABLE IF NOT EXISTS case_schema_version (
                                    version    INTEGER PRIMARY KEY,
                                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
                row = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM case_schema_version").fetchone()
                current = int(row["v"]) if row else 0
                applied = []
                for version, statements in MIGRATIONS:
                    if version <= current:
                        continue
                    for stmt in statements:
                        conn.execute(stmt)
                    conn.execute("INSERT INTO case_schema_version (version) VALUES (%s)", (version,))
                    applied.append(version)
                conn.commit()
            self._schema_ok = True
            if applied:
                log.info("Case schema migrated", applied=applied, now=SCHEMA_VERSION)

    def schema_version(self) -> int:
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            row = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM case_schema_version").fetchone()
            return int(row["v"]) if row else 0

    # ----------------------------------------------------------------- cases

    def _create_case_sync(self, owner_user_id: str, data: dict) -> dict:
        self.ensure_schema()
        case_id = uuid.uuid4()
        cols = ["case_id", "owner_user_id"] + [c for c in CASE_EDITABLE if c in data]
        vals: list[Any] = [case_id, owner_user_id] + [data[c] for c in CASE_EDITABLE if c in data]
        placeholders = ", ".join(["%s"] * len(cols))
        with self._get_pool().connection() as conn:
            row = conn.execute(
                f"INSERT INTO cases ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *",
                vals,
            ).fetchone()
            conn.commit()
        log.info("Case created", case=str(case_id)[:8], user=owner_user_id[:8])
        return _row_to_case(row)

    async def create_case(self, owner_user_id: str, data: dict) -> dict:
        return await asyncio.to_thread(self._create_case_sync, owner_user_id, data)

    def _list_cases_sync(self, owner_user_id: str, limit: int, offset: int) -> tuple[list[dict], int]:
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM cases WHERE owner_user_id = %s AND status <> 'deleted'",
                (owner_user_id,),
            ).fetchone()["n"]
            rows = conn.execute(
                """SELECT * FROM cases
                    WHERE owner_user_id = %s AND status <> 'deleted'
                    ORDER BY updated_at DESC, case_id
                    LIMIT %s OFFSET %s""",
                (owner_user_id, limit, offset),
            ).fetchall()
        return [_row_to_case(r) for r in rows], int(total)

    async def list_cases(self, owner_user_id: str, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        return await asyncio.to_thread(self._list_cases_sync, owner_user_id, limit, offset)

    def _get_case_sync(self, case_id: str, owner_user_id: str) -> Optional[dict]:
        cid = _as_uuid(case_id)
        if cid is None:
            return None
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM cases WHERE case_id = %s AND owner_user_id = %s AND status <> 'deleted'",
                (cid, owner_user_id),
            ).fetchone()
        return _row_to_case(row) if row else None

    async def get_case(self, case_id: str, owner_user_id: str) -> Optional[dict]:
        return await asyncio.to_thread(self._get_case_sync, case_id, owner_user_id)

    def _update_case_sync(self, case_id: str, owner_user_id: str, fields: dict) -> Optional[dict]:
        cid = _as_uuid(case_id)
        if cid is None:
            return None
        self.ensure_schema()
        allowed = {k: v for k, v in fields.items() if k in CASE_EDITABLE or k == "status"}
        if "status" in allowed and allowed["status"] not in ("active", "archived"):
            allowed.pop("status")
        if not allowed:
            return self._get_case_sync(case_id, owner_user_id)
        sets = ", ".join(f"{k} = %s" for k in allowed) + ", updated_at = now()"
        with self._get_pool().connection() as conn:
            row = conn.execute(
                f"""UPDATE cases SET {sets}
                     WHERE case_id = %s AND owner_user_id = %s AND status <> 'deleted'
                     RETURNING *""",
                [*allowed.values(), cid, owner_user_id],
            ).fetchone()
            conn.commit()
        if row:
            log.info("Case updated", case=str(cid)[:8], fields=sorted(allowed))
        return _row_to_case(row) if row else None

    async def update_case(self, case_id: str, owner_user_id: str, fields: dict) -> Optional[dict]:
        return await asyncio.to_thread(self._update_case_sync, case_id, owner_user_id, fields)

    # ----------------------------------------------------------------- audit

    def _record_audit_sync(self, case_id: str, user_id: str, action: str,
                           object_id: Optional[str], request_id: Optional[str]) -> None:
        cid = _as_uuid(case_id)
        if cid is None:
            return
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            conn.execute(
                """INSERT INTO case_audit (case_id, user_id, action, object_id, request_id)
                   VALUES (%s, %s, %s, %s, %s)""",
                (cid, user_id, action, object_id, request_id),
            )
            conn.commit()

    async def record_audit(self, case_id: str, user_id: str, action: str,
                           object_id: Optional[str] = None, request_id: Optional[str] = None) -> None:
        try:
            await asyncio.to_thread(self._record_audit_sync, case_id, user_id, action, object_id, request_id)
        except Exception as e:  # audit must never fail the request
            log.warning("Audit write failed", action=action, error=short_err(e))

    def _record_view_sync(self, case_id: str, user_id: str, request_id: Optional[str],
                          window_sec: int) -> bool:
        """Insert a case_view row unless one exists for (case, user) inside the window.
        Status polling therefore writes at most one row per window."""
        cid = _as_uuid(case_id)
        if cid is None:
            return False
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            row = conn.execute(
                """INSERT INTO case_audit (case_id, user_id, action, object_id, request_id)
                   SELECT %s, %s, 'case_view', NULL, %s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM case_audit
                         WHERE case_id = %s AND user_id = %s AND action = 'case_view'
                           AND at > now() - (%s * interval '1 second'))
                   RETURNING audit_id""",
                (cid, user_id, request_id, cid, user_id, int(window_sec)),
            ).fetchone()
            conn.commit()
        return row is not None

    async def record_view(self, case_id: str, user_id: str, request_id: Optional[str],
                          window_sec: int) -> bool:
        try:
            return await asyncio.to_thread(self._record_view_sync, case_id, user_id, request_id, window_sec)
        except Exception as e:
            log.warning("Audit view write failed", error=short_err(e))
            return False


# ---------------------------------------------------------------------------
# Process-wide instance (FastAPI dependency). Tests override the dependency.
# ---------------------------------------------------------------------------

_store: Optional[CaseStore] = None
_store_lock = threading.Lock()


def get_case_store() -> CaseStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                from core.settings import POSTGRES_URL
                _store = CaseStore(POSTGRES_URL)
    return _store
