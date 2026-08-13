"""Tests for the RESPONSE_CACHE_ENABLED toggle.

Background:
    The response cache stored the FIRST response to a given query and
    replayed it for an hour. Historically the Judgment agent's relevance
    gate was non-deterministic — the cached entry could be either the
    "S3 PDFs" branch or the "web fallback (vertexaisearch.cloud.google.com)"
    branch — so two users running the same query could see drastically
    different source sets. The cache shipped off-by-default until that
    was fixed.

    As of 2026-08-13 the cache is ON by default. See the comment in
    core/settings.py for the guards that made it safe:
      - cache key includes preferred_language + cite_appendix,
      - cache-hit persists to chat_store (multi-turn context preserved),
      - cache-set skips guardrail-blocked responses,
      - cache-set skips any response where an agent used web fallback,
      - cache-hit emits log_request for observability.

    This file pins the toggle's behaviour so a future accidental flip is
    loud (either direction).

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


class TestKeyComposition:
    """Cache key must include preferred_language + cite_appendix so users
    with different response-shaping inputs never collide on the same query.
    """

    def test_language_isolates_entries(self):
        c = ResponseCache(enabled=True)
        c.set("q", _entry(), language="en")
        assert c.get("q", language="hi") is None
        assert c.get("q", language="en") is not None

    def test_cite_appendix_isolates_entries(self):
        c = ResponseCache(enabled=True)
        c.set("q", _entry(), cite_appendix=True)
        assert c.get("q", cite_appendix=False) is None
        assert c.get("q", cite_appendix=True) is not None

    def test_missing_language_matches_missing_language(self):
        """Backwards-compat: callers that pass no language still hit the
        entry they set with no language. Empty string == not provided."""
        c = ResponseCache(enabled=True)
        c.set("q", _entry())
        assert c.get("q") is not None

    def test_missing_cite_appendix_differs_from_explicit_false(self):
        """None (endpoint doesn't accept the flag) is a distinct key from
        cite_appendix=False (endpoint accepts but user opted out). Prevents
        cross-endpoint pollution of drafting vs non-drafting queries."""
        c = ResponseCache(enabled=True)
        c.set("q", _entry(), cite_appendix=None)
        assert c.get("q", cite_appendix=False) is None


class TestProductionDefault:
    """Production singleton must be off by default."""

    def test_singleton_reflects_setting(self):
        from core.response_cache import response_cache
        from core.settings import RESPONSE_CACHE_ENABLED
        # Singleton honours the setting -- if env says off, stats say off
        assert response_cache.stats["enabled"] == RESPONSE_CACHE_ENABLED

    def test_default_is_on(self):
        """If RESPONSE_CACHE_ENABLED isn't set in env, the default is True.

        The cache was flipped ON on 2026-08-13 after the safety guards
        landed (language-aware key, save_turn on cache hit, fallback_used
        skip, is_blocked skip). See core/settings.py for the rationale.
        """
        import os
        from core.settings import RESPONSE_CACHE_ENABLED
        env_value = os.environ.get("RESPONSE_CACHE_ENABLED", "").lower()
        if env_value in ("0", "false", "no", "off"):
            pytest.skip("env explicitly disables cache")
        assert RESPONSE_CACHE_ENABLED is True, (
            "Cache must default ON -- the guards in chat_runner.py + "
            "gateway.py make this safe. See settings.py docstring."
        )
