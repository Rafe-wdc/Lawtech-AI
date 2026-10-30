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

_SCHEMA_V2: list[str] = [
    # ETag from HEAD at complete. S3 computes it, so (etag, size) is a trustworthy
    # same-content signal for immediate dedupe before the worker has a sha256.
    "ALTER TABLE case_documents ADD COLUMN IF NOT EXISTS s3_etag TEXT",
    """CREATE INDEX IF NOT EXISTS idx_case_documents_etag
         ON case_documents (case_id, s3_etag) WHERE s3_etag IS NOT NULL""",
]

MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, _SCHEMA_V1),
    (2, _SCHEMA_V2),
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

# The object is in S3; the row counts towards doc_count / bytes_total; download allowed.
DOC_STORED = (DOC_QUEUED, DOC_PROCESSING, DOC_READY, DOC_UNSUPPORTED, DOC_DUPLICATE)
# Holds a slot against the per-case caps (in-flight uploads included).
DOC_RESERVED = (DOC_UPLOADING, *DOC_STORED)
# What an incoming object can be a duplicate of (never another duplicate).
DOC_DEDUPE_TARGETS = (DOC_QUEUED, DOC_PROCESSING, DOC_READY, DOC_UNSUPPORTED)

DOC_COLUMNS = (
    "doc_id", "case_id", "filename", "ext", "declared_mime", "sniffed_mime", "size_bytes",
    "sha256", "sse", "status", "error_code", "duplicate_of", "kind", "kind_confidence",
    "page_count", "chars_total", "ocr_pages", "extractor", "s3_key", "s3_version_id", "s3_etag",
    "created_at", "updated_at", "ready_at",
)

JOB_INGEST = "ingest"
JOB_PURGE = "purge"

ACTION_CASE_DELETE = "case_delete"
ACTION_DOC_PRESIGN = "doc_presign"
ACTION_DOC_COMPLETE = "doc_complete"
ACTION_DOC_DOWNLOAD = "doc_download_url"
ACTION_DOC_DELETE = "doc_delete"


class CaseLimitError(Exception):
    """``create_document`` refused the upload: ``case_not_active`` |
    ``case_doc_cap`` | ``case_bytes_cap``."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _row_to_doc(row: dict) -> dict:
    out = {k: row.get(k) for k in DOC_COLUMNS}
    for k in ("doc_id", "case_id", "duplicate_of"):
        if out.get(k) is not None:
            out[k] = str(out[k])
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
            agg = conn.execute(
                """SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS b
                     FROM case_documents WHERE case_id = %s AND status = ANY(%s)""",
                (cid, list(DOC_RESERVED)),
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
        """Earliest stored document in the case with the same content signal:
        S3's ETag plus size (trustworthy, server-computed) or a SHA-256."""
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
        """``uploading`` -> ``outcome`` in one transaction: counts, and the
        ingest job when asked. Idempotent: a row that already left
        ``uploading`` is returned unchanged."""
        cid, did = _as_uuid(case_id), _as_uuid(doc_id)
        if cid is None or did is None:
            return None
        dup = _as_uuid(duplicate_of) if duplicate_of else None
        self.ensure_schema()
        already = False
        with self._get_pool().connection() as conn:
            row = conn.execute(
                """UPDATE case_documents
                      SET status = %s, sniffed_mime = %s, sse = NULLIF(%s, ''),
                          s3_version_id = NULLIF(%s, ''), s3_etag = NULLIF(%s, ''),
                          error_code = %s, duplicate_of = %s, updated_at = now()
                    WHERE doc_id = %s AND case_id = %s AND owner_user_id = %s AND status = %s
                RETURNING *""",
                (outcome, sniffed_mime or "", sse or "", version_id or "", etag or "",
                 error_code, dup, did, cid, owner_user_id, DOC_UPLOADING),
            ).fetchone()
            if row is None:
                conn.rollback()
                already = True
            else:
                if outcome in DOC_STORED:
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
                       SELECT doc_id, status AS prev_status, size_bytes FROM case_documents
                        WHERE doc_id = %s AND case_id = %s AND owner_user_id = %s AND status <> 'deleted'
                        FOR UPDATE)
                   UPDATE case_documents d
                      SET status = 'deleted', deleted_at = now(), updated_at = now()
                     FROM old WHERE d.doc_id = old.doc_id
                RETURNING d.doc_id, old.prev_status, old.size_bytes""",
                (did, cid, owner_user_id),
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            cancelled = conn.execute(
                """UPDATE case_jobs SET status = 'cancelled', finished_at = now(), updated_at = now()
                    WHERE doc_id = %s AND status = 'queued' AND type <> %s""", (did, JOB_PURGE),
            ).rowcount
            if row["prev_status"] in DOC_STORED:
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
