"""Slice 2 routes against FakeCaseStore + FakeCaseS3: presign, complete, list,
detail, download, soft delete, status, caps, audit, isolation and the
no-filename-in-logs rule. Real SQL: test_case_store_pg.py. Real boto3
parameters: test_case_s3.py.
"""
from __future__ import annotations

import logging
import uuid

import pytest

from core import settings as _settings
from core.case_s3 import raw_key
from tests.case_support import FakeCaseS3, FakeCaseStore, auth_headers, enable_feature, make_client
from tests.test_case_sniff import DOCX, MP3_ID3, PDF, PNG

U1, U2 = auth_headers("u1"), auth_headers("u2")


@pytest.fixture
def store():
    return FakeCaseStore()


@pytest.fixture
def s3():
    return FakeCaseS3()


@pytest.fixture
def client(store, s3, monkeypatch):
    enable_feature(monkeypatch)
    return make_client(store, s3)


def _case(client, headers=U1, **over) -> str:
    body = {"title": "Sharma v. Mehta Builders", "matter_type": "cheque_dishonour_ni138"}
    body.update(over)
    r = client.post("/pyapi/cases", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["case_id"]


def _presign(client, cid, filename="Legal Notice.pdf", data=PDF, headers=U1, **over):
    body = {"filename": filename, "size_bytes": len(data)}
    body.update(over)
    return client.post(f"/pyapi/cases/{cid}/documents/presign", json=body, headers=headers)


def _upload(s3: FakeCaseS3, cid: str, presigned: dict, data: bytes, sse="AES256"):
    key = raw_key(cid, presigned["doc_id"], presigned["ext"])
    assert presigned["upload"]["url"].split("?")[0].endswith(key)
    s3.put_object(key, data, presigned["upload"]["headers"]["Content-Type"], sse=sse)
    return key


def _complete(client, cid, doc_id, headers=U1):
    return client.post(f"/pyapi/cases/{cid}/documents/{doc_id}/complete", headers=headers)


def _uploaded_doc(client, s3, cid, filename="Legal Notice.pdf", data=PDF) -> dict:
    p = _presign(client, cid, filename, data).json()
    _upload(s3, cid, p, data)
    r = _complete(client, cid, p["doc_id"])
    assert r.status_code == 200, r.text
    return r.json()


# --- presign ------------------------------------------------------------------

def test_presign_returns_upload_instructions_and_an_id_only_key(client, store, s3):
    cid = _case(client)
    r = _presign(client, cid, "Legal Notice from Adv. Rao.PDF", content_type="application/pdf")
    assert r.status_code == 201, r.text
    body = r.json()
    uuid.UUID(body["doc_id"])
    assert body["status"] == "uploading" and body["ext"] == "pdf" and body["filename"] == "Legal Notice from Adv. Rao.PDF"
    assert body["upload"]["method"] == "PUT" and body["upload"]["headers"] == {"Content-Type": "application/pdf"}
    url = body["upload"]["url"]
    assert f"cases/{cid}/raw/{body['doc_id']}.pdf" in url and "Legal" not in url and "Rao" not in url
    assert body["duplicate_hint"] is None
    assert store.audit[-1]["action"] == "doc_presign" and store.audit[-1]["object_id"] == body["doc_id"]
    # nothing is counted until complete
    assert client.get(f"/pyapi/cases/{cid}", headers=U1).json()["doc_count"] == 0
    assert s3.presigned_puts[-1][1] == "application/pdf"


def test_presign_rejects_unsupported_type_and_oversize(client, monkeypatch):
    cid = _case(client)
    r = _presign(client, cid, "payload.exe", data=b"MZ" * 10)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unsupported_type"
    assert _presign(client, cid, "report.doc", data=b"x" * 10).status_code == 422       # legacy Word
    assert _presign(client, cid, "noext", data=b"x" * 10).status_code == 422
    monkeypatch.setattr(_settings, "CASE_MAX_FILE_MB", 1, raising=False)
    r = _presign(client, cid, "big.pdf", size_bytes=2 * 1024 * 1024)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "file_too_large"
    assert _presign(client, cid, "empty.pdf", size_bytes=0).status_code == 422
    assert client.post(f"/pyapi/cases/{cid}/documents/presign", json={"filename": "a.pdf", "size_bytes": 5, "owner": "x"},
                       headers=U1).status_code == 422                                     # extra=forbid


def test_presign_enforces_case_caps_and_archived_state(client, monkeypatch):
    cid = _case(client)
    monkeypatch.setattr(_settings, "CASE_MAX_DOCS_PER_CASE", 2, raising=False)
    assert _presign(client, cid, "a.pdf").status_code == 201
    assert _presign(client, cid, "b.pdf").status_code == 201
    r = _presign(client, cid, "c.pdf")
    assert r.status_code == 413 and r.json()["detail"]["code"] == "case_doc_cap"
    monkeypatch.setattr(_settings, "CASE_MAX_DOCS_PER_CASE", 500, raising=False)
    monkeypatch.setattr(_settings, "CASE_MAX_BYTES_PER_CASE_GB", 1e-6, raising=False)   # ~1 KB
    r = _presign(client, cid, "d.pdf", size_bytes=4096)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "case_bytes_cap"
    monkeypatch.setattr(_settings, "CASE_MAX_BYTES_PER_CASE_GB", 5, raising=False)
    client.patch(f"/pyapi/cases/{cid}", json={"status": "archived"}, headers=U1)
    r = _presign(client, cid, "e.pdf")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "case_not_active"


def test_presign_duplicate_hint_from_client_hash(client, s3):
    cid = _case(client)
    sha = "a" * 64
    first = _uploaded_doc(client, s3, cid)
    # the hash is only recorded at presign; attach it to a second upload and look for a third
    p2 = _presign(client, cid, "same again.pdf", data=PDF + b" ", client_sha256=sha.upper()).json()
    assert p2["duplicate_hint"] is None
    _upload(s3, cid, p2, PDF + b" ")
    assert _complete(client, cid, p2["doc_id"]).json()["status"] == "queued"
    p3 = _presign(client, cid, "third.pdf", client_sha256=sha).json()
    assert p3["duplicate_hint"] == p2["doc_id"] and p3["status"] == "uploading"
    assert first["doc_id"] != p2["doc_id"]


# --- complete -----------------------------------------------------------------

def test_complete_queues_creates_one_job_and_is_idempotent(client, store, s3):
    cid = _case(client)
    p = _presign(client, cid).json()
    key = _upload(s3, cid, p, PDF)
    r = _complete(client, cid, p["doc_id"])
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["status"] == "queued" and doc["sniffed_mime"] == "application/pdf" and doc["sse"] == "AES256"
    assert doc["error_code"] is None and doc["duplicate_of"] is None and doc["duplicate_hint"] is None
    assert doc["stored"] is True
    jobs = store.jobs_for(doc_id=p["doc_id"], type="ingest")
    assert len(jobs) == 1 and jobs[0]["status"] == "queued" and jobs[0]["case_id"] == cid
    case = client.get(f"/pyapi/cases/{cid}", headers=U1).json()
    assert case["doc_count"] == 1 and case["bytes_total"] == len(PDF)
    assert any(a["action"] == "doc_complete" and a["object_id"] == f"{p['doc_id']}:queued" for a in store.audit)
    # second complete: same answer, still one job, object untouched
    r2 = _complete(client, cid, p["doc_id"])
    assert r2.status_code == 200 and r2.json()["status"] == "queued"
    assert len(store.jobs_for(doc_id=p["doc_id"], type="ingest")) == 1
    assert key in s3.objects and not s3.deleted


def test_complete_rejects_mime_mismatch_and_deletes_the_object(client, store, s3):
    cid = _case(client)
    p = _presign(client, cid, "scan.pdf", data=PNG).json()
    key = _upload(s3, cid, p, PNG)                       # PDF extension, PNG bytes
    r = _complete(client, cid, p["doc_id"])
    assert r.status_code == 422 and r.json()["detail"] == {"code": "mime_mismatch", "doc_id": p["doc_id"]}
    doc = client.get(f"/pyapi/cases/{cid}/documents/{p['doc_id']}", headers=U1).json()
    assert doc["status"] == "failed" and doc["error_code"] == "mime_mismatch" and doc["sniffed_mime"] == "image/png"
    assert s3.deleted == [(key, "v1")] and key not in s3.objects
    assert not store.jobs_for(doc_id=p["doc_id"])
    assert client.get(f"/pyapi/cases/{cid}", headers=U1).json()["doc_count"] == 0
    assert any(a["object_id"] == f"{p['doc_id']}:failed:mime_mismatch" for a in store.audit)
    # a failed row is terminal: complete again just reports it
    assert _complete(client, cid, p["doc_id"]).json()["status"] == "failed"


def test_complete_rejects_size_mismatch_missing_sse_and_missing_object(client, store, s3):
    cid = _case(client)
    p = _presign(client, cid, "a.pdf", data=PDF).json()
    key = _upload(s3, cid, p, PDF + b"extra bytes")
    r = _complete(client, cid, p["doc_id"])
    assert r.status_code == 422 and r.json()["detail"]["code"] == "size_mismatch"
    assert (key, "v1") in s3.deleted

    p = _presign(client, cid, "b.pdf", data=PDF).json()
    key = _upload(s3, cid, p, PDF, sse="")
    r = _complete(client, cid, p["doc_id"])
    assert r.status_code == 422 and r.json()["detail"]["code"] == "sse_missing"
    assert key not in s3.objects

    p = _presign(client, cid, "c.pdf", data=PDF).json()
    r = _complete(client, cid, p["doc_id"])              # never uploaded
    assert r.status_code == 409 and r.json()["detail"]["code"] == "object_missing"
    assert client.get(f"/pyapi/cases/{cid}/documents/{p['doc_id']}", headers=U1).json()["status"] == "failed"
    assert not store.jobs and client.get(f"/pyapi/cases/{cid}", headers=U1).json()["doc_count"] == 0


def test_complete_with_wrong_sse_algorithm_is_rejected(client, s3, monkeypatch):
    monkeypatch.setattr(_settings, "CASE_S3_SSE", "aws:kms", raising=False)
    cid = _case(client)
    p = _presign(client, cid).json()
    _upload(s3, cid, p, PDF, sse="AES256")
    r = _complete(client, cid, p["doc_id"])
    assert r.status_code == 422 and r.json()["detail"]["code"] == "sse_missing"


def test_media_is_stored_as_unsupported_without_a_job(client, store, s3):
    cid = _case(client)
    p = _presign(client, cid, "hearing recording.mp3", data=MP3_ID3).json()
    assert p["upload"]["headers"] == {"Content-Type": "audio/mpeg"}
    _upload(s3, cid, p, MP3_ID3)
    doc = _complete(client, cid, p["doc_id"]).json()
    assert doc["status"] == "unsupported" and doc["sniffed_mime"] == "audio/mpeg" and doc["error_code"] is None
    assert not store.jobs_for(doc_id=p["doc_id"])
    assert client.get(f"/pyapi/cases/{cid}", headers=U1).json()["doc_count"] == 1       # stored, counted
    assert client.get(f"/pyapi/cases/{cid}/documents/{p['doc_id']}/download", headers=U1).status_code == 200


def test_same_content_is_queued_with_a_hint_never_marked_duplicate_at_complete(client, store, s3):
    """ETag + size is a hint. Only the worker marks `duplicate`, after the sha256 matches."""
    cid = _case(client)
    first = _uploaded_doc(client, s3, cid, "notice.pdf")
    second = _uploaded_doc(client, s3, cid, "notice (copy).pdf")           # same bytes, other name
    assert second["status"] == "queued" and second["duplicate_of"] is None
    assert second["duplicate_hint"] == first["doc_id"]
    assert len(store.jobs_for(case_id=cid, type="ingest")) == 2             # both are extracted
    third = _uploaded_doc(client, s3, cid, "annexure.docx", DOCX)         # different content: no hint
    assert third["status"] == "queued" and third["duplicate_hint"] is None
    assert third["sniffed_mime"].endswith("wordprocessingml.document")
    st = client.get(f"/pyapi/cases/{cid}/status", headers=U1).json()
    assert st["documents"] == {"queued": 3} and st["pending"] == 3 and st["doc_count"] == 3
    items = client.get(f"/pyapi/cases/{cid}/documents", headers=U1).json()["items"]
    assert len(items) == 3 and all(d["stored"] for d in items)


def test_same_etag_and_size_but_different_bytes_are_both_queued(client, store, s3):
    """An ETag collision must never skip extraction: each document gets its own job."""
    cid = _case(client)
    a_bytes, b_bytes = PDF + b"A" * 16, PDF + b"B" * 16                     # same length, other content
    pa = _presign(client, cid, "a.pdf", data=a_bytes).json()
    s3.put_object(raw_key(cid, pa["doc_id"], "pdf"), a_bytes, "application/pdf", etag="collide")
    pb = _presign(client, cid, "b.pdf", data=b_bytes).json()
    s3.put_object(raw_key(cid, pb["doc_id"], "pdf"), b_bytes, "application/pdf", etag="collide")
    a = _complete(client, cid, pa["doc_id"]).json()
    b = _complete(client, cid, pb["doc_id"]).json()
    assert a["status"] == b["status"] == "queued"
    assert a["duplicate_hint"] is None and b["duplicate_hint"] == a["doc_id"]     # a hint, nothing more
    assert b["duplicate_of"] is None
    jobs = store.jobs_for(case_id=cid, type="ingest")
    assert {j["doc_id"] for j in jobs} == {a["doc_id"], b["doc_id"]}
    assert all(j["status"] == "queued" for j in jobs)


# --- list, detail, status -----------------------------------------------------

def test_list_detail_and_status(client, store, s3):
    cid = _case(client)
    a = _uploaded_doc(client, s3, cid, "a.pdf")
    _presign(client, cid, "b.pdf")                                         # still uploading
    bad = _presign(client, cid, "c.pdf", data=PNG).json()
    _upload(s3, cid, bad, PNG)
    _complete(client, cid, bad["doc_id"])                                  # failed
    lst = client.get(f"/pyapi/cases/{cid}/documents", headers=U1).json()
    assert lst["total"] == 3 and lst["counts"] == {"queued": 1, "uploading": 1, "failed": 1}
    assert [d["doc_id"] for d in lst["items"]][0] == a["doc_id"]            # oldest first
    only_q = client.get(f"/pyapi/cases/{cid}/documents?status=queued", headers=U1).json()
    assert only_q["total"] == 1 and only_q["items"][0]["doc_id"] == a["doc_id"] and only_q["counts"]["failed"] == 1
    assert client.get(f"/pyapi/cases/{cid}/documents?limit=0", headers=U1).status_code == 422
    detail = client.get(f"/pyapi/cases/{cid}/documents/{a['doc_id']}", headers=U1).json()
    assert detail["filename"] == "a.pdf" and detail["status"] == "queued" and "s3_key" not in detail
    st = client.get(f"/pyapi/cases/{cid}/status", headers=U1)
    assert st.status_code == 200
    body = st.json()
    assert body["doc_count"] == 1 and body["pending"] == 2 and body["documents"]["failed"] == 1
    for _ in range(4):
        client.get(f"/pyapi/cases/{cid}/status", headers=U1)
    assert len([x for x in store.audit if x["action"] == "case_view"]) == 1   # polling is throttled


# --- download -----------------------------------------------------------------

def test_download_url_is_short_lived_owner_only_and_names_the_file_only_in_disposition(client, store, s3):
    cid = _case(client)
    doc = _uploaded_doc(client, s3, cid, "Legal Notice (final).pdf")
    r = client.get(f"/pyapi/cases/{cid}/documents/{doc['doc_id']}/download", headers=U1)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"] == "Legal Notice (final).pdf" and body["content_type"] == "application/pdf"
    url = body["url"]
    path, _, query = url.partition("?")
    assert path.endswith(f"cases/{cid}/raw/{doc['doc_id']}.pdf") and "Legal" not in path
    assert "response-content-disposition=" in query and "Legal" in __import__("urllib.parse").parse.unquote(query)
    assert "X-Amz-Expires=300" in query and "versionId=v1" in query
    assert store.audit[-1]["action"] == "doc_download_url" and store.audit[-1]["object_id"] == doc["doc_id"]
    assert client.get(f"/pyapi/cases/{cid}/documents/{doc['doc_id']}/download", headers=U2).status_code == 404
    # not downloadable states
    p = _presign(client, cid, "pending.pdf").json()
    r = client.get(f"/pyapi/cases/{cid}/documents/{p['doc_id']}/download", headers=U1)
    assert r.status_code == 409 and r.json()["detail"] == {"code": "not_downloadable", "status": "uploading"}
    bad = _presign(client, cid, "x.pdf", data=PNG).json()
    _upload(s3, cid, bad, PNG)
    _complete(client, cid, bad["doc_id"])
    assert client.get(f"/pyapi/cases/{cid}/documents/{bad['doc_id']}/download", headers=U1).status_code == 409


# --- soft delete --------------------------------------------------------------

def test_soft_delete_document_tombstones_cancels_the_job_and_schedules_purge(client, store, s3):
    cid = _case(client)
    doc = _uploaded_doc(client, s3, cid)
    r = client.delete(f"/pyapi/cases/{cid}/documents/{doc['doc_id']}", headers=U1)
    assert r.status_code == 200, r.text
    assert r.json() == {"doc_id": doc["doc_id"], "case_id": cid, "status": "deleted", "jobs_cancelled": 1}
    assert client.get(f"/pyapi/cases/{cid}/documents/{doc['doc_id']}", headers=U1).status_code == 404
    assert client.get(f"/pyapi/cases/{cid}/documents", headers=U1).json()["total"] == 0
    case = client.get(f"/pyapi/cases/{cid}", headers=U1).json()
    assert case["doc_count"] == 0 and case["bytes_total"] == 0
    ingest = store.jobs_for(doc_id=doc["doc_id"], type="ingest")
    purge = store.jobs_for(doc_id=doc["doc_id"], type="purge")
    assert ingest[0]["status"] == "cancelled" and len(purge) == 1 and purge[0]["status"] == "queued"
    assert (purge[0]["run_after"] - store._now()).days >= 6                 # grace period, not immediate
    assert [a for a in store.audit if a["action"] == "doc_delete"][-1]["object_id"] == doc["doc_id"]
    assert client.delete(f"/pyapi/cases/{cid}/documents/{doc['doc_id']}", headers=U1).status_code == 404
    assert s3.objects                                                       # object stays until the purge job


def test_soft_delete_case_tombstones_everything(client, store, s3):
    cid = _case(client)
    _uploaded_doc(client, s3, cid, "a.pdf")
    _uploaded_doc(client, s3, cid, "b.docx", DOCX)
    _presign(client, cid, "c.pdf")
    r = client.delete(f"/pyapi/cases/{cid}", headers=U1)
    assert r.status_code == 200, r.text
    assert r.json() == {"case_id": cid, "status": "deleted", "documents": 3, "jobs_cancelled": 2}
    assert client.get(f"/pyapi/cases/{cid}", headers=U1).status_code == 404
    assert client.get(f"/pyapi/cases/{cid}/status", headers=U1).status_code == 404
    assert client.get(f"/pyapi/cases/{cid}/documents", headers=U1).status_code == 404
    assert client.get("/pyapi/cases", headers=U1).json()["total"] == 0
    assert client.delete(f"/pyapi/cases/{cid}", headers=U1).status_code == 404
    purge = store.jobs_for(case_id=cid, type="purge")
    assert len(purge) == 1 and purge[0]["doc_id"] is None
    assert store.audit[-1]["action"] == "case_delete"


# --- isolation ----------------------------------------------------------------

def test_cross_user_is_404_on_every_document_route(client, store, s3):
    cid = _case(client)
    doc = _uploaded_doc(client, s3, cid)
    p = _presign(client, cid, "pending.pdf").json()
    before = (dict(store.cases[cid]), {k: dict(v) for k, v in store.documents.items()}, len(store.jobs))
    assert _presign(client, cid, "intruder.pdf", headers=U2).status_code == 404
    assert _complete(client, cid, p["doc_id"], headers=U2).status_code == 404
    assert client.get(f"/pyapi/cases/{cid}/documents", headers=U2).status_code == 404
    assert client.get(f"/pyapi/cases/{cid}/documents/{doc['doc_id']}", headers=U2).status_code == 404
    assert client.get(f"/pyapi/cases/{cid}/documents/{doc['doc_id']}/download", headers=U2).status_code == 404
    assert client.delete(f"/pyapi/cases/{cid}/documents/{doc['doc_id']}", headers=U2).status_code == 404
    assert client.get(f"/pyapi/cases/{cid}/status", headers=U2).status_code == 404
    assert client.delete(f"/pyapi/cases/{cid}", headers=U2).status_code == 404
    after = (dict(store.cases[cid]), {k: dict(v) for k, v in store.documents.items()}, len(store.jobs))
    assert before == after
    assert not [a for a in store.audit if a["user_id"] == "u2"]


def test_malformed_ids_are_404_not_500(client):
    cid = _case(client)
    assert _presign(client, "not-a-uuid", "a.pdf").status_code == 404
    assert _complete(client, cid, "not-a-uuid").status_code == 404
    assert client.get(f"/pyapi/cases/{cid}/documents/not-a-uuid", headers=U1).status_code == 404
    assert client.get(f"/pyapi/cases/{uuid.uuid4()}/documents", headers=U1).status_code == 404
    assert client.delete(f"/pyapi/cases/{cid}/documents/{uuid.uuid4()}", headers=U1).status_code == 404


def test_flag_off_and_bad_token_still_gate_document_routes(store, s3, monkeypatch):
    enable_feature(monkeypatch, enabled=False)
    c = make_client(store, s3)
    assert c.post(f"/pyapi/cases/{uuid.uuid4()}/documents/presign", json={"filename": "a.pdf", "size_bytes": 5},
                  headers=U1).status_code == 403
    enable_feature(monkeypatch)
    assert c.get(f"/pyapi/cases/{uuid.uuid4()}/documents", headers={"X-API-Key": "k"}).status_code == 401


# --- logging rule -------------------------------------------------------------

class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        kv = getattr(record, "_extra_kv", {}) or {}
        self.lines.append(record.getMessage() + " " + " ".join(f"{k}={v}" for k, v in kv.items()))


def test_no_filename_or_content_appears_in_logs(client, store, s3):
    """The structured loggers do not propagate to root, so attach a handler to
    each case logger and run the sentinel through every document route."""
    sentinel_name = "SENTINEL-Mehta-Notice-8821.pdf"
    sentinel_text = b"%PDF-1.4\nSENTINEL-TEXT-4471 cheque of Rs 4,70,000 dishonoured\n%%EOF\n"
    cap = _Capture()
    from core import case_auth, case_routes, case_s3, case_store
    loggers = [logging.getLogger(m.log._logger.name) for m in (case_routes, case_store, case_s3, case_auth)]
    for lg in loggers:
        lg.addHandler(cap)
        lg.setLevel(logging.DEBUG)
    try:
        cid = _case(client, title="SENTINEL-TITLE-Sharma v Mehta", client_name="SENTINEL-CLIENT")
        p = _presign(client, cid, sentinel_name, data=sentinel_text).json()
        _upload(s3, cid, p, sentinel_text)
        assert _complete(client, cid, p["doc_id"]).status_code == 200
        bad = _presign(client, cid, "SENTINEL-BAD-" + sentinel_name, data=PNG).json()
        _upload(s3, cid, bad, PNG)
        _complete(client, cid, bad["doc_id"])
        client.get(f"/pyapi/cases/{cid}/documents", headers=U1)
        client.get(f"/pyapi/cases/{cid}/documents/{p['doc_id']}/download", headers=U1)
        client.delete(f"/pyapi/cases/{cid}/documents/{p['doc_id']}", headers=U1)
        client.delete(f"/pyapi/cases/{cid}", headers=U1)
    finally:
        for lg in loggers:
            lg.removeHandler(cap)
    text = "\n".join(cap.lines)
    assert cap.lines, "expected the case loggers to emit something during the flow"
    for needle in ("SENTINEL", "Mehta", "8821", "4471", "dishonoured", "Sharma"):
        assert needle not in text, f"{needle!r} leaked into logs: {text[:300]}"
    assert bad["doc_id"][:8] in text                                        # ids are logged (rejection line)
