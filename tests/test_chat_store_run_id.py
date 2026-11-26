"""save_turn is idempotent by run_id (background runs, 2026-11-26).

A background run that is retried or recovered must never store its turn a
second time. Without a run_id, save_turn behaves exactly as before.
SQLite always; Postgres too when TEST_POSTGRES_URL is set.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import threading

import pytest

from core.chat_store import _PostgresChatHistoryStore, _SqliteChatHistoryStore

PG_URL = os.getenv("TEST_POSTGRES_URL", "")


def _run(coro):
    return asyncio.run(coro)


def _reset_pg(url: str) -> None:
    import psycopg
    with psycopg.connect(url, autocommit=True) as c:
        for t in ("thread_files", "feedback", "messages", "threads"):
            c.execute(f"DROP TABLE IF EXISTS {t} CASCADE")


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path, monkeypatch):
    if request.param == "postgres":
        if not PG_URL:
            pytest.skip("TEST_POSTGRES_URL not set")
        _reset_pg(PG_URL)
        s = _PostgresChatHistoryStore(PG_URL)
    else:
        s = _SqliteChatHistoryStore(str(tmp_path / "chat.db"))
    # the rolling summary calls a model after 3 turns; not under test here
    monkeypatch.setattr(s, "_maybe_regenerate_summary_sync", lambda *a, **k: None)
    yield s
    if getattr(s, "_pool", None) is not None:
        s._pool.close()


def _message_count(store, thread_id: str) -> int:
    if isinstance(store, _SqliteChatHistoryStore):
        conn = store._get_connection()
        try:
            return conn.execute("SELECT COUNT(*) FROM messages WHERE thread_id = ?",
                                (thread_id,)).fetchone()[0]
        finally:
            conn.close()
    with store._get_pool().connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM messages WHERE thread_id = %s",
                           (thread_id,)).fetchone()
        # chat_store leaves pooled connections on dict rows
        return row["n"] if isinstance(row, dict) else row[0]


class TestIdempotentSave:
    def test_same_run_saved_twice_keeps_one_turn(self, store):
        assert _run(store.save_turn("t1", "q", "a", run_id="run-1")) == 1
        assert _run(store.save_turn("t1", "q", "a again", run_id="run-1")) == 1
        assert _message_count(store, "t1") == 1
        # the turn counter was not bumped by the repeat
        assert _run(store.save_turn("t1", "q2", "a2", run_id="run-2")) == 2

    def test_without_run_id_behaviour_is_unchanged(self, store):
        assert _run(store.save_turn("t1", "q", "a")) == 1
        assert _run(store.save_turn("t1", "q", "a")) == 2
        assert _message_count(store, "t1") == 2

    def test_get_turn_by_run_id(self, store):
        _run(store.save_turn("t1", "q", "the answer", run_id="run-9"))
        got = _run(store.get_turn_by_run_id("run-9"))
        assert got["thread_id"] == "t1" and got["turn_number"] == 1
        assert got["ai_response"] == "the answer"
        assert _run(store.get_turn_by_run_id("nope")) is None
        assert _run(store.get_turn_by_run_id("")) is None

    def test_concurrent_saves_of_one_run_store_one_turn(self, store):
        """Two savers racing on one run (e.g. a recovered run and its stale
        holder): the unique index lets one win; both report the same turn."""
        _run(store.save_turn("t1", "q0", "a0"))                  # turn 1 exists
        results, barrier = [], threading.Barrier(4)

        def save():
            barrier.wait()
            results.append(store._save_turn_sync("t1", "q", "a", run_id="run-race"))

        threads = [threading.Thread(target=save) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results == [2, 2, 2, 2]
        assert _message_count(store, "t1") == 2
        # the losing savers rolled back their counter bump too
        assert _run(store.save_turn("t1", "q3", "a3")) == 3


class TestMigration:
    def test_sqlite_legacy_messages_gain_run_id(self, tmp_path):
        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE threads (
                thread_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                summary_text TEXT NOT NULL DEFAULT '',
                summary_turn_count INTEGER NOT NULL DEFAULT 0,
                total_turns INTEGER NOT NULL DEFAULT 1,
                file_context_json TEXT NOT NULL DEFAULT '');
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL, turn_number INTEGER NOT NULL,
                user_query TEXT NOT NULL, ai_response TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')));
            INSERT INTO threads (thread_id) VALUES ('old');
            INSERT INTO messages (thread_id, turn_number, user_query, ai_response)
                VALUES ('old', 1, 'q', 'a');
        """)
        conn.commit()
        conn.close()
        s = _SqliteChatHistoryStore(str(db))
        s._ensure_schema()
        conn = s._get_connection()
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
            idx = {r[1] for r in conn.execute("PRAGMA index_list(messages)")}
        finally:
            conn.close()
        assert "run_id" in cols and "uq_messages_run" in idx
        assert _run(s.save_turn("old", "q2", "a2", run_id="r1")) == 2
        assert _run(s.save_turn("old", "q2", "a2", run_id="r1")) == 2
