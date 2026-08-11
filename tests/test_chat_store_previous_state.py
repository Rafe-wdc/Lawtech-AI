"""Tests for per-turn typed state persistence in chat_store.

Level 1 of docs/followup_pipeline_simplification_plan.md adds four columns
to the `messages` table so Turn N+1 can inherit the previous turn's typed
state (user_intent, task, tasks_planned, primary_artifact_kind) instead of
re-deriving them from chat-history text.

These tests exercise the SQLite backend end-to-end against a temp DB:
migration idempotency, default-values fallback, and round-trip persistence.
The Postgres backend uses identical semantics (the only difference is DDL
placeholder syntax) — it's exercised via prod smoke, not CI, because it
requires a live server.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core.chat_store import _SqliteChatHistoryStore


def _run(coro):
    """Run an async coroutine to completion synchronously.

    Mirrors the pattern in tests/test_self_refine.py::_run — avoids the
    pytest-asyncio dependency (not pinned in the project's test deps).
    """
    return asyncio.run(coro)


@pytest.fixture
def fresh_store(tmp_path) -> _SqliteChatHistoryStore:
    """A brand-new store pointed at a temp SQLite file per test."""
    db_path = tmp_path / "chat.db"
    return _SqliteChatHistoryStore(str(db_path))


def _messages_columns(store: _SqliteChatHistoryStore) -> set[str]:
    """Return the column names on the `messages` table."""
    conn = store._get_connection()
    try:
        return {
            r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()
        }
    finally:
        conn.close()


def _fetch_row(store: _SqliteChatHistoryStore, thread_id: str, turn: int) -> dict:
    conn = store._get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM messages WHERE thread_id = ? AND turn_number = ?",
            (thread_id, turn),
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


class TestMigration:
    def test_new_columns_present_after_fresh_init(self, fresh_store):
        fresh_store._ensure_schema()
        cols = _messages_columns(fresh_store)
        for expected in (
            "user_intent_json", "task", "tasks_planned_json",
            "primary_artifact_kind",
        ):
            assert expected in cols, f"Missing column {expected} — got {sorted(cols)}"

    def test_ensure_schema_is_idempotent(self, fresh_store):
        # Two consecutive init calls must not raise (the second walks the
        # ALTER branch and sees columns already exist).
        fresh_store._ensure_schema()
        fresh_store._initialized = False  # force the second _ensure_schema to re-run
        fresh_store._ensure_schema()      # must not raise

    def test_migration_on_pre_existing_db(self, tmp_path):
        """A DB created without the new columns should migrate cleanly."""
        db_path = tmp_path / "legacy.db"
        # Build the pre-migration schema by hand: threads + messages without
        # the four new columns.
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE threads (
                thread_id         TEXT PRIMARY KEY,
                created_at        TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
                summary_text      TEXT NOT NULL DEFAULT '',
                summary_turn_count INTEGER NOT NULL DEFAULT 0,
                total_turns       INTEGER NOT NULL DEFAULT 0,
                file_context_json TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id   TEXT NOT NULL REFERENCES threads(thread_id),
                turn_number INTEGER NOT NULL,
                user_query  TEXT NOT NULL,
                ai_response TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO threads (thread_id, total_turns)
                VALUES ('legacy-thread', 1);
            INSERT INTO messages (thread_id, turn_number, user_query, ai_response)
                VALUES ('legacy-thread', 1, 'legacy q', 'legacy a');
        """)
        conn.commit()
        conn.close()

        # New store on the same DB triggers _ensure_schema, which must ALTER
        # the four columns onto the existing messages table AND preserve the
        # pre-existing row.
        store = _SqliteChatHistoryStore(str(db_path))
        store._ensure_schema()
        cols = _messages_columns(store)
        for expected in (
            "user_intent_json", "task", "tasks_planned_json",
            "primary_artifact_kind",
        ):
            assert expected in cols

        # Legacy row survived, and the migration filled the new columns
        # with their column-level DEFAULTs.
        row = _fetch_row(store, "legacy-thread", 1)
        assert row["user_query"] == "legacy q"
        assert row["ai_response"] == "legacy a"
        assert row["user_intent_json"] == ""
        assert row["task"] == ""
        assert row["tasks_planned_json"] == "[]"
        assert row["primary_artifact_kind"] == ""


class TestSaveTurnDefaults:
    """Callers who omit the new fields must not break."""

    def test_omitting_new_fields_stores_defaults(self, fresh_store):
        turn = _run(fresh_store.save_turn("thread-A", "Q1", "A1"))
        assert turn == 1
        row = _fetch_row(fresh_store, "thread-A", 1)
        assert row["user_query"] == "Q1"
        assert row["ai_response"] == "A1"
        assert row["user_intent_json"] == ""
        assert row["task"] == ""
        assert row["tasks_planned_json"] == "[]"
        assert row["primary_artifact_kind"] == ""


class TestSaveTurnRoundTrip:
    def test_all_fields_persist(self, fresh_store):
        intent_json = json.dumps({
            "language": "mr",
            "language_explicit": True,
            "response_depth": "detailed",
        })
        tasks_planned_json = json.dumps(["Drafting", "Newacts"])

        _run(fresh_store.save_turn(
            "thread-B", "Draft NDPS bail", "…draft body…",
            user_intent_json=intent_json,
            task="Drafting",
            tasks_planned_json=tasks_planned_json,
            primary_artifact_kind="draft",
        ))
        row = _fetch_row(fresh_store, "thread-B", 1)
        assert row["user_intent_json"] == intent_json
        assert row["task"] == "Drafting"
        assert row["tasks_planned_json"] == tasks_planned_json
        assert row["primary_artifact_kind"] == "draft"

    def test_multiple_turns_carry_independent_state(self, fresh_store):
        # Turn 1: draft
        _run(fresh_store.save_turn(
            "thread-C", "Draft bail", "…draft…",
            user_intent_json='{"task_intent":"draft"}',
            task="Drafting",
            tasks_planned_json='["Drafting"]',
            primary_artifact_kind="draft",
        ))
        # Turn 2: pure retrieval, no artefact
        _run(fresh_store.save_turn(
            "thread-C", "Find related cases", "…cases…",
            user_intent_json='{"task_intent":"retrieve"}',
            task="SCI_Judgment",
            tasks_planned_json='["SCI_Judgment"]',
            primary_artifact_kind="",
        ))

        row1 = _fetch_row(fresh_store, "thread-C", 1)
        row2 = _fetch_row(fresh_store, "thread-C", 2)

        assert row1["task"] == "Drafting"
        assert row1["primary_artifact_kind"] == "draft"
        assert row2["task"] == "SCI_Judgment"
        assert row2["primary_artifact_kind"] == ""


class TestLoadHistoryPreviousState:
    """load_history must return previous_* fields from the LATEST turn.

    These fields drive Level 2's drafting fast-path and the Level 1
    consumer opt-in (intent extractor / rewriter / classifier).
    """

    def test_fresh_thread_returns_empty_previous_state(self, fresh_store):
        """A thread with no rows must return the empty defaults, not None."""
        result = _run(fresh_store.load_history("nonexistent-thread"))
        assert result.previous_intent_json == ""
        assert result.previous_task == ""
        assert result.previous_tasks_planned == []
        assert result.previous_artifact_kind == ""
        assert result.previous_artifact_content == ""

    def test_latest_turn_state_surfaces(self, fresh_store):
        # Turn 1 = drafting
        _run(fresh_store.save_turn(
            "thread-D", "Draft bail app", "…draft body…",
            user_intent_json='{"language":"en","language_explicit":false}',
            task="Drafting",
            tasks_planned_json='["Drafting"]',
            primary_artifact_kind="draft",
        ))
        # Turn 2 = a pure retrieval task with different fields
        _run(fresh_store.save_turn(
            "thread-D", "Find cases", "…cases response…",
            user_intent_json='{"language":"mr","language_explicit":true}',
            task="SCI_Judgment",
            tasks_planned_json='["SCI_Judgment","Judgment"]',
            primary_artifact_kind="",
        ))

        result = _run(fresh_store.load_history("thread-D"))
        # Should reflect Turn 2's state (latest), not Turn 1's.
        assert result.previous_intent_json == '{"language":"mr","language_explicit":true}'
        assert result.previous_task == "SCI_Judgment"
        assert result.previous_tasks_planned == ["SCI_Judgment", "Judgment"]
        assert result.previous_artifact_kind == ""
        assert result.previous_artifact_content == "…cases response…"

    def test_drafting_artifact_carries_over(self, fresh_store):
        """The drafting fast-path (Level 2) reads previous_artifact_content
        as the base document to modify — this test locks in the shape."""
        prior_draft = "IN THE COURT OF SESSIONS\n\n1. …\n\nVERIFICATION …"
        _run(fresh_store.save_turn(
            "thread-E", "Draft plaint", prior_draft,
            user_intent_json='{"language":"en"}',
            task="Drafting",
            tasks_planned_json='["Drafting"]',
            primary_artifact_kind="draft",
        ))
        result = _run(fresh_store.load_history("thread-E"))
        assert result.previous_artifact_kind == "draft"
        assert result.previous_artifact_content == prior_draft

    def test_corrupt_tasks_planned_json_degrades_gracefully(self, fresh_store):
        """Corrupt JSON in tasks_planned_json must return [] not raise."""
        _run(fresh_store.save_turn(
            "thread-F", "Q", "A",
            tasks_planned_json="not valid json {",  # bypass json.dumps at caller
        ))
        result = _run(fresh_store.load_history("thread-F"))
        assert result.previous_tasks_planned == []
        # The other fields should still surface correctly.
        assert result.previous_artifact_content == "A"

    def test_legacy_row_before_migration_reads_as_empty(self, tmp_path):
        """A row saved before the migration must load with empty
        previous_* defaults (SQLite ALTER filled them with column DEFAULTs)."""
        import sqlite3
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE threads (
                thread_id         TEXT PRIMARY KEY,
                created_at        TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
                summary_text      TEXT NOT NULL DEFAULT '',
                summary_turn_count INTEGER NOT NULL DEFAULT 0,
                total_turns       INTEGER NOT NULL DEFAULT 0,
                file_context_json TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id   TEXT NOT NULL REFERENCES threads(thread_id),
                turn_number INTEGER NOT NULL,
                user_query  TEXT NOT NULL,
                ai_response TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO threads (thread_id, total_turns)
                VALUES ('legacy-thread', 1);
            INSERT INTO messages (thread_id, turn_number, user_query, ai_response)
                VALUES ('legacy-thread', 1, 'legacy q', 'legacy a');
        """)
        conn.commit()
        conn.close()

        store = _SqliteChatHistoryStore(str(db_path))
        result = _run(store.load_history("legacy-thread"))
        # chat_history / summary still work
        assert result.total_turns == 1
        assert len(result.chat_history) == 2
        # previous_* fields default to empty (never crash)
        assert result.previous_intent_json == ""
        assert result.previous_task == ""
        assert result.previous_tasks_planned == []
        assert result.previous_artifact_kind == ""
        # ai_response is still filled from the legacy row
        assert result.previous_artifact_content == "legacy a"
