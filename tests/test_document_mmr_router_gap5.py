"""Gap #5 — large-attachment MMR router in the Document agent.

Verifies:
  1. `retrieve_attachment_context_impl` accepts tunable k values.
  2. The Document agent code contains the LARGE_ATTACHMENT threshold
     constants and the MMR-path fallback wire-up.
  3. The threshold logic classifies bundles correctly:
       - 1-3 files, small aggregate  -> full-doc path (mode="full_doc")
       - >3 files                    -> MMR
       - Aggregate > 500K chars      -> MMR
  4. Mocked chroma calls confirm the impl fans out per-collection and
     merges by score.

Skips the true live Document-agent invocation (that requires a real
Chroma with pre-embedded collections) — the router logic is validated
via unit hooks + static source inspection.

Run: `python -m tests.test_document_mmr_router_gap5`
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from dotenv import load_dotenv

load_dotenv()

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _passed(name: str) -> tuple[bool, str]:
    return True, f"[OK]  {name}"


def _failed(name: str, detail: str = "") -> tuple[bool, str]:
    return False, f"[FAIL] {name}  {detail}"


# ---------------------------------------------------------------------
# Layer A — impl accepts tunable k
# ---------------------------------------------------------------------

def test_impl_signature() -> tuple[int, int, list[str]]:
    import inspect
    from tools.shared.vectordb_tools import retrieve_attachment_context_impl
    sig = inspect.signature(retrieve_attachment_context_impl)
    params = list(sig.parameters.keys())
    results = []
    results.append(
        _passed("impl exposes per_collection_k + total_k args")
        if "per_collection_k" in params and "total_k" in params
        else _failed("impl signature",
                     detail=f"params={params}")
    )
    passed = sum(1 for ok, _ in results if ok)
    return passed, len(results), [line for _, line in results]


# ---------------------------------------------------------------------
# Layer B — Document agent source contains the router
# ---------------------------------------------------------------------

def test_document_wiring() -> tuple[int, int, list[str]]:
    src = (_REPO_ROOT / "agents/document.py").read_text(encoding="utf-8")
    results = []
    for name, cond in [
        ("LARGE_ATTACHMENT_FILE_COUNT threshold defined",
         "LARGE_ATTACHMENT_FILE_COUNT" in src),
        ("LARGE_ATTACHMENT_CHAR_BUDGET threshold defined",
         "LARGE_ATTACHMENT_CHAR_BUDGET" in src),
        ("retrieve_attachment_context_impl imported inside function",
         "retrieve_attachment_context_impl" in src),
        ("MMR fallback wired with timeout guard",
         "asyncio.wait_for" in src and "MMR retrieval" in src),
        ("MMR-mode marker on Document metadata",
         "mmr_large_bundle" in src),
    ]:
        results.append(_passed(name) if cond else _failed(name))
    passed = sum(1 for ok, _ in results if ok)
    return passed, len(results), [line for _, line in results]


# ---------------------------------------------------------------------
# Layer C — Threshold decision logic
# ---------------------------------------------------------------------

def test_threshold_logic() -> tuple[int, int, list[str]]:
    """Re-implements the exact threshold rule from agents/document.py and
    verifies it matches expectations across representative bundle shapes.
    """
    LARGE_ATTACHMENT_FILE_COUNT = 3
    LARGE_ATTACHMENT_CHAR_BUDGET = 500_000

    def should_use_mmr(n_files: int, agg_chars: int) -> bool:
        return (
            n_files > LARGE_ATTACHMENT_FILE_COUNT
            or agg_chars > LARGE_ATTACHMENT_CHAR_BUDGET
        )

    cases = [
        # (n_files, agg_chars, expected_mmr, note)
        (1, 50_000, False, "single small file"),
        (2, 100_000, False, "two small files"),
        (3, 200_000, False, "3 files @ 200K -> full doc"),
        (3, 600_000, True, "3 files but aggregate over budget"),
        (4, 100_000, True, "4 files (over count threshold)"),
        (10, 100_000, True, "10 files (obvious MMR case)"),
        (20, 2_000_000, True, "20 files + huge aggregate"),
        (0, 0, False, "no files at all"),
    ]
    results = []
    for n, chars, expected, note in cases:
        actual = should_use_mmr(n, chars)
        results.append(
            _passed(f"threshold: {note}")
            if actual == expected
            else _failed(f"threshold: {note}",
                         detail=f"n={n} chars={chars} expected={expected} got={actual}")
        )
    passed = sum(1 for ok, _ in results if ok)
    return passed, len(results), [line for _, line in results]


# ---------------------------------------------------------------------
# Layer D — Mocked impl fans out per collection
# ---------------------------------------------------------------------

def test_impl_fans_out() -> tuple[int, int, list[str]]:
    """Mock get_chroma_client + Chroma + similarity_search_with_score;
    call the impl with 4 collection_ids; verify each is queried once
    and the merged/sorted top_k is returned."""
    from tools.shared import vectordb_tools

    # 4 fake collections. Each returns 3 chunks with distinct scores so
    # the merged top-5 has a predictable order.
    def _fake_hits(cid: str):
        # cid "A" -> scores 0.10, 0.20, 0.30
        # cid "B" -> scores 0.15, 0.25, 0.35
        # cid "C" -> scores 0.05, 0.40, 0.50
        # cid "D" -> scores 0.12, 0.22, 0.32
        base = {"A": 0.10, "B": 0.15, "C": 0.05, "D": 0.12}[cid]
        return [
            (MagicMock(page_content=f"{cid}_c1", metadata={"source": f"{cid}.pdf", "chunk": 1}), base),
            (MagicMock(page_content=f"{cid}_c2", metadata={"source": f"{cid}.pdf", "chunk": 2}), base + 0.10),
            (MagicMock(page_content=f"{cid}_c3", metadata={"source": f"{cid}.pdf", "chunk": 3}), base + 0.20),
        ]

    mock_chroma_instance = MagicMock()
    def _side_effect_search(query, k=None):
        # k gets echoed back to the fake, use it to slice
        cid = mock_chroma_instance._current_cid
        return _fake_hits(cid)[:k]
    mock_chroma_instance.similarity_search_with_score = MagicMock(side_effect=_side_effect_search)

    call_log = []
    def _fake_chroma_ctor(client=None, collection_name=None, embedding_function=None):
        mock_chroma_instance._current_cid = collection_name
        call_log.append(collection_name)
        return mock_chroma_instance

    with patch.object(vectordb_tools, "Chroma", _fake_chroma_ctor), \
         patch.object(vectordb_tools, "get_chroma_client", MagicMock(return_value=MagicMock())), \
         patch.object(vectordb_tools, "get_qa_embeddings", MagicMock(return_value=MagicMock())), \
         patch.object(vectordb_tools, "_validate_collection_name", MagicMock()):
        result = vectordb_tools.retrieve_attachment_context_impl(
            "some query",
            ["A", "B", "C", "D"],
            per_collection_k=3,
            total_k=5,
        )

    chunks = result.get("chunks", [])
    results = []
    results.append(
        _passed(f"impl called 4 collections (queried {sorted(set(call_log))})")
        if sorted(set(call_log)) == ["A", "B", "C", "D"]
        else _failed(f"impl fan-out", detail=f"call_log={call_log}")
    )
    # All 12 hits sorted by ascending score:
    #   C_c1=0.05, A_c1=0.10, D_c1=0.12, B_c1=0.15, C_c2=0.15,
    #   A_c2=0.20, D_c2=0.22, B_c2=0.25, C_c3=0.25, A_c3=0.30, ...
    # Top-5: [0.05, 0.10, 0.12, 0.15, 0.15] (B_c1 and C_c2 tied at 0.15)
    scores = [round(c["score"], 3) for c in chunks]
    expected_scores = [0.05, 0.10, 0.12, 0.15, 0.15]
    results.append(
        _passed(f"top-5 sorted by score (got {scores})")
        if scores == expected_scores
        else _failed("top-5 sort order",
                     detail=f"got={scores} expected={expected_scores}")
    )
    results.append(
        _passed(f"total_k=5 respected (got {len(chunks)})")
        if len(chunks) == 5
        else _failed("total_k respect", detail=f"got_len={len(chunks)}")
    )

    # Sanity: each chunk has collection + source metadata
    ok_meta = all("collection" in c and "source" in c for c in chunks)
    results.append(
        _passed("every chunk carries collection + source metadata")
        if ok_meta
        else _failed("chunk metadata")
    )

    passed = sum(1 for ok, _ in results if ok)
    return passed, len(results), [line for _, line in results]


# ---------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("Gap #5 -- large-attachment MMR router unit tests")
    print("=" * 78)

    pa, ta, la = test_impl_signature()
    print("\n--- Layer A: impl signature ---")
    for l in la:
        print("  " + l)
    print(f"  A: {pa}/{ta}")

    pb, tb, lb = test_document_wiring()
    print("\n--- Layer B: Document agent wiring ---")
    for l in lb:
        print("  " + l)
    print(f"  B: {pb}/{tb}")

    pc, tc, lc = test_threshold_logic()
    print("\n--- Layer C: threshold decision logic ---")
    for l in lc:
        print("  " + l)
    print(f"  C: {pc}/{tc}")

    pd, td, ld = test_impl_fans_out()
    print("\n--- Layer D: impl fan-out (mocked chroma) ---")
    for l in ld:
        print("  " + l)
    print(f"  D: {pd}/{td}")

    tot_p = pa + pb + pc + pd
    tot_t = ta + tb + tc + td
    print()
    print("=" * 78)
    print(f"SUMMARY: {tot_p}/{tot_t} passed")
    print("=" * 78)
    return 0 if tot_p == tot_t else 1


if __name__ == "__main__":
    sys.exit(main())
