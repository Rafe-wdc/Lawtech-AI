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

# --- Gemini model tiers (2026-09-04 upgrade: 2.5 series -> 3.x) ---
#
# Single source of truth for which Gemini model backs each tier. Every
# factory in core/clients.py reads these, so a model swap or rollback is an
# env change, not a code change. Previously each factory hardcoded its own
# model string and the MODELS dict below had drifted out of sync with them.
#
# Tier -> model rationale (measured 2026-09-04, see MODEL_UPGRADE_PLAN.md §3):
#   flash_lite  gemini-3.5-flash-lite  routing/classification. 14/15 vs 12/15
#                                      for 2.5-flash-lite at identical latency.
#   flash       gemini-3.8-flash       generation + intent extraction. Note
#                                      3.8 is both NEWER and CHEAPER than
#                                      3.5-flash ($0.75/$3.75 vs $1.50/$9.00).
#   pro         gemini-pro-latest      drafting, OCR, long-doc Q&A.
#
# `gemini-pro-latest` is a FLOATING alias — it follows Google's current Pro
# model. There is no GA `gemini-3.x-pro` to pin to yet (only
# `gemini-3.1-pro-preview`). Pin GEMINI_PRO_MODEL explicitly if alias drift
# is unacceptable for your deployment.
GEMINI_MODELS = {
    "flash_lite": os.getenv("GEMINI_FLASH_LITE_MODEL", "gemini-3.5-flash-lite"),
    "flash":      os.getenv("GEMINI_FLASH_MODEL",      "gemini-3.8-flash"),
    "pro":        os.getenv("GEMINI_PRO_MODEL",        "gemini-pro-latest"),
}

# Default thinking level for Gemini 3.x when a caller does not specify one.
# Valid: minimal | low | medium | high. NEVER use "minimal" — measured
# regression (3/5 vs 5/5 for "low" on drafting format detection). Gemini 3's
# own default is "high", which would be a large latency/cost jump over the
# 2.5-era `thinking_budget=0` this codebase used, so we pin "low".
GEMINI_THINKING_DEFAULT = os.getenv("GEMINI_THINKING_LEVEL", "low")

# --- Model IDs (per-stage; resolved from the tiers above) ---
# OpenAI entries are retained for the stages that may move to GPT later.
# NOTE: nothing in the runtime currently calls an OpenAI model — get_gpt4o /
# get_gpt4o_mini in core/clients.py have zero callers.
MODELS = {
    "orchestrator": GEMINI_MODELS["flash_lite"],
    "task_classifier": GEMINI_MODELS["flash_lite"],
    "drafting": GEMINI_MODELS["pro"],
    "judgment_metadata": GEMINI_MODELS["flash_lite"],
    "newacts_metadata": GEMINI_MODELS["flash_lite"],
    "draft_selector": GEMINI_MODELS["flash_lite"],
    "legislation_match": GEMINI_MODELS["flash_lite"],
    "scenario_web_grounded": GEMINI_MODELS["flash"],
    "legal_concepts": GEMINI_MODELS["flash"],
    "query_rewrite": GEMINI_MODELS["flash_lite"],
    "guardrail_injection": GEMINI_MODELS["flash_lite"],
    "pdf_chat": GEMINI_MODELS["pro"],
    "pdf_vision_ocr": GEMINI_MODELS["pro"],
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

# --- In-app file GC (Gap #4, 2026-09-02) ---
# Removes reliance on the .github/workflows/prod-daily-cleanup.yml cron
# so any deployment (dev / local / new prod box / one-off VM) cleans
# itself up without external orchestration. Defaults match the cron's
# thresholds so behaviour is identical -- cron can be retired safely
# once this ships to prod.
FILE_GC_ENABLED = os.getenv("FILE_GC_ENABLED", "1") != "0"
FILE_GC_INTERVAL_HOURS = int(os.getenv("FILE_GC_INTERVAL_HOURS", "6"))
UPLOAD_TTL_DAYS = int(os.getenv("UPLOAD_TTL_DAYS", "7"))
CHROMA_TTL_DAYS = int(os.getenv("CHROMA_TTL_DAYS", "30"))
TMP_PDF_TTL_HOURS = int(os.getenv("TMP_PDF_TTL_HOURS", "24"))

# --- Spreadsheet row cap (Gap #6, 2026-09-02) ---
# Cap on XLSX / CSV rows read into the extracted-text blob PER SHEET.
# Historical hard-coded value was 100 which silently dropped rows past
# that -- a critical data-integrity bug for legal spreadsheets
# (evidence lists, cheque ledgers, GST invoices routinely 200-2000
# rows). 5000 is realistic for legal use cases while still bounded by
# Gemini's 1M-token context (5000 rows * ~10 columns * ~10 chars ~=
# 500K chars, well under the limit even with agent overhead).
# Truncation still emits a loud marker in the extracted text +
# WARN log line, so we never silently drop data past the cap.
XLSX_CSV_MAX_ROWS = int(os.getenv("XLSX_CSV_MAX_ROWS", "5000"))

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

# NOTE: INTENT_EXTRACTOR_V2 env flag was retired in Phase 4 of the intent layer
# rollout (2026-06-13). The structured user-intent extractor is now always on.
# Setting INTENT_EXTRACTOR_V2 in .env is a no-op; the variable can be removed
# safely. See docs/intent_layer_implementation_plan.md.

# --- Response Cache ---
# ON by default as of 2026-08-13, after the fixes that made caching safe:
#   1. Cache key includes preferred_language + cite_appendix so two users
#      with the same query but different response-shaping headers no longer
#      collide (core/response_cache.py::_make_key).
#   2. Cache-hit path persists the turn to chat_store so multi-turn context
#      is preserved (core/chat_runner.py + core/gateway.py cache-hit blocks).
#   3. Cache-set skips guardrail-blocked responses.
#   4. Cache-set skips ANY response where a domain agent fell back to
#      web-grounded search (Tier 3). This closes the historic Judgment
#      non-determinism hole: the cached entry can no longer be the
#      "ES-hits" branch on one run and the "web-fallback" branch on
#      another — only clean ES-hits responses reach the cache.
#   5. Cache-hit path emits chat_store.log_request for observability.
# Set RESPONSE_CACHE_ENABLED=false to opt out (e.g. debugging).
RESPONSE_CACHE_ENABLED = os.getenv(
    "RESPONSE_CACHE_ENABLED", "true",
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
# Default INFO to keep prod log volume manageable — dev workstations
# that want verbose output should set LOG_LEVEL=DEBUG in .env explicitly.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
