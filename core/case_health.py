"""``case_workspace`` entry of ``GET /pyapi/health``.

Kept out of ``core/gateway.py`` so the probe can be unit-tested without
importing the agent graph. With the flag off it reports ``off`` and touches
nothing. With the flag on it reads the applied schema version through the
process-wide store (one cheap ``SELECT``), so a server whose flag is on but
whose Postgres is unreachable shows ``error`` and degrades overall health
rather than failing silently on the first case request. Slice 3 adds the
worker heartbeat age to this same entry.
"""
from __future__ import annotations

import asyncio
import time

from core import settings as _settings


async def case_workspace_check(probe_timeout_s: float = 10.0) -> dict:
    """Return the health sub-document. ``status`` is ``off`` | ``ok`` | ``error``."""
    if not bool(getattr(_settings, "CASE_WORKSPACE_ENABLED", False)):
        return {"status": "off", "enabled": False}
    try:
        from core.case_store import get_case_store
        t0 = time.time()
        version = await asyncio.wait_for(
            asyncio.to_thread(lambda: get_case_store().schema_version()), timeout=probe_timeout_s,
        )
        return {
            "status": "ok", "enabled": True, "schema_version": int(version),
            "bucket": getattr(_settings, "CASE_S3_BUCKET", "") or "",
            "latency_ms": round((time.time() - t0) * 1000),
        }
    except Exception as e:
        return {"status": "error", "enabled": True, "detail": f"{type(e).__name__}: {e}"[:200]}
