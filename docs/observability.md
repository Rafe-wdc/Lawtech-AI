# Lawtech-AI Observability Plan

## The Pyramid — Baby Steps

```
                    ▲
                   /L4\          ← Business KPIs         (future)
                  /────\
                 /  L3  \        ← LLM / Agent quality   (future)
                /────────\
               /    L2    \      ← Application health    (next)
              /────────────\
             /      L1      \    ← Is the system alive?  ✅ DONE
            /────────────────\
```

Each level only gets built after the one below is stable.

---

## Level 1 — "Is It Alive?" ✅

**Goal: Know when the system is DOWN before users tell you.**

### What was implemented

**Endpoint**: `GET /pyapi/health`

Returns a full system health check with individual component status.

**Checks performed:**
| Check | What it tests | Timeout |
|-------|--------------|---------|
| Elasticsearch | Cluster `ping()` to ES URL | 3s |
| OpenAI API key | Key present in env (no API call) | instant |
| Google API key | Key present in env (no API call) | instant |
| Memory (RAM) | `psutil` — warns if >85%, critical if >95% | instant |
| Disk space | `shutil` — warns if >80%, critical if >95% | instant |
| SQLite DB | Write + delete a test row to `chat_history.db` | 2s |

**Response format:**
```json
{
  "status": "healthy",          // healthy | degraded | unhealthy
  "version": "2.0.0",
  "uptime_seconds": 3600,
  "timestamp": "2026-03-12T10:00:00",
  "checks": {
    "elasticsearch": { "status": "ok", "latency_ms": 12 },
    "openai_key":    { "status": "ok" },
    "google_key":    { "status": "ok" },
    "memory":        { "status": "ok", "used_pct": 45.2, "available_gb": 6.1 },
    "disk":          { "status": "ok", "used_pct": 38.1, "free_gb": 120.4 },
    "sqlite":        { "status": "ok", "latency_ms": 3 }
  }
}
```

**HTTP status codes:**
- `200` — all checks pass (`healthy`)
- `200` — some checks warn but system is functional (`degraded`)
- `503` — critical check failed (`unhealthy`) → triggers UptimeRobot alert

### UptimeRobot Setup (free)

1. Create account at https://uptimerobot.com (free tier: 50 monitors, 5-min intervals)
2. Add monitor: `HTTP(s)` → URL: `https://your-domain/pyapi/health`
3. Set alert: email + Slack webhook on `503` response or timeout
4. The `503` response on critical failure ensures UptimeRobot fires the alert

---

## Level 2 — "Is It Healthy?" ✅

**Goal: Know when something is slow or breaking per endpoint/agent.**

### What was implemented

**`core/metrics.py`** — shared Prometheus metric definitions:
| Metric | Type | Labels |
|--------|------|--------|
| `lawtech_requests_total` | Counter | endpoint, method, status_code |
| `lawtech_request_latency_seconds` | Histogram | endpoint |
| `lawtech_agent_invocations_total` | Counter | agent |
| `lawtech_agent_latency_seconds` | Histogram | agent |
| `lawtech_agent_errors_total` | Counter | agent |
| `lawtech_fallback_total` | Counter | agent, tier (query_rewrite / web_search) |
| `lawtech_guardrail_blocks_total` | Counter | stage (input / output) |
| `lawtech_llm_tokens_total` | Counter | model, token_type |
| `lawtech_tasks_planned_total` | Counter | task_type |
| `lawtech_active_requests` | Gauge | — |

**`GET /pyapi/metrics`** — Prometheus text-format endpoint (scraped every 15s)

**HTTP middleware** in `gateway.py` — records every request's latency + status code automatically.

**Fallback metrics** wired into `core/agent_fallback.py` — fires on query_rewrite and web_search tiers.

**`monitoring/`** directory:
- `docker-compose.yml` — Prometheus + Grafana (starts with one command)
- `prometheus.yml` — scrape config pointing at `host.docker.internal:5000`
- `grafana/dashboards/lawtech.json` — 8-panel dashboard (requests, errors, latency, agents, fallbacks, tasks)

### How to start monitoring

```bash
docker compose -f monitoring/docker-compose.yml up -d
```

- Grafana: http://localhost:3000 (admin / admin)
- Prometheus: http://localhost:9090
- Metrics endpoint: http://localhost:5000/pyapi/metrics

---

## Level 3 — "Where Is It Hurting?" (Future)

**Goal: Per-agent visibility — token usage, cost, slow nodes.**

Planned:
- `request_log` SQLite table (user → agents → cost in one row)
- Langfuse OSS traces (LangGraph node-level latency + tokens)
- Daily cost report (GPT-4o + Gemini breakdown)

**Status: NOT YET BUILT**

---

## Level 4 — "Why Is It Hurting?" (Future)

**Goal: Improve answer quality, not just fix outages.**

Planned:
- Automated LLM-as-judge quality scoring (faithfulness, relevance)
- Data gap analysis: cluster web fallback queries → weekly backfill report
- User satisfaction correlation with agent/model

**Status: NOT YET BUILT**
