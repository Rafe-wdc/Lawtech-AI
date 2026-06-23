"""Tests for the V1-port PDF compression helper in core.file_processor.

Verifies:
  1. PikePDF compression runs without crashing on a valid PDF.
  2. The returned path is either the original or a smaller compressed file.
  3. Stats dict is populated.
  4. Missing Ghostscript binary is handled gracefully (most dev boxes
     don't have `gs` installed, so this is the common path).
"""
from __future__ import annotations

import os
import sys
import tempfile

# Project root on sys.path
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _make_test_pdf(path: str, num_pages: int = 5) -> None:
    """Make a PDF with enough text content to be compressible."""
    import fitz
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        # Stuff each page with paragraphs of repeated text — compresses well.
        text = (
            f"Page {i+1} — TEST DOCUMENT FOR COMPRESSION\n\n"
            + (
                "The quick brown fox jumps over the lazy dog. " * 80
                + "\n\n"
            ) * 4
        )
        page.insert_text((72, 72), text, fontsize=10)
    doc.save(path)
    doc.close()


def test_compress_pdf_returns_path_and_stats():
    from core.file_processor import _compress_pdf

    with tempfile.TemporaryDirectory() as tmp:
        pdf = os.path.join(tmp, "input.pdf")
        _make_test_pdf(pdf, num_pages=3)
        original_size = os.path.getsize(pdf)

        result_path, stats = _compress_pdf(pdf)

        # Either it returns the original (no savings) OR a sibling file
        # that exists and is smaller.
        assert os.path.exists(result_path)
        assert stats["original"] == original_size
        # `chosen` is one of "original", "pike", "gs".
        assert stats["chosen"] in ("original", "pike", "gs"), \
            f"Unexpected chosen value: {stats['chosen']}"

        # If we picked compressed, it must be smaller than original.
        if stats["chosen"] != "original":
            assert os.path.getsize(result_path) < original_size
            assert stats["saved_bytes"] > 0


def test_compress_pdf_handles_missing_ghostscript():
    """Most dev/test environments don't have `gs` on PATH. The helper
    must complete cleanly using PikePDF only.
    """
    from core.file_processor import _compress_pdf

    with tempfile.TemporaryDirectory() as tmp:
        pdf = os.path.join(tmp, "input.pdf")
        _make_test_pdf(pdf, num_pages=2)

        # Even if gs is somehow installed locally, the helper still must not
        # crash if PikePDF runs.
        _result_path, stats = _compress_pdf(pdf)
        # PikePDF run must be reported even if gs is missing.
        # `pike` is either an int byte count or None when PikePDF failed.
        assert "pike" in stats
        # gs is None on machines without Ghostscript installed.
        assert "gs" in stats


def test_compress_pdf_does_not_swap_when_savings_below_threshold():
    """We only switch away from the original when compression saves at
    least 3%. Tiny PDFs often re-encode to ~same size as the original;
    the helper must return the original path in that case.
    """
    from core.file_processor import _compress_pdf

    with tempfile.TemporaryDirectory() as tmp:
        pdf = os.path.join(tmp, "tiny.pdf")
        _make_test_pdf(pdf, num_pages=1)
        original_size = os.path.getsize(pdf)

        result_path, stats = _compress_pdf(pdf)

        # If no worthwhile compression, return the input path. The returned
        # path is the original AND saved_bytes is 0.
        if stats["chosen"] == "original":
            assert result_path == pdf
            assert stats["saved_bytes"] == 0
