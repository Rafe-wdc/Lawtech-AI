"""user_id from the request payload (2026-11-24).

The app sends its own user id with every chat request. Python stores it on
the thread (threads.user_id) so a thread can be tied to its owner. These
tests cover the SQLite backend, the request models and the pipeline inputs;
the Postgres backend runs the same upsert shape and is exercised on dev.
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from core.chat_store import _SqliteChatHistoryStore


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path) -> _SqliteChatHistoryStore:
    return _SqliteChatHistoryStore(str(tmp_path / "chat.db"))


def _thread_columns(store: _SqliteChatHistoryStore) -> set[str]:
    conn = store._get_connection()
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(threads)").fetchall()}
    finally:
        conn.close()


class TestStoreOwner:
    def test_fresh_schema_has_user_id(self, store):
        store._ensure_schema()
        assert "user_id" in _thread_columns(store)

    def test_first_turn_sets_owner(self, store):
        _run(store.save_turn("t1", "q", "a", user_id="user-42"))
        assert _run(store.get_thread_user_id("t1")) == "user-42"

    def test_turn_without_user_id_keeps_owner(self, store):
        _run(store.save_turn("t1", "q1", "a1", user_id="user-42"))
        _run(store.save_turn("t1", "q2", "a2"))                 # app did not send it
        assert _run(store.get_thread_user_id("t1")) == "user-42"

    def test_owner_set_later_when_first_turn_had_none(self, store):
        _run(store.save_turn("t1", "q1", "a1"))                 # before rollout
        _run(store.save_turn("t1", "q2", "a2", user_id="user-42"))
        assert _run(store.get_thread_user_id("t1")) == "user-42"

    def test_different_user_cannot_rehome_thread(self, store, caplog):
        _run(store.save_turn("t1", "q1", "a1", user_id="user-A"))
        with caplog.at_level("WARNING"):
            turn = _run(store.save_turn("t1", "q2", "a2", user_id="user-B"))
        assert turn == 2                                        # answer still saved
        assert _run(store.get_thread_user_id("t1")) == "user-A"
        assert any("user_id mismatch" in r.getMessage() for r in caplog.records)

    def test_unknown_thread_reads_empty(self, store):
        assert _run(store.get_thread_user_id("nope")) == ""

    def test_turn_numbering_unchanged(self, store):
        assert _run(store.save_turn("t1", "q1", "a1", user_id="u")) == 1
        assert _run(store.save_turn("t1", "q2", "a2", user_id="u")) == 2
        assert _run(store.save_turn("t2", "q1", "a1")) == 1


class TestMigration:
    def test_legacy_db_without_user_id_is_migrated(self, tmp_path):
        """A database created before this change gains the column and keeps
        its rows; the old thread reads as unowned."""
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
            INSERT INTO threads (thread_id, total_turns) VALUES ('old', 1);
            INSERT INTO messages (thread_id, turn_number, user_query, ai_response)
                VALUES ('old', 1, 'q', 'a');
        """)
        conn.commit()
        conn.close()

        store = _SqliteChatHistoryStore(str(db_path))
        store._ensure_schema()
        assert "user_id" in _thread_columns(store)
        assert _run(store.get_thread_user_id("old")) == ""
        # the old thread can be claimed by its owner on the next turn
        assert _run(store.save_turn("old", "q2", "a2", user_id="user-7")) == 2
        assert _run(store.get_thread_user_id("old")) == "user-7"


class TestRequestModels:
    def test_search_request_accepts_user_id(self):
        from core.gateway import SearchRequest
        r = SearchRequest(Promptquery="hi", user_id="  user-42 ")
        assert r.user_id == "user-42"                           # stripped

    def test_search_request_user_id_optional(self):
        from core.gateway import SearchRequest
        assert SearchRequest(Promptquery="hi").user_id is None
        assert SearchRequest(Promptquery="hi", user_id="").user_id is None
        assert SearchRequest(Promptquery="hi", user_id="   ").user_id is None

    def test_search_request_user_id_too_long_rejected(self):
        from pydantic import ValidationError
        from core.gateway import SearchRequest
        with pytest.raises(ValidationError):
            SearchRequest(Promptquery="hi", user_id="x" * 129)

    def test_save_turn_request_accepts_user_id(self):
        from core.gateway import SaveTurnRequest
        r = SaveTurnRequest(thread_id="t", user_query="q", ai_response="a", user_id="u")
        assert r.user_id == "u"
        assert SaveTurnRequest(thread_id="t", user_query="q", ai_response="a").user_id is None

    def test_chat_endpoint_declares_user_id_form_field(self):
        """/pyapi/chat is multipart; the field must exist on the handler."""
        import inspect
        from core.gateway import chat_with_files
        assert "user_id" in inspect.signature(chat_with_files).parameters

    def test_runner_inputs_carry_user_id(self):
        from core.chat_runner import ChatRunnerInputs
        i = ChatRunnerInputs(agent_graph=None, query="q", thread_id="t",
                             is_first_turn=True, endpoint_name="/x")
        assert i.user_id is None
        i2 = ChatRunnerInputs(agent_graph=None, query="q", thread_id="t",
                              is_first_turn=True, endpoint_name="/x", user_id="u")
        assert i2.user_id == "u"
