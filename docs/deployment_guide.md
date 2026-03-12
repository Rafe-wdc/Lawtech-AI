# Lawtech-AI — Server Deployment Guide

**Date**: 2026-03-12
**Applies to**: Any Ubuntu 22.04/24.04 VPS or bare-metal server

---

## Overview

This guide deploys the full Lawtech-AI production stack:

| Component | How it runs | Port |
|-----------|-------------|------|
| FastAPI app | gunicorn + 2 uvicorn workers | 5000 (internal) |
| PostgreSQL | Docker container | 5432 (internal) |
| Caddy | System service | 80, 443 |
| Prometheus | Docker container | 9090 |
| Grafana | Docker container | 3000 |

---

## Step 1 — Server Prerequisites

```bash
sudo apt update && sudo apt install -y \
    python3.12 python3.12-venv python3.12-dev \
    git sqlite3 curl build-essential

# Docker + Docker Compose
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in so docker group takes effect
```

---

## Step 2 — Clone the Repository

```bash
sudo mkdir -p /opt/lawtech
sudo chown $USER:$USER /opt/lawtech
git clone <your-repo-url> /opt/lawtech
cd /opt/lawtech
```

---

## Step 3 — Python Environment

```bash
cd /opt/lawtech
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Step 4 — Create Required Directories

```bash
mkdir -p data logs data/backups chroma_store uploads
```

> **Note**: Embedding models (`models/`) must be copied separately — they are not in the repo.
> Copy `bge-large-en-v1.5` and `all-MiniLM-L6-v2` into `/opt/lawtech/models/`.

---

## Step 5 — Configure Environment

```bash
cp .env.example .env
nano .env
```

Fill in all required values:

```env
# ── Required API Keys ─────────────────────────────────────────────────────────
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...

# ── Auth ──────────────────────────────────────────────────────────────────────
# Generate random keys: python3 -c "import secrets; print(secrets.token_hex(32))"
API_KEYS=<random-user-key>
ADMIN_API_KEY=<random-admin-key>

# ── CORS ──────────────────────────────────────────────────────────────────────
# Use your actual domain. Use * only for testing.
ALLOWED_ORIGINS=https://your-domain.com

# ── PostgreSQL ────────────────────────────────────────────────────────────────
POSTGRES_URL=postgresql://lawtech:changeme@localhost:5432/lawtech
POSTGRES_PASSWORD=changeme

# ── Optional ──────────────────────────────────────────────────────────────────
# ELASTICSEARCH_URL=http://139.84.219.174:9200
# RATE_LIMIT_PER_MINUTE=10
```

> **Security**: Generate strong keys with:
> ```bash
> python3 -c "import secrets; print(secrets.token_hex(32))"
> ```

---

## Step 6 — Start PostgreSQL

```bash
cd /opt/lawtech
docker compose -f deploy/docker-compose.prod.yml up -d postgres

# Wait ~10s, then verify it's healthy
docker ps
# Should show: lawtech-postgres   Up X seconds (healthy)
```

---

## Step 7 — Test App Startup (once before systemd)

```bash
cd /opt/lawtech
source venv/bin/activate
python -m uvicorn core.gateway:app --host 0.0.0.0 --port 5000
# Ctrl+C after confirming startup logs show no errors
```

Expected startup logs:
```
Startup: initializing checkpointer and compiling agent graph
PostgreSQL checkpointer initialized ...
Graph compiled successfully | nodes=18
Startup complete
```

---

## Step 8 — Create systemd Service

```bash
sudo nano /etc/systemd/system/lawtech.service
```

Paste the following (adjust `User` and paths if different):

```ini
[Unit]
Description=Lawtech-AI API Server
After=network.target docker.service
Requires=docker.service

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/lawtech
Environment="PATH=/opt/lawtech/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"
ExecStart=/opt/lawtech/venv/bin/gunicorn core.gateway:app \
    --workers 2 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:5000 \
    --timeout 300 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --access-logfile /opt/lawtech/logs/access.log \
    --error-logfile /opt/lawtech/logs/error.log
Restart=always
RestartSec=5
StandardOutput=append:/opt/lawtech/logs/app.log
StandardError=append:/opt/lawtech/logs/app.log

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable lawtech
sudo systemctl start lawtech

# Verify
sudo systemctl status lawtech
```

---

## Step 9 — Verify App is Running

```bash
# Health check (public endpoint — no key needed)
curl -s http://localhost:5000/pyapi/health | python3 -m json.tool
```

**What to check in the response:**

| Field | Expected | Notes |
|-------|----------|-------|
| `status` | `healthy` or `degraded` | `degraded` is OK (ES warn, memory warn) |
| `checks.checkpointer.type` | `postgresql` | If `in_memory`, check POSTGRES_URL in .env |
| `checks.elasticsearch.status` | `ok` | If `error`, ES is unreachable — app still works via web fallback |
| `checks.openai_key.status` | `ok` | Must be ok |
| `checks.google_key.status` | `ok` | Must be ok |

```bash
# Auth test — must return 401
curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:5000/pyapi/search \
  -H "Content-Type: application/json" \
  -d '{"Promptquery":"test"}'
# Expected: 401

# Functional test — replace YOUR_API_KEY with value from .env
curl -X POST http://localhost:5000/pyapi/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"Promptquery":"What is Section 302 IPC?","globalThreadId":"deploy-test-1"}'

# SCI routing test (was broken before fix)
curl -X POST http://localhost:5000/pyapi/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"Promptquery":"Supreme Court judgment on right to privacy","globalThreadId":"deploy-test-2"}'
# Check response metadata: "tasks_planned" should contain "SCI_Judgment"

# Admin endpoint test — must return 401 with user key
curl -s -o /dev/null -w "%{http_code}" \
  -H "X-API-Key: YOUR_API_KEY" \
  http://localhost:5000/pyapi/admin/usage_stats
# Expected: 401

# Admin endpoint with admin key — must return 200
curl -s -H "X-API-Key: YOUR_ADMIN_KEY" \
  http://localhost:5000/pyapi/admin/usage_stats | python3 -m json.tool
```

---

## Step 10 — Set Up HTTPS with Caddy

> Skip this step if you don't have a domain yet. The app runs fine on port 5000 over HTTP for testing.

```bash
# Install Caddy
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy

# Edit Caddyfile — replace your-domain.com with real domain
nano /opt/lawtech/Caddyfile

# Open firewall ports
sudo ufw allow 80
sudo ufw allow 443

# Start Caddy
caddy start --config /opt/lawtech/Caddyfile

# Verify HTTPS
curl -s https://your-domain.com/pyapi/health | python3 -m json.tool
```

---

## Step 11 — Start Monitoring Stack

```bash
# Update Prometheus config with your ADMIN_API_KEY
nano /opt/lawtech/monitoring/prometheus.yml
# Change:  credentials: "your-admin-key-here"
# To:      credentials: "YOUR_ACTUAL_ADMIN_API_KEY"

# Start Prometheus + Grafana
docker compose -f /opt/lawtech/monitoring/docker-compose.yml up -d

# Verify containers are running
docker ps | grep lawtech
```

**Access:**
- Grafana: `http://your-server-ip:3000` → Login: `admin` / `admin` → **change password on first login**
- Prometheus: `http://your-server-ip:9090/targets` → `lawtech-ai` should show **State = UP**

> **Firewall note**: Ports 3000 and 9090 should only be open to your IP, not the public internet.
> ```bash
> sudo ufw allow from YOUR_IP to any port 3000
> sudo ufw allow from YOUR_IP to any port 9090
> ```

---

## Step 12 — Set Up Daily Database Backup

```bash
# Make backup script executable
chmod +x /opt/lawtech/scripts/backup_db.sh

# Test it manually first
/opt/lawtech/scripts/backup_db.sh
# Should print: Backup created: chat_history_YYYYMMDD_HHMMSS.db.gz (X.XK)

# Add to cron (runs at 2 AM daily)
crontab -e
```

Add this line:
```
0 2 * * * /opt/lawtech/scripts/backup_db.sh >> /opt/lawtech/logs/backup.log 2>&1
```

```bash
# Verify backups exist
ls -lh /opt/lawtech/data/backups/
```

---

## Step 13 — Deploy Updates (after git push)

```bash
cd /opt/lawtech
git pull

# If requirements changed
source venv/bin/activate && pip install -r requirements.txt

# Restart app
sudo systemctl restart lawtech

# Verify it came back up
sudo systemctl status lawtech
curl -s http://localhost:5000/pyapi/health | python3 -m json.tool
```

---

## Final Verification Checklist

Run through each check before declaring the server ready:

```bash
# 1. App is running
sudo systemctl status lawtech | grep "Active:"

# 2. Health check passes
curl -s http://localhost:5000/pyapi/health | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print('STATUS:', d['status'], '| CHECKPOINTER:', d['checks'].get('checkpointer',{}).get('type','?'))"

# 3. Auth is enforced (must be 401)
curl -s -o /dev/null -w "Auth test (expect 401): %{http_code}\n" \
  -X POST http://localhost:5000/pyapi/search \
  -H "Content-Type: application/json" \
  -d '{"Promptquery":"test"}'

# 4. User key works (must be 200)
curl -s -o /dev/null -w "API key test (expect 200): %{http_code}\n" \
  -X POST http://localhost:5000/pyapi/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"Promptquery":"What is bail?","globalThreadId":"check-1"}'

# 5. Postgres checkpointer is active
curl -s http://localhost:5000/pyapi/health | python3 -c \
  "import json,sys; d=json.load(sys.stdin); cp=d['checks'].get('checkpointer',{}); print('Checkpointer:', cp.get('type'), cp.get('status'))"

# 6. Backup runs without error
/opt/lawtech/scripts/backup_db.sh

# 7. App survives restart
sudo systemctl restart lawtech && sleep 10 && \
  curl -s -o /dev/null -w "Post-restart health: %{http_code}\n" \
  http://localhost:5000/pyapi/health

# 8. Monitoring up (optional, if started)
curl -s -o /dev/null -w "Grafana: %{http_code}\n" http://localhost:3000
```

---

## Quick Reference — Key Commands

```bash
# Start / stop / restart app
sudo systemctl start lawtech
sudo systemctl stop lawtech
sudo systemctl restart lawtech

# View live logs
sudo journalctl -u lawtech -f
tail -f /opt/lawtech/logs/access.log

# Start / stop monitoring
docker compose -f /opt/lawtech/monitoring/docker-compose.yml up -d
docker compose -f /opt/lawtech/monitoring/docker-compose.yml down

# Start / stop postgres
docker compose -f /opt/lawtech/deploy/docker-compose.prod.yml up -d postgres
docker compose -f /opt/lawtech/deploy/docker-compose.prod.yml stop postgres

# Manual DB backup
/opt/lawtech/scripts/backup_db.sh

# List recent backups
ls -lht /opt/lawtech/data/backups/ | head -10
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| App won't start | Missing env var | Check `logs/app.log`, verify `.env` |
| `checkpointer: in_memory` | Postgres not running or wrong URL | `docker ps`, check `POSTGRES_URL` |
| All requests return 401 | `API_KEYS` env not set | Add `API_KEYS=...` to `.env`, restart |
| ES `error` in health check | Elasticsearch unreachable | App still works via web fallback |
| Prometheus target `down` | Wrong admin key in `prometheus.yml` | Update `credentials:` in `prometheus.yml`, restart Docker stack |
| Port 5000 already in use | Old process running | `lsof -i :5000`, kill old PID |
| Memory > 90% | Both embedding models loaded | Use remote embedding service or reduce workers to 1 |
