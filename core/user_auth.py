"""Who is asking: the app user, proven by the Node login JWT.

(Not core/identity.py, which keeps the model provider out of answers.)

`X-API-Key` identifies the app, not the user. Background runs need the user
(to file the answer and push the notification) and must keep users out of
each other's runs, so a `user_id` typed into the request body is not enough
on its own. This module verifies the Node-issued JWT offline.

Token sources, in order:
  1. `Authorization: Bearer <jwt>`
  2. `integration_token` (the same Node JWT the app already sends for
     Google Docs / Notion extraction)

Modes (settings.USER_AUTH_MODE):
  off       Nothing is verified. The payload user_id is used as sent (the
            behaviour before background runs). Run endpoints do not check
            ownership beyond the API key.
  optional  A valid token sets the user. An invalid Bearer token is 401; an
            invalid integration_token is ignored for identity (it may be a
            token Python cannot verify, e.g. when Node mints a separate
            internal token for Python). Without a token the payload user_id
            is accepted and marked source="payload". Rollout mode.
  required  A valid token is required (401 otherwise).
In optional and required, a payload user_id that differs from the token's
user is 400: the app is confused or someone is trying another user's id.

Keys: HS* algorithms use USER_JWT_SECRET (option A: Node shares its signing
secret, or option B: a separate secret for short-lived internal tokens);
RS*/ES* use USER_JWT_PUBLIC_KEY. Algorithms are pinned
(USER_JWT_ALGORITHMS); `exp` is required unless USER_JWT_REQUIRE_EXP=off.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.logger import get_logger

log = get_logger("UserAuth")

MODES = ("off", "optional", "required")


@dataclass(frozen=True)
class Identity:
    user_id: str = ""
    source: str = "none"          # "jwt" | "payload" | "none"

    @property
    def verified(self) -> bool:
        return self.source == "jwt" and bool(self.user_id)


class IdentityError(Exception):
    """Maps to an HTTP error: 401 (no valid token) or 400 (id mismatch)."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def current_mode() -> str:
    from core import settings as S
    mode = (S.USER_AUTH_MODE or "off").strip().lower()
    if mode not in MODES:
        log.error("Unknown USER_AUTH_MODE; treating as 'required'", mode=mode)
        return "required"
    return mode


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    value = value.strip()
    return value or None


def _key_for(algorithms: list[str]) -> str:
    from core import settings as S
    if any(a.upper().startswith("HS") for a in algorithms):
        return S.USER_JWT_SECRET
    return S.USER_JWT_PUBLIC_KEY


def _claim(claims: dict, path: str):
    cur = claims
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def user_id_from_claims(claims: dict) -> str:
    from core import settings as S
    for path in S.USER_JWT_USER_CLAIM:
        v = _claim(claims, path)
        if v is not None and not isinstance(v, (dict, list)) and str(v).strip():
            return str(v).strip()
    return ""


def verify_token(token: str) -> dict:
    """Claims of a valid token. Raises IdentityError(401) otherwise."""
    import jwt
    from core import settings as S

    algorithms = S.USER_JWT_ALGORITHMS or ["HS256"]
    key = _key_for(algorithms)
    if not key:
        raise IdentityError(401, "User token cannot be verified: no key configured")
    options = {
        "require": ["exp"] if S.USER_JWT_REQUIRE_EXP else [],
        "verify_aud": bool(S.USER_JWT_AUDIENCE),
        "verify_iss": bool(S.USER_JWT_ISSUER),
    }
    try:
        return jwt.decode(
            token, key, algorithms=algorithms, options=options,
            audience=S.USER_JWT_AUDIENCE or None,
            issuer=S.USER_JWT_ISSUER or None,
            leeway=S.USER_JWT_LEEWAY_S,
        )
    except jwt.ExpiredSignatureError:
        raise IdentityError(401, "User token expired") from None
    except jwt.PyJWTError as e:
        raise IdentityError(401, f"Invalid user token ({type(e).__name__})") from None


def resolve_identity(
    *,
    authorization: str | None = None,
    integration_token: str | None = None,
    payload_user_id: str | None = None,
    mode: str | None = None,
) -> Identity:
    """The caller's identity for this request, per USER_AUTH_MODE."""
    mode = mode or current_mode()
    payload_user_id = (payload_user_id or "").strip()

    if mode == "off":
        return Identity(payload_user_id, "payload" if payload_user_id else "none")

    from core import settings as S
    have_key = bool(_key_for(S.USER_JWT_ALGORITHMS or ["HS256"]))

    token_user = ""
    bearer = bearer_token(authorization)
    if bearer:
        if not have_key and mode == "optional":
            log.warning("USER_AUTH_MODE=optional but no JWT key is set; Bearer token not checked")
        else:
            token_user = user_id_from_claims(verify_token(bearer))
            if not token_user:
                raise IdentityError(401, "User token carries no user id")
    elif integration_token and have_key:
        try:
            token_user = user_id_from_claims(verify_token(integration_token))
        except IdentityError as e:
            if mode == "required":
                raise
            log.info("integration_token not usable as identity; ignored", reason=e.detail)

    if token_user:
        if payload_user_id and payload_user_id != token_user:
            log.warning("Payload user_id differs from the token's user; rejected",
                        token_user=token_user[:12], payload_user=payload_user_id[:12])
            raise IdentityError(400, "user_id does not match the signed-in user")
        return Identity(token_user, "jwt")

    if mode == "required":
        raise IdentityError(401, "Sign-in token required")
    return Identity(payload_user_id, "payload" if payload_user_id else "none")


def may_access_run(run_user_id: str, ident: Identity, *, admin: bool = False,
                   mode: str | None = None) -> bool:
    """Whether `ident` may read, stream or cancel a run owned by `run_user_id`.
    Callers answer 404 (not 403) on False so run ids cannot be probed."""
    mode = mode or current_mode()
    if admin or mode == "off":
        return True
    if not run_user_id:
        # created before identity was known; only open while not strict
        return mode != "required"
    return bool(ident.user_id) and ident.user_id == run_user_id
