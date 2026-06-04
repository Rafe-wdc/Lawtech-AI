"""Tests for the RESPONSE_CACHE_ENABLED toggle.

Background:
    The response cache stored the FIRST response to a given query and
    replayed it for an hour. Because the Judgment agent's relevance gate
    is non-deterministic, the cached entry could be either the "S3 PDFs"
    branch or the "web fallback (vertexaisearch.cloud.google.com)" branch
    -- two users running the same query then saw drastically different
    source sets. Shipping the cache off-by-default fixes that.

    This file pins the toggle's behaviour so a future revert is loud.

Usage:
    pytest tests/test_response_cache_toggle.py -v
"""
from __future__ import annotations

import pytest

from core.response_cache import ResponseCache, CacheEntry


def _entry() -> CacheEntry:
    return CacheEntry(
        response="hello",
        source_metadata=[],
        agents_used=["X"],
        tokens_consumed=0,
    )


class TestCacheDisabled:
    """When disabled, the cache must be transparent: nothing in, nothing out."""

    def test_get_returns_none_when_disabled(self):
        c = ResponseCache(enabled=False)
        c.set("q", _entry())
        assert c.get("q") is None

    def test_set_is_noop_when_disabled(self):
        c = ResponseCache(enabled=False)
        c.set("q", _entry())
        # Even though we explicitly check stats, the internal store is empty
        assert c.stats["size"] == 0
        assert c.stats["enabled"] is False

    def test_stats_exposes_enabled_flag(self):
        c = ResponseCache(enabled=False)
        assert c.stats["enabled"] is False

    def test_no_hits_or_misses_recorded_when_disabled(self):
        """get() short-circuits before the hit/miss counters."""
        c = ResponseCache(enabled=False)
        for _ in range(5):
            c.get("q")
        assert c.stats["hits"] == 0
        assert c.stats["misses"] == 0


class TestCacheEnabled:
    """When enabled, the cache behaves as before this PR."""

    def test_set_then_get_returns_entry(self):
        c = ResponseCache(enabled=True)
        entry = _entry()
        c.set("q", entry)
        got = c.get("q")
        assert got is not None
        assert got.response == "hello"

    def test_hit_counters_increment_when_enabled(self):
        c = ResponseCache(enabled=True)
        c.set("q", _entry())
        c.get("q")
        c.get("q")
        c.get("other")  # miss
        assert c.stats["hits"] == 2
        assert c.stats["misses"] == 1

    def test_stats_exposes_enabled_flag(self):
        c = ResponseCache(enabled=True)
        assert c.stats["enabled"] is True


class TestProductionDefault:
    """Production singleton must be off by default."""

    def test_singleton_reflects_setting(self):
        from core.response_cache import response_cache
        from core.settings import RESPONSE_CACHE_ENABLED
        # Singleton honours the setting -- if env says off, stats say off
        assert response_cache.stats["enabled"] == RESPONSE_CACHE_ENABLED

    def test_default_is_off(self):
        """If RESPONSE_CACHE_ENABLED isn't set in env, the default is False."""
        import os
        # The env var should NOT be set in the test environment (or set to false)
        # Either way, the resolved value must be False.
        from core.settings import RESPONSE_CACHE_ENABLED
        env_value = os.environ.get("RESPONSE_CACHE_ENABLED", "").lower()
        if env_value in ("1", "true", "yes", "on"):
            pytest.skip("env explicitly enables cache")
        assert RESPONSE_CACHE_ENABLED is False, (
            "Cache must default OFF -- see settings.py docstring for the "
            "non-determinism rationale."
        )
