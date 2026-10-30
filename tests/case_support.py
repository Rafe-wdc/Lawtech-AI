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
from core.case_s3 import HeadResult, content_disposition, get_case_s3
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
        self._clock = clock or time.time
        self._tick = 0

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


class FakeCaseS3:
    """In-memory stand-in for ``core.case_s3.CaseS3`` with the same methods.
    Tests play the browser with ``put_object`` (optionally without SSE)."""

    def __init__(self, bucket: str = "lawttorney-cases-test", sse: str = "AES256"):
        self.bucket = bucket
        self.sse = sse
        self.objects: dict[str, dict] = {}
        self.deleted: list[tuple[str, Optional[str]]] = []
        self.presigned_puts: list[tuple[str, str, int]] = []
        self.presigned_gets: list[tuple[str, str]] = []
        self._versions = 0

    def presign_put(self, key: str, content_type: str, expires_s: int) -> dict:
        self.presigned_puts.append((key, content_type, expires_s))
        return {"url": f"https://{self.bucket}.s3.test.local/{key}?X-Amz-Expires={expires_s}&X-Amz-Signature=fake",
                "method": "PUT", "headers": {"Content-Type": content_type},
                "expires_at": datetime.now(timezone.utc) + timedelta(seconds=expires_s)}

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream",
                   sse: Optional[str] = "AES256", etag: Optional[str] = None) -> str:
        """``etag`` may be forced so tests can stage an ETag collision (same
        ETag and size, different bytes), which real S3 cannot rule out."""
        self._versions += 1
        vid = f"v{self._versions}"
        self.objects[key] = {"data": bytes(data), "content_type": content_type, "sse": sse or "",
                             "etag": etag or hashlib.md5(bytes(data)).hexdigest(), "version_id": vid}
        return vid

    def head(self, key: str) -> HeadResult:
        o = self.objects.get(key)
        if not o:
            return HeadResult(False)
        return HeadResult(True, len(o["data"]), o["sse"], o["etag"], o["version_id"], o["content_type"])

    def read_range(self, key: str, start: int, end: int, version_id: Optional[str] = None) -> bytes:
        return self.objects[key]["data"][start:end + 1]

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
        self.objects.pop(key, None)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        return self.put_object(key, data, content_type, self.sse)


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
