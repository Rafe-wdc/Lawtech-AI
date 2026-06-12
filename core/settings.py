"""Centralized configuration for the multi-agent system."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Project root (Lawtech-AI/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _MaskedStr(str):
    """String subclass that masks its value in repr/str to prevent key leakage in tracebacks."""
    def __repr__(self):
        if len(self) <= 8:
            return "'***'"
        return f"'{self[:4]}...{self[-4:]}'"

    def __str__(self):
        # Return actual value when used as a string (API calls need the real key)
        return super().__str__()


# --- API Keys ---
_openai_key = os.getenv("OPENAI_API_KEY")
_google_key = os.getenv("GOOGLE_API_KEY")

_missing = [k for k, v in {
    "OPENAI_API_KEY": _openai_key,
    "GOOGLE_API_KEY": _google_key,
}.items() if not v]
if _missing:
    raise ValueError(f"Missing required env vars: {', '.join(_missing)}")

OPENAI_API_KEY = _MaskedStr(_openai_key)
GOOGLE_API_KEY = _MaskedStr(_google_key)

# --- Agent Timeouts (seconds) ---
TIMEOUT_WEB_SEARCH_SEC: float = 60.0       # Gemini + Google Search grounding (was 120)
TIMEOUT_METADATA_SEC: float = 20.0         # GPT-4o metadata extraction
TIMEOUT_ES_PARALLEL_SEC: float = 22.0      # Parallel ES search (per-agent)
TIMEOUT_CHROMADB_SEC: float = 90.0         # ChromaDB retrieval + PDF Q&A
TIMEOUT_NEARBY_SECTIONS_SEC: float = 5.0   # Newacts nearby-section lookup

# --- Model IDs ---
MODELS = {
    "orchestrator": "gpt-4o",
    "task_classifier": "gpt-4o",
    "drafting": "gemini-2.5-flash",
    "judgment_metadata": "gpt-4o",
    "newacts_metadata": "gpt-4o",
    "draft_selector": "gpt-4o-mini",
    "legislation_match": "gpt-4o-mini",
    "scenario": "gemini-2.5-pro",
    "scenario_web_grounded": "gemini-2.5-flash",
    "legal_concepts": "gemini-2.5-flash-lite",
    "query_rewrite": "gemini-2.5-flash-lite",
    "guardrail_injection": "gemini-2.5-flash-lite",
    "pdf_chat": "gemini-2.5-pro",
    "pdf_vision_ocr": "gemini-2.5-flash-lite",
}

# --- Elasticsearch / OpenSearch ---
# ES_URL is the preferred env var; fall back to ELASTICSEARCH_URL for backward compat.
# Dev default points at localhost so a missing env var fails visibly instead of silently
# routing traffic to a hardcoded production IP.
_ES_DEFAULT = "http://localhost:9200"
ELASTICSEARCH_URL = (
    os.getenv("ES_URL")
    or os.getenv("ELASTICSEARCH_URL")
    or _ES_DEFAULT
)
if ELASTICSEARCH_URL == _ES_DEFAULT and not os.getenv("ES_URL") and not os.getenv("ELASTICSEARCH_URL"):
    import logging as _logging
    _logging.getLogger("Settings").warning(
        "ES_URL / ELASTICSEARCH_URL not set — falling back to %s. "
        "Set ES_URL env var for production.", _ES_DEFAULT
    )

# Auth credentials (required for AWS OpenSearch, optional for self-hosted)
ES_USER = os.getenv("ES_USER", "")
ES_PASSWORD = os.getenv("ES_PASSWORD", "")

# Auto-detect whether SSL is needed based on URL scheme
ES_USE_SSL = ELASTICSEARCH_URL.startswith("https://")

ES_INDICES = {
    "legislation": "legislation",
    "judgments": "judgements",
    "drafting": "drafting",
    "newacts": "newacts_v1",
    "sci_judgments": "supreme_court_judgement",
    "gst_judgments": "gst_judgements",
    "constitution": "constitution",
    "maxims": "legal_maxims",
}

# --- Embedding Models (absolute paths from project root) ---
EMBEDDING_MODELS = {
    "retriever": str(_PROJECT_ROOT / "models" / "bge-large-en-v1.5"),
    "pdf_qa": str(_PROJECT_ROOT / "models" / "all-MiniLM-L6-v2"),
}

# --- Embedding Service (set to enable remote embeddings, e.g. http://localhost:5100) ---
EMBEDDING_SERVICE_URL = os.getenv("EMBEDDING_SERVICE_URL", "")

# --- ChromaDB (PDF uploads only) ---
CHROMA_STORE_ROOT = str(_PROJECT_ROOT / "chroma_store")

# --- File Upload Storage ---
UPLOADS_ROOT = str(_PROJECT_ROOT / "uploads")
MAX_FILES_PER_REQUEST = 30          # max files per single message
MAX_FILES_PER_THREAD = 30           # max accumulated files per conversation thread
MAX_FILE_SIZE_MB = 1024             # per-file size limit (1 GB)
MAX_THREAD_STORAGE_MB = 1024        # 1 GB total per thread
GEMINI_URI_EXPIRY_BUFFER_HOURS = 2  # re-upload if Gemini URI expires within this window

# --- Chat History (SQLite) ---
CHAT_HISTORY_DB_PATH = os.getenv(
    "CHAT_HISTORY_DB_PATH",
    str(_PROJECT_ROOT / "data" / "chat_history.db"),
)
# --- AWS S3 ---
S3_BUCKET = os.getenv("S3_BUCKET", "lawttorney")
S3_REGION = os.getenv("S3_REGION", "ap-south-1")

# --- Chat Service (FSD integration backend) ---
# Used for Google/Notion OAuth + content extraction.
CHAT_SERVICE_URL = os.getenv("CHAT_SERVICE_URL", "https://test.lawttorney.com/v2/api/chats")
INTEGRATION_POLL_INTERVAL_SEC: float = 3.0
INTEGRATION_POLL_TIMEOUT_SEC: float = 60.0

# --- Drafting ---
# When True, fan out Scenario/Legislation/Judgment alongside Drafting and append
# a "## REFERENCES & CITATIONS" block to the response. Off by default to keep
# drafts focused and reduce token spend. Per-request `cite_appendix` field
# (in SearchRequest / /pyapi/chat form) overrides this default.
DRAFTING_CITE_APPENDIX_DEFAULT = os.getenv(
    "DRAFTING_CITE_APPENDIX_DEFAULT", "false",
).strip().lower() in ("1", "true", "yes", "on")

# --- Response Cache ---
# Off by default. The cache stored the FIRST response to a given query and
# replayed it on subsequent identical queries. Because the Judgment agent's
# relevance gate is non-deterministic, the cached entry could be either the
# "ES hits returned (S3 PDFs)" branch OR the "ES hits rejected, web fallback
# fired (Google grounding links)" branch -- whichever ran first got pinned
# for 1 hour. Two users running the same query then saw drastically
# different source sets. Leaving the cache off is safer until the underlying
# retrieval is more deterministic. Set RESPONSE_CACHE_ENABLED=true to opt
# back in (useful for synthetic benchmarks where determinism matters more
# than freshness).
RESPONSE_CACHE_ENABLED = os.getenv(
    "RESPONSE_CACHE_ENABLED", "false",
).strip().lower() in ("1", "true", "yes", "on")

# --- Redis ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# --- PostgreSQL (LangGraph checkpointer) ---
# Format: postgresql://user:password@host:port/dbname
# Leave empty to use in-memory MemorySaver (state lost on restart).
POSTGRES_URL = os.getenv("POSTGRES_URL", "")

# --- Auth ---
# Comma-separated user API keys. Leave empty for open dev mode.
API_KEYS = os.getenv("API_KEYS", "")
# Single admin key for /pyapi/admin/* and /pyapi/metrics
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
# CORS allowed origins. "*" = open (dev). Set to domain(s) in production.
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*")

# --- Server ---
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5000"))

# --- Rate Limits ---
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "200"))
RATE_LIMIT_ADMIN_PER_MINUTE = int(os.getenv("RATE_LIMIT_ADMIN_PER_MINUTE", "200"))

# --- Logging ---
LOG_DIR = str(_PROJECT_ROOT / "logs")
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()
