# Token Usage Reporting

Every API response that runs the agent pipeline includes a `token_usage`
object describing every LLM call that served the request — input/output
tokens, cache hits, reasoning tokens, per-agent and per-model rollups,
and an estimated USD cost.

This is the single source of truth for billing, quota tracking, and
cost dashboards. The legacy flat fields (`total_tokens`,
`total_tokens_consumed`) were removed; consume `token_usage.total_tokens`
instead.

---

## Where it appears

| Endpoint | Carrier | Field path |
|---|---|---|
| `POST /pyapi/search` | JSON response | `data.token_usage` |
| `POST /pyapi/chat` | SSE `done` event | `event.token_usage` |
| `POST /pyapi/continue_draft` | SSE `done` event | `event.token_usage` |

Public-facing endpoints proxy these via `nginx /pyapiv2/* → /pyapi/*`,
so the field paths are identical.

---

## Schema

```jsonc
{
  // Aggregate totals across every LLM call in the request
  "input_tokens":          int,    // sum of prompt tokens
  "output_tokens":         int,    // sum of completion tokens (excluding reasoning)
  "total_tokens":          int,    // canonical "tokens used" — display this in your UI
  "cache_read_tokens":     int,    // Gemini context-cache hit (75% cheaper than fresh prompt)
  "cache_creation_tokens": int,    // Tokens written to context cache (one-time premium)
  "reasoning_tokens":      int,    // Thinking-model output (priced as output)
  "cost_usd":              float,  // estimated USD via per-model rate table

  // Per-agent rollup
  "by_agent": {
    "<AgentName>": {
      "input":      int,
      "output":     int,
      "total":      int,
      "cache_read": int,
      "calls":      int,    // how many LLM calls this agent made
      "cost_usd":   float
    }
    // … one entry per agent that fired
  },

  // Per-model rollup (useful for cost attribution to specific models)
  "by_model": {
    "<model_id>": {
      "input":    int,
      "output":   int,
      "total":    int,
      "calls":    int,
      "cost_usd": float
    }
    // … one entry per distinct model used
  },

  // Per-LLM-call list, in roughly the order they completed
  "calls": [
    {
      "agent":          "Orchestrator" | "Drafting" | "Document" | "Memory" | "Guardrail" | "Judgment" | "Legislation" | "Newacts" | "SCI_Judgment" | "Constitution_Maxim" | "Non_legal",
      "step":           "classify_and_plan" | "section_3_Verification" | "qa_gemini_files" | …,  // see step-label table below
      "model":          "gemini-2.5-flash-lite" | "gemini-2.5-pro" | "gpt-4o-mini" | …,
      "input":          int,
      "output":         int,
      "total":          int,
      "cache_read":     int,
      "cache_creation": int,
      "reasoning":      int,
      "cost_usd":       float
    }
    // … one entry per LLM call
  ]
}
```

---

## Real example — `/pyapi/chat` with file attached

**Request:**
```bash
curl -N -X POST https://tool.lawttorney.com/pyapiv2/chat \
  -H "X-API-Key: $LAWTECH_API_KEY" \
  -F 'query=Draft a written statement on behalf of the defendant Kunal Rajendra Patil denying the friendly loan alleged in the plaint.' \
  -F 'files=@plaint.pdf'
```

**`done` SSE event** (other events trimmed):
```jsonc
{
  "type": "done",
  "agents_used": ["Orchestrator", "Document", "Drafting", "Judgment"],
  "token_usage": {
    "input_tokens":          34389,
    "output_tokens":         11182,
    "total_tokens":          45571,
    "cache_read_tokens":     20400,
    "cache_creation_tokens": 0,
    "reasoning_tokens":      7358,
    "cost_usd":              0.0921,

    "by_agent": {
      "Orchestrator": { "input": 1288,  "output": 218,  "total": 1506,  "cache_read": 0,     "calls": 2, "cost_usd": 0.0002 },
      "Document":     { "input": 1697,  "output": 2969, "total": 4666,  "cache_read": 0,     "calls": 1, "cost_usd": 0.0493 },
      "Drafting":     { "input": 31108, "output": 7823, "total": 38931, "cache_read": 20400, "calls": 9, "cost_usd": 0.0425 },
      "Judgment":     { "input": 296,   "output": 172,  "total": 468,   "cache_read": 0,     "calls": 2, "cost_usd": 0.0001 }
    },

    "by_model": {
      "gemini-2.5-flash-lite": { "input": 1939,  "output": 522,  "total": 2461,  "calls": 5, "cost_usd": 0.0003 },
      "gemini-2.5-pro":        { "input": 1697,  "output": 2969, "total": 4666,  "calls": 1, "cost_usd": 0.0493 },
      "gemini-2.5-flash":      { "input": 30753, "output": 7691, "total": 38444, "calls": 7, "cost_usd": 0.0425 }
    },

    "calls": [
      { "agent": "Orchestrator", "step": "classify_and_plan",         "model": "gemini-2.5-flash-lite", "input": 683,  "output": 88,   "total": 771,  "cost_usd": 0.0001, "cache_read": 0, "cache_creation": 0, "reasoning": 0 },
      { "agent": "Orchestrator", "step": "rewrite_per_agent_queries", "model": "gemini-2.5-flash-lite", "input": 605,  "output": 130,  "total": 735,  "cost_usd": 0.0001, "cache_read": 0, "cache_creation": 0, "reasoning": 0 },
      { "agent": "Document",     "step": "qa_gemini_files",           "model": "gemini-2.5-pro",        "input": 1697, "output": 2969, "total": 4666, "cost_usd": 0.0493, "cache_read": 0, "cache_creation": 0, "reasoning": 612 },
      { "agent": "Drafting",     "step": "select_template",           "model": "gemini-2.5-flash-lite", "input": 1839, "output": 38,   "total": 1877, "cost_usd": 0.0002, "cache_read": 0, "cache_creation": 0, "reasoning": 0 },
      { "agent": "Judgment",     "step": "extract_metadata",          "model": "gemini-2.5-flash-lite", "input": 296,  "output": 148,  "total": 444,  "cost_usd": 0.0001, "cache_read": 0, "cache_creation": 0, "reasoning": 0 },
      { "agent": "Drafting",     "step": "outline",                   "model": "gemini-2.5-flash",      "input": 3496, "output": 2977, "total": 6473, "cost_usd": 0.0138, "cache_read": 0, "cache_creation": 0, "reasoning": 1842 },
      { "agent": "Drafting",     "step": "section_1_Preliminary Submissions",      "model": "gemini-2.5-flash", "input": 4273, "output": 529,  "total": 4802, "cost_usd": 0.0036, "cache_read": 3400, "cache_creation": 0, "reasoning": 320 },
      { "agent": "Drafting",     "step": "section_3_Defendant's Version of Facts", "model": "gemini-2.5-flash", "input": 4213, "output": 1552, "total": 5765, "cost_usd": 0.0080, "cache_read": 3400, "cache_creation": 0, "reasoning": 950 },
      { "agent": "Drafting",     "step": "section_4_Legal Grounds and Defences",   "model": "gemini-2.5-flash", "input": 4202, "output": 1144, "total": 5346, "cost_usd": 0.0063, "cache_read": 3400, "cache_creation": 0, "reasoning": 720 },
      { "agent": "Judgment",     "step": "generate",                               "model": "",                 "input": 0,    "output": 24,   "total": 24,   "cost_usd": 0.0000, "cache_read": 0,    "cache_creation": 0, "reasoning": 0 },
      { "agent": "Drafting",     "step": "section_6_Verification",                 "model": "gemini-2.5-flash", "input": 4143, "output": 664,  "total": 4807, "cost_usd": 0.0043, "cache_read": 3400, "cache_creation": 0, "reasoning": 410 },
      { "agent": "Drafting",     "step": "section_5_Prayer",                       "model": "gemini-2.5-flash", "input": 4172, "output": 1257, "total": 5429, "cost_usd": 0.0073, "cache_read": 3400, "cache_creation": 0, "reasoning": 802 },
      { "agent": "Drafting",     "step": "section_2_Specific Denials",             "model": "gemini-2.5-flash", "input": 4250, "output": 2829, "total": 7079, "cost_usd": 0.0141, "cache_read": 3400, "cache_creation": 0, "reasoning": 1810 }
    ]
  },
  "thread_id": "fac1f6fd-705e-4370-9b9e-d848f5508ba9",
  "conversation_turn": 1,
  "query_rewritten": false,
  "effective_query": null,
  "has_draft_continuation": false
}
```

What this tells you about the request:
- **45,571 tokens** consumed end-to-end, costing **$0.092**
- **Gemini Flash** did the heavy lifting (drafting sections), **Gemini Pro** read the PDF (Document agent QA), **Gemini Flash Lite** handled the cheap classification + planning + metadata extraction
- **20,400 cache-read tokens** in Drafting → context-cache hits across the parallel section generations saved ~75% on those input tokens
- **7,358 reasoning tokens** were spent (Gemini's internal "thinking" — billed as output)
- **13 LLM calls** total, all visible

---

## Step-label reference

The `step` field in each call lets you attribute cost to specific pipeline stages.

| Agent | Possible `step` values |
|---|---|
| `Memory` | `rewrite_query` |
| `Guardrail` | `injection_check` |
| `Orchestrator` | `classify_task`, `classify_and_plan`, `plan_agents`, `normalize_query`, `extract_long_query`, `rewrite_per_agent_queries`, `synthesize`, `inject_citations`, `auto_cite` |
| `Document` | `qa_gemini_files`, `qa_inline_text`, `qa_chromadb` |
| `Drafting` | `extract_case_facts`, `select_template`, `outline`, `section_<N>_<title>` |
| `Judgment` | `extract_metadata`, `generate` |
| `Legislation` | `extract_match_phrase`, `generate` |
| `Newacts` | `extract_metadata`, `generate` |
| `Constitution_Maxim` | `generate` |
| `SCI_Judgment` | `react_msg_<N>` (one per ReAct trajectory message) |
| `Non_legal` | `respond` |

---

## Per-model price table

Costs are computed via the table in [`core/token_tracker.py`](../core/token_tracker.py).
Update the table when providers revise rates.

| Model | Input ($/1M) | Output ($/1M) |
|---|---:|---:|
| `gpt-4o` | $2.50 | $10.00 |
| `gpt-4o-mini` | $0.15 | $0.60 |
| `gemini-2.5-flash` | $0.30 | $2.50 |
| `gemini-2.5-flash-lite` | $0.10 | $0.40 |
| `gemini-2.5-pro` | $1.25 | $10.00 |

If a model is not in the table (e.g. a new release or a `model: ""` for
some structured-output paths), `cost_usd` for that call is `0.0` — the
token counts are still accurate.

`reasoning_tokens` are added to `output_tokens` for cost calculation
(matching Google's pricing rules).

`cache_read_tokens` are NOT discounted in the cost estimate yet; the
estimate is an **upper bound**. If you need exact billing, either apply
a 0.25× factor to `cache_read_tokens` × input rate yourself, or update
`_estimate_cost_usd` in `core/token_tracker.py`.

---

## Consuming from JavaScript

```js
// /pyapi/search (JSON response)
const r = await fetch(`${BASE}/pyapi/search`, {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
  body: JSON.stringify({ Promptquery: "What is Section 302 of IPC?" }),
});
const data = await r.json();

console.log("tokens:", data.token_usage.total_tokens);
console.log("cost:  $", data.token_usage.cost_usd.toFixed(4));
console.log("by agent:", data.token_usage.by_agent);
```

```js
// /pyapi/chat (SSE)
const resp = await fetch(`${BASE}/pyapi/chat`, {
  method: "POST",
  headers: { "X-API-Key": API_KEY },
  body: formData,
});
const reader = resp.body.getReader();
const decoder = new TextDecoder();
let buf = "";
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  buf += decoder.decode(value, { stream: true });
  let i;
  while ((i = buf.indexOf("\n\n")) !== -1) {
    const chunk = buf.slice(0, i);
    buf = buf.slice(i + 2);
    if (!chunk.startsWith("data: ")) continue;
    const evt = JSON.parse(chunk.slice(6));
    if (evt.type === "done") {
      console.log("tokens:", evt.token_usage.total_tokens);
      console.log("cost:  $", evt.token_usage.cost_usd.toFixed(4));
      // Per-agent table for a usage dashboard
      for (const [agent, stats] of Object.entries(evt.token_usage.by_agent)) {
        console.log(`  ${agent}: ${stats.calls} calls, ${stats.total} tokens, $${stats.cost_usd.toFixed(4)}`);
      }
    }
  }
}
```

---

## Consuming from Python

```python
import requests

r = requests.post(
    "https://tool.lawttorney.com/pyapiv2/search",
    headers={"X-API-Key": API_KEY},
    json={"Promptquery": "What is Section 302 of IPC?"},
    timeout=120,
)
data = r.json()
tu = data["token_usage"]

print(f"total: {tu['total_tokens']:,} tokens, ${tu['cost_usd']:.4f}")
for agent, stats in tu["by_agent"].items():
    print(f"  {agent}: {stats['calls']} calls, {stats['total']:,} tokens, ${stats['cost_usd']:.4f}")
```

For the streaming `/pyapi/chat` endpoint, parse SSE lines and look at the
`done` event the same way.

---

## Cache-hit responses

When a `/search` or `/chat` request hits the response cache (identical
first-turn query repeated within the cache TTL), the response replays
the *originally captured* `token_usage`. The `calls` list and per-agent
rollups still describe the work the cache populator did — they do **not**
indicate that a fresh request consumed those tokens.

For older cache entries written before the per-call breakdown was
captured, `token_usage.calls` and `by_agent` / `by_model` may be empty
dicts; `total_tokens` is still set from the legacy aggregate.

You can detect cache hits via the `cached: true` field on the SSE
`done` event. Until billing logic accounts for cached replays, treat
`token_usage.total_tokens` from a cached response as informational, not
billable.

---

## Implementation

* [`core/token_tracker.py`](../core/token_tracker.py) — `TokenUsage`,
  `TokenCall`, `record(agent, step, response, model="")` helper, and
  `start_request()` / `get_tracker()` ContextVar plumbing.
* [`core/chat_runner.py`](../core/chat_runner.py) — initializes the
  tracker before the agent graph runs and emits it in the `done` event.
* [`core/gateway.py`](../core/gateway.py) — same for the JSON `/search`
  and the streaming `/continue_draft`.
* `core/response_cache.py:CacheEntry.token_usage` — cache field that
  preserves the breakdown so cache hits don't lose detail.
* All agents call `record(...)` after each LLM invocation. For
  structured-output calls the chain is built with
  `with_structured_output(Model, include_raw=True)` so the underlying
  AIMessage's `usage_metadata` is reachable.
