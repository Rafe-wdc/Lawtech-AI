# Lawtech-AI — Complete System Map & Pipeline Reference

> Generated: 2026-03-11 | Branch: master | Version: v2.4.6

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [API Endpoints](#2-api-endpoints)
3. [LangGraph Topology](#3-langgraph-topology)
4. [State Schema](#4-state-schema)
5. [Per-Agent Internal Flow](#5-per-agent-internal-flow)
6. [Elasticsearch Queries by Index](#6-elasticsearch-queries-by-index)
7. [Drafting Pipeline (Multi-Step)](#7-drafting-pipeline-multi-step)
8. [File Processing Pipeline](#8-file-processing-pipeline)
9. [Chat Memory Pipeline](#9-chat-memory-pipeline)
10. [SSE Streaming Events](#10-sse-streaming-events)
11. [3-Tier Fallback Chain](#11-3-tier-fallback-chain)
12. [Model Usage Map](#12-model-usage-map)
13. [External Dependencies](#13-external-dependencies)
14. [Error Handling Paths](#14-error-handling-paths)
15. [Database Schema](#15-database-schema)

---

## 1. Architecture Overview

```
Client (Browser / API)
        │
        ▼
  FastAPI Gateway (core/gateway.py)
  ├── Rate Limiting (slowapi, 10 req/min)
  ├── File handling (multipart → process_files())
  ├── SSE streaming (StreamingResponse)
  └── SQLite I/O (chat_store)
        │
        ▼
  LangGraph Graph (core/graph.py)
  ├── 6 infrastructure nodes
  ├── 10 domain agent nodes (parallel via Send API)
  └── Custom state reducers (merge, sum, extend)
        │
        ▼
  External Services
  ├── Elasticsearch (primary retrieval)
  ├── Google Gemini (generation + search grounding + files API)
  ├── OpenAI GPT-4o (classification + planning)
  ├── AWS S3 (PDF links)
  ├── ChromaDB (vector store for uploaded PDFs)
  └── SQLite (chat history + file registry)
```

---

## 2. API Endpoints

All endpoints prefixed with `/pyapi`.

| Method | Endpoint | Purpose | Response Type |
|--------|----------|---------|---------------|
| POST | `/search` | Batch legal Q&A | JSON (full at once) |
| POST | `/search/stream` | Streaming legal Q&A | Server-Sent Events |
| POST | `/mainqa` | PDF upload + Q&A (sync) | JSON |
| POST | `/upload_async` | Background PDF upload | JSON (job_id) |
| GET | `/job_status/{job_id}` | Poll async upload status | JSON |
| POST | `/continue_draft` | Resume incomplete draft | SSE events |
| DELETE | `/delete_vectordb/{unique_string}` | Delete PDF collection | JSON |
| GET | `/threads` | Session list for sidebar | JSON |
| GET | `/threads/{thread_id}/messages` | Restore session history | JSON |
| POST | `/feedback` | Submit rating (up/down) | JSON |
| GET | `/health` | Health check | JSON |
| GET | `/` | Frontend UI | HTML |

### Request / Response Schemas

**POST /search and /search/stream**
```
Request:
  query         string (required, max 5000 chars)
  globalThreadId string (optional, UUID)
  files[]       multipart file (optional, max 30 files, 1 GB each)

Response (batch):
  final_response  string
  sources         list[SourceMetadata]
  tokens          int
  agents_used     list[string]
  thread_id       string
  conversation_turn int

Response (stream): SSE events — see Section 10
```

**POST /mainqa**
```
Request (multipart):
  file            file (PDF only)
  query           string
  unique_string   string (collection ID)

Response:
  answer    string
  tokens    int
  sources   list[dict]
```

---

## 3. LangGraph Topology

### Graph Flow

```
START
  │
  ▼
guardrail_input
  │
  ├──[is_blocked = True]──► blocked_response ──► guardrail_output ──► END
  │
  └──[is_blocked = False]──►
                            │
                            ▼
                          memory
                            │
                            ▼
                      orchestrator_plan
                            │
                            ├──[Non_legal + no files]──► blocked_response ──► guardrail_output ──► END
                            │
                            └──[tasks_planned]──► Send API (parallel fan-out)
                                                    │
                                    ┌───────────────┼───────────────────┐
                                    │               │                   │
                                    ▼               ▼                   ▼
                               legislation      judgment           sci_judgment
                               newacts          drafting           scenario
                               constitution     maxim              legal_concepts
                               document
                                    │               │                   │
                                    └───────────────┴───────────────────┘
                                                    │ (results merged via reducers)
                                                    ▼
                                         orchestrator_synthesize
                                                    │
                                                    ▼
                                            guardrail_output
                                                    │
                                                    ▼
                                                  END
```

### Conditional Edge Logic

**`route_after_guardrail(state)`**
- `state["is_blocked"] == True` → `blocked_response`
- `state["is_blocked"] == False` → `memory`

**`route_after_orchestrator(state)`** — uses LangGraph Send API
```python
tasks = state["tasks_planned"]  # e.g., ["Judgment", "Legislation"]
AGENT_NODE_MAP = {
    "Legislation":     "legislation",
    "Judgment":        "judgment",
    "SCI_Judgment":    "sci_judgment",
    "Newacts":         "newacts",
    "Drafting":        "drafting",
    "Scenario":        "scenario",
    "Constitution":    "constitution",
    "Maxim":           "maxim",
    "Legal_Concepts":  "legal_concepts",
    "Document":        "document",
    "Other":           "scenario",   # catch-all
}
return [Send(AGENT_NODE_MAP[task], state) for task in deduplicated(tasks)]
```

### State Merging (Custom Reducers)

```python
# agent_results — dict merge, no overwrite
def _merge_agent_results(a: dict, b: dict) -> dict:
    return {**a, **b}

# tokens_consumed — accumulate
def _sum_tokens(a: int, b: int) -> int:
    return a + b

# source_metadata — list concat
Annotated[list, operator.add]

# messages — LangChain add_messages reducer
MessagesState (base class)
```

---

## 4. State Schema

```python
class LegalAgentState(MessagesState):
    # ── Query Lifecycle ──────────────────────────────────────
    original_query:       str                      # Raw user input, preserved throughout
    query:                str                      # Normalized/rewritten query (updated by memory + orchestrator)
    thread_id:            str | None               # Conversation thread UUID
    unique_string:        str | None               # Document-specific ID (PDF collections)

    # ── Task Routing ─────────────────────────────────────────
    task:                 str | None               # Primary task type (classified by orchestrator)
    tasks_planned:        list[str]                # Agents to invoke (from planner, drives fan-out)
    agent_queries:        dict[str, str]           # Per-agent rewritten queries {agent_name: query}
    response_instructions: str                     # User's format expectations (table, summary, draft, etc.)

    # ── Conversation Context ──────────────────────────────────
    chat_history:         list[BaseMessage]        # Recent HumanMessage/AIMessage pairs (max 5 turns)
    summary_text:         str                      # Rolling summary of all prior turns

    # ── Agent Results (parallel merge reducer) ───────────────
    agent_results:        Annotated[dict, merge]   # {agent_name: AgentResult}

    # ── Guardrail ─────────────────────────────────────────────
    is_blocked:           bool                     # True = query rejected
    block_reason:         str | None               # Reason for rejection

    # ── File Attachments ──────────────────────────────────────
    file_context:         dict | None              # {gemini_file_parts, inline_text,
                                                   #  chromadb_collections, file_names}

    # ── Draft Continuation ────────────────────────────────────
    draft_continuation:   dict | None              # Metadata for resuming incomplete drafts

    # ── Final Output (aggregated reducers) ───────────────────
    final_response:       str                      # Complete synthesized response
    source_metadata:      Annotated[list, extend]  # All SourceMetadata objects (serialized)
    tokens_consumed:      Annotated[int, sum]      # Total tokens from all agents
```

### AgentResult Dataclass

```python
@dataclass
class AgentResult:
    agent_name:        str              # "Legislation", "Judgment", etc.
    content:           str              # Main response text
    sources:           list[SourceMetadata]
    tokens_consumed:   int
    error:             str | None       # Set if agent failed
    retry_attempted:   bool            # True if Tier 2 was triggered
    fallback_used:     bool            # True if web search (Tier 3) was used
```

### SourceMetadata Dataclass

```python
@dataclass
class SourceMetadata:
    # Universal fields
    source_type:   str             # "judgment" | "legislation" | "newacts" | "drafting"
                                   # "sci_judgment" | "scenario" | "constitution"
                                   # "maxim" | "legal_concepts" | "document"
    title:         str | None
    content:       list[str]       # Text snippets
    doc_link:      str | None      # S3 URL (for PDF judgments)
    file_name:     str | None
    agent_name:    str
    relevance_score: float | None

    # Judgment-specific
    court_name:    str | None
    year:          int | None
    petitioner_names: list[str]
    respondent_names: list[str]
    keywords:      list[str]
    acts_or_sections_invoked: list[str]

    # SCI Judgment-specific
    case_no:       str | None
    judgment_date: str | None
    bench:         str | None
    judgment_by:   str | None
    pdf_links:     list[dict]      # [{link: "...", title: "..."}]
    parties:       str | None
    db_id:         str | None

    # Legislation / Newacts
    section_number: str | None
    act_name:       str | None

    # Drafting
    template_type:  str | None

    # Scenario / web fallback
    web_url:        str | None
    web_title:      str | None
```

---

## 5. Per-Agent Internal Flow

### guardrail_input

```
Input:  state["original_query"]
─────────────────────────────────────────────────────
Step 1: validate_input()
        → Check emptiness, length > 5000 chars
        → Return block if invalid

Step 2: detect_injection_regex()
        → Fast pattern match (15 regex patterns)
        → Patterns: "ignore previous", "you are now", "DAN", "jailbreak", etc.
        → Returns: {is_injection: bool, matched_pattern: str}

Step 3: If regex suspicious → detect_injection_llm()
        → Model: Gemini Flash Lite, temp=0.0
        → Structured output: {is_injection: bool, confidence: "low"|"medium"|"high"}
        → Threshold: block only if confidence = "high"
─────────────────────────────────────────────────────
Output: {is_blocked: bool, block_reason: str | None}
```

### memory

```
Input:  state["original_query"], state["thread_id"], state["file_context"]
─────────────────────────────────────────────────────
Step 1: expand_abbreviations(query)
        → Inline regex replacements (no LLM):
          BNS → Bharatiya Nyaya Sanhita
          BNSS → Bharatiya Nagarik Suraksha Sanhita
          BSA → Bharatiya Sakshya Adhiniyam
          IPC → Indian Penal Code
          CrPC → Code of Criminal Procedure
          IEA → Indian Evidence Act

Step 2: Load chat history (if thread_id exists)
        → Primary: SQLite load_history(thread_id, max_recent_turns=5)
          Returns: {chat_history: list[Msg], summary_text: str, total_turns: int}
        → Fallback: GET https://lawttorney.ai/api/users/getChatSummary/{thread_id}
          → Parse JSON or raw text → import_from_api_response() → SQLite insert

Step 3: _restore_file_context(thread_id)
        → Load thread_files from SQLite
        → For each Gemini-supported file:
            Check is_uri_valid(gemini_expiry, buffer=2h)
            If expired → asyncio.to_thread(upload_to_gemini, local_path)
                       → chat_store.update_gemini_uri(new_uri, new_name, new_expiry)
        → Rebuild: {gemini_file_parts, chromadb_collections, inline_text, file_names}

Step 4: Query rewriting (only if chat_history exists AND no new files this turn)
        → Model: Gemini Flash Lite, temp=0.1
        → Prompt rules:
          1. Rewrite as self-contained query
          2. Preserve legal specificity (section numbers, act names, dates)
          3. Resolve pronouns ("that section" → "Section 35 BNS")
          4. Expand vague follow-ups using history
          5. Return ONLY the rewritten query
─────────────────────────────────────────────────────
Output: {query, chat_history, summary_text, file_context}
```

### orchestrator_plan

```
Input:  state["query"], state["chat_history"], state["summary_text"], state["file_context"]
─────────────────────────────────────────────────────
Step 1: Query normalization
        → Model: Gemini Flash Lite, temp=0.1
        → Translate Hindi/regional language to English
        → Extract response_instructions (format user expects: table, explanation, draft)
        → Output: (normalized_query, response_instructions)

Step 2: Task classification
        → Model: GPT-4o, temp=0.3
        → Structured output: IdentifyTaskSchema
        → 12 task types:
          Drafting, Legislation, Judgment, SCI_Judgment, Newacts,
          Constitution, Scenario, Maxim, Legal_Concepts, Document,
          Non_legal, Other
        → Timeout fallback: _classify_task_regex_fallback()
          (keyword-based: "draft" → Drafting, "section" → Legislation, etc.)

Step 3: Non-legal check
        → If task = "Non_legal" AND no file_context → block (return welcome message)
        → If task = "Non_legal" AND file_context → override task to "Document"

Step 4: Multi-agent planning
        → Model: GPT-4o, temp=0.3
        → Structured output: AgentPlan {agents: list[str], reasoning: str}
        → Keyword safety net (adds extra agents if intent detected):
          "case laws" / "judgments" → add Judgment
          "provisions" / "section" → add Legislation or Newacts
          "draft" / "application" → add Drafting
          "arguments" → add Scenario
        → Drafting rule: always add citation agents (Judgment + Legislation or Newacts)
        → Cap: max 3 agents (4 for drafting tasks)

Step 5: Per-agent query rewriting (only if multi-agent)
        → Model: Gemini Flash Lite, temp=0.1
        → Domain-optimized rewrites:
          Legislation: normalize section format, expand act names
          Judgment: include case type, party keywords
          Newacts: identify BNS/BNSS/BSA, map old law equivalents
          Constitution: map to Article numbers
─────────────────────────────────────────────────────
Output: {query, task, tasks_planned, agent_queries, response_instructions}
```

### legislation

```
Input:  state["agent_queries"]["Legislation"] or state["query"]
─────────────────────────────────────────────────────
Tier 1: Elasticsearch ("legislation" index)
        → Parse section number + act name from query
        → Build multi-variation bool queries:
            {"bool": {"must": [{"match": {"page_content": q}},
                               {"match": {"act_name": act_name}}]}}
        → Aggregate by source, sub-search for exact section match
        → Fallback within tier: topic-based BM25 if no section parsed

Tier 2: GPT-4o-mini rewrites query (expand abbreviations, normalize terminology)
        → Retry ES search with rewritten query

Tier 3: web_search_fallback()
        → Gemini 2.5 Flash + Google Search grounding

LLM response: Gemini Flash Lite, temp=0.3
        → Prompt: system_prompt + chat_history + ES context + query
─────────────────────────────────────────────────────
Output: AgentResult {content, sources[SourceMetadata], tokens, retry_attempted, fallback_used}
```

### judgment

```
Input:  state["agent_queries"]["Judgment"] or state["query"]
─────────────────────────────────────────────────────
Tier 1: Elasticsearch ("judgements" index) — 7 search strategies tried in order:
        1. Citation / case_number exact match
        2. Both party names (exact → no-year → swapped → fuzzy, 75% minimum_should_match)
        3. Single party name (if dual-name search fails)
        4. Case type specialized (bail, quashing, writ, appeal → filter by case_type)
        5. Legal provision (BM25 on acts_or_sections_invoked)
        6. Topics + acts + lexical BM25 (combined bool)
        7. Pure BM25 on page_content (last resort within tier)

        For each hit: generate_s3_link(court, source, title) → doc_link

Tier 2: GPT-4o-mini rewrite → retry ES search
Tier 3: web_search_fallback() → Gemini 2.5 Flash + Google Search

LLM response: Gemini Flash Lite, temp=0.3
─────────────────────────────────────────────────────
Output: AgentResult with S3 PDF links in sources
```

### sci_judgment

```
Input:  state["query"]
─────────────────────────────────────────────────────
Elasticsearch ("supreme_court_judgement" index) — 5 strategies:
        1. Exact case_number / case_no match
        2. Party name search (75% match threshold)
        3. Keyword + constitutional topic
        4. Judge / bench search
        5. Pure BM25 fallback

        Returns metadata: case_no, parties, judgment_date, bench,
                          judgment_by, pdf_links[{link, title}]

NOTE: No LLM generation — ES retrieval only, metadata passed as sources
─────────────────────────────────────────────────────
Output: AgentResult {content: formatted_metadata, sources[pdf_links], tokens: 0}
```

### newacts

```
Input:  state["agent_queries"]["Newacts"] or state["query"]
─────────────────────────────────────────────────────
Tier 1: Elasticsearch ("newacts_v1" index) — Hybrid search:
        1. Extract section + act via regex or GPT-4o
        2. If identified:
           → Exact filter: {term: {section_number}}, {term: {act_name: BNS|BNSS|BSA}}
           → Hybrid: BM25 + kNN (BGE 1024-dim embeddings, cosine similarity)
           → Nearby sections: if "Section 35" found, also fetch 34, 36
        3. If no section parsed → topic-based BM25
        4. If embedding service unavailable → BM25-only fallback

Tier 2: GPT-4o-mini rewrite → retry ES
Tier 3: web_search_fallback()

LLM response: Gemini Flash Lite, temp=0.3
─────────────────────────────────────────────────────
Output: AgentResult
```

### scenario

```
Input:  state["query"], state["chat_history"]
─────────────────────────────────────────────────────
ALWAYS uses Gemini 2.5 Pro + Google Search grounding (no Elasticsearch)

google.genai.Client.generate_content(
    model="gemini-2.5-pro",
    contents=[..., query],
    tools=[{"google_search": {}}],
    config={generation_config, system_instruction=SCENARIO_PROMPT}
)

→ Extract grounding_metadata → web sources
→ Stream tokens via SSE
→ If 503: retry once with Gemini 2.5 Pro (then return error)
─────────────────────────────────────────────────────
Output: AgentResult {content, sources[web_url, web_title], tokens, fallback_used: False}
```

### constitution / maxim

```
Input:  state["query"]
─────────────────────────────────────────────────────
Tier 1: Elasticsearch
        Constitution ("constitution" index):
          bool.should: page_content(×2) + match_phrase(×3) + article_name(×2.5) + part_name

        Maxim ("legal_maxims" index):
          bool.should: page_content(×2) + match_phrase(×3) + maxim_name(×5)

Tier 2: Query rewrite → retry
Tier 3: web_search_fallback()

LLM response: Gemini Flash Lite, temp=0.3
─────────────────────────────────────────────────────
Output: AgentResult
```

### legal_concepts

```
Input:  state["query"]
─────────────────────────────────────────────────────
No Elasticsearch index — goes directly to web_search_fallback()
→ Gemini 2.5 Flash + Google Search grounding
→ fallback_used always True
─────────────────────────────────────────────────────
Output: AgentResult {content, sources[web], tokens}
```

### document

```
Input:  state["query"], state["file_context"], state["chat_history"]
─────────────────────────────────────────────────────
Step 1: Build Gemini parts from file_context.all_gemini_parts
        → URI-based parts: {file_data: {file_uri, mime_type}}
        → Legacy fallback: inline_data base64

Step 2: ChromaDB retrieval (if chromadb_collections present)
        → For each collection: search_pdf_collection(query, collection_id, k=5)
        → Returns top-5 chunks per collection
        → Concatenate as additional context

Step 3: Build prompt
        → [file_parts] + [chroma_chunks] + [inline_text] + [query]

Step 4: LLM call
        → Model: Gemini 2.5 Pro, temp=0.3
        → Multimodal: sees file content via Gemini Files API URIs
        → System prompt: DOCUMENT_QA_PROMPT
─────────────────────────────────────────────────────
Output: AgentResult {content, sources[collection_metadata], tokens}
```

### orchestrator_synthesize

```
Input:  state["agent_results"], state["task"], state["response_instructions"],
        state["chat_history"], state["file_context"]
─────────────────────────────────────────────────────
Case 1: Single agent result (no file context)
        → If agent == "Drafting":
             _auto_cite_draft() → Gemini Flash, DRAFT_CITATION_PROMPT
             Fills [CITE: description] markers with AI-generated citations
          Else:
             Pass through as-is (no synthesis LLM call)

Case 2: Drafting present in multi-agent results
        → Extract full draft from drafting result
        → Build citations_text from Judgment + Legislation + Newacts results
        → If draft.len > 40,000 chars:
             Append citations as "## REFERENCES & CITATIONS" section (no truncation)
          Else:
             _inject_citations_into_draft(draft, citations_text)
             → Model: Gemini Flash, temp=0.4, DRAFT_SYNTHESIS_PROMPT
             → LLM weaves real citations into draft body

Case 3: Multiple non-drafting agents
        → Concatenate all agent results with headers
        → LLM synthesis (streamed):
             Model: Gemini Flash, temp=0.2, SYNTHESIS_PROMPT
             Guided by response_instructions (format expectations)

Case 4: All agents empty (no results)
        → Last-resort: web_search_fallback() called from synthesize node

Post-synthesis:
        → Collect all sources from agent_results
        → Sum all tokens
        → Emit SSE "sources" + "done" events
─────────────────────────────────────────────────────
Output: {final_response, source_metadata, tokens_consumed}
```

### guardrail_output

```
Input:  state["final_response"], state["is_blocked"]
─────────────────────────────────────────────────────
Step 1: If is_blocked → format block message (welcome/legal-only notice)

Step 2: Markdown sanitization
        → Strip dangerous HTML tags
        → Normalize encoding (UTF-8 replace)

Step 3: Append disclaimer
        → "This AI response is for informational purposes..."
─────────────────────────────────────────────────────
Output: {final_response: sanitized + disclaimer}
```

---

## 6. Elasticsearch Queries by Index

### Index: `legislation`

```json
{
  "query": {
    "bool": {
      "must": [
        {"match": {"page_content": "<query>"}},
        {"match": {"act_name": "<parsed_act>"}}
      ],
      "should": [
        {"term": {"section_number": "<parsed_section>"}}
      ]
    }
  },
  "size": 5
}
```

**Fields:** `page_content (text)`, `source (keyword)`, `section_number (keyword)`, `section_type (keyword)`, `act_name (keyword)`

**Strategy:** Parse section + act → multi-variation bool → sub-search within top source → topic BM25 fallback

---

### Index: `judgements`

```json
// Strategy 1 — Citation exact match
{"match": {"citation": "<query>"}}

// Strategy 2 — Party names (75% match)
{
  "query": {
    "bool": {
      "must": [
        {"match": {"petitioner_names": {"query": "<petitioner>", "minimum_should_match": "75%"}}},
        {"match": {"respondent_names": {"query": "<respondent>", "minimum_should_match": "75%"}}}
      ]
    }
  }
}

// Strategy 4 — Case type + provision
{
  "query": {
    "bool": {
      "filter": [{"term": {"case_type": "bail"}}],
      "must": [{"match": {"acts_or_sections_invoked": "<act>"}}]
    }
  }
}

// Strategy 7 — BM25 fallback
{"query": {"match": {"page_content": "<query>"}}, "size": 10}
```

**Fields:** `page_content`, `court_name`, `petitioner_names`, `respondent_names`, `year`, `keywords`, `acts_or_sections_invoked`, `case_type`, `citation`, `case_number`, `judgment_date`, `source`

---

### Index: `newacts_v1` (Hybrid)

```json
// BM25 path
{"query": {"bool": {"filter": [{"term": {"section_number": "35"}},
                                {"term": {"act_name": "BNS"}}]}}, "size": 5}

// kNN path
{
  "query": {
    "knn": {
      "dense_vector": {
        "vector": [<1024-dim BGE embedding>],
        "k": 20,
        "num_candidates": 100
      }
    }
  }
}
// Merged via Python-side RRF: score = 1/(60 + bm25_rank) + 1/(60 + knn_rank)
```

**Fields:** `page_content`, `source`, `section_number`, `act_name (BNS|BNSS|BSA|IPC|CrPC|IEA)`, `dense_vector (1024-dim)`

---

### Index: `drafting` (Hybrid RRF)

```json
// BM25
{"query": {"match": {"page_content": "<query>"}}, "size": 20}

// kNN
{"query": {"knn": {"dense_vector": {"vector": [...], "k": 20, "num_candidates": 100}}}}

// Python RRF merge (top 15):
score[doc] = 1/(60 + bm25_rank) + 1/(60 + knn_rank)
// → Template selection via GPT-4o-mini
// → Fetch full template: {"term": {"source": "<selected_filename>"}}
```

---

### Index: `constitution`

```json
{
  "query": {
    "bool": {
      "should": [
        {"match": {"page_content": {"query": "<q>", "boost": 2.0}}},
        {"match_phrase": {"page_content": {"query": "<q>", "boost": 3.0}}},
        {"match": {"article_name": {"query": "<q>", "boost": 2.5}}},
        {"match": {"part_name": {"query": "<q>", "boost": 1.0}}}
      ]
    }
  },
  "size": 10
}
```

---

### Index: `legal_maxims`

```json
{
  "query": {
    "bool": {
      "should": [
        {"match": {"page_content": {"query": "<q>", "boost": 2.0}}},
        {"match_phrase": {"page_content": {"query": "<q>", "boost": 3.0}}},
        {"match": {"maxim_name": {"query": "<q>", "boost": 5.0}}}
      ]
    }
  },
  "size": 10
}
```

---

### Index: `supreme_court_judgement`

```json
// Strategy 1 — Exact case number
{"match": {"case_no": "<query>"}}

// Strategy 2 — Party name
{"match": {"parties": {"query": "<parties>", "minimum_should_match": "75%"}}}

// Returns: case_no, parties, judgment_date, bench, judgment_by, pdf_links[{link, title}]
```

---

## 7. Drafting Pipeline (Multi-Step)

```
User query: "Draft a bail application under Section 483 BNSS"
                               │
                               ▼
            Step 1: Hybrid Template Search
            ┌─────────────────────────────────────────┐
            │  Parallel:                              │
            │  BM25 (size 20) + kNN (k=20, cands 100)│
            │                                         │
            │  Python RRF merge → top 15 candidates  │
            └─────────────────────────────────────────┘
                               │
                               ▼
            Step 2: Template Selection (GPT-4o-mini)
            → Evaluates first 500 chars of each candidate
            → Structured output: {best_template_source: "bail_application_bnss.txt"}
                               │
                               ▼
            Step 3: Fetch Full Template (no truncation)
            → ES term query on source = best_template_source
            → Returns 2K–9K char template text
                               │
                               ▼
            Step 4: Outline Generation (Gemini 2.5 Flash, temp=0.4)
            → Structured output: DraftOutline
              {
                document_title: "APPLICATION FOR BAIL UNDER S.483 BNSS",
                court_details:  "Court: Sessions Court, ...",
                sections: [
                  {title: "Preamble", description: "...", estimated_paragraphs: 1, needs_citations: false},
                  {title: "Facts",    description: "...", estimated_paragraphs: 5, needs_citations: false},
                  {title: "Grounds",  description: "...", estimated_paragraphs: 6, needs_citations: true},
                  ...  (max 12 sections)
                ]
              }
                               │
                               ▼
            Step 5: Per-Section Generation (Gemini 2.5 Flash, asyncio.Semaphore(3))
            ┌────────────────────────────────────────────────────────────────┐
            │ Concurrent (max 3 at once, rate-limit protection):            │
            │   Section 1 (Preamble)  → streamed via SSE token events       │
            │   Section 2 (Facts)     → streamed                            │
            │   Section 3 (Grounds)   → streamed + [CITE: ...] markers      │
            │   ...                                                          │
            │ Timeout: 60s per section, retry once on 503                   │
            └────────────────────────────────────────────────────────────────┘
                               │
                               ▼
            Step 6: Assembly
            document_title + "\n\n" + court_details + "\n\n"
            + section_1 + section_2 + ... + section_N
            + court_filing_footer
                               │
                               ▼
            Step 7: Citation Injection (orchestrator_synthesize)
            ┌────────────────────────────────────────────────────────────────┐
            │ Solo drafting:                                                 │
            │   _auto_cite_draft() → Gemini Flash, DRAFT_CITATION_PROMPT    │
            │   Fills [CITE: desc] markers with AI-generated case citations  │
            │                                                                │
            │ Drafting + citation agents (Judgment/Legislation/Newacts):     │
            │   draft > 40K chars: append "## REFERENCES & CITATIONS"       │
            │   draft ≤ 40K chars: _inject_citations_into_draft()           │
            │     → Gemini Flash, temp=0.4, DRAFT_SYNTHESIS_PROMPT          │
            │     → LLM weaves real citations into draft body                │
            └────────────────────────────────────────────────────────────────┘
                               │
                               ▼
                    Final enriched draft + SSE events
```

### Draft Incomplete Handling

```
If any section fails (timeout / error):
  → SSE event: {"type": "draft_incomplete",
                "completed_sections": 6,
                "total_sections": 8,
                "failed_sections": 2}
  → Partial draft saved to draft_continuations table:
      {thread_id, failed_section_indices, outline, partial_draft}

POST /continue_draft:
  → Load draft_continuations for thread_id
  → Re-run only failed sections (Semaphore(3) again)
  → Merge with existing partial draft
  → Emit new SSE tokens
```

---

## 8. File Processing Pipeline

### Flow

```
Client uploads files (multipart/form-data, max 30 files, 1 GB each)
                     │
                     ▼
         Validation (per file)
         ├── Extension in allowed set? (.pdf .jpg .png .webp .docx .txt .md .csv .xlsx)
         ├── Size ≤ MAX_FILE_SIZE_MB (1024 MB)?
         ├── Thread count: existing_files + new ≤ 30?
         └── Thread storage: existing_bytes + new_bytes ≤ MAX_THREAD_STORAGE_MB (1024 MB)?
                     │
                     ▼
         Permanent Local Copy
         → ./uploads/{thread_id}/{file_id}_{sanitized_filename}
         → file_id = UUID4
                     │
           ┌─────────┴──────────┐
           │                    │
           ▼                    ▼
    Gemini Files API     Text Extraction
    (if MIME supported)
    ─────────────────   ──────────────────────────────────────
    PDFs, images,        PDF → PyMuPDF (fitz.open)
    CSV, TXT, MD           → If sparse (scanned): Gemini Vision OCR
                              Batch 5 pages, "Extract all visible text"
                         DOCX → python-docx (paragraphs + tables)
                         XLSX → openpyxl (sheets as markdown tables, 100 rows/sheet)
                         CSV/TXT/MD → raw text (skip if Gemini URI succeeded)

    Returns:             Returns:
    (uri, name, expiry)  extracted_text, page_count
           │                    │
           └─────────┬──────────┘
                     │
                     ▼
         ChromaDB Storage (large PDFs only)
         Condition: page_count > 20 OR text.len > 100,000 chars

         If triggered:
           RecursiveCharacterTextSplitter(chunk_size=1000, overlap=200)
           Embeddings: all-MiniLM-L6-v2
           collection_id = f"inline_{thread_id}_{file_id[:8]}"
           Chroma.from_texts(texts, embeddings, collection_name, persist_dir)
           persist_dir = ./chroma_store/{collection_id}/
                     │
                     ▼
         SQLite INSERT → thread_files table
         (thread_id, file_id, filename, mime_type, size_bytes,
          local_path, extracted_text, chromadb_collection,
          gemini_uri, gemini_name, gemini_expiry,
          gemini_supported, page_count, upload_error)
                     │
                     ▼
         FileContext assembled:
         {
           files:                list[ProcessedFile],
           gemini_file_parts:    [{file_data: {file_uri, mime_type}, name}, ...],
           inline_text:          concatenated extracted text (capped at 100K chars),
           chromadb_collections: [collection_id, ...],
           file_names:           [filename, ...]
         }
```

### Gemini Files API — Supported MIME Types

| Type | MIME | Notes |
|------|------|-------|
| PDF | `application/pdf` | Full support |
| JPEG | `image/jpeg` | Full support |
| PNG | `image/png` | Full support |
| WebP | `image/webp` | Full support |
| GIF | `image/gif` | Full support |
| CSV | `text/csv` | Full support |
| TXT | `text/plain` | Full support |
| Markdown | `text/markdown` | Full support |
| HTML | `text/html` | Full support |
| DOCX | — | **NOT supported** → text extraction only |
| XLSX | — | **NOT supported** → text extraction only |

### URI Expiry & Re-upload

```
Gemini Files API URIs expire after 48 hours.

On each follow-up turn (memory_node._restore_file_context):
  For each file in thread_files:
    expiry = datetime.fromisoformat(gemini_expiry)
    buffer = GEMINI_URI_EXPIRY_BUFFER_HOURS (default: 2)
    if now + buffer >= expiry:
      new_uri, new_name, new_expiry = upload_to_gemini(local_path, mime_type, filename)
      chat_store.update_gemini_uri(thread_id, file_id, new_uri, new_name, new_expiry)
```

---

## 9. Chat Memory Pipeline

### Load Phase (memory_node)

```
Thread exists?
  YES → SQLite load_history(thread_id, max_recent_turns=5)
          Returns: {chat_history, summary_text, total_turns}
        If empty → Legacy API fallback:
          GET https://lawttorney.ai/api/users/getChatSummary/{thread_id}
          → Parse response → import_from_api_response()
          → Parses as JSON list [{user, ai}, ...] or raw text summary
          → Insert into SQLite for future use
        If still empty → placeholder:
          [HumanMessage("Previous summary:"), AIMessage("Fresh chat started.")]

  NO  → Empty chat_history, empty summary_text
```

### Summarization (Post-Turn, Non-Blocking)

```
After save_turn() completes:
  total_turns = thread.total_turns
  summary_turn_count = thread.summary_turn_count

  if (total_turns - summary_turn_count) >= 3:
    → Load unsummarized turns
    → Gemini Flash Lite, temp=0.1, SUMMARY_PROMPT
    → Preserves:
        1. Topics discussed
        2. Legal references (statutes, sections, cases, citations)
        3. Named entities (persons, courts, institutions, places)
        4. Key insights and legal conclusions
    → UPDATE threads SET summary_text = ..., summary_turn_count = total_turns
  else:
    → Skip (summary still fresh)
```

### Save Phase (After Graph Completes — Gateway)

```
await chat_store.save_turn(thread_id, user_query, ai_response)

  _save_turn_sync():
    1. INSERT INTO threads (thread_id) ON CONFLICT DO NOTHING
    2. INSERT INTO messages (thread_id, turn_number, user_query, ai_response)
       turn_number = (SELECT MAX + 1 FROM messages WHERE thread_id = ?)
    3. UPDATE threads SET updated_at, total_turns = total_turns + 1
    4. Trigger summarization check (Step above)
    5. Return new_turn_number
```

---

## 10. SSE Streaming Events

Full sequence for a typical `/search/stream` request:

```
data: {"type": "start"}

data: {"type": "thread_id", "data": "abc12345-..."}

data: {"type": "memory", "history_turns": 3, "has_summary": true,
       "rewritten": true, "effective_query": "Section 35 BNS cases"}

data: {"type": "file_processing", "files": ["contract.pdf", "notice.jpg"]}

data: {"type": "metadata", "task": "Judgment",
       "agents": ["Judgment", "Legislation"], "thread_id": "abc12345-..."}

data: {"type": "drafting_progress", "section": 1, "total": 8, "title": "Preamble"}
data: {"type": "drafting_progress", "section": 2, "total": 8, "title": "Facts of the Case"}
... (one per section)

data: {"type": "token", "content": "The Supreme Court held that"}
data: {"type": "token", "content": " in the case of State vs. Ram Kumar,"}
data: {"type": "token", "content": " bail cannot be denied merely on..."}
... (repeated until response complete)

data: {"type": "sources", "data": [{"source_type": "judgment", "title": "...", ...}]}

data: {"type": "followup_suggestions", "data": ["What is the penalty?", "Related cases?"]}

data: {"type": "done", "total_tokens": 4521, "agents_used": ["Judgment", "Legislation"],
       "thread_id": "abc12345-...", "conversation_turn": 4}
```

**Error events:**
```
data: {"type": "error", "data": "Rate limit exceeded. Try again in 60 seconds."}

data: {"type": "draft_incomplete", "completed_sections": 6, "total_sections": 8,
       "failed_sections": 2}
```

---

## 11. 3-Tier Fallback Chain

```
                 All Domain Agents (except Scenario, Legal_Concepts)
                 ─────────────────────────────────────────────────────

TIER 1: Elasticsearch Primary Search
        ├── Index-specific query strategy (7-tier for judgments, hybrid for newacts)
        ├── Extract context, source metadata
        └── If hits > 0: proceed to LLM response generation
             │
             │ empty (0 hits)
             ▼
TIER 2: Query Rewriting + Retry
        ├── Model: GPT-4o-mini, temp=0.1
        ├── Domain-specific rewrite instructions:
        │     Legislation: "expand legal abbreviations, normalize section format"
        │     Judgment: "include party types, relevant legal keywords"
        │     Newacts: "identify correct act name (BNS/BNSS/BSA), old law equivalents"
        │     Constitution: "map to Article numbers or Part names"
        │     Maxim: "expand Latin phrases, include doctrine name"
        ├── Retry same ES query strategy with rewritten query
        └── Mark agent: retry_attempted = True
             │
             │ still empty
             ▼
TIER 3: Web Search Fallback
        ├── core.agent_fallback.web_search_fallback(query, agent_name)
        ├── Model: Gemini 2.5 Flash
        ├── Native google.genai.Client with tools=[{"google_search": {}}]
        ├── Extract grounding_metadata → web_url, web_title per source
        ├── Stream tokens via SSE
        └── Mark agent: fallback_used = True

                 Scenario Agent: ALWAYS on Tier 3 (no ES)
                 Legal_Concepts: ALWAYS on Tier 3 (no ES index)
```

---

## 12. Model Usage Map

| Component | Task | Model | Temp | Why |
|-----------|------|-------|------|-----|
| guardrail_input | Injection detection | Gemini Flash Lite | 0.0 | Deterministic safety |
| memory | Query rewriting | Gemini Flash Lite | 0.1 | Faithful context carry-over |
| orchestrator | Query normalization | Gemini Flash Lite | 0.1 | Translation + intent extraction |
| orchestrator | Task classification | GPT-4o | 0.3 | High accuracy routing |
| orchestrator | Agent planning | GPT-4o | 0.3 | Multi-intent detection |
| orchestrator | Per-agent rewrite | Gemini Flash Lite | 0.1 | Domain-optimized queries |
| orchestrator | Synthesis | Gemini 2.5 Flash | 0.2 | Coherent multi-agent merge |
| legislation | Generation | Gemini Flash Lite | 0.3 | Legislation explanation |
| judgment | Generation | Gemini Flash Lite | 0.3 | Case law explanation |
| newacts | Generation | Gemini Flash Lite | 0.3 | Code section explanation |
| drafting | Template selection | GPT-4o-mini | 0.3 | Relevance scoring |
| drafting | Outline generation | Gemini 2.5 Flash | 0.4 | Creative structure |
| drafting | Section generation | Gemini 2.5 Flash | 0.4 | Legal content generation |
| drafting | Citation injection | Gemini 2.5 Flash | 0.4 | Weave citations into draft |
| scenario | Analysis + web | Gemini 2.5 Pro | 0.5 | Real-time legal advice |
| constitution | Generation | Gemini Flash Lite | 0.3 | Constitutional provisions |
| maxim | Generation | Gemini Flash Lite | 0.3 | Latin legal maxims |
| legal_concepts | Generation | Gemini 2.5 Flash | 0.5 | Web-grounded concepts |
| document | PDF Q&A | Gemini 2.5 Pro | 0.3 | Multimodal file analysis |
| chat_store | Summary regen | Gemini Flash Lite | 0.1 | Preserve legal entities |
| fallback (all) | Query rewrite | GPT-4o-mini | 0.1 | Better search terms |
| fallback (all) | Web search | Gemini 2.5 Flash | 0.5 | General web answer |

---

## 13. External Dependencies

| Service | URL / Config | Used By | Purpose |
|---------|-------------|---------|---------|
| OpenSearch/ES | AWS OpenSearch (via `ES_URL` env var) | All domain agents | Primary legal doc retrieval |
| Google Gemini | `GOOGLE_API_KEY` | All agents, file processor | LLM generation + files API |
| Google Search | Via Gemini grounding tools | Scenario, web fallback, legal_concepts | Real-time web results |
| OpenAI GPT | `OPENAI_API_KEY` | Orchestrator, drafting, fallback rewrite | Classification + planning |
| AWS S3 | `lawttorney` bucket, `ap-south-1` | Judgment, SCI judgment | PDF document links |
| ChromaDB | `./chroma_store/` (local) | Document agent, large PDFs | Vector semantic search |
| Legacy API | `https://lawttorney.ai/api` | Memory (fallback) | Old chat history import |
| SQLite | `./data/chat_history.db` | chat_store (all flows) | Threads, messages, files, feedback |
| Local disk | `./uploads/{thread_id}/` | file_processor | Permanent file storage for re-upload |
| Local models | `./models/bge-large-en-v1.5` | newacts hybrid search | ES kNN embeddings |
| Local models | `./models/all-MiniLM-L6-v2` | ChromaDB | PDF chunk embeddings |

### ES Indices

| Index | Content | Agents |
|-------|---------|--------|
| `legislation` | Indian acts, sections, rules | Legislation |
| `judgements` | High Court + District Court judgments | Judgment |
| `supreme_court_judgement` | Supreme Court judgments | SCI Judgment |
| `newacts_v1` | BNS, BNSS, BSA + old IPC/CrPC/IEA | Newacts |
| `drafting` | Legal document templates | Drafting |
| `constitution` | Indian Constitution articles | Constitution |
| `legal_maxims` | Latin maxims, legal doctrines | Maxim |

---

## 14. Error Handling Paths

### Gateway Layer

```
Request Validation:
  ├── Missing query                → 400 Bad Request
  ├── Query > 5000 chars           → 400 Bad Request
  ├── File > 1 GB                  → 400 Bad Request
  ├── Unsupported file extension   → 400 Bad Request
  ├── Rate limit exceeded          → 429 Too Many Requests
  └── Internal exception           → 500 Internal Server Error

File Processing:
  ├── Gemini upload fails          → fallback to text extraction only
  ├── PyMuPDF fails (corrupt PDF)  → Gemini Vision OCR fallback
  ├── ChromaDB store fails         → log warning, use inline text only
  ├── Thread limit exceeded        → process subset, skip rest
  └── Local copy fails             → upload_error in thread_files, continue
```

### Agent Layer

```
guardrail_input:
  ├── LLM injection check times out → allow query (fail open)
  └── Invalid LLM output            → use regex result only

memory:
  ├── SQLite load fails             → empty chat_history (fresh chat)
  ├── Legacy API unreachable        → empty chat_history
  ├── Query rewrite fails           → use original query
  ├── File URI re-upload fails      → log error, skip file (continue)
  └── ChromaDB restore fails        → log warning, continue without

orchestrator_plan:
  ├── Classification times out      → regex fallback classifier
  ├── Planning times out            → single-agent fallback (Scenario)
  ├── Normalization fails           → use original query
  └── Per-agent rewrite fails       → agents use original query

domain agents:
  ├── ES timeout (30s)              → empty → Tier 2
  ├── ES connection error           → empty → Tier 2
  ├── Embedding service down        → BM25-only fallback
  ├── Query rewrite fails           → original query for retry
  ├── Web search fails              → AgentResult.error set
  └── LLM generation fails          → empty content, error logged

orchestrator_synthesize:
  ├── All agents empty              → web_search_fallback() last resort
  ├── Synthesis LLM fails           → concatenate raw agent results
  └── Citation injection fails      → return draft without citations

guardrail_output:
  ├── Sanitization fails            → return raw response
  └── Encoding errors               → UTF-8 replace invalid chars

SSE streaming:
  ├── Client disconnects            → asyncio cancellation (graceful)
  └── Stream write fails            → log error, continue
```

---

## 15. Database Schema

### `threads`
```sql
CREATE TABLE threads (
    thread_id          TEXT PRIMARY KEY,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    summary_text       TEXT NOT NULL DEFAULT '',
    summary_turn_count INTEGER NOT NULL DEFAULT 0,
    total_turns        INTEGER NOT NULL DEFAULT 0,
    file_context_json  TEXT NOT NULL DEFAULT ''
);
```

### `messages`
```sql
CREATE TABLE messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   TEXT NOT NULL REFERENCES threads(thread_id),
    turn_number INTEGER NOT NULL,
    user_query  TEXT NOT NULL,
    ai_response TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_messages_thread_turn ON messages(thread_id, turn_number);
```

### `thread_files`
```sql
CREATE TABLE thread_files (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id            TEXT    NOT NULL REFERENCES threads(thread_id),
    file_id              TEXT    NOT NULL,
    filename             TEXT    NOT NULL,
    file_type            TEXT    NOT NULL DEFAULT '',
    mime_type            TEXT    NOT NULL DEFAULT '',
    size_bytes           INTEGER NOT NULL DEFAULT 0,
    local_path           TEXT    NOT NULL DEFAULT '',
    extracted_text       TEXT    NOT NULL DEFAULT '',
    chromadb_collection  TEXT    NOT NULL DEFAULT '',
    gemini_uri           TEXT    NOT NULL DEFAULT '',
    gemini_name          TEXT    NOT NULL DEFAULT '',
    gemini_expiry        TEXT    NOT NULL DEFAULT '',
    gemini_supported     INTEGER NOT NULL DEFAULT 0,  -- 0/1 boolean
    page_count           INTEGER NOT NULL DEFAULT 0,
    upload_error         TEXT    NOT NULL DEFAULT '',
    created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(thread_id, file_id)
);
CREATE INDEX idx_thread_files_thread ON thread_files(thread_id, created_at DESC);
```

### `feedback`
```sql
CREATE TABLE feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    rating      TEXT NOT NULL CHECK(rating IN ('up', 'down')),
    comment     TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(thread_id, turn_number)
);
CREATE INDEX idx_feedback_thread ON feedback(thread_id, turn_number);
```

### `draft_continuations`
```sql
CREATE TABLE draft_continuations (
    thread_id  TEXT PRIMARY KEY,
    data_json  TEXT NOT NULL,  -- {failed_sections, outline, partial_draft}
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### Current DB Stats (as of 2026-03-11)

| Table | Rows |
|-------|------|
| threads | 1,375 |
| messages | 1,433 |
| thread_files | 0 (table newly created) |
| feedback | 1 |
| draft_continuations | 0 |

**Session Pattern:**
- 98% of threads are single-turn (1,348 / 1,375)
- Multi-turn sessions cluster in 30–300 second inter-turn gaps (single sittings)
- 9 / 17 threads with ≥ 3 turns have rolling summaries

---

*Generated from codebase analysis — core/gateway.py, core/graph.py, core/state.py, core/chat_store.py, core/file_processor.py, agents/\*, tools/shared/\*, config/prompts.py*
