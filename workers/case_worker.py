"""Case workspace queue worker: ``python -m workers.case_worker``.

One process, one job at a time; run several processes for throughput.
Loop: claim (``SELECT … FOR UPDATE SKIP LOCKED``) -> run -> finish, with a
guard thread that renews the job lock every third of ``CASE_JOB_LOCK_TTL_SEC``
and refreshes this worker's ``case_workers`` row. A missed renewal (the lock
was reclaimed by another worker) stops the job without writing anything.

Job types: ``ingest`` (core/case_ingest.py: download, sha256 dedupe,
extraction in a killable child process, sidecar) and ``purge``
(core/case_purge.py: every object version under the deleted case or document,
then the free-text fields on the tombstoned rows).

Outcomes per attempt: permanent errors fail the document on the spot;
transient ones go back to the queue with backoff (``CASE_RETRY_BACKOFF_SEC``)
until ``max_attempts``; every claim counts an attempt, so a job whose worker
keeps dying ends in ``failed`` with ``worker_lost``. On SIGTERM the loop
stops claiming; a job in flight gets ``CASE_WORKER_SHUTDOWN_GRACE_SEC`` to
finish, then its child is killed and the job is released to the queue with
the attempt not counted.

Housekeeping (every ``CASE_HOUSEKEEPING_INTERVAL_SEC`` and at startup):
``uploading`` rows older than ``CASE_UPLOAD_EXPIRE_HOURS`` become ``expired``
and any object they left behind is deleted; ``case_tmp`` directories older
than ``CASE_TMP_SWEEP_AGE_SEC`` are removed; worker rows unseen for a week
are pruned.

The worker refuses to start with ``CASE_WORKSPACE_ENABLED`` off. Logging:
ids, codes, counts and durations only (docs/case_workspace_foundation.md).
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from core.case_extract import DEFAULT_ENTRY
from core.case_ingest import (
    IngestError, JobAborted, job_dir, remove_job_dir, run_ingest_job, sweep_case_tmp,
)
from core.case_purge import PurgeRefused, run_purge_job
from core.case_store import (
    DOC_DELETED, DOC_FAILED, DOC_PROCESSING, DOC_QUEUED,
    JOB_CANCELLED, JOB_DONE, JOB_FAILED, JOB_INGEST, JOB_PURGE,
)
from core.logger import get_logger, short_err

log = get_logger("CaseWorker")

DEFAULT_BACKOFF_S = (30, 120, 600)
GOOD_OUTCOMES = ("ready", "duplicate", "done", "skipped")


def parse_backoff(spec: str) -> tuple[int, ...]:
    out = []
    for part in (spec or "").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return tuple(out) or DEFAULT_BACKOFF_S


class _JobGuard(threading.Thread):
    """Per-job sidecar thread: renews the lock, flags ``lost`` when the
    renewal fails, turns a stop request into ``abort`` after the grace period
    and refreshes the worker row."""

    def __init__(self, worker: "CaseWorker", job: dict, lost: threading.Event, abort: threading.Event):
        super().__init__(name=f"case-guard-{job['job_id'][:8]}", daemon=True)
        self.w, self.job, self.lost, self.abort = worker, job, lost, abort
        self._done = threading.Event()
        self.interval = max(1.0, worker.lock_ttl_s / 3.0)

    def run(self) -> None:
        next_beat = time.monotonic() + self.interval
        stop_seen_at: Optional[float] = None
        while not self._done.wait(0.25):
            now = time.monotonic()
            if self.w.stopping:
                stop_seen_at = stop_seen_at if stop_seen_at is not None else now
                if now - stop_seen_at >= self.w.shutdown_grace_s:
                    self.abort.set()
            if now < next_beat:
                continue
            next_beat = now + self.interval
            try:
                ok = self.w.store.heartbeat_job_sync(self.job["job_id"], self.w.worker_id, self.w.lock_ttl_s)
            except Exception as e:
                log.warning("Heartbeat failed", job=self.job["job_id"][:8], error=short_err(e))
                continue
            if not ok:
                log.warning("Lock lost: abandoning job", job=self.job["job_id"][:8], worker=self.w.worker_id)
                self.lost.set()
                self.abort.set()
                return
            try:
                self.w.store.worker_heartbeat_sync(self.w.worker_id, self.w.started_at,
                                                   current_job_id=self.job["job_id"])
            except Exception:
                pass

    def finish(self) -> None:
        self._done.set()
        self.join(5)


class CaseWorker:
    def __init__(self, store, s3, *, worker_id: Optional[str] = None, lock_ttl_s: int = 120,
                 poll_s: float = 2.0, ingest_timeout_s: float = 900, tmp_root: Optional[str] = None,
                 backoff_s: Sequence[int] = DEFAULT_BACKOFF_S, housekeeping_every_s: int = 600,
                 upload_expire_hours: int = 24, tmp_sweep_age_s: int = 3600, shutdown_grace_s: float = 30,
                 in_process: bool = False, extract_entry: str = DEFAULT_ENTRY, classify: bool = False,
                 job_types: Optional[Sequence[str]] = None):
        self.store, self.s3 = store, s3
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.lock_ttl_s = int(lock_ttl_s)
        self.poll_s = float(poll_s)
        self.ingest_timeout_s = float(ingest_timeout_s)
        self.tmp_root = tmp_root or os.path.join(os.getcwd(), "case_tmp")
        self.backoff_s = tuple(backoff_s) or DEFAULT_BACKOFF_S
        self.housekeeping_every_s = int(housekeeping_every_s)
        self.upload_expire_hours = int(upload_expire_hours)
        self.tmp_sweep_age_s = int(tmp_sweep_age_s)
        self.shutdown_grace_s = float(shutdown_grace_s)
        self.in_process = bool(in_process)
        self.extract_entry = extract_entry or DEFAULT_ENTRY
        self.classify = bool(classify)
        self.job_types = tuple(job_types) if job_types else None
        self.started_at = datetime.now(timezone.utc)
        self.stats = {"done": 0, "failed": 0, "claimed": 0}
        self._stop = threading.Event()
        self._next_housekeeping = 0.0

    # ------------------------------------------------------------ lifecycle

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()

    def startup(self) -> None:
        self.store.ensure_schema()
        os.makedirs(self.tmp_root, exist_ok=True)
        swept = sweep_case_tmp(self.tmp_root, self.tmp_sweep_age_s)
        self.store.worker_heartbeat_sync(self.worker_id, self.started_at)
        self._next_housekeeping = time.monotonic()              # housekeeping runs on the first loop
        log.info("Worker started", worker=self.worker_id, swept=swept, lock_ttl_s=self.lock_ttl_s,
                 timeout_s=int(self.ingest_timeout_s), in_process=self.in_process,
                 types=",".join(self.job_types) if self.job_types else "all")

    def run_forever(self) -> None:
        self.startup()
        try:
            while not self._stop.is_set():
                if time.monotonic() >= self._next_housekeeping:
                    self.housekeeping()
                    self._next_housekeeping = time.monotonic() + self.housekeeping_every_s
                if self.run_once() is None:
                    self._stop.wait(self.poll_s)
        finally:
            try:
                self.store.worker_stopped_sync(self.worker_id)
            except Exception as e:
                log.warning("Worker row not closed", error=short_err(e))
            log.info("Worker stopped", worker=self.worker_id, **self.stats)

    def run_once(self) -> Optional[str]:
        """Claim and run at most one job. ``None`` when the queue is empty."""
        try:
            job = self.store.claim_job_sync(self.worker_id, self.lock_ttl_s, self.job_types)
        except Exception as e:
            log.error("Claim failed; backing off", error=short_err(e))
            self._stop.wait(min(30.0, max(self.poll_s * 5, 1.0)))
            return None
        if job is None:
            return None
        self.stats["claimed"] += 1
        return self._run_job(job)

    # --------------------------------------------------------- housekeeping

    def housekeeping(self) -> dict:
        out = {"expired": 0, "objects_deleted": 0, "tmp_removed": 0, "workers_pruned": 0}
        try:
            rows = self.store.expire_stale_uploads_sync(self.upload_expire_hours)
            out["expired"] = len(rows)
            for r in rows:
                try:
                    head = self.s3.head(r["s3_key"])
                    if head.exists:
                        self.s3.delete(r["s3_key"], head.version_id or None)
                        out["objects_deleted"] += 1
                except Exception as e:
                    log.warning("Expired upload object not removed", doc=r["doc_id"][:8], error=short_err(e))
        except Exception as e:
            log.warning("Expire stale uploads failed", error=short_err(e))
        out["tmp_removed"] = sweep_case_tmp(self.tmp_root, self.tmp_sweep_age_s)
        try:
            out["workers_pruned"] = self.store.prune_workers_sync(7)
        except Exception as e:
            log.warning("Worker prune failed", error=short_err(e))
        log.info("Housekeeping", **out)
        return out

    # ------------------------------------------------------------------ jobs

    def _backoff(self, attempts: int) -> int:
        return int(self.backoff_s[min(max(attempts, 1) - 1, len(self.backoff_s) - 1)])

    def _run_job(self, job: dict) -> str:
        jid = job["job_id"]
        started, t0 = datetime.now(timezone.utc), time.monotonic()
        lost, abort = threading.Event(), threading.Event()
        guard = _JobGuard(self, job, lost, abort)
        guard.start()
        outcome = "failed"
        try:
            if job["attempts"] > job["max_attempts"]:
                outcome = self._fail_exhausted(job, started)
            elif job["type"] == JOB_INGEST:
                outcome = self._run_ingest(job, abort, lost, started)
            elif job["type"] == JOB_PURGE:
                outcome = self._run_purge(job, lost, started)
            else:
                self.store.finish_job_sync(jid, self.worker_id, JOB_CANCELLED, error_code="unknown_type",
                                           started_at=started)
                outcome = "cancelled"
        except Exception as e:                                   # nothing may escape the job boundary
            log.error("Job crashed", job=jid[:8], type=job["type"], error=short_err(e))
            outcome = self._close_error(job, None, "internal", short_err(e), False, started, lost)
        finally:
            guard.finish()
            try:
                remove_job_dir(job_dir(self.tmp_root, jid))      # always, success or not
            except Exception:
                pass
            self._record(job, outcome, t0)
        return outcome

    def _fail_exhausted(self, job: dict, started: datetime) -> str:
        """Reclaimed once more than ``max_attempts`` allows: the previous
        owners all died mid-job. Fail it without running it again."""
        if job["type"] == JOB_INGEST and job.get("doc_id"):
            # the dead owner may have died before or after marking the row processing
            if self.store.mark_processing_sync(job["doc_id"], job["case_id"]):
                self.store.finish_document_sync(job["doc_id"], job["case_id"], status=DOC_FAILED,
                                                error_code="worker_lost", error_detail="attempts exhausted")
        self.store.finish_job_sync(job["job_id"], self.worker_id, JOB_FAILED, error_code="worker_lost",
                                   error="attempts exhausted by reclaims", started_at=started)
        log.warning("Job failed: attempts exhausted by reclaims", job=job["job_id"][:8], attempts=job["attempts"])
        return "failed"

    def _run_ingest(self, job: dict, abort: threading.Event, lost: threading.Event, started: datetime) -> str:
        jid, did, cid = job["job_id"], job.get("doc_id"), job["case_id"]
        if not did:
            self.store.finish_job_sync(jid, self.worker_id, JOB_FAILED, error_code="internal",
                                       error="ingest job without doc_id", started_at=started)
            return "failed"
        doc = self.store.get_document_for_job_sync(did, cid)
        if doc is None or doc["case_id"] != cid:                 # the job must never cross a case boundary
            self.store.finish_job_sync(jid, self.worker_id, JOB_FAILED, error_code="internal",
                                       error="document row missing for job", started_at=started)
            return "failed"
        if doc["status"] == DOC_DELETED:
            self.store.finish_job_sync(jid, self.worker_id, JOB_CANCELLED, error_code="doc_deleted",
                                       started_at=started)
            return "cancelled"
        if doc["status"] not in (DOC_QUEUED, DOC_PROCESSING):
            log.info("Ingest skipped: document already final", job=jid[:8], doc=did[:8], status=doc["status"])
            self.store.finish_job_sync(jid, self.worker_id, JOB_DONE, started_at=started)
            return "skipped"
        if not self.store.mark_processing_sync(did, cid):
            self.store.finish_job_sync(jid, self.worker_id, JOB_CANCELLED, error_code="doc_moved",
                                       started_at=started)
            return "cancelled"
        try:
            outcome = run_ingest_job(job, doc, store=self.store, s3=self.s3, tmp_root=self.tmp_root,
                                     timeout_s=self.ingest_timeout_s, abort=abort, lost=lost,
                                     in_process=self.in_process, entry=self.extract_entry, classify=self.classify)
        except JobAborted as a:
            if a.reason == "lock_lost" or lost.is_set():
                return "lost"                                    # another worker owns it: write nothing
            self.store.finish_document_sync(did, cid, status=DOC_QUEUED)
            self.store.finish_job_sync(jid, self.worker_id, "release", started_at=started)
            log.info("Job released for shutdown", job=jid[:8], doc=did[:8])
            return "released"
        except IngestError as e:
            return self._close_error(job, did, e.code, e.detail, e.permanent, started, lost)
        except Exception as e:
            log.error("Ingest crashed", job=jid[:8], doc=did[:8], error=short_err(e))
            return self._close_error(job, did, "internal", short_err(e), False, started, lost)
        if lost.is_set():
            return "lost"
        self.store.finish_job_sync(jid, self.worker_id, JOB_DONE, started_at=started)
        return outcome

    def _close_error(self, job: dict, did: Optional[str], code: str, detail: str, permanent: bool,
                     started: datetime, lost: threading.Event) -> str:
        if lost.is_set():
            return "lost"
        jid, cid = job["job_id"], job["case_id"]
        retry = (not permanent) and job["attempts"] < job["max_attempts"]
        if retry:
            delay = self._backoff(job["attempts"])
            if did:
                self.store.finish_document_sync(did, cid, status=DOC_QUEUED, error_code=code, error_detail=detail)
            self.store.finish_job_sync(jid, self.worker_id, "retry", error_code=code, error=detail,
                                       retry_in_s=delay, started_at=started)
            log.warning("Job will retry", job=jid[:8], doc=(did or "")[:8], code=code,
                        attempt=job["attempts"], retry_in_s=delay)
            return "retry"
        if did:
            self.store.finish_document_sync(did, cid, status=DOC_FAILED, error_code=code, error_detail=detail)
        self.store.finish_job_sync(jid, self.worker_id, JOB_FAILED, error_code=code, error=detail,
                                   started_at=started)
        log.warning("Job failed", job=jid[:8], doc=(did or "")[:8], code=code, permanent=permanent,
                    attempt=job["attempts"])
        return "failed"

    def _run_purge(self, job: dict, lost: threading.Event, started: datetime) -> str:
        jid = job["job_id"]
        try:
            run_purge_job(job, store=self.store, s3=self.s3)
        except PurgeRefused as e:
            self.store.finish_job_sync(jid, self.worker_id, JOB_CANCELLED, error_code="purge_refused",
                                       error=str(e), started_at=started)
            log.warning("Purge refused: target is not deleted", job=jid[:8])
            return "cancelled"
        except IngestError as e:
            return self._close_error(job, None, e.code, e.detail, e.permanent, started, lost)
        except ValueError as e:                                  # prefix guard: a bug, not a retry
            self.store.finish_job_sync(jid, self.worker_id, JOB_FAILED, error_code="internal", error=short_err(e),
                                       started_at=started)
            return "failed"
        if lost.is_set():
            return "lost"
        self.store.finish_job_sync(jid, self.worker_id, JOB_DONE, started_at=started)
        return "done"

    def _record(self, job: dict, outcome: str, t0: float) -> None:
        elapsed = time.monotonic() - t0
        good = outcome in GOOD_OUTCOMES
        if good:
            self.stats["done"] += 1
        elif outcome == "failed":
            self.stats["failed"] += 1
        try:
            from core.metrics import METRICS
            METRICS["case_jobs_total"].labels(type=job["type"], outcome=outcome).inc()
            if job["type"] == JOB_INGEST:
                METRICS["case_ingest_seconds"].observe(elapsed)
        except Exception:
            pass
        try:
            self.store.worker_heartbeat_sync(self.worker_id, self.started_at, current_job_id=None,
                                             done_delta=1 if good else 0,
                                             failed_delta=1 if outcome == "failed" else 0)
        except Exception:
            pass
        log.info("Job closed", job=job["job_id"][:8], type=job["type"], outcome=outcome, s=round(elapsed, 2))


# ---------------------------------------------------------------- process

def build_from_settings(store=None, s3=None, **overrides) -> CaseWorker:
    from core import settings as st
    from core.case_s3 import get_case_s3
    from core.case_store import get_case_store
    kw = dict(
        lock_ttl_s=st.CASE_JOB_LOCK_TTL_SEC, poll_s=st.CASE_WORKER_POLL_SEC,
        ingest_timeout_s=st.CASE_INGEST_TIMEOUT_SEC, tmp_root=st.CASE_TMP_ROOT,
        backoff_s=parse_backoff(st.CASE_RETRY_BACKOFF_SEC), housekeeping_every_s=st.CASE_HOUSEKEEPING_INTERVAL_SEC,
        upload_expire_hours=st.CASE_UPLOAD_EXPIRE_HOURS, tmp_sweep_age_s=st.CASE_TMP_SWEEP_AGE_SEC,
        shutdown_grace_s=st.CASE_WORKER_SHUTDOWN_GRACE_SEC, classify=st.CASE_CLASSIFY_ON_INGEST,
    )
    kw.update(overrides)
    return CaseWorker(store or get_case_store(), s3 or get_case_s3(), **kw)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Case workspace queue worker")
    ap.add_argument("--once", action="store_true", help="claim and run at most one job, then exit")
    ap.add_argument("--metrics-port", type=int, default=None, help="serve Prometheus metrics on 127.0.0.1:PORT")
    ap.add_argument("--types", default="", help="comma-separated job types to claim (default: all)")
    args = ap.parse_args(argv)

    from core import settings as st
    if not st.CASE_WORKSPACE_ENABLED:
        log.error("CASE_WORKSPACE_ENABLED is off: the case worker refuses to start")
        return 2
    types = tuple(t.strip() for t in args.types.split(",") if t.strip()) or None
    worker = build_from_settings(job_types=types)

    port = args.metrics_port if args.metrics_port is not None else st.CASE_WORKER_METRICS_PORT
    if port:
        from prometheus_client import start_http_server
        start_http_server(int(port), addr="127.0.0.1")
        log.info("Metrics listener up", port=int(port))

    def _stop(signum, _frame):
        log.info("Signal received: stopping after the current job", signal=int(signum))
        worker.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):                            # not the main thread / unsupported
            pass

    if args.once:
        worker.startup()
        try:
            out = worker.run_once()
        finally:
            worker.store.worker_stopped_sync(worker.worker_id)
        print(out or "idle")
        return 0
    worker.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
