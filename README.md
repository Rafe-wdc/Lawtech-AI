# Lawtech-AI v2 — Multi-Agent Architecture

## Structure

```
├── core/                          # Core infrastructure
│   ├── state.py                   # Shared agent state (LangGraph state schema)
│   ├── graph.py                   # LangGraph graph definition (agent wiring)
│   ├── gateway.py                 # FastAPI gateway (thin API layer)
│   └── settings.py                # Centralized config (models, URLs, keys)
│
├── agents/                        # 10 specialized agents
│   ├── orchestrator.py            # #1 — Plans, delegates, merges
│   ├── guardrail.py               # #2 — Input/output safety
│   ├── memory.py                  # #3 — Chat history, query rewriting
│   ├── legislation.py             # #4 — Central/state law retrieval
│   ├── judgment.py                # #5 — Case law search
│   ├── newacts.py                 # #6 — BNS/BNSS/BSA/IPC/CrPC/IEA
│   ├── drafting.py                # #7 — Legal document drafting
│   ├── scenario.py                # #8 — Situational analysis + web search
│   ├── constitution_maxim.py      # #9 — Constitution + legal maxims
│   └── document.py                # #10 — PDF upload, OCR, document Q&A
│
├── tools/                         # Tool definitions
│   ├── inline/                    # Pure functions (no I/O, run in-process)
│   │   ├── abbreviation.py        # expand_abbreviations()
│   │   ├── section_parser.py      # parse_section_info()
│   │   ├── markdown.py            # sanitize_markdown()
│   │   ├── disclaimer.py          # add_disclaimer()
│   │   └── formatters.py          # format_judgment(), format_legislation(), etc.
│   └── shared/                    # Shared tool utilities
│       └── registry.py            # Tool registration and discovery
│
├── mcp_servers/                   # MCP tool servers (separate processes)
│   ├── elasticsearch_server/      # ES search operations
│   │   └── server.py
│   ├── vectordb_server/           # ChromaDB operations
│   │   └── server.py
│   ├── pdf_server/                # PDF processing + OCR
│   │   └── server.py
│   ├── llm_server/                # Unified LLM access with caching
│   │   └── server.py
│   ├── storage_server/            # S3 + file + chat history
│   │   └── server.py
│   ├── legal_utils_server/        # Domain-specific legal tools
│   │   └── server.py
│   └── guardrails_server/         # Safety tools
│       └── server.py
│
├── workers/                       # Background task workers
│   └── celery_app.py              # Celery worker config
│
├── config/                        # Configuration
│   └── prompts.py                 # All prompt templates (migrated from v1)
│
├── tests/                         # Tests
│   ├── agents/                    # Per-agent unit tests
│   ├── tools/                     # Per-tool unit tests
│   └── integration/               # End-to-end flow tests
│
├── requirements.txt               # v2 dependencies
└── README.md                      # This file
```

## Agent Overview

| # | Agent | Model | Trigger |
|---|-------|-------|---------|
| 1 | Orchestrator | Claude Sonnet / GPT-4o | Every request |
| 2 | Guardrail | Gemini Flash Lite | Before + after domain agents |
| 3 | Memory | Gemini Flash Lite | After guardrail, before routing |
| 4 | Legislation | Gemini Flash Lite | task == "Legislation" |
| 5 | Judgment | GPT-4o | task == "Judgment" |
| 6 | Newacts | GPT-4o + Gemini Flash Lite | task == "Newacts" |
| 7 | Drafting | GPT-4o | task == "Drafting" |
| 8 | Scenario | Gemini 2.5 Pro | task == "Scenario" / "Other" / fallback |
| 9 | Constitution & Maxim | Gemini Flash Lite | task == "Constitution" / "Maxim" / "Legal_Concepts" |
| 10 | Document | Gemini 2.5 Pro | PDF endpoints |

## Execution Flow

```
Request → Gateway → Guardrail → Memory → Orchestrator
                                             │
                          ┌──────────────────┼──────────────────┐
                          │                  │                  │
                     [Agent A]          [Agent B]          [Agent C]
                          │                  │                  │
                          └──────────────────┼──────────────────┘
                                             │
                                        Orchestrator (merge)
                                             │
                                         Guardrail (output)
                                             │
                                        Memory (store)
                                             │
                                          Response
```

## Migration from v1

This is Phase 3 of the migration plan:
- Phase 1 (DONE in v1): Redis caching + async + connection pooling
- Phase 2: Extract tools into MCP servers
- Phase 3: Build LangGraph agent graph (this project)
- Phase 4: WebSocket streaming, Celery workers, production hardening
