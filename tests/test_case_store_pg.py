"""core/case_store.py against a real PostgreSQL.

Needs TEST_POSTGRES_URL (e.g. postgresql://postgres:t@localhost:5433/postgres).
Locally without it the module is skipped; with CI=true it fails, so the
pipeline can never go green without exercising the real SQL.

Each session gets a throwaway schema (case_test_<hex>) that is dropped at the end.

Local Postgres in one line:
    docker run --rm -d --name case-pg -e POSTGRES_PASSWORD=t -p 5433:5432 postgres:16-alpine
    TEST_POSTGRES_URL=postgresql://postgres:t@localhost:5433/postgres pytest tests/test_case_store_pg.py
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

URL = os.getenv("TEST_POSTGRES_URL", "").strip()
CI = os.getenv("CI", "").strip().lower() in ("1", "true", "yes")

if not URL and CI:
    pytest.fail("CI=true but TEST_POSTGRES_URL is not set: the Postgres-backed case tests must run in CI",
                pytrace=False)

pytestmark = pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL not set (local run without Postgres)")


@pytest.fixture(scope="module")
def schema_name():
    return f"case_test_{uuid.uuid4().hex[:10]}"


@pytest.fixture(scope="module")
def store(schema_name):
    import psycopg
    from core.case_store import CaseStore
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema_name}"')
    s = CaseStore(URL, schema=schema_name, min_size=1, max_size=4)
    try:
        yield s
    finally:
        s.close()
        with psycopg.connect(URL, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema_name}" CASCADE')


def run(coro):
    return asyncio.run(coro)


def test_schema_is_created_versioned_and_idempotent(store, schema_name):
    from core.case_store import MIGRATIONS, SCHEMA_VERSION
    store.ensure_schema()
    store._schema_ok = False          # force a second pass: must be a no-op
    store.ensure_schema()
    assert store.schema_version() == SCHEMA_VERSION
    import psycopg
    with psycopg.connect(URL) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (schema_name,))}
    assert {"cases", "case_documents", "case_jobs", "case_audit", "case_workers", "case_schema_version"} <= tables
    with psycopg.connect(URL) as conn:
        n = conn.execute("SELECT COUNT(*) FROM %s.case_schema_version" % f'"{schema_name}"').fetchone()[0]
    assert n == len(MIGRATIONS)


def test_create_list_get_update(store):
    c = run(store.create_case("u1", {"title": "Sharma v. Mehta", "matter_type": "cheque_dishonour_ni138",
                                     "cnr": "MHAU010123452026"}))
    uuid.UUID(c["case_id"])
    assert c["status"] == "active" and c["doc_count"] == 0 and c["created_at"].tzinfo is not None
    run(store.create_case("u1", {"title": "Second"}))
    items, total = run(store.list_cases("u1", limit=1, offset=0))
    assert total == 2 and len(items) == 1
    got = run(store.get_case(c["case_id"], "u1"))
    assert got and got["title"] == "Sharma v. Mehta" and got["cnr"] == "MHAU010123452026"
    upd = run(store.update_case(c["case_id"], "u1", {"stage": "filed", "status": "archived", "doc_count": 99}))
    assert upd["stage"] == "filed" and upd["status"] == "archived" and upd["doc_count"] == 0
    assert run(store.update_case(c["case_id"], "u1", {})) == run(store.get_case(c["case_id"], "u1"))
    assert run(store.update_case(c["case_id"], "u1", {"status": "deleted"}))["status"] == "archived"


def test_cross_user_access_denied_in_sql(store):
    c = run(store.create_case("owner", {"title": "Private"}))
    assert run(store.get_case(c["case_id"], "intruder")) is None
    assert run(store.update_case(c["case_id"], "intruder", {"title": "Hijack"})) is None
    items, total = run(store.list_cases("intruder"))
    assert total == 0 and items == []
    assert run(store.get_case(c["case_id"], "owner"))["title"] == "Private"


def test_malformed_and_unknown_ids(store):
    assert run(store.get_case("not-a-uuid", "u1")) is None
    assert run(store.get_case(str(uuid.uuid4()), "u1")) is None
    assert run(store.update_case("not-a-uuid", "u1", {"title": "x"})) is None


def test_audit_and_view_throttle(store):
    import psycopg
    c = run(store.create_case("u1", {"title": "Audit me"}))
    run(store.record_audit(c["case_id"], "u1", "case_create", object_id=c["case_id"], request_id="r1"))
    first = run(store.record_view(c["case_id"], "u1", "r2", window_sec=900))
    again = run(store.record_view(c["case_id"], "u1", "r3", window_sec=900))
    other_user = run(store.record_view(c["case_id"], "u2", "r4", window_sec=900))
    assert first is True and again is False and other_user is True
    # a zero-second window writes every time (window is configurable)
    assert run(store.record_view(c["case_id"], "u1", "r5", window_sec=0)) is True
    with psycopg.connect(URL) as conn:
        conn.execute(f'SET search_path TO "{store._schema}", public')
        rows = conn.execute("SELECT action, user_id FROM case_audit WHERE case_id = %s ORDER BY audit_id",
                            (uuid.UUID(c["case_id"]),)).fetchall()
    assert [r[0] for r in rows] == ["case_create", "case_view", "case_view", "case_view"]
    assert [r[1] for r in rows] == ["u1", "u1", "u2", "u1"]


def test_audit_failure_never_raises(store, monkeypatch):
    c = run(store.create_case("u1", {"title": "Resilient"}))
    monkeypatch.setattr(store, "_record_audit_sync", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    run(store.record_audit(c["case_id"], "u1", "case_view"))      # swallowed, logged
    assert run(store.get_case(c["case_id"], "u1")) is not None
