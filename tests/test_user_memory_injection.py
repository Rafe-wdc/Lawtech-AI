"""User memory: injection placement (after the cached static prompt) and the
review API routes. docs/user_memory_design.md sections 5 and 7.
"""

import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import asyncio

import pytest

import core.clients as clients
from core import user_memory as um

BLOCK = "## ABOUT THE ADVOCATE USING THIS ASSISTANT (saved profile)\nAdvocate: Adv. A\n"


def test_anthropic_trailer_is_a_second_uncached_block(monkeypatch):
    monkeypatch.setitem(clients.GEMINI_MODELS, "generation", "anthropic:claude-sonnet-5")
    out = clients.cacheable_system("STATIC", trailer=BLOCK)
    assert out == [
        {"type": "text", "text": "STATIC", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": BLOCK},
    ]
    # No trailer: byte-identical to the pre-memory behaviour.
    assert clients.cacheable_system("STATIC", trailer="") == [
        {"type": "text", "text": "STATIC", "cache_control": {"type": "ephemeral"}},
    ]


def test_gemini_trailer_is_appended_after_the_static_text(monkeypatch):
    monkeypatch.setitem(clients.GEMINI_MODELS, "generation", "gemini-3.8-flash")
    assert clients.cacheable_system("STATIC", trailer=BLOCK) == "STATIC\n\n" + BLOCK
    assert clients.cacheable_system("STATIC", trailer="") == "STATIC"


def test_default_trailer_comes_from_the_request_context(monkeypatch):
    monkeypatch.setitem(clients.GEMINI_MODELS, "generation", "anthropic:claude-sonnet-5")
    assert clients.cacheable_system("STATIC") == [
        {"type": "text", "text": "STATIC", "cache_control": {"type": "ephemeral"}},
    ]
    token = um.set_request_profile_block(BLOCK)
    try:
        assert clients.cacheable_system("STATIC")[1] == {"type": "text", "text": BLOCK}

        async def in_thread():
            # asyncio.to_thread copies the context, as the drafting generators rely on.
            return await asyncio.to_thread(clients.cacheable_system, "STATIC")
        assert asyncio.run(in_thread())[1]["text"] == BLOCK
    finally:
        um.reset_request_profile_block(token)
    assert um.current_profile_block() == ""
    assert len(clients.cacheable_system("STATIC")) == 1


def test_initial_state_carries_memory_keys():
    from core.gateway import _build_initial_state
    s = _build_initial_state("q", "t", user_id="u1", user_profile_block=BLOCK)
    assert s["user_id"] == "u1" and s["user_profile_block"] == BLOCK
    s = _build_initial_state("q", "t")
    assert s["user_id"] == "" and s["user_profile_block"] == ""


# --- review API -------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    import core.chat_store as cs
    import core.gateway as gw
    from fastapi.testclient import TestClient

    store = cs._SqliteChatHistoryStore(str(tmp_path / "mem.db"))
    monkeypatch.setattr(cs, "chat_store", store)
    monkeypatch.setattr(gw, "chat_store", store)
    monkeypatch.setenv("USER_MEMORY_ENABLED", "1")
    monkeypatch.setenv("USER_MEMORY_ID_SECRET", "s3cret")
    monkeypatch.delenv("USER_MEMORY_JWT_SECRET", raising=False)
    gw.app.dependency_overrides[gw.require_user_key] = lambda: "test-key"
    try:
        # No `with`: the lifespan (graph compile, ES) must not run for these routes.
        yield TestClient(gw.app), store
    finally:
        gw.app.dependency_overrides.pop(gw.require_user_key, None)


def _hdrs(user="user_42"):
    return {"X-User-Id": user, "X-User-Sig": um.hmac_sign_user_id(user, "s3cret")}


def test_memory_routes_round_trip(client):
    c, store = client
    r = c.get("/pyapi/memory", headers=_hdrs())
    assert r.status_code == 200
    body = r.json()
    assert body["memory_on"] is True and body["rows"] == []
    assert any(s["slot"] == "designation" for s in body["slots"])

    r = c.post("/pyapi/memory", json={"slot": "advocate_name", "value": "Adv. A"}, headers=_hdrs())
    assert r.status_code == 200
    row = r.json()["rows"][0]
    assert row["source"] == "user_stated" and row["confirmed"] is True

    r = c.post("/pyapi/memory", json={"slot": "note", "value": "hearing 03/11/2026"}, headers=_hdrs())
    assert r.status_code == 400 and "matter data" in r.json()["message"]

    r = c.post("/pyapi/memory", json={"slot": "nope", "value": "x"}, headers=_hdrs())
    assert r.status_code == 400

    # Confirm an inferred identity row via PATCH (no value change).
    asyncio.run(store.insert_user_memory("user_42", "phone", "+91 98230 12345", "inferred", 0.9))
    rows = asyncio.run(store.load_user_memory("user_42"))
    phone = next(r for r in rows if r["slot"] == "phone")
    r = c.patch(f"/pyapi/memory/{phone['id']}", json={}, headers=_hdrs())
    assert r.status_code == 200
    got = next(x for x in r.json()["rows"] if x["slot"] == "phone")
    assert got["confirmed"] is True and got["injected"] is True

    r = c.patch(f"/pyapi/memory/{phone['id']}", json={"value": "+91 90000 00000"}, headers=_hdrs())
    assert r.status_code == 200

    # Other user cannot see or delete it.
    assert c.get("/pyapi/memory", headers=_hdrs("user_43")).json()["rows"] == []
    assert c.delete(f"/pyapi/memory/{phone['id']}", headers=_hdrs("user_43")).status_code == 404

    r = c.put("/pyapi/memory/settings", json={"memory_on": False}, headers=_hdrs())
    assert r.status_code == 200
    assert c.get("/pyapi/memory", headers=_hdrs()).json()["memory_on"] is False

    assert c.delete(f"/pyapi/memory/{phone['id']}", headers=_hdrs()).status_code == 200
    r = c.delete("/pyapi/memory", headers=_hdrs())
    assert r.status_code == 200 and r.json()["deleted"] == 1
    assert c.get("/pyapi/memory", headers=_hdrs()).json()["rows"] == []


def test_memory_routes_auth_and_flag(client, monkeypatch):
    c, _ = client
    # Bad signature -> 401.
    assert c.get("/pyapi/memory", headers={"X-User-Id": "user_42", "X-User-Sig": "00"}).status_code == 401
    assert c.get("/pyapi/memory", headers={"X-User-Id": "user_42"}).status_code == 401
    assert c.get("/pyapi/memory").status_code == 401
    monkeypatch.setenv("USER_MEMORY_ENABLED", "0")
    assert c.get("/pyapi/memory", headers=_hdrs()).status_code == 404
