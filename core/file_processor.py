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
import hashlib
import io
import json
import os
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from core.logger import get_logger, log_time
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
VISION_BATCH_SIZE = 10          # pages per Gemini Vision call (was 5)
VISION_DPI = 200                # render DPI for scanned PDFs (was 120)
VISION_MAX_CONCURRENT = 4       # max parallel OCR batch calls
OCR_CACHE_DIR = os.path.join(CHROMA_STORE_ROOT, ".ocr_cache")

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


def _file_hash(file_path: str) -> str:
    """SHA256 hash of a file for OCR cache keying."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_ocr_cache(file_hash: str) -> str | None:
    """Return cached OCR text if available, else None."""
    cache_file = os.path.join(OCR_CACHE_DIR, f"{file_hash}.txt")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                text = f.read()
            if text.strip():
                log.info("OCR cache hit", hash=file_hash[:12])
                return text
        except Exception:
            pass
    return None


def _save_ocr_cache(file_hash: str, text: str) -> None:
    """Persist OCR text to disk cache."""
    try:
        os.makedirs(OCR_CACHE_DIR, exist_ok=True)
        cache_file = os.path.join(OCR_CACHE_DIR, f"{file_hash}.txt")
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(text)
        log.info("OCR result cached", hash=file_hash[:12], chars=len(text))
    except Exception as e:
        log.warning("Failed to cache OCR result", error=str(e))


def _render_pdf_pages(file_path: str, page_count: int) -> list[tuple[int, str]]:
    """Render all PDF pages to base64 JPEG images. Returns [(page_start, b64), ...]

    Uses JPEG (not PNG) for ~2x smaller payloads on scanned documents.
    Uses higher DPI (200) for better OCR accuracy on legal documents.
    Groups pages into batches of VISION_BATCH_SIZE.
    """
    import base64
    import fitz

    doc = fitz.open(file_path)
    batches: list[tuple[int, list[str]]] = []
    current_batch: list[str] = []
    batch_start = 0

    for i in range(page_count):
        page = doc[i]
        pix = page.get_pixmap(dpi=VISION_DPI)
        # JPEG at quality 85 — ~2x smaller than PNG for scanned docs
        img_bytes = pix.tobytes("jpeg", jpg_quality=85)
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        current_batch.append(b64)

        if len(current_batch) == VISION_BATCH_SIZE:
            batches.append((batch_start, current_batch))
            batch_start = i + 1
            current_batch = []

    if current_batch:
        batches.append((batch_start, current_batch))

    doc.close()
    return batches


def _ocr_batch(llm, batch_b64: list[str], batch_start: int) -> str:
    """Send a batch of page images to Gemini for OCR. Returns extracted text."""
    content = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Extract all visible text from these scanned legal document images "
                        f"(Pages {batch_start + 1} to {batch_start + len(batch_b64)}). "
                        "Preserve formatting, paragraph breaks, and structure. "
                        "Pay attention to: party names, case numbers, dates, section numbers, "
                        "court names, and legal provisions. If text is blurred or illegible, "
                        "replace with most likely text based on legal context."
                    ),
                },
                *[
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    }
                    for b64 in batch_b64
                ],
            ],
        }
    ]
    resp = llm.invoke(content)
    return f"--- Pages {batch_start + 1}-{batch_start + len(batch_b64)} ---\n{resp.content.strip()}"


def _vision_ocr_pdf(file_path: str, page_count: int) -> str:
    """Run Gemini Vision OCR on PDF pages with parallel batch processing.

    Improvements over original:
    1. Hash-based cache — skip OCR if same PDF was processed before
    2. JPEG @ quality 85 — ~2x smaller payloads vs PNG
    3. 200 DPI — better accuracy for faded/handwritten legal text (was 120)
    4. Batch size 10 — fewer API calls (was 5)
    5. Parallel batch calls — up to 4 concurrent Gemini calls (was sequential)
    6. Legal-aware OCR prompt — better extraction of names, dates, sections
    """
    from core.clients import get_gemini_flash

    # Check cache first
    pdf_hash = _file_hash(file_path)
    cached = _load_ocr_cache(pdf_hash)
    if cached:
        return cached

    try:
        with log_time(log, "PDF page rendering", pages=page_count, dpi=VISION_DPI):
            batches = _render_pdf_pages(file_path, page_count)

        llm = get_gemini_flash(temperature=0.0)
        log.info("Starting parallel OCR",
                 batches=len(batches), pages=page_count,
                 batch_size=VISION_BATCH_SIZE, max_concurrent=VISION_MAX_CONCURRENT)

        # Run batches in parallel using ThreadPoolExecutor
        with log_time(log, "Parallel Vision OCR", batches=len(batches)):
            with ThreadPoolExecutor(max_workers=VISION_MAX_CONCURRENT) as executor:
                futures = [
                    executor.submit(_ocr_batch, llm, batch_images, batch_start)
                    for batch_start, batch_images in batches
                ]
                results = []
                for future in futures:
                    try:
                        results.append(future.result(timeout=120))
                    except Exception as e:
                        log.warning("OCR batch failed", error=str(e))
                        results.append("")

        text = "\n\n".join(r for r in results if r)

        # Cache the result
        if text.strip():
            _save_ocr_cache(pdf_hash, text)

        return text

    except Exception as e:
        log.error("Vision OCR failed", error=str(e))
        return ""


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

    # --- Phase 1: Validate, copy, and prepare all files ---
    prepared: list[tuple[ProcessedFile, str, str, str, bool]] = []  # (pf, local_path, ext, mime, gemini_ok)

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

        mime = get_mime_type(ext)
        gemini_ok = is_gemini_supported(ext)
        file_id = uuid.uuid4().hex

        # Copy to permanent local storage
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

        prepared.append((pf, local_path, ext, mime, gemini_ok))
        existing_count += 1
        existing_bytes += size_bytes

    # --- Phase 2: Parallel Gemini Files API uploads ---
    # Upload all Gemini-supported files concurrently instead of one-by-one
    gemini_tasks = []
    gemini_indices = []  # track which prepared[] index each task maps to
    for idx, (pf, local_path, ext, mime, gemini_ok) in enumerate(prepared):
        if gemini_ok:
            gemini_tasks.append(asyncio.to_thread(upload_to_gemini, local_path, mime, pf.original_name))
            gemini_indices.append(idx)

    if gemini_tasks:
        if status_callback:
            await status_callback(f"Uploading {len(gemini_tasks)} files to Gemini...")
        with log_time(log, "Parallel Gemini uploads", count=len(gemini_tasks)):
            gemini_results = await asyncio.gather(*gemini_tasks, return_exceptions=True)

        for i, result in enumerate(gemini_results):
            idx = gemini_indices[i]
            pf = prepared[idx][0]
            if isinstance(result, Exception):
                log.error("Gemini Files upload failed, falling back to extraction",
                          file=pf.original_name, error=str(result))
                pf.error = f"Gemini upload failed: {result}"
            else:
                uri, gname, expiry = result
                pf.gemini_uri = uri
                pf.gemini_name = gname
                pf.gemini_expiry = expiry
                ctx.gemini_file_parts.append({
                    "file_data": {"file_uri": uri, "mime_type": pf.mime_type},
                    "name": pf.original_name,
                })
                log.info("Gemini Files upload OK", file=pf.original_name, mime=pf.mime_type)

    # --- Phase 3: Process each file (text extraction, OCR, ChromaDB) ---
    for pf, local_path, ext, mime, gemini_ok in prepared:
        if status_callback:
            await status_callback(f"Processing {pf.original_name}...")

        # --- PDF-specific handling ---
        if ext == ".pdf":
            try:
                text, page_count = await asyncio.to_thread(_extract_pdf_text, local_path)
                pf.page_count = page_count
                is_scanned = not text.strip()

                if is_scanned and pf.gemini_uri:
                    # Scanned PDF but Gemini Files API has it — skip expensive OCR.
                    # Gemini Pro can read the PDF directly via its file URI.
                    # Only run OCR if we need ChromaDB (for retrieval on follow-ups).
                    log.info("Scanned PDF: Gemini URI available, skipping Vision OCR",
                             file=pf.original_name, pages=page_count)

                elif is_scanned:
                    # Scanned PDF and no Gemini URI — must run Vision OCR
                    log.info("Scanned PDF: no Gemini URI, running Vision OCR",
                             file=pf.original_name, pages=page_count)
                    text = await asyncio.to_thread(_vision_ocr_pdf, local_path, page_count)

                # Store large PDFs in ChromaDB for retrieval (even if Gemini has it,
                # ChromaDB enables targeted chunk retrieval for follow-up questions)
                if text.strip() and (page_count > MAX_INLINE_PDF_PAGES or len(text) > MAX_INLINE_TEXT_CHARS):
                    collection_id = f"inline_{thread_id}_{pf.file_id[:8]}"
                    try:
                        await asyncio.to_thread(_store_in_chromadb, text, collection_id, pf.original_name)
                        pf.chromadb_collection = collection_id
                        ctx.chromadb_collections.append(collection_id)
                        log.info("Large PDF stored in ChromaDB",
                                 file=pf.original_name, pages=page_count, collection=collection_id)
                    except Exception as e:
                        log.error("ChromaDB storage failed", error=str(e))
                        pf.extracted_text = text[:MAX_INLINE_TEXT_CHARS]
                        inline_parts.append(f"[File: {pf.original_name}]\n{pf.extracted_text}")
                elif text.strip():
                    # Small PDF with text — use inline
                    if not pf.gemini_uri:
                        pf.extracted_text = text
                        inline_parts.append(f"[File: {pf.original_name}]\n{text}")

            except Exception as e:
                if not pf.gemini_uri:
                    pf.error = f"PDF processing failed: {e}"

        # --- Step 4: Text extraction for DOCX / XLSX (Gemini Files API not supported) ---
        elif ext == ".docx":
            try:
                text = await asyncio.to_thread(_extract_docx_text, local_path)
                pf.extracted_text = text
                inline_parts.append(f"[File: {pf.original_name}]\n{text}")
            except Exception as e:
                pf.error = f"Failed to read DOCX: {e}"

        elif ext == ".xlsx":
            try:
                text = await asyncio.to_thread(_extract_xlsx_text, local_path)
                pf.extracted_text = text
                inline_parts.append(f"[File: {pf.original_name}]\n{text}")
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

        # --- Persist to SQLite thread_files ---
        try:
            await chat_store.save_thread_file(thread_id, pf)
        except Exception as e:
            log.error("Failed to save thread_file record", file=pf.original_name, error=str(e))

        log.info("File processed",
                 file=pf.original_name, type=pf.file_type,
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