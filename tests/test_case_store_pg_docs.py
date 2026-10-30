"""core/case_store.py slice 2 against a real PostgreSQL: documents, jobs, soft
delete, case status and the v1 -> v2 migration. Same TEST_POSTGRES_URL / CI
rules and throwaway-schema fixtures as tests/test_case_store_pg.py.
"""
from __future__ import annotations

import uuid

import pytest

from tests.test_case_store_pg import CI, URL, run, schema_name, store  # noqa: F401  (fixtures)

if not URL and CI:
    pytest.fail("CI=true but TEST_POSTGRES_URL is not set: the Postgres-backed case tests must run in CI",
                pytrace=False)

pytestmark = pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL not set (local run without Postgres)")

PDF_SIZE = 1234


def _doc_fields(suffix="a", size=PDF_SIZE, sha=None, ext="pdf"):
    return {"filename": "Notice.pdf", "ext": ext, "declared_mime": "application/pdf",
            "size_bytes": size, "client_sha256": sha, "s3_key": f"cases/x/raw/{suffix}.{ext}"}


def _columns(conn, schema, table):
    return {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
        (schema, table))}


def test_migration_v1_to_v2_upgrades_a_slice_1_database(schema_name):
    """A database already at version 1 (slice 1 as reviewed) is upgraded in place."""
    import psycopg
    from core.case_store import MIGRATIONS, CaseStore, _SCHEMA_V1
    name = f"{schema_name}_v1"
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{name}"')
        conn.execute(f'SET search_path TO "{name}", public')
        conn.execute("""CREATE TABLE case_schema_version (version INTEGER PRIMARY KEY,
                                                          applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        for stmt in _SCHEMA_V1:
            conn.execute(stmt)
        conn.execute("INSERT INTO case_schema_version (version) VALUES (1)")
        assert "s3_etag" not in _columns(conn, name, "case_documents")
    s = CaseStore(URL, schema=name, min_size=1, max_size=2)
    try:
        s.ensure_schema()
        assert s.schema_version() == MIGRATIONS[-1][0] == 2
        with psycopg.connect(URL, autocommit=True) as conn:
            assert "s3_etag" in _columns(conn, name, "case_documents")
            n = conn.execute(f'SELECT COUNT(*) FROM "{name}".case_schema_version').fetchone()[0]
            assert n == 2
    finally:
        s.close()
        with psycopg.connect(URL, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{name}" CASCADE')


def test_document_lifecycle(store):
    from core.case_store import CaseLimitError, DOC_DUPLICATE, DOC_FAILED, DOC_QUEUED, DOC_UNSUPPORTED
    cid = run(store.create_case("owner-1", {"title": "Doc case"}))["case_id"]
    d1 = str(uuid.uuid4())
    doc = run(store.create_document(cid, "owner-1", d1, _doc_fields("a"), max_docs=500, max_bytes=10**9))
    assert doc["status"] == "uploading" and doc["doc_id"] == d1 and doc["case_id"] == cid

    # caps are enforced under the case lock
    with pytest.raises(CaseLimitError) as e:
        run(store.create_document(cid, "owner-1", str(uuid.uuid4()), _doc_fields("b"), max_docs=1, max_bytes=10**9))
    assert e.value.code == "case_doc_cap"
    with pytest.raises(CaseLimitError) as e:
        run(store.create_document(cid, "owner-1", str(uuid.uuid4()), _doc_fields("b", size=10**9),
                                  max_docs=500, max_bytes=10**9))
    assert e.value.code == "case_bytes_cap"
    run(store.update_case(cid, "owner-1", {"status": "archived"}))
    with pytest.raises(CaseLimitError) as e:
        run(store.create_document(cid, "owner-1", str(uuid.uuid4()), _doc_fields("b"), 500, 10**9))
    assert e.value.code == "case_not_active"
    run(store.update_case(cid, "owner-1", {"status": "active"}))

    # another owner sees nothing and creates nothing
    assert run(store.create_document(cid, "owner-2", str(uuid.uuid4()), _doc_fields("c"), 500, 10**9)) is None
    assert run(store.get_document(cid, d1, "owner-2")) is None
    assert run(store.list_documents(cid, "owner-2", 10, 0)) is None
    assert run(store.case_status(cid, "owner-2")) is None

    # complete -> queued, one ingest job, counts
    out = run(store.finish_complete(cid, d1, "owner-1", outcome=DOC_QUEUED, sniffed_mime="application/pdf",
                                    sse="AES256", version_id="v1", etag="etag-1", enqueue_job=True, max_attempts=3))
    assert out["status"] == DOC_QUEUED and out["sse"] == "AES256" and out["s3_etag"] == "etag-1"
    jobs = store.list_jobs_sync(cid, d1)
    assert len(jobs) == 1 and jobs[0]["type"] == "ingest" and jobs[0]["status"] == "queued"
    assert jobs[0]["max_attempts"] == 3
    case = run(store.get_case(cid, "owner-1"))
    assert case["doc_count"] == 1 and case["bytes_total"] == PDF_SIZE

    # idempotent: a second finish (even a contradictory one) changes nothing
    again = run(store.finish_complete(cid, d1, "owner-1", outcome=DOC_FAILED, error_code="mime_mismatch",
                                      enqueue_job=True))
    assert again["status"] == DOC_QUEUED and again["error_code"] is None
    assert len(store.list_jobs_sync(cid, d1)) == 1

    # duplicate by ETag + size
    d2 = str(uuid.uuid4())
    run(store.create_document(cid, "owner-1", d2, _doc_fields("d"), 500, 10**9))
    assert run(store.find_duplicate(cid, etag="etag-1", size=PDF_SIZE, exclude_doc_id=d2)) == d1
    assert run(store.find_duplicate(cid, etag="etag-1", size=PDF_SIZE + 1, exclude_doc_id=d2)) is None
    assert run(store.find_duplicate(cid, etag="etag-1", size=PDF_SIZE, exclude_doc_id=d1)) is None
    dup = run(store.finish_complete(cid, d2, "owner-1", outcome=DOC_DUPLICATE, etag="etag-1", duplicate_of=d1))
    assert dup["status"] == DOC_DUPLICATE and dup["duplicate_of"] == d1
    assert len(store.list_jobs_sync(cid)) == 1

    # client hash hint
    d4 = str(uuid.uuid4())
    run(store.create_document(cid, "owner-1", d4, _doc_fields("f", sha="ab" * 32), 500, 10**9))
    run(store.finish_complete(cid, d4, "owner-1", outcome=DOC_QUEUED, etag="etag-4", enqueue_job=True))
    assert run(store.find_duplicate(cid, sha256="ab" * 32)) == d4

    # unsupported media counts but has no job
    d3 = str(uuid.uuid4())
    run(store.create_document(cid, "owner-1", d3, _doc_fields("e", size=50, ext="mp3"), 500, 10**9))
    run(store.finish_complete(cid, d3, "owner-1", outcome=DOC_UNSUPPORTED, sniffed_mime="audio/mpeg",
                              sse="AES256", etag="e3"))
    rows, total, counts = run(store.list_documents(cid, "owner-1", 10, 0))
    assert total == 4 and counts == {"queued": 2, "duplicate": 1, "unsupported": 1}
    assert [r["doc_id"] for r in rows] == [d1, d2, d4, d3]                       # created order
    rows, total, _ = run(store.list_documents(cid, "owner-1", 10, 0, status="unsupported"))
    assert total == 1 and rows[0]["doc_id"] == d3
    st = run(store.case_status(cid, "owner-1"))
    assert st["doc_count"] == 4 and st["bytes_total"] == PDF_SIZE * 3 + 50 and st["pending"] == 2

    # soft delete a queued document: ingest cancelled, counts down, purge scheduled later
    res = run(store.soft_delete_document(cid, d1, "owner-1", purge_after_days=7))
    assert res["prev_status"] == DOC_QUEUED and res["jobs_cancelled"] == 1
    assert {j["type"]: j["status"] for j in store.list_jobs_sync(cid, d1)} == {"ingest": "cancelled",
                                                                               "purge": "queued"}
    assert run(store.get_document(cid, d1, "owner-1")) is None
    assert run(store.soft_delete_document(cid, d1, "owner-1", 7)) is None
    case = run(store.get_case(cid, "owner-1"))
    assert case["doc_count"] == 3 and case["bytes_total"] == PDF_SIZE * 2 + 50

    # soft delete the case: everything tombstoned, purge jobs survive, one more scheduled
    res = run(store.soft_delete_case(cid, "owner-1", purge_after_days=7))
    assert res == {"case_id": cid, "status": "deleted", "documents": 3, "jobs_cancelled": 1}   # d4's ingest
    assert run(store.get_case(cid, "owner-1")) is None and run(store.case_status(cid, "owner-1")) is None
    jobs = store.list_jobs_sync(cid)
    purge = [j for j in jobs if j["type"] == "purge"]
    assert len(purge) == 2 and all(j["status"] == "queued" for j in purge)
    assert any(j["doc_id"] is None for j in purge)
    assert not [j for j in jobs if j["type"] == "ingest" and j["status"] == "queued"]
    assert run(store.soft_delete_case(cid, "owner-1", 7)) is None
