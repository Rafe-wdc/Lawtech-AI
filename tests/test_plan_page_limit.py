"""Per-plan page budget per chat thread on POST /pyapi/chat.

PLAN_UPLOAD_LIMITS[plan]["max_pages_per_session"] caps the pages already
uploaded to the thread plus the new files: First Justice Plan 60, Basic 150.
One document may use the whole budget. PDF and Word count their pages, other
files 1 page each. File processing is stubbed, so these tests stop right
after the upload checks.
"""

import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import fitz
import pytest
from fastapi.testclient import TestClient

import core.file_processor as file_processor
import core.gateway as gateway
from core.auth import require_user_key


def _pdf(pages: int) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def usage(monkeypatch):
    """(documents, pages) already uploaded per thread id."""
    used: dict[str, tuple[int, int]] = {}

    async def _usage(thread_id):
        return used.get(thread_id, (0, 0))

    monkeypatch.setattr(gateway.chat_store, "get_thread_upload_usage", _usage)
    return used


@pytest.fixture
def processed(monkeypatch):
    """Captures the page counts handed to process_files for storage."""
    seen: dict = {}

    async def _stub_process_files(*args, **kwargs):
        seen.update(kwargs.get("page_counts") or {})
        raise RuntimeError("stub: upload checks passed")

    monkeypatch.setattr(file_processor, "process_files", _stub_process_files)
    return seen


@pytest.fixture
def client(monkeypatch, usage, processed):
    monkeypatch.setattr(gateway.limiter, "enabled", False, raising=False)
    gateway.app.dependency_overrides[require_user_key] = lambda: None
    gateway.app.state.agent_graph = None
    yield TestClient(gateway.app, raise_server_exceptions=False)
    gateway.app.dependency_overrides.pop(require_user_key, None)


def _post(client, page_counts, plan=None, thread_id=None, extra_files=()):
    data = {"query": "hi"}
    if plan is not None:
        data["plan"] = plan
    if thread_id:
        data["globalThreadId"] = thread_id
    files = [
        ("files", (f"doc{i}.pdf", _pdf(n), "application/pdf"))
        for i, n in enumerate(page_counts)
    ] + list(extra_files)
    return client.post("/pyapi/chat", data=data, files=files or None)


# --- one document may use the whole budget ---------------------------------

@pytest.mark.parametrize("plan,budget", [("First Justice Plan", 60), ("Basic", 150)])
def test_one_document_can_use_the_whole_budget(client, plan, budget):
    assert _post(client, [budget], plan).status_code == 200


@pytest.mark.parametrize("plan,budget", [("First Justice Plan", 60), ("Basic", 150)])
def test_one_document_over_the_budget_is_rejected(client, plan, budget):
    r = _post(client, [budget + 1], plan)
    assert r.status_code == 403
    assert r.json()["message"] == (
        f"doc0.pdf has {budget + 1} pages. Your plan ({plan}) allows up to "
        f"{budget} pages per chat, and this chat has {budget} pages left."
    )


def test_several_documents_are_summed(client):
    assert _post(client, [30, 30], "First Justice Plan").status_code == 200
    r = _post(client, [40, 21], "First Justice Plan")
    assert r.status_code == 403
    assert r.json()["message"].startswith("These documents have 61 pages in total.")


# --- budget across turns of the same chat -----------------------------------

def test_budget_used_by_one_60_page_doc_blocks_more_uploads(client, usage):
    usage["t1"] = (1, 60)      # 499: one doc, 60 pages -> budget used up
    r = _post(client, [1], "First Justice Plan", "t1")
    assert r.status_code == 403
    assert r.json()["message"] == (
        "Your plan (First Justice Plan) allows up to 60 pages per chat, and this "
        "chat has already used 60. Start a new chat to upload more documents."
    )


def test_999_four_docs_150_pages_blocks_a_fifth(client, usage):
    usage["t2"] = (4, 150)     # 4 of 5 docs, but all 150 pages used
    r = _post(client, [1], "Basic", "t2")
    assert r.status_code == 403
    assert "already used 150" in r.json()["message"]


def test_leftover_pages_can_be_used(client, usage):
    usage["t3"] = (1, 40)      # 499: 20 pages left
    assert _post(client, [20], "First Justice Plan", "t3").status_code == 200
    r = _post(client, [25], "First Justice Plan", "t3")
    assert r.status_code == 403
    assert r.json()["message"].endswith("and this chat has 20 pages left.")


def test_text_only_follow_up_is_allowed_when_budget_is_used(client, usage):
    usage["t4"] = (1, 60)
    assert _post(client, [], "First Justice Plan", "t4").status_code == 200


def test_new_chat_starts_with_a_full_budget(client, usage):
    usage["old"] = (1, 60)
    assert _post(client, [60], "First Justice Plan", "new-chat").status_code == 200


# --- files without pages count as 1 ----------------------------------------

def test_image_and_text_files_count_one_page_each(client, processed):
    extra = [("files", ("photo.png", b"\x89PNG\r\n\x1a\n", "image/png")),
             ("files", ("notes.txt", b"some notes", "text/plain"))]
    # Basic (5 docs, 150 pages): 148-page PDF + image (1) + text (1) = 150.
    assert _post(client, [148], "Basic", extra_files=extra).status_code == 200
    assert sorted(processed.values()) == [1, 1, 148]
    r = _post(client, [149], "Basic", extra_files=extra)
    assert r.status_code == 403
    assert "151 pages in total" in r.json()["message"]


def test_page_counts_are_passed_on_for_storage(client, processed):
    _post(client, [12, 7], "Basic")
    assert sorted(processed.values()) == [7, 12]


# --- plan handling ------------------------------------------------------------

def test_plan_name_match_ignores_case_and_spaces(client):
    assert _post(client, [61], "  first justice plan ").status_code == 403


def test_document_count_over_session_limit_is_rejected(client):
    # 3 docs in a new chat on First Justice Plan (max 2 per chat).
    assert _post(client, [1, 1, 1], "First Justice Plan").status_code == 403


def test_unconfigured_plan_is_accepted_without_plan_limits(client):
    # Yearly tiers, trials and future plans must never be locked out of uploads.
    for plan in ("Yearly Plan", "Premium", "Gold"):
        assert _post(client, [200], plan).status_code == 200


def test_plan_name_containing_a_configured_plan_gets_its_limits(client):
    assert _post(client, [61], "First Justice Plan Yearly").status_code == 403
    assert _post(client, [60], "First Justice Plan (Yearly)").status_code == 200
    assert _post(client, [151], "Basic - Annual").status_code == 403
    assert _post(client, [150], "basic yearly").status_code == 200
    assert _post(client, [1, 1, 1], "First Justice Plan Yearly").status_code == 403   # 2 docs per chat


@pytest.mark.parametrize("plan", [None, ""])
def test_no_plan_has_no_page_limit(client, plan):
    assert _post(client, [200], plan).status_code == 200


def test_plan_url_route_is_gone(client):
    r = client.post("/pyapi/chat/Basic", data={"query": "hi"})
    assert r.status_code in (404, 405)
