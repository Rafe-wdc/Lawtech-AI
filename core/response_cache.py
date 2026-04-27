"""In-memory response cache for first-turn query responses.

Caches final responses keyed by normalized query hash. Reduces latency
from ~30-50s to <100ms for repeated identical queries.

Thread-safe via a simple lock. TTL-based eviction on access.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from core.logger import get_logger

log = get_logger("ResponseCache")


@dataclass
class CacheEntry:
    """Single cached response."""
    response: str
    source_metadata: list[dict]
    agents_used: list[str]
    tokens_consumed: int           # legacy aggregate; kept for backward compat
    token_usage: dict | None = None  # full per-LLM-call breakdown captured by token_tracker
    timestamp: float = field(default_factory=time.time)


class ResponseCache:
    """TTL-based in-memory cache for legal query responses."""

    def __init__(self, ttl: int = 3600, max_entries: int = 500):
        self._store: dict[str, CacheEntry] = {}
        self._ttl = ttl
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _make_key(query: str, file_fingerprint: str = "") -> str:
        """Normalize query + file identity and create cache key.

        file_fingerprint should be a string like "doc.pdf:1024,img.jpg:5000"
        (filename:size pairs). Same query + same files = same key.
        Different files = different key, even if query is identical.
        """
        normalized = query.lower().strip()
        if file_fingerprint:
            normalized += "\x00" + file_fingerprint
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def get(self, query: str, file_fingerprint: str = "") -> CacheEntry | None:
        """Look up a cached response. Returns None on miss or expiry."""
        key = self._make_key(query, file_fingerprint)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if time.time() - entry.timestamp > self._ttl:
                del self._store[key]
                self._misses += 1
                return None
            self._hits += 1
            log.info("Cache hit", key=key[:8], age_s=int(time.time() - entry.timestamp))
            return entry

    def set(self, query: str, entry: CacheEntry, file_fingerprint: str = "") -> None:
        """Store a response in the cache."""
        key = self._make_key(query, file_fingerprint)
        with self._lock:
            # Evict expired entries if at capacity
            if len(self._store) >= self._max_entries:
                self._evict_expired()
            # If still at capacity after eviction, remove oldest
            if len(self._store) >= self._max_entries:
                oldest_key = min(self._store, key=lambda k: self._store[k].timestamp)
                del self._store[oldest_key]
            self._store[key] = entry
        log.debug("Cache set", key=key[:8], agents=entry.agents_used)

    def _evict_expired(self) -> None:
        """Remove expired entries. Must be called with lock held."""
        now = time.time()
        expired = [k for k, v in self._store.items() if now - v.timestamp > self._ttl]
        for k in expired:
            del self._store[k]
        if expired:
            log.debug("Evicted expired entries", count=len(expired))

    @property
    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        return {
            "size": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(self._hits + self._misses, 1) * 100, 1),
        }


# Module-level singleton
response_cache = ResponseCache()
