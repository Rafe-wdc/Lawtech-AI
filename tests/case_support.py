"""Shared helpers for the case-workspace test suites.

``FakeCaseStore`` mirrors the owner-scoping semantics of ``core.case_store``
and ``FakeCaseS3`` the five S3 operations of ``core.case_s3``, so the route
tests run anywhere (no database, no AWS). The real SQL is covered by
``tests/test_case_store_pg.py`` against Postgres and the real boto3 parameter
construction by ``tests/test_case_s3.py`` with ``botocore.stub``.
"""
from __future__ import annotations

import hashlib
import time
import urllib.parse
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import settings as _settings
from core.auth import require_user_key
from core.case_routes import build_case_router
from core.case_s3 import HeadResult, content_disposition, get_case_s3, purge_prefix_ok
from core.case_store import (
    CASE_EDITABLE, DOC_COLUMNS, DOC_DEDUPE_TARGETS, DOC_STORED, DOC_UPLOADING,
    JOB_INGEST, JOB_PURGE, CaseLimitError, get_case_store,
)
from core.jwt_auth import mint_hs256

TEST_SECRET = "case-test-secret-not-for-prod"


def mint(user_id: str, secret: str = TEST_SECRET, ttl: int = 600, **extra) -> str:
    now = int(time.time())
    payload = {"userId": user_id, "iat": now, "exp": now + ttl}
    payload.update(extra)
    return mint_hs256(payload, secret)


def auth_headers(user_id: str, **kw) -> dict:
    return {"Authorization": f"Bearer {mint(user_id, **kw)}", "X-API-Key": "test-key"}


class FakeCaseStore:
    """In-memory CaseStoreProtocol with the same owner-scoped behaviour."""

    def __init__(self, clock=None):
        self.cases: dict[str, dict] = {}
        self.documents: dict[str, dict] = {}
        self.jobs: list[dict] = []
        self.audit: list[dict] = []
        self.workers: dict[str, dict] = {}
        self._clock = clock or time.time
        self._tick = 0

    # the worker calls these on the real store at startup / for health
    def ensure_schema(self) -> None:
        return None

    def schema_version(self) -> int:
        from core.case_store import SCHEMA_VERSION
        return SCHEMA_VERSION

    def _now(self) -> datetime:
        # strictly increasing so ORDER BY created_at is deterministic even with a frozen clock
        self._tick += 1
        return datetime.fromtimestamp(self._clock(), tz=timezone.utc) + timedelta(microseconds=self._tick)

    def _case(self, case_id: str, owner_user_id: str) -> Optional[dict]:
        c = self.cases.get(case_id)
        if not c or c["owner_user_id"] != owner_user_id or c["status"] == "deleted":
            return None
        return c

    # ---- cases ---------------------------------------------------------------

    async def create_case(self, owner_user_id: str, data: dict) -> dict:
        now = self._now()
        case = {
            "case_id": str(uuid.uuid4()), "owner_user_id": owner_user_id,
            **{c: "" for c in CASE_EDITABLE},
            **{k: v for k, v in data.items() if k in CASE_EDITABLE},
            "status": "active", "doc_count": 0, "bytes_total": 0,
            "created_at": now, "updated_at": now,
        }
        self.cases[case["case_id"]] = case
        return dict(case)

    async def list_cases(self, owner_user_id: str, limit: int = 50, offset: int = 0):
        rows = [c for c in self.cases.values()
                if c["owner_user_id"] == owner_user_id and c["status"] != "deleted"]
        rows.sort(key=lambda c: c["updated_at"], reverse=True)
        return [dict(r) for r in rows[offset:offset + limit]], len(rows)

    async def get_case(self, case_id: str, owner_user_id: str) -> Optional[dict]:
        c = self._case(case_id, owner_user_id)
        return dict(c) if c else None

    async def update_case(self, case_id: str, owner_user_id: str, fields: dict) -> Optional[dict]:
        c = self._case(case_id, owner_user_id)
        if not c:
            return None
        for k, v in fields.items():
            if k in CASE_EDITABLE or (k == "status" and v in ("active", "archived")):
                c[k] = v
        if fields:
            c["updated_at"] = self._now()
        return dict(c)

    # ---- audit ---------------------------------------------------------------

    async def record_audit(self, case_id, user_id, action, object_id=None, request_id=None) -> None:
        self.audit.append({"case_id": case_id, "user_id": user_id, "action": action,
                           "object_id": object_id, "request_id": request_id, "at": self._clock()})

    async def record_view(self, case_id, user_id, request_id, window_sec: int) -> bool:
        now = self._clock()
        for row in self.audit:
            if (row["case_id"] == case_id and row["user_id"] == user_id
                    and row["action"] == "case_view" and row["at"] > now - window_sec):
                return False
        await self.record_audit(case_id, user_id, "case_view", None, request_id)
        return True

    # ---- documents -----------------------------------------------------------

    @staticmethod
    def _doc_out(d: dict) -> dict:
        return {k: d.get(k) for k in DOC_COLUMNS}

    def _doc(self, case_id: str, doc_id: str, owner_user_id: str) -> Optional[dict]:
        d = self.documents.get(doc_id)
        if (not d or d["case_id"] != case_id or d["owner_user_id"] != owner_user_id
                or d["status"] == "deleted"):
            return None
        return d

    def _case_docs(self, case_id: str) -> list[dict]:
        return sorted((d for d in self.documents.values() if d["case_id"] == case_id and d["status"] != "deleted"),
                      key=lambda d: (d["created_at"], d["doc_id"]))

    async def create_document(self, case_id, owner_user_id, doc_id, fields, max_docs, max_bytes):
        c = self._case(case_id, owner_user_id)
        if not c:
            return None
        if c["status"] != "active":
            raise CaseLimitError("case_not_active")
        reserved = [d for d in self.documents.values()
                    if d["case_id"] == case_id and (d["status"] == DOC_UPLOADING or d["stored"])]
        if len(reserved) >= max_docs:
            raise CaseLimitError("case_doc_cap")
        if sum(d["size_bytes"] for d in reserved) + fields["size_bytes"] > max_bytes:
            raise CaseLimitError("case_bytes_cap")
        now = self._now()
        d = {k: None for k in DOC_COLUMNS}
        d.update({
            "doc_id": doc_id, "case_id": case_id, "owner_user_id": owner_user_id,
            "filename": fields["filename"], "ext": fields["ext"],
            "declared_mime": fields.get("declared_mime", "") or "", "sniffed_mime": "",
            "size_bytes": int(fields["size_bytes"]), "client_sha256": fields.get("client_sha256"),
            "s3_key": fields["s3_key"], "source": fields.get("source", "lawyer"), "status": DOC_UPLOADING,
            "stored": False, "kind": "", "kind_confidence": 0, "ocr_pages": 0,
            "created_at": now, "updated_at": now,
        })
        self.documents[doc_id] = d
        return self._doc_out(d)

    async def get_document(self, case_id, doc_id, owner_user_id):
        d = self._doc(case_id, doc_id, owner_user_id)
        return self._doc_out(d) if d else None

    async def list_documents(self, case_id, owner_user_id, limit=100, offset=0, status=None):
        if not self._case(case_id, owner_user_id):
            return None
        docs = self._case_docs(case_id)
        counts = dict(Counter(d["status"] for d in docs))
        if status:
            docs = [d for d in docs if d["status"] == status]
        return [self._doc_out(d) for d in docs[offset:offset + limit]], len(docs), counts

    async def find_duplicate(self, case_id, *, etag=None, size=None, sha256=None, exclude_doc_id=None):
        for d in self._case_docs(case_id):
            if d["status"] not in DOC_DEDUPE_TARGETS or d["doc_id"] == exclude_doc_id:
                continue
            if etag and size is not None:
                if d.get("s3_etag") == etag and d["size_bytes"] == size:
                    return d["doc_id"]
            elif sha256 and (d.get("sha256") == sha256 or d.get("client_sha256") == sha256):
                return d["doc_id"]
        return None

    async def finish_complete(self, case_id, doc_id, owner_user_id, *, outcome, sniffed_mime="", sse="",
                              version_id="", etag="", error_code=None, duplicate_of=None,
                              enqueue_job=False, max_attempts=4):
        d = self._doc(case_id, doc_id, owner_user_id)
        if not d:
            return None
        if d["status"] != DOC_UPLOADING:
            return self._doc_out(d)
        d.update(status=outcome, stored=outcome in DOC_STORED, sniffed_mime=sniffed_mime or "",
                 sse=sse or None, s3_version_id=version_id or None, s3_etag=etag or None,
                 error_code=error_code, duplicate_of=duplicate_of, updated_at=self._now())
        if d["stored"]:
            c = self.cases[case_id]
            c["doc_count"] += 1
            c["bytes_total"] += d["size_bytes"]
            c["updated_at"] = self._now()
        if enqueue_job and not any(j["doc_id"] == doc_id and j["type"] == JOB_INGEST
                                   and j["status"] in ("queued", "running") for j in self.jobs):
            self.jobs.append({"job_id": str(uuid.uuid4()), "case_id": case_id, "doc_id": doc_id,
                              "type": JOB_INGEST, "status": "queued", "priority": 100, "attempts": 0,
                              "max_attempts": max_attempts, "run_after": self._now()})
        return self._doc_out(d)

    async def soft_delete_document(self, case_id, doc_id, owner_user_id, purge_after_days):
        d = self._doc(case_id, doc_id, owner_user_id)
        if not d:
            return None
        prev = d["status"]
        d["status"] = "deleted"
        d["deleted_at"] = d["updated_at"] = self._now()
        cancelled = 0
        for j in self.jobs:
            if j["doc_id"] == doc_id and j["status"] == "queued" and j["type"] != JOB_PURGE:
                j["status"] = "cancelled"
                cancelled += 1
        if d["stored"]:
            c = self.cases[case_id]
            c["doc_count"] = max(0, c["doc_count"] - 1)
            c["bytes_total"] = max(0, c["bytes_total"] - d["size_bytes"])
        self.jobs.append({"job_id": str(uuid.uuid4()), "case_id": case_id, "doc_id": doc_id, "type": JOB_PURGE,
                          "status": "queued", "priority": 200, "attempts": 0, "max_attempts": 4,
                          "run_after": self._now() + timedelta(days=purge_after_days)})
        return {"doc_id": doc_id, "case_id": case_id, "status": "deleted", "prev_status": prev,
                "jobs_cancelled": cancelled}

    async def soft_delete_case(self, case_id, owner_user_id, purge_after_days):
        c = self._case(case_id, owner_user_id)
        if not c:
            return None
        docs = 0
        for d in self.documents.values():
            if d["case_id"] == case_id and d["status"] != "deleted":
                d["status"] = "deleted"
                d["deleted_at"] = self._now()
                docs += 1
        cancelled = 0
        for j in self.jobs:
            if j["case_id"] == case_id and j["status"] == "queued" and j["type"] != JOB_PURGE:
                j["status"] = "cancelled"
                cancelled += 1
        c["status"] = "deleted"
        c["deleted_at"] = c["updated_at"] = self._now()
        self.jobs.append({"job_id": str(uuid.uuid4()), "case_id": case_id, "doc_id": None, "type": JOB_PURGE,
                          "status": "queued", "priority": 200, "attempts": 0, "max_attempts": 4,
                          "run_after": self._now() + timedelta(days=purge_after_days)})
        return {"case_id": case_id, "status": "deleted", "documents": docs, "jobs_cancelled": cancelled}

    async def case_status(self, case_id, owner_user_id):
        c = self._case(case_id, owner_user_id)
        if not c:
            return None
        counts = dict(Counter(d["status"] for d in self._case_docs(case_id)))
        return {"case_id": case_id, "status": c["status"], "doc_count": c["doc_count"],
                "bytes_total": c["bytes_total"], "updated_at": c["updated_at"], "documents": counts,
                "pending": sum(counts.get(s, 0) for s in ("uploading", "queued", "processing"))}

    def jobs_for(self, doc_id=None, case_id=None, type=None) -> list[dict]:
        return [j for j in self.jobs
                if (doc_id is None or j["doc_id"] == doc_id)
                and (case_id is None or j["case_id"] == case_id)
                and (type is None or j["type"] == type)]

    # ---- slice 3: queue, worker-side transitions, housekeeping, workers, purge

    def _job(self, job_id):
        return next((j for j in self.jobs if j["job_id"] == job_id), None)

    def _runnable(self, j, now):
        return ((j["status"] == "queued" and j["run_after"] <= now)
                or (j["status"] == "running" and j.get("lock_expires_at") and j["lock_expires_at"] < now))

    def claim_job_sync(self, worker_id, lock_ttl_s, types=None):
        now = self._now()
        cands = [j for j in self.jobs if self._runnable(j, now) and (not types or j["type"] in types)]
        if not cands:
            return None
        j = min(cands, key=lambda j: (j["priority"], j["run_after"]))
        j.setdefault("history", [])
        if j["status"] == "running":                                   # reclaim of a dead worker's job
            j["history"].append({"attempt": j["attempts"], "worker": j.get("locked_by"),
                                 "outcome": "lost", "error_code": "worker_lost"})
        j.update(status="running", locked_by=worker_id, locked_at=now,
                 lock_expires_at=now + timedelta(seconds=lock_ttl_s), attempts=j["attempts"] + 1, updated_at=now)
        for k in ("last_error_code", "last_error", "finished_at"):
            j.setdefault(k, None)
        return dict(j)

    def heartbeat_job_sync(self, job_id, worker_id, lock_ttl_s):
        j = self._job(job_id)
        if not j or j["status"] != "running" or j.get("locked_by") != worker_id:
            return False
        j["lock_expires_at"] = self._now() + timedelta(seconds=lock_ttl_s)
        return True

    def finish_job_sync(self, job_id, worker_id, outcome, *, error_code=None, error=None,
                        retry_in_s=None, started_at=None):
        j = self._job(job_id)
        if not j or j["status"] != "running" or j.get("locked_by") != worker_id:
            return False
        status = {"done": "done", "failed": "failed", "retry": "queued",
                  "cancelled": "cancelled", "release": "queued"}[outcome]
        now = self._now()
        j.setdefault("history", []).append({"attempt": j["attempts"], "worker": worker_id,
                                            "outcome": outcome, "error_code": error_code})
        if outcome == "release":
            j["attempts"] = max(0, j["attempts"] - 1)
        j.update(status=status, locked_by=None, locked_at=None, lock_expires_at=None,
                 last_error_code=error_code, last_error=(error or "")[:200] or None, updated_at=now,
                 finished_at=now if status in ("done", "failed", "cancelled") else None)
        if status == "queued":
            j["run_after"] = now + timedelta(seconds=retry_in_s or 0)
        return True

    def get_job_sync(self, job_id):
        j = self._job(job_id)
        return dict(j) if j else None

    def get_document_for_job_sync(self, doc_id, case_id):
        d = self.documents.get(doc_id)
        return self._doc_out(d) if d and d["case_id"] == case_id else None

    def mark_processing_sync(self, doc_id, case_id):
        d = self.documents.get(doc_id)
        if not d or d["case_id"] != case_id or d["status"] not in ("queued", "processing"):
            return False
        d["status"], d["updated_at"] = "processing", self._now()
        return True

    def find_ready_by_sha256_sync(self, case_id, sha256, exclude_doc_id):
        for d in self._case_docs(case_id):
            if d["status"] == "ready" and d.get("sha256") == sha256 and d["doc_id"] != exclude_doc_id:
                return d["doc_id"]
        return None

    def finish_document_sync(self, doc_id, case_id, *, status, sha256=None, error_code=None, error_detail=None,
                             duplicate_of=None, page_count=None, chars_total=None, ocr_pages=0, extractor=None,
                             text_key=None, meta_key=None, kind="", kind_confidence=0.0):
        d = self.documents.get(doc_id)
        if not d or d["case_id"] != case_id or d["status"] != "processing":
            return None
        now = self._now()
        d.update(status=status, sha256=sha256 or d.get("sha256"), error_code=error_code,
                 error_detail=(error_detail or "")[:200] or None, duplicate_of=duplicate_of,
                 page_count=page_count, chars_total=chars_total, ocr_pages=int(ocr_pages or 0),
                 extractor=extractor, text_key=text_key, meta_key=meta_key, kind=kind or "",
                 kind_confidence=float(kind_confidence or 0), updated_at=now)
        if status == "ready":
            d["ready_at"] = now
        return self._doc_out(d)

    def expire_stale_uploads_sync(self, older_than_hours):
        cutoff = self._now() - timedelta(hours=older_than_hours)
        out = []
        for d in self.documents.values():
            if d["status"] == "uploading" and d["created_at"] < cutoff:
                d["status"], d["updated_at"] = "expired", self._now()
                out.append({"doc_id": d["doc_id"], "case_id": d["case_id"], "s3_key": d["s3_key"]})
                self.audit.append({"case_id": d["case_id"], "user_id": "system", "action": "doc_expire",
                                   "object_id": d["doc_id"], "request_id": None, "at": self._clock()})
        return out

    def worker_heartbeat_sync(self, worker_id, started_at, current_job_id=None, done_delta=0, failed_delta=0):
        w = self.workers.setdefault(worker_id, {"worker_id": worker_id, "started_at": started_at,
                                                "jobs_done": 0, "jobs_failed": 0, "stopped_at": None})
        w.update(last_seen_at=self._now(), current_job_id=current_job_id, stopped_at=None)
        w["jobs_done"] += int(done_delta)
        w["jobs_failed"] += int(failed_delta)

    def worker_stopped_sync(self, worker_id):
        w = self.workers.get(worker_id)
        if w:
            w["stopped_at"], w["current_job_id"] = self._now(), None

    def prune_workers_sync(self, older_than_days=7):
        cutoff = self._now() - timedelta(days=older_than_days)
        gone = [k for k, w in self.workers.items() if w["last_seen_at"] < cutoff]
        for k in gone:
            del self.workers[k]
        return len(gone)

    def queue_summary_sync(self, stale_after_s=180):
        now = self._now()
        active = [w for w in self.workers.values() if w["stopped_at"] is None]
        live = [w for w in active if w["last_seen_at"] > now - timedelta(seconds=stale_after_s)]
        queued = [j for j in self.jobs if j["status"] == "queued"]
        runnable = [j for j in queued if j["run_after"] <= now]
        return {
            "workers_live": len(live),
            "last_seen_age_s": round((now - max(w["last_seen_at"] for w in active)).total_seconds(), 1) if active else None,
            "queued": len(queued), "running": sum(1 for j in self.jobs if j["status"] == "running"),
            "oldest_queued_s": round((now - min(j["run_after"] for j in runnable)).total_seconds(), 1) if runnable else None,
        }

    def purge_target_sync(self, case_id, doc_id):
        if doc_id:
            d = self.documents.get(doc_id)
            if not d or d["case_id"] != case_id or d["status"] != "deleted":
                return None
            return {"case_id": case_id, "doc_id": doc_id, "s3_key": d["s3_key"], "purged_at": d.get("purged_at")}
        c = self.cases.get(case_id)
        if not c or c["status"] != "deleted":
            return None
        return {"case_id": case_id, "doc_id": None, "s3_key": None, "purged_at": c.get("purged_at")}

    def purge_document_row_sync(self, doc_id, case_id, versions_deleted=0):
        d = self.documents.get(doc_id)
        if not d or d["case_id"] != case_id or d["status"] != "deleted":
            return False
        d["filename"], d["purged_at"] = "", self._now()
        self.audit.append({"case_id": case_id, "user_id": "system", "action": "doc_purge",
                           "object_id": f"{doc_id}:{versions_deleted}", "request_id": None, "at": self._clock()})
        return True

    def purge_case_rows_sync(self, case_id, versions_deleted=0):
        c = self.cases.get(case_id)
        if not c or c["status"] != "deleted":
            return {"case": 0, "documents": 0}
        for k in ("title", "client_name", "case_number", "cnr", "objective"):
            c[k] = ""
        c["purged_at"] = self._now()
        n = 0
        for d in self.documents.values():
            if d["case_id"] == case_id and d["status"] == "deleted":
                d["filename"], d["purged_at"] = "", self._now()
                n += 1
        self.audit.append({"case_id": case_id, "user_id": "system", "action": "case_purge",
                           "object_id": f"{case_id}:{versions_deleted}", "request_id": None, "at": self._clock()})
        return {"case": 1, "documents": n}

    def case_document_keys_sync(self, case_id):
        return [d["s3_key"] for d in self.documents.values() if d["case_id"] == case_id]


class FakeCaseS3:
    """In-memory stand-in for ``core.case_s3.CaseS3`` with the same methods,
    including a version history so purge tests can prove every version is
    gone. Tests play the browser with ``put_object`` (optionally without SSE,
    optionally with a forced ETag)."""

    def __init__(self, bucket: str = "lawttorney-cases-test", sse: str = "AES256"):
        self.bucket = bucket
        self.sse = sse
        self.objects: dict[str, dict] = {}            # latest live version per key
        self.versions: dict[str, list[dict]] = {}     # every version ever written, oldest first
        self.deleted: list[tuple[str, Optional[str]]] = []
        self.presigned_puts: list[tuple[str, str, int]] = []
        self.presigned_gets: list[tuple[str, str]] = []
        self._versions = 0

    def _next_vid(self) -> str:
        self._versions += 1
        return f"v{self._versions}"

    def presign_put(self, key: str, content_type: str, expires_s: int) -> dict:
        self.presigned_puts.append((key, content_type, expires_s))
        return {"url": f"https://{self.bucket}.s3.test.local/{key}?X-Amz-Expires={expires_s}&X-Amz-Signature=fake",
                "method": "PUT", "headers": {"Content-Type": content_type},
                "expires_at": datetime.now(timezone.utc) + timedelta(seconds=expires_s)}

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream",
                   sse: Optional[str] = "AES256", etag: Optional[str] = None) -> str:
        """``etag`` may be forced so tests can stage an ETag collision (same
        ETag and size, different bytes), which real S3 cannot rule out."""
        obj = {"key": key, "data": bytes(data), "content_type": content_type, "sse": sse or "",
               "etag": etag or hashlib.md5(bytes(data)).hexdigest(), "version_id": self._next_vid(),
               "is_delete_marker": False}
        self.objects[key] = obj
        self.versions.setdefault(key, []).append(obj)
        return obj["version_id"]

    def head(self, key: str) -> HeadResult:
        o = self.objects.get(key)
        if not o:
            return HeadResult(False)
        return HeadResult(True, len(o["data"]), o["sse"], o["etag"], o["version_id"], o["content_type"])

    def _version(self, key: str, version_id: Optional[str]) -> dict:
        if version_id:
            for v in self.versions.get(key, []):
                if v["version_id"] == version_id and not v["is_delete_marker"]:
                    return v
            raise _no_such_key()
        o = self.objects.get(key)
        if not o:
            raise _no_such_key()
        return o

    def read_range(self, key: str, start: int, end: int, version_id: Optional[str] = None) -> bytes:
        return self._version(key, version_id)["data"][start:end + 1]

    def get_bytes(self, key: str, version_id: Optional[str] = None) -> bytes:
        return bytes(self._version(key, version_id)["data"])

    def download_to_file(self, key: str, path: str, version_id: Optional[str] = None,
                         chunk_size: int = 1 << 20) -> tuple[int, str]:
        data = self.get_bytes(key, version_id)
        with open(path, "wb") as f:
            f.write(data)
        return len(data), hashlib.sha256(data).hexdigest()

    def presign_get(self, key: str, expires_s: int, filename: str, content_type: str = "",
                    version_id: Optional[str] = None) -> str:
        self.presigned_gets.append((key, filename))
        q = {"response-content-disposition": content_disposition(filename), "X-Amz-Expires": str(expires_s),
             "X-Amz-Signature": "fake"}
        if content_type:
            q["response-content-type"] = content_type
        if version_id:
            q["versionId"] = version_id
        return f"https://{self.bucket}.s3.test.local/{key}?{urllib.parse.urlencode(q)}"

    def delete(self, key: str, version_id: Optional[str] = None) -> None:
        self.deleted.append((key, version_id))
        if version_id:
            vs = [v for v in self.versions.get(key, []) if v["version_id"] != version_id]
            self.versions[key] = vs
            cur = self.objects.get(key)
            if cur and cur["version_id"] == version_id:
                self.objects.pop(key, None)
                if vs and not vs[-1]["is_delete_marker"]:
                    self.objects[key] = vs[-1]
        else:                                                          # versioned bucket: a delete marker
            self.versions.setdefault(key, []).append(
                {"key": key, "data": b"", "version_id": self._next_vid(), "is_delete_marker": True})
            self.objects.pop(key, None)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        return self.put_object(key, data, content_type, self.sse)

    def list_versions(self, prefix: str) -> list[dict]:
        return [{"key": v["key"], "version_id": v["version_id"], "is_delete_marker": v["is_delete_marker"]}
                for k, vs in self.versions.items() if k.startswith(prefix) for v in vs]

    def delete_all_versions(self, prefix: str) -> int:
        if not purge_prefix_ok(prefix):
            raise ValueError("refusing to purge outside a case prefix")
        n = 0
        for k in [k for k in self.versions if k.startswith(prefix)]:
            n += len(self.versions.pop(k))
            self.objects.pop(k, None)
            self.deleted.append((k, "*"))
        return n

    def live_keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix))


def _no_such_key():
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."},
                        "ResponseMetadata": {"HTTPStatusCode": 404}}, "GetObject")


# ---- fixtures for the extraction and worker suites ----------------------------

def make_pdf(pages: list[str]) -> bytes:
    """A born-digital PDF with one text page per entry ("" makes an empty page)."""
    import fitz
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def make_docx(paragraphs: list[str]) -> bytes:
    import io
    import docx
    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    import io
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def hanging_extract(path: str, ext: str, doc_id: str) -> dict:
    """Extraction entry for timeout / abort tests: never returns in time."""
    import time as _t
    _t.sleep(120)
    return {"lines": [], "extractor": "hang", "page_count": None, "ocr_pages": 0, "chars_total": 0}


def failing_extract(path: str, ext: str, doc_id: str) -> dict:
    raise RuntimeError("extractor exploded")


def slow_extract(path: str, ext: str, doc_id: str) -> dict:
    """About three seconds, then a valid one-line result (lost-lock test)."""
    import time as _t
    _t.sleep(3)
    return {"lines": [{"unit": "page", "page": 1, "index": 0, "text": "slow", "source": "layer",
                       "confidence": None, "chars": 4, "heading": None}],
            "extractor": "pdf_text", "page_count": 1, "ocr_pages": 0, "chars_total": 4}


def enable_feature(monkeypatch, secret: str = TEST_SECRET, enabled: bool = True) -> None:
    monkeypatch.setattr(_settings, "CASE_WORKSPACE_ENABLED", enabled, raising=False)
    monkeypatch.setattr(_settings, "CASE_JWT_SECRET", secret, raising=False)
    monkeypatch.setattr(_settings, "CASE_JWT_CLAIM", "userId", raising=False)
    monkeypatch.setattr(_settings, "CASE_JWT_LEEWAY_S", 30, raising=False)
    monkeypatch.setattr(_settings, "CASE_AUDIT_VIEW_WINDOW_SEC", 900, raising=False)
    monkeypatch.setattr(_settings, "CASE_S3_SSE", "AES256", raising=False)
    monkeypatch.setattr(_settings, "CASE_MAX_FILE_MB", 1024, raising=False)
    monkeypatch.setattr(_settings, "CASE_MAX_DOCS_PER_CASE", 500, raising=False)
    monkeypatch.setattr(_settings, "CASE_MAX_BYTES_PER_CASE_GB", 5, raising=False)
    monkeypatch.setattr(_settings, "CASE_PRESIGN_TTL_SEC", 900, raising=False)
    monkeypatch.setattr(_settings, "CASE_DOWNLOAD_TTL_SEC", 300, raising=False)
    monkeypatch.setattr(_settings, "CASE_PURGE_GRACE_DAYS", 7, raising=False)
    monkeypatch.setattr(_settings, "CASE_WORKER_STALE_SEC", 180, raising=False)


def make_client(store, s3: Optional[FakeCaseS3] = None, *, mount: bool = True) -> TestClient:
    """A minimal app with only the case router, no agent graph, no gateway."""
    app = FastAPI()
    if mount:
        app.include_router(build_case_router())
    # The service-key layer is existing gateway behaviour; keep tests independent
    # of whatever API_KEYS the local .env carries.
    app.dependency_overrides[require_user_key] = lambda: "test-key"
    app.dependency_overrides[get_case_store] = lambda: store
    s3 = s3 if s3 is not None else FakeCaseS3()
    app.dependency_overrides[get_case_s3] = lambda: s3
    return TestClient(app)
