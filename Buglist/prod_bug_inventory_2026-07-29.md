# Lawtech-AI — Prod Bug Inventory (2026-07-29)

**Snapshot from 25.5-hour prod audit** since restart at `2026-07-31 06:18:15 UTC`.
Total ERR lines in window: 713. Stream errors visible to users: 0 / 1157 completions.

Prod is externally healthy — no user-visible stream errors — but 8 real code
bugs are burning under the surface. This doc lists every problem, ranked by
user impact, with the actual prod request that caused each one so the fix
can be validated end-to-end.

---

## Status legend

- 🔴 **OPEN** — real code bug, no fix shipped, causes visible or latent damage
- 🟢 **FIXED** — shipped to prod, verified silent in latest audit
- 🟡 **INFRA** — external (Google / OS / network); we handle it, no fix possible
- ⚪ **DEFERRED** — real issue, architectural, needs bigger design discussion

---

## OPEN BUGS

### 🔴 Bug #1 — Drafting: `list index out of range` on any Gemini timeout

**Occurrence:** 48 times in 25.5h (~2/hour)
**Files:** `agents/drafting.py:1172` (primary) + 17 sibling sites in 6 files
**Severity:** HIGH — silently converts a small timeout into a whole-agent failure

#### What it is (simple)

Our chunk-router code logs errors with this line:

```python
error=str(e).splitlines()[0][:200]
```

Read it as: "turn the error into text, split by lines, take the first line."

**But timeouts have no message.** `str(TimeoutError())` is `""` (empty string).
- `"".splitlines()` returns `[]` (empty list)
- `[][0]` raises **`IndexError: list index out of range`**

So every time Gemini takes >20 seconds and our chunk router times out, the
code meant to log the timeout crashes with a NEW error — which then escapes
all the local fallbacks and takes down the entire Drafting agent.

#### Why it matters (user impact)

Look at the fallback ladder:

```
Chunk router times out         → this is FINE, we have a fallback
  ↓
Try to log "chunk router failed" → CRASH here (list index bug)
  ↓
Drafting agent fails entirely  → returns empty content
  ↓
Orchestrator: "all agents empty" → fires generic web search
  ↓
User sees a generic web-search article that IGNORES their uploaded documents
```

A 20-second Gemini slowness produces a completely wrong user experience.

#### Real prod scenario

**`req=cc7845f-` at 2026-07-31 06:29:34 UTC** (endpoint `/pyapi/search/stream`)

- **User prompt:** `"attched pdf files and word files and make a writ of mandamus on above information and attched pdf an…"` (13,654 chars)
- **Uploaded files:** 4 PDFs (`part_01_prasad_avachat_case_petition...`, `part_02...`, `part_03...`, `part_04..GROUNDS_only`) totalling 1,067,666 chars
- **What happened:**
  1. Fan-out judge selected 8 sections
  2. Chunk router for section 1 (`WRIT PETITION NO. OF 2026`) → OK, picked 188 chunks of 2811
  3. Chunk router for section 2 (`IN THE HON'BLE HIGH COURT OF JUDICATURE `) → 20-second Gemini timeout
  4. The router's `except` handler crashed logging the timeout → `list index out of range`
  5. Drafting agent failed → returned empty
  6. Document agent also timed out (90s) → returned empty
  7. Orchestrator fired web-search last resort → returned a 23,628-char generic essay
- **What the user got:** A generic web article about writs of mandamus that had nothing to do with their 4 uploaded PDFs
- **What the user expected:** An actual writ drafted from their case documents

#### Fix plan

Add helper `short_err(e)` in `core/logger.py` that:
- Never crashes on empty message (uses `.splitlines()[0] if msg else ""`)
- Always prepends exception TYPE NAME so `TimeoutError` shows even when message is empty

Replace all 18 uses of `str(e).splitlines()[0][:200]` with `short_err(e)`.

**Effort:** 1 helper (~10 LOC) + 18 mechanical replacements + 1 regression test file.
**Blocks:** partially blocks Bug #7 (once #1 is fixed, we can safely change the "all agents empty → web search" behaviour).

---

### 🔴 Bug #2 — Document / Drafting: empty `error=` in logs when timeouts fire

**Occurrence:** 51 hits in 25.5h (26 QA + 25 Agent)
**File:** anywhere the code catches `asyncio.TimeoutError` and logs `str(e)`
**Severity:** MEDIUM — makes debugging impossible; masks true cause

#### What it is (simple)

`str(asyncio.TimeoutError())` returns `""`. When we log `error=str(e)`, the
log line reads `error=` with nothing after. When you look at prod later, you
can't tell whether it was a timeout, a null pointer, an SDK error, or what.

Same underlying flaw as Bug #1 — the difference is Bug #1 CRASHES the code,
Bug #2 just leaves a useless log line.

#### Real prod scenario

**Same `req=cc7845f-` above.** After Drafting crashed, Document agent kept
running against the same 4 PDFs. Its 90-second `asyncio.wait_for` timeout
hit and logged:

```
2026-07-31 06:31:14 | ERR | [Document] req=cc7845f- | Document QA generation failed | duration_ms=89999 | error=
2026-07-31 06:31:14 | ERR | [Document] req=cc7845f- | Agent failed | collection=... | error=
```

The `error=` is blank because `str(TimeoutError())` is empty. Without knowing
this was a timeout, someone reading the logs later can't decide whether to
raise the timeout, retry with backoff, or investigate a different failure.

#### Fix plan

Same fix as Bug #1 — the `short_err()` helper prepends `TimeoutError:` to
the log line. After the fix, the same event will read:

```
error=TimeoutError
```

**Effort:** covered by Bug #1's fix. Same PR.

---

### 🔴 Bug #3 — AgentFallback: NPE on Gemini `parts=None`

**Occurrence:** 6 hits in 25.5h
**Files:** `core/agent_fallback.py:180` and `core/agent_fallback.py:327` (2 sites)
**Severity:** MEDIUM — breaks the last-resort web-search path

#### What it is (simple)

When Gemini answers a web-grounded question, it returns a response with a
`candidates` list. The first candidate has a `content` object with a `parts`
list. Normally `parts` is a list of text pieces.

But sometimes Gemini returns `content.parts = None`. This happens when:
- Safety filter blocked the response
- Empty grounding (no web sources matched)
- Certain query languages (mixed Latin+Urdu, for example)

Our code assumes `.parts` exists and iterates it. When it's `None`, we get:

```
AttributeError: 'NoneType' object has no attribute 'parts'
```

#### Why it matters (user impact)

Web search is the LAST fallback in our chain. When it fails, the user gets
an empty response or an error. If Bug #1 also fires on the same request,
the user gets nothing at all.

#### Real prod scenario

**`req=1ee19833` at 2026-07-31 14:44:14 UTC** (endpoint `/pyapi/search/stream`)

- **User prompt (Urdu):** `چار سو تراسی بی این این ایس کیا ہے?`
  Translation: "What is Section 483 BNSS?"
- **Route:** Newacts agent (couldn't find the section; fell to web fallback)
- **What happened:** Gemini's web-grounded response for a mixed-script query returned `parts=None` (likely safety-filter related) → NPE on `.parts` → web fallback returned empty → user got no answer

#### Fix plan

Add a defensive guard at both `agent_fallback.py:180` and `327`:

```python
candidates = getattr(response, "candidates", None) or []
if not candidates:
    return _fallback_result()
content = getattr(candidates[0], "content", None)
parts = getattr(content, "parts", None) or []
```

**Effort:** ~10 LOC + 1 regression test with a mocked response object.

---

### 🔴 Bug #4 — Drafting chunk router: same crash pattern, different surface

**Occurrence:** ~85 hits in 25.5h across many different section names
**File:** `agents/drafting.py:1172` (SAME line as Bug #1)
**Severity:** HIGH — same root cause as #1, listed separately because it dominates the log noise

#### What it is (simple)

Every "Chunk router (section ...) failed" ERR line in prod is Bug #1 firing.
The chunk router calls Gemini Flash Lite with a 20-second timeout; when Gemini
takes longer, TimeoutError fires, the log line crashes on the empty message,
and Bug #1's cascade kicks in.

The reason it appears with so many different section names is that any
lengthy legal document has many section headings, and the router is called
once per section-in-pair.

Sample section headings that hit this in prod:
- `IN THE HON'BLE HIGH COURT OF JUDICATURE ` (36 hits — most common)
- `WRIT PETITION NO. OF 2026` (14 hits)
- `IN THE MATTER OF` (7 hits)
- `Introduction` (5 hits)
- `WRIT OF MANDAMUS` (4 hits)
- `Constitutional and Statutory Provisions ` (4 hits)
- 12 other section names (1-3 hits each)

#### Fix plan

Same as Bug #1 — `short_err()` helper. The 85 chunk-router failures become
graceful `TimeoutError` warnings that the caller absorbs by falling back to
raw source (which is what the code was DESIGNED to do all along).

---

### 🔴 Bug #5 — Orchestrator: generic web search disguises itself as a Drafting answer

**Occurrence:** every Drafting request where all agents empty
**File:** `agents/orchestrator.py` — "All agents empty — invoking web search last resort" branch
**Severity:** HIGH — this is the ACTUAL user-facing damage from Bugs #1, #2, #4

#### What it is (simple)

When all agents (Drafting, Document, etc.) return empty content, the
orchestrator says "let's fall back to a web search so the user gets
SOMETHING." It fires a generic Gemini + Google Search call.

The problem: the user's original request was **"draft a writ of mandamus
based on these 4 PDFs I uploaded"**. The web search knows nothing about
the PDFs, ignores them, and returns a generic essay on writs of mandamus.

The user gets a response that LOOKS like it's answering their question,
but it isn't. There's no visible signal that anything went wrong. The user
has no idea they should retry.

#### Real prod scenario

Same `req=cc7845f-`. The user got a 23,628-character essay about writs of
mandamus. Their 4 uploaded PDFs (which contained the actual case facts,
party names, and grounds) were never referenced.

The user has no way to know that the response is a generic article rather
than a draft from their documents.

#### Fix plan

Add a guard in the "all agents empty → web search" branch: **if the request
was classified as Drafting AND the user uploaded documents**, DO NOT fire
web search. Instead, return a specific temporary-failure message:

```
I couldn't complete this draft right now — the AI backend is
experiencing high load. Please try again in about 30 seconds.
Your uploaded documents are still attached to this thread.
```

The user KNOWS to retry. They don't waste time reading an irrelevant essay.

**Effort:** ~15 LOC guard + 1 regression test. Should ship in the same PR
as Bug #1 so we don't have a window where the crash is fixed but the bad
fallback still fires.

---

### 🔴 Bug #6 — ChromaDB storage timeout on very large PDFs

**Occurrence:** 7 hits in 25.5h (huge PDFs: 2108, 1750, 47, 13, 4 pages)
**File:** `core/file_processor.py` — 30-second timeout on Chroma insert
**Severity:** LOW — mostly self-inflicted by users uploading enormous documents

#### What it is (simple)

When a user uploads a huge PDF (1000+ pages), extracting the text and
chunking it into ChromaDB can take longer than our 30-second timeout. The
extraction succeeds, but the Chroma insert never completes. Downstream
agents then find `collection=None` and return errors.

#### Real prod scenario

**`req=01f1845-` at 2026-07-31 07:47:10 UTC**

- **User prompt:** `"attched pdf files and word files and make a writ of mandamus…"`
- **Uploaded file:** `rearrange_petition_headings_prasad_petition.-merged.pdf` — **1,750 pages**
- **What happened:**
  1. PDF processing extracted 3,583,410 characters
  2. Chroma insert started, but hit 30-second timeout
  3. Document agent started with `collection=None`
  4. Document agent's own 90-second timeout hit trying to Q&A the raw text
  5. Both agents returned empty → generic web search fallback

#### Fix plan

Two options:
- **A** (safe): Increase Chroma timeout to 60 seconds for docs >500 pages
- **B** (better UX): Detect the "collection=None + huge PDF" case at the
  Document agent and return a friendly message: "Your uploaded document is
  too large to index for Q&A. Please split it into smaller files."

Recommend **B** — the size is the real issue, not the timeout.

**Effort:** ~15 LOC.

---

### 🔴 Bug #7 — Legislation ES: `too_many_clauses` on very long queries

**Occurrence:** 2 hits in 25.5h (Gujarati case-number query, both times same query)
**File:** `agents/legislation.py` — `_search_legislation` inner variation loop
**Severity:** LOW — already caught with `continue`, not user-visible

#### What it is (simple)

Elasticsearch's Lucene backend limits any query to 1,024 boolean clauses
(a hard-coded ceiling). Our query builder for Legislation can produce more
than 1,024 clauses when the user's search text is very long or contains
many terms with wildcards.

#### Real prod scenario

**`variation=ફોજદારી કેસ નંબર - ૧૭૨૪/૨૦૨૨ રજુ તારીખ : ૨૭/૦૭/૨૦૨૨ દાખલ તાર…`**

The user submitted a Gujarati case identifier as a search variation. Our
variation-builder expanded it into ~1,400 clauses. Lucene rejected it.

#### Fix plan

Cap the number of variation clauses we generate to 800 (leaves headroom).
Or: pre-check query length and skip variation expansion if the source query
is already very long.

**Effort:** ~5 LOC. Low priority — already gracefully handled.

---

### 🔴 Bug #8 — Empty-error signatures in AgentFallback / Scenario

**Occurrence:** 7 hits total (3 AgentFallback empty + 2 Drafting empty + 1 Orchestrator + 1 Scenario)
**File:** various `except Exception: log.error(..., error=str(e))` sites
**Severity:** MEDIUM — same "unknowable cause" issue as Bug #2

#### What it is (simple)

Same underlying pattern as Bug #2, but in different call sites (AgentFallback
web-search wrappers and Scenario's Flash rescue path). When the wrapped
exception has empty `str()`, we log `error=` with nothing after and can't
diagnose later.

#### Fix plan

Covered by the `short_err()` helper from Bug #1. Once shipped, these logs
will show the exception type.

---

## DEFERRED (architectural — need bigger discussion)

### ⚪ Bug #9 — Drafting: 300-second stream timeout on "polish previous draft" follow-ups

**Occurrence:** 5 hits in 25.5h (4 on `/search/stream`, 1 on `/chat`)
**Severity:** MEDIUM — user sees generic "try again" error banner

#### What it is (simple)

When a user completes a drafting request and then sends a short follow-up
like "make this more formal" or "in Marathi" or "polish this", the Drafting
agent has no code path to consume the previous draft as an input source.
Instead, it re-generates from scratch with only the follow-up query — which
often expands into a 14-section fan-out that blows past the 300-second stream
timeout.

#### Real prod scenario

**`req=97ebe79a` at 2026-07-31 13:02:22 UTC**

- **User prompt:** `"Draft this in a very sysetmatic legal language used in court"`
  (a follow-up on a prior turn's draft)
- **Fan-out judge decided:** 14 sections
- **Duration:** 300,000ms — hit the ceiling and returned an error banner to the user

#### Deferred fix design

Documented in the memory file
`project_polish_prior_draft_gap_2026_07_26.md`. Options:
- **A** (targeted): detect demonstrative references ("this", "above", "previous")
  and prepend the prior AI turn's content into `user_facts`
- **B** (broader): always surface the last AIMessage as user_facts if it looks
  like a drafting response
- **C** (endpoint): re-introduce a specific "regenerate this draft" flow

Not shipping today — needs design conversation.

---

### ⚪ Bug #10 — Drafting per-section chunking (`DRAFTING_PER_SECTION_CHUNKING`) is silently failing

**Occurrence:** ~85 hits (same as Bug #4 — chunk router timeouts)
**Severity:** LOW after Bug #1 is fixed — the feature will gracefully degrade

#### What it is (simple)

The `DRAFTING_PER_SECTION_CHUNKING` feature (opt-in, enabled in prod) is
supposed to reduce Gemini token usage by asking a Flash Lite router which
paragraph chunks each section needs. Currently the router times out
frequently, and while it's DESIGNED to fall back gracefully, Bug #1's
crash was blocking that graceful path.

#### After Bug #1 fix

The router will still time out at similar rates, but the fallback (use raw
source unchanged) will actually work. Question is whether the feature
provides net value if it's timing out >50% of the time.

#### Deferred question

Do we want to:
- **A** — keep the feature ON, let it gracefully degrade when timeouts fire
- **B** — turn off `DRAFTING_PER_SECTION_CHUNKING` in prod env until the
  router latency is investigated
- **C** — increase the router timeout from 20s to 40s

Ship after we see post-fix behaviour. Currently the feature isn't harmful
once Bug #1 is fixed.

---

## INFRA (Google-side — no code fix)

### 🟡 Google Vision OCR timeouts and 504s

**Occurrence:** ~510 hits in 25.5h (~72% of all ERR noise)
**Signatures:**
- `OCR batch failed after retries — 504 DEADLINE_EXCEEDED (request timed out)` — 274
- `OCR batch failed after retries — 504 DEADLINE_EXCEEDED (Stream cancelled)` — 154
- `Vision OCR timed out for scanned/garbled PDF` — 35
- `Image Vision OCR failed 504` — 8
- `Image OCR timed out` — 3
- Various one-off SSL and connection errors

**What it is:** Google Vision's OCR service is transiently overloaded/slow.
Our code retries with backoff, and falls back to the text layer of the PDF
(degraded but usable) if all retries fail.

**User impact:** Some scanned PDFs come out with partial or degraded text.
The user still gets a response, but the quality on scanned docs is lower.

**Fix:** No code fix. This is Google Cloud infra.

---

## FIXED (for reference — shipped in prior deploys)

### 🟢 `TypeError: 'bool' object is not subscriptable` (logger)

**Commit:** `cfd24d2` (deployed 2026-07-28 17:27 UTC)
**Was:** Every `log.error(..., exc_info=True)` call site (~38) was crashing
inside the formatter because `StructuredLogger._log` didn't coerce
`exc_info=True` to `sys.exc_info()` before calling `makeRecord`.
**Verified:** 0 hits since deploy.

### 🟢 Postgres NOT NULL / NUL bytes in `thread_files`

**Commits:** `d32c5b3` (NUL bytes) and earlier fix for NOT NULL constraint
**Was:** `pf.gemini_uri = None` was passed to Postgres INSERT with an implicit
`None` value; PG rejected the NOT NULL column. Then later, OCR of scanned
PDFs produced NUL bytes that Postgres also rejected.
**Verified:** 0 hits since deploy.

### 🟢 Newacts + Legislation + Judgment ES compile / class_cast errors

**Commit:** `fb2c1dd` (deployed 2026-07-28 17:22 UTC)
**Was:** ES search failures with `compile error` or `class_cast_exception`
were escaping the guards; the outer generic handler killed the whole agent.
**Now:** guards broadened to catch these; agent falls back to BM25 / topic
search / web fallback gracefully.
**Verified:** 0 ERR-level occurrences since deploy (only WRN-level fallback
notices that are handled internally).

### 🟢 Drafting: 1M-token input overflow

**Commit:** `b3a18fb` (deployed 2026-07-28 17:22 UTC)
**Was:** Uploading very large documents caused every section-pair call to
fail with `400 INVALID_ARGUMENT (input token count exceeds maximum)`.
Result: empty draft with 195+ error log lines per bad request.
**Now:** preflight check in `_generate_draft` returns a friendly
"docs too large" message BEFORE any generation call fires. Uses a 3.5M-char
budget for Latin scripts, 2.4M for Devanagari (denser tokenization).
**Verified:** 0 hits on the crash path since deploy. The preflight fires
correctly when triggered (fired 10 times in the audit window).

### 🟢 Document agent: friendly overflow message

**Commit:** `581a920` (deployed 2026-07-26 19:16 UTC)
**Was:** Document Q&A on very large uploaded PDFs returned raw SDK error
strings to the user.
**Now:** catches the 1M-token overflow specifically and returns an actionable
message asking the user to narrow the question or upload smaller documents.
**Verified:** 0 hits on the crash path. Some ERR lines still fire (they log
the raw SDK error BEFORE the catch runs), but the user sees the friendly
message.

### 🟢 Google monthly spend cap

**Not a code fix.** User raised the cap at ai.studio/spend around
`2026-07-29 03:35 UTC`. Before that ~7,200 ERR lines/day were the SAME
`429 RESOURCE_EXHAUSTED — monthly spending cap exceeded` message across
every Gemini call site. After the cap lift, dropped to zero within ~9 minutes.

### 🟢 Orphan `_tax_appellate_detected` UnboundLocalError

**Commit:** `d28ba7b` (deployed 2026-07-25 22:03 UTC)
**Was:** A refactor left three references to a deleted local variable in
`orchestrator_plan_node`; every Drafting request errored out. Client
screenshot on 2026-07-25 20:35 IST hit this bug in the ~90-minute window
before the fix landed.
**Verified:** 0 hits since deploy.

---

## FALLBACK PLAN — how to serve the user right, going forward

Even after every bug above is fixed, we need to guarantee the user sees
useful responses. The proposal is a small phased plan.

### Phase 1 — ship today (bundled with Bug #1 fix)

1. **`short_err()` helper** in `core/logger.py` — kills Bugs #1, #2, #4, #8
2. **18 mechanical replacements** of `str(e).splitlines()[0]`
3. **Orchestrator guard** (Bug #5 fix) — when Drafting + files, do NOT fall
   to generic web search on empty results. Return a specific "temporary
   failure, please retry" message that names their uploaded files.

### Phase 2 — ship this week (small isolated PR)

4. **One retry per failed section pair** with 2× timeout before giving up
5. **Partial-draft warning banner** — when some sections fail, prefix the
   draft with `⚠ Note: sections X and Y could not be generated. Please
   retry to fill them in.`

### Phase 3 — ship later (needs design)

6. **Global degradation detector** — if Gemini timeout rate >30% in last
   5 min, fail fast on new Drafting requests with "high load, please retry"
   instead of holding the user for 3 minutes
7. **Structured recovery** — when the user retries a partial draft, cache
   the successful sections and only re-generate the failed ones

### User-facing message templates (the ONLY four messages a Drafting request
should ever produce)

| Case | Message |
|---|---|
| ✅ Full success | The complete draft |
| 🟨 Partial (some sections failed) | Draft + `⚠ Sections X, Y could not be generated. Retry to fill them in, or ask about those sections specifically.` |
| 🟧 Total failure, temporary | `I couldn't complete this draft right now — the AI backend is experiencing high load. Please try again in about 30 seconds. Your uploaded documents are still attached.` |
| 🟥 Total failure, structural | Existing specific messages (docs too large, quota exceeded, etc.) |

---

## Prioritization summary

| Priority | Bug | Effort | Kills |
|---|---|---|---|
| P0 — ship today | Bug #1 + #2 + #4 + #8 (all `short_err()`) | ~30 LOC + test | ~140 log-line errors/day + prevents future undetected crashes |
| P0 — ship today | Bug #5 (orchestrator guard) | ~15 LOC + test | Silent web-search-instead-of-draft UX |
| P1 — this week | Bug #3 (NPE on `parts=None`) | ~10 LOC + test | 6 hits/day, blocks web fallback |
| P1 — this week | Phase 2 (retry + partial banner) | ~40 LOC + test | Turns partial failures into visible-and-recoverable UX |
| P2 — this month | Bug #6 (ChromaDB huge PDFs) | ~15 LOC | Better UX for 1000+ page uploads |
| P2 — this month | Bug #9 (polish-prior-draft gap) | Design first | Kills 5 stream timeouts/day |
| P3 — deferred | Bug #7 (ES too_many_clauses) | ~5 LOC | 2 hits/day, already gracefully handled |
| P3 — deferred | Bug #10 (chunking feature audit) | Investigation | Zero user impact after Bug #1 fix |

---

## Validation dataset (real prod scenarios to reproduce each bug)

Each bug above lists a specific `req=` from prod journalctl. Full log
lifecycle for these requests is preserved in
`C:\Users\HOME\.claude\projects\...\tool-results\b3sjch85z.txt` (local
analysis file). Use these to build regression tests that fire the exact
prompt + file combination, then assert the fixed behaviour.

Recommended tests to add alongside the fixes:

- `tests/test_short_err.py` — assert `short_err(TimeoutError())` returns
  `"TimeoutError"`, `short_err(RuntimeError("boom"))` returns
  `"RuntimeError: boom"`, `short_err(RuntimeError("line1\nline2"))` returns
  `"RuntimeError: line1"` (first line only).
- `tests/test_orchestrator_fallback_guard.py` — mock all agents to return
  empty content on a Drafting-classified request with `has_files=True`;
  assert web_search_fallback is NOT called and the temporary-failure message
  IS returned instead.
- `tests/test_agent_fallback_none_parts.py` — mock Gemini response with
  `candidates[0].content.parts = None`; assert `web_search_fallback` does
  not raise and returns a graceful empty result.
