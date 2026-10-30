# Case workspace — foundation runbook

**Status:** slice 1 (cases, auth, schema) in review as PR #144 · slice 2 (uploads, documents, soft delete) built against the S3 fake on `feat/case-workspace-s2`, waiting for the bucket · **Design:** `Case_Workspace_Foundation_Design.md` (approved 30 Oct 2026 with amendments) · **Plan:** `Case_Study_Plan.md`

The case workspace is a feature-flagged router inside the existing API plus a separate worker process (slice 3). Cases, documents, jobs and audit live in the API's PostgreSQL next to the chat tables. Files live in the private `lawttorney-cases` bucket under `cases/{case_id}/`.

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

Slice 3 adds the worker settings (`CASE_WORKER_CONCURRENCY`, `CASE_JOB_LOCK_TTL_SEC`, `CASE_INGEST_TIMEOUT_SEC`, `CASE_TMP_ROOT`, `CASE_CLASSIFY_ON_INGEST`).

## Identity and status contract

Every route needs the service key (`X-API-Key`) **and** `Authorization: Bearer <jwt>`. The owner of a case is the token's `userId`. 403 = feature off or unconfigured on this server; 401 = token missing, expired or forged; 404 = case or document does not exist **or belongs to someone else**. There is no unsigned mode. For local calls mint a token:

```
python scripts/mint_dev_jwt.py --user-id 42 --ttl 3600
```

## Schema

Created in code by `core/case_store.py` under a transaction advisory lock, versioned in `case_schema_version`. Version 1 creates `cases`, `case_documents`, `case_jobs`, `case_audit`, `case_workers` and their indexes. Version 2 adds `case_documents.s3_etag` (plus a partial index): S3 computes the ETag, so ETag + size is a trustworthy same-content signal that lets `complete` mark a duplicate immediately, before the worker has a SHA-256. To migrate, append `(3, [statements])` to `MIGRATIONS`; never edit an applied version.

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
| POST | `/{case_id}/documents/{doc_id}/complete` | verify the object and queue it (idempotent) | `doc_complete` (`doc_id:outcome`) |
| GET | `/{case_id}/documents` | list, `?status=` filter, per-status `counts` | |
| GET | `/{case_id}/documents/{doc_id}` | detail | |
| GET | `/{case_id}/documents/{doc_id}/download` | short-lived presigned GET | `doc_download_url` |
| DELETE | `/{case_id}/documents/{doc_id}` | soft delete document, cancel its queued ingest job, schedule purge | `doc_delete` |

## Upload flow

1. **presign**: body `{filename, size_bytes, content_type?, client_sha256?}`. The filename is sanitised (path, control and reserved characters removed, Unicode kept) and the extension decides the type; the server picks the canonical `Content-Type` and signs it. Key: `cases/{case_id}/raw/{doc_id}.{ext}`. The row is `uploading` and already holds a slot against the caps. `duplicate_hint` names an existing document with the same client hash (a warning for the UI, never enforced).
2. **browser PUT** to `upload.url` with exactly `upload.headers`. Encryption comes from the bucket default.
3. **complete**: HEAD (exists, size equals the declared size and is within the cap, `ServerSideEncryption` equals `CASE_S3_SSE`), then a ranged GET of the first 8 KB sniffed against the extension. Outcome: `queued` (+ one `ingest` job, enforced unique per document), `unsupported` (audio and video: stored, no job, re-queued when transcription ships) or `duplicate` (same ETag and size as a stored sibling; `duplicate_of` set, no job). Calling complete again returns the row unchanged.

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
| `not_downloadable` | 409 | download of a row without an object (`uploading`, `failed`, `expired`) |

Supported extensions: `pdf docx xlsx csv txt md png jpg webp` (ingested; same set as the chat upload) and `mp3 wav m4a aac ogg opus flac amr mp4 mov mkv webm 3gp avi` (stored as `unsupported`). Images must match exactly (a PNG under `.jpg` is a mismatch); text extensions accept any clean UTF-8/UTF-16 text; executables, legacy `.doc`, zips that are not Word or Excel packages and unknown binaries are mismatches.

Document statuses: `uploading → queued | unsupported | duplicate | failed`, then (slice 3) `queued → processing → ready | failed`, housekeeping `uploading → expired` after 24 h, and `any → deleted` on soft delete.

## Tests

```
# fast suites (no database, no AWS)
EMBEDDING_SERVICE_URL=http://127.0.0.1:9 python -m pytest tests/test_case_jwt_auth.py tests/test_case_auth.py tests/test_case_routes.py tests/test_case_health.py tests/test_case_sniff.py tests/test_case_s3.py tests/test_case_documents.py -q

# Postgres-backed suites (throwaway schema per run)
docker run --rm -d --name case-pg -e POSTGRES_PASSWORD=t -p 5433:5432 postgres:16-alpine
TEST_POSTGRES_URL=postgresql://postgres:t@localhost:5433/postgres python -m pytest tests/test_case_store_pg.py tests/test_case_store_pg_docs.py -q
```

Without `TEST_POSTGRES_URL` the Postgres suites skip locally; with `CI=true` they fail. CI: `.github/workflows/ci-case-workspace.yml` starts a Postgres service and runs all nine suites. `tests/test_case_s3.py` checks the real boto3 parameters with `botocore.stub` (no network); `tests/case_support.FakeCaseS3` stands in for S3 everywhere else.

`GET /pyapi/health` carries a `case_workspace` entry: `{"status": "off"}` with the flag off; with it on, `ok` plus `schema_version`, `bucket` and `latency_ms`, or `error` (overall health degrades) when the store is unreachable. Slice 3 adds the worker heartbeat age here.

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

## Deletion and purge (slice 2 and 3, from the approved design)

Soft delete tombstones the row(s), cancels queued ingest jobs (never purge jobs) and inserts a `purge` job with `run_after = now + CASE_PURGE_GRACE_DAYS`. Objects stay in S3 until the purge job (slice 3) lists every object version under the case or document prefix and deletes each by `VersionId`, then scrubs `filename`, `title`, `client_name`, `case_number`, `cnr` and `objective` on the tombstoned rows. Ids are never reused.

## Worker scratch and timeouts (slice 3, from the approved design)

Extraction runs in a **child process** (`multiprocessing`, spawn) per job; the parent kills it on `CASE_INGEST_TIMEOUT_SEC` and records `timeout`. A thread cannot be killed, and restarting the whole worker would abort the other jobs in flight. Scratch lives in `case_tmp/{job_id}/` under the project root, removed in a `finally` after every job; on startup any `case_tmp` directory older than one hour is removed. `core/file_gc.py` never touches this path.
