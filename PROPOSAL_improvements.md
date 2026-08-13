# Improving Lawttorney: Cost, Quality & Reliability

*Proposal for leadership review · Prepared [date] · Owner: [name]*
*Originally based on a code review of 29 Jul 2026. **Re-verified 9 Aug 2026** against
the current code, which had moved 11 commits since. Corrections are marked inline.*

> **Status:** three quality fixes are already implemented and tested (not yet
> released) — see *Progress* below. Item-level tracking, with reasons and file
> references, lives in `FIX_REGISTER.md`.

---

## Progress since this proposal was written

| Done | What it fixes |
|---|---|
| Drafting citation check | The automatic quality review was never checking whether case citations in a draft were real. It now is — invented citations get caught before the user sees them. |
| Constitution & Maxim retry | These two topics gave up after one search and fell back to a general web search. They now retry against our own legal corpus first — cheaper and better sourced. |
| Source tracking across agents | Legal sources found by the drafting step were invisible to the rest of the system, so correct citations could be wrongly flagged. They are now shared. |

Three quality fixes, 35 automated tests, nothing released yet.

---

## TL;DR

Lawttorney is a solid, working multi-agent legal AI. A code review surfaced a set
of concrete, verified improvements in three areas — **cost, answer quality, and
reliability**. The cost and reliability changes are low-risk and measurable today;
the quality changes are promising but should be validated with a short evaluation
before we commit. We recommend a phased rollout starting with four low-risk wins
this month.

**The single most useful fact:** the system already reports the exact cost of every
request. In one real drafting request we measured, **a single call to our most
expensive AI model was 53% of the entire request's cost.** That tells us exactly
where to look.

---

## The opportunity

We are not fixing a broken system — we're removing avoidable cost and tightening
quality on a product that already works. The review found three kinds of headroom:

1. **Cost** — in several places we pay for our most expensive AI model (or re-send
   large instructions) where a cheaper approach does the same job. These are
   contained, well-understood changes.
2. **Answer quality** — two concrete improvements to how we search and rank legal
   documents could make answers more accurate, especially on large uploaded files.
3. **Reliability & process** — a few changes would reduce the risk of a slow request
   tying up the server and stop untested code from reaching production.

Because the system already measures per-request cost, **every change we make can be
proven with before/after numbers** — no guesswork on ROI.

---

## What we'd change, and why

### 1. Cost — high confidence, low risk

- **Use the right-sized AI model for the job.** A few internal steps (scenario
  analysis sub-tasks, an automatic quality-review pass, document reading) run on our
  premium model where a cheaper tier would produce the same result. Right-sizing
  these is a **4–12× cost reduction on those specific calls**, and they are among the
  most expensive in a typical request.
- **Stop paying for "thinking" we don't use.** The AI can be told to do internal
  reasoning, which we're billed for. It's switched on for straightforward tasks
  (drafting, document Q&A) that don't need it. Turning it off there is free savings.
- **Cap oversized responses.** A default setting allows unusually large (expensive)
  responses; tightening it prevents rare but costly runaways.

*Honest note:* our AI provider already gives an automatic discount when we repeat
the same instructions (we verified this is working). So the caching opportunity is
smaller than it first appears — a reliability tweak, not a headline saving.

### 2. Answer quality — promising, validate first

- **Add a "re-ranking" step.** Today we hand the AI its search results in raw score
  order. Adding a second pass that re-orders results by relevance is the industry-
  standard way to improve accuracy — and it could replace a separate AI call we make
  today. **We recommend measuring the lift on a test set before rollout.**
- **Fix the uploaded-document search index.** When we store an uploaded document we
  index only the first portion of each section, so most of the text is invisible to
  search. **Correction since the last version:** the search step that used this index
  was removed from the product on 2 Aug 2026 — today we send the whole document to the
  AI instead, which loses nothing. So this is **not currently hurting answers**; it is
  wasted processing on every upload, and a trap for any future change that reintroduces
  search over uploaded files. Lower priority than previously stated.

*Honest note:* unlike the cost items, we can't attach a precise number to these
until we run an evaluation. They're the right direction; the size of the win needs
measurement.

### 3. Reliability & process — low risk, clear benefit

- **Add safety time-limits to each agent.** Today one slow external call can tie up
  a request for up to five minutes. Per-agent limits bound the worst case.
- **Fix one call that can stall the server** under heavy load (a one-line change).
- **Run automated tests before every deployment.** Currently the deploy pipeline
  only checks that the code compiles — it doesn't run our test suite, so a logic bug
  can reach production. Adding this gate is pure risk reduction.

---

## Expected impact & how we'll measure it

| Area | What improves | How we'll prove it |
|---|---|---|
| Cost | Lower cost per request | Compare `token_usage.cost_usd` on a fixed set of test queries, before vs after |
| Quality | More accurate answers, esp. large docs | Score answers on a labelled evaluation set before rollout |
| Reliability | Fewer stalls; regressions caught pre-deploy | Latency monitoring + CI test pass/fail |

We already capture per-request cost and latency, so the cost and reliability results
will be visible immediately. Quality requires building a small evaluation set first
(a worthwhile investment regardless).

---

## Phased plan

**Phase 1 — This month (low-risk, measurable wins):**
- Right-size the premium-model calls that don't need it
- Turn off unused "thinking" on generation tasks
- Fix the server-stall call
- Add the automated-test gate to deployment

**Phase 2 — Next 30 days (cost + selective quality):**
- Run the automatic quality-review pass more selectively
- Add per-agent time-limits *(partially in place already — a request-wide limit now
  exists; per-agent budgets do not)*
- Extend citation grounding to the remaining agents *(replaces "fix large-document
  search", which the correction above downgraded)*

**Phase 3 — Next 60–90 days (larger quality bets, measured):**
- Build an evaluation set and add the re-ranking step
- Tune search-ranking; consolidate duplicated code

---

## Risks & guardrails

- **Quality changes are validated, not assumed.** Anything touching answer quality
  goes through an evaluation set before rollout — we won't ship a change we can't
  measure.
- **Sensitive components handled carefully.** A couple of areas (the drafting flow,
  the quality-review step) are marked internally as change-sensitive; those changes
  will be scoped and reviewed rather than done quickly.
- **⚠️ A security exposure WAS found — this corrects an earlier statement in this
  document.** An earlier version of this proposal stated that no credentials were
  committed. That was wrong. Our user-facing API key is hardcoded in **12 committed
  test files**, and the admin key is set to the **same value** — so that committed
  string grants administrative access, not just normal usage. The `.env` file itself
  is correctly excluded from the repository; the exposure is entirely via those test
  files. **Recommended action: rotate both keys, make them different, and remove the
  literal from those files.** This is independent of everything else in this proposal
  and should not wait for a phase.

---

## The ask

1. Approve **Phase 1** (four low-risk changes) to start now — these pay for
   themselves and are fully measurable.
2. Approve building a small **evaluation set** so the quality improvements in
   Phases 2–3 can be validated with numbers.
3. Assign an owner and we'll report cost/latency deltas after Phase 1.

---

*Detailed, code-level findings backing this proposal are in the companion documents:
`FIX_REGISTER.md` — the live tracker (every item, its status, why it matters, and
what we deliberately decided **not** to do) — and `IMPROVEMENT_GAPS.md` for the
original code-level analysis.*

*Two statements in the first version of this proposal were wrong and are corrected
above: (1) it reported no security exposure — there is one; (2) it described
large-document search as an active accuracy gap — the affected code path had already
been removed.*
