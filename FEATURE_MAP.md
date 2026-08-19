# Lawtech-AI — Feature Map

> Generated 2026-08-12 · commit `010ee10` · branch `main`

A capability-indexed map of the whole system: **what the product does**, and **where each
capability lives**. Every claim is anchored to `file:line`.

**What this is not:**

| Document | Covers |
|---|---|
| [`CLAUDE.md`](../CLAUDE.md) | Operating rules and non-negotiable invariants. Prescriptive. |
| [`SYSTEM_MAP.md`](../SYSTEM_MAP.md) | Pipeline internals — ES query DSL, DB columns. **Snapshot dated 2026-03-11; partly drifted.** |
| [`README.md`](../README.md) / [`AGENTS.md`](../AGENTS.md) | Setup and code conventions. |
| [`docs/*.md`](.) | 21 change-specific plans and postmortems. |

---

## Table of Contents

1. [Capability catalog](#1-capability-catalog)
2. [Request lifecycle](#2-request-lifecycle)
3. [Per-capability detail](#3-per-capability-detail)
4. [Cross-cutting subsystems](#4-cross-cutting-subsystems)
5. [Reference tables](#5-reference-tables)
6. [Deployment topology](#6-deployment-topology)
7. [Appendix A — Anomalies](#appendix-a--anomalies)

---

## 1. Capability catalog

Seventeen user-facing capabilities. Readable without any codebase knowledge.

### Legal research

| Capability | What the user gets | Entry point | Backing store |
|---|---|---|---|
| **Statute lookup** | Text and explanation of any central or state act section | [`agents/legislation.py`](../agents/legislation.py) | ES `legislation` |
| **Old ↔ new code mapping** | BNS↔IPC, BNSS↔CrPC, BSA↔IEA text, counterpart mapping, nearby sections, cross-act comparison | [`agents/newacts.py`](../agents/newacts.py) | ES `newacts_v1` (hybrid BM25 + vector) |
| **High Court case law** | Judgments with parties, court, year, and downloadable S3 PDF links | [`agents/judgment.py`](../agents/judgment.py) | ES `judgements` + AWS S3 |
| **Supreme Court research** | Multi-step research over 44,000+ SC judgments — by topic, party, bench, case number, date | [`agents/sci_judgment.py`](../agents/sci_judgment.py) | ES `supreme_court_judgement` |
| **GST advance rulings** | 533 GST AAAR orders — classification, ITC, valuation, HSN, state appellate orders | [`agents/gst_judgment.py`](../agents/gst_judgment.py) | OpenSearch `gst_judgements` |
| **Constitutional provisions** | Articles, Parts, fundamental rights | [`agents/constitution_maxim.py`](../agents/constitution_maxim.py) | ES `constitution` + web enrichment |
| **Legal maxims** | Latin maxims and doctrines | [`agents/constitution_maxim.py`](../agents/constitution_maxim.py) | ES `legal_maxims` + web enrichment |
| **Concept explanation** | Plain-language explanation of a legal concept | [`agents/constitution_maxim.py`](../agents/constitution_maxim.py) | Google Search grounding only |
| **Scenario analysis** | Fact-pattern analysis — applicable law, arguments, defences, remedies; also legal news | [`agents/scenario.py`](../agents/scenario.py) | Google Search grounding |

### Drafting

| Capability | What the user gets | Entry point | Backing store |
|---|---|---|---|
| **Document drafting** | A full pleading, petition, notice, or application — single-pass or section-by-section, in any of 14 languages | [`agents/drafting.py`](../agents/drafting.py) | ES `drafting` templates → web synthesis; + 4-retriever legal context |
| **Review and redraft** | Upload an existing draft; get it audited and rewritten, with the upload itself as the structural anchor | `_is_review_and_redraft_of_upload` [`agents/drafting.py:102`](../agents/drafting.py#L102) | The uploaded document |
| **Regenerate / polish** | Re-run a previous answer with new instructions, without re-running retrieval | Plan-node short-circuit [`agents/orchestrator.py:987`](../agents/orchestrator.py#L987) | Previous response |

### Documents

| Capability | What the user gets | Entry point | Backing store |
|---|---|---|---|
| **Upload + OCR ingestion** | 30 files/request, 1 GB each — PDF, DOCX, XLSX, CSV, TXT, MD, and images; scanned and legacy-font documents transcribed | [`core/file_processor.py`](../core/file_processor.py) | ChromaDB + raw text fallback |
| **Q&A over uploads** | Ask anything about the attached documents — summaries, extraction, party identification | [`agents/document.py`](../agents/document.py) | ChromaDB `chroma_store/` |
| **Google Docs / Notion import** | Paste a Docs, Sheets, Drive, or Notion URL; content is pulled in after an OAuth handshake | [`core/integration_service.py`](../core/integration_service.py) | FSD Chat Service (OAuth broker) |

### Post-processing (each is a standalone endpoint returning a downloadable file)

| Capability | What the user gets | Entry point | Backing store |
|---|---|---|---|
| **Export** | Any answer or draft as DOCX or PDF, formatted for filing, with Indic-script support | [`core/export.py`](../core/export.py) | — |
| **Compliance check** | Court-filing readiness scan across 6 axes, with a PASS / NEEDS ATTENTION / CRITICAL verdict and a score out of 10 | [`core/compliance.py`](../core/compliance.py) | — |
| **Fix draft** | Every Critical Issue from a compliance report applied, each change marked `[REVISED: reason]` | [`core/fix_draft.py`](../core/fix_draft.py) | — |
| **Table of Authorities** | Citations extracted into 5 sections — Cases, Statutes, Constitutional, Rules, Secondary | [`core/toa.py`](../core/toa.py) | — |
| **Research memo** | An answer restructured as a formal memorandum: Issue → Brief Answer → Discussion → Conclusion | [`core/memo.py`](../core/memo.py) | — |
| **Statute references** | Act name and section number inserted where a clause cites law loosely | [`core/statute_refs.py`](../core/statute_refs.py) | — |

### Conversation

| Capability | What the user gets | Entry point | Backing store |
|---|---|---|---|
| **Multi-turn memory** | Follow-ups resolve against history; a rolling summary keeps long threads coherent | [`agents/memory.py`](../agents/memory.py) | Postgres or SQLite chat store |
| **File-context restoration** | Uploads stay usable across turns; expired Gemini URIs are silently re-uploaded | `_restore_file_context` [`agents/memory.py:293`](../agents/memory.py#L293) | `thread_files` table |
| **14-language support** | Ask and receive in English, Hindi, Bengali, Telugu, Marathi, Tamil, Kannada, Malayalam, Gujarati, Punjabi, Urdu, Odia, Assamese, or Sanskrit | [`core/language.py`](../core/language.py) | — |

---

## 2. Request lifecycle

`POST /pyapi/chat` exercises the most machinery of any endpoint — uploads, integrations,
streaming, and the full graph. Walking it once explains all three SSE endpoints.

### Middleware

Starlette executes middleware in **reverse declaration order**, so the outermost layer is
the one declared last:

| Order | Middleware | Behaviour |
|---|---|---|
| 1 (outermost) | `inflight_gate` [`core/gateway.py:370`](../core/gateway.py#L370) | 50 ms non-blocking semaphore acquire; excess → `503` + `Retry-After: 5` |
| 2 | `metrics_middleware` [`core/gateway.py:319`](../core/gateway.py#L319) | `active_requests` gauge, `requests_total`, `request_latency_seconds` |
| 3 | `_cache_request_body_for_diagnostics` [`core/gateway.py:278`](../core/gateway.py#L278) | JSON/text bodies ≤ 100 KB only — deliberately skips multipart so SSE is never consumed |
| 4 | `request_id_middleware` [`core/gateway.py:257`](../core/gateway.py#L257) | 8-char UUID into `request.state` and the log ContextVar |
| 5 | `ProxyHeadersMiddleware` [`core/gateway.py:249`](../core/gateway.py#L249) | Only mounted when `TRUSTED_PROXY_IPS` is set |
| 6 (innermost) | `CORSMiddleware` [`core/gateway.py:227`](../core/gateway.py#L227) | `ALLOWED_ORIGINS` |

### Handler → graph → response

| # | Step | Location |
|---|---|---|
| 1 | `X-API-Key` check | `require_user_key` [`core/auth.py:44`](../core/auth.py#L44) |
| 2 | Rate limit, keyed by admin-exempt / API key / client IP | `_rate_limit_key` [`core/gateway.py:114`](../core/gateway.py#L114) |
| 3 | `thread_id = globalThreadId or uuid4()`; query length guard 1–200,000 chars | [`core/gateway.py:902`](../core/gateway.py#L902) |
| 4 | Per file: safe-name → temp file → `validate_upload`; rejects collected, not fatal | [`core/gateway.py:950`](../core/gateway.py#L950) |
| 5 | `token_tracker.start_request()` — **before** file processing, so Vision-OCR tokens bill to this request | [`core/gateway.py:978`](../core/gateway.py#L978) |
| 6 | `process_files(...)` runs as a task; events drained from an `asyncio.Queue` so SSE never goes silent. 240 s envelope | [`core/file_processor.py:1703`](../core/file_processor.py#L1703) |
| 7 | `run_chat_pipeline(...)` — cache disabled for `/chat` because attachments make keys unsafe | [`core/chat_runner.py:230`](../core/chat_runner.py#L230) |
| 8 | Integration URLs detected and extracted, bounded by `INTEGRATION_POLL_TIMEOUT_SEC` (60 s) | [`core/chat_runner.py:114`](../core/chat_runner.py#L114) |
| 9 | `_build_initial_state(...)` — the 22-key `LegalAgentState` | [`core/gateway.py:540`](../core/gateway.py#L540) |
| 10 | `set_deadline_seconds(285)` inside `async_timeout_cm(300)` — 15 s slack under the envelope | [`core/chat_runner.py:365`](../core/chat_runner.py#L365) |
| 11 | `agent_graph.astream(state, config, stream_mode=["updates", "custom"])` | [`core/chat_runner.py:367`](../core/chat_runner.py#L367) |
| 12 | Every token passes `_strip_html_from_token`; the final response passes `_strip_html_from_response` | [`core/chat_runner.py:100`](../core/chat_runner.py#L100) |
| 13 | Terminal events: `response` → `sources` → `followup_suggestions` | [`core/chat_runner.py:481`](../core/chat_runner.py#L481) |
| 14 | Fire-and-forget request log + sampled quality score; then `save_turn` | [`core/chat_runner.py:502`](../core/chat_runner.py#L502) |
| 15 | `done` event with `token_usage`, `agents_used`, `conversation_turn` | [`core/chat_runner.py:568`](../core/chat_runner.py#L568) |
| 16 | `finally:` unlinks every temp upload — runs even on client disconnect | [`core/gateway.py:1103`](../core/gateway.py#L1103) |

Response headers set `Cache-Control: no-cache` and `X-Accel-Buffering: no`; nginx pairs
this with `proxy_buffering off` ([`deploy/nginx/api.lawttorney.com.conf`](../deploy/nginx/api.lawttorney.com.conf)).

### Graph topology

Built in `build_graph` [`core/graph.py:174`](../core/graph.py#L174), compiled with a
checkpointer at [`core/graph.py:273`](../core/graph.py#L273).

```
                             START
                               │
                       ┌───────▼────────┐
                       │ guardrail_input│  always
                       └───────┬────────┘
                     blocked?  │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
      ┌───────────────┐                  ┌─────────────┐
      │blocked_response│                  │   memory    │  history, rewrite,
      └───────┬───────┘                  └──────┬──────┘  language, file ctx
              │                                 ▼
              │                        ┌──────────────────┐
              │                        │ orchestrator_plan│  intent + classify
              │                        └────────┬─────────┘  (parallel Flash Lite)
              │                                 │
              │                    Send() fan-out — up to 4 of 12,
              │                    all concurrent, merged by reducer
              │       ┌──────────┬──────────┬───┴───┬──────────┬──────────┐
              │       ▼          ▼          ▼       ▼          ▼          ▼
              │  legislation  judgment  sci_judgment  newacts  drafting  document
              │   scenario   constitution   maxim   legal_concepts  gst_judgment
              │                                          non_legal
              │       └──────────┴──────────┴───┬───┴──────────┴──────────┘
              │                                 ▼
              │                     ┌───────────────────────┐
              │                     │orchestrator_synthesize│  6 output paths
              │                     └───────────┬───────────┘
              └─────────────────────────────────┤
                                                ▼
                                     ┌──────────────────┐
                                     │ guardrail_output │  always (terminal)
                                     └────────┬─────────┘
                                              ▼
                                             END
```

Two short-circuits, both in `route_after_orchestrator`
[`core/graph.py:112`](../core/graph.py#L112):

- **Regenerate** — `task == "Refine"` with `final_response` already set jumps straight to
  `guardrail_output`, bypassing both fan-out and synthesis ([`core/graph.py:131`](../core/graph.py#L131)).
- **Empty plan** — defaults to a single `Send("scenario", state)` ([`core/graph.py:139`](../core/graph.py#L139)).

Parallel fan-out is safe because `agent_results` carries a merge reducer
(`_merge_agent_results`, [`core/state.py:88`](../core/state.py#L88)) rather than
last-write-wins. `source_metadata` is capped at the last 100 entries, `tokens_consumed`
sums, and `messages` caps at 40.

---

## 3. Per-capability detail

Each subsection follows the same schema: **entry point · data source · model · search
strategy · fallback tiers · gotchas**.

> **Model naming, once:** `MODELS` in [`core/settings.py:47`](../core/settings.py#L47)
> still names `gpt-4o` and `gpt-4o-mini` for six roles, but **no OpenAI model is invoked
> anywhere in the agent layer**. Every call resolves through
> [`core/clients.py`](../core/clients.py), where `get_gemini_flash` is an alias for Gemini
> 2.5 **Flash Lite** ([`core/clients.py:254`](../core/clients.py#L254)),
> `get_gemini_flash_full` is Flash ([`core/clients.py:277`](../core/clients.py#L277)), and
> `get_gemini_pro` is Pro ([`core/clients.py:301`](../core/clients.py#L301)). Docstrings
> across `agents/*.py` that mention GPT-4o are stale.

### 3.1 Statute lookup — `legislation`

- **Entry** `legislation_node` [`agents/legislation.py:273`](../agents/legislation.py#L273) · **Source** ES `legislation` · **Model** Flash (generation), Flash Lite (rewrite)
- **Strategy** — strip subsections → `parse_multi_section_info` → either a parallel
  multi-section search or a single-section search threaded with `intent.named_acts` for
  act-aware source preference → topic BM25 as the last search tier.
- **Fallbacks** — full 3-tier (ES → rewrite + retry → web), plus two extra web exits: a
  rejected relevance gate ([`:442`](../agents/legislation.py#L442)) and apology detection
  on the generated text ([`:517`](../agents/legislation.py#L517)).
- **Gotcha** — ES query-shape errors (`maxclausecount`, `too_many_clauses`, `class_cast`)
  are swallowed into "no hits" ([`:355`](../agents/legislation.py#L355)) so the fallback
  ladder still runs instead of surfacing a 500.

### 3.2 Old ↔ new code mapping — `newacts`

- **Entry** `newacts_node` [`agents/newacts.py:369`](../agents/newacts.py#L369) · **Source** ES `newacts_v1`, hybrid BM25 + BGE-large vector · **Model** Flash
- **Strategy** — the most elaborate retrieval agent, on its own 150 s budget:
  1. Metadata extraction (Flash Lite structured output, 20 s) with a regex fallback built
     on abbreviation maps.
  2. **Mapping branch** — for "what is the BNS equivalent of IPC 302"-style queries,
     fetches counterpart sections in both directions ([`:451`](../agents/newacts.py#L451)).
  3. **Cross-act branch** — when ≥ 2 of the 6 codes are named, one filtered search per act,
     top 8 merged ([`:596`](../agents/newacts.py#L596)).
  4. Hybrid-search failure degrades to BM25-only rather than erroring ([`:720`](../agents/newacts.py#L720)).
  5. Nearby-section enrichment when the result set is thin.
- **Fallbacks** — full 3-tier, plus a relevance-gate exit that is *skipped* for exact filter
  queries (`_is_exact_filter_query`, [`:348`](../agents/newacts.py#L348)).
- **Gotcha** — Newacts vetoes Legislation in plan validation; the two never co-run
  ([`agents/orchestrator.py:819`](../agents/orchestrator.py#L819)).

### 3.3 High Court case law — `judgment`

- **Entry** `judgment_node` [`agents/judgment.py:345`](../agents/judgment.py#L345) · **Source** ES `judgements` + S3 · **Model** Flash Lite (metadata), Flash (generation)
- **Strategy** — seven strategies in specificity order inside `smart_judgment_search`
  ([`tools/shared/judgment_search.py:455`](../tools/shared/judgment_search.py#L455)):
  citation → both parties (exact, no-year, swapped, fuzzy) → single party → case type →
  legal provision → topics + acts + BM25 → relaxed full text.
  A regex-metadata preliminary search runs **in parallel** with LLM metadata extraction
  (22 s bound); if the preliminary search hits, the refined search is skipped entirely
  ([`:417`](../agents/judgment.py#L417)).
- **Fallbacks** — full 3-tier + relevance-gate exit + apology exit.
- **Gotcha** — `_is_exact_section_lookup` ([`:73`](../agents/judgment.py#L73)) bypasses the
  LLM relevance judge when the user named a section and ≥ 50% of hits cite it. Without this
  the gate rejected correct results and fell through to web search.
- PDF links come from `generate_s3_link` ([`tools/shared/storage_tools.py:69`](../tools/shared/storage_tools.py#L69)), which HEAD-checks the key before emitting a URL.

### 3.4 Supreme Court research — `sci_judgment`

- **Entry** [`agents/sci_judgment.py:60`](../agents/sci_judgment.py#L60) · **Source** ES `supreme_court_judgement` · **Model** Flash, temp 0, ReAct
- **Strategy** — unlike every other retrieval agent this is a **ReAct loop**, not a
  deterministic pipeline: `create_react_agent` with 7 tools
  ([`tools/shared/sci_judgment_tools.py`](../tools/shared/sci_judgment_tools.py)) —
  `search_by_topic`, `search_by_keyword`, `search_by_case_number`, `search_by_party_name`,
  `search_by_date_range`, `search_by_judge`, `get_case_details` — typically 2–4 tool calls
  within a 90 s trajectory bound.
- **Fallbacks** — (a) **zero-tool-call rescue**: force `search_by_topic`, inject the results,
  re-invoke ReAct ([`:130`](../agents/sci_judgment.py#L130)); (b) web search when both answer
  and sources come back empty ([`:256`](../agents/sci_judgment.py#L256)). No query-rewrite
  tier is wired.

### 3.5 GST advance rulings — `gst_judgment`

- **Entry** [`agents/gst_judgment.py:102`](../agents/gst_judgment.py#L102) · **Task** `GST_Judgment` · **Source** OpenSearch `gst_judgements` · **Model** Flash ReAct, 90 s bound
- Forked from `sci_judgment`, with `gst_search_by_state` replacing `search_by_judge` and
  `state_ut` / `brief_of_order` / `ar_order_no_date` replacing bench and judge fields.
  Same zero-tool-call rescue and web last resort.

### 3.6 Constitution · Maxim · Legal Concepts

Three separate node functions in one module, specifically so they can fan out in parallel
([`agents/constitution_maxim.py:1`](../agents/constitution_maxim.py#L1)).

- **Constitution / Maxim** — `_handle_constitution_or_maxim` [`:138`](../agents/constitution_maxim.py#L138).
  ES BM25 with boosted phrase matching (Constitution boosts `article_name` ×2.5, Maxim
  boosts `maxim_name` ×5.0) runs **concurrently with web enrichment**
  (`get_web_context`, [`:152`](../agents/constitution_maxim.py#L152)); generation sees both.
  Three web exits: no documents, gate rejection, apology detected.
- **Legal Concepts** — `_handle_legal_concepts` [`:123`](../agents/constitution_maxim.py#L123).
  **No retrieval at all** — `web_search_fallback` is the primary path with
  `LEGAL_CONCEPTS_PROMPT`. This is why the planner treats `Legal_Concepts` as strictly
  single-agent.

### 3.7 Scenario analysis — `scenario`

- **Entry** [`agents/scenario.py:103`](../agents/scenario.py#L103) · **Source** Google Search grounding only · **Model** Flash, max 8000, temp 0.5, 60 s
- Calls the raw `genai.Client` with `tools=[{"google_search": {}}]` rather than LangChain,
  because LangChain cannot express the `google_search` tool.
- Also the default target for task `Other` and any unmapped task.
- **Fallback** — an **ungrounded rescue**: if the grounded call returns empty candidates
  (common on Indic-script queries and safety filtering), it retries once without the tool
  ([`:145`](../agents/scenario.py#L145)).
- Concurrency-limited to 3 per worker with `queue_status` SSE emission.

### 3.8 Drafting — `drafting`

The most complex capability in the system. 2,417 lines. Concurrency-limited to 3 per worker
with a 15 s `queue_status` heartbeat while waiting
([`agents/drafting.py:2160`](../agents/drafting.py#L2160)).

> Six invariants govern this pipeline — see the **Drafting invariants** section of
> [`CLAUDE.md`](../CLAUDE.md) and [`docs/drafting_simplification_plan.md`](drafting_simplification_plan.md).
> They are not restated here. What follows is the mechanism.

**Step 1 — assemble `user_facts`** ([`:2034`](../agents/drafting.py#L2034)).
Source priority: raw `fc.extracted_texts` (preferred even when ChromaDB is healthy, because
drafting needs every fact) → `get_full_attachment` over Chroma collections → pasted context
verbatim → integration content. Optionally gated by the injection classifier when
`INJECTION_CHECK_ENABLED=1`.

**Step 2 — acquire a reference draft.** Two branches:

*Review-and-redraft* — `_is_review_and_redraft_of_upload`
([`:102`](../agents/drafting.py#L102)) requires ≥ 200 chars of upload plus a review verb.
The uploaded document then **becomes** the reference (`reference_kind="uploaded"`), the raw
prompt is restored over the rewritten one, a system-prompt override inverts the default
"the reference belongs to a different matter" framing
([`:128`](../agents/drafting.py#L128)), and self-refine is skipped
([`:2346`](../agents/drafting.py#L2346)).

*Fresh draft* — `_acquire_reference_draft` ([`:457`](../agents/drafting.py#L457)) runs
concurrently with `_gather_relevant_context`:

1. Optional query translation to English — the `drafting` corpus is English-only, guarded
   by a Latin-character-ratio check ([`:324`](../agents/drafting.py#L324)).
2. ES `match` on `page_content`, `size: 100`, `_source: ["source"]` → distinct file paths.
3. `_pick_reference_source` ([`:389`](../agents/drafting.py#L389)) — one Flash Lite
   structured call over **file names only**. `'none'` is a valid answer.
4. ES `term` on `source.keyword` fetches the full template text.
5. **Four web-fallback exits**, each tagged in the logs: `<web:no-corpus-hit>`,
   `<web:picker-rejected>`, `<web:fetch-failed>`, and ES error. All route to
   `_acquire_reference_via_web` ([`:598`](../agents/drafting.py#L598)), which asks for a
   *sample draft*, not an essay.

`_gather_relevant_context` ([`:648`](../agents/drafting.py#L648)) runs four retrievers in
parallel — `search_newacts`, `search_legislation`, `search_judgments`, and SCI
`search_by_topic` — formatting the top 3 hits each into labelled blocks. This is why
drafting needs no citation fan-out in review mode.

**Step 3 — generate.** `_generate_draft` ([`:1875`](../agents/drafting.py#L1875)):

- **Preflight token budget** — 3.5 M chars for Latin scripts, **2.4 M for dense Indic
  scripts** (detected by Unicode-range sampling across 9 scripts), because Indic text
  tokenises at roughly 2.5 chars/token ([`:1892`](../agents/drafting.py#L1892)).
- `_judge_fanout` ([`:1300`](../agents/drafting.py#L1300)) — one Flash Lite structured call
  decides single-pass vs sectionwise. Inputs: a head-3000/tail-1000 reference excerpt, the
  target language, a depth directive derived from `intent.response_depth`, and a summary of
  the prior AI turn so polish follow-ups stay single-pass. Circuit-breaker guarded, 15 s
  bound; **any failure means single-pass**.
- **Single-pass** ([`:753`](../agents/drafting.py#L753)) — one Gemini 2.5 Pro call at temp
  0.0, 24,000 output tokens, 4,096 thinking budget. On a RECITATION/SAFETY block it retries
  once with a paraphrase instruction, then raises a clean error rather than leaking the
  `AIMessage` repr.
- **Sectionwise** ([`:1633`](../agents/drafting.py#L1633)) — walks sections **in pairs**
  (last solo if odd), one Pro call per pair, each seeing the document so far (`prior_text`),
  the raw source, the reference, and the gathered context. One retry per pair, skipped when
  under 15 s of deadline remains. Failed pairs are skipped rather than fatal — the user gets
  a `draft_incomplete` event plus a `⚠ Draft incomplete` banner.
- **Chunk router** (`DRAFTING_PER_SECTION_CHUNKING=1`, off by default) — only engages when
  `len(user_facts) > 100_000` **and** the blob splits into ≥ 4 chunks. Per pair, a Flash
  Lite router picks the chunk indices those sections need; the union becomes that call's
  source. **Every** failure mode — flag off, below threshold, too few chunks, router
  exception, empty selection — falls back to the full raw source, preserving the invariant.

**Step 4 — clean.** `validate_draft` ([`:195`](../agents/drafting.py#L195)) is mechanical
only: cp1252 mojibake repair, HTML tag stripping, leftover `[CITE: …]` removal, empty
numbered paragraphs. Warnings surface in `AgentResult.meta["draft_warnings"]`.

**Step 5 — refine.** `core.self_refine.self_refine` — see [§4.1](#41-output-quality).

**Gotcha** — `_FANOUT_HARD_CAP = 12` ([`:1956`](../agents/drafting.py#L1956)) silently
truncates the section list, while the fan-out prompt tells the model it may emit up to 15.
The cap exists because 15 sequential Pro calls exceed the 300 s gunicorn timeout.

### 3.9 Document Q&A — `document`

- **Entry** [`agents/document.py:128`](../agents/document.py#L128) · **Source** ChromaDB per-thread collections
- **Model routing by size** — under 60,000 chars → Flash with thinking disabled; larger →
  **Gemini 2.5 Pro** with a 1,024 thinking budget.
- Reads the **full document** via `get_full_attachment`, not a top-K retrieval — the MMR
  path exists in [`tools/shared/vectordb_tools.py`](../tools/shared/vectordb_tools.py) but
  is unused. Falls back to raw `fc.extracted_texts` when the Chroma path is empty
  ([`:293`](../agents/document.py#L293)).
- The relevance gate is **deliberately removed** on the full-document path
  ([`:309`](../agents/document.py#L309)) — the user attached the file, so relevance is not
  in question.
- `detect_source_languages` feeds `localize_prompt` so a Marathi PDF does not code-switch an
  English answer.

### 3.10 Document ingestion

`process_files` ([`core/file_processor.py:1703`](../core/file_processor.py#L1703)) is the
single entry point. **Guarantee: no single bad file can hang or kill the whole upload** —
every stage is individually `wait_for`-wrapped, and a failure is recorded per file while the
rest continue.

Accepted: `.pdf .jpg .jpeg .png .webp .docx .txt .md .csv .xlsx`
([`:141`](../core/file_processor.py#L141)). `.doc` gets an explicit "convert to .docx"
message. Limits are 30 files/request, 30 files/thread, 1 GB/file, 1 GB/thread
([`core/settings.py:112`](../core/settings.py#L112)).

**Four independent Vision-OCR triggers**, all on the PDF path:

1. **Empty text layer** — a pure scan.
2. **Garbled non-Latin text layer** — `_is_garbled_by_script`
   ([`:377`](../core/file_processor.py#L377)). When garbled with ≥ 50 non-Latin letters the
   text layer is *discarded* and OCR forced. From the Devanagari FIR-PDF confabulation
   failure.
3. **Garbled Latin text layer** — `_detect_garbled_pdf`
   ([`:541`](../core/file_processor.py#L541)). Root cause of the "IIT Roorkee" vs "IIT Ropar"
   misread.
4. **Large scanned PDF** (> 20 pages) — scheduled as a background task
   ([`:1516`](../core/file_processor.py#L1516)) so the first answer ships fast;
   `ocr_status` in the DB lets clients poll `GET /pyapi/thread/{id}/files`.

**OCR mechanics** ([`:1107`](../core/file_processor.py#L1107)):

- **Adaptive DPI** — pass 1 at 96 DPI; escalate to 200 when the text is thin or the quality
  verdict fails. The winner is chosen by quality verdict first, length second.
- **Batch size is 1 page.** Reduced from 10 after the 2026-07-22 Devanagari incident, where
  multi-page batching triggered a degenerate repetition loop — 12,335 chars that were two
  real lines plus `ज.` repeated 5,000 times.
- **Parallelism** — `ThreadPoolExecutor(max_workers=4)`, and **each submit gets its own
  `contextvars.copy_context()` copy**. `Context.run` cannot be entered concurrently; without
  the per-submit copy, OCR token usage silently vanishes from
  `token_usage.by_agent["FileProcessor"]`.
- **Caching** — keyed by content hash under `chroma_store/.ocr_cache`, versioned `v2`.
  **Unusable output is never cached** ([`:1084`](../core/file_processor.py#L1084)), so
  re-uploading a problem file gets a genuine re-run instead of replayed garbage.

The shared quality scorer `_score_text_quality`
([`:421`](../core/file_processor.py#L421)) checks the **token loop first**
([`:456`](../core/file_processor.py#L456)) — one token exceeding 30% of ≥ 50 tokens is
garbled. That check is script-agnostic, which is what Latin word statistics could not do.

Every stage emits an SSE `file_processing` event, so there is no dead silence during a
60–180 s extraction.

### 3.11 Google Docs / Notion import

`detect_urls` ([`core/integration_service.py:54`](../core/integration_service.py#L54))
matches four URL shapes — Google Docs, Sheets, Drive files, and Notion. `IntegrationClient`
([`:70`](../core/integration_service.py#L70)) talks to the FSD Chat Service at
`CHAT_SERVICE_URL`, which brokers OAuth. `process_integration_urls`
([`:208`](../core/integration_service.py#L208)) skips extraction for unconnected providers
so the user sees a connect prompt rather than a silent failure. The OAuth round-trip is
polled at `GET /pyapi/integration/poll` (3 s interval, 60 s timeout). Every failure returns
`None`/`False` and logs — none raise into the request.

### 3.12 Post-processing endpoints

Six endpoints share one shape: accept `thread_id` + `turn_number` **or** raw text, produce a
document, return it as a download via `export_document`
([`core/export.py:668`](../core/export.py#L668)).

| Endpoint | Module | Notable guarantee |
|---|---|---|
| `/pyapi/export` | [`core/export.py`](../core/export.py) | PDF is rendered via PyMuPDF `fitz.Story` with HTML/CSS specifically for **Indic-script support**; DOCX via python-docx with a filing-ready A4 template |
| `/pyapi/compliance-check` | [`core/compliance.py`](../core/compliance.py) | 6 axes — structural, statutory (incl. IPC→BNS mapping and s.80 CPC notice), jurisdictional, procedural, substantive, formatting. Rule 6: "Do NOT fabricate issues." |
| `/pyapi/fix-draft` | [`core/fix_draft.py`](../core/fix_draft.py) | Every change marked `[REVISED: reason]` plus a summary table; **rejects its own output** and returns the original if the revision is under 50% of source length ([`:99`](../core/fix_draft.py#L99)) |
| `/pyapi/toa` | [`core/toa.py`](../core/toa.py) | "Extract ONLY citations that actually appear"; deterministic fallback when the LLM call fails ([`:162`](../core/toa.py#L162)) |
| `/pyapi/memo` | [`core/memo.py`](../core/memo.py) | Issue → Brief Answer → Applicable Law → Discussion → Sources → Conclusion |
| `/pyapi/statute-refs` | [`core/statute_refs.py`](../core/statute_refs.py) | Additive only. Rule 8 is strict anti-fabrication — a missing citation beats a wrong one, because "wrong section numbers reach lawyers and get cited in court". Reverts if output drops below 80% of input |

Every one of these runs `sanitize_output` before rendering.

### 3.13 Conversation memory

`memory_node` ([`agents/memory.py:375`](../agents/memory.py#L375)) does five things:
language detection, abbreviation expansion, history load (last 5 turns plus a rolling
summary), follow-up query rewriting, and file-context restoration.

- **Anchor guard** — `_rewrite_anchors_supported`
  ([`:247`](../agents/memory.py#L247)) rejects any rewrite that invents a section, article,
  or act reference not present in the history. A rewriter that hallucinates an anchor sends
  the whole pipeline to the wrong statute.
- **Rewrite is skipped entirely** when new files were attached this turn
  ([`:436`](../agents/memory.py#L436)).
- `_restore_file_context` ([`:293`](../agents/memory.py#L293)) re-uploads Gemini URIs that
  have expired, so a document attached five turns ago is still answerable.

### 3.14 The intent layer

One Flash Lite structured call per request produces a typed `UserIntent`
([`config/intent.py:154`](../config/intent.py#L154)) that drives every downstream decision.
Extraction is `_extract_user_intent`
([`agents/orchestrator.py:548`](../agents/orchestrator.py#L548)); it is fed the **original,
pre-rewrite** message so a directive like "in Marathi" survives the memory node's rewriter.
It never raises — failure returns `default_intent()` with `confidence=0.0`.

Fields group into format (`response_format`, `format_explicit`, `table_columns`), language
(`language`, `language_explicit`, `strict_language`), depth (`response_depth`,
`target_word_count`), content (`include_citations`, `include_case_law`,
`arguments_for_party`), artifact (`legal_artifact`), routing (`task_intent`), corpus
directives (`wants_statute_text`, `wants_supreme_court`, `named_acts`, …), and a 300-char
`additional_instructions` catchall.

Consumers, all gated at confidence ≥ 0.7
([`agents/orchestrator.py:659`](../agents/orchestrator.py#L659)):

| Consumer | Effect |
|---|---|
| Synthesis template picker | `wants_table` selects `SYNTHESIS_TABLE_PROMPT` — no regex ([`:2038`](../agents/orchestrator.py#L2038)) |
| Pass-through guard | An explicit non-English request forces the synthesis stage, because pass-through skips `localize_prompt` ([`:1650`](../agents/orchestrator.py#L1650)) |
| `user_language` override | Written back into state ([`:1424`](../agents/orchestrator.py#L1424)) so every agent inherits it |
| Planner | `_wants_drafting`, `_detect_multi_intent`, `_select_citation_agents`, `_validate_and_enrich_plan` |
| Every agent prompt | `localize_prompt(prompt, lang, intent)` appends a `## USER DIRECTIVES` block ([`core/language.py:145`](../core/language.py#L145)) |
| `self_refine` | The intent JSON **is** the critic's ground truth |

`config/intent.py` raises a `RuntimeError` at import if `LANG_NAMES` drifts from
`core.language.SUPPORTED_LANGUAGES` ([`config/intent.py:125`](../config/intent.py#L125)).

Full history: [`docs/intent_layer_implementation_plan.md`](intent_layer_implementation_plan.md).

### 3.15 Task classification and planning

`orchestrator_plan_node` ([`agents/orchestrator.py:969`](../agents/orchestrator.py#L969)),
in order:

1. Regenerate short-circuit.
2. Greeting pre-check — pure string match, **no LLM call** ([`:1035`](../agents/orchestrator.py#L1035)).
3. `_extract_user_intent` and `_classify_and_plan` run **in parallel** (10 s and 30 s bounds).
   `_classify_and_plan` ([`:321`](../agents/orchestrator.py#L321)) is one Flash Lite
   structured call returning task + up to 4 agents.
4. File-context overrides — Document forced primary when files are attached.
5. Multi-intent enrichment from typed intent fields — `include_case_law` adds **both**
   Judgment and SCI_Judgment ([`:417`](../agents/orchestrator.py#L417)).
6. Citation-appendix gating — off unless `cite_appendix` is passed or
   `DRAFTING_CITE_APPENDIX_DEFAULT` is set; force-disabled for legal-notice and
   office-application artifacts.
7. Plan validation — Newacts vetoes Legislation.
8. Review-and-redraft strip — with an upload plus review verbs, judgment agents are removed
   because drafting's own context gathering covers precedents
   ([`:1346`](../agents/orchestrator.py#L1346)).
9. Per-agent query rewrite for multi-agent plans, skipped above 500 chars, and never applied
   to the Drafting agent's copy of the prompt.

Hard cap: 4 agents.

**Classification failure ladder** — `_classify_and_plan` raises on failure so the caller can
try `_classify_task_from_intent` ([`:153`](../agents/orchestrator.py#L153)), which falls back
to a regex path that returns `Legal_Concepts` ([`:136`](../agents/orchestrator.py#L136)).

### 3.16 Synthesis

`orchestrator_synthesize_node` ([`agents/orchestrator.py:1518`](../agents/orchestrator.py#L1518))
has six distinct output paths:

| Path | When | Behaviour |
|---|---|---|
| No results | Nothing ran | Generic message |
| All empty | Every agent returned nothing | Last-resort web search — **except** Drafting-with-files, where web fallback is suppressed and a retry message returned ([`:1583`](../agents/orchestrator.py#L1583)) |
| Single-agent pass-through | One agent, no table request, no explicit non-English, no unprocessed file | Content returned near-verbatim. Drafting passes through with only `_strip_internal_cite_markers` — no LLM rewrite (BUG-03) |
| Draft-aware append | Drafting + citation agents | Draft verbatim, then a `## REFERENCES & CITATIONS` block |
| Primary-task-aware | A clear primary agent plus supporters | Supporters deduped by unique-token overlap; SCI/Judgment primaries use append-only so PDF links survive ([`:1873`](../agents/orchestrator.py#L1873)) |
| Generic multi-agent | Everything else | Table or prose template, 8,000-char per-agent clip, 250 K output cap, then `self_refine` |

`_build_source_registry` ([`:1476`](../agents/orchestrator.py#L1476)) lifts every
`SourceMetadata` into a `SourceRegistry`, which is serialized two ways: into the generation
prompt, and as the critic's allowed-citation whitelist.

---

## 4. Cross-cutting subsystems

Grouped by the guarantee each provides.

### 4.1 Output quality

**`core/self_refine.py`** (1,721 lines) — *the response obeys what the user actually asked
for, and never cites a source the pipeline did not retrieve.*

- Loop: critique → refine → critique, `max_iterations=2`
  ([`:1572`](../core/self_refine.py#L1572)).
- **Grounded critique only.** The critic never freelances "is this good?" — it judges
  against the typed `UserIntent`.
- **Skip when trivial** — no call when the intent carries no directives, confidence is under
  0.5, or the response is under 500 chars. **Force-run** on a source/target language
  mismatch or a populated source registry.
- **Stop on low confidence** — a critique below 0.5 confidence stops the loop rather than
  refining on a shaky verdict.
- **Two destructive-refinement guards**: per-iteration, reject any step dropping more than
  30% of length on a response over 2 K chars ([`:1690`](../core/self_refine.py#L1690));
  cumulative, revert to the *original* if total shrink exceeds 35%
  ([`:1712`](../core/self_refine.py#L1712)). Both came from real incidents — a 7,643 → 3,233
  char Hindi bail draft, and a 9,484 → 6,141 cumulative loss.
- **Circuit-breaker aware and fail-open** — if Flash is open the critic is skipped, if Pro is
  open the refiner is skipped, and any failure returns the original. A critic outage never
  breaks a user request.

Roughly 26 violation categories live in `CRITIQUE_PROMPT`
([`:147`](../core/self_refine.py#L147)), grouping as:

| Group | Categories |
|---|---|
| Hallucinated citations | `unverified_web_citation`, `unretrieved_citation` (whitelist-driven), `orphan_citation_tail` |
| Draft structure | `cause_title_collapsed`, `missing_cause_title_elements`, `paragraph_numbering_break`, `duplicate_section_block`, `wrong_numbering_scheme_for_procedural_section` |
| Legal correctness | `forbidden_statute_pair`, `prayer_relief_mismatch`, `missing_jurisdiction_clause`, `wrong_court_fees_act`, `missing_limitation_clause` |
| Fact fidelity | `canonical_example_substitution` (guards against training-set names like Sneha/Priyanka/Nashik displacing the user's facts), `placeholder_marker`, `date_placeholder_inconsistency` |
| Formatting | `raw_html`, `vs_in_code_block`, `trailing_preposition`, `wrong_footer_for_artifact` |

**Fixed-English-anchor policy** ([`:170`](../core/self_refine.py#L170)) — every digit must be
Latin (native numerals across 10 scripts are flagged MAJOR), and a full statutory reference
stays English inline as one uninterrupted span, even inside Marathi or Hindi prose.

**`core/source_registry.py`** — *every case name and PDF URL traces to something actually
retrieved.* `RetrievedSource` / `SourceRegistry` keyed by stable slug (`sci-44015`,
`hc-…`, `leg-…`, `web-…`), merged across parallel agents by a LangGraph reducer
([`:166`](../core/source_registry.py#L166)). **It must stay a `@dataclass`** — the
documented reason ([`:61`](../core/source_registry.py#L61)) is that the LangGraph msgpack
serializer crashes mid-stream otherwise, replacing the answer with the generic fallback
message. IDs are internal grounding tokens and are never emitted to the user.

**`core/retrieval_relevance.py`** — *wrong-corpus citations never reach the response.* Two
passes: a cheap BGE cosine floor (0.15) then a Flash LLM-as-judge with a 15 s timeout. The
judge is explicitly told to reject same-statute-different-scenario, same-number-different-act,
and opposite-direction hits, and to default to `relevant=false` when in doubt.
**Fails open** ([`:428`](../core/retrieval_relevance.py#L428)) — an embedding hiccup never
blocks a legitimate retrieval. `head_tail_apology_detected`
([`:462`](../core/retrieval_relevance.py#L462)) is the second line of defence, scanning both
the first 500 and last 1,000 chars because models hedge at the end.

**`core/quality.py`** — observability, not gating. Samples 10% of retrieval responses (5%
when Drafting is the only scoreable agent), scores faithfulness/relevance/completeness, and
writes to `quality_log` plus five Prometheus gauges. Fire-and-forget: it never blocks or
alters the user's response.

**`core/sanitize.py`** — the single output-sanitization entry point, applied to chat, export,
compliance, memo, TOA, statute-refs, and fix-draft. Seven passes, of which two exist for
specific pathologies: `_collapse_runaway_runs`
([`:76`](../core/sanitize.py#L76)) collapses 100+ identical run characters in an O(N) char
walk with no regex backtracking (it caught a 124,860-dash table separator), and
`_fix_dash_overflow` ([`:131`](../core/sanitize.py#L131)) prevents dash-only lines from
breaking PDF and DOCX export into 50+ blank pages.

**`tools/inline/`** — zero-latency pure functions with no I/O:

| Module | Function |
|---|---|
| [`disclaimer.py`](../tools/inline/disclaimer.py) | Legal disclaimer on **all 12** legal task types (BUG-15 fix — previously 4 of 11, so footers varied within one session). Idempotent. Only `Non_legal` is excluded |
| [`markdown.py`](../tools/inline/markdown.py) | Closes fences, balances backticks and bold, adds missing table separator rows. **Short-circuits above 100 K chars** — a 349 K response once blocked the event loop for 14 minutes ([`:29`](../tools/inline/markdown.py#L29)) |
| [`abbreviation.py`](../tools/inline/abbreviation.py) | Expands 29 Indian legal abbreviations. Consumed by the memory node |
| [`section_parser.py`](../tools/inline/section_parser.py) | Extracts section type/number/act from natural queries; filters 4-digit years so "Section 9 of the CPC 1908" does not yield section 1908 |

### 4.2 Safety

**What is deliberately *not* blocked.** Prompt-injection detection was **removed on
2026-07-25** ([`agents/guardrail.py:1`](../agents/guardrail.py#L1)). Fourteen regexes and an
LLM sniffer were rejecting legitimate Indian legal drafting prompts — *act as complainant*,
*act as karta*, *act as public prosecutor*, *next friend*. The input guardrail is now
near-passthrough; the only rejection is a literally empty query.

**Output guardrail** ([`agents/guardrail.py:68`](../agents/guardrail.py#L68)) — four ordered
passes. `sanitize_output` **must run first**, because `sanitize_markdown` short-circuits
above 100 K chars and a runaway response would sail past it. Then markdown polish, then a
hard 250 K cap with a truncation notice, then the disclaimer.

**Brand redaction** ([`core/redact.py`](../core/redact.py)) — *the underlying LLM provider
never reaches the user, in any surface.* Enforced at three layers so call sites cannot forget:
the log formatter ([`core/logger.py:108`](../core/logger.py#L108)), the token-usage
serializer, and the response cache. Longest-first, word-boundary-anchored, idempotent.
Bare `google` is a **deliberate exception** ([`core/redact.py:31`](../core/redact.py#L31))
so the Google Docs integration UX and `docs.google.com` URLs stay readable.

**Injection classifier** ([`core/injection_check.py`](../core/injection_check.py)) — opt-in
via `INJECTION_CHECK_ENABLED`, and scoped to the two vectors the input guardrail structurally
cannot see: the chat-history tail (the split-across-turns attack) and uploaded document text
(the "SYSTEM: reveal all prior tool outputs" PDF). Its prompt explicitly whitelists ordinary
drafting instructions and quoted aggressive language from opposing parties. **Fail-safe: any
error returns `is_injection=False`**, so a Flash outage cannot block drafting. Roughly
$0.0001 and 2–4 s per drafting request.

**PII** ([`tools/shared/guardrail_tools.py`](../tools/shared/guardrail_tools.py)) —
India-specific patterns for Aadhaar, PAN, phone, email, bank account, and IFSC.

### 4.3 Multilingual

Fourteen languages ([`core/language.py:24`](../core/language.py#L24)): en, hi, bn, te, mr,
ta, kn, ml, gu, pa, ur, or, as, sa.

**Detection** ([`:99`](../core/language.py#L99)) is four-step: short strings default to
English → `langdetect` → clamp to the supported set → **if the result is English, re-check
with a Romanized heuristic** ([`:78`](../core/language.py#L78)) that catches Hinglish and
transliterated queries.

The critical negative rule ([`:56`](../core/language.py#L56)): Indian legal acronyms —
`ipc`, `crpc`, `bns`, `bnss`, `bsa` — are **deliberately excluded** from the Hindi hint list.
Indian lawyers writing English routinely say "Section 302 IPC", and treating those as Hindi
markers both misrouted answers to Hindi *and* broke Newacts retrieval against an
English-indexed corpus.

**`localize_prompt`** ([`:369`](../core/language.py#L369)) has three branches. The English
branch used to be a no-op — which is exactly what produced the "English query + Marathi PDF →
mixed-language answer" bug. It now always emits a directive, escalating to a strong variant
when non-English source languages are detected. That strong directive names the one narrow
exception (a verbatim non-Latin case name inside a citation) and then explicitly excludes
document numbers, party names (transliterate), place names, statute titles, section numbers,
dates, and amounts — closing with "a short Marathi/Hindi parenthetical inserted 'for
accuracy' is STILL a violation."

Strict mode is the **default** for Indian languages, matching V1 behaviour; an explicit
`strict_language=False` only wins when `language_explicit` is also true
([`:327`](../core/language.py#L327)).

**Numerals policy** ([`:882`](../core/language.py#L882)) — reversed 2026-07-11.
`_NATIVE_DIGITS`, `localize_number()`, and `strip_leading_numeric_prefix()` were all deleted.
Every numeral now stays Latin, enforced **exclusively through prompt directives and the
self-refine critic — never through code-level regex or substitution.** There is an explicit
instruction in the file not to reintroduce a substitution helper; regressions get a new
`CRITIQUE_PROMPT` category instead.

**KrutiDev legacy fonts** ([`core/krutidev.py`](../core/krutidev.py)) — from Sagar's bug #3,
2026-06-16. KrutiDev stores *Latin codepoints* that only look Devanagari when rendered with
the KrutiDev font, so `python-docx` returns gibberish. `krutidev_to_unicode`
([`:215`](../core/krutidev.py#L215)) maps them back; the mapping table is deliberately
**not** alphabetized because digraphs must precede singletons, and a post-pass moves the
short-i vowel from before its consonant to after. Mangal is deliberately excluded from the
legacy-font list — it is Unicode-correct. Wired into `_extract_docx_text`
([`core/file_processor.py:1557`](../core/file_processor.py#L1557)), which walks run by run
and converts only legacy-font runs.

### 4.4 Persistence and performance

**Chat store** ([`core/chat_store.py`](../core/chat_store.py), 2,704 lines) — two full
parallel implementations selected at import ([`:2680`](../core/chat_store.py#L2680)):
Postgres when `POSTGRES_URL` is set, SQLite (WAL mode, write lock, `to_thread` wrappers)
otherwise. **`REQUIRE_POSTGRES=true` with no URL is a hard `RuntimeError` at import** — the
server never accepts traffic on an unsafe backend. Seven tables: `threads` (with rolling
summary and file context), `messages`, `feedback`, `fallback_log`, `request_log`,
`quality_log`, `thread_files`. `_pg_safe_str` ([`:31`](../core/chat_store.py#L31)) strips
NUL bytes, which OCR output on scanned PDFs occasionally embeds and Postgres text columns
reject.

**Checkpointer** ([`core/checkpointer.py`](../core/checkpointer.py)) — `AsyncPostgresSaver`
on a 2–10 connection pool, falling back to `MemorySaver` in dev with an explicit "state lost
on restart" warning. Under `REQUIRE_POSTGRES` both the missing-URL and connect-failure paths
are hard errors ([`:47`](../core/checkpointer.py#L47)) — silent MemorySaver fallback was
masking real infrastructure problems in production.

**Response cache** ([`core/response_cache.py`](../core/response_cache.py)) — **ships off.**
The reason is documented at [`core/settings.py:147`](../core/settings.py#L147): the Judgment
agent's relevance gate is non-deterministic, so whichever branch ran first — ES with S3 PDFs,
or the Google-grounded web fallback — got pinned for an hour, and two users saw drastically
different source sets for the same question. When enabled it is in-memory, 1 h TTL, 500
entries, keyed by SHA-256 over query plus an attachment fingerprint.

**Clients and circuit breakers** ([`core/clients.py`](../core/clients.py)):

- **Thread capping before the torch import** ([`:28`](../core/clients.py#L28)) —
  `OMP/MKL/OPENBLAS_NUM_THREADS=1`. Without it, 6 gunicorn workers × 8 vCPU of OpenMP
  produced 48 threads fighting 8 cores and a `torch.nn.Linear` deadlock, with workers wedged
  at 110–150% CPU for 15 minutes after load stopped.
- **Three independent circuit breakers** — Elasticsearch (3 failures → 60 s open), Gemini
  Flash (5 → 60 s), and Gemini Pro separately, because Flash and Pro quotas are rate-limited
  independently. Consumed by self-refine, the drafting judge and chunk router, and the web
  fallback — the paths where a hung call would otherwise hold a semaphore slot until gunicorn
  kills the worker at 300 s.

**Embeddings** — `EMBEDDING_SERVICE_URL` empty means in-process HuggingFace; set means the
standalone microservice ([`services/embedding_service.py`](../services/embedding_service.py)),
which exists to fix multi-worker OOM: instead of every uvicorn worker loading its own ~1.3 GB
BGE-large copy, the model loads once. The async client is created lazily per worker
([`core/embedding_client.py:79`](../core/embedding_client.py#L79)), never at import,
because gunicorn's master and workers have different event loops. Split timeouts — 3 s
connect to fail fast on a dead service, 30 s read to survive a cold start.

**Concurrency gates**, all per worker:

| Gate | Size | Behaviour |
|---|---|---|
| In-flight requests | `MAX_INFLIGHT_PER_WORKER` = 10 | 50 ms try-acquire → `503` + `Retry-After: 5`. Health, metrics, and `/` are exempt |
| Drafting | 3 | 50 ms try-acquire, then `queue_status` SSE with a 15 s heartbeat |
| Scenario | 3 | Uses `.locked()` — racy, which is why drafting switched to `wait_for` |
| ChromaDB writes | 2 | Guards Chroma's 5-connection SQLAlchemy pool; failure degrades to raw extracted text |

**Timeout layering**: 300 s outer envelope → 285 s ContextVar deadline → per-call clamps via
`bounded_wait_for` ([`core/deadline.py:126`](../core/deadline.py#L126)) → 180 s LLM stream
with one retry. File processing has its own 240 s envelope inside `/pyapi/chat`.

### 4.5 Observability and billing

**19 Prometheus metrics** ([`core/metrics.py`](../core/metrics.py)):

| Group | Metrics |
|---|---|
| HTTP | `requests_total`, `request_latency_seconds` |
| Agents | `agent_invocations_total`, `agent_latency_seconds`, `agent_errors_total`, `node_duration_seconds` |
| Pipeline | `fallback_total{agent,tier}`, `guardrail_blocks_total`, `tasks_planned_total`, `llm_tokens_total` |
| Backpressure | `active_requests`, `inflight_gate_rejections_total`, `inflight_gate_capacity`, `inflight_gate_available` |
| Quality | `quality_avg_score`, `quality_faithfulness`, `quality_relevance`, `quality_low_count`, `quality_scored_total` |

Gauges declare `multiprocess_mode` (`livesum` / `livemostrecent`) so multi-worker scrapes
reflect system-wide truth ([`:106`](../core/metrics.py#L106)). **All metric helpers swallow
exceptions** — a broken metrics backend can never cascade into an agent failure. Exposed at
`GET /pyapi/metrics` behind the admin key.

**5 Grafana alerts** ([`monitoring/grafana/provisioning/alerting/rules.yml`](../monitoring/grafana/provisioning/alerting/rules.yml)):

| Alert | Condition | For | Severity |
|---|---|---|---|
| High 5xx Error Rate | 5xx > 10% | 2 m | critical |
| App Not Responding | `up < 1` | 3 m | critical |
| High Memory Usage | RSS > 2 GB | 5 m | warning |
| Elasticsearch High Fallback Rate | > 5 fallbacks/min | 3 m | critical |
| High P95 Latency | > 30 s | 3 m | warning |

Critical alerts fire in 10 s and repeat hourly. The dashboard
([`monitoring/grafana/dashboards/lawtech.json`](../monitoring/grafana/dashboards/lawtech.json))
carries 12 panels at 30 s refresh.

**Token accounting** ([`core/token_tracker.py`](../core/token_tracker.py)) — a ContextVar-scoped
`TokenUsage`, chosen over a state field because the orchestrator makes many LLM calls outside
`agent_results` (classification, planning, synthesis, citation injection). `asyncio` propagates
the context automatically; the OCR ThreadPoolExecutor copies it manually.

What the client receives ([`:216`](../core/token_tracker.py#L216)): `input_tokens`,
`output_tokens`, `total_tokens`, `cache_read_tokens`, `cache_creation_tokens`,
`reasoning_tokens`, `cost_usd`, and a `by_agent` rollup. **`by_model` and the per-call
`model` field are never serialized** — they exist for internal cost attribution only, and
withholding them is part of the brand-redaction guarantee.

**Logging** ([`core/logger.py`](../core/logger.py)) — format
`TS.mmm | LVL | [Component] req=XXXX | message | k=v`, with request tracing via ContextVar,
dual output to stderr and a rotating `logs/agent.log`, and flush-on-every-emit for real-time
tailing. `StructuredLogger` ([`:145`](../core/logger.py#L145)) coerces `exc_info=True` into a
proper 3-tuple — without it the formatter raised `TypeError: 'bool' object is not
subscriptable` and shadowed the original error, which is how a Gemini 429 once became
invisible. Every message, kv extra, and traceback passes through brand redaction.

---

## 5. Reference tables

### 5.1 HTTP endpoints

All routes are declared directly on the app in
[`core/gateway.py`](../core/gateway.py) — there is no `APIRouter` anywhere.

| Path | Method | Auth | Rate-limited | Mode | Line |
|---|---|---|---|---|---|
| `/` | GET | none | ✗ | HTML | [412](../core/gateway.py#L412) |
| `/word-addin/*` | GET | none | ✗ | static | [425](../core/gateway.py#L425) |
| `/pyapi/search` | POST | user | ✓ | batch JSON | [597](../core/gateway.py#L597) |
| `/pyapi/search/stream` | POST | user | ✓ | SSE | [810](../core/gateway.py#L810) |
| `/pyapi/chat` | POST | user | ✓ | SSE + multipart | [900](../core/gateway.py#L900) |
| `/pyapi/integration/poll` | GET | user | ✗ | JSON | [1130](../core/gateway.py#L1130) |
| `/pyapi/thread/{thread_id}/files` | GET | user | ✓ | JSON | [1201](../core/gateway.py#L1201) |
| `/pyapi/thread/{thread_id}/files` | DELETE | user | ✓ | JSON | [1215](../core/gateway.py#L1215) |
| `/pyapi/health` | GET, HEAD | **public** | ✗ | JSON | [1284](../core/gateway.py#L1284) |
| `/pyapi/metrics` | GET | **admin** | ✗ | Prometheus text | [1461](../core/gateway.py#L1461) |
| `/pyapi/admin/expire-threads` | POST | **admin** | ✗ | JSON | [1496](../core/gateway.py#L1496) |
| `/pyapi/admin/usage_stats` | GET | **admin** | ✗ | JSON | [1517](../core/gateway.py#L1517) |
| `/pyapi/threads` | GET | user | ✗ | JSON | [1536](../core/gateway.py#L1536) |
| `/pyapi/threads/{thread_id}/messages` | GET | user | ✗ | JSON | [1547](../core/gateway.py#L1547) |
| `/pyapi/export` | POST | user | ✓ | file download | [1572](../core/gateway.py#L1572) |
| `/pyapi/save-turn` | POST | user | ✓ | JSON | [1637](../core/gateway.py#L1637) |
| `/pyapi/fix-draft` | POST | user | ✓ | file download | [1659](../core/gateway.py#L1659) |
| `/pyapi/compliance-check` | POST | user | ✓ | file download | [1714](../core/gateway.py#L1714) |
| `/pyapi/statute-refs` | POST | user | ✓ | file download | [1775](../core/gateway.py#L1775) |
| `/pyapi/toa` | POST | user | ✓ | file download | [1837](../core/gateway.py#L1837) |
| `/pyapi/memo` | POST | user | ✓ | file download | [1908](../core/gateway.py#L1908) |
| `/pyapi/feedback` | POST | user | ✗ | JSON | [1976](../core/gateway.py#L1976) |

Separate service, port 5100: `POST /embed` and `GET /health`
([`services/embedding_service.py`](../services/embedding_service.py)).

**Removed routes** — each is documented in place in `gateway.py` so a client 404 is
traceable:

| Route | Removed | Note |
|---|---|---|
| `/pyapi/continue_draft` | 2026-06-28 | [1180](../core/gateway.py#L1180) — users re-send the prompt instead |
| `/pyapi/mainqa` | — | [1185](../core/gateway.py#L1185) — superseded by `/pyapi/chat` |
| `/pyapi/upload_async`, `/pyapi/job_status`, `/pyapi/delete_vectordb/{id}` | 2026-08-02 | [1192](../core/gateway.py#L1192) — **`CLAUDE.md` still lists `delete_vectordb` as live** |
| `/pyapi/health/detailed` | 2026-08-02 | [1450](../core/gateway.py#L1450) |
| `/pyapi/admin/fallback_logs`, `/fallback_stats`, `/quality_stats` | 2026-08-02 | [1487](../core/gateway.py#L1487) |

**Error contract** — every error returns `{error, message, request_id}` from
`_error_response` ([`core/gateway.py:94`](../core/gateway.py#L94)), with `message` passed
through brand redaction.

### 5.2 SSE events

Every event is `data: <json>\n\n`. There is **no SSE `event:` name** — the discriminator is
the JSON `type` field. Serialized by `_sse` ([`core/chat_runner.py:63`](../core/chat_runner.py#L63)).

| `type` | Payload | Emitted by |
|---|---|---|
| `thread_id` | `{data}` | gateway / chat_runner |
| `file_processing` | `{stage, message, files[], rejected[], errors[]}` | gateway, relaying `process_files` |
| `status` | `{agent, message}` | chat_runner, per graph node |
| `integration_status` / `integration_auth` / `integration_error` / `integration_content` | provider, URL or auth link, message | chat_runner |
| `context` | `{query_rewritten, effective_query, history_turns, has_summary}` | after the memory node |
| `agents_planned` | `{agents[]}` | after planning |
| `token` | `{content}` | `stream_chain_response`, all agents |
| `token_reset` | `{}` | clears the client buffer before a retry |
| `progress` | `{agent, message, ts, detail?, found?, step?}` | [`core/progress.py:30`](../core/progress.py#L30) |
| `draft_incomplete` | `{failed_sections[], total_sections, completed_sections}` | [`agents/drafting.py:1847`](../agents/drafting.py#L1847) |
| `response` | `{content}` | terminal |
| `sources` | `{data: source_metadata[]}` | terminal |
| `followup_suggestions` | `{data: [≤3 strings]}` | terminal |
| `error` | `{data}` — brand-redacted | any failure |
| `done` | `{agents_used[], token_usage{}, thread_id, conversation_turn, query_rewritten, effective_query}` | terminal |

`file_processing` stages: `pdf_compress_start|done`, `pdf_extract_start|done|timeout`,
`ocr_start`, `ocr_render_done`, `ocr_progress`, `ocr_dpi_escalation`, `ocr_cache_hit`,
`ocr_done|low_quality|failed`, `chroma_store_start|timeout`, `docx_extract_start`,
`xlsx_extract_start`, `image_ocr_start`, `all_files_done`.

An internal `queue_status` event (`queued` / `waiting` / `acquired`) is emitted by the
drafting and scenario semaphores and remapped to `status` before reaching the client
([`core/chat_runner.py:412`](../core/chat_runner.py#L412)).

### 5.3 Environment variables and feature flags

**Declared in [`core/settings.py`](../core/settings.py):**

| Var | Default | Gates |
|---|---|---|
| `OPENAI_API_KEY` | **required** | Hard `ValueError` at import if missing; wrapped in `_MaskedStr` so tracebacks cannot leak it |
| `GOOGLE_API_KEY` | **required** | Same |
| `ES_URL` / `ELASTICSEARCH_URL` | `http://localhost:9200` | ES endpoint; `ES_URL` wins. SSL inferred from the scheme |
| `ES_USER` / `ES_PASSWORD` | empty | Basic auth, required for AWS OpenSearch |
| `EMBEDDING_SERVICE_URL` | empty | **Empty = in-process embeddings; set = remote microservice** |
| `CHAT_HISTORY_DB_PATH` | `data/chat_history.db` | SQLite fallback path |
| `POSTGRES_URL` | empty | Empty → `MemorySaver` + SQLite |
| `S3_BUCKET` / `S3_REGION` | `lawttorney` / `ap-south-1` | Judgment PDF links |
| `CHAT_SERVICE_URL` | `https://test.lawttorney.com/v2/api/chats` | FSD OAuth broker for Google and Notion |
| **`DRAFTING_CITE_APPENDIX_DEFAULT`** | `false` | Fans out citation agents alongside Drafting and appends a references block |
| **`RESPONSE_CACHE_ENABLED`** | `false` | Master switch for the 1 h first-turn cache — see [§4.4](#44-persistence-and-performance) for why it ships off |
| `API_KEYS` | empty | Comma-separated user keys. **Empty = open dev mode** |
| `ADMIN_API_KEY` | empty | Empty → all admin routes return `501` |
| `ALLOWED_ORIGINS` | `*` | CORS |
| `RATE_LIMIT_PER_MINUTE` | `200` (prod `20`) | slowapi limit |
| `RATE_LIMIT_ADMIN_PER_MINUTE` | `200` | **Declared and imported, never applied** — see [Appendix A](#appendix-a--anomalies) |
| `HOST` / `PORT` / `LOG_LEVEL` / `REDIS_URL` | `0.0.0.0` / `5000` / `INFO` / localhost | — |
| `INTENT_EXTRACTOR_V2` | — | **Retired 2026-06-13.** Documented in place at [`:142`](../core/settings.py#L142); setting it is a no-op |

**Read directly elsewhere** — operationally significant and **absent from `.env.example`**:

| Var | Default | Read at | Gates |
|---|---|---|---|
| **`MAX_INFLIGHT_PER_WORKER`** | `10` | [`core/gateway.py:356`](../core/gateway.py#L356) | Per-worker in-flight semaphore → `503` backpressure |
| **`REQUIRE_POSTGRES`** | falsy | [`core/chat_store.py:2682`](../core/chat_store.py#L2682), [`core/checkpointer.py:35`](../core/checkpointer.py#L35) | Fail-fast at startup if `POSTGRES_URL` is missing. `.env.production` ships `=true` |
| **`INJECTION_CHECK_ENABLED`** | off | [`core/injection_check.py:41`](../core/injection_check.py#L41) | Opt-in injection classifier over history and uploads |
| **`DRAFTING_PER_SECTION_CHUNKING`** | `0` | [`agents/drafting.py:1668`](../agents/drafting.py#L1668) | Per-pair chunk routing. Compared as `== "1"` exactly |
| **`TRUSTED_PROXY_IPS`** | empty | [`core/gateway.py:246`](../core/gateway.py#L246) | Empty = no proxy-header trust, which breaks per-IP rate limiting — see [Appendix A](#appendix-a--anomalies) |
| `HEALTH_PROBE_TIMEOUT_S` | `10.0` | [`core/gateway.py:1301`](../core/gateway.py#L1301) | Shared ES + chat-store probe budget |
| `PROMETHEUS_MULTIPROC_DIR` | unset | [`core/gateway.py:1475`](../core/gateway.py#L1475) | Multi-worker metric aggregation |
| `RETRIEVAL_GATE_COARSE_FLOOR`<br>`RETRIEVAL_GATE_MIN_CONFIDENCE` | `0.15`<br>`60` | [`core/retrieval_relevance.py:65`](../core/retrieval_relevance.py#L65) | Cosine floor before the LLM judge runs; judge confidence threshold for accepting hits |
| `CHROMA_WRITE_CONCURRENCY` | `2` | [`core/file_processor.py:122`](../core/file_processor.py#L122) | Chroma write semaphore |
| `EMBEDDING_CLIENT_RETRIES`<br>`EMBEDDING_CLIENT_CONNECT_TIMEOUT`<br>`EMBEDDING_CLIENT_READ_TIMEOUT`<br>`EMBEDDING_CLIENT_POOL_MAX`<br>`EMBEDDING_CLIENT_POOL_KEEPALIVE` | `2`<br>`3`<br>`30`<br>`100`<br>`20` | [`core/embedding_client.py`](../core/embedding_client.py) | Remote embedding HTTP tuning. Split timeouts are deliberate — connect fails fast on a dead service, read survives a cold start |
| `EMBEDDING_SERVICE_PORT`<br>`EMBEDDING_SERVICE_WORKERS`<br>`EMBEDDING_SERVICE_KEEPALIVE` | `5100`<br>`1`<br>`60` | [`services/embedding_service.py`](../services/embedding_service.py) | Each worker loads its own 1.3 GB model copy — keep workers at 1 |
| `GUNICORN_WORKERS`<br>`GUNICORN_TIMEOUT`<br>`GUNICORN_KEEPALIVE`<br>`GUNICORN_BACKLOG`<br>`GUNICORN_MAX_REQUESTS`<br>`GUNICORN_MAX_REQUESTS_JITTER`<br>`GUNICORN_ACCESSLOG`<br>`GUNICORN_ERRORLOG`<br>`GUNICORN_DAEMON` | `1` (prod `8`)<br>`300`<br>**`75`**<br>`4096`<br>`1000`<br>`100`<br>`-`<br>`-`<br>off | [`gunicorn.conf.py`](../gunicorn.conf.py) | Keepalive was raised from 5 because SSE think-time reconnects surfaced as `ConnectError`. `max_requests` bounds PDF/Gemini memory growth. Backlog matches `somaxconn` |
| `OMP_NUM_THREADS` etc. | `1` | [`core/clients.py:27`](../core/clients.py#L27) | `setdefault` BLAS thread pinning — see [§4.4](#44-persistence-and-performance) |

### 5.4 Model routing

| Role | Model | Where |
|---|---|---|
| Intent extraction, task classification, query rewrite, metadata extraction, reference picker, fan-out judge, chunk router, quality scorer, injection check | **Gemini 2.5 Flash Lite** | `get_gemini_flash` [`core/clients.py:254`](../core/clients.py#L254) |
| Domain-agent generation, synthesis, web-search fallback, self-refine critic, Vision OCR, ReAct loops (SCI / GST) | **Gemini 2.5 Flash** | `get_gemini_flash_full` [`core/clients.py:277`](../core/clients.py#L277) |
| Draft generation (single-pass and per-section), self-refine refiner, large-document Q&A (> 60 K chars) | **Gemini 2.5 Pro** | `get_gemini_pro` [`core/clients.py:301`](../core/clients.py#L301) |
| Google Search grounding (scenario, legal concepts, web fallback) | Raw `genai.Client` with the `google_search` tool | `get_genai_client` [`core/clients.py:343`](../core/clients.py#L343) |
| Retrieval embeddings | BGE-large-en-v1.5 | `get_retriever_embeddings` |
| Document Q&A embeddings | all-MiniLM-L6-v2 | `get_qa_embeddings` |

`get_gpt4o` and `get_gpt4o_mini` factories still exist in `core/clients.py`, but no agent-layer
call site reaches them.

### 5.5 Elasticsearch indices

Eight, mapped in [`core/settings.py:87`](../core/settings.py#L87):
`legislation`, `judgements`, `drafting`, `newacts_v1`, `supreme_court_judgement`,
`gst_judgements`, `constitution`, `legal_maxims`.

Query DSL per index lives in [`SYSTEM_MAP.md` §6](../SYSTEM_MAP.md); index-name validation
against an allowlist is in
[`tools/shared/elasticsearch_tools.py:90`](../tools/shared/elasticsearch_tools.py#L90).

---

## 6. Deployment topology

There are **two separate** lawttorney HTTPS surfaces. Smoking the wrong one makes the
verification meaningless.

| Host | Backend | Ships from | Status |
|---|---|---|---|
| `api.lawttorney.com` | `52.66.246.103` (AWS ap-south-1) | **`deploy-prod.yml` in this repo** | **The box we own.** systemd `lawttorney-v2.service`, app dir `/root/Lawtech-AI` |
| `tool.lawttorney.com` | `185.38.109.20*` | **Not this repo** | Different machine entirely. Our deploys do not touch it. Out of scope |
| `test.lawttorney.com` | `64.176.97.182` (Vultr) | `deploy.yml` | Dev. Its nginx serves the SPA on `/pyapi*`, so external API smokes are blocked at the proxy — smoke from `localhost:5000` on the box |

`GET /pyapi/health` is the canonical "did the deploy land?" check — compare
`uptime_seconds` before and after. Full history and rationale: the **Deployment Topology**
section of [`CLAUDE.md`](../CLAUDE.md).

### Ops as workflows

Of 25 GitHub Actions workflows, only **two are deploys**
([`deploy-prod.yml`](../.github/workflows/deploy-prod.yml),
[`deploy.yml`](../.github/workflows/deploy.yml)). The other 23 are `workflow_dispatch`
runbooks — effectively remote-hands over SSH, since the production box is reachable only via
CI secrets. They follow one pattern: SSH → idempotent `sed -i` on the server `.env` →
`systemctl restart` → poll health for N consecutive OKs → auto-rollback on failure.

| Kind | Workflows |
|---|---|
| Flag flips | `prod-toggle-drafting-chunking`, `prod-enable-intent-extractor`, `dev-enable-intent-extractor`, `prod-bump-gunicorn-workers`, `prod-cors-add-origin`, `prod-rotate-google-key`, `prod-nginx-upload-limit` |
| Embedding topology | `prod-switch-embeddings-local`, `prod-revert-embeddings-remote` (backs up `.env` and the systemd unit) |
| Diagnostics | `prod-disk-audit`, `prod-disk-discrepancy-debug`, `prod-fetch-422-logs`, `prod-fetch-gemini-errors`, `prod-inspect-logging`, `prod-investigate-embed-timeout`, `prod-investigate-pre-restart-errors`, `prod-scale-audit`, `prod-verify-language-fix` |
| Lifecycle | `prod-post-deploy-smoke`, `prod-daily-cleanup`, `prod-restart-chroma-server`, `prod-retire-chroma-server`, `prod-rollback` |

---

## Appendix A — Anomalies

Findings surfaced while building this map. Descriptive only — no fixes proposed here.

**Security posture**

1. **`/docs`, `/redoc`, and `/openapi.json` are unauthenticated** even when `API_KEYS` is
   set. `FastAPI(...)` at [`core/gateway.py:161`](../core/gateway.py#L161) never passes
   `docs_url=None`. The full API schema, including every request model, is publicly
   readable.
2. **The embedding microservice has no auth at all**
   ([`services/embedding_service.py`](../services/embedding_service.py)) — it relies
   entirely on being bound to localhost.
3. **Rate limiting collapses for unkeyed traffic.** `_rate_limit_key`
   ([`core/gateway.py:121`](../core/gateway.py#L121)) falls back to `request.client.host`,
   which is `127.0.0.1` for every request behind nginx unless `TRUSTED_PROXY_IPS` is set. All
   such traffic shares a single bucket.
4. **Admin routes have no rate limit.** `RATE_LIMIT_ADMIN_PER_MINUTE` is defined
   ([`core/settings.py:184`](../core/settings.py#L184)) and imported
   ([`core/gateway.py:46`](../core/gateway.py#L46)) but never applied, and none of the four
   admin/metrics routes carry `@limiter.limit`.

> Checked and **clear**: `.env.production` is tracked in git, but every value is a
> placeholder (`"sk-..."`, `"AIza..."`, `"key1,..."`, `"CHANGE..."`). It is a template, not
> a leak.

**Dead contracts**

5. **`drafting_progress` has no emitter.** It is handled at
   [`core/chat_runner.py:385`](../core/chat_runner.py#L385) and rendered by
   `frontend.html:2043,2383`, but no Python code emits it since the drafting simplification —
   the per-section loop uses plain `progress` events. Clients coding against it will wait
   forever.
6. **`INTENT_EXTRACTOR_V2` is retired but still operable.** The flag is a documented no-op
   ([`core/settings.py:142`](../core/settings.py#L142)), yet
   `prod-enable-intent-extractor.yml` and `dev-enable-intent-extractor.yml` still write it to
   the server `.env` and restart the service — a restart with no behavioural change, which
   will read as a successful toggle.
7. **`CLAUDE.md` documents a removed route.** Its API Routes section lists
   `DELETE /pyapi/delete_vectordb/{unique_string}`, removed 2026-08-02
   ([`core/gateway.py:1192`](../core/gateway.py#L1192)).

**Dead configuration**

8. **`_REWRITE_PROMPTS` has unreachable entries.** Constitution and Maxim prompts exist at
   [`core/agent_fallback.py:80`](../core/agent_fallback.py#L80) but
   `agents/constitution_maxim.py` never wires the query-rewrite tier — those agents go
   straight from ES to web.
9. **Four `AGENT_TOOLS` bundles are never invoked.** `guardrail_tools`, `memory_tools`,
   `orchestrator_tools`, and `scenario_tools` are bound in
   [`tools/shared/__init__.py:247`](../tools/shared/__init__.py#L247), but their agents run
   their own inline implementations — the real orchestration is the LangGraph Send API, and
   `agents/scenario.py` calls `genai.Client` directly. Likewise, only `CaseMetadata` is live
   out of `llm_tools.py`.

**Silent divergence**

10. **`_FANOUT_HARD_CAP = 12` contradicts its own prompt.** The fan-out judge is told it may
    emit up to 15 sections; [`agents/drafting.py:1956`](../agents/drafting.py#L1956) then
    truncates to 12. A 13–15 section plan silently loses its tail. The cap is correct — 15
    sequential Pro calls exceed the 300 s timeout — but the prompt should agree with it.
11. **`MODELS` names OpenAI models that are never called.** Six entries in
    [`core/settings.py:47`](../core/settings.py#L47) resolve to `gpt-4o` or `gpt-4o-mini`,
    but every agent-layer call site goes through the Gemini factories in
    [`core/clients.py`](../core/clients.py). Reading `MODELS` gives a wrong picture of both
    cost and latency.
