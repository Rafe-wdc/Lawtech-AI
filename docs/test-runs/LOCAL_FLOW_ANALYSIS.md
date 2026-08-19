# Local run — step-by-step pipeline behaviour

Same suite, same single thread, run against `http://127.0.0.1:5000`.
Every step below is reconstructed from three independent sources captured
during the run:

1. **SSE status timeline** — the graph node the runner was told about, with
   the wall-clock offset at which the event arrived.
2. **Per-LLM-call token ledger** — the `done` event's `token_usage.calls[]`,
   which records every model call as `(agent, step, in, out, cache_read)`.
3. **Server log slice** — `logs/agent.log`, sliced by byte offset around each
   turn, so the internal decisions (routing, ES hits, fan-out judge,
   self-refine, fallbacks) are attributable to the exact turn that caused them.

| | |
|---|---|
| Thread | `e608305f-c169-4b09-86ad-75aa894b704c` |
| Started (UTC) | 2026-08-17T14:40:05.931879+00:00 |
| Finished (UTC) | 2026-08-17T14:49:58.342106+00:00 |

## The pipeline being exercised

```
POST /pyapi/chat
  ├─ file staging          (only when files[] present: extract → Chroma → Gemini Files)
  └─ core.chat_runner.run_chat_pipeline
       └─ LangGraph  (core/graph.py)
            START → guardrail_input ──[blocked?]──→ blocked_response ─┐
                         │                                            │
                         └─→ memory → orchestrator_plan               │
                                          │                           │
                              ┌───────────┴─── Send() fan-out ───┐    │
                          domain agent            domain agent   …    │
                              └───────────┬──────────────────────┘    │
                                orchestrator_synthesize               │
                                          │                           │
                                   guardrail_output ←─────────────────┘
                                          │
                                         END
```

SSE status strings map 1:1 onto those nodes via `_NODE_STATUS` in
`core/gateway.py`, which is what makes the timeline below a real node trace
rather than cosmetic progress text.

---

## Turn 1 — Partition suit + temporary injunction

**Prompt (Scenario / Drafting):** Draft a civil suit for partition along with a prayer for temporary injunction. Facts: Rupa is an adopted daughter of the deceased owner. She has no siblings. Her father died 5 years ago, leaving behind two flats in Pune. The only legal heirs are Rupa and her mother. Her name is not yet on the proper…

**Outcome:** 184.6s · agents `Drafting` · 9,668 chars · 145,284 tokens · $0.4183 · 13 LLM calls

### Step 1 — node timeline (from SSE)

| t+ | Event | Node it maps to |
|----|-------|-----------------|
| 0.913s | Validating query... | `guardrail_input` |
| 0.923s | Loading context... | `memory` |
| 5.117s | Planning search strategy... | `orchestrator_plan` |
| 182.709s | Generating legal draft... | `drafting` |
| 182.713s | Injecting citations into draft... | `orchestrator_synthesize` |
| 182.714s | Finalizing... | `guardrail_output` |

Planned by orchestrator: `Drafting` → actually reported used: `Drafting`.

### Step 2 — every LLM call, in order

| # | Agent | Step | In | Out | Cache read | Cost |
|---|-------|------|----|-----|------------|------|
| 1 | Orchestrator | `classify_and_plan` | 1,500 | 78 | 0 | $0.0002 |
| 2 | Orchestrator | `extract_user_intent` | 5,887 | 498 | 5,436 | $0.0008 |
| 3 | Drafting | `pick_reference` | 3,567 | 67 | 2,798 | $0.0004 |
| 4 | Drafting | `fanout_judge` | 3,247 | 472 | 2,499 | $0.0005 |
| 5 | Drafting | `generate_section_pair` | 11,136 | 2,936 | 0 | $0.0709 |
| 6 | Drafting | `generate_section_pair` | 11,309 | 3,805 | 0 | $0.0880 |
| 7 | Drafting | `generate_section_pair` | 11,551 | 2,225 | 8,171 | $0.0506 |
| 8 | Drafting | `generate_section_pair` | 12,358 | 2,848 | 8,172 | $0.0670 |
| 9 | SelfRefine | `critique` | 16,653 | 1,163 | 0 | $0.0079 |
| 10 | SelfRefine | `refine` | 5,600 | 3,195 | 0 | $0.0518 |
| 11 | SelfRefine | `critique` | 16,788 | 1,116 | 14,937 | $0.0078 |
| 12 | SelfRefine | `refine` | 5,706 | 4,059 | 0 | $0.0662 |
| 13 | SelfRefine | `critique` | 17,087 | 433 | 0 | $0.0062 |

Roll-up by agent:

| Agent | Calls | In | Out | Cache read | Cost |
|-------|-------|----|-----|------------|------|
| SelfRefine | 5 | 61,834 | 9,966 | 14,937 | $0.1399 |
| Drafting | 6 | 53,168 | 12,353 | 21,640 | $0.2774 |
| Orchestrator | 2 | 7,387 | 576 | 5,436 | $0.0010 |

### Step 3 — internal decisions (server log)

```log
20:10:05.943 DBG [Graph] Routing to memory (guardrail passed)
20:10:06.835 DBG [Language] Language detected | lang=en | lang_name=English | snippet=Draft a civil suit for partition along with a prayer for temporary injunction. F
20:10:06.835 INF [Memory] Language detected | lang=en | query=Draft a civil suit for partition along with a prayer for tem
20:10:06.843 DBG [Memory] Loading chat history | thread_id=e608305f-c16
20:10:06.843 DBG [Memory] SQLite history load started | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:10:06.846 INF [ChatStore] Schema initialized | db_path=C:\Users\User\Lawtech-AI\data\chat_history.db
20:10:06.847 INF [Memory] SQLite history load completed | duration_ms=5 | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:10:06.847 DBG [Memory] No history found | thread_id=e608305f-c16
20:10:06.847 INF [Memory] Chat history loaded | messages=2 | turns=0 | placeholder_history=True | has_summary=False
20:10:06.852 DBG [Memory] Skipping rewrite — fresh chat placeholder
20:10:06.852 INF [Memory] Agent completed | final_query=Draft a civil suit for partition along with a prayer for temporary injunction. Facts: Rupa is an ado | query_changed=False | history_messages=2 | file_context_restored=False
20:10:06.854 DBG [Orchestrator] Intent extraction (v2) started
20:10:11.030 INF [Orchestrator] Intent extraction (v2) completed | duration_ms=4180
20:10:11.030 INF [Orchestrator] User intent extracted | format=draft | format_explicit=False | language=en | language_explicit=False | depth=standard | confidence=0.95
20:10:11.036 INF [Orchestrator] Multi-intent enrichment via UserIntent | extra=['Legislation', 'Scenario'] | all_agents=['Drafting', 'Legislation', 'Legal_Concepts', 'Scenario']
20:10:11.038 INF [Graph] Fan-out routing | tasks_planned=['Drafting'] | nodes=['drafting'] | parallel_count=1
20:10:13.657 INF [Drafting] Reference draft acquired from corpus | source=/content/formats_and_all_drafts/A Suit for Partition and Permanent Injunction.csv | length=8699
20:10:14.173 DBG [Drafting] Fan-out judge started
20:10:16.815 INF [Drafting] Fan-out judge completed | duration_ms=2641
20:10:16.815 INF [Drafting] Fan-out judge decided | should_fanout=True | sections=8 | reasoning=Fan out - This is a civil suit for partition and injunction, which is a multi-section legal document with distinct parts like cause title, facts, legal grounds,
20:10:16.815 INF [Drafting] Drafting: section-wise selected | sections=8 | reasoning=Fan out - This is a civil suit for partition and injunction, which is a multi-section legal document with distinct parts like cause title, facts, legal grounds, prayer, and 
20:10:17.321 DBG [Drafting] Section pair gen (sections 1-2) started
20:10:41.431 INF [Drafting] Section pair gen (sections 1-2) completed | duration_ms=24104
20:10:41.431 INF [Drafting] Section pair generated | section_label=sections 1-2 | length=568
20:10:41.433 DBG [Drafting] Section pair gen (sections 3-4) started
20:11:13.408 INF [Drafting] Section pair gen (sections 3-4) completed | duration_ms=31976
20:11:13.408 INF [Drafting] Section pair generated | section_label=sections 3-4 | length=976
20:11:13.408 DBG [Drafting] Section pair gen (sections 5-6) started
20:11:32.501 INF [Drafting] Section pair gen (sections 5-6) completed | duration_ms=19092
20:11:32.503 INF [Drafting] Section pair generated | section_label=sections 5-6 | length=3869
20:11:32.503 DBG [Drafting] Section pair gen (sections 7-8) started
20:11:56.728 INF [Drafting] Section pair gen (sections 7-8) completed | duration_ms=24225
20:11:56.728 INF [Drafting] Section pair generated | section_label=sections 7-8 | length=2276
20:11:56.728 DBG [SelfRefine] Self-refine critique started
20:12:04.774 INF [SelfRefine] Self-refine critique completed | duration_ms=8045
20:12:04.774 INF [SelfRefine] Critique result | passes=False | confidence=0.9 | violation_count=9 | critical=0 | major=9 | minor=0
20:12:04.774 DBG [SelfRefine] Self-refine refinement started
20:12:28.781 INF [SelfRefine] Self-refine refinement completed | duration_ms=24006
20:12:28.781 DBG [SelfRefine] Self-refine critique started
20:12:34.721 INF [SelfRefine] Self-refine critique completed | duration_ms=5941
20:12:34.721 INF [SelfRefine] Critique result | passes=False | confidence=0.9 | violation_count=8 | critical=0 | major=7 | minor=1
20:12:34.721 DBG [SelfRefine] Self-refine refinement started
20:13:04.719 INF [SelfRefine] Self-refine refinement completed | duration_ms=29996
20:13:04.719 DBG [SelfRefine] Self-refine critique started
20:13:08.637 INF [SelfRefine] Self-refine critique completed | duration_ms=3917
20:13:08.637 INF [SelfRefine] Critique result | passes=False | confidence=1.0 | violation_count=4 | critical=0 | major=4 | minor=0
20:13:08.637 WRN [SelfRefine] Self-refine max iterations exhausted | iterations=2 | final_violations=4
20:13:08.637 INF [Drafting] Self-refine altered draft | iterations=3 | original_len=7695 | refined_len=9465
20:13:08.637 INF [Orchestrator] Synthesize phase started | agents_received=['Drafting'] | registry_size=1 | has_response_instructions=True
20:13:10.480 DBG [ChatStore] Turn saved | thread_id=e608305f-c16 | turn=1
```

**Warnings/errors in this turn (1):**

- `SelfRefine: Self-refine max iterations exhausted | iterations=2 | final_violations=4`

### Step 4 — what the client got back

- Sources: **1** (drafting)
- Response: **9,668 chars**, first 400:

```text
**IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE**

**CIVIL SUIT NO. ______ OF 2024**

**IN THE MATTER OF:**

Rupa,
adopted daughter of Late [Father's Name],

Age: [Age of Rupa] years,

Occupation: [Occupation of Rupa],

Residing at: [Address of Rupa], Pune.

.....Plaintiff

**vs**

[Mother's Name],
widow of Late [Father's Name],

Age: [Age of Mother] years,

Occupation: [Occupation of Mot…
```

- Follow-ups: `What documents prove adoption?`, `What is the timeline for this suit?`, `Can I claim mesne profits?`

---

## Turn 2 — Medical negligence damages suit

**Prompt (Scenario / Drafting):** Draft a civil suit for damages arising from medical negligence, incorporating detailed legal reasoning and relevant case laws. Scenario: My client, Mrs. Anjali Deshmukh, aged 42, and a resident of Pune, underwent gallbladder surgery at XYZ Multispecialty Hospital on 12th March 2024. During the proce…

**Outcome:** 144.4s · agents `Drafting` · 10,245 chars · 119,866 tokens · $0.3263 · 11 LLM calls

### Step 1 — node timeline (from SSE)

| t+ | Event | Node it maps to |
|----|-------|-----------------|
| 0.015s | Validating query... | `guardrail_input` |
| 0.024s | Loading context... | `memory` |
| 2.468s | Planning search strategy... | `orchestrator_plan` |
| 143.171s | Generating legal draft... | `drafting` |
| 143.178s | Injecting citations into draft... | `orchestrator_synthesize` |
| 143.179s | Finalizing... | `guardrail_output` |

Planned by orchestrator: `Drafting` → actually reported used: `Drafting`.

### Step 2 — every LLM call, in order

| # | Agent | Step | In | Out | Cache read | Cost |
|---|-------|------|----|-----|------------|------|
| 1 | Orchestrator | `classify_and_plan` | 1,551 | 84 | 1,539 | $0.0002 |
| 2 | Orchestrator | `extract_user_intent` | 5,975 | 446 | 5,474 | $0.0008 |
| 3 | Drafting | `pick_reference` | 3,885 | 72 | 3,758 | $0.0004 |
| 4 | Drafting | `fanout_judge` | 3,629 | 531 | 0 | $0.0006 |
| 5 | Drafting | `generate_section_pair` | 10,592 | 2,254 | 8,166 | $0.0565 |
| 6 | Drafting | `generate_section_pair` | 10,750 | 2,701 | 8,166 | $0.0581 |
| 7 | Drafting | `generate_section_pair` | 11,706 | 3,482 | 8,168 | $0.0715 |
| 8 | Drafting | `generate_section_pair` | 12,958 | 1,759 | 8,171 | $0.0483 |
| 9 | SelfRefine | `critique` | 17,534 | 1,138 | 0 | $0.0081 |
| 10 | SelfRefine | `refine` | 6,456 | 4,482 | 0 | $0.0748 |
| 11 | SelfRefine | `critique` | 17,115 | 766 | 0 | $0.0070 |

Roll-up by agent:

| Agent | Calls | In | Out | Cache read | Cost |
|-------|-------|----|-----|------------|------|
| Drafting | 6 | 53,520 | 10,799 | 36,429 | $0.2354 |
| SelfRefine | 3 | 41,105 | 6,386 | 0 | $0.0900 |
| Orchestrator | 2 | 7,526 | 530 | 7,013 | $0.0010 |

### Step 3 — internal decisions (server log)

```log
20:13:10.506 DBG [Graph] Routing to memory (guardrail passed)
20:13:10.512 DBG [Language] Language detected | lang=en | lang_name=English | snippet=Draft a civil suit for damages arising from medical negligence, incorporating de
20:13:10.512 INF [Memory] Language detected | lang=en | query=Draft a civil suit for damages arising from medical negligen
20:13:10.512 DBG [Memory] Loading chat history | thread_id=e608305f-c16
20:13:10.512 DBG [Memory] SQLite history load started | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:13:10.516 INF [Memory] SQLite history load completed | duration_ms=4 | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:13:10.516 INF [Memory] Chat history loaded from SQLite | thread_id=e608305f-c16 | turns=1 | messages=2 | has_summary=False | has_prev_intent=True | prev_task=Drafting | prev_artifact_kind=draft
20:13:10.516 INF [Memory] Chat history loaded | messages=2 | turns=1 | placeholder_history=False | has_summary=False
20:13:10.516 DBG [Memory] Skipping rewrite — query already standalone-length | query_chars=940
20:13:10.516 INF [Memory] Agent completed | final_query=Draft a civil suit for damages arising from medical negligence, incorporating detailed legal reasoni | query_changed=False | history_messages=2 | file_context_restored=False
20:13:10.522 DBG [Orchestrator] Intent extraction (v2) started
20:13:12.963 INF [Orchestrator] Intent extraction (v2) completed | duration_ms=2440
20:13:12.963 INF [Orchestrator] User intent extracted | format=prose | format_explicit=False | language=en | language_explicit=False | depth=detailed | confidence=0.95
20:13:12.963 INF [Orchestrator] Multi-intent enrichment via UserIntent | extra=['Judgment', 'SCI_Judgment', 'Scenario'] | all_agents=['Drafting', 'Judgment', 'SCI_Judgment', 'Scenario']
20:13:12.963 INF [Graph] Fan-out routing | tasks_planned=['Drafting'] | nodes=['drafting'] | parallel_count=1
20:13:15.680 INF [Drafting] Picker chose | picked=/content/formats_and_all_drafts/Format of Suit for Medical Negligence  FORMAT 2 .csv | reasoning=The user is asking for a civil suit for damages arising from medical negligence, and this file is a direct matc
20:13:15.731 INF [Drafting] Reference draft acquired from corpus | source=/content/formats_and_all_drafts/Format of Suit for Medical Negligence  FORMAT 2 .csv | length=5089
20:13:16.308 DBG [Drafting] Fan-out judge started
20:13:19.775 INF [Drafting] Fan-out judge completed | duration_ms=3465
20:13:19.775 INF [Drafting] Fan-out judge decided | should_fanout=True | sections=8 | reasoning=Fan out — The user requested a detailed draft for a civil suit for damages, which typically involves multiple sections like cause title, facts, legal grounds, p
20:13:19.775 DBG [Drafting] Section pair gen (sections 1-2) started
20:13:39.335 INF [Drafting] Section pair gen (sections 1-2) completed | duration_ms=19560
20:13:39.336 INF [Drafting] Section pair generated | section_label=sections 1-2 | length=603
20:13:39.336 DBG [Drafting] Section pair gen (sections 3-4) started
20:14:01.758 INF [Drafting] Section pair gen (sections 3-4) completed | duration_ms=22422
20:14:01.758 INF [Drafting] Section pair generated | section_label=sections 3-4 | length=4431
20:14:01.758 DBG [Drafting] Section pair gen (sections 5-6) started
20:14:31.553 INF [Drafting] Section pair gen (sections 5-6) completed | duration_ms=29795
20:14:31.553 INF [Drafting] Section pair generated | section_label=sections 5-6 | length=6179
20:14:31.553 DBG [Drafting] Section pair gen (sections 7-8) started
20:14:46.038 INF [Drafting] Section pair gen (sections 7-8) completed | duration_ms=14483
20:14:46.038 INF [Drafting] Section pair generated | section_label=sections 7-8 | length=1250
20:14:46.038 DBG [SelfRefine] Self-refine critique started
20:14:52.767 INF [SelfRefine] Self-refine critique completed | duration_ms=6730
20:14:52.769 INF [SelfRefine] Critique result | passes=False | confidence=0.9 | violation_count=9 | critical=0 | major=9 | minor=0
20:14:52.769 DBG [SelfRefine] Self-refine refinement started
20:15:28.676 INF [SelfRefine] Self-refine refinement completed | duration_ms=35908
20:15:28.676 DBG [SelfRefine] Self-refine critique started
20:15:33.662 INF [SelfRefine] Self-refine critique completed | duration_ms=4987
20:15:33.662 WRN [SelfRefine] Critique LLM call failed; treating as pass to avoid blocking user | error=AttributeError: 'NoneType' object has no attribute 'passes' | exc_info=True
20:15:33.662 INF [SelfRefine] Self-refine passed | iteration=1 | confidence=0.0 | cumulative_violations=9
20:15:33.662 INF [Drafting] Self-refine altered draft | iterations=2 | original_len=12469 | refined_len=10073
20:15:33.662 INF [Orchestrator] Synthesize phase started | agents_received=['Drafting'] | registry_size=1 | has_response_instructions=True
20:15:34.886 DBG [ChatStore] Turn saved | thread_id=e608305f-c16 | turn=2
```

**Warnings/errors in this turn (1):**

- `SelfRefine: Critique LLM call failed; treating as pass to avoid blocking user | error=AttributeError: 'NoneType' object has no attribute 'passes' | exc_info=True`

### Step 4 — what the client got back

- Sources: **1** (drafting)
- Response: **10,245 chars**, first 400:

```text
## IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE

C.S. No. ______ OF 2024

**IN THE MATTER OF:**

Mrs. Anjali Deshmukh
vs.
XYZ Multispecialty Hospital and Dr. Rakesh Nair

Mrs. Anjali Deshmukh,

Age: 42 years,

Occupation: [Not specified],

Residing at Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital,
Through its Director/Principal Officer,
Address: [Address of Hospital], Pun…
```

- Follow-ups: `What evidence is needed?`, `What's the statute of limitations?`, `What are typical settlement amounts?`

---

## Turn 3 — Case laws for the Rupa partition scenario

**Prompt (Citations):** Rupa is a adopted child. She doesnt have any siblings. her father passed away 5 years ago. She and her mothers are only legal heirs. Her father had 2 flats in pune. Her mother told her to leave the house and her mother says that she'll transfer the property to her cousins name. now rupa is scared th…

**Outcome:** 34.1s · agents `Scenario, Judgment, SCI_Judgment` · 30,514 chars · 100,107 tokens · $0.0337 · 11 LLM calls

### Step 1 — node timeline (from SSE)

| t+ | Event | Node it maps to |
|----|-------|-----------------|
| 0.014s | Validating query... | `guardrail_input` |
| 1.567s | Loading context... | `memory` |
| 6.988s | Planning search strategy... | `orchestrator_plan` |
| 21.012s | Analyzing legal scenario... | `scenario` |
| 27.713s | Searching Supreme Court judgments... | `sci_judgment` |
| 30.633s | Searching court judgments... | `judgment` |
| 30.641s | Injecting citations into draft... | `orchestrator_synthesize` |
| 30.642s | Finalizing... | `guardrail_output` |

Planned by orchestrator: `Scenario, Judgment, SCI_Judgment` → actually reported used: `Scenario, Judgment, SCI_Judgment`.

### Step 2 — every LLM call, in order

| # | Agent | Step | In | Out | Cache read | Cost |
|---|-------|------|----|-----|------------|------|
| 1 | Memory | `rewrite_query` | 2,368 | 99 | 0 | $0.0003 |
| 2 | Orchestrator | `classify_and_plan` | 1,462 | 67 | 0 | $0.0002 |
| 3 | Orchestrator | `extract_user_intent` | 5,896 | 364 | 5,440 | $0.0007 |
| 4 | Orchestrator | `rewrite_per_agent_queries` | 879 | 158 | 0 | $0.0002 |
| 5 | Judgment | `extract_metadata` | 1,001 | 116 | 0 | $0.0001 |
| 6 | Judgment | `relevance_judge` | 4,332 | 119 | 0 | $0.0005 |
| 7 | SCI_Judgment | `react_msg_1` | 7,774 | 88 | 5,839 | $0.0026 |
| 8 | SCI_Judgment | `react_msg_5` | 10,752 | 69 | 0 | $0.0034 |
| 9 | SCI_Judgment | `react_msg_9` | 22,842 | 96 | 0 | $0.0071 |
| 10 | SCI_Judgment | `react_msg_13` | 29,701 | 2,522 | 0 | $0.0152 |
| 11 | Judgment | `web_grounded` | 6,298 | 644 | 0 | $0.0035 |

Roll-up by agent:

| Agent | Calls | In | Out | Cache read | Cost |
|-------|-------|----|-----|------------|------|
| SCI_Judgment | 4 | 71,069 | 2,775 | 5,839 | $0.0283 |
| Judgment | 3 | 11,631 | 879 | 0 | $0.0041 |
| Orchestrator | 3 | 8,237 | 589 | 5,440 | $0.0011 |
| Memory | 1 | 2,368 | 99 | 0 | $0.0003 |

### Step 3 — internal decisions (server log)

```log
20:15:34.927 DBG [Graph] Routing to memory (guardrail passed)
20:15:34.929 DBG [Language] Language detected | lang=en | lang_name=English | snippet=Rupa is a adopted child. She doesnt have any siblings. her father passed away 5 
20:15:34.929 INF [Memory] Language detected | lang=en | query=Rupa is a adopted child. She doesnt have any siblings. her f
20:15:34.931 DBG [Memory] Loading chat history | thread_id=e608305f-c16
20:15:34.931 DBG [Memory] SQLite history load started | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:15:34.933 INF [Memory] SQLite history load completed | duration_ms=4 | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:15:34.935 INF [Memory] Chat history loaded from SQLite | thread_id=e608305f-c16 | turns=2 | messages=4 | has_summary=False | has_prev_intent=True | prev_task=Drafting | prev_artifact_kind=draft
20:15:34.935 INF [Memory] Chat history loaded | messages=4 | turns=2 | placeholder_history=False | has_summary=False
20:15:34.935 DBG [Memory] Query rewrite (LLM) started
20:15:36.475 INF [Memory] Query rewrite (LLM) completed | duration_ms=1538
20:15:36.477 INF [Memory] Query rewritten | original=Rupa is a adopted child. She doesnt have any siblings. her f | rewritten=Provide 5 or more case laws or citations relevant to a scena
20:15:36.477 INF [Memory] Agent completed | final_query=Provide 5 or more case laws or citations relevant to a scenario where Rupa, an adopted daughter with | query_changed=True | history_messages=4 | file_context_restored=False
20:15:36.483 DBG [Orchestrator] Intent extraction (v2) started
20:15:38.856 INF [Orchestrator] Intent extraction (v2) completed | duration_ms=2381
20:15:38.856 INF [Orchestrator] User intent extracted | format=prose | format_explicit=False | language=en | language_explicit=False | depth=standard | confidence=0.8
20:15:38.856 INF [Orchestrator] Multi-intent enrichment via UserIntent | extra=['Judgment', 'SCI_Judgment'] | all_agents=['Scenario', 'Judgment', 'SCI_Judgment']
20:15:38.856 DBG [Orchestrator] Per-agent query rewriting started
20:15:40.777 INF [Orchestrator] Per-agent query rewriting completed | duration_ms=1910
20:15:40.777 INF [Graph] Fan-out routing | tasks_planned=['Scenario', 'Judgment', 'SCI_Judgment'] | nodes=['sci_judgment', 'judgment', 'scenario'] | parallel_count=3
20:15:40.781 INF [Scenario] Agent started | query=Rupa, an adopted daughter with no siblings, is an heir to her deceased father's two flats in Pune. H | has_history=True | has_user_context=False | has_integration_context=False | using_agent_query=True
20:15:41.290 DBG [Judgment] Parallel metadata + ES search started
20:15:41.909 INF [Judgment] Regex fallback metadata | petitioners=[] | respondents=[] | year=None | court=None | topics=['property'] | size=5
20:15:42.149 INF [JudgmentSearch] case type match | case_type=land | hits=5
20:15:43.436 INF [Judgment] Metadata extracted | court= | petitioners=[] | respondents=[] | year=None | topics=['inheritance rights', 'adopted daughter', 'property dispute', 'disinheritance', 'heir property documents'] | size=10
20:15:43.436 INF [Judgment] Parallel metadata + ES search completed | duration_ms=2154
20:15:43.436 INF [Judgment] Preliminary ES search hit — skipping refined search | strategy=case_type:land
20:15:45.978 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=kerala high court/19af2c3a409d5339cae1694159e7bc3f35a9a275b51829f3def06859aee123 | error=Unable to locate credentials
20:15:45.980 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=madhya pradesh high court/c5db37c9c6502d1ed815c8157283ac134ff278a5f0e8204d07fbab | error=Unable to locate credentials
20:15:45.980 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=meghalaya high court/57e3ea4249bac4f21f7099074c1f49162288888cd4a12e12c6193e3a99d | error=Unable to locate credentials
20:15:45.982 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=calcutta high court/7e53955e7788d4f11b59c951bab68d75c5b0ac953dad7eb8232d35980fdf | error=Unable to locate credentials
20:15:45.982 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=bombay high court/3bcdb14479f168ba5e4eb2a6ad1d6a4fb1402df72a46676b7ede688e447bd1 | error=Unable to locate credentials
20:15:45.982 WRN [RetrievalGate] Async coarse semantic floor errored — fail-open | error=Path C:\Users\User\Lawtech-AI\models\bge-large-en-v1.5 not found
20:15:45.984 DBG [RetrievalGate] Relevance judge LLM started | agent=Judgment
20:15:48.009 INF [RetrievalGate] Relevance judge LLM completed | duration_ms=2025 | agent=Judgment
20:15:48.009 INF [Judgment] Relevance judge verdict | passed=False | strategy=case_type:land | top_match=PANACHIKKAL VELAYUDHAN ALIAS ABOOBACKER vs NARAYANIKUTTY | coarse_sim=0.0 | judge_relevant=False | judge_confidence=95 | matched_subject=inheritance righ
20:15:48.009 WRN [Judgment] Retrieved judgments failed relevance gate — falling back to web search | top_match=PANACHIKKAL VELAYUDHAN ALIAS ABOOBACKER vs NARAYANIKUTTY | coarse_sim=0.0 | judge_relevant=False | judge_confidence=95 | matched_subject=inheritanc
20:15:48.050 INF [AgentFallback] Web search fallback started | agent=Judgment | query=inheritance rights adopted daughter property dispute Pune mother disinheritance threat heir property
20:16:05.530 INF [AgentFallback] Web search fallback completed | agent=Judgment | response_len=3144 | tokens=9402 | web_sources=1
20:16:05.548 INF [Orchestrator] Synthesize phase started | agents_received=['Drafting', 'Scenario', 'Judgment', 'SCI_Judgment'] | registry_size=11 | has_response_instructions=True
20:16:05.548 INF [Orchestrator] Draft-aware synthesis starting | draft_len=10073 | citation_agents=['Scenario', 'Judgment', 'SCI_Judgment']
20:16:05.548 INF [Orchestrator] Draft synthesis completed (append-only) | enriched_len=30288 | citation_agents=['Scenario', 'Judgment', 'SCI_Judgment'] | total_tokens=95500
20:16:05.577 DBG [ChatStore] Fallback logged | agent=Judgment | tier=web | row_id=3
20:16:06.833 DBG [ChatStore] Turn saved | thread_id=e608305f-c16 | turn=3
20:16:09.012 INF [ChatStore] Summary regenerated | thread_id=e608305f-c16 | summary_len=1485 | turns_covered=3
```

**Warnings/errors in this turn (2):**

- `RetrievalGate: Async coarse semantic floor errored — fail-open | error=Path C:\Users\User\Lawtech-AI\models\bge-large-en-v1.5 not found`
- `Judgment: Retrieved judgments failed relevance gate — falling back to web search | top_match=PANACHIKKAL VELAYUDHAN ALIAS ABOOBACKER vs NARAYANIKUTTY | coarse_s`

### Step 4 — what the client got back

- Sources: **11** (drafting, judgment, scenario, sci_judgment)
- Response: **30,514 chars**, first 400:

```text
## IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE

C.S. No. ______ OF 2024

**IN THE MATTER OF:**

Mrs. Anjali Deshmukh
vs.
XYZ Multispecialty Hospital and Dr. Rakesh Nair

Mrs. Anjali Deshmukh,

Age: 42 years,

Occupation: [Not specified],

Residing at Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital,
Through its Director/Principal Officer,
Address: [Address of Hospital], Pun…
```

- Follow-ups: `Can Rupa claim father's property?`, `Can mother transfer property alone?`, `What if Rupa's name isn't on deeds?`

---

## Turn 4 — Section 65B electronic evidence case laws

**Prompt (Citations):** Provide case laws on the admissibility of electronic evidence under Section 65B of the Indian Evidence Act.

**Outcome:** 59.0s · agents `Newacts, Judgment, SCI_Judgment` · 44,037 chars · 1,208,994 tokens · $0.3659 · 28 LLM calls

### Step 1 — node timeline (from SSE)

| t+ | Event | Node it maps to |
|----|-------|-----------------|
| 0.024s | Validating query... | `guardrail_input` |
| 1.089s | Loading context... | `memory` |
| 4.628s | Planning search strategy... | `orchestrator_plan` |
| 16.693s | Searching court judgments... | `judgment` |
| 17.573s | Searching legal provisions... | `newacts` |
| 57.458s | Searching Supreme Court judgments... | `sci_judgment` |
| 57.469s | Injecting citations into draft... | `orchestrator_synthesize` |
| 57.469s | Finalizing... | `guardrail_output` |

Planned by orchestrator: `Newacts, Judgment, SCI_Judgment` → actually reported used: `Newacts, Judgment, SCI_Judgment`.

### Step 2 — every LLM call, in order

| # | Agent | Step | In | Out | Cache read | Cost |
|---|-------|------|----|-----|------------|------|
| 1 | Memory | `rewrite_query` | 2,777 | 25 | 0 | $0.0003 |
| 2 | Orchestrator | `classify_and_plan` | 1,742 | 82 | 791 | $0.0002 |
| 3 | Orchestrator | `extract_user_intent` | 6,162 | 290 | 5,553 | $0.0007 |
| 4 | Orchestrator | `rewrite_per_agent_queries` | 806 | 86 | 0 | $0.0001 |
| 5 | Newacts | `extract_metadata` | 319 | 40 | 0 | $0.0000 |
| 6 | Judgment | `extract_metadata` | 1,003 | 122 | 510 | $0.0001 |
| 7 | Judgment | `generate` | 0 | 36 | 0 | $0.0000 |
| 8 | Newacts | `generate` | 0 | 14 | 0 | $0.0000 |
| 9 | SCI_Judgment | `react_msg_1` | 7,750 | 42 | 0 | $0.0024 |
| 10 | SCI_Judgment | `react_msg_3` | 9,583 | 46 | 0 | $0.0030 |
| 11 | SCI_Judgment | `react_msg_6` | 17,628 | 64 | 5,997 | $0.0054 |
| 12 | SCI_Judgment | `react_msg_9` | 25,142 | 64 | 17,104 | $0.0077 |
| 13 | SCI_Judgment | `react_msg_12` | 32,741 | 64 | 7,068 | $0.0100 |
| 14 | SCI_Judgment | `react_msg_15` | 40,484 | 32 | 9,109 | $0.0122 |
| 15 | SCI_Judgment | `react_msg_17` | 44,142 | 32 | 24,308 | $0.0133 |
| 16 | SCI_Judgment | `react_msg_19` | 48,170 | 32 | 0 | $0.0145 |
| 17 | SCI_Judgment | `react_msg_21` | 51,911 | 32 | 47,659 | $0.0157 |
| 18 | SCI_Judgment | `react_msg_23` | 55,887 | 32 | 0 | $0.0168 |
| 19 | SCI_Judgment | `react_msg_25` | 59,605 | 33 | 6,089 | $0.0180 |
| 20 | SCI_Judgment | `react_msg_27` | 63,422 | 33 | 39,595 | $0.0191 |
| 21 | SCI_Judgment | `react_msg_29` | 67,432 | 33 | 0 | $0.0203 |
| 22 | SCI_Judgment | `react_msg_31` | 74,566 | 33 | 0 | $0.0225 |
| 23 | SCI_Judgment | `react_msg_33` | 81,912 | 33 | 43,721 | $0.0247 |
| 24 | SCI_Judgment | `react_msg_35` | 89,087 | 33 | 58,998 | $0.0268 |
| 25 | SCI_Judgment | `react_msg_37` | 96,202 | 33 | 51,897 | $0.0289 |
| 26 | SCI_Judgment | `react_msg_39` | 103,410 | 33 | 67,182 | $0.0311 |
| 27 | SCI_Judgment | `react_msg_41` | 110,688 | 33 | 102,838 | $0.0333 |
| 28 | SCI_Judgment | `react_msg_43` | 113,148 | 1,843 | 32,583 | $0.0386 |

Roll-up by agent:

| Agent | Calls | In | Out | Cache read | Cost |
|-------|-------|----|-----|------------|------|
| SCI_Judgment | 20 | 1,192,910 | 2,580 | 514,148 | $0.3643 |
| Orchestrator | 3 | 8,710 | 458 | 6,344 | $0.0011 |
| Memory | 1 | 2,777 | 25 | 0 | $0.0003 |
| Judgment | 2 | 1,003 | 158 | 510 | $0.0001 |
| Newacts | 2 | 319 | 54 | 0 | $0.0000 |

### Step 3 — internal decisions (server log)

```log
20:16:09.060 DBG [Graph] Routing to memory (guardrail passed)
20:16:09.060 DBG [Language] Language detected | lang=en | lang_name=English | snippet=Provide case laws on the admissibility of electronic evidence under Section 65B 
20:16:09.060 INF [Memory] Language detected | lang=en | query=Provide case laws on the admissibility of electronic evidenc
20:16:09.060 DBG [Memory] Loading chat history | thread_id=e608305f-c16
20:16:09.060 DBG [Memory] SQLite history load started | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:16:09.066 INF [Memory] SQLite history load completed | duration_ms=4 | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:16:09.066 INF [Memory] Chat history loaded from SQLite | thread_id=e608305f-c16 | turns=3 | messages=6 | has_summary=True | has_prev_intent=True | prev_task=Scenario | prev_artifact_kind=(none)
20:16:09.066 INF [Memory] Chat history loaded | messages=6 | turns=3 | placeholder_history=False | has_summary=True
20:16:09.070 DBG [Memory] Query rewrite (LLM) started
20:16:10.125 INF [Memory] Query rewrite (LLM) completed | duration_ms=1056
20:16:10.125 INF [Memory] Query rewritten | original=Provide case laws on the admissibility of electronic evidenc | rewritten=Case laws on admissibility of electronic evidence under Sect
20:16:10.126 INF [Memory] Agent completed | final_query=Case laws on admissibility of electronic evidence under Section 65B of the Indian Evidence Act, 1872 | query_changed=True | history_messages=6 | file_context_restored=False
20:16:10.126 DBG [Orchestrator] Intent extraction (v2) started
20:16:12.317 INF [Orchestrator] Intent extraction (v2) completed | duration_ms=2192
20:16:12.321 INF [Orchestrator] User intent extracted | format=prose | format_explicit=False | language=en | language_explicit=False | depth=standard | confidence=0.9
20:16:12.321 INF [Orchestrator] Multi-intent enrichment via UserIntent | extra=['Judgment', 'SCI_Judgment'] | all_agents=['Newacts', 'Judgment', 'SCI_Judgment']
20:16:12.323 DBG [Orchestrator] Per-agent query rewriting started
20:16:13.641 INF [Orchestrator] Per-agent query rewriting completed | duration_ms=1323
20:16:13.647 INF [Graph] Fan-out routing | tasks_planned=['Newacts', 'Judgment', 'SCI_Judgment'] | nodes=['newacts', 'sci_judgment', 'judgment'] | parallel_count=3
20:16:13.658 DBG [Judgment] Parallel metadata + ES search started
20:16:13.668 INF [Judgment] Regex fallback metadata | petitioners=[] | respondents=[] | year=None | court=None | topics=[] | size=10
20:16:13.880 INF [JudgmentSearch] multi-tier match | hits=10
20:16:14.881 DBG [Newacts] ES search started
20:16:14.997 INF [Newacts] ES search completed | duration_ms=119
20:16:15.002 INF [Newacts] Enriching with nearby sections | center=65B | current_hits=1
20:16:15.002 INF [Newacts] Relevance gate skipped -- exact filter query | act=Indian Evidence Act 1872 | sections=1
20:16:15.607 INF [Judgment] Parallel metadata + ES search completed | duration_ms=1950
20:16:15.607 INF [Judgment] Preliminary ES search hit — skipping refined search | strategy=multi_tier
20:16:15.608 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=bombay high court/07d5dec22616cad2b653e96bcd929383809365d35a082ba8955f94b6c8cb1c | error=Unable to locate credentials
20:16:15.608 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=bombay high court/daff9d1282c0762711cef5d346d192847bdd1815b6ce496b6748b15ba5f1ce | error=Unable to locate credentials
20:16:15.610 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=bombay high court/86054bb5e59953c4ff5ab0531c26217b4f0c500cb421718f10dc9057321223 | error=Unable to locate credentials
20:16:15.618 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=delhi high court/8688ecbf70bd2c6a2f384bfbf7a182370952c3356588a61779efb1b0a19fee0 | error=Unable to locate credentials
20:16:15.622 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=kerala high court/e121e40e3ded0b07c0ebeedd13892c6f5314e6320500e2968a071e57444a11 | error=Unable to locate credentials
20:16:15.624 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=madhya pradesh high court/56634270e331008a2a518e77b442f90db2c5815dd1fec4392b5af4 | error=Unable to locate credentials
20:16:15.628 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=gujarat high court/f81104dedae7e8eff3cd23745ae4c4c6880aa2034261f880bac0a97a763c8 | error=Unable to locate credentials
20:16:15.628 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=gujarat high court/d2afd4d0fbf4bb651012f3dd0a3f66639a78dc0d1605b8b547184ce1e8d37 | error=Unable to locate credentials
20:16:15.632 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=supreme court/ARJUN PANDITRAO KHOTKAR vs. KAILASH KUSHANRAO GORANTYAL AND ORS..p | error=Unable to locate credentials
20:16:15.634 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=delhi high court/7d49e4b32a4508bf2cdb0ef84457bcf84880d314c9b90866cff9084bf6d04b5 | error=Unable to locate credentials
20:16:15.634 INF [Judgment] Relevance gate skipped -- exact section lookup | sections=['section 65b indian evidence act'] | hits=10 | strategy=multi_tier
20:16:26.597 INF [Newacts] Agent completed | act=Indian Evidence Act 1872 | response_len=11915 | tokens=14 | hits=1
20:17:06.495 INF [Orchestrator] Synthesize phase started | agents_received=['Drafting', 'Scenario', 'Judgment', 'SCI_Judgment', 'Newacts'] | registry_size=18 | has_response_instructions=True
20:17:06.495 INF [Orchestrator] Draft-aware synthesis starting | draft_len=10073 | citation_agents=['Scenario', 'Judgment', 'SCI_Judgment', 'Newacts']
20:17:06.495 INF [Orchestrator] Draft synthesis completed (append-only) | enriched_len=43835 | citation_agents=['Scenario', 'Judgment', 'SCI_Judgment', 'Newacts'] | total_tokens=1207794
20:17:07.974 DBG [ChatStore] Turn saved | thread_id=e608305f-c16 | turn=4
```

### Step 4 — what the client got back

- Sources: **18** (drafting, judgment, newacts, scenario, sci_judgment)
- Response: **44,037 chars**, first 400:

```text
## IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE

C.S. No. ______ OF 2024

**IN THE MATTER OF:**

Mrs. Anjali Deshmukh
vs.
XYZ Multispecialty Hospital and Dr. Rakesh Nair

Mrs. Anjali Deshmukh,

Age: 42 years,

Occupation: [Not specified],

Residing at Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital,
Through its Director/Principal Officer,
Address: [Address of Hospital], Pun…
```

- Follow-ups: `What are the key conditions for 65B certification?`, `How does the court handle unsigned certificates?`, `Are there exceptions to 65B requirements?`

---

## Turn 5 — Anticipatory bail case laws

**Prompt (Citations):** Provide citations or case laws related to anticipatory bail

**Outcome:** 23.6s · agents `Judgment, SCI_Judgment` · 46,919 chars · 66,064 tokens · $0.0222 · 11 LLM calls

### Step 1 — node timeline (from SSE)

| t+ | Event | Node it maps to |
|----|-------|-----------------|
| 0.027s | Validating query... | `guardrail_input` |
| 1.278s | Loading context... | `memory` |
| 4.343s | Planning search strategy... | `orchestrator_plan` |
| 19.152s | Searching court judgments... | `judgment` |
| 22.248s | Searching Supreme Court judgments... | `sci_judgment` |
| 22.261s | Injecting citations into draft... | `orchestrator_synthesize` |
| 22.262s | Finalizing... | `guardrail_output` |

Planned by orchestrator: `Judgment, SCI_Judgment` → actually reported used: `Judgment, SCI_Judgment`.

### Step 2 — every LLM call, in order

| # | Agent | Step | In | Out | Cache read | Cost |
|---|-------|------|----|-----|------------|------|
| 1 | Memory | `rewrite_query` | 3,189 | 10 | 0 | $0.0003 |
| 2 | Orchestrator | `classify_and_plan` | 1,727 | 55 | 0 | $0.0002 |
| 3 | Orchestrator | `extract_user_intent` | 6,151 | 264 | 5,548 | $0.0007 |
| 4 | Orchestrator | `rewrite_per_agent_queries` | 788 | 42 | 0 | $0.0001 |
| 5 | Judgment | `extract_metadata` | 995 | 89 | 0 | $0.0001 |
| 6 | Judgment | `relevance_judge` | 4,489 | 67 | 0 | $0.0005 |
| 7 | Judgment | `generate` | 0 | 37 | 0 | $0.0000 |
| 8 | SCI_Judgment | `react_msg_1` | 7,739 | 29 | 0 | $0.0024 |
| 9 | SCI_Judgment | `react_msg_3` | 7,789 | 27 | 6,802 | $0.0024 |
| 10 | SCI_Judgment | `react_msg_5` | 9,262 | 68 | 7,828 | $0.0029 |
| 11 | SCI_Judgment | `react_msg_9` | 20,727 | 2,520 | 0 | $0.0125 |

Roll-up by agent:

| Agent | Calls | In | Out | Cache read | Cost |
|-------|-------|----|-----|------------|------|
| SCI_Judgment | 4 | 45,517 | 2,644 | 14,630 | $0.0203 |
| Orchestrator | 3 | 8,666 | 361 | 5,548 | $0.0010 |
| Judgment | 3 | 5,484 | 193 | 0 | $0.0006 |
| Memory | 1 | 3,189 | 10 | 0 | $0.0003 |

### Step 3 — internal decisions (server log)

```log
20:17:08.020 DBG [Graph] Routing to memory (guardrail passed)
20:17:08.020 DBG [Language] Language detected | lang=en | lang_name=English | snippet=Provide citations or case laws related to anticipatory bail
20:17:08.020 INF [Memory] Language detected | lang=en | query=Provide citations or case laws related to anticipatory bail
20:17:08.020 DBG [Memory] Loading chat history | thread_id=e608305f-c16
20:17:08.020 DBG [Memory] SQLite history load started | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:17:08.036 INF [Memory] SQLite history load completed | duration_ms=4 | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:17:08.036 INF [Memory] Chat history loaded from SQLite | thread_id=e608305f-c16 | turns=4 | messages=8 | has_summary=True | has_prev_intent=True | prev_task=Newacts | prev_artifact_kind=(none)
20:17:08.036 INF [Memory] Chat history loaded | messages=8 | turns=4 | placeholder_history=False | has_summary=True
20:17:08.036 DBG [Memory] Query rewrite (LLM) started
20:17:09.266 INF [Memory] Query rewrite (LLM) completed | duration_ms=1228
20:17:09.266 INF [Memory] Query rewritten | original=Provide citations or case laws related to anticipatory bail | rewritten=Supreme Court and High Court citations on anticipatory bail
20:17:09.266 INF [Memory] Agent completed | final_query=Supreme Court and High Court citations on anticipatory bail | query_changed=True | history_messages=8 | file_context_restored=False
20:17:09.271 DBG [Orchestrator] Intent extraction (v2) started
20:17:11.281 INF [Orchestrator] Intent extraction (v2) completed | duration_ms=2011
20:17:11.283 INF [Orchestrator] User intent extracted | format=prose | format_explicit=False | language=en | language_explicit=False | depth=standard | confidence=0.9
20:17:11.283 INF [Orchestrator] Multi-intent enrichment via UserIntent | extra=['SCI_Judgment'] | all_agents=['Judgment', 'SCI_Judgment']
20:17:11.283 DBG [Orchestrator] Per-agent query rewriting started
20:17:12.337 INF [Orchestrator] Per-agent query rewriting completed | duration_ms=1054
20:17:12.339 INF [Graph] Fan-out routing | tasks_planned=['Judgment', 'SCI_Judgment'] | nodes=['sci_judgment', 'judgment'] | parallel_count=2
20:17:12.345 DBG [Judgment] Parallel metadata + ES search started
20:17:12.352 INF [Judgment] Regex fallback metadata | petitioners=[] | respondents=[] | year=None | court=supreme court | topics=['bail', 'anticipatory bail'] | size=5
20:17:12.591 INF [JudgmentSearch] case type match | case_type=bail | hits=5
20:17:13.624 INF [Judgment] Parallel metadata + ES search completed | duration_ms=1282
20:17:13.624 INF [Judgment] Preliminary ES search hit — skipping refined search | strategy=case_type:bail
20:17:13.628 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=supreme court/LAXMAN PRASAD PANDEY vs. THE STATE OF UTTAR PRADESH & ANR..pdf | error=Unable to locate credentials
20:17:13.629 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=supreme court/MS. X vs. THE STATE OF MAHARASHTRA AND ANOTHER.pdf | error=Unable to locate credentials
20:17:13.629 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=supreme court/PREM GIRI vs. STATE OF RAJASTHAN.pdf | error=Unable to locate credentials
20:17:13.630 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=supreme court/SUSHILA AGGARWAL & ORS. vs. STATE (NCT OF DELHI) & ORS..pdf | error=Unable to locate credentials
20:17:13.630 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=supreme court/FIDA HUSSAIN BOHRA vs. THE STATE OF MAHARASHTRA.pdf | error=Unable to locate credentials
20:17:13.630 WRN [RetrievalGate] Async coarse semantic floor errored — fail-open | error=Path C:\Users\User\Lawtech-AI\models\bge-large-en-v1.5 not found
20:17:13.630 DBG [RetrievalGate] Relevance judge LLM started | agent=Judgment
20:17:15.431 INF [RetrievalGate] Relevance judge LLM completed | duration_ms=1798 | agent=Judgment
20:17:15.431 INF [Judgment] Relevance judge verdict | passed=True | strategy=case_type:bail | top_match=LAXMAN PRASAD PANDEY vs THE STATE OF UTTAR PRADESH | coarse_sim=0.0 | judge_relevant=True | judge_confidence=95 | matched_subject= | reason=The retrieved 
20:17:30.247 INF [Orchestrator] Synthesize phase started | agents_received=['Drafting', 'Scenario', 'Judgment', 'SCI_Judgment', 'Newacts'] | registry_size=13 | has_response_instructions=True
20:17:30.247 INF [Orchestrator] Draft-aware synthesis starting | draft_len=10073 | citation_agents=['Scenario', 'Judgment', 'SCI_Judgment', 'Newacts']
20:17:30.247 INF [Orchestrator] Draft synthesis completed (append-only) | enriched_len=46709 | citation_agents=['Scenario', 'Judgment', 'SCI_Judgment', 'Newacts'] | total_tokens=60466
20:17:31.541 DBG [ChatStore] Turn saved | thread_id=e608305f-c16 | turn=5
```

**Warnings/errors in this turn (1):**

- `RetrievalGate: Async coarse semantic floor errored — fail-open | error=Path C:\Users\User\Lawtech-AI\models\bge-large-en-v1.5 not found`

### Step 4 — what the client got back

- Sources: **13** (drafting, judgment, newacts, scenario, sci_judgment)
- Response: **46,919 chars**, first 400:

```text
## IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE

C.S. No. ______ OF 2024

**IN THE MATTER OF:**

Mrs. Anjali Deshmukh
vs.
XYZ Multispecialty Hospital and Dr. Rakesh Nair

Mrs. Anjali Deshmukh,

Age: 42 years,

Occupation: [Not specified],

Residing at Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital,
Through its Director/Principal Officer,
Address: [Address of Hospital], Pun…
```

- Follow-ups: `What is the process for applying?`, `What evidence is needed for success?`, `What are the grounds for denial?`

---

## Turn 6 — Medical negligence — plaintiff + defendant arguments (EN)

**Prompt (Argument Generation):** My client, Mrs. Anjali Deshmukh, aged 42, resident of Pune, underwent gallbladder surgery at XYZ Multispecialty Hospital on 12th March 2024. During the surgery, due to the negligence of the attending surgeon, Dr. Rakesh Nair, her bile duct was damaged. Post-surgery, she suffered from severe abdomina…

**Outcome:** 66.7s · agents `Scenario, Judgment, SCI_Judgment` · 47,153 chars · 485,041 tokens · $0.1500 · 14 LLM calls

### Step 1 — node timeline (from SSE)

| t+ | Event | Node it maps to |
|----|-------|-----------------|
| 0.03s | Validating query... | `guardrail_input` |
| 0.039s | Loading context... | `memory` |
| 2.609s | Planning search strategy... | `orchestrator_plan` |
| 27.318s | Searching court judgments... | `judgment` |
| 33.232s | Analyzing legal scenario... | `scenario` |
| 62.146s | Searching Supreme Court judgments... | `sci_judgment` |
| 62.156s | Injecting citations into draft... | `orchestrator_synthesize` |
| 62.157s | Finalizing... | `guardrail_output` |

Planned by orchestrator: `Scenario, Judgment, SCI_Judgment` → actually reported used: `Scenario, Judgment, SCI_Judgment`.

### Step 2 — every LLM call, in order

| # | Agent | Step | In | Out | Cache read | Cost |
|---|-------|------|----|-----|------------|------|
| 1 | Orchestrator | `classify_and_plan` | 1,881 | 55 | 0 | $0.0002 |
| 2 | Orchestrator | `extract_user_intent` | 6,305 | 423 | 6,172 | $0.0008 |
| 3 | Judgment | `extract_metadata` | 1,152 | 183 | 545 | $0.0002 |
| 4 | Judgment | `relevance_judge` | 4,320 | 74 | 0 | $0.0005 |
| 5 | Judgment | `web_grounded` | 6,491 | 1,720 | 0 | $0.0062 |
| 6 | SCI_Judgment | `react_msg_1` | 7,969 | 86 | 0 | $0.0026 |
| 7 | SCI_Judgment | `react_msg_5` | 11,025 | 92 | 0 | $0.0035 |
| 8 | SCI_Judgment | `react_msg_10` | 26,516 | 128 | 0 | $0.0083 |
| 9 | SCI_Judgment | `react_msg_15` | 41,468 | 128 | 0 | $0.0128 |
| 10 | SCI_Judgment | `react_msg_20` | 55,706 | 128 | 26,374 | $0.0170 |
| 11 | SCI_Judgment | `react_msg_25` | 69,576 | 96 | 54,865 | $0.0211 |
| 12 | SCI_Judgment | `react_msg_29` | 76,947 | 32 | 0 | $0.0232 |
| 13 | SCI_Judgment | `react_msg_31` | 80,103 | 32 | 11,183 | $0.0241 |
| 14 | SCI_Judgment | `react_msg_33` | 80,845 | 2,092 | 69,130 | $0.0295 |

Roll-up by agent:

| Agent | Calls | In | Out | Cache read | Cost |
|-------|-------|----|-----|------------|------|
| SCI_Judgment | 9 | 450,155 | 2,814 | 161,552 | $0.1421 |
| Judgment | 3 | 11,963 | 1,977 | 545 | $0.0069 |
| Orchestrator | 2 | 8,186 | 478 | 6,172 | $0.0010 |

### Step 3 — internal decisions (server log)

```log
20:17:31.602 DBG [Graph] Routing to memory (guardrail passed)
20:17:31.605 DBG [Language] Language detected | lang=en | lang_name=English | snippet=My client, Mrs. Anjali Deshmukh, aged 42, resident of Pune, underwent gallbladde
20:17:31.605 INF [Memory] Language detected | lang=en | query=My client, Mrs. Anjali Deshmukh, aged 42, resident of Pune, 
20:17:31.607 DBG [Memory] Loading chat history | thread_id=e608305f-c16
20:17:31.607 DBG [Memory] SQLite history load started | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:17:31.611 INF [Memory] SQLite history load completed | duration_ms=4 | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:17:31.611 INF [Memory] Chat history loaded from SQLite | thread_id=e608305f-c16 | turns=5 | messages=10 | has_summary=True | has_prev_intent=True | prev_task=Judgment | prev_artifact_kind=(none)
20:17:31.611 INF [Memory] Chat history loaded | messages=10 | turns=5 | placeholder_history=False | has_summary=True
20:17:31.611 DBG [Memory] Skipping rewrite — query already standalone-length | query_chars=785
20:17:31.611 INF [Memory] Agent completed | final_query=My client, Mrs. Anjali Deshmukh, aged 42, resident of Pune, underwent gallbladder surgery at XYZ Mul | query_changed=False | history_messages=10 | file_context_restored=False
20:17:31.611 DBG [Orchestrator] Intent extraction (v2) started
20:17:34.160 INF [Orchestrator] Intent extraction (v2) completed | duration_ms=2545
20:17:34.162 INF [Orchestrator] User intent extracted | format=prose | format_explicit=False | language=en | language_explicit=False | depth=standard | confidence=0.8
20:17:34.162 INF [Orchestrator] Multi-intent enrichment via UserIntent | extra=['Judgment', 'SCI_Judgment'] | all_agents=['Scenario', 'Judgment', 'SCI_Judgment']
20:17:34.162 DBG [Orchestrator] Skipping per-agent query rewrite — query already standalone-length | query_chars=792 | agents=['Scenario', 'Judgment', 'SCI_Judgment']
20:17:34.162 INF [Graph] Fan-out routing | tasks_planned=['Scenario', 'Judgment', 'SCI_Judgment'] | nodes=['sci_judgment', 'judgment', 'scenario'] | parallel_count=3
20:17:34.169 INF [Scenario] Agent started | query=My client, Mrs. Anjali Deshmukh, aged 42, resident of Pune, underwent gallbladder surgery at XYZ Mul | has_history=True | has_user_context=False | has_integration_context=False | using_agent_query=False
20:17:34.171 DBG [Judgment] Parallel metadata + ES search started
20:17:34.177 INF [Judgment] Regex fallback metadata | petitioners=[] | respondents=[] | year=2024 | court=None | topics=['negligence', 'compensation'] | size=5
20:17:34.252 INF [JudgmentSearch] multi-tier match | hits=5
20:17:35.827 INF [Judgment] Parallel metadata + ES search completed | duration_ms=1657
20:17:35.827 INF [Judgment] Preliminary ES search hit — skipping refined search | strategy=multi_tier
20:17:35.829 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=kerala high court/c3bb2da125981493a22d3fa733b21cc9f25437fa1af7603b31210a768f5f29 | error=Unable to locate credentials
20:17:35.829 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=bombay high court/e91f61b918bb97bec9f4637264ace2982b279a7a9f17c63696764177957f42 | error=Unable to locate credentials
20:17:35.829 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=bombay high court/8875477c6a48736635b7a45d7d0fada6fe72273cbc2e55163f10de3d38ee97 | error=Unable to locate credentials
20:17:35.829 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=rajasthan high court/d84754b06b7d8e0cd983c8ffaf73d080abc62f43efc2599389b63d81aeb | error=Unable to locate credentials
20:17:35.829 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=bombay high court/b7e3a19752ca391962cfcf255a3ea0cd398ab89551b43eef25f3844c06141c | error=Unable to locate credentials
20:17:35.829 WRN [RetrievalGate] Async coarse semantic floor errored — fail-open | error=Path C:\Users\User\Lawtech-AI\models\bge-large-en-v1.5 not found
20:17:35.833 DBG [RetrievalGate] Relevance judge LLM started | agent=Judgment
20:17:37.721 INF [RetrievalGate] Relevance judge LLM completed | duration_ms=1888 | agent=Judgment
20:17:37.721 INF [Judgment] Relevance judge verdict | passed=False | strategy=multi_tier | top_match=K.K. VARGHESE vs THE KERALA STATE ELECTRICITY BOARD LTD. | coarse_sim=0.0 | judge_relevant=False | judge_confidence=99 | matched_subject=motor vehicle accide
20:17:37.721 WRN [Judgment] Retrieved judgments failed relevance gate — falling back to web search | top_match=K.K. VARGHESE vs THE KERALA STATE ELECTRICITY BOARD LTD. | coarse_sim=0.0 | judge_relevant=False | judge_confidence=99 | matched_subject=motor vehi
20:17:37.721 INF [AgentFallback] Web search fallback started | agent=Judgment | query=My client, Mrs. Anjali Deshmukh, aged 42, resident of Pune, underwent gallbladder surgery at XYZ Mul
20:17:58.853 INF [AgentFallback] Web search fallback completed | agent=Judgment | response_len=8665 | tokens=17679 | web_sources=20
20:17:58.903 DBG [ChatStore] Fallback logged | agent=Judgment | tier=web | row_id=4
20:18:33.722 INF [Orchestrator] Synthesize phase started | agents_received=['Drafting', 'Scenario', 'Judgment', 'SCI_Judgment', 'Newacts'] | registry_size=61 | has_response_instructions=True
20:18:33.722 INF [Orchestrator] Draft-aware synthesis starting | draft_len=10073 | citation_agents=['Scenario', 'Judgment', 'SCI_Judgment', 'Newacts']
20:18:33.722 INF [Orchestrator] Draft synthesis completed (append-only) | enriched_len=46947 | citation_agents=['Scenario', 'Judgment', 'SCI_Judgment', 'Newacts'] | total_tokens=551127
20:18:35.005 DBG [ChatStore] Turn saved | thread_id=e608305f-c16 | turn=6
20:18:38.271 INF [ChatStore] Summary regenerated | thread_id=e608305f-c16 | summary_len=2482 | turns_covered=6
```

**Warnings/errors in this turn (2):**

- `RetrievalGate: Async coarse semantic floor errored — fail-open | error=Path C:\Users\User\Lawtech-AI\models\bge-large-en-v1.5 not found`
- `Judgment: Retrieved judgments failed relevance gate — falling back to web search | top_match=K.K. VARGHESE vs THE KERALA STATE ELECTRICITY BOARD LTD. | coarse_s`

### Step 4 — what the client got back

- Sources: **64** (drafting, judgment, newacts, scenario, sci_judgment)
- Response: **47,153 chars**, first 400:

```text
## IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE

C.S. No. ______ OF 2024

**IN THE MATTER OF:**

Mrs. Anjali Deshmukh
vs.
XYZ Multispecialty Hospital and Dr. Rakesh Nair

Mrs. Anjali Deshmukh,

Age: 42 years,

Occupation: [Not specified],

Residing at Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital,
Through its Director/Principal Officer,
Address: [Address of Hospital], Pun…
```

- Follow-ups: `What evidence proves negligence?`, `What are punitive damages?`, `Can hospital sue doctor?`

---

## Turn 7 — Same medical-negligence argument prompt in Marathi

**Prompt (Argument Generation):** माझ्या क्लायंट, श्रीमती अंजली देशमुख, वय ४२, पुण्यातील रहिवासी, यांची १२ मार्च २०२४ रोजी XYZ मल्टिस्पेशालिटी हॉस्पिटलमध्ये पित्ताशयाची शस्त्रक्रिया झाली. शस्त्रक्रियेदरम्यान, उपस्थित सर्जन डॉ. राकेश नायर यांच्या निष्काळजीपणामुळे, त्यांच्या पित्तनलिकेचे नुकसान झाले. शस्त्रक्रियेनंतर, त्यांना तीव्र पो…

**Outcome:** 17.3s · agents `Scenario` · 47,762 chars · 9,176 tokens · $0.0011 · 2 LLM calls

### Step 1 — node timeline (from SSE)

| t+ | Event | Node it maps to |
|----|-------|-----------------|
| 0.029s | Validating query... | `guardrail_input` |
| 0.038s | Loading context... | `memory` |
| 3.163s | Planning search strategy... | `orchestrator_plan` |
| 15.977s | Analyzing legal scenario... | `scenario` |
| 15.988s | Injecting citations into draft... | `orchestrator_synthesize` |
| 15.989s | Finalizing... | `guardrail_output` |

Planned by orchestrator: `Scenario` → actually reported used: `Scenario`.

### Step 2 — every LLM call, in order

| # | Agent | Step | In | Out | Cache read | Cost |
|---|-------|------|----|-----|------------|------|
| 1 | Orchestrator | `classify_and_plan` | 2,148 | 50 | 826 | $0.0002 |
| 2 | Orchestrator | `extract_user_intent` | 6,573 | 405 | 5,716 | $0.0008 |

Roll-up by agent:

| Agent | Calls | In | Out | Cache read | Cost |
|-------|-------|----|-----|------------|------|
| Orchestrator | 2 | 8,721 | 455 | 6,542 | $0.0011 |

### Step 3 — internal decisions (server log)

```log
20:18:38.321 DBG [Graph] Routing to memory (guardrail passed)
20:18:38.323 DBG [Language] Language detected | lang=mr | lang_name=Marathi | snippet=माझ्या क्लायंट, श्रीमती अंजली देशमुख, वय ४२, पुण्यातील रहिवासी, यांची १२ मार्च २
20:18:38.323 INF [Memory] Language detected | lang=mr | query=माझ्या क्लायंट, श्रीमती अंजली देशमुख, वय ४२, पुण्यातील रहिवा
20:18:38.323 DBG [Memory] Loading chat history | thread_id=e608305f-c16
20:18:38.323 DBG [Memory] SQLite history load started | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:18:38.330 INF [Memory] SQLite history load completed | duration_ms=4 | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:18:38.330 INF [Memory] Chat history loaded from SQLite | thread_id=e608305f-c16 | turns=6 | messages=10 | has_summary=True | has_prev_intent=True | prev_task=Scenario | prev_artifact_kind=(none)
20:18:38.330 INF [Memory] Chat history loaded | messages=10 | turns=5 | placeholder_history=False | has_summary=True
20:18:38.330 DBG [Memory] Skipping rewrite — query already standalone-length | query_chars=764
20:18:38.330 INF [Memory] Agent completed | final_query=माझ्या क्लायंट, श्रीमती अंजली देशमुख, वय ४२, पुण्यातील रहिवासी, यांची १२ मार्च २०२४ रोजी XYZ मल्टिस् | query_changed=False | history_messages=10 | file_context_restored=False
20:18:38.337 DBG [Orchestrator] Intent extraction (v2) started
20:18:41.455 INF [Orchestrator] Intent extraction (v2) completed | duration_ms=3117
20:18:41.455 INF [Orchestrator] User intent extracted | format=prose | format_explicit=False | language=mr | language_explicit=False | depth=standard | confidence=0.8
20:18:41.455 INF [Graph] Fan-out routing | tasks_planned=['Scenario'] | nodes=['scenario'] | parallel_count=1
20:18:41.460 INF [Scenario] Agent started | query=My client, Mrs. Anjali Deshmukh, aged 42, resident of Pune, underwent gallbladder surgery at XYZ Mul | has_history=True | has_user_context=False | has_integration_context=False | using_agent_query=False
20:18:54.274 INF [Orchestrator] Synthesize phase started | agents_received=['Drafting', 'Scenario', 'Judgment', 'SCI_Judgment', 'Newacts'] | registry_size=30 | has_response_instructions=True
20:18:54.274 INF [Orchestrator] Draft-aware synthesis starting | draft_len=10073 | citation_agents=['Scenario', 'Judgment', 'SCI_Judgment', 'Newacts']
20:18:54.276 INF [Orchestrator] Draft synthesis completed (append-only) | enriched_len=47532 | citation_agents=['Scenario', 'Judgment', 'SCI_Judgment', 'Newacts'] | total_tokens=521054
20:18:55.588 DBG [ChatStore] Turn saved | thread_id=e608305f-c16 | turn=7
```

### Step 4 — what the client got back

- Sources: **33** (drafting, judgment, newacts, scenario, sci_judgment)
- Response: **47,762 chars**, first 400:

```text
## IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE

C.S. No. ______ OF 2024

**IN THE MATTER OF:**

Mrs. Anjali Deshmukh
vs.
XYZ Multispecialty Hospital and Dr. Rakesh Nair

Mrs. Anjali Deshmukh,

Age: 42 years,

Occupation: [Not specified],

Residing at Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital,
Through its Director/Principal Officer,
Address: [Address of Hospital], Pun…
```

- Follow-ups: `पुढील कायदेशीर पावले काय आहेत?`, `पुरावा कसा गोळा करावा?`, `केस जिंकण्याची शक्यता किती?`

---

## Turn 8 — S.420 IPC — civil-not-criminal defence arguments

**Prompt (Argument Generation):** My client is facing a criminal case under Section 420 IPC for alleged cheating. However, there was no fraudulent intention from the beginning, and it was purely a civil dispute arising from a failed business agreement. Give me arguments to defend the client and emphasize that this is a case of civil…

**Outcome:** 28.0s · agents `Newacts, Scenario, Judgment, SCI_Judgment` · 34,016 chars · 145,735 tokens · $0.0455 · 15 LLM calls

### Step 1 — node timeline (from SSE)

| t+ | Event | Node it maps to |
|----|-------|-----------------|
| 0.017s | Validating query... | `guardrail_input` |
| 1.463s | Loading context... | `memory` |
| 5.669s | Planning search strategy... | `orchestrator_plan` |
| 11.624s | Searching legal provisions... | `newacts` |
| 20.39s | Analyzing legal scenario... | `scenario` |
| 22.003s | Searching court judgments... | `judgment` |
| 26.643s | Searching Supreme Court judgments... | `sci_judgment` |
| 26.653s | Injecting citations into draft... | `orchestrator_synthesize` |
| 26.654s | Finalizing... | `guardrail_output` |

Planned by orchestrator: `Newacts, Scenario, Judgment, SCI_Judgment` → actually reported used: `Newacts, Scenario, Judgment, SCI_Judgment`.

### Step 2 — every LLM call, in order

| # | Agent | Step | In | Out | Cache read | Cost |
|---|-------|------|----|-----|------------|------|
| 1 | Memory | `rewrite_query` | 3,694 | 63 | 1,021 | $0.0004 |
| 2 | Orchestrator | `classify_and_plan` | 2,012 | 75 | 816 | $0.0002 |
| 3 | Orchestrator | `extract_user_intent` | 6,438 | 360 | 6,230 | $0.0008 |
| 4 | Orchestrator | `rewrite_per_agent_queries` | 846 | 140 | 0 | $0.0001 |
| 5 | Newacts | `extract_metadata` | 316 | 42 | 0 | $0.0000 |
| 6 | Judgment | `extract_metadata` | 1,000 | 123 | 0 | $0.0001 |
| 7 | Newacts | `generate` | 0 | 20 | 0 | $0.0000 |
| 8 | Judgment | `generate` | 0 | 29 | 0 | $0.0000 |
| 9 | SCI_Judgment | `react_msg_1` | 8,025 | 82 | 0 | $0.0026 |
| 10 | SCI_Judgment | `react_msg_4` | 9,753 | 41 | 0 | $0.0030 |
| 11 | SCI_Judgment | `react_msg_6` | 9,815 | 46 | 8,827 | $0.0031 |
| 12 | SCI_Judgment | `react_msg_9` | 17,511 | 64 | 5,974 | $0.0054 |
| 13 | SCI_Judgment | `react_msg_12` | 22,832 | 64 | 0 | $0.0070 |
| 14 | SCI_Judgment | `react_msg_15` | 29,615 | 32 | 10,078 | $0.0090 |
| 15 | SCI_Judgment | `react_msg_17` | 30,968 | 1,729 | 22,179 | $0.0136 |

Roll-up by agent:

| Agent | Calls | In | Out | Cache read | Cost |
|-------|-------|----|-----|------------|------|
| SCI_Judgment | 7 | 128,519 | 2,058 | 47,058 | $0.0437 |
| Orchestrator | 3 | 9,296 | 575 | 7,046 | $0.0012 |
| Memory | 1 | 3,694 | 63 | 1,021 | $0.0004 |
| Judgment | 2 | 1,000 | 152 | 0 | $0.0001 |
| Newacts | 2 | 316 | 62 | 0 | $0.0000 |

### Step 3 — internal decisions (server log)

```log
20:18:55.647 DBG [Graph] Routing to memory (guardrail passed)
20:18:55.650 DBG [Language] Language detected | lang=en | lang_name=English | snippet=My client is facing a criminal case under Section 420 IPC for alleged cheating. 
20:18:55.650 INF [Memory] Language detected | lang=en | query=My client is facing a criminal case under Section 420 IPC fo
20:18:55.652 DBG [Memory] Loading chat history | thread_id=e608305f-c16
20:18:55.652 DBG [Memory] SQLite history load started | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:18:55.655 INF [Memory] SQLite history load completed | duration_ms=4 | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:18:55.655 INF [Memory] Chat history loaded from SQLite | thread_id=e608305f-c16 | turns=7 | messages=10 | has_summary=True | has_prev_intent=True | prev_task=Scenario | prev_artifact_kind=(none)
20:18:55.655 INF [Memory] Chat history loaded | messages=10 | turns=5 | placeholder_history=False | has_summary=True
20:18:55.657 DBG [Memory] Query rewrite (LLM) started
20:18:57.091 INF [Memory] Query rewrite (LLM) completed | duration_ms=1433
20:18:57.093 INF [Memory] Query rewritten | original=My client is facing a criminal case under Section 420 Indian | rewritten=Provide arguments to defend a client facing a criminal case 
20:18:57.093 INF [Memory] Agent completed | final_query=Provide arguments to defend a client facing a criminal case under Section 420 of the Indian Penal Co | query_changed=True | history_messages=10 | file_context_restored=False
20:18:57.098 DBG [Orchestrator] Intent extraction (v2) started
20:18:59.537 INF [Orchestrator] Intent extraction (v2) completed | duration_ms=2448
20:18:59.537 INF [Orchestrator] User intent extracted | format=prose | format_explicit=False | language=en | language_explicit=False | depth=standard | confidence=0.9
20:18:59.537 INF [Orchestrator] Multi-intent enrichment via UserIntent | extra=['Judgment', 'SCI_Judgment', 'Legislation'] | all_agents=['Newacts', 'Scenario', 'Judgment', 'SCI_Judgment', 'Legislation']
20:18:59.537 DBG [Orchestrator] Per-agent query rewriting started
20:19:01.273 INF [Orchestrator] Per-agent query rewriting completed | duration_ms=1725
20:19:01.275 INF [Graph] Fan-out routing | tasks_planned=['Newacts', 'Scenario', 'Judgment', 'SCI_Judgment'] | nodes=['newacts', 'sci_judgment', 'judgment', 'scenario'] | parallel_count=4
20:19:01.288 INF [Newacts] Agent started | query=Section 420 IPC cheating fraudulent intention civil dispute business agreement | using_agent_query=True
20:19:01.288 INF [Scenario] Agent started | query=Client is accused of cheating under Section 420 of the Indian Penal Code, 1860. The defense is that  | has_history=True | has_user_context=False | has_integration_context=False | using_agent_query=True
20:19:01.292 INF [Judgment] Agent started | query=cheating Section 420 IPC fraudulent intention civil dispute business agreement | using_agent_query=True
20:19:01.296 DBG [Judgment] Parallel metadata + ES search started
20:19:01.296 INF [SCI_Judgment] Agent started | query=cheating Section 420 IPC fraudulent intention civil dispute business agreement | has_user_context=False | using_agent_query=True
20:19:01.308 INF [Judgment] Regex fallback metadata | petitioners=[] | respondents=[] | year=None | court=None | topics=['cheating', 'fraud'] | size=5
20:19:01.424 INF [JudgmentSearch] multi-tier match | hits=5
20:19:02.404 DBG [Newacts] ES search started
20:19:02.516 INF [Newacts] ES search completed | duration_ms=119
20:19:02.525 INF [Newacts] Enriching with nearby sections | center=420 | current_hits=1
20:19:02.605 INF [Newacts] Relevance gate skipped -- exact filter query | act=The Indian Penal Code, 1860 | sections=1
20:19:02.605 INF [Newacts] Cross-act detection | is_cross_act=False | query_preview=section 420 ipc cheating fraudulent intention civil dispute business agreement my client is facing a criminal case under section 420 ipc for alleged cheating. h
20:19:03.020 INF [Judgment] Metadata extracted | court= | petitioners=[] | respondents=[] | year=None | topics=['cheating', 'fraudulent intention', 'civil dispute', 'business agreement'] | size=5
20:19:03.020 INF [Judgment] Parallel metadata + ES search completed | duration_ms=1723
20:19:03.020 INF [Judgment] Preliminary ES search hit — skipping refined search | strategy=multi_tier
20:19:03.020 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=madhya pradesh high court/a89c131382a814c13e54680a8c30fa3d1b774edd48f2a6d76d1a2b | error=Unable to locate credentials
20:19:03.020 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=madhya pradesh high court/6f76d3be2473c33c3dcb88e495747f9002a9e8bd14dc8c8ae8fbcb | error=Unable to locate credentials
20:19:03.020 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=rajasthan high court/40f06424791bd7b428b812c1bdab8507a73ac2d44db7527e2dd3013cd99 | error=Unable to locate credentials
20:19:03.025 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=gauhati high court/0e5b5caa243f46fe653500678339ebdf0e3f9f08a239b0839bc90b05e00c4 | error=Unable to locate credentials
20:19:03.025 DBG [Storage] S3 head_object error treated as exists | bucket=lawttorney | key=madhya pradesh high court/66f0bb9eeab99ee3ff8a63a2181767a0889f6b7ea95b415d56f436 | error=Unable to locate credentials
20:19:03.025 INF [Judgment] Relevance gate skipped -- exact section lookup | sections=['section 420 ipc'] | hits=5 | strategy=multi_tier
20:19:07.255 INF [Newacts] Agent completed | act=The Indian Penal Code, 1860 | response_len=1610 | tokens=20 | hits=3
20:19:22.275 INF [Orchestrator] Synthesize phase started | agents_received=['Drafting', 'Scenario', 'Judgment', 'SCI_Judgment', 'Newacts'] | registry_size=15 | has_response_instructions=True
20:19:22.275 INF [Orchestrator] Draft-aware synthesis starting | draft_len=10073 | citation_agents=['Scenario', 'Judgment', 'SCI_Judgment', 'Newacts']
20:19:22.278 INF [Orchestrator] Draft synthesis completed (append-only) | enriched_len=33798 | citation_agents=['Scenario', 'Judgment', 'SCI_Judgment', 'Newacts'] | total_tokens=187339
20:19:23.664 DBG [ChatStore] Turn saved | thread_id=e608305f-c16 | turn=8
```

### Step 4 — what the client got back

- Sources: **15** (drafting, judgment, newacts, scenario, sci_judgment)
- Response: **34,016 chars**, first 400:

```text
## IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE

C.S. No. ______ OF 2024

**IN THE MATTER OF:**

Mrs. Anjali Deshmukh
vs.
XYZ Multispecialty Hospital and Dr. Rakesh Nair

Mrs. Anjali Deshmukh,

Age: 42 years,

Occupation: [Not specified],

Residing at Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital,
Through its Director/Principal Officer,
Address: [Address of Hospital], Pun…
```

- Follow-ups: `What evidence proves lack of intent?`, `How to prove civil dispute elements?`, `Can Section 482 CRPC help?`

---

## Turn 9 — Defective product liability under CPA 2019

**Prompt (Consumer Laws):** Scenario 1: Defective Product Liability Prompt: Priya purchases a brand-new refrigerator from a well-known electronics store. After a week, it stops working, and the manufacturer refuses to repair or replace it, claiming that the issue was caused by improper installation. What legal options does Pri…

**Outcome:** 21.8s · agents `Legislation, Scenario` · 35,299 chars · 14,902 tokens · $0.0017 · 6 LLM calls

### Step 1 — node timeline (from SSE)

| t+ | Event | Node it maps to |
|----|-------|-----------------|
| 0.031s | Validating query... | `guardrail_input` |
| 1.633s | Loading context... | `memory` |
| 5.437s | Planning search strategy... | `orchestrator_plan` |
| 12.475s | Searching legislation... | `legislation` |
| 15.67s | Analyzing legal scenario... | `scenario` |
| 15.68s | Injecting citations into draft... | `orchestrator_synthesize` |
| 15.681s | Finalizing... | `guardrail_output` |

Planned by orchestrator: `Legislation, Scenario` → actually reported used: `Legislation, Scenario`.

### Step 2 — every LLM call, in order

| # | Agent | Step | In | Out | Cache read | Cost |
|---|-------|------|----|-----|------------|------|
| 1 | Memory | `rewrite_query` | 3,662 | 58 | 0 | $0.0004 |
| 2 | Orchestrator | `classify_and_plan` | 2,007 | 73 | 815 | $0.0002 |
| 3 | Orchestrator | `extract_user_intent` | 6,449 | 368 | 6,235 | $0.0008 |
| 4 | Orchestrator | `rewrite_per_agent_queries` | 834 | 102 | 0 | $0.0001 |
| 5 | Legislation | `relevance_judge` | 1,246 | 76 | 0 | $0.0002 |
| 6 | Legislation | `generate` | 0 | 27 | 0 | $0.0000 |

Roll-up by agent:

| Agent | Calls | In | Out | Cache read | Cost |
|-------|-------|----|-----|------------|------|
| Orchestrator | 3 | 9,290 | 543 | 7,050 | $0.0011 |
| Memory | 1 | 3,662 | 58 | 0 | $0.0004 |
| Legislation | 2 | 1,246 | 103 | 0 | $0.0002 |

### Step 3 — internal decisions (server log)

```log
20:19:23.723 DBG [Graph] Routing to memory (guardrail passed)
20:19:23.723 DBG [Language] Language detected | lang=en | lang_name=English | snippet=Scenario 1: Defective Product Liability Prompt: Priya purchases a brand-new refr
20:19:23.723 INF [Memory] Language detected | lang=en | query=Scenario 1: Defective Product Liability Prompt: Priya purcha
20:19:23.723 DBG [Memory] Loading chat history | thread_id=e608305f-c16
20:19:23.723 DBG [Memory] SQLite history load started | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:19:23.738 INF [Memory] SQLite history load completed | duration_ms=4 | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:19:23.738 INF [Memory] Chat history loaded from SQLite | thread_id=e608305f-c16 | turns=8 | messages=10 | has_summary=True | has_prev_intent=True | prev_task=Scenario | prev_artifact_kind=(none)
20:19:23.738 INF [Memory] Chat history loaded | messages=10 | turns=5 | placeholder_history=False | has_summary=True
20:19:23.738 DBG [Memory] Query rewrite (LLM) started
20:19:25.326 INF [Memory] Query rewrite (LLM) completed | duration_ms=1591
20:19:25.326 INF [Memory] Query rewritten | original=Scenario 1: Defective Product Liability Prompt: Priya purcha | rewritten=What legal options does Priya have under the Consumer Protec
20:19:25.326 INF [Memory] Agent completed | final_query=What legal options does Priya have under the Consumer Protection Act, 2019, to address a defective r | query_changed=True | history_messages=10 | file_context_restored=False
20:19:25.337 DBG [Orchestrator] Intent extraction (v2) started
20:19:27.544 INF [Orchestrator] Intent extraction (v2) completed | duration_ms=2207
20:19:27.544 INF [Orchestrator] User intent extracted | format=prose | format_explicit=False | language=en | language_explicit=False | depth=standard | confidence=0.8
20:19:27.544 INF [Orchestrator] Multi-intent enrichment via UserIntent | extra=['Scenario'] | all_agents=['Legislation', 'Scenario']
20:19:27.544 DBG [Orchestrator] Per-agent query rewriting started
20:19:29.124 INF [Orchestrator] Per-agent query rewriting completed | duration_ms=1581
20:19:29.128 INF [Graph] Fan-out routing | tasks_planned=['Legislation', 'Scenario'] | nodes=['legislation', 'scenario'] | parallel_count=2
20:19:29.135 INF [Scenario] Agent started | query=Priya purchased a defective refrigerator from a well-known electronics store. The refrigerator stopp | has_history=True | has_user_context=False | has_integration_context=False | using_agent_query=True
20:19:29.364 DBG [Legislation] ES search iteration | iteration=1 | variation=Consumer Protection Act 2019 defective product refrigerator  | hits=20
20:19:29.364 INF [Legislation] Most relevant source identified | source=The Consumer Protection Act, 2019.csv | total_hits=20 | unique_sources=11
20:19:29.421 DBG [Legislation] Targeted source search done | source=The Consumer Protection Act, 2019.csv | hits=5
20:19:29.421 WRN [RetrievalGate] Async coarse semantic floor errored — fail-open | error=Path C:\Users\User\Lawtech-AI\models\bge-large-en-v1.5 not found
20:19:29.421 DBG [RetrievalGate] Relevance judge LLM started | agent=Legislation
20:19:31.072 INF [RetrievalGate] Relevance judge LLM completed | duration_ms=1651 | agent=Legislation
20:19:31.072 INF [Legislation] Relevance judge verdict | passed=True | source=The Consumer Protection Act, 2019.csv | search_mode=standard | coarse_sim=0.0 | judge_relevant=True | judge_confidence=100 | matched_subject= | reason=The retrieved sections of the Co
20:19:39.372 INF [Orchestrator] Synthesize phase started | agents_received=['Drafting', 'Scenario', 'Judgment', 'SCI_Judgment', 'Newacts', 'Legislation'] | registry_size=16 | has_response_instructions=True
20:19:39.372 INF [Orchestrator] Draft-aware synthesis starting | draft_len=10073 | citation_agents=['Scenario', 'Judgment', 'SCI_Judgment', 'Newacts', 'Legislation']
20:19:39.372 INF [Orchestrator] Draft synthesis completed (append-only) | enriched_len=35069 | citation_agents=['Scenario', 'Judgment', 'SCI_Judgment', 'Newacts', 'Legislation'] | total_tokens=187389
20:19:40.537 DBG [ChatStore] Turn saved | thread_id=e608305f-c16 | turn=9
20:19:45.447 INF [ChatStore] Summary regenerated | thread_id=e608305f-c16 | summary_len=3314 | turns_covered=9
```

**Warnings/errors in this turn (1):**

- `RetrievalGate: Async coarse semantic floor errored — fail-open | error=Path C:\Users\User\Lawtech-AI\models\bge-large-en-v1.5 not found`

### Step 4 — what the client got back

- Sources: **20** (drafting, judgment, legislation, newacts, scenario, sci_judgment)
- Response: **35,299 chars**, first 400:

```text
## IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE

C.S. No. ______ OF 2024

**IN THE MATTER OF:**

Mrs. Anjali Deshmukh
vs.
XYZ Multispecialty Hospital and Dr. Rakesh Nair

Mrs. Anjali Deshmukh,

Age: 42 years,

Occupation: [Not specified],

Residing at Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital,
Through its Director/Principal Officer,
Address: [Address of Hospital], Pun…
```

- Follow-ups: `What evidence is needed?`, `Can I claim damages?`, `What is the timeline?`

---

## Turn 10 — Review the attached draft (PDF built from turn 1)

**Prompt (Pdf):** Review this draft and suggest changes, if any.

**Outcome:** 12.7s · agents `Document` · 35,299 chars · 11,808 tokens · $0.0033 · 3 LLM calls

### Step 1 — node timeline (from SSE)

| t+ | Event | Node it maps to |
|----|-------|-----------------|
| 0.005s | file_processing — Processing uploaded files... | *(pre-graph)* |
| 0.009s | file_processing — Compressing local_turn1_draft.pdf... | *(pre-graph)* |
| 0.208s | file_processing — Extracting text from local_turn1_draft.pdf... | *(pre-graph)* |
| 0.476s | file_processing — local_turn1_draft.pdf: 4 pages | *(pre-graph)* |
| 0.476s | file_processing — Embedding local_turn1_draft.pdf into vector store... | *(pre-graph)* |
| 0.488s | file_processing — Processed 1 pdf | *(pre-graph)* |
| 0.488s | file_processing — Processed 1 pdf | *(pre-graph)* |
| 0.492s | Validating query... | `guardrail_input` |
| 0.497s | Loading context... | `memory` |
| 2.526s | Planning search strategy... | `orchestrator_plan` |
| 11.381s | Searching uploaded documents... | `document` |
| 11.381s | Injecting citations into draft... | `orchestrator_synthesize` |
| 11.381s | Finalizing... | `guardrail_output` |

Planned by orchestrator: `Document` → actually reported used: `Document`.

### Step 2 — every LLM call, in order

| # | Agent | Step | In | Out | Cache read | Cost |
|---|-------|------|----|-----|------------|------|
| 1 | Orchestrator | `classify_and_plan` | 2,176 | 51 | 0 | $0.0002 |
| 2 | Orchestrator | `extract_user_intent` | 6,575 | 273 | 5,717 | $0.0008 |
| 3 | Document | `qa_chromadb` | 2,045 | 688 | 0 | $0.0023 |

Roll-up by agent:

| Agent | Calls | In | Out | Cache read | Cost |
|-------|-------|----|-----|------------|------|
| Orchestrator | 2 | 8,751 | 324 | 5,717 | $0.0010 |
| Document | 1 | 2,045 | 688 | 0 | $0.0023 |

### Step 3 — internal decisions (server log)

```log
20:19:45.863 DBG [FileProcessor] PDF compression skipped (no worthwhile saving) | file=local_turn1_draft.pdf | original_bytes=2034
20:19:46.135 ERR [FileProcessor] ChromaDB storage failed | error=Path C:\Users\User\Lawtech-AI\models\all-MiniLM-L6-v2 not found
20:19:46.135 DBG [ChatStore] Thread file saved | thread_id=e608305f-c16 | file=local_turn1_draft.pdf | gemini=False
20:19:46.135 INF [FileProcessor] File processed | file=local_turn1_draft.pdf | type=pdf | gemini=False | chromadb=False | text_len=743
20:19:46.135 INF [FileProcessor] File processing complete | total=1 | summary=Processed 1 pdf | chromadb=0
20:19:46.135 DBG [Graph] Routing to memory (guardrail passed)
20:19:46.135 DBG [Language] Language detected | lang=en | lang_name=English | snippet=Review this draft and suggest changes, if any.
20:19:46.135 INF [Memory] Language detected | lang=en | query=Review this draft and suggest changes, if any.
20:19:46.135 DBG [Memory] Loading chat history | thread_id=e608305f-c16
20:19:46.135 DBG [Memory] SQLite history load started | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:19:46.152 INF [Memory] SQLite history load completed | duration_ms=4 | thread_id=e608305f-c169-4b09-86ad-75aa894b704c
20:19:46.152 INF [Memory] Chat history loaded from SQLite | thread_id=e608305f-c16 | turns=9 | messages=10 | has_summary=True | has_prev_intent=True | prev_task=Legislation | prev_artifact_kind=(none)
20:19:46.152 INF [Memory] Chat history loaded | messages=10 | turns=5 | placeholder_history=False | has_summary=True
20:19:46.152 INF [Memory] Skipping query rewrite — files attached this turn | file_names=['local_turn1_draft.pdf']
20:19:46.152 INF [Memory] Agent completed | final_query=Review this draft and suggest changes, if any. | query_changed=False | history_messages=10 | file_context_restored=False
20:19:46.152 INF [Memory] Preserving new file_context from this turn (NOT overwriting) | files=['local_turn1_draft.pdf']
20:19:46.154 INF [Orchestrator] File context hint added for classification | file_names=['local_turn1_draft.pdf']
20:19:46.154 DBG [Orchestrator] Intent extraction (v2) started
20:19:47.488 INF [Orchestrator] Classify+plan completed | task=Document | agents=['Document'] | source=llm | reasoning=The user has uploaded a document and is asking for a review and suggestions for changes, which falls under the Document 
20:19:48.173 INF [Orchestrator] Intent extraction (v2) completed | duration_ms=2021
20:19:48.173 INF [Orchestrator] User intent extracted | format=prose | format_explicit=False | language=en | language_explicit=False | depth=standard | confidence=0.7
20:19:48.173 INF [Orchestrator] Plan phase completed | task=Document | agents_planned=['Document'] | agent_count=1 | agent_queries_generated=0 | has_response_instructions=True
20:19:48.173 INF [Graph] Fan-out routing | tasks_planned=['Document'] | nodes=['document'] | parallel_count=1
20:19:48.173 INF [Document] Using extracted_texts fallback (Chroma path empty) | files=1 | chroma_collections=0
20:19:48.173 DBG [Document] Document QA generation started
20:19:48.173 DBG [Language] Language detected | lang=en | lang_name=English | snippet=--- Page 4 ---
20:19:57.015 INF [Document] Document QA generation completed | duration_ms=8832
20:19:57.019 INF [Orchestrator] Synthesize phase started | agents_received=['Drafting', 'Scenario', 'Judgment', 'SCI_Judgment', 'Newacts', 'Legislation', 'Document'] | registry_size=17 | has_response_instructions=True
20:19:57.019 INF [Orchestrator] Drafting+file: dropping Document from citation block (content already incorporated into the draft)
20:19:57.019 INF [Orchestrator] Draft-aware synthesis starting | draft_len=10073 | citation_agents=['Scenario', 'Judgment', 'SCI_Judgment', 'Newacts', 'Legislation']
20:19:57.021 INF [Orchestrator] Draft synthesis completed (append-only) | enriched_len=35069 | citation_agents=['Scenario', 'Judgment', 'SCI_Judgment', 'Newacts', 'Legislation'] | total_tokens=187389
20:19:57.023 INF [Guardrail] Output sanitization started | response_len=35069 | task=Document
20:19:58.278 DBG [ChatStore] Turn saved | thread_id=e608305f-c16 | turn=10
```

**Warnings/errors in this turn (1):**

- `FileProcessor: ChromaDB storage failed | error=Path C:\Users\User\Lawtech-AI\models\all-MiniLM-L6-v2 not found`

### Step 4 — what the client got back

- Sources: **20** (drafting, judgment, legislation, newacts, scenario, sci_judgment)
- Response: **35,299 chars**, first 400:

```text
## IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE

C.S. No. ______ OF 2024

**IN THE MATTER OF:**

Mrs. Anjali Deshmukh
vs.
XYZ Multispecialty Hospital and Dr. Rakesh Nair

Mrs. Anjali Deshmukh,

Age: 42 years,

Occupation: [Not specified],

Residing at Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital,
Through its Director/Principal Officer,
Address: [Address of Hospital], Pun…
```

- Follow-ups: `What damages can be claimed?`, `What evidence is needed?`, `What is the next procedural step?`

---
