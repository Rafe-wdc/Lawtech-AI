# `/pyapiv2/search/stream` — Complete Reference for FSD

Everything the frontend needs to integrate with the streaming chat endpoint,
including the Google Docs / Notion integration feature.

**Companion docs:**
- [integration_flow_spec.md](integration_flow_spec.md) — backend contract with the FSD chat service
- [integration_flow_walkthrough.md](integration_flow_walkthrough.md) — implementation walkthrough

---

## Endpoint

```
POST https://tool.lawttorney.com/pyapiv2/search/stream
```

## Headers

| Header | Required | Value |
|--------|----------|-------|
| `X-API-Key` | ✅ | The Lawtech backend API key (shared separately) |
| `Content-Type` | ✅ | `application/json` |

## Request Body (JSON)

```json
{
  "Promptquery": "string (1-30000 chars)",
  "globalThreadId": "string (optional)",
  "preferred_language": "string (optional, ISO 639-1)",
  "integration_token": "string (optional, user's JWT)"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `Promptquery` | string | ✅ | User's chat message. 1–30000 chars. |
| `globalThreadId` | string | optional | Omit for first turn (gets a new UUID + may hit cache). Pass to continue a thread. |
| `preferred_language` | string | optional | ISO 639-1 code (`"en"`, `"hi"`, `"ta"`, etc). Overrides auto-detection. |
| `integration_token` | string | optional | **User's JWT from the login endpoint.** Required only when you want Google Docs / Notion URL extraction. |

### When to send `integration_token`

Send it on **every chat turn** once the user is logged in. If the user's
message doesn't contain a URL, the backend simply ignores it — no harm, no
extra cost. If it does contain one, extraction kicks in.

### Example — first turn with URL

```json
{
  "Promptquery": "Draft a pricing policy using https://docs.google.com/document/d/abc123/edit",
  "integration_token": "eyJhbGciOiJIUzI1NiIs..."
}
```

### Example — follow-up turn

```json
{
  "Promptquery": "Now make it shorter",
  "globalThreadId": "8f3d1a2b-4c5e-6789-abcd-ef0123456789",
  "integration_token": "eyJhbGciOiJIUzI1NiIs..."
}
```

---

## Response: Server-Sent Events (SSE)

Response is `text/event-stream`. Each event is on its own `data: ...\n\n` line.
Parse JSON after `data: `.

### Event types — ordered by when they fire

| # | Event type | Always? | Purpose |
|---|------------|---------|---------|
| 1 | `thread_id` | ✅ | First event, gives you the UUID to pass on follow-up turns |
| 2 | `integration_status` | URL detected | "Checking Google/Notion connection..." |
| 3a | `integration_auth` | URL + not connected | User needs OAuth — includes `auth_url` |
| 3b | `integration_content` | URL + connected | Content was extracted successfully |
| 3c | `integration_error` | URL + FSD failure | Something broke on FSD side |
| 4 | `status` | periodic | Human-readable progress per agent node |
| 5 | `progress` | periodic | Detailed progress with step/substep |
| 6 | `context` | after memory | Info about query rewriting + history |
| 7 | `agents_planned` | after orchestrator | Which agents will run: `["Drafting"]`, `["Judgment", "Legislation"]` etc |
| 8 | `token` | streaming | Token-by-token LLM output (append to current response bubble) |
| 9 | `token_reset` | rarely | Clear the current accumulating response (agent restarted) |
| 10 | `drafting_progress` | drafting only | `{section, total, title, status, char_count?, error?}` — fires 2× per section (in_progress + completed/failed) |
| 11 | `draft_incomplete` | drafting error | Some sections failed; can retry via `/continue_draft` |
| 12 | `response` | always | Final synthesized response text (markdown) |
| 13 | `sources` | often | List of source citations (judgments, sections, etc) |
| 14 | `followup_suggestions` | always | 3 suggested next prompts |
| 15 | `done` | ✅ | Last event. Contains final metadata |
| ⚠️ | `error` | on failure | Something went wrong mid-stream |

---

## Event shapes (what to parse)

### `thread_id` — save this for follow-ups
```json
{"type": "thread_id", "data": "8f3d1a2b-4c5e-6789-abcd-ef0123456789"}
```

### `integration_status` — show "detecting URL" indicator
```json
{
  "type": "integration_status",
  "provider": "google",
  "urls": ["https://docs.google.com/document/d/abc123/edit?usp=sharing"],
  "message": "Google link detected. Checking connection..."
}
```

- `provider` is `"google"` or `"notion"`
- `urls` is a **list** (user may paste multiple URLs)
- One event per provider — if user pastes 1 Google Doc + 1 Notion page, you get 2 events

### `integration_auth` — user needs to connect
```json
{
  "type": "integration_auth",
  "provider": "google",
  "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?...",
  "message": "Please connect your Google account to access this content."
}
```

**What to do:**
1. Render a "Connect Google" button
2. On click: `window.open(auth_url, 'oauth', 'width=500,height=600')`
3. Wait for popup redirect → close popup → call `/pyapiv2/integration/poll` (see below)
4. After content is extracted, re-send the original message

### `integration_content` — content extracted, display confirmation
```json
{
  "type": "integration_content",
  "provider": "google",
  "title": "Pricing Document",
  "word_count": 1250,
  "message": "Extracted content from Pricing Document."
}
```

Show a chip: *"Pricing Document (1,250 words)"*.

### `integration_error` — FSD/network issue
```json
{
  "type": "integration_error",
  "provider": "google",
  "message": "Failed to get Google authorization URL."
}
```

Show a retry button.

### `status` — progress indicator
```json
{"type": "status", "agent": "orchestrator_plan", "message": "Planning search strategy..."}
```

Show agent label + message in a progress row.

### `progress` — granular progress (optional to display)
```json
{"type": "progress", "agent": "memory", "message": "Loading conversation history...", "ts": 1776089902.34, "step": "history"}
```

### `context` — memory/rewrite info
```json
{"type": "context", "query_rewritten": true, "effective_query": "What is bail under CrPC 437?", "history_turns": 2, "has_summary": false}
```

Useful to show "Based on previous messages, I understood: <effective_query>".

### `agents_planned`
```json
{"type": "agents_planned", "agents": ["Drafting", "Judgment"]}
```

Show agent chips early.

### `token` — streaming text
```json
{"type": "token", "content": "The "}
{"type": "token", "content": "defendant "}
{"type": "token", "content": "shall..."}
```

Append each `content` to the current response bubble.

### `drafting_progress`

Fires **twice per section**: once when generation starts (`status: "in_progress"`),
again when it finishes (`status: "completed"` or `status: "failed"`).

```json
// Section started
{"type": "drafting_progress", "section": 2, "total": 5, "title": "Pricing Tiers", "status": "in_progress"}

// Section finished successfully
{"type": "drafting_progress", "section": 2, "total": 5, "title": "Pricing Tiers", "status": "completed", "char_count": 1820}

// Section failed (rare; /continue_draft can retry these later)
{"type": "drafting_progress", "section": 2, "total": 5, "title": "Pricing Tiers", "status": "failed", "error": "<short error message>"}
```

**Recommended UI:** render a live checklist using the section number as a key,
updating each row's state based on `status`:

```
Drafting your partnership agreement...
  ● 1. Parties and Purpose         ✓ complete
  ● 2. Firm Details                ✓ complete
  ○ 3. Capital Contribution        in progress
  ○ 4. Management                  queued
  ○ 5. Dissolution                 queued
```

**Backward compatibility:** if you only read `section`, `total`, and `title`
(ignoring `status`), you get the same behavior as before Phase C shipped —
two events per section instead of one, but functionally equivalent. No
breaking change.

### `draft_incomplete` — partial draft, retry available
```json
{
  "type": "draft_incomplete",
  "failed_sections": [3, 5],
  "total_sections": 5,
  "completed_sections": [1, 2, 4]
}
```

Show a "Retry remaining sections" button → POST `/pyapiv2/continue_draft` with
the same `globalThreadId`.

### `response` — final clean response
```json
{"type": "response", "content": "# Pricing Policy\n\n## 1. Objective\n..."}
```

This is the full markdown output. If you've been streaming tokens, this matches
what you've built up — use it as the canonical final.

### `sources` — citations
```json
{
  "type": "sources",
  "data": [
    {"source_type": "judgment", "title": "Abc vs Xyz", "court_name": "Supreme Court", "year": 2023, "doc_link": "https://..."},
    {"source_type": "legislation", "title": "Section 437 CrPC", "content": ["..."]}
  ]
}
```

Render as a citations panel below the response.

### `followup_suggestions` — next-prompt chips
```json
{"type": "followup_suggestions", "data": [
  "Can this be used in High Court?",
  "What are the exceptions?",
  "Show me related Supreme Court judgments"
]}
```

Show as clickable chips that pre-fill the input.

### `done` — stream ended
```json
{
  "type": "done",
  "agents_used": ["Drafting"],
  "total_tokens": 2340,
  "thread_id": "8f3d1a2b-...",
  "conversation_turn": 3,
  "query_rewritten": false,
  "effective_query": null,
  "has_draft_continuation": false,
  "cached": false
}
```

Close the progress indicator. If `has_draft_continuation: true`, show the
retry button.

### `error` — failure mid-stream
```json
{"type": "error", "data": "Request timed out. Please try a simpler query."}
```

Show an error bubble. Stream is done.

---

## Typical Event Sequence (Happy Path)

```
1.  thread_id                                     (save this)
2.  integration_status (provider=google, urls=[...])   if URL in query
3.  integration_content (provider=google, title="...")  if connected
4.  progress... status... progress... status...   (periodic)
5.  context (query_rewritten=false, history_turns=1)
6.  status (agent=orchestrator_plan)
7.  agents_planned (agents=["Drafting"])
8.  drafting_progress (section=1, total=5)
9.  token token token... token                     (streaming)
10. drafting_progress (section=2, total=5)
11. ... more tokens + progress ...
12. response (content="# Policy\n\n...")
13. sources (data=[...])
14. followup_suggestions (data=["...", "...", "..."])
15. done
```

## Typical Event Sequence (OAuth needed)

```
1.  thread_id
2.  integration_status (provider=google, urls=["..."])
3.  integration_auth (auth_url="https://accounts.google.com/o/oauth2/...")
4.  status... progress...   (agent continues without content)
5.  response ("I can help with that. I see you wanted to reference a doc — please connect Google first.")
6.  followup_suggestions
7.  done
```

After OAuth completes → call `/pyapiv2/integration/poll` → then re-send the
original query.

---

## Companion Endpoint — `/pyapiv2/integration/poll`

Called **after** the OAuth popup closes to fetch the content.

```http
GET /pyapiv2/integration/poll?provider=google&url=<encoded_original_url>&token=<JWT>
X-API-Key: <api-key>
```

**Returns JSON (not SSE):**

```json
{
  "connected": true,
  "extracted": true,
  "provider": "google",
  "user": "user@gmail.com",
  "title": "Pricing Document",
  "content": "Pricing Plans\n\n1. Basic...",
  "word_count": 1250,
  "metadata": {"created": "...", "modified": "...", "owner": "..."}
}
```

Or if OAuth didn't complete:
```json
{"connected": false, "provider": "google", "message": "Google is not connected. Please try again."}
```

Or connected but can't read the doc (sharing settings):
```json
{
  "connected": true,
  "extracted": false,
  "provider": "google",
  "user": "user@gmail.com",
  "message": "Connected as user@gmail.com, but failed to extract content."
}
```

---

## Full Frontend Implementation Example

```javascript
async function sendChat(query, threadId, jwt) {
  const body = {
    Promptquery: query,
    globalThreadId: threadId || undefined,
    integration_token: jwt || undefined,
  };

  const response = await fetch("https://tool.lawttorney.com/pyapiv2/search/stream", {
    method: "POST",
    headers: {
      "X-API-Key": API_KEY,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) throw new Error(`HTTP ${response.status}`);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n\n");
    buffer = lines.pop();

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const event = JSON.parse(line.slice(6));
      handleEvent(event);
    }
  }
}

function handleEvent(event) {
  switch (event.type) {
    case "thread_id":
      currentThreadId = event.data;
      break;

    case "integration_status":
      showStatus(`${event.provider} detected for ${event.urls.length} url(s)`);
      break;

    case "integration_auth":
      showConnectButton(event.provider, event.auth_url);
      break;

    case "integration_content":
      showSuccess(`Extracted ${event.title} (${event.word_count} words)`);
      break;

    case "integration_error":
      showError(event.message);
      break;

    case "status":
      updateProgressRow(event.agent, event.message);
      break;

    case "agents_planned":
      showAgentChips(event.agents);
      break;

    case "drafting_progress":
      // status is one of "in_progress" | "completed" | "failed"
      // char_count present when status="completed"; error when status="failed"
      showDraftProgress(event.section, event.total, event.title, event.status);
      break;

    case "token":
      appendToCurrentResponse(event.content);
      break;

    case "response":
      setFinalResponse(event.content);
      break;

    case "sources":
      renderCitations(event.data);
      break;

    case "followup_suggestions":
      renderFollowupChips(event.data);
      break;

    case "done":
      onStreamComplete(event);
      break;

    case "error":
      showError(event.data);
      break;
  }
}

async function onConnectClick(provider, authUrl, originalQuery, originalUrl) {
  const popup = window.open(authUrl, "oauth", "width=500,height=600");

  // Poll for popup close
  await waitForPopupClose(popup);

  // Fetch the extracted content
  const resp = await fetch(
    `https://tool.lawttorney.com/pyapiv2/integration/poll?` +
    `provider=${provider}&url=${encodeURIComponent(originalUrl)}` +
    `&token=${userJwt}`,
    { headers: { "X-API-Key": API_KEY } }
  );
  const result = await resp.json();

  if (result.extracted) {
    // Re-send the original query, which will now extract content inline
    sendChat(originalQuery, currentThreadId, userJwt);
  } else {
    showError(result.message);
  }
}
```

---

## Error Handling

| Situation | Response |
|-----------|----------|
| Missing `X-API-Key` | HTTP 401 `{"detail": "Invalid or missing API key"}` |
| `Promptquery` empty or > 30000 chars | HTTP 422 with validation error |
| Invalid JSON | HTTP 422 |
| Backend timeout (300s) | `{type: "error", data: "Request timed out..."}` event, stream closes |
| FSD Chat Service down | `integration_error` event, agent continues without content |
| Unexpected server error | `{type: "error", data: "<message>"}` event |

---

## Rate Limits

- **200 requests per minute per API key** (configurable server-side via `RATE_LIMIT_PER_MINUTE`)
- Returns HTTP 429 when exceeded with a `Retry-After` header

---

## Caching

- **First-turn queries are cached** for 1 hour (no `globalThreadId` in request)
- Follow-up turns (with `globalThreadId`) are never cached
- **Queries with `integration_token` that extract content are NOT cached**
  (doc content may change)
- When a cache hit happens, you'll see `status`: "Returning cached response..." and `done.cached: true`
- Cached responses still emit `sources` and `followup_suggestions` events

---

## Recommended Integration Checklist

- [ ] Always send `X-API-Key` header
- [ ] Always pass `integration_token` (the user's JWT) once logged in
- [ ] Save `thread_id` from first event, pass as `globalThreadId` on follow-ups
- [ ] Handle all integration events (`integration_status`, `integration_auth`, `integration_content`, `integration_error`)
- [ ] Open OAuth in a popup, not the same tab
- [ ] After popup closes, call `/pyapiv2/integration/poll`, then re-send original query
- [ ] Show `followup_suggestions` as clickable chips
- [ ] Render `sources` as a citation panel
- [ ] Handle `draft_incomplete` with a retry button to `/continue_draft`
- [ ] Show an error state for `error` events and close the stream

---

## Test Credentials (for FSD dev)

- **API key** (for `X-API-Key`): shared privately — same one used in production backend
- **JWT source** (for `integration_token`): `POST https://test.lawttorney.com/v2/api/user/auths/login` with FSD-provided credentials

---

## Current Known Blockers (on FSD's side)

1. `/v2/api/chats/integration/status` returns HTTP 500 (UserId undefined in DB).
   Our client auto-falls back to per-provider status calls, so user-invisible —
   but please fix.
2. `/v2/api/chats/google` returns an OAuth URL with
   `redirect_uri=https://lawttorney.ai/api/google/google/callback` — doubled
   path + wrong domain → Google returns `Error 400: redirect_uri_mismatch`.
   **This blocks all content extraction end-to-end.**
