"""Shared Tools: Document and PDF processing operations.

Reusable @tool functions for PDF compression and ChromaDB collection
management.

Used by the Document Agent (#10) for PDF upload/processing/chat workflows.

Uses: PyMuPDF (fitz) for PDF handling,
      PikePDF/Ghostscript for compression, ChromaDB for storage

Note (2026-07-22): the parallel OCR path here (`extract_text_pymupdf`,
`extract_text_vision`, `validate_pdf`) was deleted. The single source of
truth for text extraction and OCR is `core.file_processor`
(`_extract_pdf_text_per_page`, `_vision_ocr_pdf`, `_vision_ocr_image`).
Keeping two implementations caused prompt/model drift and was the reason
the production pipeline silently used Flash Lite while this file used
Flash — see the audit findings and the WhatsApp Devanagari-scan bug.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from langchain.tools import tool

from core.clients import get_chroma_client
from core.logger import get_logger
from core.settings import CHROMA_STORE_ROOT

log = get_logger("DocumentTools")


# --- Constants ---

MAX_PDF_SIZE_MB = 50
MAX_PDF_PAGES = 500

# Allowed base directories for PDF file operations. Files must resolve to
# one of these paths to prevent path traversal attacks where a crafted
# filename like '../../etc/passwd' could escape the expected upload dir.
_ALLOWED_PDF_DIRS: tuple[str, ...] = (
    tempfile.gettempdir(),
    CHROMA_STORE_ROOT,
    os.path.abspath(os.getcwd()),
)


def _is_safe_pdf_path(file_path: str) -> bool:
    """Return True if file_path resolves to within an allowed directory
    and has a .pdf extension."""
    if not file_path.lower().endswith(".pdf"):
        return False
    resolved = os.path.realpath(file_path)
    return any(
        resolved.startswith(os.path.realpath(allowed) + os.sep)
        or resolved == os.path.realpath(allowed)
        for allowed in _ALLOWED_PDF_DIRS
    )


# --- Tool Functions ---

@tool
def compress_pdf(file_path: str) -> dict:
    """Compress a PDF file using PikePDF (lossless) with Ghostscript fallback.

    Attempts lossless compression first. If insufficient reduction,
    falls back to Ghostscript lossy compression at /ebook quality.

    Args:
        file_path: Absolute path to the PDF file to compress

    Returns:
        Dict with keys: compressed_path (str), original_size (int), compressed_size (int), reduction_pct (float)
    """
    if not _is_safe_pdf_path(file_path):
        return {
            "compressed_path": file_path,
            "original_size": 0,
            "compressed_size": 0,
            "reduction_pct": 0.0,
            "error": f"Invalid or unsafe file path: '{os.path.basename(file_path)}'",
        }

    original_size = os.path.getsize(file_path)

    # Step 1: Try PikePDF (lossless)
    pike_out = file_path.replace(".pdf", "_pike.pdf")
    pike_size = float("inf")

    try:
        import pikepdf
        with pikepdf.open(file_path) as pdf:
            pdf.save(pike_out, compress_streams=True)
        pike_size = os.path.getsize(pike_out)
    except Exception as e:
        log.error(f"[Document] PikePDF failed: {e}")
        pike_out = None

    if pike_out and pike_size < original_size * 0.97:
        reduction = round((1 - pike_size / original_size) * 100, 1)
        return {
            "compressed_path": pike_out,
            "original_size": original_size,
            "compressed_size": pike_size,
            "reduction_pct": reduction,
        }

    # Step 2: Ghostscript fallback
    gs_out = file_path.replace(".pdf", "_gs.pdf")
    gs_size = float("inf")

    try:
        gs_cmd = [
            "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
            "-dPDFSETTINGS=/ebook", "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-sOutputFile={gs_out}", file_path,
        ]
        subprocess.run(gs_cmd, check=True, timeout=120)
        gs_size = os.path.getsize(gs_out)
    except Exception as e:
        log.error(f"[Document] Ghostscript failed: {e}")
        gs_out = None

    # Choose best result
    if gs_out and gs_size < pike_size:
        if pike_out and os.path.exists(pike_out):
            os.remove(pike_out)
        reduction = round((1 - gs_size / original_size) * 100, 1)
        return {
            "compressed_path": gs_out,
            "original_size": original_size,
            "compressed_size": gs_size,
            "reduction_pct": reduction,
        }
    elif pike_out and os.path.exists(pike_out):
        if gs_out and os.path.exists(gs_out):
            os.remove(gs_out)
        reduction = round((1 - pike_size / original_size) * 100, 1)
        return {
            "compressed_path": pike_out,
            "original_size": original_size,
            "compressed_size": pike_size,
            "reduction_pct": reduction,
        }

    # No compression achieved
    for f in [pike_out, gs_out]:
        if f and os.path.exists(f):
            os.remove(f)

    return {
        "compressed_path": file_path,
        "original_size": original_size,
        "compressed_size": original_size,
        "reduction_pct": 0.0,
    }


@tool
def delete_pdf_vectorstore(unique_string: str) -> dict:
    """Delete a user's PDF document collection from ChromaDB.

    Removes the ChromaDB collection and its persist directory,
    as well as the associated chat history file.

    Args:
        unique_string: The unique identifier of the collection to delete

    Returns:
        Dict with keys: deleted (bool), reason (str or None)
    """
    # Try server-mode delete first. Collection may live entirely in the
    # Chroma server (no local persist_dir) under multi-worker config.
    client = get_chroma_client()
    deleted_from_server = False
    try:
        client.delete_collection(name=unique_string)
        deleted_from_server = True
    except Exception as e:
        # Most common: collection doesn't exist. Fall through to filesystem
        # check below so legacy on-disk collections still get cleaned up.
        log.debug(f"[Document] delete_collection({unique_string}) → {e}")

    # Legacy filesystem cleanup (handles pre-server-mode persist dirs and
    # the chat-history JSON which is not in Chroma).
    persist_dir = os.path.join(CHROMA_STORE_ROOT, unique_string)
    dir_existed = os.path.exists(persist_dir)
    if dir_existed:
        shutil.rmtree(persist_dir)

    if not deleted_from_server and not dir_existed:
        return {"deleted": False, "reason": f"Collection '{unique_string}' not found"}

    try:
        # Delete associated chat history (independent of Chroma storage)
        chat_file = os.path.join(CHROMA_STORE_ROOT, "chat_histories", f"{unique_string}_chat.json")
        if os.path.exists(chat_file):
            os.remove(chat_file)

        log.info(f"[Document] Deleted collection: {unique_string}")
        return {"deleted": True, "reason": None}

    except Exception as e:
        log.error(f"[Document] Failed to delete collection {unique_string}: {e}")
        return {"deleted": False, "reason": str(e)}


@tool
def get_collection_metadata(unique_string: str) -> dict:
    """Get metadata about a user's PDF document collection.

    Returns collection size, document count, and creation info
    without loading the full embeddings.

    Args:
        unique_string: The unique identifier of the collection

    Returns:
        Dict with keys: exists (bool), document_count (int), disk_size_mb (float), has_chat_history (bool)
    """
    # Check existence via Chroma server (collection may not have a local
    # persist dir under server mode). Fall through to legacy disk check only
    # for backward compat with pre-server-mode persist dirs.
    client = get_chroma_client()
    persist_dir = os.path.join(CHROMA_STORE_ROOT, unique_string)
    server_exists = False
    doc_count = 0
    try:
        collection = client.get_collection(name=unique_string)
        server_exists = True
        doc_count = collection.count()
    except Exception as e:
        log.debug(f"[Document] get_collection({unique_string}) → {e}")

    if not server_exists and not os.path.exists(persist_dir):
        return {
            "exists": False,
            "document_count": 0,
            "disk_size_mb": 0.0,
            "has_chat_history": False,
        }

    # Disk size: only meaningful for legacy local persist dirs. Under server
    # mode the data lives in the shared chroma_store/, not per-collection.
    size_mb = 0.0
    if os.path.exists(persist_dir):
        total_size = 0
        for dirpath, _, filenames in os.walk(persist_dir):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)
        size_mb = round(total_size / (1024 * 1024), 2)

    # Check chat history
    chat_file = os.path.join(CHROMA_STORE_ROOT, "chat_histories", f"{unique_string}_chat.json")
    has_history = os.path.exists(chat_file)

    return {
        "exists": True,
        "document_count": doc_count,
        "disk_size_mb": size_mb,
        "has_chat_history": has_history,
    }
