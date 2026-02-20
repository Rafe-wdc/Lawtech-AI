"""Centralized configuration for the multi-agent system."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Project root (v2_multi_agent/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- API Keys ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

_missing = [k for k, v in {
    "OPENAI_API_KEY": OPENAI_API_KEY,
    "GOOGLE_API_KEY": GOOGLE_API_KEY,
}.items() if not v]
if _missing:
    raise ValueError(f"Missing required env vars: {', '.join(_missing)}")

# --- Model IDs ---
MODELS = {
    "orchestrator": "gpt-4o",
    "task_classifier": "gpt-4o",
    "drafting": "gpt-4o",
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
}

# --- Embedding Models (absolute paths from project root) ---
EMBEDDING_MODELS = {
    "retriever": str(_PROJECT_ROOT.parent / "models" / "bge-large-en-v1.5"),
    "pdf_qa": str(_PROJECT_ROOT.parent / "models" / "all-MiniLM-L6-v2"),
}

# --- ChromaDB (absolute paths from project root) ---
CHROMA_PERSIST_DIRS = {
    "constitution": str(_PROJECT_ROOT.parent / "Routing db" / "constitution db"),
    "maxim": str(_PROJECT_ROOT.parent / "Routing db" / "legal maximdb"),
}
CHROMA_STORE_ROOT = str(_PROJECT_ROOT.parent / "chroma_store")

# --- Chat History (SQLite) ---
CHAT_HISTORY_DB_PATH = os.getenv(
    "CHAT_HISTORY_DB_PATH",
    str(_PROJECT_ROOT / "data" / "chat_history.db"),
)
CHAT_HISTORY_USE_LEGACY_API = os.getenv(
    "CHAT_HISTORY_USE_LEGACY_API", "true"
).lower() == "true"

# --- AWS S3 ---
S3_BUCKET = os.getenv("S3_BUCKET", "lawttorney")
S3_REGION = os.getenv("S3_REGION", "ap-south-1")

# --- Redis ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# --- External APIs ---
LAWTTORNEY_API_BASE = os.getenv("LAWTTORNEY_API_BASE", "https://lawttorney.ai/api")

# --- Server ---
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5000"))

# --- Rate Limits ---
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))

# --- Logging ---
LOG_DIR = str(_PROJECT_ROOT / "logs")
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()
