# Scaling Plan — 50 Concurrent Active Streams

> Goal: support **50 simultaneous in-flight chat/draft/scenario requests** on a single, vertically-scaled host, with headroom for short bursts and a clean cutover that can be rolled back in under five minutes.
>
> Author / owner: Adesh (`adesh.raj@lamipak.biz`).
> Status: draft, not yet started. Created 2026-06-09.
> Companion: [production_readiness_tracker.md](production_readiness_tracker.md) (all 17 prior audit items done — this plan builds on top).

---

## 1. Decisions already locked

| Decision | Value | Rationale |
|---|---|---|
| Concurrency target | 50 in-flight active queries (worst case, all streaming) | User pick — covers peak |
| Topology | **Vertical scale first** on a single bigger box | User pick — defer HA cost |
| LLM tier | OpenAI + Gemini quotas already verified sufficient | User pick — not a constraint |
| Existing prod | `64.176.97.182` (Ubuntu 22.04, 16 GB, single uvicorn worker) | DEPLOYMENT.md |

---

## 2. Success criteria (load-test gate)

Cutover is blocked until **all** of these pass on the new host under sustained 50-stream load for ≥ 30 min:

- Zero 5xx responses, zero unhandled exceptions in `agent.log`.
- p95 time-to-first-token (TTFT) for `/pyapi/chat` and `/pyapi/search/stream` < 5 s.
- p95 time-to-completion: Legislation/Judgment < 30 s, Drafting < 90 s, Scenario < 60 s.
- No Postgres pool exhaustion (`PoolTimeout` count = 0).
- No OpenSearch transport failures (`ESBackendUnavailable` count = 0, circuit never opens).
- Embedding service p95 latency for a 10-text batch < 1 s.
- Worker RSS stable ± 10 % over the 30 min window (no leak).
- ChromaDB writes (PDF upload) succeed concurrently without `database is locked` or `IndexError` from concurrent compaction.

---

## 3. Architecture target

```
                     ┌──────────────────────────────────────┐
                     │   New box (8 vCPU / 32 GB / NVMe)    │
                     │                                       │
   client → nginx ───┤  systemd: lawttorney-v2.service       │
                     │   ├─ gunicorn (6 uvicorn workers)     │
                     │   ├─ chromadb-server (port 8000)      │
                     │   ├─ embedding-service (port 5100, 2w)│
                     │   └─ postgres-16 (local, 200 max_conn)│
                     │                                       │
                     └──────┬────────────────────────────────┘
                            │
                  AWS OpenSearch (right-sized)
```

Per-worker in-flight cap: **10**. Six workers × 10 = **60 capacity, 20 % headroom over 50**.

---

## 4. Phase 0 — Research (do not skip; ~half day)

Each item produces a written answer that the next phase depends on.

### 0.1 OpenSearch capacity

Question: can the current AWS OpenSearch cluster absorb ~500 concurrent search ops (50 streams × ~10 ES calls/turn for drafting/judgment fan-out)?

```bash
# From the dashboard, pull at peak hour:
# - CPUUtilization (avg + max per node)
# - SearchLatency p99
# - SearchRate (ops/s)
# - ThreadpoolSearchQueue, ThreadpoolSearchRejected
# - JVMMemoryPressure
```

Decision rule:
- CPU max < 60 % and zero rejections → fine.
- CPU max 60–80 % or any rejections → scale instance class one tier (e.g. `t3.medium.search` → `m6g.large.search`) before cutover.
- CPU > 80 % → also add a second data node.

Document the chosen instance class in this section before moving on.

### 0.2 Postgres connection-pool ceiling

```bash
psql "$POSTGRES_URL" -c "SHOW max_connections;"
psql "$POSTGRES_URL" -c "SHOW shared_buffers;"
psql "$POSTGRES_URL" -c "SELECT count(*) FROM pg_stat_activity;"
```

Required: `max_connections ≥ 200`. Workers (6) × LangGraph PostgresSaver pool (~10) + chat_store pool (~10) + slack = ~140. We want 30 % headroom.

If lower, raise via `postgresql.conf` (full tuning lives in [deploy/postgresql.lawtech.conf](../deploy/postgresql.lawtech.conf)):
```conf
max_connections      = 200
shared_buffers       = 8GB     # 25 % of 32 GB — Postgres-canonical OLTP value
effective_cache_size = 24GB    # 75 % of RAM — planner hint, not allocation
work_mem             = 16MB
maintenance_work_mem = 2GB
random_page_cost     = 1.1     # NVMe-aware
effective_io_concurrency = 200
```
Restart Postgres. Verify with `SHOW shared_buffers;` etc.

### 0.3 ChromaDB multi-process safety audit

Files to audit (all use `Chroma(persist_directory=...)`, default `PersistentClient`):
- [tools/shared/vectordb_tools.py:70](../tools/shared/vectordb_tools.py#L70), [127](../tools/shared/vectordb_tools.py#L127)
- [tools/shared/document_tools.py:428](../tools/shared/document_tools.py#L428)
- [agents/document.py:80](../agents/document.py#L80)
- [core/file_processor.py:707](../core/file_processor.py#L707)

Confirm none of them open the store as a server (HTTP) client. Confirmed unsafe → Phase 5 is **mandatory**, not optional.

### 0.4 SlowAPI `X-Forwarded-For` plumbing

The rate-limit key at [core/gateway.py:103-111](../core/gateway.py#L103-L111) uses `request.headers.get("X-API-Key", "")` first, falling back to `request.client.host`. Behind nginx, `request.client.host` is `127.0.0.1` for *every* unauthenticated request — meaning unauthenticated bursts share one bucket.

Verify the API-key path is exercised by all real traffic. If any endpoint is reachable without `X-API-Key`, add Starlette's `ProxyHeadersMiddleware` so `request.client.host` reflects the real client IP from `X-Forwarded-For`.

### 0.5 Sync-blocking call audit

50 active SSE streams share an event loop per worker. A single sync blocking call freezes all of them. Grep for likely offenders:

```
sentence_transformers.encode  (locked under embedding service — confirm no direct fallback)
boto3 client calls outside asyncio.to_thread
requests.get / requests.post inside agents
sqlite3.connect (chat_store should be Postgres in prod; SQLite path must not be hit)
```

Action: list any direct sync calls that run inside request handlers (not background tasks). Wrap in `asyncio.to_thread` if found. Stop the bleed before raising concurrency.

### 0.6 LangGraph PostgresSaver concurrent-write smoke test

Run two terminals against the dev box, both sending requests with the **same `globalThreadId`**, ~5 s apart. Confirm no `IntegrityError` / no checkpoint corruption. If we see issues, plan a session-pin (sticky route by `thread_id` hash) — but only if reproducible.

---

## 5. Phase 1 — Provision the new host (~half day)

### 5.1 Spec & provision
- 8 vCPU, 32 GB RAM, ≥ 100 GB NVMe, Ubuntu 22.04 LTS. Pick same provider as current box.
- Open firewall: 22 (SSH), 80, 443.
- Internal-only ports (do **not** open externally): 5000/5001 (API), 5100 (embed), 8000 (chroma), 5432 (PG).

### 5.2 Base install
```bash
apt update && apt -y upgrade
apt -y install python3.12 python3.12-venv python3-pip nginx postgresql-16 \
                redis-server logrotate certbot python3-certbot-nginx git
```

### 5.3 Copy artifacts
- Mirror `/root/Lawtech-AI` from current box (`rsync -avz --exclude logs --exclude data --exclude venv`).
- Copy models to `/root/models/` (BGE-large + MiniLM, ~1.4 GB).
- Symlink so `_PROJECT_ROOT/models` resolves (matches [core/settings.py:101-103](../core/settings.py#L101-L103)).

### 5.4 Postgres bootstrap
```bash
sudo -u postgres createuser lawtech --pwprompt
sudo -u postgres createdb lawtech -O lawtech
# Apply max_connections=200 + shared_buffers from Phase 0.2.
systemctl restart postgresql
```

### 5.5 `.env.production` → `/root/Lawtech-AI/.env`
Copy from template, fill real values, then **also set**:
```
GUNICORN_WORKERS=6
EMBEDDING_SERVICE_URL=http://localhost:5100
EMBEDDING_SERVICE_WORKERS=2
CHROMA_SERVER_HOST=localhost
CHROMA_SERVER_PORT=8000
RATE_LIMIT_PER_MINUTE=120
MAX_INFLIGHT_PER_WORKER=10        # added in Phase 3
```

### 5.6 Smoke test (single worker, old config)
Before changing anything, run the current `./start.sh` with `WORKERS=1` and hit `/pyapi/health`, `/pyapi/health/detailed`, a real `/pyapi/chat` request with `X-API-Key`. Confirm parity with the live box. **This is the rollback target.**

---

## 6. Phase 2 — Gunicorn multi-worker (~half day)

The existing [gunicorn.conf.py](../gunicorn.conf.py) already has the non-blocking warm-up hook (BUG-02 fix). All we need is to drive it.

### 6.1 Add a systemd unit
```ini
# /etc/systemd/system/lawttorney-v2.service
[Unit]
Description=Lawttorney v2 API (gunicorn, multi-worker)
After=network.target postgresql.service lawttorney-embed.service lawttorney-chroma.service
Requires=lawttorney-embed.service lawttorney-chroma.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/Lawtech-AI
EnvironmentFile=/root/Lawtech-AI/.env
ExecStart=/root/Lawtech-AI/venv/bin/gunicorn -c gunicorn.conf.py core.gateway:app
Restart=on-failure
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

Plus matching units for `lawttorney-embed.service` (already exists in spirit via `start.sh --embed-only`) and `lawttorney-chroma.service` (created in Phase 5).

### 6.2 Worker-count tuning loop
Start at `GUNICORN_WORKERS=4`, run load test at 20 streams. If p95 latency holds, step up to 6 then 8. Pick the lowest value that meets the success criteria. Don't pre-size to 16; warm-up cost and RAM scale linearly.

### 6.3 Verify warm-up does its job
After `systemctl restart lawttorney-v2`, grep `journalctl -u lawttorney-v2` for `[warmup] worker pid=... ready` lines. Should see N lines (one per worker), each within ~20 s. If a warm-up step fails (search backend / Gemini), log shows `[warmup]   X skipped: ...` — investigate before traffic.

---

## 7. Phase 3 — Request-boundary concurrency cap (~half day, code)

The drafting fan-out spawns 10–15 parallel Gemini calls per request. Without a request-entry cap, a single user's drafting burst eats all worker slots and starves everyone else. This is the single biggest reliability win.

### 7.1 New middleware in [core/gateway.py](../core/gateway.py)

Add **after** `metrics_middleware` (so 503s are counted) and **before** any agent dispatch:

```python
import asyncio
from fastapi import Request

_MAX_INFLIGHT = int(os.getenv("MAX_INFLIGHT_PER_WORKER", "10"))
_inflight_sem = asyncio.Semaphore(_MAX_INFLIGHT)
_INFLIGHT_EXCLUDE = {"/pyapi/health", "/pyapi/health/detailed", "/pyapi/metrics", "/"}

@app.middleware("http")
async def inflight_gate(request: Request, call_next):
    if request.url.path in _INFLIGHT_EXCLUDE:
        return await call_next(request)
    # Non-blocking acquire — return 503 immediately if full.
    try:
        await asyncio.wait_for(_inflight_sem.acquire(), timeout=0.05)
    except asyncio.TimeoutError:
        return _error_response(
            503, "server_busy",
            "Server at capacity. Retry shortly.",
            getattr(request.state, "request_id", ""),
        )
    try:
        return await call_next(request)
    finally:
        _inflight_sem.release()
```

### 7.2 Metric
Add to [core/metrics.py](../core/metrics.py):
```python
"inflight_gate_rejections_total": Counter(
    "lawtech_inflight_gate_rejections_total",
    "Requests rejected because the per-worker in-flight cap was hit",
    ["endpoint"],
),
```
Increment in the `TimeoutError` branch above.

### 7.3 Test
- Unit: `tests/test_inflight_gate.py` — fire 12 concurrent slow-path requests at a worker with `MAX_INFLIGHT_PER_WORKER=3`; assert exactly 9 receive 503 with `Retry-After: 5`.
- E2E: confirm the existing drafting e2e (`DRAFTING_QUALITY_E2E=1 pytest tests/test_drafting_quality.py::TestPartitionSuitE2E`) still passes (semaphore released on exceptions).

### 7.4 Stream-aware behaviour
SSE streams hold their slot for the full response. That's intentional — capacity is measured in concurrent in-flight, not RPS. Confirm `try/finally` releases the slot on `asyncio.CancelledError` (client disconnect already handled by [SSE disconnect fix](production_readiness_tracker.md#round-4-—-code-quality-researched--applied)).

---

## 8. Phase 4 — Rate-limit tuning (~1 hour)

### 8.1 Per-API-key bump
- `.env.production`: `RATE_LIMIT_PER_MINUTE=120` (from 20). Justification: 50 users sustained = ~50 req/min total fan-in; a single legitimate user might do 20 turns/minute during heavy back-and-forth.
- Admin bucket already separated by `_rate_limit_key` ([core/gateway.py:107](../core/gateway.py#L107)). Leave at 200/min.

### 8.2 IP-fallback hardening (Phase 0.4 dependent)
If 0.4 found that unauthenticated traffic exists, add `ProxyHeadersMiddleware` at the top of [core/gateway.py](../core/gateway.py) and trust `127.0.0.1` only:
```python
from starlette.middleware.proxy_headers import ProxyHeadersMiddleware
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="127.0.0.1")
```

### 8.3 Tighten upload endpoint separately
File-upload paths (large payloads, multi-MB temp writes) should have a stricter bucket. Add `@limiter.limit("30/minute")` decorator on the upload entrypoint if not present.

---

## 9. Phase 5 — ChromaDB server mode (~1–2 days, code + infra)

Removes the multi-process unsafe-write blocker. Without this, multi-worker corrupts `chroma_store/` under PDF-upload concurrency.

### 9.1 Install & run server
```bash
/root/Lawtech-AI/venv/bin/pip install "chromadb[server]"
# systemd unit:
# /etc/systemd/system/lawttorney-chroma.service
# ExecStart=/root/Lawtech-AI/venv/bin/chroma run \
#   --path /root/Lawtech-AI/chroma_store \
#   --host 127.0.0.1 --port 8000
```

### 9.2 Code change — switch `Chroma()` calls to `HttpClient`

In all four call sites:
- [tools/shared/vectordb_tools.py:70, 127](../tools/shared/vectordb_tools.py)
- [tools/shared/document_tools.py:428](../tools/shared/document_tools.py)
- [agents/document.py:80](../agents/document.py)
- [core/file_processor.py:707](../core/file_processor.py) (if it instantiates Chroma; otherwise just `clear_system_cache`)

Replace:
```python
chromadb.api.client.SharedSystemClient.clear_system_cache()
vectordb = Chroma(collection_name=unique_string,
                  persist_directory=persist_dir,
                  embedding_function=embeddings)
```
With:
```python
import chromadb
_chroma_client = chromadb.HttpClient(
    host=os.getenv("CHROMA_SERVER_HOST", "localhost"),
    port=int(os.getenv("CHROMA_SERVER_PORT", "8000")),
)
vectordb = Chroma(client=_chroma_client,
                  collection_name=unique_string,
                  embedding_function=embeddings)
```

**Lift the client to module scope** as a singleton (one connection pool per worker). Remove every `SharedSystemClient.clear_system_cache()` call — it's an in-process cache fix that no longer applies.

### 9.3 Migration of existing collections
The on-disk format is the same; pointing the server at the existing `chroma_store/` should "just work". Verify by listing collections via `_chroma_client.list_collections()` after server start — count should match what existed before.

### 9.4 Backup before cutover
```bash
tar czf /root/chroma_store.before-server.$(date +%F).tgz /root/Lawtech-AI/chroma_store
```
Rollback = stop chroma server, revert imports, restore tarball.

### 9.5 Test
- Unit: `tests/test_chroma_server_mode.py` — store + retrieve a 3-doc collection via the server.
- E2E concurrent: 5 parallel PDF uploads to distinct `unique_string`s, then 5 parallel queries. Assert all succeed.
- Cleanup: confirm `delete_pdf_vectorstore` still works (it currently does `shutil.rmtree(persist_dir)` — under server mode, switch to `_chroma_client.delete_collection(name)`).

---

## 10. Phase 6 — Observability & alerts (~half day)

### 10.1 Confirm Prometheus scraping works
- `curl http://localhost:5001/pyapi/metrics -H "X-API-Key: $ADMIN_KEY"` returns Prometheus text.
- If Grafana already exists on the box, point it at this. Otherwise install `node_exporter` + a minimal Grafana for an MVP dashboard.

### 10.2 Add missing metrics

In [core/metrics.py](../core/metrics.py):
```python
"llm_calls_total": Counter(
    "lawtech_llm_calls_total",
    "LLM API calls by provider and outcome",
    ["provider", "model", "outcome"],   # outcome: success | timeout | error
),
"llm_call_latency_seconds": Histogram(
    "lawtech_llm_call_latency_seconds",
    "LLM call latency",
    ["provider", "model"],
    buckets=_LATENCY_BUCKETS,
),
"es_errors_total": Counter(
    "lawtech_es_errors_total",
    "ES/OpenSearch failures by class",
    ["error_class"],  # transport | parse | timeout | circuit_open
),
"embedding_service_latency_seconds": Histogram(
    "lawtech_embedding_service_latency_seconds",
    "Embedding service round-trip latency",
    ["model"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5),
),
```

Wire-up points:
- LLM counters: in [core/clients.py](../core/clients.py) wrappers, or in a single decorator applied to the `init_chat_model` instances.
- ES errors: at [tools/shared/elasticsearch_tools.py:218](../tools/shared/elasticsearch_tools.py#L218) etc. where `ESBackendUnavailable` is raised.
- Embedding service latency: in [core/embedding_client.py](../core/embedding_client.py)'s request wrapper.

### 10.3 Alerts
Minimum set (Grafana or Alertmanager):

| Alert | Condition | Action |
|---|---|---|
| InFlightSaturated | `lawtech_active_requests > 50` for 2 min | Page |
| GateRejecting | `rate(lawtech_inflight_gate_rejections_total[5m]) > 0` | Page |
| ESCircuitOpen | `rate(lawtech_es_errors_total{error_class="circuit_open"}[5m]) > 0` | Page |
| LLMTimeoutSpike | `rate(lawtech_llm_calls_total{outcome="timeout"}[5m]) > 0.1` | Warn |
| WorkerOOM | `process_resident_memory_bytes > 2.5e9` | Warn |
| P95LatencyHigh | `histogram_quantile(0.95, ...request_latency...) > 60` for `/pyapi/chat` | Warn |

---

## 11. Phase 7 — Load test (~1 day, write + run + analyse)

### 11.1 Test script — `tests/load_50_concurrent.py`

Approach: `locust` or `asyncio + httpx`. Recommend `locust` for the built-in p95/RPS dashboard.

Workload mix (matches the user-pick "50 active queries in flight"):

| Task | Weight | Endpoint | Payload |
|---|---|---|---|
| Legislation lookup | 35 % | `/pyapi/chat` (SSE) | "What does Sec 9 CPC say?" |
| Judgment search | 25 % | `/pyapi/chat` (SSE) | "Find Supreme Court cases on negligence under MV Act" |
| Drafting | 25 % | `/pyapi/chat` (SSE) | "Draft a partition suit for ancestral property in Bangalore" |
| Scenario | 15 % | `/pyapi/chat` (SSE) | "Tenant defaulting on rent for 4 months — eviction options" |

Each user holds an API key, opens an SSE stream, consumes tokens until done, sleeps 0–5 s, repeats. Sustained 50 concurrent users for **30 min**.

### 11.2 Pre-test setup
- Fresh DB: snapshot Postgres + chroma_store before run, restore after.
- Restart all services: ensures we're measuring steady-state, not warm-up.
- Tail `/pyapi/metrics` into a CSV (every 5 s) for post-mortem.

### 11.3 Pass / fail
Use the success criteria from §2. If any criterion fails, do **not** proceed to cutover. Pick the smallest fix that addresses the symptom, redeploy, re-run.

### 11.4 Common failures & first action

| Symptom | First check |
|---|---|
| TTFT spikes after ~15 min | Worker memory leak — restart hygiene (`max_requests=500` in gunicorn) |
| 503s from in-flight gate | Workers too few — bump `GUNICORN_WORKERS` 6→8 |
| OpenSearch timeouts | Phase 0.1 was wrong; scale OS instance |
| Postgres `PoolTimeout` | Phase 0.2 was wrong; raise `max_connections` or pool size |
| Chroma errors on PDF upload | Phase 5 not fully cut over — find lingering `persist_directory` call |
| `CancelledError` floods logs | Client disconnect handler regression — check SSE generators |

---

## 12. Phase 8 — Cutover (~half day)

### 12.1 Add new upstream to nginx without removing old
```nginx
# /etc/nginx/sites-available/default on the FRONTING server
location /pyapiv3/ {
    rewrite ^/pyapiv3/(.*) /pyapi/$1 break;
    proxy_pass http://<NEW_BOX_IP>:5001;
    # ...same proxy_set_headers + timeouts as /pyapiv2/
}
```
Test from a controlled client. Frontend continues to use `/pyapiv2/`.

### 12.2 Frontend smoke + manual QA
- Run the manual test plan: chat, drafting, scenario, PDF upload, integration token, multilingual.
- Confirm SSE flows end-to-end with first-byte under 5 s.

### 12.3 Cutover
Update nginx to point `/pyapiv2/` at `<NEW_BOX_IP>:5001`. `nginx -t && systemctl reload nginx`. Watch `/pyapi/metrics` on the new box and `journalctl -u lawttorney-v2 -f`.

### 12.4 Keep old box warm for 24 h
Do not stop the old box. If anything regresses, point nginx back at it (single line change, < 1 min recovery).

---

## 13. Rollback playbook

| If we see... | Within 24 h | Action |
|---|---|---|
| 5xx burst, root cause unknown | Yes | Flip nginx upstream back to old box. Investigate from logs. |
| Chroma corruption | Yes | Restore tarball from Phase 5.4. Revert Chroma imports. |
| Postgres pool exhaustion | Yes | Lower `MAX_INFLIGHT_PER_WORKER` to 6. Tune `max_connections`. |
| OpenSearch saturating | Yes | Scale OS instance class up one tier. Restart workers. |
| Worker memory leak | Yes | Add `max_requests=500 max_requests_jitter=50` to gunicorn.conf.py. Restart. |
| LLM throttling | Yes | Verify quotas (was supposed to be safe). Add provider-level retry+backoff in `core/clients.py`. |

After 24 h with no incidents, stop services on the old box and decommission.

---

## 14. Open questions to resolve before kicking off

1. **Host provider** — same as today (Vultr) or a different one? Affects Phase 1.1.
2. **Postgres co-location** — keep on the app box, or stand up a managed PG (RDS/Aiven)? Co-location is simpler and cheaper; managed is safer if data grows.
3. **Backups** — Postgres + chroma_store + uploads. Daily snapshot to S3? Confirm before cutover.
4. **Acceptance signoff** — who runs the load test, who reviews the dashboards, who signs off the cutover? (Default: you + me on each phase, signoff before §12.3.)
5. **Maintenance window** — cutover during low-traffic hour. Pick a slot (e.g. 02:00 IST) and announce.

---

## 15. Effort & timeline estimate

| Phase | Effort | Can parallelise? |
|---|---|---|
| 0 — Research | 0.5 day | — |
| 1 — Provision | 0.5 day | with 0 — **runbook ready: [phase1_provision_runbook.md](phase1_provision_runbook.md)** |
| 2 — Gunicorn | 0.5 day | — |
| 3 — In-flight gate (code) | 0.5 day | with 1 |
| 4 — Rate-limit | 1 hour | with 3 |
| 5 — Chroma server (code) | 1–2 days | with 1, 3, 4 |
| 6 — Observability | 0.5 day | with 2, 3 |
| 7 — Load test | 1 day | — |
| 8 — Cutover | 0.5 day | — |

**Sequential lower bound: ~5 working days. With parallelisation: ~3–4 working days.** Add 1 day of buffer for load-test iteration.

---

## 16. What this plan does NOT cover

- **Horizontal scale / HA.** Single-box failure still takes the service down. Revisit after we hit ~80 % capacity sustained or before any HA commitment.
- **CDN / edge caching.** SSE doesn't benefit; static assets already small.
- **Background jobs.** PDF processor and embedding service are co-located on the same box. If PDF-heavy traffic appears, plan a separate worker host.
- **Cost optimisation.** Plan optimises for reliability under load, not $$. Cost review is a separate exercise.
