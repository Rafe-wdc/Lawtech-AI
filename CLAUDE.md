# Lawtech-AI

Legal AI backend powered by FastAPI, LangChain, and multiple LLM providers.

## Project Overview

This is a **Legal AI API** that provides intelligent legal assistance including:
- Legal document Q&A (Judgments, Legislation, Drafts, New Acts)
- Scenario-based legal analysis
- PDF upload and processing with vector storage
- Legal concept explanations
- Legal drafting assistance

## Tech Stack

- **Framework**: FastAPI (Python), runs on port 5000 via uvicorn
- **LLM Orchestration**: LangChain
- **LLM Providers**: OpenAI (GPT-4o for drafting), Google GenAI (Gemini 2.5 Flash/Pro), Perplexity (Sonar for web search)
- **Vector Store**: ChromaDB with HuggingFace embeddings (BGE-large-en-v1.5, all-MiniLM-L6-v2)
- **PDF Processing**: PyMuPDF (fitz), with Gemini Vision fallback for scanned PDFs
- **Storage**: AWS S3 (lawttorney bucket, ap-south-1) for judgment PDFs
- **Search**: Elasticsearch for certain retrievers

## Project Structure

```
├── main.py                  # FastAPI app entry point, CORS, router registration
├── generate_response.py     # Core response generation with task routing
├── requirements.txt         # Python dependencies
├── retrievers/              # Document retrieval modules
│   ├── __init__.py          # Embedding init, vector DB setup, state paths
│   ├── base_retriever.py    # Base retriever class
│   ├── draft_retriever.py   # Legal draft retrieval
│   ├── judgement_retriever.py   # Court judgment retrieval
│   ├── legislation_retriever.py # Legislation retrieval
│   ├── newacts_retriever.py     # New acts retrieval
│   └── hc_judgement_retriever.py # High Court judgment retrieval
├── routes/                  # API route handlers
│   ├── test.py              # Test endpoints
│   ├── llm_answer.py        # LLM answer endpoint
│   ├── mainqa.py            # Main Q&A with PDF upload/query
│   ├── mainqa_test.py       # Test variant of mainqa
│   ├── mainqa11.py          # Alternate mainqa version
│   ├── mainqa33.py          # Alternate mainqa version
│   └── delete_vectordb.py   # Vector DB deletion endpoint
├── utils/                   # Utility modules
│   ├── util.py              # Token counting, text wrapping
│   ├── custom_prompts.py    # Prompt templates
│   ├── task_identifer.py    # Task classification (Drafting/Legislation/Judgment/etc.)
│   ├── scenario.py          # Scenario-based Q&A via Gemini
│   ├── check_relevance.py   # Document relevance checking
│   ├── text_extraction.py   # PDF text extraction (PyMuPDF + Gemini Vision)
│   ├── pdf_utils.py         # PDF utilities
│   ├── customized_draft.py  # Custom draft generation
│   ├── expand_legal_abbreviations.py  # Legal abbreviation expansion
│   ├── summarize_chat_history.py      # Chat history summarization
│   ├── get_chat_history_from_thread.py # Thread history retrieval
│   └── background_summary.py          # Background summarization
└── models/                  # Local ML models (not in git)
    ├── bge-large-en-v1.5/
    └── all-MiniLM-L6-v2/
```

## Environment Variables

Required in `.env`:
- `OPENAI_API_KEY` - OpenAI API key (for GPT-4o drafting)
- `GOOGLE_API_KEY` - Google API key (for Gemini models)
- `PPLX_API_KEY` - Perplexity API key (for web search)

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
python main.py
# Or: uvicorn main:app --host 0.0.0.0 --port 5000

# Run with auto-reload (development)
uvicorn main:app --host 0.0.0.0 --port 5000 --reload
```

## API Routes

All routes are prefixed with `/pyapi`:
- `POST /pyapi/test` - Test endpoint
- `POST /pyapi/llm_answer` - Main LLM answer endpoint with task routing
- `POST /pyapi/mainqa` - PDF upload & Q&A (usecase: 'upload' or 'qa')
- `POST /pyapi/mainqa_test` - Test Q&A endpoint
- `DELETE /pyapi/delete_vectordb` - Delete vector DB collections

## Task Types

The system classifies queries into tasks via `task_identifer.py`:
- **Drafting** - Legal document drafting (uses GPT-4o)
- **Legislation** - Legislation lookup
- **Judgment** - Court judgment search (with S3 PDF links)
- **Newacts** - New acts/amendments
- **Scenario** - Scenario-based analysis (uses Gemini web search)
- **Legal_Concepts** - General legal concepts (uses Gemini Flash)
- **Other** - Fallback to web search

## Code Style

- Python 3.10+
- Use type hints for function signatures
- Follow existing patterns for new retrievers (extend base_retriever.py)
- Use Pydantic models for request/response schemas
- Keep route handlers in `routes/`, utilities in `utils/`, retrievers in `retrievers/`
- Use `langchain` abstractions for LLM chains and prompts

## Important Notes

- Local embedding models are stored in `./models/` directory (not committed to git)
- Vector DBs are stored in `./Routing db/` and `./chroma_store/` directories
- The app references Indian legal codes (IPC/BNS, CrPC/BNSS, IEA/BSA) - both old and new provisions
- Elasticsearch is used at `http://139.84.219.174:9200` for certain retrievers
- Never commit `.env` files or API keys
