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
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, Sequence

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

_SCHEMA_V2: list[str] = [
    # ETag from HEAD at complete. A HINT only (surfaced as `duplicate_hint`):
    # a document is marked `duplicate` solely by the worker, after it has
    # hashed the bytes and found a `ready` sibling with the same sha256.
    "ALTER TABLE case_documents ADD COLUMN IF NOT EXISTS s3_etag TEXT",
    """CREATE INDEX IF NOT EXISTS idx_case_documents_etag
         ON case_documents (case_id, s3_etag) WHERE s3_etag IS NOT NULL""",
    # `stored`: the object was accepted at complete; the row counts towards
    # doc_count / bytes_total / the caps and may be downloaded. Explicit rather
    # than derived from status, because `failed` has two shapes: rejected at
    # complete (object deleted, never stored) and failed at ingest (slice 3,
    # object retained, still stored).
    "ALTER TABLE case_documents ADD COLUMN IF NOT EXISTS stored BOOLEAN NOT NULL DEFAULT FALSE",
    """UPDATE case_documents SET stored = TRUE
        WHERE status IN ('queued','processing','ready','unsupported','duplicate')""",
]

_SCHEMA_V3: list[str] = [
    # Slice 3. purged_at: the purge job ran (objects gone, free text blanked).
    "ALTER TABLE cases ADD COLUMN IF NOT EXISTS purged_at TIMESTAMPTZ",
    "ALTER TABLE case_documents ADD COLUMN IF NOT EXISTS purged_at TIMESTAMPTZ",
    # Worker rows: a clean stop is distinguishable from a dead worker, and the
    # job a worker is on is visible from SQL.
    "ALTER TABLE case_workers ADD COLUMN IF NOT EXISTS stopped_at TIMESTAMPTZ",
    "ALTER TABLE case_workers ADD COLUMN IF NOT EXISTS current_job_id UUID",
]

MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, _SCHEMA_V1),
    (2, _SCHEMA_V2),
    (3, _SCHEMA_V3),
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
    # slice 2
    async def create_document(self, case_id: str, owner_user_id: str, doc_id: str, fields: dict,
                              max_docs: int, max_bytes: int) -> Optional[dict]: ...
    async def get_document(self, case_id: str, doc_id: str, owner_user_id: str) -> Optional[dict]: ...
    async def list_documents(self, case_id: str, owner_user_id: str, limit: int, offset: int,
                             status: Optional[str] = None) -> Optional[tuple[list[dict], int, dict]]: ...
    async def find_duplicate(self, case_id: str, *, etag: Optional[str] = None, size: Optional[int] = None,
                             sha256: Optional[str] = None, exclude_doc_id: Optional[str] = None) -> Optional[str]: ...
    async def finish_complete(self, case_id: str, doc_id: str, owner_user_id: str, *, outcome: str,
                              **kw) -> Optional[dict]: ...
    async def soft_delete_document(self, case_id: str, doc_id: str, owner_user_id: str,
                                   purge_after_days: int) -> Optional[dict]: ...
    async def soft_delete_case(self, case_id: str, owner_user_id: str, purge_after_days: int) -> Optional[dict]: ...
    async def case_status(self, case_id: str, owner_user_id: str) -> Optional[dict]: ...


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


# ---------------------------------------------------------------------------
# Slice 2: documents, soft delete, case status
# ---------------------------------------------------------------------------

DOC_UPLOADING = "uploading"
DOC_QUEUED = "queued"
DOC_PROCESSING = "processing"
DOC_READY = "ready"
DOC_FAILED = "failed"
DOC_DUPLICATE = "duplicate"
DOC_UNSUPPORTED = "unsupported"
DOC_EXPIRED = "expired"
DOC_DELETED = "deleted"

# Statuses a stored object passes through. Accounting (caps, counts, download)
# reads the explicit ``stored`` column, set when ``complete`` accepts the object;
# this tuple only says which complete outcomes set it.
DOC_STORED = (DOC_QUEUED, DOC_PROCESSING, DOC_READY, DOC_UNSUPPORTED, DOC_DUPLICATE)
# What the duplicate HINT may point at (client hash or ETag): never another
# duplicate, never a failed or deleted row. The authoritative match the worker
# uses is narrower: a ``ready`` sibling with the same server-computed sha256.
DOC_DEDUPE_TARGETS = (DOC_QUEUED, DOC_PROCESSING, DOC_READY, DOC_UNSUPPORTED)

DOC_COLUMNS = (
    "doc_id", "case_id", "filename", "ext", "declared_mime", "sniffed_mime", "size_bytes",
    "sha256", "sse", "status", "error_code", "duplicate_of", "kind", "kind_confidence",
    "page_count", "chars_total", "ocr_pages", "extractor", "text_key", "meta_key",
    "s3_key", "s3_version_id", "s3_etag", "stored", "created_at", "updated_at", "ready_at",
)

JOB_INGEST = "ingest"
JOB_PURGE = "purge"
JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"
JOB_COLUMNS = (
    "job_id", "case_id", "doc_id", "type", "status", "priority", "attempts", "max_attempts", "run_after",
    "locked_by", "locked_at", "lock_expires_at", "last_error_code", "last_error", "history",
    "created_at", "updated_at", "finished_at",
)

ACTION_CASE_DELETE = "case_delete"
ACTION_DOC_PRESIGN = "doc_presign"
ACTION_DOC_COMPLETE = "doc_complete"
ACTION_DOC_DOWNLOAD = "doc_download_url"
ACTION_DOC_DELETE = "doc_delete"
# written by the worker, user_id = SYSTEM_USER
ACTION_DOC_EXPIRE = "doc_expire"
ACTION_DOC_PURGE = "doc_purge"
ACTION_CASE_PURGE = "case_purge"
SYSTEM_USER = "system"


class CaseLimitError(Exception):
    """``create_document`` refused the upload: ``case_not_active`` |
    ``case_doc_cap`` | ``case_bytes_cap``."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _row_to_job(row: dict) -> dict:
    out = {k: row.get(k) for k in JOB_COLUMNS}
    for k in ("job_id", "case_id", "doc_id"):
        if out.get(k) is not None:
            out[k] = str(out[k])
    for k in ("run_after", "locked_at", "lock_expires_at", "created_at", "updated_at", "finished_at"):
        v = out.get(k)
        if isinstance(v, datetime) and not v.tzinfo:
            out[k] = v.replace(tzinfo=timezone.utc)
    out["history"] = list(out.get("history") or [])
    return out


def _row_to_doc(row: dict) -> dict:
    out = {k: row.get(k) for k in DOC_COLUMNS}
    for k in ("doc_id", "case_id", "duplicate_of"):
        if out.get(k) is not None:
            out[k] = str(out[k])
    out["stored"] = bool(out.get("stored"))
    for k in ("created_at", "updated_at", "ready_at"):
        v = out.get(k)
        if isinstance(v, datetime) and not v.tzinfo:
            out[k] = v.replace(tzinfo=timezone.utc)
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


    # ------------------------------------------------------------- documents

    def _create_document_sync(self, case_id: str, owner_user_id: str, doc_id: str, fields: dict,
                              max_docs: int, max_bytes: int) -> Optional[dict]:
        """Reserve a document row (status ``uploading``) under the case lock so
        the per-case caps hold under concurrent presigns."""
        cid, did = _as_uuid(case_id), _as_uuid(doc_id)
        if cid is None or did is None:
            return None
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            case = conn.execute(
                """SELECT status FROM cases
                    WHERE case_id = %s AND owner_user_id = %s AND status <> 'deleted' FOR UPDATE""",
                (cid, owner_user_id),
            ).fetchone()
            if case is None:
                conn.rollback()
                return None
            if case["status"] != "active":
                conn.rollback()
                raise CaseLimitError("case_not_active")
            # in-flight uploads hold a slot too, so a burst of presigns cannot pass the cap
            agg = conn.execute(
                """SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS b
                     FROM case_documents WHERE case_id = %s AND (status = %s OR stored)""",
                (cid, DOC_UPLOADING),
            ).fetchone()
            if int(agg["n"]) >= int(max_docs):
                conn.rollback()
                raise CaseLimitError("case_doc_cap")
            if int(agg["b"]) + int(fields["size_bytes"]) > int(max_bytes):
                conn.rollback()
                raise CaseLimitError("case_bytes_cap")
            row = conn.execute(
                """INSERT INTO case_documents
                       (doc_id, case_id, owner_user_id, filename, ext, declared_mime, size_bytes,
                        client_sha256, s3_key, source, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                (did, cid, owner_user_id, fields["filename"], fields["ext"],
                 fields.get("declared_mime", "") or "", int(fields["size_bytes"]),
                 fields.get("client_sha256") or None, fields["s3_key"],
                 fields.get("source", "lawyer") or "lawyer", DOC_UPLOADING),
            ).fetchone()
            conn.commit()
        log.info("Document presigned", case=str(cid)[:8], doc=str(did)[:8], bytes=int(fields["size_bytes"]))
        return _row_to_doc(row)

    async def create_document(self, case_id: str, owner_user_id: str, doc_id: str, fields: dict,
                              max_docs: int, max_bytes: int) -> Optional[dict]:
        return await asyncio.to_thread(self._create_document_sync, case_id, owner_user_id, doc_id,
                                       fields, max_docs, max_bytes)

    def _get_document_sync(self, case_id: str, doc_id: str, owner_user_id: str) -> Optional[dict]:
        cid, did = _as_uuid(case_id), _as_uuid(doc_id)
        if cid is None or did is None:
            return None
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            row = conn.execute(
                """SELECT * FROM case_documents
                    WHERE doc_id = %s AND case_id = %s AND owner_user_id = %s AND status <> 'deleted'""",
                (did, cid, owner_user_id),
            ).fetchone()
        return _row_to_doc(row) if row else None

    async def get_document(self, case_id: str, doc_id: str, owner_user_id: str) -> Optional[dict]:
        return await asyncio.to_thread(self._get_document_sync, case_id, doc_id, owner_user_id)

    def _list_documents_sync(self, case_id: str, owner_user_id: str, limit: int, offset: int,
                             status: Optional[str] = None) -> Optional[tuple[list[dict], int, dict]]:
        cid = _as_uuid(case_id)
        if cid is None:
            return None
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            owned = conn.execute(
                "SELECT 1 FROM cases WHERE case_id = %s AND owner_user_id = %s AND status <> 'deleted'",
                (cid, owner_user_id),
            ).fetchone()
            if owned is None:
                return None
            counts = {r["status"]: int(r["n"]) for r in conn.execute(
                """SELECT status, COUNT(*) AS n FROM case_documents
                    WHERE case_id = %s AND status <> 'deleted' GROUP BY status""", (cid,)).fetchall()}
            where = "case_id = %s AND owner_user_id = %s AND status <> 'deleted'"
            params: list[Any] = [cid, owner_user_id]
            if status:
                where += " AND status = %s"
                params.append(status)
            total = conn.execute(f"SELECT COUNT(*) AS n FROM case_documents WHERE {where}", params).fetchone()["n"]
            rows = conn.execute(
                f"SELECT * FROM case_documents WHERE {where} ORDER BY created_at, doc_id LIMIT %s OFFSET %s",
                [*params, int(limit), int(offset)],
            ).fetchall()
        return [_row_to_doc(r) for r in rows], int(total), counts

    async def list_documents(self, case_id: str, owner_user_id: str, limit: int = 100, offset: int = 0,
                             status: Optional[str] = None) -> Optional[tuple[list[dict], int, dict]]:
        return await asyncio.to_thread(self._list_documents_sync, case_id, owner_user_id, limit, offset, status)

    def _find_duplicate_sync(self, case_id: str, etag: Optional[str] = None, size: Optional[int] = None,
                             sha256: Optional[str] = None, exclude_doc_id: Optional[str] = None) -> Optional[str]:
        """Earliest candidate in the case with the same content HINT: S3's ETag
        plus size, or a SHA-256 (client-declared or worker-computed). Used for
        ``duplicate_hint`` only; the worker's authoritative check is
        ``find_ready_by_sha256`` (slice 3)."""
        cid = _as_uuid(case_id)
        if cid is None:
            return None
        self.ensure_schema()
        excl = _as_uuid(exclude_doc_id) if exclude_doc_id else None
        with self._get_pool().connection() as conn:
            if etag and size is not None:
                row = conn.execute(
                    """SELECT doc_id FROM case_documents
                        WHERE case_id = %s AND s3_etag = %s AND size_bytes = %s AND status = ANY(%s)
                          AND (%s::uuid IS NULL OR doc_id <> %s)
                        ORDER BY created_at LIMIT 1""",
                    (cid, etag, int(size), list(DOC_DEDUPE_TARGETS), excl, excl),
                ).fetchone()
            elif sha256:
                row = conn.execute(
                    """SELECT doc_id FROM case_documents
                        WHERE case_id = %s AND (sha256 = %s OR client_sha256 = %s) AND status = ANY(%s)
                          AND (%s::uuid IS NULL OR doc_id <> %s)
                        ORDER BY created_at LIMIT 1""",
                    (cid, sha256, sha256, list(DOC_DEDUPE_TARGETS), excl, excl),
                ).fetchone()
            else:
                row = None
        return str(row["doc_id"]) if row else None

    async def find_duplicate(self, case_id: str, *, etag: Optional[str] = None, size: Optional[int] = None,
                             sha256: Optional[str] = None, exclude_doc_id: Optional[str] = None) -> Optional[str]:
        return await asyncio.to_thread(self._find_duplicate_sync, case_id, etag, size, sha256, exclude_doc_id)

    def _finish_complete_sync(self, case_id: str, doc_id: str, owner_user_id: str, *, outcome: str,
                              sniffed_mime: str = "", sse: str = "", version_id: str = "", etag: str = "",
                              error_code: Optional[str] = None, duplicate_of: Optional[str] = None,
                              enqueue_job: bool = False, max_attempts: int = 4) -> Optional[dict]:
        """``uploading`` -> ``outcome`` in one transaction: ``stored`` and the
        counts when the object is accepted, and the ingest job when asked.
        Idempotent: a row that already left ``uploading`` is returned unchanged."""
        cid, did = _as_uuid(case_id), _as_uuid(doc_id)
        if cid is None or did is None:
            return None
        dup = _as_uuid(duplicate_of) if duplicate_of else None
        self.ensure_schema()
        already = False
        with self._get_pool().connection() as conn:
            row = conn.execute(
                """UPDATE case_documents
                      SET status = %s, stored = %s, sniffed_mime = %s, sse = NULLIF(%s, ''),
                          s3_version_id = NULLIF(%s, ''), s3_etag = NULLIF(%s, ''),
                          error_code = %s, duplicate_of = %s, updated_at = now()
                    WHERE doc_id = %s AND case_id = %s AND owner_user_id = %s AND status = %s
                RETURNING *""",
                (outcome, outcome in DOC_STORED, sniffed_mime or "", sse or "", version_id or "",
                 etag or "", error_code, dup, did, cid, owner_user_id, DOC_UPLOADING),
            ).fetchone()
            if row is None:
                conn.rollback()
                already = True
            else:
                if row["stored"]:
                    conn.execute(
                        """UPDATE cases SET doc_count = doc_count + 1, bytes_total = bytes_total + %s,
                                            updated_at = now() WHERE case_id = %s""",
                        (int(row["size_bytes"]), cid),
                    )
                if enqueue_job:
                    conn.execute(
                        """INSERT INTO case_jobs (job_id, case_id, doc_id, type, status, max_attempts)
                           VALUES (%s, %s, %s, %s, 'queued', %s)
                           ON CONFLICT (doc_id, type) WHERE status IN ('queued','running') DO NOTHING""",
                        (uuid.uuid4(), cid, did, JOB_INGEST, int(max_attempts)),
                    )
                conn.commit()
        if already:
            return self._get_document_sync(case_id, doc_id, owner_user_id)
        log.info("Document completed", case=str(cid)[:8], doc=str(did)[:8], outcome=outcome,
                 code=error_code or "", job=bool(enqueue_job))
        return _row_to_doc(row)

    async def finish_complete(self, case_id: str, doc_id: str, owner_user_id: str, *, outcome: str,
                              **kw) -> Optional[dict]:
        return await asyncio.to_thread(
            lambda: self._finish_complete_sync(case_id, doc_id, owner_user_id, outcome=outcome, **kw))

    # ----------------------------------------------------------- soft delete

    def _soft_delete_document_sync(self, case_id: str, doc_id: str, owner_user_id: str,
                                   purge_after_days: int) -> Optional[dict]:
        cid, did = _as_uuid(case_id), _as_uuid(doc_id)
        if cid is None or did is None:
            return None
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            row = conn.execute(
                """WITH old AS (
                       SELECT doc_id, status AS prev_status, size_bytes, stored FROM case_documents
                        WHERE doc_id = %s AND case_id = %s AND owner_user_id = %s AND status <> 'deleted'
                        FOR UPDATE)
                   UPDATE case_documents d
                      SET status = 'deleted', deleted_at = now(), updated_at = now()
                     FROM old WHERE d.doc_id = old.doc_id
                RETURNING d.doc_id, old.prev_status, old.size_bytes, old.stored""",
                (did, cid, owner_user_id),
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            cancelled = conn.execute(
                """UPDATE case_jobs SET status = 'cancelled', finished_at = now(), updated_at = now()
                    WHERE doc_id = %s AND status = 'queued' AND type <> %s""", (did, JOB_PURGE),
            ).rowcount
            if row["stored"]:
                conn.execute(
                    """UPDATE cases SET doc_count = GREATEST(doc_count - 1, 0),
                                        bytes_total = GREATEST(bytes_total - %s, 0), updated_at = now()
                        WHERE case_id = %s""",
                    (int(row["size_bytes"]), cid),
                )
            conn.execute(
                """INSERT INTO case_jobs (job_id, case_id, doc_id, type, status, priority, run_after)
                   VALUES (%s, %s, %s, %s, 'queued', 200, now() + %s * interval '1 day')""",
                (uuid.uuid4(), cid, did, JOB_PURGE, int(purge_after_days)),
            )
            conn.commit()
        log.info("Document deleted", case=str(cid)[:8], doc=str(did)[:8], jobs_cancelled=int(cancelled))
        return {"doc_id": str(did), "case_id": str(cid), "status": DOC_DELETED,
                "prev_status": row["prev_status"], "jobs_cancelled": int(cancelled)}

    async def soft_delete_document(self, case_id: str, doc_id: str, owner_user_id: str,
                                   purge_after_days: int) -> Optional[dict]:
        return await asyncio.to_thread(self._soft_delete_document_sync, case_id, doc_id, owner_user_id,
                                       purge_after_days)

    def _soft_delete_case_sync(self, case_id: str, owner_user_id: str, purge_after_days: int) -> Optional[dict]:
        cid = _as_uuid(case_id)
        if cid is None:
            return None
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            case = conn.execute(
                """SELECT case_id FROM cases
                    WHERE case_id = %s AND owner_user_id = %s AND status <> 'deleted' FOR UPDATE""",
                (cid, owner_user_id),
            ).fetchone()
            if case is None:
                conn.rollback()
                return None
            docs = conn.execute(
                """UPDATE case_documents SET status = 'deleted', deleted_at = now(), updated_at = now()
                    WHERE case_id = %s AND status <> 'deleted'""", (cid,),
            ).rowcount
            cancelled = conn.execute(
                """UPDATE case_jobs SET status = 'cancelled', finished_at = now(), updated_at = now()
                    WHERE case_id = %s AND status = 'queued' AND type <> %s""", (cid, JOB_PURGE),
            ).rowcount
            conn.execute(
                "UPDATE cases SET status = 'deleted', deleted_at = now(), updated_at = now() WHERE case_id = %s",
                (cid,),
            )
            conn.execute(
                """INSERT INTO case_jobs (job_id, case_id, doc_id, type, status, priority, run_after)
                   VALUES (%s, %s, NULL, %s, 'queued', 200, now() + %s * interval '1 day')""",
                (uuid.uuid4(), cid, JOB_PURGE, int(purge_after_days)),
            )
            conn.commit()
        log.info("Case deleted", case=str(cid)[:8], documents=int(docs), jobs_cancelled=int(cancelled))
        return {"case_id": str(cid), "status": "deleted", "documents": int(docs), "jobs_cancelled": int(cancelled)}

    async def soft_delete_case(self, case_id: str, owner_user_id: str, purge_after_days: int) -> Optional[dict]:
        return await asyncio.to_thread(self._soft_delete_case_sync, case_id, owner_user_id, purge_after_days)

    # ---------------------------------------------------------------- status

    def _case_status_sync(self, case_id: str, owner_user_id: str) -> Optional[dict]:
        cid = _as_uuid(case_id)
        if cid is None:
            return None
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            case = conn.execute(
                """SELECT case_id, status, doc_count, bytes_total, updated_at FROM cases
                    WHERE case_id = %s AND owner_user_id = %s AND status <> 'deleted'""",
                (cid, owner_user_id),
            ).fetchone()
            if case is None:
                return None
            counts = {r["status"]: int(r["n"]) for r in conn.execute(
                """SELECT status, COUNT(*) AS n FROM case_documents
                    WHERE case_id = %s AND status <> 'deleted' GROUP BY status""", (cid,)).fetchall()}
        updated = case["updated_at"]
        if isinstance(updated, datetime) and not updated.tzinfo:
            updated = updated.replace(tzinfo=timezone.utc)
        return {
            "case_id": str(cid), "status": case["status"], "doc_count": int(case["doc_count"]),
            "bytes_total": int(case["bytes_total"]), "updated_at": updated, "documents": counts,
            "pending": sum(counts.get(s, 0) for s in (DOC_UPLOADING, DOC_QUEUED, DOC_PROCESSING)),
        }

    async def case_status(self, case_id: str, owner_user_id: str) -> Optional[dict]:
        return await asyncio.to_thread(self._case_status_sync, case_id, owner_user_id)

    def list_jobs_sync(self, case_id: str, doc_id: Optional[str] = None) -> list[dict]:
        """Queue rows for a case (tests and the slice 3 worker tooling)."""
        cid = _as_uuid(case_id)
        if cid is None:
            return []
        self.ensure_schema()
        where, params = "case_id = %s", [cid]
        if doc_id:
            where += " AND doc_id = %s"
            params.append(_as_uuid(doc_id))
        with self._get_pool().connection() as conn:
            rows = conn.execute(
                f"""SELECT job_id, case_id, doc_id, type, status, priority, attempts, max_attempts, run_after
                      FROM case_jobs WHERE {where} ORDER BY created_at""", params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("job_id", "case_id", "doc_id"):
                if d.get(k) is not None:
                    d[k] = str(d[k])
            out.append(d)
        return out


    # =====================================================================
    # Slice 3: queue (claim / heartbeat / finish), worker-side document
    # transitions, housekeeping, worker rows, purge. All sync: the worker is
    # a plain process. Nothing here is owner-scoped; the worker trusts the
    # job row and re-checks ``doc.case_id == job.case_id``.
    # =====================================================================

    # ----------------------------------------------------------------- jobs

    def claim_job_sync(self, worker_id: str, lock_ttl_s: int,
                       types: Optional[Sequence[str]] = None) -> Optional[dict]:
        """Claim the next runnable job with ``FOR UPDATE SKIP LOCKED`` (the
        only place it is used). Runnable: ``queued`` with ``run_after``
        passed, or ``running`` with an expired lock (dead worker: reclaimed,
        and the lost attempt is written to ``history``). Every claim counts an
        attempt, so a crash loop ends in ``failed``."""
        self.ensure_schema()
        type_list = list(types) if types else None
        with self._get_pool().connection() as conn:
            row = conn.execute(
                """WITH next AS (
                       SELECT job_id FROM case_jobs
                        WHERE ((status = 'queued' AND run_after <= now())
                            OR (status = 'running' AND lock_expires_at < now()))
                          AND (%(types)s::text[] IS NULL OR type = ANY(%(types)s::text[]))
                        ORDER BY priority, run_after
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1)
                   UPDATE case_jobs j
                      SET status = 'running', locked_by = %(worker)s, locked_at = now(),
                          lock_expires_at = now() + %(ttl)s * interval '1 second',
                          attempts = j.attempts + 1, updated_at = now(),
                          history = CASE WHEN j.status = 'running' THEN
                              (CASE WHEN jsonb_array_length(j.history) >= 20 THEN j.history - 0 ELSE j.history END)
                              || jsonb_build_array(jsonb_build_object(
                                     'attempt', j.attempts, 'worker', j.locked_by,
                                     'started', j.locked_at, 'ended', now(),
                                     'outcome', 'lost', 'error_code', 'worker_lost'))
                              ELSE j.history END
                     FROM next WHERE j.job_id = next.job_id
                RETURNING j.*""",
                {"worker": worker_id, "ttl": int(lock_ttl_s), "types": type_list},
            ).fetchone()
            conn.commit()
        if row is None:
            return None
        job = _row_to_job(row)
        log.info("Job claimed", job=job["job_id"][:8], type=job["type"], case=job["case_id"][:8],
                 doc=(job["doc_id"] or "")[:8], attempt=job["attempts"], worker=worker_id)
        return job

    def heartbeat_job_sync(self, job_id: str, worker_id: str, lock_ttl_s: int) -> bool:
        """Extend the lock. ``False`` means the job is no longer this worker's
        (reclaimed after a missed heartbeat, or cancelled): stop working on it."""
        jid = _as_uuid(job_id)
        if jid is None:
            return False
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            n = conn.execute(
                """UPDATE case_jobs SET lock_expires_at = now() + %s * interval '1 second', updated_at = now()
                    WHERE job_id = %s AND locked_by = %s AND status = 'running'""",
                (int(lock_ttl_s), jid, worker_id),
            ).rowcount
            conn.commit()
        return n == 1

    def finish_job_sync(self, job_id: str, worker_id: str, outcome: str, *,
                        error_code: Optional[str] = None, error: Optional[str] = None,
                        retry_in_s: Optional[int] = None, started_at: Optional[datetime] = None) -> bool:
        """Close this worker's attempt. ``outcome``: ``done`` | ``failed`` |
        ``retry`` (back to ``queued`` after ``retry_in_s``) | ``cancelled`` |
        ``release`` (graceful shutdown: back to ``queued`` now, attempt not
        counted). Guarded by ``locked_by``: a late finish from a worker whose
        lock was reclaimed changes nothing and returns ``False``."""
        jid = _as_uuid(job_id)
        if jid is None or outcome not in (JOB_DONE, JOB_FAILED, "retry", JOB_CANCELLED, "release"):
            return False
        self.ensure_schema()
        status = {JOB_DONE: JOB_DONE, JOB_FAILED: JOB_FAILED, "retry": JOB_QUEUED,
                  JOB_CANCELLED: JOB_CANCELLED, "release": JOB_QUEUED}[outcome]
        finished = status in (JOB_DONE, JOB_FAILED, JOB_CANCELLED)
        err_text = (error or "")[:200] or None
        entry = {"worker": worker_id, "started": started_at.isoformat() if started_at else None,
                 "outcome": outcome, "error_code": error_code}
        with self._get_pool().connection() as conn:
            n = conn.execute(
                """UPDATE case_jobs
                      SET status = %(status)s,
                          finished_at = CASE WHEN %(finished)s THEN now() ELSE NULL END,
                          run_after = CASE WHEN %(status)s = 'queued'
                                           THEN now() + %(retry)s * interval '1 second' ELSE run_after END,
                          attempts = CASE WHEN %(outcome)s = 'release' THEN GREATEST(attempts - 1, 0)
                                          ELSE attempts END,
                          locked_by = NULL, locked_at = NULL, lock_expires_at = NULL,
                          last_error_code = %(code)s, last_error = %(err)s, updated_at = now(),
                          history = (CASE WHEN jsonb_array_length(history) >= 20 THEN history - 0 ELSE history END)
                                    || jsonb_build_array(%(entry)s::jsonb
                                         || jsonb_build_object('attempt', attempts, 'ended', now()))
                    WHERE job_id = %(jid)s AND locked_by = %(worker)s AND status = 'running'""",
                {"status": status, "finished": finished, "retry": int(retry_in_s or 0), "outcome": outcome,
                 "code": error_code, "err": err_text, "entry": json.dumps(entry), "jid": jid, "worker": worker_id},
            ).rowcount
            conn.commit()
        if n == 1:
            log.info("Job finished", job=str(jid)[:8], outcome=outcome, code=error_code or "",
                     retry_in_s=int(retry_in_s or 0) if outcome == "retry" else 0)
        else:
            log.warning("Job finish ignored: lock no longer held", job=str(jid)[:8], outcome=outcome,
                        worker=worker_id)
        return n == 1

    def get_job_sync(self, job_id: str) -> Optional[dict]:
        jid = _as_uuid(job_id)
        if jid is None:
            return None
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            row = conn.execute("SELECT * FROM case_jobs WHERE job_id = %s", (jid,)).fetchone()
        return _row_to_job(row) if row else None

    # ---------------------------------------------- documents, worker side

    def get_document_for_job_sync(self, doc_id: str, case_id: str) -> Optional[dict]:
        """The row by ids, whatever its status (the worker decides what a
        ``deleted`` or already-``ready`` row means for its job)."""
        cid, did = _as_uuid(case_id), _as_uuid(doc_id)
        if cid is None or did is None:
            return None
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            row = conn.execute("SELECT * FROM case_documents WHERE doc_id = %s AND case_id = %s",
                               (did, cid)).fetchone()
        return _row_to_doc(row) if row else None

    def mark_processing_sync(self, doc_id: str, case_id: str) -> bool:
        """``queued`` (or ``processing`` after a reclaim) -> ``processing``.
        ``False`` when the row moved on (deleted, failed, ready): skip the job."""
        cid, did = _as_uuid(case_id), _as_uuid(doc_id)
        if cid is None or did is None:
            return False
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            n = conn.execute(
                """UPDATE case_documents SET status = %s, updated_at = now()
                    WHERE doc_id = %s AND case_id = %s AND status IN (%s, %s)""",
                (DOC_PROCESSING, did, cid, DOC_QUEUED, DOC_PROCESSING),
            ).rowcount
            conn.commit()
        return n == 1

    def find_ready_by_sha256_sync(self, case_id: str, sha256: str, exclude_doc_id: str) -> Optional[str]:
        """The authoritative duplicate check: a ``ready`` sibling in the same
        case whose worker-computed sha256 equals ours. Client hashes and
        ETags never count here."""
        cid, excl = _as_uuid(case_id), _as_uuid(exclude_doc_id)
        if cid is None or not sha256:
            return None
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            row = conn.execute(
                """SELECT doc_id FROM case_documents
                    WHERE case_id = %s AND sha256 = %s AND status = %s AND doc_id <> %s
                    ORDER BY ready_at, created_at LIMIT 1""",
                (cid, sha256.lower(), DOC_READY, excl),
            ).fetchone()
        return str(row["doc_id"]) if row else None

    def finish_document_sync(self, doc_id: str, case_id: str, *, status: str, sha256: Optional[str] = None,
                             error_code: Optional[str] = None, error_detail: Optional[str] = None,
                             duplicate_of: Optional[str] = None, page_count: Optional[int] = None,
                             chars_total: Optional[int] = None, ocr_pages: int = 0,
                             extractor: Optional[str] = None, text_key: Optional[str] = None,
                             meta_key: Optional[str] = None, kind: str = "",
                             kind_confidence: float = 0.0) -> Optional[dict]:
        """``processing`` -> ``ready`` | ``duplicate`` | ``failed`` | ``queued``
        (transient error, will be retried). Only from ``processing``, so a
        document deleted mid-job is never resurrected."""
        cid, did = _as_uuid(case_id), _as_uuid(doc_id)
        if cid is None or did is None or status not in (DOC_READY, DOC_DUPLICATE, DOC_FAILED, DOC_QUEUED):
            return None
        dup = _as_uuid(duplicate_of) if duplicate_of else None
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            row = conn.execute(
                """UPDATE case_documents
                      SET status = %s, sha256 = COALESCE(%s, sha256), error_code = %s, error_detail = %s,
                          duplicate_of = %s, page_count = %s, chars_total = %s, ocr_pages = %s,
                          extractor = %s, text_key = %s, meta_key = %s,
                          kind = %s, kind_confidence = %s,
                          ready_at = CASE WHEN %s = 'ready' THEN now() ELSE ready_at END,
                          updated_at = now()
                    WHERE doc_id = %s AND case_id = %s AND status = %s
                RETURNING *""",
                (status, (sha256 or "").lower() or None, error_code, (error_detail or "")[:200] or None,
                 dup, page_count, chars_total, int(ocr_pages or 0), extractor, text_key, meta_key,
                 kind or "", float(kind_confidence or 0), status, did, cid, DOC_PROCESSING),
            ).fetchone()
            conn.commit()
        if row is None:
            return None
        log.info("Document finished", case=str(cid)[:8], doc=str(did)[:8], status=status,
                 code=error_code or "", pages=page_count, chars=chars_total, ocr_pages=int(ocr_pages or 0))
        return _row_to_doc(row)

    # ---------------------------------------------------------- housekeeping

    def expire_stale_uploads_sync(self, older_than_hours: int) -> list[dict]:
        """``uploading`` rows older than the window -> ``expired`` (they were
        never counted). Returns their keys so the caller can delete any
        object a browser PUT but never completed."""
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            rows = conn.execute(
                """UPDATE case_documents SET status = %s, updated_at = now()
                    WHERE status = %s AND created_at < now() - %s * interval '1 hour'
                RETURNING doc_id, case_id, s3_key""",
                (DOC_EXPIRED, DOC_UPLOADING, int(older_than_hours)),
            ).fetchall()
            for r in rows:
                conn.execute(
                    "INSERT INTO case_audit (case_id, user_id, action, object_id) VALUES (%s, %s, %s, %s)",
                    (r["case_id"], SYSTEM_USER, ACTION_DOC_EXPIRE, str(r["doc_id"])),
                )
            conn.commit()
        if rows:
            log.info("Stale uploads expired", count=len(rows), older_than_hours=int(older_than_hours))
        return [{"doc_id": str(r["doc_id"]), "case_id": str(r["case_id"]), "s3_key": r["s3_key"]} for r in rows]

    # --------------------------------------------------------------- workers

    def worker_heartbeat_sync(self, worker_id: str, started_at: datetime, current_job_id: Optional[str] = None,
                              done_delta: int = 0, failed_delta: int = 0) -> None:
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            conn.execute(
                """INSERT INTO case_workers (worker_id, started_at, last_seen_at, jobs_done, jobs_failed,
                                             current_job_id)
                   VALUES (%s, %s, now(), %s, %s, %s)
                   ON CONFLICT (worker_id) DO UPDATE
                      SET last_seen_at = now(), stopped_at = NULL,
                          jobs_done = case_workers.jobs_done + EXCLUDED.jobs_done,
                          jobs_failed = case_workers.jobs_failed + EXCLUDED.jobs_failed,
                          current_job_id = EXCLUDED.current_job_id""",
                (worker_id, started_at, int(done_delta), int(failed_delta), _as_uuid(current_job_id or "")),
            )
            conn.commit()

    def worker_stopped_sync(self, worker_id: str) -> None:
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            conn.execute(
                """UPDATE case_workers SET stopped_at = now(), last_seen_at = now(), current_job_id = NULL
                    WHERE worker_id = %s""", (worker_id,))
            conn.commit()

    def prune_workers_sync(self, older_than_days: int = 7) -> int:
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            n = conn.execute("DELETE FROM case_workers WHERE last_seen_at < now() - %s * interval '1 day'",
                             (int(older_than_days),)).rowcount
            conn.commit()
        return int(n)

    def queue_summary_sync(self, stale_after_s: int = 180) -> dict:
        """For ``/pyapi/health``: live workers, age of the newest heartbeat,
        queue depth and how long the oldest runnable job has waited."""
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            w = conn.execute(
                """SELECT COUNT(*) FILTER (WHERE stopped_at IS NULL
                                             AND last_seen_at > now() - %s * interval '1 second') AS live,
                          EXTRACT(EPOCH FROM (now() - MAX(last_seen_at) FILTER (WHERE stopped_at IS NULL))) AS age
                     FROM case_workers""", (int(stale_after_s),)).fetchone()
            j = conn.execute(
                """SELECT COUNT(*) FILTER (WHERE status = 'queued') AS queued,
                          COUNT(*) FILTER (WHERE status = 'running') AS running,
                          EXTRACT(EPOCH FROM (now() - MIN(run_after) FILTER (WHERE status = 'queued'
                                                                             AND run_after <= now()))) AS oldest
                     FROM case_jobs WHERE status IN ('queued', 'running')""").fetchone()
        return {
            "workers_live": int(w["live"] or 0),
            "last_seen_age_s": round(float(w["age"]), 1) if w["age"] is not None else None,
            "queued": int(j["queued"] or 0), "running": int(j["running"] or 0),
            "oldest_queued_s": round(float(j["oldest"]), 1) if j["oldest"] is not None else None,
        }

    # ----------------------------------------------------------------- purge

    def purge_target_sync(self, case_id: str, doc_id: Optional[str]) -> Optional[dict]:
        """What a purge job may touch. ``None`` unless the row is ``deleted``:
        a purge never runs against a live case or document."""
        cid = _as_uuid(case_id)
        if cid is None:
            return None
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            if doc_id:
                did = _as_uuid(doc_id)
                row = conn.execute(
                    """SELECT doc_id, case_id, status, s3_key, purged_at FROM case_documents
                        WHERE doc_id = %s AND case_id = %s""", (did, cid)).fetchone()
                if row is None or row["status"] != DOC_DELETED:
                    return None
                return {"case_id": str(cid), "doc_id": str(row["doc_id"]), "s3_key": row["s3_key"],
                        "purged_at": row["purged_at"]}
            row = conn.execute("SELECT status, purged_at FROM cases WHERE case_id = %s", (cid,)).fetchone()
            if row is None or row["status"] != DOC_DELETED:
                return None
            return {"case_id": str(cid), "doc_id": None, "s3_key": None, "purged_at": row["purged_at"]}

    def purge_document_row_sync(self, doc_id: str, case_id: str, versions_deleted: int = 0) -> bool:
        """Blank the one free-text field of a tombstoned document row."""
        cid, did = _as_uuid(case_id), _as_uuid(doc_id)
        if cid is None or did is None:
            return False
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            n = conn.execute(
                """UPDATE case_documents SET filename = '', purged_at = now(), updated_at = now()
                    WHERE doc_id = %s AND case_id = %s AND status = %s""", (did, cid, DOC_DELETED)).rowcount
            if n:
                conn.execute(
                    "INSERT INTO case_audit (case_id, user_id, action, object_id) VALUES (%s, %s, %s, %s)",
                    (cid, SYSTEM_USER, ACTION_DOC_PURGE, f"{did}:{int(versions_deleted)}"))
            conn.commit()
        return n == 1

    def purge_case_rows_sync(self, case_id: str, versions_deleted: int = 0) -> dict:
        """Blank title, client_name, case_number, cnr and objective on the
        tombstoned case and the filename on every tombstoned document."""
        cid = _as_uuid(case_id)
        if cid is None:
            return {"case": 0, "documents": 0}
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            c = conn.execute(
                """UPDATE cases SET title = '', client_name = '', case_number = '', cnr = '', objective = '',
                                    purged_at = now(), updated_at = now()
                    WHERE case_id = %s AND status = %s""", (cid, DOC_DELETED)).rowcount
            d = 0
            if c:
                d = conn.execute(
                    """UPDATE case_documents SET filename = '', purged_at = now(), updated_at = now()
                        WHERE case_id = %s AND status = %s""", (cid, DOC_DELETED)).rowcount
                conn.execute(
                    "INSERT INTO case_audit (case_id, user_id, action, object_id) VALUES (%s, %s, %s, %s)",
                    (cid, SYSTEM_USER, ACTION_CASE_PURGE, f"{cid}:{int(versions_deleted)}"))
            conn.commit()
        return {"case": int(c), "documents": int(d)}

    def case_document_keys_sync(self, case_id: str) -> list[str]:
        """Raw keys of every document row of a case (any status), for a
        case-level purge that must not rely on a listing alone."""
        cid = _as_uuid(case_id)
        if cid is None:
            return []
        self.ensure_schema()
        with self._get_pool().connection() as conn:
            rows = conn.execute("SELECT s3_key FROM case_documents WHERE case_id = %s", (cid,)).fetchall()
        return [r["s3_key"] for r in rows if r["s3_key"]]


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
