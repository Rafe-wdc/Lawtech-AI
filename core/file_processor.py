"""Unified file processing for inline chat attachments.

ChatGPT-style file persistence:
- All files saved permanently to ./uploads/{thread_id}/
- Gemini-supported types (PDF, images, TXT, CSV, MD) uploaded to Gemini Files API
  → persistent URI replaces base64 (48h TTL, auto re-upload from local if expired)
- DOCX / XLSX → text extracted inline (not supported by Gemini Files API)
- Large PDFs (>20 pages) → also stored in ChromaDB for targeted retrieval

Limits: 30 files/request, 30 files/thread, 1 GB/file, 1 GB/thread total.
"""

from __future__ import annotations

import asyncio
import csv
import io
import os
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from core.logger import get_logger
from core.settings import (
    CHROMA_STORE_ROOT,
    UPLOADS_ROOT,
    MAX_FILES_PER_REQUEST,
    MAX_FILES_PER_THREAD,
    MAX_FILE_SIZE_MB,
    MAX_THREAD_STORAGE_MB,
)
from core.gemini_files import (
    get_mime_type,
    is_gemini_supported,
    upload_to_gemini,
    GEMINI_SUPPORTED_MIMES,
)

_chroma_cache_lock = threading.Lock()
log = get_logger("FileProcessor")

# --- Constants ---

MAX_INLINE_TEXT_CHARS = 100_000
MAX_INLINE_PDF_PAGES = 20
VISION_BATCH_SIZE = 5

ALLOWED_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".webp",
    ".docx", ".txt", ".md", ".csv", ".xlsx",
}


# --- Data Structures ---

@dataclass
class ProcessedFile:
    """Result of processing a single uploaded file."""
    original_name: str
    file_type: str      # "pdf", "image", "docx", "txt", "csv", "xlsx"
    mime_type: str
    size_bytes: int
    file_id: str = ""           # stable UUID per file in this thread
    local_path: str = ""        # ./uploads/{thread_id}/{file_id}_{filename}
    # Content routing (one or more may be set)
    gemini_uri: str = ""        # Gemini Files API URI
    gemini_name: str = ""       # Gemini handle e.g. "files/abc123"
    gemini_expiry: str = ""     # ISO 8601 expiry
    extracted_text: str = ""    # DOCX / XLSX / fallback text
    chromadb_collection: str = ""   # large PDFs stored in ChromaDB
    page_count: int = 0
    gemini_supported: bool = False
    error: str | None = None


@dataclass
class FileContext:
    """Aggregated result of processing all uploaded files for one turn."""
    files: list[ProcessedFile] = field(default_factory=list)
    inline_text: str = ""
    gemini_file_parts: list[dict] = field(default_factory=list)
    # [{file_data: {file_uri, mime_type}, name}] — passed to Gemini content
    chromadb_collections: list[str] = field(default_factory=list)
    summary: str = ""
    file_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize for LangGraph state (JSON-serializable)."""
        return {
            "inline_text": self.inline_text,
            "gemini_file_parts": self.gemini_file_parts,
            "chromadb_collections": self.chromadb_collections,
            "summary": self.summary,
            "file_names": self.file_names,
        }


# --- Validation ---

def validate_upload(filename: str, size_bytes: int) -> str | None:
    """Return error message if file is invalid, else None."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
    if ext == ".doc":
        return "Old .doc format is not supported. Please convert to .docx"
    size_mb = size_bytes / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        return f"File too large ({size_mb:.1f} MB). Max: {MAX_FILE_SIZE_MB} MB"
    return None


# --- PDF helpers ---

def _extract_pdf_text(file_path: str) -> tuple[str, int]:
    """Extract text from PDF with PyMuPDF. Returns (text, page_count)."""
    import fitz

    doc = fitz.open(file_path)
    page_count = doc.page_count

    if doc.is_encrypted:
        doc.close()
        raise ValueError("PDF is encrypted/password-protected")

    if page_count == 0:
        doc.close()
        raise ValueError("PDF has no pages")

    all_text = []
    for page_num, page in enumerate(doc):
        text = page.get_text("text")
        if text and text.strip():
            all_text.append(f"--- Page {page_num + 1} ---\n{text.strip()}")

    doc.close()
    return "\n\n".join(all_text), page_count


def _vision_ocr_pdf(file_path: str, page_count: int) -> str:
    """Run Gemini Vision OCR on PDF pages. Returns extracted text."""
    import base64
    import fitz
    from core.clients import get_gemini_flash

    try:
        doc = fitz.open(file_path)
        llm = get_gemini_flash(temperature=0.0)
        results = []
        batch_images: list[str] = []

        for i in range(page_count):
            page = doc[i]
            pix = page.get_pixmap(dpi=120)
            buf = io.BytesIO()
            buf.write(pix.tobytes("png"))
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            batch_images.append(b64)

            if len(batch_images) == VISION_BATCH_SIZE:
                results.append(_ocr_batch(llm, batch_images, i - len(batch_images) + 1))
                batch_images = []

        if batch_images:
            results.append(_ocr_batch(llm, batch_images, page_count - len(batch_images)))

        doc.close()
        return "\n\n".join(results)

    except Exception as e:
        log.error("Vision OCR failed", error=str(e))
        return ""


def _ocr_batch(llm, batch_b64: list[str], batch_start: int) -> str:
    content = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Extract all visible text from these document images "
                        f"(Pages {batch_start + 1} to {batch_start + len(batch_b64)}). "
                        "Preserve formatting. If text is blurred or illegible, "
                        "replace with most likely text based on context."
                    ),
                },
                *[
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    }
                    for b64 in batch_b64
                ],
            ],
        }
    ]
    resp = llm.invoke(content)
    return f"--- Pages {batch_start + 1}-{batch_start + len(batch_b64)} ---\n{resp.content.strip()}"


def _store_in_chromadb(text: str, collection_id: str, filename: str) -> None:
    """Chunk text and store in ChromaDB collection."""
    import chromadb
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.vectorstores import Chroma
    from core.clients import get_qa_embeddings

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        separators=["\n\n", "\n", ". ", " "],
    )
    chunks = splitter.split_text(text)

    if not chunks:
        raise ValueError("No text chunks produced")

    persist_dir = os.path.join(CHROMA_STORE_ROOT, collection_id)
    os.makedirs(persist_dir, exist_ok=True)

    embeddings = get_qa_embeddings()
    with _chroma_cache_lock:
        chromadb.api.client.SharedSystemClient.clear_system_cache()

    metadatas = [{"source": filename, "chunk": i} for i in range(len(chunks))]
    Chroma.from_texts(
        texts=chunks,
        embedding=embeddings,
        collection_name=collection_id,
        persist_directory=persist_dir,
        metadatas=metadatas,
    )
    log.info("Stored in ChromaDB", collection=collection_id, chunks=len(chunks))


# --- Text-only extractors (for DOCX / XLSX that Gemini Files API can't handle) ---

def _extract_docx_text(file_path: str) -> str:
    from docx import Document
    doc = Document(file_path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    table_texts = []
    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            rows.append(" | ".join(cells))
        if rows:
            header = rows[0]
            sep = " | ".join(["---"] * len(table.rows[0].cells))
            table_texts.append("\n".join([header, sep] + rows[1:]))
    parts = paragraphs
    if table_texts:
        parts.append("\n\n--- Tables ---\n")
        parts.extend(table_texts)
    return "\n\n".join(parts)


def _extract_csv_text(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        rows = []
        for i, row in enumerate(reader):
            if i >= 100:
                rows.append(["... (truncated)"])
                break
            rows.append(row)
    if not rows:
        return ""
    header = " | ".join(rows[0])
    sep = " | ".join(["---"] * len(rows[0]))
    body = "\n".join(" | ".join(r) for r in rows[1:])
    return f"{header}\n{sep}\n{body}"


def _extract_xlsx_text(file_path: str) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(file_path, read_only=True, data_only=True)
    sheets_text = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= 100:
                rows.append(["... (truncated)"])
                break
            rows.append([str(c) if c is not None else "" for c in row])
        if not rows:
            continue
        header = " | ".join(rows[0])
        sep = " | ".join(["---"] * len(rows[0]))
        body = "\n".join(" | ".join(r) for r in rows[1:])
        sheets_text.append(f"### Sheet: {sheet_name}\n\n{header}\n{sep}\n{body}")
    wb.close()
    return "\n\n".join(sheets_text)


# --- Main Processing Function ---

async def process_files(
    files: list[tuple[str, str, int]],
    thread_id: str,
    status_callback=None,
) -> FileContext:
    """Process uploaded files with local storage + Gemini Files API persistence.

    Args:
        files: List of (temp_file_path, original_filename, size_bytes) tuples.
        thread_id: Used for upload dir, ChromaDB collection naming, and DB record.
        status_callback: Optional async callable(message: str) for SSE status.

    Returns:
        FileContext with gemini_file_parts, inline_text, and chromadb_collections.
    """
    from core.chat_store import chat_store

    ctx = FileContext()

    if len(files) > MAX_FILES_PER_REQUEST:
        log.warning("Too many files in request", count=len(files), max=MAX_FILES_PER_REQUEST)
        files = files[:MAX_FILES_PER_REQUEST]

    # Check current thread storage
    existing_count, existing_bytes = await chat_store.get_thread_storage(thread_id)
    max_thread_bytes = MAX_THREAD_STORAGE_MB * 1024 * 1024

    # Ensure uploads directory for this thread
    thread_upload_dir = os.path.join(UPLOADS_ROOT, thread_id)
    os.makedirs(thread_upload_dir, exist_ok=True)

    inline_parts: list[str] = []

    for file_path, filename, size_bytes in files:
        ext = Path(filename).suffix.lower()
        ctx.file_names.append(filename)

        # --- Thread-level limit checks ---
        if existing_count >= MAX_FILES_PER_THREAD:
            pf = ProcessedFile(
                original_name=filename, file_type=ext.lstrip("."),
                mime_type="", size_bytes=size_bytes,
                error=f"Thread file limit ({MAX_FILES_PER_THREAD} files) reached",
            )
            ctx.files.append(pf)
            log.warning("Thread file limit reached", thread=thread_id[:12], file=filename)
            continue

        if existing_bytes + size_bytes > max_thread_bytes:
            pf = ProcessedFile(
                original_name=filename, file_type=ext.lstrip("."),
                mime_type="", size_bytes=size_bytes,
                error=f"Thread storage limit ({MAX_THREAD_STORAGE_MB} MB) reached",
            )
            ctx.files.append(pf)
            log.warning("Thread storage limit reached", thread=thread_id[:12], file=filename)
            continue

        # --- Basic validation ---
        err = validate_upload(filename, size_bytes)
        if err:
            pf = ProcessedFile(
                original_name=filename, file_type=ext.lstrip("."),
                mime_type="", size_bytes=size_bytes, error=err,
            )
            ctx.files.append(pf)
            log.warning("File rejected", file=filename, reason=err)
            continue

        if status_callback:
            await status_callback(f"Processing {filename}...")

        mime = get_mime_type(ext)
        gemini_ok = is_gemini_supported(ext)
        file_id = uuid.uuid4().hex

        # --- Step 1: Copy to permanent local storage ---
        safe_name = Path(filename).name.replace(" ", "_")
        local_filename = f"{file_id}_{safe_name}"
        local_path = os.path.join(thread_upload_dir, local_filename)
        try:
            shutil.copy2(file_path, local_path)
        except Exception as e:
            pf = ProcessedFile(
                original_name=filename, file_type=ext.lstrip("."),
                mime_type=mime, size_bytes=size_bytes,
                error=f"Failed to save file: {e}",
            )
            ctx.files.append(pf)
            continue

        pf = ProcessedFile(
            original_name=filename,
            file_type=ext.lstrip("."),
            mime_type=mime,
            size_bytes=size_bytes,
            file_id=file_id,
            local_path=local_path,
            gemini_supported=gemini_ok,
        )

        # --- Step 2: Upload to Gemini Files API (images, PDF, TXT, CSV, MD) ---
        if gemini_ok:
            try:
                uri, gname, expiry = await asyncio.to_thread(
                    upload_to_gemini, local_path, mime, filename
                )
                pf.gemini_uri = uri
                pf.gemini_name = gname
                pf.gemini_expiry = expiry
                ctx.gemini_file_parts.append({
                    "file_data": {"file_uri": uri, "mime_type": mime},
                    "name": filename,
                })
                log.info("Gemini Files upload OK", file=filename, mime=mime)
            except Exception as e:
                log.error("Gemini Files upload failed, falling back to extraction",
                          file=filename, error=str(e))
                pf.error = f"Gemini upload failed: {e}"
                # Fall through to text extraction below

        # --- Step 3: PDF-specific handling ---
        if ext == ".pdf":
            try:
                text, page_count = await asyncio.to_thread(_extract_pdf_text, local_path)
                pf.page_count = page_count

                if not text.strip():
                    # Scanned PDF — Vision OCR fallback
                    text = await asyncio.to_thread(_vision_ocr_pdf, local_path, page_count)

                if page_count > MAX_INLINE_PDF_PAGES or len(text) > MAX_INLINE_TEXT_CHARS:
                    # Store in ChromaDB for retrieval
                    collection_id = f"inline_{thread_id}_{file_id[:8]}"
                    try:
                        await asyncio.to_thread(_store_in_chromadb, text, collection_id, filename)
                        pf.chromadb_collection = collection_id
                        ctx.chromadb_collections.append(collection_id)
                        log.info("Large PDF stored in ChromaDB",
                                 file=filename, pages=page_count, collection=collection_id)
                    except Exception as e:
                        log.error("ChromaDB storage failed", error=str(e))
                        # Fall back to truncated inline
                        pf.extracted_text = text[:MAX_INLINE_TEXT_CHARS]
                        inline_parts.append(f"[File: {filename}]\n{pf.extracted_text}")
                else:
                    # Small PDF — use extracted text as fallback context
                    # (Gemini URI already registered above for direct PDF access)
                    if not pf.gemini_uri:
                        pf.extracted_text = text
                        inline_parts.append(f"[File: {filename}]\n{text}")

            except Exception as e:
                if not pf.gemini_uri:
                    pf.error = f"PDF processing failed: {e}"

        # --- Step 4: Text extraction for DOCX / XLSX (Gemini Files API not supported) ---
        elif ext == ".docx":
            try:
                text = await asyncio.to_thread(_extract_docx_text, local_path)
                pf.extracted_text = text
                inline_parts.append(f"[File: {filename}]\n{text}")
            except Exception as e:
                pf.error = f"Failed to read DOCX: {e}"

        elif ext == ".xlsx":
            try:
                text = await asyncio.to_thread(_extract_xlsx_text, local_path)
                pf.extracted_text = text
                inline_parts.append(f"[File: {filename}]\n{text}")
            except Exception as e:
                pf.error = f"Failed to read XLSX: {e}"

        # CSV / TXT / MD: also extract text as inline fallback
        # (Gemini URI handles primary access; inline text is fallback context)
        elif ext in (".csv",) and pf.gemini_uri:
            try:
                text = await asyncio.to_thread(_extract_csv_text, local_path)
                pf.extracted_text = text
            except Exception:
                pass

        elif ext in (".txt", ".md") and pf.gemini_uri:
            try:
                with open(local_path, "r", encoding="utf-8", errors="replace") as f:
                    pf.extracted_text = f.read(MAX_INLINE_TEXT_CHARS)
            except Exception:
                pass

        # If Gemini upload failed and no text was extracted, mark as error
        if not pf.gemini_uri and not pf.extracted_text and not pf.chromadb_collection and not pf.error:
            pf.error = "No content could be extracted from this file"

        ctx.files.append(pf)

        # --- Step 5: Persist to SQLite thread_files ---
        try:
            await chat_store.save_thread_file(thread_id, pf)
        except Exception as e:
            log.error("Failed to save thread_file record", file=filename, error=str(e))

        existing_count += 1
        existing_bytes += size_bytes

        log.info("File processed",
                 file=filename, type=pf.file_type,
                 gemini=bool(pf.gemini_uri), chromadb=bool(pf.chromadb_collection),
                 text_len=len(pf.extracted_text))

    # Build combined inline text (truncated to limit)
    combined = "\n\n---\n\n".join(inline_parts)
    if len(combined) > MAX_INLINE_TEXT_CHARS:
        combined = combined[:MAX_INLINE_TEXT_CHARS] + "\n\n[... text truncated]"
    ctx.inline_text = combined

    # Build summary
    type_counts: dict[str, int] = {}
    for pf in ctx.files:
        if not pf.error:
            type_counts[pf.file_type] = type_counts.get(pf.file_type, 0) + 1
    parts = [f"{c} {t}{'s' if c > 1 else ''}" for t, c in type_counts.items()]
    ctx.summary = f"Processed {', '.join(parts)}" if parts else "No files processed"

    log.info("File processing complete",
             total=len(files), summary=ctx.summary,
             gemini_parts=len(ctx.gemini_file_parts),
             inline_chars=len(ctx.inline_text),
             chromadb=len(ctx.chromadb_collections))

    return ctx