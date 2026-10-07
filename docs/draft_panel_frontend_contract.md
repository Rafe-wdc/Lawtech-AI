# Draft side panel: frontend contract

**Status:** proposal v1, for review by the frontend team.

| Part | State |
|---|---|
| Block format (`core/draft_blocks.py`) | built |
| Word download from blocks (`POST /pyapi/export` with `artifact`) | built |
| `artifact_done` event at the end of every draft | built, off unless the server sets `DRAFT_ARTIFACT_EVENTS=1` |
| `artifact_start` / `artifact_section` (live while writing) | built, same flag. Sent for drafts written section by section; a short draft written in one pass (a notice) sends only `artifact_done` |
| Chat follow-up edits part of the draft (`artifact_patch`) | built, same flag |
| `POST /pyapi/artifact/edit` (edit a selected section) | built |
| `PUT /pyapi/artifact/{id}` (save typed text), versions, restore, export by id | built |

The panel can be built against the sample data now, and against real drafts
on the dev server once `DRAFT_ARTIFACT_EVENTS=1` is set there. With the flag off the
server stores no drafts, so the `/pyapi/artifact/...` endpoints return 404.

**Files**

| File | What it is |
|---|---|
| `docs/draft_panel_sample.json` | A complete sample draft in the block format |
| `docs/draft_panel_preview.html` | Reference renderer. Open it in a browser; it is the visual spec. "Load JSON" renders any artifact |
| `core/draft_blocks.py` | The schema (source of truth if this doc and the code ever disagree) |

---

## 1. What changes for the user

When the answer is a draft, the app opens a panel beside the chat and shows
the draft as an A4 page, filling in section by section while it is written.
The chat shows one short line ("Draft ready: Bail Application, 5 sections").
Later requests ("make the grounds stronger", or editing a selected section)
update the draft in the panel; each change is a new version.

Answers that are not drafts are unchanged.

## 2. Why blocks and not Markdown

Markdown has no centring, no right-aligned text and no hanging paragraph
numbers, so a court draft written as Markdown cannot keep its layout. A draft
is now a list of **sections**, each a list of **blocks** that carry their own
alignment. The panel, the Word download and the chat text are all produced
from the same blocks.

## 3. The artifact

```json
{
  "schema_version": 1,
  "artifact_id": "art_7f3c2a9e",
  "version": 1,
  "title": "Bail Application under Section 483 BNSS",
  "doc_type": "bail_application_regular",
  "language": "en",
  "page": {"size": "A4", "margins_cm": {"top": 2.5, "bottom": 2.5, "left": 4.0, "right": 2.5},
           "font": "Times New Roman", "font_size_pt": 14, "line_spacing": 1.5},
  "sections": [
    {"id": "s1", "heading": "Cause title", "blocks": [ ... ]}
  ]
}
```

- `artifact_id` is stable for the life of the draft in a thread; `version`
  increases by 1 on every change.
- `section.heading` is a label for the panel outline, not printed text.
  Printed headings are `heading` blocks.
- `page` values are defaults; render them, do not hard-code.
- `language`: the draft may be in Hindi, Marathi, etc. Use a font with
  Devanagari / regional script coverage when `language` is not `en`.

## 4. Block types

Every block has `id` (unique in the artifact, e.g. `s3.b2`) and `type`.

| `type` | Fields | How to render |
|---|---|---|
| `title` | `text`, `align`, `bold`, `underline` | One line, usually centred and bold. Court name, document title, "Versus" |
| `heading` | `text`, `level` (1 or 2), `align`, `underline` | Section heading in the body (FACTS, GROUNDS, PRAYER), extra space above |
| `text` | `text`, `align`, `indent_cm` | Plain, unnumbered paragraph |
| `row` | `left` (lines), `right` (lines) | Two columns: left stack, right stack **right-aligned and bottom-aligned**. Used for parties ("Name, Age, Address" … "...Applicant") and place/date against the advocate's name |
| `para` | `number` or `label`, `text`, `sub` | Numbered paragraph with a **hanging number**: number in a gutter, wrapped lines align with the text, not under the number. `label` (e.g. "A.") replaces the number for grounds. `sub` items (`marker`, `text`) nest under the paragraph with their own hanging marker |
| `signature` | `lines`, `align` | Signature block, usually right-aligned, space above |
| `table` | `header`, `rows` | Bordered table, cells top-aligned |
| `page_break` | none | New page in print and Word; a dashed rule on screen |

Unknown `type`: skip it and do not fail, so new block types can be added
without breaking older app versions.

**Paragraph numbers come from the backend.** Display `number` exactly as
sent. Do not number paragraphs in the renderer (no `<ol>`), so a number can
never be blank, skipped or repeated.

### Inline text

Inside any `text` / `lines` / `left` / `right` / cell string, only these are
interpreted:

| Syntax | Meaning |
|---|---|
| `**x**` | bold |
| `*x*` | italic |
| `__x__` | underline |
| `[[x]]` | a blank the advocate must fill. Show it highlighted, e.g. yellow background, as `[x]` |
| `\n` | line break inside the block |

Everything else is literal text. Escape HTML; never render the string as HTML.

## 5. Events (SSE, on the existing chat stream)

Sent on the same stream as today, alongside the existing events. Clients
without the panel ignore them and keep using `token` / `response`.

| Event | When | Payload |
|---|---|---|
| `artifact_start` | Drafting begins and the section plan is known | `artifact_id`, `version`, `sections: [{id, heading}]` (no blocks yet). Ids are `p1`, `p3`, … : the writer drafts two planned sections at a time, and each preview section is one such pair |
| `artifact_section` | Each pair finishes | `artifact_id`, `status: "preview"`, `section: {id, heading, blocks}` with an id from `artifact_start` |
| `artifact_done` | The draft is final (after clean-up and review) | `artifact_id`, `version`, `artifact` (complete, authoritative) |
| `artifact_patch` | An edit changed part of an existing draft | `artifact_id`, `base_version`, `version`, `sections: [ ... ]` (only the changed sections, complete) |

Rules:

1. **`artifact_done` replaces everything.** Sections from `artifact_section`
   are a live preview and may be edited by the review pass before the end.
   When `artifact_done` arrives, re-render from its `artifact`.
2. Sections may finish out of order (they are written in parallel). Place
   each by its `id` using the order from `artifact_start`.
3. A section that failed arrives in the existing `draft_incomplete` event
   (`failed_sections`); show it as "could not be generated" in its slot.
4. `artifact_patch` with a `base_version` different from the version on
   screen means the client is stale: fetch the artifact again (section 7).
5. The existing `response` event still carries a Markdown version of the
   draft, so older clients and the chat transcript keep working.

Example stream for a new draft:

```
data: {"type":"thread_id", ...}
data: {"type":"agents_planned","agents":["Drafting"]}
data: {"type":"artifact_start","artifact_id":"art_7f3c2a9e","version":1,"sections":[{"id":"p1","heading":"CAUSE TITLE / STATEMENT OF FACTS"},{"id":"p3","heading":"GROUNDS / PRAYER"},{"id":"p5","heading":"VERIFICATION"}]}
data: {"type":"drafting_progress", ...}
data: {"type":"artifact_section","artifact_id":"art_7f3c2a9e","status":"preview","section":{"id":"p1","heading":"CAUSE TITLE / STATEMENT OF FACTS","blocks":[ ... ]}}
data: {"type":"artifact_section", ... "section":{"id":"p3", ...}}
...
data: {"type":"artifact_done","artifact_id":"art_7f3c2a9e","version":1,"artifact":{ ... }}
data: {"type":"response","content":"<markdown version>"}
data: {"type":"done", ...}
```

## 6. Editing

Only the part that changes is rewritten. The rest of the draft stays exactly
as it was, and every change is a new version.

### a) Follow-up typed in the chat

The user types "add a ground on parity" in the chat. Send it as a normal
chat message; nothing new is needed from the client. The server decides:

| The message is… | What the server does | What the client receives |
|---|---|---|
| A change to part of the draft ("make ground B stronger", "the age is 36, not 34") | Rewrites only the sections it touches (usually one, at most four) | `artifact_patch`, then `response` (the whole updated draft as Markdown, for the transcript), then `done` with `"partial_edit": true` |
| A request to redo the draft, translate it, or change its type ("redraft it", "in Marathi", "make it a plaint") | Runs the full drafting pipeline | `artifact_done` with the next `version`, then `response`, `done` |
| A request for case law or citations ("add relevant case laws") | Runs the full pipeline, because only it can search the judgment database. A partial edit never adds a citation the user did not type | as above |
| A question or anything else | Normal answer; the draft is not changed | the usual events, no artifact event |

`artifact_patch`:

```json
{"type": "artifact_patch", "artifact_id": "art_7f3c2a9e", "base_version": 3, "version": 4,
 "summary": "Updated Grounds For Grant Of Regular Bail",
 "edited_section_ids": ["s3"],
 "sections": [ {"id": "s3", "heading": "...", "blocks": [ ... ]}, {"id": "s4", ...} ]}
```

- `sections` holds every section that differs from `base_version`, complete.
  Replace those sections in the panel by `id`.
- `edited_section_ids` are the ones whose wording changed. Other sections in
  the patch changed only because paragraph numbers moved (a paragraph was
  added before them). Highlight only the edited ones.
- Show `summary` in the chat instead of the full draft.

### b) Edit a selection in the panel

The user selects text or clicks a section and types an instruction.

```
POST /pyapi/artifact/edit          (SSE response, same auth as /search/stream)
{
  "artifact_id": "art_7f3c2a9e",
  "base_version": 3,
  "instruction": "make this ground stronger",
  "target": {"section_id": "s3", "selected_text": "BECAUSE the dispute is purely civil"}
}
```

- With `target.section_id`, only that section is rewritten. `selected_text`
  narrows the change to that passage and is optional.
- Without `target`, the server works out which sections the instruction
  touches.
- Events: `status` (progress text), then `artifact_patch` and `done`.
- `artifact_edit_declined` (`scope`: `"whole"` or `"none"`, `reason`) means the
  instruction needs the whole draft redone, needs a case-law search, or is not
  an edit. Send it as a normal chat message instead.
- `409` with `latest_version` if `base_version` is not the latest: load the
  latest and let the user retry. `404` unknown draft, `400` unknown section.
- An `error` event with `"error": "edit_failed"` means nothing was changed.

## 7. Versions and loading

```
GET  /pyapi/artifact/{artifact_id}                 → the latest artifact
GET  /pyapi/artifact/{artifact_id}?version=2       → that version
GET  /pyapi/artifact/{artifact_id}/versions        → {"versions": [{version, source, change_summary, created_at}]}
POST /pyapi/artifact/{artifact_id}/restore   {"version": 2}   → the restored artifact, as a NEW version
```

- `artifact_id` is one per conversation: `"art_" +` the first 12 hex
  characters of the thread id. A second draft in the same conversation is
  the next version of the same artifact.
- `source` is `draft` (generated), `assistant` (a partial edit), `user`
  (typed in the panel) or `restore`.
- Use the first call to restore a thread's panel on reload, and the list for
  a version dropdown. Restoring never deletes history.

## 8. Download

```
POST /pyapi/export   {"artifact_id": "art_7f3c2a9e", "format": "docx"}             → latest version
POST /pyapi/export   {"artifact_id": "art_7f3c2a9e", "version": 3, "format": "docx"}
POST /pyapi/export   {"artifact": { ...an artifact... }, "format": "docx"}          → without storing it
```

The Word file is built from the same blocks: real centring, right-aligned
party labels on a right tab stop, hanging paragraph numbers (typed, never a
Word auto-list, so they do not renumber when the advocate edits), highlighted
blanks and the page margins above. `"format": "pdf"` currently goes through
the Markdown renderer and loses the alignment; a block-based PDF is a later
step. An invalid artifact returns `400`. The existing `raw_text` /
`thread_id` export keeps working unchanged.

## 9. Panel behaviour (suggested)

- Open the panel on `artifact_start`; show the section outline with
  "writing…" placeholders and fill them as sections arrive.
- Keep the panel open for the thread; reopen it from a "View draft" chip on
  the chat message.
- Highlight `[[blanks]]`; a count of blanks in the panel header helps the
  advocate finish the draft ("6 blanks to fill").
- Mobile: panel becomes a full-screen view toggled from the chat.

## 10. Typing directly in the panel

The advocate can click into the page and type (fix a name, a date, a word),
as in ChatGPT Canvas. Asking the assistant remains the way to make larger
changes.

- Each block is an editable region (a rich-text editor such as TipTap or
  ProseMirror works well). Only the inline marks in section 4 are allowed.
- **Locked while typing:** paragraph numbers and labels (they stay
  backend-assigned), block type and alignment. Enter at the end of a `para`
  creates a new `para` after it with no `number`; the number appears after
  save.
- Save after a short pause, or on an explicit Save:

```
PUT /pyapi/artifact/{artifact_id}
{"base_version": 3, "sections": [ ...the changed sections, complete... ]}

→ 200 {"artifact_id": "...", "version": 4, "sections": [ ...as saved... ]}
→ 200 {"version": 3, "sections": [], "unchanged": true}     nothing differed
→ 409 {"error": "stale_version", "latest_version": 5}
→ 400 unknown section id, or a block that does not fit the format
```

- New blocks may be sent without an `id`; the server assigns them.
- The response is authoritative: re-render the returned `sections`. It can
  include sections you did not send, when their paragraph numbers moved.
- Numbering rule: paragraphs are numbered in order through the document. A
  paragraph numbered `1` that is the first numbered paragraph of its section
  starts a new sequence (an affidavit, a list of documents). Lettered
  paragraphs (`label`) are not numbered.
- The assistant always works from the latest saved version, so a later
  instruction never undoes what the advocate typed.

## 11. Roadmap after v1

| Phase | Features |
|---|---|
| 1 (this spec) | Panel, live sections, Word download, chat follow-ups update the draft, select-to-edit, typing in the panel, versions |
| 2 | Blanks counter and one form to fill all blanks; citation marks (found in our database with PDF / not verified); highlighted changes with accept / reject |
| 3 | Legal shortcut buttons (IPC to BNS, add interim prayer, translate, more formal); court format presets (margins, font per court) |
| 4 | Share by link; comments in the margin; automatic update of "as stated in para N" references; annexure list; index and list of dates |

## 12. Open questions for the frontend team

1. Framework and existing SSE handling: anything in the current event
   handling that would make new event types a problem?
2. Is a 409-and-refetch acceptable for stale edits, or do you want the
   server to merge?
