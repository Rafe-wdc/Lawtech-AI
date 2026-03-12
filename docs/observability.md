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

## Level 3 — "Where Is It Hurting?" ✅

**Goal: Per-agent visibility — token usage, cost, slow nodes.**

### What was implemented

**`request_log` SQLite table** — every request logged (fire-and-forget):
- `thread_id`, `endpoint`, `query_preview`, `user_language`
- `tasks_planned_json`, `agents_used_json`, `total_latency_ms`
- `total_tokens`, `estimated_cost_usd`, `fallback_used`, `is_blocked`

**Cost estimation** per-agent blended rates ($/1K tokens):
- Scenario / Document: $0.01 (Gemini Pro)
- Judgment / SCI: $0.0025 (GPT-4o + Gemini Flash)
- Legislation / Newacts: $0.0015 (GPT-4o-mini + Gemini Flash)
- Others: $0.00015 (Gemini Flash Lite)

**`GET /pyapi/admin/usage_stats?days=7`** — totals, per-day breakdown, top agents, task distribution

**Grafana** — 2 new panels: Tokens/hour + Fallback rate % (web vs rewrite)

---

## Level 4 — "Why Is It Hurting?" ✅

**Goal: Improve answer quality, not just fix outages.**

### What was implemented

**`core/quality.py`** — LLM-as-judge scorer (Gemini Flash Lite):
- Samples 10% of responses (fire-and-forget, never blocks response)
- Skips: greetings, blocked queries, Drafting, Document agents
- Scores 3 dimensions: `faithfulness`, `relevance`, `completeness` (0.0–1.0)
- Persists to `quality_log` SQLite table

**`quality_log` SQLite table** — per-scored-response record:
- `agent`, `query_preview`, `faithfulness`, `relevance`, `completeness`, `avg_score`

**`GET /pyapi/admin/quality_stats?days=7`** — quality analytics:
- Overall averages, per-agent breakdown, daily trend, low-quality response queue

**Prometheus metrics** (4 new gauges/counters):
- `lawtech_quality_avg_score`, `lawtech_quality_faithfulness`, `lawtech_quality_relevance`
- `lawtech_quality_low_count` — alert fires when score < 0.6

**Grafana** — 2 new panels: Quality scores timeseries + Low quality alert stat

**`scripts/gap_report.py`** — ES backfill intelligence:
```bash
python scripts/gap_report.py           # last 7 days
python scripts/gap_report.py --days 30 --agent Judgment
```
- Reads `fallback_log`, clusters queries by topic (word-overlap)
- Surfaces top ES gaps to backfill (e.g. "3 queries for NDPS bail hit web fallback")
- Auto-saves to `docs/gap_report.md`
