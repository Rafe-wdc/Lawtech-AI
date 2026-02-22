# Lawttorney AI — Feature Roadmap

**Created**: 2026-02-22
**Goal**: Elevate the conversational experience to match ChatGPT, Claude, and Gemini — tailored for Indian legal AI.

---

## Tier 1 — Quick Wins (Frontend-only, no backend changes)

### 1. Suggested Follow-up Questions

After each response, show 3–4 clickable chips that guide the user's next step.

**Examples (after showing Section 35 BNS):**
- "Find Supreme Court cases on this"
- "What is the punishment under this section?"
- "Compare with the old IPC equivalent"
- "Draft a legal notice based on this"

**Implementation Options:**
- **Option A (cheap):** Append a system instruction to the synthesis prompt: *"End your response with 3 suggested follow-up questions in a JSON array."* Parse and render as chips.
- **Option B (async):** After the main response streams, fire a separate gemini-flash-lite call to generate suggestions based on the query + response. Non-blocking, appears after response finishes.

**Why it matters:** Drives engagement, reduces the blank-page problem for users who don't know what to ask next. ChatGPT's most impactful UX feature.

---

### 2. Response Feedback (Thumbs Up / Thumbs Down)

Add a feedback mechanism on every AI response to track quality over time.

**UI:**
- Two small icons below each response: 👍 👎
- On thumbs down: expand an optional text field — "What went wrong?"
- On thumbs up: optionally tag what was good (accurate, detailed, well-cited)

**Backend:**
- New SQLite table: `feedback(id, thread_id, turn_number, rating, comment, created_at)`
- New endpoint: `POST /pyapi/feedback`
- Dashboard query: aggregate ratings by category, agent, time period

**Why it matters:** Without feedback data, you're blind to quality regressions. This is the foundation for systematic improvement.

---

### 3. Regenerate Response Button

Let users retry if the response was weak — same query, fresh agent run.

**UI:**
- "🔄 Regenerate" button below each AI response
- On click: re-send the same `Promptquery` + `globalThreadId` to `/pyapi/search/stream`
- Replace the current response with the new one (or show side-by-side)

**Implementation:** Pure frontend — no backend changes needed. The existing endpoint handles it.

**Why it matters:** Users expect control. A bad response shouldn't end the conversation — it should be retryable.

---

### 4. Copy / Export Response

Let users take responses out of the chat for real-world use.

**UI:**
- "📋 Copy" button — copies response as markdown to clipboard
- "📄 Export PDF" button — generates a downloadable PDF
- Especially valuable for drafting responses (bail applications, legal notices, rental agreements)

**Implementation:**
- Copy: `navigator.clipboard.writeText(responseMarkdown)`
- PDF: Use browser's `window.print()` with a print-specific CSS stylesheet, or a lightweight library like `html2pdf.js`

**Why it matters:** Lawyers need to use these outputs in real documents. Making export frictionless increases the tool's practical value.

---

### 5. Agent Status Indicators (Enhanced Streaming UX)

Transform the existing SSE agent steps into a visual progress indicator that builds trust during 30–60 second waits.

**Current state:** You already stream `status` events with agent names.

**Enhanced UI:**
```
✓ Safety check passed
✓ Query rewritten: "SC cases on Section 35 BNS"
⟳ Searching 44,000+ Supreme Court judgments...
⟳ Reading case details: Salil Mahajan vs Avinash Kumar...
✓ Found 5 relevant cases
⟳ Synthesizing response from 2 agents...
```

**Implementation:**
- Map agent node names to user-friendly descriptions:
  - `guardrail_input` → "Safety check passed ✓"
  - `memory` → "Query analyzed" (+ show rewritten query if changed)
  - `orchestrator_plan` → "Planning research strategy..."
  - `newacts` → "Searching legislation database..."
  - `sci_judgment` → "Searching 44,000+ Supreme Court judgments..."
  - `orchestrator_synthesize` → "Synthesizing final response..."
  - `guardrail_output` → "Quality check passed ✓"
- Show as a collapsible progress timeline above the streaming response

**Why it matters:** Perceived wait time drops dramatically when users see meaningful progress. This is especially important for legal queries that take 30–60 seconds.

---

## Tier 2 — High Impact (Small backend changes)

### 6. Conversation History Sidebar

A left sidebar listing past conversations, similar to ChatGPT's sidebar.

**UI:**
- Left panel (collapsible) showing conversation list
- Each entry: auto-generated title + date + turn count
- Click to load and continue a past conversation
- "New Chat" button at the top
- Search/filter conversations

**Backend Changes:**
- New endpoint: `GET /pyapi/threads` — returns list of threads with metadata
- New endpoint: `GET /pyapi/threads/{thread_id}/messages` — returns full conversation
- Auto-generate thread title from the first query (e.g., "Section 35 BNS — Private Defence")
- Store title in the existing `threads` SQLite table (add a `title` column)

**Why it matters:** Without conversation history, every session feels ephemeral. Users can't resume research across sessions. This is table-stakes UX for any chat application.

---

### 7. Smart Greeting + Onboarding

First-time and returning users see a contextual welcome screen instead of a blank chat.

**First-time user:**
```
Welcome to Lawttorney AI!

I'm your legal research assistant. I can help you with:

📜  Look up any Indian law, section, or act
⚖️  Find relevant Supreme Court & High Court judgments
📝  Draft legal documents (bail applications, notices, agreements)
🔍  Analyze your legal scenario and suggest remedies
📚  Explain legal concepts and maxims

Try one of these:
[Section 302 of IPC]  [Draft a bail application]  [My landlord won't return deposit]
```

**Returning user:**
```
Welcome back! You were last researching:
📋 Section 35 BNS — Right of Private Defence (3 turns)

[Continue]  [New Chat]
```

**Implementation:**
- Check localStorage for existing thread data
- If returning, fetch last thread summary from `/pyapi/threads`
- Render example queries as clickable chips

**Why it matters:** Reduces time-to-first-query. New users immediately understand what the tool can do. Returning users can resume seamlessly.

---

### 8. Related Sections / "People Also Ask"

After showing a legal section, suggest related provisions the user might want to explore.

**Example (after Section 35 BNS):**
```
📌 Related Sections:
• Section 34 — Things done in private defence
• Section 36 — Right of private defence against body (when extends to causing death)
• Section 37 — Restrictions on right of private defence
• Section 40 — Right of private defence against deadly assault
```

**Implementation:**
- You already fetch nearby sections in the Newacts agent (`Enriching with nearby sections`)
- Expose nearby sections as a separate field in the response: `related_sections: [{number, title, act}]`
- Frontend renders them as clickable links that auto-fill the query input

**Why it matters:** Legal research is inherently exploratory. Adjacent sections are almost always relevant. This mirrors how lawyers actually read statutes — never in isolation.

---

### 9. Citation Preview Cards (Expandable Sources)

Transform the flat source list into rich, interactive citation cards.

**Collapsed state:**
```
📄 Salil Mahajan vs Avinash Kumar | SC | 08-12-2025
```

**Expanded state (on click):**
```
📄 Salil Mahajan vs Avinash Kumar
   Court: Supreme Court of India
   Case No: Crl.A. No.-005313-005313 - 2025
   Date: 08-12-2025
   Bench: Justice Sanjay Karol, Justice N.K. Singh

   Key Excerpt:
   "The appellant filed an FIR alleging misappropriation of
   over Rs. 3 crores by the respondent, a Senior Accountant
   at Amandeep Hospital..."

   [📥 Download PDF]  [🔍 Get Full Details]
```

**Implementation:**
- All data already exists in `source_metadata` (parties, court, date, bench, PDF links, content excerpt)
- Frontend: CSS accordion/collapsible cards
- "Get Full Details" button: sends a new query like "Tell me about case [case_name]"

**Why it matters:** Citations are the most valuable part of legal research. Making them interactive and detailed increases trust and usability.

---

## Tier 3 — Differentiators (Medium effort, big impact)

### 10. Side-by-Side Comparison Mode (Old Law vs New Law)

"Compare Section 302 IPC with Section 103 BNS" — rendered in a two-column layout.

**UI:**
```
┌─────────────────────────┬─────────────────────────┐
│ OLD: Section 302 IPC    │ NEW: Section 103 BNS    │
├─────────────────────────┼─────────────────────────┤
│ Punishment for murder.  │ Punishment for murder.  │
│ Whoever commits murder  │ Whoever commits murder  │
│ shall be punished with  │ shall be punished with  │
│ death, or imprisonment  │ death, or imprisonment  │
│ for life, and shall     │ for life, and shall     │
│ also be liable to fine. │ also be liable to fine. │
├─────────────────────────┼─────────────────────────┤
│ Key Changes:                                      │
│ • Section number changed from 302 to 103          │
│ • Substantive content remains the same            │
└───────────────────────────────────────────────────┘
```

**Implementation:**
- You already have `map_old_to_new_law` tool that maps IPC↔BNS, CrPC↔BNSS, IEA↔BSA
- Add a comparison-specific prompt that outputs structured JSON: `{old: {section, title, text}, new: {section, title, text}, changes: [...]}`
- Frontend: detect comparison queries and render in two-column layout
- Trigger: queries containing "compare", "vs", "equivalent", "old vs new"

**Why it matters:** India's 2023 criminal law reforms replaced IPC/CrPC/IEA with BNS/BNSS/BSA. Every lawyer needs to understand the mapping. No other legal AI does this well. This is a unique differentiator.

---

### 11. Multi-language Support (Hindi + Regional)

Indian legal AI without Hindi support is a significant gap, especially for district-level lawyers and litigants.

**Approach:**
- Add a language toggle: English | Hindi | Bilingual
- Use Gemini's native Hindi capability for response generation
- System prompt addition: *"Respond in {language}. Keep section numbers, act names, and case citations in English. Translate explanations and analysis into Hindi."*
- Bilingual mode: English headings + Hindi explanations (common in Indian legal practice)

**Implementation:**
- Add `language` field to `SearchRequest` model
- Pass language preference to synthesis prompt
- Frontend: language selector dropdown in the header
- Consider: Devanagari font rendering in the chat UI

**Why it matters:** Hindi is the first language of 600M+ Indians. District court lawyers often prefer Hindi explanations of English legal text. This dramatically expands the addressable market.

---

### 12. Voice Input

Add a microphone button for speech-to-text input using browser-native Web Speech API.

**Implementation (zero backend changes):**
```javascript
const recognition = new webkitSpeechRecognition();
recognition.lang = 'en-IN'; // Indian English
recognition.continuous = false;
recognition.interimResults = true;

recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    document.getElementById('chatInput').value = transcript;
};
```

**UI:**
- Microphone icon button next to the send button
- Pulsing animation while recording
- Auto-stop after silence detection
- Support both English and Hindi speech (`hi-IN` locale)

**Why it matters:** Lawyers dictate — it's their natural workflow (dictating to clerks, paralegals). Voice input feels natural for legal professionals. Also improves accessibility.

---

### 13. Bookmark / Save Responses

Let users star important responses for quick reference later.

**UI:**
- Bookmark icon (🔖) on each AI response
- "Saved" tab in the sidebar showing all bookmarked responses
- Optional: add tags/labels to bookmarks (e.g., "Section 302 research", "Bail case prep")
- Export all bookmarks as a research compilation (PDF/markdown)

**Implementation:**
- **Option A (frontend-only):** Store bookmarks in localStorage: `{thread_id, turn_number, query, response_preview, tags, timestamp}`
- **Option B (backend):** New SQLite table + API endpoint for cross-device sync

**Why it matters:** Legal research spans days/weeks. Lawyers need to save and revisit key findings. Without bookmarks, they resort to copy-pasting into Word documents.

---

### 14. Session Context Banner

Show a persistent banner during multi-turn conversations displaying the current research context.

**UI (below header, above chat):**
```
📋 Topic: Section 35 BNS — Right of Private Defence
🔄 Turn 3 of 4 | Thread: b90272d2
🔀 Last query was rewritten for context: "SC cases on Salil Mahajan..."
```

**Implementation:**
- You already send `context` events in SSE with `query_rewritten`, `effective_query`, and `conversation_turn`
- Frontend: render as a sticky banner that updates after each turn
- Collapsible — click to see full rewrite history

**Why it matters:** In multi-turn conversations, users lose track of what the AI "remembers." This banner makes the AI's understanding transparent, building trust and helping users correct misunderstandings early.

---

## Tier 4 — Advanced (Larger effort)

### 15. Document Workspace (Split-View PDF Chat)

After a PDF is uploaded, show a split view with the PDF on one side and the chat on the other.

**UI:**
```
┌──────────────────────┬──────────────────────┐
│                      │                      │
│   PDF Viewer         │   Chat Panel         │
│                      │                      │
│   [Page 1 of 45]     │  You: What are the   │
│                      │  key findings?       │
│   ████████████████   │                      │
│   ████████████████   │  AI: The judgment    │
│   ██ highlighted ██  │  holds that...       │
│   ████████████████   │                      │
│   ████████████████   │  [Section 4.2 cited] │
│                      │  ← click to scroll   │
│                      │                      │
└──────────────────────┴──────────────────────┘
```

**Features:**
- Render uploaded PDF using PDF.js (browser-native, no server needed)
- When AI cites a page/section, clicking the citation scrolls the PDF to that location
- Highlight relevant passages in the PDF when referenced in the response
- Thumbnail navigation for quick page browsing

**Implementation:**
- Frontend: PDF.js viewer in a resizable left panel
- Backend: Return page numbers / character offsets in source_metadata when citing from uploaded PDFs
- The document agent already extracts text with page-level granularity (PyMuPDF)

**Why it matters:** Lawyers work with documents. The current flow (upload → ask questions → read text response) loses the visual connection to the source document. Split view is how modern legal research tools work (Westlaw, SCC Online).

---

### 16. Legal Research Trail (Agent Reasoning Visualization)

Show the full agent reasoning chain as an interactive, collapsible tree.

**UI:**
```
▾ Research Trail (7 steps, 34.9s)
  ✓ Safety Check — Input validated (0.1s)
  ✓ Memory — Query rewritten: "SC cases on Section 35 BNS" (2.4s)
  ✓ Orchestrator — Planned 2 agents: SCI_Judgment + Newacts (5.2s)
  ▾ Parallel Execution (19.7s)
    ├─ ✓ SCI_Judgment (19.7s)
    │    ├─ search_by_keyword → 5 results
    │    ├─ search_by_party_name "Salil Mahajan" → 4 results
    │    └─ get_case_details #5313 → read 3000 chars
    └─ ✓ Newacts (7.6s)
         ├─ Metadata: BNS Section 35
         ├─ ES search → 1 hit + 2 nearby
         └─ LLM generation → 1980 chars
  ✓ Synthesizer — Combined 2 agents → 5724 chars (7.5s)
  ✓ Quality Check — Output sanitized (0.1s)
```

**Implementation:**
- Extend SSE events to include tool call details (tool name, arguments, result count)
- Frontend: render as a collapsible tree with timing badges
- Click any step to see the raw tool input/output (debugging mode)

**Why it matters:** Transparency builds trust. Legal professionals need to understand *how* the AI reached its conclusion — not just *what* it concluded. This also helps you debug issues in production.

---

### 17. Proactive Case Alerts

Notify users when new judgments are added to the database that are relevant to their past research.

**Example:**
```
🔔 New judgment alert!
A new Supreme Court judgment was uploaded last week that's relevant
to your research on "Section 35 BNS — Right of Private Defence":

  Rajesh Kumar vs State of UP (Crl.A. 123/2026, 18-02-2026)
  — Discusses limits of private defence in property disputes

[View Details]  [Dismiss]
```

**Implementation:**
- Track user's research topics per thread (extract from orchestrator's task classification)
- When new judgments are indexed in ES, run a background matching job
- Store alerts in a `notifications` table
- New endpoint: `GET /pyapi/notifications`
- Frontend: bell icon with badge count, notification dropdown

**Why it matters:** This transforms the tool from reactive (answer questions) to proactive (push relevant updates). Legal research is ongoing — new judgments can change the legal landscape overnight. Lawyers currently rely on manual monitoring of court websites.

---

## Priority Matrix

| Priority | Feature | Effort | Impact | Category |
|----------|---------|--------|--------|----------|
| **P0** | 1. Suggested follow-ups | Low | High | Engagement |
| **P0** | 5. Agent status indicators | Low | High | Trust/UX |
| **P1** | 2. Response feedback | Low | High | Quality |
| **P1** | 6. Conversation history sidebar | Medium | High | Core UX |
| **P1** | 3. Regenerate response | Low | Medium | UX |
| **P1** | 4. Copy/export response | Low | Medium | Utility |
| **P2** | 9. Citation preview cards | Low | High | Trust |
| **P2** | 7. Smart greeting | Low | Medium | Onboarding |
| **P2** | 8. Related sections | Medium | Medium | Discovery |
| **P2** | 14. Session context banner | Low | Medium | Transparency |
| **P3** | 10. Comparison mode | Medium | High | Differentiator |
| **P3** | 11. Multi-language | Medium | High | Market reach |
| **P3** | 12. Voice input | Low | Medium | Accessibility |
| **P3** | 13. Bookmark/save | Low | Medium | Utility |
| **P4** | 15. Document workspace | High | High | Power feature |
| **P4** | 16. Research trail | Medium | Medium | Transparency |
| **P4** | 17. Proactive alerts | High | Medium | Differentiator |

---

*Generated on 2026-02-22 for Lawttorney AI v2 multi-agent architecture.*
