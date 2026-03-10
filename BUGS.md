# Bug Tracker — Lawtech-AI

> Investigation date: 2026-03-11
> Total issues found: 16 (3 Critical · 5 High · 4 Medium · 4 Low)

---

## CRITICAL

### BUG-01 — NameError crash when new files attached in multi-turn chat
- **File**: `agents/memory.py` ~L277-306
- **Category**: Logic error / Crash
- **Status**: Fixed — initialized `restored_file_context = None` before `if thread_id:` block
- **Description**: `restored_file_context` is only assigned inside the `else` branch (no new files uploaded). The return block at L306 references it unconditionally. If files ARE attached this turn, the variable is never defined → `NameError` crashes the request.
- **Trigger**: Second turn of any conversation where the user attaches a new file.
- **Fix**: Initialize `restored_file_context = None` before the `if/else` block.

---

### BUG-02 — KeyError crash in document agent on missing state key
- **File**: `agents/document.py` ~L202, `agents/drafting.py` ~L633
- **Category**: Crash / KeyError
- **Status**: Fixed — replaced `state["original_query"]` with `state.get("original_query", "")` in both files
- **Description**: Agent accesses `state["original_query"]` directly (no `.get()` fallback). If the orchestrator skips populating this key due to an edge case, the server crashes with a 500.
- **Trigger**: Malformed or partial initial state passed to document agent.
- **Fix**: Replace with `state.get("original_query", "")`.

---

### BUG-03 — TypeError on draft continuation resume (Pydantic unpacking)
- **File**: `agents/drafting.py` ~L502
- **Category**: Runtime error / Type mismatch
- **Status**: Fixed — replaced `DraftOutline(**dict)` with `DraftOutline.model_validate(dict)`
- **Description**: `DraftOutline(**continuation["outline"])` assumes `outline` is a flat dict. If the stored JSON has nested Pydantic-serialized structures (from `outline.model_dump()`), the unpacking fails with a TypeError or Pydantic validation error.
- **Trigger**: User resumes a draft that was saved in a previous session.
- **Fix**: Use `DraftOutline.model_validate(continuation["outline"])` instead of `**` unpacking.

---

## HIGH

### BUG-04 — Race condition on ChromaDB `clear_system_cache()`
- **File**: `core/file_processor.py` ~L227, `agents/document.py` ~L99
- **Category**: Race condition / Data corruption
- **Status**: Fixed — `file_processor.py` already had `_chroma_cache_lock`; added same lock to `document.py`
- **Description**: Multiple agents call `SharedSystemClient.clear_system_cache()` without any lock. Concurrent PDF uploads and constitution agent queries can interleave, corrupting the ChromaDB shared state.
- **Trigger**: Simultaneous file upload + constitution/maxim query from different users.
- **Fix**: Wrap `clear_system_cache()` calls in a module-level `asyncio.Lock`.

---

### BUG-05 — AttributeError in agent fallback when all models fail
- **File**: `core/agent_fallback.py` ~L156-164
- **Category**: Unhandled exception
- **Status**: Not a bug — `RuntimeError` is raised before any `.candidates` access; outer except catches it correctly
- **Description**: When all Gemini models fail, `response` stays `None`. The guard at L156 raises `RuntimeError("All models failed")` correctly, but the next line (`response.candidates`) is reached in some code paths where the exception is caught upstream, resulting in AttributeError on `None`.
- **Trigger**: All Gemini Flash + Pro endpoints return errors simultaneously (rate limit / outage).
- **Fix**: Ensure the RuntimeError propagates cleanly and is not swallowed by outer try/except.

---

### BUG-06 — TOCTOU race on `_collection_locks` dict in gateway
- **File**: `core/gateway.py` ~L51-59
- **Category**: Race condition
- **Status**: Not a bug — check and insert are both inside `with _collection_locks_guard:`, making them atomic
- **Description**: `_collection_locks` dict is mutated inside `_get_collection_lock()`. Between the check-and-insert there is a window where two concurrent requests for the same `unique_string` can both create separate locks, defeating the per-collection serialization.
- **Trigger**: Two concurrent PDF uploads with the same `unique_string`.
- **Fix**: Use `setdefault()` atomically or protect the dict mutation with a module-level lock.

---

### BUG-07 — ChromaDB concurrent access in document agent (no per-collection lock)
- **File**: `agents/document.py` ~L94-105
- **Status**: Partial — concurrent reads are safe in WAL mode; BUG-04 fix (lock on `clear_system_cache`) protects cache corruption. Write conflicts handled by gateway's per-collection upload lock.
- **Category**: Race condition / Data corruption
- **Status**: Open
- **Description**: `Chroma.from_texts()` and `Chroma()` are invoked without holding a per-collection lock. Two users querying the same uploaded document concurrently can cause ChromaDB to recreate the collection or corrupt the underlying SQLite store.
- **Trigger**: Two concurrent queries against the same document collection.
- **Fix**: Use the existing `_get_collection_lock()` mechanism from gateway.py before accessing the collection.

---

### BUG-08 — `file_context` key never initialized for non-file requests
- **File**: `core/gateway.py` ~L174-204 (`_build_initial_state`)
- **Category**: Missing initialization / Silent failure
- **Status**: Not a bug — `_build_initial_state` always includes `"file_context": file_context` (defaults to `None`)
- **Description**: `file_context` is only added to the initial state dict when `file_ids` is present. Agents (memory.py, document.py) call `FileContextData.from_state(state)` expecting the key — its absence causes silent failure or KeyError downstream.
- **Trigger**: Any chat request that does not include file attachments.
- **Fix**: Always initialize `"file_context": None` (or empty `FileContextData`) in `_build_initial_state`.

---

## MEDIUM

### BUG-09 — Timeout in orchestrator query normalization swallows traceback
- **File**: `agents/orchestrator.py` ~L440-451
- **Category**: Error handling / Observability
- **Status**: Fixed — added `exc_info=True` to timeout warning log
- **Description**: `asyncio.wait_for()` timeout is caught and falls back to the original query silently. The exception traceback is discarded — no structured log is emitted, making it invisible in production monitoring.
- **Trigger**: Query analysis LLM call takes >10 seconds.
- **Fix**: Log the timeout as a structured warning with `exc_info=True` before falling back.

---

### BUG-10 — Stale `effective_query` used in streaming response metadata
- **File**: `core/gateway.py` ~L369, L422, L524
- **Category**: Logic error / Wrong metadata
- **Status**: Not a bug — streaming code correctly updates `effective_query` at L422 from memory agent's state update
- **Description**: `effective_query` is set to `data.prompt_query` before the memory agent runs. If memory agent is skipped or returns early, the variable stays at the initial value and is written into the streaming response metadata at L524 as if it were the rewritten query.
- **Trigger**: Streaming request where memory agent is skipped (no thread_id or early return).
- **Fix**: Initialize `effective_query` only after memory agent result is confirmed, or default to original query explicitly.

---

### BUG-11 — Blank PDF silently processed, returns empty results to user
- **File**: `core/file_processor.py` ~L117-139
- **Category**: Silent failure / UX
- **Status**: Not a bug — safety net at L493-495 detects empty extraction and sets `pf.error = "No content could be extracted"`
- **Description**: `_extract_pdf_text()` returns `("", page_count)` for blank PDFs with no error. If the Vision OCR fallback also fails, the exception is caught silently. The file appears successfully processed but searches against it return nothing.
- **Trigger**: User uploads a blank or image-only PDF where both text extraction and OCR fail.
- **Fix**: After both extraction paths fail, set an `upload_error` flag on the file record and return a user-visible error in the response.

---

### BUG-12 — Failed scenario agent returns misleading placeholder source
- **File**: `agents/scenario.py` ~L141-153
- **Category**: Wrong output / UX
- **Status**: Fixed — error path now returns `sources=[]`
- **Description**: On exception, `AgentResult` is built with a fake `"AI-Generated Legal Analysis"` source entry. Downstream rendering treats it as a real citation and displays it to the user.
- **Trigger**: Any unhandled exception inside the scenario agent.
- **Fix**: Set `sources=[]` on error path; populate `error` field instead.

---

## LOW

### BUG-13 — Newacts regex fallback misses non-abbreviated act names
- **File**: `agents/newacts.py` ~L158-190
- **Category**: Incomplete feature / Wrong results
- **Status**: Fixed — added 10 informal name variants to `_ABBREVIATION_MAP` (e.g. "new criminal procedure code", "new penal code", "evidence act")
- **Description**: Patterns like "new criminal procedure code" don't match any abbreviation regex, so the agent falls through to hybrid search which may return results from unrelated acts.
- **Trigger**: User queries with informal or non-abbreviated act names.
- **Fix**: Add broader string matching (case-insensitive substring) before falling back to hybrid search.

---

### BUG-14 — `asyncio.gather()` leaves orphaned tasks on partial failure in drafting
- **File**: `agents/drafting.py` ~L418
- **Category**: Resource leak
- **Status**: Not a bug — `_gen_one` has internal exception handling so gather tasks never raise; all complete normally
- **Description**: If one section-generation task in `asyncio.gather(*tasks)` raises, the except block doesn't cancel the remaining in-flight tasks. Orphaned coroutines continue consuming memory and LLM quota.
- **Trigger**: One section fails (e.g. Gemini error) while others are still running.
- **Fix**: Use `asyncio.gather(*tasks, return_exceptions=True)` and handle per-task errors, or explicitly cancel remaining tasks in the except block.

---

### BUG-15 — Malformed AgentResult from broken agent causes AttributeError in gateway
- **Status**: Already guarded — token accumulation uses `hasattr(r, "tokens_consumed")` check; low real-world risk since all agents return typed `AgentResult`.
- **File**: `core/gateway.py` ~L374-403
- **Category**: Defensive coding / Crash
- **Status**: Open
- **Description**: `r.tokens_consumed` and other fields are accessed on items in `agent_results` without validating they are proper `AgentResult` objects. A dict returned from a broken agent causes AttributeError in the result aggregation loop.
- **Trigger**: Any agent that returns a raw dict instead of an `AgentResult` instance.
- **Fix**: Add `isinstance(r, AgentResult)` guard or validate in the aggregation loop.

---

### BUG-16 — Draft continuation breaks on `DraftOutline` schema version mismatch
- **File**: `core/chat_store.py` (draft continuation save/load)
- **Category**: Data compatibility / Data loss
- **Status**: Fixed — added `_schema_version` field to saved JSON; load discards stale data with a warning instead of crashing
- **Description**: Draft continuation JSON is saved without a schema version field. If `DraftOutline` fields change between deployments, existing saved continuations fail Pydantic validation and are silently unloadable — user loses in-progress drafts.
- **Trigger**: Server upgrade that modifies `DraftOutline` schema while drafts are in-flight.
- **Fix**: Add a `schema_version` field to saved draft JSON and validate/migrate on load.

---

## Summary

| ID | File | Severity | Status |
|----|------|----------|--------|
| BUG-01 | agents/memory.py | Critical | Open |
| BUG-02 | agents/document.py | Critical | Open |
| BUG-03 | agents/drafting.py | Critical | Open |
| BUG-04 | core/file_processor.py, agents/constitution_maxim.py | High | Open |
| BUG-05 | core/agent_fallback.py | High | Open |
| BUG-06 | core/gateway.py | High | Open |
| BUG-07 | agents/document.py | High | Open |
| BUG-08 | core/gateway.py | High | Open |
| BUG-09 | agents/orchestrator.py | Medium | Open |
| BUG-10 | core/gateway.py | Medium | Open |
| BUG-11 | core/file_processor.py | Medium | Open |
| BUG-12 | agents/scenario.py | Medium | Open |
| BUG-13 | agents/newacts.py | Low | Open |
| BUG-14 | agents/drafting.py | Low | Open |
| BUG-15 | core/gateway.py | Low | Open |
| BUG-16 | core/chat_store.py | Low | Open |
