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
#   flash       gemini-3.8-flash       intent extraction, fan-out planner,
#                                      and other flash-tier tasks that are NOT
#                                      user-facing answer generation. Note
#                                      3.8 is both NEWER and CHEAPER than
#                                      3.5-flash ($0.75/$3.75 vs $1.50/$9.00).
#   generation  anthropic:claude-sonnet-5  User-facing response generation
#                                      AND drafting. Claude Sonnet 5 at
#                                      $2/$10 per 1M tokens. Override with
#                                      GENERATION_MODEL env var. Falls back
#                                      to flash tier if unset.
#
# NO PRO TIER (removed 2026-09-07). Every stage that used it has been measured
# onto Flash and none regressed:
#   drafting   CLAUDE.md invariant #2 — 2.5 Pro broke the "USE THE EXACT
#              HEADING TEXT GIVEN" rule MORE often than Flash Lite
#   OCR        equal accuracy, faster (5.4s vs 8.1s), production incident data
#   pdf_chat   73k-char doc: Flash 14.2s/$0.0666 vs Pro 16.3s/$0.0693, same
#              needle found and same hallucination refused
# The published numbers agree: gemini-3.8-flash beats gemini-3.1-pro-preview on
# aggregate (51.0 vs 43.4) and reasoning (46.9 vs 45.1), costs 3.3-4x less, and
# its training data is 14 months newer (Mar 2026 vs Jan 2025) — which matters
# for Indian legal work. There is no GA gemini-3.x-pro to pin to; the old
# `gemini-pro-latest` alias resolved to a PREVIEW model. If a Pro tier is ever
# needed again, add it back with an eval, not on the assumption that Pro wins.
#   vision      gemini-3.6-flash       Vision OCR ONLY. Pinned separately
#                                      from the flash tier because the model
#                                      choice here rests on production
#                                      evidence, not benchmarks: on the
#                                      2026-09-06 WhatsApp-screenshot
#                                      incident gemini-2.5-flash returned 504
#                                      DEADLINE_EXCEEDED on 2 of 3 pages,
#                                      while 3.6-flash OCR'd all 3 in 36s vs
#                                      273s. Pro was measured at equal
#                                      accuracy on clean documents but has no
#                                      production track record here and costs
#                                      ~5x. Note gemini-3.8-flash is the same
#                                      price as 3.6 and benchmarks higher
#                                      (Artificial Analysis 59 vs 50), but
#                                      that has NOT been validated on scanned
#                                      Indic/handwritten legal pages — A/B it
#                                      via GEMINI_VISION_MODEL before moving.
GEMINI_MODELS = {
    "flash_lite": os.getenv("GEMINI_FLASH_LITE_MODEL", "gemini-3.5-flash-lite"),
    "flash":      os.getenv("GEMINI_FLASH_MODEL",      "gemini-3.8-flash"),
    "vision":     os.getenv("GEMINI_VISION_MODEL",     "gemini-3.6-flash"),
    # Generation tier — user-facing answer synthesis and drafting.
    # Defaults to Claude Sonnet 5 (provider-qualified).  Override with
    # GENERATION_MODEL env var, e.g. GENERATION_MODEL=gemini-3.8-flash to
    # fall back to Gemini Flash.
    "generation": os.getenv("GENERATION_MODEL",        "anthropic:claude-sonnet-5"),
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
    # Generation tier — Claude Sonnet 5 for user-facing answer generation.
    # Override with GENERATION_MODEL env var.
    "drafting": GEMINI_MODELS["generation"],
    "judgment_metadata": GEMINI_MODELS["flash_lite"],
    "newacts_metadata": GEMINI_MODELS["flash_lite"],
    "draft_selector": GEMINI_MODELS["flash_lite"],
    "legislation_match": GEMINI_MODELS["flash_lite"],
    # GOOGLE-CLIENT-ONLY STAGES — these MUST stay on a Gemini id.
    #
    # Both values are passed straight to google.genai's
    # `client.models.generate_content(model=...)` for Google Search grounding
    # (agents/scenario.py, core/agent_fallback.py), NOT through LangChain. A
    # non-Gemini id here is a hard 404 from Google with no fallback:
    #   404 models/anthropic:claude-sonnet-5 is not found for API version
    #   v1beta, or is not supported for generateContent
    # That breaks the Scenario agent AND `web_search_fallback`, which is
    # tier-3 resilience for EVERY domain agent (see CLAUDE.md "Agent
    # Resilience"). Legal_Concepts is web-grounded too
    # (agents/constitution_maxim.py) and routes through the same path.
    # Enforced below by _assert_google_model.
    "scenario_web_grounded": GEMINI_MODELS["flash"],
    "legal_concepts": GEMINI_MODELS["flash"],
    "query_rewrite": GEMINI_MODELS["flash_lite"],
    "guardrail_injection": GEMINI_MODELS["flash_lite"],
    "pdf_chat": os.getenv("PDF_CHAT_MODEL", GEMINI_MODELS["flash"]),
    "pdf_vision_ocr": GEMINI_MODELS["vision"],
}


# --- Guard: stages that bypass LangChain must carry a Gemini model id ---
# These are dispatched via google.genai directly (Google Search grounding).
# Catching a bad value at import beats a 404 in production traffic.
_GOOGLE_CLIENT_ONLY_STAGES = ("scenario_web_grounded", "legal_concepts")


def _assert_google_model(stage: str, model_id: str) -> None:
    if ":" in model_id and not model_id.startswith("google_genai:"):
        raise ValueError(
            f"MODELS[{stage!r}] = {model_id!r} is not a Gemini model. This "
            "stage is dispatched through google.genai directly for Google "
            "Search grounding, so a non-Gemini id returns 404 from Google "
            "with no fallback — breaking the Scenario agent and "
            "web_search_fallback (tier-3 resilience for every domain agent). "
            "Point it at a GEMINI_MODELS tier instead."
        )


for _stage in _GOOGLE_CLIENT_ONLY_STAGES:
    _assert_google_model(_stage, MODELS[_stage])

# The flash TIER is also dispatched directly through google.genai — by the
# four scenario tools in tools/shared/scenario_tools.py, which moved onto it
# when the Pro tier was removed. Same 404 exposure, same guard.
_assert_google_model("GEMINI_MODELS['flash']", GEMINI_MODELS["flash"])
_assert_google_model("GEMINI_MODELS['vision']", GEMINI_MODELS["vision"])

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
