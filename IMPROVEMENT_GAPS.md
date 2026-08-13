# Lawttorney — Improvement Gaps

*Code-level analysis. Originally written against `main` @ `cfd24d2` (28 Jul 2026);
**re-verified 9 Aug 2026 against `3a0d77b`**, which had moved 11 commits since.
Entries that changed are marked ✅ FIXED, ⚠️ CORRECTED, or 🔸 PARTIALLY ADDRESSED.*

> **This document is analysis, not a tracker.** For live status — what is done, what is
> next, why each item matters, and what we deliberately decided **not** to do — see
> **`FIX_REGISTER.md`**. Item IDs there (Q-1, C-3, R-5 …) are stable; cross-reference
> them rather than these bullets.

**How to read confidence:**
- **Clear win** — verified in code, mechanism is direct, low risk. You will see the improvement.
- **Needs measurement** — the gap is real, but the size of the gain depends on data we don't have yet (needs an A/B or eval to quantify).
- **Trade-off** — real, but "fixing" it trades one thing for another (a product decision, not a pure bug).

> The system already emits per-request cost telemetry (`token_usage` in every API
> response), so any change below can be measured before/after on real queries.

---

## Tier 1 — Clear wins (cost)

- **Premium model on Scenario sub-steps.** Four structured-extraction calls run on
  the most expensive model (Gemini 2.5 Pro, ~$1.25/$10 per M tokens) where the
  cheapest tier (Flash-Lite, ~$0.10/$0.40) does the same job. Moving them is a
  ~4–12× cost cut on those calls with negligible quality risk.
  *(`tools/shared/scenario_tools.py:93/148/237/339`)*

- **Premium refiner runs on most answers.** The automatic "critique-and-refine"
  quality pass fires whenever any source was retrieved (i.e. most substantive
  queries), and its refine step uses the **premium** model. Gating it more
  selectively removes a premium call from the majority of requests.
  *(`core/self_refine.py:1423` refiner; gating at `:1514`)*

- **"Hidden reasoning" tokens billed on simple tasks.** The model's internal
  thinking is billed as output. It's left switched on for document Q&A and drafting,
  which are format-following tasks that don't need it. Turning it off on those paths
  removes billed reasoning tokens (at the Pro output rate of ~$10/M).
  *(`core/clients.py:216`; document/drafting call sites)*

- **Runaway output ceilings.** The default max-output size is the maximum (65,535
  tokens); any call path that forgets to lower it can emit huge, expensive
  responses (the code documents past runaway incidents). Lowering the default caps
  tail-risk cost. *(`core/clients.py:171/192/216/235`)*

---

## Tier 1 — Clear wins (reliability)

- **A database call blocks the server's event loop.** One retry path calls the
  search database directly on the async loop instead of off-thread, so it stalls
  other requests. Real, but note it's on the retrieval-miss retry path, so it bites
  under load *and* misses, not on every request. One-line fix. *(`agents/newacts.py:809`)*

- 🔸 **PARTIALLY ADDRESSED — Most agents have no time limit.** `core/deadline.py`
  (118 lines) is **new since this was written**: `core/gateway.py:657` now seeds a
  request-scoped `deadline_scope(285)`, and Drafting consumes it via `bounded_wait_for`
  / `remaining`. That bounds the *request*, not the agent — one stuck external call can
  still consume the whole budget, and no other agent reads the deadline. Remaining work
  is per-agent budgets. → **FIX_REGISTER R-2**

- **Automated tests don't run before deployment.** The deploy pipeline only does a
  "syntax & import check"; the ~29 real test files never run automatically, so a
  logic regression can ship. Adding a test gate is pure risk reduction.
  *(`.github/workflows/deploy.yml`)*

- 🔸 **PARTIALLY ADDRESSED — Failures are silently masked.** The relevance-check step
  still **fails open** — an internal error counts as a pass, so an outage looks like a
  normal result, and any accept-rate you compute is biased upward.

  The second half is **fixed**: agent errors *are* now recorded —
  `agent_errors_total` exists at `core/metrics.py:54` with a `record_agent_error`
  helper in use across agents. → **FIX_REGISTER R-3**

- **Search-client startup race.** The shared search client is created without a
  lock, so two concurrent first-requests can build two clients. *(`core/clients.py:62–95`)*

---

## Tier 2 — Needs measurement (answer quality)

- **No result re-ranking step.** After retrieval, candidate documents are fed to the
  AI in raw search-score order — there's no second-pass re-ranker to push the most
  relevant result to the top. This is the standard way to lift precision (and could
  replace the per-query relevance-check LLM call), but the size of the gain needs an
  evaluation set to confirm. *(absent codebase-wide)*

  **Added 9 Aug 2026 — the strongest evidence for this is already in your own code.**
  `core/retrieval_relevance.py:9–24` documents BM25 returning acts that *prohibit*
  injunctions for a query about *granting* one, scoring **0.73 vs 0.74** against the
  correct CPC text. A bi-encoder cannot separate those — it captures topical overlap,
  not subject-matter direction. A cross-encoder reads query and document together and
  can. Note also that **7 of 8 ES search paths are BM25-only** (only `search_newacts`
  hybrid uses vectors), so reranking helps more paths than adding embeddings would.
  → **FIX_REGISTER Q-10**

- ⚠️ **CORRECTED — Uploaded-document chunk/embed mismatch (real, but DORMANT).**
  Uploaded docs are split into 15,000-character chunks, but the QA embedder truncates
  at 256 tokens (~1,000 chars) — **measured, `max_seq_length=256`** — so ~93% of every
  chunk is invisible to its own embedding.

  **The previous version of this entry was wrong.** It said truncation "degrades
  large-document retrieval" via a "chunked ChromaDB path… retrieves ~30 chunks."
  That path was **deleted on 2026-08-02** (`agents/document.py:35–43`, "P2 dead-code
  sweep" — `_retrieve_from_collections` and `_retrieve_docs` removed). **Nothing
  retrieves chunks today**; `get_full_attachment` reads every chunk back and
  reassembles the whole document, so no information is lost at answer time.

  What it actually costs now: embedding CPU on every upload to build an index nothing
  reads, and a landmine under any future work that reintroduces retrieval over
  uploads. Priority **down**, not up. *(`core/file_processor.py:84–85`, telemetry at
  `tools/shared/vectordb_tools.py:134–145`)* → **FIX_REGISTER Q-11**

- **Search-ranking weights are hand-tuned, not calibrated.** The formula blending
  keyword + semantic search (`bm25 + 100 × cosine`) uses a fixed multiplier and
  hand-picked boost numbers, unnormalized and never tuned against a labelled set;
  they've drifted between code paths. Re-tuning likely helps, but needs an A/B to
  prove. *(`tools/shared/elasticsearch_tools.py:819`, `agents/newacts.py:314`)*

- **Corpus embedding model is not domain-adapted.** The legal-corpus embedder is a
  general-purpose, English, 512-token model — long provisions are truncated on the
  index side too, and it isn't tuned for Indian legal language. A domain/legal
  embedder would likely improve retrieval, subject to evaluation. *(`core/settings.py:100–103`)*

- **Uploaded-doc embeddings are un-normalized.** The document embeddings skip the
  normalization the corpus embeddings apply, while results are ranked by raw
  distance — a possible ranking-accuracy smell. Low confidence; needs a test to
  confirm it changes real rankings. *(`core/clients.py:344`)*

---

## Tier 2 — Needs measurement (cost)

- **Explicit prompt caching not configured.** The large static instruction prompts
  (the ~950-line critique prompt; the 2,777-line master prompt file) are re-sent on
  every call. **Correction:** Gemini's *implicit* caching is already active —
  telemetry shows ~20,400 cached-input tokens on a single drafting request — so the
  ~75% repeat-prefix discount already applies opportunistically within a request.
  The genuine gap is that **explicit** caching isn't configured, which would make
  that discount reliable (guaranteed TTL) for the static prompts that repeat across
  requests, rather than best-effort. Real but incremental — not "75% off
  everything." *(`core/self_refine.py:147`, `config/prompts.py`)*

- **Redundant query rewriting.** The user's query is rewritten by up to three
  separate AI calls per multi-agent turn (memory, intent normalization, per-agent
  rewrite). Folding these together removes 1–2 cheap calls per query.
  *(`agents/memory.py:189`, `agents/orchestrator.py:567/776`)*

---

## Tier 3 — Trade-offs (not pure bugs)

- **"With case laws" runs two court agents.** Requesting case law automatically runs
  both the High Court and Supreme Court search agents. This is **intentional** (the
  code comment explains: the Supreme Court corpus has the landmark precedents, the
  High Court corpus has regional/recent ones). It costs more but improves coverage —
  reducing it trades cost for completeness, a product decision. *(`agents/orchestrator.py:441–463`)*

---

## Engineering process (housekeeping)

- **Code duplication / no shared base agent.** ~300–400 lines of near-identical
  retrieve→gate→fallback and ReAct logic are copy-pasted across agents and have
  already diverged. *(`agents/sci_judgment.py` ≈ `agents/gst_judgment.py`; gate block ×4)*

- **Scattered magic numbers.** Output caps, client timeouts, and character limits are
  hardcoded at call sites instead of centralized in settings.

- 🔸 **PARTIALLY FIXED — Documentation drift.** The stale model name is corrected:
  query rewriting is Gemini Flash Lite, not GPT-4o-mini (`core/agent_fallback.py`,
  `CLAUDE.md`). `CLAUDE.md`'s false "all domain agents have a 3-tier fallback" claim
  is replaced with a per-agent table.

  **Still outstanding, and worse than "drift":** `SYSTEM_MAP.md:709–739` documents a
  **kNN + RRF retrieval design over a `dense_vector` field that was never built** —
  a repo-wide grep for `dense_vector` / `knn` / `rrf` across `*.py` returns **zero
  matches**. The live code uses a `script_score` over a field named `embedding`.
  Anyone re-indexing from that document would write the wrong field name and get a
  silently non-functional vector search. Also a stale hardcoded ES IP in several docs
  (the code default is now `localhost:9200`, changed deliberately).
  → **FIX_REGISTER P-3b**

- ⚠️ **CORRECTED — API key IS committed. This is a real exposure, not hygiene.**
  The previous version of this entry read *"hygiene only, no exposure… real credentials
  live in the un-committed `.env`."* The `.env` part is true and it is already
  gitignored (`.gitignore:2`), so the suggested fix was already done. But the
  conclusion was wrong:

  The value of `API_KEYS` is **hardcoded in 12 committed files** — `tests/chat_pdf_eval.py`,
  `tests/verify_bug01_routing.py`, `tests/verify_bug02_drafting_uses_pdf.py`,
  `tests/verify_bug04_document_qa.py`, `tests/verify_bug06_08_no_hallucination.py`,
  `tests/verify_bug13_14_15.py`, `tests/test_all_features.py`,
  `tests/test_attach_pdf_judgement.py`, `tests/test_features_3_4.py`,
  `tests/test_fir_bail_draft.py`, `tests/investigate_writ_petition.py`,
  `tests/run_injunction_draft.py` — on a repo with a GitHub remote.

  Worse: **`ADMIN_API_KEY` is set to the same value**, so that committed string grants
  admin access to `/pyapi/admin/*`, `/pyapi/metrics` and `/pyapi/health/detailed`, not
  just normal chat.

  **Action:** rotate both, make them different, strip the literal from those files.
  `python -c "import secrets; print(secrets.token_urlsafe(32))"` → **FIX_REGISTER S-1**

---

## Suggested first moves

The clearest, lowest-risk starting points — all Tier 1, all measurable via the
existing `token_usage` telemetry:

1. Right-size the Scenario Pro sub-calls → cheaper tier.
2. Turn off hidden-reasoning on document/drafting generation.
3. Fix the blocking database call (`newacts.py:809`).
4. Add a test gate to the deploy pipeline.

---

## Revision history

**29 Jul 2026** — verified against `cfd24d2`. Two overstatements corrected: implicit
caching is already active (only *explicit* caching is missing); document truncation
scoped to the ChromaDB path rather than everyday document Q&A.

**9 Aug 2026** — re-verified against `3a0d77b`, 11 commits later. Five entries changed:

| Entry | Change |
|---|---|
| Uploaded-document truncation | ⚠️ **Corrected.** Described a code path deleted 2026-08-02. Real but dormant; priority **down**. |
| Committed `.env` templates | ⚠️ **Corrected.** "No exposure" was wrong — the API key is in 12 committed files and admin == user key. Priority **up**. |
| Failures silently masked | 🔸 Half fixed — `agent_errors_total` now exists; fail-open remains. |
| Most agents have no time limit | 🔸 Partially addressed — `core/deadline.py` is new; request-scoped only. |
| Documentation drift | 🔸 Partially fixed — model name corrected; `SYSTEM_MAP.md` still documents a kNN/RRF design that was never built. |

Re-confirmed still accurate at `3a0d77b`: the blocking `es.search` on the event loop
(`agents/newacts.py:809`), the deploy pipeline running only a "Syntax & Import Check",
the unlocked ES client startup race, and the absence of any re-ranking step.

**Live status for every item is in `FIX_REGISTER.md`** — including four proposals we
investigated and deliberately **rejected**, with reasons.
