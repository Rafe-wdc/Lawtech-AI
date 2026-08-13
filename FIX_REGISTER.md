# Lawttorney — Fix Register

**The single place to see what is broken, what we have fixed, what we are doing next,
and why each thing matters.**

Verified against branch `fix/audit-coverage`, commit `3a0d77b`, on 9 August 2026.

---

## How to use this document

This register exists because the information was previously scattered across three
places: a code-level analysis (`IMPROVEMENT_GAPS.md`), a leadership summary
(`PROPOSAL_improvements.md`), and a working roadmap that lived outside the repository
entirely. That made it impossible to answer a simple question — *"what is actually
being fixed right now, and why?"*

Everything is now here. The other two documents still exist and are still useful, but
they are **analysis**, not tracking. This is the tracker.

### Status markers

| Marker | What it means |
|---|---|
| ✅ **DONE** | Written and tested. Not yet committed to git unless stated otherwise. |
| 🔴 **URGENT** | Security. Should not wait in a queue behind feature work. |
| 🔵 **NEXT** | Ready to start today. Nothing is blocking it. |
| ⬜ **BACKLOG** | A real problem, but either blocked by something else or lower priority. |
| ⛔ **REJECTED** | We looked into it properly and decided **not** to do it. The reason is recorded so nobody has to rediscover it. |

### Item IDs

Every item has a permanent ID so you can refer to it in conversation without ambiguity
("what's the status of Q-6?"). The letter indicates the category:

- **S** — Security
- **Q** — Quality and retrieval (does the system give good legal answers?)
- **C** — Cost (are we paying more than we need to?)
- **R** — Reliability (does it stay up and behave predictably?)
- **P** — Process (engineering hygiene, docs, CI)

IDs are never reused or renumbered, even if an item is rejected or completed.

### A note on jargon

This system involves a lot of search and AI terminology. Rather than assume it, there
is a **glossary at the bottom** of this document explaining BM25, embeddings,
cross-encoders, reducers, and the other terms that appear below. If a sentence doesn't
land, the term is probably defined there.

---

## At a glance

| Category | Done | Urgent | Next | Backlog | Rejected |
|---|---|---|---|---|---|
| Security | — | 2 | — | — | — |
| Quality / retrieval | 4 | — | 1 | 13 | 3 |
| Cost | — | — | — | 6 | — |
| Reliability | 3 | — | — | 3 | — |
| Process | 2 | — | — | 4 | 1 |
| **Total** | **9** | **2** | **1** | **26** | **4** |

> **Handing this to another session?** Start with **`HANDOFF.md`** — working-tree state,
> what is verified vs merely reasoned, environment setup, and the next task with its
> analysis already done.

**Changes on 11 Aug 2026:**
- **R-4 done** — and it was three singletons, not one. Two of them load models.
- **C-1 corrected, not implemented.** Its premise was wrong: those four calls are
  Google-Search-grounded generation, not cheap extraction. See its entry.
- **Four items added that had been discussed but never recorded** — the register was
  losing findings, which is the one job it has:
  - **Q-15** wrong court format, **Q-16** Marathi translation *(from a lawyer's review)*
  - **Q-17** parent-child retrieval *(proposed by the user, analysed, never written down)*
  - **P-4** model settings nothing reads, **P-5** dual search-backend hedge

**Current state of the working tree:** 8 files changed, 5 new test files,
**44 automated tests all passing**. Nothing has been committed to git yet.

---

# Part 1 — Completed work

Seven items are done. All are written, tested, and sitting uncommitted in the working
tree so you can review them before anything ships.

**Read this first — an honest assessment of what these seven actually buy you.** Every
claim below is a mechanism argument from reading code. **None of it is measured.**

| Item | Real defect? | Improves answers today? |
|---|---|---|
| Q-1 | Yes | Probably — but it also raises cost and carries a rewrite risk (see its entry) |
| Q-2 | Yes | Only on queries where the corpus returns **zero** results |
| Q-3 | Yes | **Effectively no** — infrastructure for Q-5 (see its entry) |
| R-1 | Yes | No — throughput under load, not answer quality |
| R-4 | Yes | No — prevents duplicate model loads under concurrency |
| P-1 | n/a | No — prevents future regressions |
| P-3a | n/a | No — documentation only |

That table is the reason Q-9 (the evaluation set) is a hard gate on everything
downstream. Until it exists, "did this help?" cannot be answered.

---

## Q-1 — The drafting quality check was never verifying case citations ✅

### What was happening

The system has an automatic quality-review step called `self_refine`. After a legal
draft is generated, this step re-reads the draft and looks for problems — one of which
is a category called `unretrieved_citation`, meaning *"this draft cites a court case
that we never actually found in our database."* In other words, an invented citation.

For that check to work, the reviewer needs a list of what was genuinely retrieved. That
list is passed in as an argument called `source_registry`.

**The drafting agent was never passing it.**

When that argument is missing, the reviewing code does not fail or warn. It substitutes
a placeholder string that reads, literally:

> `(none — the caller passed no source registry; skip the unretrieved_citation category for this call)`

So the reviewer was politely instructed to skip citation checking entirely, on every
single draft, silently.

### Why this mattered

Of all the outputs this system produces, a **legal draft** is the one where a fabricated
case citation does the most damage. A wrong answer in a chat window is an inconvenience.
A fabricated precedent inside a document that goes to a court is a professional problem
for the person who filed it.

There is a second, more frustrating aspect. The information needed for the check was
**already sitting in memory**. A function called `_gather_relevant_context` runs a few
seconds earlier and retrieves the real statutes and judgments from Elasticsearch. It
formatted them into text for the prompt, and then threw the structured data away. The
check was failing for want of data the system had already paid to fetch.

### What we changed

`_gather_relevant_context` now returns two things instead of one: the formatted text
blocks it always returned, plus a structured registry of everything it retrieved. That
registry covers all four retrieval sources — new criminal codes (BNS/BNSS/BSA), general
legislation, High Court judgments, and Supreme Court judgments — and is handed to
`self_refine`.

**Files:** `agents/drafting.py` · **Tests:** 13

### A problem we caught while implementing this

The plan for this fix said *"reuse the existing adapter functions — no new adapters
needed."* That sounded efficient and was wrong.

The adapter functions read their input using `getattr(...)`, meaning they expect
**objects with attributes**. The Elasticsearch tools return plain **dictionaries**.
In Python, `getattr` on a dictionary does not find dictionary keys — it returns the
default, which here is `None`.

So every adapter would have returned `None`, the registry would have been **empty**, and
`self_refine` would have seen an empty registry and behaved exactly as before. The fix
would have appeared to land, tests would have been green, and citation checking would
still have been switched off.

This was resolved by constructing a `SourceMetadata` object first — which is the type
the adapters were designed for — so their formatting logic is genuinely reused rather
than bypassed. See *Part 8* for why this pattern is worth watching for.

### ⚠️ Correction — this fix does more than described above, and it has a cost

The original write-up said the critic was *"running with citation checking disabled."*
That understated it. Look at the guard at the top of `self_refine`:

```python
has_registry = bool(source_registry) and len(source_registry) > 0
if not (has_directives or has_lang_mismatch or has_registry):
    return response, []
```

Before this fix, on a drafting request where the user gave **no explicit directives** and
the language matched, `self_refine` **returned immediately and did nothing at all.** Not
"ran without one check" — it never ran.

So Q-1 does not switch on a single check. It activates the **entire critic** on drafts
that previously bypassed it. That is the documented design intent — the comment above
that guard says exactly this — but it has two consequences worth tracking:

**Cost.** The refine step uses the **premium** model. This increases Gemini 2.5 Pro usage
on drafting requests that previously skipped it. It therefore pulls in the opposite
direction to **C-2**, and the two should be priced together rather than separately.

**Risk of making some drafts worse.** `CLAUDE.md` records that in review-and-redraft mode
the critic *"reliably fires 3+ violations against the correct section-writer output, and
the refiner then wholesale rewrites the corrected draft into a generic template."* That
is why that mode short-circuits the critic — a short-circuit this fix does not touch. But
for ordinary drafting, the critic now runs where it did not before.

**How to watch it:** `grep -c "Self-refine altered draft" logs/agent.log` shows how often
the refiner is now rewriting. If that number is high, compare a few before/after drafts
before shipping.

---

## Q-2 — Constitution and Maxim queries gave up too early ✅

### What was happening

Most of the search agents in this system follow a three-step ladder when they cannot
find something:

1. Search our own Elasticsearch corpus.
2. If that returns nothing, ask a cheap AI model to **rewrite the query** in better
   legal terminology, then search our corpus again.
3. If that still returns nothing, fall back to a Google web search.

Step 2 matters. A user asking about *"the right to life"* may find nothing, whereas
*"Article 21 right to life and personal liberty"* finds the correct provision
immediately. It is a cheap call, and it keeps the answer grounded in our own corpus
rather than the open web.

The Constitution and Maxim agents **skipped step 2 entirely** and went straight from
"nothing found" to Google.

### The frustrating part

The rewrite prompts for these two agents **already existed**. Somebody wrote them,
tailored to the domain — the Constitution one maps everyday phrases to Article numbers,
the Maxim one maps English descriptions to Latin legal terms. They were sitting in a
dictionary called `_REWRITE_PROMPTS`, fully written, and **no code ever called them**.
Dead configuration.

Meanwhile `CLAUDE.md` confidently stated *"All domain agents have a 3-tier fallback,"*
which was not true and is presumably why nobody noticed.

### What we changed

Added the rewrite-and-retry step to both agents, mirroring the pattern already used by
the Legislation agent.

One design decision worth recording: the rewritten query is used **only to search
again**. The relevance check that runs afterwards still judges results against the
user's **original** question. Otherwise the system would be checking whether the results
match its own paraphrase rather than what the person actually asked.

**Files:** `agents/constitution_maxim.py:158` · **Tests:** 10

### An important scope correction

The original roadmap estimated this as *"tier-2 retry for the other 10 agents, roughly
half a day."*

The real number is **2 agents and about 12 lines**, because of this:

```python
system_prompt = _REWRITE_PROMPTS.get(agent_name)
if not system_prompt:
    return query          # ← silently returns the query unchanged
```

If you call this helper with an agent name that has no prompt entry, it hands back your
original query with **no error and no log line**. So adding call sites for the other
eight agents would have produced eight silent no-ops — code that looks like a working
retry and does nothing at all.

Only five agent names have prompts: Newacts, Legislation, Judgment, Constitution, Maxim.
The first three already had call sites; the last two were the actual gap.

---

## Q-3 — A whole data channel was declared, typed, and never used ✅

### What was happening

The system's shared state object (`LegalAgentState`) declares a field like this:

```python
source_registry: Annotated[SourceRegistry, merge_source_registries]
```

In LangGraph, that second part is a **reducer** — a function that merges values when
several agents write to the same field at the same time. It exists precisely because
agents run in parallel and would otherwise overwrite each other.

This field was documented with a thoughtful comment. It was typed. It had a reducer.
And **no production code ever wrote to it or read from it.** The reducer had zero
callers. It was infrastructure for a feature nobody had connected.

### Why this was not just tidiness

Here is the concrete consequence.

When the drafting agent finishes, it reports its sources in a field called
`AgentResult.sources`. If you look at what it puts there, it is **only the reference
template** — the example document it used for structure. That is all.

The BNS sections, the legislation, the High Court and Supreme Court judgments it
retrieved through `_gather_relevant_context`? Not in there. Invisible to everything
downstream.

So on a multi-agent request, the orchestrator assembles its list of "sources we
retrieved" and sees only a template — with no record of the BNS sections or judgments
the draft actually cites.

### ⚠️ Correction — the harm described above does not currently occur

An earlier version of this entry claimed that multi-agent synthesis *"could flag a
genuinely retrieved citation as fabricated, and the refiner might strip it."*

**I traced it, and that does not happen.** When Drafting is part of a multi-agent plan,
the orchestrator takes the **draft-aware synthesis** branch: it removes Drafting from the
result set, appends the other agents' findings as a References section, and returns.
**That branch never calls the critic at all.**

So the registry is not consulted on the one path where drafting's sources would matter:

| Request shape | What happens to the registry |
|---|---|
| Drafting alone | Pass-through; drafting's own critic already ran internally |
| Drafting + other agents | Draft-aware synthesis — **no critic**, registry unused |
| No drafting involved | Only drafting writes the channel, so state is empty; the merge is a no-op |

**Honest verdict: Q-3's effect on answers today is approximately zero.**

It remains worth having, for two concrete reasons:

1. It is the **prerequisite for Q-5**. The channel had never been exercised end-to-end;
   now it has a working writer, a working merge, and tests. Q-5 replicates a proven
   pattern instead of debugging a dead one.
2. It stopped the single-agent path from **building the registry and discarding it** —
   paying the cost for nothing. That is required for Q-6.

The gap it exposed is real and is now tracked: draft-aware synthesis is one of the
critic-free exits listed under **Q-6**.

### What we changed

Three things:

1. **The drafting agent now writes to the state channel.** This is the first production
   writer that channel has ever had.
2. **The orchestrator merges two sources** — what agents wrote directly, and what it
   derives from `AgentResult.sources`. The merge is additive, so agents that write
   nothing lose nothing and no existing behaviour changes.
3. **Both single-agent exit paths now keep the registry** instead of discarding it. The
   orchestrator was building the registry and then returning without it on that path —
   paying the cost and throwing the result away. This is also a prerequisite for Q-6.

**Files:** `agents/drafting.py`, `agents/orchestrator.py` · **Tests:** 12

---

## P-3a — Documentation that described code that does not exist ✅

### What was happening

Two documented claims were false:

**"Query rewriting uses GPT-4o-mini."** It uses Gemini Flash Lite. The docstring said
one thing, line 83 of the same file called something else. This appeared in
`core/agent_fallback.py` twice and in `CLAUDE.md` once.

**"All domain agents have a 3-tier fallback."** They do not, as Q-2 above demonstrates.
This claim is very likely why the gap in Constitution and Maxim went unnoticed — the
document said it was handled.

### What we changed

Corrected the model name in all three places. Replaced the blanket 3-tier claim with a
per-agent table showing what each agent actually has. Added an explicit warning about
the silent-no-op behaviour so the next person to extend this does not fall into the same
trap.

**Files:** `CLAUDE.md`, `core/agent_fallback.py`

---

## R-1 — Two synchronous calls were freezing the whole server ✅

### What was happening

This system runs on a single asyncio event loop. Both the Elasticsearch client
(`opensearch-py`) and the ChromaDB reader are **synchronous** libraries. Calling either
one directly from an `async def` does not just make that request slow — it **freezes the
entire loop** until the call returns. Every other request in flight stops dead.

The established pattern here is `await asyncio.to_thread(...)`, which hands the blocking
work to a background thread. Almost every call site already did this. Two did not.

**`agents/newacts.py` — the retry search.** When the first search returned nothing, the
retry called `es.search(...)` directly. It survived unnoticed because it needs a
retrieval *miss* **and** concurrent load at the same time — quiet traffic never reveals
it.

**`agents/drafting.py` — reading uploaded documents.** This one is worse and was **not**
in the original register; it was found while auditing for the first. It called
`get_full_attachment.invoke(...)` directly, inside a loop over every uploaded collection.
That function reads the *entire* document out of ChromaDB — up to roughly 3.5 million
characters for a 500-page PDF. Blocking the loop on an ES query is bad; blocking it on a
multi-megabyte document read, once per attached file, is considerably worse.

Notably, `agents/document.py` makes that **exact same call correctly**, with both
`to_thread` and a timeout. Drafting simply diverged.

### What we changed

Both now use `await asyncio.to_thread(...)`. The drafting one additionally gets the
`TIMEOUT_CHROMADB_SEC` bound, matching how `document.py` does it.

**Files:** `agents/newacts.py`, `agents/drafting.py`

### And a guard, so there is no third instance

Rather than pin two line numbers — brittle, and useless against the next occurrence —
`tests/test_no_blocking_calls.py` walks the syntax tree of every agent module and fails
on **any** blocking call sitting directly in an async function.

It understands the legitimate patterns: a blocking call inside a nested plain `def` or a
`lambda` is fine, because those exist to be handed to `to_thread`.

The guard was validated against the **committed** versions of both files and correctly
flagged both real bugs:

```
agents/newacts.py:809    es.search(...)
agents/drafting.py:2074  get_full_attachment.invoke(...)
```

…and reports zero on the fixed working tree. A guard that has never been shown to fail
is not a guard, so this check is part of the test suite itself.

**Tests:** 4

---

## P-1 — Tests now actually run before deploying ✅

### What was happening

The deployment pipeline had one job before shipping, named **"Syntax & Import Check"**.
It confirmed the files parse and four modules import. It ran **zero** tests. Around 29
test files existed and none of them executed before code reached a server.

A syntax check catches typos. It cannot catch a logic regression — code that runs
perfectly and produces the wrong result. Every fix in this register is exactly that kind
of change.

### What we changed

Two new jobs in `.github/workflows/deploy.yml`, both of which now block the deploy:

**`structural`** — runs the AST guard from R-1. Imports nothing from the application, so
it needs no dependencies, no API keys, and no models. It finishes in seconds and cannot
fail for environmental reasons. This is the gate that is genuinely safe to hard-block on.

**`unit`** — runs the four offline suites written for Q-1 through R-1 (39 tests).

`deploy` changed from `needs: test` to `needs: [test, structural, unit]`.

### Two honest notes

**This correction matters:** an earlier version of this entry claimed these tests need
"no models." That is wrong. Importing the agent modules transitively pulls in `torch`,
`transformers`, and `sentence-transformers`, so the `unit` job installs
`requirements.lock.txt` in full. No model is ever *loaded* —
`EMBEDDING_SERVICE_URL` short-circuits the eager load and nothing dials it — but the
packages must be present. That is why `structural` is split out as the lightweight gate.

**The test list is curated, not `pytest tests/`.** Much of `tests/` needs live
Elasticsearch, real API keys, or PDF fixtures that are gitignored. Pointing CI at the
whole directory would produce a permanently red pipeline that everyone learns to ignore.
Suites get added to that list once they are known to pass offline.

**Files:** `.github/workflows/deploy.yml`

---

> ### Summary of completed work
>
> **8 files changed, 5 new test files, 44 tests passing.**
>
> Nothing is committed. Note that the test files are excluded by `.gitignore:157`
> (`tests/test_*.py`), so committing them requires `git add -f`.
>
> The working tree is **shared with another session** building OCR blur detection
> (`core/file_processor.py`, `core/gateway.py`, `frontend.html`, `scripts/`, test
> images). Stage by explicit path — never `git add .`.

---

# Part 2 — Security 🔴

These are not on the roadmap because they should not queue behind feature work.

---

## S-1 — The API key is committed to GitHub, and it is also the admin key 🔴

### What is happening

The value stored in `API_KEYS` — the key that authenticates requests to this API — is
**hardcoded as a literal string in 12 files that are committed to the repository**:

```
tests/chat_pdf_eval.py                  tests/verify_bug01_routing.py
tests/test_all_features.py              tests/verify_bug02_drafting_uses_pdf.py
tests/test_attach_pdf_judgement.py      tests/verify_bug04_document_qa.py
tests/test_features_3_4.py              tests/verify_bug06_08_no_hallucination.py
tests/test_fir_bail_draft.py            tests/verify_bug13_14_15.py
tests/investigate_writ_petition.py      tests/run_injunction_draft.py
```

This repository has a GitHub remote. Anyone with access to the repository has that key.

### Why it is worse than one leaked key

`ADMIN_API_KEY` is set to **the same value** as `API_KEYS`.

The system has two permission tiers. Normal keys can use chat endpoints. The admin key
unlocks `/pyapi/admin/*`, `/pyapi/metrics`, and `/pyapi/health/detailed` — operational
data and administrative functions.

Because both variables hold the same string, that committed value does not just grant
chat access. **It grants administrative access.** The two-tier permission system exists
in the code but is not actually in effect.

### What to do

1. Generate two **different** keys:
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`
2. Update `.env` with both.
3. Remove the literal from all 12 files — they should read the key from an environment
   variable instead. Most already support `LAWTECH_TEST_API_KEY`; they just fall back to
   the hardcoded value.
4. Consider the old key permanently compromised.

The `.env` file itself is **correctly** excluded from git (`.gitignore:2`). The exposure
comes entirely from those test files.

### ⚠️ This contradicts a statement made to leadership

`PROPOSAL_improvements.md` previously stated:

> *"No security exposure found. For the record, the review specifically checked: real
> credentials are not committed to the code repository."*

That was incorrect. It has now been corrected in that document, and marked explicitly as
a correction rather than quietly edited — if leadership read the original, they were
given a false assurance and should know it changed.

---

## S-2 — Credentials were shared in a chat transcript 🔴

OpenAI, Google, Langfuse, and Elasticsearch credentials were pasted into a conversation
during this work. If any of those are production credentials, treat them as exposed and
rotate them.

This is not a code defect — it is a consequence of sharing file contents — but it
belongs in the same review as S-1.

---

# Part 3 — Next up 🔵

One item remains here. The other two originally in this section — R-1 (blocking calls)
and P-1 (test gate) — are now complete and have moved to Part 1.

---

## Q-4 — Measure the relevance gate before optimising anything around it 🔵

*Costs nothing. Requires no code. Should be done first.*

### The situation

Several items further down this register are priced on an assumption: that the
**relevance gate** is rejecting too many good results.

The relevance gate is a filter that runs after retrieval. It takes the documents
Elasticsearch returned, checks them against the user's question using a similarity score
plus a small AI judge, and decides whether they are actually relevant. If it says no, the
agent abandons the results and falls back to a web search.

If that gate is over-rejecting, it is throwing away good legal sources and replacing
them with generic web content. That would be the single biggest quality problem in the
system, and it would reorder most of this register.

**Nobody has measured it.**

### The good news

**The measurement already exists.** Every one of the four places that calls this gate
already writes a log line containing the verdict, the similarity score, the AI judge's
confidence, and the name of the agent. No instrumentation is needed — only counting:

```bash
grep "Relevance judge verdict" logs/agent.log | grep -c "passed=True"
grep "Relevance judge verdict" logs/agent.log | grep -c "passed=False"
grep -c "Single agent pass-through" logs/agent.log
```

The third command answers a **second** open question at the same time — whether
single-agent responses really are the majority of traffic, which is the unverified
premise behind Q-6.

### Two caveats when reading the numbers

**The accept rate will look better than it is.** The gate "fails open" — if the AI judge
errors or times out, the result counts as a pass. Errors and genuine approvals are
indistinguishable in the current logs.

**The denominator is incomplete.** Three code paths skip the gate entirely and never
reach the logging line at all: exact-section lookups in the Judgment agent, and
exact-filter and cross-act queries in the Newacts agent.

### A correction to the original plan

The roadmap described this as *"~10 lines to build telemetry."* The telemetry is already
built. What is missing is a **Prometheus counter** for dashboards — and a correct one is
roughly 30–45 lines, because it needs labels to separate the three skip paths and to
distinguish a real approval from a failed-open error.

**Read the numbers first. Decide on the counter afterwards.**

*Blocked only on starting the server, which needs your go-ahead.*

---

---

# Part 4 — Backlog: retrieval quality

The theme running through this section: **the system retrieves reasonably well and then
loses track of what it retrieved.** Q-1 and Q-3 fixed two instances of that. The rest
are the same problem in other places, plus a set of genuine search-quality improvements
that are all gated behind having a way to measure them (Q-9).

---

## Q-5 — Extend source tracking to the remaining eight agents ⬜

Q-3 proved the state channel works, using drafting as the first writer. Eight retrieval
agents still do not write to it: Legislation, Judgment, Newacts, SCI, GST, Constitution,
Maxim, and Document.

**This is bigger than it looks.** Writing to the channel is mechanical. But the *point*
of the registry is to let a generator see what was retrieved — and right now **no domain
agent's generation prompt has a slot for it.** Only the orchestrator's synthesis prompt
has a `{retrieved_sources}` placeholder.

So this is a prompt-engineering change as much as a plumbing one, and each agent's prompt
needs individual attention.

---

## Q-6 — The quality reviewer never runs on single-agent answers ⬜

*Depends on Q-5.*

### What is happening

When the orchestrator receives results from exactly one agent, it takes a shortcut: it
returns that agent's answer directly, without synthesis and **without running
`self_refine`** — the quality reviewer that checks citations, scope, and hedging.

### Why this is likely most of your traffic

The planner's instructions specify single-agent as the **default**. Its hard rules state
that Non-legal, Document, and Legal_Concepts tasks must use exactly one agent, and the
general default is "single agent equal to the primary task."

So the most common request shape is probably the one with the least quality control.
**Q-4's log grep would confirm this** — it is currently an inference from the prompt, not
a measurement.

### Two things the original description missed

**Drafting-solo is already covered.** The drafting agent runs `self_refine` internally,
so a drafting-only request is reviewed. The real gap is single-agent **non-drafting**.

**Four other exits also skip the reviewer**, on multi-agent paths — plus a fifth skip
when the user asked for a table or the registry is empty. Fixing only the single-agent
path would leave those open.

---

## Q-7 — Seven of eight search paths use no semantic search at all ⬜

### What is happening

Only **one** function in the entire search layer uses embeddings: `search_newacts`, and
only when it decides to run in hybrid mode.

Every other search path is **BM25 only** — pure keyword matching:

| Search function | Method |
|---|---|
| `search_newacts` (hybrid mode) | BM25 + semantic |
| `search_legislation` | BM25 only |
| `search_legislation_by_topic` | BM25 only |
| `search_judgments` | BM25 only |
| `search_constitution` | BM25 only |
| `search_legal_maxims` | BM25 only |
| `search_drafts` | BM25 only |
| `search_newacts_by_topic` | BM25 only |

### Why, and what it would take

This is not a coding oversight. **The other indices have no vector field.** You cannot
run a similarity search against data that was never embedded at index time. Fixing it
means **re-indexing the corpus**, not changing code.

Worth knowing before investing: research consistently finds BM25 is a **strong baseline
for legal text specifically** — statutes are cited by exact identifiers, which is
precisely what keyword matching excels at. Published comparisons put hybrid search at
roughly 5–8% better than either method alone. Real, worth having, not transformative.

---

## Q-8 — Drafting picks its template from filenames alone ⬜

### What is happening

The drafting agent chooses which template to base a document on through this sequence:

1. Run a keyword search over the template corpus, requesting **100 results**.
2. Reduce those 100 passage matches down to a list of **unique file paths**.
3. Ask an AI model to pick the best one — **from the file paths only**, not the content.

Step 3 is deliberate; a corpus census found filenames highly descriptive (averaging 67
characters, only 0.8% opaque). That is a defensible design.

### The risk in step 2

The search asks for 100 **passages**, not 100 **documents**. If templates are stored as
multiple passages each, one long template can occupy many of those 100 slots. The number
of distinct documents the picker gets to choose from could be far smaller than 100 — and
the right template might rank 150th and never appear.

**The picker cannot choose what the search never returned.**

### How to check cheaply, and the likely fix

Log the length of the unique file-path list per request. If it is consistently near 100,
this is a non-issue. If it collapses to single digits, it is the drafting quality
problem.

The fix, if confirmed, is an Elasticsearch `collapse` on the source field, which makes
`size: 100` return 100 **distinct** templates. **No re-index required.**

---

## Q-9 — Build an evaluation set ⬜ **← this gates Q-10 and Q-11**

### Why this comes before the interesting work

Q-10 and Q-11 are both retrieval changes. Neither can be validated without labelled
data. You would be changing numbers you have no way to read — shipping on vibes, and
unable to tell an improvement from a regression.

An evaluation set is roughly 150 real legal queries, each labelled with the sections or
cases that *should* be retrieved. Then any change can be measured before and after.

Now unblocked, since Elasticsearch credentials are available.

### One design point that saves significant work later

Label the gold answer as a **substring of the source text**, never as "chunk number 7 of
document D."

Chunk numbers change whenever the chunking strategy changes — so a chunk-indexed label
set would need re-labelling for every variant you test, which would kill the project.
A substring label is derived at test time and works against any chunking strategy
forever.

---

## Q-10 — Add a reranker ⬜

*Depends on Q-9.*

### What a reranker does

Search returns results in score order. That order is decided by a fast method that
compares a query and a document *separately*. A reranker takes the top ~20 results and
re-scores them with a slower, more accurate model that reads the query and document
**together**, then reorders them.

### Why the evidence points here rather than at embeddings

The strongest argument is already documented in your own codebase. `core/retrieval_relevance.py`
records two production failures where keyword search returned acts that **prohibit**
injunctions in response to a query about **granting** one — and notes that those wrong
results scored **0.73** in embedding similarity against the correct text's **0.74**.

That gap is noise. An embedding model captures *topical* similarity — both documents are
about injunctions — but not **direction**: grants versus prohibits. A model that reads
query and document together can catch that. One that encodes them separately
structurally cannot.

You are also already paying for a cruder version of this: the AI judge in the relevance
gate. But that judge only accepts or rejects the whole set. It never reorders.

### ⚠️ Important correction — use the search engine's built-in reranker

An earlier version of this entry recommended running a reranking model in Python on the
application server. **That was the wrong approach**, for two reasons.

**First, your search backend has this built in.** Your cluster is **Amazon OpenSearch
Service** (`search-lawttorney-search-*.ap-south-1.es.amazonaws.com`), not Elasticsearch.
OpenSearch has supported a `rerank` search response processor since **version 2.12**,
which reranks results inside the cluster using a cross-encoder — hosted in-cluster, or
through a connector to Amazon SageMaker or Amazon Bedrock (including Cohere Rerank).

**Second, running it in Python would be actively bad here.** This application pins
`OMP_NUM_THREADS=1` and already loads about 1.4 GB of embedding models per worker.
Adding cross-encoder inference to that process would compete with request handling on a
single thread. Reranking inside the search cluster costs the application nothing.

*Note: Elasticsearch's own reranking features — `text_similarity_reranker` and the
Elastic Rerank model — are Elastic-licensed and **not available in OpenSearch**. The
OpenSearch processor is the relevant one for you.*

### The open question

**What version is the cluster?** The rerank processor needs 2.12 or later. A single
read-only request to the cluster root returns this, and `GET /_search/pipeline` shows
whether any pipeline is already configured. Both are harmless reads.

Also note: ML Commons on Amazon OpenSearch Service requires either ML nodes in-cluster
or a connector to SageMaker/Bedrock — additional AWS setup and cost.

---

## Q-11 — Uploaded documents are chunked far larger than the index can read ⬜

*Depends on Q-9.*

### What is happening

When a document is uploaded, it is split into pieces of **15,000 characters** each, and
each piece is converted to a vector for searching.

The model that creates those vectors accepts a maximum of **256 tokens — roughly 1,000
characters**. This was measured directly on this machine: `max_seq_length=256`.

So each 15,000-character piece is represented by a vector built from its **first ~7%**.
About 93% of every chunk is invisible to search.

### ⚠️ This is real but currently dormant — a correction

`IMPROVEMENT_GAPS.md` previously described this as degrading large-document retrieval,
via a search path that "retrieves ~30 chunks."

**That path was deleted on 2 August 2026** in a dead-code sweep. Today nothing searches
these chunks at all. The system reads every chunk back and reassembles the complete
document, which it sends to the AI in full — losing nothing.

### So what does it actually cost?

Two things:

1. **Wasted processing on every upload** — the system computes embeddings, single-threaded
   on CPU, inside a 30-second budget, to build an index nothing reads.
2. **A trap for future work.** The moment anyone re-enables search over uploaded
   documents, it will perform badly with no error to indicate why.

Concrete example of the future failure: a 300-page evidence bundle produces 40 chunks.
Chunk 12 begins with an annexure header and contains, at character 9,400, *"the defendant
admitted liability for Rs. 10,00,000."* Its vector describes only the header. Asked *"did
the defendant admit liability?"*, search would rank that chunk low and the system would
answer **"the document does not contain an admission"** — confidently, wrongly, with the
admission sitting in the file.

**Priority: lower than previously stated.** Not currently harming answers.

---

## Q-12 — Search ranking weights were never calibrated ⬜

The hybrid formula is `bm25_score + (100 × cosine_similarity)`.

BM25 scores typically land in single digits to about 30. Cosine similarity ranges from 0
to 1. Multiplying it by 100 produces 0–100 — which means **the semantic score dominates
almost entirely** and keyword matching acts as little more than a tiebreaker.

That may be intentional or may be an accident of a number chosen by hand. It was never
tuned against labelled data. *Needs Q-9.*

---

## Q-13 — The corpus embedding model is not built for legal text ⬜

The model indexing the legal corpus is BGE-large: general-purpose, English-only, with a
**512-token limit**. Long statutory provisions are truncated at index time as well — the
same class of problem as Q-11, milder.

A legal-domain model would likely improve retrieval. *Needs Q-9.*

---

## Q-14 — Uploaded-document embeddings skip normalization ⬜

The corpus embeddings are normalized (`normalize_embeddings: True`); the uploaded-document
embeddings are not, while results are ranked by raw distance.

This may or may not change real rankings. Low confidence — needs a test rather than a
rewrite. `core/clients.py:344`

---

## Q-15 — Drafts come back in the wrong court's format ⬜

*Reported by a lawyer reviewing real output. Different courts require different formats.*

### Root cause

**Template selection has no awareness of which court you are filing in.**

The word "court" appears in `agents/drafting.py` only inside prompt text — *"PRESERVE
every party name, court name, case number, forum…"*. That instructs the generator to keep
a court name it has already been given. **Nothing selects a template by court.**

The actual selection is:

```
BM25 keyword match on page_content (size 100)
  → deduplicate to unique file paths
  → an LLM picks one, seeing ONLY the filenames
```

No court filter. No court boost. No court field in the query. A Bombay High Court writ
template and a District Court plaint compete purely on keyword overlap, and the picker
cannot tell them apart unless the court happens to appear in the filename.

Because the reference template is the **only** source of document structure (see Q-8 and
the design note below), the wrong template means the wrong format — and nothing
downstream can catch it, because the system holds no ground truth for "what a Bombay High
Court writ should look like."

### Candidate fixes, cheapest first

1. **Show the picker more than filenames.** Pass the first ~200 characters of each
   candidate; a cause title almost always names the court explicitly. No re-index.
2. **Fix the funnel (Q-8).** `size: 100` returns 100 *passages*, not 100 documents. A few
   long templates can consume most slots, so the right court's template may never reach
   the picker. ES `collapse` on `source.keyword` fixes it. No re-index.
3. **Use the court as a retrieval signal.** The `UserIntent` extractor already runs on
   every request; extract the forum and boost or filter on it. *Open question: does the
   drafting index carry a court field? If not, this one needs a re-index.*
4. **Verify after picking.** One cheap check that the chosen template's court matches the
   request; re-pick if not.

### ⚠️ Constraint — do not solve this with court-format rules in code

`CLAUDE.md` invariant #5 explicitly forbids reintroducing `GENERIC_COURT_SKELETONS`,
`DOC_TYPE_TO_FOOTER_KIND`, and doc-type taxonomy. That approach existed, could not keep
pace with the variety of real Indian legal documents, and was deliberately removed.

**The reference template is the structural anchor.** The fix therefore belongs in
*choosing a better template*, not in encoding formats.

**Suggested start:** #1 and #2 together. Both are small, neither needs a re-index, and
together they would show quickly whether selection is really the cause.

---

## Q-18 — Drafting invented case facts when none were supplied ✅

*Reported after three identical test runs produced materially different drafts.*

### What was happening

The query `"Draft a bail application for cheating under Section 420 IPC"` names a
document type and nothing else — no party, no FIR number, no court. Three runs
produced three different documents. Two invented particulars:

```
Sandip Ramkisan Funde  ·  C.R. No. 271 of 2021  ·  Mumbai Naka Police Station, Nashik
"in judicial custody"  ·  "no criminal antecedents"  ·  "sole breadwinner"
```

The third correctly emitted `[ACCUSED'S NAME]`-style placeholders.

### It was not hallucination in the usual sense — verified against the corpus

I fetched both templates from Elasticsearch and searched them:

| | In the template? |
|---|---|
| `Funde` / `Nashik` / `Mumbai Naka` / `271` | ❌ **absent** |
| *"sole breadwinner of his family, and his aged parents are dependent on him"* | ✅ **verbatim** in the Section 439 template |
| *"permanent resident and is working at ____"* | ✅ in both templates |

The **sentences** are template boilerplate. The template carries 22 `____` blanks.
The model kept the sentences and **filled the blanks with fiction**.

### Root cause — the guards are conditional, and the condition was false

Every fact-grounding rule assumes a case source exists:

```
config/prompts.py:1310   "do NOT substitute canonical example values
                          WHEN THE SOURCE NAMES DIFFERENT REAL PARTIES"
drafting.py (closing)    "MUST come VERBATIM from UPLOADED SOURCE
                          DOCUMENTS or the USER QUERY"
```

With no uploaded document and no particulars in the query, **every one of those
guards evaluates to a no-op.** What survives is the emphatic heading
`prompts.py:1302` — *"USE REAL FACTS FROM THE SOURCE, NOT PLACEHOLDERS"*.

The model was also squeezed from both sides:

| Rule | Pushes toward |
|---|---|
| "USE REAL FACTS … NOT PLACEHOLDERS" | inventing |
| `placeholder_marker` (`self_refine.py:496`) — *"if they survive, MAJOR"* | **penalises the correct behaviour** |
| `canonical_example_substitution` | only flags a fixed name list — the invented names weren't on it |

**Not temperature.** Both paths already use `temperature=0.0`, with a comment
describing this exact bug from a previous attempt to fix it that way.

### What we changed

**1. Placeholder mode.** When `user_facts` is empty, an explicit directive is
appended **last** (highest salience) to both generation paths, inverting the rule:
every case-specific value must be a bracketed placeholder. It names the exact
categories observed being invented, and instructs that template boilerplate be
bracketed — `[IF APPLICABLE: The applicant is the sole breadwinner…]` — rather
than asserted or deleted.

**2. A regex-only validator**, `validate_draft_grounding`. No LLM call, no added
latency. Strips bracketed spans first, so placeholders are correct output; flags
bare FIR numbers, police stations, personal names, dates, amounts, and the
custody / antecedents / breadwinner / residence assertions. Anything the user's
own query stated is allowed through.

**3. One bounded regeneration** — single-pass path only, and only when a
violation is found, so a clean draft costs nothing.

**4. Logging** by category and count, never the invented values.

**Files:** `agents/drafting.py` · **Tests:** 12

### Known limitation

The section-wise path **logs but does not regenerate**. Redoing N sections costs N
Pro calls, and a single corrective pass over an assembled fan-out draft risks
flattening its structure. Since the bail application took the section-wise path
(9 sections), this is where a violation would still reach the user — visible in
the logs rather than silent.

### Deliberately not done

- **No canonical section schema.** CLAUDE.md invariant #5 forbids reintroducing
  document skeletons; the reference template is the structural anchor.
- **No extra `self_refine` loop** — it already accounts for ~35% of request time.
- **No hard-coded "420 IPC → 439 CrPC"** legal mapping.

### Still open — the structural variance

This fixes invented *facts*. It does **not** fix varying *structure*: the three
runs picked different templates (Section **439** twice, Section **436** once —
different bail provisions, hence 9 sections vs 5). That is template selection —
**Q-8** and **Q-15**.

---

## Q-17 — Parent-child retrieval over the legal corpus ⬜

*Proposed by the user. Added 11 Aug 2026 — it had been discussed at length and never
recorded, which is exactly the kind of loss this register exists to prevent.*

**Not the same as Q-11.** Q-11 is about **uploaded documents** (chunk size vs the QA
embedder). This is about the **Elasticsearch legal corpus** and how statutes are indexed
for search. Different data, different fix, different blocker.

### The proposal

Index a statute at two levels. The **parent** is the whole section — say Section 318 BNS.
The **children** are its internal parts: definition, punishment, illustrations,
explanations, provisos.

Embed only the children, so each vector describes one specific idea rather than an
averaged blur of the whole section. When a child matches a query, **return its parent** —
so the model receives the complete provision, not an orphaned fragment.

Fine-grained matching, complete legal context.

### Why the instinct is right

Indian statutes have a genuine hierarchy — Act → Chapter → Section → Sub-section →
Explanation → Illustration → Proviso — and it is **printed in the text**. Splitting along
those lines is how lawyers actually read and cite. It is a far better fit than generic
semantic chunking, which is why that was rejected (see Part 8).

There is also a real defect underneath it: **BGE-large caps at 512 tokens (~2,000
characters)**. A long section — definition plus punishment plus four illustrations —
exceeds that, so its embedding is built from the opening only. Same disease as Q-11,
milder, on the corpus side. Splitting into children fixes it.

### Why it is not the next thing to do

**Half of it already exists.** ES documents are already one-section-per-document, with
`section_number` and `act_name`. Retrieving a section and returning that whole section
**is what happens now**. The missing half is only the child layer.

**It would improve one search path out of eight.** Parent-child improves *embedding*
precision — and per Q-7, only `search_newacts` in hybrid mode uses embeddings at all.
Every other path is BM25, which is unaffected by how finely you embed.

**It requires a full re-index**, which is external work.

### Sequencing

| Do this first | Why |
|---|---|
| **Q-9** — evaluation set | Otherwise there is no way to tell whether it helped |
| **Q-10** — reranker | Bigger gain, helps every BM25 path, no re-index |
| **Q-7** — vectors on the other paths | Makes embedding quality matter more broadly |
| **Then Q-17** | At that point more paths depend on vectors, so the payoff is real |

**Verdict: right technique, correct instinct about legal structure — wrong point in the
order.** Doing it now buys a better version of the one retrieval path used least broadly.

One caveat for when it lands: returning full parent sections at `size: 20` inflates
context per query considerably. Worth watching the token cost alongside the recall gain.

---

## Q-16 — English draft → "convert to Marathi" does not translate ⬜ **needs discussion before implementing**

*Reported by a lawyer. Three separate causes compound here.*

### Cause 1 — there is a translation tool, and nothing can call it

`translate_draft(draft, target_language)` exists at
`tools/shared/elasticsearch_tools.py:1579`. It is written, it preserves section numbers
and citations in English, and it is registered in `AGENT_TOOLS["drafting"]`.

But `AGENT_TOOLS` is only ever read by `sci_judgment.py` and `gst_judgment.py`. Drafting
is a fixed pipeline and never consumes its entry. **The tool is unreachable dead code** —
the third instance of this pattern, after the dead rewrite prompts (Q-2) and the dead
state channel (Q-3).

### Cause 2 — translation is only a prompt request, never a step

With that tool unreachable, the only mechanism is `localize_prompt`, which appends a
directive to the system prompt. So generation is:

```
English reference template  +  English source draft  +  "please write in Marathi"
```

The drafting corpus is **English-only** (`agents/drafting.py:483-485` says so). The model
is surrounded by English context and asked once to write in Marathi. **Nothing verifies
the output language afterwards.**

### Cause 3 — the safety net is disabled on exactly this request

`self_refine` has a language-mismatch check that *forces* the critic to run when source
and target languages differ. That is the guard designed for this.

But the critic is skipped entirely in review-and-redraft mode:

```python
if draft and intent_obj is not None and not use_upload_as_ref:
```

"Here is my English draft, convert it to Marathi" **is** review-and-redraft of an upload.
So the one request type that most needs the language check is the one where it is turned
off.

### Options — to be decided, not yet chosen

| Option | What it does | Risk |
|---|---|---|
| **A. Verify output script, translate if wrong** | After generation, check whether the draft is actually in the target script; if not, run `translate_draft`. | Low — does not touch the critic short-circuit at all |
| **B. Run the critic on language mismatch even in redraft mode** | Restores the intended guard | Medium — the short-circuit exists because the critic rewrites correct redrafts into generic templates |
| **C. Route "translate this" as its own intent** | Skips the drafting pipeline entirely; no English template to fight | Larger change, but architecturally the most correct |
| **D. Wire up or delete `translate_draft`** | Stops it being neither used nor removed | Low |

**My preference is A + D**, because they fix the reported bug without touching the
sensitive review-and-redraft behaviour, and they turn a silent failure into a detectable
one. **C** is the better long-term answer. **B** is the smallest change but carries the
regression the short-circuit was added to prevent.

**Status: paused for discussion at the user's request. Nothing implemented.**

---

# Part 5 — Backlog: cost

These are the most straightforward items in the register. Each is a contained change with
a directly measurable result, because the system already reports per-request cost.

---

## C-1 — ⚠️ CORRECTED: the premise was wrong, this is not free money ⬜

**The original claim** (inherited from `IMPROVEMENT_GAPS.md`): *"Four structured-extraction
calls run on Gemini 2.5 Pro where the cheapest tier — Flash-Lite — does the same job.
A 4–12× cost reduction with minimal quality risk."*

**Verified 11 Aug 2026 — that is not what those four calls are.** Every one of them is
**Google-Search-grounded generation**, not extraction:

| Line | Function | What it actually does |
|---|---|---|
| `:93` | `web_search_grounded` | The full legal analysis, grounded, 8000-token output |
| `:148` | `analyze_scenario` | Applicable laws, legal position, precedents, remedies — grounded |
| `:237` | `find_similar_cases` | Grounded case-law research |
| `:339` | `get_legal_news` | Grounded news search over named authoritative sources |

All four pass `tools: [{"google_search": {}}]`. Lines 93 and 148 **are the user-facing
scenario answer.**

**And the extraction calls are already cheap.** The genuinely structured-output calls in
this same file already use Flash-full, not Pro:
`cite_provisions:185`, the case-parsing step at `:254`, and `suggest_remedies:286`.

So the proposal as written was "downgrade the model that writes the legal analysis to the
cheapest tier available." That is a quality regression, not a free saving —
and `find_similar_cases` is the worst candidate of all, since its own prompt says *"Only
cite cases you are confident exist."*

### The honest, smaller opportunity

**Pro → Flash-full** (not Flash-Lite) on the two lower-stakes calls. Flash supports
Google Search grounding, and `settings.py` already sets `scenario_web_grounded` to
`gemini-2.5-flash`, so there is precedent in the codebase.

| Call | Candidate? |
|---|---|
| `get_legal_news:339` | ✅ Best — summarising search results, low reasoning demand |
| `find_similar_cases:237` | ⚠️ Maybe — output is re-parsed by Flash anyway, but hallucinated citations are the risk |
| `analyze_scenario:148` | ❌ Keep Pro — this is the answer |
| `web_search_grounded:93` | ❌ Keep Pro — this is the answer |

Realistic saving: roughly **4×** on **one or two** calls, not 4–12× on four.
That is worth having, but it is a quality trade on legal output and should be decided
deliberately — ideally measured once Q-9 exists.

**Status: not implemented. Premise corrected; needs a decision on scope.**

---

## C-2 — The premium quality-review pass runs on most answers ⬜

The `self_refine` step fires whenever any source was retrieved — which is most
substantive queries — and its refine stage uses the **premium** model. Gating it more
selectively removes a premium call from the majority of requests.
`core/self_refine.py:1423`, gating at `:1514`

---

## C-3 — Paying for reasoning we do not need ⬜

Modern models can perform internal "thinking" before answering, and that thinking is
billed as output tokens at the premium rate.

It is switched on for document Q&A and drafting — both **format-following** tasks that do
not benefit from it. Turning it off on those paths is a straight saving.
`core/clients.py:216`

---

## C-4 — No sensible ceiling on response size ⬜

The default maximum output is set to the absolute maximum the model allows: **65,535
tokens**. Any code path that forgets to lower it can emit an enormous, expensive
response. The code itself documents past runaway incidents.
`core/clients.py:171/192/216/235`

---

## C-5 — Explicit prompt caching is not configured ⬜

*This one is smaller than it first appears, and the register should say so.*

The system sends very large static instruction prompts on every call. It would seem
obvious to cache them.

**But automatic caching is already working** — telemetry showed roughly 20,400 cached
input tokens on a single real drafting request. The provider's implicit discount already
applies opportunistically.

The genuine gap is **explicit** caching, which makes that discount reliable with a
guaranteed lifetime rather than best-effort. Real, but incremental — **not "75% off
everything."**

---

## C-6 — The same query gets rewritten up to three times ⬜

In a multi-agent turn, the user's query can be rewritten by three separate AI calls: the
memory node, intent normalization, and per-agent rewriting. Folding these together
removes one or two cheap calls per query.
`agents/memory.py:189`, `agents/orchestrator.py:567/776`

---

# Part 6 — Backlog: reliability

---

## R-2 — Time limits are per-request, not per-agent ⬜ *(partially addressed)*

`core/deadline.py` is **new since the original analysis** — the request now carries an
overall 285-second budget, and the drafting agent honours it.

But that bounds the **request**, not the individual agent. One stuck external call can
still consume the entire budget, and no agent other than drafting reads the deadline.
Per-agent budgets remain outstanding.

---

## R-3 — The relevance gate treats its own failures as approvals ⬜

If the AI judge errors or times out, the code returns "relevant = true" and proceeds.

The intent is sensible — a judge outage should not break search. The consequence is that
**an outage looks identical to normal operation**, and any accept-rate you measure (see
Q-4) is biased upward by an unknown amount.

*Half of this entry is now fixed:* agent errors **are** recorded in metrics —
`agent_errors_total` exists at `core/metrics.py:54`. Only the fail-open behaviour remains.

---

## R-4 — Concurrent first requests could build the same singleton twice ✅

*Moved to done 11 Aug 2026. Scope grew: it was three singletons, not one.*

### What was happening

Three functions in `core/clients.py` used the classic unguarded lazy-init:

```python
global _thing
if _thing is None:
    _thing = build()      # ← two threads can both reach this line
return _thing
```

Agents call these from `asyncio.to_thread`, so the concurrency is real, not theoretical.

The register originally listed only the Elasticsearch client. Auditing found **two
more, and they are the expensive ones**:

| Singleton | Cost of building it twice |
|---|---|
| `get_es_client` | A leaked connection pool; "Search client initialized" logs twice |
| `get_retriever_embeddings` | **BGE-large loaded twice — about 1.3 GB per extra copy** |
| `get_qa_embeddings` | MiniLM loaded twice — about 90 MB |

### Why the embedding races were not obvious

`core/clients.py` eager-loads both models at import, which normally hides the race.

It is reachable in two situations: when `EMBEDDING_SERVICE_URL` is set (the preload is
skipped entirely), and — more importantly — **when the eager load fails**. That failure
is caught and logged as a warning, leaving the globals at `None` for the first concurrent
requests to fight over. That is not hypothetical; it is exactly the state this machine
was in before the models were downloaded.

Loading 1.3 GB twice, single-threaded under `OMP_NUM_THREADS=1`, on a box already
holding both models, is a meaningfully bad outcome.

### What we changed

Double-checked locking on all three: a lock-free fast path once initialised, and a
re-check inside the lock so the loser of the race returns the winner's instance.

The embedding pair shares one lock; the ES client has its own. Deliberately **not**
reusing `_es_lock` — that guards the circuit-breaker counters, and holding it across
client construction would block every `is_es_available()` check.

### The test has a negative control

`tests/test_client_singletons.py` replaces each builder with a deliberately slow counting
stub, widening the race window from microseconds to ~50 ms, then fires 8 threads through
a barrier so they arrive simultaneously.

It also runs the **old unguarded pattern** under identical conditions and asserts it
**does** build more than once. Without that control, the other tests could pass simply
because the harness never produced real concurrency. It fails as expected — so the
concurrency is genuine and the locking tests mean something.

**Files:** `core/clients.py` · **Tests:** 5

---

## R-6 — A crashed quality check reported itself as a pass ✅

*Found in a real production log, 13 Aug 2026, while verifying Q-18.*

### What was happening

`_critique` builds its model with `with_structured_output(Critique, include_raw=True)`,
which returns `{"raw", "parsed", "parsing_error"}`. **`parsed` is `None` whenever the
model's output fails schema validation.** The code did:

```python
result: Critique = raw_and_parsed["parsed"]
...
passes=result.passes            # ← AttributeError on None
```

The surrounding `except Exception` caught that and logged the generic *"Critique LLM call
failed"*. Two consequences:

- **`parsing_error` — which names the exact schema violation — was thrown away.** The
  real cause never reached the logs.
- The fail-open `Critique(passes=True)` was **indistinguishable from a genuine pass**.

### What that hid

From the actual log:

```
Critique result   | passes=False | violation_count=8 | critical=1 | major=7
Refinement done   | original_len=5630 | refined_len=6918 | len_diff=+1288
Critique          | CRASHED → AttributeError
Self-refine passed ✅
```

The critic found **8 violations including one critical**, the refiner **grew** the draft
by 1,288 characters, the verification critique then crashed — and the unverified draft
shipped under a success message.

The loop's low-confidence guard could not help: `if critique.passes:` returns **before**
the `confidence < 0.5` check ever runs.

### What we changed

1. **Explicit `None` handling** — returns a `Critique` instead of raising, and logs
   `parsing_error` plus the raw response length.
2. **All three fail-open exits share `_UNVERIFIED_NOTE`** — call error, unparseable
   output, and circuit-breaker-open previously returned indistinguishable unlabelled
   passes.
3. **The loop tells them apart.** A fail-open now logs
   `WARNING — Self-refine returning UNVERIFIED … refined=True` instead of
   `INFO — Self-refine passed`. The `refined` flag is the important part: it says the
   draft *was* modified and then never checked.

**Files:** `core/self_refine.py` · **Tests:** 7

### Deliberately unchanged — a decision for later

**Fail-open behaviour stays.** A critic outage must not block a user's response; the fix
makes it visible, not fatal.

But when a *post-refinement* check fails, an unverified modified draft still ships. The
alternatives — fall back to the pre-refinement draft, or retry the parse once — both
change behaviour meaningfully. Left as-is and made loud instead. Worth a decision.

---

## R-5 — Failed uploads re-do all the expensive work ⬜

When storing an uploaded document fails, the code retries up to three times. But each
retry **deletes the collection and re-computes every embedding from scratch**.

Embeddings are a pure function of the text — a failed *write* does not invalidate them.
So on the exact failure the retries exist for (a saturated connection pool), the code
burns up to **three times** the compute inside a 30-second ceiling, making the timeout it
is trying to survive **more likely**.

Moving the embedding step outside the retry loop roughly **triples** the number of chunks
that fit in the budget. Needs rebasing onto the current `file_processor.py`.

---

# Part 7 — Backlog: process

## P-2 — Configuration values scattered across the codebase ⬜

Output caps, client timeouts, and character limits are hardcoded at call sites rather
than centralised in settings, so changing one requires finding all of them.

## P-3b — A documented design that was never built ⬜

`SYSTEM_MAP.md` describes a retrieval design using **kNN search over a `dense_vector`
field, merged with Reciprocal Rank Fusion**. It gives query examples and a scoring
formula.

**None of it exists.** A repo-wide search for `dense_vector`, `knn`, `rrf`, or
`reciprocal` across all Python files returns **zero matches**. The live code uses a
different mechanism entirely, over a field with a **different name** (`embedding`).

This is more dangerous than ordinary drift. Anyone re-indexing the corpus from that
document would write data to the wrong field name and produce a vector search that
silently returns nothing useful. It is also the most likely source of the belief that
"a reranker exists but isn't being used" — RRF *is* a rank-fusion technique, and the
document describes it as though it were implemented.

Also outstanding: a stale hardcoded Elasticsearch IP in several documents. The code
default was deliberately changed to `localhost:9200` so a missing configuration fails
visibly instead of silently reaching a production server.

## P-4 — Model settings that nothing actually reads ⬜

*Found while answering "which model does OCR use?". Added 11 Aug 2026.*

`core/settings.py` defines a `MODELS` dictionary mapping each task to a model id. Several
entries are **never consulted** — the code hardcodes a different model at the call site:

| Setting | Says | Code actually uses |
|---|---|---|
| `pdf_vision_ocr` | `gemini-2.5-flash-lite` | **`gemini-2.5-flash`** via `get_gemini_flash_full` |
| `scenario` | `gemini-2.5-pro` | Hardcoded `model="gemini-2.5-pro"` inline in `scenario_tools.py` |

The OCR one is the clearer defect. `core/file_processor.py` calls
`get_gemini_flash_full` directly and comments in **three places** that it deliberately
uses Flash, *not* Flash-Lite, "so transcription quality is preserved." The setting says
the opposite.

**Why it matters:** someone tuning cost would change `MODELS["pdf_vision_ocr"]`, observe
no change in spend, and have no idea why. Same class as P-3b — configuration that
describes something the code does not do.

**Fix:** either make the call sites read `MODELS`, or delete the entries that lie.
Deleting is safer, since the code's own comments explain why those choices are
deliberate.

## P-5 — The codebase hedges between two search backends ⬜

`requirements.txt` pins **both** `opensearch-py>=2.4.0` and `elasticsearch>=8.0.0`, and
`core/clients.py:34-37` prefers OpenSearch with an Elasticsearch fallback.

But the backend is unambiguous: `ES_URL` points at **Amazon OpenSearch Service**
(`*.es.amazonaws.com`), and `opensearch-py` is what actually runs.

**Why it matters:** the dual import invites someone to write Elasticsearch-only query
syntax that silently fails or behaves differently on OpenSearch. It is also the most
likely reason Q-10's original write-up reached for Elastic's built-in reranker, which
**does not exist in OpenSearch**.

Low urgency, but worth resolving so nobody has to work out which engine they are
targeting.

---

# Part 8 — Rejected, with reasons

Recording these matters as much as recording the work. Each cost real investigation time,
and without a written reason someone will propose them again in three months.

---

## ⛔ Adding the relevance gate to the SCI and GST agents

**Proposed as:** *"These two agents have no gate. The config already exists — someone
wrote a domain hint for SCI_Judgment that nothing calls. We're just connecting the wire."*

**Why we are not doing it:** it is not a wire.

Both agents are built with `create_react_agent`. That means the entire cycle — the AI
decides to search, calls a tool, reads the result, decides again, and writes its answer —
happens inside **one function call**. The agent code regains control only after the answer
already exists.

Three consequences:

1. **There is no moment to insert a gate.** The gate's job is to check retrieved passages
   *before* generation. That moment does not exist in this architecture.
2. **The data is the wrong shape.** These tools return formatted display strings
   (`**Parties** (DB ID: …)`), not document text. Every existing gate call passes real
   passage content.
3. **Gating afterwards cannot prevent anything.** By the time you can inspect the result,
   the answer is written. Rejecting means discarding a trajectory that already consumed
   up to its 90-second budget — a redo, not a gate.

**And the premise is partly false.** There is no `GST_Judgment` hint at all. Wiring GST
would silently fall back to the *Legislation* hint, judging tax appellate orders against
a description of "statutory provisions of central or state legislation" — a wrong-hint
failure, not a no-op.

**If revisited:** gate only the rare branch where the agent made no tool call at all and a
single discrete result exists, and add the missing GST hint. Also worth noting that SCI
and GST are not uniquely exposed — drafting, scenario, and document have no gate either,
and SCI/GST additionally lack the apology guard the other agents have.

---

## ⛔ Building a shared retrieve → gate → fallback helper

**Proposed as:** *"Fixes 3, 4 and 5 are the same underlying problem — every agent
hand-rolls this logic and they have drifted. One shared helper fixes all three and stops
future drift."*

**Why we are not doing it:** they are not the same size.

Verified: Q-2 was **12 lines**. The SCI/GST gate is **architectural**, for the reasons
above. Q-5 needs **state design and per-agent prompt changes**. A shared abstraction over
three problems of such different shapes would be forced, and would make each of them
harder to reason about.

**The underlying observation is correct** — `sci_judgment.py` and `gst_judgment.py` are
near-duplicates and have diverged. That is worth addressing on its own terms. These three
items are simply not the right vehicle.

---

## ⛔ Semantic chunking for uploaded documents

**Proposed as:** splitting documents at meaning boundaries rather than fixed sizes, using
`langchain_experimental.SemanticChunker`.

**Why we are not doing it:**

Semantic chunking finds boundaries by embedding every sentence group and looking for
similarity drops. On a 700 KB document that is roughly **490 seconds** of computation, on
a machine with a 30-second budget for the entire operation.

A regular expression keyed to Indian legal document structure — numbered paragraphs,
cause titles, `PRAYER`, `VERIFICATION`, `WHEREFORE` — finds better boundaries in about
**0.1 seconds**. These documents *declare* their structure in the text. Paying 3,500×
more to statistically estimate a boundary that is printed on the page is not a good
trade.

It would also add a dependency the library's own maintainers mark as unstable.

---

## ⛔ Reducing the uploaded-document chunk size globally

**Proposed as:** the chunks are too big for the embedding model (see Q-11), so make them
smaller.

**Why we are not doing it:** this has been tried and it took production down.

The arithmetic: the entire chunk-embed-write operation has a 30-second budget and retries
three times. That works out to a ceiling of roughly **52 chunks** for a large document —
which independently reproduces the 15,000-character size that was chosen empirically.

When chunk size was previously set to 1,000, a 524-page PDF produced about **750
embedding calls** and the system hung silently for **600 seconds** in one case and **1
hour 44 minutes** in another. This is documented in
`Buglist/v2_file_processing_regression_2026-06-23.md`.

Any global reduction re-creates that outage. If chunk size must come down, R-5
(embed-once) has to land first — it roughly triples the ceiling — and the change should
be gated on document size.

---

# Part 9 — Corrections made to the other documents

`IMPROVEMENT_GAPS.md` was pinned to commit `cfd24d2` and had fallen **11 commits behind**.
Five entries were corrected on 9 August 2026:

| Entry | What was wrong |
|---|---|
| Uploaded-document truncation | Described a code path **deleted on 2 Aug 2026**, and used it to justify priority. Real but dormant — priority **down**. See Q-11. |
| Committed `.env` templates | Concluded *"no exposure."* **There is exposure** — the API key is in 12 committed files and admin equals user. Priority **up**. See S-1. |
| Failures silently masked | Half fixed — `agent_errors_total` now exists. See R-3. |
| Most agents have no time limit | Partially addressed — `core/deadline.py` is new. See R-2. |
| Documentation drift | Partially fixed. The remaining part is worse than drift — see P-3b. |

**Re-confirmed still accurate:** the blocking database call, the syntax-only deploy check,
the unlocked client startup race, and the absence of any reranking step.

`PROPOSAL_improvements.md` had its security assurance corrected, and the large-document
item rewritten and downgraded. Both are marked **as corrections** rather than silently
edited.

---

# Part 10 — A pattern worth watching for

Twice now, a plan has been costed by checking that a function **exists** rather than
checking what it **does**:

**Q-1's plan** said *"reuses the existing adapters."* The adapters read object
attributes; the search tools return dictionaries. As written, every record would have
come back empty and the fix would have shipped doing nothing.

**Q-2's plan** said the rewrite helper *"exists and works."* It does — for five agent
names. For any other name it returns the query unchanged with no error, so the proposed
"ten agents" version would have been eight silent no-ops.

Both would have produced changes that **look landed and are not**: green tests, clean
diffs, no behaviour change. That is the most expensive kind of bug, because nobody goes
looking for it.

**The rule this suggests:** treat every *"X already exists, we just need to call it"*
claim as unverified until someone has read X's signature and confirmed the data you plan
to hand it is the shape it expects.

---

# Glossary

**BM25** — The standard keyword-matching algorithm used by search engines. Scores
documents by how often query terms appear, adjusted for term rarity and document length.
Very strong for exact identifiers like "Section 302 IPC."

**Embedding** — A list of numbers representing a piece of text's meaning. Similar
meanings produce similar numbers, so you can search by meaning rather than exact words.

**Bi-encoder** — Encodes the query and the document **separately**, then compares the two
vectors. Fast, because documents can be encoded once in advance. Weaker at nuance,
because neither side ever "sees" the other.

**Cross-encoder** — Reads the query and the document **together** in a single pass and
outputs a relevance score. Much more accurate — it can catch negation and direction
("grants" vs "prohibits") — but far slower, so it is used only to rerank a small
shortlist.

**Reranker** — A second pass that reorders the top results from an initial search using a
more accurate (and slower) model, typically a cross-encoder.

**RRF (Reciprocal Rank Fusion)** — A method for merging two ranked lists (for example
keyword and semantic results) using each document's *rank* rather than its raw score.
Documented in `SYSTEM_MAP.md`; never implemented (see P-3b).

**Hybrid search** — Running keyword and semantic search together and combining the
scores.

**Chunk** — A piece of a document. Documents are split because search and embedding
models both have size limits.

**Relevance gate** — This system's post-retrieval filter: a similarity floor plus a small
AI judge deciding whether the retrieved documents actually answer the question. It
accepts or rejects the whole set; **it does not reorder**.

**Fail-open** — When a safety check errors, it allows the request through rather than
blocking it. Keeps the system running during an outage, but makes failures invisible.

**Tier-2 fallback** — The middle step of this system's three-step search ladder: rewrite
the query and search again, before resorting to a web search.

**ReAct agent** — An agent pattern where the AI loops — decide, call a tool, read the
result, decide again — until it can answer. The whole loop runs inside a single call,
which is why inserting a checkpoint mid-loop is hard (see Part 8).

**Reducer (LangGraph)** — A function that merges values when several parallel agents write
to the same shared state field, instead of letting them overwrite each other.

**State channel** — A named field in the shared object that agents read from and write to
as a request flows through the system.

**Source registry** — This system's record of what was genuinely retrieved during a
request, used to verify that citations in the final answer are real.
