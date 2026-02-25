# Lawttorney v2 — Known Issues & Tech Debt

> Generated: 2026-02-25 | Based on full codebase audit of v2.4.1 | Last updated: 2026-02-26 (v2.4.5)
>
> Items are grouped by severity. Each entry links to the relevant file and describes the exact problem and recommended fix.

---

## Critical — Security

### C1. No Authentication or Authorization
**File:** `core/gateway.py`
**Problem:** Any HTTP caller who knows a user's `unique_string` can query or delete their entire PDF collection. There is no user identity check, token validation, or session verification on any endpoint.
**Fix:** Add JWT middleware or API-key validation. Bind `unique_string` to authenticated user identity server-side.

---

### ~~C2. Elasticsearch Query Not Sanitized~~ ✅ FIXED (v2.4.2)
**File:** `tools/shared/elasticsearch_tools.py`
**Fix applied:** Added `_sanitize_es_input()` (Unicode NFC, control-char strip, 500-char truncation, Lucene metachar escaping) applied at every `@tool` entry point. Added `_sanitize_section_number()` for section number lists. Added `_validate_index_name()` whitelist check in `get_docs_by_source` to block index-name injection — the highest-risk vector.

---

### ~~C3. Shell Injection in PDF Compression~~ ✅ FIXED (v2.4.3)
**File:** `tools/shared/document_tools.py`
**Fix applied:** Added `_is_safe_pdf_path()` helper that uses `os.path.realpath()` to resolve the path and checks it falls within `tempfile.gettempdir()`, `CHROMA_STORE_ROOT`, or `cwd`. Also enforces `.pdf` extension. Validation applied at the entry of all four file-path tools: `validate_pdf`, `extract_text_pymupdf`, `extract_text_vision`, `compress_pdf`. Subprocess command was already a list (no shell=True), so shell metacharacter injection was already mitigated; path traversal is now also closed.

---

### ~~C4. Section Range Expansion Unbounded~~ ✅ FIXED (v2.4.2)
**File:** `tools/inline/section_parser.py`
**Fix applied:** `parse_multi_section_info()` range expansion capped at 20 with `min(end, start + _RANGE_CAP - 1)`. Large-range strategy added in `agents/newacts.py` — queries with >20 sections switch to BM25 topic/overview search instead of per-section retrieval.

---

### ~~C5. Injection Regex Patterns Bypassable~~ ✅ FIXED (v2.4.3)
**File:** `tools/shared/guardrail_tools.py`
**Fix applied:** Added `_normalize_for_injection_check()` that applies NFKC Unicode normalization (collapsing homoglyphs like `ⅈ→i`, `Ａ→A`) and strips zero-width characters (U+200B–U+200F, U+2060–U+2064, U+FEFF). Normalization applied in both `detect_injection_regex()` and the `has_suspicious` keyword pre-filter in `detect_injection_llm()`.

---

### ~~C6. IFSC Code Regex Incorrect~~ ✅ ALREADY CORRECT
**File:** `tools/shared/guardrail_tools.py`
**Note:** Audit was a false positive — the current regex `r'\b[A-Z]{4}0[A-Z0-9]{6}\b'` correctly matches the IFSC format (4 letters + literal `0` + 6 alphanumeric = 11 chars). No change needed.

---

## Critical — Reliability

### C7. LLM Calls Without try/except — Crash Risk
**Files:** `agents/orchestrator.py`, `agents/judgment.py`, `agents/newacts.py`
**Problem:** Several LLM invocations (task classification, GPT-4o metadata extraction) are not wrapped in `try/except`. A single API timeout, rate-limit error, or malformed response crashes the entire request with an unhandled exception.
**Fix:** Wrap all `llm.invoke()` / `llm.ainvoke()` calls in `try/except Exception`. On failure, use regex fallback (already implemented in some agents) or return a partial result with `error` field set.
**Fix applied (v2.4.4):** `_judgment_regex_fallback()` added to judgment.py (extracts party names, year, court, topics via regex without LLM). `_extract_case_metadata()` now uses `asyncio.to_thread` + `asyncio.wait_for(timeout=20)`, catching both `asyncio.TimeoutError` and `Exception`. Same upgrade applied to newacts `_extract_act_metadata()`.

---

### ~~C8. No Request Timeouts on LLM or ES Calls~~ ✅ FIXED (v2.4.4)
**Files:** `core/streaming.py`, `agents/orchestrator.py`, `agents/judgment.py`, `agents/newacts.py`
**Fix applied:**
- `core/streaming.py`: `stream_chain_response()` wraps streaming calls with `asyncio.wait_for(timeout=90)`, batch calls with `asyncio.wait_for(timeout=60)`. `asyncio.TimeoutError` is re-raised to the calling agent.
- `agents/orchestrator.py`: `asyncio.to_thread(_classify_task)` wrapped with `asyncio.wait_for(timeout=30)`, fallback to `_classify_task_regex_fallback()`. `asyncio.to_thread(_plan_agents)` wrapped with `asyncio.wait_for(timeout=25)`, fallback to single-agent plan.
- `agents/judgment.py`: Metadata extraction moved from blocking call to `asyncio.to_thread + wait_for(timeout=20)`, fallback to `_judgment_regex_fallback()`.
- `agents/newacts.py`: Same upgrade — `asyncio.to_thread + wait_for(timeout=20)` with existing regex fallback.
- ES client: Already had `request_timeout=30` in `get_es_client()` — no change needed.

---

### C9. In-Memory PDF Job Store — Lost on Restart
**File:** `workers/pdf_processor.py`
**Problem:** Background PDF processing jobs are stored in a Python dict (`_jobs`) in memory. All in-progress and completed job records are lost when the server restarts. Users who uploaded PDFs cannot check their job status after a restart.
**Fix:** Replace with Redis (`aioredis`) or a SQLite jobs table. Persist `job_id`, `status`, `progress`, `error` fields with proper TTL/cleanup.

---

### ~~C10. Non-Thread-Safe ChromaDB Initialization~~ ✅ ALREADY CORRECT
**File:** `agents/constitution_maxim.py`
**Note:** Audit was a false positive — `_vectordbs[task] = Chroma(...)` is stored **inside** the `with _vectordb_lock:` block. The outer check-before-lock is the correct double-checked locking pattern for performance. No change needed.

---

### C11. PDF Chat History Full-File Rewrite on Append
**File:** `tools/shared/storage_tools.py`
**Problem:** `save_pdf_chat_history()` reads the entire JSON file, appends the new turn, and writes back the whole file. If the process crashes mid-write, the file is truncated and all history is lost.
**Fix:** Append to a JSONL (newline-delimited JSON) file instead of rewriting a JSON array. Use `open(path, 'a')` and write one record per line.

---

## High — Correctness

### ~~H1. `SearchRequest.Promptquery` — Inconsistent Field Name~~ ✅ FIXED (v2.4.5)
**File:** `core/gateway.py`
**Fix applied:** Field renamed to `prompt_query: str = Field(..., alias="Promptquery", ...)` with `model_config = ConfigDict(populate_by_name=True)`. Existing clients sending `Promptquery` continue to work unchanged. All 8 internal `data.Promptquery` references updated to `data.prompt_query`.

---

### ~~H2. S3 PDF Links Generated Without Existence Check~~ ✅ FIXED (v2.4.5)
**File:** `tools/shared/storage_tools.py`
**Fix applied:** Added `_s3_key_exists(bucket, key)` — a `@lru_cache(maxsize=2048)` helper that calls `boto3.client("s3").head_object()`. Returns `False` only on confirmed `404`/`NoSuchKey`; falls through to `True` on `403`, missing credentials, or network errors. `generate_s3_link()` now returns `None` for missing objects (prevents dead links). Results are memoized per process so each S3 key is checked at most once. Added `boto3>=1.26.0` to `requirements.txt`.

---

### ~~H3. `search_by_semantic` Uses BM25, Not Semantic Search~~ ✅ FIXED (v2.4.5)
**File:** `tools/shared/sci_judgment_tools.py`
**Fix applied:** Renamed function `search_by_semantic` → `search_by_topic`. Updated docstring to say "BM25 keyword matching" (was misleadingly called "intelligent text matching"). Updated import and both tool lists (`ALL_TOOLS`, `AGENT_TOOLS["sci_judgment"]`) in `tools/shared/__init__.py`.

---

### ~~H4. Year Filter Applied in Python After ES Fetch~~ ✅ ALREADY CORRECT
**File:** `agents/judgment.py`, `tools/shared/judgment_search.py`
**Note:** Audit was a false positive. Year is applied at ES query time via `{"term": {"year": year}}` inside `bool.filter` clauses in all strategies that accept a year parameter (party name exact/swapped/fuzzy, single party, case type, and multi-tier). No Python-side post-filtering of year. No change needed.

---

### ~~H5. Duplicate PDF Chunks on Re-Upload~~ ✅ FIXED (v2.4.5)
**File:** `core/gateway.py` (mainqa upload handler)
**Fix applied:** After loading `existing_filenames` from ChromaDB collection metadata, filter `texts` to only chunks whose `metadata["source"]` is not already in `existing_set`. Only calls `vectordb.add_documents(new_texts)` when there are new chunks. Logs skip events with counts. Existing file list in metadata stays unchanged so users see all their files.

---

### ~~H6. Blocking `requests.get` Inside Async Functions~~ ✅ FIXED (v2.4.5)
**Files:** `agents/memory.py`
**Fix applied:** Two blocking sync calls in `memory_node` (async) wrapped with `asyncio.to_thread()`:
1. `_load_from_legacy_api(thread_id)` → `await asyncio.to_thread(_load_from_legacy_api, thread_id)` (already had `timeout=10` in the underlying `requests.get`)
2. `_rewrite_query(query, chat_history)` → `await asyncio.to_thread(_rewrite_query, query, chat_history)` (wraps the `chain.invoke()` call)
Added `import asyncio` to memory.py.

---

## Medium — Code Quality & Tech Debt

### M1. Hardcoded Production IP in Source Code
**File:** `core/settings.py`
**Problem:** The Elasticsearch URL fallback is `http://139.84.219.174:9200` — a real production server IP embedded in the source code. This exposes infrastructure details and makes it harder to manage different environments (dev/staging/prod).
**Fix:** Remove the hardcoded IP fallback. Require `ELASTICSEARCH_URL` to be set in `.env`. Fail fast at startup if missing.

---

### M2. Query Rewrite Prompts Not Centralized
**File:** `core/agent_fallback.py`
**Problem:** Domain-specific query rewrite prompts (for Newacts, Legislation, Judgment, etc.) are defined as inline strings inside `rewrite_query_for_domain()` instead of being imported from `config/prompts.py`. This makes prompt management inconsistent.
**Fix:** Move all rewrite prompts to `config/prompts.py` under a `REWRITE_PROMPTS` dict keyed by agent name.

---

### M3. Prompts Not Versioned
**File:** `config/prompts.py`
**Problem:** Prompt changes are not versioned or tracked beyond git commits. There is no mechanism to A/B test prompt variants, roll back a prompt change, or measure the impact of a prompt edit on response quality.
**Fix:** Add a `PROMPT_VERSION` constant to each prompt. Log the prompt version alongside each request in the structured log. Consider storing prompts in a config file (YAML/TOML) separate from Python code.

---

### M4. Drafting Sections Generated Sequentially
**File:** `agents/drafting.py`
**Problem:** The multi-section drafting pipeline generates each document section in a sequential `for` loop with `await` calls. A 6-section document makes 6 serial LLM calls when they could run in parallel.
**Fix:** Replace the sequential loop with `asyncio.gather(*[generate_section(s) for s in sections])` to run all section generation calls concurrently.

---

### M5. BM25 Retriever Recreated Per Query
**File:** `agents/constitution_maxim.py`
**Problem:** A new `BM25Retriever` is instantiated from the full document corpus on every query. BM25 index construction is O(n) in the number of documents and adds unnecessary latency on every retrieval.
**Fix:** Cache the `BM25Retriever` instance alongside the ChromaDB vectorstore in `_vectordbs`. Invalidate cache only when the underlying data changes.

---

### M6. Top-Source Selection Uses Document Count, Not Relevance Score
**Files:** `agents/legislation.py`, `agents/judgment.py`, `agents/newacts.py`
**Problem:** The "best source" (act name or case) is selected using `Counter(hit["_source"].get("source"))` — whichever source appears most often in ES results. This ignores relevance scores, so a low-scoring but frequently appearing source beats a high-scoring relevant source.
**Fix:** Weight source selection by ES `_score`. Sum scores per source instead of counting occurrences. Select the source with the highest cumulative score.

---

### M7. No Version Pinning in requirements.txt
**File:** `requirements.txt`
**Problem:** All dependencies use `>=X.Y.Z` bounds with no upper cap. A `pip install` six months from now may install breaking versions of `langchain`, `langgraph`, `chromadb`, or `elasticsearch`.
**Fix:** Pin all production dependencies to exact versions (or use `~=X.Y.Z` for patch-level flexibility). Generate a `requirements-lock.txt` from `pip freeze` after a working install.

---

### M8. Rate Limit is IP-Based and Spoofable
**File:** `core/gateway.py`
**Problem:** `slowapi` rate limiting uses `get_remote_address()` which reads `X-Forwarded-For` or `REMOTE_ADDR`. Behind a proxy or load balancer, all requests may share one IP. An attacker can also spoof `X-Forwarded-For`.
**Fix:** Rate-limit by authenticated user ID (once auth is added). Until then, use `X-Real-IP` with proxy IP whitelisting, and set a lower rate limit as a safety floor.

---

### M9. CORS Open to All Origins
**File:** `core/gateway.py`
**Problem:** `allow_origins=["*"]` allows any website to make cross-origin requests to the API. In a production environment with sensitive legal data, this should be restricted to known frontend domains.
**Fix:** Set `allow_origins` to an explicit list from an environment variable (e.g., `CORS_ORIGINS=https://lawttorney.ai,https://app.lawttorney.ai`).

---

### M10. SQLite Chat Store Won't Scale Horizontally
**File:** `core/chat_store.py`
**Problem:** Chat history is stored in a local SQLite file (`data/chat_history.db`). WAL mode allows concurrent reads, but a second server instance (for horizontal scaling or rolling restart) cannot access the same SQLite file safely over a network share.
**Fix:** For multi-instance deployments, migrate to PostgreSQL (via `asyncpg`) or add a Redis-backed session cache as a write-through layer.

---

### M11. Synthesis Does Not Validate Agent Result Format
**File:** `agents/orchestrator.py`
**Problem:** `orchestrator_synthesize_node()` iterates over `agent_results` assuming each value is a valid `AgentResult` dataclass. If a parallel agent returns a malformed dict or raises an unhandled exception that is caught upstream, synthesis will fail with an `AttributeError`.
**Fix:** Add input validation at synthesis entry: check each value in `agent_results` has `.content` and `.sources` attributes before merging. Skip malformed entries with a log warning.

---

### M12. Legacy API Compatibility Layer Is Active by Default
**File:** `core/chat_store.py`, `agents/memory.py`
**Problem:** `CHAT_HISTORY_USE_LEGACY_API=true` is the default. This means every new conversation first tries to load history from the external `https://lawttorney.ai/api` endpoint before falling back to SQLite. This adds latency and a hard dependency on an external service for every request.
**Fix:** Default `CHAT_HISTORY_USE_LEGACY_API=false`. Only enable it explicitly for users with legacy thread IDs that need migration.

---

### M13. No Word or Length Limit on Generated Drafts
**File:** `agents/drafting.py`
**Problem:** There is no cap on how many sections an outline can contain or how long each section can be. A complex drafting request could trigger 10+ LLM calls and return a 50,000-word document, consuming excessive tokens and time.
**Fix:** Cap `estimated_sections` in `DraftOutline` at 8. Add a `max_tokens` parameter to each section generation call (e.g., 1500 tokens per section).

---

### M14. `get_web_context()` Timeout Not Enforced
**File:** `core/agent_fallback.py`
**Problem:** `get_web_context()` runs inside `asyncio.to_thread()` but has no `asyncio.wait_for()` wrapper. If the Gemini model is slow, this can delay the response without a bound.
**Fix:** Wrap the `asyncio.to_thread(...)` call with `asyncio.wait_for(..., timeout=20)`.

---

### M15. Stale Vectorstore Cache Not Invalidated
**File:** `agents/constitution_maxim.py`, `tools/shared/vectordb_tools.py`
**Problem:** ChromaDB vectorstore handles are cached globally in `_vectordbs`. If the underlying ChromaDB collection is updated (new documents added, old ones deleted), the cached handle still returns the old data until the server restarts.
**Fix:** Add a TTL to the vectorstore cache (e.g., 1 hour). Invalidate on document upload/delete events. Or expose a `POST /pyapi/admin/reload-vectordb` endpoint.

---

## Low — Minor / Housekeeping

### L1. Abbreviation Expansion Applied to All Queries
**File:** `tools/inline/abbreviation.py`
**Problem:** Legal abbreviation expansion runs on every query, even those without abbreviations. Adds overhead for the majority of queries that don't need it.
**Fix:** Add a fast pre-check: only run expansion if the query contains any token matching a known abbreviation key (use a `set` lookup).

---

### ~~L2. `save_chat_history` Tool Is Blocking — No Timeout~~ ✅ ALREADY CORRECT
**File:** `tools/shared/memory_tools.py`
**Note:** Audit was a false positive. `save_chat_history()` now writes to the local SQLite store via `chat_store._save_turn_sync()` — there is no external HTTP call at all. No change needed.

---

### L3. Embedding Service Has No Authentication
**File:** `services/embedding_service.py`
**Problem:** The embedding microservice (`/embed` endpoint) is exposed with no authentication. Anyone who can reach the port can request arbitrary text to be embedded, burning compute and potentially extracting information about the corpus.
**Fix:** Add a shared secret header (`X-Embed-Token`) checked against an env variable. Only accept connections from `127.0.0.1` if running on the same host.

---

### L4. Embedding Service Lazy-Loads on First Request
**File:** `services/embedding_service.py`
**Problem:** BGE-large-en-v1.5 (~1.3GB) is loaded on the first `/embed` request, not at startup. The first request after service start takes ~30 seconds while the model loads, causing a timeout.
**Fix:** Load the model at service startup (in the FastAPI `lifespan` event or `@app.on_event("startup")`). The health endpoint should return `{"status": "loading"}` until ready.

---

### L5. No Duplicate Detection for ES Results
**File:** `tools/shared/elasticsearch_tools.py`
**Problem:** When multiple search strategies run (multi-query variations), the same document can appear multiple times in the result set. The LLM receives duplicate content, wasting tokens and degrading coherence.
**Fix:** Deduplicate hits by `_id` after aggregating results from all query variations. Keep the highest-scoring occurrence of each document.

---

### ~~L6. `pikepdf` Not Listed in requirements.txt~~ ✅ ALREADY CORRECT
**File:** `requirements.txt`
**Note:** Audit was a false positive. `pikepdf>=9.0.0` is already present in `requirements.txt`. No change needed.

---

### L7. No Dev/Test Dependencies Documented
**File:** `requirements.txt`
**Problem:** Testing tools (`pytest`, `pytest-asyncio`, `httpx` for TestClient), linting (`ruff`, `mypy`), and formatting tools are not listed anywhere. New developers cannot set up a complete dev environment from `requirements.txt` alone.
**Fix:** Create `requirements-dev.txt` with dev-only dependencies: `pytest>=8.0`, `pytest-asyncio>=0.24`, `httpx>=0.27`, `ruff>=0.4`, `mypy>=1.9`.

---

### L8. Hardcoded Thresholds Throughout
**Files:** Multiple
**Problem:** Multiple magic numbers are embedded in business logic with no documentation or configuration:
- `score > 5.0` — ES "strong match" threshold (`elasticsearch_tools.py`)
- `700` chars/page — OCR fallback trigger (`document_tools.py`, `document.py`)
- `100` words — "long query → Scenario" threshold (`orchestrator.py`)
- `200` chars — sorry-pattern detection window (now 300, still hardcoded)
- `0.5 / 0.5` — BM25/Chroma ensemble weights (`vectordb_tools.py`)
- `k=10`, `fetch_k=50` — ChromaDB retrieval parameters (hardcoded)

**Fix:** Move all thresholds to `core/settings.py` with descriptive names and environment variable overrides.

---

## Summary Table

| ID | Severity | Area | File |
|----|----------|------|------|
| C1 | 🔴 Critical | Security — No Auth | `core/gateway.py` |
| ~~C2~~ | ✅ Fixed (v2.4.2) | Security — ES Injection | `tools/shared/elasticsearch_tools.py` |
| ~~C3~~ | ✅ Fixed (v2.4.3) | Security — Shell Injection / Path Traversal | `tools/shared/document_tools.py` |
| ~~C4~~ | ✅ Fixed (v2.4.2) | Security — DoS via Section Range | `tools/inline/section_parser.py` |
| ~~C5~~ | ✅ Fixed (v2.4.3) | Security — Injection Unicode Bypass | `tools/shared/guardrail_tools.py` |
| ~~C6~~ | ✅ Already Correct | Security — PII Regex | `tools/shared/guardrail_tools.py` |
| ~~C7~~ | ✅ Fixed (v2.4.4) | Reliability — LLM Crash | `agents/orchestrator.py`, `agents/judgment.py` |
| ~~C8~~ | ✅ Fixed (v2.4.4) | Reliability — No Timeouts | All agents |
| C9 | 🔴 Critical | Reliability — Jobs Lost on Restart | `workers/pdf_processor.py` |
| ~~C10~~ | ✅ Already Correct | Reliability — Race Condition | `agents/constitution_maxim.py` |
| C11 | 🔴 Critical | Reliability — Data Loss on Append | `tools/shared/storage_tools.py` |
| ~~H1~~ | ✅ Fixed (v2.4.5) | Correctness — Bad Field Name | `core/gateway.py` |
| ~~H2~~ | ✅ Fixed (v2.4.5) | Correctness — Dead S3 Links | `tools/shared/storage_tools.py` |
| ~~H3~~ | ✅ Fixed (v2.4.5) | Correctness — Misleading Tool Name | `tools/shared/sci_judgment_tools.py` |
| ~~H4~~ | ✅ Already Correct | Correctness — Year Filter | `agents/judgment.py` |
| ~~H5~~ | ✅ Fixed (v2.4.5) | Correctness — Duplicate Chunks | `core/gateway.py` |
| ~~H6~~ | ✅ Fixed (v2.4.5) | Correctness — Blocking Async | `agents/memory.py` |
| M1 | 🟡 Medium | Tech Debt — Hardcoded IP | `core/settings.py` |
| M2 | 🟡 Medium | Tech Debt — Inline Prompts | `core/agent_fallback.py` |
| M3 | 🟡 Medium | Tech Debt — No Prompt Versioning | `config/prompts.py` |
| M4 | 🟡 Medium | Performance — Sequential Drafting | `agents/drafting.py` |
| M5 | 🟡 Medium | Performance — BM25 Not Cached | `agents/constitution_maxim.py` |
| M6 | 🟡 Medium | Correctness — Score vs Count | Multiple agents |
| M7 | 🟡 Medium | Ops — No Dep Pinning | `requirements.txt` |
| M8 | 🟡 Medium | Security — Spoofable Rate Limit | `core/gateway.py` |
| M9 | 🟡 Medium | Security — Open CORS | `core/gateway.py` |
| M10 | 🟡 Medium | Scalability — SQLite | `core/chat_store.py` |
| M11 | 🟡 Medium | Reliability — No Result Validation | `agents/orchestrator.py` |
| M12 | 🟡 Medium | Tech Debt — Legacy API Default On | `core/chat_store.py` |
| M13 | 🟡 Medium | Reliability — Unbounded Drafts | `agents/drafting.py` |
| M14 | 🟡 Medium | Reliability — No Timeout | `core/agent_fallback.py` |
| M15 | 🟡 Medium | Reliability — Stale Cache | `agents/constitution_maxim.py` |
| L1 | 🔵 Low | Performance — Abbrev Expansion | `tools/inline/abbreviation.py` |
| ~~L2~~ | ✅ Already Correct | Reliability — Blocking Save | `tools/shared/memory_tools.py` |
| L3 | 🔵 Low | Security — Embed No Auth | `services/embedding_service.py` |
| L4 | 🔵 Low | Ops — Slow First Request | `services/embedding_service.py` |
| L5 | 🔵 Low | Correctness — ES Duplicates | `tools/shared/elasticsearch_tools.py` |
| ~~L6~~ | ✅ Already Correct | Ops — Missing Dep | `requirements.txt` |
| L7 | 🔵 Low | Ops — No Dev Deps | `requirements.txt` |
| L8 | 🔵 Low | Maintainability — Magic Numbers | Multiple files |

---

**Total Issues: 37** (18 resolved: C2–C8,C10 + H1–H6 + L2,L6 | 19 open: C1,C9,C11 + M1–M15 + L1,L3–L5,L7–L8)
Critical open: 3 (C1,C9,C11) | Medium open: 15 | Low open: 6
