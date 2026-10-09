"""Per-plan upload size budget per chat thread on POST /pyapi/chat.

PLAN_UPLOAD_LIMITS[plan]["max_mb_per_session"] caps the bytes already uploaded
to the thread plus the new files: First Justice Plan 40 MB, Basic 100 MB. It
is a budget for the chat, not a per-file cap: two 20 MB files or one 40 MB
file both fit First Justice. Every file type counts. File processing is
stubbed, so these tests stop right after the upload checks.
"""

import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import pytest
from fastapi.testclient import TestClient

import core.file_processor as file_processor
import core.gateway as gateway
from core.auth import require_user_key
from core.settings import PLAN_UPLOAD_LIMITS

MB = 1024 * 1024


@pytest.fixture
def used(monkeypatch):
    """Bytes / (documents, pages) already uploaded per thread id."""
    state: dict[str, dict] = {}

    async def _usage(thread_id):
        return state.get(thread_id, {}).get("usage", (0, 0))

    async def _bytes(thread_id):
        return state.get(thread_id, {}).get("bytes", 0)

    monkeypatch.setattr(gateway.chat_store, "get_thread_upload_usage", _usage)
    monkeypatch.setattr(gateway.chat_store, "get_thread_upload_bytes", _bytes)
    return state


@pytest.fixture
def processed(monkeypatch):
    """Sizes of the files that got past the upload checks."""
    seen: list[int] = []

    async def _stub_process_files(files, *args, **kwargs):
        seen.extend(size for _, _, size in files)
        raise RuntimeError("stub: upload checks passed")

    monkeypatch.setattr(file_processor, "process_files", _stub_process_files)
    return seen


@pytest.fixture
def client(monkeypatch, used, processed):
    monkeypatch.setattr(gateway.limiter, "enabled", False, raising=False)
    gateway.app.dependency_overrides[require_user_key] = lambda: None
    gateway.app.state.agent_graph = None
    yield TestClient(gateway.app, raise_server_exceptions=False)
    gateway.app.dependency_overrides.pop(require_user_key, None)


def _txt(mb: float) -> bytes:
    return b"a" * int(mb * MB)


def _post(client, sizes_mb, plan=None, thread_id=None, names=None):
    data = {"query": "hi"}
    if plan is not None:
        data["plan"] = plan
    if thread_id:
        data["globalThreadId"] = thread_id
    files = [
        ("files", ((names[i] if names else f"doc{i}.txt"), _txt(mb), "text/plain"))
        for i, mb in enumerate(sizes_mb)
    ]
    return client.post("/pyapi/chat", data=data, files=files)


def _passed(resp, processed, n):
    """The request got past the upload checks with `n` files."""
    return resp.status_code == 200 and len(processed) == n


def test_limits_are_configured():
    assert PLAN_UPLOAD_LIMITS["First Justice Plan"]["max_mb_per_session"] == 40
    assert PLAN_UPLOAD_LIMITS["Basic"]["max_mb_per_session"] == 100


# --- First Justice Plan: 40 MB per chat --------------------------------------

def test_first_justice_two_files_of_20mb_fit(client, processed):
    assert _passed(_post(client, [20, 20], plan="First Justice Plan"), processed, 2)


def test_first_justice_one_file_may_use_the_whole_budget(client, processed):
    assert _passed(_post(client, [40], plan="First Justice Plan"), processed, 1)


def test_first_justice_uneven_split_fits(client, processed):
    assert _passed(_post(client, [35, 5], plan="First Justice Plan"), processed, 2)


def test_first_justice_single_file_over_the_budget_is_rejected(client, processed):
    resp = _post(client, [41], plan="First Justice Plan", names=["big.txt"])
    assert resp.status_code == 403
    msg = resp.json()["message"]
    assert "big.txt is 41 MB" in msg
    assert msg == ("big.txt is 41 MB. Your plan (First Justice Plan) allows up to "
                   "40 MB of documents per chat.")   # nothing used yet: no "left" clause
    assert processed == []


def test_first_justice_two_files_over_the_budget_are_rejected_together(client, processed):
    resp = _post(client, [30, 15], plan="First Justice Plan")
    assert resp.status_code == 403
    assert "These documents are 45 MB in total" in resp.json()["message"]
    assert processed == []          # neither file is kept


# --- Basic: 100 MB per chat ---------------------------------------------------

def test_basic_five_files_of_20mb_fit(client, processed):
    assert _passed(_post(client, [20] * 5, plan="Basic"), processed, 5)


def test_basic_one_file_of_100mb_fits(client, processed):
    assert _passed(_post(client, [100], plan="Basic"), processed, 1)


def test_basic_three_files_over_100mb_are_rejected(client, processed):
    resp = _post(client, [40, 40, 30], plan="Basic")
    assert resp.status_code == 403
    assert "110 MB in total" in resp.json()["message"] and "100 MB" in resp.json()["message"]


# --- The budget is per chat ---------------------------------------------------

def test_earlier_uploads_in_the_chat_count(client, used, processed):
    used["t1"] = {"usage": (1, 5), "bytes": 30 * MB}
    resp = _post(client, [15], plan="First Justice Plan", thread_id="t1", names=["second.txt"])
    assert resp.status_code == 403
    msg = resp.json()["message"]
    assert "second.txt is 15 MB" in msg and "10 MB left" in msg


def test_remaining_budget_can_be_used_exactly(client, used, processed):
    used["t1"] = {"usage": (1, 5), "bytes": 30 * MB}
    assert _passed(_post(client, [10], plan="First Justice Plan", thread_id="t1"), processed, 1)


def test_used_up_budget_says_to_start_a_new_chat(client, used, processed):
    used["t1"] = {"usage": (1, 5), "bytes": 40 * MB}
    resp = _post(client, [1], plan="First Justice Plan", thread_id="t1")
    assert resp.status_code == 403
    assert "already used 40 MB" in resp.json()["message"]
    assert "Start a new chat" in resp.json()["message"]


def test_a_new_chat_starts_with_a_fresh_budget(client, used, processed):
    used["t1"] = {"usage": (2, 60), "bytes": 40 * MB}
    assert _passed(_post(client, [40], plan="First Justice Plan"), processed, 1)


# --- Scope --------------------------------------------------------------------

def test_yearly_plan_name_inherits_the_monthly_budget(client, processed):
    resp = _post(client, [41], plan="First Justice Plan Yearly")
    assert resp.status_code == 403 and "40 MB" in resp.json()["message"]


def test_plan_without_configured_limits_has_no_size_budget(client, processed):
    assert _passed(_post(client, [60], plan="Yearly Plan"), processed, 1)


def test_no_plan_has_no_size_budget(client, processed):
    assert _passed(_post(client, [60]), processed, 1)


def test_rejected_file_type_does_not_use_the_budget(client, processed):
    """A 30 MB .exe is refused for its type; the 20 MB document still fits."""
    resp = client.post(
        "/pyapi/chat", data={"query": "hi", "plan": "First Justice Plan"},
        files=[("files", ("tool.exe", _txt(30), "application/octet-stream")),
               ("files", ("doc.txt", _txt(20), "text/plain"))])
    assert resp.status_code == 200 and processed == [20 * MB]


def test_page_budget_still_applies_alongside_size(client, used, processed):
    """Small in bytes, but the chat's pages are already used up."""
    used["t1"] = {"usage": (1, 60), "bytes": 1 * MB}
    resp = _post(client, [1], plan="First Justice Plan", thread_id="t1")
    assert resp.status_code == 403 and "pages" in resp.json()["message"]


def test_understated_content_length_is_caught_while_reading(client, processed, monkeypatch):
    """If the declared sizes cannot be used, the bytes actually read are
    counted. The pre-read check is skipped here by leaving it nothing to add up."""
    monkeypatch.setattr(gateway, "_plan_counts_upload", lambda filename: False)
    resp = _post(client, [41], plan="First Justice Plan")
    assert resp.status_code == 403
    assert "larger than your plan allows" in resp.json()["message"]
    assert processed == []


# --- Storage ------------------------------------------------------------------

def test_stored_bytes_are_summed_per_thread(tmp_path):
    import asyncio
    import sqlite3
    from core.chat_store import _SqliteChatHistoryStore

    store = _SqliteChatHistoryStore(str(tmp_path / "chat.db"))
    store._ensure_schema()
    conn = sqlite3.connect(str(tmp_path / "chat.db"))
    conn.execute("INSERT INTO threads (thread_id) VALUES ('t1'), ('t2')")
    conn.executemany(
        "INSERT INTO thread_files (thread_id, file_id, filename, size_bytes) VALUES (?, ?, ?, ?)",
        [("t1", "a", "a.pdf", 20 * MB), ("t1", "b", "b.pdf", 15 * MB), ("t2", "c", "c.pdf", 7 * MB)])
    conn.commit()
    conn.close()

    assert asyncio.run(store.get_thread_upload_bytes("t1")) == 35 * MB
    assert asyncio.run(store.get_thread_upload_bytes("t2")) == 7 * MB
    assert asyncio.run(store.get_thread_upload_bytes("nope")) == 0
