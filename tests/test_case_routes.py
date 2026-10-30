"""core/case_routes.py — slice 1 endpoints against the in-memory store fake.

Covers: flag gating, auth statuses at the HTTP layer, create/list/get/patch,
validation, cross-user isolation (404, never 403), audit writes and the
case_view throttle. The real SQL semantics are in test_case_store_pg.py.
"""
from __future__ import annotations

import uuid

import pytest

from tests.case_support import FakeCaseStore, auth_headers, enable_feature, make_client


@pytest.fixture
def store():
    return FakeCaseStore()


@pytest.fixture
def client(store, monkeypatch):
    enable_feature(monkeypatch)
    return make_client(store)


def _create(client, user="u1", **over):
    body = {"title": "Sharma v. Mehta Builders", "matter_type": "Cheque Dishonour NI138",
            "stage": "notice sent", "our_side": "Complainant", "cnr": "mhau01-012345-2026"}
    body.update(over)
    r = client.post("/pyapi/cases", json=body, headers=auth_headers(user))
    return r


# --- flag and auth ---------------------------------------------------------

def test_router_absent_when_flag_off(store, monkeypatch):
    enable_feature(monkeypatch, enabled=False)
    client = make_client(store, mount=False)      # gateway does not mount it when the flag is off
    assert client.get("/pyapi/cases", headers=auth_headers("u1")).status_code == 404


def test_mounted_but_flag_off_is_403(store, monkeypatch):
    enable_feature(monkeypatch, enabled=False)
    client = make_client(store)
    assert client.get("/pyapi/cases", headers=auth_headers("u1")).status_code == 403


def test_enabled_without_secret_is_403(store, monkeypatch):
    enable_feature(monkeypatch, secret="")
    client = make_client(store)
    assert client.get("/pyapi/cases", headers=auth_headers("u1")).status_code == 403


def test_missing_or_bad_token_is_401(client):
    assert client.get("/pyapi/cases", headers={"X-API-Key": "k"}).status_code == 401
    assert client.get("/pyapi/cases", headers={"X-API-Key": "k", "Authorization": "Bearer junk"}).status_code == 401
    assert client.get("/pyapi/cases", headers=auth_headers("u1", secret="wrong")).status_code == 401
    assert client.get("/pyapi/cases", headers=auth_headers("u1", ttl=-600)).status_code == 401


# --- CRUD -------------------------------------------------------------------

def test_create_normalises_and_returns_case(client, store):
    r = _create(client)
    assert r.status_code == 201, r.text
    body = r.json()
    uuid.UUID(body["case_id"])
    assert body["title"] == "Sharma v. Mehta Builders"
    assert body["matter_type"] == "cheque_dishonour_ni138"      # slugged, free text
    assert body["stage"] == "notice_sent"
    assert body["our_side"] == "complainant"
    assert body["cnr"] == "MHAU010123452026"
    assert body["status"] == "active" and body["doc_count"] == 0
    assert store.audit[-1]["action"] == "case_create"


def test_create_validation(client):
    assert client.post("/pyapi/cases", json={}, headers=auth_headers("u1")).status_code == 422
    assert client.post("/pyapi/cases", json={"title": "   "}, headers=auth_headers("u1")).status_code == 422
    assert client.post("/pyapi/cases", json={"title": "x" * 201}, headers=auth_headers("u1")).status_code == 422
    assert client.post("/pyapi/cases", json={"title": "ok", "cnr": "bad cnr!!"}, headers=auth_headers("u1")).status_code == 422
    assert client.post("/pyapi/cases", json={"title": "ok", "owner_user_id": "u2"}, headers=auth_headers("u1")).status_code == 422
    # unknown matter_type is accepted: config-driven, not an enum
    r = client.post("/pyapi/cases", json={"title": "ok", "matter_type": "Something New"}, headers=auth_headers("u1"))
    assert r.status_code == 201 and r.json()["matter_type"] == "something_new"


def test_list_get_patch_round_trip(client):
    cid = _create(client).json()["case_id"]
    _create(client, title="Second")
    lst = client.get("/pyapi/cases?limit=1", headers=auth_headers("u1")).json()
    assert lst["total"] == 2 and len(lst["items"]) == 1 and lst["limit"] == 1
    got = client.get(f"/pyapi/cases/{cid}", headers=auth_headers("u1"))
    assert got.status_code == 200 and got.json()["case_id"] == cid
    upd = client.patch(f"/pyapi/cases/{cid}", json={"stage": "Filed", "status": "archived"}, headers=auth_headers("u1"))
    assert upd.status_code == 200 and upd.json()["stage"] == "filed" and upd.json()["status"] == "archived"
    assert client.patch(f"/pyapi/cases/{cid}", json={"status": "deleted"}, headers=auth_headers("u1")).status_code == 422
    assert client.patch(f"/pyapi/cases/{cid}", json={"doc_count": 9}, headers=auth_headers("u1")).status_code == 422
    assert client.patch(f"/pyapi/cases/{cid}", json={}, headers=auth_headers("u1")).status_code == 200
    assert client.get("/pyapi/cases?limit=0", headers=auth_headers("u1")).status_code == 422
    assert client.get("/pyapi/cases?limit=201", headers=auth_headers("u1")).status_code == 422


def test_meta_lists_suggestions(client):
    r = client.get("/pyapi/cases/meta", headers=auth_headers("u1"))
    assert r.status_code == 200
    body = r.json()
    assert {"value": "cheque_dishonour_ni138", "label": "Cheque dishonour (s.138 NI Act)"} in body["matter_types"]
    assert body["limits"]["title_max"] == 200


# --- isolation --------------------------------------------------------------

def test_cross_user_access_is_404_not_403(client):
    cid = _create(client, user="u1").json()["case_id"]
    other = auth_headers("u2")
    assert client.get(f"/pyapi/cases/{cid}", headers=other).status_code == 404
    assert client.patch(f"/pyapi/cases/{cid}", json={"title": "Hijack"}, headers=other).status_code == 404
    assert client.get("/pyapi/cases", headers=other).json()["total"] == 0
    # and the owner's record is untouched
    assert client.get(f"/pyapi/cases/{cid}", headers=auth_headers("u1")).json()["title"] == "Sharma v. Mehta Builders"


def test_unknown_and_malformed_ids_are_404(client):
    assert client.get(f"/pyapi/cases/{uuid.uuid4()}", headers=auth_headers("u1")).status_code == 404
    assert client.get("/pyapi/cases/not-a-uuid", headers=auth_headers("u1")).status_code == 404


# --- audit ------------------------------------------------------------------

def test_view_audit_is_throttled_per_user_per_case(monkeypatch):
    now = [1_000_000.0]
    store = FakeCaseStore(clock=lambda: now[0])
    enable_feature(monkeypatch)
    client = make_client(store)
    cid = _create(client).json()["case_id"]
    for _ in range(5):
        client.get(f"/pyapi/cases/{cid}", headers=auth_headers("u1"))
    views = [a for a in store.audit if a["action"] == "case_view"]
    assert len(views) == 1
    now[0] += 15 * 60 + 1
    client.get(f"/pyapi/cases/{cid}", headers=auth_headers("u1"))
    assert len([a for a in store.audit if a["action"] == "case_view"]) == 2


def test_router_accepts_gateway_limiter(store, monkeypatch):
    """core/gateway.py mounts the router with its slowapi limiter; the factory
    must wrap every handler the same way the gateway's own routes are wrapped."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address
    from core.auth import require_user_key
    from core.case_routes import build_case_router
    from core.case_store import get_case_store

    enable_feature(monkeypatch)
    limiter = Limiter(key_func=get_remote_address)
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(build_case_router(limiter=limiter, limit_value=lambda: "2/minute"))
    app.dependency_overrides[require_user_key] = lambda: "k"
    app.dependency_overrides[get_case_store] = lambda: store
    c = TestClient(app)
    codes = [c.post("/pyapi/cases", json={"title": f"t{i}"}, headers=auth_headers("u1")).status_code
             for i in range(3)]
    assert codes == [201, 201, 429]


def test_patch_audits_changed_fields_only(client, store):
    cid = _create(client).json()["case_id"]
    client.patch(f"/pyapi/cases/{cid}", json={}, headers=auth_headers("u1"))
    assert not [a for a in store.audit if a["action"] == "case_update"]
    client.patch(f"/pyapi/cases/{cid}", json={"forum": "JMFC Andheri"}, headers=auth_headers("u1"))
    upd = [a for a in store.audit if a["action"] == "case_update"]
    assert len(upd) == 1 and upd[0]["object_id"] == "forum"
