"""Unified file processing for inline chat attachments.

Handles PDF, images, DOCX, TXT/MD, CSV, and XLSX files uploaded
alongside chat messages. Reuses existing PyMuPDF and Gemini Vision
OCR patterns from document_tools.

Small files (PDF ≤20 pages, text files) → inline text injection.
Large PDFs (>20 pages or >100K chars) → ChromaDB vector storage.
Images → base64 for Gemini multimodal.
"""

from __future__ import annotations

import base64
import csv
import io
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from core.logger import get_logger
from core.settings import CHROMA_STORE_ROOT

_chroma_cache_lock = threading.Lock()

log = get_logger("FileProcessor")

# --- Constants ---

MAX_FILES = 5
MAX_FILE_SIZE_MB = 50
MAX_INLINE_TEXT_CHARS = 100_000
MAX_INLINE_PDF_PAGES = 20
MAX_IMAGES = 3
MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2MB before resize
VISION_BATCH_SIZE = 5

ALLOWED_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".webp",
    ".docx", ".txt", ".md", ".csv", ".xlsx",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


# --- Data Structures ---

@dataclass
class ProcessedFile:
    """Result of processing a single uploaded file."""
    original_name: str
    file_type: str  # "pdf", "image", "docx", "txt", "csv", "xlsx"
    size_bytes: int
    extracted_text: str = ""
    image_base64: str = ""
    image_mime: str = ""
    page_count: int = 0
    stored_in_chromadb: bool = False
    unique_string: str = ""
    error: str | None = None


@dataclass
class FileContext:
    """Aggregated result of processing all uploaded files."""
    files: list[ProcessedFile] = field(default_factory=list)
    inline_text: str = ""
    image_data: list[dict] = field(default_factory=list)  # [{base64, mime}]
    chromadb_collections: list[str] = field(default_factory=list)
    summary: str = ""
    file_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize for LangGraph state (must be JSON-serializable)."""
        return {
            "inline_text": self.inline_text,
            "image_data": self.image_data,
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


# --- Per-Type Extractors ---

def _extract_pdf(file_path: str, thread_id: str) -> ProcessedFile:
    """Extract text from PDF. Small → inline, large → ChromaDB."""
    import fitz

    name = os.path.basename(file_path)
    size = os.path.getsize(file_path)
    result = ProcessedFile(original_name=name, file_type="pdf", size_bytes=size)

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        result.error = f"Cannot open PDF: {e}"
        return result

    if doc.is_encrypted:
        doc.close()
        result.error = "PDF is encrypted/password-protected"
        return result

    result.page_count = doc.page_count

    if doc.page_count == 0:
        doc.close()
        result.error = "PDF has no pages"
        return result

    # Extract text with PyMuPDF
    all_text = []
    for page_num, page in enumerate(doc):
        text = page.get_text("text")
        if text and text.strip():
            all_text.append(f"--- Page {page_num + 1} ---\n{text.strip()}")

    full_text = "\n\n".join(all_text)

    # If scanned (no text), try Vision OCR
    if not full_text.strip() and doc.page_count > 0:
        doc.close()
        full_text = _vision_ocr_pdf(file_path, 0, result.page_count - 1)
        if not full_text:
            result.error = "Could not extract text from scanned PDF"
            return result

    doc.close()

    # Decide: inline vs ChromaDB
    if result.page_count > MAX_INLINE_PDF_PAGES or len(full_text) > MAX_INLINE_TEXT_CHARS:
        # Store in ChromaDB
        collection_id = f"inline_{thread_id}_{uuid.uuid4().hex[:8]}"
        try:
            _store_in_chromadb(full_text, collection_id, name)
            result.stored_in_chromadb = True
            result.unique_string = collection_id
            log.info("PDF stored in ChromaDB",
                     file=name, pages=result.page_count, collection=collection_id)
        except Exception as e:
            log.error("Failed to store PDF in ChromaDB", error=str(e))
            # Fall back to truncated inline
            result.extracted_text = full_text[:MAX_INLINE_TEXT_CHARS]
    else:
        result.extracted_text = full_text

    return result


def _vision_ocr_pdf(file_path: str, start_page: int, end_page: int) -> str:
    """Run Gemini Vision OCR on PDF pages. Returns extracted text."""
    import fitz
    from core.clients import get_gemini_flash

    try:
        doc = fitz.open(file_path)
        llm = get_gemini_flash(temperature=0.0)
        results = []
        batch_images: list[str] = []

        for i in range(start_page, end_page + 1):
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
            results.append(_ocr_batch(llm, batch_images, end_page - len(batch_images) + 1))

        doc.close()
        return "\n\n".join(results)

    except Exception as e:
        log.error("Vision OCR failed", error=str(e))
        return ""


def _ocr_batch(llm, batch_b64: list[str], batch_start: int) -> str:
    """OCR a batch of page images using Gemini Vision."""
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
    # Lock around cache clear to prevent race with concurrent workers
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


def _extract_image(file_path: str) -> ProcessedFile:
    """Read image file and base64 encode for Gemini multimodal."""
    name = os.path.basename(file_path)
    size = os.path.getsize(file_path)
    ext = Path(file_path).suffix.lower()
    mime = IMAGE_MIME_MAP.get(ext, "image/jpeg")
    result = ProcessedFile(original_name=name, file_type="image", size_bytes=size)

    try:
        with open(file_path, "rb") as f:
            data = f.read()

        # Resize if too large
        if len(data) > MAX_IMAGE_BYTES:
            data = _resize_image(data, mime)

        result.image_base64 = base64.b64encode(data).decode("utf-8")
        result.image_mime = mime
    except Exception as e:
        result.error = f"Failed to read image: {e}"

    return result


def _resize_image(data: bytes, mime: str) -> bytes:
    """Resize image to fit within 2MB using PIL."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        # Scale down proportionally
        scale = (MAX_IMAGE_BYTES / len(data)) ** 0.5
        new_w = int(img.width * scale)
        new_h = int(img.height * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        fmt = "JPEG" if "jpeg" in mime else "PNG"
        img.save(buf, format=fmt, quality=85)
        return buf.getvalue()
    except ImportError:
        log.warning("PIL not available, returning original image")
        return data


def _extract_docx(file_path: str) -> ProcessedFile:
    """Extract text from DOCX using python-docx."""
    name = os.path.basename(file_path)
    size = os.path.getsize(file_path)
    result = ProcessedFile(original_name=name, file_type="docx", size_bytes=size)

    try:
        from docx import Document

        doc = Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]

        # Extract tables
        table_texts = []
        for table in doc.tables:
            rows = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                rows.append(" | ".join(cells))
            if rows:
                header = rows[0]
                separator = " | ".join(["---"] * len(table.rows[0].cells))
                table_text = "\n".join([header, separator] + rows[1:])
                table_texts.append(table_text)

        parts = paragraphs
        if table_texts:
            parts.append("\n\n--- Tables ---\n")
            parts.extend(table_texts)

        result.extracted_text = "\n\n".join(parts)
        result.page_count = len(doc.paragraphs) // 40 or 1  # rough estimate

    except Exception as e:
        result.error = f"Failed to read DOCX: {e}"

    return result


def _extract_text_file(file_path: str) -> ProcessedFile:
    """Extract text from TXT/MD files."""
    name = os.path.basename(file_path)
    size = os.path.getsize(file_path)
    result = ProcessedFile(original_name=name, file_type="txt", size_bytes=size)

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            result.extracted_text = f.read(MAX_INLINE_TEXT_CHARS)
    except Exception as e:
        result.error = f"Failed to read text file: {e}"

    return result


def _extract_csv(file_path: str) -> ProcessedFile:
    """Extract CSV data as markdown table (first 100 rows)."""
    name = os.path.basename(file_path)
    size = os.path.getsize(file_path)
    result = ProcessedFile(original_name=name, file_type="csv", size_bytes=size)

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            rows = []
            for i, row in enumerate(reader):
                if i >= 100:
                    rows.append(["... (truncated)"])
                    break
                rows.append(row)

        if not rows:
            result.error = "CSV file is empty"
            return result

        # Convert to markdown table
        header = " | ".join(rows[0])
        separator = " | ".join(["---"] * len(rows[0]))
        body = "\n".join(" | ".join(r) for r in rows[1:])
        result.extracted_text = f"{header}\n{separator}\n{body}"

    except Exception as e:
        result.error = f"Failed to read CSV: {e}"

    return result


def _extract_xlsx(file_path: str) -> ProcessedFile:
    """Extract XLSX data as markdown tables (one per sheet, first 100 rows each)."""
    name = os.path.basename(file_path)
    size = os.path.getsize(file_path)
    result = ProcessedFile(original_name=name, file_type="xlsx", size_bytes=size)

    try:
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
                rows.append([str(cell) if cell is not None else "" for cell in row])

            if not rows:
                continue

            header = " | ".join(rows[0])
            separator = " | ".join(["---"] * len(rows[0]))
            body = "\n".join(" | ".join(r) for r in rows[1:])
            sheets_text.append(f"### Sheet: {sheet_name}\n\n{header}\n{separator}\n{body}")

        wb.close()
        result.extracted_text = "\n\n".join(sheets_text)

    except Exception as e:
        result.error = f"Failed to read XLSX: {e}"

    return result


# --- Extractor Dispatch ---

_EXTRACTORS = {
    ".pdf": lambda fp, tid: _extract_pdf(fp, tid),
    ".jpg": lambda fp, _: _extract_image(fp),
    ".jpeg": lambda fp, _: _extract_image(fp),
    ".png": lambda fp, _: _extract_image(fp),
    ".webp": lambda fp, _: _extract_image(fp),
    ".docx": lambda fp, _: _extract_docx(fp),
    ".txt": lambda fp, _: _extract_text_file(fp),
    ".md": lambda fp, _: _extract_text_file(fp),
    ".csv": lambda fp, _: _extract_csv(fp),
    ".xlsx": lambda fp, _: _extract_xlsx(fp),
}


# --- Main Processing Function ---

async def process_files(
    files: list[tuple[str, str, int]],
    thread_id: str,
    status_callback=None,
) -> FileContext:
    """Process uploaded files and return aggregated FileContext.

    Args:
        files: List of (temp_file_path, original_filename, size_bytes) tuples.
        thread_id: Thread ID for ChromaDB collection naming.
        status_callback: Optional async callable(message: str) for SSE status updates.

    Returns:
        FileContext with inline text, image data, and ChromaDB collection IDs.
    """
    ctx = FileContext()

    if len(files) > MAX_FILES:
        log.warning("Too many files", count=len(files), max=MAX_FILES)
        files = files[:MAX_FILES]

    image_count = 0
    inline_parts: list[str] = []

    for file_path, filename, size_bytes in files:
        ext = Path(filename).suffix.lower()
        ctx.file_names.append(filename)

        # Validate
        err = validate_upload(filename, size_bytes)
        if err:
            pf = ProcessedFile(
                original_name=filename, file_type=ext.lstrip("."),
                size_bytes=size_bytes, error=err,
            )
            ctx.files.append(pf)
            log.warning("File rejected", file=filename, reason=err)
            continue

        # Status update
        if status_callback:
            await status_callback(f"Processing {filename}...")

        # Extract
        extractor = _EXTRACTORS.get(ext)
        if not extractor:
            pf = ProcessedFile(
                original_name=filename, file_type=ext.lstrip("."),
                size_bytes=size_bytes, error=f"No extractor for {ext}",
            )
            ctx.files.append(pf)
            continue

        pf = extractor(file_path, thread_id)
        ctx.files.append(pf)

        if pf.error:
            log.warning("Extraction failed", file=filename, error=pf.error)
            continue

        # Aggregate results
        if pf.file_type == "image" and pf.image_base64:
            if image_count < MAX_IMAGES:
                ctx.image_data.append({
                    "base64": pf.image_base64,
                    "mime": pf.image_mime,
                    "name": filename,
                })
                image_count += 1
            else:
                log.warning("Max images reached, skipping", file=filename)

        elif pf.stored_in_chromadb:
            ctx.chromadb_collections.append(pf.unique_string)

        elif pf.extracted_text:
            inline_parts.append(f"[File: {filename}]\n{pf.extracted_text}")

        log.info("File processed", file=filename, type=pf.file_type,
                 text_len=len(pf.extracted_text), chromadb=pf.stored_in_chromadb)

    # Build inline text (truncate to limit)
    combined = "\n\n---\n\n".join(inline_parts)
    if len(combined) > MAX_INLINE_TEXT_CHARS:
        combined = combined[:MAX_INLINE_TEXT_CHARS] + "\n\n[... text truncated]"
    ctx.inline_text = combined

    # Build summary
    type_counts: dict[str, int] = {}
    for pf in ctx.files:
        if not pf.error:
            type_counts[pf.file_type] = type_counts.get(pf.file_type, 0) + 1
    parts = [f"{count} {ftype}{'s' if count > 1 else ''}" for ftype, count in type_counts.items()]
    ctx.summary = f"Processed {', '.join(parts)}" if parts else "No files processed"

    log.info("File processing complete",
             total=len(files), summary=ctx.summary,
             inline_chars=len(ctx.inline_text),
             images=len(ctx.image_data),
             chromadb=len(ctx.chromadb_collections))

    return ctx
