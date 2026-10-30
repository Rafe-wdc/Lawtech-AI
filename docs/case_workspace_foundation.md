# Case workspace — foundation runbook

**Status:** slice 1 (cases, auth, schema) in review · **Design:** `Case_Workspace_Foundation_Design.md` (approved 30 Oct 2026 with amendments) · **Plan:** `Case_Study_Plan.md`

The case workspace is a feature-flagged router inside the existing API plus a separate worker process (slice 3). Cases, documents, jobs and audit live in the API's PostgreSQL next to the chat tables. Files live in S3 under a per-case prefix (slice 2).

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `CASE_WORKSPACE_ENABLED` | `0` | Mount `/pyapi/cases`. Off means every case path is 404. |
| `CASE_JWT_SECRET` | falls back to `USER_MEMORY_JWT_SECRET` | HS256 secret of Node's access token. Flag on with no secret → every case route is 403 (fail closed). |
| `CASE_JWT_CLAIM` | `userId` | Claim that carries the owner id; comma-separated list accepted. |
| `CASE_JWT_LEEWAY_S` | `30` | Clock skew tolerated on `exp` / `nbf`. |
| `CASE_AUDIT_VIEW_WINDOW_SEC` | `900` | `case_view` is written at most once per user per case per window. |
| `POSTGRES_URL` | required | The case store is Postgres-only. |

| `CASE_S3_BUCKET` | `lawttorney-cases` | Separate private bucket for case files (see Bucket below). |
| `CASE_S3_REGION` | `S3_REGION` (`ap-south-1`) | Region of that bucket. |

Slice 2 adds `CASE_MAX_FILE_MB`, `CASE_MAX_DOCS_PER_CASE`, `CASE_MAX_BYTES_PER_CASE_GB`, `CASE_PRESIGN_TTL_SEC`, `CASE_DOWNLOAD_TTL_SEC`. Slice 3 adds the worker settings.

## Identity and status contract

Every route needs the service key (`X-API-Key`) **and** `Authorization: Bearer <jwt>`. The owner of a case is the token's `userId`. 403 = feature off or unconfigured on this server; 401 = token missing, expired or forged; 404 = case does not exist **or belongs to someone else**. There is no unsigned mode. For local calls mint a token:

```
python scripts/mint_dev_jwt.py --user-id 42 --ttl 3600
```

## Schema

Created in code by `core/case_store.py` under a transaction advisory lock, versioned in `case_schema_version`. Version 1 creates `cases`, `case_documents`, `case_jobs`, `case_audit`, `case_workers` and their indexes. To migrate, append `(2, [statements])` to `MIGRATIONS`; never edit version 1.

## Tests

```
# fast suites (no database)
EMBEDDING_SERVICE_URL=http://127.0.0.1:9 python -m pytest tests/test_case_jwt_auth.py tests/test_case_auth.py tests/test_case_routes.py -q

# Postgres-backed suite (throwaway schema per run)
docker run --rm -d --name case-pg -e POSTGRES_PASSWORD=t -p 5433:5432 postgres:16-alpine
TEST_POSTGRES_URL=postgresql://postgres:t@localhost:5433/postgres python -m pytest tests/test_case_store_pg.py -q
```

Without `TEST_POSTGRES_URL` the Postgres suite skips locally; with `CI=true` it fails. CI: `.github/workflows/ci-case-workspace.yml` starts a Postgres service and runs all four suites.

## Logging rule

Case code logs ids, counts, codes and durations only. Never a title, client name, filename, extracted text or anything a lawyer typed. Helpers that take a name for log lines receive the `doc_id`.

## Bucket (decision 4, final 30 Oct 2026)

Case files live in a **separate private bucket**, `lawttorney-cases` (`CASE_S3_BUCKET`, region `CASE_S3_REGION`, default `ap-south-1`). Not a `cases/` prefix in the public `lawttorney` bucket: Block Public Access and versioning are bucket-wide, and the judgment pipelines' credentials must not be able to write or delete client files. Keys are always `cases/{case_id}/...`; there is no prefix setting.

Bucket configuration (created by the admin):

- Block Public Access on (all four settings); versioning on; default encryption SSE-S3 (`AES256`).
- Lifecycle: abort incomplete multipart uploads after 1 day; expire noncurrent versions after 30 days as a backstop to purge.
- CORS for the web origins: `PUT`, `GET`, `HEAD`; allowed headers `Content-Type`, `x-amz-server-side-encryption`; expose `ETag`.
- IAM policy for the API and worker credentials, scoped to `arn:aws:s3:::lawttorney-cases/cases/*` (plus `s3:ListBucket` / `s3:ListBucketVersions` on the bucket with a `cases/` prefix condition): `s3:PutObject`, `s3:GetObject`, `s3:GetObjectVersion`, `s3:DeleteObject`, `s3:DeleteObjectVersion`. No other principal has access.

Slice 2 starts once the bucket exists; `complete` verifies size and `ServerSideEncryption: AES256` on every object.

## Deletion and purge (slice 2 and 3, from the approved design)

Soft delete tombstones the row and cancels queued jobs. Purge runs as a job after `CASE_PURGE_GRACE_DAYS`: it lists every object version under the case or document prefix and deletes each by `VersionId`, then scrubs `filename`, `title`, `client_name`, `case_number`, `cnr` and `objective` on the tombstoned rows. Ids are never reused.

## Worker scratch and timeouts (slice 3, from the approved design)

Extraction runs in a **child process** (`multiprocessing`, spawn) per job; the parent kills it on `CASE_INGEST_TIMEOUT_SEC` and records `timeout`. A thread cannot be killed, and restarting the whole worker would abort the other jobs in flight. Scratch lives in `case_tmp/{job_id}/` under the project root, removed in a `finally` after every job; on startup any `case_tmp` directory older than one hour is removed. `core/file_gc.py` never touches this path.
