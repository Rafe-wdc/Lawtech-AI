"""The purge job (worker side): what soft delete scheduled after the grace period.

For a document: every version and delete marker of its raw object and of its
``extracted/{doc_id}/`` folder is deleted, then ``filename`` is blanked on
the tombstoned row. For a case: everything under ``cases/{case_id}/`` goes,
then ``title``, ``client_name``, ``case_number``, ``cnr`` and ``objective``
are blanked on the case row and ``filename`` on every tombstoned document.

A purge never touches a live row: ``purge_target_sync`` returns nothing
unless the row is ``deleted``, and the S3 wrapper refuses any prefix that is
not a case, a raw object or an extracted folder. Ids are never reused.
"""
from __future__ import annotations

import time

from core.case_s3 import case_prefix, extracted_prefix
from core.case_ingest import IngestError
from core.logger import get_logger, short_err

log = get_logger("CasePurge")


class PurgeRefused(Exception):
    """The target is not a tombstoned row; the job is cancelled, nothing is deleted."""


def run_purge_job(job: dict, *, store, s3) -> dict:
    cid, did = job["case_id"], job.get("doc_id")
    target = store.purge_target_sync(cid, did)
    if target is None:
        raise PurgeRefused("target is not deleted")
    t0 = time.monotonic()
    prefixes = [case_prefix(cid)] if did is None else [target["s3_key"], extracted_prefix(cid, did)]
    deleted = 0
    for prefix in prefixes:
        try:
            deleted += s3.delete_all_versions(prefix)
        except ValueError:
            raise                                   # bad prefix: a bug, never retried
        except Exception as e:
            raise IngestError("storage_error", short_err(e), permanent=False) from e
    if did is None:
        rows = store.purge_case_rows_sync(cid, deleted)
    else:
        rows = {"documents": int(store.purge_document_row_sync(did, cid, deleted))}
    log.info("Purge done", case=cid[:8], doc=(did or "")[:8], versions_deleted=deleted, rows=rows,
             ms=int((time.monotonic() - t0) * 1000))
    return {"versions_deleted": deleted, "rows": rows}
