"""``case_workspace`` entry of ``GET /pyapi/health``.

Kept out of ``core/gateway.py`` so the probe can be unit-tested without
importing the agent graph. With the flag off it reports ``off`` and touches
nothing. With the flag on it reads the applied schema version through the
process-wide store (one cheap ``SELECT``), so a server whose flag is on but
whose Postgres is unreachable shows ``error`` and degrades overall health
rather than failing silently on the first case request. Slice 3 adds the
``queue`` sub-document (live workers, newest heartbeat age, depth); jobs
queued with no live worker make the entry ``warn``.
"""
from __future__ import annotations

import asyncio
import time

from core import settings as _settings


async def case_workspace_check(probe_timeout_s: float = 10.0) -> dict:
    """Return the health sub-document. ``status`` is ``off`` | ``ok`` | ``warn`` | ``error``."""
    if not bool(getattr(_settings, "CASE_WORKSPACE_ENABLED", False)):
        return {"status": "off", "enabled": False}
    try:
        from core.case_store import get_case_store
        stale = int(getattr(_settings, "CASE_WORKER_STALE_SEC", 180) or 180)

        def _probe():
            store = get_case_store()
            return store.schema_version(), store.queue_summary_sync(stale)

        t0 = time.time()
        version, queue = await asyncio.wait_for(asyncio.to_thread(_probe), timeout=probe_timeout_s)
        out = {
            "status": "ok", "enabled": True, "schema_version": int(version),
            "bucket": getattr(_settings, "CASE_S3_BUCKET", "") or "",
            "latency_ms": round((time.time() - t0) * 1000),
            "queue": queue,
        }
        if queue.get("queued", 0) > 0 and queue.get("workers_live", 0) == 0:
            out["status"] = "warn"
            out["detail"] = "jobs queued but no live worker"
        return out
    except Exception as e:
        return {"status": "error", "enabled": True, "detail": f"{type(e).__name__}: {e}"[:200]}
