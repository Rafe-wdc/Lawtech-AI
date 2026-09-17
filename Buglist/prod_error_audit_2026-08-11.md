# Lawtech-AI — Prod Error Audit (2026-08-11)

**Snapshot window:** 16.5 hours since restart at `2026-08-10 20:33:05 UTC`
through audit time `2026-08-11 13:01:35 UTC`.

**Health at a glance:**

| Metric | Value | Vs. 2026-07-29 audit |
|---|---|---|
| Stream completions | 818 | ↓ from 1157 (shorter window) |
| Stream errors visible to users | **12** | ⚠ ↑ from 0 (new "terminating connection" pattern) |
| Total ERR lines | 294 | ↓ from 713 (fixes holding; ~59% reduction) |
| Distinct error signatures | ~80 | — |

Complete error list follows. See
`Buglist/prod_bug_inventory_2026-07-29.md` for the fix backlog and
fallback plan for the recurring bugs listed here.

---

## Status legend

- 🔴 **User-visible / high-impact** — reaches the browser or breaks a workflow
- 🟠 **Real code bug / open** — internal error, needs a fix
- 🟡 **Infra / handled** — Google or DB side; retries or fallbacks cover it
- ⚪ **NEW pattern** — first time seen at material volume

---

## A. 🔴 User-visible stream errors (12 hits) — the ones that reach the browser

| Count | Signature | Notes |
|---|---|---|
| **10** | `[ChatRunner] Stream error \| endpoint=/pyapi/search/stream \| error=terminating connection due to administrator command` | ⚪ **NEW.** Postgres kicked our connection. Common causes: DB restart, connection pool recycle, `pg_terminate_backend()`, cloud maintenance. Worth pulling exact timestamps to see if they cluster around a DB event. |
| 1 | `[ChatRunner] Stream timed out after 300s \| endpoint=/pyapi/search/stream` | 🟠 Bug #9 in inventory doc — "polish previous draft" architectural gap. |
| 1 | `[ChatRunner] Stream timed out after 300s \| endpoint=/pyapi/chat` | 🟠 Same as above, different endpoint. |

---

## B. 🔴 Bug #3 unfixed — Gemini `NoneType.parts` NPE + web search empty errors (17 hits)

The `NoneType.parts` NPE is Bug #3 from the inventory doc — the `short_err()` + `parts=None` defensive-guard PR (Phase 1) would kill all of these.

| Count | Signature |
|---|---|
| 3 | `[AgentFallback] Web search fallback failed \| agent=Newacts \| error=AttributeError: 'NoneType' object has no attribute 'parts'` |
| 3 | `[AgentFallback] Web search fallback failed \| agent=Drafting \| error=TimeoutError` |
| 2 | `[AgentFallback] Web search fallback failed \| agent=Judgment \| error=TimeoutError` |
| 2 | `[AgentFallback] Web search fallback failed \| agent=Drafting \| error=AttributeError: 'NoneType' object has no attribute 'parts'` |
| 2 | `[AgentFallback] Web search fallback failed \| agent=Constitution \| error=AttributeError: 'NoneType' object has no attribute 'parts'` |
| 5 | `[AgentFallback] [model] + Google Search failed \| duration_ms=<N> \| error=` (empty) |

---

## C. 🟠 Gemini 1M-token input overflow (84 hits) — informational; friendly-message fires

Preflight guard shipped in `b3a18fb` fires WRN when triggered (16 times this window). The individual SDK errors are still logged BEFORE the friendly-message path runs — so they appear here but don't reach users. All 84 events are on the same handful of oversized requests.

| Count | Signature |
|---|---|
| 18 | `[Document] LLM generation ([model]) failed \| error=400 INVALID_ARGUMENT (input token count exceeds 1048576)` |
| 18 | `[Document] Document QA generation failed \| error=400 INVALID_ARGUMENT (input token count exceeds 1048576)` |
| 6 | `[Drafting] Section pair gen (sections 1-2) failed \| error=400 INVALID_ARGUMENT (1048576)` |
| 6 | `[Drafting] Section pair gen (sections 3-4) failed \| error=400 INVALID_ARGUMENT (1048576)` |
| 6 | `[Drafting] Section pair gen (sections 5-6) failed \| error=400 INVALID_ARGUMENT (1048576)` |
| 6 | `[Drafting] Section pair gen (sections 7-8) failed \| error=400 INVALID_ARGUMENT (1048576)` |
| 6 | `[Drafting] Section pair LLM call failed \| section_label=sections 1-2 \| error=400 INVALID_ARGUMENT (1048576)` |
| 6 | `[Drafting] Section pair LLM call failed \| section_label=sections 3-4 \| error=400 INVALID_ARGUMENT (1048576)` |
| 6 | `[Drafting] Section pair LLM call failed \| section_label=sections 5-6 \| error=400 INVALID_ARGUMENT (1048576)` |
| 6 | `[Drafting] Section pair LLM call failed \| section_label=sections 7-8 \| error=400 INVALID_ARGUMENT (1048576)` |

---

## D. 🟠 Chunk router failures — ~85 hits, all `error=` empty, no cascade

**Bug #1 (`list index out of range`) is now zero in this window.** The chunk router still times out at similar rates, but the fatal cascade is no longer firing. Worth verifying whether the fix landed silently or the underlying behaviour changed.

### English section names (top 10 by count)

| Count | Section heading |
|---|---|
| 6 | `IN THE HON'BLE HIGH COURT OF JUDICATURE ` |
| 5 | `Facts of the Case` |
| 4 | `Prayer` |
| 4 | `IN THE MATTER OF` |
| 3 | `Verification` |
| 3 | `IN THE MATTER OF ARTICLE 226 OF THE CONS` |
| 3 | `Grounds for Writ of Mandamus` |
| 3 | `Grounds for Relief` |
| 3 | `Advocate Details` |
| 2 | `WRIT PETITION UNDER ARTICLE 226 OF THE C` |
| 2 | `Shri. Prasad Nandkumar Avachat ...PETITI` |
| 2 | `VERSUS` |

### Marathi devanagari section names (⚪ NEW multilingual signal)

| Count | Section heading |
|---|---|
| 2 | `लेखी युक्तिवाद` (written arguments) |
| 2 | `महसूल फेरविचार अर्ज क्रमांक_______/२०२६ ` (revenue review application no.) |
| 2 | `प्रकरणाची पार्श्वभूमी` (case background) |
| 1 | `सत्यापन` (verification) |
| 1 | `संदर्भ` (subject/reference) |
| 1 | `विषय आणि संदर्भ` (subject and reference) |
| 1 | `माननीय महसूल मंत्री, महाराष्ट्र राज्य, म` (Hon'ble Revenue Minister, Maharashtra State) |
| 1 | `मागणी` (demand) |
| 1 | `प्रार्थना` (prayer) |
| 1 | `ठिकाण, दिनांक आणि सही` (place, date and signature) |
| 1 | `अर्जदार विरुद्ध गैर-अर्जदार` (applicant vs non-applicant) |
| 1 | `अंतरिम राहत` (interim relief) |

### Single-hit English section names (all `error=` empty)

`WRIT PETITION NO. OF 2026`, `VERIFICATION`, `Subject Matter of the Petition`, `Shri. Prasad Nandkumar Avachat Age: 42 y`, `Shri. Prasad Nandkumar Avachat`, `Revenue Revision Application No. _______`, `Respondent Details`, `Reliefs Sought`, `Preliminary Submissions`, `Petitioner Details`, `Parties to the Petition`, `PRELIMINARY SUBMISSIONS`, `PETITIONER`, `PARTIES`, `Grounds for Filing This Petition`, `GROUNDS FOR WRIT OF MANDAMUS`, `FACTS OF THE CASE`, `Subject: Written arguments on behalf of `

### The one chunk-router failure WITH a real error message

| Count | Signature |
|---|---|
| 1 | `[Drafting] Chunk router (section ADVOCATE FOR PETITIONER) failed \| error=Request deadline already exceeded; refusing to start work` |

This one tells the true story: the LangGraph / async deadline propagated into the router call. Every other chunk-router failure is likely the same shape but the exception's `str()` returned empty.

---

## E. ⚪ Gateway 240s file processing budget exceeded (20 hits) — NEW pattern

Users uploading files whose OCR + chunking can't finish in the 4-minute budget. Currently INVISIBLE to the user — file just quietly doesn't get processed and downstream agents see empty context.

| Count | File count in request |
|---|---|
| 5 | 1 file |
| 4 | 2 files |
| 2 | 5 files |
| 2 | 7 files |
| 2 | 9 files |
| 1 | 3 files |
| 1 | 4 files |
| 1 | 8 files |
| 1 | 12 files |
| 1 | 13 files |
| 1 | 15 files |
| 1 | 20 files |
| 1 | 30 files |

**Note the pattern:** 5 hits with just 1 file — single very large or garbled scanned PDF. Then a long tail up to 30 files. The single-file cases are probably the worst UX (user waits 4 minutes on one document, gets nothing back).

---

## F. 🟡 Google Vision OCR infrastructure (63 hits) — external, retries handle it

| Count | Signature |
|---|---|
| 29 | `OCR batch failed after retries \| 504 DEADLINE_EXCEEDED (request timed out)` |
| 18 | `OCR batch failed after retries \| 504 DEADLINE_EXCEEDED (Stream cancelled RPC)` |
| 7 | `Image OCR timed out` |
| 4 | `Image Vision OCR failed \| 504 (Stream cancelled)` |
| 3 | `Image Vision OCR failed \| 504 (request timed out)` |
| 1 | `OCR batch failed after retries \| The read operation timed out` |
| 1 | `Image Vision OCR failed \| 504 (Stream cancelled)` (file-scoped variant) |

Total infra noise: **63 / 294 ≈ 21%** (much lower than previous audits — 72% last time).

---

## G. 🟠 SelfRefine issues (9 hits)

| Count | Signature |
|---|---|
| 4 | `[SelfRefine] Self-refine critique failed \| error=Request deadline already exceeded; refusing to start work` |
| 3 | `[SelfRefine] Self-refine refinement failed \| error=` (empty) |
| 2 | `[SelfRefine] Self-refine critique failed \| error=` (empty) |

The 4 with "Request deadline already exceeded" indicate the request has already timed out at the outer level and self-refine is being called too late. Suggests either the outer timeout should short-circuit the self-refine loop, or self-refine should preflight-check the remaining time budget.

---

## H. 🟡 Drafting Gemini 503 UNAVAILABLE (5 hits) — transient overload

| Count | Signature |
|---|---|
| 1 | `[Drafting] Section pair gen (sections 5-6) failed \| error=503 UNAVAILABLE (high demand)` |
| 1 | `[Drafting] Section pair gen (section 3) failed \| error=503 UNAVAILABLE (high demand)` |
| 1 | `[Drafting] Section pair LLM call failed \| section_label=sections 5-6 \| error=503 UNAVAILABLE (high demand)` |
| 1 | `[Drafting] Section pair LLM call failed \| section_label=section 3 \| error=503 UNAVAILABLE (high demand)` |
| 1 | `[Drafting] Section pair blocked twice; emitting empty section \| section_label=sections 5-6` |

These represent one or two requests where Gemini backed off. Not a code bug — but worth adding a single retry per section-pair when 503 fires (Phase 2 in the fallback plan).

---

## I. 🟠 Scenario agent (2 hits)

| Count | Signature |
|---|---|
| 1 | `[Scenario] [model] Flash + Google Search failed \| error=` (empty) |
| 1 | `[Scenario] Agent failed \| error=TimeoutError` |

Same empty-error pattern; covered by the `short_err()` fix in Phase 1.

---

## J. 🟡 Legislation ES `maxClauseCount` (3 hits) — handled internally, non-user-visible

Marathi and Malayalam queries with complex clause structures exceeded Lucene's 1024-clause limit.

| Count | Variation (truncated) |
|---|---|
| 1 | `प्रमाणक क्र. ३८ दिनांक १४/०५/२०२४ अन्वये ₹ ४,५०,०००/- वनवासव` — `failed to create query: maxClauseCount` |
| 1 | `प्रमाणक क्र. ३१ दिनांक ३०/०४/२०२४ अन्वये ₹१६,०००/- पाणीपुरवठ` — `failed to create query: maxClauseCount` |
| 1 | `IN THE HIGH COURT OF KERALA AT ERNAKULAM O.P. No. _______ of` — `too_many_clauses: maxClauseCount` |

Already handled by the `continue` at the variation-loop boundary — the request still succeeds. Bug #7 in the inventory doc.

---

## K. 🟠 Drafting misc (1 hit)

| Count | Signature |
|---|---|
| 1 | `[Drafting] Translate query for ES match failed \| error=` (empty) |

Empty error — covered by `short_err()`.

---

## Category summary

| Category | Count | % of total | Nature |
|---|---|---|---|
| A — Visible stream errors | 12 | 4.1% | ⚠ 10 are the NEW "terminating connection"; 2 are known Bug #9 |
| B — Web fallback NPE + empty | 17 | 5.8% | 🔴 Bug #3 unfixed |
| C — Gemini 1M overflow | 84 | 28.6% | 🟠 Informational; friendly-message fires downstream |
| D — Chunk router (empty error) | ~85 | 28.9% | 🟠 Feature degraded but no longer crashes |
| E — 240s file budget | 20 | 6.8% | ⚪ NEW; needs user-facing message |
| F — Google Vision infra | 63 | 21.4% | 🟡 External, retries handle |
| G — SelfRefine issues | 9 | 3.1% | 🟠 4 are "deadline exceeded"; 5 empty |
| H — Drafting 503 UNAVAILABLE | 5 | 1.7% | 🟡 Transient |
| I — Scenario | 2 | 0.7% | 🟠 Empty error |
| J — Legislation maxClauseCount | 3 | 1.0% | 🟡 Handled internally |
| K — Drafting misc | 1 | 0.3% | 🟠 Empty error |
| **Total** | **294** | **100%** | |

---

## Three things that stand out in this audit

### 1. 🚨 "Terminating connection due to administrator command" (10 hits)

**First time seeing this signature.** Postgres explicitly killed 10 stream connections mid-request. Common triggers:
- Postgres restart (planned or unplanned)
- Connection pool recycling
- Cloud provider maintenance window
- `pg_terminate_backend()` called externally
- Long-running query hitting `statement_timeout`

**Action:** pull the exact timestamps to see if they cluster around a specific
event, then decide whether to add retry logic or investigate the root cause.

### 2. ⚪ 240-second file processing budget being hit (20 requests)

**New enough pattern to name.** Users uploading files (single very-large PDFs
or 20+ file batches) that can't complete OCR in the 4-minute budget. Currently
the file just quietly doesn't finish and downstream agents get empty context.

**Action (deferred):** surface a clear user-facing message: `"Your uploads
took longer than 4 minutes to process. Please try again with smaller/fewer
files."` Would need to be plumbed through the streaming response.

### 3. 🎉 Bug #1 (`list index out of range`) is silent

**Down from 48 hits in the last audit to 0.** The chunk router is still
failing at similar rates (~85 events), but the fatal cascade has stopped.

Possible causes:
- The `str(e).splitlines()[0]` pattern was patched by someone else
- A library update changed how empty exceptions serialize
- Some other change altered the call sequence

**Action:** SSH the prod box and grep the deployed `agents/drafting.py:1172`
to verify — before we ship a `short_err()` PR that assumes it still exists.

---

## Comparison with 2026-07-29 audit

| Signature class | 2026-07-29 count | 2026-08-11 count | Delta |
|---|---|---|---|
| Bug #1 `list index out of range` | 48 | **0** | ✅ Silent |
| Bug #3 `NoneType.parts` | 6 | 12 | ↑ still open |
| Chunk router failures | ~85 | ~85 | = unchanged |
| Gemini 1M overflow (informational) | ~275 | 84 | ↓ friendly-message doing its job |
| Google Vision infra | ~510 | 63 | ↓ Google side calmer today |
| Stream errors visible | 5 | 12 | ↑ mostly the new "terminating connection" |
| Total ERR lines | 713 | 294 | ↓ 59% reduction |

**Net trajectory:** Prod is meaningfully quieter (294 vs 713). Fixes are
holding. The main new concern is the "terminating connection" cluster and
the file-processing budget pattern. Bug #3 is still open and small; the
Phase 1 PR would clear it plus most empty-error signatures across categories
B, G, I, K.

---

## Recommended next steps

1. **Investigate the 10 "terminating connection" errors** — pull timestamps
   and correlate with any known DB / cluster events. If recurring, add
   connection retry logic. If one-off, note the cause and move on.
2. **Verify Bug #1 is truly silent in code** — SSH prod, grep the deployed
   `agents/drafting.py:1172`. Decide whether to still ship `short_err()`
   for the other 17 sites.
3. **Ship the Phase 1 PR** — `short_err()` helper (kills ~10-15 empty-error
   log lines) + `web_search_fallback` NPE guard (kills 12 Bug #3 hits) +
   orchestrator "no web fallback for Drafting+files" guard (Bug #5).
