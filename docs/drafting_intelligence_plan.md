# Drafting Intelligence Plan

**Date:** 2026-06-22
**Author:** Architecture research synthesis
**Scope:** Reshape `agents/drafting.py` and its supporting infrastructure into a self-orchestrating, evidence-grounded, self-healing legal drafting agent that handles complex user-authored skeletons (e.g. the Avachat Writ of Mandamus) efficiently across all major Indian forums.

**Status:** Plan — not implementation. Awaiting user review before any code changes.

---

## 1. Vision

Move from a **fixed pipeline** that imagines structure and assembles boilerplate to a **self-orchestrating agent** that:

1. Reads the user's request and decides which stages to run
2. Honors user-authored structural contracts (Parts, paragraph counts, GROUNDS, PRAYER) verbatim
3. Pulls case facts from attached evidence PDFs **per section** via RAG, with provenance back to page/paragraph
4. Pulls case-law authority **per section** from Elasticsearch judgment indices, tailored to the section's specific doctrinal point
5. Validates the assembled draft against structural + factual + jurisdictional criteria; regenerates **only failing sections** rather than rewriting the whole thing
6. Adapts cause-title, verification, court-fee, affidavit, and annexure conventions to the **specific forum** (Bombay HC Appellate Side, Delhi HC, SC, ITAT, NCLT, CESTAT, GST Appellate, NCLAT, SAT, DRT/DRAT)

The system already has every primitive needed — `LegalAgentState` reducer, LangGraph `Send` for parallel fan-out, ChromaDB per-file collections, Gemini Flash + Pro, ES indices for judgments / legislation / newacts / constitution / maxims, a Pydantic typed `UserIntent` extractor, a self-refine loop. The work is **rewiring**, not greenfield.

---

## 2. Diagnosis Summary

From the three research passes, the binding facts:

### 2.1 The 60 KB guardrail is the binding production ceiling

[`agents/guardrail.py:38`](../agents/guardrail.py#L38) clamps every `final_response` to **60 000 chars** and appends a "Response truncated…" suffix. The orchestrator's own cap is 250 000 ([`agents/orchestrator.py:1877`](../agents/orchestrator.py#L1877)). For a 17-section writ that assembles to ~80–120 KB, the guardrail throws away the last third. The user sees a banner suggesting they "ask a narrower question" — wrong advice for a Drafting workload.

### 2.2 `_MAX_SECTIONS = 16` is applied twice

[`agents/drafting.py:58`](../agents/drafting.py#L58); enforced at [`drafting.py:1772`](../agents/drafting.py#L1772) (post-outline) and [`drafting.py:4539`](../agents/drafting.py#L4539) (post-procedural-injection). For the Avachat writ that needs 15 Parts + GROUNDS + PRAYER + 4 procedural blocks = 21 sections, the cap lops the last 5. PRAYER, Verification, List of Documents, Affidavit, Annexures Index are silently dropped.

### 2.3 Paragraph numbering is precomputed from estimates, never reconciled

[`drafting.py:3496-3504`](../agents/drafting.py#L3496-L3504): `start_para_offsets` is computed from `SectionPlan.estimated_paragraphs` **before any section LLM runs**. When actual paragraph count diverges from estimate, every downstream section's offset is wrong. No post-assembly renumber pass exists. The Avachat draft showed gaps at Sec 9 (jumps from 50→59), overlap at Sec 11 (restarts at 74 colliding with Sec 10), and local-numbering breakage at Sec 3 and 4 (the section LLM echoed the user's literal "PARAGRAPH DESCRIPTIVE 7 TO 13" notation instead of honoring the start offset).

### 2.4 No per-section retrieval over either evidence OR legal corpus

The doctrinal stance ([`drafting.py:2419`](../agents/drafting.py#L2419)) generates 3–6 anchor cases by **LLM imagination** — not retrieval. `_verify_stance_cases` ([`drafting.py:2504`](../agents/drafting.py#L2504)) is a post-hoc ES verifier; it can drop fabricated cases but cannot **add** cases the LLM failed to think of. The same 4 cases get cited across all 15 sections of the writ because that's the stance pool.

Evidence retrieval is worse. [`drafting.py:4181`](../agents/drafting.py#L4181) reads `fc.inline_text[:30000]` into a `user_facts` blob; every section call receives the same 30 KB chunk (re-truncated to 25 KB inside the section prompt at [`drafting.py:3149`](../agents/drafting.py#L3149)). Drafting **never reads `fc.chromadb_collections`** — they're dead code for this pipeline. The Document agent does use them; Drafting doesn't. So a 100-page evidence bundle that's been chunked and embedded sits unused while Drafting generates from a truncated blob.

### 2.5 ChromaDB write threshold is page-based, not content-based

[`file_processor.py:1288`](../core/file_processor.py#L1288): PDFs ≤ 20 pages AND ≤ 100 KB text never get chunked into ChromaDB. A 50-page evidence bundle that extracts to 80 KB has **zero retrievable chunks**. Collections are also per-file, not per-conversation — there's no aggregated "all evidence for thread X" collection.

### 2.6 Self-refine refines the entire draft, not the failing section

[`core/self_refine.py:817-847`](../core/self_refine.py#L817-L847): the critic audits the assembled draft against `UserIntent`; the refiner ([`self_refine.py:724`](../core/self_refine.py#L724)) rewrites the **entire response** via Gemini Pro at temperature 0.3. There is no section-level regenerate. If one section fails a structural rule, the entire ~80 KB draft is re-emitted — slow, expensive, and prone to introducing new issues in previously-good sections.

### 2.7 Critic checks intent compliance, not structural correctness

[`CRITIQUE_PROMPT`](../core/self_refine.py#L142) (~400 lines) audits language identity, script enforcement, response format, response depth, case-law inclusion. It does **not** check: all requested sections present? Paragraph counts honored? Procedural blocks present? Citation diversity? No empty paragraphs? No orphan citations? Structural validation is a different axis from intent compliance, and the system has neither.

### 2.8 Industry patterns we can adopt directly

Three patterns from the survey have outsized relevance:

- **Anthropic Contextual Retrieval** (Sept 2024) — prepending 50–100 token chunk-specific context before embedding reduces top-20 retrieval failure by 49% (BM25+embed) or 67% (with reranking). One-time preprocessing cost, ~$1.02 per million tokens with prompt caching. **Highest-ROI single change** for the evidence-RAG layer.
- **Evaluator-Optimizer + Orchestrator-Workers** ([Anthropic, "Building effective agents", Dec 2024](https://anthropic.com/research/building-effective-agents)) — orchestrator owns the section map, parallel workers draft, per-section evaluator gates each section, only failing nodes loop back. This **is** the "regenerate only failing parts" pattern we want; it's not novel.
- **EvenUp's structured-record-before-drafting** (evenuplaw.com/blog/document-ai) — extract case entities (parties, dates, events, evidence references) into a structured record with source links per field **before** drafting. The drafter then cites record rows. Closest public analog to "100-page evidence bundles → fact-grounded pleading." We already have `_extract_case_facts`; it just needs to become structured + provenance-bearing.

Reflexion's tool-grounded critique, CRITIC's tool-calling validators, and Anthropic's Citations API are all directly applicable.

---

## 3. Architecture Principles

These govern every phase:

### 3.1 Separation: LLM does reasoning, code does structure

Harvey's redesign moved state-machine work, XML/OOXML handling, and edit generation out of the LLM into deterministic code. We adopt the same line: the LLM owns legal reasoning (what to say); deterministic Python owns structure (where it goes, how it's numbered, what's mandatory). The current paragraph-numbering bug (LLM expected to honor `start_para_num` while also reading the user's prompt) is exactly the kind of "LLM-as-state-machine" failure Harvey called out.

### 3.2 User authorship is a contract, not a hint

When the user writes "PART A: <heading> PARAGRAPH DESCRIPTIVE 7 TO 13", that's a typed contract: section A, that heading, 7–13 numbered paragraphs. The outline LLM does **not** get to invent a different structure. We extract a `UserDraftSkeleton` typed object before the outline call and the outline becomes a **layout** of the user's intent, not an imagined structure.

### 3.3 Every claim has a source

When evidence PDFs are attached, every factual claim in the draft must trace to a quoted snippet from the evidence. Anthropic's Citations API gives us span-level citation natively; we lean on it for evidence-grounding rather than building our own provenance ledger.

### 3.4 Retrieve, don't imagine

Cases come from ES retrieval, not LLM memory. Statutes come from ES retrieval, not LLM memory. The doctrinal stance becomes a **retrieval-conditioned** structured output, not a free-form generation. The LLM's job is to **select and synthesize** retrieved material, not to **recall** it.

### 3.5 Validate structurally, regenerate targetedly

Structural correctness (every requested section present, every paragraph count met, procedural blocks present, no empty paragraphs, citation diversity, no orphan citations) is checked by deterministic validators. When a section fails, only that section regenerates. The whole-draft rewrite at iteration boundary is reserved for intent-level violations (language, format, depth) where structure is fine but tone or directives drift.

### 3.6 Pipeline shape adapts to request shape

Anthropic's "Building effective agents" warning is operative: don't reach for a complex framework before exhausting simple patterns. The router decides which stages run **based on the request**. A simple legal notice skips stance + per-section retrieval + procedural injection; a Bombay HC writ with attached evidence runs every stage. The pipeline molds; it doesn't force every request through the maximum shape.

### 3.7 Forum is a first-class profile

Procedural compliance for Bombay HC Appellate Side ≠ Delhi HC ≠ Supreme Court SLP ≠ ITAT written submission. A forum profile carries: cause-title format, verification clause text, court-fee rule, affidavit format, annexure nomenclature, prayer style. Outline + section + assembly all consult the profile. Forum is extracted from the user's query (LLM-driven, not regex) once at request start.

---

## 4. Target Architecture (the six shifts)

Each shift is a phase. Phases stack — Phase 1 ships independently; Phase N depends on prior phases as noted.

### 4.1 Shift 1 — Skeleton-aware intent extraction

**What:** A new `UserDraftSkeleton` Pydantic model extracted via Gemini Flash Lite (cheap, ~150 input tokens) from the user's prompt before the outline call. Carries:

```python
class UserDraftSection(BaseModel):
    label: str                    # "PART A", "GROUNDS", "PRAYER", or system-generated "Section 1"
    title: str                    # the heading text
    target_para_min: int          # honored verbatim, not "estimated"
    target_para_max: int
    must_appear: bool = True      # user-authored sections are non-negotiable
    section_role: Literal["substantive", "grounds", "prayer", "procedural"]
    description: str = ""         # optional content hint from the user

class UserDraftSkeleton(BaseModel):
    user_authored: bool           # True if extractor detected enumerated structure
    sections: list[UserDraftSection]
    forum: Optional[str]          # extracted from cause-title block, e.g. "Bombay HC Appellate Side"
    case_type: Optional[str]      # writ, plaint, bail, notice, ...
    language: Optional[str]       # extracted overrides user_intent.language
    procedural_blocks_expected: list[str]  # the system adds Verification, etc., if missing
```

**How:** A new extractor `_extract_user_skeleton(query: str) -> UserDraftSkeleton` in `agents/drafting.py` parallel to `_extract_case_facts`. Runs at request start. When `user_authored=True`, the outline LLM call is **skipped entirely** — `DraftOutline` is constructed deterministically from the skeleton.

**Downstream:** outline LLM call is conditional. Stance, per-section retrieval, parallel section gen, assembly all read the skeleton as ground truth.

### 4.2 Shift 2 — Dynamic pipeline router

**What:** A LangGraph router node that reads `(skeleton, file_context, intent, query)` and emits a `PipelinePlan`:

```python
class PipelinePlan(BaseModel):
    skip_outline_imagination: bool   # user_authored skeleton present
    run_evidence_rag: bool           # evidence PDFs attached and chunkable
    run_per_section_authority: bool  # court_filing | tribunal_appellate doc type
    run_stance: bool                 # any case-law citations expected
    run_procedural_injection: bool   # court_filing only
    run_self_refine: bool            # intent has directives
    enable_forum_profile: bool       # forum extracted; profile exists
    enable_section_validator: bool   # always true except trivial cases
    target_section_count: Optional[int]
    max_regenerate_iterations: int   # 0..3
```

**How:** Pure Python computation from inputs — no LLM call. Reads skeleton + file_context + intent + classified doc_type and computes the plan. Drafting becomes a LangGraph subgraph keyed off `PipelinePlan` rather than the current monolithic `drafting_node`.

**Why:** Anthropic's Orchestrator-Workers pattern. The router is the orchestrator; each enabled stage is a worker. Stages skip cheaply; the simple-case path (legal notice from skeleton) is 4 LLM calls instead of 12.

### 4.3 Shift 3 — Evidence RAG with provenance

**What:** Replace `user_facts[:25000]` per-section blob with per-section retrieval over a per-conversation evidence collection.

**Three components:**

**(a) Per-conversation Chroma collection** — a new `evidence_{thread_id}` collection populated at upload time, aggregating chunks from every uploaded PDF this thread. Replaces the per-file `inline_{thread_id}_{file_id[:8]}` collections for *evidence* purposes (the per-file collections remain for Document-agent Q&A).

**(b) Anthropic Contextual Retrieval** — at chunk-store time, generate a 50–100 token chunk-specific context ("This chunk is from the SIC order dated 08.06.2016 at page 4, discussing the directive to the Tahsildar to provide foundational documents") and prepend before embedding **and** before BM25 indexing. One-time cost, ~$1 per million tokens with prompt caching. Reduces top-20 retrieval failure 49–67%.

**(c) Per-section retrieval at section-gen time** — each `_generate_section` call queries the evidence collection with a section-specific query derived from the section's title + description + skeleton role. Top-k snippets are injected into the section prompt with explicit page/exhibit references, replacing the monolithic `user_facts` blob.

**Provenance:** chunk metadata carries `{file_id, file_name, page_number, paragraph_number, chunk_index}`. The section LLM is prompted to cite as "[Exhibit P-2, page 14, ¶3]" in body text. We adopt **Anthropic Citations API** when generating sections that need verbatim grounding — it gives sentence-level span citations automatically, eliminating fabricated references.

**Threshold change:** drop the page-based ChromaDB write threshold ([`file_processor.py:1288`](../core/file_processor.py#L1288)). All evidence chunks go in regardless of PDF size; the inline_text path becomes a small-doc convenience, not the only path.

### 4.4 Shift 4 — Per-section authority retrieval

**What:** Each substantive section, after the stance generates a broad doctrinal lane, queries the `judgements` and `supreme_court_judgement` indices with a section-tailored query and pulls 2–3 cases relevant to **that section's specific point**.

**Stance redesign:**

- `DoctrinalStance.key_cases` (currently 3–6 LLM-imagined cases) expands to `case_pool: list[DoctrinalCase]` with 12–20 cases, each tagged with `topic: list[str]` (e.g. `["mandamus", "Article 300A"]`, `["fraud-on-court", "void-ab-initio"]`).
- The stance LLM now generates a doctrinal **lane** + statutes; cases come from **retrieval-first** stance derivation: before the stance LLM call, we run topic-mapped ES queries against `judgements` based on the skeleton + facts and feed the retrieved cases as candidates the stance LLM can endorse, supplement, or drop.

**Per-section authority:**

- After outline → stance → procedural injection, **before** parallel section generation, a small dispatcher classifies each section's doctrinal topic (one Flash Lite call per section, parallelizable).
- Each section receives a `section_authority: list[Case]` derived by: (i) filter the stance `case_pool` by topic overlap; (ii) supplement with a top-3 ES retrieval per section against the judgments index. The section prompt receives this curated list with the explicit instruction "cite ONLY these cases; cite the doctrine without a case label if no case in this list fits the point."
- Citation diversity validator (Shift 5) enforces: no case appears in more than ⌈N/3⌉ sections (where N is total sections), preventing the same case being hammered across 15 sections.

**Note:** This is **retrieval, not invention**. The stance LLM's role shrinks from "imagine 4 cases" to "pick the right doctrinal lane and confirm retrieved cases fit"; the case material is from ES.

### 4.5 Shift 5 — Self-healing validation loop

**What:** A new `StructuralValidator` runs after `validate_draft` (which stays as cheap mojibake/citation cleanup). On any failure, the LangGraph subgraph emits `Send` events to regenerate **only the failing sections**, not the whole draft.

**Structural checks:**

```python
class StructuralViolation(BaseModel):
    section_index: int
    kind: Literal[
        "missing_section",            # skeleton requires; output missing
        "paragraph_count_under_min",  # section has fewer paragraphs than user requested
        "paragraph_count_over_max",
        "empty_paragraph",            # numbered paragraph with no body
        "duplicate_heading",          # title rendered twice
        "orphan_citation",            # "as held in." with no case
        "citation_overuse",           # same case used too many sections
        "forum_compliance",           # forum profile rule violated (e.g. missing verification)
        "evidence_unsourced",         # paragraph claims a fact not in retrieved evidence
    ]
    detail: str
    suggested_fix: str
```

**Regeneration policy:**

- `missing_section`, `paragraph_count_under_min`, `empty_paragraph`, `duplicate_heading`, `forum_compliance`, `evidence_unsourced` → regenerate the section with a sharpened prompt explaining the prior failure (Reflexion-style)
- `orphan_citation` → deterministic fix (remove the orphan tail) without regen
- `citation_overuse` → regenerate the offending section with `must_not_cite: [case_names]` in the prompt
- `paragraph_count_over_max` → deterministic fix (truncate to max; reflect renumber pass)

**Iteration cap:** 3 regenerate rounds, then accept and emit `draft_warnings`. The current `self_refine` loop stays as the **intent-level** refiner (language, format, depth) and runs **after** structural validation has converged.

**Renumber pass:** After all sections accept, a deterministic `_renumber_paragraphs(outline, sections)` walks substantive sections in order, counts actual `^\d+\.` paragraphs, rewrites globally. Procedural sections keep their own local schemes. Fixes Defect 3 from the bug report at the root.

### 4.6 Shift 6 — Forum-specific compliance profiles

**What:** A `ForumProfile` registry keyed by forum identity (extracted from skeleton or query):

```python
class ForumProfile(BaseModel):
    forum_id: str                              # "BHC_APPELLATE_SIDE"
    full_name: str                             # "Bombay High Court, Appellate Side"
    cause_title_template: str                  # markdown layout for the cause-title block
    party_block_format: str                    # "....Petitioner" vs "(Appellant)" vs "Yours sincerely"
    verification_clause: str                   # exact statutory wording
    affidavit_format: Optional[str]            # notarised affidavit template
    court_fee_rule: str                        # statutory citation + computation note
    annexure_naming: str                       # "Annexure-A", "Exhibit P-1", "Document-1"
    schedule_format: Optional[str]             # tabular schedule conventions
    prayer_style: Literal["a_b_c", "i_ii_iii", "numbered_inline"]
    procedural_blocks_required: list[str]      # forum-specific mandatory blocks
    citation_conventions: dict                 # judgment citation style for this forum
    body_register_rules: list[str]             # "It is most respectfully submitted that..." etc.
    language_default: str                      # "en" or regional
```

**Profiles to ship (Phase 6 scope = all major forums):**

High Court level: BHC Appellate Side, BHC Original Side, Delhi HC, Madras HC, Calcutta HC, Karnataka HC, generic HC fallback.

Supreme Court: SLP (Civil), SLP (Criminal), Writ (Article 32), Appeals, Curative.

Tribunals: ITAT, GST Appellate Tribunal, CESTAT, NCLT, NCLAT, DRT, DRAT, SAT, MAT (Maharashtra Administrative Tribunal), MACT (Motor Accident Claims Tribunal).

Other: Family Court, Consumer Forum (District/State/National), Labour Court, Sessions Court magistrate complaint.

**How:** Each profile is a Python data file in `config/forum_profiles/`, registered in a `FORUM_PROFILES: dict[str, ForumProfile]`. Forum extractor (Flash Lite) reads the cause-title block + query, returns `forum_id`. Outline, section, and assembly all consult the profile. The current `DraftOutline.court_details` layout selector (Layouts A–F embedded as 250-line field docstring in `drafting.py`) is **replaced** by profile lookup — profiles carry the layout, the docstring shrinks dramatically.

**Migration:** existing `doc_type` classifier ([`drafting.py:475`](../agents/drafting.py#L475)) maps onto `forum_profile.case_type`; the seven `DOC_TYPES` collapse into profile attributes. Backward compat preserved via the registry falling back to a `GENERIC_COURT_FILING` profile when forum extraction is uncertain.

---

## 5. Phase Plan

Six phases, sequenced for risk reduction and incremental user-visible improvement. Each phase ships independently and is testable in isolation.

### 5.1 Phase 1 — Mechanical fixes (1–2 days)

**Goal:** Eliminate the visible failures in the Avachat run without architectural change.

**Deliverables:**

| # | Change | File | Risk |
|---|---|---|---|
| 1.1 | Raise `MAX_FINAL_RESPONSE_CHARS` to 250 000 OR exempt Drafting from this cap | [`agents/guardrail.py:38`](../agents/guardrail.py#L38) | Low — orchestrator already caps at 250 K; we're just matching it |
| 1.2 | Raise `_MAX_SECTIONS` to 28; re-cap warn-only (don't lop) | [`agents/drafting.py:58`](../agents/drafting.py#L58), [`:1772`](../agents/drafting.py#L1772), [`:4539`](../agents/drafting.py#L4539) | Low — caps move up, behavior is additive |
| 1.3 | Strip leading `##` lines from `section_text` before assembler's `startswith("#")` check; always inject canonical heading | [`drafting.py:3946-3958`](../agents/drafting.py#L3946-L3958) | Low — defensive |
| 1.4 | Post-assembly global renumber pass (walks substantive sections, counts actual paragraphs, rewrites numbers; skips procedural sections by title keyword) | New function in [`drafting.py`](../agents/drafting.py) called from `_assemble_document` | Medium — regex over assembled text; needs unit tests |
| 1.5 | Detect empty `^\d+\.\s*$` paragraph lines in `validate_draft`; log warning + remove the orphan line | [`drafting.py:1822`](../agents/drafting.py#L1822) | Low |
| 1.6 | Remove `## {section_title}` echo from the section prompt; add explicit "DO NOT emit your own heading" instruction | [`drafting.py:3347`](../agents/drafting.py#L3347) | Low |

**Tests:** Reuse `tests/test_drafting_quality.py`; add 5 regression tests targeting each defect.

**User-visible after Phase 1:** Avachat re-run produces 17 substantive sections + procedural blocks, no truncation banner, no duplicate Section 4 heading, continuous paragraph numbering. Content quality (generic boilerplate, repeated cases) is unchanged.

### 5.2 Phase 2 — User skeleton + dynamic router (3–5 days)

**Goal:** Honor user-authored structural contracts; collapse the pipeline for simple requests.

**Deliverables:**

| # | Change | Files | Notes |
|---|---|---|---|
| 2.1 | `UserDraftSkeleton` Pydantic model + extractor | New: `config/user_skeleton.py`, extractor in `agents/drafting.py` | Single Flash Lite call, structured output |
| 2.2 | Outline construction from skeleton (skips outline LLM) | `agents/drafting.py:_generate_outline` | Conditional on `skeleton.user_authored` |
| 2.3 | `PipelinePlan` router computed pre-pipeline | New: `agents/drafting/router.py` | Pure Python, no LLM |
| 2.4 | LangGraph subgraph keyed off `PipelinePlan` (each stage becomes a node; conditional edges skip skipped stages) | `core/graph.py`, refactor `drafting_node` into subgraph | Bigger surgery; mitigated by isolating Drafting subgraph behind the existing graph edge |
| 2.5 | Skeleton parser supports the "PART X / PARAGRAPH N TO M / GROUNDS / PRAYER" notation patterns from Indian legal drafting tradition | The extractor LLM prompt | Trained-by-example, no regex |
| 2.6 | Skeleton field `procedural_blocks_expected` triggers deterministic injection of Verification + Affidavit + List of Documents + Annexures Index when forum requires them | New: `_inject_skeleton_procedural` | Replaces stance-driven procedural injection for skeleton-authored requests |

**Tests:** New `tests/test_user_skeleton.py` covering 15+ skeleton patterns (Indian writ, Hindi-language outline, Marathi mixed, English with Marathi terms, partial skeleton, no skeleton).

**User-visible after Phase 2:** Avachat re-run honors his exact Parts A–O sequence with their stated paragraph counts; GROUNDS appears as a discrete 40–50 paragraph section; PRAYER appears with proper (a)/(b)/(c) numbering; simple legal notice request takes ~12s instead of ~45s.

**Phase 1 dependency:** Yes — needs the section-cap and renumber pass.

### 5.3 Phase 3 — Evidence RAG with provenance (1 week)

**Goal:** Per-section retrieval over a 100-page evidence bundle with page/paragraph citations.

**Deliverables:**

| # | Change | Files | Notes |
|---|---|---|---|
| 3.1 | Per-conversation `evidence_{thread_id}` Chroma collection; lifecycle tied to thread (created on first upload, deleted on thread cleanup) | `core/file_processor.py`, `core/chat_store.py` | New collection separate from per-file collections |
| 3.2 | Drop page-based write threshold; all evidence chunks land in Chroma | `core/file_processor.py:1288` | Behavior change: now even small PDFs are retrievable |
| 3.3 | Anthropic Contextual Retrieval: at chunk-store time, generate 50–100 token chunk-specific context via Flash Lite + prompt caching; prepend to chunk text before embedding | New: `core/contextual_retrieval.py` | One-time cost per upload, ~$1/M tokens with caching |
| 3.4 | Chunk metadata schema: `{file_id, file_name, page_number, paragraph_number, chunk_index, exhibit_label}` | `core/file_processor.py`, `tools/shared/vectordb_tools.py` | Required for citation rendering |
| 3.5 | `_extract_section_evidence(section, evidence_collection, k=5)` helper called per section in parallel generation | `agents/drafting.py` | Replaces the monolithic `user_facts` blob inside section prompts |
| 3.6 | Section prompt updated: receives `evidence_snippets` block with explicit `[Exhibit P-N, page X, ¶Y]` citations; instructed to cite inline | `agents/drafting.py:3344-3380` | Adds a new prompt slot; preserves existing structure |
| 3.7 | Per-conversation collection deletion wired to `delete_thread_files` and the existing `DELETE /pyapi/delete_vectordb/{unique_string}` endpoint | `core/gateway.py`, `core/chat_store.py` | Closes lifecycle gap noted in evidence map |
| 3.8 | Optional: Anthropic Citations API path for sections that need verbatim factual grounding | `agents/drafting.py:_generate_section` | Feature-flagged; falls back to manual citation rendering when Citations API unavailable |

**Tests:** `tests/test_evidence_rag.py` with synthetic 50-page evidence bundle; checks per-section retrieval pulls relevant snippets; checks page/paragraph citations render correctly; checks contextual retrieval improves recall vs baseline.

**User-visible after Phase 3:** Avachat re-run with attached SIC order + prior writ judgment + ADC order PDFs produces sections that quote specific paragraphs of those documents with `[Exhibit P-1, page 4, ¶3]` citations. Generic mandamus boilerplate gives way to fact-specific argumentation. This is the single biggest quality jump.

**Phase 1, 2 dependency:** Yes — needs structural pipeline + skeleton-aware section routing.

### 5.4 Phase 4 — Per-section authority retrieval (3–5 days)

**Goal:** Each section cites cases specific to its doctrinal point; citation diversity enforced.

**Deliverables:**

| # | Change | Files | Notes |
|---|---|---|---|
| 4.1 | Stance redesign: `case_pool` (12–20 cases tagged by topic) instead of `key_cases` (3–6 cases) | `agents/drafting.py:2334-2336` | Schema bump; migration shim for old stance objects in continuation state |
| 4.2 | Retrieval-first stance derivation: before stance LLM call, retrieve candidate cases from `judgements` and `supreme_court_judgement` indices keyed by skeleton + facts | New: `agents/drafting/stance_retrieval.py` | Stance LLM endorses retrieved candidates, not blank-page imagine |
| 4.3 | Per-section topic classifier (Flash Lite, one call per section, parallelizable) | `agents/drafting.py` | Maps section title + description to topic tags matching stance case-pool tags |
| 4.4 | Per-section ES retrieval: filter stance pool by topic + supplement with top-3 ES query targeted at the section's argument | `agents/drafting.py` | Builds `section_authority` per section |
| 4.5 | Section prompt receives `section_authority` instead of stance `key_cases`; instruction tightened: cite ONLY these cases, OR cite doctrine without label | `agents/drafting.py:3419-3427` | Existing fallback already in place; reuse pattern |
| 4.6 | Citation diversity validator: post-assembly check that no case appears in > ⌈N/3⌉ sections; offending sections re-roll with `must_not_cite: [...]` | New: part of Shift 5 structural validator | Cross-cuts with Phase 5 |

**Tests:** `tests/test_authority_retrieval.py` covering: stance retrieval finds real corpus cases; per-section retrieval differs across sections of one draft; diversity validator catches overuse.

**User-visible after Phase 4:** Avachat re-run cites 25–40 distinct cases across the writ, each matched to its section's specific doctrine (Article 300A cases in property sections, Lalita Kumari for FIR refusal, fraud-on-court cases for civil litigation misuse, contempt cases for HC order defiance). No more 4-case monopoly.

**Phase 1, 2, 3 dependency:** Yes — needs structural pipeline; benefits from skeleton-extracted forum identity for jurisdiction-specific case selection.

### 5.5 Phase 5 — Self-healing validation loop (3–5 days)

**Goal:** Structural correctness enforced deterministically; only failing sections regenerate.

**Deliverables:**

| # | Change | Files | Notes |
|---|---|---|---|
| 5.1 | `StructuralValidator` class with checks listed in §4.5 | New: `agents/drafting/structural_validator.py` | Pure Python validators, no LLM |
| 5.2 | Section-level regeneration: targeted re-run of `_generate_section` with sharpened prompt explaining prior failure (Reflexion-style natural-language post-mortem prepended) | `agents/drafting.py` | Mirrors existing `continue_draft_node` pattern; reuses retrieval + stance |
| 5.3 | Regenerate orchestrator: collects violations, dispatches per-section regen via LangGraph `Send`, accumulates new sections, re-runs validator | New: `agents/drafting/regen_orchestrator.py` | Cap 3 iterations |
| 5.4 | Deterministic fixes: orphan citation removal, paragraph truncate-to-max, duplicate heading collapse — applied without LLM | `agents/drafting.py:validate_draft` | Cheap; pre-empts regen for fixable issues |
| 5.5 | Self-refine downgrade: existing `core/self_refine.py` loop becomes the **intent-level** refiner, running after structural validator converges | `core/self_refine.py`, `agents/drafting.py:4628-4646` | Keeps the language/format/depth audit; removes redundancy with new validator |
| 5.6 | Telemetry: log per-violation type, iteration count, success rate per check kind | `core/logger.py` integration | For tuning thresholds post-launch |

**Tests:** `tests/test_structural_validator.py` covering each violation type + regeneration; `tests/test_drafting_regen.py` with seeded failures to verify only failing sections re-run.

**User-visible after Phase 5:** Drafts emerge structurally complete on first delivery; visible iteration count in `draft_warnings` for transparency; failure rate on user-visible structural defects drops to near zero.

**Phase 1, 2 dependency:** Yes — needs skeleton (for "missing_section" check) and renumber pass (so validator knows actual numbering).

### 5.6 Phase 6 — Forum-specific profiles (1 week)

**Goal:** Procedural compliance per forum across all major Indian forums.

**Deliverables:**

| # | Change | Files | Notes |
|---|---|---|---|
| 6.1 | `ForumProfile` schema and registry | New: `config/forum_profiles/__init__.py` + one `<forum>.py` per profile | Pythonic registration via decorator |
| 6.2 | Forum extractor (Flash Lite, one call per request) reads cause-title + query, returns `forum_id`; fallback to `GENERIC_COURT_FILING` on uncertainty | New: `agents/drafting/forum_extractor.py` | ~150 input tokens; cacheable per query |
| 6.3 | Outline construction consults profile for procedural blocks, prayer style, language defaults | `agents/drafting.py:_generate_outline` (or skeleton-deterministic path) | |
| 6.4 | Section generation consults profile for body register rules, citation conventions | `agents/drafting.py:_generate_section` | New prompt slot for `forum_block` |
| 6.5 | Assembly consults profile for cause-title layout, party block format, footer | `agents/drafting.py:_assemble_document` | Replaces inline Layout A–F docstring |
| 6.6 | Structural validator's `forum_compliance` checks (Phase 5) become profile-driven | `agents/drafting/structural_validator.py` | Each profile declares its checks |
| 6.7 | Ship 25 profiles: 7 HC (BHC Appellate, BHC Original, Delhi, Madras, Calcutta, Karnataka, generic), 5 SC (SLP-C, SLP-Cri, Writ-32, Appeal, Curative), 9 tribunals (ITAT, GST App, CESTAT, NCLT, NCLAT, DRT, DRAT, SAT, MAT, MACT), 4 other (Family, Consumer, Labour, Sessions magistrate) | 25 files in `config/forum_profiles/` | Sourced from official forum rules + practitioner manuals |
| 6.8 | Profile docs: each profile carries `__doc__` with source citations to forum rules | Inline | For auditability + future updates |

**Tests:** `tests/test_forum_profiles.py` with one e2e per major forum; verifies cause title, verification clause, prayer style, mandatory procedural blocks render correctly per profile.

**User-visible after Phase 6:** Bombay HC writ uses Bombay HC Appellate Side Rules 1980 conformant cause title and verification; ITAT submission uses Form 35 conventions; NCLT petition uses NCLT-AT format. Procedural compliance is forum-correct, not generic.

**Phase 1, 2, 5 dependency:** Yes — needs skeleton (for forum hints), structural pipeline, and validator (for compliance enforcement).

---

## 6. Cross-Cutting Work

### 6.1 Evaluation framework

The current `tests/test_drafting_quality.py` (24 unit + 8 e2e) covers the existing invariants. We extend with:

- **Skeleton-honor suite** — 50+ synthetic skeletons (Indian writ, Marathi notice, Hindi plaint, English with vernacular terms, partial skeletons, conflicting skeletons); pass = output structure matches skeleton.
- **Evidence-grounding suite** — 20+ scenarios with attached evidence bundles + a ground-truth list of facts the draft must cite from evidence; pass = recall threshold met.
- **Authority diversity suite** — long-form drafts (15+ sections); pass = no case used more than ⌈N/3⌉ times AND each section cites at least one case relevant to its topic.
- **Forum compliance suite** — one drafting scenario per shipped forum profile; pass = profile's structural validator runs to convergence.
- **Regression suite** — every prior bug pinned as a test (truncation banner, MAX_SECTIONS, paragraph numbering, duplicate heading, empty paragraph, PRAYER drop).

**Eval harness:** existing `tests/test_drafting_quality.py` pattern (sync helpers + async e2e gated by `DRAFTING_QUALITY_E2E=1`). Add per-phase eval gates to CI.

### 6.2 Observability

- New SSE events: `pipeline_plan_decided`, `evidence_retrieval_done` (per section), `authority_retrieval_done` (per section), `structural_violation_found`, `section_regenerating`, `section_regenerated`, `forum_profile_applied`.
- Per-stage timing emitted to logger.
- Per-violation telemetry to chat_store metadata for post-hoc analysis.
- Frontend can render a real-time progress checklist using the existing `drafting_progress` events plus the new ones.

### 6.3 Migration & rollback

User authorized breaking changes — we use it cleanly:

- Old `drafting_node` deprecated; new subgraph entry point.
- `LegalAgentState` gains `user_skeleton`, `pipeline_plan`, `forum_profile`, `evidence_collection_id`, `structural_warnings` fields. Existing state shape compatible (additive).
- `DoctrinalStance.key_cases` renamed to `case_pool` with topic tags — migration shim in continuation-state restoration (`draft_continuation`).
- Each phase is independently revertable via feature flag (env: `DRAFTING_USE_SKELETON`, `DRAFTING_USE_EVIDENCE_RAG`, etc.) for the first two weeks after each phase ships, then removed.
- Old `_MAX_SECTIONS = 16` stays as a sanity ceiling at 28 — additive, not a removal.

### 6.4 Cost model

Rough delta per draft (relative to current pipeline):

| Component | Current | After all phases | Delta |
|---|---|---|---|
| Doc-type classify | 1 call (Flash Lite) | 1 call (Flash Lite, replaced by forum extract) | 0 |
| Skeleton extract | 0 | 1 call (Flash Lite) | +1 |
| Forum extract | 0 | 1 call (Flash Lite) | +1 |
| Pipeline plan | 0 | 0 (pure Python) | 0 |
| Case-fact extract | 1 call (Flash) | 1 call (Flash) | 0 |
| Template search/select | 1 ES + 1 Flash | 1 ES + 1 Flash | 0 |
| Contextual chunk store | 0 | 1 Flash Lite per chunk (one-time per upload, cached) | One-time |
| Outline | 1 call (Flash) | 0 when skeleton authored, 1 otherwise | -0.5 avg |
| Stance | 1 call (Flash) | 1 call (Flash) + retrieval | 0 |
| Per-section topic classify | 0 | N calls (Flash Lite, parallel) | +N |
| Per-section evidence retrieval | 0 | N Chroma queries | minimal |
| Per-section authority retrieval | 0 | N ES queries | minimal |
| Parallel section gen | N calls (Flash) | N calls (Flash) | 0 |
| Validate | 0 (Python) | 0 (Python) | 0 |
| Structural regenerate | 0 | 0–3 × M calls (Flash, M=failing sections) | +iter*M |
| Self-refine | 0–2 calls (Flash critic + Pro refiner) | 0–2 calls (same, smaller surface) | 0 |

Net per draft: ~+3 LLM calls (skeleton, forum, contextual one-time) + ~N Flash Lite topic classifications + iteration regens (typically 0–1 per draft when validators are good).

Per-token cost: Flash Lite is ~10× cheaper than Flash; the N topic calls are ~$0.001 per draft. Contextual retrieval one-time cost is per-upload, prompt-cached; ~$1 per 1M tokens of evidence. **Net cost increase is small relative to quality gain.**

---

## 7. Sequencing & Critical Path

Optimal order is the phase numbering (1 → 2 → 3 → 4 → 5 → 6). Justification:

- **Phase 1 ships in 1–2 days** and gives an immediate user-visible improvement (no truncation, full draft length). Independent of all other phases; can ship today as a quick win while planning continues.
- **Phase 2 unblocks Phases 3–6.** The skeleton + router are the structural foundation everything else builds on.
- **Phase 3 (evidence RAG) is the biggest quality lever.** Should land before Phase 4 because per-section authority retrieval is easier to reason about when per-section evidence retrieval is already working (same dispatcher pattern).
- **Phase 4 polishes Phase 3.** Once each section has the right facts, getting it the right cases is the natural next step.
- **Phase 5 (self-healing) depends on Phase 2 (knowing what was expected) and benefits from Phases 3/4 (so violations are about *structure*, not *content*).** Goes after 4.
- **Phase 6 (forum profiles)** is the highest-value differentiator but largest scope. Goes last because it depends on Phase 5's structural validator for enforcement, and on Phase 2's skeleton for forum identity.

**Total timeline:** 4–6 weeks for one engineer, assuming standard reviews and no major scope changes.

**Parallelizable work** (if two engineers): Phase 1 + Phase 2 in week 1; Phase 3 + Phase 6 profile catalog in weeks 2–3; Phase 4 + Phase 5 in week 4; Phase 6 integration in weeks 5–6.

---

## 8. Success Metrics

Each phase has a measurable gate.

| Phase | Metric | Target | Method |
|---|---|---|---|
| 1 | Avachat re-run: visible truncation banner | 0 occurrences | Manual smoke + regression test |
| 1 | Drafts ≥ 60K chars accept on first try | 100% | Histogram of `final_response_len` |
| 1 | Section count required by skeleton vs delivered | match | Regression test |
| 2 | Skeleton extraction accuracy on labeled test set | ≥ 95% | New eval suite |
| 2 | Simple legal notice end-to-end latency | ≤ 15s | p50 latency metric |
| 3 | Per-section evidence recall on labeled fact list | ≥ 80% top-5 | Evidence-grounding suite |
| 3 | Drafts with attached evidence containing zero `[Exhibit ...]` citations | 0% | Citation rendering check |
| 4 | Case diversity (max single-case section occupancy) | ≤ ⌈N/3⌉ | Citation diversity suite |
| 4 | % sections with at least one corpus-verified case citation | ≥ 90% | Authority retrieval suite |
| 5 | Drafts emerging structurally-valid on first pass | ≥ 80% | Validator hit rate |
| 5 | Avg structural-regeneration iterations per draft | ≤ 1.0 | Telemetry |
| 6 | Forum-compliance pass rate per forum profile | ≥ 95% | Forum compliance suite |
| 6 | Per-forum smoke test pass | 100% | E2E suite |

---

## 9. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Skeleton extractor over-fits Indian writ notation, misses other patterns | Medium | Medium | Train-by-example with 50+ skeleton variants in extractor prompt; structured-output Pydantic catches obvious failures |
| Per-conversation evidence collection lifecycle leaks (Chroma collections orphaned) | Medium | Low | Wire deletion to `delete_thread_files`; weekly Chroma orphan-collection sweeper |
| Contextual retrieval prompt-cache miss → cost balloon | Low | Medium | Monitor cache hit rate; batch chunks by source document to maximize cache reuse |
| Per-section authority retrieval returns unrelated cases due to weak query construction | Medium | Medium | Reranker pass post-retrieval; structural validator catches "no cited case on topic" and re-queries |
| Forum profiles drift from actual forum rules over time | High (long-term) | Medium | Each profile carries source citations in docstring; quarterly review against forum rule updates |
| Section regeneration iterates indefinitely on a hard-to-satisfy violation | Low | Medium | Hard cap at 3 iterations; warning emitted in `draft_warnings`; user can manually trigger continue_draft |
| LangGraph subgraph refactor breaks existing chat / search / file Q&A paths | Medium | High | Drafting subgraph isolated behind existing graph edge; tests for non-drafting paths in CI |
| Anthropic Citations API unavailable for our Gemini-primary stack | Certain | Low | Fall back to manual citation rendering using chunk metadata; Citations API is a v2 enhancement |
| Eval harness can't generate realistic legal drafts at scale for testing | Medium | Medium | Curate ~100 real anonymized drafts from prior threads as ground truth; semi-supervised eval |

---

## 10. Open Questions for User

Before I start Phase 1, four decisions:

1. **Phase 1 quick-ship policy:** the Phase 1 fixes are independently useful and ship in 1–2 days. Do I ship them as a standalone PR (recommended) or hold for the full plan to be approved end-to-end?

2. **Contextual Retrieval cost authorization:** Phase 3 adds ~$1 per 1 M tokens of evidence (one-time per upload) for Anthropic contextual chunk preprocessing. For a typical thread with 500 KB of evidence, that's ~$0.001. Approved as a baseline cost?

3. **Forum profile sourcing — who validates the profiles?** Phase 6 ships 25 profiles. Each needs validation against actual forum rules. Options:
   - (a) I sole-source them from publicly available forum rules + practitioner manuals (faster, risk of inaccuracy)
   - (b) Validated by a domain SME (slower, higher accuracy)
   - (c) Ship first 7 (top HCs + SC + ITAT + NCLT), defer the rest until usage data shows demand

4. **Backwards compatibility for `continue_draft`:** the `draft_continuation` state field changes shape in Phase 2 (new fields) and Phase 4 (`key_cases` → `case_pool`). Should we migrate in-flight continuation drafts (write a one-shot migration when state is loaded) or invalidate them (any in-flight draft must restart)?

---

## 11. What "smart, dynamic, self-healing" means after this plan ships

Concretely, after all phases:

- **Smart:** every stage's input is conditioned on retrieved evidence + retrieved authority, not LLM imagination. The system knows what cases exist, what the user's PDFs say, what the forum requires.
- **Dynamic:** the pipeline shape adapts to the request. Legal notice from a 2-line prompt: 4 LLM calls, ~10 seconds. Bombay HC writ from a 60-line skeleton with 100-page evidence: 30+ LLM calls, ~90 seconds, but every paragraph is fact-grounded and authority-cited.
- **Self-healing:** structural defects are detected deterministically, only failing sections re-run, the user sees the corrected draft. The 60K char truncation, missing PRAYER, broken numbering, empty paragraphs, duplicate headings — none of these reach the user.
- **Flexible / molding:** stages skip themselves cheaply when the request doesn't need them. The same code path handles a 5-section bail application and a 25-section writ with attached evidence.
- **Advanced / dynamic prompting:** prompts assembled per-call from the skeleton, the forum profile, the retrieved evidence, the retrieved authority, the structural failure post-mortems (Reflexion-style). No more 250-line static prompt that the LLM has to selectively obey.
- **Understanding:** the system reads the user's prompt as a *contract* (skeleton extraction), the user's evidence as *facts* (per-section retrieval), the user's forum as a *constraint* (profile), and the user's intent as *directives* (existing UserIntent layer). It doesn't impose its own structure on top.

The user's Avachat writ, re-run after all phases, produces a court-ready first draft with:
- 15 Parts + GROUNDS + PRAYER + 4 procedural blocks (Verification, Affidavit, List of Documents, Annexures Index) — all present, in user's specified order, with user's specified paragraph counts
- Bombay HC Appellate Side cause-title format, verification clause, prayer (a)/(b)/(c) style
- Specific facts from the attached SIC order, prior writ judgment, ADC order — cited as `[Exhibit P-1, page 4, ¶3]`
- 25–40 distinct case citations, matched to each section's specific doctrinal point
- Continuous paragraph numbering, no duplicate headings, no empty paragraphs, no orphan citations
- ~85–110 KB output, no truncation, delivered in a single response

That's the target. The plan above gets us there.
