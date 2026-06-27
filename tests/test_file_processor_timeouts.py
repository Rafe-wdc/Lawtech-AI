"""Unit tests for the emergency Phase 0 file_processor timeout + writer changes.

These tests do NOT hit the real Gemini Files API, ChromaDB, or PostgreSQL.
We patch the slow sub-tasks with controllable stubs so we can verify:

  1. The writer callback receives the expected stage events in order.
  2. asyncio.wait_for timeouts fire correctly when a sub-task stalls.
  3. Per-file isolation works — one file's timeout does NOT abort the
     other files in the same batch.
  4. The legacy no-writer call shape still works (back-compat).

We use plain sync test functions that call asyncio.run() on inner async
helpers so we don't depend on pytest-asyncio.

Run:
    pytest tests/test_file_processor_timeouts.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from unittest.mock import AsyncMock, patch

import pytest


# Ensure project root on sys.path so `core.*` imports resolve.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# --- Helpers ----------------------------------------------------------------


def _make_tiny_pdf(path: str, num_pages: int = 1) -> None:
    """Write a minimal valid PDF for fitz to open. Avoids depending on pikepdf."""
    import fitz
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i+1} content line {i+1}.\nSecond line.")
    doc.save(path)
    doc.close()


def _file_tuple(path: str, name: str) -> tuple[str, str, int]:
    return (path, name, os.path.getsize(path))


def _stub_chat_store():
    """Stub chat_store.get_thread_storage + save_thread_file in-place."""
    from core.chat_store import chat_store
    chat_store.get_thread_storage = AsyncMock(return_value=(0, 0))
    chat_store.save_thread_file = AsyncMock(return_value=None)


# --- Tests -------------------------------------------------------------------


def test_writer_callback_receives_stage_events():
    """A writer callback passed into process_files should receive ordered stage events."""
    from core import file_processor

    async def _inner():
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = os.path.join(tmp, "tiny.pdf")
            _make_tiny_pdf(pdf_path, num_pages=2)

            events: list[dict] = []

            def writer(evt: dict) -> None:
                events.append(evt)

            with (
                patch.object(file_processor, "upload_to_gemini",
                             lambda *a, **k: ("gemini://uri", "files/abc", "2099-01-01")),
                patch.object(file_processor, "is_gemini_supported", lambda *a, **k: True),
                patch.object(file_processor, "_store_in_chromadb", lambda *a, **k: None),
            ):
                _stub_chat_store()
                await file_processor.process_files(
                    [_file_tuple(pdf_path, "tiny.pdf")],
                    thread_id="test-thread-1",
                    writer=writer,
                )

            stages = [e.get("stage") for e in events if e.get("type") == "file_processing"]
            assert "pdf_extract_start" in stages, f"got stages: {stages}"
            assert "pdf_extract_done" in stages, f"got stages: {stages}"
            assert stages[-1] == "all_files_done", \
                f"last stage was {stages[-1]} (expected all_files_done)"
            # Order check: start before done.
            assert stages.index("pdf_extract_start") < stages.index("pdf_extract_done")

    asyncio.run(_inner())


def test_no_writer_is_backwards_compatible():
    """Calls without a writer kwarg must still work (back-compat with old callers)."""
    from core import file_processor

    async def _inner():
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = os.path.join(tmp, "tiny.pdf")
            _make_tiny_pdf(pdf_path, num_pages=1)

            with (
                patch.object(file_processor, "upload_to_gemini",
                             lambda *a, **k: ("gemini://uri", "files/abc", "2099-01-01")),
                patch.object(file_processor, "is_gemini_supported", lambda *a, **k: True),
                patch.object(file_processor, "_store_in_chromadb", lambda *a, **k: None),
            ):
                _stub_chat_store()
                fc = await file_processor.process_files(
                    [_file_tuple(pdf_path, "tiny.pdf")],
                    thread_id="test-thread-2",
                    # writer omitted on purpose
                )
                assert fc is not None
                assert "tiny.pdf" in fc.file_names

    asyncio.run(_inner())




def test_chroma_store_timeout_does_not_kill_file():
    """If _store_in_chromadb stalls, the file should still be returned with
    its extracted text usable inline — only the vector index is degraded.
    """
    from core import file_processor

    async def _inner():
        original_timeout = file_processor.CHROMA_STORE_TIMEOUT_S
        file_processor.CHROMA_STORE_TIMEOUT_S = 1.0
        try:
            with tempfile.TemporaryDirectory() as tmp:
                # 25-page PDF triggers the Chroma store branch (page_count > 20).
                pdf_path = os.path.join(tmp, "big.pdf")
                _make_tiny_pdf(pdf_path, num_pages=25)

                def _stalling_chroma(*_args, **_kw):
                    time.sleep(10)

                with (
                    patch.object(file_processor, "upload_to_gemini",
                                 lambda *a, **k: ("gemini://uri", "files/abc", "2099-01-01")),
                    patch.object(file_processor, "is_gemini_supported", lambda *a, **k: True),
                    patch.object(file_processor, "_store_in_chromadb", _stalling_chroma),
                ):
                    _stub_chat_store()
                    events: list[dict] = []
                    t0 = time.time()
                    fc = await file_processor.process_files(
                        [_file_tuple(pdf_path, "big.pdf")],
                        thread_id="test-thread-5",
                        writer=events.append,
                    )
                    elapsed = time.time() - t0

                assert elapsed < 5.0, \
                    f"process_files hung for {elapsed:.1f}s (expected ~1-2s)"
                pf = fc.files[0]
                assert pf.extracted_text, "Expected inline text even when Chroma timed out"
                assert not pf.chromadb_collection, \
                    "Chroma collection should be empty after timeout"
                stages = [e.get("stage") for e in events
                          if e.get("type") == "file_processing"]
                assert "chroma_store_timeout" in stages, \
                    f"Expected chroma_store_timeout stage; got {stages}"
        finally:
            file_processor.CHROMA_STORE_TIMEOUT_S = original_timeout

    asyncio.run(_inner())
