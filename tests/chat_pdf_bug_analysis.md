# Deep investigation: `/pyapiv2/chat` + `plaint.pdf` x 10 prompts

**Test artefacts**
- Runner: [tests/chat_pdf_eval.py](chat_pdf_eval.py)
- Full answers: [tests/_eval_full_answers.txt](_eval_full_answers.txt)
- Raw event captures: [tests/_debug_events_P03_loan_amount.json](_debug_events_P03_loan_amount.json), [tests/_debug_events_P04_limitation.json](_debug_events_P04_limitation.json)
- Score table: [tests/chat_pdf_eval_report.md](chat_pdf_eval_report.md)

## Ground-truth facts in `plaint.pdf`

| Field | Actual value |
|---|---|
| Court | Civil Judge (Junior Division), Pune |
| Case # | Civil Suit No. 114 of 2025 |
| Plaintiff | Arun Shankar Deshmukh, 42, businessman, Bibwewadi Pune-411037 |
| Defendant | Kunal Rajendra Patil, 39, self-employed, Kothrud Pune-411038 |
| Filing under | Order VII Rule 1 CPC, recovery of money |
| Loan | ₹10,00,000 cash on **15-Apr-2023** (witnesses: Naik, Pawar) |
| Repayment due | within 6 months → **15-Oct-2023** |
| Legal notice | dated 10-Jan-2024, served 15-Jan-2024 |
| Interest claimed | 12% p.a. from 15-Apr-2023 |
| Court fee | **blank in plaint** ("₹___") |
| Verified | Pune, 29-Jun-2025, Adv. Meera Kulkarni |

---

## Per-prompt fact audit

| # | Prompt intent | Verdict | Hallucinated facts |
|---|---|---|---|
| 1 | Summarize plaint | **FAIL** | Returned Maharashtra Rent Control eviction template — zero PDF facts |
| 2 | Who is plaintiff/defendant | **FAIL** | Returned SRA Sec.6 trespass template, "Mr. A vs Mr. B" |
| 3 | Loan amount + interest | **FAIL** | Generic loan-recovery template with `[Loan Amount]`, `[Interest Rate]%` placeholders |
| 4 | Limitation analysis | **FAIL** (rated WEAK) | Rs.8L, 15.01.2020 disbursement, fictional Rs.2L part-payment 30.06.2023, plaint dated 25.04.2026, **opposite legal conclusion** |
| 5 | Evidence to lead | **FAIL** | Returned "Application for summons to witness" for a specific-performance suit |
| 6 | Draft written statement | PASS | Correctly used "Kunal Rajendra Patil" |
| 7 | Court fee | PASS-but-hallucinating | Asserted "Rs. 12,500 per Schedule I Article 1" — plaint has the amount **blank** |
| 8 | Explain Order VII Rule 1 | **FAIL** | Returned "goods sold and delivered" template; only briefly explains the rule |
| 9 | Case law on cash loans | PASS | General-knowledge answer, real cases |
| 10 | Defendant's defences | **FAIL** (rated WEAK) | "Chennai District Court", "Mr. A in Bangalore", "Rs. 25L sale agreement", "April 2026 plaint" — entirely different case |

---

## Routing evidence (from event capture)

**Prompt 3 (`...claimed in this plaint?`)** — `agents_planned: ["Drafting", "Document", "Judgment", "Legislation"]`
- 4 agents fired despite the user asking a simple Q&A
- Drafting selected `Suit For Recovery Of Money` template (good match), then generated a draft with `[______]` placeholders despite the PDF being available
- 130s elapsed; 21,297 tokens consumed
- Final synthesis used `_inject_citations_into_draft` — Document agent's PDF-grounded content was treated as citations and effectively discarded

**Prompt 4 (`limitation period?`)** — `agents_planned: ["Document"]`
- Single Document agent path via Gemini Files API (`"Multimodal file analysis (Gemini Files API)"`)
- 15s elapsed; 2,025 tokens
- Document agent itself **hallucinated every fact** — Rs.8L, wrong dates, fictional part payment, opposite legal conclusion
- Suggests Gemini either didn't ingest the file URI properly or applied the wrong cached context

---

## Bug list

### BUG-01 [CRITICAL] — Substring keyword match in `_DRAFT_KEYWORDS` forces wrong routing
**Location:** [agents/orchestrator.py:868-877](../agents/orchestrator.py#L868-L877)
```python
_DRAFT_KEYWORDS = ("draft", "prepare", "create", "write", "generate", "make",
                   "bail application", "petition", "plaint", "notice", ...)
if fc and fc.has_content and any(kw in _orig_lower for kw in _DRAFT_KEYWORDS):
    if "Drafting" not in tasks_planned:
        tasks_planned.insert(0, "Drafting")
```
- `"plaint" in "plaintiff"` → True. Any Q&A query mentioning "plaintiff" / "complaint" / "explaining" + a file attached force-routes to Drafting.
- Confirmed for prompts 1, 2, 3, 8.
- "create" matches "creative", "make" matches "Makers", etc.
- **Fix:** Use word-boundary regex (`\bplaint\b`) and gate on explicit verbs ("draft this", "prepare a"), not bare nouns.

### BUG-02 [CRITICAL] — Drafting agent ignores injected PDF facts
**Location:** [agents/drafting.py:281-292](../agents/drafting.py#L281-L292) section-generation prompt order
- Orchestrator injects PDF text into `agent_queries["Drafting"]` with the directive *"Do NOT use placeholders for information available below"* ([orchestrator.py:922-937](../agents/orchestrator.py#L922-L937)).
- But the section-generation prompt sends the chosen empty template (Reference Template) **first** and the PDF-laden user query **last**. The LLM apes the template and ignores the PDF.
- Result: every drafting answer for this run had `[Plaintiff Name]`, `[Defendant Name]`, `[Date]`, `[Amount]` placeholders — even though Arun Deshmukh / Kunal Patil / Rs.10,00,000 / 15-Apr-2023 were available.
- **Fix:** Either (a) lead the prompt with the PDF facts and demote the template to a structural reference, or (b) pre-substitute placeholder tokens by extracting entities from the PDF before generation.

### BUG-03 [CRITICAL] — Multi-agent synthesis discards Document agent answer when Drafting is present
**Location:** [agents/orchestrator.py:1071-1127](../agents/orchestrator.py#L1071-L1127)
```python
if "Drafting" in valid_results:
    drafting_result = valid_results.pop("Drafting")
    citation_results = valid_results   # Document, Judgment, Legislation, etc.
    ...
```
- All non-Drafting agent outputs become "citation_results" and get either appended as a postscript (drafts >40K) or melted into a citation block.
- The Document agent's accurate PDF-grounded answer is lost behind a placeholder-filled template.
- Confirmed for prompts 1, 2, 3, 5, 8.
- **Fix:** When a file is attached, prefer the Document agent's content as the primary response unless the user *explicitly* asked to draft a document.

### BUG-04 [CRITICAL] — Document agent hallucinates via Gemini Files API path
**Location:** [agents/document.py:245-321](../agents/document.py#L245-L321) (`all_gemini_parts` branch)
- Prompt 4 routed cleanly to `["Document"]` only, used `"Multimodal file analysis (Gemini Files API)"`, took 15s — yet returned a totally fictional case (Rs.8L, 15.01.2020, part payment 30.06.2023, verification 25.04.2026; opposite legal conclusion).
- Likely causes:
  - Gemini Files URI expired (24-48h TTL); upload silently fell back to no-context.
  - File-data part rejected by Gemini and the LLM defaulted to its training prior.
  - System prompt's "extract names, dates, section numbers" cues triggered template-style generation when content access failed.
- The system prompt explicitly says "NEVER generate fake case names, case numbers, or court details" — that guard is being violated.
- **Fix:** (a) verify Gemini upload status before invoking; (b) always pass `inline_text` alongside the URI as a fact anchor; (c) add a post-hoc fact-check that flags any names/dates/amounts not found in `inline_text`.

### BUG-05 [HIGH] — Drafting template selection LLM picks templates by question keywords, not document content
**Location:** [agents/drafting.py:144-184](../agents/drafting.py#L144-L184)
- Selected templates for the run:
  - P1 "summarize plaint" → **Maharashtra Rent Control Act eviction**
  - P2 "who is plaintiff" → **SRA Sec.6 trespass / dispossession**
  - P5 "what evidence to lead" → **Application for summons to witness (specific performance suit)**
  - P8 "Explain Order VII Rule 1" → **Suit for goods sold and delivered**
- The selection LLM only sees the user query + 200-char template previews. The actual PDF context never enters template selection.
- **Fix:** Pass an entity summary of the attached PDF (case-type, parties, claim) to the template-selection LLM.

### BUG-06 [HIGH] — Hallucinated court-fee specifics on Prompt 7
- Answer asserts: *"the requisite court-fee of Rs. 12,500/- is paid herewith as per Schedule I, Article 1 of the Maharashtra Court-fees Act"*
- Actual plaint: *"affixes the requisite court fee of ₹___"* (left blank)
- Eval marked PASS because the eval evaluator only had the doc summary, not the verbatim text.
- This is a high-trust hallucination — Lawttorney is asserting facts about a court filing that aren't in the document.

### BUG-07 [HIGH] — Limitation analysis is opposite of correct (Prompt 4)
- Agent: "barred by limitation, suit filed beyond 3-year period"
- Reality: cause of action 15-Oct-2023 → 3-yr bar 15-Oct-2026 → plaint verified 29-Jun-2025 is **well within** limitation
- A user relying on this analysis would mis-advise their client.

### BUG-08 [HIGH] — Hallucinated case substitution on Prompt 10
- Returned an analysis about "Court of District Judge at Chennai", "Mr. A in Bangalore", "Rs. 25,00,000 sale agreement", "plaint dated April 20, 2026", limitation under Article 54 (specific performance).
- Plaint is in Pune, money recovery, Rs. 10L, dated 29-Jun-2025, governed by Article 19/22.
- The agent fabricated an entirely different case from training data.

### BUG-09 [MEDIUM] — `[CITE: No matching case found — verify]` placeholders leaked to user
- Prompts 1, 2 contain literal text `[CITE: No matching case found — verify]` and `[CITE: No matching SC case found — verify]`.
- These are internal markers from auto-citation fallback; should be stripped before final response.
- **Fix:** strip-pattern in synthesis output sanitiser.

### BUG-10 [MEDIUM] — 130-150s latency on misrouted Drafting calls
- Prompt 1: 131s, Prompt 2: 122s, Prompt 3: 99s, Prompt 5: 154s, Prompt 6: 150s, Prompt 8: 142s
- Each runs 8-section parallel generation (sem=3) on Gemini Pro/Flash plus a 4-agent fan-out plus citation injection.
- For Q&A questions misrouted here this burns ~20K tokens per request.
- **Fix:** ensure routing correctness first (BUG-01), then this load disappears.

### BUG-11 [MEDIUM] — `Identified: Drafting, Document` shown to user when user did not ask to draft
- Frontend `progress` event surfaces `"Identified: Drafting, Document"` for prompts that were pure Q&A.
- Misleads users about what the system is doing.

### BUG-12 [MEDIUM] — Statute-reference injection runs on placeholder-filled drafts
- P3 events show `"Adding statute references..."` after Drafting completed → tokens spent decorating a useless template.

### BUG-13 [LOW] — `drafting_progress` events arrive out of order
- P3 event log: section 3 completes before section 1, section 4 starts before section 2 completes, etc.
- Sections run in parallel via `asyncio.Semaphore(3)`, so this is expected, but the frontend checklist UI may render confusingly.
- **Fix:** sort by `section` index in the renderer or emit a final `drafting_complete` summary.

### BUG-14 [LOW] — Memory agent reports `Found 1 previous turns` on a fresh thread
- Both P3 and P4 (different thread_ids, brand-new uuids) show `history_turns: 1` in the `context` event and "Found 1 previous turns" progress.
- Either (a) the current turn is being counted as history before it's the *current* turn, or (b) there's cross-thread leakage in chat_store.
- Worth checking [core/chat_store.py](../core/chat_store.py).

### BUG-15 [LOW] — Inconsistent disclaimer footer
- Prompts 6, 9, 10 end with `**Disclaimer:** This response is generated by an AI assistant...`
- Prompts 1-5, 7, 8 do not.
- Likely depends on which agent path produced the answer.

### BUG-16 [LOW] — Drafting agent's first-call BM25 is bloated by 30K char PDF text
- [orchestrator.py:922-937](../agents/orchestrator.py#L922-L937) appends the full PDF to `agent_queries["Drafting"]`.
- That bloated query is then passed straight into [drafting.py:_search_templates](../agents/drafting.py#L109) which uses it as a BM25 match string.
- Sanitiser truncates to 500 chars but `[Lucene-escaped 30K]` produces noisy BM25 scores either way.
- **Fix:** use a separate short query (the user's question) for template search, and inject PDF facts only into outline/section generation.

---

## Severity summary

| Severity | Count | Bugs |
|---|---|---|
| Critical | 4 | BUG-01, 02, 03, 04 |
| High | 4 | BUG-05, 06, 07, 08 |
| Medium | 4 | BUG-09, 10, 11, 12 |
| Low | 4 | BUG-13, 14, 15, 16 |

Fixing **BUG-01** alone (substring → word-boundary match) would route prompts 1/2/3/8 correctly and eliminate the empty-template responses. Fixing **BUG-04** (Document agent hallucination) is required to make prompts 4 and 10 trustworthy. **BUG-03** (synthesis discard) is the secondary fallback that masks the Document agent's correct answers when Drafting is misrouted in.
