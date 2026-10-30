"""S3 access for the case workspace: one place for bucket, keys, SSE and presigning.

Keys are built from ids only (never a filename) and live in the private
``lawttorney-cases`` bucket (decision 4). The API never proxies file bytes:
the browser PUTs to a presigned URL, ``complete`` HEADs the object and reads
at most ``SNIFF_BYTES``, and downloads are short-lived presigned GETs whose
only trace of the filename is the ``Content-Disposition`` S3 is asked to
return. ``tests/case_support.FakeCaseS3`` implements the same methods in
memory; ``tests/test_case_s3.py`` checks the real parameter construction
with ``botocore.stub``.
"""
from __future__ import annotations

import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

from core import settings as _settings
from core.logger import get_logger, short_err

log = get_logger("CaseS3")


class HeadResult(NamedTuple):
    exists: bool
    size: int = 0
    sse: str = ""
    etag: str = ""
    version_id: str = ""
    content_type: str = ""


# ---------------------------------------------------------------------- keys

def case_prefix(case_id: str) -> str:
    return f"cases/{case_id}/"


def raw_key(case_id: str, doc_id: str, ext: str) -> str:
    return f"cases/{case_id}/raw/{doc_id}.{ext}" if ext else f"cases/{case_id}/raw/{doc_id}"


def extracted_prefix(case_id: str, doc_id: str) -> str:
    return f"cases/{case_id}/extracted/{doc_id}/"


def text_key(case_id: str, doc_id: str) -> str:
    return extracted_prefix(case_id, doc_id) + "text.jsonl"


def meta_key(case_id: str, doc_id: str) -> str:
    return extracted_prefix(case_id, doc_id) + "meta.json"


def content_disposition(filename: str) -> str:
    """RFC 6266 attachment header with an ASCII fallback and a UTF-8 form."""
    ascii_name = (filename.encode("ascii", "ignore").decode("ascii")
                  .replace('"', "").replace("\\", "").replace(";", "").strip()) or "document"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(filename, safe='')}"


def _utc_in(seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=int(seconds))


# -------------------------------------------------------------------- client

class CaseS3:
    """Thin boto3 wrapper. ``client`` may be injected (tests use a stubbed one)."""

    def __init__(self, bucket: str, region: str, sse: str = "AES256", client=None,
                 connect_timeout: float = 5.0, read_timeout: float = 15.0):
        if not bucket:
            raise RuntimeError("CASE_S3_BUCKET is not set: the case workspace needs its private bucket")
        self.bucket = bucket
        self.region = region
        self.sse = sse or "AES256"
        self._client = client
        self._lock = threading.Lock()
        self._timeouts = (connect_timeout, read_timeout)

    def client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    import boto3
                    from botocore.config import Config
                    self._client = boto3.client(
                        "s3",
                        region_name=self.region or None,
                        config=Config(
                            signature_version="s3v4",
                            s3={"addressing_style": "virtual"},
                            connect_timeout=self._timeouts[0],
                            read_timeout=self._timeouts[1],
                            retries={"max_attempts": 2, "mode": "standard"},
                        ),
                    )
        return self._client

    # ---- upload --------------------------------------------------------------

    def presign_put(self, key: str, content_type: str, expires_s: int) -> dict:
        """Presigned PUT. The browser must send exactly the returned headers
        (``Content-Type`` is signed). Encryption comes from the bucket default
        and is verified on HEAD at complete (decision 5)."""
        url = self.client().generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=int(expires_s),
            HttpMethod="PUT",
        )
        return {"url": url, "method": "PUT", "headers": {"Content-Type": content_type},
                "expires_at": _utc_in(expires_s)}

    def head(self, key: str) -> HeadResult:
        from botocore.exceptions import ClientError
        try:
            r = self.client().head_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            code = str(e.response.get("Error", {}).get("Code", ""))
            status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in ("404", "NoSuchKey", "NotFound") or status == 404:
                return HeadResult(False)
            raise
        return HeadResult(
            True,
            int(r.get("ContentLength", 0) or 0),
            str(r.get("ServerSideEncryption", "") or ""),
            str(r.get("ETag", "") or "").strip('"'),
            str(r.get("VersionId", "") or ""),
            str(r.get("ContentType", "") or ""),
        )

    def read_range(self, key: str, start: int, end: int, version_id: Optional[str] = None) -> bytes:
        kw = {"Bucket": self.bucket, "Key": key, "Range": f"bytes={int(start)}-{int(end)}"}
        if version_id:
            kw["VersionId"] = version_id
        body = self.client().get_object(**kw)["Body"]
        try:
            return body.read()
        finally:
            try:
                body.close()
            except Exception:
                pass

    # ---- download ------------------------------------------------------------

    def presign_get(self, key: str, expires_s: int, filename: str, content_type: str = "",
                    version_id: Optional[str] = None) -> str:
        params = {"Bucket": self.bucket, "Key": key,
                  "ResponseContentDisposition": content_disposition(filename)}
        if content_type:
            params["ResponseContentType"] = content_type
        if version_id:
            params["VersionId"] = version_id
        return self.client().generate_presigned_url("get_object", Params=params,
                                                    ExpiresIn=int(expires_s), HttpMethod="GET")

    # ---- housekeeping --------------------------------------------------------

    def delete(self, key: str, version_id: Optional[str] = None) -> None:
        """Delete one object (a specific version when given; the bucket is
        versioned, so without ``version_id`` this only writes a delete marker)."""
        kw = {"Bucket": self.bucket, "Key": key}
        if version_id:
            kw["VersionId"] = version_id
        try:
            self.client().delete_object(**kw)
        except Exception as e:
            log.warning("S3 delete failed", error=short_err(e))
            raise

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        """Server-side write (sidecars, slice 3). Returns the new version id."""
        r = self.client().put_object(Bucket=self.bucket, Key=key, Body=data,
                                     ContentType=content_type, ServerSideEncryption=self.sse)
        return str(r.get("VersionId", "") or "")


# ---------------------------------------------------------------------------
# Process-wide instance (FastAPI dependency). Tests override the dependency.
# ---------------------------------------------------------------------------

_s3: Optional[CaseS3] = None
_s3_lock = threading.Lock()


def get_case_s3() -> CaseS3:
    global _s3
    if _s3 is None:
        with _s3_lock:
            if _s3 is None:
                _s3 = CaseS3(
                    bucket=(getattr(_settings, "CASE_S3_BUCKET", "") or "").strip(),
                    region=(getattr(_settings, "CASE_S3_REGION", "") or "").strip(),
                    sse=(getattr(_settings, "CASE_S3_SSE", "AES256") or "AES256").strip(),
                )
    return _s3
