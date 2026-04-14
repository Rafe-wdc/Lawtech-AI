# Detailed Walkthrough: Drafting Flow with Google Doc Content

End-to-end explanation of how a user's chat message containing a Google Doc URL
becomes an AI-drafted response with content grounded in that doc.

**Companion doc:** [integration_flow_spec.md](integration_flow_spec.md) (API contract + team spec).
This doc is the *implementation walkthrough* — what happens in the code step-by-step.

## Table of Contents

- [Stage 1](#stage-1-gateway-receives-request) — Gateway receives request
- [Stage 2](#stage-2-sse-event-generator-starts) — SSE event generator starts
- [Stage 3](#stage-3-url-detection) — URL detection
- [Stage 4](#stage-4-emit-checking-status-event) — Emit "checking" status event
- [Stage 5](#stage-5-check-fsd-for-connection-status) — Check FSD for connection status
- [**Stage 5b**](#stage-5b-not-connected--oauth-flow) — **NOT connected branch: OAuth flow**
- [Stage 6](#stage-6-extract-content-from-the-doc) — Extract content from the doc
- [Stage 7](#stage-7-emit-content-extracted-event--build-state-dict) — Emit "content extracted" event
- [Stage 8](#stage-8-inject-into-langgraph-state) — Inject into LangGraph state
- [Stage 9](#stage-9-langgraph-agent-graph-runs) — LangGraph agent graph runs
- [Stage 10](#stage-10-drafting-agent-reads-integration_context) — Drafting agent reads integration_context
- [Stage 11](#stage-11-the-query-transformation) — The query transformation
- [Stage 12](#stage-12-drafting-agent-continues) — Drafting agent continues
- [Stage 13](#stage-13-response-streams-to-frontend) — Response streams to frontend
- [Full diagram](#the-entire-journey-in-one-diagram) — End-to-end diagram (with both branches)

---

## Starting Point: The User's Request

**User types in chat:**
```
Draft a pricing policy using https://docs.google.com/document/d/abc123/edit
```

**Frontend sends:**
```http
POST /pyapiv2/chat
X-API-Key: <our-api-key>
Content-Type: multipart/form-data

query             = "Draft a pricing policy using https://docs.google.com/..."
integration_token = "eyJhbGci...(JWT from FSD login)"
globalThreadId    = "<optional thread uuid>"
```

The user's JWT is from logging into `test.lawttorney.com`. It's how the FSD chat service identifies *which user's* Google token to use when fetching the doc.

---

## Stage 1: Gateway Receives Request

**File:** [core/gateway.py:848-856](../core/gateway.py#L848)

```python
@app.post("/pyapi/chat", dependencies=[Depends(require_user_key)])
async def chat_with_files(
    request: Request,
    query: str = Form(...),
    globalThreadId: Optional[str] = Form(None),
    preferred_language: Optional[str] = Form(None),
    integration_token: Optional[str] = Form(None),  # <- the FSD JWT
    files: List[UploadFile] = File(default=[]),
):
```

FastAPI parses the multipart body. The endpoint returns an SSE stream (not a regular JSON response).

**At this moment:**
```python
query             = "Draft a pricing policy using https://docs.google.com/document/d/abc123/edit"
integration_token = "eyJhbGci..."
```

---

## Stage 2: SSE Event Generator Starts

**File:** [core/gateway.py:913](../core/gateway.py#L913)

```python
async def event_generator():
    yield f"data: {json.dumps({'type': 'thread_id', 'data': thread_id})}\n\n"
```

**Frontend receives:**
```
data: {"type": "thread_id", "data": "abc-123-uuid"}
```

This tells the frontend "we're processing, stay connected for streaming events."

---

## Stage 3: URL Detection

**Files:** [core/gateway.py:944-960](../core/gateway.py#L944) + [core/integration_service.py:41-66](../core/integration_service.py#L41)

```python
# Inside event_generator()
integration_context_dict = None
if integration_token:                           # Only run if we have a JWT
    from core.integration_service import (
        detect_urls, IntegrationClient, process_integration_urls,
    )
    detected = detect_urls(query)              # <- regex match on the query
```

### How `detect_urls` Works

```python
_URL_PATTERNS = [
    ("google", "doc",   re.compile(r"https?://docs\.google\.com/document/d/([\w-]+)")),
    ("google", "sheet", re.compile(r"https?://docs\.google\.com/spreadsheets/d/([\w-]+)")),
    ("google", "drive", re.compile(r"https?://drive\.google\.com/file/d/([\w-]+)")),
    ("notion", "notion_page", re.compile(r"https?://(?:www\.)?notion\.(so|site)/\S+")),
]
```

Runs each regex over the query. Returns:
```python
detected = [
    DetectedURL(
        provider="google",
        url="https://docs.google.com/document/d/abc123/edit",
        url_type="doc",
    )
]
```

---

## Stage 4: Emit "Checking" Status Event

**File:** [core/chat_runner.py:88-96](../core/chat_runner.py#L88)

```python
for provider in providers_needed:
    provider_urls = [d.url for d in detected if d.provider == provider]
    yield (_sse({
        "type": "integration_status",
        "provider": provider,
        "urls": provider_urls,
        "message": f"{provider.title()} link detected. Checking connection...",
    }), None)
```

**Frontend receives:**
```json
{
  "type": "integration_status",
  "provider": "google",
  "urls": ["https://docs.google.com/document/d/abc123/edit"],
  "message": "Google link detected. Checking connection..."
}
```

If the user pastes multiple URLs for the same provider, `urls` is a list of
all of them. Frontend UI renders: *"Checking your Google connection for N
document(s)..."* — can show the doc URLs or filename chips right away.

---

## Stage 5: Check FSD for Connection Status

**File:** [core/integration_service.py:261-296](../core/integration_service.py#L261) — `process_integration_urls()`

```python
all_status = await client.check_all_status(token)
# -> {"google": True, "notion": False}

statuses = {"google": True}  # user IS connected to Google
```

**Under the hood, `check_all_status` hits:**
```http
GET https://test.lawttorney.com/v2/api/chats/integration/status
Authorization: Bearer eyJhbGci...

Response: {"status": true, "data": {"google": true, "notion": false}}
```

FSD looks up the user's record (by JWT) and checks if they have a valid Google OAuth token stored.

If this combined endpoint fails (it currently has a bug returning 500), the
client transparently falls back to calling `/google/status` and `/notion/status`
individually.

---

## Stage 5b: NOT Connected — OAuth Flow

If `check_all_status` returns `{"google": false}`, the user hasn't linked their
Google account to the FSD chat service yet. We cannot fetch the doc. The flow
branches into a conversational OAuth prompt.

### 5b.1 — Skip Extraction, Get Auth URL

**File:** [core/integration_service.py:290-299](../core/integration_service.py#L290)

```python
for det in detected:
    if not statuses.get(det.provider, False):
        logger.info("Skipping extraction -- not connected", provider=det.provider)
        continue   # <- skips extract_content() entirely
```

Back in the gateway:

**File:** [core/gateway.py:962-971](../core/gateway.py#L962)

```python
for provider in providers_needed:
    if not statuses.get(provider, False):
        auth_url = await _client.get_auth_url(provider, integration_token)
        if auth_url:
            yield f"data: {json.dumps({
                'type': 'integration_auth',
                'provider': provider,
                'auth_url': auth_url,
                'message': f'Please connect your {provider.title()} account to access this content.'
            })}\n\n"
        else:
            yield f"data: {json.dumps({
                'type': 'integration_error',
                'provider': provider,
                'message': f'Failed to get {provider.title()} authorization URL.'
            })}\n\n"
```

### 5b.2 — `get_auth_url` Calls FSD

**File:** [core/integration_service.py:139-152](../core/integration_service.py#L139)

```python
async def get_auth_url(self, provider: Provider, token: str) -> str | None:
    async with httpx.AsyncClient(timeout=self._timeout) as client:
        resp = await client.get(
            f"{self._base_url}/{provider}",
            headers=self._headers(token),
            follow_redirects=False,
        )
        resp.raise_for_status()
        return resp.json().get("url")
```

**Under the hood:**
```http
GET https://test.lawttorney.com/v2/api/chats/google
Authorization: Bearer eyJhbGci...

Response:
{
  "url": "https://accounts.google.com/o/oauth2/v2/auth?
          client_id=682158974410-...apps.googleusercontent.com
          &scope=https://www.googleapis.com/auth/documents.readonly
                https://www.googleapis.com/auth/drive.metadata.readonly
                https://www.googleapis.com/auth/userinfo.email
                https://www.googleapis.com/auth/userinfo.profile
          &redirect_uri=https://test.lawttorney.com/v2/api/chats/google/callback
          &response_type=code
          &access_type=offline
          &prompt=consent"
}
```

The URL is a real Google OAuth consent URL. When the user opens it, Google
handles the auth and posts back to FSD's callback endpoint.

### 5b.3 — Frontend Receives `integration_auth`

**SSE event to frontend:**
```json
{
  "type": "integration_auth",
  "provider": "google",
  "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?client_id=...",
  "message": "Please connect your Google account to access this content."
}
```

**Frontend UI renders a chat bubble:**
```
  Bot: I see a Google Doc link. To read this document, I need access
       to your Google account.

       [Connect Google Account]   <- clickable button

       Click above to authorize. I'll continue when you're done.
```

### 5b.4 — OAuth Popup

User clicks the button. Frontend runs:
```javascript
const popup = window.open(auth_url, 'oauth', 'width=500,height=600');
```

**What the user sees in the popup:**
```
Google: "Choose an account"
  -> user selects rohitjakkam@gmail.com

Google: "Lawtech-AI wants to access your Google Account"
  - See, edit, create Google Docs
  - See file metadata on your Drive
  - View email address and basic profile
  -> user clicks "Allow"
```

**Google redirects the popup to FSD's callback:**
```
GET https://test.lawttorney.com/v2/api/chats/google/callback?code=4/0AX...
```

**FSD's backend:**
1. Exchanges the `code` for an access token + refresh token (calls Google OAuth server-to-server)
2. Stores `{user_id: 14, google_access_token, google_refresh_token, google_email}` in DB
3. Redirects the popup to `{FRONTEND_URI}/NewChat?sync=success`

**Frontend detects the redirect** (via `postMessage` or polling `popup.location`)
and closes the popup.

### 5b.5 — Frontend Calls `/pyapiv2/integration/poll`

Once the popup closes, the original chat stream has already ended. Frontend
needs to fetch the content it skipped. It calls our poll endpoint:

**Request:**
```http
GET /pyapiv2/integration/poll?provider=google&url=<original_url>&token=<JWT>
X-API-Key: <our-api-key>
```

**File:** [core/gateway.py:1141-1189](../core/gateway.py#L1141)

```python
@app.get("/pyapi/integration/poll", dependencies=[Depends(require_user_key)])
async def integration_poll(provider: str, url: str, token: str):
    if provider not in ("google", "notion"):
        raise HTTPException(status_code=400, detail="Unsupported provider")

    client = IntegrationClient()
    status = await client.check_provider_status(provider, token)
    connected = client.is_connected(provider, status)

    if not connected:
        return {"connected": False, "provider": provider,
                "message": f"{provider.title()} is not connected. Please try again."}

    user_display = client.get_user_display(provider, status)
    result = await client.extract_content(provider, url, token)

    if not result:
        return {"connected": True, "extracted": False, "provider": provider,
                "user": user_display,
                "message": f"Connected as {user_display}, but failed to extract content."}

    return {
        "connected": True,
        "extracted": True,
        "provider": provider,
        "user": user_display,
        "title": result.title,
        "content": result.content,
        "word_count": len(result.content.split()),
        "metadata": result.metadata,
    }
```

### 5b.6 — Three Possible Outcomes from Poll

**Outcome A — Success:**
```json
{
  "connected": true,
  "extracted": true,
  "provider": "google",
  "user": "rohitjakkam@gmail.com",
  "title": "Pricing Document",
  "content": "Pricing Plans\n\n1. Basic Plan - $9/month...",
  "word_count": 1250,
  "metadata": {"created": "...", "modified": "...", "owner": "..."}
}
```

Frontend shows: *"Connected as rohitjakkam@gmail.com. Extracted Pricing Document (1,250 words)."*

Now the frontend sends a **new chat message** with the same query, and this time
the FSD status check returns `{"google": true}` -> the full happy path runs ->
agent gets the content -> produces an informed draft.

**Outcome B — Still not connected:**
```json
{"connected": false, "provider": "google",
 "message": "Google is not connected. Please try again."}
```

Happens if the OAuth popup failed or user cancelled. Frontend shows a "Retry
Connection" button.

**Outcome C — Connected but extraction failed:**
```json
{"connected": true, "extracted": false, "provider": "google",
 "user": "rohitjakkam@gmail.com",
 "message": "Connected as rohitjakkam@gmail.com, but failed to extract content."}
```

Usually means the doc's sharing settings don't permit the connected Google
account. Frontend shows: *"I'm connected, but this doc's sharing settings don't
allow me to view it. Please check sharing and try again."*

### 5b.7 — Full Conversation Flow (Not Connected Case)

```
User:  Draft a pricing policy using https://docs.google.com/document/d/abc/edit

                [SSE stream 1 begins]
Bot:   [integration_status]  Google link detected. Checking connection...
Bot:   [integration_auth]    Please connect your Google account.
                             [Connect Google Account]   <- button
                [stream 1 ends — agent runs without content, gives generic answer]

User:  *clicks button*
                [popup opens -> Google OAuth -> popup closes]

                [Frontend calls /pyapiv2/integration/poll]
Bot:   Connected as rohitjakkam@gmail.com.
       Extracted "Pricing Document" (1,250 words).

User:  [same prompt, possibly auto-retriggered by frontend]
       Draft a pricing policy using https://docs.google.com/document/d/abc/edit

                [SSE stream 2 begins]
Bot:   [integration_status]   Google link detected. Checking connection...
Bot:   [integration_content]  Extracted Pricing Document (1,250 words).
                              [happy path: agent has content, drafts accurately]
```

### 5b.8 — The FSD OAuth Bug (Currently Blocks This Flow)

When we call `GET /v2/api/chats/google`, FSD currently returns an OAuth URL
whose `redirect_uri` is:

```
https://lawttorney.ai/api/google/google/callback
```

Two issues:
1. **Domain mismatch** — points to `lawttorney.ai` instead of `test.lawttorney.com`
2. **Doubled path** — `/google/google/callback` (typo)

Google rejects with **Error 400: `redirect_uri_mismatch`** because this URI
isn't registered in the OAuth client's Authorized Redirect URIs list in
Google Cloud Console.

**Until FSD fixes this**, users cannot actually complete OAuth -> Stage 5b.5
(the poll call) always returns `{"connected": false}` -> cannot extract content.

Our code is correct and ready. It's a config fix on their end.

---

## Stage 6: Extract Content from the Doc

**Files:** [core/integration_service.py:290-295](../core/integration_service.py#L290) + [core/integration_service.py:165-210](../core/integration_service.py#L165)

```python
for det in detected:
    if not statuses.get(det.provider, False):
        continue  # skip if not connected
    result = await client.extract_content(det.provider, det.url, token)
    if result:
        contents.append(result)
```

### What `extract_content` Does

```python
# Google uses "url", Notion uses "input" — normalized here
body = {"url": url} if provider == "google" else {"input": url}

POST https://test.lawttorney.com/v2/api/chats/google/process
Authorization: Bearer eyJhbGci...
Body: {"url": "https://docs.google.com/document/d/abc123/edit"}

Response:
{
  "success": true,
  "document": {
    "id": "abc123",
    "title": "Mock Pricing Document",
    "content": "Pricing Plans\n\n1. Basic Plan - $9/month...",
    "metadata": {
      "created": "2026-01-15T10:30:00Z",
      "modified": "2026-04-01T14:20:00Z",
      "owner": "test@gmail.com"
    }
  }
}
```

FSD's backend uses the user's stored Google OAuth token to call Google Docs API (`docs.googleapis.com`), gets the doc content, and returns plain text.

The function **normalizes** Google's response shape (it's under `.document`) vs Notion's (under `.page`) into a single object:

```python
return IntegrationContent(
    provider="google",
    title="Mock Pricing Document",
    content="Pricing Plans\n\n1. Basic Plan - $9/month...",
    url="https://docs.google.com/...",
    metadata={"created": ..., "modified": ..., "owner": "test@gmail.com"},
)
```

---

## Stage 7: Emit "Content Extracted" Event & Build State Dict

**File:** [core/gateway.py:973-991](../core/gateway.py#L973)

```python
if contents:
    combined_text = "\n\n".join(
        f"--- {c.title} ({c.provider}) ---\n{c.content}"
        for c in contents
    )
    integration_context_dict = {
        "provider": contents[0].provider,
        "title": contents[0].title,
        "content": combined_text,
        "url": contents[0].url,
        "metadata": contents[0].metadata,
        "documents": [{...} for c in contents],  # per-doc summary
    }
    yield f"data: {json.dumps({'type': 'integration_content', ...})}\n\n"
```

**Frontend receives:**
```json
{"type": "integration_content", "provider": "google",
 "title": "Mock Pricing Document", "word_count": 1250,
 "message": "Extracted content from Mock Pricing Document."}
```

UI updates: *"Extracted Mock Pricing Document (1,250 words)"*

---

## Stage 8: Inject into LangGraph State

**File:** [core/gateway.py:993-1002](../core/gateway.py#L993)

```python
initial_state = _build_initial_state(
    query, thread_id,
    file_context=file_context_dict,
    preferred_language=preferred_language,
)
if integration_context_dict:
    initial_state["integration_context"] = integration_context_dict
```

**The state is now:**
```python
{
    "original_query": "Draft a pricing policy using https://docs.google.com/...",
    "query": "Draft a pricing policy using https://docs.google.com/...",
    "thread_id": "abc-123-uuid",
    "user_language": "en",
    "task": None,                 # orchestrator will set this
    "tasks_planned": [],          # orchestrator will set this
    "agent_queries": {},          # orchestrator will set this
    "chat_history": [],
    "agent_results": {},
    "is_blocked": False,
    "file_context": None,         # no PDFs uploaded
    "integration_context": {      # <- OUR CONTENT IS HERE
        "provider": "google",
        "title": "Mock Pricing Document",
        "content": "--- Mock Pricing Document (google) ---\nPricing Plans\n\n1. Basic Plan - $9/month\n...",
        "url": "https://docs.google.com/document/d/abc123/edit",
        "metadata": {"created": ..., "modified": ..., "owner": "test@gmail.com"},
        "documents": [{"provider": "google", "title": "Mock Pricing Document", "word_count": 1250}],
    },
    "draft_continuation": None,
    "final_response": "",
    "source_metadata": [],
    "tokens_consumed": 0,
}
```

---

## Stage 9: LangGraph Agent Graph Runs

**Files:** [core/gateway.py:1003-1006](../core/gateway.py#L1003) + [core/graph.py](../core/graph.py)

```python
async for event in agent_graph.astream(
    initial_state,
    config=config,
    stream_mode=["updates", "custom"],
):
    ...
```

### Graph Node Sequence

```
 guardrail_input (checks query safety)
      |
      v
 memory (language detection, chat history, query rewriting)
      |
      v
 orchestrator (classifies task: "Drafting" -> plans agents: ["Drafting"])
      |
      v
 drafting <- OUR CONSUMPTION HAPPENS HERE
      |
      v
 orchestrator_synthesize (merges agent results)
      |
      v
 guardrail_output (PII check, hallucination check)
```

The orchestrator classifies the query. The word "draft" plus the URL -> task = `"Drafting"`. Only one agent needed.

---

## Stage 10: Drafting Agent Reads integration_context

**File:** [agents/drafting.py:665-678](../agents/drafting.py#L665)

```python
agent_queries = state.get("agent_queries", {})
query = agent_queries.get("Drafting") or state.get("query") or state.get("original_query", "")
user_context = state.get("user_context", "")
user_language = state.get("user_language", "en")
integration_ctx = IntegrationContextData.from_state(state)  # <- HERE

log.info("Agent started", query=query[:100],
         has_user_context=bool(user_context),
         has_integration_context=bool(integration_ctx and integration_ctx.has_content),
         using_agent_query="Drafting" in agent_queries)

# For long queries: include pasted content in the drafting query
if user_context:
    query = f"User's document/context:\n{user_context}\n\nUser's instruction:\n{query}"

# Prepend content fetched from third-party integrations (Google Docs, Notion)
if integration_ctx and integration_ctx.has_content:
    query = integration_ctx.as_prompt_prefix() + f"User's instruction:\n{query}"
```

### How `IntegrationContextData.from_state` Works

**File:** [core/state.py:213-244](../core/state.py#L213)

```python
@dataclass
class IntegrationContextData:
    provider: str = ""
    title: str = ""
    content: str = ""
    url: str = ""
    metadata: dict = field(default_factory=dict)
    documents: list[dict] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(self.content)

    def as_prompt_prefix(self) -> str:
        if not self.content:
            return ""
        label = f"{self.provider.title()} document ({self.title})"
        return f"Reference content from user's {label}:\n{self.content}\n\n"

    @classmethod
    def from_state(cls, state: dict) -> IntegrationContextData | None:
        ic = state.get("integration_context")
        if not ic:
            return None
        return cls(**{k: v for k, v in ic.items()
                      if k in cls.__dataclass_fields__})
```

**After deserializing:**
```python
integration_ctx = IntegrationContextData(
    provider="google",
    title="Mock Pricing Document",
    content="--- Mock Pricing Document (google) ---\nPricing Plans\n\n1. Basic Plan - $9/month\n...",
    url="https://docs.google.com/...",
    metadata={...},
    documents=[{...}],
)
integration_ctx.has_content == True
```

Same pattern exists in the **Scenario** agent — see [agents/scenario.py:47-61](../agents/scenario.py#L47).

---

## Stage 11: The Query Transformation

### Before

```python
query = "Draft a pricing policy using https://docs.google.com/document/d/abc123/edit"
```

### After `as_prompt_prefix()`

`as_prompt_prefix()` returns:

```
Reference content from user's Google document (Mock Pricing Document):
--- Mock Pricing Document (google) ---
Pricing Plans

1. Basic Plan - $9/month
   - 5 projects
   - 10GB storage
   - Email support

2. Pro Plan - $29/month
   - 50 projects
   - 100GB storage
   - Priority support
   - API access

3. Enterprise Plan - $99/month
   - Unlimited projects
   - 1TB storage
   - 24/7 phone support
   - Custom integrations


```

### After the full prepend

```python
query = as_prompt_prefix() + f"User's instruction:\n{query}"
```

Now `query` is:

```
Reference content from user's Google document (Mock Pricing Document):
--- Mock Pricing Document (google) ---
Pricing Plans

1. Basic Plan - $9/month
   - 5 projects
   - 10GB storage
...

3. Enterprise Plan - $99/month
   - Unlimited projects
   - 1TB storage
   - 24/7 phone support


User's instruction:
Draft a pricing policy using https://docs.google.com/document/d/abc123/edit
```

This is now a **rich prompt with reference data**. The LLM can see both:
- The user's intent ("draft a pricing policy")
- The actual reference data (the 3 tiers, their features, prices)

---

## Stage 12: Drafting Agent Continues

The drafting agent does several more things with this enhanced query:

1. **Select template** — [agents/drafting.py:683-710](../agents/drafting.py#L683) picks the closest drafting template from Elasticsearch (policy, agreement, notice, etc.).

2. **Generate outline** — Gemini 2.5 Flash with structured output produces a section list:
```json
{"sections": [
  {"title": "1. Policy Objective"},
  {"title": "2. Pricing Tiers"},
  {"title": "3. Billing and Payment Terms"},
  {"title": "4. Discounts and Promotions"},
  {"title": "5. Policy Review Schedule"}
]}
```

3. **Generate each section** — For "2. Pricing Tiers", the LLM has the full reference content in its prompt, so it produces text like:

```
## 2. Pricing Tiers

The Company offers three tiers:

a) Basic Plan — $9 per month
   Intended for small teams, includes up to 5 projects and 10GB storage,
   with email-based support.

b) Pro Plan — $29 per month
   Intended for growing organizations, includes up to 50 projects, 100GB
   storage, priority support, and API access.

c) Enterprise Plan — $99 per month
   Intended for large organizations, includes unlimited projects, 1TB
   storage, 24/7 phone support, and custom integrations.
```

Because the prompt had the exact tier names, prices, and features — the draft is **accurate, not hallucinated**.

4. **Stream tokens back** — As each section generates, tokens stream via `token` SSE events.

---

## Stage 13: Response Streams to Frontend

```
data: {"type": "token", "content": "## "}
data: {"type": "token", "content": "1."}
data: {"type": "token", "content": " Policy"}
data: {"type": "token", "content": " Objective"}
...
data: {"type": "drafting_progress", "section": 2, "total": 5, "title": "Pricing Tiers", "status": "in_progress"}
data: {"type": "drafting_progress", "section": 2, "total": 5, "title": "Pricing Tiers", "status": "completed", "char_count": 1820}
...
data: {"type": "response", "content": "<full markdown draft>"}
data: {"type": "sources", "data": [...]}
data: {"type": "followup_suggestions", "data": [...]}
data: {"type": "done", "agents_used": ["Drafting"], "total_tokens": 2340, ...}
```

Frontend assembles the tokens into a live-streaming response bubble, then shows the final formatted draft.

---

## The Entire Journey in One Diagram

```
+-------------------------------------------------------------------------+
|  1. User types in chat: "Draft a pricing policy using <google doc URL>" |
+-----------------------------+-------------------------------------------+
                              v
+-------------------------------------------------------------------------+
|  2. POST /pyapiv2/chat with query + integration_token (JWT)             |
+-----------------------------+-------------------------------------------+
                              v
+-------------------------------------------------------------------------+
|  3. detect_urls(query) -> [DetectedURL(provider='google', url='...')]   |
+-----------------------------+-------------------------------------------+
                              v
+-------------------------------------------------------------------------+
|  4. SSE: integration_status "Google link detected. Checking..."         |
+-----------------------------+-------------------------------------------+
                              v
+-------------------------------------------------------------------------+
|  5. FSD API: /integration/status -> {"google": true or false}           |
+-----------------------------+-------------------------------------------+
                              v
                        branch on status
                  +-----------+-----------+
                  |                       |
            connected=true         connected=false
                  |                       |
                  |                       v
                  |   +----------------------------------------------+
                  |   | 5b.1 get_auth_url() -> FSD GET /google       |
                  |   | 5b.2 returns Google OAuth URL                |
                  |   | 5b.3 SSE: integration_auth (frontend)        |
                  |   | 5b.4 user opens popup, Google consent,       |
                  |   |      FSD callback stores token               |
                  |   | 5b.5 frontend -> GET /pyapiv2/integration/   |
                  |   |      poll re-checks status, then extracts    |
                  |   |      returns JSON content                    |
                  |   | 5b.6 frontend resends original chat message  |
                  |   +-------------------+--------------------------+
                  |                       |
                  +-----------+-----------+
                              v
+-------------------------------------------------------------------------+
|  6. FSD API: /google/process {url: ...} -> {document: {title, content}} |
+-----------------------------+-------------------------------------------+
                              v
+-------------------------------------------------------------------------+
|  7. SSE: integration_content "Extracted Mock Pricing Document"          |
+-----------------------------+-------------------------------------------+
                              v
+-------------------------------------------------------------------------+
|  8. state["integration_context"] = {provider, title, content, ...}      |
+-----------------------------+-------------------------------------------+
                              v
+-------------------------------------------------------------------------+
|  9. agent_graph.astream(state) — LangGraph runs                         |
|     guardrail -> memory -> orchestrator(task=Drafting) -> drafting -> ..|
+-----------------------------+-------------------------------------------+
                              v
+-------------------------------------------------------------------------+
| 10. Drafting agent:                                                     |
|     ic = IntegrationContextData.from_state(state)                       |
|     if ic.has_content:                                                  |
|         query = ic.as_prompt_prefix() + "User's instruction:\n" + query |
+-----------------------------+-------------------------------------------+
                              v
+-------------------------------------------------------------------------+
| 11. Gemini LLM gets prompt with:                                        |
|     "Reference content from user's Google document (Pricing Doc):       |
|      <actual doc text>                                                  |
|                                                                         |
|      User's instruction: Draft a pricing policy using <URL>"            |
+-----------------------------+-------------------------------------------+
                              v
+-------------------------------------------------------------------------+
| 12. LLM generates informed, accurate draft referencing real $9/$29/$99  |
|     prices and feature lists from the doc (no hallucination)            |
+-----------------------------+-------------------------------------------+
                              v
+-------------------------------------------------------------------------+
| 13. SSE: token ... token ... token -> response -> done                  |
|     Frontend renders markdown draft                                     |
+-------------------------------------------------------------------------+
```

---

## Why This Design Is Clean

**Separation of concerns:**
- **Gateway** knows how to talk HTTP/SSE, nothing about drafting
- **integration_service** knows how to talk to FSD, nothing about agents
- **state.py** defines the contract, nothing about either side
- **Drafting agent** knows how to draft, nothing about where content came from

The agent doesn't care if the content came from a Google Doc, a Notion page, a Linear issue, or anywhere else — it just sees `integration_context.content` and uses it.

**Extensibility:**

To add a new provider (e.g., Linear):
1. Add a regex pattern to `_URL_PATTERNS`
2. Add FSD endpoints (they already did this)
3. Normalize the response shape in `extract_content()` switch
4. **Zero changes needed in agents** — they already read any content via `IntegrationContextData`

**Graceful degradation:**

If FSD is down, `IntegrationClient` catches the error, returns `None`, logs it, and the agent simply runs without the enhanced context — no crash, user still gets a response.

---

## Current Status (2026-04-13)

Verified live on `https://tool.lawttorney.com/pyapiv2/chat`:

| Stage | Status |
|-------|--------|
| 1-5: URL detection + SSE events + FSD status check | ✅ Working |
| 6-7: Content extraction | ⚠️ Blocked on FSD OAuth bug (`redirect_uri_mismatch`) |
| 8-10: State injection + agent consumption | ✅ Code verified against mock server |
| 11-13: LLM generation + streaming response | ✅ Working when content present |

Once FSD fixes the OAuth config (`https://lawttorney.ai/api/google/google/callback` typo + domain mismatch), the full end-to-end flow will work without any code changes on our side.

---

## Files Touched in This Feature

| File | Role |
|------|------|
| [core/settings.py](../core/settings.py) | `CHAT_SERVICE_URL` config |
| [core/state.py](../core/state.py) | `integration_context` field + `IntegrationContextData` helper |
| [core/integration_service.py](../core/integration_service.py) | URL detection, FSD API client, content normalization |
| [core/gateway.py](../core/gateway.py) | Chat endpoint wiring + `/pyapi/integration/poll` |
| [agents/drafting.py](../agents/drafting.py) | Consumes `integration_context` in prompt |
| [agents/scenario.py](../agents/scenario.py) | Same — for scenario-based queries |
| [tests/mock_integration_server.py](../tests/mock_integration_server.py) | Local FSD mock for testing |
| [tests/live_integration_test.py](../tests/live_integration_test.py) | Live probe against test.lawttorney.com |
| [docs/integration_flow_spec.md](integration_flow_spec.md) | Team-facing API spec |
| [docs/integration_flow_walkthrough.md](integration_flow_walkthrough.md) | This document |
