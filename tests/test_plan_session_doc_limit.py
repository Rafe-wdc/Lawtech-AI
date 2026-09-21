"""Per-plan document limit per chat thread on POST /pyapi/chat.

PLAN_UPLOAD_LIMITS[plan]["max_docs_per_session"] caps the files already
uploaded to the thread plus the ones attached now: First Justice Plan 2,
Basic 5. Messages without attachments are never blocked. File processing is
stubbed, so these tests stop right after the upload checks.
"""

import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import fitz
import pytest
from fastapi.testclient import TestClient

import core.file_processor as file_processor
import core.gateway as gateway
from core.auth import require_user_key


def _pdf() -> bytes:
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def thread_files(monkeypatch):
    """Files already stored per thread id, as get_thread_storage reports them."""
    counts: dict[str, int] = {}

    async def _storage(thread_id):
        return counts.get(thread_id, 0), 0

    monkeypatch.setattr(gateway.chat_store, "get_thread_storage", _storage)
    return counts


@pytest.fixture
def client(monkeypatch, thread_files):
    async def _stub_process_files(*args, **kwargs):
        raise RuntimeError("stub: upload checks passed")

    monkeypatch.setattr(file_processor, "process_files", _stub_process_files)
    monkeypatch.setattr(gateway.limiter, "enabled", False, raising=False)
    gateway.app.dependency_overrides[require_user_key] = lambda: None
    gateway.app.state.agent_graph = None
    yield TestClient(gateway.app, raise_server_exceptions=False)
    gateway.app.dependency_overrides.pop(require_user_key, None)


def _post(client, n_files, plan, thread_id=None):
    data = {"query": "read this", "plan": plan}
    if thread_id:
        data["globalThreadId"] = thread_id
    files = [("files", (f"doc{i}.pdf", _pdf(), "application/pdf")) for i in range(n_files)]
    return client.post("/pyapi/chat", data=data, files=files or None)


@pytest.mark.parametrize("plan,limit", [("First Justice Plan", 2), ("Basic", 5)])
def test_up_to_the_limit_in_a_new_chat_is_accepted(client, plan, limit):
    assert _post(client, limit, plan).status_code == 200


@pytest.mark.parametrize("plan,limit", [("First Justice Plan", 2), ("Basic", 5)])
def test_over_the_limit_in_one_message_is_rejected(client, plan, limit):
    r = _post(client, limit + 1, plan)
    assert r.status_code == 403
    assert r.json()["message"] == (
        f"Your plan ({plan}) allows up to {limit} documents per chat. "
        f"This chat has 0, so you can attach up to {limit} more."
    )


def test_second_upload_after_limit_is_reached_is_blocked(client, thread_files):
    thread_files["t1"] = 2        # 2 docs attached earlier in this chat
    r = _post(client, 1, "First Justice Plan", "t1")
    assert r.status_code == 403
    assert r.json()["message"] == (
        "Your plan (First Justice Plan) allows up to 2 documents per chat, and "
        "this chat already has 2. Start a new chat to upload more documents."
    )


def test_follow_up_without_attachments_is_allowed(client, thread_files):
    thread_files["t1"] = 2
    assert _post(client, 0, "First Justice Plan", "t1").status_code == 200


def test_remaining_slots_in_the_chat_can_be_used(client, thread_files):
    thread_files["t2"] = 3        # Basic: 3 of 5 used
    assert _post(client, 2, "Basic", "t2").status_code == 200
    r = _post(client, 3, "Basic", "t2")
    assert r.status_code == 403
    assert "This chat has 3, so you can attach up to 2 more." in r.json()["message"]


def test_a_new_chat_starts_from_zero(client, thread_files):
    thread_files["old"] = 2
    assert _post(client, 2, "First Justice Plan", "new-chat").status_code == 200


def test_no_plan_has_no_session_limit(client, thread_files):
    thread_files["t3"] = 10
    assert _post(client, 3, "", "t3").status_code == 200
