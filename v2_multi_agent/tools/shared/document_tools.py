"""Shared Tools: Document and PDF processing operations.

Reusable @tool functions for PDF validation, text extraction (direct + OCR),
compression, and ChromaDB collection management.

Used by the Document Agent (#10) for PDF upload/processing/chat workflows.

Uses: PyMuPDF (fitz) for PDF handling, Gemini Flash Lite for Vision OCR,
      PikePDF/Ghostscript for compression, ChromaDB for storage
"""

from __future__ import annotations

import io
import os
import base64
import shutil
import subprocess
import tempfile

from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate

from core.clients import get_gemini_flash, get_qa_embeddings
from core.logger import get_logger
from core.settings import CHROMA_STORE_ROOT

log = get_logger("DocumentTools")

import chromadb


# --- Constants ---

MAX_PDF_SIZE_MB = 50
MAX_PDF_PAGES = 500
GEMINI_VISION_BATCH_SIZE = 5

# Allowed base directories for PDF file operations.
# Files must resolve to one of these paths to prevent path traversal.
_ALLOWED_PDF_DIRS: tuple[str, ...] = (
    tempfile.gettempdir(),
    CHROMA_STORE_ROOT,
    os.path.abspath(os.getcwd()),
)


def _is_safe_pdf_path(file_path: str) -> bool:
    """Return True if file_path resolves to within an allowed directory.

    Prevents path traversal attacks where a crafted filename like
    '../../etc/passwd' could escape the expected upload directory.
    Also enforces a .pdf extension requirement.
    """
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
def validate_pdf(file_path: str, max_size_mb: int = 50, max_pages: int = 500) -> dict:
    """Validate a PDF file for processing eligibility.

    Checks file existence, size limits, page count limits, and
    whether the PDF is encrypted or corrupted.

    Args:
        file_path: Absolute path to the PDF file
        max_size_mb: Maximum allowed file size in MB (default 50)
        max_pages: Maximum allowed page count (default 500)

    Returns:
        Dict with keys: valid (bool), reason (str or None), pages (int), size_mb (float)
    """
    import fitz

    if not _is_safe_pdf_path(file_path):
        return {"valid": False, "reason": f"Invalid or unsafe file path: '{os.path.basename(file_path)}'", "pages": 0, "size_mb": 0}

    if not os.path.exists(file_path):
        return {"valid": False, "reason": "File not found", "pages": 0, "size_mb": 0}

    size_bytes = os.path.getsize(file_path)
    size_mb = round(size_bytes / (1024 * 1024), 2)

    if size_mb > max_size_mb:
        return {
            "valid": False,
            "reason": f"File size ({size_mb} MB) exceeds limit ({max_size_mb} MB)",
            "pages": 0,
            "size_mb": size_mb,
        }

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        return {"valid": False, "reason": f"Cannot open PDF: {e}", "pages": 0, "size_mb": size_mb}

    if doc.is_encrypted:
        doc.close()
        return {"valid": False, "reason": "PDF is encrypted/password-protected", "pages": 0, "size_mb": size_mb}

    pages = doc.page_count
    doc.close()

    if pages > max_pages:
        return {
            "valid": False,
            "reason": f"Page count ({pages}) exceeds limit ({max_pages})",
            "pages": pages,
            "size_mb": size_mb,
        }

    if pages == 0:
        return {"valid": False, "reason": "PDF has no pages", "pages": 0, "size_mb": size_mb}

    return {"valid": True, "reason": None, "pages": pages, "size_mb": size_mb}


@tool
def extract_text_pymupdf(file_path: str) -> dict:
    """Extract text from a PDF using PyMuPDF (fitz) — direct text extraction.

    Fast extraction that works for text-based (non-scanned) PDFs.
    Returns page-by-page text content.

    Args:
        file_path: Absolute path to the PDF file

    Returns:
        Dict with keys: text (str), pages_extracted (int), has_text (bool)
    """
    import fitz

    if not _is_safe_pdf_path(file_path):
        return {"text": "", "pages_extracted": 0, "has_text": False, "error": f"Invalid or unsafe file path: '{os.path.basename(file_path)}'"}

    try:
        doc = fitz.open(file_path)
        all_text = []
        pages_with_text = 0

        for page_num, page in enumerate(doc):
            text = page.get_text("text")
            if text and text.strip():
                all_text.append(f"--- Page {page_num + 1} ---\n{text.strip()}")
                pages_with_text += 1

        doc.close()

        full_text = "\n\n".join(all_text)
        return {
            "text": full_text,
            "pages_extracted": pages_with_text,
            "has_text": pages_with_text > 0,
        }

    except Exception as e:
        return {"text": "", "pages_extracted": 0, "has_text": False, "error": str(e)}


@tool
def extract_text_vision(file_path: str, start_page: int = 0, end_page: int = -1) -> dict:
    """Extract text from PDF pages using Gemini Flash Lite Vision OCR.

    Converts PDF pages to images and uses vision model to extract text.
    Handles scanned PDFs, handwritten content, and blurred text.
    Processes in batches of 5 pages.

    Args:
        file_path: Absolute path to the PDF file
        start_page: First page to process (0-indexed, default 0)
        end_page: Last page to process (-1 for all pages)

    Returns:
        Dict with keys: text (str), pages_processed (int)
    """
    import fitz

    if not _is_safe_pdf_path(file_path):
        return {"text": "", "pages_processed": 0, "error": f"Invalid or unsafe file path: '{os.path.basename(file_path)}'"}

    try:
        doc = fitz.open(file_path)
        total_pages = doc.page_count

        if end_page < 0:
            end_page = total_pages - 1
        end_page = min(end_page, total_pages - 1)

        results = []
        batch_images = []

        llm = get_gemini_flash(temperature=0.0)

        def process_batch(batch_b64: list[str], batch_start: int) -> str:
            content = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Extract all visible text and markdown tables from these document images "
                                f"(Pages {batch_start + 1} to {batch_start + len(batch_b64)}). "
                                "If anything is missing, blurred or illegible, replace it with most similar text or possible context."
                            ),
                        },
                        *[
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64_img}"},
                            }
                            for b64_img in batch_b64
                        ],
                    ],
                }
            ]
            resp = llm.invoke(content)
            return f"--- Pages {batch_start + 1}-{batch_start + len(batch_b64)} ---\n{resp.content.strip()}"

        for i in range(start_page, end_page + 1):
            page = doc[i]
            pix = page.get_pixmap(dpi=120)
            buf = io.BytesIO()
            buf.write(pix.tobytes("png"))
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            batch_images.append(b64)

            if len(batch_images) == GEMINI_VISION_BATCH_SIZE:
                results.append(process_batch(batch_images, i - len(batch_images) + 1))
                batch_images = []

        if batch_images:
            results.append(process_batch(batch_images, end_page - len(batch_images) + 1))

        doc.close()

        return {
            "text": "\n\n".join(results),
            "pages_processed": end_page - start_page + 1,
        }

    except Exception as e:
        log.error(f"[Document] Vision extraction failed: {e}")
        return {"text": "", "pages_processed": 0, "error": str(e)}


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
    persist_dir = os.path.join(CHROMA_STORE_ROOT, unique_string)

    if not os.path.exists(persist_dir):
        return {"deleted": False, "reason": f"Collection '{unique_string}' not found"}

    try:
        # Delete the ChromaDB persist directory
        shutil.rmtree(persist_dir)

        # Delete associated chat history
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
    persist_dir = os.path.join(CHROMA_STORE_ROOT, unique_string)

    if not os.path.exists(persist_dir):
        return {
            "exists": False,
            "document_count": 0,
            "disk_size_mb": 0.0,
            "has_chat_history": False,
        }

    # Calculate disk size
    total_size = 0
    for dirpath, _, filenames in os.walk(persist_dir):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total_size += os.path.getsize(fp)
    size_mb = round(total_size / (1024 * 1024), 2)

    # Get document count from ChromaDB
    doc_count = 0
    try:
        embeddings = get_qa_embeddings()
        chromadb.api.client.SharedSystemClient.clear_system_cache()
        from langchain_community.vectorstores import Chroma
        vectordb = Chroma(
            collection_name=unique_string,
            persist_directory=persist_dir,
            embedding_function=embeddings,
        )
        collection = vectordb._collection
        doc_count = collection.count()
    except Exception as e:
        log.error(f"[Document] Failed to get doc count for {unique_string}: {e}")

    # Check chat history
    chat_file = os.path.join(CHROMA_STORE_ROOT, "chat_histories", f"{unique_string}_chat.json")
    has_history = os.path.exists(chat_file)

    return {
        "exists": True,
        "document_count": doc_count,
        "disk_size_mb": size_mb,
        "has_chat_history": has_history,
    }
