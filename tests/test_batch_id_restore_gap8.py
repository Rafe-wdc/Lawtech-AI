"""Gap #8 — `batch_id`-based file-context restoration.

Verifies the fixed `_restore_file_context` behaviour:

  1. Files with the same batch_id are grouped together, regardless of
     how far apart their created_at timestamps are.
  2. Files with different batch_ids are treated as separate batches;
     restore returns only the latest batch.
  3. Legacy rows with empty batch_id fall back to the ±10s timestamp
     window (the pre-Gap-#8 behaviour) — old threads don't break.
  4. Mixed scenarios (one batch with batch_id, one legacy) route
     correctly.

Uses direct calls to `_restore_file_context` after seeding fake
`chat_store.load_thread_files` output — no live DB required.

Run: `python -m tests.test_batch_id_restore_gap8`
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from dotenv import load_dotenv

load_dotenv()


def _passed(name: str) -> tuple[bool, str]:
    return True, f"[OK]  {name}"


def _failed(name: str, detail: str = "") -> tuple[bool, str]:
    return False, f"[FAIL] {name}  {detail}"


def _fake_row(
    file_id: str,
    batch_id: str,
    created_at_utc: datetime,
    chroma_collection: str | None = None,
    filename: str | None = None,
) -> dict:
    return {
        "file_id": file_id,
        "filename": filename or f"{file_id}.pdf",
        "batch_id": batch_id,
        "created_at": created_at_utc.isoformat(),
        "chromadb_collection": chroma_collection or f"col_{file_id}",
        "local_path": f"/uploads/thread/{file_id}",
        "upload_error": "",
        "file_kind": "",
        "file_kind_confidence": 0.0,
    }


async def _run_restore(rows: list[dict]) -> dict | None:
    from agents.memory import _restore_file_context
    with patch(
        "agents.memory.chat_store.load_thread_files",
        AsyncMock(return_value=rows),
    ):
        return await _restore_file_context("test_thread")


async def main() -> int:
    results: list[tuple[bool, str]] = []
    now = datetime.now(timezone.utc)

    # ---- Test 1: two batches by batch_id, latest returned ----
    print("--- T1: two batch_ids, latest wins ---")
    rows = [
        _fake_row("f1", "batchA", now - timedelta(hours=2)),
        _fake_row("f2", "batchA", now - timedelta(hours=2, minutes=5)),
        _fake_row("f3", "batchB", now - timedelta(minutes=1)),
        _fake_row("f4", "batchB", now - timedelta(seconds=30)),
    ]
    out = await _run_restore(rows)
    got_names = set(out.get("file_names", [])) if out else set()
    print(f"  file_names: {got_names}")
    results.append(
        _passed("T1 returns only batchB (f3 + f4)")
        if got_names == {"f3.pdf", "f4.pdf"}
        else _failed("T1", detail=f"got={got_names}")
    )

    # ---- Test 2: single batch, spread far apart in time ----
    # OLD behaviour would split this into two "batches" because the
    # timestamps are >10s apart. batch_id fixes it.
    print("\n--- T2: single batch, timestamps 30s apart ---")
    rows = [
        _fake_row("g1", "wide_batch", now - timedelta(seconds=45)),
        _fake_row("g2", "wide_batch", now - timedelta(seconds=15)),
        _fake_row("g3", "wide_batch", now),
    ]
    out = await _run_restore(rows)
    got_names = set(out.get("file_names", [])) if out else set()
    print(f"  file_names: {got_names}")
    results.append(
        _passed("T2 keeps all 3 files despite >10s timestamp spread")
        if got_names == {"g1.pdf", "g2.pdf", "g3.pdf"}
        else _failed("T2", detail=f"got={got_names}")
    )

    # ---- Test 3: rapid double-click, DIFFERENT batch_ids ----
    # OLD behaviour would merge these (10s window). batch_id keeps them apart.
    print("\n--- T3: two batches within 5s but different batch_ids ---")
    rows = [
        _fake_row("h1", "click_1", now - timedelta(seconds=3)),
        _fake_row("h2", "click_2", now - timedelta(seconds=1)),
    ]
    out = await _run_restore(rows)
    got_names = set(out.get("file_names", [])) if out else set()
    print(f"  file_names: {got_names}")
    results.append(
        _passed("T3 keeps only latest click_2 (h2), not merged with click_1")
        if got_names == {"h2.pdf"}
        else _failed("T3", detail=f"got={got_names}")
    )

    # ---- Test 4: legacy fallback — all rows have empty batch_id ----
    print("\n--- T4: legacy rows (batch_id=''), ±10s window applies ---")
    rows = [
        _fake_row("i1", "", now - timedelta(seconds=45)),   # older batch (past 10s)
        _fake_row("i2", "", now - timedelta(seconds=3)),    # latest batch
        _fake_row("i3", "", now - timedelta(seconds=1)),    # latest batch
    ]
    out = await _run_restore(rows)
    got_names = set(out.get("file_names", [])) if out else set()
    print(f"  file_names: {got_names}")
    results.append(
        _passed("T4 legacy path returns latest ±10s window (i2 + i3)")
        if got_names == {"i2.pdf", "i3.pdf"}
        else _failed("T4", detail=f"got={got_names}")
    )

    # ---- Test 5: latest row has batch_id but older rows are legacy ----
    print("\n--- T5: mixed batch_id + legacy; latest has batch_id ---")
    rows = [
        _fake_row("j1", "", now - timedelta(hours=1)),      # ancient, no batch_id
        _fake_row("j2", "new_batch", now - timedelta(seconds=5)),
        _fake_row("j3", "new_batch", now - timedelta(seconds=1)),
    ]
    out = await _run_restore(rows)
    got_names = set(out.get("file_names", [])) if out else set()
    print(f"  file_names: {got_names}")
    results.append(
        _passed("T5 mixed: uses batch_id path when latest has one (j2 + j3 only)")
        if got_names == {"j2.pdf", "j3.pdf"}
        else _failed("T5", detail=f"got={got_names}")
    )

    # ---- Test 6: empty input ----
    print("\n--- T6: no thread files at all ---")
    out = await _run_restore([])
    results.append(
        _passed("T6 returns None on empty input")
        if out is None
        else _failed("T6", detail=f"got={out}")
    )

    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print()
    print("=" * 78)
    print(f"SUMMARY: {passed}/{total} passed")
    print("=" * 78)
    for _, line in results:
        print(f"  {line}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
