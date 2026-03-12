"""API key authentication — two-tier system.

User keys  → /pyapi/search*, /pyapi/chat, /pyapi/mainqa, /pyapi/threads*, etc.
Admin key  → /pyapi/admin/*, /pyapi/metrics

Public (no key needed): /, /pyapi/health

Configure via .env:
    API_KEYS=key1,key2,key3          # comma-separated user keys
    ADMIN_API_KEY=your-admin-key     # single admin key

If API_KEYS is empty (dev mode), all user endpoints are open with a warning.
If ADMIN_API_KEY is empty, admin endpoints return 501 Not Implemented.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request, Security
from fastapi.security.api_key import APIKeyHeader

from .logger import get_logger
from .settings import API_KEYS as _RAW_API_KEYS, ADMIN_API_KEY as _ADMIN_API_KEY_RAW

log = get_logger("Auth")

# Parse user keys (comma-separated, whitespace-stripped)
_USER_API_KEYS: set[str] = {k.strip() for k in _RAW_API_KEYS.split(",") if k.strip()}
_ADMIN_API_KEY: str = _ADMIN_API_KEY_RAW.strip()

_DEV_MODE = not bool(_USER_API_KEYS)
if _DEV_MODE:
    log.warning(
        "AUTH: API_KEYS not set — running in open dev mode. "
        "Set API_KEYS in .env before exposing to the internet."
    )
else:
    log.info("AUTH: user key auth enabled", key_count=len(_USER_API_KEYS))

# FastAPI header scheme (shows up correctly in /docs)
_user_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)
_admin_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_user_key(x_api_key: str | None = Security(_user_key_scheme)) -> str:
    """Dependency: require a valid user API key.

    In dev mode (API_KEYS not set), passes through with a log warning.
    Returns the validated key string.
    """
    if _DEV_MODE:
        # Allow through in dev — already warned at startup
        return "dev"

    if not x_api_key or x_api_key not in _USER_API_KEYS:
        log.warning("Rejected request: invalid or missing user API key")
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    return x_api_key


async def require_admin_key(
    request: Request,
    x_api_key: str | None = Security(_admin_key_scheme),
) -> str:
    """Dependency: require the admin API key.

    Accepts the key via:
      - X-API-Key header  (curl, clients)
      - Authorization: Bearer <key>  (Prometheus scrape config uses bearer_token)

    Returns 501 if ADMIN_API_KEY is not configured (admin not set up yet).
    """
    if not _ADMIN_API_KEY:
        raise HTTPException(
            status_code=501,
            detail="Admin access not configured. Set ADMIN_API_KEY in .env.",
        )

    # Extract key from X-API-Key or Bearer token
    candidate = x_api_key
    if not candidate:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            candidate = auth_header[7:].strip()

    if not candidate or candidate != _ADMIN_API_KEY:
        log.warning("Rejected admin request: invalid or missing admin API key")
        raise HTTPException(status_code=401, detail="Invalid or missing admin API key")

    return candidate


def get_key_identifier(request_key: str) -> str:
    """Return a safe identifier for rate-limit keying (last 8 chars of key, or 'dev')."""
    if request_key == "dev":
        return "dev"
    return f"key:{request_key[-8:]}" if len(request_key) >= 8 else f"key:{request_key}"
