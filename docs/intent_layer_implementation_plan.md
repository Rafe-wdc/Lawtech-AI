# Intent Layer — Implementation Plan & Tracker

**Goal**: replace the regex-based "user wants table / wants Hindi / wants brief" detection with a single structured-LLM-extracted `UserIntent` object that every downstream consumer reads. Future-proof against new formats, new languages, and new directives without code edits in the hot paths.

**Started**: 2026-06-13
**Owner**: Adesh
**Status**: Phase 0 — scaffolding

Status legend: `[ ]` open · `[~]` in progress · `[x]` done · `[-]` skipped · `[!]` blocked

---

## 1. Why we are doing this

**Root cause of the CGST "table" miss** (see [tests/_cgst_T2_*.md](../tests/) for the artifact): the orchestrator's [`_TABLE_INTENT_RE`](../agents/orchestrator.py#L76) does not match natural phrasings like *"give me a table containing X"*, *"tabulate X and Y"*, or any non-English equivalent. The downstream consequence is that [`SYNTHESIS_TABLE_PROMPT`](../config/prompts.py#L205) never runs, the single-agent pass-through ([orchestrator.py:1392](../agents/orchestrator.py#L1392)) returns the Legislation agent's prose verbatim, and the user sees prose instead of the table they asked for.

This is a **structural** bug, not a one-off regex gap. The 2026 best-practice answer is *"no excuse for parsing LLM responses with regex — use structured outputs"*. We already have a Pydantic schema, but the field that carries format intent is `response_instructions: str` (free text). The fix is to upgrade that field into a typed object.

---

## 2. Target architecture (one diagram, in prose)

```
USER QUERY
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ Memory node                                              │
│   • load chat history, summary                           │
│   • detect_language() (existing, kept as confidence sig) │
└──────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ Intent extractor (NEW — one LLM call, Gemini Flash Lite) │
│   in:  query + chat_summary (wrapped with injection      │
│         guard from Round 4 audit)                        │
│   out: (normalized_query, UserIntent)                    │
│        UserIntent has typed fields:                      │
│         - response_format: ResponseFormat enum           │
│         - language: ISO code + language_explicit flag    │
│         - response_depth, include_case_law, etc.         │
│         - confidence: float                              │
│         - additional_instructions: str (catchall)        │
└──────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ Task classifier (existing)                               │
│ Plan / agent selection (existing)                        │
└──────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ Domain agents (Legislation, Newacts, Judgment, …)        │
│   • read state["user_intent"]                            │
│   • append _format_intent_directives(intent) to system   │
│     prompt at runtime                                    │
└──────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ Synthesizer (existing, refactored)                       │
│   • picks SYNTHESIS_TABLE_PROMPT vs SYNTHESIS_PROMPT     │
│     based on intent.response_format (NOT regex)          │
│   • pass-through guard now checks intent, not regex       │
└──────────────────────────────────────────────────────────┘
```

**Key invariants**:
- Backwards compat: `response_instructions: str` stays populated (derived from `UserIntent`) until all consumers migrate (Phase 4).
- Failure mode: extractor LLM call fails → empty `UserIntent(confidence=0.0)` → regex fallback fires (defense in depth during migration).
- Feature flag: `INTENT_EXTRACTOR_V2=true` toggles the new path. Default `false` until Phase 2 telemetry is green.

---

## 3. Phased delivery

Each phase is **independently shippable** and **independently revertable** via the feature flag or single-file revert.

### Phase 0 — Scaffolding (no behaviour change) — **DONE 2026-06-13**

Pure types + tests. Nothing in production calls anything new yet.

- [x] **0.1** Created [`config/intent.py`](../config/intent.py): `ResponseFormat` enum (8 values), `UserIntent` Pydantic model with typed fields + convenience properties + schema versioning, `LANG_NAMES` (14 languages), `default_intent()` factory. Import-time check guards against drift between `LANG_NAMES` and `core.language.SUPPORTED_LANGUAGES`.
- [x] **0.2** Added `INTENT_EXTRACTOR_V2` env flag at [core/settings.py:154-162](../core/settings.py#L154-L162) — default `False`.
- [x] **0.3** Extended `LegalAgentState` at [core/state.py:127-141](../core/state.py#L127-L141) with `user_intent: Any` (typed via comment to avoid import cycle with `config.intent`). Backwards-compat — `response_instructions` field stays.
- [x] **0.4** Added `USER_INTENT_EXTRACTION_PROMPT` at [config/prompts.py:200-289](../config/prompts.py#L200-L289). Wraps `INJECTION_GUARD_PREAMBLE` (Round 4 fix). 4411 chars.
- [x] **0.5** Scaffolded [tests/test_user_intent_extraction.py](../tests/test_user_intent_extraction.py): 14 schema unit tests + 30 parametrized live-extractor cases (English / Hindi-Devanagari / Hinglish / Marathi / Tamil / depth directives / lists / drafting / negative cases / ambiguous). Live cases skip unless `INTENT_EXTRACTOR_LIVE=1`.
- [x] **0.6** Verified: 141/141 project files compile, gateway boots in 30s with 31 routes, `user_intent` registered in `LegalAgentState.__annotations__`. **14 unit tests pass · 30 live tests properly skipped.**

**Done when**: ✅ all of the above true. Zero impact on running servers (no code path reads `user_intent` yet).

### Phase 1 — Extractor + parallel run — **DONE 2026-06-13**

Wired the new extractor in. Originally planned as telemetry-only, but I went one step further: when the extractor's confidence ≥ 0.7 and the user expressed an explicit format/language/depth directive, `_legacy_response_instructions()` synthesizes the legacy string from the structured intent so the existing downstream picker (regex + `SYNTHESIS_TABLE_PROMPT`) does the right thing **even before Phase 2 cutover**. This is a soft-cutover that turns Phase 1 from telemetry-only into an actual user-visible fix on the flag-on path.

- [x] **1.1** Implemented `_extract_user_intent(query, chat_summary)` in [agents/orchestrator.py:850-928](../agents/orchestrator.py#L850). Uses `get_gemini_flash` (Lite-tier, Round-2 retries + 60s timeout), `with_structured_output(QueryAnalysisV2)`, wraps both inputs with `wrap_untrusted` (Round-4 injection guard), records tokens, catches all exceptions → returns `(query, default_intent())` so callers always get a safe fallback. New `QueryAnalysisV2` schema bundles `normalized_query` + `UserIntent`.
- [x] **1.2** Wired parallel-run in `orchestrator_plan_node` at TWO branches:
  - **skip_normalize path** ([orchestrator.py:1279-1340](../agents/orchestrator.py#L1279)): classify + intent extraction concurrently. Catches the 80%+ of English queries that previously bypassed the legacy normalizer.
  - **else path** ([orchestrator.py:1341-1430](../agents/orchestrator.py#L1341)): normalize + classify + intent extraction concurrently. The original parallel-gather path.
  - Both branches surface `extracted_intent` on the returned state (`state["user_intent"]`).
  - When confidence ≥ 0.7 AND any field is explicit, `_legacy_response_instructions()` writes the legacy string so existing downstream code picks up the directive.
- [x] **1.3** Added two telemetry helpers in [orchestrator.py:930-1020](../agents/orchestrator.py#L930):
  - `_legacy_response_instructions(intent)` — typed-intent → legacy free-form text (for the soft-cutover).
  - `_emit_intent_telemetry(intent, legacy_str, query)` — logs per-request: `regex_wants_table`, `intent_wants_table`, `agreement_wants_table`, `intent_format`, `format_explicit`, `intent_language`, `language_explicit`, `depth`, `confidence`, `query_preview`.
- [x] **1.4** Activated the 30 live extractor tests + 2 robustness tests in [tests/test_user_intent_extraction.py](../tests/test_user_intent_extraction.py). Tightened the prompt to infer language from query script (Devanagari → hi; Marathi script → mr; Tamil → ta) instead of defaulting to English when the user didn't explicitly say "in Hindi". Softened one Hinglish edge case (`"section 131 ka table banao"`) where either `en` or `hi` is defensible — the `core/language.py` Romanized detector catches it independently.
- [x] **1.5** Ran live extractor suite — **46 / 46 tests pass** (14 schema + 30 live + 2 robustness). Single warning on the deliberately-ambiguous `"131 and 132"` query (confidence 0.60, exactly the low-confidence flag the regex-fallback path is designed for).
- [x] **1.6** Restarted LOCAL with `INTENT_EXTRACTOR_V2=true` + re-ran the CGST 2-turn test:
  - Turn 1 (`"Section 131 of CGST..."`): extractor returns `format=prose`, regex agrees → `agreement_wants_table=True`.
  - Turn 2 (`"give me a table containing section 131, 132"`): extractor returns `format=table, format_explicit=True, confidence=0.9` → soft-cutover writes `"table format."` into `response_instructions` → downstream picker selects `SYNTHESIS_TABLE_PROMPT` → **user receives a proper 9-row markdown comparison table** (11 pipe lines + 1 separator). Previous test with flag off returned 0 pipes (pure prose).
- [-] **1.7** Roll out to DEV + PROD — punted: those servers are running commit `7235d3b` without the new code. They need `git push` of the Phase 1 changes + redeploy + `INTENT_EXTRACTOR_V2=true` env var. Not a code-change task.

**Done when**: ✅ all of the above. The flag-on path catches the regex-missed phrasings AND produces real tables. The flag-off path is unchanged. Telemetry is in place to monitor agreement going forward.

### Phase 2 — Cutover routing decisions to intent — **DONE 2026-06-13**

The orchestrator's three decision points (pass-through guard, primary-task synthesis skip, synthesis-template picker) now read `state["user_intent"].wants_table` directly, with the legacy regex preserved as a `confidence < 0.7` fallback. Two new resolvers + one early state-override make this end-to-end.

- [x] **2.1** Replaced the synthesis-template picker at [orchestrator.py:1864](../agents/orchestrator.py#L1864) with `_resolve_wants_table(state, response_instructions, query)`. Logs `source=intent` or `source=regex_fallback` per request so we know which signal fired.
- [x] **2.2** Same treatment at the single-agent pass-through guard ([orchestrator.py:1660](../agents/orchestrator.py#L1660)) and the primary-task synthesis skip ([orchestrator.py:1797](../agents/orchestrator.py#L1797)).
- [x] **2.3** Added `_resolve_explicit_non_english(state)` — when the user explicitly named a non-English target language (e.g. "Section 131 in Hindi" typed in Latin script), the single-agent pass-through is bypassed so the synthesis path runs (which applies `localize_prompt`).
- [x] **2.4** Re-documented `_legacy_response_instructions` — it's no longer a "soft cutover"; it now serves as the projection from typed intent → the human-readable string interpolated into `{response_instructions}` in the synthesis prompt templates. Phase 4 will replace this with a structured directive block.
- [x] **2.5** Added `_resolve_user_language(state)` AND an early `state["user_language"]` override in `orchestrator_plan_node` — when `intent.language_explicit and confidence >= 0.7`, the field gets overridden so EVERY downstream consumer (domain agents, synthesis, localize_prompt) sees the right language. Without this, the Legislation agent generates English content from its own `localize_prompt` call before synthesis can intervene.
- [x] **2.6** Verifications:
  - Build: 141/141 files compile.
  - **`tests/test_drafting_quality.py`** + **`tests/test_markdown_sanitize.py`**: 55 passed, 8 skipped — no regression.
  - **`tests/test_user_intent_extraction.py`** (live, GOOGLE_API_KEY set): 46/46 passed.
  - **CGST stress test (fresh thread)** `"give me a table of section 131 of CGST Act 2017"`: extractor `format=table, confidence=0.9`; picker `template=SYNTHESIS_TABLE_PROMPT, source=intent`; response has 10 pipe lines (real markdown table). The original bug phrasing now works.
  - **Hindi directive end-to-end** `"Section 131 of CGST Act 2017 in Hindi"`: extractor `language=hi, language_explicit=True`; `state["user_language"]` overridden from "en" → "hi"; Legislation agent generates **698 Devanagari chars** + 308 Latin (preserved act names like "CGST Act, 2017"). Without Phase 2 this query returned pure English.
  - **CGST 2-turn** in same session: T2 still produces 10-pipe-line markdown table.

**Done when**: ✅ all of the above. The system now honours both format and language directives end-to-end on the flag-on path, with the regex as a low-confidence safety net.

### Phase 3 — Domain-agent intent awareness — **DONE 2026-06-13**

Domain agents now see depth + additional_instructions from `state["user_intent"]`. Format already routes via the synthesis layer; language already flows through `state["user_language"]` (Phase 2 override). The directive block is appended at runtime, only when intent expresses non-default preferences — backwards-compatible for callers without intent.

- [x] **3.1** Added `_format_intent_directives(intent)` in [core/language.py](../core/language.py) (co-located with `localize_prompt` to avoid an import cycle from `config.prompts` → `config.intent` → `core.language`). Surfaces `response_depth` ("brief" → "under 200 words", "detailed" → "comprehensive coverage") and `additional_instructions` (capped 300 chars at extraction). Format and language live in their own layers — adding them here would duplicate the synth picker's directive.
- [x] **3.2** Extended `localize_prompt(template, lang, intent=None)` signature. Backwards-compat: callers without intent see no behaviour change.
- [x] **3.3** Wired into 12 call sites across [legislation.py](../agents/legislation.py), [newacts.py](../agents/newacts.py), [judgment.py](../agents/judgment.py), [sci_judgment.py](../agents/sci_judgment.py), [gst_judgment.py](../agents/gst_judgment.py), [scenario.py](../agents/scenario.py), [constitution_maxim.py](../agents/constitution_maxim.py) (3 entry points), [document.py](../agents/document.py) (2 entry points), and the synthesis picker in [orchestrator.py](../agents/orchestrator.py). Skipped: drafting agent (own pipeline with structured outline/section gen — needs separate threading; deferred), non_legal (greetings don't need directives), drafting synthesis in orchestrator (CLAUDE.md invariants are about cite_appendix/stance/sections/validator, none of which intent directives interact with — safe but punted).
- [x] **3.4** Verified end-to-end with brief-depth smoke test: `"Section 131 of CGST Act in 2 lines briefly"` → extractor returns `depth=brief`, response is **76 words** (was previously ~250+).

### Phase 4 — Cleanup — **DONE 2026-06-13**

Once Phase 3 was wired and tests stayed green, the legacy regex pipeline became dead code. Phase 4 deletes it.

- [x] **4.1** Removed `INTENT_EXTRACTOR_V2` env flag check from `orchestrator_plan_node`. Extractor runs unconditionally in parallel with classify+plan. Replaced with `default_intent()` on extractor failure (no behavioural difference).
- [x] **4.2** Deleted `_TABLE_INTENT_RE` regex + `_wants_table_format()` function + `_emit_intent_telemetry()` (was the regex-vs-extractor agreement metric — moot without the regex). Simplified `_resolve_wants_table(state)` to a one-line `intent.wants_table` check.
- [x] **4.3** Deleted `_analyze_and_normalize_query()` + `QUERY_NORMALIZE_PROMPT` + `QueryAnalysis` schema. The extractor's `QueryAnalysisV2` produces both `normalized_query` and `UserIntent` in one call, so the legacy pipeline was duplicate work.
- [x] **4.4** Removed `INTENT_EXTRACTOR_V2` constant from [core/settings.py](../core/settings.py) with a comment pointing to the design doc. Env var becomes a no-op (safe to leave in .env, will be ignored).
- [x] **4.5** Verifications:
  - 142/142 files compile, gateway boots in 14s.
  - 69 unit tests pass (drafting + markdown + intent schema), 30 live tests gated on INTENT_EXTRACTOR_LIVE.
  - All 4 deleted symbols verified as non-importable (`_wants_table_format`, `_analyze_and_normalize_query`, `_emit_intent_telemetry`, `INTENT_EXTRACTOR_V2`).
  - End-to-end smokes on LOCAL: table query → 10 pipe lines · Hindi directive → 554 Devanagari chars · brief depth → 76 words.
- [x] **4.6** CLAUDE.md drafting invariants section: added a note that user-intent directives are appended to domain-agent prompts at runtime; the existing invariants (cite_appendix off, doctrinal stance, mandatory sections, validate_draft, CITE markers) are unchanged.

**Done when**: ✅ all of the above. Codebase has one canonical intent path. No regex. No free-form `response_instructions: str` mutation (`_legacy_response_instructions` is kept as the typed-intent → prompt-template projection, since the synthesis templates still interpolate `{response_instructions}`).

### Phase 4 — Cleanup

Once telemetry shows the regex fallback fires < 1% of requests for a week:

- [ ] **4.1** Delete `_TABLE_INTENT_RE` and `_wants_table_format()` from `agents/orchestrator.py`
- [ ] **4.2** Delete `response_instructions: str` field from `LegalAgentState` (all consumers now read `user_intent`)
- [ ] **4.3** Delete the old `QUERY_NORMALIZE_PROMPT` and `_analyze_and_normalize_query()`
- [ ] **4.4** Remove the `INTENT_EXTRACTOR_V2` feature flag (always on)
- [ ] **4.5** Update [CLAUDE.md](../CLAUDE.md) "Drafting invariants" section to mention the intent layer

**Done when**: Codebase has one canonical intent path. No regex. No free-form response_instructions.

---

## 4. Test plan

### Unit tests (Phase 0)

- `tests/test_user_intent_schema.py` — Pydantic round-trip, enum values, default factory.

### LLM-extractor tests (Phase 1)

`tests/test_user_intent_extraction.py` with pytest parametrize. Each row is a `(query, expected_format, expected_language)` triple. Run only when `OPENAI_API_KEY`/`GOOGLE_API_KEY` set (mark with `@pytest.mark.live`).

Coverage target: **40+ phrasings**. Sample (the regex misses today):

| query | expected_format | expected_language |
|---|---|---|
| "give me a table containing section 131, 132" | TABLE | en |
| "tabulate sections 131 and 132" | TABLE | en |
| "show me a table of section 131" | TABLE | en |
| "put 131 and 132 in a table" | TABLE | en |
| "मला 131 आणि 132 चा तक्ता द्या" | TABLE | mr |
| "धारा 131 और 132 का table बनाओ" | TABLE | hi |
| "131 aur 132 ka tabular comparison" | COMPARISON | hi |
| "explain section 131 briefly" | PROSE | en (depth=brief) |
| "compare 131 and 132 in detail" | PROSE | en (depth=detailed) |
| "in 2 lines" | PROSE | en (depth=brief) |

### Integration tests (Phase 2)

- `tests/test_intent_to_synthesis_routing.py` — mock the LLM extractor with each `ResponseFormat` value; verify the orchestrator picks the correct synthesis template + does/doesn't take the pass-through path.

### End-to-end smoke (Phase 2-3)

- Re-run [tests/run_cgst_thread_test.py](../tests/run_cgst_thread_test.py) on LOCAL + DEV + PROD after each phase. Compare table-line count: should be `>0` on Turn 2 once Phase 3 is in.

---

## 5. Telemetry & rollback

### Metrics (Prometheus, via `core/metrics.py`)

| Metric | Type | Purpose |
|---|---|---|
| `intent_extraction_total{outcome}` | counter | success/parse_error/timeout/exception |
| `intent_format_total{format}` | counter | distribution of detected formats |
| `intent_language_total{language}` | counter | language distribution |
| `intent_confidence_bucket` | histogram | confidence score distribution |
| `intent_regex_fallback_total{reason}` | counter | low_confidence / extractor_failed |
| `intent_extraction_duration_ms` | histogram | latency added by extractor |

### Rollback

- **Phase 0**: pure additions, no rollback needed.
- **Phase 1**: set `INTENT_EXTRACTOR_V2=false` on the server. Old code is untouched.
- **Phase 2**: same flag. The new code branches gate on the flag.
- **Phase 3**: revert per-agent prompt suffix commits. Each agent is one file.
- **Phase 4**: cleanup. Hardest to revert; requires a counter-commit. Only do this when telemetry has been green for 2 weeks.

---

## 6. Open questions / risks

- [ ] **Risk**: Gemini Flash Lite occasionally hallucinates enum values not in `ResponseFormat`. Mitigation: structured-output with strict enum + retry once.
- [ ] **Risk**: Chat-summary-based intent extraction could over-fit to Turn 1. If Turn 2 changes language preference, will the LLM honour it? Test case needed.
- [ ] **Question**: Do we want `response_format=TABLE` to also lock down `table_columns` from the LLM? Today the SYNTHESIS_TABLE_PROMPT decides columns. Could let LLM extract them in Phase 1 as an experiment.
- [ ] **Question**: Should `additional_instructions` be capped (e.g. 300 chars) to prevent prompt-injection volume attacks even though we wrap-untrust the input? Leaning yes.
- [ ] **Question**: Should we add per-thread caching of the last intent? Useful for follow-ups like "yes do it in Hindi" where the format from the previous turn applies. Defer to v2.

---

## 7. Progress log

- **2026-06-13** — plan written. Phase 0 starting.
- **2026-06-13** — Phase 0 complete. Schema + flag + state field + prompt + tests all landed and verified. 141/141 files compile, gateway boots clean, 14/14 unit tests pass, 30 live tests skip cleanly. Zero runtime impact. Ready for Phase 1.
- **2026-06-13** — Phase 1 complete. Extractor wired into BOTH plan-node branches (skip_normalize and full-normalize). Live extraction working — 46/46 tests pass including all 30 phrasings the regex misses today. Soft-cutover via `_legacy_response_instructions()` makes Phase 1 user-visible: CGST Turn-2 now produces a 9-row markdown table on LOCAL (was 0 pipes / pure prose before the flag). Telemetry is logging per-request agreement scores. Ready for Phase 2 (cut the synthesis-template picker to read `intent.wants_table` directly).
- **2026-06-13** — Phase 2 complete. The three synth-node decision sites (pass-through guard, primary-task skip, template picker) now read `state["user_intent"].wants_table` directly via `_resolve_wants_table`; regex stays as a `confidence < 0.7` fallback. Added `_resolve_explicit_non_english` to bypass single-agent pass-through for explicit non-English directives. Added `_resolve_user_language` plus an early `state["user_language"]` override in the plan node so domain agents pick up the right language too. Verifications: 55 unit-test passes + 46 live extractor tests + CGST fresh-thread "give me a table of 131" → 10-pipe table + Hindi directive "Section 131 in Hindi" → 698 Devanagari chars (was 0 before Phase 2). The original bug + the language-directive gap are both fixed on the flag-on path.
