"""User memory: SQLite store methods, apply_ops, after-turn extraction and the
remember / forget shortcut, end to end against a temp database with the
extractor LLM stubbed. docs/user_memory_design.md.
"""

import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import asyncio

import pytest

import core.chat_store as cs
from core import user_memory as um


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = cs._SqliteChatHistoryStore(str(tmp_path / "mem.db"))
    monkeypatch.setattr(cs, "chat_store", s)
    monkeypatch.setenv("USER_MEMORY_ENABLED", "1")
    monkeypatch.delenv("USER_MEMORY_ID_SECRET", raising=False)
    monkeypatch.delenv("USER_MEMORY_JWT_SECRET", raising=False)
    return s


def run(coro):
    return asyncio.run(coro)


# --- store primitives ------------------------------------------------------

def test_store_crud_and_settings(store):
    assert run(store.load_user_memory("u1")) == []
    rid = run(store.insert_user_memory("u1", "advocate_name", "Adv. A", "user_stated", 1.0, "t1"))
    assert rid > 0
    # Same (user, slot, value) upserts rather than duplicating.
    rid2 = run(store.insert_user_memory("u1", "advocate_name", "Adv. A", "inferred", 0.8, "t2"))
    assert rid2 == rid
    rows = run(store.load_user_memory("u1"))
    assert len(rows) == 1 and rows[0]["source"] == "inferred" and rows[0]["thread_id"] == "t1"
    assert run(store.update_user_memory(rid, "u1", "Adv. B", "user_stated", 1.0))
    assert run(store.load_user_memory("u1"))[0]["value"] == "Adv. B"
    # Another user cannot touch it.
    assert not run(store.update_user_memory(rid, "u2", "X", "user_stated", 1.0))
    assert not run(store.delete_user_memory(rid, "u2"))
    assert run(store.load_user_memory("u2")) == []
    assert run(store.delete_user_memory(rid, "u1"))
    assert run(store.load_user_memory("u1")) == []

    assert run(store.get_user_memory_enabled("u1")) is True
    run(store.set_user_memory_enabled("u1", False))
    assert run(store.get_user_memory_enabled("u1")) is False
    run(store.set_user_memory_enabled("u1", True))
    assert run(store.get_user_memory_enabled("u1")) is True

    assert run(store.get_thread_no_memory("thread-x")) is False
    run(store.set_thread_no_memory("thread-x", True))       # thread row created on demand
    assert run(store.get_thread_no_memory("thread-x")) is True
    # The ordinary turn save still works on that pre-created thread row.
    turn = run(store.save_turn("thread-x", "hi", "hello"))
    assert turn == 1
    assert run(store.get_thread_no_memory("thread-x")) is True


def test_delete_all(store):
    for v in ("a", "b", "c"):
        run(store.insert_user_memory("u1", "note", v, "user_stated", 1.0))
    run(store.insert_user_memory("u2", "note", "z", "user_stated", 1.0))
    assert run(store.delete_user_memory_all("u1")) == 3
    assert run(store.load_user_memory("u1")) == []
    assert len(run(store.load_user_memory("u2"))) == 1


# --- apply_ops ---------------------------------------------------------------

def test_apply_ops_single_update_multi_add_delete_and_caps(store):
    rows = []
    ops, _ = um.validate_ops([
        {"op": "ADD", "slot": "office_address", "value": "201 Lex Towers, Pune",
         "source": "user_stated", "confidence": 1.0},
        {"op": "ADD", "slot": "courts", "value": "Bombay High Court",
         "source": "inferred", "confidence": 0.9},
    ], rows)
    applied = run(um.apply_ops("u1", "t1", ops, rows))
    assert [o["op"] for o in applied] == ["ADD", "ADD"]

    rows = run(store.load_user_memory("u1"))
    ops, _ = um.validate_ops([
        {"op": "ADD", "slot": "office_address", "value": "5th floor, Sadhu Vaswani Chowk, Pune",
         "source": "user_stated", "confidence": 1.0},
        {"op": "DELETE", "slot": "courts", "value": "Bombay High Court",
         "source": "inferred", "confidence": 0.9},
    ], rows)
    applied = run(um.apply_ops("u1", "t2", ops, rows))
    assert [o["op"] for o in applied] == ["UPDATE", "DELETE"]
    rows = run(store.load_user_memory("u1"))
    assert len(rows) == 1
    assert rows[0]["slot"] == "office_address"
    assert rows[0]["value"] == "5th floor, Sadhu Vaswani Chowk, Pune"
    assert rows[0]["thread_id"] == "t1"       # learned-in thread is kept on update

    # Multi-slot cap: 8 rows, the 9th evicts the oldest inferred row.
    for i in range(8):
        run(store.insert_user_memory("u1", "practice_areas", f"area {i}",
                                     "inferred" if i == 0 else "user_stated", 0.9))
    rows = run(store.load_user_memory("u1"))
    ops, _ = um.validate_ops([{"op": "ADD", "slot": "practice_areas", "value": "area 8",
                               "source": "user_stated", "confidence": 1.0}], rows)
    run(um.apply_ops("u1", "t3", ops, rows))
    values = {r["value"] for r in run(store.load_user_memory("u1")) if r["slot"] == "practice_areas"}
    assert len(values) == 8 and "area 8" in values and "area 0" not in values
    # All user_stated and full: the ADD is dropped.
    rows = run(store.load_user_memory("u1"))
    ops, _ = um.validate_ops([{"op": "ADD", "slot": "practice_areas", "value": "area 9",
                               "source": "user_stated", "confidence": 1.0}], rows)
    assert run(um.apply_ops("u1", "t3", ops, rows)) == []


# --- extraction pipeline with a stubbed extractor -----------------------------

class _FakeLLM:
    def __init__(self, text):
        self.text_out = text
        self.messages = None

    def invoke(self, messages):
        self.messages = messages

        class R:
            text = self.text_out
            content = self.text_out
        return R()


def _stub_extractor(monkeypatch, text):
    fake = _FakeLLM(text)
    monkeypatch.setattr(um, "_extractor_llm", lambda: fake)
    return fake


def test_after_turn_extraction_applies_validated_ops(store, monkeypatch):
    fake = _stub_extractor(monkeypatch, """{"ops": [
        {"op": "ADD", "slot": "advocate_name", "value": "Adv. Priya Kulkarni", "source": "user_stated", "confidence": 1.0},
        {"op": "ADD", "slot": "enrolment_no", "value": "MAH/1234/2015", "source": "user_stated", "confidence": 1.0},
        {"op": "ADD", "slot": "note", "value": "client Ramesh Yadav FIR 112/2026", "source": "inferred", "confidence": 0.9}
    ]}""")
    msg = ("Draft a bail application for my client Ramesh Yadav, FIR 112/2026 PS Hadapsar. "
           "I'm Adv. Priya Kulkarni, enrolment MAH/1234/2015.")
    applied = run(um.after_turn_extraction("u1", "t1", msg))
    assert sorted(o["slot"] for o in applied) == ["advocate_name", "enrolment_no"]
    rows = run(store.load_user_memory("u1"))
    assert {r["slot"] for r in rows} == {"advocate_name", "enrolment_no"}
    # The extractor saw the profile and the user text, never an assistant reply.
    sent = fake.messages[0].content
    assert "## Current profile" in sent and "## This turn" in sent
    assert "Assistant" not in sent.split("## This turn")[1]


def test_after_turn_extraction_gates(store, monkeypatch):
    calls = []
    monkeypatch.setattr(um, "extract_ops", lambda *a, **k: calls.append(a) or asyncio.sleep(0, []))
    # No first-person marker: no call.
    run(um.after_turn_extraction("u1", "t1", "what does section 138 say"))
    # Pasted document: no call.
    run(um.after_turn_extraction("u1", "t1", "IN THE HON'BLE HIGH COURT\nmy client ..."))
    # Flag off: no call.
    monkeypatch.setenv("USER_MEMORY_ENABLED", "0")
    run(um.after_turn_extraction("u1", "t1", "I'm Adv. A"))
    monkeypatch.setenv("USER_MEMORY_ENABLED", "1")
    # User switched memory off: no call.
    run(store.set_user_memory_enabled("u1", False))
    run(um.after_turn_extraction("u1", "t1", "I'm Adv. A"))
    run(store.set_user_memory_enabled("u1", True))
    # Thread marked no_memory: no call.
    run(store.set_thread_no_memory("t-private", True))
    run(um.after_turn_extraction("u1", "t-private", "I'm Adv. A"))
    assert calls == []
    # Normal turn: one call.
    run(um.after_turn_extraction("u1", "t1", "I'm Adv. A"))
    assert len(calls) == 1


def test_after_turn_extraction_never_raises(store, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("llm down")
    monkeypatch.setattr(um, "_extractor_llm", boom)
    assert run(um.after_turn_extraction("u1", "t1", "I'm Adv. A")) == []


def test_remember_command_saves_and_confirms(store, monkeypatch):
    _stub_extractor(monkeypatch, """{"ops": [
        {"op": "ADD", "slot": "advocate_name", "value": "Adv. Priya S. Kulkarni", "source": "inferred", "confidence": 0.8},
        {"op": "ADD", "slot": "enrolment_no", "value": "MAH/1234/2015", "source": "user_stated", "confidence": 1.0}
    ]}""")
    kind, rest = um.parse_direct_command("Remember that I'm Adv. Priya S. Kulkarni, enrolment MAH/1234/2015")
    reply = run(um.handle_direct_command("u1", "t1", kind, rest))
    assert reply.startswith("Saved to your profile:")
    assert "- Advocate name: Adv. Priya S. Kulkarni" in reply
    assert "- Enrolment number: MAH/1234/2015" in reply
    rows = run(store.load_user_memory("u1"))
    # Explicit directives are user_stated even if the model said inferred.
    assert all(r["source"] == "user_stated" for r in rows)


def test_remember_matter_fact_is_refused(store, monkeypatch):
    _stub_extractor(monkeypatch, """{"ops": [
        {"op": "ADD", "slot": "note", "value": "hearing on 3 November 2026", "source": "user_stated", "confidence": 1.0}
    ]}""")
    reply = run(um.handle_direct_command("u1", "t1", "remember", "the hearing is on 3 November 2026"))
    assert reply.startswith("Nothing was saved.")
    assert "detail of a matter" in reply
    assert run(store.load_user_memory("u1")) == []


def test_forget_command_deletes_and_confirms(store, monkeypatch):
    run(store.insert_user_memory("u1", "phone", "+91 98230 12345", "user_stated", 1.0))
    run(store.insert_user_memory("u1", "email", "p@x.in", "user_stated", 1.0))
    _stub_extractor(monkeypatch, """{"ops": [
        {"op": "DELETE", "slot": "phone", "value": "+91 98230 12345"}
    ]}""")
    reply = run(um.handle_direct_command("u1", "t1", "forget", "my phone number"))
    assert reply == "Removed from your profile:\n- Phone: +91 98230 12345"
    assert [r["slot"] for r in run(store.load_user_memory("u1"))] == ["email"]


def test_forget_all_and_no_memory_thread(store, monkeypatch):
    run(store.insert_user_memory("u1", "phone", "+91 98230 12345", "user_stated", 1.0))
    assert run(um.handle_direct_command("u1", "t1", "forget_all", "everything")).startswith("Cleared your saved profile (1 item removed)")
    assert run(store.load_user_memory("u1")) == []
    run(store.set_thread_no_memory("t-private", True))
    assert run(um.handle_direct_command("u1", "t-private", "remember", "I'm Adv. A")) == \
        "Memory is off for this chat, so nothing was saved or changed."
    run(store.set_user_memory_enabled("u1", False))
    assert "switched off for your account" in run(um.handle_direct_command("u1", "t1", "remember", "I'm Adv. A"))


def test_load_profile_block_respects_setting(store):
    run(store.insert_user_memory("u1", "advocate_name", "Adv. A", "user_stated", 1.0))
    assert "Advocate: Adv. A" in run(um.load_profile_block("u1"))
    run(store.set_user_memory_enabled("u1", False))
    assert run(um.load_profile_block("u1")) == ""
    assert run(um.load_profile_block("")) == ""


def test_rows_for_review_marks_unconfirmed_identity(store):
    run(store.insert_user_memory("u1", "advocate_name", "Adv. A", "inferred", 0.9))
    run(store.insert_user_memory("u1", "courts", "Bombay High Court", "inferred", 0.9))
    out = {r["slot"]: r for r in um.rows_for_review(run(store.load_user_memory("u1")))}
    assert out["advocate_name"]["confirmed"] is False and out["advocate_name"]["injected"] is False
    assert out["courts"]["confirmed"] is False and out["courts"]["injected"] is True
    assert out["advocate_name"]["label"] == "Advocate name"
