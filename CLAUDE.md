# Lawtech-AI

Legal AI backend powered by FastAPI, LangGraph, LangChain, and multiple LLM providers.

## Project Overview

This is a **Legal AI API** that provides intelligent legal assistance including:
- Legal document Q&A (Judgments, Legislation, Drafts, New Acts)
- Scenario-based legal analysis with web search grounding
- PDF upload and processing with vector storage
- Legal concept explanations
- Legal drafting assistance with per-section generation and continuation

## Tech Stack

- **Framework**: FastAPI (Python), runs on port 5000 via uvicorn
- **Agent Orchestration**: LangGraph multi-agent system with 11 agents
- **LLM Orchestration**: LangChain
- **LLM Providers**: OpenAI (GPT-4o for orchestration/metadata), Google GenAI (Gemini 2.5 Flash/Pro for generation), Google Search grounding for web fallback
- **Vector Store**: ChromaDB with HuggingFace embeddings (BGE-large-en-v1.5, all-MiniLM-L6-v2)
- **PDF Processing**: PyMuPDF (fitz), with Gemini Vision fallback for scanned PDFs
- **Storage**: AWS S3 (lawttorney bucket, ap-south-1) for judgment PDFs, SQLite for chat history
- **Search**: Elasticsearch for legal document retrieval

## Project Structure

```
├── core/
│   ├── gateway.py           # FastAPI app, SSE streaming, all API endpoints
│   ├── graph.py             # LangGraph graph definition and compilation
│   ├── state.py             # Shared state schema (LegalAgentState)
│   ├── settings.py          # All configuration, env vars, model IDs
│   ├── clients.py           # LLM client singletons (GPT-4o, Gemini, ES)
│   ├── chat_store.py        # SQLite chat history store
│   ├── agent_fallback.py    # Query rewrite + web search fallback utilities
│   └── logger.py            # Structured logging setup
├── agents/
│   ├── guardrail.py         # Input/output guardrail agents
│   ├── memory.py            # Chat history + query rewriting agent
│   ├── orchestrator.py      # Task planning + result synthesis agent
│   ├── legislation.py       # Legislation search agent (ES)
│   ├── judgment.py          # High Court judgment search agent (ES + S3)
│   ├── sci_judgment.py      # Supreme Court judgment search agent (ES + S3)
│   ├── newacts.py           # New acts search agent (ES)
│   ├── drafting.py          # Legal document drafting agent (per-section)
│   ├── scenario.py          # Scenario analysis agent (web grounded)
│   ├── constitution_maxim.py # Constitution + Maxim + Legal Concepts agent
│   └── document.py          # PDF upload/chat agent
├── tools/shared/            # @tool functions used by agents
│   ├── elasticsearch_tools.py
│   ├── vectordb_tools.py
│   ├── llm_tools.py
│   ├── storage_tools.py
│   ├── guardrail_tools.py
│   ├── memory_tools.py
│   ├── orchestrator_tools.py
│   ├── scenario_tools.py
│   ├── document_tools.py
│   └── judgment_search.py   # Smart judgment search with metadata extraction
├── config/
│   └── prompts.py           # All system prompts
├── tests/                   # Test suites and evaluation scripts
├── services/                # Embedding service for remote deployment
├── frontend.html            # Built-in test UI
└── requirements.txt         # Python dependencies
```

## Environment Variables

Required in `.env`:
- `OPENAI_API_KEY` - OpenAI API key (GPT-4o, GPT-4o-mini)
- `GOOGLE_API_KEY` - Google API key (Gemini models + Search grounding)

Optional:
- `ES_URL` - OpenSearch/ES URL (default: `http://139.84.219.174:9200`). Falls back to `ELASTICSEARCH_URL`.
- `ES_USER` - OpenSearch/ES username (required for AWS OpenSearch)
- `ES_PASSWORD` - OpenSearch/ES password (required for AWS OpenSearch)
- `EMBEDDING_SERVICE_URL` - Remote embedding service URL
- `LOG_LEVEL` - Logging level (default: DEBUG)

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
python -m uvicorn core.gateway:app --host 0.0.0.0 --port 5000

# Run with auto-reload (development)
python -m uvicorn core.gateway:app --host 0.0.0.0 --port 5000 --reload

# Run tests
python tests/test_agents.py --concurrency 3
python tests/evaluate_agents.py
```

## API Routes

All routes are prefixed with `/pyapi`:
- `POST /pyapi/search` - Batch mode legal Q&A
- `POST /pyapi/search/stream` - Streaming legal Q&A (Server-Sent Events)
- `POST /pyapi/chat` - Unified chat endpoint (SSE streaming + inline file uploads, 30 files, 1GB/file)
- `DELETE /pyapi/delete_vectordb/{unique_string}` - Delete PDF collections
- `POST /pyapi/feedback` - Submit feedback
- `GET /pyapi/health` - Health check
- `GET /` - Frontend UI

## Deployment Topology — read before smoking "prod"

There are **two separate** lawttorney HTTPS surfaces. Smoke against
the wrong one and your verification is meaningless.

| Host | Backend IP | nginx | Source of code | Notes |
|---|---|---|---|---|
| `api.lawttorney.com` | `52.66.246.103` (AWS, ap-south-1) | 1.24.0 | **`deploy-prod.yml` workflow_dispatch from this repo** | **This is the box we own.** All commits on `main` ship here. Health: `https://api.lawttorney.com/pyapi/health`. SSH'd via `secrets.SSH_PRIVATE_KEY_PROD`. systemd unit: `lawttorney-v2.service`. App dir: `/root/Lawtech-AI`. |
| `tool.lawttorney.com` | `185.38.109.20*` (separate infra, unknown provenance) | 1.18.0 | NOT this repo | Different machine entirely — different CPU/disk/uptime/nginx version. Our `deploy-prod.yml` does NOT touch this. End users may hit it; smokes against it do not validate our deploys. Treat as out-of-scope unless you have explicit ops context. |

Practical rules:

1. **Smoke prod against `https://api.lawttorney.com/pyapi/*`** — NOT
   `tool.lawttorney.com/pyapiv2/*`. Both URLs return JSON that *looks*
   identical (same Pydantic models, same agent schemas) but they're
   served by different machines that may have drifted.
2. **The health endpoint is the canonical "did our deploy land?" check**.
   Compare `uptime_seconds` before and after CI's deploy completes.
   `api.lawttorney.com` uptime increments on every `deploy-prod.yml`
   run; `tool.lawttorney.com` uptime does its own thing.
3. **Dev URL is `https://test.lawttorney.com`** (Vultr, `64.176.97.182`,
   `deploy.yml`). Its nginx currently returns the frontend SPA on
   `/pyapi*` and `/pyapiv2*` paths, so external API smokes against
   dev are blocked at the proxy. To smoke dev, run from `localhost:5000`
   on the box (or fix the nginx route).
4. **If a smoke fails on one URL and passes on the other**, you're not
   looking at a code bug — you're looking at the topology gap. Resolve
   the topology question first.

History note: prior to 2026-06-14, internal lore (older docs, smoke
scripts in `tests/`) routinely used `tool.lawttorney.com/pyapiv2/*` as
"prod". That was misleading — the deploy pipeline always pointed at
`api.lawttorney.com`. The smokes happened to look right because both
backends ran similar (but not identical) code. Don't perpetuate the
mistake.

## Task Types

The orchestrator classifies queries into tasks:
- **Drafting** - Legal document drafting (per-section generation with Gemini Flash)
- **Legislation** - Legislation lookup (Elasticsearch)
- **Judgment** - Court judgment search (ES + S3 PDF links)
- **SCI_Judgment** - Supreme Court judgment search
- **Newacts** - New acts/amendments (BNS, BNSS, BSA)
- **Scenario** - Scenario-based analysis (Gemini + Google Search grounding)
- **Constitution** - Constitutional provisions
- **Maxim** - Legal maxims and doctrines
- **Legal_Concepts** - General legal concepts (web-grounded)
- **Non_legal** - Non-legal query detection
- **Other** - Fallback

## User intent layer (canonical, always on)

The orchestrator extracts a typed `config.intent.UserIntent` per request via
a single Gemini Flash Lite call (`_extract_user_intent` in
`agents/orchestrator.py`). The intent flows through `state["user_intent"]`
and drives every downstream decision:

- **Synthesis-template picker** reads `intent.wants_table` (no regex).
- **Pass-through guard** bypasses when `intent.format_explicit` or
  `intent.language_explicit and intent.language != "en"`.
- **`state["user_language"]`** is overridden from `intent.language` when the
  user explicitly named a target language (e.g. "Section 131 in Hindi"
  typed in Latin script — langdetect says English, but the extractor catches
  the directive). All domain agents pick up the right language.
- **Domain-agent system prompts** receive an appended `## USER DIRECTIVES`
  block from `core.language._format_intent_directives` covering
  `response_depth` ("brief" / "detailed") and `additional_instructions`.
  Format and language live in their own layers (synth picker + localize_prompt).

The legacy `_TABLE_INTENT_RE` regex, `_wants_table_format`,
`_analyze_and_normalize_query`, `QUERY_NORMALIZE_PROMPT`, and the
`INTENT_EXTRACTOR_V2` env flag were removed in Phase 4 (2026-06-13). The
free-form `response_instructions: str` field is still used as a synthesis
prompt-template variable; `_legacy_response_instructions(intent)` projects
the typed intent into that string. See
`docs/intent_layer_implementation_plan.md`.

## Drafting invariants (do not regress)

The Drafting pipeline was simplified on 2026-06-28 (see
`docs/drafting_simplification_plan.md`) and further simplified on 2026-07-02
(the case-fact bullet-extraction middleman was removed — see the memory
project doc `project_drafting_raw_source_2026_07_02.md`). It is now a
raw-source / dispatcher-pick flow with no enums, no skeletons, no
doc-type classifier, no mandatory-section injection, and no case-fact
extraction. Per-section fan-out IS implemented (commit 03014fe) behind a
judge call that decides single-pass vs per-section. Before touching
`agents/drafting.py`, `agents/orchestrator.py`, `core/self_refine.py`, or
`config/prompts.py`, know these invariants:

1. **Reference draft must come from ES first; web only on rejection.** The
   pipeline runs an ES `match` on the `drafting` index (size 100) →
   `_pick_reference_source` (single Gemini Flash Lite call) picks the best file
   path OR returns the literal string `'none'` → if a path is picked, an ES
   term query on `source.keyword` fetches the full template content; if
   `'none'` (or empty corpus, or fetch miss), `_acquire_reference_via_web`
   fires `core.agent_fallback.web_search_fallback` with
   `DRAFTING_WEB_FALLBACK_PROMPT` to synthesize a reference draft from the
   open web. Do NOT short-circuit to web search for any other reason.
2. **Scope critic + self-refine must run before final return.** After
   generation, `core.self_refine.self_refine` audits the draft against the
   typed `UserIntent` and refines on violations. CRITIQUE_PROMPT covers
   drafting-specific categories (placeholder_marker, orphan_citation_tail,
   forbidden_statute_pair, cause_title_collapsed, paragraph_numbering_break,
   prayer_relief_mismatch, canonical_example_substitution, etc.). Do NOT add
   per-rule regex/threshold checks anywhere — extend CRITIQUE_PROMPT instead.

   **EXCEPTION (2026-09-02) — two failures are checked deterministically in
   code, NOT through the critic.** Both were measured slipping past it:

   - **Off-target language.** 4 of 42 regional drafts came back wholly or
     largely in English despite a regional request, two at a script ratio of
     0.00. `core.language.is_off_target_language` gates on script share
     (threshold 0.85; every correct draft in the sample scored ≥0.85, every
     failure ≤0.60). On failure `_generate_draft` regenerates ONCE, and only
     when >150s of request budget remains — a full redraft costs about as
     much as the first pass against a 300s ceiling — otherwise it ships the
     draft behind a warning banner.
   - **Section-plan adherence.** The writer can return fluent text that
     ignores the section list it was given. Observed on Odia: the planner
     produced a correct 8-section bail plan 3/3 runs, and the writer emitted
     "An analysis regarding a regular bail application", dropped Prayer and
     Verification, and invented a "Relevant Legal Provisions" section holding
     the retrieved statutes verbatim in English.
     `_missing_planned_sections` compares planned headings against emitted
     ones and repairs missing sections in pairs, capped and budget-gated.

   The rule above still holds for everything else. The exception exists
   because the critic was observed mis-parsing and defaulting to `passes=True`
   on exactly these drafts, and because bug 1 (the doc-type flip) established
   that a prompt rule alone does not hold — Gemini 2.5 Pro violated
   DRAFTING_SECTION_PAIR_PROMPT rule 2 ("USE THE EXACT HEADING TEXT GIVEN")
   at a higher rate than Flash Lite. Substantive legal critique stays in
   CRITIQUE_PROMPT; structural guarantees that must never fail live in code.
3. **User query AND uploaded source documents flow verbatim into generation.**
   The user's drafting instruction is passed unchanged. The raw extracted
   text of every uploaded PDF/docx (`user_facts`) is passed as the
   `## UPLOADED SOURCE DOCUMENTS` block — no bullet-summary extraction, no
   truncation, no per-file cap. Gemini 2.5 Pro's 2M-token window absorbs it.
   This invariant comes from saved feedback (`feedback_no_mechanical_patterns`,
   `feedback_preserve_user_query`) and is non-negotiable. Anti-substitution
   discipline (do not swap in canonical example names like Sneha/Priyanka/
   Nashik/Sangamner) lives in the DRAFTING_SYSTEM_PROMPT and
   DRAFTING_SECTION_PAIR_PROMPT anti-substitution paragraphs plus the
   CRITIQUE_PROMPT `canonical_example_substitution` category.
4. **`validate_draft` is bug-fixes only.** It auto-fixes cp1252-misread-as-UTF-8
   mojibake, strips HTML tags, strips leftover `[CITE: ...]` placeholders, and
   removes empty numbered paragraphs. Substantive critique (forbidden statute
   pairs, orphan citation tails, missing procedural sections, prayer-relief
   mismatch, canonical example substitution) lives in
   `core.self_refine.self_refine`. Do NOT add substantive checks to
   `validate_draft`.
5. **No hand-curated taxonomy or skeleton.** The deleted `DOC_TYPES`,
   `SYNTHETIC_SKELETONS`, `GENERIC_COURT_SKELETONS`, `DOC_TYPE_TO_FOOTER_KIND`,
   `DRAFT_OUTLINE_RULES_BY_TYPE`, `_inject_mandatory_sections`,
   `_generate_doctrinal_stance`, `_extract_case_facts`, and
   `_CASE_FACTS_PROMPT` MUST NOT be reintroduced. The reference draft is the
   structural anchor; the user's query shapes scope; the uploaded source
   documents supply the paragraph structure and authoritative facts. If a
   quality regression surfaces, extend the generation prompt or
   CRITIQUE_PROMPT — do NOT add an enum gate or a facts-extraction step.
6. **Per-section fan-out is implemented and threads raw source into every
   pair-call.** `_generate_draft` dispatches to either `_generate_single_pass`
   or `_generate_sectionwise` based on `_judge_fanout`'s decision. The
   sectionwise path walks section pairs (last is solo if odd), each pair
   seeing (a) the document drafted so far via `prior_text`, (b) the raw
   uploaded source documents via `user_facts`. Raw source is threaded into
   EVERY pair-call (not just the first) so any section that walks the source
   paragraph-by-paragraph — para-wise reply, rejoinder denials, counter-
   affidavit response — has direct access to the source's paragraph
   structure and numbering.

   **Opt-in per-section routing** (`DRAFTING_PER_SECTION_CHUNKING=1`,
   off by default): when enabled AND `len(user_facts) > 100_000`, each
   pair calls a Gemini Flash Lite router (`_pick_relevant_chunk_indices`,
   prompt `DRAFTING_CHUNK_ROUTER_PROMPT`) that selects the paragraph
   chunks the pair's sections need. Union of the two sections' picks is
   passed as `user_facts`. Whenever the router fails, returns empty, or
   the blob has <4 chunks, the pair falls back to the raw source —
   preserving the invariant on the fallback path. Language-aware
   preflight: Devanagari-dominant uploads use a 2.4M-char budget (vs
   3.5M for Latin) because Indic scripts tokenise ~2.5 chars/token.

Tests live in `tests/test_drafting_simplification.py` (31 unit + 4 e2e).
Run them via:
```bash
pytest tests/test_drafting_simplification.py -v
DRAFTING_SIMPLIFICATION_E2E=1 pytest tests/test_drafting_simplification.py::TestEndToEndSmokes -v
```

## Agent Resilience

All domain agents have a 3-tier fallback:
1. **Primary search** — Elasticsearch/ChromaDB retrieval
2. **Query rewrite + retry** — GPT-4o-mini rewrites query, retries search
3. **Web search fallback** — Gemini 2.5 Flash + Google Search grounding

Shared utilities in `core/agent_fallback.py`.

## Code Style

- Python 3.12+
- Use type hints for function signatures
- Use `core.logger.get_logger("ModuleName")` for structured logging (never print())
- Use Pydantic models for request/response schemas
- Use `langchain` abstractions for LLM chains and prompts
- State accessed as dict: `state["key"]` or `state.get("key")` (TypedDict)
- LangChain imports: `langchain.messages` for message types, `langchain_core.messages` for BaseMessage

## Important Notes

- Local embedding models stored in `./models/` directory (not committed to git)
- The app references Indian legal codes (IPC/BNS, CrPC/BNSS, IEA/BSA) — both old and new provisions
- ES indices: legislation, judgements, drafting, newacts_v1, supreme_court_judgement, constitution, legal_maxims
- Never commit `.env` files or API keys
