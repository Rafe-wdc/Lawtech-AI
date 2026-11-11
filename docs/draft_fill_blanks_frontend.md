# Fill the blanks: frontend handoff

**For:** the web and mobile app teams building the draft side panel.
**Adds to:** `docs/draft_panel_frontend_contract.md`. Nothing in that contract
changes: the same events, endpoints and block format, and the same yellow
`[[blanks]]` in the page.

**No new endpoints.** The form uses the two the panel already calls:

| Call | Without the new option | With it |
|---|---|---|
| `GET /pyapi/artifact/{id}` | the draft, exactly as today | `?include=fields` also returns the form fields |
| `PUT /pyapi/artifact/{id}` | saves typed `sections`, exactly as today | `values` fills blanks from the form |

## 1. What the user gets

A form beside the draft with one input per blank. The advocate types a value
once and every place that blank appears in the draft is filled. "District:
Pune" fills the cause title, the address and "Place:" together.

- Blanks the writer spelled differently are one field. `[DISTRICT]` in the
  cause title and `[District]` in an address both belong to the field
  **District**.
- Each fill is saved as a new version of the draft, so "Undo" is a restore of
  the previous version.
- Filled text is ordinary text. It is no longer highlighted, it can be typed
  over in the page, and the Word download prints it normally.

## 2. Layout (desktop)

The draft panel needs more room once the form sits beside the page.

**Default when a draft is open:** chat 30%, draft panel 70%. Inside the
panel, the form is a fixed column of about 300 px on the left of the page.

```
┌──────────── chat 30% ────────────┬─────────────────── draft panel 70% ────────────────────┐
│                                  │ Title · v3 ▾ · Word · [<]                              │
│  messages                        ├─────────────────┬──────────────────────────────────────┤
│                                  │ Fill blanks  ‹  │                                      │
│  Draft ready · View draft        │ 9 fields · 12   │        A4 page                       │
│                                  │ blanks left     │                                      │
│                                  │                 │   IN THE COURT OF SESSIONS AT        │
│                                  │ District  (3)   │   [District]                         │
│                                  │ [ Pune      ]   │                                      │
│                                  │ Father's Name   │                                      │
│                                  │ [           ]   │                                      │
│  [ Ask a legal question… ]       │ [ Apply ]       │                                      │
└──────────────────────────────────┴─────────────────┴──────────────────────────────────────┘
```

**`[<]` expands the panel to the full width** and hides the chat. The button
then shows `[>]`, which returns to 30 / 70.

```
┌────────────────────────────── draft panel 100% ──────────────────────────────┐
│ Title · v3 ▾ · Word · [>]                                                    │
├─────────────────┬────────────────────────────────────────────────────────────┤
│ Fill blanks  ‹  │                    A4 page                                 │
└─────────────────┴────────────────────────────────────────────────────────────┘
```

**`‹` on the form collapses it** to a narrow strip or a "Fill blanks (9)" pill,
which gives the page the room back. Advocates who type straight into the page
don't need the form open.

Guidelines:

- Don't let the page go below about 640 px wide. On a small laptop screen,
  collapse the chat (as if `[<]` were pressed) or the form, instead of
  squeezing the page.
- Scale the page to fit (zoom), don't reflow it, so it still looks like A4.
  The text, numbering and Word download are the same at any width.
- Remember the user's choice (expanded, form collapsed) on the device.
- Hide the form when the draft has no blanks (`blanks` is 0), and show a small
  "All blanks filled" state.
- 70 / 30 is a better default than 80 / 20. At 20%, the chat on a 1366 px
  laptop is under 280 px wide, too narrow to read. Use `[<]` when the user
  wants the full width.

**Mobile:** the draft is full screen. "Fill blanks (9)" opens a bottom sheet
or a tab with the same list of inputs.

## 3. Loading the fields

```
GET /pyapi/artifact/{artifact_id}?include=fields
GET /pyapi/artifact/{artifact_id}?version=2&include=fields     (an older version, read only)

→ 200
{
  "artifact_id": "art_7f3c2a9e",
  "version": 3,
  ...the draft, exactly as without include (title, page, sections)...,
  "blanks": 12,                       every place still empty, all fields together
  "fields": [
    {
      "key": "district",
      "label": "District",
      "kind": "text",
      "count": 3,
      "places": [
        {"section_id": "s1", "block_id": "s1.b1", "context": "IN THE COURT OF SESSIONS AT [District]"},
        {"section_id": "s2", "block_id": "s2.b2", "context": "…by the police of [Police Station], District [DISTRICT], on [Date of Arrest]."},
        {"section_id": "s3", "block_id": "s3.b3", "context": "Place: [District]"}
      ]
    },
    {
      "key": "date of arrest",
      "label": "Date of Arrest",
      "kind": "date",
      "count": 1,
      "places": [
        {"section_id": "s2", "block_id": "s2.b2", "context": "…of [Police Station], District [DISTRICT], on [Date of Arrest]."}
      ]
    }
  ]
}
→ 404 {"error": "not_found"}         no stored draft with this id
```

So one request gives the page and the form. Without `?include=fields`, the
response is exactly what it is today.

`fields` and `blanks` are not part of the draft. For Word, export by
`artifact_id` (the usual way). If you ever send the draft object itself to
`POST /pyapi/export`, leave these two keys out: the export rejects keys it
doesn't know.

| Field | Meaning |
|---|---|
| `key` | What you send back when filling. Treat it as an opaque string |
| `label` | The input's label. Show as is; some are in capitals, such as `BAIL APPLICATION NO.` |
| `kind` | An input hint: `date`, `number` (age, year), `amount` (₹), `address` (multi-line box) or `text`. The value is always sent as text |
| `count` | How many places this value fills. Show it, e.g. "District (3)" |
| `places` | Where the blank is, in document order. `block_id` is the block to highlight or scroll to. `context` is the words around it, for a hint under the input |

Fields come in the order their blanks first appear in the draft, from the
cause title to the verification.

**When to reload:** whenever the draft changes, with
`GET /pyapi/artifact/{id}?include=fields`. That means after `artifact_done`,
`artifact_patch`, a typed save, or a restore. A fill already returns the new
`fields`, so it doesn't need a second request.

## 4. Filling

```
PUT /pyapi/artifact/{artifact_id}          (the same save the panel uses for typed text)
{
  "base_version": 3,
  "values": {"district": "Pune", "fathers name": "Ramesh Kumar"}
}

→ 200
{
  "artifact_id": "art_7f3c2a9e",
  "version": 4,
  "sections": [ ...the changed sections, complete... ],
  "filled": ["district", "fathers name"],
  "unknown": [],                      keys that match no blank (the draft changed); ignore or refresh
  "fields": [ ...the fields still to fill... ]
}
→ 200 {"version": 3, "sections": [], "unchanged": true, ...}   nothing matched
→ 409 {"error": "stale_version", "latest_version": 5}          reload the draft and the fields, then retry
→ 422                                                          neither "sections" nor "values", or empty "values"
→ 404                                                          no stored draft
```

- `values` and typed `sections` can go in the same request. Both are saved as
  one version.
- A save with only `sections` is unchanged and answers exactly as today, with
  no `filled`, `unknown` or `fields`.

- Send several fields in one request, e.g. on "Apply" or after a pause of
  about 1 to 2 seconds. Empty values are skipped, so you can send the whole
  form.
- Re-render the returned `sections`; they replace those sections in the page.
  Then use the returned `version` as the next `base_version`.
- Optionally, show the value in the page while the user types, then replace it
  with the server's sections. The server's version is the one that counts.
- The version history shows "Filled blanks: District, Father's Name". Undo is
  `POST /pyapi/artifact/{id}/restore` with the previous version.

**What the server does with a value** (don't repeat this in the app):

- **Case.** The value takes the case of the words next to its blank:
  - In a capitals line it is filled in capitals: "IN THE COURT OF SESSIONS AT PUNE", "STATE OF MAHARASHTRA".
  - Inside a sentence it stays as typed: "District Pune", "son of Ramesh Kumar".
  - A blank alone on its line follows its label: `[NAME OF APPLICANT]` gives "RAHUL SHARMA".
- **One line.** Line breaks become ", ", so an address typed on three lines
  becomes one line.
- **Length.** At most 500 characters.
- **No new blanks.** `[[` and `]]` are removed, so a value can't create a blank.
- **Matching keys.** A label also works as a key. "Father's Name" and
  "fathers name" both reach the same field.

## 5. Interactions worth building

1. **Focus a field:** highlight all of its blanks in the page (`places[].block_id`)
   and scroll to the first. Show "1 of 3" if the user steps through them.
2. **Click a yellow blank in the page:** open the form and focus its field.
   Find the field by matching the blank's label with the same rule the server
   uses:
   ```js
   const fieldKey = l => l.normalize("NFKC").replace(/[‘’']/g, "")
     .replace(/\s+/g, " ").replace(/^[ .:;,-]+|[ .:;,-]+$/g, "").toLowerCase();
   ```
3. **Progress and next:** "9 fields · 12 blanks left" (`fields.length`,
   `blanks`) and a "Next empty field" button.
4. **Inputs by `kind`:**
   - `date`: a date picker that writes the date as text, e.g. "1 May 2026".
   - `number`: a number pad.
   - `amount`: a ₹ prefix.
   - `address`: a two- or three-line box.
5. **Download warning:** if `blanks` > 0 when the user clicks Word, ask
   "12 blanks are still empty. Download anyway?". Unfilled blanks print
   highlighted.
6. **A blank that means two things:** if one field covers places that need
   different values (two different dates both written `[Date]`), the user types
   the value at that place in the page instead (the existing `PUT` save). The
   form then shows only the places still empty.

## 6. Status and testing

| Part | State |
|---|---|
| `GET /pyapi/artifact/{id}?include=fields` | built, in PR against dev |
| `PUT /pyapi/artifact/{id}` with `values` | built, in PR against dev |
| Layout, form, page highlights | frontend |

Once deployed on dev (`https://tool.lawttorney.com/pyapi`, same `X-API-Key`):

```
curl -H "X-API-Key: $KEY" "https://tool.lawttorney.com/pyapi/artifact/<artifact_id>?include=fields"
curl -X PUT -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"base_version": 1, "values": {"district": "Pune"}}' \
  https://tool.lawttorney.com/pyapi/artifact/<artifact_id>
```

Use a draft made on dev; its `artifact_id` arrives in `artifact_done`. Drafts
made before 6 Nov 2026 were never stored and return 404.

## 7. Later (needs per-user login)

- **Saved clients:** keep the values for a client (name, father's name, age,
  address) and offer them in the next draft for that client, like the
  "+ Add a client" in other drafting tools.
- **Prefill from case papers:** fill the blanks that an uploaded FIR or
  agreement already answers, and show them for the advocate to confirm.
