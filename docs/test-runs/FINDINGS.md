# Single-thread prompt-suite run — findings

**Date:** 2026-08-17 · **Suite:** every prompt in [`docs/test prompt.txt`](../test%20prompt.txt), sent
**in order, inside one thread**, on two environments.

| Environment | URL | Thread | Turns | Wall clock | Tokens | Cost |
|---|---|---|---|---|---|---|
| Prod | `https://api.lawttorney.com` | `1e37e0a0-…` | 10/10 OK | 524s | 1,057,167 | $0.86 |
| Local | `http://127.0.0.1:5000` (this working tree) | `e608305f-…` | 10/10 OK | 592s | 2,306,977 | $1.37 |

No turn returned an HTTP error. **Every turn from #3 onward returned substantially
wrong content anyway.** The failure is invisible to status-code monitoring, which is
why it has survived.

Companion documents:

- [`API_REQUESTS_AND_RESPONSES_PROD.md`](API_REQUESTS_AND_RESPONSES_PROD.md) — user prompt + exact HTTP request + exact response, per turn (prod)
- [`API_REQUESTS_AND_RESPONSES_LOCAL.md`](API_REQUESTS_AND_RESPONSES_LOCAL.md) — same, local
- [`LOCAL_FLOW_ANALYSIS.md`](LOCAL_FLOW_ANALYSIS.md) — per-turn node timeline, every LLM call, and the server-log decision trace
- [`PROD_VS_LOCAL.md`](PROD_VS_LOCAL.md) — routing/cost/latency diff

---

## F1 — CRITICAL: agent results from earlier turns are never cleared, and get re-synthesised into every later answer

### What happens

The graph is invoked with `config = {"configurable": {"thread_id": i.thread_id}}`
([core/chat_runner.py:370](../../core/chat_runner.py#L370)) against a compiled graph that
carries a checkpointer ([core/graph.py:273-287](../../core/graph.py#L273-L287)). So the whole
`LegalAgentState` survives between turns of a thread. `agent_results` is declared with a
merge reducer:

```python
# core/state.py:88-90, :182
def _merge_agent_results(existing: dict, new: dict) -> dict:
    return {**existing, **new}

agent_results: Annotated[dict[str, AgentResult], _merge_agent_results]
```

Keys are agent *names*. A new turn therefore only overwrites the agents it re-runs;
every agent that ran in an **earlier** turn stays in the dict with its **stale** content,
and `orchestrator_synthesize` consumes the whole dict.

### Evidence — the synthesizer's own log line, local run

`agents_received` grows monotonically across a single thread:

| Turn | Agents that actually ran | `agents_received` at synthesis |
|------|--------------------------|-------------------------------|
| 1 | Drafting | `['Drafting']` |
| 2 | Drafting | `['Drafting']` |
| 3 | Scenario, Judgment, SCI_Judgment | `['Drafting', 'Scenario', 'Judgment', 'SCI_Judgment']` |
| 5 | Judgment, SCI_Judgment | `['Drafting', 'Scenario', 'Judgment', 'SCI_Judgment', 'Newacts']` |
| 9 | Legislation, Scenario | `['Drafting', 'Scenario', 'Judgment', 'SCI_Judgment', 'Newacts', 'Legislation']` |
| 10 | Document | `['Drafting', 'Scenario', 'Judgment', 'SCI_Judgment', 'Newacts', 'Legislation', 'Document']` |

By turn 10 the synthesizer is handed a `Drafting` result generated **nine turns earlier**
and treats it as live material.

### Evidence — the shape of the responses

Response length grows monotonically, and each answer opens with the *same* stale
turn-2 draft:

| Turn | Prod chars | Shared prefix with previous turn |
|------|-----------|----------------------------------|
| 2 | 13,242 | 52 chars |
| 3 | 35,842 | 13,014 (98% of turn 2) |
| 6 | 45,051 | 13,071 |
| 8 | 60,486 | 13,074 |
| 9 | 68,893 | 13,649 |
| 10 | 68,893 | **68,893 — turn 9 reproduced byte-for-byte** |

That stable ~13,074-char prefix is the turn-2 medical-negligence plaint being re-emitted
verbatim at the top of every answer for the rest of the thread — including on turns
asking about anticipatory bail, s.65B evidence, and a refrigerator warranty.

### User-visible consequence

Prod turn 9 asked *"What legal options does Priya have under the Consumer Protection
Act, 2019?"*. The 68,893-character answer contains **neither "Priya" nor "refrigerator"**.
The question was simply not answered. (Local turn 9 did answer it — but buried under
~10,000 characters of unrelated stale draft.)

### Reproduced on both environments

Prod runs PostgreSQL chat store + PostgreSQL checkpointer; local runs SQLite +
in-memory `MemorySaver`. Both show identical behaviour, so this is application logic,
not infrastructure.

---

## F2 — CRITICAL: the final turn returns the previous turn's answer verbatim and ignores the uploaded PDF

Turn 10 uploads a PDF and asks *"Review this draft and suggest changes, if any."*
The file is accepted and processed correctly (`pdf_extract_done: 4 pages`,
`chroma_store_start`, `all_files_done`), the `Document` agent is planned and runs
(one `qa_chromadb` LLM call). The response is nevertheless **byte-identical** to turn 9's.

### Controlled isolation

Same prompt, same full 4-page PDF, only the thread differs:

| Condition | Prod result | Local result |
|---|---|---|
| **Fresh thread** | 10,532 chars — *"The draft plaint is well-structured … here are some suggestions"* | 5,978 chars — *"Here's a review of the draft plaint with suggested changes"* |
| **Same thread as turns 1-9** | 68,893 chars — identical to turn 9, no review | 35,299 chars — identical to turn 9, no review |

The document pipeline is healthy. The thread state is what breaks it. Raw data in
`control_freshthread_raw.json` and `control_samethread_fullpdf_raw.json`.

---

## F3 — The later answers are not being *generated* at all; they are assembled from stored state

Output tokens billed vs characters delivered. Natural English runs ~4 chars per output
token, so anything much above that means text arrived from somewhere other than the model:

| Turn | Prod output tokens | Prod chars | chars / output token |
|------|-------------------|------------|----------------------|
| 1 | 17,752 | 10,273 | 0.6 |
| 2 | 15,316 | 13,242 | 0.9 |
| 4 | 3,038 | 54,886 | 18.1 |
| 7 | **462** | **45,460** | **98.4** |
| 9 | **687** | **68,893** | **100.3** |

Turn 9 delivered 68,893 characters on 687 output tokens. That is roughly 25× more text
than the model produced. It is direct confirmation of F1: the bulk of every late-thread
answer is copied out of accumulated state, not written for the question asked.

This also means **token-based cost metrics understate the damage** — the responses are
huge and cheap, so a cost dashboard shows nothing wrong.

---

## F4 — HIGH: the `SCI_Judgment` ReAct loop is bounded by wall-clock only, and blew 1.2M tokens on one turn

Local turn 4 (s.65B case law) consumed **1,208,994 tokens** — 20× the same prompt on prod
(61,341). Breakdown of that turn's largest calls:

```
SCI_Judgment  react_msg_43   in=113,148  out=1,843
SCI_Judgment  react_msg_41   in=110,688  out=   33
SCI_Judgment  react_msg_39   in=103,410  out=   33
SCI_Judgment  react_msg_37   in= 96,202  out=   33
…20 ReAct calls, 1,195,490 tokens total
```

The loop re-sends a transcript that grows every step (67k → 113k input tokens), while
most steps emit 33 output tokens — i.e. just another tool call. `create_react_agent` is
invoked with **no `recursion_limit`** and only a 90-second `asyncio.wait_for`
([agents/sci_judgment.py:64-82](../../agents/sci_judgment.py#L64-L82)):

```python
agent = create_react_agent(llm, tools, prompt=SystemMessage(...))
result = await asyncio.wait_for(agent.ainvoke({"messages": [("user", query)]}), timeout=90)
```

A time budget does not bound token spend — a fast model just iterates more. Turn 6 hit
the same path for 452,969 tokens. SCI_Judgment alone accounts for roughly half of the
entire local run's 2.3M tokens.

**Fix direction:** pass `config={"recursion_limit": N}` to `ainvoke` and keep the timeout
as a backstop.

---

## F5 — MEDIUM: drafting self-refine gives up with major violations still open

Local turn 1, from the server log:

```
Critique result | passes=False | violation_count=9 | critical=0 | major=9 | minor=0
Self-refine refinement completed | duration_ms=24006
Critique result | passes=False | violation_count=8 | critical=0 | major=7 | minor=1
Self-refine refinement completed | duration_ms=29996
Critique result | passes=False | violation_count=4 | critical=0 | major=4 | minor=0
WRN Self-refine max iterations exhausted | iterations=2 | final_violations=4
```

Three critiques and two refinements — 92 seconds and ~140k tokens, half of turn 1's
total cost — and the draft still ships with 4 major violations. Violations do fall
(9 → 8 → 4), so the loop works; it just runs out of budget before converging. Worth
deciding whether to raise the iteration cap for drafting or to surface the residual
violations to the caller instead of discarding them silently.

---

## F6 — MEDIUM: the Marathi turn came back in English

Turn 7 is the medical-negligence prompt written in Marathi. Detection is correct at
every stage locally:

```
[Language]     Language detected | lang=mr | lang_name=Marathi
[Orchestrator] User intent extracted | language=mr | confidence=0.8
[Graph]        Fan-out routing | tasks_planned=['Scenario']
```

Yet the delivered answer contains **0 Devanagari characters** locally, and only 12% on
prod. The same synthesis step received five agents' worth of stale **English** results
(`agents_received=['Drafting','Scenario','Judgment','SCI_Judgment','Newacts']`), which
dominate the output. This is a downstream consequence of F1, not an independent
language-layer bug — but it is the most user-visible symptom: *a Marathi question gets
an English answer.*

---

## F7 — INFO: routing divergence between environments on turn 9

| | Prod | Local |
|---|---|---|
| Turn 9 (Consumer Protection Act, 2019) | `Newacts`, `Scenario` | `Legislation`, `Scenario` |

Local is the better route. `Newacts` indexes BNS/BNSS/BSA; the Consumer Protection Act
belongs to the `Legislation` corpus — exactly the wrong-corpus case
[`_select_citation_agents`](../../agents/orchestrator.py#L370) warns about. Routing agreed
on the other 9/10 turns, so this is planner non-determinism rather than a config gap.

---

## Environment deltas worth knowing

| | Prod | Local |
|---|---|---|
| Chat store | PostgreSQL | SQLite |
| Checkpointer | PostgreSQL | in-memory `MemorySaver` (health reports `degraded`) |
| Embedding models | present | `bge-large-en-v1.5` and `all-MiniLM-L6-v2` missing from `./models/` |
| Turn-10 upload → ChromaDB | stored | **failed** — `ChromaDB storage failed \| error=Path …all-MiniLM-L6-v2 not found`, so the turn ran with `chromadb=False` |
| Response cache | enabled, 65 entries | enabled, cold |

None of these affected the findings — F1, F2 and F3 reproduce identically on both.
The local Chroma failure is worth a separate note: the `Document` agent still ran its
`qa_chromadb` step and still produced a correct review in the fresh-thread control, so
the uploaded text reached it through the raw extracted-text path. That is the intended
resilience, but it also means **a silent embedding-store failure is not surfaced to the
caller** — the SSE stream reported `Processed 1 pdf` with no error event.

---

## Suggested order of work

1. **F1** — clear or scope `agent_results` per turn. The state is keyed by thread for
   memory purposes, but `agent_results` is per-turn working data and should not survive
   the turn. Everything else in this report except F4 is a symptom of it.
2. **F2** — verify with the fresh-vs-same-thread control above; it should resolve with F1.
3. **F4** — add `recursion_limit` to the ReAct invocations (`sci_judgment`, `gst_judgment`).
4. **F5/F6** — re-measure after F1 lands; both may move on their own.

### Regression test worth keeping

The cheapest permanent guard is an assertion on `agents_received` size, or on the
chars-per-output-token ratio from F3 — both catch this class of bug without needing a
human to read a 68k-character answer.

---

## How this run was produced

```bash
# one thread, all 10 prompts, SSE captured with timings
python tests/run_thread_prompt_suite.py --base-url https://api.lawttorney.com --label prod
python tests/run_thread_prompt_suite.py --base-url http://127.0.0.1:5000  --label local \
    --capture-log logs/agent.log

# markdown reports
python tests/gen_thread_report.py --raw docs/test-runs/prod_raw.json \
    --api-report docs/test-runs/API_REQUESTS_AND_RESPONSES_PROD.md
python tests/gen_thread_report.py --raw docs/test-runs/local_raw.json \
    --api-report docs/test-runs/API_REQUESTS_AND_RESPONSES_LOCAL.md \
    --flow-report docs/test-runs/LOCAL_FLOW_ANALYSIS.md --log-dir docs/test-runs/local_log
python tests/gen_thread_report.py --compare docs/test-runs/prod_raw.json \
    docs/test-runs/local_raw.json docs/test-runs/PROD_VS_LOCAL.md
```

### Caveats on the harness

- The PDF attached to turn 10 **during the main runs** carried only 6% of the turn-1
  draft — a pagination bug in the harness's `build_pdf` (`insert_textbox` is
  all-or-nothing; the fitted prefix was never written). Fixed, and F2 was then re-verified
  with a full 4-page PDF in both a fresh and the original thread, so no conclusion rests
  on the truncated file.
- `logs/agent.log` is shared with a pre-existing `--reload` dev server on port 5050, so
  log slices are filtered to this run's request id (`thread_id[:8]`).
- Turn 10's prompt was run three times per environment (in-thread truncated PDF,
  in-thread full PDF, fresh-thread full PDF). The in-thread turns each added another
  `Document` entry to the accumulating state.
