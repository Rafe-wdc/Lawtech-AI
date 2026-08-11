# Follow-up Pipeline Simplification Plan

**Status:** Design doc — awaiting approval before any code is written.
**Author:** Claude (drafted 2026-08-12)
**Related docs:** `docs/drafting_simplification_plan.md`, `docs/intent_layer_implementation_plan.md`, `docs/dynamic_orchestrator_plan.md`
**Related memory:** `project_language_switch_followup_plan.md`, `feedback_preserve_user_query`

---

## 1. Motivation

Roughly 60% of user complaints in the "abrupt / useless / didn't understand me"
bucket trace back to a single failure mode: **follow-up turns get re-derived
from scratch instead of continued from Turn 1's typed state.**

The pipeline currently runs 6–10 LLM calls before any domain work on every
turn (rewriter, intent extractor, classifier, per-agent rewriter, fan-out
judge, chunk router, synthesis, critic, refiner). Each call re-derives
context from strings — and each string-based re-derivation is lossy.

Three concrete user experiences currently caused by this:

- **"in Marathi" produces a different draft than the English original.** The
  reference picker refires on Turn 2's rewritten query, may pick a different
  template, produces a document with different section ordering.
- **"Add a prayer clause" throws away Turn 1's draft.** Drafting has no
  "start from prior draft" mode; it produces a fresh draft with the new
  clause instead of Turn 1's draft with the clause added.
- **"Find related cases" on a bail draft turns into a general case-law
  search.** The intent extractor sees a short Turn-2 query + text summary
  and reclassifies `task_intent`, losing Turn 1's context.

The root cause is architectural, not a prompt bug: **there is no first-class
"conversation state" that survives the pipeline.** `chat_history` is a text
record. `summary_text` is a text compression. `file_context` persists only
Chroma collection IDs. Everything else — intent, task plan, agent results,
uploaded raw text, prior draft — gets rebuilt from those text representations
on every turn.

This doc proposes three levels of simplification, sequenced so each can ship
independently and each unlocks the next.

## 2. Non-goals

- **NOT proposing changes to first-turn behaviour.** Turn 1 works reasonably
  well; the failure mode is Turn 2+.
- **NOT proposing a new agent, retrieval source, or model.** All three
  levels are refactors of existing machinery.
- **NOT proposing to remove the existing rewriter, intent extractor, or
  classifier.** They stay as-is for the first turn and for cases where
  the sticky-state fast path doesn't apply. This is additive.
- **NOT touching Pattern B (format/depth ignored in synthesis), Pattern C
  ("unable to find" boilerplate), or Pattern E (language detection).** Those
  are separate audit items. This plan addresses Pattern A only.
- **NOT changing the SSE streaming contract.** The frontend continues to
  receive the same event types.

## 3. Current state (as of 2026-08-12)

### 3.1 What persists between turns

Persisted in SQLite (`core/chat_store.py` — `threads` and `messages` tables):

| Field | Where | Notes |
|---|---|---|
| `user_query` | `messages.user_query` | Raw Turn N input |
| `ai_response` | `messages.ai_response` | Raw Turn N output |
| `summary_text` | `threads.summary_text` | Rolling chat summary |
| `file_context_json` | `threads.file_context_json` | Serialised file context |
| `thread_files` | `thread_files` table | Per-file records (Chroma IDs) |

### 3.2 What does NOT persist

- `user_intent` (typed `UserIntent`) — `LegalAgentState.user_intent` field
  exists ([core/state.py:160](../core/state.py#L160)) but is never written
  to SQLite. Rebuilt from `_original_query + summary` every turn.
- `task` / `tasks_planned` — rebuilt via `_classify_and_plan` every turn.
- `agent_results` — populated per turn, discarded at end of turn.
- Uploaded document raw text — only `chromadb_collections` survive
  ([agents/memory.py:293-370](../agents/memory.py#L293-L370)). Agents
  re-fetch via `get_full_attachment(collection_id)`.
- `AgentResult.content` (the actual draft body) — visible only in the AI
  message field, forced through string-round-tripping if drafting wants
  it on Turn 2.

### 3.3 Existing partial-precedent for state carry-forward

`LegalAgentState.regenerate_of: str | None` ([core/state.py:189](../core/state.py#L189))
already ships. When set, the orchestrator short-circuits the full agent
pipeline and runs a single refinement call over the prior response. This
is the pattern this plan generalises — turn a special-case shortcut into
first-class conversation continuity.

### 3.4 The 6–10 LLM calls per turn today

For a Turn 2 drafting follow-up (e.g. `"in Marathi"`), current call count:

1. `_rewrite_query` (Flash Lite) — reconstruct standalone query
2. `_extract_user_intent` (Flash Lite) — typed UserIntent
3. `_classify_and_plan` (GPT-4o) — task selection
4. `_rewrite_queries_for_agents` (Flash Lite × N agents)
5. `_pick_reference_source` (Flash Lite) — template picker
6. `_translate_query_for_es_match` (Flash Lite, conditional)
7. `_gather_relevant_context` (multiple retrieval calls, not LLM)
8. `_judge_fanout` (Flash Lite) — single-pass vs sectionwise
9. `_pick_relevant_chunk_indices` (Flash Lite × N pairs, opt-in)
10. Section-pair generation (Gemini Pro × N pairs)
11. `_critique` + `_refine` (Flash + Pro, ×2 iterations)
12. Synthesis (GPT-4o) if multi-agent

Nine of these are re-deriving state that Turn 1 already produced.

## 4. The three levels

The levels are ordered by dependency, not by user impact. Level 1 is a
prerequisite for the others; Levels 2 and 3 can be done in either order
after Level 1 lands.

---

### Level 1 — Persist typed state (foundation)

**Goal:** Give the pipeline access to Turn N-1's typed decisions so it can
choose continuity over re-derivation.

#### 1.1 Schema changes

Add columns to `messages` table via ALTER migration (following the
`file_context_json` precedent at [chat_store.py:212-217](../core/chat_store.py#L212-L217)):

```sql
ALTER TABLE messages ADD COLUMN user_intent_json      TEXT NOT NULL DEFAULT '';
ALTER TABLE messages ADD COLUMN task                  TEXT NOT NULL DEFAULT '';
ALTER TABLE messages ADD COLUMN tasks_planned_json    TEXT NOT NULL DEFAULT '[]';
ALTER TABLE messages ADD COLUMN primary_artifact_kind TEXT NOT NULL DEFAULT '';
```

- `user_intent_json` — serialised `UserIntent` from that turn.
- `task` — the primary task type (e.g. `"Drafting"`).
- `tasks_planned_json` — all tasks that fired.
- `primary_artifact_kind` — `"draft"` | `"answer"` | `""` — signals whether
  the turn produced a modifiable artefact.

**Design change during implementation (2026-08-12):** the plan originally
listed a fifth column, `primary_artifact_ref` (SHA-256 fingerprint of the
draft content), as a hook for a future artefacts table. Dropped during
Level 1 implementation per the "no premature abstraction" principle —
`primary_artifact_kind == "draft"` combined with `ai_response` is
sufficient for Levels 2/3, and adding an unused column is schema debt.
If a separate artefacts table is ever needed, add the ref then.

Migration is idempotent — checks `PRAGMA table_info(messages)` for column
presence before ALTER (SQLite) or wraps each ALTER in try/rollback
(Postgres). Zero-downtime.

#### 1.2 Save path

Extend `chat_store.save_turn` ([core/chat_store.py:403](../core/chat_store.py#L403))
to accept and persist the new fields. Called from
`_finalise_turn` in the orchestrator's synthesise path.

Serialisation: `UserIntent.model_dump_json()` (already Pydantic).

#### 1.3 Load path

**Design change during PR 2 implementation (2026-08-12):** the plan
originally called for a NEW `ConversationSnapshot` dataclass. Dropped in
favour of extending the existing `ChatHistoryResult` — same role ("what
the memory agent needs from the store"), and adding a parallel dataclass
would be premature abstraction. `ChatHistoryResult` now carries:

```python
@dataclass
class ChatHistoryResult:
    # Existing
    chat_history: list[BaseMessage]
    summary_text: str
    total_turns: int
    raw_turns: list[dict]
    # PR 2 additions (Level 1)
    previous_intent_json: str = ""            # JSON string; consumer deserialises
    previous_task: str = ""
    previous_tasks_planned: list[str]
    previous_artifact_kind: str = ""          # "" | "draft" | "answer"
    previous_artifact_content: str = ""       # ai_response of the latest turn
```

`previous_intent_json` stays a raw string at the storage boundary so the
store doesn't take a `config.intent` import (would be circular through
`state.py`). `memory_node` deserialises via `UserIntent.model_validate_json`
before writing to `LegalAgentState.previous_intent`, so consumers see a
typed object.

The five previous_* fields are populated from the most-recent `messages`
row. `previous_artifact_content` is just `messages.ai_response` — we don't
duplicate storage.

#### 1.4 State plumbing

Add to `LegalAgentState`:

```python
previous_intent: Any            # UserIntent | None
previous_task: str | None
previous_artifact_kind: str     # "" | "draft" | "answer"
previous_artifact_content: str  # empty when no prior turn
```

Populate in `memory_node` from the `ConversationSnapshot`. No downstream
consumer is *required* to read these — they're optional inputs. Existing
code paths unchanged.

#### 1.5 Consumer changes (opt-in)

**Intent extractor (`_extract_user_intent` in orchestrator.py):**
- New parameter: `previous_intent: UserIntent | None = None`.
- Prompt gains a `## PREVIOUS TURN INTENT` block when populated.
- Extractor is instructed to inherit fields from `previous_intent` unless
  Turn N explicitly changes them. E.g. Turn 2 = `"in Marathi"` inherits
  `task_intent`, `legal_artifact`, `response_depth` from Turn 1; overrides
  `language` and `language_explicit`.

**Rewriter (`_rewrite_query` in memory.py):**
- New parameter: `previous_task: str | None = None`.
- When `previous_task == "Drafting"` and query is a short directive
  (< 200 chars, matches a directive-verb regex), skip the rewriter
  entirely — return the raw query. Downstream Drafting will read
  `previous_artifact_content` via Level 3.
- For other short follow-ups, existing rewriter behaviour unchanged.

**Classifier (`_classify_and_plan` in orchestrator.py):**
- New parameter: `previous_task: str | None = None`.
- When Turn N is a directive follow-up on a drafting task and the
  extractor's confidence < 0.7, prefer `previous_task` over reclassifying.
  This blocks the "Turn 2 short directive gets reclassified as chat"
  failure mode.

Every consumer change is guarded — if `previous_*` is None, behaviour is
identical to today.

#### 1.6 Tests

- Unit: `ConversationSnapshot` populates fields correctly from SQLite.
- Unit: intent extractor prompt includes `## PREVIOUS TURN INTENT` when
  populated, omits it otherwise.
- Integration: 3-turn thread with directive Turn 2 — verify
  `previous_intent` reaches drafting.
- Migration: fresh DB creates new columns; existing DB migrates
  idempotently.

#### 1.7 Rollout

1. Ship migration + save path (writes new columns but nothing reads them).
2. Wait 24h — verify writes succeed on prod, no schema errors.
3. Ship load path + `ConversationSnapshot`.
4. Wait 24h — verify `previous_*` fields populate in state.
5. Ship consumer changes one at a time (extractor → rewriter → classifier),
   with per-consumer telemetry.

#### 1.8 Blast radius

**Low.** Additive fields, additive prompt blocks, guarded consumer changes.
No existing code path breaks if `previous_*` is None.

**Cost estimate:** 1–2 days for schema + save/load + state plumbing.
Additional 1 day per consumer change. Total ~4 days.

---

### Level 2 — Drafting follow-up bypass (biggest UX win)

**Goal:** Turn 2+ drafting follow-ups skip the entire drafting pipeline and
run a single "modify prior draft" LLM call. Latency drops from ~90s → ~8s.

**Depends on:** Level 1 (needs `previous_artifact_kind == "draft"` +
`previous_artifact_content` in state).

#### 2.1 Detection

Add `_is_drafting_followup_directive` at the top of `drafting_node`:

```python
def _is_drafting_followup_directive(
    query: str, previous_artifact_kind: str, previous_artifact_content: str,
) -> bool:
    if previous_artifact_kind != "draft":
        return False
    if not previous_artifact_content or len(previous_artifact_content) < 500:
        return False
    if not query or len(query) > 300:
        return False
    return bool(_DIRECTIVE_VERBS_RE.search(query))
```

`_DIRECTIVE_VERBS_RE` covers:
- Language: `in (marathi|hindi|english|tamil|...)|translate to|मराठीत|हिंदी में`
- Length: `shorten|expand|elaborate|more concise|make (it )?longer|make (it )?shorter`
- Content: `add (a )?(prayer|verification|paragraph|clause|section|ground)`
- Format: `as a table|in bullet points|as numbered list`
- Polish: `polish|refine|improve|clean up|make (it )?formal`
- Party changes: `change (party|court|forum|address)`

The regex is intentionally lenient — a mis-hit on Turn 2 costs the user 8s
of the fast path instead of 90s of the slow path; a mis-miss costs the
slow path with no regression from today.

#### 2.2 The bypass

New function `_generate_draft_modification` in `agents/drafting.py`:

```python
async def _generate_draft_modification(
    directive: str,
    prior_draft: str,
    user_intent: UserIntent,
    user_language: str,
    user_facts: str,          # new uploads this turn, if any
) -> str:
    """One Gemini 2.5 Pro call: apply `directive` to `prior_draft`."""
```

Prompt shape (new `DRAFTING_MODIFICATION_PROMPT` in `config/prompts.py`):

```
You are modifying an existing legal draft. The user has an EXISTING
DRAFT (below) and wants you to apply a specific DIRECTIVE. Return the
COMPLETE modified draft — do not return a diff, do not return only the
changed section, do not add commentary.

## EXISTING DRAFT
{prior_draft}

## USER DIRECTIVE
{directive}

## USER INTENT DIRECTIVES
{intent_directives_block}

## NEW UPLOADED SOURCES (if any — for facts the directive references)
{user_facts}

## RULES
1. PRESERVE every party name, court name, case number, date, address,
   monetary amount, and statutory reference from the EXISTING DRAFT
   VERBATIM — unless the directive explicitly changes them.
2. PRESERVE the document type. If the existing draft is a bail
   application, return a modified bail application. Do NOT convert to
   a different document type.
3. Apply the directive fully:
   - Language switch → translate every non-anchor sentence; keep
     numerals, statute names, party names as fixed English anchors
     (per INDIAN_LEGAL_FIXED_ENGLISH_ANCHORS).
   - Length change → adjust prose density; do NOT drop entire
     sections unless the directive names them.
   - Add content → insert at the appropriate structural location;
     preserve paragraph numbering continuity.
   - Format change → reformat only; do NOT rewrite prose.
4. Return the COMPLETE draft, ready to ship. No preamble, no postscript,
   no "here is the modified version" wrapper.
```

Length: ~250 words vs the 800+ word `DRAFTING_SYSTEM_PROMPT`.

Configuration: `temperature=0.0, max_output_tokens=24000, thinking_budget=4096`
(same as single-pass).

#### 2.3 Wiring

In `drafting_node`, before the existing pipeline:

```python
if _is_drafting_followup_directive(
    query, state.get("previous_artifact_kind", ""),
    state.get("previous_artifact_content", ""),
):
    log.info("Drafting: follow-up directive fast path")
    progress("drafting", "Modifying prior draft...", step="modify")
    draft = await _generate_draft_modification(
        directive=query,
        prior_draft=state["previous_artifact_content"],
        user_intent=intent_obj,
        user_language=user_language,
        user_facts=user_facts,
    )
    draft, warnings = validate_draft(draft)
    # Self-refine SKIPPED — the prior draft already passed self-refine
    # on Turn 1; running it again reliably produces destructive shrinks
    # (see the cumulative-shrink guard in self_refine.py:1709).
    return _build_agent_result(
        draft, warnings, reference_kind="prior_turn",
        reference_source="<prior_turn:modification>",
    )
```

Below this branch, existing pipeline runs unchanged.

#### 2.4 What gets skipped in the fast path

- `_acquire_reference_draft` (ES picker + web fallback)
- `_gather_relevant_context` (parallel ES retrievers)
- `_judge_fanout` (single-pass vs sectionwise)
- `_pick_relevant_chunk_indices` (per-section chunk router)
- `_generate_sectionwise` (all section-pair calls)
- `self_refine.self_refine` (critic + refiner)

Roughly 9 LLM calls → 1.

#### 2.5 Rejection criteria

Skip the fast path (fall through to existing pipeline) when:

- Turn 2 query > 300 chars — likely a new drafting task, not a directive.
- `previous_artifact_content` < 500 chars — Turn 1 didn't produce a real
  draft (probably an error message or refusal).
- Turn 2 uploaded a NEW document — user is asking for a different draft,
  not modifying the prior one. (`FileContextData.from_state(state).has_content`
  AND turn_number == 1 for that upload batch.)
- Directive-verbs regex doesn't match.
- Fast path itself returns empty content or fails.

Each rejection logs a decision reason. Fallback to existing pipeline is
transparent.

#### 2.6 Multi-turn compounding

Turn 3's fast path modifies Turn 2's draft (which modified Turn 1's).
Guard: if `previous_artifact_content` shows a `⚠ **Draft incomplete**`
banner from the sectionwise generator, refuse the fast path and go
through the full pipeline instead — the prior draft is known-degraded.

#### 2.7 Tests

- Unit: detection function covers all directive verb families.
- Unit: rejection criteria fire correctly on edge cases (long directive,
  short prior, new upload, banner in prior).
- Integration: 3-turn thread (fresh draft → "in Marathi" → "shorten to
  one page") — verify all three turns produce a coherent progression.
- E2E prod smoke: pick 10 existing follow-up threads from `messages`
  table, replay Turn 2 with and without the fast path, compare
  latency and quality (manual review).

#### 2.8 Blast radius

**Medium.** New code path, but strictly opt-in (`previous_artifact_kind ==
"draft"`). Cannot fire on Turn 1. Cannot fire when Level 1 hasn't
populated the previous_* fields.

Worst case: fast path is wrong for a directive class and produces a bad
modification. Mitigation: rollout behind `DRAFTING_FOLLOWUP_FAST_PATH=1`
env flag, off by default in prod for the first week.

**Cost estimate:** 2–3 days after Level 1. Bulk of the work is prompt
design + eval, not code.

---

### Level 3 — Collapse the three planning calls into one

**Goal:** Replace three sequential-ish LLM calls (rewriter, intent
extractor, classifier) with one "turn planner" LLM call that produces
a typed `TurnPlan`.

**Depends on:** Level 1 (needs `previous_intent + previous_task` to
inform the planner).

#### 3.1 The new call

```python
class TurnPlan(BaseModel):
    normalized_query: str
    intent: UserIntent
    task: TaskType
    tasks_planned: list[TaskType]
    agent_queries: dict[str, str]  # per-agent rewrites
    reasoning: str

async def _plan_turn(
    raw_query: str,
    chat_history: list[BaseMessage],
    previous_intent: UserIntent | None,
    previous_task: str | None,
    file_context: FileContextData | None,
    summary: str,
) -> TurnPlan:
    """Single LLM call replacing rewriter + extractor + classifier."""
```

Model choice: **GPT-4o**, not Flash Lite. Rationale:
- The current classifier is already GPT-4o; consolidating maintains
  quality at that stage.
- Consolidating three Flash Lite calls into one GPT-4o call is
  roughly cost-neutral at ~200 tokens output.
- Flash Lite hallucinated the "wrong Section 138 IPC vs Section 188
  IPC" family of provenance bugs — GPT-4o has been more reliable on
  legal-anchor precision.

#### 3.2 Prompt shape

Single ~800-word system prompt with sections:

- `## CONVERSATION HISTORY` — full chat + summary
- `## PREVIOUS TURN STATE` — previous_intent, previous_task
- `## FILE CONTEXT` — uploaded doc names + Chroma collection IDs
- `## CURRENT TURN` — raw query
- `## YOUR JOB` — produce TurnPlan
- `## RULES` (consolidated from current three prompts):
  - Rewrite the query to be standalone (or return as-is when already so).
  - Extract typed UserIntent (inherit from previous_intent when Turn N
    doesn't override).
  - Classify task (prefer previous_task on directive follow-ups).
  - Produce per-agent query rewrites.
  - Return structured output.

The rules are ORDERED — the LLM should perform them in sequence, so the
output feels like three steps compressed into one call rather than a
single amorphous decision.

#### 3.3 Rollout

**Do NOT ship this without an A/B eval.** Consolidating three specialised
prompts into one general prompt is exactly the kind of change that looks
clean in a design doc and quietly regresses 5% of queries in prod.

Proposed eval:
1. Collect 200 real threads from `request_log` (100 first-turn, 100
   follow-up, spanning drafting / retrieval / scenario tasks).
2. Replay each thread with (a) current three-call pipeline and (b) new
   one-call planner.
3. Compare on: `task` agreement, `intent` field-by-field agreement,
   `normalized_query` semantic equivalence (LLM judge), latency, cost.
4. Ship only if quality parity + latency win + cost neutral.

Roll out behind `TURN_PLANNER_UNIFIED=1` env flag, per-worker.

#### 3.4 What gets deleted (only after unified planner passes eval)

- `agents/memory.py::_rewrite_query`
- `agents/orchestrator.py::_extract_user_intent`
- `agents/orchestrator.py::_classify_and_plan`
- `agents/orchestrator.py::_rewrite_queries_for_agents`
- `config/prompts.py::REWRITE_PROMPT`
- `config/prompts.py::USER_INTENT_EXTRACTION_PROMPT`
- Related regex helpers (`_rewrite_anchors_supported`,
  `_classify_task_regex_fallback`, `_classify_task_from_intent`).

Rough LOC delta: ~500 LOC removed.

#### 3.5 Blast radius

**High.** Touches every request's planning path.

**Cost estimate:** 3–5 days for prompt + code. Additional 5 days for
eval design, replay tooling, and manual grading. Total ~2 weeks.

---

## 5. Recommended sequence

**Ship Level 1 first, standalone. Watch prod for one week. Then decide.**

Level 1 removes the largest cause of Pattern A (state not persisting)
with minimal blast radius, and it's a prerequisite for both other levels.
It's also the level with the clearest test surface — you can unit-test
serialisation, migration, and consumer prompt changes independently.

After Level 1 lands:
- If drafting follow-ups are still the top complaint → Level 2.
- If drafting follow-ups are fixed by Level 1 (with the extractor and
  classifier now inheriting previous_* fields) but latency is still bad
  → Level 3.
- If both → do Level 2 first (bigger UX win, lower risk).

**Do NOT bundle Levels 1+2 into one PR.** They deploy differently — Level 1
is a schema migration + additive code; Level 2 is a new code path behind
an env flag. Bundling forces both to ship on the slower rollout schedule.

## 6. Rejection criteria (when NOT to do this work)

Do not ship any of these levels if:

- **Follow-up complaints are actually about first-turn quality.** If
  users mostly say "the tool gave a bad first answer, and Turn 2 didn't
  fix it either", the fix is in the first-turn agents, not the
  follow-up path.
- **The frontend doesn't distinguish threads from single queries.** If
  most traffic hits `/pyapi/chat` without a `thread_id`, follow-ups are
  a rounding-error minority and the whole plan is over-invested.
- **Level 1 telemetry after 1 week shows `previous_intent` is populated
  but ignored by consumers.** Means the consumers don't actually need
  it — abandon Level 2/3, delete Level 1.

Signal to watch: `messages` table row where `turn_number > 1` count as
% of all `messages` rows. Currently unknown — Level 1's telemetry
should surface it.

## 7. Open questions

1. **Should `previous_artifact_content` be stored on `messages` or in a
   new `artifacts` table?** `messages.ai_response` already has it —
   duplicating hurts storage. But if we ever want per-artefact metadata
   (draft revision history, section-by-section diff), the extra table
   is where that lives. Recommend: reuse `messages.ai_response` for now;
   revisit if the artefact model grows.

2. **How many prior turns should the planner see?** Currently rewriter
   sees last 10 messages (5 turns). Level 1's consumer changes could
   pass more or fewer. Recommend: keep 10 for the rewriter/planner;
   the typed `previous_intent` covers what the summary would have.

3. **Should the fast path re-run `_gather_relevant_context`?** Turn 2
   "add a prayer for interim injunction" might need new precedents on
   interim injunctions that Turn 1 didn't retrieve. Recommend: yes,
   run `_gather_relevant_context(query)` in parallel with the
   modification LLM call — it's cheap and the LLM can use it or ignore
   it. Do NOT run `_acquire_reference_draft` (the prior draft IS the
   reference).

4. **What happens to the `regenerate_of` path?** It's the precedent
   this plan generalises. Recommend: after Level 2 ships, delete
   `regenerate_of` and route "regenerate" through the same fast path
   with a synthetic directive `"regenerate this — polish and improve
   without changing substance"`. Reduces two code paths to one.

## 8. What this plan does NOT fix

Explicit list so we don't over-claim:

- **Pattern B** (asked for a table, got prose) — separate synthesis
  work.
- **Pattern C** ("unable to find" boilerplate) — fallback chain
  redesign.
- **Pattern D** (generic template instead of user's matter) — partially
  helped by Level 2, but the underlying "review-and-redraft detection
  is verb-based" issue stays.
- **Pattern E** (Hinglish detection) — language pipeline work.
- **Pattern F** (draft feels abrupt / cut off) — self-refine tuning +
  synthesis truncation cap.
- **Pattern G** (multi-intent prompts only run one agent) — Phase 2
  work reverted 2026-07-26; needs a 300s ceiling fix first.
- **Pattern H** (guardrail refusals) — separate injection classifier
  tuning.

Pattern A is roughly the largest single bucket of user complaints, but
it's not the only bucket. This plan is scoped intentionally.

## 9. Approval to proceed

This plan is drafted for review. No code has been written. On approval,
next step is Level 1 implementation, shipped in three PRs:

- PR 1: Migration + schema + save path (writes only).
- PR 2: `ConversationSnapshot` + state plumbing (reads populate state).
- PR 3: Consumer changes (extractor / rewriter / classifier prompts).

Levels 2 and 3 get their own design-doc addenda before implementation.
