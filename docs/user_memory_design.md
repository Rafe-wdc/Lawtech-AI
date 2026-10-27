# User memory (cross-session lawyer profile)

Status: BUILT on branch `feat/user-memory` (2026-10-27), behind
`USER_MEMORY_ENABLED` (default off). Design approved 2026-10-27 with the
amendments folded in below.

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
- UPDATE with no matching row becomes ADD; DELETE with no match drops.

## 4. Extraction

Model: `get_gemini_flash_lite(temperature=0)`, one call per turn after
`save_turn`, 20 s ceiling, never on the request path. Skipped when any of
these hold, cheapest first:

1. flag off, or no verified `user_id`;
2. the message looks like a pasted document (over 1,500 chars, 8+ line
   breaks, or markers such as "IN THE HON'BLE COURT", "WHEREAS",
   "VERIFICATION", "PRAYER", "AFFIDAVIT", markdown headings);
3. no first-person marker. English: I, I'm, I've, I am, my, me, we, our,
   us. Hinglish / Marathi romanised: mera, meri, mere, hamara, hamari,
   hum, main, mujhe, majha, majhi, amcha, amchi, mala, aamhi. Devanagari:
   मैं, मेरा, मेरी, मेरे, मुझे, हम, हमारा, हमारी, हमें, मी, माझ, मला,
   आम्ही, आमच;
4. the user has memory switched off;
5. the thread is `no_memory`.

Input: the current profile as JSON, and the **user text only**, capped at
500 characters, plus a short window around the first first-person marker
when it lies past the cap (so "I'm Adv. X" at the end of a long request
still reaches the extractor). The assistant reply, uploads and pasted
drafts are never passed.

The prompt is `EXTRACTION_PROMPT` in `core/user_memory.py` (the approved
text, with `designation` added to the slot list and
`preferred_language` pinned to ISO codes).

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

All routes need the usual `X-API-Key` and a verified `X-User-Id`
(section 8). 404 when the flag is off; 401 when the id is missing or the
signature is bad. Error bodies follow the gateway's `{error, message}`
shape.

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

**User id (decided)**: header `X-User-Id`. Signing is required before
prod, in this order of preference:

1. If Node already issues a JWT the frontend sends as
   `Authorization: Bearer <jwt>`, set `USER_MEMORY_JWT_SECRET` to Node's
   HS256 secret. Python verifies the signature and `exp`, and takes the
   id from `sub` / `userId` / `user_id` / `uid` / `id` / `_id`. With this
   set, a bare `X-User-Id` is ignored.
2. Otherwise set `USER_MEMORY_ID_SECRET`; Node sends
   `X-User-Sig = hex(HMAC-SHA256(secret, user_id))` beside `X-User-Id`.
   A missing or wrong signature means memory is off for that request and
   the review API answers 401.
3. With neither secret set a bare `X-User-Id` is accepted, with a warning
   in the log. Dev only.

Flags:

| Env | Default | Meaning |
|---|---|---|
| `USER_MEMORY_ENABLED` | `0` | master switch |
| `USER_MEMORY_ID_SECRET` | unset | HMAC secret for `X-User-Sig` |
| `USER_MEMORY_JWT_SECRET` | unset | HS256 secret of Node's JWT |
| `USER_MEMORY_EXTRACT_TIMEOUT_S` | `20` | ceiling on the extraction call |

Cost: one Flash Lite call of roughly 1,800 input / 100 output tokens per
extracted turn (about USD 0.0002), and the gates skip most turns.

Guardrails: hard exclusions plus the regex filter run on every value;
every row keeps `thread_id` for tracing; a profile-bearing request never
touches the shared response cache (a cached draft would carry one
lawyer's signature block to the next user); shared firm logins build one
mixed profile, which the review page is the remedy for.

## 9. Frontend brief (short)

1. Send `X-User-Id` (and `X-User-Sig` or the JWT per section 8) on
   `POST /pyapi/chat`, `POST /pyapi/search/stream`, `POST /pyapi/search`
   and all `/pyapi/memory*` calls.
2. Optional `no_memory=true` form field on `POST /pyapi/chat` (JSON field
   on the search endpoints) for a "private chat" toggle.
3. A profile page built on section 7: list rows grouped by `label`, mark
   `confirmed: false` as "unconfirmed, tap to confirm", edit / delete,
   "Clear all", and the memory on/off switch.
4. A memory-command reply arrives as a normal `response` event with
   `agents_used: ["Memory"]` in `done`; no special rendering needed.

## 10. Tests

`tests/test_user_memory_core.py` (gates, exclusions, validation rules,
rendering, direct-command parsing, id verification),
`tests/test_user_memory_store.py` (SQLite store, apply_ops, extraction
pipeline with a stubbed extractor, remember / forget),
`tests/test_user_memory_injection.py` (cacheable_system placement,
ContextVar propagation, review API via TestClient).
