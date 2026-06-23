# V2 file processing — regression vs V1

**Date:** 2026-06-23
**Trigger:** User attached two test PDFs (524 pages, 29 MB combined) to `POST /pyapi/chat` on `api.lawttorney.com`. Pipeline silently hung for 600+ seconds with zero progress events. User reports: **"these was smoothly handled by our previous system."**

**Plain-English summary:** When migrating from V1 (`mainqa_test.py` based) to V2 (LangGraph + multi-agent), we replaced V1's lean and reliable PDF-processing path with a richer-but-fragile path that introduces multiple new ways to silently hang on large evidence bundles. The user's claim is correct and verifiable.

---

## Empirical reproduction

Three prod runs against `api.lawttorney.com/pyapi/chat`:

| Test | Bundle | First run | Second run |
|---|---|---|---|
| Avachat writ | no PDFs, text only | drafting defects (truncation, missing PRAYER) | n/a |
| Nisha appeal | 177 pages / 6.6 MB | **silent hang 1h44m** | succeeded 248s |
| Bail summary | 524 pages / 29 MB | **silent hang 600s, client aborted** | not attempted |

In every hang: server emitted `thread_id` + `file_processing: "Processing uploaded files..."` at t+2-9s, then went silent. Zero files persisted to `thread_files` SQLite. Prod box stayed healthy (no OOM, no restart) — the hang is in the request worker only.

---

## What V1 did that worked

V1 codebase: `C:\lawtech_backup\home\ubuntu\Lawtech-AI\`. Active routes: `routes/mainqa.py` (legacy) and `routes/mainqa_test.py` (current).

V1's file-handling architecture (from the research deep-map):

### 1. Split upload from compute — two endpoints

- `POST /pyapi/upload_validate` (`mainqa_test.py:157-276`) — accepts multipart files, runs `fitz.open()` just to count pages, enforces `MAX_SIZE = 300 MB` and `MAX_PAGES = 500`, moves files into `./temp_uploads/{uniqueString}/`, returns JSON immediately. **No extraction, no embeddings, no LLM calls.** User sees a fast response.
- `POST /pyapi/processing` (`mainqa_test.py:286-483`) — pure JSON body `{uniqueString}`, reads files from disk, runs extraction → chunking → Chroma write, deletes temp dir at end. Can be retried independently if it fails.

### 2. Compression before extraction

`utils/pdf_utils.py:7-80 compress_pdf` — tries PikePDF lossless, falls back to Ghostscript `-dPDFSETTINGS=/ebook` lossy, picks the smaller. This cuts byte count fed into PyMuPDF and Vision OCR by ~30-60%.

**V2 removed this step entirely.**

### 3. Lean Vision OCR

- 96 DPI (V2 uses 200 DPI — 4× the pixels per page)
- 20 pages per batch (V2 uses 10)
- `asyncio.Semaphore(10)` (V2 uses `ThreadPoolExecutor(4)`)
- `gemini-2.5-flash-lite`, `temperature=0`, `max_output_tokens=2048`
- Triggered only when `total_text < pages × 700` chars (cheap PyMuPDF-first check)

Net effect: V1 ran Vision OCR at roughly **5× V2's throughput** per page when it had to OCR.

### 4. Large chunks → few embedding calls

- `RecursiveCharacterTextSplitter(chunk_size=15000, chunk_overlap=200)`
- A 524-page PDF (~700 KB text) → ~50 chunks → ~50 embedding calls

**V2 uses chunk_size=1000 (and 800 in `vectordb_tools.py`).** Same PDF → ~750 chunks → ~750 embedding calls. **15× more embedding round-trips** to the embedding service. Each call has no timeout — service slowness compounds.

### 5. No Gemini Files API

V1 never uploaded PDFs to the Gemini Files API. Vision OCR worked off inline base64 PNG content blocks (stateless per call). No persistent file handles, no upload-then-reference dance.

**V2 mandates a Gemini Files upload** for every PDF in `_phase_2` (`file_processor.py:1094-1121`). This is a new failure mode V1 didn't have.

### 6. No background tasks spawned from the request

V1's `mainqa_test.py` has zero `asyncio.create_task`, zero Celery, zero subprocess job queues in the live path. The request is async but linear.

**V2 spawns `asyncio.create_task(_background_ocr_and_store(...))`** at `file_processor.py:1241` for scanned PDFs over 20 pages. The task is orphaned — not awaited, not tracked, not bounded.

### 7. Per-file failure isolation

V1's processing loop wraps each file in `try/except` and `continue`s on failure (`mainqa_test.py:358-361`). One bad PDF doesn't kill the whole upload.

**V2 has selective try/except** but a stalled `upload_to_gemini` in `Phase 2`'s `asyncio.gather` takes down the entire request.

---

## What V2 did differently — and where it hangs

The V2 file processing pipeline in `core/file_processor.py`:

```
process_files(file_tuples, thread_id)
  ├── Phase 1: prepare per-file metadata (page count, mime, size cap)
  ├── Phase 2: PARALLEL Gemini Files API upload      ← stall point #1
  │   asyncio.gather(upload_to_gemini × N)            no per-upload timeout
  ├── Phase 3: per-file processing loop (sequential)
  │   ├── _extract_pdf_text_per_page  (PyMuPDF)      ← stall point #2 on damaged PDF
  │   ├── _score_text_quality on all pages           ← slow loop, no timeout
  │   ├── _is_garbled_by_script + _detect_garbled    ← may force Vision OCR
  │   ├── _render_pdf_pages @ 200 DPI                ← stall point #3 (524 pages of JPEGs)
  │   ├── _vision_ocr_pdf (timeout = 900s)           ← bounded
  │   └── _store_in_chromadb                         ← stall point #4 (no timeout)
  │       ├── chunk(1000/200)
  │       ├── embed via EMBEDDING_SERVICE_URL        no timeout
  │       └── chroma.add_documents                   no timeout
  └── save_thread_file → SQLite                       only reached if all above complete
```

### V2's timeout gaps (the smoking gun)

V2 has **selective timeouts** but missed these:

| Sub-task | V2 timeout | V1 equivalent |
|---|---|---|
| `upload_to_gemini` per file | **none** | doesn't exist in V1 |
| `_extract_pdf_text_per_page` (PyMuPDF) | **none** | none, but bounded by compressed input |
| `_render_pdf_pages` @ 200 DPI for OCR | **none** | 96 DPI, smaller bytes |
| `_score_text_quality` per page | **none** | doesn't exist in V1 |
| `_store_in_chromadb` embed + write | **none** | none, but 15× fewer chunks |
| Garble-triggered Vision OCR | 900 s | n/a (Vision triggered differently) |
| Scanned-PDF Vision OCR | 900 s | none, but 96 DPI keeps it fast |
| Image OCR (jpg/png/webp) | 120 s | n/a |
| Per-OCR batch future | 120 s | n/a |
| `chat_store.save_thread_file` SQLite | **none** | doesn't exist (no SQLite persistence in V1) |

For the **29 MB / 524 page** workload that hung: the most likely smoking guns in order of probability:

1. **Gemini Files API upload of two 14-15 MB PDFs in parallel** — `gather(upload_to_gemini × 2)` with no timeout. The `google-generativeai` SDK uses resumable upload for files over 10 MB; if any HTTP turn stalls without raising, the thread blocks indefinitely. `return_exceptions=True` only catches *raised* exceptions.
2. **`_render_pdf_pages` at 200 DPI for 524 pages** — single `asyncio.to_thread` blob, no timeout, blocks an event-loop thread-pool slot for minutes.
3. **`_store_in_chromadb` writing ~750 chunks** via a remote embedding service that lacks per-call timeout. If the service is slow or unreachable, this stalls silently.

V1 never hit any of these because:
- It didn't use Gemini Files API.
- It rendered at 96 DPI (16% of V2's pixel count).
- It wrote ~50 chunks instead of ~750.
- It compressed first.

---

## Root cause statement

**V2 traded reliability for higher-fidelity OCR and unified multimodal input.** Each individual choice (200 DPI, 1 000-char chunks, Gemini Files API, no compression) made sense in isolation for short documents. Together, they multiply against large evidence bundles and produce silent hangs because the harness wrapping them lacks the timeouts and progress signals V1 implicitly relied on (V1's lean payload kept every stage fast enough that timeouts were not needed).

This is not a single bug; it is **five architectural decisions** that together regress against the V1 baseline on large inputs.

---

## Recommended fix path (sized to ship in 2-3 days)

Land these **before** the larger 6-phase drafting reshape. They unbreak today's user-blocking regression.

### Emergency Phase 0 — restore V1's working pattern in V2's shape

1. **Add PDF compression** ([`tools/shared/document_tools.py:compress_pdf`](../tools/shared/document_tools.py) — port [`utils/pdf_utils.py:7-80`](C:/lawtech_backup/home/ubuntu/Lawtech-AI/utils/pdf_utils.py) from V1). Run before extraction. PikePDF lossless → Ghostscript fallback → keep smaller.

2. **Drop Vision DPI to 96** ([`core/file_processor.py:51`](../core/file_processor.py#L51)) — `VISION_DPI = 96` instead of 200. V1 ran at 96 for the full lifetime of the product and quality was acceptable for legal documents. Or make it adaptive: 200 only when `_score_text_quality` confirms genuine OCR-grade poor text.

3. **Raise chunk size to 8 000 or 15 000** ([`core/file_processor.py:785-789`](../core/file_processor.py#L785-L789) and [`tools/shared/vectordb_tools.py:37-41`](../tools/shared/vectordb_tools.py#L37-L41)). Match V1's 15 000/200. Reduces embedding service load by 15×. Larger chunks reduce per-chunk semantic precision slightly but legal documents are coherent paragraphs — large chunks work.

4. **Wrap every `process_files` sub-task in `asyncio.wait_for`** with appropriate budgets:
   - `upload_to_gemini`: 180s per file (allows for slow Indian → US upload of 15 MB)
   - `_extract_pdf_text_per_page`: 90s
   - `_render_pdf_pages`: 180s (for OCR path)
   - `_store_in_chromadb`: 120s
   - `save_thread_file` (SQLite): 30s
   Each timeout → log + skip the file with a clear `file_processing` rejection event. No silent failure.

5. **Emit `file_processing` heartbeats every 5-10 seconds**. The client today sees one event at t+8s then silence. Add per-stage events: "Compressing Vol-1...", "Extracting text from Vol-1 (page 145/250)...", "Embedding chunks (12/45)...", "Saving to vector store...". User sees progress instead of dead silence.

6. **Per-file failure isolation in Phase 2 + Phase 3** — if Vol 1's Gemini upload stalls and times out, drop it and proceed with Vol 2. Today a stalled gather() blocks both.

### What this does NOT fix

- The orchestrator routing Document agent unnecessarily for pure Drafting tasks (60s wasted Gemini Pro call) — that's Phase 2 of the bigger plan.
- The `add_statute_references` 58s silent block — that's a separate fix tied to Phase 5 (structural validator).
- The drafting quality defects (Avachat truncation, missing Issue 2, broken numbering) — those are Phase 1 + Phase 2 of the 6-phase plan.

### Optional: split upload from compute (V1's two-endpoint pattern)

Larger change but matches V1's proven shape. `POST /pyapi/chat/upload` returns immediately with `{thread_id, file_status: queued}`. Client polls `GET /pyapi/chat/upload_status/{thread_id}` or subscribes to a separate SSE stream. Then `POST /pyapi/chat` with `{thread_id, query}` runs only the LLM pipeline. Decouples 4-minute file processing from the 30-second LLM call.

This is a larger refactor (touches gateway routing + frontend) but eliminates a whole class of user-visible coupling.

---

## Open questions for user

1. **Approve emergency Phase 0** (compression + DPI drop + chunk size + timeouts + heartbeats + isolation) as a 2-3 day standalone PR, separate from the 6-phase plan? It unbreaks the bail-summary class of failure today.
2. **Are we willing to drop OCR DPI to 96**? V1 ran at 96 for years and produced acceptable output. V2 raised it to 200 for sharper Indic-script OCR. If we drop back to 96, Indic-script-only PDFs may lose some accuracy.
3. **Are we willing to use 15 000-char chunks for evidence PDFs**? Larger chunks reduce per-retrieval precision but cut embedding calls by 15×. The retrieval quality impact for legal documents needs an eval — V1's evidence shows it works in practice.
4. **Should we revisit the Gemini Files API decision?** V2's multimodal Gemini Pro reads PDFs directly via file URIs (the `qa_gemini_files` path in `agents/document.py`) — this is genuinely better quality than V1's image_url base64 approach. But the upload step is the #1 hang source. Options: (a) keep, with strict timeout; (b) make it optional (only for small PDFs); (c) remove entirely and use the inline image_url path V1 used.
