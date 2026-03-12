# Lawtech-AI — Production Readiness Report

**Date**: 2026-03-12
**Current Rating**: 6.5 / 10
**Target Rating**: 9 / 10

---

## Overall Scorecard

| Layer | Score | Status |
|---|---|---|
| Agent Architecture | 9/10 | Solid |
| Legal Domain Coverage | 9/10 | Solid |
| Resilience & Fallback | 8/10 | Solid |
| Observability (L1–L4) | 8/10 | Solid |
| Streaming & UX | 8/10 | Solid |
| Code Quality & Logging | 7/10 | Good |
| **Security / Auth** | **2/10** | **Critical Gap** |
| **Deployment / Scaling** | **3/10** | **Critical Gap** |
| **Data Durability** | **4/10** | **Significant Gap** |
| **Routing Accuracy** | **6/10** | **Needs Work** |

---

## P0 — Critical (Fix Before Any Real Users)

### 1. No Authentication on Any Endpoint

**Risk**: CRITICAL — data exposure, unlimited API abuse, cost explosion
**Current state**: Every endpoint is fully public. No API keys, no tokens, no auth.

Exposed without any protection:
- `POST /pyapi/search` — anyone can run unlimited LLM queries at your cost
- `POST /pyapi/search/stream` — same
- `GET /pyapi/admin/fallback_logs` — exposes raw user queries
- `GET /pyapi/admin/usage_stats` — exposes cost data, usage patterns
- `GET /pyapi/admin/quality_stats` — exposes quality scoring data
- `GET /pyapi/admin/fallback_stats` — exposes system internals

**Fix**: API key middleware (2–3 hours)
```python
# core/auth.py
API_KEYS = set(os.getenv("API_KEYS", "").split(","))

async def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key not in API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid API key")
```
- Public endpoints: `/`, `/pyapi/health`
- Protected with user API key: `/pyapi/search`, `/pyapi/search/stream`, `/pyapi/chat`
- Protected with admin API key: `/pyapi/admin/*`, `/pyapi/metrics`

---

### 2. No HTTPS / TLS

**Risk**: CRITICAL — all data (legal queries, responses) transmitted in plaintext
**Current state**: Plain HTTP on port 5000. No SSL termination anywhere.

**Fix**: Add nginx reverse proxy with Let's Encrypt (1–2 hours)
```nginx
server {
    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/domain/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/domain/privkey.pem;
    location / { proxy_pass http://127.0.0.1:5000; }
}
```
Or use Caddy (automatic HTTPS, zero config):
```
your-domain.com {
    reverse_proxy localhost:5000
}
```

---

### 3. Single Process — No Redundancy

**Risk**: HIGH — one uvicorn crash = full outage, zero redundancy
**Current state**: Single `uvicorn core.gateway:app` process. No process manager.

**Fix**: Use gunicorn with multiple workers + systemd (2 hours)
```bash
gunicorn core.gateway:app \
  --workers 2 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:5000 \
  --timeout 300 \
  --graceful-timeout 30 \
  --access-logfile logs/access.log
```

Note: keep workers=2 (not more) because embedding models load into memory per worker.

Also add systemd service for auto-restart on crash:
```ini
[Service]
ExecStart=/usr/bin/gunicorn core.gateway:app ...
Restart=always
RestartSec=5
```

---

### 4. In-Memory LangGraph Checkpointer (State Lost on Restart)

**Risk**: HIGH — all multi-turn conversation history lost on every server restart
**Current state**: `MemorySaver()` stores state in-process RAM.
**Secondary risk**: Memory grows unbounded — 1000 active threads = potential OOM.

**Fix**: Switch to Redis checkpointer (3 hours)
```python
from langgraph.checkpoint.redis import RedisSaver
checkpointer = RedisSaver.from_conn_string(REDIS_URL)
compiled = graph.compile(checkpointer=checkpointer)
```
Redis already in `requirements.txt` and `settings.py`. Just needs wiring.

---

## P1 — Significant (Fix Within 2 Weeks)

### 5. Admin Endpoints Need Separate Auth Layer

**Risk**: HIGH — even with API keys, admin routes should require a separate admin key
**Current state**: No separation between user-facing and admin API access.

**Fix**: Two-tier API key system
```python
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
USER_API_KEYS = set(os.getenv("API_KEYS", "").split(","))

# /pyapi/admin/* → require ADMIN_API_KEY
# /pyapi/search* → require any key in USER_API_KEYS
```

---

### 6. SCI_Judgment Agent Misrouting

**Risk**: MEDIUM — Supreme Court queries routing to Non_legal instead of SCI_Judgment
**Observed**: "Supreme Court judgment on right to privacy" → `Non_legal` (wrong)

**Root cause**: Orchestrator query normalization rewrites SC queries before classification.
The Non_legal greeting pre-check may also be too aggressive.

**Fix**: Update orchestrator classification prompts + add SCI keywords to regex fallback
```python
# In orchestrator.py regex fallback
_SCI_KEYWORDS = {"supreme court", "sc judgment", "hon'ble sc", "article 136"}
if any(k in query_lower for k in _SCI_KEYWORDS):
    return "SCI_Judgment"
```

---

### 7. No Circuit Breaker on Elasticsearch

**Risk**: MEDIUM — ES unavailable = all search requests hang until 300s timeout
**Current state**: ES failure causes silent hang, wastes LLM budget on fallback.

**Fix**: Fast-fail circuit breaker pattern (2 hours)
```python
# Track consecutive ES failures
_es_failure_count = 0
_es_open_until = 0.0  # epoch time

def is_es_available() -> bool:
    if time.time() < _es_open_until:
        return False  # circuit open
    return True

def record_es_failure():
    global _es_failure_count, _es_open_until
    _es_failure_count += 1
    if _es_failure_count >= 3:
        _es_open_until = time.time() + 60  # open for 60s
```

---

### 8. Judgment Agent Latency (120s+ on Complex Queries)

**Risk**: MEDIUM — client timeouts, poor UX for bail/criminal queries
**Observed**: "Bail conditions for NDPS Act" exceeded 120s end-to-end
**Root cause**: GPT-4o metadata extraction + ES search + S3 PDF fetch + LLM generation = serial chain

**Fix**: Parallelize metadata extraction with ES search (3 hours)
```python
# Run metadata extraction and ES search concurrently
metadata_task = asyncio.create_task(extract_case_metadata(query))
es_task = asyncio.create_task(search_es(query))
metadata, es_results = await asyncio.gather(metadata_task, es_task)
```

---

### 9. No SQLite Backup

**Risk**: MEDIUM — SQLite DB contains: chat history, fallback logs, request logs, quality scores
**Current state**: Single file at `data/chat_history.db`. No backup. One `rm` = total data loss.

**Fix**: Daily backup script (30 minutes)
```bash
#!/bin/bash
# scripts/backup_db.sh
DATE=$(date +%Y%m%d)
sqlite3 data/chat_history.db ".backup data/backups/chat_history_$DATE.db"
# Keep last 7 days
find data/backups/ -name "*.db" -mtime +7 -delete
```
Add to cron: `0 2 * * * /path/to/backup_db.sh`

---

### 10. Rate Limiting Only by IP (No Per-User Limiting)

**Risk**: MEDIUM — IP-based rate limit bypassed by rotating IPs
**Current state**: `slowapi` limits by `get_remote_address` — 10 req/min per IP

**Fix**: Rate limit by API key once auth is added
```python
def get_api_key_for_rate_limit(request: Request):
    return request.headers.get("X-API-Key", get_remote_address(request))

limiter = Limiter(key_func=get_api_key_for_rate_limit)
```

---

## P2 — Improvements (Fix Within 1 Month)

### 11. No CI/CD Pipeline

**Risk**: LOW-MEDIUM — manual deployment = high risk of breaking production
**Current state**: Manual `git pull` + restart.

**Fix**: GitHub Actions workflow
```yaml
# .github/workflows/deploy.yml
on: [push to main]
jobs:
  test:
    - run: python tests/test_agents.py
  deploy:
    - run: ssh server "cd /app && git pull && systemctl restart lawtech"
```

---

### 12. No Request Queuing for Heavy Operations

**Risk**: LOW-MEDIUM — 10 simultaneous Scenario queries = 10 × Gemini Pro calls = slow + expensive
**Current state**: All requests processed synchronously, no queue.

**Fix**: Add Celery + Redis for async heavy jobs (Drafting, Scenario) — 1 day
Or simply enforce a concurrency limit per endpoint using asyncio.Semaphore:
```python
_HEAVY_SEMAPHORE = asyncio.Semaphore(3)  # max 3 concurrent Scenario/Drafting requests

async def scenario_node(state):
    async with _HEAVY_SEMAPHORE:
        ...
```

---

### 13. No Request ID in API Responses

**Risk**: LOW — hard to correlate client errors with server logs
**Current state**: Request IDs logged server-side but not returned to clients.

**Fix**: Add `X-Request-ID` response header (30 minutes)
```python
@app.middleware("http")
async def add_request_id_header(request, call_next):
    response = await call_next(request)
    response.headers["X-Request-ID"] = get_current_request_id()
    return response
```

---

### 14. SQLite Not Suitable for 50+ Concurrent Users

**Risk**: LOW (now) — SQLite WAL mode handles ~20 concurrent writers max
**Timeline**: Becomes a bottleneck at 50+ daily active users

**Fix (future)**: Migrate to PostgreSQL
```python
# core/chat_store.py → use asyncpg instead of sqlite3
# All schema SQL is already standard — minimal changes needed
```

---

### 15. Embedding Models Load Per Worker

**Risk**: LOW — with 2+ gunicorn workers, BGE-large loads twice = 2× RAM
**Current state**: Models load in-process at first request

**Fix**: Use the existing `EMBEDDING_SERVICE_URL` remote embeddings service
```bash
# Start embedding service separately (already built in services/)
python services/embedding_service.py --port 5100
# Set in .env:
EMBEDDING_SERVICE_URL=http://localhost:5100
```

---

### 16. No Structured Error Responses

**Risk**: LOW — inconsistent error format confuses API consumers
**Current state**: Mix of FastAPI default `{"detail": "..."}` and custom formats

**Fix**: Global exception handler with consistent schema
```python
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": str(exc), "request_id": "..."}
    )
```

---

### 17. Memory Growth from MemorySaver (Until Redis Added)

**Risk**: LOW-MEDIUM — long-running server with 500+ threads = RAM exhaustion
**Mitigation (immediate)**: Add periodic cleanup of old threads from MemorySaver
**Real fix**: P0 item #4 (Redis checkpointer)

---

## What's Already Production-Grade

| Feature | Notes |
|---|---|
| Multi-agent LangGraph pipeline | Clean architecture, proper fan-out |
| 3-tier fallback (ES → rewrite → web) | Resilient, graceful degradation |
| Input/output guardrails | PII detection, injection prevention, hallucination flagging |
| Rate limiting | slowapi, configurable per-minute |
| Structured logging | structlog, request IDs, log_time() profiling |
| L1–L4 observability stack | Health, Prometheus metrics, usage tracking, quality scoring |
| Prometheus + Grafana | 12-panel dashboard, auto-provisioned |
| ES backfill gap report | Weekly intelligence for data team |
| Per-section legal drafting | Streaming, continuation support |
| Multilingual support | 14 Indian languages |
| SSE streaming | Token-level, status events, draft progress |
| PDF processing | PyMuPDF + Gemini Vision OCR fallback |
| Chat history | SQLite, rolling summaries, multi-turn context |
| S3 PDF links | Judgment PDFs served from lawttorney bucket |

---

## Fix Priority Roadmap

### Week 1 (Immediate — Before Any Public Access)
- [ ] P0-1: API key authentication middleware
- [ ] P0-2: HTTPS via Caddy or nginx
- [ ] P0-3: Gunicorn multi-worker + systemd
- [ ] P0-4: Redis checkpointer (replace MemorySaver)

### Week 2
- [ ] P1-5: Admin API key separation
- [ ] P1-6: Fix SCI_Judgment routing
- [ ] P1-7: ES circuit breaker
- [ ] P1-9: SQLite daily backup script

### Week 3–4
- [ ] P1-8: Parallelize Judgment metadata + ES search
- [ ] P2-11: GitHub Actions CI/CD
- [ ] P2-12: Concurrency semaphore for heavy agents
- [ ] P2-13: X-Request-ID response header

### Month 2+
- [ ] P2-14: PostgreSQL migration (when DAU > 50)
- [ ] P2-15: Remote embedding service in prod
- [ ] P2-16: Consistent error response schema

---

## Estimated Rating After Each Phase

| After | Rating | What changes |
|---|---|---|
| Current | 6.5/10 | Baseline |
| Week 1 (P0 done) | 8.0/10 | Auth + HTTPS + process stability |
| Week 2 (P1 done) | 8.5/10 | Routing accuracy + resilience |
| Month 1 (P2 done) | 9.0/10 | CI/CD + performance + consistency |
| Month 2+ | 9.5/10 | PostgreSQL + scaling |
