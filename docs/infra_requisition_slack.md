# Slack / IM version — short-form infra ask

> Use this when the approver lives in Slack / Teams / email and reads short.
> Full version: [infra_requisition_memo.md](infra_requisition_memo.md).
> Paste the block below as-is. Edit the SSH IP range in §4 before sending.

---

**Need approval: 1 new EC2 for Lawtech-AI v2 production (50 concurrent users)**

*Why:* v2 currently runs single-worker, ceiling ~10 active users. Product target is 50. Existing v1 host (`13.233.251.147`) is 69% disk-full and can't host both stacks.

*Spec:*
• `m5.2xlarge` (8 vCPU / 32 GB RAM) — Ubuntu Server 24.04 LTS — `ap-south-1a`
• Root: 200 GB `gp3` EBS, 3000 IOPS baseline
• Daily EBS snapshot, 7-day retention, termination protection ON
• Same VPC + public subnet as v1 host (needs to reach OpenSearch + S3)

*IAM + Network:*
• Reuse v1 host's IAM model — S3 `lawttorney` read/write + OpenSearch HTTP access.
• Security group inbound: 22 (corp IP range — please confirm), 80, 443. Everything else loopback-bound.
• Add the new instance's SG to OpenSearch cluster's inbound allowlist.

*Cost:* ~$346/mo on-demand → ~$231/mo on 1-yr RI (ap-south-1 list price).

*Timeline:* Provision Day 0 → smoke-test Day 1 → cutover Day 6. End-to-end ~1 week.

*Need from you (4 quick yes/no):*
1. m5.2xlarge approved, or alternative?
2. Start on-demand, convert to 1-yr RI after 30 days — OK?
3. Reuse v1's IAM policy, or mint a fresh least-privilege role?
4. Confirm corporate SSH IP range to whitelist on port 22.

Full plan + cost trade-offs: `docs/scale_50_concurrent_users_plan.md`, `docs/infra_requisition_memo.md`.

---
