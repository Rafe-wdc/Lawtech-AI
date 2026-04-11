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
- `POST /pyapi/continue_draft` - Continue incomplete drafts
- `DELETE /pyapi/delete_vectordb/{unique_string}` - Delete PDF collections
- `POST /pyapi/feedback` - Submit feedback
- `GET /pyapi/health` - Health check
- `GET /` - Frontend UI

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
