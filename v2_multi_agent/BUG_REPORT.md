# Bug Report — v2 Multi-Agent System

**Date:** 2026-02-23
**Codebase:** `v2_multi_agent/`
**Scope:** Full codebase audit of 65+ files, ~12,600 lines

---

## BUG 1: Constitution + Maxim dedup silently drops one task

**Severity:** Medium
**Data Loss Risk:** Yes — task silently dropped
**Fix Effort:** Medium

**Files:**
- `core/graph.py` lines 91–98
- `agents/constitution_maxim.py` lines 199–204

**Problem:**

When the orchestrator plans both `Constitution` AND `Maxim` (or any combination of `Constitution`/`Maxim`/`Legal_Concepts`), they all map to the same graph node `"constitution_maxim"` in `AGENT_NODE_MAP`:

```python
# core/graph.py
AGENT_NODE_MAP = {
    ...
    "Constitution": "constitution_maxim",
    "Maxim": "constitution_maxim",
    "Legal_Concepts": "constitution_maxim",
    ...
}
```

The dedup logic in `route_after_orchestrator` sends only ONE `Send` to that node:

```python
# core/graph.py — route_after_orchestrator
seen_nodes: set[str] = set()
for task in planned:
    node = AGENT_NODE_MAP.get(task, "scenario")
    if node not in seen_nodes:
        seen_nodes.add(node)
        sends.append(Send(node, state))
```

Inside the agent, it picks the **first** matching task from `tasks_planned`:

```python
# agents/constitution_maxim.py
our_tasks = {"Constitution", "Maxim", "Legal_Concepts"}
if task not in our_tasks:
    planned = state.get("tasks_planned", [])
    task = next((t for t in planned if t in our_tasks), "Legal_Concepts")
```

So for `tasks_planned=["Constitution", "Maxim"]`, it handles Constitution and **silently ignores Maxim**.

**Trigger Example:**

> "Explain Article 21 and the doctrine of audi alteram partem"

The planner returns `[Constitution, Maxim]` → only Constitution executes → Maxim explanation is missing from the response.

**Fix Options:**
- **Option A:** Have the node iterate over ALL matching tasks from `tasks_planned` and combine results
- **Option B:** Split into separate graph nodes (`constitution_node`, `maxim_node`, `legal_concepts_node`)

---

## BUG 2: Streaming retry emits duplicate/partial tokens

**Severity:** Medium
**Data Loss Risk:** No — final `response` event is correct
**Fix Effort:** Low

**Files:**
- `agents/newacts.py` lines 558–563
- `agents/legislation.py` lines 319–324
- `agents/drafting.py` (same pattern)
- `agents/judgment.py` (same pattern)

**Problem:**

The retry pattern used in multiple agents:

```python
try:
    llm_response = await stream_chain_response(chain, invoke_kwargs)
except Exception as llm_err:
    log.warning("LLM generation failed, retrying once", error=str(llm_err)[:200])
    llm_response = await stream_chain_response(chain, invoke_kwargs)
```

If the first `astream()` call partially streams tokens before failing, those tokens have already been emitted via `get_stream_writer()` to the frontend. The retry then streams the **full** response again. The frontend token buffer accumulates:

```
[partial tokens from attempt 1] + [full tokens from attempt 2]
```

The final `response` SSE event replaces everything with the correct text, so the end result is accurate. But **during streaming**, the user sees garbled/duplicated text until the response event arrives.

**Fix Options:**
- Emit a `token_reset` event before retrying so the frontend clears its buffer
- Wrap the first attempt in a "tentative" mode that doesn't emit tokens, only retry with streaming
- Accumulate tokens locally first, only emit via writer after success

---

## BUG 3: Sync blocking calls in async agent nodes

**Severity:** Low–Medium
**Data Loss Risk:** No — performance degradation only
**Fix Effort:** Medium

**Files:**
- `agents/orchestrator.py` lines 36–45, 88–125
- `agents/constitution_maxim.py` lines 94–182 (`_retrieve_from_chromadb`)
- `agents/sci_judgment.py` line 95 (fallback path)

**Problem:**

Several `async def` agent nodes call synchronous functions that block the asyncio event loop:

**orchestrator_plan_node:**
```python
# Both are synchronous — block for 2-5 seconds total (two GPT-4o calls)
task = _classify_task(query, chat_summary=summary)   # sync llm.invoke()
tasks_planned = _plan_agents(query, task)             # sync llm.invoke()
```

**constitution_maxim_node:**
```python
# _retrieve_from_chromadb does sync ChromaDB queries + sync MultiQueryRetriever
docs = _retrieve_from_chromadb(task, query)  # sync — blocks event loop
```

While these sync calls execute, the event loop is blocked. No other async work can proceed: SSE writing, health checks, concurrent requests on the same worker are all stalled.

**Impact:**

- Single-user: unnoticeable
- Concurrent load: request latency increases because sync calls serialize on the event loop
- Parallel agents via `Send` effectively run sequentially when one of them blocks

**Fix:**

Wrap sync calls in `await asyncio.to_thread(...)`:

```python
task = await asyncio.to_thread(_classify_task, query, summary)
tasks_planned = await asyncio.to_thread(_plan_agents, query, task)
```

---

## BUG 4: Follow-up suggestions JSON parse is fragile

**Severity:** Low
**Data Loss Risk:** No — graceful fallback to empty list
**Fix Effort:** Low

**File:** `core/gateway.py` lines 117–125

**Problem:**

The follow-up suggestions parser:

```python
text = result.content.strip()
# Strip markdown code fences if present
if text.startswith("```"):
    text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
suggestions = json.loads(text)  # Can throw ValueError
if isinstance(suggestions, list) and len(suggestions) >= 3:
    return [str(s)[:60] for s in suggestions[:3]]
return []
```

This fails silently in these common LLM output patterns:

| LLM Output | Failure |
|-------------|---------|
| `Here are 3 suggestions: ["...", "...", "..."]` | `json.loads` fails on surrounding text |
| `` `["...", "...", "..."]` `` | Single backticks not stripped |
| `["q1", "q2"]` (only 2 items) | `len(suggestions) >= 3` check fails, returns `[]` |
| `{"suggestions": ["...", "...", "..."]}` | `isinstance(suggestions, list)` fails |

The outer `try/except` catches these errors, so no crash — but users lose follow-up suggestions more often than necessary.

**Fix Options:**
- Use regex extraction: `re.search(r'\[.*\]', text, re.DOTALL)` before `json.loads`
- Use `with_structured_output()` for guaranteed schema compliance
- Relax the `>= 3` check to `>= 1`

---

## BUG 5: `related_sections` has no reducer — parallel agents overwrite

**Severity:** Low (latent)
**Data Loss Risk:** Latent — will manifest if more agents return related sections
**Fix Effort:** Trivial

**File:** `core/state.py` line 119

**Problem:**

```python
class LegalAgentState(MessagesState):
    ...
    agent_results: Annotated[dict[str, AgentResult], _merge_agent_results]  # ✅ has reducer
    tokens_consumed: Annotated[int, _sum_tokens]                            # ✅ has reducer
    related_sections: list[dict[str, Any]]                                  # ❌ NO reducer
```

Without a reducer annotation, LangGraph uses **last-write-wins** semantics. If two parallel agents both return `related_sections`, the second agent's data overwrites the first — the first agent's related sections are silently discarded.

Currently only `newacts` returns `related_sections`, so no actual data loss occurs. But if `legislation` or `constitution_maxim` ever starts returning them, data would be lost without any warning.

**Fix:**

```python
import operator

related_sections: Annotated[list[dict[str, Any]], operator.add]
```

---

## BUG 6: `source_metadata` also has no reducer — same overwrite risk

**Severity:** Low (latent)
**Data Loss Risk:** Latent — currently safe because only orchestrator_synthesize sets it
**Fix Effort:** Trivial

**File:** `core/state.py` line 118

**Problem:**

```python
source_metadata: list[dict[str, Any]]  # ❌ NO reducer
```

Same issue as Bug 5. The `source_metadata` field is currently only set by `orchestrator_synthesize` (not by domain agents directly), so there's no parallel write conflict today. But the lack of a reducer makes the architecture fragile — any refactoring that moves source serialization into domain agents would silently lose data.

**Fix:**

```python
source_metadata: Annotated[list[dict[str, Any]], operator.add]
```

---

## BUG 7: Chat history saves original query instead of rewritten query

**Severity:** Low
**Data Loss Risk:** No — summary quality degraded
**Fix Effort:** Trivial

**Files:**
- `core/gateway.py` line 418 (stream endpoint)
- `core/gateway.py` line 220 (batch endpoint)

**Problem:**

The memory agent rewrites follow-up queries into standalone form:

> Original: "tell me more about that"
> Rewritten: "Explain Section 35 of BNS — Right of Private Defence in detail"

But the chat store saves the **original** query:

```python
# Stream endpoint (gateway.py:417-418)
conversation_turn = await chat_store.save_turn(
    thread_id, data.Promptquery, final_response  # ← original, not rewritten
)

# Batch endpoint (gateway.py:220)
conversation_turn = await chat_store.save_turn(
    thread_id, data.Promptquery, final_response  # ← same issue
)
```

When the rolling summary is regenerated from stored turns, it processes vague text like "tell me more" and "what about that case" instead of the resolved standalone queries. This produces lower-quality summaries that lose important context.

**Fix:**

```python
effective_query = final_state.get("query", data.Promptquery)
conversation_turn = await chat_store.save_turn(
    thread_id, effective_query, final_response
)
```

---

## BUG 8: Summary regeneration blocks the write lock during LLM call

**Severity:** Low
**Data Loss Risk:** No — performance degradation under concurrent writes
**Fix Effort:** Low

**File:** `core/chat_store.py` lines 210–252

**Problem:**

The `_save_turn_sync` method holds the write lock during the entire operation, including summary regeneration:

```python
def _save_turn_sync(self, thread_id, user_query, ai_response) -> int:
    with self._write_lock:                          # Lock acquired
        conn = self._get_connection()
        try:
            # ... INSERT message, UPDATE thread ...
            conn.commit()
            self._maybe_regenerate_summary_sync(thread_id, conn)  # LLM call!
            return new_turn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()                            # Lock released here
```

`_maybe_regenerate_summary_sync` calls `chain.invoke()` — a synchronous Gemini Flash Lite LLM call that takes ~1–3 seconds. **During this time, all other write operations for any thread are blocked.**

This triggers every 3 turns per thread. With multiple concurrent users, write contention increases significantly during summary regeneration.

**Fix Options:**
- Release the write lock after `conn.commit()`, regenerate summary outside the lock
- Move summary regeneration to an async background task
- Use a per-thread lock instead of a global write lock

---

## BUG 9: Frontend `renderMarkdown` doesn't escape HTML — XSS vulnerability

**Severity:** Low (test console only)
**Data Loss Risk:** No
**Fix Effort:** Low

**File:** `frontend.html` lines 1299–1328

**Problem:**

The markdown renderer converts text to HTML without escaping HTML entities first:

```javascript
function renderMarkdown(text) {
  if (!text) return '';
  let html = text
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    // ... no HTML escaping anywhere ...
  return '<p>' + html + '</p>';
}
```

If the AI response contains HTML tags (possible through prompt injection in source data, or reflected user input in the response), they're rendered as live HTML via `innerHTML`:

```
AI response contains: <img src=x onerror=alert(1)>
→ Rendered as live HTML in the chat
```

**Current risk is limited** because:
1. This is a test console, not production
2. AI responses come from the LLM (not direct user input)
3. The guardrail sanitizes some patterns

**Fix:**

Add HTML escaping before markdown processing:

```javascript
function renderMarkdown(text) {
  if (!text) return '';
  let html = escapeHtml(text)  // ← Escape FIRST
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
    // ... rest of markdown processing ...
}
```

---

## Summary Table

| # | Bug | Severity | Data Loss | Fix Effort |
|---|-----|----------|-----------|------------|
| 1 | Constitution+Maxim dedup drops task | **Medium** | Yes | Medium |
| 2 | Streaming retry emits duplicate tokens | **Medium** | No (visual glitch) | Low |
| 3 | Sync blocking in async nodes | Low–Medium | No (perf only) | Medium |
| 4 | Follow-up JSON parse fragile | Low | No (fallback) | Low |
| 5 | `related_sections` no reducer | Low | Latent risk | Trivial |
| 6 | `source_metadata` no reducer | Low | Latent risk | Trivial |
| 7 | Chat saves original query, not rewritten | Low | Quality degraded | Trivial |
| 8 | Summary regen blocks write lock | Low | No (perf only) | Low |
| 9 | Frontend XSS in renderMarkdown | Low | No (test console) | Low |

**Recommended fix priority:** Bug 1 → Bug 2 → Bug 7 → Bugs 5+6 → Bug 3 → Bug 4 → Bug 8 → Bug 9

---

*Generated by full codebase audit on 2026-02-23.*
