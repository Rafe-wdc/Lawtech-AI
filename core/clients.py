"""Shared async clients — singleton instances reused across all agents.

Instead of each agent creating its own ES/LLM/embedding connections,
all agents import from here. This gives us:
- Connection pooling (one ES pool, not one per agent)
- Model reuse (load embeddings once, not per-request)
- Easy swap to MCP later (change imports here, agents untouched)

Uses: init_chat_model() for provider-agnostic LLM initialization (LangChain 1.0+)
Ref: https://docs.langchain.com/oss/python/langchain/models
"""

from __future__ import annotations

import os
import threading
import time
from functools import lru_cache

# Cap BLAS / OpenMP / tokenizer threads BEFORE PyTorch is imported.
# Without this, every gunicorn worker's PyTorch tries to use all 8 vCPUs
# via OpenMP. With 6 workers, that's 48 threads fighting for 8 cores —
# the torch.nn.Linear deadlock Phase 7 observed (workers wedged at
# 110-150% CPU for 15 min after load stopped). True parallelism comes
# from the 6 worker PROCESSES; each worker should do one matmul thread
# at a time. setdefault preserves anything the systemd unit already set.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# Try opensearch-py first (for AWS OpenSearch), fall back to elasticsearch
try:
    from opensearchpy import OpenSearch as _SearchClient
    _USING_OPENSEARCH = True
except ImportError:
    from elasticsearch import Elasticsearch as _SearchClient
    _USING_OPENSEARCH = False

from langchain.chat_models import init_chat_model
from langchain_community.embeddings import HuggingFaceEmbeddings
from google import genai

from .settings import (
    GEMINI_MODELS,
    GEMINI_THINKING_DEFAULT,
    ELASTICSEARCH_URL,
    ES_USER,
    ES_PASSWORD,
    ES_USE_SSL,
    EMBEDDING_MODELS,
    EMBEDDING_SERVICE_URL,
)
from .logger import get_logger

_log = get_logger("Clients")


# --- Elasticsearch / OpenSearch ---

_es_client: _SearchClient | None = None


def get_es_client(max_retries: int = 3, timeout: int = 30) -> _SearchClient:
    """Get or create singleton search client (OpenSearch or Elasticsearch)."""
    global _es_client
    if _es_client is None:
        kwargs: dict = {
            "request_timeout": timeout,
            "max_retries": max_retries,
            "retry_on_timeout": True,
        }

        if ES_USER and ES_PASSWORD:
            kwargs["http_auth"] = (ES_USER, ES_PASSWORD)

        if ES_USE_SSL:
            # opensearch-py accepts use_ssl/ssl_show_warn; elasticsearch>=8
            # rejects them (use https:// URL scheme + verify_certs only).
            kwargs["verify_certs"] = True
            if _USING_OPENSEARCH:
                kwargs["use_ssl"] = True
                kwargs["ssl_show_warn"] = True

        _es_client = _SearchClient(
            ELASTICSEARCH_URL,
            **kwargs,
        )

        _log.info(
            "Search client initialized",
            backend="opensearch" if _USING_OPENSEARCH else "elasticsearch",
            url=ELASTICSEARCH_URL[:40] + "...",
            ssl=ES_USE_SSL,
            auth=bool(ES_USER),
        )
    return _es_client


# --- Elasticsearch Circuit Breaker ---
# Prevents cascading hangs when ES is unavailable.
# After 3 consecutive failures, opens for 60s (fast-fail period).
# Auto-resets after 60s to allow ES to recover.

_es_failure_count: int = 0
_es_open_until: float = 0.0   # epoch time when circuit opens until
_ES_FAILURE_THRESHOLD: int = 3
_ES_OPEN_DURATION_SEC: float = 60.0
_es_lock = threading.Lock()   # guards _es_failure_count and _es_open_until


def is_es_available() -> bool:
    """Return False if the ES circuit is open (fast-fail period active)."""
    with _es_lock:
        return time.time() >= _es_open_until


def record_es_failure() -> None:
    """Record an ES failure. Opens the circuit after 3 consecutive failures."""
    global _es_failure_count, _es_open_until
    with _es_lock:
        _es_failure_count += 1
        if _es_failure_count >= _ES_FAILURE_THRESHOLD:
            _es_open_until = time.time() + _ES_OPEN_DURATION_SEC
            _log.warning("ES circuit OPEN — fast-failing for 60s",
                         failure_count=_es_failure_count)


def record_es_success() -> None:
    """Reset the circuit breaker after a successful ES call."""
    global _es_failure_count, _es_open_until
    with _es_lock:
        if _es_failure_count > 0:
            _log.info("ES circuit RESET after successful call",
                      previous_failures=_es_failure_count)
        _es_failure_count = 0
        _es_open_until = 0.0


# --- Gemini Circuit Breaker ---
# Mirrors the ES pattern above. Prevents cascading Gemini calls during a
# regional outage / quota trip. When Gemini returns 5xx or a 429 or hits
# `ResourceExhausted`, agents that guard critical paths with
# `is_gemini_available()` can skip the Gemini call and fall back to
# cached / no-op behaviour. This targets self_refine, the drafting
# judge/router, and web_search_fallback — the paths where a hung Gemini
# holds a semaphore slot until gunicorn kills the worker.
#
# Two breakers because Flash quota is separately rate-limited from Pro
# quota — Pro can be down while Flash is fine.

_gemini_flash_failure_count: int = 0
_gemini_flash_open_until: float = 0.0
_gemini_pro_failure_count: int = 0
_gemini_pro_open_until: float = 0.0
_GEMINI_FAILURE_THRESHOLD: int = 5
_GEMINI_OPEN_DURATION_SEC: float = 60.0
_gemini_lock = threading.Lock()


def is_gemini_flash_available() -> bool:
    """Return False if the Gemini Flash circuit is open (fast-fail active)."""
    with _gemini_lock:
        return time.time() >= _gemini_flash_open_until


def is_gemini_pro_available() -> bool:
    """Return False if the Gemini Pro circuit is open (fast-fail active)."""
    with _gemini_lock:
        return time.time() >= _gemini_pro_open_until


def record_gemini_flash_failure() -> None:
    """Increment Flash failure count; open the circuit after 5 hits."""
    global _gemini_flash_failure_count, _gemini_flash_open_until
    with _gemini_lock:
        _gemini_flash_failure_count += 1
        if _gemini_flash_failure_count >= _GEMINI_FAILURE_THRESHOLD:
            _gemini_flash_open_until = time.time() + _GEMINI_OPEN_DURATION_SEC
            _log.warning(
                "Gemini Flash circuit OPEN — fast-failing for 60s",
                failure_count=_gemini_flash_failure_count,
            )


def record_gemini_flash_success() -> None:
    """Reset Flash breaker after a successful call."""
    global _gemini_flash_failure_count, _gemini_flash_open_until
    with _gemini_lock:
        if _gemini_flash_failure_count > 0:
            _log.info(
                "Gemini Flash circuit RESET after successful call",
                previous_failures=_gemini_flash_failure_count,
            )
        _gemini_flash_failure_count = 0
        _gemini_flash_open_until = 0.0


def record_gemini_pro_failure() -> None:
    """Increment Pro failure count; open the circuit after 5 hits."""
    global _gemini_pro_failure_count, _gemini_pro_open_until
    with _gemini_lock:
        _gemini_pro_failure_count += 1
        if _gemini_pro_failure_count >= _GEMINI_FAILURE_THRESHOLD:
            _gemini_pro_open_until = time.time() + _GEMINI_OPEN_DURATION_SEC
            _log.warning(
                "Gemini Pro circuit OPEN — fast-failing for 60s",
                failure_count=_gemini_pro_failure_count,
            )


def record_gemini_pro_success() -> None:
    """Reset Pro breaker after a successful call."""
    global _gemini_pro_failure_count, _gemini_pro_open_until
    with _gemini_lock:
        if _gemini_pro_failure_count > 0:
            _log.info(
                "Gemini Pro circuit RESET after successful call",
                previous_failures=_gemini_pro_failure_count,
            )
        _gemini_pro_failure_count = 0
        _gemini_pro_open_until = 0.0


# --- LLM Clients via init_chat_model (provider-agnostic, LangChain 1.0+) ---
# Pattern: init_chat_model("provider:model_name", **kwargs)
# Docs: https://docs.langchain.com/oss/python/langchain/models

@lru_cache(maxsize=1)
def get_gpt4o(temperature: float = 0.3):
    """GPT-4o — kept for backward compat, prefer Gemini models."""
    return init_chat_model("openai:gpt-4o", temperature=temperature, max_retries=2)


@lru_cache(maxsize=4)
def get_openai_drafting_fallback(temperature: float = 0.0,
                                 max_tokens: int = 12000):
    """GPT-4o standby for the drafting section writer.

    Every generation path in this service runs on Gemini. When Gemini hangs
    or the circuit opens there is nothing behind it, so the section is
    dropped and the user receives a short or empty draft with no error —
    silent degradation. This gives that one path a second provider.

    Deliberately tighter than `get_gpt4o()`:
      - `max_retries=0` — the caller is already inside a request deadline
        and has its own retry; SDK-level retries are what let the Gemini
        call burn 592 s against a 300 s budget in the first place.
      - `timeout=90` — a fallback that answers late is no better than no
        fallback. Fail fast so the caller can degrade deliberately.
    """
    return init_chat_model(
        "openai:gpt-4o",
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=0,
        timeout=90,
    )


def is_openai_fallback_configured() -> bool:
    """True when OPENAI_API_KEY is set, so callers can skip the fallback
    path cleanly rather than raising an auth error inside an except block.
    """
    return bool(os.getenv("OPENAI_API_KEY"))


@lru_cache(maxsize=1)
def get_gpt4o_mini(temperature: float = 0.3):
    """GPT-4o-mini — kept for backward compat, prefer Gemini models."""
    return init_chat_model("openai:gpt-4o-mini", temperature=temperature, max_retries=2)


# --- Gemini Model Tier ---
# Flash Lite: classification, metadata extraction, query rewrite (fastest, cheapest)
# Flash:      response generation, synthesis, ReAct agents (balanced quality + speed)
# Pro:        scenario analysis, complex reasoning (highest quality)
#
# Output budget caps (max_output_tokens) are set to the Gemini 2.5 hard ceiling
# (65,535) by default to prevent silent truncation. Callers can override per-use.
#
# `thinking_budget` controls Gemini 2.5's hidden reasoning tokens. CRITICAL:
# thinking tokens are charged AGAINST max_output_tokens (Google counts them as
# part of the output budget). For pure-generation tasks (synthesis, drafting,
# OCR), set thinking_budget=0 to disable thinking and reclaim the full budget
# for visible output. For tool-use / analytical tasks (ReAct agents, scenario,
# PDF chat), leave thinking_budget at the default to allow planning.

# --- Gemini 2.5 -> 3.x thinking-parameter bridge ---
#
# Gemini 3.x REPLACED the integer `thinking_budget` with the enum
# `thinking_level` (minimal | low | medium | high). Passing `thinking_budget`
# to a 3.x Lite model returns 400 INVALID_ARGUMENT, and passing BOTH is a 400
# on every 3.x model. This codebase has ~11 callsites that pass
# `thinking_budget`, so rather than rewrite them all, the tier factories keep
# accepting `thinking_budget` and translate it here.
#
# Mapping (deliberately never emits "minimal" — measured regression):
#   budget <= 0     -> "low"     (2.5-era "thinking off"; 3.x cannot fully
#                                 disable thinking, and "minimal" scored
#                                 3/5 vs 5/5 for "low" on drafting format
#                                 detection, 2026-09-04)
#   budget <= 2048  -> "low"
#   budget >  2048  -> "medium"
#
# Callers may pass `thinking_level=` directly to bypass the translation.
def _split_provider(model_id: str) -> tuple[str, str]:
    """Split a tier value into (provider, bare_model_id).

    Tier values may be provider-qualified (`anthropic:claude-opus-5`,
    `openai:gpt-5.4`) or bare, in which case google_genai is assumed so every
    pre-existing GEMINI_*_MODEL value keeps working unchanged.

    This is what makes a cross-provider move a config change: setting
    GEMINI_FLASH_MODEL=anthropic:claude-opus-5 routes the whole Flash tier to
    Claude without touching code.
    """
    if ":" in model_id:
        provider, _, bare = model_id.partition(":")
        return provider, bare
    return "google_genai", model_id


def _provider_kwargs(provider: str, temperature: float | None) -> dict:
    """Sampling kwargs that the target provider actually accepts.

    Anthropic's current models (claude-opus-5, claude-sonnet-5, and the
    4.6/4.7/4.8 family) REJECT `temperature` with a 400 — sampling params were
    removed. Gemini 3.x Flash-Lite accepts it but silently ignores it. Passing
    it blindly across providers is therefore a hard failure on Claude, so it is
    dropped for anthropic and passed through elsewhere.
    """
    if provider == "anthropic":
        # Claude rejects `temperature` (sampling params removed on current
        # models). Thinking is configured here rather than in
        # _thinking_kwargs, which speaks Gemini's dialect only.
        #
        # `{"type": "adaptive"}` is the only on-mode for Sonnet 5 — omitting
        # `thinking` also runs adaptive, but stating it makes the intent
        # explicit and survives a future default change. `output_config.effort`
        # controls depth; drafting is the product's highest-stakes output, so
        # it gets "high" (also Anthropic's own default) rather than a
        # cost-tuned lower level.
        # MEASURED 2026-09-07 — effort is NOT set by default.
        # Forcing output_config.effort="high" on the S.138 drafting task made
        # things strictly worse: 110-133s vs ~68s unset, and $0.25-0.38 vs
        # $0.21, with no gain on the quality checks. effort=low/medium were
        # cheaper but did not fix the substantive miss either (Claude omits
        # the s.138(b) 30-day limitation plea inconsistently at every level).
        # Anthropic's own default is already "high"; setting it explicitly
        # measurably changed behaviour for the worse here, so the default is
        # left alone and exposed for tuning rather than pinned.
        kw = {"thinking": {"type": "adaptive"}}
        _effort = os.getenv("ANTHROPIC_EFFORT", "").strip()
        if _effort:
            kw["output_config"] = {"effort": _effort}
        return kw
    return {} if temperature is None else {"temperature": temperature}


def _output_tokens_kwarg(provider: str, max_output_tokens: int) -> dict:
    """Map the output-token-limit parameter to the provider's name.

    - google_genai / openai: `max_output_tokens`
    - anthropic: `max_tokens` (Claude rejects `max_output_tokens` with a
      TypeError). Claude Sonnet 5's hard ceiling is 16384; passing a higher
      value is silently capped by the API, but we clamp here to be explicit.
    """
    if provider == "anthropic":
        # 16384 is NOT Claude's ceiling — the Models API reports
        # max_tokens=128000 for claude-sonnet-5. It is a NON-STREAMING guard:
        # the SDK requires streaming for values large enough to risk the
        # 10-minute HTTP timeout, and this codebase invokes drafting
        # non-streaming. Raise ANTHROPIC_MAX_TOKENS only alongside switching
        # that path to .stream(); the API rejects large non-streaming requests
        # with "Streaming is required for operations that may take longer than
        # 10 minutes" (verified 2026-09-07 at 32000).
        cap = int(os.getenv("ANTHROPIC_MAX_TOKENS", "16384"))
        return {"max_tokens": min(max_output_tokens, cap)}
    return {"max_output_tokens": max_output_tokens}


def _thinking_kwargs(model_id: str,
                     thinking_budget: int | None,
                     thinking_level: str | None) -> dict:
    """Return the correct thinking kwarg for `model_id`'s generation.

    Gemini 2.5 models keep `thinking_budget` so that rolling GEMINI_*_MODEL
    back to a 2.5 id stays a working configuration.

    Non-Gemini providers get NOTHING from this function. Both `thinking_budget`
    and `thinking_level` are Gemini-specific spellings; Anthropic uses
    `thinking={"type": "adaptive"}` plus `output_config.effort`, and sending a
    Gemini kwarg to Claude is a 400. Wiring Anthropic's thinking controls is
    deliberately left undone until the path can be tested against a real key.
    """
    provider, bare = _split_provider(model_id)
    if provider != "google_genai":
        return {}
    model_id = bare
    is_25 = model_id.startswith("gemini-2.5")
    if is_25:
        # 2.5 does not understand thinking_level — map it back to a budget.
        if thinking_level is not None and thinking_budget is None:
            return {"thinking_budget": {"minimal": 0, "low": 1024,
                                        "medium": 4096, "high": 8192}
                    .get(thinking_level, 0)}
        return {"thinking_budget": thinking_budget if thinking_budget is not None else 0}

    # Gemini 3.x — thinking_level only. Never send both (400).
    if thinking_level is not None:
        return {"thinking_level": thinking_level}
    if thinking_budget is None:
        return {"thinking_level": GEMINI_THINKING_DEFAULT}
    if thinking_budget <= 2048:
        return {"thinking_level": "low"}
    return {"thinking_level": "medium"}


@lru_cache(maxsize=8)
def get_gemini_flash_lite(temperature: float = 0.3,
                          max_output_tokens: int = 8192,
                          thinking_budget: int | None = None,
                          thinking_level: str | None = None):
    """Cheapest tier — classification, routing, metadata, query rewrite.

    Model comes from settings.GEMINI_MODELS["flash_lite"]
    (default `gemini-3.5-flash-lite`).

    NOTE: Gemini 3.x Flash-Lite uses FIXED sampling defaults and IGNORES
    `temperature` (LangChain emits a UserWarning). Code that relied on
    temperature=0.0 for deterministic output no longer gets that guarantee
    on this tier — see MODEL_UPGRADE_PLAN.md §4.
    """
    model_id = GEMINI_MODELS["flash_lite"]
    provider, _bare = _split_provider(model_id)
    return init_chat_model(
        model_id if ":" in model_id else f"google_genai:{model_id}",
        max_output_tokens=max_output_tokens,
        max_retries=2,
        timeout=60,
        **_provider_kwargs(provider, temperature),
        **_thinking_kwargs(model_id, thinking_budget, thinking_level),
    )


# Alias: existing callers use get_gemini_flash() — keep pointing to Flash Lite
get_gemini_flash = get_gemini_flash_lite


@lru_cache(maxsize=8)
def get_gemini_flash_planning(temperature: float = 0.0,
                               max_output_tokens: int = 8192,
                               thinking_budget: int | None = None,
                               thinking_level: str | None = None):
    """Gemini-only Flash tier for internal planning / structured-output tasks.

    Always targets GEMINI_MODELS["flash"] (default gemini-3.8-flash), regardless
    of what the generation tier is set to.  Used by:
      - The drafting fan-out judge (with_structured_output → _FanoutStrategy)
      - Feedback / critic calls that run structured output against Pydantic
    These MUST stay on Gemini because:
      1. They use `with_structured_output()` which has different semantics on
         Anthropic (tool_choice vs function_call).
      2. They are internal planning calls, not user-facing answers.
    """
    model_id = GEMINI_MODELS["flash"]
    provider, _bare = _split_provider(model_id)
    return init_chat_model(
        model_id if ":" in model_id else f"google_genai:{model_id}",
        max_output_tokens=max_output_tokens,
        max_retries=2,
        timeout=120,
        **_provider_kwargs(provider, temperature),
        **_thinking_kwargs(model_id, thinking_budget, thinking_level),
    )


import os as _os  # noqa: E402
# Back-compat alias for rohit/dev's 2026-09-06 Flash upgrade.
#
# That change introduced GEMINI_FLASH_MODEL_ID after a WhatsApp-screenshot OCR
# incident where gemini-2.5-flash returned 504 DEADLINE_EXCEEDED on 2 of 3
# pages ("RPC from prefill to decode failed"); the 3.x Flash model OCR'd all 3
# in 36s vs 273s. That rationale still holds — the tier map in settings just
# generalises it to all three tiers. The GEMINI_FLASH_MODEL env var is
# unchanged and still rolls the Flash tier back (e.g. gemini-2.5-flash).
#
# `_flash_thinking_kwargs` was removed in the 2026-09-07 merge. It guarded on
# `"3.6" in GEMINI_FLASH_MODEL_ID`, so setting GEMINI_FLASH_MODEL to any other
# 3.x id (e.g. gemini-3.8-flash) slipped past it and sent thinking_budget=0 to
# a model that rejects it. It also returned {} on 3.6, which silently selected
# Gemini 3's DEFAULT thinking level — "high" — on every Flash generation call.
# `_thinking_kwargs` above replaces it: model-generation aware, and explicit.
GEMINI_FLASH_MODEL_ID = GEMINI_MODELS["flash"]


@lru_cache(maxsize=8)
def get_gemini_flash_full(temperature: float = 0.3,
                          max_output_tokens: int = 65535,
                          thinking_budget: int | None = None,
                          thinking_level: str | None = None):
    """Balanced Flash tier — response generation, synthesis, ReAct agents.

    Model comes from settings.GEMINI_MODELS["flash"] (default
    `gemini-3.8-flash` — both newer AND cheaper than 3.5-flash:
    $0.75/$3.75 vs $1.50/$9.00 per 1M tokens). Override with
    GEMINI_FLASH_MODEL env var.

    For Gemini targets, thinking defaults to "low" rather than the 2.5-era
    "off". See MODEL_UPGRADE_PLAN.md §1.
    """
    model_id = GEMINI_MODELS["flash"]
    provider, _bare = _split_provider(model_id)
    return init_chat_model(
        model_id if ":" in model_id else f"google_genai:{model_id}",
        **_output_tokens_kwarg(provider, max_output_tokens),
        max_retries=2,
        timeout=120,
        **_provider_kwargs(provider, temperature),
        **_thinking_kwargs(model_id, thinking_budget, thinking_level),
    )


@lru_cache(maxsize=8)
def get_gemini_vision(temperature: float = 0.0,
                      max_output_tokens: int = 8192,
                      thinking_budget: int | None = None,
                      thinking_level: str | None = None):
    """Vision OCR tier — scanned PDFs and image uploads.

    Model comes from settings.GEMINI_MODELS["vision"] (default
    `gemini-3.6-flash`), pinned SEPARATELY from the flash tier: this choice
    rests on the 2026-09-06 production OCR incident, not on benchmarks, and
    should not drift when the general flash tier is bumped. See the
    GEMINI_MODELS comment in core/settings.py before changing it.
    """
    model_id = GEMINI_MODELS["vision"]
    provider, _bare = _split_provider(model_id)
    return init_chat_model(
        model_id if ":" in model_id else f"google_genai:{model_id}",
        max_output_tokens=max_output_tokens,
        max_retries=2,
        timeout=180,
        **_provider_kwargs(provider, temperature),
        **_thinking_kwargs(model_id, thinking_budget, thinking_level),
    )


@lru_cache(maxsize=8)
def get_drafting_llm(max_output_tokens: int = 65535,
                     thinking_budget: int | None = None,
                     thinking_level: str | None = None):
    """Legal drafting — the highest-stakes output in the product.

    Uses settings.GEMINI_MODELS["generation"] (default
    `anthropic:claude-sonnet-5`). Override with GENERATION_MODEL env var.

    Claude Sonnet 5 is the default because drafting is constrained
    format-following against a retrieved template — a production workload
    where Anthropic's own guidance recommends Sonnet over Opus. It runs in
    adaptive thinking mode; `temperature` is fixed at 0.4 for Gemini
    fallbacks and suppressed for Claude.

    If Anthropic Claude encounters an error (e.g. credit limit, network,
    or service degradation), it automatically falls back to Gemini 3.8 Flash
    so drafts never fail.

    To revert to Gemini Flash permanently: set GENERATION_MODEL=gemini-3.8-flash.
    """
    model_id = GEMINI_MODELS["generation"]
    provider, _bare = _split_provider(model_id)
    primary = init_chat_model(
        model_id if ":" in model_id else f"google_genai:{model_id}",
        **_output_tokens_kwarg(provider, max_output_tokens),
        max_retries=1,
        timeout=180,
        **_provider_kwargs(provider, 0.4),
        **_thinking_kwargs(model_id, thinking_budget, thinking_level),
    )
    if provider == "anthropic":
        # LOUD FALLBACK.
        #
        # 2026-09-07: a full "Claude Sonnet 5 vs Gemini" drafting evaluation
        # was run and reported as a Claude win. Every one of those calls was
        # actually served by Gemini: the Anthropic account had no credit, the
        # 400 was swallowed by this fallback, and nothing in the logs, the
        # response, or the test output said so. The conclusion was Gemini
        # compared against Gemini.
        #
        # A fallback that hides which model answered makes every downstream
        # measurement untrustworthy, so it now announces itself. Callers that
        # must know can read `response.response_metadata["model_name"]` — and
        # any test asserting a model change MUST assert on that field rather
        # than on the call merely succeeding.
        _log.warning(
            "Generation tier is Anthropic — a Gemini fallback is armed. If "
            "Anthropic errors (credit, quota, outage) the response will be "
            "served by Gemini and will NOT be the configured model. Verify "
            "with response_metadata['model_name'] before drawing conclusions.",
            configured=model_id, fallback=GEMINI_MODELS["flash"],
        )
        fb_model = GEMINI_MODELS["flash"]
        fallback = init_chat_model(
            fb_model if ":" in fb_model else f"google_genai:{fb_model}",
            max_output_tokens=min(max_output_tokens, 65535),
            max_retries=2,
            timeout=180,
            **_provider_kwargs("google_genai", 0.4),
            **_thinking_kwargs(fb_model, thinking_budget, thinking_level),
        )
        return primary.with_fallbacks([fallback])
    return primary



def cacheable_system(text: str) -> "str | list":
    """System-message content that Anthropic will prompt-cache.

    Returns the string unchanged for Gemini/OpenAI. For Anthropic, wraps it in
    a single text block carrying `cache_control: ephemeral` so the prefix is
    written to cache once and read back at 0.1x input price
    ($0.20/MTok vs $2.00 on claude-sonnet-5).

    This matters here because DRAFTING_SYSTEM_PROMPT is ~10,100 tokens and is
    byte-identical on every request for a given language + niche combination —
    it is the single largest repeated cost in the drafting path. Caching is a
    PREFIX match, so anything volatile must stay in the user message, which is
    already how drafting is structured (facts and query go in the human block).

    Minimum cacheable prefix is 1024-4096 tokens depending on model; the
    drafting prompt clears it comfortably. A shorter prompt simply won't cache
    and costs the same as today, so this is safe to apply unconditionally.
    """
    provider, _bare = _split_provider(GEMINI_MODELS["generation"])
    if provider != "anthropic":
        return text
    return [{"type": "text", "text": text,
             "cache_control": {"type": "ephemeral"}}]


# --- Google GenAI client (for Gemini with Google Search grounding) ---

@lru_cache(maxsize=1)
def get_genai_client() -> genai.Client:
    """Google GenAI client for web-grounded search (Scenario agent)."""
    return genai.Client(
        http_options={"timeout": 120_000},  # 120s timeout for web-grounded search
    )


# --- ChromaDB client (PersistentClient — v1 behaviour restored 2026-07-01) ---
#
# Every process/worker uses its own PersistentClient view of the on-disk
# chroma_store. Matches how v1 shipped for a year without incident and
# how huge PDFs were handled without timing out.
#
# History: an intermediate "chroma-server" mode (lawttorney-chroma.service
# HttpClient) was tried during the scale-50 work under the assumption that
# multi-worker writes to the same SQLite file would race. In practice it
# introduced a shared SQLAlchemy pool (~5 connections) that gunicorn's
# workers can exhaust in seconds, producing the misleading
# "vector embedding timed out" event and leaving collections empty.
# The theoretical corruption never surfaced; the pool exhaustion did,
# repeatedly. We're reverting to PersistentClient permanently. If real
# corruption is ever observed, we'll fix it locally (SQLite WAL mode,
# per-collection locks) rather than reintroducing a shared server.
#
# CHROMA_SERVER_HOST is intentionally ignored — the mode is baked in
# so it can't be re-enabled by a stale env var.

_chroma_client = None


def get_chroma_client():
    """Singleton ChromaDB client — always PersistentClient (v1 mode)."""
    import chromadb
    global _chroma_client
    if _chroma_client is None:
        from .settings import CHROMA_STORE_ROOT
        _chroma_client = chromadb.PersistentClient(path=CHROMA_STORE_ROOT)
        _log.info(
            "ChromaDB PersistentClient initialized",
            path=CHROMA_STORE_ROOT,
        )
    return _chroma_client


# --- Embedding Models ---
# When EMBEDDING_SERVICE_URL is set, embeddings are computed by a remote
# microservice (services/embedding_service.py) instead of loading models
# locally. This allows multi-worker deployments without OOM crashes.

_retriever_embeddings = None
_qa_embeddings = None


def get_retriever_embeddings():
    """BGE-large-en-v1.5 for legal document retrieval (ES hybrid search, ChromaDB).

    Returns a LangChain-compatible Embeddings object — either a remote HTTP
    client (production) or a local HuggingFaceEmbeddings (development).
    """
    global _retriever_embeddings
    if _retriever_embeddings is None:
        if EMBEDDING_SERVICE_URL:
            from .embedding_client import RemoteEmbeddings
            _retriever_embeddings = RemoteEmbeddings(
                EMBEDDING_SERVICE_URL, model_name="retriever",
            )
        else:
            _retriever_embeddings = HuggingFaceEmbeddings(
                model_name=EMBEDDING_MODELS["retriever"],
                model_kwargs={"device": "cpu"},
                encode_kwargs={"normalize_embeddings": True},
            )
    return _retriever_embeddings


def get_qa_embeddings():
    """all-MiniLM-L6-v2 for PDF document Q&A.

    Returns a LangChain-compatible Embeddings object — either a remote HTTP
    client (production) or a local HuggingFaceEmbeddings (development).
    """
    global _qa_embeddings
    if _qa_embeddings is None:
        if EMBEDDING_SERVICE_URL:
            from .embedding_client import RemoteEmbeddings
            _qa_embeddings = RemoteEmbeddings(
                EMBEDDING_SERVICE_URL, model_name="qa",
            )
        else:
            _qa_embeddings = HuggingFaceEmbeddings(
                model_name=EMBEDDING_MODELS["pdf_qa"],
                model_kwargs={"device": "cpu"},
            )
    return _qa_embeddings


# --- Eager-load both embedding models in local mode ---
# In remote mode the models live in the embed microservice; nothing to load
# here. In local mode (the prod default since 2026-06-28), we must load
# both BGE-large (retriever) and MiniLM (QA) at module import so that
# gunicorn's preload_app=True puts them in the master's address space
# BEFORE workers fork — each forked worker then inherits the resident
# models via copy-on-write. Resident memory: ~1.3 GB BGE + ~80 MB MiniLM
# = ~1.4 GB shared across master + all 6 workers (single copy, not 7
# copies). Both models are hot from request #1 on every worker.
#
# Without this, the first request that triggers an embed path pays the
# 3-15s model load inside the request hot path — counting against
# CHROMA_STORE_TIMEOUT_S (180s) for PDFs and against the relevance-gate
# budget for ES queries.
#
# Guarded by EMBEDDING_SERVICE_URL so remote-mode deployments skip
# the local load. Per-model try/except so a BGE failure doesn't block
# MiniLM (or vice versa). Eager-load failure falls back to lazy on
# first call — never blocks startup.
if not EMBEDDING_SERVICE_URL:
    for _name, _loader in (
        ("retriever (BGE-large)", get_retriever_embeddings),
        ("QA (MiniLM)", get_qa_embeddings),
    ):
        try:
            _t0 = time.time()
            _loader()
            _log.info("Embedding model eager-loaded at import",
                      model=_name, load_s=round(time.time() - _t0, 2))
        except Exception as _e:
            _log.warning("Embedding model eager-load failed; will lazy-load",
                         model=_name, error=str(_e)[:200])
