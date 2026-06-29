# Incident — `/pyapi/health` reports unhealthy while prod is serving traffic (2026-06-29)

**Date:** 2026-06-29
**Surface:** `api.lawttorney.com` (AWS, 52.66.246.103, `lawttorney-v2.service`)
**Severity:** Low (UptimeRobot false-page; users unaffected for the most part — some streams stuck)
**Duration:** ~6+ hours of unhealthy status before detection; resolved by service restart at 06:22 UTC

## Symptom

```bash
$ curl https://api.lawttorney.com/pyapi/health
HTTP 503
{
  "status": "unhealthy",
  "checks": {
    "elasticsearch": {"status": "error", "detail": ""},
    "chat_store":    {"status": "error", "detail": ""},
    "checkpointer":  {"status": "ok",    "type": "postgresql", "latency_ms": 1},
    ...
  }
}
```

Two probes failing with **empty `detail`** strings. UptimeRobot alarmed.

## What was actually happening

1. **ES and Postgres were healthy.** Direct CLI probes from the prod box:
   - OpenSearch ping: **40 ms → 10 ms → 0 ms**
   - Same Postgres URL was passing the `checkpointer` probe at **1 ms** inside the same health request.
2. **Health probes had a 3.0 s timeout** in [core/gateway.py:1295-1297](../core/gateway.py#L1295) and [core/gateway.py:1381](../core/gateway.py#L1381):
   ```python
   await asyncio.wait_for(asyncio.to_thread(es.ping), timeout=3.0)
   ```
   5/5 health hits returned in **exactly ~6.00 s** = 3 s ES + 3 s chat_store, sequential.
3. **Empty `detail`** was the diagnostic clue: `str(asyncio.TimeoutError()) == ""`. Real ES/PG errors would have had non-empty messages (e.g. `ConnectionRefusedError(...)`, `OperationalError(...)`).
4. **Worker event loops were wedged.** Every single health request landed on the *same* worker PID (`437422`, the newest one). The other 7 workers weren't accepting connections — their event loops were stuck. Same worker was logging:
   ```
   ERR | [ChatRunner] | Stream timed out after 300s | steps=0 | duration_ms=300000
   ```
   — agent graph never advanced past step 0. Classic event-loop-blocked signature.
5. **Box itself was fine:** 0% CPU, load 1.20 on 8 vCPU, 12.9 / 31.7 GB used. Not resource-bound. The wedge was purely in worker scheduling.

So `/pyapi/health` was reporting **false negatives** for ES and chat_store: the upstream services were responsive; the probes were just losing the race against whatever was blocking the worker's event loop. With `str(e)` being empty, the alert message gave us nothing to grep on.

## Why the wedge happened (probable root cause, not yet fixed)

Two known issues already tracked in memory both feed into the same failure mode:

- **PDF pipeline timeout cascade** ([project_prod_slowness_2026_06_25.md](../memory/project_prod_slowness_2026_06_25.md)) — Drafting-with-Annexures chains 120 s → 60 s → 30 s timeouts that approach gunicorn's 300 s `worker_timeout`. When a worker survives just past the timeout, gunicorn doesn't kill it cleanly; the event loop is left in a bad state.
- **ChromaDB pool exhaustion** ([project_chroma_pool_exhaustion_2026_06_28.md](../memory/project_chroma_pool_exhaustion_2026_06_28.md)) — default SQLAlchemy pool (5 conn) vs 8 gunicorn workers; coroutines stack up waiting on pool acquisition. Code-level retry not yet shipped.

Box had been up **11.5 h** without restart; standard worker rotation had been masked because the 300 s `worker_timeout` was triggering before any natural recycle.

## What we did

### 1. Operational fix (2026-06-29 06:22 UTC)
```bash
sudo systemctl restart lawttorney-v2.service
```
Result — 32 s restart window, then:

| Check | Before | After |
|---|---|---|
| Overall | **unhealthy (HTTP 503)** | **healthy (HTTP 200)** |
| elasticsearch | error, detail="" | ok, 11 ms |
| chat_store | error, detail="" | ok, 1 ms |
| checkpointer | ok, 1 ms | ok, 0 ms |
| memory | 40.7% used | 22.5% used |
| uptime | 41,581 s | 49 s |
| Worker rotation | wedged on PID 437422 | 8 fresh workers |

### 2. Code mitigation (this PR)
Two changes in `core/gateway.py` so the next occurrence is visible and survives a slower scheduler:

- **Probe budget 3 s → 10 s** (env-overridable via `HEALTH_PROBE_TIMEOUT_S`). The wedge wasn't an upstream failure; we want the probe to *survive* event-loop hiccups so alerts only fire on actual outages.
- **Capture exception type in `detail`** — `f"{type(e).__name__}: {e}"[:200]` instead of `str(e)[:120]`. Stops `TimeoutError` (which has empty `str()`) from showing up as `detail: ""`.

This is **not a root-cause fix**. Probes will still time out if the event loop is wedged for >10 s; we'd then see `detail: "TimeoutError: "` instead of nothing.

## What still needs doing (not in this PR)

1. **Ship the ChromaDB retry** from [the 2026-06-28 note](../memory/project_chroma_pool_exhaustion_2026_06_28.md).
2. **Cap the PDF timeout chain** so the cumulative budget stays under gunicorn's `worker_timeout`. See [the 2026-06-25 investigation](../memory/project_prod_slowness_2026_06_25.md).
3. **Add a "worker rotation wedged" detector** — e.g., alert when one worker PID handles >80% of `/pyapi/health` 200s over 1 min. Would have caught this hours earlier.
4. **Consider a lightweight `/pyapi/health/live` (liveness only — process up, no upstream probes)** for UptimeRobot, separate from `/pyapi/health/ready` (the deep check). Current single endpoint conflates "the process is alive" with "all upstreams are reachable", and we end up paging on the latter when the former is what the LB actually needs.

## Timeline (UTC)

| Time | Event |
|---|---|
| 2026-06-28 ~18:44 | Last full service restart — workers spawned, fresh event loops |
| 2026-06-28 20:56–20:58 | Brief gunicorn worker-management activity in logs |
| 2026-06-29 ~03:07 | Worker 437422 spawned (replacement) |
| 2026-06-29 06:13:38 | First confirmed external 503 captured in this investigation |
| 2026-06-29 06:13–06:21 | Diagnostics: SSH'd in, confirmed ES/PG healthy, identified wedged workers + timeout false-positives |
| 2026-06-29 06:22:14 | `sudo systemctl restart lawttorney-v2.service` issued |
| 2026-06-29 06:22:44 | Healthy across the board, 8 fresh workers |
| 2026-06-29 06:23:13 | External probe confirms 200 OK |
