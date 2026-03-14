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

from core.settings import CHAT_HISTORY_DB_PATH, POSTGRES_URL
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

class _SqliteChatHistoryStore:
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

                CREATE TABLE IF NOT EXISTS fallback_log (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT    NOT NULL DEFAULT (datetime('now')),
                    agent           TEXT    NOT NULL,
                    query           TEXT    NOT NULL,
                    original_query  TEXT    NOT NULL DEFAULT '',
                    fallback_tier   TEXT    NOT NULL DEFAULT 'web',
                    response_preview TEXT   NOT NULL DEFAULT '',
                    web_sources_json TEXT   NOT NULL DEFAULT '[]',
                    tokens          INTEGER NOT NULL DEFAULT 0,
                    backfilled      INTEGER NOT NULL DEFAULT 0,
                    backfill_date   TEXT    DEFAULT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_fallback_agent_ts
                    ON fallback_log(agent, timestamp DESC);

                CREATE INDEX IF NOT EXISTS idx_fallback_backfilled
                    ON fallback_log(backfilled, agent);

                CREATE TABLE IF NOT EXISTS request_log (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp           TEXT    NOT NULL DEFAULT (datetime('now')),
                    thread_id           TEXT    NOT NULL DEFAULT '',
                    endpoint            TEXT    NOT NULL DEFAULT '',
                    query_preview       TEXT    NOT NULL DEFAULT '',
                    user_language       TEXT    NOT NULL DEFAULT 'en',
                    tasks_planned_json  TEXT    NOT NULL DEFAULT '[]',
                    agents_used_json    TEXT    NOT NULL DEFAULT '[]',
                    total_latency_ms    INTEGER NOT NULL DEFAULT 0,
                    total_tokens        INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_usd  REAL    NOT NULL DEFAULT 0.0,
                    fallback_used       INTEGER NOT NULL DEFAULT 0,
                    is_blocked          INTEGER NOT NULL DEFAULT 0,
                    error               TEXT    DEFAULT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_request_log_ts
                    ON request_log(timestamp DESC);

                CREATE INDEX IF NOT EXISTS idx_request_log_thread
                    ON request_log(thread_id, timestamp DESC);

                CREATE TABLE IF NOT EXISTS quality_log (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp        TEXT    NOT NULL DEFAULT (datetime('now')),
                    request_log_id   INTEGER DEFAULT NULL,
                    thread_id        TEXT    NOT NULL DEFAULT '',
                    agent            TEXT    NOT NULL DEFAULT '',
                    query_preview    TEXT    NOT NULL DEFAULT '',
                    faithfulness     REAL    NOT NULL DEFAULT 0.0,
                    relevance        REAL    NOT NULL DEFAULT 0.0,
                    completeness     REAL    NOT NULL DEFAULT 0.0,
                    avg_score        REAL    NOT NULL DEFAULT 0.0,
                    model_used       TEXT    NOT NULL DEFAULT 'gemini-2.5-flash-lite'
                );

                CREATE INDEX IF NOT EXISTS idx_quality_log_ts
                    ON quality_log(timestamp DESC);

                CREATE INDEX IF NOT EXISTS idx_quality_log_agent
                    ON quality_log(agent, timestamp DESC);

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
                        ocr_status           TEXT    NOT NULL DEFAULT '',
                        created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
                        UNIQUE(thread_id, file_id)
                    )
                """)
                conn.execute(
                    "CREATE INDEX idx_thread_files_thread ON thread_files(thread_id, created_at DESC)"
                )
                conn.commit()
                log.info("Migrated: created thread_files table")

            # Migration: add ocr_status to thread_files for background OCR tracking
            if "thread_files" in existing_tables:
                tf_cols = [r[1] for r in conn.execute("PRAGMA table_info(thread_files)").fetchall()]
                if "ocr_status" not in tf_cols:
                    conn.execute(
                        "ALTER TABLE thread_files ADD COLUMN ocr_status TEXT NOT NULL DEFAULT ''"
                    )
                    conn.commit()
                    log.info("Migrated thread_files: added ocr_status column")

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
                       gemini_supported, page_count, upload_error, ocr_status
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

    def _update_ocr_status_sync(
        self, thread_id: str, file_id: str, status: str, error: str = "",
    ) -> None:
        """Update ocr_status (and optionally upload_error) for a thread file."""
        self._ensure_schema()
        with self._write_lock:
            conn = self._get_connection()
            try:
                if error:
                    conn.execute("""
                        UPDATE thread_files
                        SET ocr_status = ?, upload_error = ?
                        WHERE thread_id = ? AND file_id = ?
                    """, (status, error, thread_id, file_id))
                else:
                    conn.execute("""
                        UPDATE thread_files SET ocr_status = ?
                        WHERE thread_id = ? AND file_id = ?
                    """, (status, thread_id, file_id))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    async def update_ocr_status(
        self, thread_id: str, file_id: str, status: str, error: str = "",
    ) -> None:
        """Async wrapper for updating background OCR status."""
        return await asyncio.to_thread(
            self._update_ocr_status_sync, thread_id, file_id, status, error
        )

    def _delete_thread_files_sync(self, thread_id: str) -> list[dict]:
        """Delete all file records for a thread. Returns deleted records for cleanup."""
        self._ensure_schema()
        records = self._load_thread_files_sync(thread_id)
        if not records:
            return []
        with self._write_lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM thread_files WHERE thread_id = ?", (thread_id,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return records

    async def delete_thread_files(self, thread_id: str) -> list[dict]:
        """Async wrapper — deletes all file records, returns them for external cleanup."""
        return await asyncio.to_thread(self._delete_thread_files_sync, thread_id)

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
    # Fallback Log (web search fallback tracking for ES backfill)
    # ------------------------------------------------------------------

    def _log_fallback_sync(
        self,
        agent: str,
        query: str,
        original_query: str,
        response_preview: str,
        web_sources: list[str],
        tokens: int,
        fallback_tier: str = "web",
    ) -> int:
        """Insert a fallback log entry. Returns the new row id."""
        self._ensure_schema()
        with self._write_lock:
            conn = self._get_connection()
            try:
                cur = conn.execute("""
                    INSERT INTO fallback_log
                        (agent, query, original_query, fallback_tier,
                         response_preview, web_sources_json, tokens)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    agent,
                    query[:1000],
                    original_query[:1000],
                    fallback_tier,
                    response_preview[:600],
                    json.dumps(web_sources),
                    tokens,
                ))
                conn.commit()
                row_id = cur.lastrowid
                log.debug("Fallback logged",
                          agent=agent, tier=fallback_tier, row_id=row_id)
                return row_id
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    async def log_fallback(
        self,
        agent: str,
        query: str,
        original_query: str,
        response_preview: str,
        web_sources: list[str],
        tokens: int,
        fallback_tier: str = "web",
    ) -> int:
        """Async wrapper — log a fallback event without blocking the response."""
        return await asyncio.to_thread(
            self._log_fallback_sync,
            agent, query, original_query, response_preview,
            web_sources, tokens, fallback_tier,
        )

    def _get_fallback_logs_sync(
        self,
        agent: str | None = None,
        backfilled: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Query fallback_log with optional filters."""
        self._ensure_schema()
        conn = self._get_connection()
        try:
            conditions = []
            params: list = []
            if agent:
                conditions.append("agent = ?")
                params.append(agent)
            if backfilled is not None:
                conditions.append("backfilled = ?")
                params.append(int(backfilled))
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            rows = conn.execute(f"""
                SELECT id, timestamp, agent, query, original_query,
                       fallback_tier, response_preview, web_sources_json,
                       tokens, backfilled, backfill_date
                FROM fallback_log
                {where}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset]).fetchall()

            total_row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM fallback_log {where}", params
            ).fetchone()

            return {
                "total": total_row["cnt"],
                "limit": limit,
                "offset": offset,
                "items": [
                    {
                        "id": r["id"],
                        "timestamp": r["timestamp"],
                        "agent": r["agent"],
                        "query": r["query"],
                        "original_query": r["original_query"],
                        "fallback_tier": r["fallback_tier"],
                        "response_preview": r["response_preview"],
                        "web_sources": json.loads(r["web_sources_json"] or "[]"),
                        "tokens": r["tokens"],
                        "backfilled": bool(r["backfilled"]),
                        "backfill_date": r["backfill_date"],
                    }
                    for r in rows
                ],
            }
        finally:
            conn.close()

    async def get_fallback_logs(
        self,
        agent: str | None = None,
        backfilled: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Async wrapper for get_fallback_logs."""
        return await asyncio.to_thread(
            self._get_fallback_logs_sync, agent, backfilled, limit, offset
        )

    def _get_fallback_stats_sync(self) -> dict:
        """Aggregate stats for the fallback log."""
        self._ensure_schema()
        conn = self._get_connection()
        try:
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM fallback_log"
            ).fetchone()["cnt"]

            pending = conn.execute(
                "SELECT COUNT(*) as cnt FROM fallback_log WHERE backfilled = 0"
            ).fetchone()["cnt"]

            by_agent = {
                r["agent"]: r["cnt"]
                for r in conn.execute(
                    "SELECT agent, COUNT(*) as cnt FROM fallback_log "
                    "GROUP BY agent ORDER BY cnt DESC"
                ).fetchall()
            }

            # Last 7 days by date
            by_date = {
                r["day"]: r["cnt"]
                for r in conn.execute(
                    "SELECT strftime('%Y-%m-%d', timestamp) as day, COUNT(*) as cnt "
                    "FROM fallback_log "
                    "WHERE timestamp >= datetime('now', '-7 days') "
                    "GROUP BY day ORDER BY day DESC"
                ).fetchall()
            }

            # Top 10 most repeated queries (by normalized query text)
            top_queries = [
                {"query": r["query"], "count": r["cnt"], "agent": r["agent"]}
                for r in conn.execute(
                    "SELECT query, agent, COUNT(*) as cnt FROM fallback_log "
                    "GROUP BY query, agent ORDER BY cnt DESC LIMIT 10"
                ).fetchall()
            ]

            return {
                "total": total,
                "pending_backfill": pending,
                "backfilled": total - pending,
                "by_agent": by_agent,
                "by_date_last_7d": by_date,
                "top_repeated_queries": top_queries,
            }
        finally:
            conn.close()

    async def get_fallback_stats(self) -> dict:
        """Async wrapper for get_fallback_stats."""
        return await asyncio.to_thread(self._get_fallback_stats_sync)


    # ------------------------------------------------------------------
    # Request Log (per-request usage tracking for L3 observability)
    # ------------------------------------------------------------------

    # LLM cost estimates (blended $/1K tokens per agent group, conservative estimates)
    _AGENT_COST_PER_1K: dict[str, float] = {
        "Non_legal":    0.00015,   # Gemini Flash (cheap)
        "legislation":  0.00150,   # GPT-4o-mini + Gemini Flash
        "judgment":     0.00250,   # GPT-4o metadata + Gemini Flash
        "sci_judgment": 0.00250,   # GPT-4o metadata + Gemini Flash
        "newacts":      0.00150,   # GPT-4o-mini + Gemini Flash
        "constitution": 0.00015,   # Gemini Flash
        "maxim":        0.00015,   # Gemini Flash
        "legal_concepts": 0.00015, # Gemini Flash Lite
        "drafting":     0.00030,   # Gemini Flash
        "scenario":     0.01000,   # Gemini Pro (expensive — web grounded)
        "document":     0.01000,   # Gemini Pro PDF chat
    }
    _DEFAULT_COST_PER_1K = 0.00250  # fallback blended rate

    def _estimate_cost(self, agents_used: list[str], total_tokens: int) -> float:
        """Estimate USD cost based on which agents ran and total token count."""
        if not total_tokens:
            return 0.0
        if not agents_used:
            return round(total_tokens / 1000 * self._DEFAULT_COST_PER_1K, 6)
        # Use highest-cost agent as the dominant rate
        rates = [self._AGENT_COST_PER_1K.get(a, self._DEFAULT_COST_PER_1K) for a in agents_used]
        dominant_rate = max(rates)
        return round(total_tokens / 1000 * dominant_rate, 6)

    def _log_request_sync(
        self,
        thread_id: str,
        endpoint: str,
        query_preview: str,
        user_language: str,
        tasks_planned: list[str],
        agents_used: list[str],
        total_latency_ms: int,
        total_tokens: int,
        fallback_used: bool,
        is_blocked: bool,
        error: str | None = None,
    ) -> int:
        """Insert a request log entry. Returns the new row id."""
        self._ensure_schema()
        estimated_cost = self._estimate_cost(agents_used, total_tokens)
        with self._write_lock:
            conn = self._get_connection()
            try:
                cur = conn.execute("""
                    INSERT INTO request_log
                        (thread_id, endpoint, query_preview, user_language,
                         tasks_planned_json, agents_used_json, total_latency_ms,
                         total_tokens, estimated_cost_usd, fallback_used,
                         is_blocked, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    thread_id,
                    endpoint,
                    query_preview[:300],
                    user_language or "en",
                    json.dumps(tasks_planned),
                    json.dumps(agents_used),
                    total_latency_ms,
                    total_tokens,
                    estimated_cost,
                    int(fallback_used),
                    int(is_blocked),
                    error,
                ))
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    async def log_request(
        self,
        thread_id: str,
        endpoint: str,
        query_preview: str,
        user_language: str,
        tasks_planned: list[str],
        agents_used: list[str],
        total_latency_ms: int,
        total_tokens: int,
        fallback_used: bool = False,
        is_blocked: bool = False,
        error: str | None = None,
    ) -> int:
        """Async wrapper — log a request without blocking the response."""
        return await asyncio.to_thread(
            self._log_request_sync,
            thread_id, endpoint, query_preview, user_language,
            tasks_planned, agents_used, total_latency_ms,
            total_tokens, fallback_used, is_blocked, error,
        )

    def _get_usage_stats_sync(self, days: int = 7) -> dict:
        """Daily and aggregate usage stats from request_log."""
        self._ensure_schema()
        conn = self._get_connection()
        try:
            # Totals
            totals = conn.execute("""
                SELECT
                    COUNT(*)                        as total_requests,
                    SUM(total_tokens)               as total_tokens,
                    ROUND(SUM(estimated_cost_usd), 4) as total_cost_usd,
                    ROUND(AVG(total_latency_ms))    as avg_latency_ms,
                    SUM(fallback_used)              as fallback_count,
                    SUM(is_blocked)                 as blocked_count
                FROM request_log
                WHERE timestamp >= datetime('now', ? || ' days')
            """, (f"-{days}",)).fetchone()

            # Per-day breakdown
            by_day = [
                dict(r) for r in conn.execute("""
                    SELECT
                        strftime('%Y-%m-%d', timestamp)  as date,
                        COUNT(*)                          as requests,
                        SUM(total_tokens)                 as tokens,
                        ROUND(SUM(estimated_cost_usd), 4) as cost_usd,
                        ROUND(AVG(total_latency_ms))      as avg_latency_ms,
                        SUM(fallback_used)                as fallbacks
                    FROM request_log
                    WHERE timestamp >= datetime('now', ? || ' days')
                    GROUP BY date ORDER BY date DESC
                """, (f"-{days}",)).fetchall()
            ]

            # Top agents by invocation count
            top_agents = [
                {"agent": r["agent"], "count": r["cnt"]}
                for r in conn.execute("""
                    SELECT value as agent, COUNT(*) as cnt
                    FROM request_log, json_each(agents_used_json)
                    WHERE timestamp >= datetime('now', ? || ' days')
                    GROUP BY agent ORDER BY cnt DESC LIMIT 10
                """, (f"-{days}",)).fetchall()
            ]

            # Task type distribution
            task_dist = [
                {"task": r["task"], "count": r["cnt"]}
                for r in conn.execute("""
                    SELECT value as task, COUNT(*) as cnt
                    FROM request_log, json_each(tasks_planned_json)
                    WHERE timestamp >= datetime('now', ? || ' days')
                    GROUP BY task ORDER BY cnt DESC
                """, (f"-{days}",)).fetchall()
            ]

            return {
                "period_days": days,
                "totals": dict(totals) if totals else {},
                "by_day": by_day,
                "top_agents": top_agents,
                "task_distribution": task_dist,
            }
        finally:
            conn.close()

    async def get_usage_stats(self, days: int = 7) -> dict:
        """Async wrapper for usage stats."""
        return await asyncio.to_thread(self._get_usage_stats_sync, days)

    # ------------------------------------------------------------------
    # Quality Log (L4 — LLM-as-judge response scoring)
    # ------------------------------------------------------------------

    def _log_quality_sync(
        self,
        request_log_id: int | None,
        thread_id: str,
        agent: str,
        query_preview: str,
        faithfulness: float,
        relevance: float,
        completeness: float,
        avg_score: float,
        model_used: str = "gemini-2.5-flash-lite",
    ) -> int:
        """Insert a quality score entry. Returns new row id."""
        self._ensure_schema()
        with self._write_lock:
            conn = self._get_connection()
            try:
                cur = conn.execute("""
                    INSERT INTO quality_log
                        (request_log_id, thread_id, agent, query_preview,
                         faithfulness, relevance, completeness, avg_score, model_used)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    request_log_id,
                    thread_id,
                    agent,
                    query_preview[:300],
                    faithfulness,
                    relevance,
                    completeness,
                    avg_score,
                    model_used,
                ))
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    async def log_quality(
        self,
        request_log_id: int | None,
        thread_id: str,
        agent: str,
        query_preview: str,
        faithfulness: float,
        relevance: float,
        completeness: float,
        avg_score: float,
        model_used: str = "gemini-2.5-flash-lite",
    ) -> int:
        """Async wrapper — log a quality score entry."""
        return await asyncio.to_thread(
            self._log_quality_sync,
            request_log_id, thread_id, agent, query_preview,
            faithfulness, relevance, completeness, avg_score, model_used,
        )

    def _get_quality_stats_sync(self, days: int = 7) -> dict:
        """Aggregate quality stats from quality_log."""
        self._ensure_schema()
        conn = self._get_connection()
        try:
            # Overall averages
            totals = conn.execute("""
                SELECT
                    COUNT(*)                          as total_scored,
                    ROUND(AVG(avg_score), 4)          as avg_score,
                    ROUND(AVG(faithfulness), 4)       as avg_faithfulness,
                    ROUND(AVG(relevance), 4)          as avg_relevance,
                    ROUND(AVG(completeness), 4)       as avg_completeness,
                    SUM(CASE WHEN avg_score < 0.6 THEN 1 ELSE 0 END) as low_quality_count
                FROM quality_log
                WHERE timestamp >= datetime('now', ? || ' days')
            """, (f"-{days}",)).fetchone()

            # Per-agent averages
            by_agent = [
                dict(r) for r in conn.execute("""
                    SELECT
                        agent,
                        COUNT(*)                    as scored,
                        ROUND(AVG(avg_score), 4)    as avg_score,
                        ROUND(AVG(faithfulness), 4) as avg_faithfulness,
                        ROUND(AVG(relevance), 4)    as avg_relevance
                    FROM quality_log
                    WHERE timestamp >= datetime('now', ? || ' days')
                    GROUP BY agent ORDER BY avg_score ASC
                """, (f"-{days}",)).fetchall()
            ]

            # Daily trend
            by_day = [
                dict(r) for r in conn.execute("""
                    SELECT
                        strftime('%Y-%m-%d', timestamp)  as date,
                        COUNT(*)                          as scored,
                        ROUND(AVG(avg_score), 4)          as avg_score,
                        ROUND(AVG(faithfulness), 4)       as avg_faithfulness
                    FROM quality_log
                    WHERE timestamp >= datetime('now', ? || ' days')
                    GROUP BY date ORDER BY date DESC
                """, (f"-{days}",)).fetchall()
            ]

            # Lowest scoring recent responses (for review queue)
            low_quality = [
                dict(r) for r in conn.execute("""
                    SELECT timestamp, agent, query_preview, avg_score,
                           faithfulness, relevance, completeness
                    FROM quality_log
                    WHERE avg_score < 0.6
                      AND timestamp >= datetime('now', ? || ' days')
                    ORDER BY avg_score ASC LIMIT 10
                """, (f"-{days}",)).fetchall()
            ]

            return {
                "period_days": days,
                "totals": dict(totals) if totals else {},
                "by_agent": by_agent,
                "by_day": by_day,
                "low_quality_responses": low_quality,
            }
        finally:
            conn.close()

    async def get_quality_stats(self, days: int = 7) -> dict:
        """Async wrapper for quality stats."""
        return await asyncio.to_thread(self._get_quality_stats_sync, days)


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------


# ------------------------------------------------------------------
# PostgreSQL-backed store (multi-worker safe)
# ------------------------------------------------------------------

class _PostgresChatHistoryStore:
    """PostgreSQL-backed chat history store.

    Uses psycopg3 (sync) with a ConnectionPool.  Safe for multiple
    gunicorn/uvicorn workers because PostgreSQL handles concurrent writes
    natively — no threading.Lock required.

    Public API is identical to _SqliteChatHistoryStore.
    """

    _DRAFT_SCHEMA_VERSION = 1

    # LLM cost estimates shared with SQLite class
    _AGENT_COST_PER_1K: dict[str, float] = {
        "Non_legal":      0.00015,
        "legislation":    0.00150,
        "judgment":       0.00250,
        "sci_judgment":   0.00250,
        "newacts":        0.00150,
        "constitution":   0.00015,
        "maxim":          0.00015,
        "legal_concepts": 0.00015,
        "drafting":       0.00030,
        "scenario":       0.01000,
        "document":       0.01000,
    }
    _DEFAULT_COST_PER_1K = 0.00250

    def __init__(self, postgres_url: str):
        self._postgres_url = postgres_url
        self._pool = None
        self._pool_init_lock = threading.Lock()
        self._schema_ok = False
        self._schema_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_pool(self):
        if self._pool is None:
            with self._pool_init_lock:
                if self._pool is None:
                    from psycopg_pool import ConnectionPool
                    p = ConnectionPool(
                        conninfo=self._postgres_url,
                        min_size=2,
                        max_size=10,
                        open=True,
                        timeout=10.0,
                    )
                    self._pool = p
                    log.info("PostgreSQL chat-store pool initialised")
        return self._pool

    def _ensure_schema(self) -> None:
        if self._schema_ok:
            return
        with self._schema_lock:
            if self._schema_ok:
                return
            from psycopg.rows import dict_row
            with self._get_pool().connection() as conn:
                conn.row_factory = dict_row
                stmts = [
                    """CREATE TABLE IF NOT EXISTS threads (
                        thread_id          TEXT PRIMARY KEY,
                        created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        summary_text       TEXT NOT NULL DEFAULT '',
                        summary_turn_count INTEGER NOT NULL DEFAULT 0,
                        total_turns        INTEGER NOT NULL DEFAULT 0,
                        file_context_json  TEXT NOT NULL DEFAULT ''
                    )""",
                    """CREATE TABLE IF NOT EXISTS messages (
                        id          BIGSERIAL PRIMARY KEY,
                        thread_id   TEXT NOT NULL REFERENCES threads(thread_id),
                        turn_number INTEGER NOT NULL,
                        user_query  TEXT NOT NULL,
                        ai_response TEXT NOT NULL,
                        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )""",
                    "CREATE INDEX IF NOT EXISTS idx_messages_thread_turn ON messages(thread_id, turn_number)",
                    """CREATE TABLE IF NOT EXISTS feedback (
                        id          BIGSERIAL PRIMARY KEY,
                        thread_id   TEXT NOT NULL,
                        turn_number INTEGER NOT NULL,
                        rating      TEXT NOT NULL CHECK(rating IN ('up', 'down')),
                        comment     TEXT DEFAULT '',
                        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE(thread_id, turn_number)
                    )""",
                    "CREATE INDEX IF NOT EXISTS idx_feedback_thread ON feedback(thread_id, turn_number)",
                    """CREATE TABLE IF NOT EXISTS draft_continuations (
                        thread_id  TEXT PRIMARY KEY,
                        data_json  TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )""",
                    """CREATE TABLE IF NOT EXISTS fallback_log (
                        id               BIGSERIAL PRIMARY KEY,
                        timestamp        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        agent            TEXT NOT NULL,
                        query            TEXT NOT NULL,
                        original_query   TEXT NOT NULL DEFAULT '',
                        fallback_tier    TEXT NOT NULL DEFAULT 'web',
                        response_preview TEXT NOT NULL DEFAULT '',
                        web_sources_json TEXT NOT NULL DEFAULT '[]',
                        tokens           INTEGER NOT NULL DEFAULT 0,
                        backfilled       INTEGER NOT NULL DEFAULT 0,
                        backfill_date    TIMESTAMPTZ DEFAULT NULL
                    )""",
                    "CREATE INDEX IF NOT EXISTS idx_fallback_agent_ts ON fallback_log(agent, timestamp DESC)",
                    "CREATE INDEX IF NOT EXISTS idx_fallback_backfilled ON fallback_log(backfilled, agent)",
                    """CREATE TABLE IF NOT EXISTS request_log (
                        id                 BIGSERIAL PRIMARY KEY,
                        timestamp          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        thread_id          TEXT NOT NULL DEFAULT '',
                        endpoint           TEXT NOT NULL DEFAULT '',
                        query_preview      TEXT NOT NULL DEFAULT '',
                        user_language      TEXT NOT NULL DEFAULT 'en',
                        tasks_planned_json TEXT NOT NULL DEFAULT '[]',
                        agents_used_json   TEXT NOT NULL DEFAULT '[]',
                        total_latency_ms   INTEGER NOT NULL DEFAULT 0,
                        total_tokens       INTEGER NOT NULL DEFAULT 0,
                        estimated_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                        fallback_used      INTEGER NOT NULL DEFAULT 0,
                        is_blocked         INTEGER NOT NULL DEFAULT 0,
                        error              TEXT DEFAULT NULL
                    )""",
                    "CREATE INDEX IF NOT EXISTS idx_request_log_ts ON request_log(timestamp DESC)",
                    "CREATE INDEX IF NOT EXISTS idx_request_log_thread ON request_log(thread_id, timestamp DESC)",
                    """CREATE TABLE IF NOT EXISTS quality_log (
                        id             BIGSERIAL PRIMARY KEY,
                        timestamp      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        request_log_id BIGINT DEFAULT NULL,
                        thread_id      TEXT NOT NULL DEFAULT '',
                        agent          TEXT NOT NULL DEFAULT '',
                        query_preview  TEXT NOT NULL DEFAULT '',
                        faithfulness   DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                        relevance      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                        completeness   DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                        avg_score      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                        model_used     TEXT NOT NULL DEFAULT 'gemini-2.5-flash-lite'
                    )""",
                    "CREATE INDEX IF NOT EXISTS idx_quality_log_ts ON quality_log(timestamp DESC)",
                    "CREATE INDEX IF NOT EXISTS idx_quality_log_agent ON quality_log(agent, timestamp DESC)",
                    """CREATE TABLE IF NOT EXISTS thread_files (
                        id                  BIGSERIAL PRIMARY KEY,
                        thread_id           TEXT NOT NULL REFERENCES threads(thread_id),
                        file_id             TEXT NOT NULL,
                        filename            TEXT NOT NULL,
                        file_type           TEXT NOT NULL DEFAULT '',
                        mime_type           TEXT NOT NULL DEFAULT '',
                        size_bytes          INTEGER NOT NULL DEFAULT 0,
                        local_path          TEXT NOT NULL DEFAULT '',
                        extracted_text      TEXT NOT NULL DEFAULT '',
                        chromadb_collection TEXT NOT NULL DEFAULT '',
                        gemini_uri          TEXT NOT NULL DEFAULT '',
                        gemini_name         TEXT NOT NULL DEFAULT '',
                        gemini_expiry       TEXT NOT NULL DEFAULT '',
                        gemini_supported    INTEGER NOT NULL DEFAULT 0,
                        page_count          INTEGER NOT NULL DEFAULT 0,
                        upload_error        TEXT NOT NULL DEFAULT '',
                        ocr_status          TEXT NOT NULL DEFAULT '',
                        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE(thread_id, file_id)
                    )""",
                    "CREATE INDEX IF NOT EXISTS idx_thread_files_thread ON thread_files(thread_id, created_at DESC)",
                ]
                for stmt in stmts:
                    conn.execute(stmt)
                conn.commit()
            # Migration: add ocr_status to thread_files if missing
            try:
                conn.execute(
                    "ALTER TABLE thread_files ADD COLUMN ocr_status TEXT NOT NULL DEFAULT ''"
                )
                conn.commit()
                log.info("PG migration: added ocr_status to thread_files")
            except Exception:
                conn.rollback()  # column already exists

            self._schema_ok = True
            log.info("PostgreSQL chat-store schema ensured")

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def _load_history_sync(
        self,
        thread_id: str,
        max_recent_turns: int = 5,
    ) -> "ChatHistoryResult":
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            thread_row = conn.execute(
                "SELECT summary_text, total_turns FROM threads WHERE thread_id = %s",
                (thread_id,),
            ).fetchone()

            if not thread_row:
                return ChatHistoryResult()

            rows = conn.execute("""
                SELECT user_query, ai_response, turn_number
                FROM messages
                WHERE thread_id = %s
                ORDER BY turn_number DESC
                LIMIT %s
            """, (thread_id, max_recent_turns)).fetchall()

        rows = list(reversed(rows))
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

    async def load_history(
        self,
        thread_id: str,
        max_recent_turns: int = 5,
    ) -> "ChatHistoryResult":
        return await asyncio.to_thread(
            self._load_history_sync, thread_id, max_recent_turns
        )

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def _save_turn_sync(
        self,
        thread_id: str,
        user_query: str,
        ai_response: str,
    ) -> int:
        """Save a Q&A turn atomically. Returns the new turn_number."""
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            # Atomic upsert: increment total_turns, get new value in one statement
            row = conn.execute("""
                INSERT INTO threads (thread_id, total_turns)
                VALUES (%s, 1)
                ON CONFLICT(thread_id) DO UPDATE SET
                    total_turns = threads.total_turns + 1,
                    updated_at  = NOW()
                RETURNING total_turns
            """, (thread_id,)).fetchone()
            new_turn = row["total_turns"]

            conn.execute("""
                INSERT INTO messages (thread_id, turn_number, user_query, ai_response)
                VALUES (%s, %s, %s, %s)
            """, (thread_id, new_turn, user_query, ai_response))
            conn.commit()
            log.debug("Turn saved", thread_id=thread_id[:12], turn=new_turn)

        # Summary regeneration outside the write transaction (LLM call ~1-3s)
        try:
            self._maybe_regenerate_summary_sync(thread_id)
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
        return await asyncio.to_thread(
            self._save_turn_sync, thread_id, user_query, ai_response
        )

    # ------------------------------------------------------------------
    # Summary management
    # ------------------------------------------------------------------

    def _maybe_regenerate_summary_sync(self, thread_id: str) -> None:
        """Regenerate rolling summary if enough new turns accumulated (gap >= 3)."""
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            row = conn.execute(
                "SELECT total_turns, summary_turn_count, summary_text "
                "FROM threads WHERE thread_id = %s",
                (thread_id,),
            ).fetchone()

            if not row:
                return

            gap = row["total_turns"] - row["summary_turn_count"]
            if gap < 3:
                return

            log.debug("Summary regeneration triggered",
                      thread_id=thread_id[:12], gap=gap, total=row["total_turns"])

            unsummarized = conn.execute("""
                SELECT user_query, ai_response, turn_number
                FROM messages
                WHERE thread_id = %s AND turn_number > %s
                ORDER BY turn_number ASC
            """, (thread_id, row["summary_turn_count"])).fetchall()

        new_turns_text = "\n".join(
            f"Turn {r['turn_number']}:\n  User: {r['user_query']}\n  AI: {r['ai_response'][:300]}"
            for r in unsummarized
        )
        existing = row["summary_text"] or "No previous summary."

        try:
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
        except Exception as e:
            log.error("Summary LLM call failed", thread_id=thread_id[:12], error=str(e))
            return

        if new_summary and len(new_summary) > 10:
            from psycopg.rows import dict_row
            with self._get_pool().connection() as conn:
                conn.row_factory = dict_row
                conn.execute("""
                    UPDATE threads
                    SET summary_text = %s, summary_turn_count = %s
                    WHERE thread_id = %s
                """, (new_summary, row["total_turns"], thread_id))
                conn.commit()
            log.info("Summary regenerated",
                     thread_id=thread_id[:12],
                     summary_len=len(new_summary),
                     turns_covered=row["total_turns"])
        else:
            log.warning("Summary generation returned empty result")

    # ------------------------------------------------------------------
    # Draft continuation
    # ------------------------------------------------------------------

    def _save_draft_continuation_sync(self, thread_id: str, data: dict) -> None:
        self._ensure_schema()
        versioned = {**data, "_schema_version": self._DRAFT_SCHEMA_VERSION}
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            conn.execute("""
                INSERT INTO draft_continuations (thread_id, data_json)
                VALUES (%s, %s)
                ON CONFLICT(thread_id) DO UPDATE SET
                    data_json  = EXCLUDED.data_json,
                    created_at = NOW()
            """, (thread_id, json.dumps(versioned)))
            conn.commit()

    async def save_draft_continuation(self, thread_id: str, data: dict) -> None:
        return await asyncio.to_thread(
            self._save_draft_continuation_sync, thread_id, data
        )

    def _load_draft_continuation_sync(self, thread_id: str) -> "dict | None":
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            row = conn.execute(
                "SELECT data_json FROM draft_continuations WHERE thread_id = %s",
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

    async def load_draft_continuation(self, thread_id: str) -> "dict | None":
        return await asyncio.to_thread(
            self._load_draft_continuation_sync, thread_id
        )

    def _clear_draft_continuation_sync(self, thread_id: str) -> None:
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            conn.execute(
                "DELETE FROM draft_continuations WHERE thread_id = %s",
                (thread_id,),
            )
            conn.commit()

    async def clear_draft_continuation(self, thread_id: str) -> None:
        return await asyncio.to_thread(
            self._clear_draft_continuation_sync, thread_id
        )

    # ------------------------------------------------------------------
    # Thread Files Registry
    # ------------------------------------------------------------------

    def _save_thread_file_sync(self, thread_id: str, pf) -> None:
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            # Ensure thread row exists
            conn.execute("""
                INSERT INTO threads (thread_id, total_turns)
                VALUES (%s, 0)
                ON CONFLICT(thread_id) DO NOTHING
            """, (thread_id,))
            conn.execute("""
                INSERT INTO thread_files (
                    thread_id, file_id, filename, file_type, mime_type,
                    size_bytes, local_path, extracted_text,
                    chromadb_collection, gemini_uri, gemini_name,
                    gemini_expiry, gemini_supported, page_count, upload_error
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(thread_id, file_id) DO UPDATE SET
                    gemini_uri    = EXCLUDED.gemini_uri,
                    gemini_name   = EXCLUDED.gemini_name,
                    gemini_expiry = EXCLUDED.gemini_expiry,
                    upload_error  = EXCLUDED.upload_error
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

    async def save_thread_file(self, thread_id: str, pf) -> None:
        return await asyncio.to_thread(self._save_thread_file_sync, thread_id, pf)

    def _load_thread_files_sync(self, thread_id: str) -> list:
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            rows = conn.execute("""
                SELECT file_id, filename, file_type, mime_type, size_bytes,
                       local_path, extracted_text, chromadb_collection,
                       gemini_uri, gemini_name, gemini_expiry,
                       gemini_supported, page_count, upload_error, ocr_status
                FROM thread_files
                WHERE thread_id = %s
                ORDER BY created_at ASC
            """, (thread_id,)).fetchall()
        return [dict(r) for r in rows]

    async def load_thread_files(self, thread_id: str) -> list:
        return await asyncio.to_thread(self._load_thread_files_sync, thread_id)

    def _update_gemini_uri_sync(
        self,
        thread_id: str,
        file_id: str,
        new_uri: str,
        new_name: str,
        new_expiry: str,
    ) -> None:
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            conn.execute("""
                UPDATE thread_files
                SET gemini_uri = %s, gemini_name = %s, gemini_expiry = %s
                WHERE thread_id = %s AND file_id = %s
            """, (new_uri, new_name, new_expiry, thread_id, file_id))
            conn.commit()

    async def update_gemini_uri(
        self,
        thread_id: str,
        file_id: str,
        new_uri: str,
        new_name: str,
        new_expiry: str,
    ) -> None:
        return await asyncio.to_thread(
            self._update_gemini_uri_sync, thread_id, file_id, new_uri, new_name, new_expiry
        )

    def _get_thread_storage_bytes_sync(self, thread_id: str) -> tuple:
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            row = conn.execute("""
                SELECT COUNT(*) as cnt, COALESCE(SUM(size_bytes), 0) as total
                FROM thread_files WHERE thread_id = %s
            """, (thread_id,)).fetchone()
        return row["cnt"], row["total"]

    async def get_thread_storage(self, thread_id: str) -> tuple:
        return await asyncio.to_thread(self._get_thread_storage_bytes_sync, thread_id)

    def _update_ocr_status_sync(
        self, thread_id: str, file_id: str, status: str, error: str = "",
    ) -> None:
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            if error:
                conn.execute("""
                    UPDATE thread_files SET ocr_status = %s, upload_error = %s
                    WHERE thread_id = %s AND file_id = %s
                """, (status, error, thread_id, file_id))
            else:
                conn.execute("""
                    UPDATE thread_files SET ocr_status = %s
                    WHERE thread_id = %s AND file_id = %s
                """, (status, thread_id, file_id))
            conn.commit()

    async def update_ocr_status(
        self, thread_id: str, file_id: str, status: str, error: str = "",
    ) -> None:
        return await asyncio.to_thread(
            self._update_ocr_status_sync, thread_id, file_id, status, error
        )

    def _delete_thread_files_sync(self, thread_id: str) -> list:
        self._ensure_schema()
        records = self._load_thread_files_sync(thread_id)
        if not records:
            return []
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            conn.execute("DELETE FROM thread_files WHERE thread_id = %s", (thread_id,))
            conn.commit()
        return records

    async def delete_thread_files(self, thread_id: str) -> list:
        return await asyncio.to_thread(self._delete_thread_files_sync, thread_id)

    # ------------------------------------------------------------------
    # File Context Persistence
    # ------------------------------------------------------------------

    def _save_file_context_sync(self, thread_id: str, file_context: dict) -> None:
        self._ensure_schema()
        safe_ctx = {
            "inline_text":          file_context.get("inline_text", ""),
            "chromadb_collections": file_context.get("chromadb_collections", []),
            "file_names":           file_context.get("file_names", []),
            "image_data":           [],
        }
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            conn.execute("""
                INSERT INTO threads (thread_id, file_context_json)
                VALUES (%s, %s)
                ON CONFLICT(thread_id) DO UPDATE SET
                    file_context_json = EXCLUDED.file_context_json,
                    updated_at        = NOW()
            """, (thread_id, json.dumps(safe_ctx)))
            conn.commit()
            log.debug("File context saved",
                      thread_id=thread_id[:12],
                      files=safe_ctx["file_names"],
                      chromadb=len(safe_ctx["chromadb_collections"]),
                      inline_chars=len(safe_ctx["inline_text"]))

    async def save_file_context(self, thread_id: str, file_context: dict) -> None:
        return await asyncio.to_thread(
            self._save_file_context_sync, thread_id, file_context
        )

    def _load_file_context_sync(self, thread_id: str) -> "dict | None":
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            row = conn.execute(
                "SELECT file_context_json FROM threads WHERE thread_id = %s",
                (thread_id,),
            ).fetchone()
        if row and row["file_context_json"]:
            try:
                ctx = json.loads(row["file_context_json"])
                if ctx.get("inline_text") or ctx.get("chromadb_collections") or ctx.get("file_names"):
                    return ctx
            except (json.JSONDecodeError, TypeError) as e:
                log.error("Corrupted file_context_json",
                          thread_id=thread_id, error=str(e))
        return None

    async def load_file_context(self, thread_id: str) -> "dict | None":
        return await asyncio.to_thread(
            self._load_file_context_sync, thread_id
        )

    # ------------------------------------------------------------------
    # List Threads
    # ------------------------------------------------------------------

    def _list_threads_sync(self, limit: int = 50, offset: int = 0) -> list:
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            rows = conn.execute("""
                SELECT
                    t.thread_id,
                    TO_CHAR(t.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                    TO_CHAR(t.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at,
                    t.total_turns,
                    t.summary_text,
                    (SELECT m.user_query FROM messages m
                     WHERE m.thread_id = t.thread_id
                     ORDER BY m.turn_number ASC LIMIT 1
                    ) AS first_query
                FROM threads t
                WHERE t.total_turns > 0
                ORDER BY t.updated_at DESC
                LIMIT %s OFFSET %s
            """, (limit, offset)).fetchall()
        return [
            {
                "thread_id":    r["thread_id"],
                "created_at":   r["created_at"],
                "updated_at":   r["updated_at"],
                "total_turns":  r["total_turns"],
                "preview":      (r["first_query"] or "")[:120],
                "summary_text": (r["summary_text"] or "")[:300],
            }
            for r in rows
        ]

    async def list_threads(self, limit: int = 50, offset: int = 0) -> list:
        return await asyncio.to_thread(self._list_threads_sync, limit, offset)

    def _load_thread_messages_sync(self, thread_id: str, limit: int = 50) -> list:
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            rows = conn.execute("""
                SELECT turn_number, user_query, ai_response,
                       TO_CHAR(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at
                FROM messages
                WHERE thread_id = %s
                ORDER BY turn_number ASC
                LIMIT %s
            """, (thread_id, limit)).fetchall()
        return [dict(r) for r in rows]

    async def load_thread_messages(self, thread_id: str, limit: int = 50) -> list:
        return await asyncio.to_thread(self._load_thread_messages_sync, thread_id, limit)

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
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            conn.execute("""
                INSERT INTO feedback (thread_id, turn_number, rating, comment)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT(thread_id, turn_number) DO UPDATE SET
                    rating     = EXCLUDED.rating,
                    comment    = EXCLUDED.comment,
                    created_at = NOW()
            """, (thread_id, turn_number, rating, comment))
            conn.commit()
            log.info("Feedback saved",
                     thread_id=thread_id[:12], turn=turn_number, rating=rating)
        return True

    async def save_feedback(
        self,
        thread_id: str,
        turn_number: int,
        rating: str,
        comment: str = "",
    ) -> bool:
        return await asyncio.to_thread(
            self._save_feedback_sync, thread_id, turn_number, rating, comment
        )

    # ------------------------------------------------------------------
    # Fallback Log
    # ------------------------------------------------------------------

    def _log_fallback_sync(
        self,
        agent: str,
        query: str,
        original_query: str,
        response_preview: str,
        web_sources: list,
        tokens: int,
        fallback_tier: str = "web",
    ) -> int:
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            row = conn.execute("""
                INSERT INTO fallback_log
                    (agent, query, original_query, fallback_tier,
                     response_preview, web_sources_json, tokens)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                agent,
                query[:1000],
                original_query[:1000],
                fallback_tier,
                response_preview[:600],
                json.dumps(web_sources),
                tokens,
            )).fetchone()
            conn.commit()
            row_id = row["id"]
            log.debug("Fallback logged", agent=agent, tier=fallback_tier, row_id=row_id)
        return row_id

    async def log_fallback(
        self,
        agent: str,
        query: str,
        original_query: str,
        response_preview: str,
        web_sources: list,
        tokens: int,
        fallback_tier: str = "web",
    ) -> int:
        return await asyncio.to_thread(
            self._log_fallback_sync,
            agent, query, original_query, response_preview,
            web_sources, tokens, fallback_tier,
        )

    def _get_fallback_logs_sync(
        self,
        agent: "str | None" = None,
        backfilled: "int | None" = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        self._ensure_schema()
        from psycopg.rows import dict_row
        conditions: list = []
        params: list = []
        if agent:
            conditions.append("agent = %s")
            params.append(agent)
        if backfilled is not None:
            conditions.append("backfilled = %s")
            params.append(int(backfilled))
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            rows = conn.execute(
                f"""SELECT id, TO_CHAR(timestamp AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') AS timestamp,
                           agent, query, original_query, fallback_tier,
                           response_preview, web_sources_json, tokens, backfilled, backfill_date
                    FROM fallback_log {where}
                    ORDER BY timestamp DESC
                    LIMIT %s OFFSET %s""",
                params + [limit, offset],
            ).fetchall()
            total_row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM fallback_log {where}", params
            ).fetchone()
        return {
            "total":  total_row["cnt"],
            "limit":  limit,
            "offset": offset,
            "items": [
                {
                    "id":               r["id"],
                    "timestamp":        r["timestamp"],
                    "agent":            r["agent"],
                    "query":            r["query"],
                    "original_query":   r["original_query"],
                    "fallback_tier":    r["fallback_tier"],
                    "response_preview": r["response_preview"],
                    "web_sources":      json.loads(r["web_sources_json"] or "[]"),
                    "tokens":           r["tokens"],
                    "backfilled":       bool(r["backfilled"]),
                    "backfill_date":    str(r["backfill_date"]) if r["backfill_date"] else None,
                }
                for r in rows
            ],
        }

    async def get_fallback_logs(
        self,
        agent: "str | None" = None,
        backfilled: "int | None" = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        return await asyncio.to_thread(
            self._get_fallback_logs_sync, agent, backfilled, limit, offset
        )

    def _get_fallback_stats_sync(self) -> dict:
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM fallback_log"
            ).fetchone()["cnt"]
            pending = conn.execute(
                "SELECT COUNT(*) as cnt FROM fallback_log WHERE backfilled = 0"
            ).fetchone()["cnt"]
            by_agent = {
                r["agent"]: r["cnt"]
                for r in conn.execute(
                    "SELECT agent, COUNT(*) as cnt FROM fallback_log "
                    "GROUP BY agent ORDER BY cnt DESC"
                ).fetchall()
            }
            by_date = {
                r["day"]: r["cnt"]
                for r in conn.execute("""
                    SELECT TO_CHAR(timestamp, 'YYYY-MM-DD') as day, COUNT(*) as cnt
                    FROM fallback_log
                    WHERE timestamp >= NOW() - INTERVAL '7 days'
                    GROUP BY day ORDER BY day DESC
                """).fetchall()
            }
            top_queries = [
                {"query": r["query"], "count": r["cnt"], "agent": r["agent"]}
                for r in conn.execute(
                    "SELECT query, agent, COUNT(*) as cnt FROM fallback_log "
                    "GROUP BY query, agent ORDER BY cnt DESC LIMIT 10"
                ).fetchall()
            ]
        return {
            "total":                total,
            "pending_backfill":     pending,
            "backfilled":           total - pending,
            "by_agent":             by_agent,
            "by_date_last_7d":      by_date,
            "top_repeated_queries": top_queries,
        }

    async def get_fallback_stats(self) -> dict:
        return await asyncio.to_thread(self._get_fallback_stats_sync)

    # ------------------------------------------------------------------
    # Request Log
    # ------------------------------------------------------------------

    def _estimate_cost(self, agents_used: list, total_tokens: int) -> float:
        if not total_tokens:
            return 0.0
        if not agents_used:
            return round(total_tokens / 1000 * self._DEFAULT_COST_PER_1K, 6)
        rates = [self._AGENT_COST_PER_1K.get(a, self._DEFAULT_COST_PER_1K) for a in agents_used]
        return round(total_tokens / 1000 * max(rates), 6)

    def _log_request_sync(
        self,
        thread_id: str,
        endpoint: str,
        query_preview: str,
        user_language: str,
        tasks_planned: list,
        agents_used: list,
        total_latency_ms: int,
        total_tokens: int,
        fallback_used: bool,
        is_blocked: bool,
        error: "str | None" = None,
    ) -> int:
        self._ensure_schema()
        estimated_cost = self._estimate_cost(agents_used, total_tokens)
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            row = conn.execute("""
                INSERT INTO request_log
                    (thread_id, endpoint, query_preview, user_language,
                     tasks_planned_json, agents_used_json, total_latency_ms,
                     total_tokens, estimated_cost_usd, fallback_used,
                     is_blocked, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                thread_id,
                endpoint,
                query_preview[:300],
                user_language or "en",
                json.dumps(tasks_planned),
                json.dumps(agents_used),
                total_latency_ms,
                total_tokens,
                estimated_cost,
                int(fallback_used),
                int(is_blocked),
                error,
            )).fetchone()
            conn.commit()
        return row["id"]

    async def log_request(
        self,
        thread_id: str,
        endpoint: str,
        query_preview: str,
        user_language: str,
        tasks_planned: list,
        agents_used: list,
        total_latency_ms: int,
        total_tokens: int,
        fallback_used: bool = False,
        is_blocked: bool = False,
        error: "str | None" = None,
    ) -> int:
        return await asyncio.to_thread(
            self._log_request_sync,
            thread_id, endpoint, query_preview, user_language,
            tasks_planned, agents_used, total_latency_ms,
            total_tokens, fallback_used, is_blocked, error,
        )

    def _get_usage_stats_sync(self, days: int = 7) -> dict:
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            totals = conn.execute("""
                SELECT
                    COUNT(*)                              AS total_requests,
                    SUM(total_tokens)                     AS total_tokens,
                    ROUND(SUM(estimated_cost_usd)::numeric, 4) AS total_cost_usd,
                    ROUND(AVG(total_latency_ms)::numeric) AS avg_latency_ms,
                    SUM(fallback_used)                    AS fallback_count,
                    SUM(is_blocked)                       AS blocked_count
                FROM request_log
                WHERE timestamp >= NOW() - (%s * INTERVAL '1 day')
            """, (days,)).fetchone()

            by_day = [
                dict(r) for r in conn.execute("""
                    SELECT
                        TO_CHAR(timestamp, 'YYYY-MM-DD')               AS date,
                        COUNT(*)                                        AS requests,
                        SUM(total_tokens)                               AS tokens,
                        ROUND(SUM(estimated_cost_usd)::numeric, 4)     AS cost_usd,
                        ROUND(AVG(total_latency_ms)::numeric)           AS avg_latency_ms,
                        SUM(fallback_used)                              AS fallbacks
                    FROM request_log
                    WHERE timestamp >= NOW() - (%s * INTERVAL '1 day')
                    GROUP BY date ORDER BY date DESC
                """, (days,)).fetchall()
            ]

            top_agents = [
                {"agent": r["agent"], "count": r["cnt"]}
                for r in conn.execute("""
                    SELECT t.agent AS agent, COUNT(*) AS cnt
                    FROM request_log rl,
                         LATERAL jsonb_array_elements_text(
                             COALESCE(NULLIF(rl.agents_used_json, ''), '[]')::jsonb
                         ) AS t(agent)
                    WHERE rl.timestamp >= NOW() - (%s * INTERVAL '1 day')
                    GROUP BY t.agent ORDER BY cnt DESC LIMIT 10
                """, (days,)).fetchall()
            ]

            task_dist = [
                {"task": r["task"], "count": r["cnt"]}
                for r in conn.execute("""
                    SELECT t.task AS task, COUNT(*) AS cnt
                    FROM request_log rl,
                         LATERAL jsonb_array_elements_text(
                             COALESCE(NULLIF(rl.tasks_planned_json, ''), '[]')::jsonb
                         ) AS t(task)
                    WHERE rl.timestamp >= NOW() - (%s * INTERVAL '1 day')
                    GROUP BY t.task ORDER BY cnt DESC
                """, (days,)).fetchall()
            ]

        def _to_float(v):
            return float(v) if v is not None else None

        totals_clean = {}
        if totals:
            for k, v in totals.items():
                totals_clean[k] = _to_float(v) if hasattr(v, '__float__') else v

        return {
            "period_days":       days,
            "totals":            totals_clean,
            "by_day":            by_day,
            "top_agents":        top_agents,
            "task_distribution": task_dist,
        }

    async def get_usage_stats(self, days: int = 7) -> dict:
        return await asyncio.to_thread(self._get_usage_stats_sync, days)

    # ------------------------------------------------------------------
    # Quality Log
    # ------------------------------------------------------------------

    def _log_quality_sync(
        self,
        request_log_id: "int | None",
        thread_id: str,
        agent: str,
        query_preview: str,
        faithfulness: float,
        relevance: float,
        completeness: float,
        avg_score: float,
        model_used: str = "gemini-2.5-flash-lite",
    ) -> int:
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            row = conn.execute("""
                INSERT INTO quality_log
                    (request_log_id, thread_id, agent, query_preview,
                     faithfulness, relevance, completeness, avg_score, model_used)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                request_log_id,
                thread_id,
                agent,
                query_preview[:300],
                faithfulness,
                relevance,
                completeness,
                avg_score,
                model_used,
            )).fetchone()
            conn.commit()
        return row["id"]

    async def log_quality(
        self,
        request_log_id: "int | None",
        thread_id: str,
        agent: str,
        query_preview: str,
        faithfulness: float,
        relevance: float,
        completeness: float,
        avg_score: float,
        model_used: str = "gemini-2.5-flash-lite",
    ) -> int:
        return await asyncio.to_thread(
            self._log_quality_sync,
            request_log_id, thread_id, agent, query_preview,
            faithfulness, relevance, completeness, avg_score, model_used,
        )

    def _get_quality_stats_sync(self, days: int = 7) -> dict:
        self._ensure_schema()
        from psycopg.rows import dict_row
        with self._get_pool().connection() as conn:
            conn.row_factory = dict_row
            totals = conn.execute("""
                SELECT
                    COUNT(*)                              AS total_scored,
                    ROUND(AVG(avg_score)::numeric, 4)     AS avg_score,
                    ROUND(AVG(faithfulness)::numeric, 4)  AS avg_faithfulness,
                    ROUND(AVG(relevance)::numeric, 4)     AS avg_relevance,
                    ROUND(AVG(completeness)::numeric, 4)  AS avg_completeness,
                    SUM(CASE WHEN avg_score < 0.6 THEN 1 ELSE 0 END) AS low_quality_count
                FROM quality_log
                WHERE timestamp >= NOW() - (%s * INTERVAL '1 day')
            """, (days,)).fetchone()

            by_agent = [
                dict(r) for r in conn.execute("""
                    SELECT
                        agent,
                        COUNT(*) AS scored,
                        ROUND(AVG(avg_score)::numeric, 4)    AS avg_score,
                        ROUND(AVG(faithfulness)::numeric, 4) AS avg_faithfulness,
                        ROUND(AVG(relevance)::numeric, 4)    AS avg_relevance
                    FROM quality_log
                    WHERE timestamp >= NOW() - (%s * INTERVAL '1 day')
                    GROUP BY agent ORDER BY avg_score ASC
                """, (days,)).fetchall()
            ]

            by_day = [
                dict(r) for r in conn.execute("""
                    SELECT
                        TO_CHAR(timestamp, 'YYYY-MM-DD')              AS date,
                        COUNT(*) AS scored,
                        ROUND(AVG(avg_score)::numeric, 4)             AS avg_score,
                        ROUND(AVG(faithfulness)::numeric, 4)          AS avg_faithfulness
                    FROM quality_log
                    WHERE timestamp >= NOW() - (%s * INTERVAL '1 day')
                    GROUP BY date ORDER BY date DESC
                """, (days,)).fetchall()
            ]

            low_quality = [
                dict(r) for r in conn.execute("""
                    SELECT TO_CHAR(timestamp, 'YYYY-MM-DD HH24:MI:SS') AS timestamp,
                           agent, query_preview, avg_score,
                           faithfulness, relevance, completeness
                    FROM quality_log
                    WHERE avg_score < 0.6
                      AND timestamp >= NOW() - (%s * INTERVAL '1 day')
                    ORDER BY avg_score ASC LIMIT 10
                """, (days,)).fetchall()
            ]

        totals_clean = {}
        if totals:
            for k, v in totals.items():
                totals_clean[k] = float(v) if hasattr(v, '__float__') else v

        return {
            "period_days":           days,
            "totals":                totals_clean,
            "by_agent":              by_agent,
            "by_day":                by_day,
            "low_quality_responses": low_quality,
        }

    async def get_quality_stats(self, days: int = 7) -> dict:
        return await asyncio.to_thread(self._get_quality_stats_sync, days)


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

if POSTGRES_URL:
    chat_store: "_PostgresChatHistoryStore | _SqliteChatHistoryStore" = _PostgresChatHistoryStore(POSTGRES_URL)
    log.info("Chat store: PostgreSQL backend", url=POSTGRES_URL[:30] + "...")
else:
    chat_store = _SqliteChatHistoryStore(CHAT_HISTORY_DB_PATH)
    log.warning(
        "Chat store: SQLite backend (multi-worker write contention possible). "
        "Set POSTGRES_URL in .env for production."
    )
