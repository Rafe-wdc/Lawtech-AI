# Model Upgrade Plan — Response Quality & Attachment Pipeline

**Date:** 2026-09-04
**Scope:** LLM model selection across all pipeline stages, PDF/vision attachment handling
**Status:** SUPERSEDED IN PART - see "What actually shipped" below.

> ## What actually shipped (2026-09-07)
>
> This records the analysis as it stood on 2026-09-04. Later measurement in the
> same effort overturned three of its recommendations. The code reflects the
> later findings, not this document:
>
> | This doc recommended | What shipped | Why |
> |---|---|---|
> | Drafting on `gemini-pro-latest` | `anthropic:claude-sonnet-5` | Team decision. Pro also broke the heading rule more often than Flash (CLAUDE.md drafting invariant #2) |
> | Vision OCR on `gemini-pro-latest` | `gemini-3.6-flash`, own pinned tier | Equal accuracy, faster, and backed by the 2026-09-06 production OCR incident |
> | Keeping a `pro` tier | **Removed entirely** | 3.8-flash beats 3.1-pro on aggregate (51.0 vs 43.4) and reasoning (46.9 vs 45.1), costs 4x less, training data 14 months newer |
>
> Also resolved since: section 9's "Claude was not tested" - Anthropic credit
> arrived and Claude is now measured end-to-end; and pytest is installed, with
> the integration suite passing 18/19 locally.
>
> The MEASUREMENTS below remain valid. The RECOMMENDATIONS do not.
> `core/settings.py` GEMINI_MODELS is the source of truth for what runs.

Every claim marked **[measured]** was verified by running code against the live
APIs with this repo's own prompts and schemas during the analysis session.
Claims marked **[unmeasured]** are recommendations that still need an eval run.

---

## 1. Executive summary

Users report poor response quality. The investigation found the cause is **not
primarily the model tier** — it is four configuration decisions plus one dead
code path:

1. `get_gemini_flash()` silently returns **Flash Lite**, so the orchestrator's
   routing brain runs on the weakest model in the stack.
2. `thinking_budget=0` on every answer-generating call disables reasoning on
   the stages that most need it.
3. The routing model deterministically misroutes real client fact-patterns to
   the generic web-grounded explainer. **[measured]**
4. Intent extraction mis-detects drafting requests as prose format. **[measured]**
5. The native-PDF path is dead code — the model never sees an uploaded PDF as
   a document, only as flattened text.

Item 3 alone reproduces the exact user complaint: a real fact pattern returns a
generic explanation instead of case-specific analysis.

---

## 2. Current state

### 2.1 Configured models — `core/settings.py:47`

| Key | Model |
|---|---|
| `orchestrator`, `task_classifier`, `judgment_metadata`, `newacts_metadata` | `gpt-4o` |
| `draft_selector`, `legislation_match` | `gpt-4o-mini` |
| `drafting`, `scenario_web_grounded` | `gemini-2.5-flash` |
| `legal_concepts`, `query_rewrite`, `guardrail_injection`, `pdf_vision_ocr` | `gemini-2.5-flash-lite` |
| `pdf_chat` | `gemini-2.5-pro` |

### 2.2 Provider reality **[measured]**

| Provider | Key present | SDK installed | Live in runtime |
|---|---|---|---|
| Gemini | Yes | Yes | **All 29 LLM callsites** |
| OpenAI | Yes | Yes | **None** — `get_gpt4o` / `get_gpt4o_mini` have zero callers |
| Claude | **No** | **No** | No |

Two consequences:

- The `MODELS` dict has **drifted from the code**. It names `gpt-4o` for the
  orchestrator, but `agents/orchestrator.py` calls Gemini. The factories in
  `core/clients.py` hardcode their own model strings and never read `MODELS`.
- `core/settings.py:33` raises `ValueError` on a missing `OPENAI_API_KEY`,
  hard-failing startup for a provider nothing uses.

### 2.3 The Flash-Lite alias trap — `core/clients.py:275`

```python
get_gemini_flash = get_gemini_flash_lite
```

Every caller reading as "Flash" gets **Flash Lite**. Affected hot paths:

| Callsite | Purpose |
|---|---|
| `agents/orchestrator.py:369` | classify + plan |
| `agents/orchestrator.py:624` | intent extraction |
| `agents/orchestrator.py:743` | dynamic planner |
| `agents/orchestrator.py:1108` | agent query rewrite |
| `agents/judgment.py:191` | judgment metadata |
| `agents/newacts.py:164` | newacts metadata |
| `agents/memory.py:349` | memory summarisation |
| `core/chat_store.py:575`, `:2021` | chat title / summary |

The repo already documents this failure mode at `agents/orchestrator.py:966`:
Flash Lite passed a Hindi language violation the prompt explicitly named as
MAJOR — *"Lite does not make it inside a prompt this long."* That critic was
moved to full Flash. The same reasoning was never applied to intent extraction,
whose prompt is **four times longer** and runs on every request.

### 2.4 Prompt sizes **[measured]**

| Prompt | Chars | ~Tokens |
|---|---|---|
| `USER_INTENT_EXTRACTION_PROMPT` (`config/prompts.py:729`) | 26,015 | **6,503** |
| `CLASSIFY_AND_PLAN_PROMPT` (`agents/orchestrator.py:201`) | 9,717 | **2,429** |
| `DYNAMIC_PLANNER_PROMPT` (`config/prompts.py:3108`) | — | ~192 lines |
| `AGENT_QUERY_REWRITE_PROMPT` | — | 37 lines |
| `QUERY_REWRITE_PROMPT` | 567 | 141 |

~9,000 tokens of routing prompt on **every request**, with **zero prompt
caching** anywhere in the codebase.

---

## 3. Measured results

Harness: 15 labelled Indian-legal queries through the repo's real prompts and
Pydantic schemas; decisive cases re-run 5× to separate signal from sampling noise.

### 3.1 Classification + planning **[measured]**

| Model | Accuracy | p50 | max |
|---|---|---|---|
| `gemini-2.5-flash-lite` (current) | **12/15** | 3.1s | 3.5s |
| `gemini-3.5-flash-lite` `low` | **14/15** | 3.1s | 3.6s |
| `gemini-3.8-flash` `low` | 14/15 | 3.7s | **15.8s** |

Both failures are deterministic, not noise:

```
"My landlord has not returned my security deposit of Rs 2 lakh
 after 8 months despite repeated reminders..."
    2.5-flash-lite : 0/5 → Legal_Concepts   (WRONG)
    all Gemini 3.x : 5/5 → Scenario         (correct)

"Explain the doctrine of basic structure in brief, in Hindi"
    2.5-flash-lite : 1/5 → Legal_Concepts   (WRONG)
    all Gemini 3.x : 5/5 → Constitution     (correct)
```

**This is the user complaint reproduced.** A real client fact-pattern is routed
to the generic web-grounded explainer every single time.

### 3.2 Intent extraction **[measured]**

```
"Draft a legal notice to my employer for unpaid salary
 and cite the relevant case law"      →  response_format should be `draft`

  gemini-2.5-flash-lite     0/5  → prose
  gemini-3.5-flash-lite/min 0/5  → prose
  gemini-3.5-flash-lite/low 0/5  → prose
  gemini-3.8-flash/low      5/5  → draft   ✓
```

A drafting request typed as `prose` yields an explanation *about* the notice
instead of the notice. **The Lite tier fails this at every generation** — this
stage needs full Flash.

`thinking_level="minimal"` is a measured regression: 3/5 vs 5/5 for `low` on
core drafting format detection. **Do not use `minimal` anywhere.**

### 3.3 Vision OCR **[measured]**

Scored against ground truth (`docs/test-runs/control_draft.pdf` has a text layer)
at both production DPIs, using the real `_OCR_PROMPT_BODY`:

```
DPI 96 and DPI 200 — identical results
  gemini-2.5-flash (current)   recall 100.0%  precision 100.0%   11.0s
  gemini-3.8-flash             recall 100.0%  precision 100.0%    8.1s
  gemini-pro-latest            recall 100.0%  precision 100.0%    8.1s
  gemini-3.1-pro-preview       recall 100.0%  precision 100.0%    8.7s
```

Zero legal-signal token disagreement across all 7 tested models on the NDA scan.
All 7 correctly returned `[illegible]` on the genuinely blurry image.

**This is a ceiling effect, not proof of equivalence.** The repo contains no
Devanagari scan, no handwriting, and no photocopy — the cases that would
discriminate. The 2026-07-22 WhatsApp Devanagari incident documented in
`core/file_processor.py:66` is exactly the discriminating case and cannot be
reproduced without the file.

Differences that *were* observed:

| Model | Escaped `\_` pollution | Notes |
|---|---|---|
| `gemini-2.5-flash` (current) | 0 | baseline |
| **`gemini-pro-latest`** | **0** | **faster than current** |
| `gemini-3.8-flash` | 0 | fastest |
| `gemini-3.5-flash` | **108** | markdown escaping pollution |
| `gemini-3.1-pro-preview` | **143** | markdown escaping pollution |
| `gpt-5.4` | 0 | **68 `[illegible]`** where Gemini read fine |
| `gpt-5.5` | 0 | truncated to 729 chars, 43s |

Escaped underscores flow into ChromaDB and then into every agent prompt.

---

## 4. Migration blockers **[measured]**

Hit by running real calls, not read from docs:

1. **`thinking_budget` returns 400 on Gemini 3.x Lite.**
   `gemini-3.5-flash-lite` + `thinking_budget=0` → `INVALID_ARGUMENT`.
   Gemini 3 replaced it with `thinking_level` (`minimal`/`low`/`medium`/`high`).
   Passing both in one request is a 400. Every factory in `core/clients.py`
   passes `thinking_budget`.

2. **Gemini 3's default `thinking_level` is `high`.**
   Dropping `thinking_budget=0` without substituting `thinking_level` silently
   switches *maximum* reasoning on everywhere — the opposite of current tuning.

3. **`gemini-3.5-flash-lite` ignores `temperature` entirely.**
   LangChain warns: *"uses fixed sampling defaults; temperature will be ignored."*
   The `temperature=0.0` determinism assumption for table mode
   (`agents/orchestrator.py:2663`) silently stops applying.

4. **Google recommends `temperature=1.0` for Gemini 3.**
   Lowering it risks looping. This repo already had a runaway table-padding loop
   producing a 140k-char response (`agents/orchestrator.py:2650`). Do not carry
   `temperature=0.0` over blindly.

5. **Claude blockers (if adopted):** `temperature` is rejected on
   `claude-opus-5` / `claude-sonnet-5` (400). `budget_tokens` is removed (400) —
   use `thinking={"type":"adaptive"}` + `output_config={"effort": ...}`.
   Assistant prefill returns 400.

### Deprecation status

The [official Gemini API deprecations page](https://ai.google.dev/gemini-api/docs/deprecations)
lists **no announced shutdown date** for `gemini-2.5-flash`, `-flash-lite`, or
`-pro`. The Oct 2026 date circulating online applies to Vertex AI / Agent
Platform; this project uses `generativelanguage.googleapis.com`. There is
runway — but the quality gap in §3 is present today.

---

## 5. Available models **[measured against project keys]**

| Tier | Gemini | OpenAI | Claude |
|---|---|---|---|
| Cheap | `gemini-3.5-flash-lite` | `gpt-5.4-nano`, `gpt-5.4-mini` | `claude-haiku-4-5` ($1/$5) |
| Mid | `gemini-3.8-flash` | `gpt-5.4`, `gpt-5.5` | `claude-sonnet-5` ($2/$10) |
| Top | `gemini-pro-latest`, `gemini-3.1-pro-preview` | `gpt-5.5-pro`, `o3-pro` | `claude-opus-5` ($5/$25) |

All Gemini candidates: 1M input / 65K output, thinking supported.
There is currently **no GA `gemini-3.x-pro`** — only `gemini-3.1-pro-preview`
and the floating `gemini-pro-latest` alias.

---

## 6. Per-stage model map

### 6.1 Control plane — every request, latency-critical

Keep this entire plane on Gemini. Cross-provider calls here add a second
failure domain and latency tail on the hot path for no measured gain.

| # | Stage | File | Now | Target |
|---|---|---|---|---|
| 1 | Intent extraction | `agents/orchestrator.py:624` | 2.5-flash-lite | **`gemini-3.8-flash` `low`** [measured] |
| 2 | Classify + plan | `agents/orchestrator.py:369` | 2.5-flash-lite | **`gemini-3.5-flash-lite` `low`** [measured] |
| 3 | Dynamic planner | `agents/orchestrator.py:743` | 2.5-flash-lite | `gemini-3.5-flash-lite` `low` |
| 4 | Agent query rewrite | `agents/orchestrator.py:1108` | 2.5-flash-lite | `gemini-3.5-flash-lite` `low` |
| 5 | Follow-up query rewrite | `core/chat_store.py:575` | 2.5-flash-lite | `gemini-3.5-flash-lite` `low` |
| 6 | Guardrail / injection | `MODELS["guardrail_injection"]` | 2.5-flash-lite | `gemini-3.5-flash-lite` `low` |
| 7 | Chat title + summary | `core/chat_store.py:2021` | 2.5-flash-lite | `gemini-3.5-flash-lite` `low` |
| 8 | Judgment metadata | `agents/judgment.py:191` | 2.5-flash-lite | `gemini-3.5-flash-lite` `low` |
| 9 | Newacts metadata | `agents/newacts.py:164` | 2.5-flash-lite | `gemini-3.5-flash-lite` `low` |
| 10 | Draft template picker | `MODELS["draft_selector"]` | gpt-4o-mini *(config only)* | `gemini-3.5-flash-lite` `low` |
| 11 | Legislation match | `MODELS["legislation_match"]` | gpt-4o-mini *(config only)* | `gemini-3.5-flash-lite` `low` |

### 6.2 Generation plane — where quality complaints live

| # | Stage | File | Now | Gemini | Claude | OpenAI |
|---|---|---|---|---|---|---|
| 12–18 | Legislation / Judgment / Newacts / SCI / GST / Constitution / Maxim / Legal_Concepts | `agents/legislation.py:504` et al | 2.5-flash, **thinking 0** | `gemini-3.8-flash` `low` | `claude-sonnet-5` | `gpt-5.4` |
| 19 | **Scenario (web-grounded)** | `agents/scenario.py:106` | 2.5-flash | **Gemini-locked** | — | — |
| 20 | **Drafting** | `agents/drafting.py:1042` | 2.5-flash | `gemini-pro-latest` | **`claude-opus-5`** | `gpt-5.5-pro` |
| 21 | **Synthesis / merge** | `agents/orchestrator.py:2663` | 2.5-flash, thinking 0 | `gemini-3.8-flash` | **`claude-opus-5`** | `gpt-5.5` |
| 22 | Self-refine critic | `core/self_refine.py:1491` | 2.5-flash-lite | `gemini-3.8-flash` | **`claude-opus-5`** | `gpt-5.4` |
| 23 | Self-refine refiner | `core/self_refine.py:1589` | 2.5-flash | `gemini-3.8-flash` | `claude-opus-5` | `gpt-5.4` |
| 24 | Document Q&A | `agents/document.py:146` | 2.5-flash / 2.5-pro | `gemini-3.8-flash` | `claude-opus-5` | `gpt-5.4` |
| 25 | Language audit critic | `agents/orchestrator.py:982` | 2.5-flash | `gemini-3.8-flash` | `claude-sonnet-5` | `gpt-5.4` |
| 26 | Fix draft | `core/fix_draft.py:86` | 2.5-flash | `gemini-3.8-flash` | `claude-sonnet-5` | `gpt-5.4` |
| 27 | Compliance | `core/compliance.py:156` | 2.5-flash | `gemini-3.8-flash` | `claude-sonnet-5` | `gpt-5.4` |
| 28 | **Vision OCR — PDF** | `core/file_processor.py:1047` | 2.5-flash | **`gemini-pro-latest`** | untested | rejected |
| 29 | **Vision OCR — image** | `core/file_processor.py:942` | 2.5-flash | **`gemini-pro-latest`** | untested | rejected |

**Stage 19 is Gemini-locked** — Scenario uses Google Search grounding through
the raw `genai` client (`core/agent_fallback.py:193`). Moving it means replacing
grounding, not swapping a model string.

**Stages 28–29:** `gemini-pro-latest` is the recommendation — Pro tier, zero
escaping pollution, and *faster* than the current model. Note it is a floating
alias; pin `gemini-3.8-flash` instead if alias drift is unacceptable.

### 6.3 Provider strategy

- **Gemini stays the spine.** Control plane, retrieval agents, Scenario, OCR.
- **Claude at three places only** — Drafting (20), Synthesis (21), self-refine
  critic (22). A critic sharing the generator's blind spots is a weak critic;
  cross-provider is a real architectural argument here, unlike on the router.
  Requires: `pip install langchain-anthropic anthropic`, an `ANTHROPIC_API_KEY`,
  and new rows in the price table at `core/token_tracker.py:44`.
- **OpenAI: delete or demote to fallback.** Today it is a required key with no
  consumer. If kept, wire it into `core/agent_fallback.py` so the Gemini circuit
  breaker has somewhere to go.

---

## 7. PDF / attachment pipeline

### 7.1 How it works today (verified)

1. Upload → `process_files` (`core/file_processor.py:2087`)
2. PyMuPDF per-page text extraction (120s cap)
3. Garble detection — script-aware for Indic scripts (broken CMaps → force OCR);
   `_detect_garbled_pdf` for Latin (re-OCR, 900s cap)
4. No text layer → Gemini Vision OCR at 96 DPI, **1 page per call**, 4 concurrent,
   escalating to 200 DPI if thin/garbled; cached by file hash
5. Text → 15,000-char chunks → MiniLM embeddings → ChromaDB collection
   `inline_{thread}_{file_id}` (30s cap, semaphore of 2). Raw text also kept on
   `pf.extracted_text` as fallback
6. Question asked → orchestrator forces `Document` into the plan
   (`agents/orchestrator.py:1697`)
7. Document agent calls `get_full_attachment()` — whole document, no retrieval
   (`agents/document.py:246`)
8. <60,000 chars → Flash, `thinking_budget=0`; ≥60,000 → Pro, thinking 1024
   (`agents/document.py:130`). Output capped at 8,000 tokens.

### 7.2 Issues

**A. The native-PDF path is dead code.**
`pf.gemini_uri` is declared at `core/file_processor.py:187` and is *only ever
assigned `None`* (`:2311`). `upload_to_gemini` is imported at `:43` and never
called. `core/gemini_files.py` is orphaned, and the "large scanned PDF" branch
at `:2376` is unreachable.

Consequence: the model never sees the PDF as a document. Tables collapse,
two-column judgments interleave, stamps / signatures / handwritten endorsements
/ checkboxes vanish, clause-to-page mapping is lost. **Upgrading the OCR model
does nothing for PDFs with a text layer, because no vision model is invoked on
that path at all.**

**B. Large scanned PDFs fail outright.**
With the branch in (A) dead, every scanned PDF takes full Vision OCR under a
hard 900s ceiling (`:2409`). At `VISION_BATCH_SIZE = 1` with 4 concurrent calls,
a 200+ page scan cannot finish. On timeout `text = ""` → nothing reaches Chroma,
nothing reaches `extracted_text` → user sees *"I was unable to read the uploaded
file"* (`agents/document.py:222`). All-or-nothing; should return partial pages.

**C. The common case gets the weakest settings.**
Most uploads are <60,000 chars → Flash with `thinking_budget=0` on what is
fundamentally a reasoning task. Output capped at 8,000 tokens truncates
clause-by-clause analysis.

**D. Nothing verifies the answer against the document.**
The relevance gate was deliberately removed (`agents/document.py:255`).
Defensible with full-document reads, but combined with `thinking_budget=0`
there is now no check at all.

**E. No page citations.**
Extraction writes `--- Page N ---` markers, but the Document prompt
(`agents/document.py:151`) is three generic sentences and never asks for page
anchors. Users cannot verify a claim against the source.

**F. PDF embeddings use all-MiniLM-L6-v2** — a 2021 384-dim general model, weak
on legal text. Lower priority since `get_full_attachment` bypasses retrieval,
but `retrieve_attachment_context` still depends on it.

---

## 8. Implementation order

| # | Change | Why first |
|---|---|---|
| 1 | Config-drive model IDs: make `core/clients.py` factories read `MODELS` from `core/settings.py`, backed by env vars. Add `thinking_level` support. | Prerequisite for everything else. Fixes the `MODELS` drift. Makes every future migration a config change, rollback-able in seconds. |
| 2 | Drop the `get_gemini_flash` → Lite alias; point routing at the right tier. | Fixes wrong-agent answers. |
| 3 | Switch stages 1 and 2 to the measured targets. | Highest-confidence quality win. |
| 4 | Build a routing eval: 150–200 labelled production queries. | Currently there is **no** routing-accuracy metric — `core/quality.py` scores final answers only, and the Drafting-agreement telemetry cross-check was retired (`agents/orchestrator.py:394`). A misroute is invisible today. |
| 5 | Turn thinking back on for generation stages (`low`/`medium`) + raise output ceilings. | Largest single generation-quality lever. |
| 6 | Vision OCR → `gemini-pro-latest`. **Bump `OCR_CACHE_VERSION` `"v2"` → `"v3"`** (`core/file_processor.py:77`). | Without the bump, every previously-uploaded file keeps serving old-model OCR from cache. |
| 7 | Restore the native-PDF path (send PDF *and* OCR text). | Bigger vision lever than the OCR model itself. |
| 8 | Add prompt caching on the long stable prompts. | Zero caching exists today; the 6,503-token intent prompt is byte-identical every request. Cuts cost *and* latency. |
| 9 | Claude for stages 20 / 21 / 22, once a key exists. | Cross-provider critic. |
| 10 | Extend the eval harness with an LLM judge for generation stages. | Gate before moving stages 12–27. |

### Also worth doing

- Split `USER_INTENT_EXTRACTION_PROMPT`. A 412-line prompt producing a 15-field
  object is doing too much in one call; language and format directives are
  largely independent of task intent. Even a strong model does better against a
  tighter spec.
- Remove the hard `OPENAI_API_KEY` requirement at `core/settings.py:33` if
  OpenAI stays unused.

---

## 9. Reproducing the measurements

Two standalone harnesses were written during the analysis. They run against
`.env` and require no server:

- `model_ab.py` — routing/intent A/B. `python model_ab.py classify` or
  `python model_ab.py intent`. Uses the repo's real prompts and Pydantic schemas.
- `vision_ab.py` — vision OCR A/B across providers, scored with the repo's own
  `_ocr_result_is_usable` and `count_illegible_markers` gates.

Both are checked in at `tests/model_ab_eval.py` and `tests/vision_ab_eval.py`.
Extend them before any stage in §6.2 is switched.

### Known limits of this analysis

- 15 queries catches deterministic failures; it does **not** rank models
  separated by a point or two. Re-run against a few hundred real queries.
- One labelling call was ambiguous: the Hindi statute query had all four models
  answer `Newacts` against a `Legislation` label. 498A IPC → BNS genuinely is
  Newacts territory — that case was excluded from the conclusions.
- Vision testing hit a ceiling effect. No Devanagari scan, handwriting sample,
  or photocopy exists in the repo, so the discriminating cases were untestable.
- Claude was not tested — no `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` exists
  on the machine and `langchain-anthropic` is not installed. `tests/vision_ab_eval.py`
  now carries a guarded `claude-sonnet-5` entry that activates automatically once
  a credential is present:

  ```bash
  pip install langchain-anthropic
  export ANTHROPIC_API_KEY=sk-ant-...      # or add to .env
  python tests/vision_ab_eval.py
  ```

  Note for whoever wires it: `claude-sonnet-5` **rejects `temperature` and
  `budget_tokens` with a 400**. Omit both — omitting `thinking` runs adaptive,
  which is the only on-mode for Sonnet 5.

---

## 10. Sources

- [Gemini API deprecations](https://ai.google.dev/gemini-api/docs/deprecations)
- [Gemini 3 developer guide](https://ai.google.dev/gemini-api/docs/gemini-3)
- [Thinking — Agent Platform docs](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/thinking)
- Model availability and specs read live from the Gemini and OpenAI models
  endpoints using this project's keys on 2026-09-04.
