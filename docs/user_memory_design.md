# User memory (cross-session lawyer profile)

Status: BUILT on branch `feat/user-memory` (2026-10-27), behind
`USER_MEMORY_ENABLED` (default off). Design approved 2026-10-27 with the
amendments folded in below. Pre-prod hardening 2026-10-28: fail-closed
auth, JWT as the only prod identity path (Node confirmed the frontend
holds an HS256 JWT from login).

Scope:

- Memory is per **user**, not per thread. It stores durable facts about the
  lawyer only. It never stores client, opposite-party, matter, case-number,
  hearing-date or amount facts.
- Extraction returns per-fact operations (ADD / UPDATE / DELETE / NONE) and
  the operations are applied to the table. The table is never append-only.
- The profile is injected **after** the static system prompt and **before**
  the user message, so the cached prefix stays byte-identical across users.
- A per-thread `no_memory` flag skips extraction for that thread. A per-user
  setting switches memory off entirely (no injection, no extraction).
- Explicit "remember that ..." / "forget that ..." messages are saved or
  deleted directly as `user_stated`, and the reply confirms what changed.
- First consumer: drafting fills `[Advocate Name]`, `[Advocate Address]`
  and the signature block from the profile.

## 1. Where it plugs in

| Step | Where | What |
|---|---|---|
| Identify the user | `core/gateway.py` (`_resolve_memory_user`) -> `core/user_memory.py::resolve_user_id` | `X-User-Id` header, verified (section 8). Empty or unverifiable -> memory is off for that request. |
| Per-thread opt-out | form field / JSON field `no_memory` -> `threads.no_memory` | Sticky per thread once set. |
| Direct command | `core/chat_runner.py` and `/pyapi/search`, before the cache and the graph | "remember that ..." / "forget ..." as the whole message. Graph does not run. Section 6. |
| Load profile | same place, `load_profile_block(user_id)` | One SELECT; rendered block goes on state as `user_profile_block`. A profile-bearing request never reads or writes the shared response cache. |
| Inject | `agents/drafting.py::drafting_node` sets a request-scoped ContextVar; `core/clients.py::cacheable_system` appends it | A second, uncached system text block after the cached one. Section 5. |
| Extract | after `chat_store.save_turn(...)` in both paths | Fire-and-forget, 20 s ceiling, Gemini Flash Lite, raw user text only. Section 4. |
| Review API | `core/gateway.py` `/pyapi/memory*` | Section 7. |

Both chat stores (`_SqliteChatHistoryStore` dev, `_PostgresChatHistoryStore`
prod) carry the same tables and methods: `load_user_memory`,
`insert_user_memory`, `update_user_memory`, `delete_user_memory`,
`delete_user_memory_all`, `get/set_user_memory_enabled`,
`get/set_thread_no_memory`. Schema is created on first use, like the other
tables.

## 2. Profile schema

```sql
CREATE TABLE user_memory (
    id          INTEGER PRIMARY KEY,     -- BIGSERIAL on Postgres
    user_id     TEXT NOT NULL,
    slot        TEXT NOT NULL,           -- closed set below
    value       TEXT NOT NULL,           -- plain text, <= 300 chars
    source      TEXT NOT NULL CHECK (source IN ('user_stated', 'inferred')),
    confidence  REAL NOT NULL DEFAULT 1.0,   -- 1.0 user_stated; 0.70-0.95 inferred
    thread_id   TEXT NOT NULL DEFAULT '',    -- where learned; audit only, never injected
    created_at, updated_at,
    UNIQUE (user_id, slot, value)
);
CREATE TABLE user_memory_settings (       -- per-user on/off for the review page
    user_id     TEXT PRIMARY KEY,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at
);
ALTER TABLE threads ADD COLUMN no_memory BOOLEAN NOT NULL DEFAULT FALSE;
```

| Slot | Cardinality | Identity | Drafting uses it for |
|---|---|---|---|
| `advocate_name` | single | yes | `[Advocate Name]`, signature block |
| `designation` | single | yes | "Advocate" / "Senior Advocate" / "Partner" in the signature block |
| `enrolment_no` | single | yes | signature block, Vakalatnama |
| `firm_name` | single | yes | letterhead, From block |
| `office_address` | single | yes | `[Advocate Address]`, From block |
| `phone`, `email` | single | yes | From block, notices |
| `courts` | multi (<= 8) | no | default forum when the request names none |
| `practice_areas` | multi (<= 8) | no | nothing yet |
| `preferred_language` | single | no | default draft language |
| `drafting_preference` | multi (<= 8) | no | style lines in the injected block |
| `note` | multi (<= 8) | no | review page only, never injected |

Rules:

- Single slots hold one row; ADD on an existing single slot is an UPDATE.
- **Identity slots**: inferred rows are stored but **never injected**. The
  review page shows them as unconfirmed (`confirmed: false`,
  `injected: false`); PATCH (edit or plain confirm) promotes them to
  `user_stated`.
- A `user_stated` row can be modified (UPDATE, DELETE, or ADD-as-UPDATE on
  a single slot) **only by an op that is itself `user_stated`**
  (confidence 1.0). Enforced server-side in `validate_ops`, independent of
  what the extractor claims.
- Multi slots: a 9th ADD evicts the oldest `inferred` row; if all 8 are
  `user_stated` the ADD is dropped. Whole profile caps at 40 rows.

## 3. Operations contract

Extractor output:

```json
{"ops": [
  {"op": "ADD",    "slot": "courts",         "value": "Bombay High Court",            "source": "inferred",    "confidence": 0.85},
  {"op": "UPDATE", "slot": "office_address", "value": "201, Lex Towers, Pune 411004", "old_value": "...", "source": "user_stated", "confidence": 1.0},
  {"op": "DELETE", "slot": "phone",          "value": "+91 98xxxxxx12", "source": "user_stated", "confidence": 1.0}
]}
```

`{"ops": []}` means NONE. Server-side validation before any write:

- `slot`, `op`, `source` from the closed sets; `value` 1-300 chars.
- `user_stated` is forced to confidence 1.0; `inferred` below 0.70 drops.
- Hard-exclusion filter on every value: CNR pattern, case-number patterns
  (`No. 123`, `123/2026`, `FIR/WP/CC ... 12`), amounts (`Rs`, `₹`, `lakh`,
  `/-`), dates (numeric and written), and for identity slots any
  party-role word (client, accused, petitioner, ...). `enrolment_no` is
  exempt from the case-number pattern only (`MAH/1234/2015`).
- The `user_stated` protection rule above.
- **Implicit identity lock** (added 2026-10-28 after live negative test
  b2): from post-turn extraction, an existing `user_stated` row in
  `advocate_name`, `enrolment_no` or `firm_name` can be neither updated
  nor deleted, whatever source the model claims. The model's
  "user_stated" label is its own judgement and a quoted notice fooled it.
  A name, enrolment number or firm changes only on "remember that" /
  "forget that" or on the review page. Office, phone, email and
  designation stay implicitly updatable (the approved "our office has
  moved" case). A first ADD into an empty locked slot is still allowed.
- UPDATE with no matching row becomes ADD; DELETE with no match drops.

## 4. Extraction

Model: `get_gemini_flash_lite(temperature=0)`, one call per turn after
`save_turn`, 20 s ceiling, never on the request path. Skipped when any of
these hold, cheapest first:

1. flag off, or no verified `user_id`;
2. the message looks like a pasted document (over 1,500 chars, 8+ line
   breaks, or markers such as "IN THE HON'BLE COURT", "WHEREAS",
   "VERIFICATION", "PRAYER", "AFFIDAVIT", markdown headings) or like
   correspondence the lawyer received ("LEGAL NOTICE", "TAKE NOTICE",
   lines starting `From:` / `To:` / `Sub:` / `Ref:` / `Dated:`, "under
   instructions from", "Yours faithfully", "Dear Sir", "call upon you",
   "this is to inform you"), checked on the original text;
3. no first-person marker **after quoted spans are removed** (anything
   24+ chars inside "...", "...", «...» or a clearly quoted '...' is
   replaced by `[quoted material omitted]`; whoever says "I" inside a
   quotation is not the lawyer). English: I, I'm, I've, I am, my, me, we, our,
   us. Hinglish / Marathi romanised: mera, meri, mere, hamara, hamari,
   hum, main, mujhe, majha, majhi, amcha, amchi, mala, aamhi. Devanagari:
   मैं, मेरा, मेरी, मेरे, मुझे, हम, हमारा, हमारी, हमें, मी, माझ, मला,
   आम्ही, आमच;
4. the user has memory switched off;
5. the thread is `no_memory`.

Input: the current profile as JSON, and the **user text only** with
quoted spans removed, capped at 500 characters, plus a short window around the first first-person marker
when it lies past the cap (so "I'm Adv. X" at the end of a long request
still reaches the extractor). The assistant reply, uploads and pasted
drafts are never passed.

The prompt is `EXTRACTION_PROMPT` in `core/user_memory.py` (the approved
text, with `designation` added to the slot list, `preferred_language`
pinned to ISO codes, and two exclusions added 2026-10-28: quoted or
received correspondence, and one-off instructions for the current draft,
with the b2 notice as a worked `{"ops": []}` example).

## 5. Injection block

Rendered by `render_profile_block(rows)`. Empty profile, or only inferred
identity rows, -> no block and no second system message, so such requests
are byte-identical to today.

```text
## ABOUT THE ADVOCATE USING THIS ASSISTANT (saved profile)
These are the advocate's OWN details and standing preferences. Use them
to fill the advocate's name, designation, enrolment number, firm, address,
phone and email wherever a document needs them (signature block, "From"
block, Vakalatnama, notice letterhead) instead of bracketed placeholders
such as [Advocate Name] or [Advocate Address]. They are NOT facts of the
matter: never present the advocate as a party, witness or deponent. If
the current request gives a different value for any of these, the
request wins.

Advocate: Adv. Priya S. Kulkarni, Advocate (Enrolment MAH/1234/2015)
Firm: Kulkarni & Associates
Office: 201, Lex Towers, Fergusson College Rd, Pune 411004
Phone: +91 98xxxxxx12 | Email: priya@kulkarnilaw.in
Regular courts: Bombay High Court; District Court, Pune
Practice areas: criminal; cheque dishonour
Preferred draft language: Marathi
Drafting preferences:
- number prayer clauses (a), (b), (c)
- prefers "Hon'ble Court" over "the Court"
```

About 150-250 tokens; hard cap 2,000 characters (~500 tokens), trimmed
from the bottom. `thread_id`, `source`, `confidence` and `note` rows are
never injected.

Placement (`cacheable_system(text, trailer)`; `trailer=None` reads the
request-scoped block the drafting node set):

```python
# Anthropic: cached static block first, then an UNCACHED block.
SystemMessage(content=[
    {"type": "text", "text": static_prompt, "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": profile_block},
])
# Gemini / OpenAI: string system prompt; profile appended AFTER the static text.
SystemMessage(content=static_prompt + "\n\n" + profile_block)
```

No static prompt text changed, so caching and output for users without a
profile are unaffected.

## 6. Explicit "remember that" / "forget that"

Detected deterministically when the **whole** message (<= 600 chars) is
the directive: `remember / note / save (that) ...`, `forget / delete /
remove (that|about) ...`, Hindi and Marathi forms (`याद रखो कि`, `लक्षात ठेव`,
`भूल जाओ`, `विसरा`), romanised `yaad rakho`, `bhool jao`. "Forget
everything" / "delete all my details" clears the profile.

- The graph does not run; the turn is still saved to history as task
  `Memory`.
- "remember": the remainder goes to the extractor in remember mode; every
  resulting op is forced to `user_stated`; hard exclusions still apply, so
  "remember the hearing is on 3 Nov" saves nothing and the reply says why.
- "forget": extractor in forget mode, DELETE ops only, `user_stated`.
- Reply lists exactly what was saved or removed, in the stored wording:

```text
Saved to your profile:
- Advocate name: Adv. Priya S. Kulkarni
- Enrolment number: MAH/1234/2015

You can see or change this any time from your profile page.
```

A long drafting request that merely contains "remember that I ..." goes
through the normal pipeline; the post-turn extractor picks the fact up as
`user_stated`.

## 7. Review API

All routes need the usual `X-API-Key` and a verified user identity
(section 8: `Authorization: Bearer <jwt>`). Status contract agreed with
Node: **403** when memory is off on this server (flag off, or flag on
with no verification secret: fail closed); **401** when the access token
is missing, expired, forged or disagrees with an `X-User-Id` sent beside
it (the frontend refreshes the token on 401 and retries). Error bodies
follow the gateway's `{error, message}` shape.

| Route | Body | Returns |
|---|---|---|
| `GET /pyapi/memory` | - | `{memory_on, rows: [{id, slot, label, value, source, confirmed, injected, updated_at}], slots: [{slot, label, single}]}` |
| `PUT /pyapi/memory/settings` | `{"memory_on": bool}` | per-user on/off |
| `POST /pyapi/memory` | `{"slot", "value"}` | adds a `user_stated` row (400 if the value looks like matter data, 409 if duplicate / slot full) |
| `PATCH /pyapi/memory/{id}` | `{"value"?}` | edits, or with no value confirms; becomes `user_stated` |
| `DELETE /pyapi/memory/{id}` | - | removes one row |
| `DELETE /pyapi/memory` | - | clears the profile |

`confirmed: false` rows are the "unconfirmed" items on the page;
`injected` says whether the model currently sees the row.

## 8. Identity, flags, cost, guardrails

**User identity (decided 2026-10-28, Node's answers folded in)**: the
frontend receives an HS256 access token from Node at login (15 min life;
refresh for 30 days via `POST /v2/api/user/auths/refresh`). That token is
the only production identity path for memory. The frontend forwards it
unchanged as `Authorization: Bearer <jwt>`; Python verifies it with
Node's secret (`USER_MEMORY_JWT_SECRET`), `algorithms=["HS256"]` only
("none" and RS* are rejected) and `exp` with about 30 s of leeway
(`USER_MEMORY_JWT_LEEWAY_S`). There is no `sub`, `iss` or `aud` and none
is checked. The user id is the `userId` claim, Node's numeric DB primary
key, cast to a string before use (`USER_MEMORY_JWT_CLAIM` can point at
another claim if that ever changes). No other claim is read: the
`username` claim never populates the email slot or anything else. The id
must match `^[A-Za-z0-9_.:@\-]{1,128}$`.

**Secret mapping (runbook)**: `USER_MEMORY_JWT_SECRET` in the Python
`.env` must equal the user-service's `JWT_ACCESS_SECRET` (falling back to
its `JWT_SECRET` where `JWT_ACCESS_SECRET` is unset). Rotate both
together. The same block is in `docs/deployment_guide.md`, Step 5.

A bare `X-User-Id` is not an identity on the JWT path. If the frontend
still sends it, it must equal the JWT claim; a mismatch is treated as a
bug and the request runs with memory off (401 on the review API).

**Fail closed**: with `USER_MEMORY_ENABLED=1` and no secret configured,
no user id is ever resolved, so extraction and injection stay off and
every `/pyapi/memory*` route answers 403. Bare unsigned ids are accepted
only behind an explicit `USER_MEMORY_ALLOW_UNSIGNED=1`, for local dev.
A configured secret always wins over that opt-in. The earlier HMAC
`X-User-Sig` path is kept in code (`USER_MEMORY_ID_SECRET`) but is not
the production path and is not in the frontend brief.

Flags:

| Env | Default | Meaning |
|---|---|---|
| `USER_MEMORY_ENABLED` | `0` | master switch |
| `USER_MEMORY_JWT_SECRET` | unset | HS256 secret of Node's login JWT (prod path) |
| `USER_MEMORY_JWT_CLAIM` | `userId` | claim holding the user id (comma list allowed) |
| `USER_MEMORY_JWT_LEEWAY_S` | `30` | clock-skew allowance on `exp` / `nbf` |
| `USER_MEMORY_ALLOW_UNSIGNED` | `0` | local dev only: accept bare `X-User-Id` when no secret is set |
| `USER_MEMORY_ID_SECRET` | unset | HMAC secret for `X-User-Sig` (kept, not the prod path) |
| `USER_MEMORY_EXTRACT_TIMEOUT_S` | `20` | ceiling on the extraction call |

Cost: one Flash Lite call of roughly 1,800 input / 100 output tokens per
extracted turn (about USD 0.0002), and the gates skip most turns.

Guardrails: hard exclusions plus the regex filter run on every value;
every row keeps `thread_id` for tracing; a profile-bearing request never
touches the shared response cache (a cached draft would carry one
lawyer's signature block to the next user); shared firm logins build one
mixed profile, which the review page is the remedy for.

## 9. Frontend brief (short)

1. Forward the existing access token as `Authorization: Bearer <jwt>` on
   `POST /pyapi/chat`, `POST /pyapi/search/stream`, `POST /pyapi/search`
   and every `/pyapi/memory*` call. No other identity header is needed;
   if `X-User-Id` is sent anyway it must be the same id as the `userId`
   claim. On 401 from a memory route, refresh the token
   (`POST /v2/api/user/auths/refresh`) and retry once; the token is checked
   once at the start of a stream, so a 15-minute token never cuts a
   running stream short.
   The stream client must support custom request headers: a plain
   `EventSource` cannot set `Authorization`, so use `fetch` with a
   ReadableStream reader (or `@microsoft/fetch-event-source`) for the SSE
   endpoints.
2. Optional `no_memory=true` form field on `POST /pyapi/chat` (JSON field
   on the search endpoints) for a "private chat" toggle.
3. A profile page built on section 7: list rows grouped by `label`, mark
   `confirmed: false` as "unconfirmed, tap to confirm", edit / delete,
   "Clear all", and the memory on/off switch. Treat 403 as "memory is off
   on this server" and hide the page.
4. A memory-command reply arrives as a normal `response` event with
   `agents_used: ["Memory"]` in `done`; no special rendering needed.

Ops: set `USER_MEMORY_ENABLED=1` and `USER_MEMORY_JWT_SECRET` (= the
user-service's `JWT_ACCESS_SECRET`, fallback `JWT_SECRET`) together.

## 10. Tests

`tests/test_user_memory_core.py` (gates, exclusions, validation rules,
rendering, direct-command parsing, id verification),
`tests/test_user_memory_store.py` (SQLite store, apply_ops, extraction
pipeline with a stubbed extractor, remember / forget),
`tests/test_user_memory_injection.py` (cacheable_system placement,
ContextVar propagation, review API via TestClient).

## 11. Verification log and follow-ups

Local live runs with the flag on (Gemini generation; Anthropic credit was
out on both days; `scratchpad/live_negative_test.py` in the session notes):

- 2026-10-27, unsigned dev path: remember command saved 8 facts; a
  Section 138 notice draft used the profile for letterhead, From block
  and signature with no `[Advocate Name]` placeholder; "Our office has
  moved ..." mid-question updated the address; "Forget my phone number"
  removed it.
- 2026-10-28, JWT path (throwaway secret, numeric `userId` claim):
  bare id / forged token / mismatched `X-User-Id` all 401, good token
  200. (a) the prompt's Ramesh Yadav example stored exactly
  advocate_name, enrolment_no, office_address and nothing about the
  client, FIR or hearing. (b1) a pasted legal notice naming opposing
  counsel left the profile untouched. (b2) a short QUOTED notice in
  which opposing counsel wrote "I, Adv. Rakesh Mehta ..." FAILED on the
  first run: the extractor overwrote the lawyer's name and address and
  added the other side's firm, phone and email as user_stated. Fixed the
  same day (quote stripping, correspondence markers, implicit identity
  lock, prompt exclusions; sections 3 and 4); b2 and a marker-free
  variant (b3) then left the profile untouched, and (a) still passed.

Follow-ups:

- **Claude draft with the flag on** (pending Anthropic credit): one live
  draft confirming the profile is used and `cache_read_tokens` in the
  `done` event is still non-zero, i.e. the trailer block did not break
  the cached prefix.
- **Legislation agent treats a profile sentence as part of the question**
  (observed 2026-10-27): "Our office has moved to ... What is the
  limitation period ..." produced an answer opening with "An office
  relocation does not alter ... the limitation period". Harmless but
  visible. Candidate fix: strip the sentence that the extractor consumed
  from the query handed to the router, or add a one-line instruction to
  the Legislation prompt to ignore personal asides. Tracked as
  https://github.com/Rohitjakkam/Lawtech-AI/issues/141.

