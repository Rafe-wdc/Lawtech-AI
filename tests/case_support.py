"""Shared helpers for the case-workspace test suites.

``FakeCaseStore`` mirrors the owner-scoping semantics of ``core.case_store``
so the route tests run anywhere (no database). The real SQL is covered by
``tests/test_case_store_pg.py`` against Postgres.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import settings as _settings
from core.auth import require_user_key
from core.case_routes import build_case_router
from core.case_store import CASE_EDITABLE, get_case_store
from core.jwt_auth import mint_hs256

TEST_SECRET = "case-test-secret-not-for-prod"


def mint(user_id: str, secret: str = TEST_SECRET, ttl: int = 600, **extra) -> str:
    now = int(time.time())
    payload = {"userId": user_id, "iat": now, "exp": now + ttl}
    payload.update(extra)
    return mint_hs256(payload, secret)


def auth_headers(user_id: str, **kw) -> dict:
    return {"Authorization": f"Bearer {mint(user_id, **kw)}", "X-API-Key": "test-key"}


class FakeCaseStore:
    """In-memory CaseStoreProtocol with the same owner-scoped behaviour."""

    def __init__(self, clock=None):
        self.cases: dict[str, dict] = {}
        self.audit: list[dict] = []
        self._clock = clock or time.time

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), tz=timezone.utc)

    async def create_case(self, owner_user_id: str, data: dict) -> dict:
        now = self._now()
        case = {
            "case_id": str(uuid.uuid4()), "owner_user_id": owner_user_id,
            **{c: "" for c in CASE_EDITABLE},
            **{k: v for k, v in data.items() if k in CASE_EDITABLE},
            "status": "active", "doc_count": 0, "bytes_total": 0,
            "created_at": now, "updated_at": now,
        }
        self.cases[case["case_id"]] = case
        return dict(case)

    async def list_cases(self, owner_user_id: str, limit: int = 50, offset: int = 0):
        rows = [c for c in self.cases.values()
                if c["owner_user_id"] == owner_user_id and c["status"] != "deleted"]
        rows.sort(key=lambda c: c["updated_at"], reverse=True)
        return [dict(r) for r in rows[offset:offset + limit]], len(rows)

    async def get_case(self, case_id: str, owner_user_id: str) -> Optional[dict]:
        c = self.cases.get(case_id)
        if not c or c["owner_user_id"] != owner_user_id or c["status"] == "deleted":
            return None
        return dict(c)

    async def update_case(self, case_id: str, owner_user_id: str, fields: dict) -> Optional[dict]:
        c = self.cases.get(case_id)
        if not c or c["owner_user_id"] != owner_user_id or c["status"] == "deleted":
            return None
        for k, v in fields.items():
            if k in CASE_EDITABLE or (k == "status" and v in ("active", "archived")):
                c[k] = v
        if fields:
            c["updated_at"] = self._now()
        return dict(c)

    async def record_audit(self, case_id, user_id, action, object_id=None, request_id=None) -> None:
        self.audit.append({"case_id": case_id, "user_id": user_id, "action": action,
                           "object_id": object_id, "request_id": request_id, "at": self._clock()})

    async def record_view(self, case_id, user_id, request_id, window_sec: int) -> bool:
        now = self._clock()
        for row in self.audit:
            if (row["case_id"] == case_id and row["user_id"] == user_id
                    and row["action"] == "case_view" and row["at"] > now - window_sec):
                return False
        await self.record_audit(case_id, user_id, "case_view", None, request_id)
        return True


def enable_feature(monkeypatch, secret: str = TEST_SECRET, enabled: bool = True) -> None:
    monkeypatch.setattr(_settings, "CASE_WORKSPACE_ENABLED", enabled, raising=False)
    monkeypatch.setattr(_settings, "CASE_JWT_SECRET", secret, raising=False)
    monkeypatch.setattr(_settings, "CASE_JWT_CLAIM", "userId", raising=False)
    monkeypatch.setattr(_settings, "CASE_JWT_LEEWAY_S", 30, raising=False)
    monkeypatch.setattr(_settings, "CASE_AUDIT_VIEW_WINDOW_SEC", 900, raising=False)


def make_client(store, *, mount: bool = True) -> TestClient:
    """A minimal app with only the case router, no agent graph, no gateway."""
    app = FastAPI()
    if mount:
        app.include_router(build_case_router())
    # The service-key layer is existing gateway behaviour; keep tests independent
    # of whatever API_KEYS the local .env carries.
    app.dependency_overrides[require_user_key] = lambda: "test-key"
    app.dependency_overrides[get_case_store] = lambda: store
    return TestClient(app)
