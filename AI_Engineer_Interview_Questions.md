# AI Engineer Interview Questions & Answers

A 20-question interview guide for hiring an AI engineer, with detailed reference answers. Use answers as a rubric — strong candidates will cover the bolded points and add nuance from real experience.

---

## Foundational ML / LLM

### 1. Walk me through the difference between fine-tuning, RAG, and prompt engineering — when would you pick each?

**Prompt engineering** changes only the input string sent to a frozen model. Cheapest, fastest, no infra. Best for: format control, persona, few-shot examples, reasoning scaffolds (CoT, ReAct).

**RAG (Retrieval-Augmented Generation)** keeps the model frozen but injects relevant documents at inference time from a vector store, keyword index, or hybrid. Best for: knowledge that changes (policies, docs, legal updates), grounding/citations, large corpora where fine-tuning would be wasteful. Trade-off: latency + retrieval quality becomes the bottleneck.

**Fine-tuning** updates model weights (full FT, LoRA/QLoRA, or instruction-tuning). Best for: style/format the model can't reliably follow via prompts, domain jargon, structured output where prompting fails >5% of the time, latency reduction (smaller fine-tuned model beats larger general one). Trade-off: training cost, eval infrastructure, weight versioning, can hurt general capability.

**Decision rule:** Start with prompts → add RAG when knowledge is the gap → fine-tune only when prompt+RAG still fails on a measurable benchmark. Most "we need fine-tuning" requests are actually prompt or retrieval problems.

---

### 2. How does a transformer's attention mechanism actually work, and why did it replace RNNs for most NLP tasks?

For each token, attention computes three vectors — **Query (Q)**, **Key (K)**, **Value (V)** — by multiplying the embedding by learned weight matrices. The attention score between two tokens is `softmax(QKᵀ / √dₖ)`, which weights how much one token "looks at" another. The output is a weighted sum of V vectors.

**Multi-head attention** runs this in parallel across multiple subspaces so the model captures different relationship types (syntax, coreference, semantic similarity) in one layer.

**Why it beat RNNs:**
- **Parallelism:** RNNs process tokens sequentially; attention sees the whole sequence at once → trains on GPUs efficiently.
- **Long-range dependencies:** RNN gradients vanish over long sequences. Attention has O(1) path length between any two tokens.
- **Scalability:** Performance keeps improving with more data and parameters, which is exactly what the GPU/cluster era could supply.

The cost is O(n²) memory in sequence length — which is why context-window extensions (FlashAttention, sliding window, sparse attention, state-space models like Mamba) are active research.

---

### 3. Explain embeddings. How would you choose between BGE-large, OpenAI's `text-embedding-3`, and a small MiniLM model?

An embedding is a dense vector (typically 384–3072 dimensions) where semantically similar texts have small cosine distance. Trained via contrastive learning on positive/negative pairs.

**Selection criteria:**

| Model | Dim | Best for | Watch out for |
|---|---|---|---|
| `all-MiniLM-L6-v2` | 384 | Fast, on-device, prototypes, low-stakes search | Lower recall on nuanced queries |
| `BGE-large-en-v1.5` | 1024 | Strong English retrieval, self-hosted, no API dependency | Slower; English-centric |
| OpenAI `text-embedding-3-large` | 3072 (truncatable) | High recall, multilingual, no infra to manage | API cost + vendor lock-in + privacy |
| `multilingual-e5-large` | 1024 | Multilingual (Hindi, Marathi, etc.) | Larger memory footprint |

**Decision factors:**
1. **Language coverage** — English-only? BGE. Multilingual? E5 or OpenAI.
2. **Latency budget** — sub-50ms? MiniLM. 200ms ok? Large models.
3. **Privacy/compliance** — can't ship data to OpenAI? Self-host BGE/E5.
4. **Eval** — always benchmark on YOUR data with NDCG@10 or MRR. Public leaderboards (MTEB) are a starting point, not a verdict.

---

### 4. What's the difference between a context window, a token limit, and prompt caching? How do they affect cost?

- **Token** — the unit a model reads/writes. ~4 chars of English ≈ 1 token. Languages with non-Latin scripts (Hindi, Chinese) tokenize less efficiently → more tokens per character.
- **Context window** — max combined input + output tokens the model can handle in one call (e.g., 200k for Claude Sonnet, 1M for Gemini 2.5, 128k for GPT-4o).
- **Token limit** — sometimes refers to output cap (e.g., `max_tokens=4096`), distinct from context window.

**Cost shape:** You pay per input token AND per output token, usually at different rates. Output tokens are typically 3–5× more expensive than input.

**Prompt caching** lets you mark a stable prefix (system prompt, retrieved docs, examples) so the provider keeps its computed KV cache between requests. Cache hits cost ~10% of the normal input price and reduce TTFT. TTL is usually 5 minutes.

**Engineering implications:**
1. Put stable content (system prompt, docs) FIRST in the message, volatile content LAST → maximizes cache hits.
2. Keep prefixes byte-identical across requests — trailing whitespace breaks the cache.
3. Long context ≠ free. A 500k-token prompt to Gemini still costs real money and adds latency.

---

### 5. How do you evaluate an LLM output when there's no single "correct" answer (e.g., a legal summary)?

Combine three layers:

**1. Programmatic / cheap checks**
- Structural: valid JSON, required sections present, length bounds, language matches expected.
- Citation existence: every quoted case/section can be matched against a source list.
- Forbidden patterns: no placeholders like `[CITE: ...]`, no mojibake, no hallucinated URLs.

**2. LLM-as-judge (with caveats)**
- Use a stronger model (or different family) to score outputs on rubric dimensions: factual grounding, completeness, tone, structure.
- Always pair with: chain-of-thought reasoning, ground-truth examples, pairwise comparison (more reliable than absolute scores), and bias controls (alternate position, blind to model identity).
- Validate the judge against human labels on a sample before trusting it.

**3. Human eval**
- Subject-matter experts rate ~50–200 outputs per release on Likert scales.
- Track inter-rater agreement (Cohen's kappa) — low agreement means your rubric is ambiguous.
- Use it to calibrate the LLM judge, not to score every PR.

**Practical setup:** Pin a regression set of 100–500 representative inputs. Each release runs programmatic + LLM-judge automatically; humans review only the deltas vs the previous release. Track per-category metrics, not just global scores.

---

## Applied LLM Engineering

### 6. Describe a RAG pipeline you've built end-to-end. What broke first when you scaled it, and how did you fix it?

A strong answer walks through:

**Ingestion:** source → cleaning → chunking strategy (fixed-size vs semantic vs document-structure-aware) → embedding → vector store write. Mention chunk size trade-off (small = precise but loses context, large = wastes context window).

**Retrieval:** query → embed → ANN search (HNSW, IVF) → optional keyword/BM25 hybrid → re-ranker (cross-encoder) → top-k to LLM.

**Generation:** prompt template with retrieved chunks → LLM call → post-processing (citation extraction, format validation).

**What breaks first (common):**
- **Recall collapses on rephrased queries** — fix with query expansion, HyDE (hypothetical document embeddings), or multi-query retrieval.
- **Stale chunks** — fix with incremental ingestion + per-doc TTLs.
- **Long-tail queries dominate hallucination** — fix with a confidence/no-answer path: if top-k score < threshold, return "I don't have this" instead of generating.
- **Vector DB latency at scale** — fix with sharding, index tuning (efConstruction/efSearch for HNSW), or moving from in-process Chroma to a server (Qdrant, Weaviate).
- **Token cost explodes with k=10 chunks** — fix with re-ranker + smaller k, or contextual compression (LLM summarizes chunks before passing).

Bonus signals: candidate mentions **eval harness** before scaling, and **observability** (per-query trace of retrieved chunks + final answer).

---

### 7. How would you design a multi-agent system where one orchestrator routes to specialist agents? What goes wrong?

**Design:**
- **Orchestrator** classifies intent (LLM with structured output: `task_type`, `entities`, `language`) and decides: single agent, parallel fan-out, or sequential pipeline.
- **Specialist agents** are stateless tools with narrow scope (e.g., legislation search, judgment search, drafting) — each owns its retrieval + prompt + post-processing.
- **Shared state** (TypedDict in LangGraph) carries query, intermediate results, user context, language.
- **Synthesizer** merges parallel results into one coherent answer.
- **Guardrails** at input (PII, injection, scope) and output (hallucination check, format).

**What goes wrong:**
1. **Orchestrator misclassifies edge cases** — "draft a notice citing Section 138" is both Drafting and Legislation. Fix with multi-label intent or letting agents declare "I'm not the right one" and re-route.
2. **Latency stacks up** — sequential calls add up fast. Parallelize when independent; use streaming for the longest leg.
3. **Cost explosion** — fan-out to all agents "just in case" burns tokens. Gate behind orchestrator confidence.
4. **State pollution** — agents writing to shared keys overwrite each other. Use custom reducers (LangGraph `Annotated[type, reducer_fn]`) so writes merge.
5. **Debugging hell** — without per-agent tracing (LangSmith, Langfuse, OpenTelemetry), failures are opaque. Trace every node in/out.
6. **Cascading failures** — one agent times out → whole graph stalls. Per-agent timeouts + graceful degradation in synthesizer.

---

### 8. What's your strategy for reducing hallucinations in a production LLM app?

Layered defense:

1. **Ground in retrieval.** RAG with high-quality sources is the single biggest lever. Hallucinations correlate with the model "filling gaps" from parametric memory.
2. **Force citations.** Require the model to quote source snippets verbatim and tag them with IDs. Post-validate that every claim has a citation traceable to a real source.
3. **Confidence-aware refusal.** If retrieval scores are low OR the model expresses uncertainty, return "I don't have reliable info on this" rather than guessing. Train the prompt to do this explicitly.
4. **Structured output.** Pydantic / JSON schema with required fields like `source_id`, `quote`, `claim`. Schema violations get rejected and retried.
5. **Self-critique loop.** A second LLM call checks: "does every claim in this answer appear in the provided sources? List violations." Re-generate with violations in the prompt. (See Lawtech-AI's self_refine pattern.)
6. **Temperature 0 for factual tasks.** Reserve higher temps for creative tasks only.
7. **Eval set of known-hard hallucination cases.** Track hallucination rate over time as a release gate.
8. **Model choice matters.** Newer/larger models hallucinate less on the same prompt. Don't over-engineer around a model that's just too small.

---

### 9. How do you handle streaming responses (SSE/WebSockets) while still running post-processing like guardrails on the final output?

This is a real tension: users want first-token latency low (stream), but you can't validate output until it's complete.

**Pattern: stream-then-reconcile.**

1. **Stream the model's tokens directly to the client** as they arrive — feels instant.
2. **Accumulate the full text in a buffer** server-side.
3. **When the model finishes**, run post-processing on the full buffer: guardrails, citation validation, mojibake fixes, format checks.
4. **If post-processing changes the text**, send a `final` event with the corrected version. The client REPLACES the streamed content with the final.
5. **Track this invariant** — streamed tokens must converge to the final answer. If they diverge often, users see jarring rewrites; fix the prompt instead of papering over with post-processing.

**Alternative for stricter guardrails:** Stream to an internal buffer, validate, then stream-replay to the client. Loses the latency win but guarantees safety.

**Watch out for:**
- Multiple parallel agents — only stream from the "solo agent" path; fan-out results buffer until synthesis.
- Mid-stream cancellation — clean up the LLM connection so you don't pay for tokens nobody sees.
- SSE retry semantics — clients reconnecting mid-stream should not double-consume.

---

### 10. When a user query is in Hindi but typed in Latin script, how would you detect intent and route the response language?

This is the classic "Hinglish" / transliteration problem. `langdetect` will call it English.

**Multi-signal detection:**

1. **Explicit directive extraction** — a small LLM call (Gemini Flash Lite, GPT-4o-mini) extracts a typed `UserIntent` with `language` and `language_explicit` fields. Catches "Section 131 in Hindi" even when typed in Latin script.
2. **Script-based fallback** — count Devanagari/Tamil/Bengali Unicode ranges. If > X%, that's the script.
3. **Library detection** — `langdetect` / `fasttext-lid` for ambiguous cases.
4. **User preference** — explicit UI selector (locale, language dropdown) overrides everything. Store in user profile.

**Routing:**
- Override the auto-detected `user_language` when intent extraction returns `language_explicit: true`.
- Propagate language through state to every downstream agent.
- Agents call `localize_prompt(system_prompt, user_language)` so generation happens in the target language end-to-end, not as a post-translate step (which loses citations and legal terminology fidelity).

**Watch out for:**
- Numerals, statute names, and case names should stay in their canonical form (often English/Devanagari mix in Indian legal context). Encode this in the prompt.
- Citations like "Section 138" don't transliterate cleanly — define a glossary.

---

## Systems / Production

### 11. How would you load-test an LLM-backed API targeting 50 concurrent streaming users? What metrics matter?

**Approach:**

1. **Define the workload** — realistic mix of query types weighted by production traffic (e.g., 60% short Q&A, 20% RAG, 15% drafting, 5% PDF chat). Don't load-test on a single canned query.
2. **Tool** — k6, Locust, or Vegeta for HTTP/SSE. Custom client for streaming since most tools don't natively track per-token latency.
3. **Ramp profile** — start at 1 user, ramp to 50 over 5 min, hold 15 min, ramp down. Watch for cliff behavior.
4. **Run from outside the VPC** to capture real network latency.

**Metrics that matter:**

| Metric | Target | Why |
|---|---|---|
| TTFT (Time To First Token) | < 1.5s p95 | Perceived responsiveness |
| Tokens/sec per stream | > 30 | Reading speed feel |
| End-to-end latency | < 30s p95 | Actual completion time |
| Error rate | < 0.5% | Stability |
| Concurrent active streams | 50 sustained | Capacity goal |
| Worker queue depth | < 2 | Backpressure early warning |
| Memory growth | flat over 15 min | Leak detection |
| Upstream LLM 429 rate | 0% | Provider rate-limit headroom |

**Infrastructure dimensions to flex:** gunicorn worker count, in-flight request semaphore size, LLM concurrency budget, vector DB connection pool. Find the knee of the latency-vs-concurrency curve, not just "does it not crash."

---

### 12. Walk me through how you'd debug "the API is slow" when latency spans LLM calls, vector search, and PDF parsing.

**Step 1 — Reproduce and scope.** Get a specific slow request: timestamp, user, query, response time. Don't debug "feels slow" — debug "this trace took 47s."

**Step 2 — Read the trace.** If you have OpenTelemetry / Langfuse / LangSmith, look at the span waterfall. Each agent, tool call, LLM call, DB query should be a span with start/end. The longest bar is the suspect.

**Step 3 — If no tracing, add it.** Wrap each phase with `time.perf_counter()` and log durations. Ship within an hour; this is the highest-leverage instrumentation you can do.

**Step 4 — Common culprits, in order of frequency:**

1. **PDF parsing on the request path** — multi-MB PDFs, OCR, vision LLM fallback. Should be async or pre-processed.
2. **LLM provider tail latency** — p99 is wildly worse than p50. Mitigation: hedged requests, fallback provider, smaller model for first-pass.
3. **Vector DB cold cache** — first query after restart is slow. Warm on boot.
4. **N+1 LLM calls** — e.g., per-section drafting fan-out that's actually sequential. Parallelize.
5. **Synchronous post-processing** — citation validation, translation, formatting all chained after the LLM. Stream + reconcile instead.
6. **Worker timeout chains** — 30s tool → 60s agent → 120s graph → 300s gunicorn → request hangs at the gateway timeout, not the actual bottleneck.
7. **Network egress** — embedding service over WAN, S3 fetches.

**Step 5 — Verify the fix with a load test, not a single curl.** Single-request improvement can mask new concurrency regressions.

---

### 13. How do you version prompts and roll back a bad prompt change in production?

**Treat prompts as code:**
- Live in the repo, code-reviewed, in `config/prompts.py` or similar.
- Tagged with the release SHA. No untracked edits in a notebook → prod.

**Eval gate on PRs:**
- Every prompt change runs the regression eval (LLM judge + programmatic checks) on a frozen test set.
- Fail the PR if any tracked metric regresses beyond tolerance.

**Rollout strategy:**
- **Feature-flag the prompt** — old and new live in code; flag picks which one. Roll out at 5% → 25% → 100% with metrics watched at each step.
- **Or:** deploy behind a `prompt_version` env var, flip on the box, roll back by flipping env and restarting (seconds).

**Rollback mechanics:**
- Git revert → CI deploy is the canonical path. Should be < 10 min from "this is broken" to "rolled back."
- For faster rollback, keep last-known-good prompt in the codebase under a `LEGACY_` constant, switchable by env without a deploy.

**Observability:**
- Log `prompt_version` on every request so you can correlate "quality dropped" with "prompt change at 14:00 UTC."
- Sample real prod outputs into a review queue — humans catch what evals miss.

**What NOT to do:** Edit prompts on the prod box. No record, no review, no rollback.

---

### 14. What's your approach to secrets management for multiple LLM provider keys (OpenAI, Google, Anthropic)?

**Storage:**
- Never in code, never in git (use `.gitignore` + secret scanning hooks like `trufflehog` or GitHub secret scanning).
- Dev: `.env` files (excluded from git), loaded via `python-dotenv`.
- Prod: cloud secret manager (AWS Secrets Manager, GCP Secret Manager, Azure Key Vault, HashiCorp Vault, or Doppler/1Password Connect).
- CI: encrypted GitHub Actions secrets (or equivalent), referenced as `${{ secrets.OPENAI_API_KEY }}`.

**Access:**
- Inject as env vars at runtime via the platform (systemd `EnvironmentFile`, k8s `Secret` → env, ECS Secrets).
- App reads via `os.environ[...]` — code is the same in dev and prod.
- Never log them. Add a regex scrubber in your logger if you log request bodies.

**Rotation:**
- All providers support multiple active keys → rotate by adding new, deploying app with new, revoking old.
- Have a runbook. Rehearse it.

**Scoping:**
- Separate keys per environment (dev/staging/prod) — leaked dev key shouldn't drain prod budget.
- Provider-side restrictions where supported (IP allowlist, usage caps, scoped projects).

**Budget guard:**
- Set spend caps at the provider dashboard. Set alerts at 50/80/100% of monthly budget.
- App-side per-tenant rate limits so one user can't burn the key.

---

### 15. Tell me about a time a model upgrade (e.g., GPT-4 → GPT-4o, Gemini 1.5 → 2.5) broke something subtle.

Looking for war-story signals. Strong candidates will name:

- **Output format drift** — newer model wraps JSON in markdown code fences, or adds preambles ("Here's the JSON: ..."). Tightens or relaxes structured output adherence differently.
- **Refusal behavior change** — new safety tuning refuses queries the old model handled, or vice versa. Breaks domain apps in regulated fields (medical, legal, security).
- **Token count changes** — same prompt tokenizes differently → cost surprise, or context overflows.
- **Tool-calling differences** — argument shape, parallel vs sequential tool calls, names of fields in the tool spec.
- **Streaming chunk boundaries** — chunks split mid-word or include extra metadata events; client parsers break.
- **Temperature semantics** — same temperature feels more/less creative; need to re-tune.
- **Latency profile** — newer model is faster on average but worse p99; SLOs blow up.
- **Eval regression** — average score is up, but a specific category (legal citations, code generation) dropped.

**The right process they should describe:**
1. Pin the model version in code (`gpt-4o-2024-08-06`, not `gpt-4o`).
2. Run the eval set against the new version BEFORE switching.
3. Canary deploy to 5% of traffic with side-by-side metrics.
4. Have rollback ready (env var flip, not redeploy).

---

## Code & Tooling

### 16. Show me code for a function that retries an LLM call with exponential backoff and falls back to a second provider.

A reasonable solution (Python):

```python
import asyncio
import random
from typing import Callable, Awaitable

async def call_with_retry_and_fallback(
    primary: Callable[[], Awaitable[str]],
    fallback: Callable[[], Awaitable[str]],
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> str:
    """Try primary with exp backoff + jitter; on persistent failure, try fallback once."""
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return await primary()
        except RateLimitError as e:
            # Honor Retry-After when provider supplies it
            delay = getattr(e, "retry_after", None) or min(
                max_delay, base_delay * (2 ** attempt)
            )
            delay += random.uniform(0, delay * 0.1)  # jitter
            await asyncio.sleep(delay)
            last_exc = e
        except (TimeoutError, ConnectionError) as e:
            delay = min(max_delay, base_delay * (2 ** attempt))
            delay += random.uniform(0, delay * 0.1)
            await asyncio.sleep(delay)
            last_exc = e
        except InvalidRequestError:
            raise  # don't retry 4xx that won't fix itself

    # Primary exhausted — try fallback once
    try:
        return await fallback()
    except Exception as fb_exc:
        raise RuntimeError(
            f"Both providers failed. Primary: {last_exc}. Fallback: {fb_exc}"
        )
```

**Watch for these details:**
- **Jitter** prevents thundering-herd retries during provider outages.
- **Don't retry 4xx** like invalid API key or bad request — they won't self-heal.
- **Honor `Retry-After`** headers when the provider sends them.
- **Cap total wait** with `max_delay` so a stuck call doesn't blow your request timeout.
- **Distinguish retriable from terminal errors** — network/429/5xx retry; auth/schema errors don't.
- **Log both failures** so you can debug which provider died.
- **Circuit breaker** for production — after N failures in a window, skip the primary entirely for M seconds.

---

### 17. How would you write tests for a non-deterministic LLM agent? What does "passing" mean?

LLM tests are not unit tests with `assertEqual`. Use a layered approach:

**Layer 1 — Deterministic structural tests (`pytest`)**
- Schema validation: response parses as the expected Pydantic model.
- Length bounds, required fields, no forbidden patterns.
- Idempotent with `temperature=0` and a fixed seed where supported.
- These run in CI on every PR; they catch 80% of regressions.

**Layer 2 — Snapshot / golden tests**
- Pin a small set of canonical inputs. Record the output once a human approves it.
- On change, diff new vs old; reviewer accepts or rejects update.
- Good for prompt regressions — you see exactly what changed.

**Layer 3 — Behavioral / property tests**
- Property: "if the query is in Hindi, the response is in Hindi" — run 50 Hindi queries, assert script detection.
- Property: "if the query is non-legal, the agent refuses" — sample of off-topic queries.
- Pass = ≥95% conformance, not 100%.

**Layer 4 — LLM-as-judge eval**
- Regression set of 100–500 examples graded by a stronger model on rubric dimensions.
- "Passing" = aggregate score doesn't regress from baseline beyond tolerance (e.g., -2% triggers review).

**Layer 5 — Human eval (release gate)**
- SMEs review 50 outputs per release on the hardest categories.

**What "passing" means:**
- L1/L2: hard pass/fail, blocks merge.
- L3/L4: soft thresholds, regression triggers human review.
- L5: gates production rollout, not every commit.

**Anti-pattern:** Asserting exact string match on LLM output. It'll flake on every model update and you'll start adding `# noqa: flaky` everywhere.

---

### 18. Compare LangChain, LangGraph, LlamaIndex, and raw SDK calls — when is each the right choice?

| Tool | Strengths | Weaknesses | Pick when |
|---|---|---|---|
| **Raw SDK** (anthropic, openai, google-genai) | Zero abstraction tax, full control, easy to debug | You build retries, parsing, tool routing yourself | Single LLM call, scripts, tight perf budget, or when you've outgrown frameworks |
| **LangChain** | Huge integration surface (chat models, vector stores, retrievers), unified interface | API churn, abstraction layers obscure what's happening, can over-couple your code | Multi-provider, lots of integrations, want common interfaces for chains |
| **LangGraph** | Explicit stateful graph, parallel agent fan-out, human-in-the-loop, checkpointing | Steeper learning curve, debugging stateful graphs is harder | Multi-agent systems, branching workflows, need durable state and replay |
| **LlamaIndex** | Best-in-class indexing/retrieval primitives (router, multi-doc, structured), strong RAG abstractions | More opinionated, smaller community than LangChain | RAG-heavy app where retrieval is the hard part |

**Practical advice:**
- Start with **raw SDK** for the first POC — you learn what you actually need.
- Move to **LangGraph** when you have ≥3 agents or need parallel fan-out + shared state.
- Use **LangChain** as a utility library (model wrappers, prompts, retrievers) within a LangGraph app.
- Use **LlamaIndex** for the retrieval layer if you're going beyond naive top-k.

**Anti-pattern:** Picking the framework first, then forcing the problem to fit. The frameworks all evolve fast — coupling tightly costs you on every upgrade.

---

## Judgment & Collaboration

### 19. A PM asks for a feature that requires fine-tuning a model. You think RAG would solve 90% of it for 5% of the cost. How do you handle that conversation?

This question tests judgment, communication, and ability to push back constructively.

**Strong-answer shape:**

1. **Don't say no first. Ask what success looks like.** "What outcome are we trying to drive? Specific user pain? Quality bar? Latency target?" Often the PM has heard "fine-tuning" from a stakeholder and is repeating it without owning the technical choice.

2. **Quantify both options briefly.**
   - RAG path: 1 week, $X/month, hits Y% of quality bar (cite eval if you have it).
   - Fine-tune path: 4–8 weeks, $20X upfront + ongoing eval infra, hits Y+5% of quality bar.

3. **Propose a stepped plan.** "Let's ship RAG in 2 weeks, instrument the failure cases, and revisit fine-tuning if the residual is meaningful enough to justify the cost." This respects their goal while reducing risk.

4. **Be honest about the 10% gap.** "Here's what RAG won't solve well: [domain style, structured output reliability, etc.]. If those matter more than I think, we should reconsider."

5. **Decide WITH them, not FOR them.** Final call belongs to the PM, but they should have the trade-off in front of them.

**Anti-patterns to avoid:**
- Engineer condescension ("you don't understand the tech").
- Building what they asked for without flagging the cheaper path.
- Silently doing RAG and surprising them later — they may have committed externally to "we're fine-tuning."

---

### 20. What's the most recent AI paper, model release, or tool that changed how you work — and why?

There's no single right answer — this is a signal question for genuine engagement with the field. Strong candidates should be able to:

- **Name something specific from the last 3–6 months.** Vague "I follow the field" doesn't count.
- **Explain mechanism, not hype.** What does it actually do differently?
- **Connect to their work.** "I tried it on X, here's what changed."
- **Acknowledge trade-offs.** No tool is pure upside.

**Good example areas to listen for (mid-2026 context):**
- New model releases (Claude Opus 4.7 1M context, Gemini 2.5 Pro, GPT-5) and how the context window or reasoning changed their architecture.
- Inference-time compute (chain-of-thought scaling, search, verifier models) reshaping how they prompt for hard problems.
- Open weights closing the gap with closed (Llama 4, DeepSeek, Qwen) and what that unlocks for self-hosting.
- New eval frameworks (BIG-Bench Hard, AgentBench, custom domain evals) and what they revealed.
- Tooling shifts — observability (Langfuse, Helicone, Arize Phoenix), evals (Braintrust, Patronus, Weave), orchestration (DSPy, Inspect AI).
- Multimodal (vision, audio) opening new use cases.

**Red flags:**
- Can't name anything specific.
- Only names what a tech influencer tweeted last week, no critical view.
- Talks about a paper but can't explain what's actually novel about it.
- Last thing they engaged with was 2 years old.

---

## Interview Tips

**For the interviewer:**
- Don't grade on whether they "got the answer" — grade on the reasoning, trade-off awareness, and depth of experience.
- Follow up with "what would break first at 10x scale?" or "what did you actually try before that worked?"
- For coding questions, screen-share matters more than the final code — watch their debugging process.
- Mix difficulty: 5 should be easy warm-ups, 10 mid, 5 hard. If they breeze through, go deeper on follow-ups.
- Leave 15 minutes at the end for THEIR questions about your team — quality of their questions is itself a strong signal.

**Red flags across all answers:**
- "It just works" with no detail.
- Defensive when challenged on trade-offs.
- Can't name anything they've struggled with or gotten wrong.
- Treats prompts/LLMs as magic rather than systems to instrument and debug.

**Green flags:**
- Reaches for evals before reaching for fancier architecture.
- Mentions cost and latency unprompted.
- Distinguishes "I read about this" from "I shipped this."
- Asks clarifying questions before answering open-ended ones.
