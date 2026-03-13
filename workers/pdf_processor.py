"""Background PDF processing worker.

Handles async PDF upload processing:
1. Validate PDF (size, pages, encryption)
2. Extract text (PyMuPDF direct, with Vision OCR fallback)
3. Chunk and store in ChromaDB

Uses an in-memory job store for status tracking.
For production, swap to Redis-backed storage.

Usage from gateway:
    from workers.pdf_processor import submit_pdf_job, get_job_status
    job_id = submit_pdf_job(file_path, unique_string, filename)
    status = get_job_status(job_id)
"""

from __future__ import annotations

import os
import time
import uuid
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.logger import get_logger

log = get_logger("PdfWorker")


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class PdfJob:
    job_id: str
    unique_string: str
    filename: str
    status: JobStatus = JobStatus.PENDING
    progress: str = ""
    chunks_stored: int = 0
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None


# In-memory job store (swap to Redis for production)
_jobs: dict[str, PdfJob] = {}
_lock = threading.Lock()

# Per-collection lock to prevent concurrent metadata read-modify-write corruption
_collection_locks: dict[str, threading.Lock] = {}
_collection_locks_guard = threading.Lock()


def _get_collection_lock(collection_name: str) -> threading.Lock:
    with _collection_locks_guard:
        if collection_name not in _collection_locks:
            _collection_locks[collection_name] = threading.Lock()
        return _collection_locks[collection_name]


def get_job_status(job_id: str) -> dict[str, Any] | None:
    """Get the current status of a PDF processing job."""
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        return {
            "job_id": job.job_id,
            "status": job.status.value,
            "progress": job.progress,
            "chunks_stored": job.chunks_stored,
            "error": job.error,
            "filename": job.filename,
        }


def _process_pdf(job: PdfJob, file_path: str) -> None:
    """Process a PDF file in a background thread.

    Steps:
    1. Validate with PyMuPDF
    2. Extract text (direct + Vision OCR fallback)
    3. Chunk with RecursiveCharacterTextSplitter
    4. Store in ChromaDB
    """
    import fitz
    from langchain_core.documents import Document
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.vectorstores import Chroma

    from core.clients import get_qa_embeddings
    from core.settings import CHROMA_STORE_ROOT
    from tools.shared.document_tools import extract_text_vision

    try:
        with _lock:
            job.status = JobStatus.PROCESSING
            job.progress = "Validating PDF..."

        # Step 1: Validate
        pdf_doc = fitz.open(file_path)
        if pdf_doc.is_encrypted:
            raise ValueError("PDF is encrypted/password-protected")
        num_pages = pdf_doc.page_count
        if num_pages == 0:
            raise ValueError("PDF has no pages")

        with _lock:
            job.progress = f"Extracting text from {num_pages} pages..."

        # Step 2: Extract text
        text_threshold = num_pages * 700
        extracted_text = ""
        for page in pdf_doc:
            extracted_text += page.get_text("text") + "\n"
        pdf_doc.close()

        if len(extracted_text.strip()) < text_threshold:
            with _lock:
                job.progress = "Text sparse, using Vision OCR fallback..."
            vision_result = extract_text_vision.invoke(
                {"file_path": file_path, "start_page": 0, "end_page": -1}
            )
            pdf_text = vision_result.get("text", "")
        else:
            pdf_text = extracted_text

        if not pdf_text.strip():
            raise ValueError("No text could be extracted from the PDF")

        with _lock:
            job.progress = "Chunking text..."

        # Step 3: Chunk
        text_splitter = RecursiveCharacterTextSplitter(
            separators=[""], chunk_size=15000, chunk_overlap=200
        )
        documents = [Document(page_content=pdf_text, metadata={"source": job.filename})]
        texts = text_splitter.split_documents(documents)

        with _lock:
            job.progress = f"Storing {len(texts)} chunks in ChromaDB..."

        # Step 4: Store in ChromaDB
        collection_name = f"collection_{job.unique_string}"
        persist_dir = os.path.join(CHROMA_STORE_ROOT, job.unique_string)
        os.makedirs(persist_dir, exist_ok=True)

        embeddings = get_qa_embeddings()
        collection_exists = os.path.exists(os.path.join(persist_dir, "chroma.sqlite3"))

        # Lock per collection to prevent concurrent metadata corruption
        with _get_collection_lock(collection_name):
            existing_filenames = []
            if collection_exists:
                vectordb = Chroma(
                    embedding_function=embeddings,
                    collection_name=collection_name,
                    persist_directory=persist_dir,
                )
                try:
                    meta = vectordb._collection.metadata
                    if meta:
                        fstr = meta.get("filenames", "")
                        existing_filenames = [f for f in fstr.split(",") if f]
                except Exception:
                    pass
                vectordb.add_documents(texts)
            else:
                vectordb = Chroma.from_documents(
                    documents=texts,
                    embedding=embeddings,
                    collection_name=collection_name,
                    persist_directory=persist_dir,
                )

            all_filenames = list(set(existing_filenames + [job.filename]))
            vectordb._collection.modify(
                metadata={
                    "filenames": ",".join(all_filenames),
                    "upload_date": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "file_count": str(len(all_filenames)),
                }
            )

        with _lock:
            job.status = JobStatus.COMPLETED
            job.chunks_stored = len(texts)
            job.progress = "Done"
            job.completed_at = time.time()

        log.info(f"PDF processing complete: {job.filename} -> {len(texts)} chunks")

    except Exception as e:
        log.error(f"PDF processing failed for {job.filename}: {e}")
        with _lock:
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.completed_at = time.time()

    finally:
        # Clean up temp file
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass


def submit_pdf_job(file_path: str, unique_string: str, filename: str) -> str:
    """Submit a PDF file for background processing.

    Returns the job_id for status polling.
    """
    job_id = str(uuid.uuid4())
    job = PdfJob(job_id=job_id, unique_string=unique_string, filename=filename)

    with _lock:
        _jobs[job_id] = job

    thread = threading.Thread(target=_process_pdf, args=(job, file_path), daemon=True)
    thread.start()

    return job_id
