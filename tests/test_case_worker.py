"""workers/case_worker.py + core/case_ingest.py + core/case_purge.py against
FakeCaseStore and FakeCaseS3, driven end to end through the HTTP routes
(presign -> PUT -> complete) and then ``CaseWorker.run_once``.

Extraction runs in-process (``in_process=True``) except where a real child
is the point (timeout, shutdown). The Postgres side of the queue is in
tests/test_case_store_pg_jobs.py.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import case_ingest, settings as _settings
from core.case_ingest import parse_sidecar, sweep_case_tmp
from core.case_s3 import case_prefix, meta_key, raw_key, text_key
from tests.case_support import (
    FakeCaseS3, FakeCaseStore, auth_headers, enable_feature, make_client, make_docx, make_pdf,
)
from workers.case_worker import CaseWorker

U1 = auth_headers("u1")


@pytest.fixture
def env(tmp_path, monkeypatch):
    enable_feature(monkeypatch)
    monkeypatch.setattr(_settings, "CASE_JOB_MAX_ATTEMPTS", 3, raising=False)
    now = [time.time()]
    store, s3 = FakeCaseStore(clock=lambda: now[0]), FakeCaseS3()
    client = make_client(store, s3)
    tmp_root = str(tmp_path / "case_tmp")
    worker = CaseWorker(store, s3, worker_id="w1", lock_ttl_s=30, tmp_root=tmp_root, in_process=True,
                        backoff_s=(1, 2, 3), shutdown_grace_s=0, poll_s=0.1)
    worker.startup()

    def advance(seconds: float):
        now[0] += seconds

    return SimpleNamespace(store=store, s3=s3, client=client, worker=worker, tmp_root=Path(tmp_root),
                           advance=advance)


def _case(env, **over) -> str:
    body = {"title": "Sharma v. Mehta Builders", "matter_type": "cheque_dishonour_ni138"}
    body.update(over)
    r = env.client.post("/pyapi/cases", json=body, headers=U1)
    assert r.status_code == 201, r.text
    return r.json()["case_id"]


def _upload(env, cid: str, filename: str, data: bytes, etag=None) -> dict:
    p = env.client.post(f"/pyapi/cases/{cid}/documents/presign",
                        json={"filename": filename, "size_bytes": len(data)}, headers=U1)
    assert p.status_code == 201, p.text
    p = p.json()
    env.s3.put_object(raw_key(cid, p["doc_id"], p["ext"]), data, p["upload"]["headers"]["Content-Type"], etag=etag)
    r = env.client.post(f"/pyapi/cases/{cid}/documents/{p['doc_id']}/complete", headers=U1)
    assert r.status_code == 200, r.text
    return r.json()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- ingest ---------------------------------------------------------------------

def test_ingest_pdf_to_ready_with_a_sidecar_that_names_the_document_on_every_line(env):
    cid = _case(env)
    data = make_pdf(["ONE alpha", "", "THREE gamma"])
    doc = _upload(env, cid, "notice.pdf", data)
    did = doc["doc_id"]
    assert doc["status"] == "queued"
    from core.metrics import METRICS
    before = METRICS["case_jobs_total"].labels(type="ingest", outcome="ready")._value.get()

    assert env.worker.run_once() == "ready"

    row = env.store.documents[did]
    assert row["status"] == "ready" and row["page_count"] == 3 and row["ocr_pages"] == 0
    assert row["extractor"] == "pdf_text" and row["sha256"] == _sha(data) and row["ready_at"] is not None
    assert row["text_key"] == text_key(cid, did) and row["meta_key"] == meta_key(cid, did)
    lines = parse_sidecar(env.s3.get_bytes(text_key(cid, did)))
    assert [ln["page"] for ln in lines] == [1, 2, 3] and all(ln["doc_id"] == did for ln in lines)
    assert "ONE" in lines[0]["text"] and lines[1]["text"] == "" and lines[1]["chars"] == 0
    meta = json.loads(env.s3.get_bytes(meta_key(cid, did)))
    assert meta["doc_id"] == did and meta["case_id"] == cid and meta["sidecar_schema"] == 1
    assert meta["sha256"] == row["sha256"] and meta["lines"] == 3 and meta["extractor"] == "pdf_text"
    job = env.store.jobs_for(doc_id=did, type="ingest")[0]
    assert job["status"] == "done" and job["locked_by"] is None and job["history"][-1]["outcome"] == "done"
    assert not (env.tmp_root / job["job_id"]).exists()                       # scratch removed
    assert METRICS["case_jobs_total"].labels(type="ingest", outcome="ready")._value.get() == before + 1
    assert env.worker.run_once() is None                                      # queue drained

    st = env.client.get(f"/pyapi/cases/{cid}/status", headers=U1).json()
    assert st["documents"] == {"ready": 1} and st["pending"] == 0
    api = env.client.get(f"/pyapi/cases/{cid}/documents/{did}", headers=U1).json()
    assert api["status"] == "ready" and api["page_count"] == 3 and api["stored"] is True and api["sha256"] == _sha(data)
    assert env.client.get(f"/pyapi/cases/{cid}/documents/{did}/download", headers=U1).status_code == 200


def test_duplicate_is_confirmed_by_sha256_and_gets_its_own_sidecar(env, monkeypatch):
    calls = []
    real = case_ingest.run_extraction
    monkeypatch.setattr(case_ingest, "run_extraction", lambda *a, **k: (calls.append(a[2]), real(*a, **k))[1])
    cid = _case(env)
    data = make_pdf(["same bytes, two names"])
    a = _upload(env, cid, "notice.pdf", data)
    b = _upload(env, cid, "notice (copy).pdf", data)
    assert b["status"] == "queued" and b["duplicate_hint"] == a["doc_id"]      # hint only at complete

    assert env.worker.run_once() == "ready"
    assert env.worker.run_once() == "duplicate"
    assert calls == [a["doc_id"]]                                             # b was never extracted
    ra, rb = env.store.documents[a["doc_id"]], env.store.documents[b["doc_id"]]
    assert rb["status"] == "duplicate" and rb["duplicate_of"] == a["doc_id"] and rb["sha256"] == ra["sha256"]
    assert rb["page_count"] == ra["page_count"] == 1 and rb["extractor"] == "pdf_text"
    lines = parse_sidecar(env.s3.get_bytes(text_key(cid, b["doc_id"])))
    assert all(ln["doc_id"] == b["doc_id"] and ln["duplicate_of"] == a["doc_id"] for ln in lines)
    assert lines[0]["text"] == parse_sidecar(env.s3.get_bytes(text_key(cid, a["doc_id"])))[0]["text"]
    assert json.loads(env.s3.get_bytes(meta_key(cid, b["doc_id"])))["duplicate_of"] == a["doc_id"]
    st = env.client.get(f"/pyapi/cases/{cid}/status", headers=U1).json()
    assert st["documents"] == {"ready": 1, "duplicate": 1} and st["doc_count"] == 2


def test_same_etag_and_size_but_different_bytes_are_both_extracted(env):
    """The user's rule: never skip extraction on ETag + size alone."""
    cid = _case(env)
    a_bytes, b_bytes = make_pdf(["ALPHA content"]), make_pdf(["BRAVO content"])
    assert len(a_bytes) == len(b_bytes) and a_bytes != b_bytes
    a = _upload(env, cid, "a.pdf", a_bytes, etag="collide")
    b = _upload(env, cid, "b.pdf", b_bytes, etag="collide")
    assert b["duplicate_hint"] == a["doc_id"]                                 # the hint fired...
    assert env.worker.run_once() == "ready" and env.worker.run_once() == "ready"   # ...and both were processed
    ra, rb = env.store.documents[a["doc_id"]], env.store.documents[b["doc_id"]]
    assert ra["status"] == rb["status"] == "ready" and ra["sha256"] != rb["sha256"]
    assert rb["duplicate_of"] is None
    ta = parse_sidecar(env.s3.get_bytes(text_key(cid, a["doc_id"])))[0]["text"]
    tb = parse_sidecar(env.s3.get_bytes(text_key(cid, b["doc_id"])))[0]["text"]
    assert "ALPHA" in ta and "BRAVO" in tb


def test_docx_ingest_produces_sections(env):
    cid = _case(env)
    doc = _upload(env, cid, "reply.docx", make_docx(["1. Background", "The loan was advanced.", "It was not repaid."]))
    assert env.worker.run_once() == "ready"
    row = env.store.documents[doc["doc_id"]]
    assert row["extractor"] == "docx" and row["page_count"] is None
    lines = parse_sidecar(env.s3.get_bytes(text_key(cid, doc["doc_id"])))
    assert lines[0]["unit"] == "section" and lines[0]["heading"] == "1. Background" and lines[0]["page"] is None


def test_corrupt_file_fails_alone_while_its_sibling_becomes_ready(env):
    cid = _case(env)
    good = _upload(env, cid, "good.pdf", make_pdf(["fine"]))
    bad = _upload(env, cid, "bad.pdf", b"%PDF-1.4\n" + b"\x00garbage" * 50)     # passes the sniff, not PyMuPDF
    assert {env.worker.run_once(), env.worker.run_once()} == {"ready", "failed"}
    rb = env.store.documents[bad["doc_id"]]
    assert rb["status"] == "failed" and rb["error_code"] in ("corrupt_file", "empty_pdf")
    assert rb["stored"] is True                                               # the object is kept
    assert env.client.get(f"/pyapi/cases/{cid}/documents/{bad['doc_id']}/download", headers=U1).status_code == 200
    assert env.store.documents[good["doc_id"]]["status"] == "ready"
    job = env.store.jobs_for(doc_id=bad["doc_id"], type="ingest")[0]
    assert job["status"] == "failed" and job["attempts"] == 1 and job["last_error_code"] == rb["error_code"]
    st = env.client.get(f"/pyapi/cases/{cid}/status", headers=U1).json()
    assert st["documents"] == {"ready": 1, "failed": 1} and st["doc_count"] == 2   # still counted


def test_transient_error_retries_with_backoff_then_fails_after_max_attempts(env):
    env.worker.extract_entry = "tests.case_support:failing_extract"
    cid = _case(env)
    doc = _upload(env, cid, "flaky.pdf", make_pdf(["x"]))
    did = doc["doc_id"]

    assert env.worker.run_once() == "retry"
    job = env.store.jobs_for(doc_id=did, type="ingest")[0]
    assert job["status"] == "queued" and job["attempts"] == 1 and job["last_error_code"] == "extract_error"
    assert env.store.documents[did]["status"] == "queued" and env.store.documents[did]["error_code"] == "extract_error"
    assert env.worker.run_once() is None                                      # backoff not elapsed
    env.advance(1.5)
    assert env.worker.run_once() == "retry" and job["attempts"] == 2
    env.advance(2.5)
    assert env.worker.run_once() == "failed" and job["attempts"] == 3 and job["status"] == "failed"
    assert env.store.documents[did]["status"] == "failed" and env.store.documents[did]["error_code"] == "extract_error"
    assert [h["outcome"] for h in job["history"]] == ["retry", "retry", "failed"]
    assert env.worker.stats == {"done": 0, "failed": 1, "claimed": 3}


def test_timeout_kills_the_child_and_retries(env):
    env.worker.in_process = False
    env.worker.extract_entry = "tests.case_support:hanging_extract"
    env.worker.ingest_timeout_s = 3
    cid = _case(env)
    doc = _upload(env, cid, "slow.pdf", make_pdf(["x"]))
    t0 = time.monotonic()
    assert env.worker.run_once() == "retry"
    assert time.monotonic() - t0 < 60
    job = env.store.jobs_for(doc_id=doc["doc_id"], type="ingest")[0]
    assert job["last_error_code"] == "timeout" and job["status"] == "queued"
    assert env.store.documents[doc["doc_id"]]["error_code"] == "timeout"
    assert not (env.tmp_root / job["job_id"]).exists()


def test_lost_lock_abandons_the_job_without_writing(env):
    env.worker.lock_ttl_s = 3                                                 # guard beats every second
    env.worker.extract_entry = "tests.case_support:slow_extract"              # ~3 s, in-process
    cid = _case(env)
    doc = _upload(env, cid, "contested.pdf", make_pdf(["x"]))
    did = doc["doc_id"]
    job = env.store.jobs_for(doc_id=did, type="ingest")[0]

    def steal():
        time.sleep(0.5)
        job["locked_by"] = "w2"                                               # another worker reclaimed it

    threading.Thread(target=steal, daemon=True).start()
    assert env.worker.run_once() == "lost"
    assert job["locked_by"] == "w2" and job["status"] == "running"             # untouched by w1
    assert env.store.documents[did]["status"] == "processing"
    assert text_key(cid, did) not in env.s3.objects                           # nothing written


def test_shutdown_releases_the_job_and_requeues_the_document(env):
    env.worker.in_process = False
    env.worker.extract_entry = "tests.case_support:hanging_extract"
    cid = _case(env)
    doc = _upload(env, cid, "interrupted.pdf", make_pdf(["x"]))
    threading.Timer(1.0, env.worker.stop).start()
    assert env.worker.run_once() == "released"
    job = env.store.jobs_for(doc_id=doc["doc_id"], type="ingest")[0]
    assert job["status"] == "queued" and job["attempts"] == 0 and job["locked_by"] is None
    assert env.store.documents[doc["doc_id"]]["status"] == "queued"
    assert job["history"][-1]["outcome"] == "release"


def test_reclaimed_beyond_max_attempts_fails_with_worker_lost(env):
    cid = _case(env)
    doc = _upload(env, cid, "cursed.pdf", make_pdf(["x"]))
    job = env.store.jobs_for(doc_id=doc["doc_id"], type="ingest")[0]
    job.update(status="running", locked_by="dead-worker", attempts=job["max_attempts"],
               lock_expires_at=env.store._now() - timedelta(seconds=1))
    assert env.worker.run_once() == "failed"
    assert job["status"] == "failed" and job["last_error_code"] == "worker_lost"
    assert [h["outcome"] for h in job["history"]][-2:] == ["lost", "failed"]
    assert env.store.documents[doc["doc_id"]]["error_code"] == "worker_lost"


def test_document_deleted_before_the_job_runs_is_cancelled(env):
    cid = _case(env)
    doc = _upload(env, cid, "gone.pdf", make_pdf(["x"]))
    env.store.documents[doc["doc_id"]]["status"] = "deleted"                  # tombstoned under the job's feet
    assert env.worker.run_once() == "cancelled"
    job = env.store.jobs_for(doc_id=doc["doc_id"], type="ingest")[0]
    assert job["status"] == "cancelled" and job["last_error_code"] == "doc_deleted"


# --- purge -----------------------------------------------------------------------

def test_document_purge_removes_every_version_and_blanks_the_filename(env):
    cid = _case(env)
    doc = _upload(env, cid, "Mehta notice.pdf", make_pdf(["x"]))
    did = doc["doc_id"]
    assert env.worker.run_once() == "ready"
    env.s3.put_object(raw_key(cid, did, "pdf"), b"%PDF-1.4 second version", "application/pdf")   # a stray overwrite
    assert env.client.delete(f"/pyapi/cases/{cid}/documents/{did}", headers=U1).status_code == 200
    assert env.worker.run_once() is None                                      # purge not due for 7 days
    env.advance(8 * 86400)
    assert env.worker.run_once() == "done"
    assert env.s3.list_versions(f"cases/{cid}/raw/{did}") == [] and env.s3.list_versions(f"cases/{cid}/extracted/{did}/") == []
    row = env.store.documents[did]
    assert row["status"] == "deleted" and row["filename"] == "" and row["purged_at"] is not None
    assert any(a["action"] == "doc_purge" and a["object_id"] == f"{did}:4" and a["user_id"] == "system"
               for a in env.store.audit)                                      # 2 raw versions + text + meta


def test_case_purge_blanks_intake_fields_and_deletes_the_whole_prefix(env):
    cid = _case(env, client_name="Rakesh Mehta", case_number="CC 123/2026", cnr="MHPU010012342026",
                objective="Secure conviction under s.138", stage="pre_trial")
    d1 = _upload(env, cid, "a.pdf", make_pdf(["a"]))
    d2 = _upload(env, cid, "b.docx", make_docx(["b"]))
    assert env.worker.run_once() == "ready" and env.worker.run_once() == "ready"
    assert env.client.delete(f"/pyapi/cases/{cid}", headers=U1).status_code == 200
    env.advance(8 * 86400)
    assert env.worker.run_once() == "done"
    c = env.store.cases[cid]
    assert (c["title"], c["client_name"], c["case_number"], c["cnr"], c["objective"]) == ("", "", "", "", "")
    assert c["stage"] == "pre_trial" and c["status"] == "deleted" and c["purged_at"] is not None
    assert all(env.store.documents[d["doc_id"]]["filename"] == "" for d in (d1, d2))
    assert env.s3.list_versions(case_prefix(cid)) == [] and env.s3.live_keys(case_prefix(cid)) == []
    assert any(a["action"] == "case_purge" for a in env.store.audit)
    assert env.worker.run_once() is None


def test_purge_refuses_a_live_target(env):
    cid = _case(env)
    doc = _upload(env, cid, "live.pdf", make_pdf(["x"]))
    assert env.worker.run_once() == "ready"
    env.store.jobs.append({"job_id": "9e1b4c1e-6b7d-4a5e-9a5a-0d3f6a0b1c2d", "case_id": cid, "doc_id": doc["doc_id"],
                           "type": "purge", "status": "queued", "priority": 200, "attempts": 0, "max_attempts": 4,
                           "run_after": env.store._now()})
    assert env.worker.run_once() == "cancelled"
    assert env.store.jobs[-1]["last_error_code"] == "purge_refused"
    assert raw_key(cid, doc["doc_id"], "pdf") in env.s3.objects and env.store.documents[doc["doc_id"]]["filename"] == "live.pdf"


# --- housekeeping -----------------------------------------------------------------

def test_housekeeping_expires_stale_uploads_and_sweeps_old_scratch(env):
    cid = _case(env)
    p = env.client.post(f"/pyapi/cases/{cid}/documents/presign",
                        json={"filename": "never completed.pdf", "size_bytes": 10}, headers=U1).json()
    key = raw_key(cid, p["doc_id"], "pdf")
    env.s3.put_object(key, b"%PDF-1.4 x", "application/pdf")                    # the browser did PUT
    fresh = env.client.post(f"/pyapi/cases/{cid}/documents/presign",
                            json={"filename": "in flight.pdf", "size_bytes": 10}, headers=U1).json()
    env.advance(25 * 3600)
    env.store.documents[fresh["doc_id"]]["created_at"] = env.store._now()       # presigned just now
    old_dir = env.tmp_root / "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f"
    new_dir = env.tmp_root / "1a1a1a1a-1a1a-4a1a-8a1a-1a1a1a1a1a1a"
    stray = env.tmp_root / "not-a-job"
    for d in (old_dir, new_dir, stray):
        d.mkdir(parents=True)
    old = time.time() - 7200
    os.utime(old_dir, (old, old))
    os.utime(stray, (old, old))

    out = env.worker.housekeeping()

    assert out["expired"] == 1 and out["objects_deleted"] == 1 and out["tmp_removed"] == 1
    assert env.store.documents[p["doc_id"]]["status"] == "expired" and key not in env.s3.objects
    assert env.store.documents[fresh["doc_id"]]["status"] == "uploading"
    assert not old_dir.exists() and new_dir.exists() and stray.exists()         # only old job dirs go
    assert any(a["action"] == "doc_expire" and a["user_id"] == "system" for a in env.store.audit)
    assert env.client.get(f"/pyapi/cases/{cid}", headers=U1).json()["doc_count"] == 0


def test_startup_sweep_and_case_tmp_lives_outside_the_gc_roots(tmp_path):
    root = tmp_path / "case_tmp"
    old_dir = root / "2b2b2b2b-2b2b-4b2b-8b2b-2b2b2b2b2b2b"
    old_dir.mkdir(parents=True)
    old = time.time() - 7200
    os.utime(old_dir, (old, old))
    assert sweep_case_tmp(str(root), 3600) == 1 and not old_dir.exists()
    assert sweep_case_tmp(str(tmp_path / "missing"), 3600) == 0
    from core import file_gc
    default_root = Path(_settings.CASE_TMP_ROOT).resolve()
    for other in (_settings.UPLOADS_ROOT, _settings.CHROMA_STORE_ROOT, tempfile.gettempdir()):
        assert not default_root.is_relative_to(Path(other).resolve()), other
    assert hasattr(file_gc, "sweep_all")                                        # the GC has no case_tmp bucket


# --- health and worker rows ------------------------------------------------------

def test_worker_rows_feed_the_health_entry(env, monkeypatch):
    import asyncio
    from core import case_health
    import core.case_store as cs
    monkeypatch.setattr(cs, "get_case_store", lambda: SimpleNamespace(
        schema_version=lambda: 3, queue_summary_sync=env.store.queue_summary_sync))
    out = asyncio.run(case_health.case_workspace_check())
    assert out["status"] == "ok" and out["queue"]["workers_live"] == 1 and out["queue"]["queued"] == 0
    cid = _case(env)
    _upload(env, cid, "x.pdf", make_pdf(["x"]))
    env.store.worker_stopped_sync("w1")
    out = asyncio.run(case_health.case_workspace_check())
    assert out["status"] == "warn" and out["queue"] == {"workers_live": 0, "last_seen_age_s": None, "queued": 1,
                                                        "running": 0, "oldest_queued_s": out["queue"]["oldest_queued_s"]}
    assert out["queue"]["oldest_queued_s"] is not None


# --- logging rule -----------------------------------------------------------------

class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        kv = getattr(record, "_extra_kv", {}) or {}
        self.lines.append(record.getMessage() + " " + " ".join(f"{k}={v}" for k, v in kv.items()))


def test_no_filename_or_text_in_worker_logs(env):
    from core import case_extract, case_purge, case_s3, case_store, file_processor
    import workers.case_worker as wk
    cap = _Capture()
    loggers = [logging.getLogger(m.log._logger.name)
               for m in (wk, case_ingest, case_extract, case_purge, case_store, case_s3, file_processor)]
    for lg in loggers:
        lg.addHandler(cap)
        lg.setLevel(logging.DEBUG)
    try:
        cid = _case(env, title="SENTINEL-TITLE", client_name="SENTINEL-CLIENT")
        doc = _upload(env, cid, "SENTINEL-Mehta-Notice-8821.pdf", make_pdf(["SENTINEL-TEXT-4471 dishonoured cheque"]))
        assert env.worker.run_once() == "ready"
        bad = _upload(env, cid, "SENTINEL-bad.pdf", b"%PDF-1.4\n" + b"\x00junk" * 40)
        assert env.worker.run_once() == "failed"
        env.client.delete(f"/pyapi/cases/{cid}", headers=U1)
        env.advance(8 * 86400)
        assert env.worker.run_once() == "done"
    finally:
        for lg in loggers:
            lg.removeHandler(cap)
    text = "\n".join(cap.lines)
    assert cap.lines and doc["doc_id"][:8] in text and bad["doc_id"][:8] in text
    for needle in ("SENTINEL", "Mehta", "8821", "4471", "dishonoured", "Sharma"):
        assert needle not in text, f"{needle!r} leaked into logs: {text[:400]}"
