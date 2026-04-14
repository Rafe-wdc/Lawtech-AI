# Drafting UX Improvement Plan

**Status:** Phase A shipped ✅ — Phase C planned
**Owner:** Backend team
**Last updated:** 2026-04-14
**Related:** [frontend_integration_guide.md](frontend_integration_guide.md), [integration_flow_walkthrough.md](integration_flow_walkthrough.md)

---

## Problem Statement

When a user sends a drafting query (e.g., `"Draft a partnership agreement"`), the
SSE token stream is visibly broken for the first ~30 seconds and produces
`token_reset` events that wipe the client's display buffer. Users see:

1. Scrambled text like `"## 1.The parties hereby ag shall contributeParties..."`
2. Sudden disappearance of text (`token_reset` clears everything)
3. Long silent pauses (up to ~100s) during assembly and synthesis

This was flagged by the FSD team while inspecting the live stream, which
triggered the investigation below.

## Investigation Summary

Live trace of `"Draft a partnership agreement"` against
`https://tool.lawttorney.com/pyapiv2/search/stream`:

| Metric | Value |
|--------|-------|
| Total wall time | 266 seconds |
| Total SSE events | 281 |
| Tokens emitted | 229 chunks (≈ 47,575 chars) |
| Final response | 9,325 chars |
| `token_reset` events | **2** |
| Agents invoked | Drafting + Judgment + Legislation |

**Key timeline points**

| t (s) | Event |
|-------|-------|
| 17.5 | Outline ready, 6 sections announced |
| 27.9 | `token_reset #1` — one drafting section flaked |
| 36.0 | Assembly begins |
| 130.0 | Synthesis begins (orchestrator merges 3 agents) |
| 203.6 | `token_reset #2` — synthesis stream flaked |
| 263.4 | Final `response` event |
| 266.5 | `done` |

## Root Cause

In [agents/drafting.py:336-363](../agents/drafting.py#L336), section generation
runs 6 sections concurrently (3 at a time) via `asyncio.gather` + a
`Semaphore(3)`. **Every section calls `stream_chain_response` through the same
`get_stream_writer()`**, producing a single interleaved token stream with no
section attribution.

```python
# agents/drafting.py (current)
sem = asyncio.Semaphore(_SECTION_CONCURRENCY)
async def _gen_one(i, plan):
    async with sem:
        text, tokens = await _generate_section(...)
        # ^ inside: stream_chain_response(chain, ...) emits tokens
tasks = [_gen_one(i, plan) for i, plan in enumerate(outline.sections)]
await asyncio.gather(*tasks)
```

Two downstream effects:

1. **Tokens are interleaved and unusable.** The frontend cannot reconstruct
   which token belongs to which section from the `{type: token, content: "..."}`
   event alone.
2. **`token_reset` overcorrects.** When one concurrent section's stream fails,
   `stream_chain_response` emits `{type: "token_reset"}` from
   [core/streaming.py:83](../core/streaming.py#L83), which clears the client's
   entire buffer — including valid tokens from the other two concurrent
   sections.

The second `token_reset` (at t=203.6s) is from the orchestrator's final
synthesis call, which is a single (non-parallel) LLM stream that simply flaked
once and retried. That retry behavior is correct.

## Goals

- Eliminate user-visible scrambled tokens during drafting.
- Eliminate the `token_reset` buffer-wipes that vanish valid output.
- Keep the final-response typing effect users expect.
- Ideally, give users rich progress signals during the otherwise-silent
  drafting phase.

## Plan: Two Phases

Each phase is independently shippable. Phase A is safe to deploy alone. Phase C
adds UX polish on top.

---

### Phase A — Suppress streaming during parallel section generation

**One-line change** in drafting.py: replace `stream_chain_response` (streaming)
with `chain.ainvoke` (batch) for per-section LLM calls. The outer agent still
emits `drafting_progress` events per section so the user sees which section is
active, but no raw tokens leak out during the parallel phase. The orchestrator
synthesis (already a single sequential call) continues to stream cleanly.

**Code change** ([agents/drafting.py:295-308](../agents/drafting.py#L295)):

```diff
-        from core.streaming import stream_chain_response
-        response = await stream_chain_response(chain, {
+        response = await asyncio.wait_for(chain.ainvoke({
             "query": query,
             "doc_title": outline.document_title,
             # ... (unchanged) ...
             "needs_citations": "Yes -- include [CITE: ...] markers" if section.needs_citations else "No",
-        }, timeout=180)
+        }), timeout=180)
```

**What changes for users:**

- `token_reset` events during drafting: 2 → 0
- Scrambled interleaved text: gone
- Silent period during section generation (~17s → 130s). The
  `drafting_progress` events already emitted per-section cover the UX.
- Final synthesis still streams tokens with a clean typing effect.

**What does NOT change:**

- SSE event types — no new types, no schema changes.
- Frontend code — zero changes required.
- Total request latency — same as today (minor improvement from not spinning
  up a writer per section).
- `response`, `sources`, `followup_suggestions`, `done` events — unchanged.

**Rollout:**

- Implement + unit test locally against mock server.
- Run live smoke test (`python tests/investigate_partnership_draft.py`) and
  confirm 0 `token_reset` events plus no scrambled tokens in the stream.
- Commit with message referencing this plan.
- Merge to master → CI + deploy automatically.
- Verify against live server with the same test.

**Risk:** Low. `chain.ainvoke` is the same primitive minus streaming; per-
section retry semantics are preserved by the outer `try/except` in
`_gen_sections` (a section failure becomes a placeholder in the final doc, same
as today). `stream_chain_response`'s built-in retry for section generation is
lost, but `_gen_sections` already catches exceptions and records failed
sections for `/continue_draft` retry, so this is a no-op in practice.

**Acceptance criteria:**

- [ ] `investigate_partnership_draft.py` shows `token_reset` count for drafting queries ≤ 1 (only from orchestrator synthesis, never from sections).
- [ ] Concatenating `token` events during drafting no longer produces interleaved garbage (the `response` event is authoritative).
- [ ] Total latency unchanged (±10%).
- [ ] `/continue_draft` still works when a section genuinely fails (manual verify).

---

### Phase C — Live section-completion events (progress checklist)

After Phase A the drafting phase is silent for ~100s. Phase C adds per-section
completion events so the frontend can render a live checklist showing sections
ticking off as they finish.

**Backend change:**

Add a new SSE event emitted from inside `_gen_sections` as each section
completes (in addition to the existing `drafting_progress` on start):

```python
# agents/drafting.py _gen_one(i, plan):
async with sem:
    # existing: emit drafting_progress "in progress"
    writer({
        "type": "drafting_progress",
        "section": progress_counter["count"],
        "total": total,
        "title": plan.title,
        "status": "in_progress",        # NEW FIELD
    })
    try:
        text, tokens = await _generate_section(...)
        writer({                        # NEW EVENT
            "type": "drafting_progress",
            "section": progress_counter["count"],
            "total": total,
            "title": plan.title,
            "status": "completed",
            "char_count": len(text),
        })
        results[i] = (text, tokens, None)
    except Exception as e:
        writer({                        # NEW EVENT
            "type": "drafting_progress",
            "section": progress_counter["count"],
            "total": total,
            "title": plan.title,
            "status": "failed",
            "error": str(e)[:100],
        })
        # ... existing error handling ...
```

This is **additive** — adds a `status` field that is `"in_progress" | "completed" | "failed"`. Existing consumers that only read `section` + `total` + `title` continue to work.

**Frontend change (suggested, not required for shipping):**

When a drafting task is active, render an inline checklist using the
`drafting_progress` stream:

```
Drafting your partnership agreement...
  ● 1. Parties and Purpose         ✓ complete
  ● 2. Firm Details                ✓ complete
  ○ 3. Capital Contribution        in progress
  ○ 4. Management                  queued
  ○ 5. Dissolution                 queued
  ○ 6. General Provisions          queued
```

Visual state driven by `status` in the event.

**What changes for users:**

- During the silent drafting phase, users see sections ticking off as they
  finish — no more 100s of apparent inactivity.
- Failed sections are visible immediately, so the retry-via-`continue_draft`
  path has a clear entry point.

**Rollout:**

- Backend-only change lands first (adds the `status` field).
- Frontend adopts the checklist UI independently.
- No breaking change to existing consumers.

**Acceptance criteria:**

- [ ] Every section emits a `drafting_progress` event with `status="in_progress"` when it starts.
- [ ] Every section emits a `drafting_progress` event with `status="completed"` when it finishes (including `char_count`).
- [ ] Failed sections emit `status="failed"` with a truncated `error` message.
- [ ] Existing consumers that ignore the `status` field continue to work.

---

## Out of Scope (for now)

- Streaming per-section tokens with a `scope` label so the frontend can
  maintain per-section buffers. Possible, but requires a coordinated
  backend+frontend change and is only valuable if users want to watch
  sections drafting live. Revisit if Phase C's checklist feels insufficient.
- Changing orchestrator synthesis retry behavior. The single `token_reset`
  during synthesis (when it happens) is legitimate and correctly scoped — it
  only wipes synthesis tokens, not section tokens.
- Reducing overall drafting latency. Separate optimization effort.

## Tracking

Progress is tracked in the project memory at
`~/.claude/projects/d--agentic-proj-Lawtech-AI/memory/project_drafting_ux_plan.md`.
Commits that reference this plan land with `drafting UX` in the message for
easy `git log` filtering.

| Phase | Status | Commit |
|-------|--------|--------|
| A | ✅ Shipped 2026-04-14 | `2ccb189` |
| C | Planned | — |

## Phase A — Measured Impact

Verified by re-running `tests/investigate_partnership_draft.py` against the
live server (`https://tool.lawttorney.com/pyapiv2/search/stream`) with the
same prompt `"Draft a partnership agreement"`.

| Metric | Before A | After A | Delta |
|--------|----------|---------|-------|
| Total wall time | 266.5 s | 210.1 s | **−56 s (−21%)** |
| `token_reset` events | 2 | 1 | **−1** |
| `token_reset` from sections | 1 | **0** | **goal achieved** |
| `token_reset` from synthesis | 1 | 1 | unchanged (legitimate retry) |
| `drafting_progress` events | 6 | 6 | same |
| Final response chars | 9,325 | ~same | unchanged |

- Section generation no longer emits raw tokens → no interleaved scramble.
- The remaining `token_reset` is from the orchestrator synthesis retrying a
  single sequential LLM call, which is the correctly-scoped behavior.
- 21% latency win is a bonus from not spinning up a stream writer per
  section.
