# Endpoint Consolidation Plan: Eliminate `/pyapiv2/chat`, Use `/search/stream` for Everything

**Status:** Planned — no code changes yet
**Owner:** Backend team
**Created:** 2026-04-15
**Last updated:** 2026-04-15
**Companion docs:** [frontend_integration_guide.md](frontend_integration_guide.md), [drafting_ux_improvement_plan.md](drafting_ux_improvement_plan.md)

---

## Goal

Make `/pyapiv2/search/stream` the **single chat endpoint** for all use cases.
After this work:

- `/pyapiv2/chat` is gone (deleted from the codebase).
- Everything that today goes through `/chat` (file uploads in particular) goes
  through `/search/stream`.
- Frontend has one URL to call. FSD docs simplify to a single endpoint.
- The shared `core/chat_runner.run_chat_pipeline` is the only chat code path.

## Non-goals

- **Not** changing the agent graph, state schema, SSE event shapes, or the
  drafting / integration / cache behavior. This is a thin-edge refactor that
  rearranges *how requests get in*, not what happens after.
- **Not** changing `/search` (the non-streaming batch endpoint). Out of scope.
- **Not** introducing a separate `/files` upload endpoint as a new resource
  type. That's an alternative architecture (Option B below) — we'll pick one.

---

## Current State (as of 2026-04-15)

### Two endpoints, near-identical behavior

| | `/pyapiv2/search/stream` | `/pyapiv2/chat` |
|--|--------------------------|-----------------|
| Body format | `application/json` (`SearchRequest`) | `multipart/form-data` (Form fields + Files) |
| Streaming | SSE | SSE |
| Files | ❌ no | ✅ up to 30, 1 GB each |
| `integration_token` | ✅ as JSON field | ✅ as form field |
| `globalThreadId` | ✅ as JSON field | ✅ as form field |
| `preferred_language` | ✅ as JSON field | ✅ as form field |
| Underlying logic | `chat_runner.run_chat_pipeline` | `chat_runner.run_chat_pipeline` |
| Cache | enabled (first-turn only) | disabled (uploads + integration) |
| Lines of dedicated code in `gateway.py` | ~60 (lines 582-639) | ~135 (lines 641-775) |

After commit `c932ce6` ("Extract shared chat pipeline"), the only meaningful
difference between the two endpoints is **input parsing** (JSON vs multipart)
plus the file-staging step that runs BEFORE the pipeline for `/chat`.

### What `/chat` does that `/search/stream` doesn't

1. **Accepts file uploads** (`files: List[UploadFile]`)
2. **Validates + persists files** to disk via `validate_upload` + `process_files`
3. **Builds `file_context_dict`** from the staged files (Gemini Files API URIs,
   inline text for small files, ChromaDB collections for large PDFs)
4. **Cleans up temp files** after the stream ends

Once `file_context_dict` is built, both endpoints converge into the shared
`chat_runner` pipeline. So functionally, the only thing `/chat` adds is:
**accept binary uploads and turn them into a `file_context` dict before
running the agent graph.**

### Callers found in the codebase

| File | Reference |
|------|-----------|
| `frontend.html:1944,1949` | `sendChatWithFiles()` posts to `/pyapi/chat` (only when there are files) |
| `core/gateway.py:641-775` | The endpoint handler |
| `core/chat_runner.py` | Sets `endpoint_name="/pyapi/chat"` for log attribution |
| `docs/integration_flow_walkthrough.md` | Mentions `/pyapi/chat` in flow examples |
| `docs/integration_flow_spec.md` | Lists endpoint in spec |
| `docs/FILE_ATTACHMENT_GUIDE.md` | Whole guide is about `/chat` file uploads |
| `docs/production_readiness.md` | Mentions in endpoint inventory |
| `tests/test_pdf_chat.py` | Uses `/chat` for PDF upload tests |
| `tests/simulate_chat_integration.py` | Has commented references |
| `core/auth.py` | Listed in route registry comments |
| `CLAUDE.md` | Project overview lists `/chat` as a feature |

**No external (non-frontend) consumers identified.** This is a frontend-only
endpoint in practice.

---

## Architectural Decision

We have three viable architectures. Pick one before implementation.

### Option A — `/search/stream` accepts BOTH content types (recommended)

`POST /pyapiv2/search/stream` becomes a content-type-aware endpoint:

- `Content-Type: application/json` → parse `SearchRequest` Pydantic model
  (current behavior; clean and unchanged for callers without files).
- `Content-Type: multipart/form-data` → parse Form fields + UploadFile list,
  stage files via `process_files`, build `file_context` (current `/chat`
  behavior, just at the new URL).

Both paths build a `ChatRunnerInputs` and call the shared pipeline. Net change
is the input-parsing layer; everything downstream is identical.

**Why:**

- Single URL — matches the user's stated goal "eliminate /pyapiv2/chat and use
  /search/stream".
- No new resource type for FSD to learn.
- Backwards compatible during migration: keep `/chat` as a thin alias that
  forwards to `/search/stream` until callers migrate.
- FastAPI supports this cleanly via two endpoint signatures or via `Request`
  body parsing.
- Net cost in `gateway.py`: collapses ~135 lines (current `/chat`) into ~30
  additional lines on `/search/stream`.

**Tradeoffs:**

- One URL with two contracts is mildly unusual in REST. OpenAPI/Swagger UI
  shows two operations (or one with multiple body schemas) which can confuse
  doc-tooling-driven clients. Acceptable for a single in-house frontend.
- Internal endpoint logic gets a `if request.headers["content-type"] starts
  with "multipart"` branch.

### Option B — Separate `/pyapiv2/files` upload endpoint

Files uploaded via `POST /pyapiv2/files` (multipart) → returns `file_ids`.
`/search/stream` JSON body grows a `file_ids: list[str]` field.

**Why not (for this goal):**

- Doesn't actually eliminate `/chat` — it renames the file-upload contract to
  `/files`. The goal is a single URL, not "make file uploads asynchronous."
- Two-call flow (upload then chat) is a frontend ergonomics regression vs.
  today's one-shot `/chat` post.
- Adds a persistence layer for "uploaded but not yet attached" files —
  background cleanup, lifecycle, expiry, more code.

If we ever want **persistent file uploads reusable across threads**, Option B
becomes attractive. It's not what we want today.

### Option C — Drop file uploads as a feature

Pure deletion. `/chat` removed, no replacement. Frontend's file-attach UI gone.

**Why not:** product feature loss. Skip.

### Recommendation

**Option A.** It's the only one that actually achieves "single endpoint" while
keeping the file-upload product feature. The rest of this plan assumes Option A.

---

## Phased Rollout

Each phase is independently shippable, reversible, and adds zero downtime risk.

### Phase 1 — Add multipart support to `/search/stream` (additive)

`/search/stream` learns to handle multipart input. `/chat` still works
unchanged. No breaking change for any caller.

**Backend changes:**

1. In `core/gateway.py`, update the `search_stream` route signature to accept
   `Request` (raw) instead of just the parsed `SearchRequest`:

   ```python
   @app.post("/pyapi/search/stream", dependencies=[Depends(require_user_key)])
   @limiter.limit(_get_limit_for_request)
   async def search_stream(request: Request):
       content_type = request.headers.get("content-type", "")
       if content_type.startswith("multipart/"):
           # Multipart path — parse Form + Files (current /chat behavior)
           form = await request.form()
           query = form.get("query", "")
           thread_id_form = form.get("globalThreadId")
           preferred_language_form = form.get("preferred_language")
           integration_token_form = form.get("integration_token")
           upload_files = form.getlist("files")
           # ... validate + stage files via process_files ...
           # Build SearchRequest-equivalent inputs from form data.
       else:
           # JSON path — existing behavior, parse Pydantic model
           body_bytes = await request.body()
           data = SearchRequest.model_validate_json(body_bytes)
           # ... current path unchanged ...
       # Both paths converge: build ChatRunnerInputs, return StreamingResponse
   ```

2. Refactor file processing out of the `/chat` handler into a helper
   `async def _stage_uploaded_files(form, thread_id) -> tuple[dict | None, list[str]]`
   that returns `(file_context_dict, temp_paths)`. Called from the multipart
   branch of `/search/stream` and (for the moment) still from `/chat`.

3. **Don't touch** `/chat` yet. It keeps working for any caller still using it.

**Frontend changes:** none required for Phase 1. Existing callers keep working.

**Acceptance criteria:**

- [ ] `POST /pyapi/search/stream` with `Content-Type: application/json` behaves
      identically to today (verified with `tests/investigate_partnership_draft.py`
      and the `verify_phase_c.py` smoke).
- [ ] `POST /pyapi/search/stream` with `Content-Type: multipart/form-data`
      and a `query` form field returns the same SSE stream as the JSON path.
- [ ] `POST /pyapi/search/stream` with multipart + a PDF file performs file
      processing and the agent graph receives `file_context` (verified with a
      new integration test `tests/test_search_stream_multipart.py`).
- [ ] `/pyapi/chat` still works unchanged.
- [ ] No regression in existing CI integration tests.

**Risk:** Low. Additive; old paths untouched. Adds one branch and one helper
function.

**Rollback:** Revert the commit; no data migration, no schema changes.

---

### Phase 2 — Migrate the frontend to `/search/stream` for everything

Update `frontend.html` so `sendChatWithFiles` posts multipart to
`/search/stream` instead of `/chat`. After this lands, the frontend never
hits `/chat`.

**Frontend changes:**

```diff
  async function sendChatWithFiles(query, threadId) {
    const formData = new FormData();
    formData.append('query', query);
    if (threadId) formData.append('globalThreadId', threadId);
    // ... existing form-building code unchanged ...
    pendingFiles.forEach(f => formData.append('files', f));

-   addLog('info', `POST /pyapi/chat (files: ${pendingFiles.length}) ...`);
+   addLog('info', `POST /pyapi/search/stream (files: ${pendingFiles.length}) ...`);

    var chatHeaders = {};
    var k = getApiKey();
    if (k) chatHeaders['X-API-Key'] = k;
-   const r = await fetch(getBase() + '/pyapi/chat', {
+   const r = await fetch(getBase() + '/pyapi/search/stream', {
      method: 'POST',
      headers: chatHeaders,
      body: formData,
    });
```

That's the whole frontend change. **One-line URL swap.**

**Acceptance criteria:**

- [ ] User uploads a PDF and sends a query → SSE stream behaves identically
      to before (file_processing event, agent runs with file context, response
      streams).
- [ ] Multiple file types (PDF, image, DOCX, CSV) all work.
- [ ] Server logs show the request hitting `/search/stream` (not `/chat`).
- [ ] No SSE events are missing or out of order vs Phase-1 baseline.

**Risk:** Low if Phase 1 acceptance criteria all pass. The frontend is the
only known caller, so flipping its URL covers 100% of real traffic.

**Rollback:** revert the frontend change; the old URL is still alive.

---

### Phase 3 — Mark `/chat` as deprecated (warn but don't remove)

`/chat` route stays alive but logs a deprecation warning every time it's hit
and adds `Deprecation: true` and `Sunset: <date>` HTTP headers per RFC 8594.
The OpenAPI definition marks it `deprecated=True`.

**Backend changes:**

```python
@app.post("/pyapi/chat", dependencies=[Depends(require_user_key)],
          deprecated=True)  # shows in /docs as deprecated
@limiter.limit(_get_limit_for_request)
async def chat_with_files(...):
    log.warning("DEPRECATED endpoint /pyapi/chat called",
                client_ip=request.client.host,
                user_agent=request.headers.get("user-agent", ""))
    # ... existing implementation, returns SSE with an extra header ...
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Deprecation": "true",
            "Sunset": "Sat, 15 Jul 2026 00:00:00 GMT",  # 90-day notice
            "Link": '</pyapi/search/stream>; rel="successor-version"',
            ...
        },
    )
```

**Acceptance criteria:**

- [ ] `/chat` still returns valid SSE.
- [ ] Server log shows a `WARNING` for every `/chat` hit, with the caller
      identifying info (IP, user-agent, API key prefix).
- [ ] After 7 days of monitoring, deprecation log count is **zero** (means
      no one is hitting the old URL anymore).
- [ ] OpenAPI docs at `/docs` show `/chat` with a strikethrough / deprecated
      tag.

**Risk:** Zero — purely informational + observational.

**Rollback:** remove `deprecated=True` and the warning log; takes 30 seconds.

---

### Phase 4 — Delete `/chat` from the codebase

After Phase 3 confirms no callers, delete the route + its dedicated tests +
references in docs. The shared file-staging helper from Phase 1 stays — it's
now used only by the multipart branch of `/search/stream`.

**Backend changes:**

- Delete the `chat_with_files` function (lines 641-775 of `gateway.py`).
- Update `chat_runner` default `endpoint_name` to drop the "/chat" reference.
- Delete `tests/test_pdf_chat.py` (or migrate the assertions to a new
  `tests/test_search_stream_files.py`).
- Update [docs/FILE_ATTACHMENT_GUIDE.md](docs/FILE_ATTACHMENT_GUIDE.md) to
  reference `/search/stream` instead of `/chat`.
- Update [CLAUDE.md](../CLAUDE.md), [docs/integration_flow_spec.md](integration_flow_spec.md),
  [docs/integration_flow_walkthrough.md](integration_flow_walkthrough.md),
  [docs/frontend_integration_guide.md](frontend_integration_guide.md),
  [docs/production_readiness.md](production_readiness.md) to remove `/chat`
  references.

**Acceptance criteria:**

- [ ] `grep -rn "pyapi/chat\|pyapiv2/chat" .` returns 0 hits across code.
- [ ] CI passes — no test references `/chat`.
- [ ] Health check + a representative file-upload prompt both work via
      `/search/stream`.
- [ ] The route is no longer in the OpenAPI spec at `/docs`.

**Risk:** Low if Phase 3 confirmed zero callers. If any forgotten consumer is
still hitting `/chat`, they get HTTP 405/404 and need to migrate.

**Rollback:** restore the deleted function from git history (`git revert`
the deletion commit). The shared file-staging helper from Phase 1 makes this
trivial — restored `/chat` would call it the same way.

---

## Risk Assessment Matrix

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Multipart path on `/search/stream` parses different form-field names than `/chat` does today | Low | Medium — file uploads break | Phase 1 unit + integration tests cover every existing form field; mock + live testing before frontend migration |
| FastAPI's `Request.form()` doesn't handle large file uploads as efficiently as `UploadFile = File(default=[])` | Low | Medium — slower / OOM on big PDFs | Benchmark Phase 1 with a 100MB PDF before merging; use streaming form parser if needed |
| Cache key collision between JSON and multipart requests for the same query | Very low | Low — wrong cache hit | Cache is keyed on query text only; both paths pass the same `query`; multipart requests always disable cache (file context = no cache, same as `/chat` today) |
| Existing OpenAPI clients break when `/search/stream` schema gains a multipart variant | Low | Low — only affects auto-generated SDKs | We have no auto-generated SDKs; FSD frontend uses raw `fetch` |
| Phase 3 deprecation warnings get ignored, Phase 4 deletes too early | Low | High — a hidden caller breaks | Hard requirement: 7 consecutive days of zero `/chat` hits in logs before Phase 4 deletion |
| Forgetting to remove `/chat` references from `tests/test_pdf_chat.py` causes CI to fail at Phase 4 | Medium | Low — caught immediately | Phase 4 PR includes the test migration / deletion |

---

## Testing Strategy

### Phase 1 (Backend additive)

**New tests to add:**

```python
# tests/test_search_stream_multipart.py
def test_search_stream_accepts_json_unchanged():
    """Existing JSON callers see no change."""
    ...

def test_search_stream_accepts_multipart():
    """Multipart with no files behaves like JSON."""
    ...

def test_search_stream_accepts_multipart_with_files():
    """Multipart with a PDF processes the file and runs the agent."""
    ...

def test_search_stream_multipart_form_fields():
    """All form fields (query, globalThreadId, preferred_language,
    integration_token) are honored."""
    ...
```

**Existing tests that must still pass:**

- `tests/integration/test_api.py` (full smoke suite)
- `tests/investigate_partnership_draft.py` (drafting flow)
- `tests/verify_phase_c.py` (per-section status events)
- `tests/test_pdf_chat.py` (PDF upload via `/chat` — still hits `/chat` until
  Phase 2)

### Phase 2 (Frontend swap)

**Manual:**

- Upload a PDF via the test frontend → response includes file context.
- Upload an image → vision/OCR path runs.
- Upload a 50MB PDF → no timeout, processed correctly.
- Network tab shows `/search/stream` (not `/chat`).

**Automated:** none new — `tests/test_pdf_chat.py` continues to validate
the multipart-with-files behavior at the API level.

### Phase 3 (Deprecation monitoring)

- Daily check: `grep "DEPRECATED endpoint /pyapi/chat called"
  /root/v2_multi_agent/logs/error.log | wc -l` should drop to 0 within a
  day of Phase 2 deploy.
- Add a Prometheus counter `lawtech_deprecated_endpoint_calls{endpoint="chat"}`
  if we want a dashboard view (optional).

### Phase 4 (Deletion)

- Run the full integration suite + manual file-upload smoke test.
- Confirm `/docs` no longer lists `/chat`.

---

## Frontend Migration Notes

This is what the **product frontend team** needs to know:

1. **Replace one URL.** That's the entire migration:
   ```diff
   - fetch(`${API}/pyapi/chat`, { method: 'POST', body: formData })
   + fetch(`${API}/pyapi/search/stream`, { method: 'POST', body: formData })
   ```

2. **No payload changes.** Form field names (`query`, `globalThreadId`,
   `preferred_language`, `integration_token`, `files`) are identical.

3. **No SSE event-handling changes.** The same events fire in the same order
   from both endpoints (because they share the `chat_runner` pipeline since
   commit `c932ce6`).

4. **Decision rule for content-type:**
   - If you have files → use multipart (`FormData`).
   - If you don't → use JSON (cheaper, faster to parse server-side).
   - The endpoint accepts both. Same URL.

5. **Do this AFTER Phase 1 backend ships.** Until Phase 1, `/search/stream`
   will reject multipart requests with 422.

---

## Documentation Updates Required

| File | Phase | Change |
|------|-------|--------|
| [docs/frontend_integration_guide.md](frontend_integration_guide.md) | 1 | Document multipart variant under `/search/stream`; cross-reference from current `/chat` section |
| [docs/integration_flow_spec.md](integration_flow_spec.md) | 4 | Remove `/chat` references |
| [docs/integration_flow_walkthrough.md](integration_flow_walkthrough.md) | 4 | Update flow diagrams that mention `/chat` |
| [docs/FILE_ATTACHMENT_GUIDE.md](FILE_ATTACHMENT_GUIDE.md) | 4 | Rewrite to reference `/search/stream`; consider renaming to `file_attachments_via_search_stream.md` |
| [docs/production_readiness.md](production_readiness.md) | 4 | Endpoint inventory update |
| [CLAUDE.md](../CLAUDE.md) | 4 | Project overview — drop `/chat` from listed endpoints |

---

## Estimated Effort

| Phase | Backend | Frontend | Docs | Total |
|-------|---------|----------|------|-------|
| 1 — Add multipart to `/search/stream` | ~3 hr | 0 | 1 hr | **~4 hr** |
| 2 — Frontend URL swap | 0 | 0.5 hr | 0 | **~30 min** |
| 3 — Deprecation warning + monitor | 30 min | 0 | 0 | **30 min + 7 days monitoring** |
| 4 — Delete `/chat` + cleanup | 1 hr | 0 | 1 hr | **~2 hr** |
| **Total active dev time** | | | | **~7-8 hr** |
| **Calendar time (incl. monitoring)** | | | | **~10-12 days** |

---

## Open Questions

1. **Cache behavior with multipart-but-no-files requests** — a multipart
   request without files looks logically identical to a JSON request. Should
   the cache treat them the same (cacheable when first-turn) or always skip
   cache for multipart (current `/chat` behavior)? **Recommendation:**
   detect "multipart with files == False" and enable cache; otherwise skip.

2. **Maximum form size** — FastAPI / Starlette default form size is unbounded
   when streamed. Our `MAX_FILE_SIZE_MB=1024` handles per-file but should we
   add a request-level total cap (e.g., 4 GB)?

3. **Do we drop `/pyapi/search` (batch, non-streaming) in the same wave?**
   It's a separate endpoint with separate semantics (returns full JSON,
   not SSE). Leaving it for now; revisit in a later proposal.

4. **Should `/chat` redirect to `/search/stream` (HTTP 308) during Phase 3
   instead of double-handling?** Pro: forces clients to migrate immediately.
   Con: breaks any caller that doesn't follow 308 redirects on POST. Stick
   with deprecation header + warning log.

---

## Tracking

| Phase | Status | Commit | Date |
|-------|--------|--------|------|
| 1 — Multipart support on `/search/stream` | Planned | — | — |
| 2 — Frontend URL swap | Planned | — | — |
| 3 — Deprecation marker on `/chat` | Planned | — | — |
| 4 — Delete `/chat` | Planned | — | — |

Updates to this table land alongside each phase's commit (mirroring the
[drafting UX plan doc](drafting_ux_improvement_plan.md) workflow).

---

## Decision Needed Before Starting

Confirm:

1. ✅ Architecture: Option A (single URL, dual content-types). Approve?
2. ✅ Sunset date in deprecation header: **2026-07-15** (~90 days). Adjust?
3. ✅ Whether to drop `/pyapi/search` (batch) at the same time. **Default: no.**
4. Approve start of Phase 1?
