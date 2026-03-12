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
    "legal_concepts": "gemini-2.5-flash-lite",
    "query_rewrite": "gemini-2.5-flash-lite",
    "guardrail_injection": "gemini-2.5-flash-lite",
    "pdf_chat": "gemini-2.5-pro",
    "pdf_vision_ocr": "gemini-2.5-flash-lite",
}

# --- Elasticsearch ---
ELASTICSEARCH_URL = os.getenv("ELASTICSEARCH_URL", "http://139.84.219.174:9200")
ES_INDICES = {
    "legislation": "legislation",
    "judgments": "judgements",
    "drafting": "drafting",
    "newacts": "newacts_v1",
    "sci_judgments": "supreme_court_judgement",
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

# --- Redis ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# --- Auth ---
# Comma-separated user API keys. Leave empty for open dev mode.
API_KEYS = os.getenv("API_KEYS", "")
# Single admin key for /pyapi/admin/* and /pyapi/metrics
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

# --- Server ---
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5000"))

# --- Rate Limits ---
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))

# --- Logging ---
LOG_DIR = str(_PROJECT_ROOT / "logs")
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()
