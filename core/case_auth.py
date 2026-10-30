"""Identity for the case workspace: who owns the case being touched.

The owner of every case is the ``userId`` claim of Node's HS256 access token,
sent as ``Authorization: Bearer <jwt>``. There is no bare-id or unsigned mode:
if the feature is on and no verification secret is configured, every case
route fails closed with 403. This mirrors the status contract the user-memory
feature agreed with Node (2026-10-28): 403 = feature off or unconfigured on
this server, 401 = token missing, expired or forged.

Settings are read from ``core.settings`` at call time (not import time) so
tests can monkeypatch them the way the existing suites do.
"""
from __future__ import annotations

from typing import Mapping

from fastapi import HTTPException, Request

from core import settings as _settings
from core.jwt_auth import verify_hs256
from core.logger import get_logger, short_err

log = get_logger("CaseAuth")

# Outcomes of verify_case_bearer(). Only AUTH_OK carries a user id.
AUTH_OK = "ok"
AUTH_ABSENT = "absent"              # no Authorization header -> 401
AUTH_INVALID = "invalid"            # token present but forged / expired / malformed -> 401
AUTH_UNCONFIGURED = "unconfigured"  # flag on, no secret: fail closed -> 403
AUTH_OFF = "off"                    # CASE_WORKSPACE_ENABLED not set -> 403 (router is normally not even mounted)


def feature_enabled() -> bool:
    return bool(getattr(_settings, "CASE_WORKSPACE_ENABLED", False))


def jwt_secret() -> str:
    return (getattr(_settings, "CASE_JWT_SECRET", "") or "").strip()


def jwt_claims() -> tuple[str, ...]:
    raw = (getattr(_settings, "CASE_JWT_CLAIM", "") or "").strip()
    if not raw:
        return ("userId",)
    return tuple(c.strip() for c in raw.split(",") if c.strip()) or ("userId",)


def jwt_leeway_s() -> float:
    try:
        return max(0.0, float(getattr(_settings, "CASE_JWT_LEEWAY_S", 30)))
    except (TypeError, ValueError):
        return 30.0


def _header(headers: Mapping[str, str], name: str) -> str:
    # Starlette headers are case-insensitive; plain dicts (tests) may not be.
    v = headers.get(name) or headers.get(name.lower()) or headers.get(name.title())
    return (v or "").strip()


def verify_case_bearer(headers: Mapping[str, str]) -> tuple[str, str]:
    """(user_id, status) for this request's identity headers."""
    if not feature_enabled():
        return "", AUTH_OFF
    secret = jwt_secret()
    if not secret:
        return "", AUTH_UNCONFIGURED
    auth = _header(headers, "Authorization")
    if not auth:
        return "", AUTH_ABSENT
    if not auth.lower().startswith("bearer "):
        return "", AUTH_INVALID
    uid = verify_hs256(auth[7:].strip(), secret, jwt_claims(), jwt_leeway_s())
    if not uid:
        return "", AUTH_INVALID
    hinted = _header(headers, "X-User-Id")
    if hinted and hinted != uid:
        log.warning("X-User-Id does not match the JWT claim", user=uid[:8])
        return "", AUTH_INVALID
    return uid, AUTH_OK


_warned_unconfigured = False


def resolve_case_user(request: Request) -> str:
    """FastAPI dependency: the verified owner id, or an HTTP error. Never ''."""
    global _warned_unconfigured
    try:
        user_id, status = verify_case_bearer(request.headers)
    except Exception as e:  # defensive: header parsing must never 500
        log.warning("Case identity resolution failed", error=short_err(e))
        raise HTTPException(status_code=401, detail="Missing, expired or invalid access token "
                                                    "(Authorization: Bearer <jwt>)")
    if status == AUTH_OFF:
        raise HTTPException(status_code=403, detail="Case workspace is not enabled on this server")
    if status == AUTH_UNCONFIGURED:
        if not _warned_unconfigured:
            _warned_unconfigured = True
            log.error("CASE_WORKSPACE_ENABLED=1 but CASE_JWT_SECRET (or USER_MEMORY_JWT_SECRET) "
                      "is not set: case routes fail closed")
        raise HTTPException(status_code=403, detail="Case workspace is enabled but CASE_JWT_SECRET "
                                                    "is not configured on this server")
    if status != AUTH_OK:
        raise HTTPException(status_code=401, detail="Missing, expired or invalid access token "
                                                    "(Authorization: Bearer <jwt>)")
    return user_id
