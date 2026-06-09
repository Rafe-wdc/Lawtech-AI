# Phase 1 — New Host Provisioning Runbook

> Companion: [scale_50_concurrent_users_plan.md](scale_50_concurrent_users_plan.md). This runbook executes §5 of that plan.
>
> Goal: stand up a fresh Ubuntu 24.04 LTS box (8 vCPU / 32 GB / NVMe) that runs the existing Lawtech-AI stack in **single-worker parity mode** by the end. Multi-worker cutover is Phase 2.
>
> Estimated wall time: **2–3 hours** (≈ 30 min of human input, the rest is downloads + tests).

---

## 0. Inputs you need before starting

| Item | Where to get it |
|---|---|
| New host root SSH access | Provider console after provisioning |
| Existing prod box SSH access | `root@64.176.97.182` (per DEPLOYMENT.md) |
| `.env.production` values (real secrets) | 1Password / your secrets vault |
| AWS OpenSearch endpoint + creds | Already in current `.env` |
| `OPENAI_API_KEY`, `GOOGLE_API_KEY` | Vault |
| `API_KEYS`, `ADMIN_API_KEY` | Vault (or generate fresh per §4.2) |

---

## 1. Sizing decision

**Recommended box: 8 vCPU / 32 GB RAM / 160 GB NVMe / Ubuntu 24.04 LTS, same provider as current prod (Vultr).**

| Provider | Plan | Spec | ~Monthly | Notes |
|---|---|---|---|---|
| **Vultr High Frequency** (recommended) | `vhf-8c-32gb` | 8 vCPU AMD EPYC, 32 GB RAM, 512 GB NVMe | ~$192 | Same console & billing as current prod. NVMe baseline. |
| Vultr Bare Metal | E-2388G or similar | dedicated cores, 64+ GB | ~$300 | Best perf, overkill for 50 streams unless we plan to host PG + Chroma + workers + DB all here for years. |
| Hetzner Cloud CCX23 | CCX23 | 8 dedicated vCPU, 32 GB RAM, 240 GB NVMe | ~$60 | ~70% cheaper but ops surface widens (different console, network). |
| AWS c7i.2xlarge | on-demand | 8 vCPU, 16 GB (need r7i.2xlarge for 32 GB) | ~$300+ | Worst $/spec for steady-state. |

**Why Vultr HF**: it matches what you already run and operate. Switching providers in the same sprint as scaling out doubles the risk surface. Hetzner is the right call only if budget is the deciding factor.

**Why Ubuntu 24.04 LTS**: native Python 3.12 (project requires 3.12+ per CLAUDE.md), support until April 2029. 24.04.1+ has shipped — production-safe.

**Why NVMe**: ChromaDB writes (PDF upload concurrency in Phase 5) and Postgres `effective_io_concurrency=200` both assume NVMe-class storage. Block storage drops both perf and tuning assumptions.

---

## 2. Pre-flight on the **old** box (no risk)

Snapshot what we're about to mirror. This is the rollback target if Phase 8 cutover regresses.

```bash
ssh root@64.176.97.182

# Snapshot Postgres
cd /root
sudo -u postgres pg_dump lawtech | gzip > /root/lawtech-pgdump.$(date +%F).sql.gz
ls -lh /root/lawtech-pgdump.*.sql.gz

# Snapshot chroma_store (small; OK to keep on box)
tar czf /root/chroma_store.$(date +%F).tgz /root/Lawtech-AI/chroma_store
ls -lh /root/chroma_store.*.tgz

# Note the running config so we can compare parity later
cat /root/Lawtech-AI/.env > /root/env.snapshot.$(date +%F)
chmod 600 /root/env.snapshot.*
```

Pull these to your laptop too (`scp`) — belt and braces.

---

## 3. Provision the VM (provider console)

In Vultr:
- **Type**: High Frequency
- **Location**: same region as current prod (avoid cross-region latency on internal calls if you ever federate)
- **OS**: Ubuntu 24.04 LTS x64
- **Plan**: 8 vCPU / 32 GB RAM / 512 GB NVMe
- **Backups**: enable (~$10/mo). We're also doing logical backups, but disk-image backups give faster recovery.
- **SSH key**: upload yours
- **Hostname**: `lawtech-v2-prod` (matches systemd unit name `lawttorney-v2.service`)

Once running:

```bash
# Replace with the new IP
NEW_IP=YOUR.NEW.HOST.IP
ssh root@$NEW_IP "uname -a && lsb_release -a"
# Expect: Linux ... GNU/Linux, Ubuntu 24.04.x LTS
```

---

## 4. Bootstrap from your laptop (or jump host)

### 4.1 Rsync the code

```bash
# From your laptop, pointed at the cloned Lawtech-AI repo:
cd /path/to/Lawtech-AI

rsync -avz --delete \
    --exclude '__pycache__' --exclude '*.pyc' \
    --exclude '.pytest_cache' --exclude 'logs' \
    --exclude 'data' --exclude 'venv' \
    --exclude 'chroma_store' --exclude 'uploads' \
    --exclude '.env' \
    ./ root@$NEW_IP:/root/Lawtech-AI/
```

### 4.2 Copy embedding models (the big one — ~1.4 GB)

```bash
# Pull from old box → push to new box.
# Alternative: rsync directly between boxes if their networks talk.
rsync -avz root@64.176.97.182:/root/Lawtech-AI/models/ /tmp/lawtech-models/
rsync -avz /tmp/lawtech-models/ root@$NEW_IP:/root/Lawtech-AI/models/
```

Verify on the new box:
```bash
ssh root@$NEW_IP "ls -la /root/Lawtech-AI/models/"
# Should show: bge-large-en-v1.5  all-MiniLM-L6-v2
```

### 4.3 Restore Postgres data (optional for Phase 1 smoke; required before cutover)

```bash
# Copy the dump from your laptop (or scp -3 between boxes).
scp root@64.176.97.182:/root/lawtech-pgdump.*.sql.gz /tmp/
scp /tmp/lawtech-pgdump.*.sql.gz root@$NEW_IP:/root/

# Restore happens AFTER provisioning (PG isn't installed yet). See §6.4.
```

### 4.4 Place the `.env`

```bash
ssh root@$NEW_IP
cp /root/Lawtech-AI/.env.production /root/Lawtech-AI/.env
chmod 600 /root/Lawtech-AI/.env
nano /root/Lawtech-AI/.env   # fill in real values
```

Fill these explicitly (rest can take defaults):
```ini
OPENAI_API_KEY="sk-..."
GOOGLE_API_KEY="AIza..."
API_KEYS="<paste from vault, or generate fresh>"
ADMIN_API_KEY="<paste from vault>"
ALLOWED_ORIGINS="https://tool.lawttorney.com"
POSTGRES_URL="postgresql://lawtech:CHANGE_ME@localhost:5432/lawtech"   # gets patched by provision script
REQUIRE_POSTGRES=true
ES_URL="https://your-opensearch-endpoint.ap-south-1.es.amazonaws.com"
ES_USER="..."
ES_PASSWORD="..."
LOG_LEVEL="INFO"

# Phase 1 additions (forward-compat — used in Phase 2/3/5)
GUNICORN_WORKERS=6
EMBEDDING_SERVICE_URL=http://localhost:5100
EMBEDDING_SERVICE_WORKERS=2
CHROMA_SERVER_HOST=localhost
CHROMA_SERVER_PORT=8000
RATE_LIMIT_PER_MINUTE=120
MAX_INFLIGHT_PER_WORKER=10
HOST=0.0.0.0
PORT=5001
```

To generate strong keys if you don't have them in the vault:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 5. Run the provisioning script

```bash
ssh root@$NEW_IP
chmod +x /root/Lawtech-AI/deploy/provision_new_host.sh
bash /root/Lawtech-AI/deploy/provision_new_host.sh 2>&1 | tee /root/provision.log
```

The script:
1. Installs all APT packages (Python 3.12, PG 16, nginx, certbot, ufw, fail2ban, redis).
2. Tunes the kernel (`/etc/sysctl.d/99-lawtech.conf`) and file-descriptor limits.
3. Enables `ufw` (only 22/80/443 inbound).
4. Bootstraps Postgres: creates `lawtech` DB + user, drops the tuned config from [deploy/postgresql.lawtech.conf](../deploy/postgresql.lawtech.conf), restarts PG.
5. Patches `POSTGRES_URL` in your `.env`.
6. Creates the venv and installs `requirements.lock.txt` + `gunicorn + uvloop + httptools`.
7. Smoke-imports `core.gateway:app` to catch startup errors early.
8. Installs the three systemd units from [deploy/systemd/](../deploy/systemd/) (but only enables `lawttorney-embed`; v2 + chroma stay disabled until Phases 2 & 5).
9. Installs logrotate.

Watch for `[WARN]` lines — those flag manual follow-ups (e.g. missing models, missing `.env`).

**Expected wall time: 5–10 min** (mostly APT + pip).

---

## 6. Verify the provisioned box

### 6.1 Postgres
```bash
# As lawtech user (from venv with psycopg2 installed via the lock file)
/root/Lawtech-AI/venv/bin/python -c "
import psycopg2, os
url = open('/root/Lawtech-AI/.env').read()
for line in url.splitlines():
    if line.startswith('POSTGRES_URL'):
        u = line.split('=',1)[1].strip().strip('\"')
        conn = psycopg2.connect(u)
        with conn.cursor() as c:
            c.execute('SHOW server_version;'); print('PG:', c.fetchone()[0])
            c.execute('SHOW max_connections;'); print('max_connections:', c.fetchone()[0])
            c.execute('SHOW shared_buffers;'); print('shared_buffers:', c.fetchone()[0])
        conn.close()
        break
"
```
Expected:
```
PG: 16.x
max_connections: 200
shared_buffers: 8GB
```

### 6.2 Embedding service
```bash
systemctl start lawttorney-embed
systemctl status lawttorney-embed --no-pager
# Wait ~30s for BGE-large to load (warm-up), then:
curl -s http://localhost:5100/health | jq .
# Expect: {"status": "ok", "models_loaded": [...], "available_models": ["retriever", "qa"]}
```
Tail logs if it doesn't come up: `journalctl -u lawttorney-embed -f`.

### 6.3 Restore the Postgres dump (real data for the smoke test)
```bash
gunzip -c /root/lawtech-pgdump.*.sql.gz | sudo -u postgres psql -d lawtech
# Validate
sudo -u postgres psql -d lawtech -c "SELECT COUNT(*) FROM chat_messages;"
```

### 6.4 Single-worker API smoke test (parity with current prod)
```bash
cd /root/Lawtech-AI
# Use the existing start.sh, which runs uvicorn with 1 worker — exactly what
# the current prod box does. NO multi-worker yet — that's Phase 2.
WORKERS=1 ./start.sh
```
Watch `tail -f /root/Lawtech-AI/logs/api.log` and look for:
```
... | Compiling agent graph at startup
... | Graph compiled successfully | nodes=...
INFO:     Uvicorn running on http://0.0.0.0:5001
```

### 6.5 End-to-end probes
```bash
# Basic health
curl -s http://localhost:5001/pyapi/health
# Expect: {"status":"ok","version":"2.0.0"}

# Deep health (admin-gated; checks OpenAI + Gemini)
curl -s -H "X-API-Key: $ADMIN_API_KEY" http://localhost:5001/pyapi/health/detailed | jq .
# All entries should be "ok".

# Metrics endpoint
curl -s -H "X-API-Key: $ADMIN_API_KEY" http://localhost:5001/pyapi/metrics | head -20
# Expect Prometheus exposition format with lawtech_* metrics.

# Real query (replace API key with one from your .env)
USER_KEY=$(grep '^API_KEYS=' /root/Lawtech-AI/.env | cut -d= -f2 | tr -d '"' | cut -d, -f1)
curl -N -H "X-API-Key: $USER_KEY" -H "Content-Type: application/json" \
     -d '{"Promptquery":"What does Section 9 of the CPC say?"}' \
     http://localhost:5001/pyapi/search/stream
# Expect SSE stream — `event: token` lines arriving over a few seconds.
```

All four probes green → **Phase 1 done.**

---

## 7. Postgres tuning rationale

These values came from §6 of [scale_50_concurrent_users_plan.md](scale_50_concurrent_users_plan.md) and the web research recorded below. Recorded here so future-you / a teammate can challenge them without re-doing the homework.

| Setting | Value | Why |
|---|---|---|
| `max_connections` | 200 | 6 workers × ~20 conns (LangGraph saver pool + chat_store pool) ≈ 120; 40 % slack. |
| `shared_buffers` | 8 GB | 25 % of RAM — Postgres-canonical value for OLTP. Higher (40 %) helps only on read-mostly DWH workloads. |
| `effective_cache_size` | 24 GB | 75 % of RAM. This is a *hint to the planner*, not an allocation — it shapes index-scan vs seq-scan choices. |
| `work_mem` | 16 MB | Per-sort/hash op. 200 conns × 16 MB ceiling = 3.2 GB worst case — safely clear of OOM. |
| `maintenance_work_mem` | 2 GB | RAM/16. Speeds up autovacuum + index builds; only used by maintenance, not query path. |
| `wal_buffers` | 16 MB | Auto-tunes to ~3 % of `shared_buffers` if `-1`; 16 MB is a safe explicit value. |
| `max_wal_size` | 4 GB | Lets WAL grow between checkpoints — reduces checkpoint frequency at modest cost in recovery time. |
| `random_page_cost` | 1.1 | NVMe random IO is near-sequential. Default of 4 biases the planner toward seq scans on indexed tables. |
| `effective_io_concurrency` | 200 | NVMe handles deep queues. Postgres uses this for bitmap-index scans. |
| `max_parallel_workers_per_gather` | 4 | 8 vCPU box — leave headroom for foreground OLTP under parallel sort/aggregate. |
| `log_min_duration_statement` | 1000 | 1 s slow-query log. Catches N+1 from the saver / chat_store under load. |

---

## 8. Kernel + ulimit tuning rationale

In `/etc/sysctl.d/99-lawtech.conf`:

| Setting | Value | Why |
|---|---|---|
| `net.core.somaxconn` | 4096 | Backlog of completed-handshake connections waiting for `accept()`. Default 4096 on Ubuntu 24.04 but we pin it. Uvicorn `--backlog` passes through to `listen()`. |
| `net.ipv4.tcp_max_syn_backlog` | 4096 | Half-open SYN queue — protects against bursty client reconnect storms. |
| `net.ipv4.ip_local_port_range` | 1024 65535 | Default ephemeral range is 32768-60999 ≈ 28k ports. For 50 streams × multiple upstream sockets, this is fine, but raising is free insurance. |
| `net.ipv4.tcp_tw_reuse` | 1 | Reuse TIME_WAIT sockets for outbound — relevant since we make many short-lived calls to OpenAI/Gemini. |
| `net.ipv4.tcp_fin_timeout` | 15 | Default 60s is conservative; we don't need to hold dead sockets that long. |
| `fs.file-max` | 2 097 152 | System-wide FD ceiling. Process limit (LimitNOFILE) is the user-facing cap. |
| `vm.overcommit_memory` | 1 | Lets Python allocate large arenas without triggering OOM-killer too eagerly. Standard for Python/numpy workloads. |

In `/etc/security/limits.d/99-lawtech.conf`: `nofile 65536` for all users and root — covers admin shells. Services get their own via `LimitNOFILE=` in the systemd unit.

---

## 9. Rollback / undo

If anything in Phase 1 breaks the box beyond easy recovery:

```bash
# Stop services
systemctl stop lawttorney-embed lawttorney-v2 2>/dev/null
systemctl disable lawttorney-embed lawttorney-v2 lawttorney-chroma 2>/dev/null

# Remove systemd units
rm /etc/systemd/system/lawttorney-*.service
systemctl daemon-reload

# Reset Postgres config (drops our tuning, keeps data)
rm /etc/postgresql/16/main/conf.d/lawtech.conf
systemctl restart postgresql

# Reset sysctl / limits
rm /etc/sysctl.d/99-lawtech.conf /etc/security/limits.d/99-lawtech.conf
sysctl --system
```

The new box is now a clean(ish) Ubuntu 24.04. **The current prod box at `64.176.97.182` was never touched** — all traffic still flows there.

---

## 10. What's NOT done in Phase 1

- Multi-worker gunicorn (Phase 2)
- In-flight semaphore middleware (Phase 3)
- Rate-limit bump in code (Phase 4 — env value is already set, code default still 200)
- ChromaDB server-mode migration (Phase 5)
- New Prometheus metrics + alerts (Phase 6)
- Load test (Phase 7)
- nginx upstream flip — current prod still serves all traffic (Phase 8)

End-state of Phase 1: the new box runs the same single-worker config the old box does, with PG tuned, embed service running, and all systemd units installed but most of them disabled. It is **idle production-spec hardware ready for Phase 2**.

---

## 11. Open questions / decisions to revisit

1. **Backups**: provider snapshot daily + `pg_dump` to S3 nightly? (Recommended.) Not yet wired — track in Phase 6.
2. **Monitoring**: Grafana on this box or external? Phase 6 picks. Today, `/pyapi/metrics` exists but is unscraped.
3. **DNS / nginx fronting**: Still routes through the *old* box's nginx → port 5001 on localhost. Phase 8 will add a new upstream block pointing at the new box. Until then, the new box is reachable only via direct IP.
4. **Provider lock-in**: if Hetzner becomes attractive later, the only re-do is the VM + the IP allowlist in OpenSearch.

---

## 12. Files touched / created in Phase 1

| File | Purpose |
|---|---|
| [deploy/postgresql.lawtech.conf](../deploy/postgresql.lawtech.conf) | Tuned PG 16 snippet, installed into `/etc/postgresql/16/main/conf.d/lawtech.conf`. |
| [deploy/systemd/lawttorney-v2.service](../deploy/systemd/lawttorney-v2.service) | Gunicorn unit. Installed, not enabled (Phase 2 owns activation). |
| [deploy/systemd/lawttorney-embed.service](../deploy/systemd/lawttorney-embed.service) | Embedding microservice unit. Enabled and started by Phase 1. |
| [deploy/systemd/lawttorney-chroma.service](../deploy/systemd/lawttorney-chroma.service) | Chroma server unit. Installed, not enabled (Phase 5 owns activation). |
| [deploy/provision_new_host.sh](../deploy/provision_new_host.sh) | Idempotent bootstrap script. Run once per fresh host. |
| [docs/phase1_provision_runbook.md](phase1_provision_runbook.md) | This document. |

No application code is modified in Phase 1. The new box runs the **same code** the current prod box runs.
