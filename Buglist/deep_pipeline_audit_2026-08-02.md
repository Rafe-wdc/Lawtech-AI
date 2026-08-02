# Lawtech-AI — Deep Pipeline Audit (2026-08-02)

Scope: end-to-end audit of the Legal-AI backend after the 2026-07-29 prod bug
inventory. Five parallel audit passes were run against
`d:\agentic_proj\Lawtech-AI` — endpoints & streaming, per-agent, drafting +
self_refine, dead code & doc drift, and multi-agent SOTA gaps.

The 2026-07-29 inventory (`Buglist/prod_bug_inventory_2026-07-29.md`) already
enumerates 10 bugs plus the `short_err()` fix bundle. **This document does not
re-list those**. It captures **NEW** findings, tightens their fix hints with
file:line evidence, and adds the cross-cutting reliability gaps that a
99.9% user-serve target requires.

---

## How to read this document

- **Severity:** 🔴 CRITICAL / 🟠 HIGH / 🟡 MEDIUM / ⚪ LOW.
- Every finding cites `file:line` and, where a prod incident already exposed
  it, references the `req=` from journalctl.
- Findings adjacent to a Buglist entry are labelled `(→ Bug #N)` so the two
  files stay in sync.
- Fixes marked **P0** should ship in the same batch as Bug #1 (`short_err()`).
  **P1** ships this week. **P2** this month. **DESIGN** = needs a plan doc.

---

## Executive summary

The pipeline is externally healthy but the internal safety net is thin. The
five most important structural risks — none owned by any single agent —
are:

1. **`str(e).splitlines()[0]` crash pattern extends beyond drafting.**
   The Buglist counted ~18 sites; audit found **20+**, including 2 sites
   inside `core/self_refine.py` and 1 inside `agents/orchestrator.py:596`
   plus 2 in `core/embedding_client.py`. Same `short_err()` fix, wider
   blast radius.

2. **`self_refine` silently passes on any failure.** On critic timeout,
   quota, or empty exception, drafts ship un-audited with `passes=True`.
   Combined with (1), a single Gemini Flash blip bypasses the entire
   quality-audit layer with no telemetry.

3. **No LLM timeouts in the two ReAct agents (`sci_judgment.py`,
   `gst_judgment.py`) or in `self_refine`.** A hung Gemini call holds the
   drafting semaphore until the gunicorn 300s worker_timeout kills the
   whole worker — cascading failure across every in-flight request.

4. **Flipped-arg `_error_response(...)` calls in 20 sites in
   `core/gateway.py`.** Every export/memo/toa/statute-refs/save-turn/fix-draft/
   compliance-check endpoint returns generic 500 with wrong body shape on
   any validation error. Silent because the happy path never touches these
   branches.

5. **Frontend still calls a deleted endpoint** (`/pyapi/continue_draft`
   at `frontend.html:3772`). Every click of the "continue draft" UX
   returns 404. Present since 2026-06-28.

Every one of the five is code-fixable in <100 LOC. Combined they should
move stream-error-visible rate close to zero and let the deferred
architectural work (Bug #9 polish-prior-draft, Bug #10 chunking audit)
be tackled from a known-good baseline.

---

## Table of contents

- Part A — HTTP surface & streaming ( Sections A.1 – A.6 )
- Part B — LangGraph state & orchestrator ( Sections B.1 – B.3 )
- Part C — Agents ( Section C — 14 agents )
- Part D — Drafting + self_refine ( Sections D.1 – D.7 )
- Part E — Dead code, orphans, doc drift ( Sections E.1 – E.5 )
- Part F — Reliability infrastructure (SOTA gaps) ( Sections F.1 – F.11 )
- Part G — Ranked action list

---

## Part A — HTTP surface & streaming

### A.1 Frontend → deleted `/pyapi/continue_draft` — 🔴 CRITICAL

- `frontend.html:3772` POSTs to `/pyapi/continue_draft`. The endpoint was
  removed 2026-06-28 (`core/gateway.py:1137-1140` documents the removal).
  Every click of the UX returns 404 with the frontend fetch throwing —
  silent UX break.
- **Stale docs perpetuating the illusion:** `README.md:151`,
  `AGENTS.md:104`, `CLAUDE.md:104`, `SYSTEM_MAP.md:68, 882`,
  `API_DOCUMENTATION.md:22, 528, 786, 804, 824, 835, 1783`.
- **Fix (P0):** remove the frontend button + fetch, delete the 7 doc
  references. Alternative: reintroduce as a redirect that turns
  "continue this draft" into a new `/pyapi/chat` call carrying the prior
  AI turn as `user_facts` — this is exactly what Bug #9 needs anyway.

### A.2 Flipped `_error_response(...)` args in 20 gateway sites — 🔴 CRITICAL

- Signature: `_error_response(status_code: int, error: str, message: str,
  request_id: str = "")` (`core/gateway.py:94`).
- Callers pass `_error_response("string_error", "string_message", 400)` —
  i.e. status-code goes as message, `error` string goes as status_code.
- Sites: `core/gateway.py:1666, 1671, 1678, 1696, 1718, 1743, 1745, 1771,
  1803, 1807, 1833, 1863, 1867, 1893, 1927, 1932, 1963, 2003, 2008, 2043`.
- Effect: `JSONResponse(status_code="not_found", ...)` raises `TypeError`
  inside the try → the endpoint's outer `except Exception` fires →
  `_error_response(500, "...", "...")` with correct args → global 500
  handler wins. Every validation/not-found path in **7 endpoints**
  (`/pyapi/export`, `/pyapi/memo`, `/pyapi/toa`, `/pyapi/statute-refs`,
  `/pyapi/save-turn`, `/pyapi/fix-draft`, `/pyapi/compliance-check`)
  returns 500 with the wrong body shape.
- **Fix (P0):** repair arg order at all 20 sites, or make
  `_error_response` accept either shape (kwargs) and warn on the legacy
  form until removed. Add a regression test that hits each endpoint's
  400/404 branch.

### A.3 Cached-response SSE schema drift — 🟠 HIGH

- `core/chat_runner.py:238` on cache hit emits a `done` event that omits
  `conversation_turn`, `query_rewritten`, `effective_query` — fields the
  non-cached path always emits. Frontend consumers that treat the payload
  as invariant break silently on cache hits.
- **Fix (P1):** align the payload with the non-cached emission at
  `chat_runner.py:400+`.
- Note: `RESPONSE_CACHE_ENABLED` defaults `false` — bug is latent until
  someone turns it on in prod.

### A.4 `_handle_integrations` has no timeout — 🟠 HIGH

- `core/chat_runner.py:96-174` calls `process_integration_urls(...)` with
  no `asyncio.wait_for`. `INTEGRATION_POLL_TIMEOUT_SEC = 60.0` is defined
  in `core/settings.py:132` but never applied here.
- A stalled FSD JWT check hangs the SSE stream until the outer 300 s
  envelope in `chat_runner.py:291` fires. User sees a blank stream
  followed by a generic error at the very end.
- **Fix (P1):** wrap the call in `asyncio.wait_for(..., timeout=
  INTEGRATION_POLL_TIMEOUT_SEC)`; on timeout emit `integration_error`
  and continue without integration context.

### A.5 `/pyapi/chat` has no wall-clock envelope on file-processing — 🟠 HIGH

- The multipart handler in `core/gateway.py:892-1079` writes up to 30
  files × 1 GB and calls `process_files` under per-file inner timeouts
  (60–180 s). If all 30 files hit their inner ceiling serially the
  client waits ~30 × 180 s = 90 min before the SSE stream even reaches
  the graph. There is no outer envelope on file processing — the
  300 s `async_timeout(300)` only wraps the graph phase.
- **Fix (P1):** cap total file-processing budget (e.g. 240 s across all
  files). Emit `file_processing_timeout` event and continue with the
  successfully-processed subset.

### A.6 Other stream-surface findings — 🟡 MEDIUM

- **HTML strip runs only on final response** (`chat_runner.py:400`).
  Token events carry raw HTML mid-stream; a malicious `<script>` in a
  token chunk renders. `_FINAL_TAG_RE` only defends the terminal
  `response` event. Add per-token strip or a `<`-detection early-out.
- **Pad-char runaway** in `core/streaming.py:132-201` silently drops
  characters past `_STREAM_PAD_RUN_EMIT_CAP=64` per pad-run. Legit
  table cells needing 200 dashes lose 136 dashes with no user signal.
- **Client disconnect not propagated to graph.** `core/gateway.py:848-856`
  closes the SSE writer but never cancels the graph task. On a
  user-closed tab, drafting completes server-side (wasted Gemini spend).
- **`agent_graph.astream` timeout leaves checkpointer state dangling.**
  `chat_runner.py:291-388` — the cancellation is clean but LangGraph
  keeps the partial `agent_results` under the thread's checkpoint. Next
  turn may resume from mid-fanout state with stale entries.

---

## Part B — LangGraph state, wiring, orchestrator

### B.1 State-schema hazards — 🟠 HIGH / 🟡 MEDIUM

- `state.py:171 tokens_consumed: Annotated[int, _sum_tokens]` — accumulator
  runs on every merge but the response is built from
  `token_tracker.to_dict()` (`chat_runner.py:377-380`). Dead reducer that
  costs a merge step per agent update. 🟡 (delete for cleanup)
- `state.py:180 messages: []` initialised in `_build_initial_state`
  (`gateway.py:578`). Pipeline uses `chat_history`/`summary_text`; nothing
  writes to `messages`. Under `add_messages` reducer, any accidental
  future write causes silent checkpoint bloat. 🟡
- `state.py:129-131 response_instructions` — CLAUDE.md declares it
  legacy but it's still populated by `_legacy_response_instructions` in
  the orchestrator. Comment says "kept until Phase 4"; Phase 4 shipped
  2026-06-13 per CLAUDE.md. Rename or drop the comment. 🟡

### B.2 Node wiring — 🟡 MEDIUM

- `core/graph.py:129` comment excludes `Non_legal` from
  `_validate_agent_map()` but `AGENT_NODE_MAP` (`graph.py:52`) does
  include `"Non_legal": "non_legal"`. Docstring drift, no functional bug.
- `graph.py:45 "Other": "scenario"` — `TaskType == "Other"` falls to
  Scenario as a de-facto catch-all. Non-obvious to future maintainers.
  Prefer explicit `_handle_other()` node or a `Fallback` task type.

### B.3 Orchestrator — 🟠 HIGH / 🟡 MEDIUM

- `agents/orchestrator.py:596` — **9th `str(e).splitlines()[0]` site**,
  missed by Buglist Bug #1's "18 sites in 6 files" enumeration. Same
  `short_err()` fix. 🔴 (→ Bug #1)
- **Bug #5 confirmed:** `orchestrator.py:1582-1602` "All agents empty —
  invoking web search last resort" has no guard against firing web-search
  when `task=Drafting` AND user uploaded files. The Buglist proposed
  fix stands. 🔴 (→ Bug #5)
- `_MAX_AGENT_CONTENT = 8000/12000` truncates agent OUTPUT for synth
  prompts (`orchestrator.py:1888, 1994`). Not a violation of the
  "preserve user query" invariant (that's the input path), but a
  truncated primary agent output can drop citations before the
  synthesizer sees them. Worth telemetry: emit a `synth_content_truncated`
  metric with agent + original_length. 🟡
- `_REFINE_PROMPT` (`orchestrator.py:903`) defined inline — for
  consistency with the codebase policy, move to `config/prompts.py`.
  Non-blocking. ⚪

---

## Part C — 14 agents

Detailed per-agent findings are in the raw audit output kept locally; this
section captures only the NEW / P0-worthy items.

### C.1 Silent bare `except: pass` sites — 🔴 CRITICAL

Silent failure = zero telemetry on real prod issues.

- `agents/judgment.py:415` — swallows `es_task.result()` exception with no
  log. On ES failure the agent proceeds with empty preliminary results
  and falls to the refined-search path — correct behaviour, but the
  operator has no signal that ES faulted. **Fix (P0):** log
  `except Exception as e: log.warning("ES task failed", error=short_err(e))`.
- `agents/document.py:203` — swallows Chroma count check with
  `return False`. Under Chroma failure this quietly reports "collection
  empty" and the agent falls into the extracted-text branch — again
  correct, but silent. **Fix (P0):** same as above.

### C.2 Unbounded LLM calls in ReAct agents — 🔴 CRITICAL

- `agents/sci_judgment.py:77, 169` — `agent.ainvoke({"messages": ...})`
  has NO `asyncio.wait_for`. A slow ReAct trajectory consumes the entire
  180 s / 300 s gateway budget with no local ceiling.
- `agents/gst_judgment.py:118, 174` — same pattern.
- **Fix (P0):** wrap in `asyncio.wait_for(..., timeout=90)`. Fall back to
  the existing topic-search retry path on timeout.

### C.3 Unbounded LLM calls in memory + non_legal — 🟠 HIGH

- `agents/memory.py:_rewrite_query` — `get_gemini_flash().invoke(...)` at
  line 193 has no timeout. Wrapped in `asyncio.to_thread` (line 449) so
  the loop is safe, but under Gemini slowness the memory step alone
  burns 20-60 s. **Fix (P1):** wrap with `asyncio.wait_for(..., timeout=15)`.
- `agents/non_legal.py:65` — `chain.ainvoke(...)` unbounded (bounded output).
  Low risk but wrap for consistency. 🟡

### C.4 No web fallback in ReAct judgment agents — 🟠 HIGH

- `agents/sci_judgment.py`, `agents/gst_judgment.py` — 3-tier fallback is
  incomplete. When ReAct + topic search both fail, the agent returns
  empty; the orchestrator then fires the generic web fallback (Bug #5).
- SCI corpus is broad enough that the ReAct path usually works; GST AAAR
  is 533 orders and misses are more likely. **Fix (P2):** add
  `web_search_fallback` when the ReAct trajectory returns empty content.

### C.5 ES calls without `request_timeout=` — 🟠 HIGH

- `agents/legislation.py:155, 275, 298` — synchronous `es.search(...)`
  with no client-side timeout. Relies on OpenSearch default (30 s).
- `agents/constitution_maxim.py:93` — same, wrapped in `to_thread`.
- **Fix (P1):** pass `request_timeout=8` explicitly. ES failures should
  fail fast into the rewrite/topic/web ladder.

### C.6 `str(e).splitlines()[0]` and `error=str(e)` sites — 🟠 HIGH

Beyond the 18 sites in Buglist Bug #1, the agent audit found:

- 9 sites in `agents/drafting.py` (Buglist counted 8; extra site at line
  1624)
- 1 site in `agents/orchestrator.py:596`
- 2 sites in `core/self_refine.py:1454, 1516` (Buglist missed these)
- 2 sites in `core/embedding_client.py:143, 174` (Buglist missed these)

Plus ~45 `except Exception as e: log.error(..., error=str(e))` sites in
`agents/` that will log blank `error=` on TimeoutError. All covered by
`short_err()` from Buglist Bug #1/#2.

### C.7 `user_intent` under-consumption — 🟡 MEDIUM

Agents that receive `user_intent` only via `localize_prompt` and never
read its typed fields:

- `agents/judgment.py`, `agents/sci_judgment.py`, `agents/gst_judgment.py`,
  `agents/newacts.py`, `agents/scenario.py`.

Fields that could tune retrieval or output: `wants_supreme_court`
(judgment routing), `response_depth` (result size), `named_acts`
(judgment/newacts pre-filter), `wants_table` (Scenario output template).

Not a bug — untapped signal. Ticket for a phase-5 intent-consumption
sweep. ⚪

---

## Part D — Drafting + self_refine

### D.1 `self_refine` critic/refiner crash cascade — 🔴 CRITICAL

- `core/self_refine.py:1454` (critic failure logger) and
  `core/self_refine.py:1516` (refiner failure logger) both use
  `str(e).splitlines()[0][:200]`. On any empty-message exception
  (`TimeoutError()`, `KeyboardInterrupt`, cancellation), the
  `IndexError` **escapes** the fallback path.
- The intended `Critique(passes=True, ...)` fallback at line 1457 is
  bypassed. The `IndexError` propagates to `agents/drafting.py:1990-1992`
  where the outer `except Exception as refine_err` silently swallows it.
- **Effect:** every Gemini Flash hiccup bypasses the entire critic layer.
  The un-audited draft ships with no violations flagged, no telemetry,
  no user-visible signal. This is `project_pipeline_audit_2026_07_11`'s
  HIGH item, now confirmed and pinned to file:line.
- **Fix (P0):** same `short_err()` helper as Buglist Bug #1. Also add
  an explicit `self_refine_skipped_total{reason}` counter so operators
  see this class of failure.

### D.2 `self_refine` LLM calls have no timeout — 🔴 CRITICAL

- Critic: `core/self_refine.py:1426` — no `asyncio.wait_for`, no
  `request_timeout`, no retries.
- Refiner: `core/self_refine.py:1487` — same.
- A hung critic call holds the drafting `_AGENT_SEMAPHORE` slot
  indefinitely → drafting workers backlog → gunicorn `timeout=300` kills
  the worker → every in-flight request in that worker dies.
- **Fix (P0):** wrap critic in `asyncio.wait_for(..., timeout=45)` and
  refiner in `asyncio.wait_for(..., timeout=60)`. On timeout: emit
  metric, log with `short_err()`, return `Critique(passes=True, ...)`.

### D.3 Chunk-router `IndexError` escapes `asyncio.gather` — 🔴 CRITICAL

- `agents/drafting.py:1172` (Buglist Bug #1 site) runs inside
  `asyncio.gather(*[_pick_relevant_chunk_indices(...)])` at
  `_generate_sectionwise:1578`.
- When the router times out AND the log line crashes, the exception
  escapes `gather` — **the entire sectionwise loop dies**, not just the
  timed-out pair. The `try/except` around `_generate_section_pair` at
  `drafting.py:1606-1626` never runs because the crash is BEFORE the
  pair generation is called.
- **Fix (P0):** wrap the log call itself in a try/except OR use
  `short_err()` (recommended, per Buglist). Also add
  `return_exceptions=True` to the `gather` so a per-section router
  crash never cross-contaminates other sections.

### D.4 Fan-out judge blindness + no code cap → 300 s timeouts — 🟠 HIGH (→ Bug #9)

- `_judge_fanout` (`agents/drafting.py:1200-1280`) sees only `query`,
  `reference_excerpt`, `user_language_name`, `depth_directive`. It cannot
  see:
  - `state["messages"]` / chat history → can't detect polish/redraft
    follow-ups.
  - `user_facts` (raw upload) → blind to source size + shape.
  - Any explicit fan-out cap.
- `_FanoutStrategy.sections` has an inline comment "soft-capped at 15 by
  the prompt; no code-level cap" (`drafting.py:1041`). The LLM regularly
  emits >15 on polish prompts.
- Sectionwise pair execution is **strictly sequential**
  (`drafting.py:1560 while i < total`, no `asyncio.gather` across
  pairs). 14 sections = 7 sequential Pro calls × 25-40 s = 175-280 s
  just for generation, then self_refine adds 30-90 s more.
- **Fix (P1):** three-pronged.
  1. Hard cap: `sections = sections[:12]` after judge returns.
  2. Parallelise pairs where `prior_text` dependency isn't semantic
     (start with heading-only pairs; keep body-continuation pairs
     sequential). `asyncio.gather` with a per-pair semaphore(3).
  3. Feed `state["messages"][-4:]` into the judge prompt so it can
     detect "polish this / in Marathi / make more formal" and
     shortcut to a rewrite of the last AI turn.

### D.5 Preflight budget scope gaps — 🟠 HIGH

- `agents/drafting.py:1662-1684` only detects Devanagari via
  `ऀ`-`ॿ` (U+0900-U+097F). Bengali (`ঀ`-`৿`), Tamil (`௦`-`௿`), Telugu,
  Kannada, Malayalam, Gujarati, Gurmukhi, Odia all tokenise at similar
  density and get the wrong 3.5 M Latin budget. A 3 M-char Kannada PDF
  passes the guard, then 400s at generation.
- Budget is only checked against `user_facts`. Ignores `reference_draft`
  (20-100 KB), `gathered_context` (up to 9.6 KB), and system prompt
  (~40 KB). Aggregate can exceed budget while `user_facts` alone passes.
- **Fix (P1):**
  1. Extend the script detection to all 9 dense Indic scripts.
  2. Compute budget over `user_facts + reference_draft + system_prompt`
     total. Reserve 300 KB for overhead.

### D.6 `user_context` truncated to 30 000 chars — 🟠 HIGH

- `agents/drafting.py:1815` — `fact_blocks.append(f"[Pasted context]\n
  {user_context[:30000]}")` violates invariant #3 (from CLAUDE.md
  Drafting invariants). Pasted context past 30 K chars is silently
  dropped. `feedback_preserve_user_query` memory says no truncation.
- **Fix (P0):** remove the `[:30000]` slice OR make it opt-in via an
  intent field with a warning event emitted when truncation occurs.

### D.7 Queue-status + progress SSE gaps — 🟡 MEDIUM

- `agents/drafting.py:1839` — `_AGENT_SEMAPHORE.locked()` is race-prone;
  two simultaneous arrivals when 1 slot is free both see `not locked()`,
  neither emits `queue_status`, one blocks silently on `acquire()`.
- Queue-status is emitted once with no "still queued" heartbeat.
- self_refine emits one "Auditing draft..." event for a 30-60 s window
  with no intermediate progress.
- **Fix (P2):** replace `.locked()` heuristic with a wrapping context
  manager that emits `queue_status` on every `await acquire()` that
  didn't return immediately. Heartbeat every 15 s.

### D.8 Silent per-pair failure corrupts continuity — 🟡 MEDIUM

- `agents/drafting.py:1620-1626` — a failed pair sets `pair_text = ""`
  and the loop continues. `prior_text` for the next pair only includes
  `completed`, so the next pair loses cause-title / party-labels
  established by the failed pair.
- **Fix (P2):** align with the "Partial draft warning banner" proposed
  in Buglist Phase 2. Include per-section retry (one retry with 2×
  timeout) before giving up.

---

## Part E — Dead code, orphans, doc drift

### E.1 Deleted-endpoint doc drift — 🔴 CRITICAL

Already listed at A.1. Frontend + 7 doc files still reference
`/pyapi/continue_draft`. Fix in the same PR as the frontend removal.

### E.2 Orphaned admin/internal endpoints — 🟡 MEDIUM

Endpoints with zero references in `frontend.html`, `tests/`, or
`.github/workflows/`:

- `GET /pyapi/integration/poll` (`gateway.py:1087`) — no local caller;
  presumably a Word add-in / FSD dep. If yes, whitelist; if no, delete.
- `DELETE /pyapi/delete_vectordb/{unique_string}` (`gateway.py:1149`) —
  superseded by `DELETE /pyapi/thread/{tid}/files`.
- `GET /pyapi/thread/{tid}/files` (`gateway.py:1194`)
- `DELETE /pyapi/thread/{tid}/files` (`gateway.py:1208`)
- `GET /pyapi/health/detailed` (`gateway.py:1447`) — contains 2 Bug #1
  tripwires for an endpoint no one calls.
- `GET /pyapi/admin/fallback_logs` (`gateway.py:1540`)
- `GET /pyapi/admin/fallback_stats` (`gateway.py:1568`)
- `GET /pyapi/admin/quality_stats` (`gateway.py:1592`)

**Fix (P2):** confirm caller status via one week of access logs; delete
the confirmed-dead ones. Wrap the surviving admin endpoints with a
`?verify=true` smoke check.

### E.3 Path drift in tests — ⚪ LOW

- `tests/integration/test_api.py:285` doc-string references
  `/pyapi/admin/usage`; actual route is `/pyapi/admin/usage_stats`.
  Cosmetic.

### E.4 Dead code in agents — 🟡 MEDIUM

- **`agents/legislation.py`:** `_extract_match_phrase`,
  `QueryMetadata`, `MATCH_PHRASE_PROMPT` — ~35 LOC, never called.
- **`agents/judgment.py`:** unused imports `detect_citation`,
  `detect_case_type`, `TIMEOUT_METADATA_SEC`.
- **`agents/document.py`:** ~150 LOC of specialised-artifact scaffolding
  that no longer runs. `_pick_specialized_prompt`,
  `_llm_config_for_artifact`, `_SPECIALIZED_PROMPTS`, 8 specialized
  prompt imports, `_retrieve_docs`, `_retrieve_from_collections`,
  `_get_or_create_collection`, `_collection_has_data`, and the
  `from core.self_refine import self_refine` at line 43. Docstrings
  47-88 describe a runtime dispatch that no longer exists — actively
  misleads future maintainers.
- **`agents/sci_judgment.py:85`:** `pdf_links = []` declared, never used.

**Fix (P2):** delete in a single "dead code" PR after the P0/P1 fixes
land. Update `agents/document.py` docstring to reflect the current
raw-context flow.

### E.5 Known-removed symbols — ✅ CLEAN

Every symbol from the CLAUDE.md "must not be reintroduced" list
(`DOC_TYPES`, `SYNTHETIC_SKELETONS`, `_extract_case_facts`,
`_TABLE_INTENT_RE`, `LAWTTORNEY_API_BASE`, `localize_number`, etc.) has
zero live references in production source. Only surviving hits are
documentation comments in `CLAUDE.md` / `AGENTS.md`.

---

## Part F — Reliability infrastructure (SOTA gaps)

The single biggest lever to move error rate from ~1 % to ~0.1 %.

### F.1 No LangSmith / OpenTelemetry tracing — 🔴 CRITICAL

Every prod incident today requires grepping `agent.log` for a `request_id`
and manually reconstructing the DAG. **Fix (P0-config):** enable
`LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY` — zero code change,
gives per-node timings + LLM inputs/outputs. Highest leverage in this
audit.

### F.2 No deadline propagation — 🔴 CRITICAL

The 300 s outer timeout (`gateway.py:653`, `chat_runner.py:291`) is a
hard cutoff, not a budget. If ES takes 20 s + judge 15 s + gather 10 s +
generation 240 s = 285 s, self_refine still fires with no signal it has
< 15 s of the budget left. This is the mechanical root cause of the
Bug #9 polish-prior-draft 300 s failures.

**Fix (P1):** add `core/deadline.py` with a `Deadline` contextvar seeded
at 285 s (leaves 15 s for finalisation). Every `asyncio.wait_for` in
agents takes `min(local_timeout, deadline.remaining())`. Propagate via
`RunnableConfig.configurable["deadline"]`.

### F.3 `str(e).splitlines()[0]` crash pattern is broader than Buglist — 🔴 CRITICAL

Total sites found:

- `agents/drafting.py`: 8 (Buglist counted 8)
- `agents/orchestrator.py`: 1 (Buglist missed)
- `core/self_refine.py`: 2 (Buglist missed)
- `core/embedding_client.py`: 2 (Buglist missed)
- `core/gateway.py:1476, 1493`: 2 (admin health-detailed — Buglist
  Bug #2 adjacent)

Grand total: **~15+ sites** in production code paths. `short_err()` fix
should update all of them.

### F.4 No LLM circuit breaker — 🔴 CRITICAL

`is_es_available()` (`core/clients.py:110`) implements a circuit breaker
for ES. Nothing equivalent exists for Gemini. When Gemini has a regional
outage the drafting pipeline retries 24× per request × 2 = 48 wasted
calls before failing.

**Fix (P1):** mirror the ES pattern with `is_gemini_flash_available()`
+ `is_gemini_pro_available()`. 60 s cooldown after 5 consecutive 5xx.
On tripped: agents skip Gemini and web fallback → Scenario returns a
scoped "Gemini is degraded, using cached knowledge" reply.

### F.5 No 429 back-off — 🟠 HIGH

No code path handles Gemini 429 with exponential backoff. Under quota
exhaustion, retry-immediately-get-429-again-mark-failed. The Buglist
"Google monthly spend cap" incident (fixed non-code by raising the cap)
is the clearest evidence.

**Fix (P1):** wrap Gemini calls with `tenacity.retry(
retry=retry_if_exception_type(ResourceExhausted),
wait=wait_exponential(multiplier=2, min=1, max=60), stop=stop_after_delay(20))`.

### F.6 Checkpointer silently downgrades to MemorySaver — 🔴 CRITICAL

`core/checkpointer.py:70-76` catches ALL exceptions and downgrades to
in-memory `MemorySaver`. In prod that means silent data loss across
worker restarts. `REQUIRE_POSTGRES` env exists (used only by chat_store)
but not by the checkpointer.

**Fix (P0):** read `REQUIRE_POSTGRES` in the checkpointer and hard-fail
startup when set. Emit `checkpointer_backend{type}` gauge for
observability. Add a health probe that verifies the backend.

### F.7 Prometheus metric wiring is incomplete — 🟠 HIGH

`core/metrics.py:54` defines `agent_errors_total` but grep confirms
**zero code sites** increment it. Every "agent error rate > 1 %"
dashboard alert is dead.

Also missing:
- Per-node latency histogram (`lawtech_node_duration_seconds{node}`)
- Per-model LLM latency (`lawtech_llm_call_duration_seconds{model, agent, step}`)
- Fallback resolution outcome (`fallback_resolved_total{agent, tier, outcome}`)
- Chunk-router timeout rate
- self_refine bypass rate
- Chroma wait time histogram
- Postgres pool available/waiting gauges
- Drafting queue depth

**Fix (P1):** add the missing metrics + wire the existing
`agent_errors_total`. All are label-based; ~200 LOC total.

### F.8 No `max_requests` in gunicorn — 🟠 HIGH

`gunicorn.conf.py:21-38` — no `max_requests` / `max_requests_jitter`.
No worker-recycling protection. PDF processing + Gemini uploads cause
slow memory growth per worker.

**Fix (P1):** `max_requests=1000, max_requests_jitter=100`. Standard
gunicorn hygiene.

### F.9 Deploy pipeline gaps — 🟠 HIGH

- **No auto-rollback** on health degradation. Health poll fails at 120 s
  but service is already down.
- **Integration tests are `continue-on-error: true`.** Informational only.
- **No canary / blue-green.** Every deploy is `systemctl restart`.
- **No post-deploy smoke workflow** that hits `/pyapi/search/stream`
  end-to-end with a real query.
- **No rollback workflow.** Requires `git revert && push && deploy-prod`.

**Fix (P2):** add `prod-post-deploy-smoke.yml` that runs 5 canonical
queries (one per agent) against `api.lawttorney.com` after
`deploy-prod.yml`. Fail the deploy on any 500. Add
`prod-rollback.yml` that redeploys the previous known-good tar.

### F.10 State bloat on multi-turn threads — 🟠 HIGH

`messages`, `chat_history`, `agent_results`, `source_registry` grow
per turn without eviction. Add per-thread TTL: prune `agent_results`
after turn N, keep only last-K in `messages`, expire threads > 30 d.

### F.11 Log-level default DEBUG in prod — 🟡 MEDIUM

`core/settings.py:189 LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")`.
Swap default to `INFO`. Cuts log volume 5-10×.

### F.12 `flag_hallucination` orphan — 🟡 MEDIUM

`tools/shared/guardrail_tools.py:105-141` — defined but never wired
into any agent or `self_refine`. Either delete or wire.

### F.13 Injection defense narrow — 🟡 MEDIUM

`agents/guardrail.py` only checks user input. Chat history + uploaded
file text (a common bypass vector) are not scanned. Add a Flash Lite
classifier before drafting for these inputs.

### F.14 Only 2 languages have ceremonial-block coverage — 🟡 MEDIUM

`core/language.py:489-556` — Hindi + Marathi. The other 12 supported
languages guess ceremonial forms. Extend for Bengali, Tamil, Telugu,
Kannada, Malayalam, Gujarati, Punjabi, Urdu, Odia, Assamese.

### F.15 Quality scorer excludes Drafting — 🟡 MEDIUM

`core/quality.py:27-30 _SCOREABLE_AGENTS` deliberately excludes Drafting.
No signal on drafting regression between deploys. Enable Drafting
scoring on a 5 % sample.

---

## Part G — Ranked action list

### 🔴 P0 — ship today, ideally in the Bug #1 fix PR

| # | Action | Files | LOC |
|---|---|---|---|
| G-1 | Add `short_err()` in `core/logger.py` and replace ~20 crash sites (Buglist Bug #1 + new sites in orchestrator, self_refine, embedding_client) | `core/logger.py`, `agents/drafting.py`, `agents/orchestrator.py`, `core/self_refine.py`, `core/embedding_client.py`, `core/gateway.py` | ~40 |
| G-2 | Orchestrator guard: skip web fallback when `task=Drafting` AND `has_files` — return the friendly retry message from Buglist Bug #5 | `agents/orchestrator.py:1582-1602` | ~15 |
| G-3 | Fix `_error_response` flipped arg order in 20 gateway sites | `core/gateway.py:1666-2043` | ~20 mechanical |
| G-4 | Frontend: remove `/pyapi/continue_draft` fetch + UI, or reroute it to `/pyapi/chat` with prior AI turn as `user_facts` | `frontend.html:3772` + `README.md`, `AGENTS.md`, `CLAUDE.md`, `SYSTEM_MAP.md`, `API_DOCUMENTATION.md` | ~20 |
| G-5 | Add `asyncio.wait_for` around SCI + GST + memory + non_legal LLM calls | `agents/sci_judgment.py`, `agents/gst_judgment.py`, `agents/memory.py`, `agents/non_legal.py` | ~15 |
| G-6 | Bare `except: pass` → logged warning in judgment.py:415 + document.py:203 | 2 sites | ~10 |
| G-7 | Remove `user_context[:30000]` truncation (drafting invariant #3) | `agents/drafting.py:1815` | ~5 |
| G-8 | Add `asyncio.wait_for` around self_refine critic + refiner LLM calls (also add `return_exceptions=True` to the sectionwise `asyncio.gather` in drafting) | `core/self_refine.py:1426, 1487`, `agents/drafting.py:1578` | ~15 |
| G-9 | Enable LangSmith tracing via env vars | `.env.production` | 2 |
| G-10 | Checkpointer: fail hard when `REQUIRE_POSTGRES=1` and Postgres is unreachable | `core/checkpointer.py:70-76` | ~10 |

### 🟠 P1 — ship this week

- G-11 Add `Deadline` propagation (`core/deadline.py`) + threading into `asyncio.wait_for` calls in agents. ~120 LOC.
- G-12 Wrap `_handle_integrations` in `asyncio.wait_for(60)`. `chat_runner.py:96-174`.
- G-13 Total wall-clock budget on `/pyapi/chat` file processing. ~30 LOC.
- G-14 Fix cached-response SSE schema drift. `chat_runner.py:238`. ~10 LOC.
- G-15 Wire `agent_errors_total` at every agent `except` site. Add per-node latency histogram + LLM latency histogram. ~200 LOC across metrics.py + agents.
- G-16 Add Gemini circuit breaker mirroring the ES pattern. `core/clients.py` + call sites. ~60 LOC.
- G-17 Add 429 backoff via `tenacity` in `agent_fallback.py` + streaming.py. ~40 LOC.
- G-18 Fan-out cap: `sections = sections[:12]` after judge. Feed `state["messages"][-4:]` into judge prompt. Parallelise heading-only pairs. `agents/drafting.py`. ~80 LOC.
- G-19 Extend Devanagari-only budget detection to all 9 dense Indic scripts. Compute over `user_facts + reference_draft + system_prompt`. ~20 LOC.
- G-20 Add `max_requests=1000, max_requests_jitter=100` to `gunicorn.conf.py`.
- G-21 ES calls in `legislation.py` and `constitution_maxim.py` — add `request_timeout=8`.
- G-22 Log-level default `INFO` in production.

### 🟡 P2 — ship this month

- G-23 Per-agent partial-retry with 2× timeout on failed section pair (Buglist Phase 2).
- G-24 Partial-draft warning banner emission from drafting.
- G-25 Delete confirmed-dead endpoints (`delete_vectordb`, `admin/*` orphans, `health/detailed`) after 1 week of access log review.
- G-26 Dead code sweep in `agents/legislation.py`, `agents/judgment.py`, `agents/document.py`, `agents/sci_judgment.py`.
- G-27 Injection classifier before drafting on chat_history + user_facts.
- G-28 Per-thread state TTL (prune `agent_results`, cap `messages`, expire threads).
- G-29 Post-deploy smoke workflow (`prod-post-deploy-smoke.yml`) + rollback workflow (`prod-rollback.yml`).
- G-30 Extend ceremonial-block coverage in `core/language.py` to 10 more Indic languages.
- G-31 Wire Drafting into `_SCOREABLE_AGENTS` at 5 % sample.
- G-32 Chunk-router queue-status heartbeat + `queue_status` race fix.
- G-33 Add web fallback to SCI + GST ReAct agents.
- G-34 HTML strip on token events (defense-in-depth vs `<script>`).

### ⚪ Design conversations needed

- Bug #9 polish-prior-draft — options A/B/C in Buglist. Consider using
  `/pyapi/chat` regen + prior AI turn as `user_facts`.
- Bug #10 chunking feature — decide A/B/C after G-1 fixes the crash.
- Response cache + Redis-backed feature-flag toggle service.
- A/B / holdout for prompt regression detection.
- Blue-green deploy or canary via nginx `split_clients`.

---

## Appendix — validation test seeds

Every fix should ship with a regression test. Suggested test files (some
already exist per Buglist §Validation dataset):

- `tests/test_short_err.py` — `TimeoutError()` → `"TimeoutError"`,
  `RuntimeError("boom")` → `"RuntimeError: boom"`,
  `RuntimeError("line1\nline2")` → `"RuntimeError: line1"`.
- `tests/test_orchestrator_fallback_guard.py` — all-agents-empty +
  `has_files=True` → NO `web_search_fallback` call.
- `tests/test_agent_fallback_none_parts.py` — Gemini
  `candidates[0].content.parts = None` → graceful empty result.
- `tests/test_gateway_error_response_args.py` — hit every 400/404
  branch in the 7 misfiring endpoints, assert `status_code` + body
  shape.
- `tests/test_self_refine_critic_timeout.py` — mock critic to raise
  `TimeoutError()`; assert self_refine returns `(response, [])` with a
  `self_refine_skipped_total{reason="critic_timeout"}` increment.
- `tests/test_drafting_chunking_gather_isolation.py` — mock one
  section's router to raise; assert other sections still complete
  (validates `return_exceptions=True`).
- `tests/test_deadline_propagation.py` — seed a `Deadline(30s)`, run
  a fake agent with 3 sequential `wait_for(20)`; assert third call
  gets `wait_for(<10s)`.
- `tests/test_react_agent_wait_for.py` — mock SCI ReAct to hang; assert
  90 s cap fires and topic-search fallback runs.

---

## Signed off

Audit conducted 2026-08-02. Parallel passes: endpoint & streaming
(agent a151caea6d), per-agent (aa85c0ee68), dead code (Explore), drafting
+ self_refine (a31a847eb5), SOTA gaps (a5e2e02437). Consolidated into
this file for hand-off. Reference: `Buglist/prod_bug_inventory_2026-07-29.md`
for the underlying open bugs — do not duplicate that file's items when
scoping PRs.
