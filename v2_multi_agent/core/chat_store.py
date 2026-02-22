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
                    total_turns       INTEGER NOT NULL DEFAULT 0
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
            """)
            conn.commit()
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

                return new_turn
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        # Summary regeneration runs OUTSIDE the write lock so it doesn't
        # block other writes during the LLM call (~1-3 seconds).
        try:
            conn = self._get_connection()
            try:
                self._maybe_regenerate_summary_sync(thread_id, conn)
            finally:
                conn.close()
        except Exception as e:
            log.error("Post-save summary regeneration failed",
                      thread_id=thread_id[:12], error=str(e))

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
    # Migration helper (import from legacy API response)
    # ------------------------------------------------------------------

    def _import_from_api_response_sync(
        self,
        thread_id: str,
        summary_text: str,
    ) -> None:
        """Import legacy API summary into SQLite.

        Parses JSON array format [{"user": "...", "ai": "..."}] or
        stores raw text as a summary if not parseable.
        """
        self._ensure_schema()
        with self._write_lock:
            conn = self._get_connection()
            try:
                # Check if thread already exists
                existing = conn.execute(
                    "SELECT total_turns FROM threads WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
                if existing and existing["total_turns"] > 0:
                    return  # Already has data, skip import

                # Try to parse as JSON array of turns
                turns = []
                try:
                    parsed = json.loads(summary_text)
                    if isinstance(parsed, list):
                        turns = parsed
                except (json.JSONDecodeError, TypeError):
                    pass

                if turns:
                    # Insert as individual turns
                    conn.execute("""
                        INSERT INTO threads (thread_id, total_turns, summary_text, summary_turn_count)
                        VALUES (?, ?, '', 0)
                        ON CONFLICT(thread_id) DO UPDATE SET
                            total_turns = excluded.total_turns
                    """, (thread_id, len(turns)))

                    for i, turn in enumerate(turns, 1):
                        user_q = turn.get("user", "")
                        ai_r = turn.get("ai", "")
                        if user_q or ai_r:
                            conn.execute("""
                                INSERT INTO messages (thread_id, turn_number, user_query, ai_response)
                                VALUES (?, ?, ?, ?)
                            """, (thread_id, i, user_q, ai_r))

                    log.info("Legacy turns imported",
                             thread_id=thread_id[:12], turns=len(turns))
                else:
                    # Store raw text as summary only (no individual turns)
                    conn.execute("""
                        INSERT INTO threads (thread_id, total_turns, summary_text, summary_turn_count)
                        VALUES (?, 0, ?, 0)
                        ON CONFLICT(thread_id) DO UPDATE SET
                            summary_text = excluded.summary_text
                    """, (thread_id, summary_text))

                    log.info("Legacy summary imported",
                             thread_id=thread_id[:12],
                             summary_len=len(summary_text))

                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    async def import_from_api_response(
        self,
        thread_id: str,
        summary_text: str,
    ) -> None:
        """Async wrapper for legacy import."""
        return await asyncio.to_thread(
            self._import_from_api_response_sync, thread_id, summary_text
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
