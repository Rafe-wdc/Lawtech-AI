# File Attachment Handling — Complete Technical Guide

> How Lawttorney processes uploaded files from upload to AI response, including PDF OCR, Gemini Files API, ChromaDB storage, and multi-turn persistence.

---

## Overview

```
User uploads files via /pyapi/chat
         │
    ┌────┴────┐
    │ Validate │  Max 30 files, 1GB each, 10 supported formats
    └────┬────┘
         │
    ┌────┴─────────────────────────────────────┐
    │ Process (parallel per file type)          │
    │                                           │
    │  PDF ──→ Text Extract (PyMuPDF)           │
    │      ──→ Scanned? → Gemini Vision OCR     │
    │      ──→ Large? → ChromaDB chunked store  │
    │      ──→ Gemini Files API upload          │
    │                                           │
    │  Image ──→ Gemini Files API upload        │
    │                                           │
    │  DOCX ──→ Text Extract (python-docx)      │
    │       ──→ Large? → ChromaDB               │
    │                                           │
    │  XLSX ──→ Table Extract (openpyxl)        │
    │       ──→ Large? → ChromaDB               │
    │                                           │
    │  TXT/CSV/MD ──→ Gemini Files API upload   │
    └────┬─────────────────────────────────────┘
         │
    ┌────┴────┐
    │ Route   │  Orchestrator adds "Document" agent to plan
    └────┬────┘
         │
    ┌────┴────────────────────────────────────┐
    │ Document Agent (3 modes)                │
    │                                          │
    │  Mode 1: Gemini File Parts (native PDF) │
    │  Mode 2: Inline Text (extracted content) │
    │  Mode 3: ChromaDB Retrieval (large docs) │
    └────┬────────────────────────────────────┘
         │
    AI Response with file-aware analysis
```

---

## 1. Supported File Types

| Format | Extension | Processing Method | Gemini Upload | Max Size |
|--------|-----------|------------------|---------------|----------|
| **PDF** | `.pdf` | PyMuPDF text + Gemini Vision OCR | Yes | 1 GB |
| **Image** | `.jpg`, `.jpeg`, `.png`, `.webp` | Gemini Files API (native vision) | Yes | 1 GB |
| **Word** | `.docx` | python-docx paragraph + table extraction | No | 1 GB |
| **Excel** | `.xlsx` | openpyxl sheet-by-sheet (max 100 rows/sheet) | No | 1 GB |
| **Text** | `.txt`, `.md` | Direct read (first 100K chars) | Yes | 1 GB |
| **CSV** | `.csv` | Direct read | Yes | 1 GB |

---

## 2. Upload & Validation

### API Endpoint

```
POST /pyapi/chat
Content-Type: multipart/form-data

Fields:
  query: str (required, 1-30,000 chars)
  globalThreadId: str (optional, for multi-turn)
  preferred_language: str (optional, ISO 639-1)
  files: File[] (optional, up to 30 files)
```

### Limits

| Parameter | Value | Location |
|-----------|-------|----------|
| Max files per request | 30 | `core/settings.py` |
| Max files per thread | 30 | `core/settings.py` |
| Max file size | 1 GB (1024 MB) | `core/settings.py` |
| Max thread storage | 1 GB total | `core/settings.py` |
| Allowed extensions | 10 formats | `core/file_processor.py` |

### Validation Checks

1. **File count** — rejects if > 30 files in request
2. **Thread-level limits** — rejects if thread already has 30 files or 1GB total
3. **File size** — rejects individual files > 1GB
4. **Extension whitelist** — rejects unsupported formats
5. **Encrypted PDFs** — detected and rejected with clear error
6. **Empty PDFs** — detected (0 pages) and rejected

---

## 3. Processing Pipeline

### Phase 1: Save & Prepare

```
For each uploaded file:
  1. Generate file_id (UUID)
  2. Copy to permanent storage: ./uploads/{thread_id}/{file_id}_{filename}
  3. Detect MIME type
  4. Create ProcessedFile record
```

### Phase 2: Gemini Files API Upload (Parallel)

Files supported by Gemini (PDF, images, TXT, CSV, MD) are uploaded concurrently:

```
asyncio.gather(
    upload_to_gemini(file1, mime1, name1),
    upload_to_gemini(file2, mime2, name2),
    ...
)
```

Each upload returns:
- `uri`: HTTPS URL for Gemini API calls (e.g., `https://generativelanguage.googleapis.com/v1beta/files/abc123`)
- `gemini_name`: Handle for deletion (e.g., `files/abc123`)
- `expiry_iso`: 48-hour expiry timestamp

### Phase 3: File-Type-Specific Processing

#### PDF Processing

```
PDF uploaded
    │
    ├── Extract text (PyMuPDF) → text, page_count
    │
    ├── Is text empty? (scanned PDF)
    │   │
    │   ├── YES + Gemini URI + >20 pages
    │   │   └── Background OCR (async) + ChromaDB for follow-ups
    │   │       └── Gemini URI used for immediate response
    │   │
    │   ├── YES + Gemini URI + ≤20 pages
    │   │   └── Gemini Pro reads PDF natively (no OCR needed)
    │   │
    │   └── YES + No Gemini URI
    │       └── Synchronous Vision OCR → inline text
    │
    └── Has text (not scanned)
        │
        ├── >20 pages OR >100K chars
        │   └── ChromaDB chunked storage + truncated inline
        │
        └── ≤20 pages AND ≤100K chars
            └── Full inline text
```

#### OCR Pipeline (Scanned PDFs)

```
Scanned PDF
    │
    ├── Check cache: SHA256 hash → .ocr_cache/{hash}.txt
    │   └── Cache hit? Return cached text
    │
    ├── Render pages to JPEG (DPI: 200, quality: 85)
    │   └── Batches of 10 pages
    │
    ├── Send batches to Gemini Flash Lite (max 4 concurrent)
    │   └── Legal-aware OCR prompt:
    │       "Extract ALL text including party names, case numbers,
    │        dates, section numbers, court names..."
    │
    ├── Combine batch results
    │
    └── Cache result for future use
```

**OCR Configuration:**

| Parameter | Value |
|-----------|-------|
| Rendering DPI | 200 |
| JPEG quality | 85 |
| Batch size | 10 pages |
| Max concurrent batches | 4 |
| OCR timeout | 120 seconds |
| Cache location | `chroma_store/.ocr_cache/` |

#### DOCX Processing

```
DOCX uploaded
    │
    ├── Extract paragraphs (python-docx)
    ├── Extract tables (pipe-delimited format)
    │
    ├── >100K chars? → ChromaDB chunked storage
    └── ≤100K chars? → Inline text
```

#### XLSX Processing

```
XLSX uploaded
    │
    ├── Iterate sheets (max 100 rows per sheet)
    ├── Format as markdown tables
    │
    ├── >100K chars? → ChromaDB chunked storage
    └── ≤100K chars? → Inline text
```

---

## 4. ChromaDB Storage (Large Files)

When a file exceeds inline limits, it's chunked and stored in ChromaDB for semantic retrieval.

### Chunking Strategy

```python
RecursiveCharacterTextSplitter(
    chunk_size=1000,       # chars per chunk
    chunk_overlap=200,     # overlap for context continuity
    separators=["\n\n", "\n", ". ", " "]
)
```

### Storage

| Parameter | Value |
|-----------|-------|
| Collection ID | `inline_{thread_id}_{file_id[:8]}` |
| Persist directory | `chroma_store/{collection_id}` |
| Embedding model | `all-MiniLM-L6-v2` |
| Metadata per chunk | `{source: filename, chunk: index}` |

### Retrieval (Document Agent)

| Scenario | k (results) | fetch_k | Strategy |
|----------|-------------|---------|----------|
| Single collection | 30 | 50 | MMR |
| Multiple collections | 15 per collection | 30 | MMR, merged |

---

## 5. File Context in LangGraph State

### State Schema

```python
class LegalAgentState(MessagesState):
    file_context: dict | None  # raw dict stored in state
```

### FileContextData Helper

```python
@dataclass
class FileContextData:
    inline_text: str = ""              # Combined extracted text (max 100K chars)
    gemini_file_parts: list[dict] = [] # [{file_data: {file_uri, mime_type}, name}]
    image_data: list[dict] = []        # Legacy base64 (backward compat)
    chromadb_collections: list[str] = [] # Collection IDs for vector retrieval
    file_names: list[str] = []         # Original filenames
    summary: str = ""                  # "2 PDFs, 1 image"

    @property
    def has_content(self) -> bool:
        return bool(self.inline_text or self.gemini_file_parts 
                     or self.image_data or self.chromadb_collections)

    @property
    def all_gemini_parts(self) -> list[dict]:
        # Combines URI-based + legacy base64 parts
```

---

## 6. Orchestrator Routing with Files

When files are attached, the orchestrator adjusts its behavior:

### Classification

```
User query + "[User has uploaded files: contract.pdf, invoice.xlsx]"
                    │
                    ▼
            Task Classification
                    │
                    ├── Non-legal query + files → Override to "Document"
                    └── Any query + files → Add "Document" agent to plan
```

### Plan Enrichment

```python
# Always include Document agent if files are attached
if fc and fc.has_content and "Document" not in tasks_planned:
    tasks_planned.append("Document")
```

### Synthesis

```python
# Inject file content into synthesis prompt (up to 50K chars)
if fc and fc.inline_text:
    query = f"{query}\n\n--- Uploaded File Content ---\n{fc.inline_text[:50000]}"
```

---

## 7. Document Agent — Three Processing Modes

### Mode 1: Gemini File Parts (Best Quality)

**When:** Files have Gemini URIs (PDF, images, TXT, CSV, MD)

```
Gemini Pro receives:
  - System prompt (legal-aware analysis instructions)
  - File URI references (native multimodal processing)
  - User query text
  - Chat history
```

- LLM reads the actual PDF/image natively
- Best for: scanned documents, complex layouts, images, charts
- Timeout: 90 seconds

### Mode 2: Inline Text

**When:** Files have extracted text but no Gemini URI (DOCX, XLSX)

```
Gemini Flash Full receives:
  - System prompt
  - Extracted text (first 80K chars)
  - User query
  - Chat history
```

- LLM processes raw text in prompt
- Good for: text-heavy documents, spreadsheets
- Fast but loses formatting/layout

### Mode 3: ChromaDB Retrieval

**When:** Large files with ChromaDB collections (follow-up questions)

```
1. Semantic search: query → ChromaDB → top 30 relevant chunks
2. Gemini receives: retrieved chunks + query
```

- Best for: large PDFs where only parts are relevant
- MMR (Maximal Marginal Relevance) ensures diverse results
- Used primarily for multi-turn follow-up questions

### Mode Selection Priority

```
1. Gemini File Parts? → Mode 1 (native multimodal)
2. Inline Text? → Mode 2 (text in prompt)  
3. ChromaDB Collections? → Mode 3 (vector retrieval)
```

---

## 8. Multi-Turn File Persistence

### Turn 1: Upload

```
User uploads PDF + asks question
    │
    ├── process_files() → FileContext
    ├── chat_store.save_thread_file() → SQLite persistence
    ├── Gemini URI passed to Document agent
    └── Response generated
```

### Turn 2+: Follow-up (No New Files)

```
User asks follow-up question
    │
    ├── Memory node: load_thread_files() from SQLite
    ├── Check Gemini URI expiry:
    │   ├── Valid → Reuse URI
    │   └── Expired → Re-upload from local storage
    ├── Rebuild FileContextData
    └── Document agent uses restored context
```

### Database Schema

```sql
CREATE TABLE thread_files (
    id              INTEGER PRIMARY KEY,
    thread_id       TEXT NOT NULL,
    file_id         TEXT NOT NULL,
    filename        TEXT,
    file_type       TEXT,
    mime_type       TEXT,
    size_bytes      INTEGER,
    local_path      TEXT,
    extracted_text  TEXT,
    chromadb_collection TEXT,
    gemini_uri      TEXT,
    gemini_name     TEXT,
    gemini_expiry   TEXT,
    gemini_supported INTEGER,
    page_count      INTEGER,
    upload_error    TEXT DEFAULT '',
    ocr_status      TEXT DEFAULT '',  -- pending, complete, failed
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Gemini URI Lifecycle

```
Upload → URI valid for 48 hours
    │
    ├── Turn within 46 hours → Reuse URI
    ├── Turn after 46 hours → Re-upload from local file
    └── Local file deleted → Cannot restore (warn user)
```

Buffer: 2 hours before expiry triggers re-upload.

---

## 9. Error Handling & Fallbacks

| Failure | Fallback | Impact |
|---------|----------|--------|
| Gemini upload fails | Use text extraction only | Loses multimodal capability |
| OCR fails | Use raw PDF text (if any) | May have empty content for scanned PDFs |
| ChromaDB storage fails | Truncate to inline text (100K chars) | Loses chunks beyond 100K |
| Gemini URI expired | Re-upload from local storage | Slight delay on next turn |
| Local file missing | Log warning, skip file | Cannot process in future turns |
| Encrypted PDF | Reject with clear error | User must provide unencrypted version |
| Empty PDF (0 pages) | Reject with clear error | User must provide valid PDF |
| Extraction returns empty | Mark as error on ProcessedFile | Shown to user as "No content extracted" |
| Background OCR fails | Update status to "failed" in SQLite | User informed, can retry |

---

## 10. API Endpoints

### Upload & Process

```
POST /pyapi/chat
Content-Type: multipart/form-data

Fields:
  query: str (required)
  globalThreadId: str (optional)
  files: File[] (optional, up to 30)

Response: SSE stream with:
  - file_processing events (progress)
  - token events (AI response)
  - sources events (citations)
  - done event (metadata)
```

### List Thread Files

```
GET /pyapi/thread/{thread_id}/files

Response:
  {files: [{file_id, filename, file_type, size_bytes, 
            gemini_uri, ocr_status, upload_error}]}
```

### Delete Thread Files

```
DELETE /pyapi/thread/{thread_id}/files

Cleanup:
  - Local storage (./uploads/{thread_id}/)
  - Gemini Files API entries
  - ChromaDB collections
  - SQLite thread_files records
```

---

## 11. Configuration Summary

| Parameter | Value | File |
|-----------|-------|------|
| Max files per request | 30 | `core/settings.py` |
| Max files per thread | 30 | `core/settings.py` |
| Max file size | 1 GB | `core/settings.py` |
| Max thread storage | 1 GB | `core/settings.py` |
| Max inline text | 100,000 chars | `core/file_processor.py` |
| Max inline PDF pages | 20 | `core/file_processor.py` |
| OCR batch size | 10 pages | `core/file_processor.py` |
| OCR DPI | 200 | `core/file_processor.py` |
| Max concurrent OCR | 4 | `core/file_processor.py` |
| OCR timeout | 120 sec | `core/file_processor.py` |
| ChromaDB timeout | 90 sec | `core/settings.py` |
| Gemini URI buffer | 2 hours | `core/settings.py` |
| JPEG quality (OCR) | 85 | `core/file_processor.py` |
| Chunk size | 1,000 chars | `core/file_processor.py` |
| Chunk overlap | 200 chars | `core/file_processor.py` |
| MMR k (single collection) | 30 | `agents/document.py` |
| MMR k (multi collection) | 15 per | `agents/document.py` |
| Query max length | 30,000 chars | `core/gateway.py` |

---

*Documentation generated April 2026. Reflects current codebase on `features/25-spectrum` branch.*
