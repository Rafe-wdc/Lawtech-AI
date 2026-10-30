"""HS256 JWT verification shared by user memory and the case workspace.

Node's user service issues an HS256 access token whose only identity claim is
the numeric database key in ``userId`` (no ``sub`` / ``iss`` / ``aud``). This
module verifies that token with the standard library only — no PyJWT — and is
the single place that logic lives: ``core/user_memory.py`` (feat/user-memory,
follow-up branch feat/user-memory-shared-jwt) imports ``verify_hs256`` from
here, so both features verify the same way. Keep this file identical on both
branches until they are merged, or the merge conflicts.

Guarantees of ``verify_hs256``:
  * ``alg`` is pinned to HS256 — ``none`` and RS*/ES* headers are rejected;
  * signature compared in constant time;
  * ``exp`` and ``nbf`` honoured when present, with ``leeway_s`` of skew;
  * the id claim is cast to ``str`` and must match ``USER_ID_RE``;
  * any malformed input yields ``""`` — it never raises.

``mint_hs256`` exists for tests and the dev helper script only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from typing import Iterable

USER_ID_RE = re.compile(r"^[A-Za-z0-9_.:@\-]{1,128}$")
DEFAULT_CLAIMS: tuple[str, ...] = ("userId",)
DEFAULT_LEEWAY_S = 30.0


def b64url_decode(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def verify_hs256(
    token: str,
    secret: str,
    claims: Iterable[str] = DEFAULT_CLAIMS,
    leeway_s: float = DEFAULT_LEEWAY_S,
) -> str:
    """Return the user id carried by a valid HS256 JWT, else ``""``.

    Equivalent to ``jwt.decode(token, secret, algorithms=["HS256"],
    leeway=leeway_s)`` followed by reading the first present claim in
    ``claims``. An empty ``secret`` always fails (fail closed).
    """
    if not token or not secret:
        return ""
    try:
        h_b64, p_b64, s_b64 = token.split(".")
        header = json.loads(b64url_decode(h_b64))
        if not isinstance(header, dict) or str(header.get("alg", "")) != "HS256":
            return ""
        expected = hmac.new(
            secret.encode("utf-8"), f"{h_b64}.{p_b64}".encode("ascii"), hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, b64url_decode(s_b64)):
            return ""
        payload = json.loads(b64url_decode(p_b64))
        if not isinstance(payload, dict):
            return ""
        now = time.time()
        leeway = max(0.0, float(leeway_s))
        exp = payload.get("exp")
        if exp is not None and float(exp) + leeway < now:
            return ""
        nbf = payload.get("nbf")
        if nbf is not None and float(nbf) - leeway > now:
            return ""
        for claim in claims:
            v = payload.get(claim)
            if v is None or v == "" or isinstance(v, bool):
                continue
            v = str(v)  # numeric primary key -> string user id
            return v if USER_ID_RE.match(v) else ""
    except Exception:
        return ""
    return ""


def mint_hs256(payload: dict, secret: str) -> str:
    """Sign ``payload`` as an HS256 JWT. Tests and ``scripts/mint_dev_jwt.py`` only."""
    header = {"alg": "HS256", "typ": "JWT"}
    h_b64 = b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p_b64 = b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), f"{h_b64}.{p_b64}".encode("ascii"), hashlib.sha256).digest()
    return f"{h_b64}.{p_b64}.{b64url_encode(sig)}"
