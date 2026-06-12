"""Shared Tools: Storage and external API operations.

Reusable @tool functions for:
- AWS S3 link generation for judgment PDFs
- Chat history loading (SQLite-backed)

Uses: core.settings for S3 config
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

import requests
from langchain.tools import tool

from core.settings import S3_BUCKET, S3_REGION, CHROMA_STORE_ROOT
from core.logger import get_logger

log = get_logger("Storage")


# --- S3 Existence Check (cached per server lifetime) ---

@lru_cache(maxsize=1)
def _get_s3_client():
    """Lazy S3 client with explicit connect/read timeouts so head_object can't hang."""
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        config=Config(
            connect_timeout=5,
            read_timeout=5,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


@lru_cache(maxsize=2048)
def _s3_key_exists(bucket: str, key: str) -> bool:
    """Return True if the S3 object exists, False only on a confirmed 404/NoSuchKey.

    On any other error (403, no credentials, network failure, timeout) returns
    True so callers get a URL rather than silently dropping the link.
    Results are memoized so each key is only checked once per process lifetime.
    """
    try:
        _get_s3_client().head_object(Bucket=bucket, Key=key)
        return True
    except Exception as e:
        # Extract error code if this is a botocore ClientError
        response = getattr(e, "response", None)
        if response:
            code = response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey"):
                return False
        # 403 / missing credentials / network error / timeout — assume exists
        log.debug("S3 head_object error treated as exists",
                  bucket=bucket, key=key[:80], error=str(e)[:200])
        return True


# --- S3 Link Generation ---

@tool
def generate_s3_link(court: str, file_name: str, title: str) -> Optional[str]:
    """Generate a verified public S3 URL for a judgment PDF stored in AWS S3.

    The URL pattern depends on the court:
    - Supreme Court: {bucket}/{court}/{title}.pdf
    - Other courts: {bucket}/{court}/{file_basename}

    Performs a lightweight HEAD check to confirm the file exists.
    Returns None if the object is confirmed missing, preventing dead links.
    Results are cached so repeated lookups for the same key are free.

    Args:
        court: Court name (e.g. "supreme", "bombay high court")
        file_name: Source file path from ES hit metadata
        title: Case title (petitioner vs respondent) for Supreme Court PDFs

    Returns:
        Verified public S3 URL string, or None if object not found in S3
    """
    s3_root = (court or "").strip().lower()
    file_base = os.path.basename(file_name.replace("\\", "/"))

    if s3_root == "supreme":
        s3_key = f"{s3_root}/{title}.pdf"
    else:
        s3_key = f"{s3_root}/{file_base}"

    if not _s3_key_exists(S3_BUCKET, s3_key):
        return None
    return f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{s3_key}"


# --- PDF Chat History ---

# --- Chat History (SQLite-backed) ---

@tool
def load_chat_history_from_api(thread_id: str) -> dict:
    """Load chat history, preferring local SQLite store with API fallback.

    Args:
        thread_id: The conversation thread ID to fetch history for

    Returns:
        Dict with keys: messages (list of {role, content} dicts), summary_text (raw)
    """
    from core.chat_store import chat_store

    # Try SQLite first
    try:
        result = chat_store._load_history_sync(thread_id)
        if result.total_turns > 0:
            messages = []
            for turn in result.raw_turns:
                messages.append({"role": "user", "content": turn["user_query"]})
                messages.append({"role": "assistant", "content": turn["ai_response"]})
            return {"messages": messages[-10:], "summary_text": result.summary_text}
    except Exception as e:
        # History load failures must NOT be silent — caller gets a fresh-start payload,
        # which means the user loses chat context. Log so prod incidents are debuggable.
        from core.logger import get_logger
        get_logger("StorageTools").warning(
            "Chat history load failed; returning fresh-start payload",
            thread_id=thread_id,
            error=str(e)[:200],
            exc_info=True,
        )

    # No legacy API — return fresh start
    return {
        "messages": [
            {"role": "user", "content": "Previous summary:"},
            {"role": "assistant", "content": "Fresh chat started."},
        ],
        "summary_text": "",
    }
