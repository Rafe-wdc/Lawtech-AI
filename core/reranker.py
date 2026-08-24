"""Cross-encoder reranking (app-side, optional, fail-safe).

Reorders a set of already-retrieved ES `hits` by *true* query-passage
relevance, using a **local** cross-encoder. This is the single biggest
retrieval-precision lever, and — unlike adding vector search — it works on
BM25-only paths too, because it reorders whatever the first stage returned.

Why local: the model runs on this box like the BGE embedder, so no client
data leaves your infrastructure (privacy) and there is no per-call API cost.

Why app-side (not the native OpenSearch rerank pipeline): the OpenSearch
cluster is AWS-managed, so the native pipeline needs cluster-level config
(an ML connector + search pipeline). App-side reranking slots into the
existing `hits -> docs_text` flow with no cluster changes and is portable.

Safety contract (this module must NEVER break a query):
- Off by default. Enable with `RERANKER_ENABLED=true`.
- If the package/model is missing, the model fails to load, scoring errors,
  or the input is empty/tiny, `rerank()` returns the input hits UNCHANGED.

Public API:
    rerank(query, hits, top_k=None) -> list[hit]   # reordered (+ optional trim)
    is_enabled() -> bool
"""
from __future__ import annotations

import os
import threading
import time

from core.logger import get_logger

log = get_logger("Reranker")

# --- Config (read once) ---
_ENABLED = os.getenv("RERANKER_ENABLED", "false").strip().lower() in ("1", "true", "yes")
# Default: a small, fast, widely-used cross-encoder. Swap via env for quality:
#   - cross-encoder/ms-marco-MiniLM-L-6-v2   (default; ~80MB, English, fast)
#   - BAAI/bge-reranker-base                 (~1.1GB, stronger)
#   - BAAI/bge-reranker-v2-m3                (~2.3GB, strongest + multilingual)
# May also be a local path under ./models (like the embedders).
_MODEL_NAME = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2").strip()
# Max chars of each passage fed to the scorer — cross-encoders truncate long
# inputs anyway, and this bounds CPU time. The head of a legal chunk carries
# the section heading + subject, which is what discriminates relevance.
_MAX_PASSAGE_CHARS = int(os.getenv("RERANKER_MAX_PASSAGE_CHARS", "2000"))

_model = None
_model_load_failed = False
_lock = threading.Lock()


def is_enabled() -> bool:
    """True when reranking is switched on via RERANKER_ENABLED."""
    return _ENABLED


def _get_model():
    """Lazily load the cross-encoder once. Returns None on any failure."""
    global _model, _model_load_failed
    if _model is not None or _model_load_failed:
        return _model
    with _lock:
        if _model is not None or _model_load_failed:
            return _model
        try:
            from sentence_transformers import CrossEncoder

            # Prefer a local copy under ./models (offline, like the embedders);
            # fall back to the HF id, which downloads + caches on first use.
            from core.settings import _PROJECT_ROOT  # type: ignore

            local_path = _PROJECT_ROOT / "models" / _MODEL_NAME.split("/")[-1]
            model_ref = str(local_path) if local_path.exists() else _MODEL_NAME

            t0 = time.time()
            _model = CrossEncoder(model_ref, max_length=512)
            log.info("Reranker model loaded", model=model_ref,
                     load_s=round(time.time() - t0, 2))
        except Exception as e:
            _model_load_failed = True
            log.warning("Reranker model load failed — reranking disabled for this run",
                        model=_MODEL_NAME, error=str(e).splitlines()[0][:200])
    return _model


def _passage_of(hit: dict) -> str:
    """Extract the passage text from an ES hit dict (fail-safe)."""
    try:
        text = (hit.get("_source", {}) or {}).get("page_content", "") or ""
    except Exception:
        text = ""
    return text[:_MAX_PASSAGE_CHARS]


def rerank(query: str, hits: list, top_k: int | None = None) -> list:
    """Reorder `hits` by cross-encoder relevance to `query`.

    Args:
        query: the user/search query.
        hits: list of ES hit dicts (each with `_source.page_content`).
        top_k: if given, keep only the top-k after reranking; else keep all
            (reorder-only — the default, since downstream char caps already
            bound the prompt size).

    Returns the reranked hits, each annotated with `_rerank_score`. On any
    problem (disabled, model missing, scoring error, <2 hits) returns the
    input hits UNCHANGED — reranking must never break retrieval.
    """
    if not _ENABLED:
        return hits
    if not hits or len(hits) < 2:
        return hits

    model = _get_model()
    if model is None:
        return hits

    try:
        pairs = [(query, _passage_of(h)) for h in hits]
        scores = model.predict(pairs)  # list[float], higher = more relevant

        order = sorted(range(len(hits)), key=lambda i: float(scores[i]), reverse=True)
        reranked = []
        for rank, i in enumerate(order):
            h = hits[i]
            # Annotate for telemetry / downstream thresholding without mutating
            # the original list order semantics elsewhere.
            try:
                h = dict(h)
                h["_rerank_score"] = float(scores[i])
                h["_rerank_position"] = rank
            except Exception:
                pass
            reranked.append(h)

        if top_k is not None and top_k > 0:
            reranked = reranked[:top_k]

        log.debug("Reranked hits",
                  n=len(hits), top_score=round(float(scores[order[0]]), 3),
                  moved_top=(order[0] != 0))
        return reranked
    except Exception as e:
        log.warning("Reranking failed — returning original order",
                    error=str(e).splitlines()[0][:200])
        return hits
