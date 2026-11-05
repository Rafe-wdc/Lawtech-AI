"""Drafting concurrency limit (per worker).

Was a hard-coded 3: with complete Claude drafts holding a slot ~5 min, the
4th concurrent user saw "Drafting agent is busy, queuing your request..."
for minutes, and the wait counted against the 480 s request deadline.
"""

import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import agents.drafting as d


def test_default_is_ten(monkeypatch):
    monkeypatch.delenv("DRAFTING_MAX_CONCURRENT", raising=False)
    assert d._drafting_max_concurrent() == 10


def test_env_override_and_bad_values(monkeypatch):
    monkeypatch.setenv("DRAFTING_MAX_CONCURRENT", "12")
    assert d._drafting_max_concurrent() == 12
    monkeypatch.setenv("DRAFTING_MAX_CONCURRENT", "0")
    assert d._drafting_max_concurrent() == 1
    monkeypatch.setenv("DRAFTING_MAX_CONCURRENT", "lots")
    assert d._drafting_max_concurrent() == 10


def test_semaphore_uses_the_limit():
    assert d._AGENT_SEMAPHORE._value == d._drafting_max_concurrent()
