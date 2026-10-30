"""Case workspace HTTP API. Slice 1: cases. Slice 2: documents, status, soft delete.

Mounted by ``core/gateway.py`` only when ``CASE_WORKSPACE_ENABLED`` is on, so
with the flag off every path under ``/pyapi/cases`` is a plain 404.

Every route requires the service API key (``require_user_key``, like the rest
of the gateway) **and** the lawyer's JWT (``resolve_case_user``). Every store
call is scoped by ``owner_user_id``; a case or document that belongs to
someone else is indistinguishable from one that does not exist (404).

Uploads never pass through this process: ``presign`` hands the browser a
presigned PUT for an id-only key, ``complete`` verifies the object with a
HEAD (size, server-side encryption) and sniffs its first 8 KB, then marks the
row ``queued`` (plus an ``ingest`` job), ``unsupported`` (audio and video,
stored for later) or ``duplicate`` (same ETag and size as a stored sibling).
Rejections mark the row ``failed`` with a code and delete the object.

Logging rule: ids, sizes, codes and outcomes only. Never a filename.

The router is built by a factory so the gateway can pass its slowapi limiter
(defined in ``core/gateway.py``; importing it here would be circular and would
pull the whole agent graph into tests). Tests build the router without one.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from config.case_types import MATTER_TYPES, SIDES, STAGES, as_suggestions
from core import settings as _settings
from core.auth import require_user_key
from core.case_auth import resolve_case_user
from core.case_models import (
    CASE_NUMBER_MAX, CNR_MAX, FILENAME_MAX, OBJECTIVE_MAX, SHORT_MAX, SLUG_MAX, TITLE_MAX,
    CaseCreate, CaseDeleteOut, CaseListOut, CaseMetaOut, CaseOut, CaseStatusOut, CaseUpdate,
    DocumentDeleteOut, DocumentListOut, DocumentOut, DocumentPresignIn, DocumentPresignOut, DownloadOut,
)
from core.case_s3 import get_case_s3, raw_key
from core.case_sniff import SNIFF_BYTES, SUPPORTED_TYPES, canonical_mime, is_media_ext, matches, sanitise_filename
from core.case_store import (
    ACTION_CASE_CREATE, ACTION_CASE_DELETE, ACTION_CASE_UPDATE,
    ACTION_DOC_COMPLETE, ACTION_DOC_DELETE, ACTION_DOC_DOWNLOAD, ACTION_DOC_PRESIGN,
    DOC_DUPLICATE, DOC_FAILED, DOC_QUEUED, DOC_STORED, DOC_UNSUPPORTED, DOC_UPLOADING,
    CaseLimitError, CaseStoreProtocol, get_case_store,
)
from core.logger import get_logger, get_request_id, short_err

log = get_logger("CaseRoutes")

LIST_MAX_LIMIT = 200
DOC_LIST_MAX_LIMIT = 500


def _req_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", "") or get_request_id()
    return rid if rid and rid != "-" else ""


def _cfg(name: str, default, cast=int):
    try:
        return cast(getattr(_settings, name, default))
    except (TypeError, ValueError):
        return default


def _view_window_sec() -> int:
    return max(0, _cfg("CASE_AUDIT_VIEW_WINDOW_SEC", 900))


def _max_file_bytes() -> int:
    return int(_cfg("CASE_MAX_FILE_MB", 1024, float) * 1024 * 1024)


def _max_case_bytes() -> int:
    return int(_cfg("CASE_MAX_BYTES_PER_CASE_GB", 5, float) * 1024 ** 3)


def _max_docs() -> int:
    return max(1, _cfg("CASE_MAX_DOCS_PER_CASE", 500))


def _presign_ttl() -> int:
    return max(60, _cfg("CASE_PRESIGN_TTL_SEC", 900))


def _download_ttl() -> int:
    return max(30, _cfg("CASE_DOWNLOAD_TTL_SEC", 300))


def _purge_grace_days() -> int:
    return max(0, _cfg("CASE_PURGE_GRACE_DAYS", 7))


def _job_max_attempts() -> int:
    return max(1, _cfg("CASE_JOB_MAX_ATTEMPTS", 4))


def _expected_sse() -> str:
    return (getattr(_settings, "CASE_S3_SSE", "AES256") or "").strip()


def _uuid_or_none(value: str) -> Optional[str]:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _not_found(what: str = "Case") -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} not found")


def build_case_router(limiter=None, limit_value: Optional[Callable[[], str] | str] = None) -> APIRouter:
    router = APIRouter(prefix="/pyapi/cases", tags=["cases"],
                       dependencies=[Depends(require_user_key)])

    def _rl(fn):
        """Apply the gateway's rate limit when a limiter is provided."""
        if limiter is not None and limit_value is not None:
            return limiter.limit(limit_value)(fn)
        return fn

    # ---- meta (before /{case_id} so it is never captured as an id) --------

    @router.get("/meta", response_model=CaseMetaOut)
    @_rl
    async def case_meta(request: Request, user_id: str = Depends(resolve_case_user)):
        return CaseMetaOut(
            matter_types=as_suggestions(MATTER_TYPES),
            stages=as_suggestions(STAGES),
            sides=as_suggestions(SIDES),
            limits={
                "title_max": TITLE_MAX, "short_max": SHORT_MAX, "slug_max": SLUG_MAX,
                "case_number_max": CASE_NUMBER_MAX, "cnr_max": CNR_MAX,
                "objective_max": OBJECTIVE_MAX, "list_max_limit": LIST_MAX_LIMIT,
                "filename_max": FILENAME_MAX, "max_file_bytes": _max_file_bytes(),
                "max_docs_per_case": _max_docs(), "max_bytes_per_case": _max_case_bytes(),
                "supported_extensions": sorted(SUPPORTED_TYPES),
            },
        )

    # ---- cases ---------------------------------------------------------------

    @router.post("", response_model=CaseOut, status_code=status.HTTP_201_CREATED)
    @_rl
    async def create_case(
        request: Request,
        body: CaseCreate,
        user_id: str = Depends(resolve_case_user),
        store: CaseStoreProtocol = Depends(get_case_store),
    ):
        case = await store.create_case(user_id, body.model_dump())
        await store.record_audit(case["case_id"], user_id, ACTION_CASE_CREATE,
                                 object_id=case["case_id"], request_id=_req_id(request))
        return case

    @router.get("", response_model=CaseListOut)
    @_rl
    async def list_cases(
        request: Request,
        limit: int = Query(50, ge=1, le=LIST_MAX_LIMIT),
        offset: int = Query(0, ge=0),
        user_id: str = Depends(resolve_case_user),
        store: CaseStoreProtocol = Depends(get_case_store),
    ):
        items, total = await store.list_cases(user_id, limit=limit, offset=offset)
        return CaseListOut(items=items, total=total, limit=limit, offset=offset)

    @router.get("/{case_id}", response_model=CaseOut)
    @_rl
    async def get_case(
        request: Request,
        case_id: str,
        user_id: str = Depends(resolve_case_user),
        store: CaseStoreProtocol = Depends(get_case_store),
    ):
        case = await store.get_case(case_id, user_id)
        if case is None:
            raise _not_found()
        await store.record_view(case["case_id"], user_id, _req_id(request), _view_window_sec())
        return case

    @router.patch("/{case_id}", response_model=CaseOut)
    @_rl
    async def update_case(
        request: Request,
        case_id: str,
        body: CaseUpdate,
        user_id: str = Depends(resolve_case_user),
        store: CaseStoreProtocol = Depends(get_case_store),
    ):
        changes = body.changes()
        case = await store.update_case(case_id, user_id, changes)
        if case is None:
            raise _not_found()
        if changes:
            await store.record_audit(case["case_id"], user_id, ACTION_CASE_UPDATE,
                                     object_id=",".join(sorted(changes)), request_id=_req_id(request))
        return case

    # ---- slice 2: status and soft delete of a case -----------------------------

    @router.get("/{case_id}/status", response_model=CaseStatusOut)
    @_rl
    async def case_status(
        request: Request,
        case_id: str,
        user_id: str = Depends(resolve_case_user),
        store: CaseStoreProtocol = Depends(get_case_store),
    ):
        """Polling endpoint: per-status document counts. Audited as a view, throttled."""
        st = await store.case_status(case_id, user_id)
        if st is None:
            raise _not_found()
        await store.record_view(st["case_id"], user_id, _req_id(request), _view_window_sec())
        return st

    @router.delete("/{case_id}", response_model=CaseDeleteOut)
    @_rl
    async def delete_case(
        request: Request,
        case_id: str,
        user_id: str = Depends(resolve_case_user),
        store: CaseStoreProtocol = Depends(get_case_store),
    ):
        """Soft delete: tombstones the case and its documents, cancels queued
        jobs and schedules the purge job after the grace period."""
        out = await store.soft_delete_case(case_id, user_id, _purge_grace_days())
        if out is None:
            raise _not_found()
        await store.record_audit(out["case_id"], user_id, ACTION_CASE_DELETE,
                                 object_id=out["case_id"], request_id=_req_id(request))
        return out

    # ---- slice 2: documents ----------------------------------------------------

    @router.post("/{case_id}/documents/presign", response_model=DocumentPresignOut,
                 status_code=status.HTTP_201_CREATED)
    @_rl
    async def presign_document(
        request: Request,
        case_id: str,
        body: DocumentPresignIn,
        user_id: str = Depends(resolve_case_user),
        store: CaseStoreProtocol = Depends(get_case_store),
        s3=Depends(get_case_s3),
    ):
        cid = _uuid_or_none(case_id)
        if cid is None:
            raise _not_found()
        safe_name, ext = sanitise_filename(body.filename)
        spec = SUPPORTED_TYPES.get(ext)
        if spec is None:
            raise HTTPException(status_code=422, detail={
                "code": "unsupported_type", "ext": ext, "allowed": sorted(SUPPORTED_TYPES)})
        max_file = _max_file_bytes()
        if body.size_bytes > max_file:
            raise HTTPException(status_code=413, detail={"code": "file_too_large", "max_bytes": max_file})

        doc_id = str(uuid.uuid4())
        key = raw_key(cid, doc_id, ext)
        client_sha = body.client_sha256.lower() or None
        fields = {
            "filename": safe_name, "ext": ext, "declared_mime": body.content_type or spec[1],
            "size_bytes": body.size_bytes, "client_sha256": client_sha, "s3_key": key, "source": "lawyer",
        }
        try:
            doc = await store.create_document(cid, user_id, doc_id, fields,
                                              max_docs=_max_docs(), max_bytes=_max_case_bytes())
        except CaseLimitError as e:
            http = 409 if e.code == "case_not_active" else 413
            raise HTTPException(status_code=http, detail={"code": e.code})
        if doc is None:
            raise _not_found()

        upload = await asyncio.to_thread(s3.presign_put, key, spec[1], _presign_ttl())
        hint = None
        if client_sha:
            hint = await store.find_duplicate(cid, sha256=client_sha, exclude_doc_id=doc_id)
        await store.record_audit(cid, user_id, ACTION_DOC_PRESIGN, object_id=doc_id, request_id=_req_id(request))
        return {**doc, "upload": upload, "duplicate_hint": hint}

    @router.post("/{case_id}/documents/{doc_id}/complete", response_model=DocumentOut)
    @_rl
    async def complete_document(
        request: Request,
        case_id: str,
        doc_id: str,
        user_id: str = Depends(resolve_case_user),
        store: CaseStoreProtocol = Depends(get_case_store),
        s3=Depends(get_case_s3),
    ):
        doc = await store.get_document(case_id, doc_id, user_id)
        if doc is None:
            raise _not_found("Document")
        if doc["status"] != DOC_UPLOADING:
            return doc                                     # idempotent: already completed
        cid, did, key = doc["case_id"], doc["doc_id"], doc["s3_key"]
        rid = _req_id(request)

        async def reject(code: str, http: int, head=None, sniffed: str = ""):
            if head is not None and head.exists:
                try:
                    await asyncio.to_thread(s3.delete, key, head.version_id or None)
                except Exception as e:
                    log.warning("Delete of rejected object failed", case=cid[:8], doc=did[:8], error=short_err(e))
            await store.finish_complete(cid, did, user_id, outcome=DOC_FAILED, error_code=code,
                                        sniffed_mime=sniffed)
            await store.record_audit(cid, user_id, ACTION_DOC_COMPLETE, object_id=f"{did}:failed:{code}",
                                     request_id=rid)
            log.info("Document complete rejected", case=cid[:8], doc=did[:8], code=code)
            raise HTTPException(status_code=http, detail={"code": code, "doc_id": did})

        try:
            head = await asyncio.to_thread(s3.head, key)
        except Exception as e:
            log.warning("HEAD failed at complete", case=cid[:8], doc=did[:8], error=short_err(e))
            raise HTTPException(status_code=503, detail={"code": "storage_error", "doc_id": did})
        if not head.exists:
            await reject("object_missing", 409)
        if head.size != int(doc["size_bytes"]) or head.size > _max_file_bytes():
            await reject("size_mismatch", 422, head)
        expected = _expected_sse()
        if not head.sse or (expected and head.sse != expected):
            await reject("sse_missing", 422, head)
        try:
            first = await asyncio.to_thread(s3.read_range, key, 0, SNIFF_BYTES - 1, head.version_id or None)
        except Exception as e:
            log.warning("Ranged read failed at complete", case=cid[:8], doc=did[:8], error=short_err(e))
            raise HTTPException(status_code=503, detail={"code": "storage_error", "doc_id": did})
        ok, sniffed = matches(first, doc["ext"])
        if not ok:
            await reject("mime_mismatch", 422, head, sniffed)

        outcome, duplicate_of, enqueue = DOC_QUEUED, None, True
        if is_media_ext(doc["ext"]):
            outcome, enqueue = DOC_UNSUPPORTED, False          # stored; transcription comes later
        else:
            duplicate_of = await store.find_duplicate(cid, etag=head.etag, size=head.size, exclude_doc_id=did)
            if duplicate_of:
                outcome, enqueue = DOC_DUPLICATE, False
        out = await store.finish_complete(
            cid, did, user_id, outcome=outcome, sniffed_mime=sniffed, sse=head.sse,
            version_id=head.version_id, etag=head.etag, duplicate_of=duplicate_of,
            enqueue_job=enqueue, max_attempts=_job_max_attempts(),
        )
        await store.record_audit(cid, user_id, ACTION_DOC_COMPLETE, object_id=f"{did}:{outcome}", request_id=rid)
        return out

    @router.get("/{case_id}/documents", response_model=DocumentListOut)
    @_rl
    async def list_documents(
        request: Request,
        case_id: str,
        limit: int = Query(100, ge=1, le=DOC_LIST_MAX_LIMIT),
        offset: int = Query(0, ge=0),
        status_filter: Optional[str] = Query(None, alias="status", max_length=20),
        user_id: str = Depends(resolve_case_user),
        store: CaseStoreProtocol = Depends(get_case_store),
    ):
        res = await store.list_documents(case_id, user_id, limit=limit, offset=offset, status=status_filter)
        if res is None:
            raise _not_found()
        items, total, counts = res
        return DocumentListOut(items=items, total=total, limit=limit, offset=offset, counts=counts)

    @router.get("/{case_id}/documents/{doc_id}", response_model=DocumentOut)
    @_rl
    async def get_document(
        request: Request,
        case_id: str,
        doc_id: str,
        user_id: str = Depends(resolve_case_user),
        store: CaseStoreProtocol = Depends(get_case_store),
    ):
        doc = await store.get_document(case_id, doc_id, user_id)
        if doc is None:
            raise _not_found("Document")
        return doc

    @router.get("/{case_id}/documents/{doc_id}/download", response_model=DownloadOut)
    @_rl
    async def download_document(
        request: Request,
        case_id: str,
        doc_id: str,
        user_id: str = Depends(resolve_case_user),
        store: CaseStoreProtocol = Depends(get_case_store),
        s3=Depends(get_case_s3),
    ):
        """Short-lived presigned GET. The filename travels only in the
        Content-Disposition S3 is asked to return, never in the key or logs."""
        doc = await store.get_document(case_id, doc_id, user_id)
        if doc is None:
            raise _not_found("Document")
        if doc["status"] not in DOC_STORED:
            raise HTTPException(status_code=409, detail={"code": "not_downloadable", "status": doc["status"]})
        ttl = _download_ttl()
        content_type = canonical_mime(doc["ext"])
        url = await asyncio.to_thread(s3.presign_get, doc["s3_key"], ttl, doc["filename"], content_type,
                                      doc.get("s3_version_id") or None)
        await store.record_audit(doc["case_id"], user_id, ACTION_DOC_DOWNLOAD, object_id=doc["doc_id"],
                                 request_id=_req_id(request))
        return DownloadOut(url=url, expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
                           filename=doc["filename"], size_bytes=int(doc["size_bytes"]), content_type=content_type)

    @router.delete("/{case_id}/documents/{doc_id}", response_model=DocumentDeleteOut)
    @_rl
    async def delete_document(
        request: Request,
        case_id: str,
        doc_id: str,
        user_id: str = Depends(resolve_case_user),
        store: CaseStoreProtocol = Depends(get_case_store),
    ):
        out = await store.soft_delete_document(case_id, doc_id, user_id, _purge_grace_days())
        if out is None:
            raise _not_found("Document")
        await store.record_audit(out["case_id"], user_id, ACTION_DOC_DELETE, object_id=out["doc_id"],
                                 request_id=_req_id(request))
        return out

    return router
