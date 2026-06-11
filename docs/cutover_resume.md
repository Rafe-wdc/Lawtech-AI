# Cutover Resume Checklist

> **Paused:** 2026-06-11. Pick up from here when ready.
>
> **Where we are:** AWS new box (`52.66.246.103`) is fully built, Phase 7 fixes deployed, nginx configured for `api.lawttorney.com` (HTTP-only). **No public traffic on it yet** — production users still hit Vultr (`64.176.97.182`) via the `185.38.109.x` proxy cluster. Nothing user-facing is broken; we just stopped before the DNS / TLS swap.
>
> **Full context:** [scale_50_concurrent_users_plan.md](scale_50_concurrent_users_plan.md), [phase1_provision_runbook.md](phase1_provision_runbook.md).

---

## 1. Production cutover (the immediate next step)

### Your side (do these in parallel)

- [ ] **Add DNS A-record** at whoever manages `lawttorney.com` DNS:
      ```
      Type:  A
      Name:  api
      Value: 52.66.246.103
      TTL:   300
      ```
      Verify: `nslookup api.lawttorney.com 8.8.8.8` → `52.66.246.103`. Propagation 1-5 min.

- [ ] **Open port 443 inbound** in AWS Security Group for instance `52.66.246.103`:
      AWS Console → EC2 → Instances → select instance → Security → click the SG → Edit inbound rules → Add:
      ```
      Type:        HTTPS
      Protocol:    TCP
      Port:        443
      Source:      0.0.0.0/0
      ```
      Keep port 80 open — Certbot needs it for the ACME challenge.

### My side (once both above are done, takes ~5 min)

- [ ] SSH to box, run `sudo certbot --nginx -d api.lawttorney.com --redirect`. Certbot edits the nginx config in-place to add the 443 server block + http→https redirect. Renewal auto-runs via systemd timer.
- [ ] Add `TRUSTED_PROXY_IPS=127.0.0.1` to `/root/Lawtech-AI/.env`. Activates the Phase 4 `ProxyHeadersMiddleware` so the app sees real client IPs from `X-Forwarded-For` instead of `127.0.0.1`. Restart v2.
- [ ] Public smoke test:
      - `curl -s https://api.lawttorney.com/pyapi/health | jq .` (should match the existing /pyapiv2/health response)
      - `curl -N -H "X-API-Key: $USER_KEY" -d '{"Promptquery":"Section 9 CPC"}' https://api.lawttorney.com/pyapi/search/stream` (should SSE-stream)
- [ ] Verify metrics: `curl -H "X-API-Key: $ADMIN_KEY" https://api.lawttorney.com/pyapi/metrics | head` (should show aggregated counters from all 6 workers).

### Your side (after my side passes)

- [ ] **Frontend cutover** — update the React app's API base URL from
      `https://tool.lawttorney.com/pyapiv2/` (or current value) → `https://api.lawttorney.com/pyapi/`
      Likely a single constant in `src/config.js` or similar. Deploy frontend; verify in browser.
- [ ] Watch for 24 h. If anything wrong: revert frontend to old base URL. The old Vultr backend stays running so rollback is a one-line change.

---

## 2. Vultr test-box provisioning (deferred)

Goal: turn `64.176.97.182` (Vultr, 16 GB) into a real v2 staging environment matching the AWS prod box, so the team can deploy to test → validate → ship to prod.

### Pre-work needed from you

- [ ] **SSH access to Vultr** — memory says password auth, password was previously shared but considered compromised. Either rotate password and share, or set up key auth and add a public key.
- [ ] Decide whether the OLD `Fast-api` on `/root/Fast-api/` (currently running on pm2) needs to be preserved as a fallback or can be retired entirely.

### My side (~1 hour once SSH is available)

- [ ] Inventory current state on Vultr: services, disk, RAM, what pm2 is running.
- [ ] Stop old pm2 services if approved (`pm2 stop fastapi-app lawtechtest`).
- [ ] Clean up `/root/Lawtech-AI` directory (currently has stale older v2 from memory).
- [ ] Run `deploy/provision_new_host.sh` with scaled-down env values for 16 GB / 4 vCPU:
      ```
      GUNICORN_WORKERS=4
      EMBEDDING_SERVICE_WORKERS=3
      MAX_INFLIGHT_PER_WORKER=8
      ```
      And tune Postgres for 16 GB (`shared_buffers=4GB`, `effective_cache_size=12GB`).
- [ ] Deploy v2 + chroma + embed services (same systemd units, scaled).
- [ ] Decide DNS for test: `test.lawttorney.com` → `64.176.97.182`? Then Certbot on Vultr too.
- [ ] Document the deploy-to-test-first workflow somewhere visible (e.g. CLAUDE.md or a CONTRIBUTING.md).

---

## 3. Decommissioning (later, after stability proves out)

- [ ] **AWS old v1 box** (`13.233.251.147`) — `/pyapi/*` already 502s, v1 is decommissioned. Once you're sure nothing references it: terminate the instance. Saves ~$190/mo.
- [ ] **Old SSH key cleanup** — the `LawTech_LLM (1).pem` was deleted from `mainserver/` earlier. If kept anywhere else (laptop, password manager), prune.
- [ ] **Old Vultr `/root/Fast-api/`** — once Vultr is repurposed as v2 test, the OLD Fast-api directory can go.

---

## 4. Code hygiene (low priority, before the next round of changes)

Currently the **Phase 2-7 code changes live ONLY on the AWS box** (deployed via scp), not on git master. The full list of unpushed work:

- [ ] **gunicorn.conf.py** — env-tunable workers / daemon / accesslog / errorlog, plus `keepalive=75`, `backlog=4096`, `child_exit` hook for Prometheus multiproc cleanup.
- [ ] **core/clients.py** — `get_chroma_client()` singleton.
- [ ] **core/embedding_client.py** — `aembed_query` / `aembed_documents` via `httpx.AsyncClient`.
- [ ] **core/retrieval_relevance.py** — `_coarse_semantic_floor_async` using `aembed_query`.
- [ ] **core/gateway.py** — `inflight_gate` middleware, `ProxyHeadersMiddleware`, multiproc `MultiProcessCollector` in `/pyapi/metrics`, ChromaDB server-aware delete endpoints.
- [ ] **core/metrics.py** — multiprocess_mode on Gauges, 3 new gate metrics.
- [ ] **core/file_processor.py** — Chroma server-mode integration.
- [ ] **agents/document.py** — Chroma server-mode integration.
- [ ] **tools/shared/vectordb_tools.py**, **tools/shared/document_tools.py** — Chroma server-mode.
- [ ] **deploy/systemd/lawttorney-embed.service** — uvicorn CLI + `OMP_NUM_THREADS=1` etc.
- [ ] **deploy/systemd/lawttorney-v2.service** — `PROMETHEUS_MULTIPROC_DIR`, `RuntimeDirectory`.
- [ ] **deploy/nginx/api.lawttorney.com.conf** — new file, the nginx site config.
- [ ] **tests/test_inflight_gate.py**, **tests/test_embedding_async.py** — new tests, all green.

Plus 19 unrelated local modifications from before this work started (agents/drafting.py, agents/orchestrator.py, etc.). Those should be separate commits.

When you're ready: I can craft 4-6 focused commits that group these by phase. The current working state was validated under the capacity test (98.8 % success at 70 concurrent), so committing it as-is is safe.

---

## 5. Quick reference

### IPs + roles

| Host | IP | Role | SSH key |
|---|---|---|---|
| AWS new prod | `52.66.246.103` | Built v2, target for `api.lawttorney.com` | `mainserver/lawtech-ai-v2-key.pem` |
| Vultr test/current prod | `64.176.97.182` | Currently serves `/pyapiv2/` via `185.38.109.x` proxy. Will become test box. | password auth (rotate) |
| AWS old v1 | `13.233.251.147` | v1 decommissioned (returns 502) | key deleted, decommission candidate |
| Unknown proxy cluster | `185.38.109.200-209` | Currently fronts `tool.lawttorney.com` | provider unknown |

### Costs while paused

- AWS new box (m5.2xlarge on-demand): **~$10/day = ~$340/mo**
- Vultr test box: depends on current plan (~$80/mo for 16 GB)
- AWS old v1: ~$6/day = ~$190/mo (decommission target)

### Open security exposure on new box

- Port 80 open externally. Anyone with the IP can hit `http://52.66.246.103/pyapi/health` and see the unauthenticated health JSON (key status, disk/RAM %, ES latency). Not a credential leak, but a probe surface.
- If you want zero exposure during the pause, ssh in and:
  ```bash
  sudo ufw deny in from any to any port 80
  sudo ufw reload
  ```
  Re-allow with `sudo ufw allow 80/tcp` before resuming Certbot.

### Service names on the AWS new box

```
lawttorney-v2.service       (gunicorn + 6 uvicorn workers, port 5001)
lawttorney-embed.service    (BGE-large + MiniLM, port 5100, 6 workers + OMP=1)
lawttorney-chroma.service   (ChromaDB server, 127.0.0.1:8000)
```

Status check: `for s in lawttorney-{v2,embed,chroma}; do sudo systemctl is-active $s; done`

### Key files modified this session (on AWS box, not on git master)

See section 4 above.
