"""Per-plan PDF page limit on POST /pyapi/chat/{plan}.

Each PDF may have at most PLAN_UPLOAD_LIMITS[plan]["max_pages_per_doc"] pages.
The document count per plan is enforced by the frontend, not here.
File processing is stubbed, so these tests stop right after the upload checks.
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
def client(monkeypatch):
    async def _stub_process_files(*args, **kwargs):
        raise RuntimeError("stub: upload checks passed")

    monkeypatch.setattr(file_processor, "process_files", _stub_process_files)
    monkeypatch.setattr(gateway.limiter, "enabled", False, raising=False)
    gateway.app.dependency_overrides[require_user_key] = lambda: None
    gateway.app.state.agent_graph = None
    yield TestClient(gateway.app, raise_server_exceptions=False)
    gateway.app.dependency_overrides.pop(require_user_key, None)


def _post(client, url, page_counts):
    files = [
        ("files", (f"doc{i}.pdf", _pdf(n), "application/pdf"))
        for i, n in enumerate(page_counts)
    ]
    return client.post(url, data={"query": "hi"}, files=files)


@pytest.mark.parametrize("plan", ["499", "999"])
def test_pdfs_within_page_limit_are_accepted(client, plan):
    assert _post(client, f"/pyapi/chat/{plan}", [30, 1]).status_code == 200


@pytest.mark.parametrize("plan", ["499", "999"])
def test_pdf_over_page_limit_is_rejected(client, plan):
    r = _post(client, f"/pyapi/chat/{plan}", [5, 31])
    assert r.status_code == 403
    assert r.json()["message"] == (
        f"doc1.pdf has 31 pages. Your {plan} plan allows up to 30 pages per document"
    )


def test_document_count_is_not_limited_by_backend(client):
    assert _post(client, "/pyapi/chat/499", [1, 1, 1]).status_code == 200


def test_unknown_plan_is_rejected(client):
    r = _post(client, "/pyapi/chat/123", [1])
    assert r.status_code == 400
    assert "Unknown plan '123'" in r.json()["message"]


def test_plain_chat_route_has_no_page_limit(client):
    assert _post(client, "/pyapi/chat", [31]).status_code == 200


def test_plan_cannot_be_set_by_query_param(client):
    assert _post(client, "/pyapi/chat?plan=499", [31]).status_code == 200
