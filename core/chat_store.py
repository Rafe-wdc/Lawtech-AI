"""SQLite-backed chat history store.

Thread-safe, zero external dependencies (sqlite3 is in Python stdlib).
DB location controlled by CHAT_HISTORY_DB_PATH setting.

Usage:
    from core.chat_store import chat_store
    result = await chat_store.load_history("thread-123")
    await chat_store.save_turn("thread-123", "user query", "ai response")
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Optional

from langchain.messages import HumanMessage, AIMessage
from langchain_core.messages import BaseMessage

from core.settings import CHAT_HISTORY_DB_PATH
from core.logger import get_logger

log = get_logger("ChatStore")


# --- Return type ---

@dataclass
class ChatHistoryResult:
    """What the memory agent needs from the store."""
    chat_history: list[BaseMessage] = field(default_factory=list)
    summary_text: str = ""
    total_turns: int = 0
    raw_turns: list[dict] = field(default_factory=list)


# --- Rolling summary prompt ---

_SUMMARY_PROMPT = """You are a legal conversation summarizer. Given an existing summary and new conversation turns, produce an updated summary.

Preserve ALL legally significant details in these categories:
1. **Topics Discussed** — main legal topics and subtopics
2. **Legal References** — all statutes, sections, act names, case citations mentioned
3. **Named Entities** — persons, courts, institutions, parties
4. **Key Insights** — important advice, findings, conclusions

Existing Summary:
{existing_summary}

New Conversation Turns:
{new_turns}

Updated Summary (concise, structured, preserve every legal detail):"""


# --- Store class ---

class ChatHistoryStore:
    """Thread-safe SQLite chat history store.

    SQLite in WAL mode supports concurrent reads + single writer.
    Uses threading.Lock for writes and asyncio.to_thread() for async wrappers.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._write_lock = threading.Lock()
        self._initialized = False

    def _get_connection(self) -> sqlite3.Connection:
        """Create a new short-lived connection with WAL mode."""
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _ensure_schema(self) -> None:
        """Create tables and indexes if they don't exist (idempotent)."""
        if self._initialized:
            return

        # Ensure directory exists
        db_dir = os.path.dirname(self._db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        conn = self._get_connection()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS threads (
                    thread_id         TEXT PRIMARY KEY,
                    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
                    summary_text      TEXT NOT NULL DEFAULT '',
                    summary_turn_count INTEGER NOT NULL DEFAULT 0,
                    total_turns       INTEGER NOT NULL DEFAULT 0,
                    file_context_json TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id   TEXT NOT NULL REFERENCES threads(thread_id),
                    turn_number INTEGER NOT NULL,
                    user_query  TEXT NOT NULL,
                    ai_response TEXT NOT NULL,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_messages_thread_turn
                    ON messages(thread_id, turn_number);

                CREATE TABLE IF NOT EXISTS feedback (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id   TEXT NOT NULL,
                    turn_number INTEGER NOT NULL,
                    rating      TEXT NOT NULL CHECK(rating IN ('up', 'down')),
                    comment     TEXT DEFAULT '',
                    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(thread_id, turn_number)
                );

                CREATE INDEX IF NOT EXISTS idx_feedback_thread
                    ON feedback(thread_id, turn_number);

                CREATE TABLE IF NOT EXISTS draft_continuations (
                    thread_id   TEXT PRIMARY KEY,
                    data_json   TEXT NOT NULL,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
                );

            """)
            conn.commit()

            # Migration: add file_context_json if upgrading from older schema
            cols = [r[1] for r in conn.execute("PRAGMA table_info(threads)").fetchall()]
            if "file_context_json" not in cols:
                conn.execute("ALTER TABLE threads ADD COLUMN file_context_json TEXT NOT NULL DEFAULT ''")
                conn.commit()
                log.info("Migrated threads table: added file_context_json")

            # Migration: create thread_files if not present (executescript cannot use
            # datetime('now') as DEFAULT on all SQLite versions, so we create it here)
            existing_tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "thread_files" not in existing_tables:
                conn.execute("""
                    CREATE TABLE thread_files (
                        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                        thread_id            TEXT    NOT NULL REFERENCES threads(thread_id),
                        file_id              TEXT    NOT NULL,
                        filename             TEXT    NOT NULL,
                        file_type            TEXT    NOT NULL DEFAULT '',
                        mime_type            TEXT    NOT NULL DEFAULT '',
                        size_bytes           INTEGER NOT NULL DEFAULT 0,
                        local_path           TEXT    NOT NULL DEFAULT '',
                        extracted_text       TEXT    NOT NULL DEFAULT '',
                        chromadb_collection  TEXT    NOT NULL DEFAULT '',
                        gemini_uri           TEXT    NOT NULL DEFAULT '',
                        gemini_name          TEXT    NOT NULL DEFAULT '',
                        gemini_expiry        TEXT    NOT NULL DEFAULT '',
                        gemini_supported     INTEGER NOT NULL DEFAULT 0,
                        page_count           INTEGER NOT NULL DEFAULT 0,
                        upload_error         TEXT    NOT NULL DEFAULT '',
                        created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
                        UNIQUE(thread_id, file_id)
                    )
                """)
                conn.execute(
                    "CREATE INDEX idx_thread_files_thread ON thread_files(thread_id, created_at DESC)"
                )
                conn.commit()
                log.info("Migrated: created thread_files table")

            self._initialized = True
            log.info("Schema initialized", db_path=self._db_path)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Read operations (no lock needed — WAL allows concurrent reads)
    # ------------------------------------------------------------------

    def _load_history_sync(
        self,
        thread_id: str,
        max_recent_turns: int = 5,
    ) -> ChatHistoryResult:
        """Load chat history for a thread from SQLite."""
        self._ensure_schema()
        conn = self._get_connection()
        try:
            thread_row = conn.execute(
                "SELECT summary_text, total_turns FROM threads WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()

            if not thread_row:
                return ChatHistoryResult()

            # Get recent messages (newest first, then reverse)
            rows = conn.execute("""
                SELECT user_query, ai_response, turn_number
                FROM messages
                WHERE thread_id = ?
                ORDER BY turn_number DESC
                LIMIT ?
            """, (thread_id, max_recent_turns)).fetchall()

            rows = list(reversed(rows))  # oldest first

            chat_history: list[BaseMessage] = []
            raw_turns = []
            for row in rows:
                chat_history.append(HumanMessage(content=row["user_query"]))
                chat_history.append(AIMessage(content=row["ai_response"]))
                raw_turns.append({
                    "user_query": row["user_query"],
                    "ai_response": row["ai_response"],
                    "turn_number": row["turn_number"],
                })

            return ChatHistoryResult(
                chat_history=chat_history,
                summary_text=thread_row["summary_text"],
                total_turns=thread_row["total_turns"],
                raw_turns=raw_turns,
            )
        finally:
            conn.close()

    async def load_history(
        self,
        thread_id: str,
        max_recent_turns: int = 5,
    ) -> ChatHistoryResult:
        """Async wrapper — runs SQLite read in thread pool."""
        return await asyncio.to_thread(
            self._load_history_sync, thread_id, max_recent_turns
        )

    # ------------------------------------------------------------------
    # Write operations (lock-protected)
    # ------------------------------------------------------------------

    def _save_turn_sync(
        self,
        thread_id: str,
        user_query: str,
        ai_response: str,
    ) -> int:
        """Save a Q&A turn. Returns the new turn_number."""
        self._ensure_schema()

        # Step 1: Save turn inside write lock
        with self._write_lock:
            conn = self._get_connection()
            try:
                # Upsert thread
                conn.execute("""
                    INSERT INTO threads (thread_id, total_turns)
                    VALUES (?, 0)
                    ON CONFLICT(thread_id) DO NOTHING
                """, (thread_id,))

                # Get current turn count
                row = conn.execute(
                    "SELECT total_turns FROM threads WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
                new_turn = (row["total_turns"] if row else 0) + 1

                # Insert message
                conn.execute("""
                    INSERT INTO messages (thread_id, turn_number, user_query, ai_response)
                    VALUES (?, ?, ?, ?)
                """, (thread_id, new_turn, user_query, ai_response))

                # Update thread metadata
                conn.execute("""
                    UPDATE threads
                    SET total_turns = ?, updated_at = datetime('now')
                    WHERE thread_id = ?
                """, (new_turn, thread_id))

                conn.commit()
                log.debug("Turn saved",
                          thread_id=thread_id[:12], turn=new_turn)
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        # Step 2: Summary regeneration runs OUTSIDE the write lock so it
        # doesn't block other writes during the LLM call (~1-3 seconds).
        try:
            conn = self._get_connection()
            try:
                self._maybe_regenerate_summary_sync(thread_id, conn)
            finally:
                conn.close()
        except Exception as e:
            log.error("Post-save summary regeneration failed",
                      thread_id=thread_id[:12], error=str(e))

        return new_turn

    async def save_turn(
        self,
        thread_id: str,
        user_query: str,
        ai_response: str,
    ) -> int:
        """Async wrapper — runs SQLite write in thread pool."""
        return await asyncio.to_thread(
            self._save_turn_sync, thread_id, user_query, ai_response
        )

    # ------------------------------------------------------------------
    # Summary management
    # ------------------------------------------------------------------

    def _maybe_regenerate_summary_sync(
        self,
        thread_id: str,
        conn: sqlite3.Connection,
    ) -> None:
        """Regenerate rolling summary if enough new turns accumulated.

        Policy: regenerate when (total_turns - summary_turn_count) >= 3.
        """
        row = conn.execute(
            "SELECT total_turns, summary_turn_count, summary_text "
            "FROM threads WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()

        if not row:
            return

        gap = row["total_turns"] - row["summary_turn_count"]
        if gap < 3:
            return

        log.debug("Summary regeneration triggered",
                  thread_id=thread_id[:12], gap=gap,
                  total=row["total_turns"])

        try:
            # Load unsummarized turns
            unsummarized = conn.execute("""
                SELECT user_query, ai_response, turn_number
                FROM messages
                WHERE thread_id = ? AND turn_number > ?
                ORDER BY turn_number ASC
            """, (thread_id, row["summary_turn_count"])).fetchall()

            new_turns_text = "\n".join(
                f"Turn {r['turn_number']}:\n  User: {r['user_query']}\n  AI: {r['ai_response'][:300]}"
                for r in unsummarized
            )

            existing = row["summary_text"] or "No previous summary."

            # Generate summary using Gemini Flash Lite
            from core.clients import get_gemini_flash as get_gemini_flash_lite
            from langchain_core.prompts import ChatPromptTemplate

            llm = get_gemini_flash_lite(temperature=0.1)
            prompt = ChatPromptTemplate.from_template(_SUMMARY_PROMPT)
            chain = prompt | llm
            result = chain.invoke({
                "existing_summary": existing,
                "new_turns": new_turns_text,
            })

            new_summary = result.content.strip()
            if new_summary and len(new_summary) > 10:
                conn.execute("""
                    UPDATE threads
                    SET summary_text = ?, summary_turn_count = ?
                    WHERE thread_id = ?
                """, (new_summary, row["total_turns"], thread_id))
                conn.commit()
                log.info("Summary regenerated",
                         thread_id=thread_id[:12],
                         summary_len=len(new_summary),
                         turns_covered=row["total_turns"])
            else:
                log.warning("Summary generation returned empty result")

        except Exception as e:
            log.error("Summary regeneration failed",
                      thread_id=thread_id[:12], error=str(e))

    # ------------------------------------------------------------------
    # Draft continuation (incomplete draft metadata)
    # ------------------------------------------------------------------

    _DRAFT_SCHEMA_VERSION = 1

    def _save_draft_continuation_sync(self, thread_id: str, data: dict) -> None:
        """Save or update draft continuation data for a thread."""
        self._ensure_schema()
        versioned = {**data, "_schema_version": self._DRAFT_SCHEMA_VERSION}
        with self._write_lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    INSERT INTO draft_continuations (thread_id, data_json)
                    VALUES (?, ?)
                    ON CONFLICT(thread_id) DO UPDATE SET
                        data_json = excluded.data_json,
                        created_at = datetime('now')
                """, (thread_id, json.dumps(versioned)))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    async def save_draft_continuation(self, thread_id: str, data: dict) -> None:
        return await asyncio.to_thread(
            self._save_draft_continuation_sync, thread_id, data
        )

    def _load_draft_continuation_sync(self, thread_id: str) -> dict | None:
        """Load draft continuation data for a thread."""
        self._ensure_schema()
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT data_json FROM draft_continuations WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            if row:
                try:
                    data = json.loads(row["data_json"])
                    version = data.get("_schema_version", 0)
                    if version != self._DRAFT_SCHEMA_VERSION:
                        log.warning("Draft continuation schema version mismatch, discarding",
                                    thread_id=thread_id,
                                    stored_version=version,
                                    expected=self._DRAFT_SCHEMA_VERSION)
                        return None
                    return data
                except (json.JSONDecodeError, TypeError) as e:
                    log.error("Corrupted draft continuation JSON",
                              thread_id=thread_id, error=str(e))
                    return None
            return None
        finally:
            conn.close()

    async def load_draft_continuation(self, thread_id: str) -> dict | None:
        return await asyncio.to_thread(
            self._load_draft_continuation_sync, thread_id
        )

    def _clear_draft_continuation_sync(self, thread_id: str) -> None:
        """Remove draft continuation data after successful completion."""
        self._ensure_schema()
        with self._write_lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "DELETE FROM draft_continuations WHERE thread_id = ?",
                    (thread_id,),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    async def clear_draft_continuation(self, thread_id: str) -> None:
        return await asyncio.to_thread(
            self._clear_draft_continuation_sync, thread_id
        )

    # ------------------------------------------------------------------
    # Thread Files Registry (ChatGPT-style per-thread file persistence)
    # ------------------------------------------------------------------

    def _save_thread_file_sync(self, thread_id: str, pf) -> None:
        """Insert or update a file record in thread_files."""
        self._ensure_schema()
        with self._write_lock:
            conn = self._get_connection()
            try:
                # Ensure thread row exists before inserting a file
                conn.execute("""
                    INSERT INTO threads (thread_id, total_turns)
                    VALUES (?, 0)
                    ON CONFLICT(thread_id) DO NOTHING
                """, (thread_id,))
                conn.execute("""
                    INSERT INTO thread_files (
                        thread_id, file_id, filename, file_type, mime_type,
                        size_bytes, local_path, extracted_text,
                        chromadb_collection, gemini_uri, gemini_name,
                        gemini_expiry, gemini_supported, page_count, upload_error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(thread_id, file_id) DO UPDATE SET
                        gemini_uri    = excluded.gemini_uri,
                        gemini_name   = excluded.gemini_name,
                        gemini_expiry = excluded.gemini_expiry,
                        upload_error  = excluded.upload_error
                """, (
                    thread_id,
                    getattr(pf, "file_id", ""),
                    getattr(pf, "original_name", ""),
                    getattr(pf, "file_type", ""),
                    getattr(pf, "mime_type", ""),
                    getattr(pf, "size_bytes", 0),
                    getattr(pf, "local_path", ""),
                    getattr(pf, "extracted_text", ""),
                    getattr(pf, "chromadb_collection", ""),
                    getattr(pf, "gemini_uri", ""),
                    getattr(pf, "gemini_name", ""),
                    getattr(pf, "gemini_expiry", ""),
                    int(getattr(pf, "gemini_supported", False)),
                    getattr(pf, "page_count", 0),
                    getattr(pf, "error", "") or "",
                ))
                conn.commit()
                log.debug("Thread file saved",
                          thread_id=thread_id[:12],
                          file=getattr(pf, "original_name", ""),
                          gemini=bool(getattr(pf, "gemini_uri", "")))
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    async def save_thread_file(self, thread_id: str, pf) -> None:
        """Async wrapper for saving a thread file record."""
        return await asyncio.to_thread(self._save_thread_file_sync, thread_id, pf)

    def _load_thread_files_sync(self, thread_id: str) -> list[dict]:
        """Load all file records for a thread, oldest first."""
        self._ensure_schema()
        conn = self._get_connection()
        try:
            rows = conn.execute("""
                SELECT file_id, filename, file_type, mime_type, size_bytes,
                       local_path, extracted_text, chromadb_collection,
                       gemini_uri, gemini_name, gemini_expiry,
                       gemini_supported, page_count, upload_error
                FROM thread_files
                WHERE thread_id = ?
                ORDER BY created_at ASC
            """, (thread_id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    async def load_thread_files(self, thread_id: str) -> list[dict]:
        """Async wrapper for loading all thread file records."""
        return await asyncio.to_thread(self._load_thread_files_sync, thread_id)

    def _update_gemini_uri_sync(
        self,
        thread_id: str,
        file_id: str,
        new_uri: str,
        new_name: str,
        new_expiry: str,
    ) -> None:
        """Update Gemini URI/name/expiry after a re-upload."""
        self._ensure_schema()
        with self._write_lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    UPDATE thread_files
                    SET gemini_uri = ?, gemini_name = ?, gemini_expiry = ?
                    WHERE thread_id = ? AND file_id = ?
                """, (new_uri, new_name, new_expiry, thread_id, file_id))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    async def update_gemini_uri(
        self,
        thread_id: str,
        file_id: str,
        new_uri: str,
        new_name: str,
        new_expiry: str,
    ) -> None:
        """Async wrapper for updating Gemini URI after re-upload."""
        return await asyncio.to_thread(
            self._update_gemini_uri_sync, thread_id, file_id, new_uri, new_name, new_expiry
        )

    def _get_thread_storage_bytes_sync(self, thread_id: str) -> tuple[int, int]:
        """Return (file_count, total_bytes) for a thread."""
        self._ensure_schema()
        conn = self._get_connection()
        try:
            row = conn.execute("""
                SELECT COUNT(*) as cnt, COALESCE(SUM(size_bytes), 0) as total
                FROM thread_files WHERE thread_id = ?
            """, (thread_id,)).fetchone()
            return row["cnt"], row["total"]
        finally:
            conn.close()

    async def get_thread_storage(self, thread_id: str) -> tuple[int, int]:
        """Async wrapper — returns (file_count, total_bytes) for a thread."""
        return await asyncio.to_thread(self._get_thread_storage_bytes_sync, thread_id)

    # ------------------------------------------------------------------
    # File Context Persistence (for multi-turn file memory)
    # ------------------------------------------------------------------

    def _save_file_context_sync(self, thread_id: str, file_context: dict) -> None:
        """Persist file context for a thread (inline_text, chromadb_collections, file_names).

        Images are NOT persisted — they are too large (base64) and cannot be
        meaningfully re-used without the original bytes in a follow-up.
        """
        self._ensure_schema()
        # Strip images before saving
        safe_ctx = {
            "inline_text": file_context.get("inline_text", ""),
            "chromadb_collections": file_context.get("chromadb_collections", []),
            "file_names": file_context.get("file_names", []),
            "image_data": [],  # intentionally empty — images not persisted
        }
        with self._write_lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    INSERT INTO threads (thread_id, file_context_json)
                    VALUES (?, ?)
                    ON CONFLICT(thread_id) DO UPDATE SET
                        file_context_json = excluded.file_context_json,
                        updated_at = datetime('now')
                """, (thread_id, json.dumps(safe_ctx)))
                conn.commit()
                log.debug("File context saved",
                          thread_id=thread_id[:12],
                          files=safe_ctx["file_names"],
                          chromadb=len(safe_ctx["chromadb_collections"]),
                          inline_chars=len(safe_ctx["inline_text"]))
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    async def save_file_context(self, thread_id: str, file_context: dict) -> None:
        """Async wrapper for saving file context."""
        return await asyncio.to_thread(
            self._save_file_context_sync, thread_id, file_context
        )

    def _load_file_context_sync(self, thread_id: str) -> dict | None:
        """Load persisted file context for a thread, or None if absent."""
        self._ensure_schema()
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT file_context_json FROM threads WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            if row and row["file_context_json"]:
                try:
                    ctx = json.loads(row["file_context_json"])
                    # Only return if it has meaningful content
                    if ctx.get("inline_text") or ctx.get("chromadb_collections") or ctx.get("file_names"):
                        return ctx
                except (json.JSONDecodeError, TypeError) as e:
                    log.error("Corrupted file_context_json",
                              thread_id=thread_id, error=str(e))
            return None
        finally:
            conn.close()

    async def load_file_context(self, thread_id: str) -> dict | None:
        """Async wrapper for loading file context."""
        return await asyncio.to_thread(
            self._load_file_context_sync, thread_id
        )

    # ------------------------------------------------------------------
    # List Threads (for session history sidebar)
    # ------------------------------------------------------------------

    def _list_threads_sync(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """List recent threads with their first message preview.

        Returns list of dicts:
          {thread_id, created_at, updated_at, total_turns, preview, summary_text}
        Ordered by most recently updated first.
        """
        self._ensure_schema()
        conn = self._get_connection()
        try:
            rows = conn.execute("""
                SELECT
                    t.thread_id,
                    t.created_at,
                    t.updated_at,
                    t.total_turns,
                    t.summary_text,
                    (SELECT m.user_query FROM messages m
                     WHERE m.thread_id = t.thread_id
                     ORDER BY m.turn_number ASC LIMIT 1
                    ) AS first_query
                FROM threads t
                WHERE t.total_turns > 0
                ORDER BY t.updated_at DESC
                LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()

            return [
                {
                    "thread_id": r["thread_id"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "total_turns": r["total_turns"],
                    "preview": (r["first_query"] or "")[:120],
                    "summary_text": (r["summary_text"] or "")[:300],
                }
                for r in rows
            ]
        finally:
            conn.close()

    async def list_threads(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Async wrapper for list_threads."""
        return await asyncio.to_thread(
            self._list_threads_sync, limit, offset
        )

    def _load_thread_messages_sync(
        self,
        thread_id: str,
        limit: int = 50,
    ) -> list[dict]:
        """Load all messages for a thread (for session restore).

        Returns list of {turn_number, user_query, ai_response, created_at}.
        """
        self._ensure_schema()
        conn = self._get_connection()
        try:
            rows = conn.execute("""
                SELECT turn_number, user_query, ai_response, created_at
                FROM messages
                WHERE thread_id = ?
                ORDER BY turn_number ASC
                LIMIT ?
            """, (thread_id, limit)).fetchall()

            return [
                {
                    "turn_number": r["turn_number"],
                    "user_query": r["user_query"],
                    "ai_response": r["ai_response"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
        finally:
            conn.close()

    async def load_thread_messages(
        self,
        thread_id: str,
        limit: int = 50,
    ) -> list[dict]:
        """Async wrapper for load_thread_messages."""
        return await asyncio.to_thread(
            self._load_thread_messages_sync, thread_id, limit
        )

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    def _save_feedback_sync(
        self,
        thread_id: str,
        turn_number: int,
        rating: str,
        comment: str = "",
    ) -> bool:
        """Save or update feedback for a specific message (upsert)."""
        self._ensure_schema()
        with self._write_lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    INSERT INTO feedback (thread_id, turn_number, rating, comment)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(thread_id, turn_number) DO UPDATE SET
                        rating = excluded.rating,
                        comment = excluded.comment,
                        created_at = datetime('now')
                """, (thread_id, turn_number, rating, comment))
                conn.commit()
                log.info("Feedback saved",
                         thread_id=thread_id[:12], turn=turn_number,
                         rating=rating)
                return True
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    async def save_feedback(
        self,
        thread_id: str,
        turn_number: int,
        rating: str,
        comment: str = "",
    ) -> bool:
        """Async wrapper for feedback save."""
        return await asyncio.to_thread(
            self._save_feedback_sync, thread_id, turn_number, rating, comment
        )


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

chat_store = ChatHistoryStore(CHAT_HISTORY_DB_PATH)
