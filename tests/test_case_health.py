"""core/case_health.py — the case_workspace entry of /pyapi/health."""
from __future__ import annotations

import asyncio

import pytest

from core import case_health, settings as _settings


_IDLE_QUEUE = {"workers_live": 1, "last_seen_age_s": 2.0, "queued": 0, "running": 0, "oldest_queued_s": None}


class _Store:
    def __init__(self, version=None, exc=None, queue=None):
        self._v, self._exc, self._q = version, exc, queue if queue is not None else dict(_IDLE_QUEUE)

    def schema_version(self):
        if self._exc:
            raise self._exc
        return self._v

    def queue_summary_sync(self, stale_after_s):
        return self._q


def _run(coro):
    return asyncio.run(coro)


def test_flag_off_reports_off_without_touching_the_store(monkeypatch):
    monkeypatch.setattr(_settings, "CASE_WORKSPACE_ENABLED", False, raising=False)
    import core.case_store as cs
    monkeypatch.setattr(cs, "get_case_store", lambda: pytest.fail("store must not be built with the flag off"))
    assert _run(case_health.case_workspace_check()) == {"status": "off", "enabled": False}


def test_flag_on_reports_schema_version_and_bucket(monkeypatch):
    monkeypatch.setattr(_settings, "CASE_WORKSPACE_ENABLED", True, raising=False)
    monkeypatch.setattr(_settings, "CASE_S3_BUCKET", "lawttorney-cases", raising=False)
    import core.case_store as cs
    monkeypatch.setattr(cs, "get_case_store", lambda: _Store(version=1))
    out = _run(case_health.case_workspace_check())
    assert out["status"] == "ok" and out["enabled"] is True
    assert out["schema_version"] == 1 and out["bucket"] == "lawttorney-cases"
    assert isinstance(out["latency_ms"], int) and out["queue"] == _IDLE_QUEUE


def test_jobs_queued_with_no_live_worker_is_warn(monkeypatch):
    monkeypatch.setattr(_settings, "CASE_WORKSPACE_ENABLED", True, raising=False)
    import core.case_store as cs
    q = {"workers_live": 0, "last_seen_age_s": 900.0, "queued": 4, "running": 0, "oldest_queued_s": 850.0}
    monkeypatch.setattr(cs, "get_case_store", lambda: _Store(version=3, queue=q))
    out = _run(case_health.case_workspace_check())
    assert out["status"] == "warn" and out["detail"] == "jobs queued but no live worker" and out["queue"] == q
    # an empty queue with no worker is fine (a dev box without a worker is not degraded)
    monkeypatch.setattr(cs, "get_case_store", lambda: _Store(version=3, queue={**q, "queued": 0}))
    assert _run(case_health.case_workspace_check())["status"] == "ok"


def test_flag_on_with_unreachable_store_is_error_not_exception(monkeypatch):
    monkeypatch.setattr(_settings, "CASE_WORKSPACE_ENABLED", True, raising=False)
    import core.case_store as cs
    monkeypatch.setattr(cs, "get_case_store", lambda: _Store(exc=RuntimeError("connection refused")))
    out = _run(case_health.case_workspace_check(probe_timeout_s=2))
    assert out["status"] == "error" and out["enabled"] is True
    assert out["detail"].startswith("RuntimeError")
