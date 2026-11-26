# Lawttorney v2 — Deployment Guide

> **Server**: 64.176.97.182 (Ubuntu 22.04, 16GB RAM, Python 3.10.12)
> **Domain**: tool.lawttorney.com
> **Last deployed**: 2026-02-25 (v2.4.5)

---

## Architecture Overview

```
Client (browser)
  │
  ▼
nginx (443 SSL)
  ├── /pyapi/*     → 127.0.0.1:5000  (v1 — Fast-api/)
  ├── /pyapiv2/*   → 127.0.0.1:5001  (v2 — Lawtech-AI/)
  └── /v2test.html → static frontend  (/var/www/html/v2test.html)
```

---

## Prerequisites on Server

- Ubuntu 22.04+ with Python 3.10+
- nginx with SSL (Certbot/Let's Encrypt)
- Embedding models at `/root/models/` (symlinked from `/root/Fast-api/models/` or `/root/Lawtech-AI/models/`)
  - `bge-large-en-v1.5` (~1.3GB)
  - `all-MiniLM-L6-v2` (~80MB)
- ChromaDB databases at `/root/Routing db/`
  - `constitution db`
  - `legal maximdb`
- Elasticsearch / OpenSearch accessible (AWS OpenSearch or self-hosted)

---

## Step-by-Step Deployment

### 1. SSH into the server

```bash
ssh root@64.176.97.182
```

### 2. Upload the v2 code

From your local machine:

```bash
# Create tarball (exclude caches and logs)
cd /path/to/Lawtech-AI
tar czf /tmp/Lawtech-AI.tar.gz \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache' \
    --exclude='logs' \
    --exclude='data' \
    --exclude='venv' \
    .

# Upload to server
scp /tmp/Lawtech-AI.tar.gz root@64.176.97.182:/root/Lawtech-AI.tar.gz
```

On the server:

```bash
cd /root
mkdir -p Lawtech-AI && tar xzf Lawtech-AI.tar.gz -C Lawtech-AI
rm Lawtech-AI.tar.gz
```

### 3. Symlink embedding models

The v2 settings expect models at `./models/` relative to the project root (`/root/Lawtech-AI/`). If models already exist at `/root/Fast-api/models/`, create a symlink:

```bash
ln -sf /root/Fast-api/models /root/models
```

Verify:

```bash
ls /root/models/
# Should show: all-MiniLM-L6-v2  bge-large-en-v1.5
```

### 4. Configure environment variables

Create `/root/Lawtech-AI/.env`:

```bash
cat > /root/Lawtech-AI/.env << 'EOF'
OPENAI_API_KEY="sk-..."
GOOGLE_API_KEY="AIza..."
PORT=5001
EOF
```

Required keys:
| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key (GPT-4o for orchestrator, metadata, drafting) |
| `GOOGLE_API_KEY` | Yes | Google API key (Gemini models for most agents) |
| `PORT` | No | Server port (default: 5000, set 5001 to avoid v1 conflict) |
| `ES_URL` | No | OpenSearch/ES URL. Default: `http://139.84.219.174:9200`. Falls back to `ELASTICSEARCH_URL`. |
| `ES_USER` | No | OpenSearch/ES username (required for AWS OpenSearch) |
| `ES_PASSWORD` | No | OpenSearch/ES password (required for AWS OpenSearch) |
| `S3_BUCKET` | No | Default: `lawttorney` |
| `S3_REGION` | No | Default: `ap-south-1` |
| `HOST` | No | Default: `0.0.0.0` |
| `LOG_LEVEL` | No | Default: `DEBUG` |

### 5. Create virtual environment and install dependencies

```bash
python3 -m venv /root/Lawtech-AI/venv
/root/Lawtech-AI/venv/bin/pip install --upgrade pip
/root/Lawtech-AI/venv/bin/pip install -r /root/Lawtech-AI/requirements.txt
```

This installs ~150 packages including LangChain 1.x, LangGraph, ChromaDB, sentence-transformers, PyTorch, FastAPI, etc.

### 6. Create the startup script

Create `/root/Lawtech-AI/run_v2.sh`:

```bash
cat > /root/Lawtech-AI/run_v2.sh << 'EOF'
#!/bin/bash
cd /root/Lawtech-AI
source venv/bin/activate
export $(grep -v "^#" .env | xargs)
export PORT=5001
mkdir -p logs data
nohup python -m uvicorn core.gateway:app \
    --host 0.0.0.0 \
    --port 5001 \
    --log-level info \
    --timeout-keep-alive 120 \
    --workers 1 \
    >> logs/api.log 2>&1 &
echo $! > logs/api.pid
echo "Started with PID $!"
EOF
chmod +x /root/Lawtech-AI/run_v2.sh
```

### 7. Start the v2 server

```bash
bash /root/Lawtech-AI/run_v2.sh
```

Verify it started:

```bash
# Check logs
tail -20 /root/Lawtech-AI/logs/api.log

# Expected output:
# ... | Compiling agent graph at startup
# ... | Graph compiled successfully | nodes=17
# INFO:     Uvicorn running on http://0.0.0.0:5001

# Health check
curl -s http://localhost:5001/pyapi/health
# {"status":"ok","version":"2.0.0"}
```

### 8. Configure nginx reverse proxy

Edit `/etc/nginx/sites-available/default` and add the v2 location block **before** the existing `/pyapi` block:

```nginx
    # API v2 (test) — proxies /pyapiv2/... to port 5001 /pyapi/...
    location /pyapiv2/ {
        rewrite ^/pyapiv2/(.*) /pyapi/$1 break;
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 600s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;
    }

    # API v1 (existing — keep as is)
    location /pyapi {
        proxy_pass http://127.0.0.1:5000;
        ...
    }
```

Test and reload:

```bash
nginx -t
systemctl reload nginx
```

Verify external access:

```bash
curl -s https://tool.lawttorney.com/pyapiv2/health
# {"status":"ok","version":"2.0.0"}
```

### 9. Deploy the frontend test page

Copy the frontend HTML to the nginx web root, replacing API paths to point to v2:

```bash
cp /root/Lawtech-AI/frontend.html /var/www/html/v2test.html

# Replace default API base URL
sed -i 's|http://localhost:5055|https://tool.lawttorney.com|g' /var/www/html/v2test.html

# Rewrite all /pyapi/ paths to /pyapiv2/
sed -i 's|/pyapi/|/pyapiv2/|g' /var/www/html/v2test.html
```

Frontend is now accessible at: `https://tool.lawttorney.com/v2test.html`

---

## Operations

### Restart the server

```bash
kill $(cat /root/Lawtech-AI/logs/api.pid)
bash /root/Lawtech-AI/run_v2.sh
```

### View live logs

```bash
tail -f /root/Lawtech-AI/logs/api.log
```

### Check server status

```bash
# Is the process running?
ps -p $(cat /root/Lawtech-AI/logs/api.pid 2>/dev/null) 2>/dev/null && echo "RUNNING" || echo "STOPPED"

# Port check
lsof -i :5001

# Health check
curl -s http://localhost:5001/pyapi/health
```

### Stop the server

```bash
kill $(cat /root/Lawtech-AI/logs/api.pid)
```

### Redeploy updated code

From your local machine:

```bash
# Repackage and upload
cd /path/to/Lawtech-AI
tar czf /tmp/Lawtech-AI.tar.gz \
    --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='.pytest_cache' --exclude='logs' \
    --exclude='data' --exclude='venv' --exclude='.env' \
    .

scp /tmp/Lawtech-AI.tar.gz root@64.176.97.182:/root/

# On the server:
ssh root@64.176.97.182
kill $(cat /root/Lawtech-AI/logs/api.pid)
cd /root
# Preserve venv, .env, logs, data
cp -r Lawtech-AI/venv /tmp/v2_venv_backup
cp Lawtech-AI/.env /tmp/v2_env_backup
tar xzf Lawtech-AI.tar.gz -C Lawtech-AI
mv /tmp/v2_venv_backup Lawtech-AI/venv
cp /tmp/v2_env_backup Lawtech-AI/.env
rm Lawtech-AI.tar.gz
bash /root/Lawtech-AI/run_v2.sh
```

---

## Background runs (answers keep going after the app closes)

Off by default. With `BACKGROUND_RUNS=on`, `/pyapi/chat` and
`/pyapi/search/stream` stop generating inside the HTTP request. They queue a
*run* in Postgres and stream its stored events; a separate **runner**
process (`workers/run_worker.py`) generates the answer. Closing the app, a
dropped connection, or gunicorn recycling a worker only ends the stream. The
app can reattach and gets everything it missed. When the run finishes, Python
tells Node (`RUN_CALLBACK_URL`), which stores the turn and sends a push if
nobody was watching. Design: `Background_Runs_Implementation_Plan.md`.

### Processes

| Process | Unit | Does |
|---|---|---|
| Web (FastAPI under gunicorn) | `lawttorney-v2` | auth, queue the run, stream stored events, reattach, Stop |
| Runner | `lawttorney-runner@1` (template; `@2` adds one) | claims runs, runs the unchanged pipeline, saves the turn, notifies Node |
| Postgres | `postgresql` | `runs`, `run_events`, `run_watchers`, `run_outbox`, `run_workers` (created on first use) |

The runner has no HTTP port. It needs the same `.env`, `models/` and
`POSTGRES_URL` as the web tier. If no runner has reported alive for 45 s,
the web tier answers inline as before and logs `runner_unavailable_fallback`.

### Settings (.env)

| Setting | Default | Meaning |
|---|---|---|
| `BACKGROUND_RUNS` | `off` | `on` routes chat and stream through the runner |
| `RUNNER_CONCURRENCY` | `8` | runs one runner works on at once (prod peak is ~8-10 in flight) |
| `RUN_LEASE_S` | `60` | runner lease; renewed every 15 s; a dead runner's runs are recovered after it expires |
| `RUN_CALLBACK_URL` | empty | Node run-complete endpoint; empty = nothing is sent |
| `RUN_CALLBACK_SECRET` | empty | sent as `X-Service-Secret` |
| `USER_AUTH_MODE` | `off` | `optional` / `required`: verify the Node login JWT (`USER_JWT_SECRET`) |
| `USER_JWT_SECRET` | empty | HS256 key that verifies the app's user token |

Others (`RUN_TOKEN_FLUSH_MS`, `RUN_WATCHER_TTL_S`, `RUN_EVENT_RETENTION_DAYS`,
`RUN_QUEUE_MAX_WAIT_S`, `RUNNER_WARMUP`, `USER_JWT_*`) are documented in
`core/settings.py`.

### Memory

The runner loads its own copy of the embedding models (~1.4 GB; gunicorn's
copy is shared by its workers and does not help) plus libraries: about
2.5-3 GB idle. Prod had 5.5 GB available on 2026-11-25. The unit caps the
runner at `MemoryHigh=4500M`, `MemoryMax=5500M`. Before switching prod on,
measure on dev (`systemctl status lawttorney-runner@1` shows memory) and
consider `GUNICORN_WORKERS=3`: the web workers no longer generate.

### Switching it on (dev first)

1. Deploy. The workflow installs `lawttorney-runner@.service`, enables `@1`
   and restarts it. With the flag off it exits at once (inactive is correct).
2. In `.env`: `BACKGROUND_RUNS=on` (and later `RUN_CALLBACK_URL`,
   `RUN_CALLBACK_SECRET`, `USER_AUTH_MODE`, `USER_JWT_SECRET`).
3. `systemctl restart lawttorney-runner@1 lawttorney-v2` (dev: rerun the
   deploy, or start `python -m workers.run_worker` as the deploy does).
4. `curl -s localhost:5001/pyapi/health/runs` must show `"status":"ok"`,
   `"runners":1`, `"listener_connected":true` once a stream has been opened.
5. `python scripts/background_runs_smoke.py --base http://127.0.0.1:5001`
   (real questions; checks disconnect + reattach, 409 on a busy thread, Stop).
   Then by hand with `--pause-for-restart`: restart `lawttorney-v2` while it
   waits, then the runner, and confirm the answer still completes.

Rollback: `BACKGROUND_RUNS=off`, restart `lawttorney-v2`. Runs in flight
finish in the runner; it then exits.

### Operations

```bash
curl -s localhost:5001/pyapi/health/runs           # runners, queue, outbox
journalctl -u 'lawttorney-runner@*' -f              # runner log
systemctl restart lawttorney-runner@1               # drains (<= 540 s), then restarts
systemctl enable --now lawttorney-runner@2          # second runner (~3 GB more RAM)
# a run-complete message Node never accepted:
curl -s -X POST -H "X-API-Key: $ADMIN_API_KEY" localhost:5001/pyapi/admin/runs/outbox/<id>/retry
```

Grep for: `runner_unavailable_fallback` (no runner; answered inline),
`Lease lost` (Stop or a runner that stalled), `Sweep:` (recovery after a
runner died), `outbox_dead` (Node rejected or was unreachable for 24 h),
`run_completed` (one line per run: events, token batches, persist p95,
queue wait, duration). Prometheus: `lawtech_background_runs_*`,
`lawtech_background_run_event_delay_seconds`.

### Endpoints

| Method and path | Purpose |
|---|---|
| `POST /pyapi/chat`, `POST /pyapi/search/stream` | unchanged request; SSE starts with `thread_id`, then `run_id`; every event carries `seq` |
| `GET /pyapi/runs/{id}/stream?after=N` | reattach: replay after N, then live |
| `GET /pyapi/runs/{id}` | status |
| `GET /pyapi/threads/{id}/active-run` | the thread's run in progress, else its latest |
| `POST /pyapi/runs/{id}/cancel` | Stop (thread free at once) |
| `GET /pyapi/health/runs` | runner and queue health |

A second question on a thread that is still answering gets **409** with
`active_run_id`; the app should attach to that run.

---

## Directory Layout on Server

```
/root/
├── .env                          # v1 API keys (not used by v2)
├── Fast-api/                     # v1 deployment (port 5000)
│   └── models/                   # Embedding models (shared)
│       ├── bge-large-en-v1.5/
│       └── all-MiniLM-L6-v2/
├── models -> /root/Fast-api/models  # Symlink for v2
├── Routing db/                   # ChromaDB databases (shared)
│   ├── constitution db/
│   └── legal maximdb/
├── Lawtech-AI/                   # v2 deployment (port 5001)
│   ├── .env                      # v2 API keys + PORT=5001
│   ├── run_v2.sh                 # Startup script
│   ├── venv/                     # Python virtual environment
│   ├── logs/
│   │   ├── api.log               # Server logs
│   │   └── api.pid               # PID file
│   ├── data/
│   │   └── chat_history.db       # SQLite chat store
│   ├── core/                     # FastAPI gateway, graph, settings
│   ├── agents/                   # 10 LangGraph agents
│   ├── tools/                    # 54 shared + inline tools
│   ├── config/                   # Prompts, settings
│   ├── workers/                  # PDF background processor
│   └── services/                 # Embedding microservice
└── /var/www/html/
    └── v2test.html               # Frontend test console
```

---

## Access URLs

| Resource | URL |
|----------|-----|
| v2 API (test) | `https://tool.lawttorney.com/pyapiv2/` |
| v2 Health check | `https://tool.lawttorney.com/pyapiv2/health` |
| v2 Frontend | `https://tool.lawttorney.com/v2test.html` |
| v1 API (production) | `https://tool.lawttorney.com/pyapi/` |

---

## Fixes Applied During Deployment

| Issue | Fix |
|-------|-----|
| `search_by_semantic` renamed to `search_by_topic` (ISSUES.md H3) but `agents/sci_judgment.py` still imported old name | Updated import and all 3 references in `sci_judgment.py` to use `search_by_topic` |

---

## Known Limitations

- **No auto-restart**: Server does not restart on crash or reboot. Consider adding a systemd service (see below).
- **Single worker**: Running with `--workers 1` to avoid model loading per-worker. Scale via reverse proxy to multiple instances instead.
- **No log rotation**: `logs/api.log` grows unbounded. Add `logrotate` config for production.

### Optional: systemd service for auto-restart

```bash
cat > /etc/systemd/system/lawttorney-v2.service << 'EOF'
[Unit]
Description=Lawttorney v2 API Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/Lawtech-AI
EnvironmentFile=/root/Lawtech-AI/.env
ExecStart=/root/Lawtech-AI/venv/bin/python -m uvicorn core.gateway:app --host 0.0.0.0 --port 5001 --log-level info --timeout-keep-alive 120 --workers 1
Restart=on-failure
RestartSec=5
StandardOutput=append:/root/Lawtech-AI/logs/api.log
StandardError=append:/root/Lawtech-AI/logs/api.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable lawttorney-v2
systemctl start lawttorney-v2
systemctl status lawttorney-v2
```
