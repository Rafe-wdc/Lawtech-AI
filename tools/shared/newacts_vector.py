"""In-process vector ranking for the `newacts_v1` index.

WHY THIS EXISTS
---------------
Hybrid (BM25 + vector) search on the new criminal codes never ran. The
index stores a correct 1024-dim BGE embedding on every row, but the field is
not mapped as `knn_vector`, so OpenSearch rejects every vector function:

    cosineSimilarity(params.query_vector, 'embedding')        -> class_cast_exception
    cosineSimilarity(params.query_vector, doc['embedding'])   -> class_cast_exception
    knn_score                                                 -> "field type must be knn_vector"

Every topic query logged "Hybrid search failed, falling back to BM25-only",
and BM25 alone misses sections whose text does not contain the user's words
("punishment for cheating" did not surface BNS 318 in the top 100).

Re-mapping the field needs index-admin rights the application user does not
have, so the vectors are scored here instead. The index is small (about 2,400
rows, ~10 MB as float32), so it is loaded once per process and searched with
one matrix product.

`fuse()` interleaves the BM25 and vector rankings, so neither score scale has
to be calibrated against the other.
"""

from __future__ import annotations

import re
import threading
import time

import numpy as np

from core.clients import get_es_client
from core.logger import get_logger
from core.settings import ES_INDICES

log = get_logger("NewactsVector")

# Rows change only when the corpus is re-ingested; refresh a few times a day.
CACHE_TTL_S = 6 * 3600
_SCROLL_PAGE = 200

_lock = threading.Lock()
_cache: dict | None = None   # {"loaded_at", "ids", "sources", "sections", "matrix"}


def _load() -> dict:
    """Read every row's id, source, section number and embedding."""
    es = get_es_client()
    index = ES_INDICES["newacts"]
    ids: list[str] = []
    sources: list[str] = []
    sections: list[str] = []
    vectors: list[list[float]] = []
    body = {
        "size": _SCROLL_PAGE,
        "_source": ["source", "section_number", "embedding"],
        "query": {"exists": {"field": "embedding"}},
        "sort": [{"_id": "asc"}],
    }
    search_after = None
    while True:
        if search_after is not None:
            body["search_after"] = search_after
        hits = es.search(index=index, body=body, request_timeout=60)["hits"]["hits"]
        if not hits:
            break
        for h in hits:
            emb = h["_source"].get("embedding")
            if not isinstance(emb, list) or not emb:
                continue
            ids.append(h["_id"])
            sources.append(h["_source"].get("source") or "")
            sections.append(str(h["_source"].get("section_number") or ""))
            vectors.append(emb)
        search_after = hits[-1]["sort"]
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix /= norms
    return {
        "loaded_at": time.monotonic(),
        "ids": ids,
        "sources": np.asarray(sources, dtype=object),
        "sections": np.asarray(sections, dtype=object),
        "matrix": matrix,
    }


_loading = False


def _refresh() -> None:
    """Load the matrix and swap it in. Runs on a worker thread."""
    global _cache, _loading
    try:
        t0 = time.monotonic()
        fresh = _load()
        _cache = fresh
        log.info("Newacts embeddings loaded", rows=len(fresh["ids"]),
                 dims=int(fresh["matrix"].shape[1]) if len(fresh["ids"]) else 0,
                 load_s=round(time.monotonic() - t0, 2))
    except Exception as e:  # keep serving BM25 (or the stale matrix)
        log.warning("Newacts embeddings load failed; vector ranking unavailable",
                    error=f"{type(e).__name__}: {e}"[:200])
    finally:
        with _lock:
            _loading = False


def warm_in_background() -> None:
    """Start loading the matrix unless a load is already running."""
    global _loading
    with _lock:
        if _loading:
            return
        _loading = True
    threading.Thread(target=_refresh, name="newacts-vector-load", daemon=True).start()


def get_index(block: bool = False) -> dict | None:
    """The cached embedding matrix.

    The first load reads ~45 MB from the index, so a request never waits for
    it: with `block=False` a cold cache returns None (the caller ranks with
    BM25 alone) and a load starts in the background; an expired cache is
    served stale while it refreshes. `block=True` is for scripts and tests.
    """
    global _loading
    c = _cache
    if c is not None and time.monotonic() - c["loaded_at"] < CACHE_TTL_S:
        return c
    if not block:
        warm_in_background()
        return c
    with _lock:
        someone_else_loading = _loading
        _loading = True
    if someone_else_loading:
        while _loading:
            time.sleep(0.2)
    else:
        _refresh()
    return _cache


def reset_cache() -> None:
    global _cache
    with _lock:
        _cache = None


# The Act's own name in the query pulls in its short-title and repeal
# sections, which repeat that name ("Bharatiya Nagarik Suraksha Sanhita"
# matched BNSS s.1 and s.530 ahead of the section asked about). The Act is
# already applied as a filter, so it is dropped from the text that is embedded.
_ACT_NAME_RE = re.compile(
    r"\b(?:the\s+)?(?:"
    r"bharatiya\s+nyaya\s+sanhita|bharatiya\s+nagarik\s+suraksha\s+sanhita|"
    r"bharatiya\s+sakshya\s+adhiniyam|indian\s+penal\s+code|"
    r"code\s+of\s+criminal\s+procedure|indian\s+evidence\s+act|"
    r"bnss|bns|bsa|ipc|cr\.?p\.?c\.?|iea"
    r")\b(?:\s*,?\s*(?:18|19|20)\d{2})?",
    re.IGNORECASE,
)
_LEADING_FILLER_RE = re.compile(
    r"\b(?:under|in|of|as per|according to|corresponding|and its|and the)\s*(?=[?.,;]|$)",
    re.IGNORECASE,
)


def text_for_embedding(query: str) -> str:
    """`query` without Act names, for embedding only."""
    stripped = _ACT_NAME_RE.sub(" ", query or "")
    stripped = _LEADING_FILLER_RE.sub(" ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip(" ,.;?")
    # A query that was only an Act name keeps its original text.
    return stripped if len(stripped) >= 8 else (query or "")


def vector_rank(query_vector, source: str | None = None,
                sections: list[str] | None = None, k: int = 50) -> list[tuple[str, float]]:
    """Top `k` (doc_id, cosine) for `query_vector`, optionally scoped to one
    source file (an Act) and/or a set of section numbers."""
    idx = get_index()
    if idx is None or not idx["ids"]:
        return []
    q = np.asarray(query_vector, dtype=np.float32)
    n = float(np.linalg.norm(q))
    if n == 0:
        return []
    scores = idx["matrix"] @ (q / n)
    mask = np.ones(len(scores), dtype=bool)
    if source:
        mask &= idx["sources"] == source
    if sections:
        mask &= np.isin(idx["sections"], [str(s) for s in sections])
    candidates = np.flatnonzero(mask)
    if candidates.size == 0:
        return []
    order = candidates[np.argsort(-scores[candidates])][:k]
    return [(idx["ids"][i], float(scores[i])) for i in order]


def fuse(bm25_ids: list[str], vector_ids: list[str], size: int = 20) -> list[tuple[str, float]]:
    """Interleave the two rankings, vector first -> [(doc_id, score)].

    The top hit of each ranking always lands in the first two results. That
    matters because the rankings fail differently: BM25 finds nothing for a
    lay term the statute does not use ("mob lynching", "hit and run"), and
    the vector ranking drifts on short exact phrases. Reciprocal-rank fusion
    was measured first and rejected: a section ranked 1st by vectors and
    absent from BM25 lost to sections ranked ~10th in both, so BNS 103 came
    13th for "punishment for mob lynching". On 32 natural-language questions
    with a known section (2026-10-07), top-5 recall was BM25 20, RRF 23,
    vector alone 26, interleaved 27.

    `score` is a descending position score so callers that sort by `_score`
    keep this order.
    """
    out: list[str] = []
    seen: set[str] = set()
    for i in range(max(len(bm25_ids), len(vector_ids))):
        for ranking in (vector_ids, bm25_ids):
            if i < len(ranking) and ranking[i] not in seen:
                seen.add(ranking[i])
                out.append(ranking[i])
    out = out[:size]
    return [(doc_id, 1.0 - pos / max(len(out), 1)) for pos, doc_id in enumerate(out)]


def hybrid_hits(bm25_body: dict, query_text: str, source: str | None = None,
                sections: list[str] | None = None, size: int = 20) -> list[dict]:
    """Run `bm25_body`, add the vector ranking, return fused hits.

    Hits keep the OpenSearch shape (`_id`, `_source`, `_score`) so callers do
    not change. When the vectors are unavailable (cold cache, embedding
    service down) the BM25 hits are returned as they are.
    """
    from core.clients import get_retriever_embeddings

    es = get_es_client()
    index = ES_INDICES["newacts"]
    body = dict(bm25_body)
    body["_source"] = {"excludes": ["embedding"]}
    bm25 = es.search(index=index, body=body)["hits"]["hits"]
    try:
        if get_index() is None:
            log.info("Newacts vector ranking skipped: embeddings still loading")
            return bm25[:size]
        qv = get_retriever_embeddings().embed_query(text_for_embedding(query_text))
        ranked = vector_rank(qv, source=source, sections=sections, k=50)
    except Exception as e:
        log.warning("Newacts vector ranking failed; using BM25 only",
                    error=f"{type(e).__name__}: {e}"[:200])
        return bm25[:size]
    if not ranked:
        return bm25[:size]

    fused = fuse([h["_id"] for h in bm25], [d for d, _ in ranked], size=size)
    by_id = {h["_id"]: h for h in bm25}
    missing = [d for d, _ in fused if d not in by_id]
    if missing:
        fetched = es.search(index=index, body={
            "size": len(missing),
            "_source": {"excludes": ["embedding"]},
            "query": {"ids": {"values": missing}},
        })["hits"]["hits"]
        by_id.update({h["_id"]: h for h in fetched})
    out = []
    for doc_id, score in fused:
        hit = by_id.get(doc_id)
        if hit is not None:
            out.append({**hit, "_score": score})
    log.info("Newacts hybrid ranking", bm25=len(bm25), vector=len(ranked),
             fused=len(out), vector_only=len(missing))
    return out
