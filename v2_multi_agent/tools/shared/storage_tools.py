"""Shared Tools: Storage and external API operations.

Reusable @tool functions for:
- AWS S3 link generation for judgment PDFs
- PDF chat history persistence (local JSON)
- Chat history loading from external lawttorney.ai API

Uses: core.settings for S3 config and API base URL
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Optional

import requests
from langchain.tools import tool

from core.settings import S3_BUCKET, S3_REGION, CHROMA_STORE_ROOT, LAWTTORNEY_API_BASE


# --- S3 Existence Check (cached per server lifetime) ---

@lru_cache(maxsize=2048)
def _s3_key_exists(bucket: str, key: str) -> bool:
    """Return True if the S3 object exists, False only on a confirmed 404/NoSuchKey.

    On any other error (403, no credentials, network failure) returns True so
    that callers get a URL rather than silently dropping the link.
    Results are memoized so each key is only checked once per process lifetime.
    """
    try:
        import boto3
        from botocore.exceptions import ClientError

        boto3.client("s3").head_object(Bucket=bucket, Key=key)
        return True
    except Exception as e:
        # Extract error code if this is a botocore ClientError
        response = getattr(e, "response", None)
        if response:
            code = response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey"):
                return False
        # 403 / missing credentials / network error — assume exists
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

@tool
def load_pdf_chat_history(unique_string: str) -> dict:
    """Load PDF-specific chat history from local JSON file.

    Each uploaded PDF collection has its own chat history stored
    at chroma_store/chat_histories/{unique_string}_chat.json.

    Args:
        unique_string: The unique identifier for the user's document collection

    Returns:
        Dict with keys: recent (last 5 messages), all_chats (full history list)
    """
    chat_dir = os.path.join(CHROMA_STORE_ROOT, "chat_histories")
    chat_file = os.path.join(chat_dir, f"{unique_string}_chat.json")

    if not os.path.exists(chat_file):
        return {"recent": [], "all_chats": []}

    try:
        with open(chat_file, "r", encoding="utf-8") as f:
            all_chats = json.load(f)
        recent = all_chats[-5:] if len(all_chats) > 5 else all_chats
        return {"recent": recent, "all_chats": all_chats}
    except Exception as e:
        print(f"[Storage] Failed to load chat history for {unique_string}: {e}")
        return {"recent": [], "all_chats": []}


@tool
def save_pdf_chat_history(
    unique_string: str,
    question: str,
    answer: str,
) -> dict:
    """Save a Q&A pair to the PDF-specific chat history file.

    Appends the new Q&A pair to the existing chat history JSON file.
    Creates the file and directory if they don't exist.

    Args:
        unique_string: The unique identifier for the user's document collection
        question: The user's question
        answer: The generated answer

    Returns:
        Dict with keys: saved (bool), total_messages (int)
    """
    chat_dir = os.path.join(CHROMA_STORE_ROOT, "chat_histories")
    os.makedirs(chat_dir, exist_ok=True)
    chat_file = os.path.join(chat_dir, f"{unique_string}_chat.json")

    # Load existing history
    all_chats = []
    if os.path.exists(chat_file):
        try:
            with open(chat_file, "r", encoding="utf-8") as f:
                all_chats = json.load(f)
        except Exception:
            all_chats = []

    all_chats.append({"question": question, "answer": answer})

    try:
        with open(chat_file, "w", encoding="utf-8") as f:
            json.dump(all_chats, f, ensure_ascii=False, indent=2)
        return {"saved": True, "total_messages": len(all_chats)}
    except Exception as e:
        print(f"[Storage] Failed to save chat history for {unique_string}: {e}")
        return {"saved": False, "total_messages": len(all_chats)}


# --- Chat History (SQLite-first with legacy API fallback) ---

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
    except Exception:
        pass

    # Fallback to legacy API
    messages = []
    summary_text = ""

    try:
        url = f"{LAWTTORNEY_API_BASE}/users/getChatSummary/{thread_id}"
        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("status") and res_json.get("data"):
                summary_text = res_json["data"].get("chatSummary", "").strip()
    except Exception as e:
        print(f"[Storage] Error fetching chat history for thread {thread_id}: {e}")

    if summary_text:
        try:
            parsed = json.loads(summary_text)
            if isinstance(parsed, list):
                for turn in parsed[-5:]:
                    messages.append({"role": "user", "content": turn.get("user", "")})
                    messages.append({"role": "assistant", "content": turn.get("ai", "")})
            else:
                messages.append({"role": "user", "content": "Previous summary:"})
                messages.append({"role": "assistant", "content": summary_text})
        except json.JSONDecodeError:
            messages.append({"role": "user", "content": "Previous summary:"})
            messages.append({"role": "assistant", "content": summary_text})
    else:
        messages.append({"role": "user", "content": "Previous summary:"})
        messages.append({"role": "assistant", "content": "Fresh chat started."})

    return {"messages": messages, "summary_text": summary_text}
