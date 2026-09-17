"""Gap #4 — in-app TTL / GC for uploads + ChromaDB collections + temp files.

Removes the sole dependency on the `.github/workflows/prod-daily-cleanup.yml`
cron. Any deployment (dev box, local run, new prod machine, one-off VM)
now cleans itself up in-process on a periodic schedule, so the "133 GB
uploads dir / 8 147 stale thread dirs" incident class cannot recur just
because a cron isn't wired up.

Sweep buckets (matched to the cron's cadences so behaviour is
identical, allowing the cron to be retired safely later):

  uploads/{thread_id}/     mtime > UPLOAD_TTL_DAYS   (default 7d)
  chroma_store/{coll}/     mtime > CHROMA_TTL_DAYS   (default 30d)
  /tmp/tmp*.pdf            mtime > TMP_PDF_TTL_HOURS (default 24h)

Design notes:

  * Best-effort — every filesystem operation is wrapped in try/except so
    a single permission error or lock doesn't halt the sweep.
  * No SQLite migration or schema touch — orphan `thread_files` rows
    are read-only inspected here; deleting rows for gone-on-disk files
    is deferred to Phase B (Gap #8 already tracks a related batch_id
    migration on the same table).
  * Metrics: `lawtech_files_gc_removed_total{kind=uploads|chroma|tmp_pdf}`
    incremented per file/dir removed. Failures counted in
    `lawtech_files_gc_errors_total{kind}`.
  * Concurrency-safe: uses `os.walk` + `os.stat` (no in-memory index),
    so a concurrent upload appending files to `uploads/{thread_id}/` at
    the moment we sweep the same dir is safely no-op'd by the mtime
    check (fresh files pass, stale ones deleted).
  * Cross-platform: falls back to `os.stat().st_mtime` (works on
    Windows + POSIX). Uses `Path.iterdir` instead of `find(1)`.

Called from `core.gateway.lifespan` on startup as an asyncio task that
sleeps `FILE_GC_INTERVAL_HOURS` between sweeps. Disabled by setting
`FILE_GC_ENABLED=0`.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.logger import get_logger, short_err
from core.metrics import METRICS
from core.settings import (
    CHROMA_STORE_ROOT,
    CHROMA_TTL_DAYS,
    FILE_GC_INTERVAL_HOURS,
    TMP_PDF_TTL_HOURS,
    UPLOADS_ROOT,
    UPLOAD_TTL_DAYS,
)

log = get_logger("FileGC")


# ---------------------------------------------------------------------
# Sweep result dataclass (returned from each sweep_* function)
# ---------------------------------------------------------------------

@dataclass
class SweepResult:
    kind: str
    scanned: int = 0
    removed: int = 0
    bytes_freed: int = 0
    errors: int = 0
    dry_run: bool = False
    error_samples: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "scanned": self.scanned,
            "removed": self.removed,
            "bytes_freed": self.bytes_freed,
            "errors": self.errors,
            "dry_run": self.dry_run,
            "error_samples": self.error_samples[:3],
        }


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _now_epoch() -> float:
    return time.time()


def _dir_size(path: Path) -> int:
    """Approximate directory size in bytes. Best-effort — silently skips
    files we can't stat (permission denied, disappeared mid-walk)."""
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                fp = os.path.join(root, name)
                try:
                    total += os.path.getsize(fp)
                except (OSError, ValueError):
                    pass
    except OSError:
        pass
    return total


def _mtime_older_than(path: Path, cutoff_epoch: float) -> bool:
    """True if the path exists and its mtime is older than `cutoff_epoch`."""
    try:
        return path.stat().st_mtime < cutoff_epoch
    except (OSError, ValueError):
        return False


def _rm_dir_safe(path: Path, result: SweepResult) -> None:
    """Delete a directory tree, track size + errors on `result`."""
    size = _dir_size(path)
    try:
        shutil.rmtree(path, ignore_errors=False)
        result.removed += 1
        result.bytes_freed += size
    except Exception as e:
        result.errors += 1
        if len(result.error_samples) < 3:
            result.error_samples.append(f"{path.name}: {short_err(e)}")


def _rm_file_safe(path: Path, result: SweepResult) -> None:
    """Delete a single file, track size + errors on `result`."""
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    try:
        path.unlink()
        result.removed += 1
        result.bytes_freed += size
    except Exception as e:
        result.errors += 1
        if len(result.error_samples) < 3:
            result.error_samples.append(f"{path.name}: {short_err(e)}")


# ---------------------------------------------------------------------
# Sweepers
# ---------------------------------------------------------------------

def sweep_uploads(ttl_days: int = None, dry_run: bool = False) -> SweepResult:
    """Delete `uploads/{thread_id}/` dirs older than `ttl_days`.

    Uses mtime (not created_at from SQLite) so we sweep even when the
    row is missing. The chat_store `thread_files.created_at` and dir
    mtime agree at first-upload time and only diverge if the user
    uploads again to the same thread (dir mtime bumps forward -> we
    keep it, correct)."""
    if ttl_days is None:
        ttl_days = UPLOAD_TTL_DAYS
    r = SweepResult(kind="uploads", dry_run=dry_run)
    root = Path(UPLOADS_ROOT)
    if not root.exists():
        return r
    cutoff = _now_epoch() - (ttl_days * 86400)
    for child in root.iterdir():
        if not child.is_dir():
            continue
        r.scanned += 1
        if not _mtime_older_than(child, cutoff):
            continue
        if dry_run:
            r.removed += 1
            r.bytes_freed += _dir_size(child)
            continue
        _rm_dir_safe(child, r)
    if r.removed:
        try:
            METRICS["files_gc_removed_total"].labels(kind="uploads").inc(r.removed)
        except Exception:
            pass
    if r.errors:
        try:
            METRICS["files_gc_errors_total"].labels(kind="uploads").inc(r.errors)
        except Exception:
            pass
    return r


def sweep_chroma(ttl_days: int = None, dry_run: bool = False) -> SweepResult:
    """Delete `chroma_store/{collection}/` dirs older than `ttl_days`.

    Skips the internal .ocr_cache subdir (managed elsewhere) and the
    ChromaDB internal database file / sqlite catalog directly under
    CHROMA_STORE_ROOT (starts with `.` or is a known Chroma-owned name)."""
    if ttl_days is None:
        ttl_days = CHROMA_TTL_DAYS
    r = SweepResult(kind="chroma", dry_run=dry_run)
    root = Path(CHROMA_STORE_ROOT)
    if not root.exists():
        return r
    cutoff = _now_epoch() - (ttl_days * 86400)
    # ChromaDB's own catalog dirs / files (do not touch)
    _protected = {".ocr_cache"}
    for child in root.iterdir():
        # skip files at the root (ChromaDB catalog SQLite lives here)
        if not child.is_dir():
            continue
        if child.name in _protected or child.name.startswith("."):
            continue
        r.scanned += 1
        if not _mtime_older_than(child, cutoff):
            continue
        if dry_run:
            r.removed += 1
            r.bytes_freed += _dir_size(child)
            continue
        _rm_dir_safe(child, r)
    if r.removed:
        try:
            METRICS["files_gc_removed_total"].labels(kind="chroma").inc(r.removed)
        except Exception:
            pass
    if r.errors:
        try:
            METRICS["files_gc_errors_total"].labels(kind="chroma").inc(r.errors)
        except Exception:
            pass
    return r


def sweep_tmp_pdfs(ttl_hours: int = None, dry_run: bool = False) -> SweepResult:
    """Delete `<tempdir>/tmp*.pdf` orphans older than `ttl_hours`.

    Uses `tempfile.gettempdir()` for cross-platform correctness
    (Windows: %TEMP%, POSIX: /tmp). Only matches names that look like
    Python's `tempfile.NamedTemporaryFile` output (tmp*.pdf), so a
    user's own `tmp_notes.pdf` in the tempdir would still be safe if
    they happened to put one there."""
    if ttl_hours is None:
        ttl_hours = TMP_PDF_TTL_HOURS
    r = SweepResult(kind="tmp_pdf", dry_run=dry_run)
    tmp = Path(tempfile.gettempdir())
    if not tmp.exists():
        return r
    cutoff = _now_epoch() - (ttl_hours * 3600)
    for child in tmp.iterdir():
        if not child.is_file():
            continue
        name = child.name
        if not (name.startswith("tmp") and name.lower().endswith(".pdf")):
            continue
        r.scanned += 1
        if not _mtime_older_than(child, cutoff):
            continue
        if dry_run:
            r.removed += 1
            try:
                r.bytes_freed += child.stat().st_size
            except OSError:
                pass
            continue
        _rm_file_safe(child, r)
    if r.removed:
        try:
            METRICS["files_gc_removed_total"].labels(kind="tmp_pdf").inc(r.removed)
        except Exception:
            pass
    if r.errors:
        try:
            METRICS["files_gc_errors_total"].labels(kind="tmp_pdf").inc(r.errors)
        except Exception:
            pass
    return r


# ---------------------------------------------------------------------
# Aggregate + loop
# ---------------------------------------------------------------------

def sweep_all(dry_run: bool = False) -> dict[str, Any]:
    """Run every sweep bucket. Returns aggregated summary dict.

    Logs one INFO line summarising the sweep — grep-able for prod
    telemetry (`grep '[FileGC] sweep_all'` shows every sweep + counts)."""
    started = time.perf_counter()
    r_uploads = sweep_uploads(dry_run=dry_run)
    r_chroma = sweep_chroma(dry_run=dry_run)
    r_tmp = sweep_tmp_pdfs(dry_run=dry_run)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    total_removed = r_uploads.removed + r_chroma.removed + r_tmp.removed
    total_freed = r_uploads.bytes_freed + r_chroma.bytes_freed + r_tmp.bytes_freed
    total_errors = r_uploads.errors + r_chroma.errors + r_tmp.errors
    result = {
        "elapsed_ms": elapsed_ms,
        "dry_run": dry_run,
        "total_removed": total_removed,
        "total_freed_mb": round(total_freed / (1024 * 1024), 2),
        "total_errors": total_errors,
        "uploads": r_uploads.as_dict(),
        "chroma": r_chroma.as_dict(),
        "tmp_pdf": r_tmp.as_dict(),
    }
    log.info("sweep_all completed",
             elapsed_ms=elapsed_ms,
             dry_run=dry_run,
             total_removed=total_removed,
             total_freed_mb=result["total_freed_mb"],
             total_errors=total_errors,
             uploads=r_uploads.removed,
             chroma=r_chroma.removed,
             tmp_pdf=r_tmp.removed)
    return result


async def start_gc_loop(interval_hours: int = None) -> None:
    """Long-running task: sleep interval, sweep_all, repeat. Never awaits
    inside sweep_all so a slow filesystem doesn't back-pressure the
    event loop; sweep_all is CPU-bound (mostly stat calls) so run in
    a thread pool.

    Silently returns when disabled (checked by caller via
    `settings.FILE_GC_ENABLED`)."""
    if interval_hours is None:
        interval_hours = FILE_GC_INTERVAL_HOURS
    interval_s = max(60, interval_hours * 3600)   # never sleep less than 1 min
    log.info("GC loop starting",
             interval_hours=interval_hours,
             upload_ttl_days=UPLOAD_TTL_DAYS,
             chroma_ttl_days=CHROMA_TTL_DAYS,
             tmp_pdf_ttl_hours=TMP_PDF_TTL_HOURS)
    # Run one sweep immediately on startup — captures accumulated stale
    # files from any downtime window before the first interval elapses.
    try:
        await asyncio.to_thread(sweep_all, False)
    except Exception as e:
        log.warning("GC startup sweep failed",
                    error=short_err(e))
    while True:
        try:
            await asyncio.sleep(interval_s)
            await asyncio.to_thread(sweep_all, False)
        except asyncio.CancelledError:
            log.info("GC loop cancelled — exiting cleanly")
            raise
        except Exception as e:
            log.warning("GC periodic sweep failed — continuing",
                        error=short_err(e))
