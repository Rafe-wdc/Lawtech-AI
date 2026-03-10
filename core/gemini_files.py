"""Gemini Files API wrapper.

Handles upload, expiry checking, and re-upload of files for multi-turn
file persistence. The google.genai SDK is synchronous — always call these
functions via asyncio.to_thread() from async code.

Supported MIME types (Gemini Files API):
  Images  : image/jpeg, image/png, image/webp, image/gif
  Docs    : application/pdf
  Text    : text/plain, text/html, text/csv, text/markdown, text/xml
  NOT supported: .docx, .xlsx (handled via text extraction instead)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

from core.logger import get_logger
from core.settings import GEMINI_URI_EXPIRY_BUFFER_HOURS

log = get_logger("GeminiFiles")

# MIME types accepted by Gemini Files API for direct upload
GEMINI_SUPPORTED_MIMES: frozenset[str] = frozenset({
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "application/pdf",
    "text/plain",
    "text/html",
    "text/csv",
    "text/markdown",
    "text/xml",
})

# Extension → MIME map (covers all ALLOWED_EXTENSIONS)
EXT_TO_MIME: dict[str, str] = {
    ".pdf":  "application/pdf",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".webp": "image/webp",
    ".gif":  "image/gif",
    ".txt":  "text/plain",
    ".md":   "text/markdown",
    ".csv":  "text/csv",
    ".html": "text/html",
    ".xml":  "text/xml",
    # DOCX / XLSX not supported by Gemini Files API
    ".docx": "",
    ".xlsx": "",
}


def get_mime_type(ext: str) -> str:
    """Return the MIME type for a file extension, or '' if not supported."""
    return EXT_TO_MIME.get(ext.lower(), "")


def is_gemini_supported(ext: str) -> bool:
    """True if the extension has a Gemini-supported MIME type."""
    mime = get_mime_type(ext)
    return mime in GEMINI_SUPPORTED_MIMES


def is_uri_valid(expiry_iso: str) -> bool:
    """Return True if the Gemini URI is still usable (not expiring within buffer window)."""
    if not expiry_iso:
        return False
    try:
        expiry = datetime.fromisoformat(expiry_iso)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) + timedelta(hours=GEMINI_URI_EXPIRY_BUFFER_HOURS)
        return expiry > cutoff
    except (ValueError, TypeError):
        return False


def upload_to_gemini(local_path: str, mime_type: str, display_name: str) -> tuple[str, str, str]:
    """Upload a file to Gemini Files API (synchronous — use via asyncio.to_thread).

    Returns:
        (uri, gemini_name, expiry_iso)
        - uri         : full HTTPS URI for use in Gemini content parts
        - gemini_name : "files/abc123" handle for deletion/lookup
        - expiry_iso  : ISO 8601 datetime string (48h from upload)

    Raises:
        Exception on upload failure.
    """
    from google.genai import types as genai_types
    from core.clients import get_genai_client

    client = get_genai_client()
    with open(local_path, "rb") as f:
        file_obj = client.files.upload(
            file=f,
            config=genai_types.UploadFileConfig(
                mime_type=mime_type,
                display_name=display_name,
            ),
        )

    expiry_iso = ""
    if file_obj.expiration_time:
        expiry_iso = file_obj.expiration_time.isoformat()

    log.info("Uploaded to Gemini Files API",
             name=file_obj.name, mime=mime_type,
             display=display_name, expiry=expiry_iso[:19] if expiry_iso else "none")

    return file_obj.uri, file_obj.name, expiry_iso


def delete_gemini_file(gemini_name: str) -> None:
    """Delete a file from Gemini Files API. Silently ignores errors."""
    if not gemini_name:
        return
    try:
        from core.clients import get_genai_client
        client = get_genai_client()
        client.files.delete(name=gemini_name)
        log.info("Deleted Gemini file", name=gemini_name)
    except Exception as e:
        log.warning("Failed to delete Gemini file", name=gemini_name, error=str(e))