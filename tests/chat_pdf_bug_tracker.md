# `/chat` + plaint.pdf bug tracker

Sequential fix-and-verify workflow. Each bug must be fixed permanently for all cases, with a focused test demonstrating the fix.

**Source artefacts**
- Investigation report: [chat_pdf_bug_analysis.md](chat_pdf_bug_analysis.md)
- Original eval results: [chat_pdf_eval_results.json](chat_pdf_eval_results.json)
- Eval runner: [chat_pdf_eval.py](chat_pdf_eval.py)

**Testing setup**
- Local server: `python -m uvicorn core.gateway:app --host 0.0.0.0 --port 5000`
- Eval endpoint after fixes: `http://localhost:5000/pyapi/chat`
- PDF: `D:\agentic_proj\Lawtech-AI\test_pdfs\plaint.pdf`

## Status legend
- ⬜ Not started
- 🔧 In progress
- ✅ Fixed + verified
- ❌ Fix failed verification
- ⏭️ Deferred (transitively fixed by another bug)

## Bug list

| # | Severity | Bug | Status | Fix commit / notes |
|---|---|---|---|---|
| BUG-01 | Critical | Substring keyword match in `_DRAFT_KEYWORDS` (`"plaint"` matches `"plaintiff"`) — forces wrong routing | ✅ | regex-based intent detector ([orchestrator.py:60-180](../agents/orchestrator.py#L60-L180)); 54/54 unit + 7/7 integration |
| BUG-02 | Critical | Drafting agent ignores injected PDF facts; template dominates over PDF in section-gen prompt | ✅ | structured `case_facts` extraction + reordered prompt + rule-#8/#13 strengthened. Verified: draft now contains "Mr. Arun Shankar Deshmukh", "₹10,00,000", "15th April 2023", "Mr. Rohit Naik" etc. |
| BUG-03 | Critical | Multi-agent synthesis demotes Document agent answer to "citations" when Drafting present | ✅ | append-only synthesis ([orchestrator.py](../agents/orchestrator.py) replaced `_inject_citations_into_draft` LLM rewrite + drops Document from citations when file is attached) |
| BUG-04 | Critical | Document agent hallucinates via Gemini Files API path (P4 fabricated all facts) | ✅ | resolved transitively by BUG-02 file_processor fix (inline_text now always populated for PDFs → Document agent's "Additional document text" prompt anchor is non-empty). Verified: limitation conclusion now CORRECT ("within the prescribed limitation period"), no Rs.8L/15.01.2020/Chennai/Bangalore/Mr.A/Rs.25L hallucinations |
| BUG-05 | High | Template selection LLM picks by question keywords, not PDF content | ✅ | `_select_best_template` now receives a 1500-char case-context summary alongside the question (drafting.py) |
| BUG-06 | High | Hallucinated court-fee specifics (P7 invented "Rs.12,500 / Schedule I Article 1") | ✅ | transitive fix; verified: agent now correctly says *"the space for the actual court fee amount has been left blank in the document. Therefore, the file does not contain the information required to answer your question."* |
| BUG-07 | High | Limitation analysis returned opposite legal conclusion (P4) | ✅ | transitive fix; verified: agent now correctly concludes *"Yes, the suit is filed within the limitation period"* with correct cause-of-action analysis (15-Oct-2023 + 3 yrs = 15-Oct-2026, plaint dated 29-Jun-2025 is within) |
| BUG-08 | High | Substituted entirely fictional case (P10: Chennai, Mr.A, Rs.25L) | ✅ | transitive fix; verified: P10 answer now references real Pune court, real Rs.10,00,000, real limitation analysis — no Chennai/Bangalore/Mr.A/Rs.25L/sale-deed fabrications |
| BUG-09 | Medium | `[CITE: No matching case found — verify]` internal markers leaked to user | ✅ | `_strip_internal_cite_markers` regex applied in solo + multi-agent synthesis paths |
| BUG-10 | Medium | 130-150s latency on misrouted drafting calls | ⏭️ | resolved transitively by BUG-01 (no longer misrouted) |
| BUG-11 | Medium | UI shows "Identified: Drafting, Document" for pure Q&A queries | ⏭️ | resolved transitively by BUG-01 (verifier confirms `Identified: Document` for Q&A) |
| BUG-12 | Medium | Statute-reference injection runs on placeholder-filled drafts | ⬜ | likely transitive ⏭️ from BUG-02 |
| BUG-13 | Low | `drafting_progress` events arrive out of order | ✅ | added stable `index` (0-based) + `start_order` fields to drafting_progress events ([drafting.py](../agents/drafting.py)) and extended SSE allowlist ([chat_runner.py](../core/chat_runner.py)). Verified: 12/12 events on 7-section draft carry `index = section - 1` correctly aligned. |
| BUG-14 | Low | Fresh thread reports `Found 1 previous turns` (cross-thread leak?) | ✅ | not a leak — memory agent's `_load_chat_history` returns 2-message placeholder ("Previous summary:" + "Fresh chat started.") for new threads; the turn count was naively `len // 2`. Now detects placeholder and reports 0. ([memory.py](../agents/memory.py)) |
| BUG-15 | Low | Inconsistent AI-disclaimer footer across answers | ✅ | extended `DISCLAIMER_TASKS` from 4 to 11 task types ([disclaimer.py](../tools/inline/disclaimer.py)) — every legal task now gets the disclaimer; only `Non_legal` is excluded. Verified: Document and Drafting answers both end with disclaimer. |
| BUG-16 | Low | 30K-char PDF bloats BM25 template search | ✅ | resolved transitively — orchestrator no longer concatenates PDF into agent_queries["Drafting"]; BM25 receives only the user's clean question |

## Per-bug logs

### BUG-01 — Substring keyword match in `_DRAFT_KEYWORDS`  ✅
- **Status:** Fixed and verified
- **Location:** [agents/orchestrator.py:60-180](../agents/orchestrator.py#L60-L180) (new `_wants_drafting` helper) and [agents/orchestrator.py:980-988](../agents/orchestrator.py#L980-L988) (call site)
- **Fix summary:**
  - Replaced `any(kw in _orig_lower for kw in _DRAFT_KEYWORDS)` with intent-aware `_wants_drafting(query)`.
  - Detects: (1) explicit drafting verbs `draft|drafting|redraft` (any tense), (2) verb + article + document-noun (`write a plaint`, `compose an MOU`), (3) format/sample/template requests (`format of bail application`, `sample plaint`), (4) `drafting/preparation of X`.
  - Q&A short-circuit: queries with `summarize|explain|who is|what is|how|when|why|which` etc. are blocked from triggering drafting unless they explicitly use a drafting verb or format request.
  - Format/template requests override Q&A short-circuit (e.g. "What is the format of a bail application?" still routes to drafting).
- **Verification:**
  - **Unit:** `python tests/test_wants_drafting.py` → 54/54 pass ([test_wants_drafting.py](test_wants_drafting.py))
  - **Integration:** `python tests/verify_bug01_routing.py` → 7/7 pass ([verify_bug01_routing.py](verify_bug01_routing.py))
    - P1, P2, P3, P5, P8 now route to `["Document"]` only (was 4 agents incl. Drafting)
    - P6 ("Draft a written statement...") still routes to `["Drafting", "Document", "Scenario", "Judgment"]`
    - P10 ("...defenses against this suit...") routes to `["Document", "Scenario", "Legislation"]` (no Drafting)

### BUG-02 — Drafting agent ignores injected PDF facts
- **Status:** ⬜
- _filled in when fix begins_

### BUG-02 — Drafting ignores PDF facts  ✅
- **Status:** Fixed and verified
- **Root cause chain:**
  1. Orchestrator was concatenating 30K of PDF text into `agent_queries["Drafting"]`, bloating BM25 template search and burying facts.
  2. Section-generation prompt presented the (placeholder-laden) reference template BEFORE the user's facts, so the LLM aped the template.
  3. System-prompt rule #8 told the LLM to "use placeholders for missing details", which directly contradicted rule #13's "use real facts when provided".
  4. **Critically**, the file-processor only populated `inline_text` for PDFs when the Gemini Files API upload *failed* — so when Gemini upload succeeded (the normal case), `fc.inline_text=""` and the drafting agent had nothing to work with.
- **Fix locations:**
  - [core/file_processor.py:652-660](../core/file_processor.py#L652-L660) — always populate `inline_text` for PDFs with extractable text (not just on Gemini failure)
  - [agents/orchestrator.py:1043-1059](../agents/orchestrator.py#L1043-L1059) — removed PDF-into-query concatenation; drafting agent now reads `file_context` directly
  - [agents/drafting.py:135-200](../agents/drafting.py#L135-L200) — new `_extract_case_facts()` runs once, produces structured bullet list of entities (parties, amount, dates, court) for use in every section prompt
  - [agents/drafting.py:_generate_outline](../agents/drafting.py) — accepts `user_facts` + `case_facts`; outline tailored to user's case
  - [agents/drafting.py:_generate_section](../agents/drafting.py) — facts placed BEFORE template, template framed as "structure-only"
  - [config/prompts.py DRAFTING_SYSTEM_PROMPT rule #8 + #13](../config/prompts.py) — clarified placeholder/facts policy; no contradiction
  - [config/prompts.py DRAFT_OUTLINE_PROMPT](../config/prompts.py) — outline must be tailored to user-provided facts, not template's example
- **Verification:** [tests/verify_bug02_drafting_uses_pdf.py](verify_bug02_drafting_uses_pdf.py) → 13/13 assertions pass. Sample draft body now reads: *"In the Court of the Civil Judge (Junior Division) at Pune, Civil Suit No. 114 of 2025, Mr. Arun Shankar Deshmukh ... Mr. Kunal Rajendra Patil ... received a sum of ₹10,00,000/- (Rupees Ten Lakhs only) in cash from the Plaintiff on 15th April 2023 ... Mr. Rohit Naik and Mr. Sandeep Pawar as witnesses ... legal notice dated 10th January 2024 ... interest thereon at 12% per annum from 15th April 2023"*.

### BUG-03 — Synthesis layer rewrote draft and stripped facts  ✅
- **Status:** Fixed and verified (alongside BUG-02)
- **Root cause:** `_inject_citations_into_draft` and `_auto_cite_draft` in [orchestrator.py](../agents/orchestrator.py) made an LLM call to "merge draft + citations". Despite the `PRESERVE every single word` instruction, the Gemini Flash LLM rewrote the draft and replaced specific facts with placeholders. This destroyed the file-grounded content the drafting agent had carefully built.
- **Fix:** [agents/orchestrator.py:1148-1182](../agents/orchestrator.py#L1148-L1182) (solo) + [orchestrator.py:1188-1247](../agents/orchestrator.py#L1188-L1247) (multi-agent) — both paths now use **append-only** synthesis: the drafting agent's content is preserved verbatim, internal `[CITE: ...]` markers are stripped, citations are appended as a `## REFERENCES & CITATIONS` block. The Document agent's content is dropped from the citation block when a file is attached (its content is already incorporated into the file-grounded draft).
- **Verification:** Same as BUG-02 — the user-visible final answer now contains all the real facts from the PDF.

### BUG-05 — Template selection by question keywords  ✅
- **Status:** Fixed and verified (alongside BUG-02)
- **Fix:** [agents/drafting.py:_select_best_template](../agents/drafting.py) now accepts a `user_facts` arg; first 1500 chars are surfaced to the LLM as "USER CASE CONTEXT". The selection prompt explicitly tells the LLM to use the case context to pick a template that matches the user's actual matter, not just keyword overlap with the question.

### BUG-09 — Internal `[CITE:]` markers leaked  ✅
- **Status:** Fixed and verified
- **Fix:** [agents/orchestrator.py:_strip_internal_cite_markers](../agents/orchestrator.py) — regex strips any `[CITE: ...]` token in the synthesis output (both solo-Drafting and multi-agent draft paths). Whitespace/punctuation tidied after substitution.

### BUG-16 — 30K PDF bloated BM25 template search  ✅
- **Status:** Fixed transitively
- **Fix:** Same orchestrator change that fixed BUG-02 — PDF text is no longer concatenated into `agent_queries["Drafting"]`, so the BM25 template search receives only the user's clean question.

### BUG-04 — Document agent hallucinates via Gemini Files API
- **Status:** ⬜
- _filled in when fix begins_

### BUG-04 — Document agent hallucinates via Gemini Files API
- **Status:** ⬜
- _filled in when fix begins_

### BUG-13 — `drafting_progress` events out of order  ✅
- **Status:** Fixed and verified
- **Root cause:** `progress_counter["count"]` was incremented inside the semaphore (start order), so the `section` field reflected acquisition order rather than the section's natural document position. Frontend checklist UI couldn't slot completion events into the correct row when sections finished out-of-order.
- **Fix:** [agents/drafting.py:_gen_one](../agents/drafting.py) — `section` now uses `i + 1` (natural index, stable). New `index` (0-based) and `start_order` (debug) fields added. SSE allowlist in [core/chat_runner.py](../core/chat_runner.py) extended to forward both new fields.
- **Verification:** 12/12 events on a 7-section draft carry `index = section - 1`.

### BUG-14 — Fresh thread reports `Found 1 previous turns`  ✅
- **Status:** Fixed and verified — not a cross-thread leak
- **Root cause:** [agents/memory.py:_load_chat_history](../agents/memory.py) returns a 2-message placeholder `[HumanMessage("Previous summary:"), AIMessage("Fresh chat started.")]` for new threads with no SQLite history. The downstream code computed `turns = len(chat_history) // 2`, which yielded `1` for the placeholder.
- **Fix:** [agents/memory.py:325](../agents/memory.py) detects the placeholder pattern and reports `turns=0`. Log line now includes `placeholder_history=true` for clarity.
- **Verification:** Both fresh-thread tests show `Found 0 previous turns`.

### BUG-15 — Inconsistent disclaimer footer  ✅
- **Status:** Fixed and verified
- **Root cause:** `DISCLAIMER_TASKS = {"Scenario", "Legal_Concepts", "Other", "Drafting"}` only covered 4 of the 11 legal task types. Document, Judgment, Legislation, etc. answers had no disclaimer, so the footer's presence depended on which agent produced the answer.
- **Fix:** [tools/inline/disclaimer.py](../tools/inline/disclaimer.py) extends `DISCLAIMER_TASKS` to all 11 legal task types. Only `Non_legal` (greetings, casual chat) is excluded.
- **Verification:** Both Document Q&A and Drafting answers end with `**Disclaimer:** This response is generated by an AI assistant ... does not constitute legal advice ... consult a qualified legal professional`.
