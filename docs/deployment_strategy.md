# Lawtech-AI: Scalable Deployment Strategy

## System Profile Summary

| Dimension | Current State | Target |
|-----------|--------------|--------|
| Concurrent users | ~10-20 | 500+ |
| P95 latency | 25-40s (multi-agent) | <15s |
| Availability | Single server, manual deploy | 99.9% uptime |
| Infra | Bare-metal VPS + gunicorn 16 workers | Container orchestration, auto-scaling |
| Monitoring | Prometheus + Grafana (basic) | Full observability + alerting + tracing |
| CI/CD | GitHub Actions → SSH deploy | Blue-green with health gates |

---

## Architecture: Current vs Target

### Current (Single-Server)

```
┌─────────────────────────────────────────────────┐
│  VPS (Single Server)                            │
│                                                 │
│  Caddy (HTTPS) → Gunicorn (16 workers)          │
│       ↕              ↕                          │
│  Let's Encrypt    FastAPI + LangGraph           │
│                      ↕                          │
│              ┌───────┴────────┐                  │
│              │ Local SQLite   │                  │
│              │ Local ChromaDB │                  │
│              │ Local Models   │                  │
│              └────────────────┘                  │
│                                                 │
│  External: ES (139.84.219.174) + S3 + LLM APIs │
└─────────────────────────────────────────────────┘
```

### Target (Scalable)

```
                    ┌──────────────┐
                    │  CloudFlare  │  CDN + DDoS + WAF
                    │  or AWS ALB  │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────▼──┐  ┌─────▼──┐  ┌─────▼──┐
        │ App-1  │  │ App-2  │  │ App-3  │   Auto-scaling group
        │ 2 wkrs │  │ 2 wkrs │  │ 2 wkrs │   (container or VM)
        └───┬────┘  └───┬────┘  └───┬────┘
            │            │            │
     ┌──────┴────────────┴────────────┴──────┐
     │              Shared Services           │
     │                                        │
     │  ┌──────────┐  ┌──────────┐            │
     │  │PostgreSQL│  │  Redis   │            │
     │  │ (RDS/HA) │  │ (cache)  │            │
     │  └──────────┘  └──────────┘            │
     │                                        │
     │  ┌──────────────────┐                  │
     │  │ Embedding Service│  1-2 instances   │
     │  │ (port 5100)      │  (GPU optional)  │
     │  └──────────────────┘                  │
     │                                        │
     │  ┌──────────────────┐                  │
     │  │ Elasticsearch    │  3-node cluster  │
     │  │ (managed or HA)  │                  │
     │  └──────────────────┘                  │
     │                                        │
     │  ┌──────────────────┐                  │
     │  │ ChromaDB Server  │  Shared storage  │
     │  │ (or Qdrant)      │  (optional)      │
     │  └──────────────────┘                  │
     └────────────────────────────────────────┘
```

---

## Phase 1: Harden Current Single-Server (Week 1-2)

**Goal**: Maximize reliability and performance on existing infra before scaling out.

### 1.1 Right-Size Gunicorn Workers

**Problem**: 16 workers each load embedding models = 16 × 1.5GB = 24GB RAM wasted.

**Fix**: Deploy embedding service + reduce workers.

```bash
# Start embedding service (loads models once)
python -m uvicorn services.embedding_service:app --host 127.0.0.1 --port 5100 --workers 1

# Set in .env
EMBEDDING_SERVICE_URL=http://127.0.0.1:5100
```

**Worker Formula**:
```
workers = min(2 * CPU_cores + 1, max_memory_GB / 0.5)
```

For a 4-core / 8GB server: **4 workers** (not 16).
For an 8-core / 16GB server: **8 workers**.

Update `gunicorn.conf.py`:
```python
import multiprocessing
workers = min(2 * multiprocessing.cpu_count() + 1, 9)  # Cap at 9
worker_class = "uvicorn.workers.UvicornWorker"
bind = "0.0.0.0:5001"
timeout = 300
graceful_timeout = 30
keepalive = 5
preload_app = True          # Share compiled graph across workers
max_requests = 1000         # Recycle workers to prevent memory leaks
max_requests_jitter = 50    # Stagger recycling
```

### 1.2 Enable PostgreSQL for State

**Problem**: SQLite can't handle concurrent writes from multiple workers.

```bash
# Already in docker-compose.prod.yml — just enable it
docker compose -f deploy/docker-compose.prod.yml up -d postgres

# Set in .env
POSTGRES_URL=postgresql://lawtech:STRONG_PASSWORD@localhost:5432/lawtech
```

This enables:
- LangGraph `AsyncPostgresSaver` (multi-turn state persistence across workers)
- Concurrent chat history writes without "database is locked"

### 1.3 Add Redis Response Cache

**Problem**: In-memory cache is per-worker (no sharing). Cache hit rate drops with more workers.

```yaml
# Add to deploy/docker-compose.prod.yml
redis:
  image: redis:7-alpine
  container_name: lawtech-redis
  command: redis-server --maxmemory 512mb --maxmemory-policy allkeys-lru
  volumes:
    - redis_data:/data
  networks:
    - lawtech-net
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 10s
    retries: 3
```

```bash
# .env
REDIS_URL=redis://localhost:6379
```

**Impact**: Response cache shared across all workers. Repeated queries → <100ms.

### 1.4 Optimize Elasticsearch Queries

**Current issue**: 7 indices, single client, no query optimization.

**Actions**:
1. Add index aliases for versioned rollover:
   ```
   legislation_v1 → alias: legislation
   ```
2. Set `request_timeout=10` on ES client (down from 30s default)
3. Add `_source` filtering — only fetch needed fields, not full documents
4. Enable ES query cache: `"request_cache": true` on search bodies

### 1.5 Implement Request Queuing

**Problem**: Under load, all 16 workers busy = 503s. No backpressure.

**Fix**: Add connection limits to Caddy:

```caddyfile
tool.lawttorney.com {
    reverse_proxy lawtech-app:5000 {
        # Max concurrent connections to backend
        transport http {
            max_conns_per_host 50
        }
        # Health-based load balancing
        health_uri /pyapi/health
        health_interval 10s
    }

    # Rate limit at edge
    rate_limit {
        zone dynamic {
            key    {remote_host}
            events 30
            window 1m
        }
    }
}
```

---

## Phase 2: Containerize & Orchestrate (Week 3-4)

**Goal**: Move from bare-metal to reproducible container deployment.

### 2.1 Production Docker Compose (Full Stack)

```yaml
# deploy/docker-compose.full.yml
version: "3.9"

services:
  # ─── Application ──────────────────────────────────
  app:
    build:
      context: ..
      dockerfile: deploy/Dockerfile
    container_name: lawtech-app
    env_file: ../.env
    environment:
      - POSTGRES_URL=postgresql://lawtech:${POSTGRES_PASSWORD}@postgres:5432/lawtech
      - REDIS_URL=redis://redis:6379
      - EMBEDDING_SERVICE_URL=http://embedding:5100
    volumes:
      - ../data:/app/data
      - ../logs:/app/logs
      - ../chroma_store:/app/chroma_store
    deploy:
      replicas: 3                    # 3 app instances
      resources:
        limits:
          memory: 2G
          cpus: "2.0"
        reservations:
          memory: 1G
          cpus: "1.0"
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
      embedding: { condition: service_healthy }
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/pyapi/health"]
      interval: 15s
      timeout: 10s
      retries: 3
      start_period: 60s
    networks:
      - lawtech-net
    restart: unless-stopped

  # ─── Embedding Service ────────────────────────────
  embedding:
    build:
      context: ..
      dockerfile: deploy/Dockerfile.embedding
    container_name: lawtech-embedding
    volumes:
      - ../models:/app/models:ro
    deploy:
      resources:
        limits:
          memory: 3G               # BGE-large needs ~1.5GB
          cpus: "2.0"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5100/health"]
      interval: 30s
      retries: 3
      start_period: 120s          # Model loading takes time
    networks:
      - lawtech-net
    restart: unless-stopped

  # ─── PostgreSQL ───────────────────────────────────
  postgres:
    image: postgres:16-alpine
    container_name: lawtech-postgres
    environment:
      POSTGRES_DB: lawtech
      POSTGRES_USER: lawtech
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    deploy:
      resources:
        limits:
          memory: 1G
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U lawtech -d lawtech"]
      interval: 10s
      retries: 5
    networks:
      - lawtech-net
    restart: unless-stopped

  # ─── Redis ────────────────────────────────────────
  redis:
    image: redis:7-alpine
    container_name: lawtech-redis
    command: >
      redis-server
      --maxmemory 512mb
      --maxmemory-policy allkeys-lru
      --save 60 1000
      --appendonly yes
    volumes:
      - redis_data:/data
    deploy:
      resources:
        limits:
          memory: 768M
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      retries: 3
    networks:
      - lawtech-net
    restart: unless-stopped

  # ─── Caddy (Reverse Proxy + HTTPS) ────────────────
  caddy:
    image: caddy:2-alpine
    container_name: lawtech-caddy
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - ../Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    environment:
      - DOMAIN=${DOMAIN}
    depends_on:
      - app
    networks:
      - lawtech-net
    restart: unless-stopped

networks:
  lawtech-net:
    driver: bridge

volumes:
  postgres_data:
  redis_data:
  caddy_data:
  caddy_config:
```

### 2.2 Embedding Service Dockerfile

```dockerfile
# deploy/Dockerfile.embedding
FROM python:3.12-slim

WORKDIR /app

# Install only embedding dependencies
RUN pip install --no-cache-dir \
    fastapi uvicorn \
    sentence-transformers torch --extra-index-url https://download.pytorch.org/whl/cpu

COPY services/embedding_service.py .

EXPOSE 5100

CMD ["python", "-m", "uvicorn", "embedding_service:app", \
     "--host", "0.0.0.0", "--port", "5100", "--workers", "1"]
```

### 2.3 Caddyfile for Load Balancing

```caddyfile
{$DOMAIN} {
    # Load balance across app replicas
    reverse_proxy app:5000 {
        lb_policy round_robin
        health_uri /pyapi/health
        health_interval 10s
        fail_duration 30s

        # SSE-friendly settings
        flush_interval -1
        transport http {
            read_timeout 360s
            write_timeout 360s
        }
    }

    # Security headers
    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
        -Server
    }

    log {
        output file /var/log/caddy/access.log {
            roll_size 100mb
            roll_keep 5
        }
    }
}
```

---

## Phase 3: Performance Optimization (Week 3-5)

### 3.1 LLM Call Optimization (Biggest Latency Win)

**Problem**: Multi-agent queries make 5-15 LLM calls. Each adds 0.5-3s.

**Strategy: Tiered Model Assignment**

| Call Type | Current Model | Optimized Model | Latency Savings |
|-----------|--------------|-----------------|-----------------|
| Task classification | GPT-4o (2-4s) | Gemini Flash Lite (0.3-0.5s) | **1.5-3.5s** |
| Query rewrite | Gemini Flash Lite | Gemini Flash Lite | 0 (already optimal) |
| Metadata extraction | GPT-4o (1-2s) | Gemini Flash Lite + regex (0.3s) | **0.7-1.7s** |
| Response generation | Gemini Flash (1-3s) | Gemini Flash (keep) | 0 |
| Synthesis (multi-agent) | Gemini Flash (1-3s) | Skip if single agent | **1-3s** |
| Followup suggestions | Async (fire-and-forget) | Already async | 0 |

**Total potential savings**: 3-8s per multi-agent request.

### 3.2 Agent-Level Timeouts & Partial Results

**Problem**: Slowest agent determines total latency. No partial result return.

**Proposed: Orchestrator timeout with partial merge**

```python
# In orchestrator_synthesize:
AGENT_DEADLINE_SEC = 15  # Max wait for any single agent

async def orchestrator_synthesize(state):
    # Wait for agents with deadline
    results = state.get("agent_results", {})
    planned = state.get("tasks_planned", [])

    # If some agents haven't returned yet, proceed with what we have
    available = {k: v for k, v in results.items() if v.content}

    if not available:
        # All agents failed — return web fallback
        return await web_search_fallback(state["query"], "General", GENERAL_PROMPT)

    # Merge available results (don't wait for stragglers)
    return merge_and_synthesize(available, state)
```

### 3.3 Response Streaming Optimization

**Problem**: Full synthesis happens before streaming starts. User sees nothing for 5-15s.

**Strategy: Progressive streaming**

```
Phase 1 (0-2s):   Stream status messages ("Searching legislation...")
Phase 2 (2-5s):   Stream first agent's raw result (fastest agent)
Phase 3 (5-10s):  Stream synthesized response (if multi-agent)
Phase 4 (10-15s): Stream sources + followup suggestions
```

This gives users visible output within 2s regardless of total processing time.

### 3.4 Parallelization Fixes

**Fix 1: S3 Link Generation (Judgment Agent)**
```python
# Before: Serial
for hit in hits:
    link = generate_s3_link.invoke({...})

# After: Parallel
import asyncio
links = await asyncio.gather(*[
    asyncio.to_thread(generate_s3_link.invoke, {...})
    for hit in hits
])
```
**Savings**: 0.5-2s per judgment query.

**Fix 2: Newacts Mapping (Parallel old↔new lookup)**
```python
# Run both mapping directions concurrently
old_to_new, new_to_old = await asyncio.gather(
    search_mapping(old_section, "old_to_new"),
    search_mapping(new_section, "new_to_old"),
)
```

**Fix 3: Multi-Collection ChromaDB (Document Agent)**
```python
# Parallel retrieval across collections
results = await asyncio.gather(*[
    asyncio.to_thread(search_collection, coll_name, query)
    for coll_name in collections
])
```

---

## Phase 4: Horizontal Scaling (Week 5-8)

### 4.1 Scaling Matrix

| Component | Scaling Strategy | Stateless? | Shared State |
|-----------|-----------------|------------|--------------|
| **App workers** | Horizontal (add replicas) | Yes | PostgreSQL + Redis |
| **Embedding service** | Horizontal (1-2 replicas) | Yes | None (stateless inference) |
| **PostgreSQL** | Vertical first, then read replicas | N/A | Primary DB |
| **Redis** | Vertical (single node sufficient to 1000 QPS) | N/A | Cache layer |
| **Elasticsearch** | Horizontal (add data nodes) | N/A | Shared indices |
| **ChromaDB** | Migrate to server mode or Qdrant | N/A | Shared vector store |

### 4.2 Auto-Scaling Policy (if using Kubernetes or Docker Swarm)

```yaml
# Kubernetes HPA example
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: lawtech-app
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: lawtech-app
  minReplicas: 2
  maxReplicas: 10
  metrics:
    # Scale on active requests (Prometheus custom metric)
    - type: Pods
      pods:
        metric:
          name: lawtech_active_requests
        target:
          type: AverageValue
          averageValue: 8       # Scale up when avg > 8 active requests per pod
    # Scale on CPU
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
        - type: Pods
          value: 2
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300    # Wait 5 min before scaling down
      policies:
        - type: Pods
          value: 1
          periodSeconds: 120
```

### 4.3 Elasticsearch HA Cluster

**Current**: Single node at `139.84.219.174:9200` (single point of failure).

**Target**: 3-node cluster with replicas.

```yaml
# ES cluster configuration
cluster.name: lawtech-es
node.name: es-node-${NODE_NUM}

# Minimum master nodes (prevents split brain)
discovery.seed_hosts:
  - es-node-1:9300
  - es-node-2:9300
  - es-node-3:9300

cluster.initial_master_nodes:
  - es-node-1
  - es-node-2
  - es-node-3

# Index settings (per-index)
index.number_of_shards: 2        # Split across nodes
index.number_of_replicas: 1      # 1 replica = survives 1 node loss
index.refresh_interval: 30s      # Reduce from default 1s (legal data rarely changes)
```

**Sizing per node** (for ~10M documents total):
- CPU: 4 cores
- RAM: 8GB (4GB heap)
- Disk: 100GB SSD

### 4.4 ChromaDB → Managed Vector DB Migration

**When to migrate**: When PDF uploads exceed 10GB or need cross-worker access.

**Options**:
| Service | Pros | Cons | Cost |
|---------|------|------|------|
| **Qdrant** (self-hosted) | Fast, good API, free | Must manage infra | Infra only |
| **Pinecone** | Managed, scalable | Vendor lock-in | $70+/mo |
| **Weaviate** | Hybrid search, open source | Complex setup | Infra only |
| **ChromaDB Server Mode** | Minimal code change | Still maturing | Free |

**Recommended**: ChromaDB Server Mode first (minimal change), migrate to Qdrant when scale demands.

---

## Phase 5: CI/CD & Zero-Downtime Deploys (Week 4-6)

### 5.1 Blue-Green Deployment

```
┌──────────────────────────────┐
│ Load Balancer (Caddy/ALB)    │
│                              │
│    Active: BLUE (v1.2.3)     │ ←── 100% traffic
│    Standby: GREEN (v1.2.4)   │ ←── 0% traffic
└──────────────────────────────┘

Deploy new version:
1. Build GREEN with new code
2. Run health checks on GREEN
3. Run smoke tests on GREEN
4. Shift traffic: BLUE 0% ← → GREEN 100%
5. Monitor for 5 min
6. If OK: decommission BLUE
7. If errors: rollback (shift back to BLUE)
```

### 5.2 Enhanced GitHub Actions Pipeline

```yaml
# .github/workflows/deploy-v2.yml
name: Deploy (Blue-Green)

on:
  push:
    branches: [master]

jobs:
  # ─── Stage 1: Quality Gates ─────────────────────
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install deps
        run: pip install -r requirements.txt

      - name: Syntax check
        run: python -m py_compile $(find . -name "*.py" -not -path "./venv/*")

      - name: Import check (critical modules)
        run: |
          python -c "from core.settings import settings"
          python -c "from core.auth import verify_api_key"
          python -c "from core.checkpointer import get_checkpointer"

      - name: Type check (optional)
        run: pip install mypy && mypy core/ --ignore-missing-imports || true

  # ─── Stage 2: Build Container ────────────────────
  build:
    needs: lint-and-test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build Docker image
        run: |
          docker build -f deploy/Dockerfile \
            -t lawtech-app:${{ github.sha }} \
            -t lawtech-app:latest .

      - name: Save image
        run: docker save lawtech-app:${{ github.sha }} | gzip > image.tar.gz

      - uses: actions/upload-artifact@v4
        with:
          name: docker-image
          path: image.tar.gz

  # ─── Stage 3: Deploy GREEN ───────────────────────
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment: production
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: docker-image

      - name: Deploy to server
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.SERVER_HOST }}
          username: ${{ secrets.SERVER_USER }}
          key: ${{ secrets.SSH_PRIVATE_KEY }}
          script: |
            set -e

            # Load new image
            docker load < /tmp/image.tar.gz

            # Start GREEN alongside BLUE
            cd /opt/lawtech
            docker compose -f deploy/docker-compose.full.yml up -d --no-deps --scale app=3 app

            # Wait for health
            for i in $(seq 1 30); do
              if curl -sf http://localhost:5000/pyapi/health > /dev/null; then
                echo "GREEN healthy"
                break
              fi
              sleep 2
            done

            # Smoke test
            RESP=$(curl -s -o /dev/null -w "%{http_code}" \
              -H "X-API-Key: ${{ secrets.SMOKE_TEST_KEY }}" \
              -X POST http://localhost:5000/pyapi/search \
              -H "Content-Type: application/json" \
              -d '{"Promptquery":"What is Section 302 IPC?"}')

            if [ "$RESP" != "200" ]; then
              echo "Smoke test failed (HTTP $RESP), rolling back"
              docker compose -f deploy/docker-compose.full.yml rollback
              exit 1
            fi

            echo "Deploy successful"
```

### 5.3 Database Migration Strategy

```bash
# Before deploy: run migrations
# Using alembic (if added) or manual SQL

# PostgreSQL migration for chat_store tables:
psql $POSTGRES_URL <<'SQL'
-- Idempotent migration (safe to re-run)
CREATE TABLE IF NOT EXISTS threads (
    thread_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    summary_text TEXT,
    total_turns INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    thread_id TEXT REFERENCES threads(thread_id),
    turn_number INTEGER,
    user_query TEXT,
    ai_response TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, turn_number);
SQL
```

---

## Phase 6: Observability & Reliability (Ongoing)

### 6.1 Enhanced Monitoring Stack

**Current**: Prometheus + Grafana with 5 alert rules.

**Add**:

| Component | Purpose | Tool |
|-----------|---------|------|
| **Distributed tracing** | Request flow across services | OpenTelemetry + Jaeger |
| **Log aggregation** | Centralized structured logs | Loki + Grafana |
| **Error tracking** | Exception grouping + alerts | Sentry |
| **Uptime monitoring** | External health checks | UptimeRobot / Checkly |
| **Cost tracking** | LLM API spend per user/agent | Custom Prometheus metric |

### 6.2 Key SLOs (Service Level Objectives)

| SLO | Target | Measurement | Alert Threshold |
|-----|--------|-------------|-----------------|
| **Availability** | 99.9% (8.7h downtime/year) | `up` metric | Down > 3 min |
| **Latency P95** | < 15s (single agent) | `lawtech_request_latency_seconds` | > 30s for 3 min |
| **Latency P95** | < 25s (multi-agent) | Same | > 45s for 3 min |
| **Error rate** | < 1% 5xx | `lawtech_requests_total{status=~"5.."}` | > 5% for 2 min |
| **ES fallback rate** | < 10% | `lawtech_fallback_total{tier="web_search"}` | > 20% for 5 min |
| **Quality score** | > 0.7 avg | `lawtech_quality_avg` | < 0.6 for 10 samples |

### 6.3 OpenTelemetry Integration

```python
# core/tracing.py (new file)
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource

def setup_tracing():
    resource = Resource.create({"service.name": "lawtech-api"})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint="http://jaeger:4317")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

tracer = trace.get_tracer("lawtech")
```

Usage in agents:
```python
@tracer.start_as_current_span("legislation_search")
async def legislation_node(state):
    span = trace.get_current_span()
    span.set_attribute("query", state["query"][:100])
    span.set_attribute("agent", "Legislation")
    # ... agent logic
    span.set_attribute("fallback_used", fallback_used)
    span.set_attribute("result_count", len(results))
```

### 6.4 Cost Tracking Dashboard

```python
# Add to core/metrics.py
llm_cost_total = Counter(
    "lawtech_llm_cost_usd_total",
    "Estimated LLM cost in USD",
    ["provider", "model"]
)

# Token-to-cost mapping (approximate)
COST_PER_1K = {
    "gpt-4o":              {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini":         {"input": 0.00015, "output": 0.0006},
    "gemini-2.5-flash":    {"input": 0.00015, "output": 0.0006},
    "gemini-2.5-flash-lite": {"input": 0.0, "output": 0.0},  # Free tier
    "gemini-2.5-pro":      {"input": 0.00125, "output": 0.005},
}
```

---

## Phase 7: Disaster Recovery & Data Protection

### 7.1 Backup Strategy

| Data | Frequency | Retention | Method |
|------|-----------|-----------|--------|
| **PostgreSQL** | Every 6 hours | 30 days | `pg_dump` → S3 |
| **SQLite** (if still used) | Daily | 14 days | `scripts/backup_db.sh` → S3 |
| **Elasticsearch indices** | Daily snapshot | 7 days | ES snapshot API → S3 |
| **ChromaDB** | Daily tar | 7 days | tar → S3 |
| **Config/secrets** | On change | Indefinite | AWS Secrets Manager / Vault |

### 7.2 Automated PostgreSQL Backup

```bash
#!/bin/bash
# scripts/backup_postgres.sh
set -euo pipefail

BACKUP_DIR="/opt/lawtech/backups"
S3_BUCKET="lawttorney-backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="lawtech_pg_${TIMESTAMP}.sql.gz"

# Dump and compress
pg_dump "$POSTGRES_URL" | gzip > "${BACKUP_DIR}/${FILENAME}"

# Upload to S3
aws s3 cp "${BACKUP_DIR}/${FILENAME}" "s3://${S3_BUCKET}/postgres/${FILENAME}"

# Cleanup local (keep 3 days)
find "${BACKUP_DIR}" -name "lawtech_pg_*.sql.gz" -mtime +3 -delete

echo "Backup complete: ${FILENAME}"
```

Cron:
```
0 */6 * * * /opt/lawtech/scripts/backup_postgres.sh >> /opt/lawtech/logs/backup.log 2>&1
```

### 7.3 ES Snapshot Repository

```bash
# Register S3 snapshot repo
curl -X PUT "http://es-node-1:9200/_snapshot/lawtech_backups" -H 'Content-Type: application/json' -d '{
  "type": "s3",
  "settings": {
    "bucket": "lawttorney-backups",
    "region": "ap-south-1",
    "base_path": "elasticsearch"
  }
}'

# Daily snapshot policy
curl -X PUT "http://es-node-1:9200/_slm/policy/daily-snap" -H 'Content-Type: application/json' -d '{
  "schedule": "0 0 2 * * ?",
  "name": "<lawtech-snap-{now/d}>",
  "repository": "lawtech_backups",
  "config": {
    "indices": ["legislation", "judgements", "supreme_court_judgement", "newacts_v1", "drafting", "constitution", "legal_maxims"]
  },
  "retention": {
    "expire_after": "7d",
    "max_count": 10
  }
}'
```

---

## Resource Requirements Summary

### Minimum Viable Production (50 concurrent users)

| Service | Instances | CPU | RAM | Disk | Monthly Cost (est.) |
|---------|-----------|-----|-----|------|-------------------|
| App (gunicorn) | 2 | 2 cores | 2GB | 10GB | $40 |
| Embedding service | 1 | 2 cores | 3GB | 5GB | $30 |
| PostgreSQL | 1 | 1 core | 1GB | 20GB | $15 |
| Redis | 1 | 0.5 core | 512MB | 1GB | $5 |
| Elasticsearch | 1 (existing) | 4 cores | 8GB | 100GB | $60 |
| Caddy | 1 | 0.5 core | 256MB | 1GB | — |
| Monitoring | 1 | 1 core | 1GB | 20GB | $15 |
| **Total infra** | | **11 cores** | **16GB** | **157GB** | **~$165/mo** |
| **LLM APIs** | | | | | **~$100-300/mo** |

### Growth Target (500 concurrent users)

| Service | Instances | CPU | RAM | Disk | Monthly Cost (est.) |
|---------|-----------|-----|-----|------|-------------------|
| App (gunicorn) | 5 | 2 cores each | 2GB each | 10GB | $200 |
| Embedding service | 2 | 2 cores each | 3GB each | 5GB | $60 |
| PostgreSQL (RDS) | 1 primary + 1 read replica | 4 cores | 8GB | 100GB | $150 |
| Redis | 1 | 2 cores | 2GB | 5GB | $30 |
| Elasticsearch | 3-node cluster | 4 cores each | 8GB each | 100GB each | $360 |
| Caddy/ALB | 1 | 1 core | 512MB | 1GB | $20 |
| Monitoring | 1 | 2 cores | 2GB | 50GB | $30 |
| **Total infra** | | **33 cores** | **51GB** | **476GB** | **~$850/mo** |
| **LLM APIs** | | | | | **~$500-1500/mo** |

---

## Implementation Priority

| Priority | Task | Impact | Effort | Phase |
|----------|------|--------|--------|-------|
| **P0** | Right-size workers + embedding service | Prevents OOM, saves 20GB RAM | Low | 1 |
| **P0** | Enable PostgreSQL for state | Fixes multi-worker concurrency | Low | 1 |
| **P0** | Add Redis shared cache | 50%+ cache hit rate improvement | Low | 1 |
| **P1** | Optimize LLM model tiers | 3-8s latency reduction | Medium | 3 |
| **P1** | Add agent-level timeouts | Prevents tail latency blowup | Medium | 3 |
| **P1** | Parallelize S3/ChromaDB calls | 1-3s savings per request | Low | 3 |
| **P1** | Containerize full stack | Reproducible deploys | Medium | 2 |
| **P2** | Blue-green deployment | Zero-downtime deploys | Medium | 5 |
| **P2** | ES HA cluster | Eliminates ES SPOF | High | 4 |
| **P2** | OpenTelemetry tracing | Debug latency issues | Medium | 6 |
| **P3** | Auto-scaling (K8s/Swarm) | Handle traffic spikes | High | 4 |
| **P3** | ChromaDB → managed vector DB | Scale PDF uploads | Medium | 4 |
| **P3** | Cost tracking dashboard | Control LLM spend | Low | 6 |

---

## Quick Wins Checklist (Do This Week)

- [ ] Deploy embedding service on port 5100, set `EMBEDDING_SERVICE_URL`
- [ ] Reduce gunicorn workers from 16 to `2 * CPU + 1` (max 9)
- [ ] Enable PostgreSQL (`POSTGRES_URL` in `.env`)
- [ ] Add Redis container, set `REDIS_URL`
- [ ] Add `max_requests=1000` + `max_requests_jitter=50` to gunicorn.conf.py
- [ ] Set ES `request_timeout=10` in clients.py
- [ ] Add `_source` filtering to all ES queries (fetch only needed fields)
- [ ] Parallelize S3 link generation in judgment agent
