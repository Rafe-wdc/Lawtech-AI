# Drafting Simplification Plan

**Date:** 2026-06-28
**Author:** Rohit (direction) + Claude (synthesis)
**Scope:** Strip `agents/drafting.py` down to a dynamic two-source pipeline (ES drafting index → Gemini web search) feeding a single LLM generation call. Delete every enum-driven branch.

**Status:** Phase A + Phase B shipped 2026-06-28 (new pipeline coexists with legacy dead code). Phase C–F not yet started. Smoke verified end-to-end on two prompts (Section 138 NI Act notice via corpus hit; RTI application via web fallback).

**Supersedes:** `docs/drafting_intelligence_plan.md` (2026-06-22). That plan moved the agent toward *more* machinery (forum profiles, evidence RAG, per-section authority retrieval). This plan moves the opposite direction: less code, more LLM judgment, no hand-curated taxonomies.

---

## 1. Why

User feedback (2026-06-28): "our drafting work is useless… users feeling lawttorney's drafting is stupid." Root cause is the chain of enum-driven branches that shape every output before generation runs. The pipeline currently:

1. Classifies into one of 7 hand-curated `DOC_TYPES` ([drafting.py:494](../agents/drafting.py#L494))
2. Routes 4 of those types around the ES drafting index entirely, into `SYNTHETIC_SKELETONS` ([drafting.py:626+](../agents/drafting.py#L626))
3. Routes the other 3 through `DRAFT_OUTLINE_RULES_BY_TYPE` (doc-type-conditional outline rules)
4. Injects `_inject_mandatory_sections` (Schedule of Properties, Verification, IA under Order XXXIX, etc.) regardless of whether the user asked for them
5. Runs a doctrinal-stance JSON contract before section generation
6. Falls back to `GENERIC_COURT_SKELETONS` keyed by `doc_type` ([drafting.py:1633](../agents/drafting.py#L1633))
7. Validates against a forbidden-statute-pairing list (Sec 38 SRA + temp injunction, Sec 54 CPC + residential partition)

Every output is shape-constrained by hand-curated patterns. This directly contradicts the saved feedback rule **"No regex/hardcoded patterns — prefer dynamic prompting"** (`feedback_no_mechanical_patterns.md`). Users feel the output is canned because it *is* canned.

---

## 2. Target Pipeline

Four stages, all LLM-driven, zero enums.

### Stage 1 — Reference Draft Acquisition

Mirrors the **v1 `DraftRetriever`** pattern preserved in the backup at `C:\lawtech_backup\home\ubuntu\Lawtech-AI\retrievers\draft_retriever.py`. v1 worked because the `drafting` index's `source` field carries descriptive file names ("Bail Application Sec 437 CrPC.pdf", "RTI Application to PIO.pdf"), and a single LLM call can pick the right one from a list of paths without needing previews / BM25+kNN hybrid / family verifier / doc-type classifier.

```
ES match query on page_content, size=100  (size=100 from v1; tune later)
       ↓
collect distinct `source` (file paths)
       ↓
single LLM call: "given user query, pick the most relevant file path —
                  or say 'none' if no path matches what the user asked for"
       ↓                              ↓
   picked path                     'none'
       ↓                              ↓
ES term query on                  core/agent_fallback.web_search_fallback
source.keyword → fetch            (Gemini 2.5 Flash + Google Search grounding)
full page_content                 synthesizes a reference draft from open web
       ↓                              ↓
       └──────────── use as reference ──────────────┘
```

- **No doc-type classification.** ES `match` already returns whatever shape matches the query — letter, petition, agreement, notice. The file-path picker absorbs both v1's "best file" selector AND v1's separate `check_relevance` gate — one LLM call, not two.
- **'none' is the rejection signal.** When the LLM says no file path fits, we drop to web fallback. This is what v1 was missing: v1 fell to `Scenario_qa` (legal QA, not draft synthesis), so niche-format queries got essay answers instead of drafts. We use the existing `core/agent_fallback.web_search_fallback` and instruct it to **synthesize a reference draft**, not answer.
- **Reference draft is just text** — not a structured object, not a typed contract. It's input to Stage 2.

### v1 parallels (and v1 gaps we're filling)

| v1 step | Plan equivalent | Notes |
|---|---|---|
| `DraftRetriever.retrieve_documents` ES match → file paths | Same | Keep size=100; tune in Phase B if needed |
| `select_file_source_draft` (GPT-4o-mini structured output picks one path) | Same — but allow `'none'` as output to trigger web fallback | v1 had no rejection path; this is the one gap that bit users |
| `check_relevance(query, documents)` (separate LLM call) | **Folded into the file-path picker** | Saves one LLM call. The "none" output IS the rejection. |
| v1 fallback: `Scenario_qa` (web search returning ESSAY) | Web fallback: `web_search_fallback` synthesizing a **reference draft** | v1 fallback didn't produce a draft — this is why niche formats failed |
| `generate_response` single GPT-4o call with `{docs}` + `{query}` + system prompt | Stage 2 single Gemini Pro call with reference + query + case_facts + user_intent | Same shape; better model; multilingual; intent-aware |
| `customize_legal_draft` (utils/customized_draft.py) | **Intentionally not brought back** | This was the v1 continue_draft equivalent; the route is unused in prod per `project_continue_draft_unused.md` and gets dropped in Phase C |

### Stage 2 — Single-Pass Generation

```
inputs:
  - user_query (verbatim, never truncated/normalized — per feedback rule)
  - case_facts (extracted from attachments, existing _extract_case_facts step)
  - reference_draft (from Stage 1)
  - user_intent (existing typed extractor, gives depth/format/language/additional_instructions)
       ↓
single Gemini 2.5 Pro call
       ↓
full draft
```

The LLM decides headings, sections, length, format, footer, signature block — based on the reference + the user's actual ask. No outline template. No mandatory section injection. No doc-type-conditional rules block. The reference draft is the structural anchor; the user query + intent shapes adaptation.

#### Generation seam (future: per-section fan-out)

Stage 2 is implemented behind a single function:

```python
async def _generate_draft(
    query: str,
    case_facts: str,
    reference_draft: str,
    user_intent: UserIntent,
    progress: ProgressEmitter,
) -> str: ...
```

**Today (Phase B):** the implementation is a single Gemini 2.5 Pro call.

**Future (deferred, but the seam is reserved now so we don't rewrite the agent again):** the same function can dispatch between single-pass and per-section fan-out based on the reference draft + intent — *dynamically, not by enum*. Triggers we'd expect to use (none of them hardcoded gates; all observable from the inputs):

- Reference draft length / detected section count (long writs with 15+ Parts, GROUNDS, PRAYER → fan-out for quality and to dodge single-call output-token ceilings)
- Estimated output length vs. Gemini Pro's effective output budget
- `user_intent.response_depth == "detailed"` combined with a long reference
- A short "should we fan out?" LLM judge call given the reference structure

The fan-out path, when added, would be: extract the section list **from the reference draft itself** (one LLM call) → fan out parallel per-section generation → assemble. **Critically, the section list is reference-derived, not enum-derived** — no `DRAFT_OUTLINE_RULES_BY_TYPE`, no `_inject_mandatory_sections`. The reference is the spec.

This keeps the door open for the per-section streaming UI (per CLAUDE.md "Drafting UX plan, A + C shipped 2026-04-14") without committing to it now.

To keep the seam clean:
- The new `_generate_draft` returns a `str` — it does not return a structured outline object that callers depend on. A future fan-out variant can still expose per-section progress events via `progress(...)` without changing its public signature.
- `tests/test_drafting_simplification.py` does not assert single-pass vs fan-out internals; only output properties (shape, scope, language).
- The scope critic + self-refine in Stage 3 work identically over the assembled output regardless of which generation strategy ran.

### Stage 3 — Scope Critic + Self-Refine

```
generated draft
       ↓
self-refine loop (already shipped, in core/self_refine.py)
   - critic audits against typed UserIntent
   - refiner rewrites on violations
       ↓
final draft
```

This is your "control over generation — do not generate unwanted things" lever. **Implementation note (Phase B finding, 2026-06-28):** the existing `core/self_refine.self_refine` IS the scope critic — no separate function needed. CRITIQUE_PROMPT already audits drafts against typed intent (language, format, depth, additional_instructions) AND drafting-specific categories (placeholder_marker, orphan_citation_tail, forbidden_statute_pair, cause_title_collapsed, paragraph_numbering_break, prayer_relief_mismatch). The new `drafting_node` calls `self_refine(draft, query, intent, source_languages)` directly — same wiring as the legacy pipeline used at its Step 8.

The one limitation worth noting: CRITIQUE_PROMPT's `missing_procedural_section` check pushes the generator to ADD Schedule/Verification/Affidavit on civil suits even if the user just asked for a "draft a plaint." If smoke evidence shows over-injection, tighten that category in a follow-up — extend the prompt, don't add a regex.

### Stage 4 — Mechanical Cleanup

Bug-fix only. No behavior gates.

- Mojibake fix (cp1252-misread-as-UTF-8) — keep, real charset bug
- `[CITE: ...]` placeholder strip — keep, defensive against the prompt slipping
- Drop: forbidden-statute-pairing warnings (replaced by the scope critic)
- Drop: footer auto-attach (the LLM in Stage 2 emits whatever footer the document needs)

---

## 3. Delete List

All paths below in `agents/drafting.py` unless noted. Approx. **1500 LOC removed**.

| Block | Why it goes |
|---|---|
| `DOC_TYPES` tuple ([L494](../agents/drafting.py#L494)) | 7-label enum, hand-curated |
| `DocTypeChoice` + `_DOC_TYPE_CLASSIFIER_PROMPT` + `_classify_doc_type` ([L516-623](../agents/drafting.py#L516-L623)) | The classifier gate the user asked about |
| `DOC_TYPE_TO_FOOTER_KIND` ([L505](../agents/drafting.py#L505)) | Hand mapping |
| `SYNTHETIC_SKELETONS` ([L626+](../agents/drafting.py#L626)) and `DOC_TYPES_USE_SKELETON` ([L771](../agents/drafting.py#L771)) | Hardcoded markdown templates for non-court types |
| `GENERIC_COURT_SKELETONS` ([L1375+](../agents/drafting.py#L1375)) and `_generic_skeleton_for` ([L1644](../agents/drafting.py#L1644)) | Terminal hand-coded fallback |
| `DRAFT_OUTLINE_RULES_BY_TYPE` (in [config/prompts.py](../config/prompts.py)) | Doc-type-conditional outline rules block |
| `_inject_mandatory_sections` (search for "_MANDATORY_PACKS" history at [L2065-2076](../agents/drafting.py#L2065-L2076)) | Force-injects Schedule of Properties, IA, Verification |
| `_generate_doctrinal_stance` + the JSON-contract step | Hand-crafted JSON schema, replaced by reference draft + user intent in Stage 2 |
| Forbidden-statute-pairing warnings in `validate_draft` | Replaced by scope critic |
| Doc-type-aware grading in template critic (`_verify_template_family`, the "good/marginal/bad" split, marginal-path web fallback at [L4661-4742](../agents/drafting.py#L4661-L4742)) | Replaced by the single Stage 1 LLM judge |
| Section-level parallel fan-out + `_SECTION_CONCURRENCY` semaphore | Single-pass generation in Stage 2 |
| `_MAX_SECTIONS = 28` cap ([L63](../agents/drafting.py#L63)) | No section list anymore |
| `_generate_section`, per-section prompts, section assembly | Single-pass generation |
| `/pyapi/continue_draft` + `continue_draft_node` + `draft_continuation` state key | Already unused in prod per `project_continue_draft_unused.md` |

---

## 4. Keep List

- ES drafting index BM25 search (the dynamic data source)
- `core/agent_fallback.web_search_fallback` (already agent-agnostic)
- `_extract_case_facts` step (pulls real names/dates/amounts from attachments)
- `user_intent` layer (typed, drives depth/format/language/additional_instructions, already on hot path per CLAUDE.md "User intent layer")
- `core/self_refine.self_refine` loop (already shipped)
- `core/language.localize_prompt` (multilingual, already shipped)
- Mojibake fix + `[CITE:]` strip in `validate_draft`
- Per-section progress events for UI checklist (Stage 2 emits one "generating" + one "done" since it's single-pass — UI checklist becomes a single bar instead of a per-section list; this is a UX change worth flagging to FSD)

---

## 5. Phases (single branch, full removal)

User chose **full removal in one branch**, no feature flag, no two-phase.

### Phase A — Plan + sign-off (this doc) — ✅ shipped 2026-06-28

- ✅ Plan reviewed
- ✅ Supersession of `drafting_intelligence_plan.md` recorded with banner at top of that file
- ✅ CLAUDE.md "Drafting invariants" rewrite deferred to Phase E (same PR as code deletion — no transitional state where docs and code disagree)

### Phase B — New pipeline build — ✅ shipped 2026-06-28

What landed:

- **3 new prompts** in [config/prompts.py](../config/prompts.py): `DRAFTING_PICKER_PROMPT` (2231 chars), `DRAFTING_SYSTEM_PROMPT_V2` (11541 chars including INDIAN_LEGAL_* shared blocks), `DRAFTING_WEB_FALLBACK_PROMPT` (2341 chars). Phase C renames `_V2` → canonical.
- **4 new helpers** in [agents/drafting.py](../agents/drafting.py):
  - `_pick_reference_source(query, file_paths) -> str | None` — Gemini Flash Lite, structured output `_PickerChoice`, mirrors v1's `select_file_source_draft` + adds `'none'` as a valid output. Fuzzy fallback if LLM returns a slightly-massaged path.
  - `_acquire_reference_draft(query, progress_emit, *, user_language, intent, original_query) -> tuple[str, str, str]` — ES `match` size=100 → distinct sources → picker → fetch by `source.keyword`. Falls to web on `'none'`, empty corpus, or fetch miss. Returns `(text, source, kind)` where `kind ∈ {'es', 'web'}`.
  - `_acquire_reference_via_web(query, ...) -> str` — wraps `core.agent_fallback.web_search_fallback` with `DRAFTING_WEB_FALLBACK_PROMPT` to synthesize a reference draft (not an essay — v1's gap).
  - `_generate_draft(query, case_facts, reference_draft, user_intent, user_language, progress_emit) -> str` — single Gemini 2.5 Pro call (temperature 0.4, max 65K output, 2K thinking budget). The seam for future per-section fan-out.
- **`drafting_node` rewritten** to use new helpers: read state → build user_facts blob → semaphore acquire → extract case facts → acquire reference → generate → `validate_draft(draft, stance=None)` cleanup → `self_refine` audit pass → AgentResult with source attribution.
- **No new `_scope_critic` helper.** Phase B finding: `core/self_refine.self_refine` already audits drafts against typed UserIntent + drafting-specific categories (placeholder_marker, orphan_citation_tail, forbidden_statute_pair, cause_title_collapsed, etc.). Wiring `self_refine` is sufficient.
- **One missing import added**: `get_gemini_pro` to drafting.py's `core.clients` import line.
- **Legacy code preserved as dead code**: renamed under `_LEGACY_drafting_body_DELETE_IN_PHASE_C` (around L5069 of drafting.py). Not callable; visible in `git diff` of Phase C.

Smoke verified on:
1. **Section 138 NI Act demand notice** — corpus hit → picker chose "Notice under Section 138 of Negotiable Instruments Act.csv" → single-pass generation produced a 2479-char letter-shaped output using real party names + dates + amounts from the user query directly (not bracketed). **No court cause title, no Verification, no Prayer, no Schedule.**
2. **RTI application to Pune Passport Office** — corpus rejected → picker returned `'none'` → web fallback synthesized a reference draft from 12 web sources → single-pass generation produced a 2087-char proper RTI application with real Pune Passport Office address, Section 6(1) RTI Act citation, numbered facts, "Yours faithfully" sign-off. **No court scaffolding force-injected.**

### Phase C — Delete

- Remove every item in §3 from `agents/drafting.py` and `config/prompts.py`
- Remove `/pyapi/continue_draft` from `core/gateway.py`, `continue_draft_node`, `draft_continuation` state field
- Remove tests in `tests/test_drafting_quality.py` that assert old-pipeline behavior (mandatory sections, stance JSON, forbidden pairings, generic skeleton fallback)

### Phase D — Replacement tests

- `tests/test_drafting_simplification.py` (new):
  - Reference acquisition: ES hit usable → judge accepts → no web call
  - Reference acquisition: ES hit irrelevant → judge rejects → web fallback fires
  - Scope critic: user asks for "demand notice" → draft contains no Prayer/Verification block
  - Scope critic: user asks for "RTI application" → draft contains no Cause Title
  - User intent flow: `wants_brief` → output stays brief; `language=hi` → output Hindi
  - End-to-end: bail application produces a court-shaped output even without `doc_type` routing
  - End-to-end: leave letter produces a letter-shaped output without `SYNTHETIC_SKELETONS`

### Phase E — CLAUDE.md update

- Rewrite the "Drafting invariants (do not regress)" section
- New invariants:
  1. Reference draft must come from ES first, web only on fallback
  2. Scope critic must run before final return; user-unrequested sections are forbidden
  3. User query is never truncated or summarized (existing feedback rule)
  4. Mojibake + `[CITE:]` cleanup are bug fixes, not behavior gates
- Mark `drafting_intelligence_plan.md` as superseded by this doc

### Phase F — Memory updates

- Add `project_drafting_simplification.md` to MEMORY.md
- Update `project_drafting_intelligence_plan.md` and `project_drafting_quality_overhaul.md` entries with "superseded" notes
- Update `project_continue_draft_unused.md` with "removed" once Phase C lands

---

## 6. Risks (be honest)

| Risk | Mitigation |
|---|---|
| Partition-suit cross-section contradictions (self-acquired vs ancestral) — the 2026-06-02 overhaul fix | Reference draft + scope critic. The reference itself locks the theory; the single-pass generation can't drift across sections because there are no sections to drift across. |
| Sec 38 SRA + Order XXXIX CPC mis-pairing | Scope critic catches it. The user didn't ask for an injunction → critic flags injunction prayer as "not requested". |
| Long writs (15 Parts + GROUNDS + PRAYER) blow Gemini Pro's output budget in one call | The `_generate_draft` seam (§2 Stage 2) reserves the per-section fan-out path. If Phase B prototyping on the Avachat writ shows single-pass can't hit length, the same function dispatches to a fan-out implementation — section list extracted **from the reference draft**, not from `DRAFT_OUTLINE_RULES_BY_TYPE`. No rewrite of the agent, just a second strategy inside `_generate_draft`. |
| Loss of per-section UI progress events | Notify FSD: drafting will emit one "generating" + one "done", not 16-section checklist. Acceptable UX trade-off for the simplification. |
| Web fallback latency on every cold query | Acceptable — only fires when ES judge rejects. ES hit rate on real traffic is high; tax-appellate + niche-format queries are the only routine misses. |
| The 24 unit + 8 e2e tests in `test_drafting_quality.py` go red | Expected and acceptable. They encode the very behavior we're removing. Phase D adds replacement tests. |

---

## 7. Acceptance Criteria

Before merge:

1. `_classify_doc_type`, `DOC_TYPES`, `SYNTHETIC_SKELETONS`, `GENERIC_COURT_SKELETONS`, `DOC_TYPE_TO_FOOTER_KIND`, `DRAFT_OUTLINE_RULES_BY_TYPE`, `_inject_mandatory_sections`, `_generate_doctrinal_stance` — none of these symbols exist in the codebase.
2. `agents/drafting.py` is under 1000 LOC (currently ~4900).
3. New tests in `tests/test_drafting_simplification.py` pass.
4. Smoke against `api.lawttorney.com/pyapi/search/stream` on these 5 prompts (bail application, RTI application, partition suit plaint, Section 138 NI Act notice, leave letter) returns a shape-appropriate draft for each — verified by Rohit, not just by the scope critic.
5. CLAUDE.md "Drafting invariants" rewritten.
6. `drafting_intelligence_plan.md` marked superseded.

---

## 8. Open follow-ups (out of scope for this PR)

- **Per-section fan-out implementation behind the `_generate_draft` seam.** Phase B ships single-pass only. The fan-out path stays as a reserved hook (§2 Stage 2) — added later when (a) a real Avachat-class writ shows single-pass truncation, or (b) we want the per-section streaming UI back. Implementation when triggered: one LLM call to extract section list **from the reference draft itself**, then parallel section gen, then assemble. No enums.
- Reference-draft caching (same query → same reference → skip Stage 1 LLM judge call): defer until traffic data shows the cost
- Multi-reference blending (top-2 ES hits both useful → judge picks best vs. ask Stage 2 to blend): deferred, single-reference simpler
- Forum-specific cause-title (Bombay HC Appellate Side vs Delhi HC nuance from `drafting_intelligence_plan.md` §6.6): defer; if it matters, the reference draft already encodes the forum's conventions
- `LegalAgentState.user_facts` cleanup if no other agent consumes it after `_extract_case_facts` becomes drafting-internal: defer
