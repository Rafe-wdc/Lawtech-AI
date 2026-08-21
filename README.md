# Lawtech-AI

Legal AI backend powered by FastAPI, LangGraph, LangChain, and multiple LLM providers. Provides intelligent legal assistance for Indian law: judgment Q&A, legislation lookup, scenario analysis, PDF document chat, legal drafting, and more.

## ⚠️ Security notice — rotate before deploying from an old checkout

The user/admin API key `ff6c3e959…f23a51` was previously hardcoded in ~33 scratch
runners and smoke scripts committed to this repo. On **2026-08-14** every literal
was removed and the scripts now read the key from `LAWTECH_API_KEY` (Python) or
`$env:LAWTECH_API_KEY` (PowerShell). The old value **is still in git history**.

Before deploying from any checkout dated before 2026-08-14:

1. **Rotate the user + admin API keys.** Generate new values with
   `python -c "import secrets; print(secrets.token_urlsafe(32))"` and update
   `API_KEYS` / `ADMIN_API_KEY` in the prod server's `.env`, plus the GitHub
   secrets `PROD_INTEGRATION_API_KEY` and `PROD_INTEGRATION_ADMIN_KEY`.
2. **Do not restore the leaked value anywhere.** The scripts now fail loudly
   if `LAWTECH_API_KEY` (or `SMOKE_KEY` / `PROD_API_KEY` for the older ones)
   is unset — set them in your shell for local smokes.
3. **Also treat `.env.production.example` as a template only.** The old
   `.env.production` used to be un-ignored in `.gitignore`; that negation is
   gone. Any file named `.env*` (except `*.example`) is now ignored.
4. **OpenAI / Google / OpenSearch credentials were not committed** — they only
   ever lived in `.env` (correctly gitignored). No rotation needed for those
   unless you have other evidence of exposure.

## Tech Stack

- **Framework**: FastAPI (Python 3.12+), uvicorn on port 5000
- **Agent orchestration**: LangGraph 1.x multi-agent graph
- **LLM orchestration**: LangChain 1.x (provider-agnostic via `init_chat_model`)
- **LLM providers**: OpenAI (GPT-4o for orchestration/metadata), Google GenAI (Gemini 2.5 Flash / Pro for generation), Google Search grounding for web fallback
- **Search**: AWS OpenSearch (via `opensearch-py`, with `elasticsearch` fallback) for legal documents
- **Vector store**: ChromaDB (PDF uploads only) with HuggingFace embeddings (BGE-large-en-v1.5, all-MiniLM-L6-v2)
- **PDF processing**: PyMuPDF, with Gemini Vision fallback for scanned/garbled PDFs (Indic-script-aware detection)
- **Storage**: AWS S3 (`lawttorney` bucket, `ap-south-1`) for judgment PDFs; PostgreSQL for chat history

## Project Structure

```
├── core/                          # Core infrastructure
│   ├── gateway.py                 # FastAPI app, SSE streaming, all API endpoints
│   ├── graph.py                   # LangGraph graph definition and compilation
│   ├── state.py                   # Shared state schema (LegalAgentState)
│   ├── settings.py                # Configuration, env vars, model IDs
│   ├── clients.py                 # LLM client singletons (GPT-4o, Gemini, OpenSearch)
│   ├── chat_store.py              # PostgreSQL chat history store
│   ├── chat_runner.py             # Chat orchestration helper
│   ├── streaming.py               # SSE event streaming
│   ├── file_processor.py          # PDF/image processing (PyMuPDF + Gemini Vision)
│   ├── language.py                # Multilingual support (14 Indian languages)
│   ├── agent_fallback.py          # Query rewrite + web search fallback utilities
│   ├── auth.py, compliance.py     # Auth, rate limiting, audit
│   ├── checkpointer.py            # LangGraph checkpoint persistence
│   ├── response_cache.py          # Response caching layer
│   └── logger.py                  # Structured logging
│
├── agents/                        # 12 LangGraph nodes
│   ├── orchestrator.py            # Plans tasks, fans out to domain agents, merges
│   ├── guardrail.py               # Input/output safety (injection, PII, hallucination)
│   ├── memory.py                  # Chat history + query rewriting + follow-up detection
│   ├── legislation.py             # Legislation lookup (OpenSearch)
│   ├── judgment.py                # High Court judgment search (OpenSearch + S3)
│   ├── sci_judgment.py            # Supreme Court judgment search
│   ├── gst_judgment.py            # GST AAAR appellate-order search
│   ├── newacts.py                 # BNS / BNSS / BSA new-code lookup
│   ├── drafting.py                # Per-section legal-document drafting (Gemini Flash)
│   ├── scenario.py                # Scenario analysis (Gemini + Google Search grounding)
│   ├── constitution_maxim.py      # Constitution + maxims + general legal concepts
│   ├── document.py                # PDF upload, OCR, vector storage, Q&A
│   └── non_legal.py               # Non-legal query handler
│
├── tools/
│   ├── inline/                    # Pure in-process helpers
│   │   ├── abbreviation.py        # expand_abbreviations()
│   │   ├── section_parser.py      # parse_section_info()
│   │   └── markdown.py            # sanitize_markdown()
│   └── shared/                    # @tool functions for agents
│       ├── elasticsearch_tools.py
│       ├── vectordb_tools.py
│       ├── llm_tools.py
│       ├── storage_tools.py
│       ├── guardrail_tools.py
│       ├── memory_tools.py
│       ├── orchestrator_tools.py
│       ├── scenario_tools.py
│       ├── document_tools.py
│       ├── judgment_search.py
│       ├── sci_judgment_tools.py
│       └── gst_judgment_tools.py
│
├── config/
│   └── prompts.py                 # All system prompts (per agent)
│
├── services/                      # Embedding service for remote deployment
├── workers/                       # Background workers
├── scripts/                       # Operational scripts
├── deploy/                        # Deployment manifests
├── docs/                          # Internal docs / plans
├── tests/                         # Test suites and evaluation scripts
├── frontend.html                  # Built-in test UI
├── requirements.txt
└── CLAUDE.md                      # Project guide for Claude Code
```

## Agent Overview

| # | Agent | Model | Trigger |
|---|-------|-------|---------|
| 1 | Orchestrator | GPT-4o | Every request — plans tasks, fans out, synthesizes |
| 2 | Guardrail (in/out) | Gemini Flash Lite | Before + after domain agents |
| 3 | Memory | Gemini Flash Lite | After guardrail, before routing |
| 4 | Legislation | Gemini Flash | task == `Legislation` |
| 5 | Judgment (High Court) | GPT-4o + Gemini | task == `Judgment` |
| 6 | SCI Judgment (Supreme Court) | GPT-4o + Gemini | task == `SCI_Judgment` |
| 7 | GST Judgment (AAAR) | GPT-4o + Gemini | task == `GST_Judgment` |
| 8 | Newacts | GPT-4o + Gemini Flash | task == `Newacts` |
| 9 | Drafting | Gemini Flash (per-section) | task == `Drafting` |
| 10 | Scenario | Gemini 2.5 Pro + Google Search | task == `Scenario` / fallback |
| 11 | Constitution & Maxim | Gemini Flash | `Constitution` / `Maxim` / `Legal_Concepts` |
| 12 | Document | Gemini 2.5 Pro | PDF chat endpoints |

## Task Types

The orchestrator classifies queries into one of:
`Drafting`, `Legislation`, `Judgment`, `SCI_Judgment`, `GST_Judgment`, `Newacts`, `Scenario`, `Constitution`, `Maxim`, `Legal_Concepts`, `Non_legal`, `Other`.

## Agent Resilience

All domain agents have a 3-tier fallback:

1. **Primary search** — OpenSearch / ChromaDB retrieval
2. **Query rewrite + retry** — GPT-4o-mini rewrites the query, retries search
3. **Web search fallback** — Gemini 2.5 Flash + Google Search grounding

Shared utilities live in `core/agent_fallback.py`.

## Multilingual Support

`core/language.py` detects user language (14 Indian languages supported) and localizes system prompts via `localize_prompt()`. The gateway accepts a `preferred_language` parameter; the frontend persists the selection in `localStorage`. Query fallback order:
`agent_queries[key] → state["query"] (normalized EN) → state["original_query"]`.

## Execution Flow

```
Request → Gateway → Guardrail(in) → Memory → Orchestrator
                                                  │
                              ┌───────────────────┼───────────────────┐
                              │                   │                   │
                         [Agent A]           [Agent B]           [Agent C]   (parallel via Send)
                              │                   │                   │
                              └───────────────────┼───────────────────┘
                                                  │
                                       Orchestrator (synthesize)
                                                  │
                                          Guardrail(out)
                                                  │
                                      Memory (persist history)
                                                  │
                                           SSE Response
```

## API Routes

All routes are prefixed with `/pyapi`:

| Method | Path | Purpose |
|---|---|---|
| POST | `/pyapi/search` | Batch legal Q&A |
| POST | `/pyapi/search/stream` | Streaming Q&A (Server-Sent Events) |
| POST | `/pyapi/chat` | Unified chat (SSE + inline file uploads, up to 30 files / 1 GB each) |
| DELETE | `/pyapi/delete_vectordb/{unique_string}` | Delete a PDF collection |
| POST | `/pyapi/feedback` | Submit user feedback |
| GET | `/pyapi/health` | Health check |
| GET | `/` | Frontend UI (`frontend.html`) |

## Environment Variables

Required in `.env`:

| Var | Purpose |
|---|---|
| `OPENAI_API_KEY` | OpenAI (GPT-4o, GPT-4o-mini) |
| `GOOGLE_API_KEY` | Google GenAI (Gemini) + Search grounding |

Optional:

| Var | Default | Purpose |
|---|---|---|
| `ES_URL` | `http://139.84.219.174:9200` | OpenSearch endpoint (falls back to `ELASTICSEARCH_URL`) |
| `ES_USER` | — | OpenSearch username (required for AWS OpenSearch) |
| `ES_PASSWORD` | — | OpenSearch password (required for AWS OpenSearch) |
| `EMBEDDING_SERVICE_URL` | — | Remote embedding service URL |
| `RATE_LIMIT_PER_MINUTE` | 200 | Per-key request limit |
| `API_KEYS` | — | Comma-separated user API keys |
| `ADMIN_API_KEY` | — | Admin API key |
| `LOG_LEVEL` | `DEBUG` | Logging level |

OpenSearch indices used: `legislation`, `judgements`, `drafting`, `newacts_v1`, `supreme_court_judgement`, `constitution`, `legal_maxims`.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
python -m uvicorn core.gateway:app --host 0.0.0.0 --port 5000

# Development with auto-reload
python -m uvicorn core.gateway:app --host 0.0.0.0 --port 5000 --reload

# Run tests
python tests/test_agents.py --concurrency 3
python tests/evaluate_agents.py
```

## Code Style

- Python 3.12+ with type hints
- `core.logger.get_logger("ModuleName")` for logging — never `print()`
- Pydantic models for request/response schemas
- LangChain abstractions for LLM chains and prompts
- LangGraph state accessed as dict: `state["key"]` / `state.get("key")` (TypedDict)
- Imports: `langchain.messages` for message types (`HumanMessage`, `AIMessage`, …); `langchain_core.messages` for the `BaseMessage` base class

## Notes

- Local embedding models live in `./models/` (not committed)
- References Indian legal codes — both old (IPC, CrPC, IEA) and new (BNS, BNSS, BSA)
- `.env` and API keys must never be committed
- See [CLAUDE.md](CLAUDE.md) for the Claude Code project guide
