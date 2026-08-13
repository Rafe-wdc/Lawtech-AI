"""Regression tests for singleton construction races in `core/clients.py`.

THE BUG
=======
Three singletons used the classic unguarded lazy-init:

    global _thing
    if _thing is None:
        _thing = build()          # <-- two threads can both get here
    return _thing

Agents call these from `asyncio.to_thread`, so genuine concurrency exists.
Two concurrent first requests could both observe `None` and each build one:

  * `get_es_client`            — leaks a connection pool, logs init twice
  * `get_retriever_embeddings` — loads BGE-large **twice** (~1.3 GB each)
  * `get_qa_embeddings`        — loads MiniLM twice (~90 MB each)

The embedding ones are the expensive pair. They are normally hidden because
`core/clients.py` eager-loads both at import — but that preload is skipped
when `EMBEDDING_SERVICE_URL` is set, and it is swallowed as a warning when it
fails, leaving the globals `None` for concurrent requests to race over. That
failure mode is not hypothetical: it is exactly the state this machine was in
before the models were downloaded.

THE TEST
========
Each builder is replaced with a deliberately slow counting stub, which widens
the race window from microseconds to ~50 ms so the failure is deterministic
rather than luck. N threads then call the getter at once and we assert the
builder ran exactly once.

`test_unlocked_pattern_would_fail` is the negative control: it runs the OLD
unguarded pattern under identical conditions and asserts it DOES build more
than once. Without it, these tests could pass simply because the harness never
produced real concurrency.

Run:
    pytest tests/test_client_singletons.py -v
    python tests/test_client_singletons.py     # no pytest required
"""

from __future__ import annotations

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("OPENAI_API_KEY", "test-key-unused")
os.environ.setdefault("GOOGLE_API_KEY", "test-key-unused")
# Skips the eager model preload; nothing here dials the URL.
os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://localhost:1")

import core.clients as clients  # noqa: E402

_THREADS = 8
_BUILD_DELAY = 0.05   # widens the race window; real model loads take seconds


class _SlowCounter:
    """Stand-in builder: slow enough to race, and counts its own calls."""

    def __init__(self):
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, *args, **kwargs):
        with self._lock:
            self.calls += 1
        time.sleep(_BUILD_DELAY)
        return object()


def _hammer(fn) -> list:
    """Call `fn` from _THREADS threads simultaneously; return all results."""
    barrier = threading.Barrier(_THREADS)

    def _call():
        barrier.wait()        # release all threads at the same instant
        return fn()

    with ThreadPoolExecutor(max_workers=_THREADS) as pool:
        return [f.result() for f in [pool.submit(_call) for _ in range(_THREADS)]]


class TestSingletonsBuildExactlyOnce:

    def test_es_client(self):
        stub = _SlowCounter()
        original, clients._SearchClient = clients._SearchClient, stub
        clients._es_client = None
        try:
            results = _hammer(clients.get_es_client)
        finally:
            clients._SearchClient = original
            clients._es_client = None

        assert stub.calls == 1, (
            f"ES client built {stub.calls}× under {_THREADS} concurrent callers "
            f"— each extra one leaks a connection pool"
        )
        assert len({id(r) for r in results}) == 1, "callers got different clients"

    def test_retriever_embeddings(self):
        stub = _SlowCounter()
        original = clients.HuggingFaceEmbeddings
        clients.HuggingFaceEmbeddings = stub
        clients._retriever_embeddings = None
        prior_url, clients.EMBEDDING_SERVICE_URL = clients.EMBEDDING_SERVICE_URL, ""
        try:
            results = _hammer(clients.get_retriever_embeddings)
        finally:
            clients.HuggingFaceEmbeddings = original
            clients.EMBEDDING_SERVICE_URL = prior_url
            clients._retriever_embeddings = None

        assert stub.calls == 1, (
            f"BGE-large loaded {stub.calls}× — that is ~1.3 GB per extra copy"
        )
        assert len({id(r) for r in results}) == 1

    def test_qa_embeddings(self):
        stub = _SlowCounter()
        original = clients.HuggingFaceEmbeddings
        clients.HuggingFaceEmbeddings = stub
        clients._qa_embeddings = None
        prior_url, clients.EMBEDDING_SERVICE_URL = clients.EMBEDDING_SERVICE_URL, ""
        try:
            results = _hammer(clients.get_qa_embeddings)
        finally:
            clients.HuggingFaceEmbeddings = original
            clients.EMBEDDING_SERVICE_URL = prior_url
            clients._qa_embeddings = None

        assert stub.calls == 1, f"MiniLM loaded {stub.calls}×"
        assert len({id(r) for r in results}) == 1

    def test_fast_path_returns_without_locking(self):
        """Once built, the getter must not take the lock on every request."""
        clients._es_client = sentinel = object()
        try:
            # Hold the construction lock; a correct fast path is unaffected.
            with clients._es_client_lock:
                assert clients.get_es_client() is sentinel
        finally:
            clients._es_client = None


class TestTheGuardHasTeeth:

    def test_unlocked_pattern_would_fail(self):
        """Negative control — the OLD pattern must visibly break here.

        If this ever passes, the harness is not producing real concurrency and
        the three tests above prove nothing.
        """
        stub = _SlowCounter()
        holder: dict = {"value": None}

        def unlocked_get():
            if holder["value"] is None:      # the bug, verbatim
                holder["value"] = stub()
            return holder["value"]

        _hammer(unlocked_get)

        assert stub.calls > 1, (
            "the unlocked pattern built only once — the race window is not "
            "being hit, so the locking tests above are not meaningful"
        )


# --- Standalone runner (venv has no pytest) ---------------------------------

if __name__ == "__main__":
    failures = 0
    for cls in (TestSingletonsBuildExactlyOnce, TestTheGuardHasTeeth):
        inst = cls()
        for name in sorted(n for n in dir(inst) if n.startswith("test_")):
            try:
                getattr(inst, name)()
                print(f"  PASS  {cls.__name__}.{name}")
            except Exception as e:
                failures += 1
                print(f"  FAIL  {cls.__name__}.{name}: {type(e).__name__}: {e}")
    print("\nALL PASSED" if not failures else f"\n{failures} FAILURE(S)")
    sys.exit(1 if failures else 0)
