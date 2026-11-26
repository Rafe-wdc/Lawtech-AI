"""User identity from the Node login JWT (background runs, 2026-11-26)."""
from __future__ import annotations

import time

import jwt
import pytest

from core import settings as S
from core.user_auth import (
    Identity, IdentityError, bearer_token, may_access_run, resolve_identity,
    user_id_from_claims, verify_token,
)

SECRET = "test-node-secret-that-is-long-enough-for-hs256"


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(S, "USER_JWT_SECRET", SECRET)
    monkeypatch.setattr(S, "USER_JWT_PUBLIC_KEY", "")
    monkeypatch.setattr(S, "USER_JWT_ALGORITHMS", ["HS256"])
    monkeypatch.setattr(S, "USER_JWT_ISSUER", "")
    monkeypatch.setattr(S, "USER_JWT_AUDIENCE", "")
    monkeypatch.setattr(S, "USER_JWT_USER_CLAIM", ["sub", "userId", "id", "_id", "user._id", "user.id"])
    monkeypatch.setattr(S, "USER_JWT_LEEWAY_S", 0)
    monkeypatch.setattr(S, "USER_JWT_REQUIRE_EXP", True)
    monkeypatch.setattr(S, "USER_AUTH_MODE", "optional")


def tok(claims=None, *, secret=SECRET, alg="HS256", exp_in=600):
    c = {"userId": "u-42"} if claims is None else dict(claims)
    if exp_in is not None:
        c["exp"] = int(time.time()) + exp_in
    return jwt.encode(c, secret, algorithm=alg)


class TestToken:
    def test_bearer_parsing(self):
        assert bearer_token("Bearer abc") == "abc"
        assert bearer_token("bearer  abc ") == "abc"
        assert bearer_token("Basic abc") is None
        assert bearer_token("") is None and bearer_token(None) is None

    def test_valid_token(self):
        assert verify_token(tok())["userId"] == "u-42"

    def test_expired(self):
        with pytest.raises(IdentityError) as e:
            verify_token(tok(exp_in=-10))
        assert e.value.status_code == 401 and "expired" in e.value.detail

    def test_wrong_secret(self):
        with pytest.raises(IdentityError):
            verify_token(tok(secret="another-secret-also-long-enough-for-hs256"))

    def test_missing_exp_rejected_unless_allowed(self, monkeypatch):
        t = tok(exp_in=None)
        with pytest.raises(IdentityError):
            verify_token(t)
        monkeypatch.setattr(S, "USER_JWT_REQUIRE_EXP", False)
        assert verify_token(t)["userId"] == "u-42"

    def test_algorithm_is_pinned(self):
        # alg=none must never verify
        unsigned = jwt.encode({"userId": "u-42", "exp": int(time.time()) + 60}, None, algorithm="none")
        with pytest.raises(IdentityError):
            verify_token(unsigned)

    def test_audience_and_issuer_checked_when_set(self, monkeypatch):
        monkeypatch.setattr(S, "USER_JWT_AUDIENCE", "pyapi")
        monkeypatch.setattr(S, "USER_JWT_ISSUER", "node")
        with pytest.raises(IdentityError):
            verify_token(tok({"userId": "u", "aud": "other", "iss": "node"}))
        assert verify_token(tok({"userId": "u", "aud": "pyapi", "iss": "node"}))["userId"] == "u"

    def test_no_key_configured(self, monkeypatch):
        monkeypatch.setattr(S, "USER_JWT_SECRET", "")
        with pytest.raises(IdentityError):
            verify_token(tok())

    @pytest.mark.parametrize("claims,expected", [
        ({"sub": "a"}, "a"), ({"userId": 7}, "7"), ({"_id": "m"}, "m"),
        ({"user": {"_id": "nested"}}, "nested"), ({"email": "x@y"}, ""),
        ({"sub": "", "id": "fallback"}, "fallback"),
    ])
    def test_user_claim_order(self, claims, expected):
        assert user_id_from_claims(claims) == expected


class TestResolve:
    def test_off_mode_uses_payload_and_ignores_token(self):
        ident = resolve_identity(authorization="Bearer garbage", payload_user_id="p-1", mode="off")
        assert ident == Identity("p-1", "payload")
        assert resolve_identity(mode="off") == Identity("", "none")

    def test_bearer_sets_verified_user(self):
        ident = resolve_identity(authorization=f"Bearer {tok()}")
        assert ident == Identity("u-42", "jwt") and ident.verified

    def test_integration_token_used_when_no_bearer(self):
        assert resolve_identity(integration_token=tok()) == Identity("u-42", "jwt")

    def test_bearer_wins_over_integration_token(self):
        other = tok({"userId": "someone-else"})
        assert resolve_identity(authorization=f"Bearer {tok()}",
                                integration_token=other).user_id == "u-42"

    def test_invalid_bearer_is_401_in_optional(self):
        with pytest.raises(IdentityError) as e:
            resolve_identity(authorization="Bearer not-a-jwt")
        assert e.value.status_code == 401

    def test_invalid_integration_token_ignored_in_optional(self):
        ident = resolve_identity(integration_token="not-a-jwt", payload_user_id="p-1")
        assert ident == Identity("p-1", "payload")

    def test_payload_fallback_in_optional(self):
        assert resolve_identity(payload_user_id=" p-1 ") == Identity("p-1", "payload")

    def test_mismatch_is_400(self):
        with pytest.raises(IdentityError) as e:
            resolve_identity(authorization=f"Bearer {tok()}", payload_user_id="other")
        assert e.value.status_code == 400

    def test_matching_payload_is_fine(self):
        assert resolve_identity(authorization=f"Bearer {tok()}",
                                payload_user_id="u-42") == Identity("u-42", "jwt")

    def test_required_without_token_is_401(self):
        with pytest.raises(IdentityError) as e:
            resolve_identity(payload_user_id="p-1", mode="required")
        assert e.value.status_code == 401

    def test_required_with_bad_integration_token_is_401(self):
        with pytest.raises(IdentityError):
            resolve_identity(integration_token="nope", mode="required")

    def test_token_without_user_claim_is_401(self):
        with pytest.raises(IdentityError):
            resolve_identity(authorization=f"Bearer {tok({'email': 'a@b'})}")

    def test_optional_without_key_falls_back_to_payload(self, monkeypatch):
        monkeypatch.setattr(S, "USER_JWT_SECRET", "")
        assert resolve_identity(authorization=f"Bearer {tok()}",
                                payload_user_id="p-1") == Identity("p-1", "payload")

    def test_unknown_mode_is_strict(self, monkeypatch):
        monkeypatch.setattr(S, "USER_AUTH_MODE", "maybe")
        with pytest.raises(IdentityError):
            resolve_identity(payload_user_id="p-1")


class TestRunAccess:
    def test_owner_only_in_optional(self):
        assert may_access_run("u-1", Identity("u-1", "jwt"))
        assert not may_access_run("u-1", Identity("u-2", "jwt"))
        assert not may_access_run("u-1", Identity("", "none"))

    def test_unowned_run_open_unless_required(self):
        assert may_access_run("", Identity("", "none"), mode="optional")
        assert not may_access_run("", Identity("u-1", "jwt"), mode="required")

    def test_admin_and_off_mode_bypass(self):
        assert may_access_run("u-1", Identity("u-2", "jwt"), admin=True)
        assert may_access_run("u-1", Identity("", "none"), mode="off")
