# Case workspace — foundation runbook

**Status:** slice 1 (cases, auth, schema) in review as PR #144 · slice 2 (uploads, documents, soft delete) on `feat/case-workspace-s2`, waiting for the bucket · slice 3 (queue, worker, ingestion, sidecar, purge) built on `feat/case-workspace-s3`, stacked on slice 2 · **Design:** `Case_Workspace_Foundation_Design.md` (approved 30 Oct 2026 with amendments) · **Plan:** `Case_Study_Plan.md`

The case workspace is a feature-flagged router inside the existing API plus a separate worker process (`python -m workers.case_worker`). Cases, documents, jobs and audit live in the API's PostgreSQL next to the chat tables. Files live in the private `lawttorney-cases` bucket under `cases/{case_id}/`.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `CASE_WORKSPACE_ENABLED` | `0` | Mount `/pyapi/cases`. Off means every case path is 404. |
| `CASE_JWT_SECRET` | falls back to `USER_MEMORY_JWT_SECRET` | HS256 secret of Node's access token. Flag on with no secret → every case route is 403 (fail closed). |
| `CASE_JWT_CLAIM` | `userId` | Claim that carries the owner id; comma-separated list accepted. |
| `CASE_JWT_LEEWAY_S` | `30` | Clock skew tolerated on `exp` / `nbf`. |
| `CASE_AUDIT_VIEW_WINDOW_SEC` | `900` | `case_view` is written at most once per user per case per window (also covers `/status` polling). |
| `POSTGRES_URL` | required | The case store is Postgres-only. |
| `CASE_S3_BUCKET` | `lawttorney-cases` | Separate private bucket for case files (see Bucket below). |
| `CASE_S3_REGION` | `S3_REGION` (`ap-south-1`) | Region of that bucket. |
| `CASE_S3_SSE` | `AES256` | `ServerSideEncryption` value `complete` expects on HEAD; anything else is rejected as `sse_missing`. |
| `CASE_MAX_FILE_MB` | `1024` | Per-file cap, checked at presign (declared) and complete (actual). |
| `CASE_MAX_DOCS_PER_CASE` | `500` | Counts uploading + stored documents. |
| `CASE_MAX_BYTES_PER_CASE_GB` | `5` | Same set, by size. |
| `CASE_PRESIGN_TTL_SEC` | `900` | Life of the presigned PUT. |
| `CASE_DOWNLOAD_TTL_SEC` | `300` | Life of a download URL. |
| `CASE_PURGE_GRACE_DAYS` | `7` | Soft delete → purge job `run_after`. |
| `CASE_JOB_MAX_ATTEMPTS` | `4` | `max_attempts` on ingest jobs created at complete. |

Worker (slice 3):

| Variable | Default | Meaning |
|---|---|---|
| `CASE_TMP_ROOT` | `<project>/case_tmp` | Scratch, one directory per job id; removed in a `finally`, swept at startup. |
| `CASE_JOB_LOCK_TTL_SEC` | `120` | Job lock; renewed every third of it. A worker that misses it loses the job. |
| `CASE_INGEST_TIMEOUT_SEC` | `900` | Extraction child killed after this; the attempt is `timeout` (transient). |
| `CASE_WORKER_POLL_SEC` | `2` | Idle sleep between claims. |
| `CASE_WORKER_SHUTDOWN_GRACE_SEC` | `30` | On SIGTERM, how long a running job may finish before it is released. |
| `CASE_HOUSEKEEPING_INTERVAL_SEC` | `600` | Expire stale uploads, sweep scratch, prune worker rows. |
| `CASE_UPLOAD_EXPIRE_HOURS` | `24` | `uploading` rows older than this become `expired`; a stray object is deleted. |
| `CASE_TMP_SWEEP_AGE_SEC` | `3600` | Age of a leftover `case_tmp/{job_id}` before the sweep removes it. |
| `CASE_RETRY_BACKOFF_SEC` | `30,120,600` | Delay before attempt 2, 3, 4… of a transient failure. |
| `CASE_CLASSIFY_ON_INGEST` | `0` | Gemini kind classification after extraction (bounded, never fatal). Off until evaluated. |
| `CASE_WORKER_METRICS_PORT` | `0` | Prometheus listener on `127.0.0.1:PORT`; `0` = none. |
| `CASE_WORKER_STALE_SEC` | `180` | Health: a worker unseen for longer is not live. |

There is no in-process concurrency setting: one job per process, run more processes (systemd unit copy or `--scale case-worker=N`); the queue is claimed with `SKIP LOCKED`.

## Identity and status contract

Every route needs the service key (`X-API-Key`) **and** `Authorization: Bearer <jwt>`. The owner of a case is the token's `userId`. 403 = feature off or unconfigured on this server; 401 = token missing, expired or forged; 404 = case or document does not exist **or belongs to someone else**. There is no unsigned mode. For local calls mint a token:

```
python scripts/mint_dev_jwt.py --user-id 42 --ttl 3600
```

## Schema

Created in code by `core/case_store.py` under a transaction advisory lock, versioned in `case_schema_version`. Version 1 creates `cases`, `case_documents`, `case_jobs`, `case_audit`, `case_workers` and their indexes. Version 2 adds `case_documents.s3_etag` (plus a partial index) and `case_documents.stored`. The ETag is a **hint only**: `complete` reports a sibling with the same ETag and size as `duplicate_hint`, and the worker marks `duplicate` after it has hashed the bytes and found a `ready` sibling with the same SHA-256; extraction is never skipped on ETag + size alone (an ETag collision with different bytes is possible). `stored` is set when `complete` accepts the object and drives the caps, `doc_count` / `bytes_total` and download, so an ingest failure (object retained) and a complete-time rejection (object deleted) can both be `failed` without the accounting guessing from status. The migration backfills `stored` from the status of rows written by a version 1 server. Version 3 adds `purged_at` on `cases` and `case_documents` and `stopped_at` / `current_job_id` on `case_workers`. To migrate, append `(4, [statements])` to `MIGRATIONS`; never edit an applied version.

## Endpoints

All under `/pyapi/cases`, all rate-limited with the gateway's per-key limiter.

| Method | Path | Does | Audit |
|---|---|---|---|
| GET | `/meta` | UI suggestions and limits (caps, supported extensions) | |
| POST | `` | create case (201) | `case_create` |
| GET | `` | list own cases | |
| GET | `/{case_id}` | case detail | `case_view` (throttled) |
| PATCH | `/{case_id}` | intake fields, `status` active/archived | `case_update` (field names) |
| GET | `/{case_id}/status` | polling: `documents` counts by status, `pending`, `doc_count`, `bytes_total` | `case_view` (throttled) |
| DELETE | `/{case_id}` | soft delete case: tombstone, tombstone documents, cancel queued ingest jobs, schedule purge | `case_delete` |
| POST | `/{case_id}/documents/presign` | reserve a document row and return a presigned PUT (201) | `doc_presign` |
| POST | `/{case_id}/documents/{doc_id}/complete` | verify the object and queue it (idempotent); `duplicate_hint` names a stored sibling with the same ETag and size | `doc_complete` (`doc_id:outcome`) |
| GET | `/{case_id}/documents` | list, `?status=` filter, per-status `counts` | |
| GET | `/{case_id}/documents/{doc_id}` | detail | |
| GET | `/{case_id}/documents/{doc_id}/download` | short-lived presigned GET | `doc_download_url` |
| DELETE | `/{case_id}/documents/{doc_id}` | soft delete document, cancel its queued ingest job, schedule purge | `doc_delete` |

## Upload flow

1. **presign**: body `{filename, size_bytes, content_type?, client_sha256?}`. The filename is sanitised (path, control and reserved characters removed, Unicode kept) and the extension decides the type; the server picks the canonical `Content-Type` and signs it. Key: `cases/{case_id}/raw/{doc_id}.{ext}`. The row is `uploading` and already holds a slot against the caps. `duplicate_hint` names an existing document with the same client hash (a warning for the UI, never enforced).
2. **browser PUT** to `upload.url` with exactly `upload.headers`. Encryption comes from the bucket default.
3. **complete**: HEAD (exists, size equals the declared size and is within the cap, `ServerSideEncryption` equals `CASE_S3_SSE`), then a ranged GET of the first 8 KB sniffed against the extension. Outcome: `queued` (+ one `ingest` job, enforced unique per document) or `unsupported` (audio and video: stored, no job, re-queued when transcription ships). The answer carries `stored: true` and, when a stored sibling has the same ETag and size, `duplicate_hint`; that hint is advisory and the job is queued regardless. Calling complete again returns the row unchanged.

Rejections mark the row `failed` with `error_code`, delete the object (by version id) and answer:

| Code | HTTP | Where |
|---|---|---|
| `unsupported_type` | 422 | presign; `allowed` lists the extensions |
| `file_too_large` | 413 | presign |
| `case_doc_cap`, `case_bytes_cap` | 413 | presign |
| `case_not_active` | 409 | presign on an archived case |
| `object_missing` | 409 | complete before the PUT landed |
| `size_mismatch`, `sse_missing`, `mime_mismatch` | 422 | complete |
| `storage_error` | 503 | complete, S3 unreachable (row stays `uploading`, retry later) |
| `not_downloadable` | 409 | download of a row that is not `stored` (`uploading`, rejected at complete, `expired`) |

Supported extensions: `pdf docx xlsx csv txt md png jpg webp` (ingested; same set as the chat upload) and `mp3 wav m4a aac ogg opus flac amr mp4 mov mkv webm 3gp avi` (stored as `unsupported`). Images must match exactly (a PNG under `.jpg` is a mismatch); text extensions accept any clean UTF-8/UTF-16 text; executables, legacy `.doc`, zips that are not Word or Excel packages and unknown binaries are mismatches.

Document statuses: `uploading → queued | unsupported | failed`, then (slice 3) `queued → processing → ready | duplicate | failed`, housekeeping `uploading → expired` after 24 h, and `any → deleted` on soft delete. `duplicate` is only ever set by the worker, with `duplicate_of` pointing at the `ready` document whose SHA-256 matched.

## Tests

```
# fast suites (no database, no AWS)
EMBEDDING_SERVICE_URL=http://127.0.0.1:9 python -m pytest tests/test_case_jwt_auth.py tests/test_case_auth.py tests/test_case_routes.py tests/test_case_health.py tests/test_case_sniff.py tests/test_case_s3.py tests/test_case_documents.py tests/test_case_extract.py tests/test_case_worker.py -q

# Postgres-backed suites (throwaway schema per run)
docker run --rm -d --name case-pg -e POSTGRES_PASSWORD=t -p 5433:5432 postgres:16-alpine
TEST_POSTGRES_URL=postgresql://postgres:t@localhost:5433/postgres python -m pytest tests/test_case_store_pg.py tests/test_case_store_pg_docs.py tests/test_case_store_pg_jobs.py -q
```

Without `TEST_POSTGRES_URL` the Postgres suites skip locally; with `CI=true` they fail. CI: `.github/workflows/ci-case-workspace.yml` starts a Postgres service and runs all twelve suites (PyMuPDF, python-docx and openpyxl are installed for the extraction fixtures; Vision OCR is monkeypatched, so no credentials are needed). `tests/test_case_s3.py` checks the real boto3 parameters with `botocore.stub` (no network); `tests/case_support.FakeCaseS3` stands in for S3 everywhere else.

`GET /pyapi/health` carries a `case_workspace` entry: `{"status": "off"}` with the flag off; with it on, `ok` plus `schema_version`, `bucket`, `latency_ms` and `queue` (`workers_live`, `last_seen_age_s`, `queued`, `running`, `oldest_queued_s`), `warn` when jobs are queued and no worker has been seen within `CASE_WORKER_STALE_SEC` (overall health degrades), or `error` when the store is unreachable.

## Logging rule

Case code logs ids, counts, codes and durations only. Never a title, client name, filename, extracted text or anything a lawyer typed. Helpers that take a name for log lines receive the `doc_id`. `tests/test_case_documents.py::test_no_filename_or_content_appears_in_logs` runs a sentinel filename and sentinel content through presign, complete, list, download and delete with a handler on every case logger.

## Bucket (decision 4, final 30 Oct 2026)

Case files live in a **separate private bucket**, `lawttorney-cases` (`CASE_S3_BUCKET`, region `CASE_S3_REGION`, default `ap-south-1`). Not a `cases/` prefix in the public `lawttorney` bucket: Block Public Access and versioning are bucket-wide, and the judgment pipelines' credentials must not be able to write or delete client files. Keys are always `cases/{case_id}/...`; there is no prefix setting.

Bucket configuration (created by the admin):

- Block Public Access on (all four settings); versioning on; default encryption SSE-S3 (`AES256`).
- Lifecycle: abort incomplete multipart uploads after 1 day; expire noncurrent versions after 30 days as a backstop to purge.
- CORS for the web origins: `PUT`, `GET`, `HEAD`; allowed headers `Content-Type`, `x-amz-server-side-encryption`; expose `ETag`.
- IAM policy for the API and worker credentials, scoped to `arn:aws:s3:::lawttorney-cases/cases/*` (plus `s3:ListBucket` / `s3:ListBucketVersions` on the bucket with a `cases/` prefix condition): `s3:PutObject`, `s3:GetObject`, `s3:GetObjectVersion`, `s3:DeleteObject`, `s3:DeleteObjectVersion`. No other principal has access.

### Real-bucket smoke test (run once, before the slice 2 PR opens)

```
python scripts/case_bucket_smoke.py --base https://tool.lawttorney.com --api-key "$API_KEY" \
    --secret "$CASE_JWT_SECRET" --user-id 42 --origin https://tool.lawttorney.com \
    --bucket lawttorney-cases --region ap-south-1
```

It walks presign → CORS preflight from the web origin → PUT with the signed headers → complete (sniff and SSE verified) → download URL serves the same bytes with the filename in `Content-Disposition` → anonymous GET and HEAD of the raw key are 403 → a PNG under `.pdf` is rejected and its object removed → soft delete. Every step prints PASS or FAIL and the exit code is non-zero on any failure. The script has not been run yet: it waits for the bucket.

## Worker (slice 3)

```
python -m workers.case_worker            # loop; SIGTERM stops cleanly
python -m workers.case_worker --once     # claim and run at most one job, print the outcome (ops / smoke)
python -m workers.case_worker --types purge
```

Deployment: `deploy/systemd/lawttorney-case-worker.service` (enable when the flag goes on; `TimeoutStopSec=90` covers the shutdown grace) or the `case-worker` service in `deploy/docker-compose.prod.yml` under the `cases` profile. The worker refuses to start with `CASE_WORKSPACE_ENABLED` off.

**Job lifecycle.** `claim_job_sync` is the only `SKIP LOCKED` query: it takes the next `queued` job whose `run_after` has passed, or a `running` job whose `lock_expires_at` has passed (dead worker; the lost attempt is written to `history` with `worker_lost`). Every claim counts an attempt. A guard thread renews the lock every `ttl/3`; if a renewal fails the job belongs to someone else and the worker writes nothing more for it (the extraction child is killed). `finish_job_sync` is guarded by `locked_by`, so a late finish after a reclaim is ignored.

| Attempt ends with | Document | Job |
|---|---|---|
| success | `ready` (or `duplicate`) | `done` |
| permanent error (`encrypted_pdf`, `empty_pdf`, `corrupt_file`, `object_missing`, `size_mismatch`, `unsupported_type`) | `failed`, `error_code` set, object kept, still counted and downloadable | `failed` |
| transient error (`extract_error`, `ocr_error`, `timeout`, `storage_error`, `internal`), attempts < max | back to `queued` with the last `error_code` | `queued`, `run_after = now + backoff` |
| transient error at `max_attempts` | `failed` | `failed` |
| reclaimed more often than `max_attempts` | `failed`, `worker_lost` | `failed`, `worker_lost` |
| lock lost mid-job | untouched (the new owner continues) | untouched |
| SIGTERM after the grace period | back to `queued` | `queued` now, attempt not counted (`release`) |
| document deleted meanwhile | tombstone untouched | `cancelled` (`doc_deleted`) |

**Ingest.** Download by key and `s3_version_id` (streamed, sha256 computed on the way; size must equal the row) → **authoritative dedupe**: a `ready` sibling in the same case with the same sha256 makes this document `duplicate` (`duplicate_of`), with its own copy of the sibling's sidecar re-stamped with its `doc_id`, so a later purge of the original takes nothing away from it. Client hashes and ETags never decide this. Otherwise → extraction in a **spawned child process** per job (`core/case_extract.py`; `spawn`, not `fork`, so the child inherits neither the psycopg pool nor boto's sockets) through the three public wrappers in `core/file_processor.py` (`extract_pdf_pages`, `extract_image_page`, `extract_office_text`, additive, given the `doc_id` as their log name) → sidecar → optional kind classification → row `ready` with `page_count`, `chars_total`, `ocr_pages`, `extractor`, `text_key`, `meta_key`.

Why a child process: PyMuPDF, python-docx and a stuck Gemini call cannot be interrupted from a thread, and restarting the whole worker would abort the other jobs in flight. The parent polls the child, kills it (`Process.kill`) on `CASE_INGEST_TIMEOUT_SEC`, on a lost lock or on shutdown, and reads the result from `case_tmp/{job_id}/result.json`; a child that dies without a result is a transient `extract_error`.

PDF policy (same as the chat upload path): text layer per page plus raster coverage; pages whose layer is a scanner app's junk OCR and image-backed pages with no layer are re-read with Vision OCR one page at a time (cached per file hash and page in `chroma_store/.ocr_cache`); a garbled non-Latin layer or junk on most pages sends the whole file to OCR. OCR that returns nothing for the whole file (or for three or more pages) is `ocr_error`, transient, so an expired key retries instead of producing an empty sidecar.

**Sidecar** `cases/{case_id}/extracted/{doc_id}/text.jsonl`, one JSON object per line, `doc_id` on every line:

```json
{"doc_id":"…","unit":"page","page":3,"index":2,"heading":null,"text":"…","source":"layer","confidence":null,"chars":1830}
{"doc_id":"…","unit":"page","page":4,"index":3,"heading":null,"text":"…","source":"ocr","confidence":null,"chars":910}
{"doc_id":"…","unit":"section","page":null,"index":0,"heading":"1. Background","text":"…","source":"docx","confidence":null,"chars":640}
{"doc_id":"…","unit":"sheet","page":null,"index":0,"heading":"Ledger","text":"…","source":"xlsx","confidence":null,"chars":2210}
```

`page` is 1-based and authoritative for PDFs and images (empty pages are written with `text: ""`), `null` for DOCX / TXT / MD sections (blocks of at most 4000 characters at paragraph boundaries; `heading` is the first paragraph when it looks like one) and for XLSX / CSV sheets. `source` is `layer`, `ocr`, `docx`, `text`, `xlsx` or `csv`; `confidence` is reserved (the OCR helpers do not report one). A duplicate's lines also carry `duplicate_of`. `meta.json` next to it: `sidecar_schema`, ids, `ext`, `size_bytes`, `sha256`, `extractor` (`pdf_text` | `pdf_text+ocr_pages` | `pdf_ocr` | `image_ocr` | `docx` | `xlsx` | `csv` | `txt` | `md`), `page_count`, `chars_total`, `ocr_pages`, `lines`, `duplicate_of`, `created_at`.

**Housekeeping** (startup and every `CASE_HOUSEKEEPING_INTERVAL_SEC`): `uploading` rows older than `CASE_UPLOAD_EXPIRE_HOURS` → `expired` (never counted; a stray object is deleted; audit `doc_expire` by user `system`); `case_tmp/{uuid}` directories older than `CASE_TMP_SWEEP_AGE_SEC` removed (`core/file_gc.py` never touches `case_tmp`, which lives outside `uploads/`, `chroma_store/` and the system temp dir); worker rows unseen for a week pruned.

**Metrics.** `lawtech_case_jobs_total{type,outcome}` and `lawtech_case_ingest_seconds`, served by the worker itself on `127.0.0.1:CASE_WORKER_METRICS_PORT` (a separate process cannot share the API's multiproc dir).

Inspecting the queue:

```sql
SELECT type, status, COUNT(*) FROM case_jobs GROUP BY 1, 2;
SELECT job_id, doc_id, attempts, last_error_code, run_after, locked_by, lock_expires_at
  FROM case_jobs WHERE status IN ('queued','running') ORDER BY priority, run_after;
SELECT worker_id, last_seen_at, stopped_at, current_job_id, jobs_done, jobs_failed FROM case_workers;
```

## Deletion and purge

Soft delete (API) tombstones the row(s), cancels queued ingest jobs (never purge jobs) and inserts a `purge` job with `run_after = now + CASE_PURGE_GRACE_DAYS`. The purge job (worker) first checks the target is still `deleted` (`purge_target_sync`; a live row makes the job `cancelled` with `purge_refused`), then lists **every object version and delete marker** under the document's raw key and `extracted/{doc_id}/` folder, or under `cases/{case_id}/` for a case, and deletes them by `VersionId` in batches of 1000 (`delete_all_versions`, which refuses any prefix that is not one of those three shapes). Then it blanks `filename` on the tombstoned document row(s) and, for a case, `title`, `client_name`, `case_number`, `cnr` and `objective` on the case row, sets `purged_at`, and writes `doc_purge` / `case_purge` audit rows with the number of versions deleted. Categorical fields (`stage`, `forum`, `matter_type`, `our_side`) and hashes stay. Ids are never reused. A purge that cannot reach S3 is `storage_error` and retried with the normal backoff; the bucket lifecycle rule (noncurrent versions expire after 30 days) is the backstop if it keeps failing.

## Logging rule, worker side

The worker logs `job_id`, `doc_id`, `case_id`, sizes, page and line counts, codes and durations. Scratch files are `case_tmp/{job_id}/{doc_id}.{ext}`; the extraction helpers receive the `doc_id` as their log name; exceptions go through `short_err`. `tests/test_case_worker.py::test_no_filename_or_text_in_worker_logs` and `tests/test_case_extract.py::test_no_filename_or_text_in_extraction_logs` run a sentinel filename and sentinel content through ingest, failure and purge with a handler on every case logger and the file-processor logger.
