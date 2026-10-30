"""core/case_store.py slice 3 against a real PostgreSQL: SKIP LOCKED claim,
heartbeat, reclaim, finish outcomes and history, worker-side document
transitions, housekeeping, worker rows, purge row scrubbing and the
v2 -> v3 migration. Same TEST_POSTGRES_URL / CI rules and throwaway-schema
fixtures as tests/test_case_store_pg.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from tests.test_case_store_pg import CI, URL, run, schema_name, store  # noqa: F401  (fixtures)

if not URL and CI:
    pytest.fail("CI=true but TEST_POSTGRES_URL is not set: the Postgres-backed case tests must run in CI",
                pytrace=False)

pytestmark = pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL not set (local run without Postgres)")


def _sql(store, statement, params=()):
    with store._get_pool().connection() as conn:
        cur = conn.execute(statement, params)
        rows = cur.fetchall() if cur.description else None
        conn.commit()
    return rows


def _queued_doc(store, owner="owner-1", size=100, cid=None):
    from core.case_store import DOC_QUEUED
    cid = cid or run(store.create_case(owner, {"title": "Queue case", "client_name": "Someone"}))["case_id"]
    did = str(uuid.uuid4())
    run(store.create_document(cid, owner, did, {"filename": "f.pdf", "ext": "pdf", "size_bytes": size,
                                                "s3_key": f"cases/{cid}/raw/{did}.pdf"}, 500, 10**9))
    run(store.finish_complete(cid, did, owner, outcome=DOC_QUEUED, sse="AES256", version_id="v1",
                              etag=f"e-{did[:6]}", enqueue_job=True, max_attempts=3))
    job = store.list_jobs_sync(cid, did)[0]
    return cid, did, job["job_id"]


def test_migration_v2_to_v3(schema_name):
    import psycopg
    from core.case_store import MIGRATIONS, CaseStore, _SCHEMA_V1, _SCHEMA_V2
    name = f"{schema_name}_v2"
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{name}"')
        conn.execute(f'SET search_path TO "{name}", public')
        conn.execute("""CREATE TABLE case_schema_version (version INTEGER PRIMARY KEY,
                                                          applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        for stmt in _SCHEMA_V1 + _SCHEMA_V2:
            conn.execute(stmt)
        conn.execute("INSERT INTO case_schema_version (version) VALUES (1), (2)")
    s = CaseStore(URL, schema=name, min_size=1, max_size=2)
    try:
        s.ensure_schema()
        assert s.schema_version() == MIGRATIONS[-1][0] == 3
        with psycopg.connect(URL, autocommit=True) as conn:
            cols = {(r[0], r[1]) for r in conn.execute(
                "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = %s", (name,))}
        assert {("cases", "purged_at"), ("case_documents", "purged_at"), ("case_workers", "stopped_at"),
                ("case_workers", "current_job_id")} <= cols
    finally:
        s.close()
        with psycopg.connect(URL, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{name}" CASCADE')


def test_claim_is_exclusive_and_reclaims_an_expired_lock(store):
    cid, d1, j1 = _queued_doc(store)
    _, d2, j2 = _queued_doc(store, cid=cid)
    a = store.claim_job_sync("w1", lock_ttl_s=60)
    b = store.claim_job_sync("w2", lock_ttl_s=60)
    assert {a["job_id"], b["job_id"]} == {j1, j2} and a["status"] == b["status"] == "running"
    assert a["attempts"] == 1 and a["locked_by"] == "w1" and a["lock_expires_at"] > a["locked_at"]
    assert store.claim_job_sync("w3", 60) is None                              # nothing left: never claimed twice
    assert store.claim_job_sync("w3", 60, types=["purge"]) is None

    assert store.heartbeat_job_sync(a["job_id"], "w1", 60) is True
    assert store.heartbeat_job_sync(a["job_id"], "w3", 60) is False            # not the holder

    # w1 dies: its lock expires and w3 reclaims the job, attempt counted, loss recorded
    _sql(store, "UPDATE case_jobs SET lock_expires_at = now() - interval '1 second' WHERE job_id = %s", (a["job_id"],))
    c = store.claim_job_sync("w3", 60)
    assert c["job_id"] == a["job_id"] and c["locked_by"] == "w3" and c["attempts"] == 2
    assert c["history"][-1]["outcome"] == "lost" and c["history"][-1]["worker"] == "w1"
    assert store.heartbeat_job_sync(a["job_id"], "w1", 60) is False
    assert store.finish_job_sync(a["job_id"], "w1", "done") is False           # the late finish changes nothing
    assert store.get_job_sync(a["job_id"])["locked_by"] == "w3"
    assert store.finish_job_sync(a["job_id"], "w3", "done") is True
    assert store.finish_job_sync(b["job_id"], "w2", "done") is True


def test_finish_job_outcomes_history_and_release(store):
    cid, did, jid = _queued_doc(store)
    started = datetime.now(timezone.utc)
    j = store.claim_job_sync("w1", 60)
    assert j["job_id"] == jid
    assert store.finish_job_sync(jid, "w1", "retry", error_code="extract_error", error="x" * 500,
                                 retry_in_s=60, started_at=started) is True
    j = store.get_job_sync(jid)
    assert j["status"] == "queued" and j["attempts"] == 1 and j["locked_by"] is None
    assert j["last_error_code"] == "extract_error" and len(j["last_error"]) == 200
    assert (j["run_after"] - datetime.now(timezone.utc)).total_seconds() > 50
    assert store.claim_job_sync("w1", 60) is None                              # backoff not elapsed
    _sql(store, "UPDATE case_jobs SET run_after = now() WHERE job_id = %s", (jid,))
    j = store.claim_job_sync("w1", 60)
    assert j["attempts"] == 2
    assert store.finish_job_sync(jid, "w1", "release", started_at=started) is True   # graceful shutdown
    j = store.get_job_sync(jid)
    assert j["status"] == "queued" and j["attempts"] == 1 and j["run_after"] <= datetime.now(timezone.utc)
    j = store.claim_job_sync("w2", 60)
    assert j["attempts"] == 2
    assert store.finish_job_sync(jid, "w2", "failed", error_code="timeout", started_at=started) is True
    j = store.get_job_sync(jid)
    assert j["status"] == "failed" and j["finished_at"] is not None and j["last_error_code"] == "timeout"
    assert [h["outcome"] for h in j["history"]] == ["retry", "release", "failed"]
    assert j["history"][-1]["attempt"] == 2 and j["history"][-1]["worker"] == "w2"
    assert j["history"][0]["started"] == started.isoformat()
    assert store.finish_job_sync(jid, "w2", "done") is False                   # already closed
    assert store.finish_job_sync(jid, "w2", "nonsense") is False

    # history is capped at 20 entries, oldest dropped
    _, _, j2 = _queued_doc(store, cid=cid)
    _sql(store, "UPDATE case_jobs SET history = %s::jsonb WHERE job_id = %s",
         ('[' + ','.join(f'{{"attempt": {i}}}' for i in range(20)) + ']', j2))
    store.claim_job_sync("w1", 60, types=["ingest"])
    assert store.finish_job_sync(j2, "w1", "cancelled", error_code="doc_deleted") is True
    h = store.get_job_sync(j2)["history"]
    assert len(h) == 20 and h[0] == {"attempt": 1} and h[-1]["outcome"] == "cancelled"
    assert store.get_job_sync(j2)["status"] == "cancelled"


def test_document_transitions_on_the_worker_side(store):
    from core.case_store import DOC_DUPLICATE, DOC_FAILED, DOC_QUEUED, DOC_READY
    cid, d1, _ = _queued_doc(store)
    _, d2, _ = _queued_doc(store, cid=cid)
    row = store.get_document_for_job_sync(d1, cid)
    assert row["status"] == DOC_QUEUED and row["stored"] is True
    assert store.get_document_for_job_sync(d1, str(uuid.uuid4())) is None       # wrong case: nothing

    assert store.mark_processing_sync(d1, cid) is True
    assert store.mark_processing_sync(d1, cid) is True                         # reclaim: processing -> processing
    assert store.find_ready_by_sha256_sync(cid, "ab" * 32, exclude_doc_id=d2) is None
    ready = store.finish_document_sync(d1, cid, status=DOC_READY, sha256="AB" * 32, page_count=3, chars_total=120,
                                       ocr_pages=1, extractor="pdf_text+ocr_pages",
                                       text_key=f"cases/{cid}/extracted/{d1}/text.jsonl",
                                       meta_key=f"cases/{cid}/extracted/{d1}/meta.json", kind="notice",
                                       kind_confidence=0.9)
    assert ready["status"] == DOC_READY and ready["sha256"] == "ab" * 32 and ready["ready_at"] is not None
    assert ready["page_count"] == 3 and ready["kind"] == "notice" and ready["text_key"].endswith("text.jsonl")
    assert store.mark_processing_sync(d1, cid) is False                        # final rows never go back
    assert store.finish_document_sync(d1, cid, status=DOC_FAILED, error_code="x") is None

    # the authoritative match ignores client hashes and non-ready rows
    assert store.find_ready_by_sha256_sync(cid, "ab" * 32, exclude_doc_id=d2) == d1
    assert store.find_ready_by_sha256_sync(cid, "ab" * 32, exclude_doc_id=d1) is None
    assert store.mark_processing_sync(d2, cid) is True
    dup = store.finish_document_sync(d2, cid, status=DOC_DUPLICATE, sha256="ab" * 32, duplicate_of=d1,
                                     page_count=3, chars_total=120, extractor="pdf_text+ocr_pages",
                                     text_key="t", meta_key="m")
    assert dup["status"] == DOC_DUPLICATE and dup["duplicate_of"] == d1
    assert store.find_ready_by_sha256_sync(cid, "ab" * 32, exclude_doc_id=str(uuid.uuid4())) == d1   # never the duplicate

    # transient failure: back to queued keeps the error code; then failed keeps the object (stored)
    _, d3, _ = _queued_doc(store, cid=cid)
    assert store.mark_processing_sync(d3, cid)
    back = store.finish_document_sync(d3, cid, status=DOC_QUEUED, error_code="timeout", error_detail="slow")
    assert back["status"] == DOC_QUEUED and back["error_code"] == "timeout"
    assert store.mark_processing_sync(d3, cid)
    failed = store.finish_document_sync(d3, cid, status=DOC_FAILED, error_code="corrupt_file", error_detail="x" * 300)
    assert failed["status"] == DOC_FAILED and failed["stored"] is True
    api = run(store.get_document(cid, d3, "owner-1"))
    assert api["status"] == DOC_FAILED and api["stored"] is True
    assert run(store.get_case(cid, "owner-1"))["doc_count"] == 3                # ingest failures stay counted
    res = run(store.soft_delete_document(cid, d3, "owner-1", 7))
    assert res["prev_status"] == DOC_FAILED and run(store.get_case(cid, "owner-1"))["doc_count"] == 2


def test_expire_stale_uploads_worker_rows_and_queue_summary(store):
    cid = run(store.create_case("owner-1", {"title": "HK"}))["case_id"]
    stale, fresh = str(uuid.uuid4()), str(uuid.uuid4())
    for did in (stale, fresh):
        run(store.create_document(cid, "owner-1", did, {"filename": "f.pdf", "ext": "pdf", "size_bytes": 5,
                                                        "s3_key": f"cases/{cid}/raw/{did}.pdf"}, 500, 10**9))
    _sql(store, "UPDATE case_documents SET created_at = now() - interval '25 hours' WHERE doc_id = %s", (stale,))
    rows = store.expire_stale_uploads_sync(24)
    assert rows == [{"doc_id": stale, "case_id": cid, "s3_key": f"cases/{cid}/raw/{stale}.pdf"}]
    assert run(store.get_document(cid, stale, "owner-1"))["status"] == "expired"
    assert run(store.get_document(cid, fresh, "owner-1"))["status"] == "uploading"
    assert store.expire_stale_uploads_sync(24) == []
    audit = _sql(store, "SELECT user_id, action, object_id FROM case_audit WHERE case_id = %s AND action = 'doc_expire'",
                 (cid,))
    assert audit == [{"user_id": "system", "action": "doc_expire", "object_id": stale}]
    assert run(store.get_case(cid, "owner-1"))["doc_count"] == 0

    started = datetime.now(timezone.utc)
    store.worker_heartbeat_sync("host:1:abcd", started)
    store.worker_heartbeat_sync("host:1:abcd", started, current_job_id=None, done_delta=2, failed_delta=1)
    w = _sql(store, "SELECT jobs_done, jobs_failed, stopped_at FROM case_workers WHERE worker_id = 'host:1:abcd'")[0]
    assert (w["jobs_done"], w["jobs_failed"], w["stopped_at"]) == (2, 1, None)
    summary = store.queue_summary_sync(stale_after_s=180)
    assert summary["workers_live"] == 1 and summary["last_seen_age_s"] < 5
    before = summary["queued"]                                                # the schema is shared by the module
    _, _, _ = _queued_doc(store, cid=cid)
    summary = store.queue_summary_sync(180)
    assert summary["queued"] == before + 1 and summary["running"] == 0 and summary["oldest_queued_s"] is not None
    _sql(store, "UPDATE case_workers SET last_seen_at = now() - interval '10 minutes'")
    summary = store.queue_summary_sync(180)
    assert summary["workers_live"] == 0 and summary["last_seen_age_s"] >= 600
    store.worker_stopped_sync("host:1:abcd")
    assert store.queue_summary_sync(180)["last_seen_age_s"] is None             # a clean stop is not a dead worker
    assert store.prune_workers_sync(older_than_days=0) == 1


def test_purge_scrubs_only_tombstoned_rows(store):
    from core.case_store import DOC_READY
    cid = run(store.create_case("owner-1", {"title": "Sharma v Mehta", "client_name": "R. Mehta",
                                            "case_number": "CC 12/2026", "cnr": "MHPU010012342026",
                                            "objective": "conviction", "stage": "trial"}))["case_id"]
    _, d1, _ = _queued_doc(store, cid=cid)
    _, d2, _ = _queued_doc(store, cid=cid)
    store.mark_processing_sync(d1, cid)
    store.finish_document_sync(d1, cid, status=DOC_READY, sha256="cd" * 32, text_key="t", meta_key="m")

    # live rows: nothing is a purge target, nothing is scrubbed
    assert store.purge_target_sync(cid, None) is None and store.purge_target_sync(cid, d1) is None
    assert store.purge_document_row_sync(d1, cid) is False
    assert store.purge_case_rows_sync(cid) == {"case": 0, "documents": 0}
    assert run(store.get_case(cid, "owner-1"))["title"] == "Sharma v Mehta"

    run(store.soft_delete_document(cid, d1, "owner-1", 7))
    t = store.purge_target_sync(cid, d1)
    assert t["doc_id"] == d1 and t["s3_key"] == f"cases/{cid}/raw/{d1}.pdf" and t["purged_at"] is None
    assert store.purge_document_row_sync(d1, cid, versions_deleted=3) is True
    row = _sql(store, "SELECT filename, status, purged_at, sha256 FROM case_documents WHERE doc_id = %s", (d1,))[0]
    assert row["filename"] == "" and row["status"] == "deleted" and row["purged_at"] is not None
    assert row["sha256"] == "cd" * 32                                         # only free text is blanked
    assert store.purge_target_sync(cid, d2) is None                            # d2 is still live

    run(store.soft_delete_case(cid, "owner-1", 7))
    assert store.purge_target_sync(cid, None)["doc_id"] is None
    assert store.purge_case_rows_sync(cid, versions_deleted=9) == {"case": 1, "documents": 2}
    c = _sql(store, "SELECT title, client_name, case_number, cnr, objective, stage, purged_at FROM cases "
                    "WHERE case_id = %s", (cid,))[0]
    assert (c["title"], c["client_name"], c["case_number"], c["cnr"], c["objective"]) == ("", "", "", "", "")
    assert c["stage"] == "trial" and c["purged_at"] is not None
    assert all(r["filename"] == "" for r in _sql(store, "SELECT filename FROM case_documents WHERE case_id = %s", (cid,)))
    assert sorted(store.case_document_keys_sync(cid)) == sorted([f"cases/{cid}/raw/{d1}.pdf", f"cases/{cid}/raw/{d2}.pdf"])
    actions = {r["action"] for r in _sql(store, "SELECT action FROM case_audit WHERE case_id = %s", (cid,))}
    assert {"doc_purge", "case_purge"} <= actions
    assert run(store.get_case(cid, "owner-1")) is None
