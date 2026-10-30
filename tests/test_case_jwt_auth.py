"""core/jwt_auth.py — the shared HS256 verifier (stdlib only)."""
from __future__ import annotations

import json
import time

from core.jwt_auth import b64url_encode, mint_hs256, verify_hs256

SECRET = "s3cret"


def _now() -> int:
    return int(time.time())


def test_valid_token_returns_user_id_as_string():
    tok = mint_hs256({"userId": 42, "iat": _now(), "exp": _now() + 60}, SECRET)
    assert verify_hs256(tok, SECRET) == "42"


def test_wrong_secret_rejected():
    tok = mint_hs256({"userId": 42, "exp": _now() + 60}, SECRET)
    assert verify_hs256(tok, "other") == ""


def test_empty_secret_fails_closed():
    tok = mint_hs256({"userId": 42}, SECRET)
    assert verify_hs256(tok, "") == ""


def test_alg_none_rejected():
    header = b64url_encode(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = b64url_encode(json.dumps({"userId": 42}).encode())
    assert verify_hs256(f"{header}.{payload}.", SECRET) == ""


def test_rs256_header_rejected_even_with_valid_hmac():
    # A token signed with HMAC but claiming RS256 must not pass the alg pin.
    header = b64url_encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = b64url_encode(json.dumps({"userId": 42}).encode())
    import hashlib, hmac
    sig = hmac.new(SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    assert verify_hs256(f"{header}.{payload}.{b64url_encode(sig)}", SECRET) == ""


def test_expired_rejected_and_leeway_honoured():
    tok = mint_hs256({"userId": 42, "exp": _now() - 10}, SECRET)
    assert verify_hs256(tok, SECRET, leeway_s=30) == "42"
    assert verify_hs256(tok, SECRET, leeway_s=0) == ""


def test_not_before_rejected():
    tok = mint_hs256({"userId": 42, "nbf": _now() + 120}, SECRET)
    assert verify_hs256(tok, SECRET) == ""


def test_claim_order_and_custom_claim():
    tok = mint_hs256({"sub": "abc", "userId": 7}, SECRET)
    assert verify_hs256(tok, SECRET, claims=("sub",)) == "abc"
    assert verify_hs256(tok, SECRET, claims=("missing", "userId")) == "7"


def test_bad_id_characters_rejected():
    tok = mint_hs256({"userId": "42; drop table"}, SECRET)
    assert verify_hs256(tok, SECRET) == ""


def test_boolean_and_empty_claims_skipped():
    assert verify_hs256(mint_hs256({"userId": True}, SECRET), SECRET) == ""
    assert verify_hs256(mint_hs256({"userId": ""}, SECRET), SECRET) == ""


def test_malformed_tokens_never_raise():
    for tok in ("", "a", "a.b", "a.b.c", "x.y.z.w", "..", "é.é.é"):
        assert verify_hs256(tok, SECRET) == ""
