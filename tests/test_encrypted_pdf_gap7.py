"""Gap #7 — encrypted-PDF early-rejection test.

Builds a password-protected PDF on the fly using PyMuPDF, feeds it
through `core.file_processor.process_files`, and asserts that the
returned ProcessedFile carries the friendly rejection message rather
than a raw "PDF processing failed: ..." string. Also verifies a
plain (unencrypted) PDF still processes cleanly to prove the check
didn't over-trigger.

Run: `python -m tests.test_encrypted_pdf_gap7`
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from dotenv import load_dotenv

load_dotenv()


def _passed(name: str) -> tuple[bool, str]:
    return True, f"[OK]  {name}"


def _failed(name: str, detail: str = "") -> tuple[bool, str]:
    return False, f"[FAIL] {name}  {detail}"


def _make_encrypted_pdf(path: Path, password: str = "test123") -> None:
    """Build a tiny PDF with owner + user passwords using PyMuPDF."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "This is a locked test PDF.", fontsize=14)
    # AES-256 encryption; both owner and user password set
    doc.save(
        path.as_posix(),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw=password,
        user_pw=password,
    )
    doc.close()


def _make_plain_pdf(path: Path) -> None:
    """Build a tiny unencrypted PDF."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Plain PDF — no password.", fontsize=14)
    doc.save(path.as_posix())
    doc.close()


async def run() -> int:
    from core.file_processor import process_files

    results: list[tuple[bool, str]] = []
    workspace = Path(tempfile.mkdtemp(prefix="gap7_"))
    print(f"workspace: {workspace}\n")

    try:
        # Build fixtures
        enc_pdf = workspace / "locked.pdf"
        plain_pdf = workspace / "plain.pdf"
        _make_encrypted_pdf(enc_pdf, password="secret42")
        _make_plain_pdf(plain_pdf)
        print(f"built: {enc_pdf.name} ({enc_pdf.stat().st_size}B), "
              f"{plain_pdf.name} ({plain_pdf.stat().st_size}B)")
        print()

        # Stub chat_store to skip the real SQLite persistence (this is a
        # UNIT test — we don't need the real DB write to complete). We
        # want only the pre-check logic + ProcessedFile.error assertion.
        with patch(
            "core.chat_store.chat_store.get_thread_storage",
            AsyncMock(return_value=(0, 0)),
        ), patch(
            "core.chat_store.chat_store.save_thread_file",
            AsyncMock(return_value=None),
        ):

            # --- Encrypted PDF ---
            print("--- T1: encrypted PDF ---")
            file_tuples = [(str(enc_pdf), enc_pdf.name, enc_pdf.stat().st_size)]
            ctx = await process_files(file_tuples, thread_id="test_thread_enc")
            print(f"  ctx.files: {len(ctx.files)}")
            for pf in ctx.files:
                print(f"    pf: name={pf.original_name!r} error={pf.error!r}")
            enc_pf = ctx.files[0] if ctx.files else None
            has_friendly_msg = bool(
                enc_pf and enc_pf.error
                and "password-protected" in enc_pf.error.lower()
                and "unlock" in enc_pf.error.lower()
                and "re-upload" in enc_pf.error.lower()
            )
            no_extract_leak = bool(
                enc_pf and enc_pf.error
                and "PDF processing failed" not in enc_pf.error
            )
            results.append(
                _passed("T1 encrypted PDF surfaces friendly message")
                if has_friendly_msg
                else _failed("T1 encrypted PDF",
                             detail=f"error={enc_pf.error if enc_pf else 'None'}")
            )
            results.append(
                _passed("T1 no downstream 'PDF processing failed' leakage")
                if no_extract_leak
                else _failed("T1 no leak")
            )
            results.append(
                _passed("T1 no chroma collection created (skipped)")
                if enc_pf and not enc_pf.chromadb_collection
                else _failed("T1 no chroma")
            )
            print()

            # --- Plain PDF ---
            print("--- T2: plain PDF (regression) ---")
            file_tuples = [(str(plain_pdf), plain_pdf.name, plain_pdf.stat().st_size)]
            ctx2 = await process_files(file_tuples, thread_id="test_thread_plain")
            print(f"  ctx.files: {len(ctx2.files)}")
            for pf in ctx2.files:
                print(f"    pf: name={pf.original_name!r} error={pf.error!r} "
                      f"text_len={len(pf.extracted_text)} coll={pf.chromadb_collection!r}")
            plain_pf = ctx2.files[0] if ctx2.files else None
            not_flagged_encrypted = bool(
                plain_pf and not (plain_pf.error and "password" in (plain_pf.error or "").lower())
            )
            results.append(
                _passed("T2 plain PDF NOT flagged as encrypted (no false positive)")
                if not_flagged_encrypted
                else _failed("T2 plain PDF false positive",
                             detail=f"error={plain_pf.error if plain_pf else 'None'}")
            )
            # Note: plain PDF may still hit other errors (chroma unavailable
            # in test env, etc.) — we don't assert full success here, only
            # that our check didn't misfire on the plain file.
            print()

    finally:
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)

    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print("=" * 78)
    print(f"SUMMARY: {passed}/{total} passed")
    print("=" * 78)
    for _, line in results:
        print(f"  {line}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
