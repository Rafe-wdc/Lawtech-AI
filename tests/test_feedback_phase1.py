"""Feedback loop, phase 1: reasons, user actions and answer traces are captured.

See docs/rlhf_feedback_loop_plan.md. Uses the configured chat store; every
test works in its own thread and purges it afterwards.
"""

import asyncio
import os
import uuid

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import pytest
from fastapi.testclient import TestClient

import core.gateway as gateway
from core import feedback_store
from core.auth import require_admin_key, require_user_key
from core.chat_store import chat_store


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(gateway.limiter, "enabled", False, raising=False)
    gateway.app.dependency_overrides[require_user_key] = lambda: None
    gateway.app.dependency_overrides[require_admin_key] = lambda: None
    yield TestClient(gateway.app, raise_server_exceptions=False)
    gateway.app.dependency_overrides.pop(require_user_key, None)
    gateway.app.dependency_overrides.pop(require_admin_key, None)


@pytest.fixture
def turn():
    """A saved question/answer turn in a fresh thread."""
    tid = f"test-fb-{uuid.uuid4().hex[:10]}"
    n = asyncio.run(chat_store.save_turn(tid, "Draft a bail application", "IN THE COURT OF ..."))
    yield tid, n
    asyncio.run(feedback_store.purge(tid))


def _review(client, tid, rating="down"):
    items = client.get(f"/pyapi/admin/feedback/review?rating={rating}&days=1&limit=1000").json()["items"]
    return [i for i in items if i["thread_id"] == tid]


def _rate(client, tid, n, rating, **extra):
    return client.post("/pyapi/feedback", json={"thread_id": tid, "turn_number": n, "rating": rating, **extra})


def _event(client, tid, n, event, **extra):
    return client.post("/pyapi/feedback/event", json={"thread_id": tid, "turn_number": n, "event": event, **extra})


def test_reason_menu_is_served(client):
    codes = [r["code"] for r in client.get("/pyapi/feedback/reasons").json()["reasons"]]
    assert {"wrong_law", "old_law", "hallucinated_case", "incomplete", "other"} <= set(codes)


def test_thumbs_down_with_reasons_reaches_the_review_queue(client, turn):
    tid, n = turn
    r = _rate(client, tid, n, "down", comment="cites IPC 420",
              reasons=["old_law", "wrong_law", "not_a_code", "old_law"])
    assert r.status_code == 200
    (item,) = _review(client, tid)
    assert item["reasons"] == ["old_law", "wrong_law"]      # unknown and duplicate codes dropped
    assert item["comment"] == "cites IPC 420"
    assert item["user_query"] == "Draft a bail application"
    assert item["ai_response"].startswith("IN THE COURT")


def test_old_clients_sending_only_a_rating_still_work(client, turn):
    tid, n = turn
    assert _rate(client, tid, n, "up").status_code == 200
    (item,) = _review(client, tid, "up")
    assert item["reasons"] is None and item["events"] == []


def test_user_actions_are_recorded(client, turn):
    tid, n = turn
    assert _event(client, tid, n, "copy").status_code == 200
    assert _event(client, tid, n, "download").status_code == 200
    assert _event(client, tid, n, "edit", final_text="IN THE HIGH COURT OF ...").status_code == 200
    assert _event(client, tid, n, "hack").status_code == 422
    _rate(client, tid, n, "up")
    (item,) = _review(client, tid, "up")
    assert sorted(item["events"]) == ["copy", "download", "edit"]
    assert item["final_text"].startswith("IN THE HIGH COURT")


def test_answer_trace_is_joined_to_the_rating(client, turn):
    tid, n = turn
    asyncio.run(feedback_store.save_answer_trace(
        thread_id=tid, turn_number=n, request_id="abc12345", endpoint="/pyapi/chat",
        task="Drafting", agents_used=["Drafting"],
        token_usage={"total_tokens": 1234, "cost_usd": 0.05,
                     "by_model": {"claude-sonnet-5": {"total": 1000}}},
        source_metadata=[{"agent_name": "Drafting", "title": "Bail template",
                          "doc_link": "https://lawttorney.s3.x/y.pdf"}],
        language="en", user_plan="Basic", web_fallback=False, latency_ms=4200))
    _rate(client, tid, n, "down", reasons=["incomplete"])
    (item,) = _review(client, tid)
    assert item["task"] == "Drafting" and item["agents"] == ["Drafting"]
    assert item["models"] == {"claude-sonnet-5": 1000}
    assert item["user_plan"] == "Basic" and item["latency_ms"] == 4200
    assert item["sources"][0]["title"] == "Bail template"
    assert len(item["prompt_version"]) == 12


def test_regenerate_counts_against_the_previous_answer(turn):
    tid, n = turn
    before = asyncio.run(feedback_store.summary(days=1))["events"].get("regenerate", 0)
    asyncio.run(feedback_store.save_answer_trace(thread_id=tid, turn_number=n + 1, regenerate_of="x"))
    after = asyncio.run(feedback_store.summary(days=1))["events"].get("regenerate", 0)
    assert after == before + 1


def test_trace_never_raises():
    asyncio.run(feedback_store.save_answer_trace(thread_id="", turn_number=0))
    tid = f"test-fb-{uuid.uuid4().hex[:10]}"
    asyncio.run(feedback_store.save_answer_trace(thread_id=tid, turn_number=1,
                                                 token_usage={"by_model": "bad"}))
    asyncio.run(feedback_store.purge(tid))


def test_summary_counts_ratings_and_reasons(client, turn):
    tid, n = turn
    _rate(client, tid, n, "down", reasons=["wrong_format"])
    s = client.get("/pyapi/admin/feedback/summary?days=1").json()
    assert s["ratings"].get("down", 0) >= 1 and s["reasons"].get("wrong_format", 0) >= 1


def test_purge_removes_everything_for_a_thread(client, turn):
    tid, n = turn
    _rate(client, tid, n, "down", reasons=["other"])
    _event(client, tid, n, "copy")
    assert asyncio.run(feedback_store.purge(tid)) >= 3
    assert _review(client, tid) == []
