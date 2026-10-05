# Draft side panel: frontend contract

**Status:** proposal v1, for review by the frontend team. The block format is
implemented in `core/draft_blocks.py`. The events and the edit endpoint below
are **not built yet**; they are specified here so the panel can be built in
parallel against the sample data.

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
| `artifact_start` | Drafting begins and the section plan is known | `artifact_id`, `version`, `title`, `doc_type`, `language`, `page`, `sections: [{id, heading}]` (no blocks yet) |
| `artifact_section` | Each section finishes | `artifact_id`, `version`, `section: {id, heading, blocks}`, `status: "preview"` |
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
data: {"type":"artifact_start","artifact_id":"art_7f3c2a9e","version":1,"title":"Bail Application under Section 483 BNSS", "sections":[{"id":"s1","heading":"Cause title"}, ...]}
data: {"type":"drafting_progress", ...}
data: {"type":"artifact_section","artifact_id":"art_7f3c2a9e","version":1,"status":"preview","section":{"id":"s2", ...}}
data: {"type":"artifact_section", ... "section":{"id":"s1", ...}}
...
data: {"type":"artifact_done","artifact_id":"art_7f3c2a9e","version":1,"artifact":{ ... }}
data: {"type":"response","content":"<markdown version>"}
data: {"type":"done", ...}
```

## 6. Editing

### a) Follow-up in the chat

The user types "add a ground on parity" in the chat. Nothing new is needed
from the client: the backend recognises a change to the open draft and
replies with `artifact_patch` (or a full `artifact_done` with the next
version if the change touches most of the draft).

### b) Edit a selection in the panel

The user selects text or clicks a section and types an instruction.

```
POST /pyapi/artifact/edit          (SSE response, same auth as /search/stream)
{
  "globalThreadId": "…",
  "artifact_id": "art_7f3c2a9e",
  "base_version": 3,
  "target": {"section_id": "s3", "block_ids": ["s3.b2"], "selected_text": "BECAUSE the dispute is purely civil"},
  "instruction": "make this ground stronger and cite the relevant Supreme Court position"
}
```

- `section_id` is required; `block_ids` and `selected_text` narrow the
  target and are optional.
- Response stream: `artifact_patch` (the changed sections, complete), then
  `done`. Only the targeted section changes.
- If `base_version` is not the latest, the server replies `409` with the
  latest version number; refetch and let the user retry.

## 7. Versions and loading

```
GET /pyapi/artifact/{artifact_id}                 → latest artifact
GET /pyapi/artifact/{artifact_id}?version=2       → that version
GET /pyapi/artifact/{artifact_id}/versions        → [{version, created_at, change_summary}]
```

Use these to restore a thread's panel on reload, and for a version dropdown.
Restoring an old version is a new version (`version` = latest + 1) with the
old content, so history is never lost.

## 8. Download

**Available now** — send the artifact itself:

```
POST /pyapi/export   {"artifact": { ...the artifact from artifact_done... }, "format": "docx"}
```

**Planned** (once versions are stored server-side):

```
POST /pyapi/export   {"artifact_id": "art_7f3c2a9e", "version": 3, "format": "docx" | "pdf"}
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

## 10. Open questions for the frontend team

1. Framework and existing SSE handling: anything in the current event
   handling that would make new event types a problem?
2. Is a 409-and-refetch acceptable for stale edits, or do you want the
   server to merge?
3. Should the panel be editable by hand (typing directly in the page), or
   only through instructions to the assistant in v1? Hand-editing needs a
   save endpoint and is not in this spec yet.
