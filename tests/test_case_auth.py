"""core/case_auth.py — status contract: 403 off / unconfigured, 401 bad token."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from core import case_auth
from tests.case_support import TEST_SECRET, enable_feature, mint


def _request(headers: dict) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "query_string": b""})


def test_feature_off_is_auth_off(monkeypatch):
    enable_feature(monkeypatch, enabled=False)
    assert case_auth.verify_case_bearer({"Authorization": f"Bearer {mint('1')}"}) == ("", case_auth.AUTH_OFF)
    with pytest.raises(HTTPException) as e:
        case_auth.resolve_case_user(_request({"Authorization": f"Bearer {mint('1')}"}))
    assert e.value.status_code == 403


def test_enabled_without_secret_fails_closed(monkeypatch):
    enable_feature(monkeypatch, secret="")
    assert case_auth.verify_case_bearer({"Authorization": f"Bearer {mint('1')}"})[1] == case_auth.AUTH_UNCONFIGURED
    with pytest.raises(HTTPException) as e:
        case_auth.resolve_case_user(_request({"Authorization": f"Bearer {mint('1')}"}))
    assert e.value.status_code == 403


def test_absent_and_invalid_tokens_are_401(monkeypatch):
    enable_feature(monkeypatch)
    assert case_auth.verify_case_bearer({}) == ("", case_auth.AUTH_ABSENT)
    assert case_auth.verify_case_bearer({"Authorization": "Basic abc"}) == ("", case_auth.AUTH_INVALID)
    assert case_auth.verify_case_bearer({"Authorization": f"Bearer {mint('1', secret='wrong')}"}) == ("", case_auth.AUTH_INVALID)
    assert case_auth.verify_case_bearer({"Authorization": f"Bearer {mint('1', ttl=-120)}"}) == ("", case_auth.AUTH_INVALID)
    for headers in ({}, {"Authorization": "Bearer nope"}):
        with pytest.raises(HTTPException) as e:
            case_auth.resolve_case_user(_request(headers))
        assert e.value.status_code == 401


def test_valid_token_resolves_user(monkeypatch):
    enable_feature(monkeypatch)
    assert case_auth.verify_case_bearer({"Authorization": f"Bearer {mint('42')}"}) == ("42", case_auth.AUTH_OK)
    assert case_auth.resolve_case_user(_request({"authorization": f"Bearer {mint('42')}"})) == "42"


def test_x_user_id_hint_must_match_claim(monkeypatch):
    enable_feature(monkeypatch)
    tok = mint("42")
    assert case_auth.verify_case_bearer({"Authorization": f"Bearer {tok}", "X-User-Id": "42"})[1] == case_auth.AUTH_OK
    assert case_auth.verify_case_bearer({"Authorization": f"Bearer {tok}", "X-User-Id": "43"})[1] == case_auth.AUTH_INVALID


def test_no_unsigned_mode_exists(monkeypatch):
    """A bare X-User-Id never authenticates, whatever else is set."""
    enable_feature(monkeypatch)
    assert case_auth.verify_case_bearer({"X-User-Id": "42"}) == ("", case_auth.AUTH_ABSENT)


def test_claim_override_from_settings(monkeypatch):
    enable_feature(monkeypatch)
    monkeypatch.setattr(case_auth._settings, "CASE_JWT_CLAIM", "sub", raising=False)
    tok = mint("ignored", sub="u-9")
    assert case_auth.verify_case_bearer({"Authorization": f"Bearer {tok}"}) == ("u-9", case_auth.AUTH_OK)
    assert TEST_SECRET  # keep import used
