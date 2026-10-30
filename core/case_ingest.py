"""The per-document ingest job (worker side).

    download by key + version (streamed, sha256 computed on the way)
      -> size check
      -> authoritative dedupe: a `ready` sibling with the same sha256
      -> extraction in a child process (core.case_extract)
      -> sidecar text.jsonl (doc_id on every line) + meta.json in S3
      -> optional kind classification (CASE_CLASSIFY_ON_INGEST)
      -> row `ready` (or `duplicate`, with its own copy of the sidecar)

Scratch is ``case_tmp/{job_id}/`` under ``CASE_TMP_ROOT``; the worker removes
it in a ``finally`` and sweeps leftovers at startup (``sweep_case_tmp``).

Errors: ``IngestError(code, permanent)``; the worker decides retry vs fail.
``JobAborted`` means stop writing: the lock was lost (another worker owns
the job now) or the worker is shutting down (the job is released).

Logging rule: ids, sizes, counts, codes, durations. Never a filename or text.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.case_extract import (
    DEFAULT_ENTRY, PERMANENT_CODES, SIDECAR_SCHEMA, ExtractAborted, ExtractTimeout, run_extraction,
)
from core.case_s3 import meta_key, text_key
from core.case_store import DOC_DUPLICATE, DOC_READY
from core.logger import get_logger, short_err

log = get_logger("CaseIngest")

SIDECAR_CONTENT_TYPE = "application/x-ndjson"
CLASSIFY_SAMPLE_CHARS = 20_000
CLASSIFY_TIMEOUT_S = 60


class IngestError(Exception):
    def __init__(self, code: str, detail: str = "", permanent: Optional[bool] = None):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = (detail or "")[:200]
        self.permanent = (code in PERMANENT_CODES) if permanent is None else bool(permanent)


class JobAborted(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason            # "lock_lost" | "shutdown"


# ------------------------------------------------------------------ scratch

def job_dir(tmp_root: str, job_id: str) -> Path:
    """``case_tmp/{job_id}``; the id is re-validated so no path comes from data."""
    return Path(tmp_root) / str(uuid.UUID(str(job_id)))


def remove_job_dir(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception as e:  # pragma: no cover - rmtree with ignore_errors should not raise
        log.warning("Scratch removal failed", error=short_err(e))


def sweep_case_tmp(tmp_root: str, older_than_s: int, now: Optional[float] = None) -> int:
    """Remove job directories older than ``older_than_s`` (a worker that died
    mid-job left them). Only direct children named like a UUID are touched."""
    root = Path(tmp_root)
    if not root.is_dir():
        return 0
    cutoff = (now if now is not None else time.time()) - older_than_s
    removed = 0
    for child in root.iterdir():
        try:
            uuid.UUID(child.name)
        except ValueError:
            continue
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        log.info("Stale scratch removed", dirs=removed, older_than_s=older_than_s)
    return removed


# ------------------------------------------------------------------ sidecar

def sidecar_bytes(lines: list[dict], doc_id: str, duplicate_of: Optional[str] = None) -> bytes:
    """One JSON object per line; ``doc_id`` is on every line so a line that
    is quoted elsewhere still names its document."""
    out = []
    for ln in lines:
        rec = {
            "doc_id": doc_id, "unit": ln["unit"], "page": ln.get("page"), "index": ln["index"],
            "heading": ln.get("heading"), "text": ln["text"], "source": ln["source"],
            "confidence": ln.get("confidence"), "chars": ln["chars"],
        }
        if duplicate_of:
            rec["duplicate_of"] = duplicate_of
        out.append(json.dumps(rec, ensure_ascii=False))
    return ("\n".join(out) + "\n").encode("utf-8")


def parse_sidecar(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def meta_document(doc: dict, *, sha256: str, extractor: str, page_count: Optional[int], chars_total: int,
                  ocr_pages: int, lines: int, duplicate_of: Optional[str] = None) -> dict:
    return {
        "sidecar_schema": SIDECAR_SCHEMA, "doc_id": doc["doc_id"], "case_id": doc["case_id"],
        "ext": doc["ext"], "size_bytes": int(doc["size_bytes"]), "sha256": sha256,
        "extractor": extractor, "page_count": page_count, "chars_total": chars_total,
        "ocr_pages": ocr_pages, "lines": lines, "duplicate_of": duplicate_of,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _put_sidecar(s3, case_id: str, doc_id: str, body: bytes, meta: dict) -> tuple[str, str]:
    tk, mk = text_key(case_id, doc_id), meta_key(case_id, doc_id)
    try:
        s3.put_bytes(tk, body, SIDECAR_CONTENT_TYPE)
        s3.put_bytes(mk, json.dumps(meta, ensure_ascii=False).encode("utf-8"), "application/json")
    except Exception as e:
        raise IngestError("storage_error", short_err(e), permanent=False) from e
    return tk, mk


# ----------------------------------------------------------- classification

def classify_kind(lines: list[dict], ext: str, doc_id: str) -> tuple[str, float]:
    """Gemini kind classification over the extracted text, bounded and never
    fatal: any failure or timeout returns ("", 0.0). The classifier receives
    a generic name, not the lawyer's filename."""
    text = "\n".join(ln.get("text") or "" for ln in lines)[:CLASSIFY_SAMPLE_CHARS]
    if not text.strip():
        return "", 0.0
    ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="case-classify")
    try:
        from core.file_classifier import classify_file_kind
        kind, conf = ex.submit(classify_file_kind, text, f"document.{ext}").result(timeout=CLASSIFY_TIMEOUT_S)
        return str(kind or ""), float(conf or 0.0)
    except Exception as e:
        log.warning("Kind classification skipped", doc=doc_id[:8], error=short_err(e))
        return "", 0.0
    finally:
        ex.shutdown(wait=False)


# ------------------------------------------------------------------- the job

def _check_abort(abort, lost) -> None:
    if lost is not None and lost.is_set():
        raise JobAborted("lock_lost")
    if abort is not None and abort.is_set():
        raise JobAborted("shutdown")


def _is_missing(exc: Exception) -> bool:
    resp = getattr(exc, "response", None) or {}
    code = str((resp.get("Error") or {}).get("Code", ""))
    status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    return code in ("NoSuchKey", "NoSuchVersion", "404", "NotFound") or status == 404


def run_ingest_job(job: dict, doc: dict, *, store, s3, tmp_root: str, timeout_s: float,
                   abort=None, lost=None, in_process: bool = False, entry: Optional[str] = None,
                   classify: bool = False) -> str:
    """Process one queued document. Returns ``"ready"`` | ``"duplicate"`` |
    ``"skipped"`` (the row left ``processing`` meanwhile, e.g. deleted).
    Raises ``IngestError`` or ``JobAborted``. The caller owns the job row,
    the scratch directory removal and the lock heartbeat."""
    did, cid = doc["doc_id"], doc["case_id"]
    jdir = job_dir(tmp_root, job["job_id"])
    jdir.mkdir(parents=True, exist_ok=True)
    local = jdir / f"{did}.{doc['ext']}"
    t0 = time.monotonic()

    # 1. download + hash
    try:
        size, sha = s3.download_to_file(doc["s3_key"], str(local), doc.get("s3_version_id") or None)
    except Exception as e:
        if _is_missing(e):
            raise IngestError("object_missing", short_err(e), permanent=True) from e
        raise IngestError("storage_error", short_err(e), permanent=False) from e
    if size != int(doc["size_bytes"]):
        raise IngestError("size_mismatch", f"object {size} bytes, row {doc['size_bytes']}", permanent=True)
    log.info("Object fetched", case=cid[:8], doc=did[:8], bytes=size, ms=int((time.monotonic() - t0) * 1000))
    _check_abort(abort, lost)

    # 2. authoritative dedupe: same sha256 as a sibling that already has a sidecar
    original = store.find_ready_by_sha256_sync(cid, sha, exclude_doc_id=did)
    if original:
        return _finish_duplicate(doc, original, sha, store=store, s3=s3, abort=abort, lost=lost)

    # 3. extraction (child process)
    try:
        result = run_extraction(str(local), doc["ext"], did, str(jdir / "result.json"), timeout_s=timeout_s,
                                abort=_either(abort, lost), in_process=in_process, entry=entry or DEFAULT_ENTRY)
    except ExtractTimeout as e:
        raise IngestError("timeout", str(e), permanent=False) from e
    except ExtractAborted:
        _check_abort(abort, lost)
        raise JobAborted("shutdown")
    if not result.get("ok"):
        raise IngestError(result.get("code") or "extract_error", result.get("detail") or "",
                          permanent=result.get("permanent"))
    _check_abort(abort, lost)

    # 4. sidecar
    lines = result["lines"]
    meta = meta_document(doc, sha256=sha, extractor=result["extractor"], page_count=result["page_count"],
                         chars_total=result["chars_total"], ocr_pages=result["ocr_pages"], lines=len(lines))
    tk, mk = _put_sidecar(s3, cid, did, sidecar_bytes(lines, did), meta)

    # 5. kind (flagged)
    kind, conf = classify_kind(lines, doc["ext"], did) if classify else ("", 0.0)
    _check_abort(abort, lost)

    # 6. finalise
    row = store.finish_document_sync(
        did, cid, status=DOC_READY, sha256=sha, page_count=result["page_count"],
        chars_total=result["chars_total"], ocr_pages=result["ocr_pages"], extractor=result["extractor"],
        text_key=tk, meta_key=mk, kind=kind, kind_confidence=conf,
    )
    log.info("Ingest done", case=cid[:8], doc=did[:8], extractor=result["extractor"], lines=len(lines),
             pages=result["page_count"], ocr_pages=result["ocr_pages"], chars=result["chars_total"],
             ms=int((time.monotonic() - t0) * 1000), finalised=row is not None)
    return "ready" if row is not None else "skipped"


def _either(a, b):
    """An object with ``is_set()`` true when either event is set."""
    class _E:
        @staticmethod
        def is_set():
            return (a is not None and a.is_set()) or (b is not None and b.is_set())
    return _E()


def _finish_duplicate(doc: dict, original: str, sha: str, *, store, s3, abort, lost) -> str:
    """Give the duplicate its own copy of the original's sidecar (lines
    re-stamped with this doc_id) so every document is self-contained and a
    later purge of the original takes nothing away from it."""
    did, cid = doc["doc_id"], doc["case_id"]
    try:
        lines = parse_sidecar(s3.get_bytes(text_key(cid, original)))
        orig_meta = json.loads(s3.get_bytes(meta_key(cid, original)).decode("utf-8"))
    except Exception as e:
        raise IngestError("storage_error", short_err(e), permanent=False) from e
    _check_abort(abort, lost)
    meta = meta_document(doc, sha256=sha, extractor=orig_meta.get("extractor") or "",
                         page_count=orig_meta.get("page_count"), chars_total=int(orig_meta.get("chars_total") or 0),
                         ocr_pages=int(orig_meta.get("ocr_pages") or 0), lines=len(lines), duplicate_of=original)
    tk, mk = _put_sidecar(s3, cid, did, sidecar_bytes(lines, did, duplicate_of=original), meta)
    row = store.finish_document_sync(
        did, cid, status=DOC_DUPLICATE, sha256=sha, duplicate_of=original,
        page_count=meta["page_count"], chars_total=meta["chars_total"], ocr_pages=meta["ocr_pages"],
        extractor=meta["extractor"], text_key=tk, meta_key=mk,
    )
    log.info("Duplicate confirmed by sha256", case=cid[:8], doc=did[:8], original=original[:8],
             finalised=row is not None)
    return "duplicate" if row is not None else "skipped"


def local_scratch_root() -> str:
    from core import settings as _settings
    root = getattr(_settings, "CASE_TMP_ROOT", "") or os.path.join(os.getcwd(), "case_tmp")
    return str(root)
