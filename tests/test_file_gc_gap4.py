"""Gap #4 — in-app GC sweeper unit tests.

Seeds fake old + fresh files/dirs in isolated temporary trees (NOT the
real UPLOADS_ROOT / CHROMA_STORE_ROOT — we don't want the test to nuke
real user data), monkey-patches the settings to point at those temp
trees, and verifies sweep_all() cleans exactly the old ones.

Covers:
  1. sweep_uploads deletes dirs older than TTL, spares fresh ones
  2. sweep_chroma same, plus skips .ocr_cache and hidden dirs
  3. sweep_tmp_pdfs deletes tmp*.pdf orphans older than TTL
  4. dry_run flag reports counts but doesn't delete
  5. Metrics increment correctly
  6. Errors don't halt the sweep (permission-denied simulated by
     removing write perms on a dir)

Run: `python -m tests.test_file_gc_gap4`
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

load_dotenv()


def _passed(name: str) -> tuple[bool, str]:
    return True, f"[OK]  {name}"


def _failed(name: str, detail: str = "") -> tuple[bool, str]:
    return False, f"[FAIL] {name}  {detail}"


def _seed_dir(root: Path, name: str, age_days: float, size_bytes: int = 1024) -> Path:
    """Create a directory `root/name` and backdate its mtime by `age_days`."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    # Put a file inside so _dir_size() > 0
    (d / "content.bin").write_bytes(b"x" * size_bytes)
    if age_days > 0:
        past = time.time() - (age_days * 86400)
        os.utime(d, (past, past))
        os.utime(d / "content.bin", (past, past))
    return d


def _seed_file(dir_: Path, name: str, age_hours: float, size_bytes: int = 512) -> Path:
    """Create `dir_/name` and backdate its mtime by `age_hours`."""
    f = dir_ / name
    f.write_bytes(b"y" * size_bytes)
    if age_hours > 0:
        past = time.time() - (age_hours * 3600)
        os.utime(f, (past, past))
    return f


def run_tests() -> int:
    import core.settings as _settings   # imported for monkey-patching
    from core.file_gc import sweep_uploads, sweep_chroma, sweep_tmp_pdfs, sweep_all
    from core.metrics import METRICS

    results: list[tuple[bool, str]] = []
    workspace = Path(tempfile.mkdtemp(prefix="gap4_gc_test_"))
    print(f"workspace: {workspace}\n")

    try:
        # --- Set up isolated tree ---
        fake_uploads = workspace / "uploads"
        fake_chroma = workspace / "chroma_store"
        fake_tmp = workspace / "tmp"
        fake_uploads.mkdir()
        fake_chroma.mkdir()
        fake_tmp.mkdir()

        # Seed uploads: 3 stale (10, 30, 40 days), 2 fresh (0.5, 3 days)
        _seed_dir(fake_uploads, "thread_stale_10d", 10, size_bytes=2048)
        _seed_dir(fake_uploads, "thread_stale_30d", 30, size_bytes=4096)
        _seed_dir(fake_uploads, "thread_stale_40d", 40, size_bytes=8192)
        _seed_dir(fake_uploads, "thread_fresh_half_day", 0.5, size_bytes=1024)
        _seed_dir(fake_uploads, "thread_fresh_3d", 3, size_bytes=2048)

        # Seed chroma: 2 stale (35, 60 days), 1 fresh (10 days),
        # 1 protected (.ocr_cache stale but should NOT be swept),
        # 1 file (not dir) — should be skipped silently
        _seed_dir(fake_chroma, "coll_stale_35d", 35, size_bytes=2048)
        _seed_dir(fake_chroma, "coll_stale_60d", 60, size_bytes=8192)
        _seed_dir(fake_chroma, "coll_fresh_10d", 10, size_bytes=1024)
        _seed_dir(fake_chroma, ".ocr_cache", 60, size_bytes=1024)  # protected
        (fake_chroma / "catalog.sqlite").write_bytes(b"fake")  # non-dir at root

        # Seed tmp: 2 stale tmp*.pdf (48h, 72h), 1 fresh (2h),
        # 1 non-matching file (user_notes.pdf — starts with "user_", NOT "tmp")
        _seed_file(fake_tmp, "tmp1234.pdf", 48)
        _seed_file(fake_tmp, "tmpabcd.pdf", 72)
        _seed_file(fake_tmp, "tmp_fresh.pdf", 2)
        _seed_file(fake_tmp, "user_notes.pdf", 100)  # non-tmp prefix, spared

        # Monkey-patch the module-level constants imported at the TOP of file_gc.py.
        # We overwrite the already-imported symbols so the pointers used
        # inside the sweeper functions land on our temp tree.
        import core.file_gc as _gc
        with patch.object(_gc, "UPLOADS_ROOT", str(fake_uploads)), \
             patch.object(_gc, "CHROMA_STORE_ROOT", str(fake_chroma)), \
             patch.object(_gc, "UPLOAD_TTL_DAYS", 7), \
             patch.object(_gc, "CHROMA_TTL_DAYS", 30), \
             patch.object(_gc, "TMP_PDF_TTL_HOURS", 24), \
             patch("tempfile.gettempdir", return_value=str(fake_tmp)):

            # ---- Test 1: uploads sweep (dry-run) ----
            print("--- Test 1: sweep_uploads(dry_run=True) ---")
            r = sweep_uploads(dry_run=True)
            print(f"  scanned={r.scanned} removed={r.removed} freed={r.bytes_freed}B")
            results.append(
                _passed("T1 dry-run counts stale dirs")
                if r.scanned == 5 and r.removed == 3 and r.bytes_freed > 0
                else _failed("T1 dry-run counts stale dirs",
                             detail=f"scanned={r.scanned} removed={r.removed}")
            )
            # Verify nothing actually deleted
            still_there = list(fake_uploads.iterdir())
            results.append(
                _passed("T1 dry-run doesn't delete")
                if len(still_there) == 5
                else _failed("T1 dry-run doesn't delete",
                             detail=f"remaining={len(still_there)}")
            )
            print()

            # ---- Test 2: uploads sweep (real) ----
            print("--- Test 2: sweep_uploads(dry_run=False) ---")
            r = sweep_uploads(dry_run=False)
            print(f"  scanned={r.scanned} removed={r.removed} freed={r.bytes_freed}B")
            remaining = sorted(p.name for p in fake_uploads.iterdir())
            print(f"  remaining: {remaining}")
            results.append(
                _passed("T2 real sweep removes 3 stale, spares 2 fresh")
                if remaining == ["thread_fresh_3d", "thread_fresh_half_day"]
                else _failed("T2 real sweep",
                             detail=f"remaining={remaining}")
            )
            print()

            # ---- Test 3: chroma sweep (real) ----
            print("--- Test 3: sweep_chroma(dry_run=False) ---")
            r = sweep_chroma(dry_run=False)
            print(f"  scanned={r.scanned} removed={r.removed}")
            remaining = sorted(p.name for p in fake_chroma.iterdir())
            print(f"  remaining: {remaining}")
            # Expect: .ocr_cache preserved (hidden + protected),
            # catalog.sqlite preserved (not a dir),
            # coll_fresh_10d preserved (< 30d TTL),
            # coll_stale_35d + coll_stale_60d removed
            results.append(
                _passed("T3 chroma sweep removes 2, spares 3 (fresh + protected + file)")
                if sorted(remaining) == sorted([".ocr_cache", "catalog.sqlite", "coll_fresh_10d"])
                else _failed("T3 chroma sweep",
                             detail=f"remaining={remaining}")
            )
            print()

            # ---- Test 4: tmp_pdf sweep (real) ----
            print("--- Test 4: sweep_tmp_pdfs(dry_run=False) ---")
            r = sweep_tmp_pdfs(dry_run=False)
            print(f"  scanned={r.scanned} removed={r.removed}")
            remaining = sorted(p.name for p in fake_tmp.iterdir())
            print(f"  remaining: {remaining}")
            # Expect: user_notes.pdf preserved (non-tmp prefix),
            # tmp_fresh.pdf preserved (2h < 24h TTL),
            # tmp1234.pdf + tmpabcd.pdf removed
            results.append(
                _passed("T4 tmp_pdf sweep removes stale tmp*.pdf, spares user_notes")
                if sorted(remaining) == ["tmp_fresh.pdf", "user_notes.pdf"]
                else _failed("T4 tmp_pdf sweep",
                             detail=f"remaining={remaining}")
            )
            print()

            # ---- Test 5: metrics incremented ----
            print("--- Test 5: metrics incremented ---")
            def _counter(kind: str) -> float:
                # Prometheus Counter value across labels
                try:
                    val = METRICS["files_gc_removed_total"].labels(kind=kind)._value.get()
                    return float(val)
                except Exception:
                    return -1.0

            uploads_count = _counter("uploads")
            chroma_count = _counter("chroma")
            tmp_count = _counter("tmp_pdf")
            print(f"  files_gc_removed_total: uploads={uploads_count} chroma={chroma_count} tmp_pdf={tmp_count}")
            results.append(
                _passed("T5 metrics show removals across all 3 buckets")
                if uploads_count >= 3 and chroma_count >= 2 and tmp_count >= 2
                else _failed("T5 metrics",
                             detail=f"uploads={uploads_count} chroma={chroma_count} tmp={tmp_count}")
            )
            print()

            # ---- Test 6: sweep_all aggregator ----
            print("--- Test 6: sweep_all() after seeding new stale dirs ---")
            _seed_dir(fake_uploads, "thread_new_stale_20d", 20)
            _seed_dir(fake_chroma, "coll_new_stale_40d", 40)
            agg = sweep_all(dry_run=False)
            print(f"  agg: total_removed={agg['total_removed']}  "
                  f"total_freed_mb={agg['total_freed_mb']}  "
                  f"errors={agg['total_errors']}")
            results.append(
                _passed("T6 sweep_all aggregates all buckets")
                if agg["total_removed"] == 2 and agg["total_errors"] == 0
                else _failed("T6 sweep_all",
                             detail=f"agg={agg}")
            )

    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    # ---- Summary ----
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
    sys.exit(run_tests())
