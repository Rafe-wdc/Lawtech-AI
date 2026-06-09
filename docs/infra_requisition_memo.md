# Infrastructure Requisition — Production Host for Lawtech-AI v2

**To:** Infrastructure / Cloud Admin
**From:** Adesh Raj <adesh.raj@lamipak.biz>
**Date:** 2026-06-09
**Priority:** High — blocks v2 capacity launch
**Required by:** Within 5 working days of approval

---

## Summary

Requesting one new EC2 instance (~₹28,000 / $340/month) plus minor IAM and security-group changes to host the Lawtech-AI v2 production stack at its design capacity of **50 concurrent active users**. The current v2 host cannot meet this target without horizontal compute, and the existing v1 host (`13.233.251.147` / `r5.xlarge`) is already at 69% disk and 65% memory under v1 load alone.

This is a **dedicated EC2 instance** in our existing AWS account and Mumbai region. **No new managed services** (RDS, ElastiCache, MSK, etc.) are required — all support services (Postgres, ChromaDB, Redis, embedding microservice) run on the same host.

---

## 1. Business justification

Lawtech-AI v2 is the next-generation multi-agent legal AI backend (FastAPI + LangGraph + Gemini/GPT-4o). It serves Drafting, Scenario analysis, Judgment/Legislation lookup, and PDF Q&A to the Lawttorney product.

Current v2 production:
- Runs a **single uvicorn worker**, which can comfortably handle ~10 concurrent active streams.
- Beyond that, p95 latency degrades sharply and drafting requests start timing out.

Scaling target — agreed product requirement:
- **50 concurrent active streams** sustained.
- p95 time-to-first-token < 5 s, p95 time-to-completion < 60 s (non-drafting).
- Zero 5xx during sustained peak; graceful 503 with `Retry-After` at saturation.

The full scaling plan is documented at `docs/scale_50_concurrent_users_plan.md` (8 phases). This requisition unblocks Phase 1 (provisioning).

---

## 2. Why a separate dedicated host

We considered re-using `13.233.251.147`:

| Path | Pros | Cons |
|---|---|---|
| **New dedicated host (this request)** | Zero risk to v1. Clean roll-forward / roll-back. v1 and v2 fully decoupled. | One more EC2 instance to operate. |
| Resize `13.233.251.147` in place | Saves ~$200/mo of EC2 spend. | Requires v1 downtime (~30 min for instance resize), and v1 + v2 then compete for the same CPU/RAM under load. |
| Use as-is at 4 vCPU | Cheapest. | Cuts the concurrency target from 50 → ~25 and leaves no headroom. Misses the product commitment. |

**Recommendation: provision a new host.** Path 2 couples two independent risks (v1 stability + v2 capacity launch) and Path 3 misses the headline number we committed to.

---

## 3. Required spec — one EC2 instance

| Item | Spec | Rationale |
|---|---|---|
| **Instance type** | `m5.2xlarge` (8 vCPU, 32 GB RAM) | 8 cores → 6 gunicorn workers + 2 cores reserved for embedding service, ChromaDB, Postgres. 32 GB RAM fits app workers (~6 GB) + embedding service (~3 GB) + Postgres `shared_buffers=8 GB` + OS cache. |
| **Region / AZ** | `ap-south-1a` (Mumbai) | Same region as existing OpenSearch cluster and S3 bucket `lawttorney`. Cross-region traffic would double LLM-call latency and add data-transfer cost. |
| **OS** | Ubuntu Server 24.04 LTS (HVM, EBS-backed) | Native Python 3.12 (project requires 3.12+); supported until April 2029. Avoids deadsnakes PPA on 22.04. |
| **Root volume** | 200 GB `gp3` EBS, 3000 IOPS / 125 MB/s baseline | App (~1 GB) + embedding models (~1.4 GB) + Postgres data (grows ~2 GB/month at current chat volume) + ChromaDB (existing 6,676 collections, ~5 GB and growing) + PDF uploads buffer (~50 GB working set) + logs + WAL. 200 GB gives 12–18 months runway. |
| **VPC / subnet** | Existing VPC, public subnet (same as v1 host) | Needs outbound internet (OpenAI, Gemini), Inbound 443/80 for nginx, internal route to OpenSearch. |
| **Backups** | Daily EBS snapshot, 7-day retention | Disaster recovery. (We additionally do nightly `pg_dump` to S3 at the app layer.) |
| **Termination protection** | Enabled | Prevents accidental delete. |

### Alternative if cost is the deciding factor
`r5.xlarge` (4 vCPU / 32 GB) would cut compute cost to ~$190/mo but only delivers a ~25-stream ceiling. Not recommended.

---

## 4. Required IAM

Attach an instance profile (IAM role for EC2) with:

| Action | Resource | Reason |
|---|---|---|
| `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket` | `arn:aws:s3:::lawttorney/*` and `arn:aws:s3:::lawttorney` | Read judgment PDFs, write PDF backups. |
| `es:ESHttp*` (or AWS OpenSearch fine-grained access via existing master user) | OpenSearch domain ARN | Read/write across `legislation`, `judgements`, `drafting`, `newacts_v1`, `supreme_court_judgement`, `constitution`, `legal_maxims` indices. |
| (optional) `cloudwatch:PutMetricData` | `*` | If we use CloudWatch metrics in Phase 6 alongside Prometheus. |

**Simplest path:** reuse the IAM policy already attached to `13.233.251.147` if it exists. Otherwise create a fresh role following the actions above (least-privilege).

---

## 5. Required networking

### Security group inbound rules

| Port | Protocol | Source | Purpose |
|---|---|---|---|
| 22 | TCP | Corporate VPN range / engineering IPs only | SSH (admin) |
| 80 | TCP | `0.0.0.0/0` | HTTP (Certbot challenge + redirect to HTTPS) |
| 443 | TCP | `0.0.0.0/0` | HTTPS (public API) |
| All others | — | — | Bound to `127.0.0.1` only; nothing to open |

Do **not** open ports 5000, 5001, 5100, 8000, 5432, or 6379 externally — they're all loopback-bound by service config.

### OpenSearch security group inbound
Add the new instance's private IP (or its security group ID) to the existing OpenSearch cluster's inbound allowlist on port 443. Same convention used for `13.233.251.147` today.

---

## 6. Existing infrastructure — no change requested here

These resources are already live and require no action as part of this requisition:

- **AWS OpenSearch cluster** (`ap-south-1`). *Note:* a separate task will verify it can absorb the 5× search-call increase at peak (50 streams × ~10 ES calls/turn). If it cannot, we'll raise a follow-up ticket for a one-tier instance bump. Not blocking this requisition.
- **S3 bucket `lawttorney`** (`ap-south-1`). No change.
- **DNS `tool.lawttorney.com`**. A CNAME/A-record change is required at cutover (Phase 8) — separate ask at that time.
- **Existing v1 host `13.233.251.147`**. Stays running through cutover for rollback. Decommission planned 24 h after a successful v2 cutover (separate ticket).

---

## 7. What is explicitly NOT requested

To pre-empt scope creep:

| Not requested | Why |
|---|---|
| RDS Postgres | Postgres runs on the EC2 box. Avoids managed-DB cost (~$80+/mo) at our current data volume. Revisit when chat volume hits 100 GB or HA becomes a requirement. |
| ElastiCache (Redis) | Redis (if needed) runs on the box. Used as a rate-limit backend only; managed cache is overkill. |
| ALB / NLB | Single host; no LB required yet. Adding HA is a separate Phase 9 conversation. |
| EKS / ECS / Fargate | Bare-metal systemd deployment matches our current ops model; container orchestration would be a different (larger) initiative. |
| Site-to-site VPN | All inter-service traffic is intra-VPC. No on-prem connectivity needed. |

---

## 8. Cost estimate

AWS Mumbai (`ap-south-1`) on-demand list prices, mid-2026:

| Line | Monthly | Annual |
|---|---|---|
| `m5.2xlarge` (730 hrs × $0.428) | **$312** | $3,744 |
| `gp3` 200 GB ($0.0924 × 200) | **$19** | $228 |
| Daily EBS snapshot (200 GB × $0.05) | **$10** | $120 |
| Data transfer out (estimate ~50 GB/mo × $0.09) | **$5** | $60 |
| **Total — on-demand** | **~$346 / mo** | **~$4,152 / yr** |

Savings options:

- **1-year Reserved Instance, no upfront:** EC2 line drops to ~$197/mo → **total ~$231/mo** (~$2,800/yr).
- **3-year RI, no upfront:** EC2 line drops to ~$140/mo → **total ~$174/mo** (~$2,090/yr).
- **Compute Savings Plan (1-year):** ~30% savings, more flexible across instance families.

**Recommendation:** start on on-demand, convert to 1-year RI after 30 days of stable operation. Avoids locking in a wrong instance type.

---

## 9. Timeline

| Day | Activity | Owner |
|---|---|---|
| 0 | This requisition approved | Approver |
| 0–1 | EC2 + EBS + IAM + SG provisioned | Cloud Admin |
| 1 | I run `deploy/provision_new_host.sh` (Postgres, Python 3.12, embedding service) | Adesh |
| 2 | Smoke test single-worker parity with v1 | Adesh |
| 3–4 | Phase 2 (gunicorn multi-worker), Phase 3 (concurrency cap), Phase 5 (ChromaDB server) | Adesh |
| 5 | Load test at 50 concurrent streams (Phase 7) | Adesh |
| 6 | Cutover (nginx upstream flip), Phase 8 | Adesh |
| 7 | Decommission old v2 host, 24 h grace | Adesh |

End-to-end: **~1 week** from approval to v2 serving 50-user capacity.

---

## 10. Decisions needed from approver

1. **Approve `m5.2xlarge` + 200 GB gp3** at ~$346/mo on-demand? (If a cheaper tier is preferred, please review §3 trade-off table first.)
2. **RI commitment** — go straight to 1-yr RI, or run 30 days on-demand and convert?
3. **Backup retention** — 7 days on EBS snapshot OK, or do you want 14/30 days?
4. **IAM** — reuse the v1 host's S3 policy (fastest), or mint a fresh least-privilege role for v2?
5. **SSH allowlist** — confirm the corporate IP range(s) to whitelist on port 22.

---

## 11. Risks if approval is delayed

- **Capacity**: current v2 single-worker setup degrades beyond ~10 concurrent users. Any growth in product usage above that level will start hitting p95 latency and 5xx.
- **Disk**: v1 host (`13.233.251.147`) is at **69% disk utilisation today** (`/dev/root` 58 GB, 40 GB used, 19 GB free). PDF upload growth alone will exhaust the remaining 19 GB within weeks. This is independent of the v2 scaling work but reinforces the case for fresh hardware.
- **Operational**: every week of single-worker v2 is a week without graceful 503s, in-flight backpressure, or multi-worker fault isolation. A single Python exception in the worker takes the whole service down.

---

## 12. Appendix — current-state evidence

Captured 2026-06-09 from SSH inventory of `13.233.251.147`:

```
Instance type       : r5.xlarge (4 vCPU / 30 GiB RAM)
OS                  : Ubuntu 22.04.3 LTS, kernel 6.8.0-1030-aws
Python              : 3.10.12 (no 3.12)
Disk                : /dev/root ext4 — 58 GB total, 40 GB used (69%), 19 GB free
Memory              : 30 GiB total, 20 GiB used by running services
Running app         : pm2 / uvicorn (v1 stack), port 5000, 12 worker procs
Existing dirs       : /home/ubuntu/Lawtech-AI, /home/ubuntu/Routing db, /home/ubuntu/chroma_store (6,676 collections)
Postgres            : not installed
Swap                : 0
```

This evidence supports the conclusion that `13.233.251.147` cannot be the v2 scaling target without either disruptive resize or capacity reduction.

---

**Reply with approval (and answers to §10) and I'll begin provisioning the same day.**

Adesh Raj
adesh.raj@lamipak.biz
