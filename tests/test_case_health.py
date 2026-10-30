"""core/case_health.py — the case_workspace entry of /pyapi/health."""
from __future__ import annotations

import asyncio

import pytest

from core import case_health, settings as _settings


class _Store:
    def __init__(self, version=None, exc=None):
        self._v, self._exc = version, exc

    def schema_version(self):
        if self._exc:
            raise self._exc
        return self._v


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
    assert isinstance(out["latency_ms"], int)


def test_flag_on_with_unreachable_store_is_error_not_exception(monkeypatch):
    monkeypatch.setattr(_settings, "CASE_WORKSPACE_ENABLED", True, raising=False)
    import core.case_store as cs
    monkeypatch.setattr(cs, "get_case_store", lambda: _Store(exc=RuntimeError("connection refused")))
    out = _run(case_health.case_workspace_check(probe_timeout_s=2))
    assert out["status"] == "error" and out["enabled"] is True
    assert out["detail"].startswith("RuntimeError")
