# User memory (cross-session lawyer profile) - design for review

Status: DESIGN, nothing built yet. Review the profile schema (section 2) and
the extraction prompt (section 4) before implementation starts.

Scope, as agreed on 2026-10-27:

- Memory is per **user**, not per thread. It stores durable facts about the
  lawyer only. It never stores client, opposite-party, matter, case-number,
  hearing-date or amount facts.
- Extraction returns per-fact operations (ADD / UPDATE / DELETE / NONE) and
  the operations are applied to the table. The table is never append-only.
- The profile is injected **after** the static system prompt and **before**
  the user message, so the cached prefix stays byte-identical across users.
- A per-thread `no_memory` flag skips extraction for that thread.
- Explicit "remember that ..." / "forget that ..." messages are saved or
  deleted directly with a `user_stated` tag, and the reply confirms it.
- Everything is gated on `USER_MEMORY_ENABLED` (default off).
- First consumer: drafting fills `[Advocate Name]`, `[Advocate Address]`
  and the signature block from the profile.

## 1. Where it plugs in

| Step | Where | What |
|---|---|---|
| Identify the user | `core/gateway.py` `chat_with_files` / `chat` | New form field `user_id` (same style as `plan`), header `X-User-Id` accepted as fallback. Empty -> memory is off for that request. |
| Per-thread opt-out | `core/gateway.py` + `threads.no_memory` | New form field `no_memory` (bool). Once set on a thread it sticks for the thread. |
| Load profile | `agents/memory.py::memory_node` | One SELECT by `user_id`, rendered to the injection block (section 5). Put on state as `user_profile_block`. |
| Inject | `agents/drafting.py` (3 call sites that build `[SystemMessage(cacheable_system(...)), HumanMessage(...)]`) | A second, uncached system text block after the cached one. Section 5. |
| Direct save / delete | `agents/memory.py::memory_node`, before the rewriter | Regex for a whole-message "remember that ..." / "forget that ..." (English, Hindi, Marathi forms). Section 6. |
| Extract | `core/gateway.py`, right after `chat_store.save_turn(...)` | Fire-and-forget task, 20 s ceiling, Gemini Flash Lite. Never on the request's critical path. Section 4. |
| Review API | `core/gateway.py` | `GET /pyapi/memory`, `PATCH /pyapi/memory/{id}`, `DELETE /pyapi/memory/{id}`, `DELETE /pyapi/memory`. Section 7. |

Both chat stores (`_SqliteChatHistoryStore` for dev, `_PostgresChatHistoryStore`
for prod) get the same new table and the same five methods:
`load_user_memory`, `apply_memory_ops`, `delete_user_memory`,
`set_thread_no_memory`, `get_thread_no_memory`.

## 2. Profile schema

One row per fact. A fixed set of **slots** keeps UPDATE and DELETE
well-defined and keeps the injected block predictable.

```sql
CREATE TABLE IF NOT EXISTS user_memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,   -- BIGSERIAL on Postgres
    user_id     TEXT NOT NULL,
    slot        TEXT NOT NULL,          -- closed set, see table below
    value       TEXT NOT NULL,          -- plain text, <= 300 chars
    source      TEXT NOT NULL CHECK (source IN ('user_stated', 'inferred')),
    confidence  REAL NOT NULL DEFAULT 1.0,   -- 1.0 for user_stated; 0.70-0.95 for inferred
    thread_id   TEXT NOT NULL DEFAULT '',    -- where it was learned; audit only, never injected
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, slot, value)
);
CREATE INDEX IF NOT EXISTS idx_user_memory_user ON user_memory(user_id);

ALTER TABLE threads ADD COLUMN no_memory INTEGER NOT NULL DEFAULT 0;
```

Slots:

| Slot | Cardinality | Example value | Used by drafting for |
|---|---|---|---|
| `advocate_name` | single | `Adv. Priya S. Kulkarni` | `[Advocate Name]`, signature block |
| `enrolment_no` | single | `MAH/1234/2015` | signature block, Vakalatnama |
| `firm_name` | single | `Kulkarni & Associates` | letterhead line, From block |
| `office_address` | single | `201, Lex Towers, Fergusson College Rd, Pune 411004` | `[Advocate Address]`, From block |
| `phone` | single | `+91 98xxxxxx12` | From block, notices |
| `email` | single | `priya@kulkarnilaw.in` | From block, notices |
| `courts` | multi | `Bombay High Court`; `District Court, Pune` | default forum when the user does not say |
| `practice_areas` | multi | `criminal`; `family`; `cheque dishonour` | nothing yet (future: retrieval hints) |
| `preferred_language` | single | `mr` | default draft language when `preferred_language` form field is empty |
| `drafting_preference` | multi | `prefers "Hon'ble Court" over "the Court"`; `wants prayer clauses numbered (a), (b)` | style instruction in the injection block |
| `note` | multi | `teaches at ILS Law College on Saturdays` | nothing; shown to the user on the review page only |

Rules:

- Single-cardinality slots hold at most one row per user. ADD on an existing
  single slot behaves as UPDATE.
- Multi slots can hold up to 8 rows per user. A 9th ADD evicts the oldest
  `inferred` row; if all 8 are `user_stated`, the ADD is dropped and logged.
- `user_stated` rows are never changed or deleted by the extractor. Only the
  user (a "forget that ..." message or the review API) can remove them. The
  extractor may still UPDATE an `inferred` row with a `user_stated` one.
- Total profile cap: 40 rows per user.
- `thread_id` is written on insert and never shown to the model.

## 3. Operations contract

The extractor returns:

```json
{"ops": [
  {"op": "ADD",    "slot": "courts",         "value": "Bombay High Court",            "source": "inferred",    "confidence": 0.85},
  {"op": "UPDATE", "slot": "office_address", "value": "201, Lex Towers, Pune 411004", "source": "user_stated", "confidence": 1.0},
  {"op": "DELETE", "slot": "phone",          "value": "+91 98xxxxxx12"}
]}
```

`{"ops": []}` means NONE. Applying:

| op | single slot | multi slot |
|---|---|---|
| ADD | replace the existing row (same as UPDATE) | insert if `(user_id, slot, value)` not present |
| UPDATE | replace the existing row's value, source, confidence | find row by `(slot, old_value)` if given, else by exact `value`; replace |
| DELETE | delete the row for the slot | delete the row matching `(slot, value)`; a missing row is a no-op |

Server-side validation before any write (the model is not trusted):

- `slot` must be in the closed set, `op` in the three verbs, `value` 1-300
  chars after trim, `confidence` in [0, 1].
- `inferred` ops with confidence < 0.70 are dropped.
- A hard-exclusion filter runs on every `value` regardless of what the
  model said: anything matching a CNR pattern (`[A-Z]{4}\d{12}`), a case
  number pattern (`\d+/\d{4}`, `No\. ?\d+`), a currency amount
  (`Rs\.?|₹|INR` followed by digits), or a date is dropped and logged.
- The extractor cannot DELETE or UPDATE a `user_stated` row.

## 4. Extraction prompt

Model: `get_gemini_flash_lite(temperature=0.0)`, JSON output, called once per
turn after `save_turn`, with a 20 s ceiling. It is skipped when any of these
hold: flag off, no `user_id`, thread `no_memory`, the turn was a
conversational acknowledgement, or the user message has no first-person
marker (`\b(I|I'm|my|me|we|our|मैं|मेरा|मेरी|हम|मी|माझ[ाीे]|आम्ही)\b`).
The last check avoids paying for turns such as "what does section 138 say".

Inputs are the current profile (as a JSON list of `{slot, value, source}`),
the user's message for this turn (first 4,000 characters, uploads excluded),
and the first 600 characters of the assistant's reply. Pasted drafts and
uploaded documents are never passed.

```text
You maintain a short profile of the LAWYER who is using a legal drafting
assistant. Your only job is to decide what, if anything, in this
conversation turn is a DURABLE fact about the lawyer themself, and to
return the operations needed to keep the profile correct.

The profile is used to fill the advocate's own details in court documents
(signature block, "From" address, Vakalatnama) and to apply the lawyer's
standing preferences. It is NOT a record of the lawyer's cases.

## What to store (durable facts about the lawyer)
- Their own name, enrolment / bar council number, firm name, office
  address, phone, email.
- Courts and forums where they regularly practise.
- Practice areas.
- Preferred language for drafts.
- Standing drafting preferences ("always number prayer clauses", "use
  'Hon'ble Court'", "keep notices under two pages").
- Other stable personal-professional facts the lawyer states about
  themself (teaches, is a notary, is a senior counsel).

## What you MUST NOT store, under any circumstances
- Client names, opposite-party names, accused, complainant, witness,
  deponent, or any person who is not the lawyer.
- Facts of any matter: what happened, where, when, to whom.
- Case numbers, CNR numbers, FIR numbers, diary numbers, file numbers.
- Hearing dates, filing dates, limitation dates, or any date tied to a
  matter.
- Amounts: claim values, cheque amounts, fees, compensation, fines.
- Anything taken from an uploaded document or a pasted draft.
- Judges' names, opposing counsel's names.
- Anything about the current matter's court or forum when it is said
  only for this matter ("file this in Agra ITAT"). Store a court ONLY
  when the lawyer says they practise there regularly or asks to use it
  by default.
- Temporary states ("I am busy today", "I have a hearing tomorrow").
- Anything the lawyer asks you to forget.
If a fact is about a matter rather than about the lawyer, return no
operation for it. When in doubt, do not store.

## Slots (use exactly these names)
single-valued: advocate_name, enrolment_no, firm_name, office_address,
               phone, email, preferred_language
multi-valued:  courts, practice_areas, drafting_preference, note

## Operations
- ADD: a new fact. For a single-valued slot this replaces any existing
  value.
- UPDATE: the lawyer corrected or changed an existing fact. Give the new
  value in "value" and the old one in "old_value".
- DELETE: the lawyer said a fact is wrong or no longer applies, or asked
  to forget it. Give the exact stored value.
- If nothing durable about the lawyer appears, return {"ops": []}.

## Source and confidence
- "user_stated": the lawyer said it about themself in plain words, or
  used "remember that". confidence 1.0.
- "inferred": a reasonable reading that the lawyer did not state
  outright (for example, their fourth draft in Bombay High Court
  suggests they practise there). confidence between 0.70 and 0.95.
  Below 0.70, return nothing.
Never UPDATE or DELETE a fact whose source is "user_stated" unless the
lawyer explicitly changes or withdraws it in this turn.

## Output
Return ONLY a JSON object, no prose, no code fence:
{"ops": [{"op": "ADD|UPDATE|DELETE", "slot": "<slot>", "value": "<text>",
          "old_value": "<text or omit>", "source": "user_stated|inferred",
          "confidence": <number>}]}
Values are plain text, at most 300 characters, written the way they
should appear in a document (for example "Adv. Priya S. Kulkarni", not
"the user is Priya").

## Examples
Turn: "Draft a bail application for my client Ramesh Yadav, FIR 112/2026
PS Hadapsar, hearing on 3 Nov. I'm Adv. Priya Kulkarni, enrolment
MAH/1234/2015, office at 201 Lex Towers, Pune."
-> ops: ADD advocate_name "Adv. Priya Kulkarni" (user_stated, 1.0);
        ADD enrolment_no "MAH/1234/2015" (user_stated, 1.0);
        ADD office_address "201 Lex Towers, Pune" (user_stated, 1.0).
   Nothing about Ramesh Yadav, the FIR, Hadapsar or 3 Nov.

Turn: "Reply to this legal notice. The notice demands Rs 4,50,000."
-> {"ops": []}

Turn: "Our office has moved to 5th floor, Sadhu Vaswani Chowk, Pune."
Profile has office_address "201 Lex Towers, Pune".
-> ops: UPDATE office_address value "5th floor, Sadhu Vaswani Chowk,
        Pune", old_value "201 Lex Towers, Pune" (user_stated, 1.0).

Turn: "Forget my phone number."
Profile has phone "+91 98xxxxxx12".
-> ops: DELETE phone "+91 98xxxxxx12".

Turn: "Always write the prayer clauses as (a), (b), (c), not 1, 2, 3."
-> ops: ADD drafting_preference "number prayer clauses (a), (b), (c)"
        (user_stated, 1.0).

Turn: "File this appeal in ITAT Agra."
-> {"ops": []}   (forum for this matter only)

## Current profile
{profile_json}

## This turn
User: {user_message}
Assistant (first part): {assistant_preview}
```

## 5. Injection block

Rendered by `core/user_memory.py::render_profile_block(rows)`. Empty profile
-> no block, no second system message, so requests from users without a
profile are byte-identical to today.

```text
## ABOUT THE ADVOCATE USING THIS ASSISTANT (saved profile)
These are the advocate's OWN details and standing preferences. Use them
to fill the advocate's name, enrolment number, firm, address, phone and
email wherever a document needs them (signature block, "From" block,
Vakalatnama, notice letterhead) instead of bracketed placeholders. They
are NOT facts of the matter: never present the advocate as a party,
witness or deponent. If the current request gives a different value for
any of these, the request wins.

Advocate: Adv. Priya S. Kulkarni (Enrolment MAH/1234/2015)
Firm: Kulkarni & Associates
Office: 201, Lex Towers, Fergusson College Rd, Pune 411004
Phone: +91 98xxxxxx12 | Email: priya@kulkarnilaw.in
Regular courts: Bombay High Court; District Court, Pune
Preferred draft language: Marathi
Drafting preferences:
- number prayer clauses (a), (b), (c)
- prefers "Hon'ble Court" over "the Court"
```

Size: about 150-250 tokens for a full profile; hard cap 500 tokens (rows
dropped from the bottom, `note` first).

Placement, per the caching rule:

```python
# Anthropic: cached static block first, then an UNCACHED block.
[SystemMessage(content=[
     {"type": "text", "text": static_prompt, "cache_control": {"type": "ephemeral"}},
     {"type": "text", "text": profile_block},
 ]),
 HumanMessage(content=user_block)]

# Gemini / OpenAI: string system prompt; profile appended AFTER the static
# text so the implicit-cache prefix is unchanged.
[SystemMessage(content=static_prompt + "\n\n" + profile_block),
 HumanMessage(content=user_block)]
```

Implemented as `cacheable_system(text, trailer=profile_block)` in
`core/clients.py`, so the three drafting call sites change by one argument.
The `thread_id`, `source`, `confidence` and `note` rows are never injected.

## 6. Explicit "remember that" / "forget that"

Detected deterministically in `memory_node` before the rewriter, only when
the **whole** message is the directive (so a long drafting request that
contains "remember that I ..." mid-sentence still goes to the normal
pipeline, and the extractor picks the fact up with `user_stated`).

Patterns (case-insensitive, leading/trailing punctuation ignored):

```text
^(please )?(remember|note|save) (that )?(.+)$
^(please )?(forget|delete|remove) (that )?(.+)$
^(याद रखो|याद रखें|लक्षात ठेव|लक्षात ठेवा) (कि )?(.+)$
^(भूल जाओ|भूल जाइए|विसरा|विसरून जा) (कि )?(.+)$
```

- "remember": the remainder goes through the same extractor with the
  instruction "the user explicitly asked you to remember this; every
  durable lawyer fact here is user_stated". Hard exclusions still apply.
  If the extractor returns nothing storable (for example "remember that
  the hearing is on 3 Nov"), the reply says so and explains what memory
  does and does not keep.
- "forget": the remainder is matched against the profile by the extractor
  (DELETE ops only). "Forget everything" / "सब भूल जाओ" deletes the whole
  profile.
- The graph is not run. The reply is a short confirmation listing exactly
  what was saved or removed, with the stored wording, so the user can
  correct it. Example:

```text
Saved to your profile:
- Advocate name: Adv. Priya S. Kulkarni
- Enrolment number: MAH/1234/2015
You can see or change this any time from your profile page.
```

## 7. Review API

All routes read `user_id` from the `X-User-Id` header (see section 8 on
trusting it).

| Route | Returns |
|---|---|
| `GET /pyapi/memory` | `{"enabled": true, "rows": [{id, slot, label, value, source, updated_at}]}` |
| `PATCH /pyapi/memory/{id}` body `{"value": "..."}` | the updated row; sets `source=user_stated` |
| `DELETE /pyapi/memory/{id}` | 204 |
| `DELETE /pyapi/memory` | 204, deletes the whole profile |
| `POST /pyapi/memory` body `{"slot": "...", "value": "..."}` | the created row, `source=user_stated` |

`label` is the display name per slot ("Advocate name", "Office address").
The frontend page is the frontend team's work; this is the contract.

## 8. Flags, cost, guardrails

- `USER_MEMORY_ENABLED` (default `0`). Off: no table reads, no extraction,
  no injection, review API returns 404. On with no `user_id`: same as off
  for that request.
- `USER_MEMORY_EXTRACT_MODEL` (default Flash Lite). Each extraction is one
  call of roughly 1,500 input / 100 output tokens; about USD 0.0002 per
  turn, and the first-person pre-filter skips most turns.
- Extraction never blocks the response. A failure is logged and dropped.
- `no_memory` on a thread also stops the "remember that" shortcut, and
  the reply says memory is off for this chat.
- Confidentiality: the extractor's hard exclusions plus the regex filter
  in section 3 are both in place because the model alone is not enough.
  Every stored row keeps `thread_id` so a bad row can be traced to the
  turn that produced it.
- Trust in `user_id`: the frontend calls the Python API directly, so a
  bare `X-User-Id` header is spoofable and would let one user read
  another's profile. Recommended: Node signs the id (HMAC with a shared
  secret in `USER_MEMORY_ID_SECRET`, sent as `X-User-Id: <id>.<sig>`),
  Python verifies. If Node cannot do that this sprint, phase 1 accepts
  the bare id, with the risk noted here.
- Shared logins: a firm account used by several advocates will build one
  mixed profile. The review page is the remedy; nothing automatic.

## 9. Open decisions (need an answer before build)

1. Who sends `user_id`, and in which form: form field `user_id` next to
   `plan`, or `X-User-Id` header. Both are supported; one must be chosen
   for the frontend brief.
2. Signed id (section 8) in phase 1, or bare id now and signing later.
3. The slot list in section 2: anything to add or remove.

## 10. Build plan once approved

| Day | Work |
|---|---|
| 1 | `user_memory` table + `no_memory` column in both stores; `core/user_memory.py` (render, validate, apply ops, hard-exclusion filter); unit tests |
| 2 | Extraction call + post-`save_turn` hook; "remember/forget" shortcut; tests with the prompt examples above as fixtures |
| 3 | Injection via `cacheable_system(trailer=)`; drafting consumer check (signature block filled, no `[Advocate Name]` left when the profile has a name); review API; eval of 10 drafts with and without a profile to confirm no regression elsewhere in the draft |
| 4 | Frontend brief, dev deploy with flag on for internal users only |
