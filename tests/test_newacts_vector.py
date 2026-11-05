"""In-process vector ranking for the new-codes index, fused with BM25.

Hybrid search on `newacts_v1` never ran: the `embedding` field is not a
knn_vector, every script_score query raised class_cast_exception, and the
agent fell back to BM25 alone ("punishment for cheating" did not surface
BNS 318). These tests pin the replacement with a fake index and fake
embeddings: no network, no model.
"""

from __future__ import annotations

import time

import numpy as np

import tools.shared.newacts_vector as nv

BNS = "/content/Bharitya New Acts/bns.csv"
BNSS = "/content/Bharitya New Acts/bnss.csv"


def _install_index(monkeypatch, rows):
    """rows: [(doc_id, source, section, vector)]"""
    m = np.asarray([r[3] for r in rows], dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    monkeypatch.setattr(nv, "_cache", {
        "loaded_at": time.monotonic(),
        "ids": [r[0] for r in rows],
        "sources": np.asarray([r[1] for r in rows], dtype=object),
        "sections": np.asarray([r[2] for r in rows], dtype=object),
        "matrix": m,
    })


ROWS = [
    ("d318", BNS, "318", [1.0, 0.0, 0.0]),
    ("d303", BNS, "303", [0.0, 1.0, 0.0]),
    ("d1",   BNS, "1",   [0.2, 0.2, 0.9]),
    ("d173", BNSS, "173", [0.9, 0.1, 0.0]),
]


def test_vector_rank_orders_by_cosine_and_scopes_to_the_act(monkeypatch):
    _install_index(monkeypatch, ROWS)
    ranked = nv.vector_rank([1.0, 0.05, 0.0], source=BNS, k=10)
    assert [d for d, _ in ranked] == ["d318", "d1", "d303"]   # BNSS row excluded
    assert ranked[0][1] > ranked[1][1]
    assert [d for d, _ in nv.vector_rank([1.0, 0.0, 0.0], k=2)] == ["d318", "d173"]


def test_vector_rank_section_filter_and_empty_cases(monkeypatch):
    _install_index(monkeypatch, ROWS)
    assert [d for d, _ in nv.vector_rank([0, 1, 0], source=BNS, sections=["303"])] == ["d303"]
    assert nv.vector_rank([0, 0, 0]) == []                      # zero vector
    assert nv.vector_rank([1, 0, 0], source="/nope.csv") == []


def test_fuse_keeps_the_top_hit_of_each_ranking_in_the_first_two():
    """A section the vectors rank first and BM25 never returns must not be
    buried under sections that are mid-table in both ("mob lynching")."""
    bm25 = [f"b{i}" for i in range(10)]
    vector = ["v_top"] + [f"b{i}" for i in range(9)]
    ids = [d for d, _ in nv.fuse(bm25, vector, size=20)]
    assert ids[0] == "v_top" and ids[1] == "b0"
    scores = [s for _, s in nv.fuse(bm25, vector, size=20)]
    assert scores == sorted(scores, reverse=True)       # order survives a sort by score


def test_fuse_dedupes_and_respects_size():
    ids = [d for d, _ in nv.fuse(["a", "b", "c"], ["c", "a", "d"], size=3)]
    assert ids == ["c", "a", "b"]


def test_act_name_is_dropped_from_the_embedded_text():
    assert nv.text_for_embedding(
        "What is the punishment for cheating under the Bharatiya Nyaya Sanhita, 2023?"
    ) == "What is the punishment for cheating"
    assert nv.text_for_embedding("anticipatory bail under BNSS") == "anticipatory bail"
    # a query that is only an Act name is left alone
    assert nv.text_for_embedding("BNS") == "BNS"


class _FakeES:
    def __init__(self, bm25_hits, by_id):
        self.bm25_hits, self.by_id, self.bodies = bm25_hits, by_id, []

    def search(self, index=None, body=None, **_):
        self.bodies.append(body)
        if "ids" in body.get("query", {}):
            wanted = body["query"]["ids"]["values"]
            return {"hits": {"hits": [self.by_id[i] for i in wanted if i in self.by_id]}}
        return {"hits": {"hits": list(self.bm25_hits)}}


class _FakeEmb:
    def __init__(self, vec):
        self.vec, self.seen = vec, []

    def embed_query(self, text):
        self.seen.append(text)
        return self.vec


def _hit(doc_id, section):
    return {"_id": doc_id, "_score": 1.0,
            "_source": {"page_content": f"text {section}", "source": BNS,
                        "section_number": section}}


def test_hybrid_hits_adds_a_section_bm25_missed(monkeypatch):
    """BM25 returns only s.303 and s.1; the vector ranking brings in s.318."""
    _install_index(monkeypatch, ROWS)
    es = _FakeES([_hit("d303", "303"), _hit("d1", "1")], {"d318": _hit("d318", "318")})
    emb = _FakeEmb([1.0, 0.0, 0.0])
    monkeypatch.setattr(nv, "get_es_client", lambda: es)
    monkeypatch.setattr("core.clients.get_retriever_embeddings", lambda: emb)

    hits = nv.hybrid_hits({"size": 50, "query": {"match_all": {}}},
                          "punishment for cheating under BNS", source=BNS)
    sections = [h["_source"]["section_number"] for h in hits]
    assert "318" in sections
    assert emb.seen == ["punishment for cheating"]              # Act name stripped
    assert es.bodies[0]["_source"] == {"excludes": ["embedding"]}
    assert all("_score" in h for h in hits)


def test_hybrid_hits_falls_back_to_bm25_when_vectors_are_not_ready(monkeypatch):
    monkeypatch.setattr(nv, "_cache", None)
    monkeypatch.setattr(nv, "warm_in_background", lambda: None)   # no real load
    es = _FakeES([_hit("d303", "303")], {})
    monkeypatch.setattr(nv, "get_es_client", lambda: es)
    hits = nv.hybrid_hits({"size": 50, "query": {"match_all": {}}}, "theft", source=BNS)
    assert [h["_id"] for h in hits] == ["d303"]
    assert len(es.bodies) == 1


def test_hybrid_hits_falls_back_to_bm25_when_embedding_fails(monkeypatch):
    _install_index(monkeypatch, ROWS)
    es = _FakeES([_hit("d303", "303")], {})
    monkeypatch.setattr(nv, "get_es_client", lambda: es)

    class _Boom:
        def embed_query(self, _):
            raise ConnectionError("embedding service down")

    monkeypatch.setattr("core.clients.get_retriever_embeddings", lambda: _Boom())
    hits = nv.hybrid_hits({"size": 50, "query": {"match_all": {}}}, "theft", source=BNS)
    assert [h["_id"] for h in hits] == ["d303"]


def test_newacts_query_no_longer_uses_a_vector_script():
    """The script that OpenSearch rejected must not come back."""
    from agents.newacts import ActQueryMetadata, _build_newacts_query
    meta = ActQueryMetadata(section_number=None,
                            act_name="The Bharatiya Nyaya Sanhita, 2023", hybrid_search=True)
    body = _build_newacts_query(meta, "punishment for cheating")
    assert "script_score" not in str(body) and "cosineSimilarity" not in str(body)
    assert "boosting" in body["query"]
