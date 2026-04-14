# Integration Flow Specification — Chat-Based URL Content Extraction

**Date:** 2026-04-13  
**Status:** Live API verified against `https://test.lawttorney.com/v2/api/chats`  
**Purpose:** Define the end-to-end flow for handling third-party URL integrations (Google Docs, Notion) within the chat interface.  
**Audience:** Frontend, Backend (FSD), AI/Chat Team

---

## Overview

When a user pastes a third-party URL (Google Doc, Drive, Notion) into the chat, the system should:

1. Detect the URL and identify the integration type
2. Check if the user has an active connection to that service
3. If not connected — guide the user through OAuth via SSE events
4. Once connected — extract the content from the URL
5. Inject the extracted content into the AI agent pipeline as context

The **chat layer** (our Python backend) orchestrates the flow. The **FSD chat service** handles OAuth, token management, and content extraction.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Frontend (Chat UI)                │
│  - Sends user message to /pyapi/chat                │
│    (includes integration_token form field)          │
│  - Receives SSE events (status, auth_url, token)    │
│  - Opens OAuth popup when auth_url event received   │
│  - Polls /pyapi/integration/poll after OAuth        │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│              Our Backend (gateway.py)                │
│  core/integration_service.py                        │
│  - Detects URLs in user query                       │
│  - Calls FSD Chat Service APIs                      │
│  - Emits SSE events for frontend                    │
│  - Injects extracted content into agent state       │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│           FSD Chat Service                          │
│  Base: https://test.lawttorney.com/v2/api/chats     │
│  - OAuth flows (Google, Notion)                     │
│  - Token storage per user                           │
│  - Content extraction from URLs                     │
└─────────────────────────────────────────────────────┘
```

---

## FSD Chat Service — Endpoints (Verified Live)

**Base URL:** `https://test.lawttorney.com/v2/api/chats`  
**Auth:** `Authorization: Bearer <JWT_TOKEN>` on all endpoints

### Login to get JWT (for reference)

```
POST /v2/api/user/auths/login
Body: { "email": "...", "password": "..." }
→ { "status": true, "message": "User Login Successfully", "token": "eyJhbG..." }
```

### Integration Endpoints

| # | Method | Endpoint | Purpose | Status |
|---|--------|----------|---------|--------|
| 1 | `GET` | `/integration/status` | All integrations status | **BUG: 500 error** |
| 2 | `GET` | `/google` | Get Google OAuth URL | OK |
| 3 | `GET` | `/google/callback?code=` | OAuth callback (FSD handles) | OK |
| 4 | `GET` | `/google/status` | Check Google connection | OK |
| 5 | `POST` | `/google/disconnect` | Disconnect Google | OK |
| 6 | `POST` | `/google/process` | Extract from Google Doc | OK |
| 7 | `GET` | `/notion` | Get Notion OAuth URL | OK |
| 8 | `GET` | `/notion/callback?code=` | OAuth callback (FSD handles) | OK |
| 9 | `GET` | `/notion/status` | Check Notion connection | OK |
| 10 | `POST` | `/notion/disconnect` | Disconnect Notion | OK |
| 11 | `POST` | `/notion/process` | Extract from Notion page | OK |

### Known FSD Bug

`GET /v2/api/chats/integration/status` returns:
```json
{"error":"WHERE parameter \"UserId\" has invalid \"undefined\" value"}
```
HTTP 500. **Our client auto-falls back to per-provider status calls** (`/google/status` + `/notion/status`) so end users aren't affected. FSD team should fix.

### Key Response Shapes

**Google status:**
```json
{ "authenticated": true, "user": { "id": "...", "email": "...", "name": "..." } }
// or
{ "authenticated": false }
```

**Notion status:**
```json
{ "connected": true, "workspace": { "id": "...", "name": "..." }, "user": { "id": "...", "name": "..." } }
// or
{ "connected": false, "workspace": null, "user": null }
```

**Google extraction:**
```json
POST /google/process  { "url": "https://docs.google.com/..." }
→ { "success": true, "document": { "id": "...", "title": "...", "content": "...", "metadata": {...} } }
```

**Notion extraction:**
```json
POST /notion/process  { "input": "https://www.notion.so/..." }
→ { "success": true, "page": { "id": "...", "title": "...", "content": "..." } }
```

### Key Differences — Google vs Notion

| Aspect | Google | Notion |
|--------|--------|--------|
| Status field | `authenticated` | `connected` |
| Extract body field | `url` | `input` |
| Extract response key | `response.document.content` | `response.page.content` |
| OAuth callback redirect | `?sync=success` | `?notion=success` |

Our [integration_service.py](../core/integration_service.py) normalizes these behind a unified interface.

---

## URL Detection Patterns

| Integration  | URL Pattern | Regex |
|-------------|-------------|-------|
| Google Docs  | `docs.google.com/document/d/{id}` | `docs\.google\.com/document/d/[\w-]+` |
| Google Drive | `drive.google.com/file/d/{id}` | `drive\.google\.com/file/d/[\w-]+` |
| Google Sheets | `docs.google.com/spreadsheets/d/{id}` | `docs\.google\.com/spreadsheets/d/[\w-]+` |
| Notion | `notion.so/{slug}` or `{workspace}.notion.site/` | `notion\.(so|site)/\S+` |

---

## SSE Events (New Types for Integration)

Emitted during the chat stream when an integration URL is detected:

```json
// 1. URL detected, checking connection
{"type": "integration_status", "provider": "google", "urls": ["https://docs.google.com/document/d/abc123/edit"], "message": "Google link detected. Checking connection..."}

// 2a. Not connected — need OAuth
{"type": "integration_auth", "provider": "google", "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?...", "message": "Please connect your Google account..."}

// 2b. Connected — content extracted
{"type": "integration_content", "provider": "google", "title": "Document Title", "word_count": 1250, "message": "Extracted content from Document Title."}

// 3. Error
{"type": "integration_error", "provider": "google", "message": "Failed to get Google authorization URL."}
```

---

## Frontend Handling

| SSE Event | Frontend Action |
|-----------|----------------|
| `integration_status` | Show progress message in chat bubble |
| `integration_auth` | Show "Connect" button + open OAuth popup on click |
| `integration_content` | Show success message, continue streaming AI response |
| `integration_error` | Show error message with retry option |

### OAuth Popup Flow

1. Frontend receives `integration_auth` event with `auth_url`
2. User clicks "Connect" → `window.open(auth_url, '_blank', 'popup')`
3. OAuth completes → FSD redirects to `{FRONTEND_URI}/NewChat?sync=success` (Google) or `?notion=success` (Notion)
4. Frontend detects redirect, closes popup
5. Frontend calls `GET /pyapi/integration/poll?provider=google&url=<original_url>&token=<jwt>`
6. Our backend re-checks status and extracts content, returns JSON result

---

## State Changes

New field in `LegalAgentState`:

```python
integration_context: dict | None
# {
#   "provider": "google",
#   "title": "Document Title",
#   "content": "Full extracted text...",
#   "url": "https://docs.google.com/...",
#   "metadata": { ... },
#   "documents": [ ... ]  # if multiple URLs
# }
```

Agents can access this via `state.get("integration_context")` and include the content as additional context in their LLM prompts.

---

## Responsibilities

### AI/Chat Backend (Us) — DONE

- [core/integration_service.py](../core/integration_service.py) — URL detection, FSD API client, content normalization
- [core/settings.py](../core/settings.py) — `CHAT_SERVICE_URL` config
- [core/state.py](../core/state.py) — `integration_context` field
- [core/gateway.py](../core/gateway.py) — SSE events in `/pyapi/chat`, new `/pyapi/integration/poll`
- [tests/mock_integration_server.py](../tests/mock_integration_server.py) — mock server for offline testing

### FSD / Chat Service Team

- All endpoints at `https://test.lawttorney.com/v2/api/chats/`
- OAuth implementation (Google, Notion) — working
- Token storage, refresh, revocation — working
- Content extraction from provider APIs — working
- **Fix**: `/integration/status` returns 500 error (UserId undefined)

### Frontend Team

- Handle `integration_*` SSE events in chat UI
- OAuth popup management (open, detect completion, close)
- Pass user's JWT via `integration_token` form field on `/pyapi/chat`
- Call `/pyapi/integration/poll` after OAuth popup closes

---

## Open Questions for Meeting

1. **Google Drive files** — `/google/process` currently supports Docs. Will it support Drive files (PDF, DOCX, Sheets)?
2. **Linear integration** — Not in the current FSD API. Is it planned? Timeline?
3. **Token expiry mid-conversation** — If a JWT expires during extraction, does FSD auto-refresh or return 401?
4. **Content size limits** — Max content length from `/process` endpoints?
5. **Multiple URLs** — If user pastes 2 URLs in one message, our code handles both. Confirm this is desired.
6. **Caching** — Should we cache extracted content for the duration of a thread? What if the source doc changes?
7. **Auth token source** — Is the same JWT used for both our Python API and the FSD chat service? Or separate tokens?

---

## Files in this PR

| File | Purpose |
|------|---------|
| `core/integration_service.py` | URL detection, FSD API client |
| `core/settings.py` | `CHAT_SERVICE_URL` config |
| `core/state.py` | `integration_context` state field |
| `core/gateway.py` | Chat endpoint wiring + `/pyapi/integration/poll` |
| `tests/mock_integration_server.py` | Mock FSD server for testing |
| `docs/integration_flow_spec.md` | This document |

---

## Testing

### Against real FSD API (verified 2026-04-13)

```python
from core.integration_service import IntegrationClient, process_integration_urls

client = IntegrationClient()  # uses CHAT_SERVICE_URL from settings
token = "<JWT from login>"

detected, statuses, contents = await process_integration_urls(
    "Update from https://docs.google.com/document/d/abc/edit",
    token, client,
)
```

### Against local mock server

```bash
# Terminal 1: start mock
python tests/mock_integration_server.py

# Terminal 2: override CHAT_SERVICE_URL
export CHAT_SERVICE_URL=http://localhost:9001/api/chats
# Then run your tests
```
