# Pipeline Investigation — What's Actually Confusing the System

Deep dive into the two failure modes observed in [flow_analysis.md](flow_analysis.md).
The question isn't "does the pipeline have gaps" — every pipeline does — it's
**"what pieces are logically broken?"** i.e. wrong by construction, not just
missing a feature.

Findings, in order of blast radius:

---

## 🔴 Break #1 — Multi-intent drops are structural, not accidental

**Location**: [`agents/orchestrator.py:1383`](../../agents/orchestrator.py#L1383)

```python
if cite_appendix_on:
    citation_agents = _select_citation_agents(extracted_intent, tasks_planned)
    for ca in citation_agents:
        if ca not in tasks_planned:
            tasks_planned.append(ca)
    tasks_planned = tasks_planned[:4]
    log.info("Drafting citation appendix enabled", ...)
else:
    tasks_planned = [t for t in tasks_planned if t == "Drafting" or t == "Document"][:3]
    log.info("Drafting citation appendix skipped", ...)
```

**What breaks**: the `else` branch runs whenever `cite_appendix` is unset
(env default = OFF). It **filters `tasks_planned` down to `[Drafting]`
(and Document if present)** — regardless of what the LLM classifier
actually decided.

**Turn 1 evidence** (server.log:59):
```
Classify+plan completed | task=Drafting |
agents=['Drafting', 'Legal_Concepts', 'Legislation', 'Maxim']
                     ^^^^^^^^^^^^^^^^^  ^^^^^^^^^^^^^  ^^^^^^^^  ← LLM said all four
```
Log line 63:
```
Plan phase completed | agents_planned=['Drafting'] | agent_count=1
                                      ^^^^^^^^^^^^  ← reduced to one
```

The 3 non-Drafting agents the LLM specifically selected for the mega-prompt
(Legal_Concepts for the consumer scenario, Legislation for §65B / §420 IPC
lookups, Maxim for legal maxims) were **silently discarded by a filter that
has nothing to do with what the LLM decided.**

**Why it's logically broken, not a gap**:

The `else` branch's purpose (per the comment on line 1339 above) is
"citations doubled response length and ~50% of token spend without making
the draft itself more file-ready. Callers that want the appendix pass
`cite_appendix=true`." That's fine for suppressing *auto-added citation
agents* — but the code doesn't distinguish "citation agents I would have
added" from "agents the LLM classifier already decided were part of the
plan." It strips both.

**What SHOULD happen**: the `else` branch should only remove agents that
were added *by* `_select_citation_agents(...)` for the appendix — not the
ones the LLM classifier had in its output. Compare set-difference, not
a blanket filter.

**Blast radius**: every drafting request with additional asks (arguments,
citations, definitions, scenario analysis) drops silently. Every multi-
intent prompt where drafting is the primary task returns only the draft.
This is exactly what the user observed on the turn-1 mega-prompt.

---

## 🔴 Break #2 — Cross-turn state leakage via the `agent_results` reducer

**Location**: [`core/state.py:88-90`](../../core/state.py#L88-L90)

```python
def _merge_agent_results(existing: dict, new: dict) -> dict:
    """Custom reducer: merge new agent results into existing dict without overwriting."""
    return {**existing, **new}
```

**What breaks**: this reducer is called by LangGraph whenever any node
returns a new `agent_results` dict. **It never resets between turns.**
Because the local run uses an in-memory checkpointer (and prod uses
Postgres — same behaviour), turn N's `agent_results` starts with every
completed agent from turn N-1 already present.

**Turn 2 evidence** (server.log:117):
```
Synthesize phase started | agents_received=['Drafting', 'Scenario'] | registry_size=2
                                            ^^^^^^^^^^  ← inherited from turn 1
                                                        ← Scenario ran fresh
```
Turn 2's plan was `agents_planned=['Scenario']` (log line 112). Only
Scenario ran. But the synthesizer sees BOTH agents — including turn 1's
English partition draft — because the reducer merged them.

**Why it's logically broken, not a gap**:

A reducer named `_merge_agent_results` that merges across *turns* is doing
the wrong job. Within a single turn's fan-out (Drafting + Scenario in
parallel), merging is correct — but that merge happens within one graph
run; the checkpointer is what persists the state to the next run. Nothing
in the code path clears `agent_results` when the graph is re-invoked on
turn 2.

The `messages` field has a hard cap (`_MESSAGES_CAP = 40`, capped-add
reducer at line 115). The `source_metadata` field has a cap
(`_cap_source_metadata` at line 93). The `tokens_consumed` field
accumulates deliberately (`_sum_tokens` at line 103). But
`agent_results` — the field whose CONTENT drives the entire synthesis
step — silently accumulates too, with no cap and no reset.

**Downstream effect on turn 2**:

The draft-aware synth path at [`orchestrator.py:1760-1804`](../../agents/orchestrator.py#L1760-L1804):

```python
if "Drafting" in valid_results:
    drafting_result = valid_results.pop("Drafting")    # ← turn 1's English draft
    citation_results = valid_results                    # ← {"Scenario": Marathi output}

    enriched = _strip_internal_cite_markers(drafting_result.content)
    if citations_text.strip():
        enriched += "\n\n---\n\n## REFERENCES & CITATIONS\n" + citations_text
```

Turn 2 asked "Convert above text into marathi." The synthesizer sees
`{"Drafting": <turn 1 English draft>, "Scenario": <fresh Marathi arguments>}`.
It picks Drafting as the primary because Drafting is present, keeps the
English body verbatim (`_strip_internal_cite_markers` doesn't touch
language), and appends Scenario's Marathi output as an appendix.

**Net result**: mixed-language output. Not a bug in `localize_prompt`, not
a bug in the intent extractor. A cross-turn state-leak bug that the
language layer never gets a chance to fix.

**Blast radius**: every follow-up turn that runs *different agents than
the prior turn* will have this failure mode. If the prior turn ran
Drafting and the current turn runs Judgment, the synthesizer will see
BOTH. If prior ran Legislation and current runs Scenario, same. The user
sees stale prior-turn content leaking into every same-thread follow-up
where the agent selection changed.

---

## 🟡 Break #3 — Rewriter directive-verb regex is Latin-anchored and verb-narrow

**Location**: [`agents/drafting.py:177-210`](../../agents/drafting.py#L177-L210)
(the `_DIRECTIVE_VERBS_RE` regex is READ by the memory rewriter's fast-path
skip at [`agents/memory.py:187-199`](../../agents/memory.py#L187))

```python
r"\bin\s+(marathi|hindi|english|tamil|telugu|kannada|malayalam|bengali|"
r"punjabi|gujarati|urdu|odia|assamese|sanskrit)|"
r"translate\s+to|translate\s+into|"
```

**What breaks**: the regex expects `\bin\s+<lang>` (word-boundary "in"
followed by whitespace) OR `translate to/into`. The user typed
`"Convert above text into marathi"`.

Two misses in a row:
1. `\bin\s+marathi` doesn't match `into marathi` — `into` starts with
   `in` but there's no word boundary between `in` and `to` (it's a single
   word). So the regex sees "into marathi", not "in marathi".
2. The verb list has `translate to|translate into` but not `convert`.

**Why it's logically broken, not a gap**: the regex is intended to catch
"the user typed a directive follow-up in any language" but it's
Latin-only, misses common English synonyms (`convert`, `render`,
`rewrite in`), and its word-boundary handling drops the most-common
preposition form (`into`).

Result: on the user's exact query, the fast-path skip never fires. The
REWRITE_PROMPT LLM then rewrites the query, and (see Break #4) rewrites
it wrong. **Then**, even if the regex HAD matched, the fast-path skip is
gated behind `DRAFTING_FOLLOWUP_FAST_PATH=1` (env default OFF) — so on
current prod config, the skip cannot fire at all.

**Blast radius**: every language-switch follow-up in English natural
phrasing ("convert this to Hindi", "give me this in Tamil", "rewrite in
Marathi", "in Marathi please") that doesn't hit the narrow verb list.
Plus every install where `DRAFTING_FOLLOWUP_FAST_PATH=1` isn't set.

---

## 🟡 Break #4 — REWRITE_PROMPT picks the wrong "above text" on multi-language history

**Location**: [`agents/memory.py:71-137`](../../agents/memory.py#L71-L137) (REWRITE_PROMPT)

**What breaks**: when the user's follow-up is "Convert above text into
marathi" and turn 1's history contains BOTH the assistant's English
partition-suit response AND a Marathi paragraph embedded in the user's
original prompt, the LLM has no explicit rule for what "above text"
refers to.

**Turn 2 evidence** (server.log:106):
```
Query rewritten
  original="Convert above text into marathi"
  rewritten="माझ्या क्लायंट, श्रीमती अंजली देशमुख, वय ४२, पुण्यातील रहिवासी,
             यांची १२ मार्च २०२४ रोजी XYZ मल्टीस्पेशालिटी..."
             ↑ this is the Marathi paragraph from turn 1's USER PROMPT,
               NOT the assistant's turn 1 response.
```

The LLM read the Marathi paragraph in the user's turn 1 prompt and
concluded "the user wants that translated" — but the user's intent was
almost certainly "translate the DRAFT you just gave me."

**Why it's logically broken, not a gap**: the REWRITE_PROMPT's examples
show language-switch directives (`"in marathi"` → full task reconstruction),
but the reconstruction is grounded in the ORIGINAL TASK from the user's
prior prompt. It has no notion of "translate the assistant's prior
response". The rewriter is designed to produce a fresh SEARCH query,
not a "polish the assistant's last message" instruction.

This is compounded by Break #3: had the fast-path skip fired, the raw
query "Convert above text into marathi" would have flowed into the
downstream drafting fast-path, which does understand "modify the prior
draft" — but the fast-path is disabled by default.

**Prod comparison**: prod's rewriter was cleaner (`वरील मजकूर मराठीत
रूपांतरित करा` — literal translation of the directive). Same underlying
prompt template — the LLM just made a better call. But the DOWNSTREAM
classifier still saw a directive-shaped query and routed to Scenario
(fresh fact analysis), producing the same mixed-output failure.

**Blast radius**: every language-switch on a thread where the user's
prior prompt contained embedded content in a language other than
English. Prod-side less severe (better rewrites) but still fails the
user's actual intent (translate the assistant's last message).

---

## 🟢 Break #5 — Silent intent-extractor `AttributeError` (already diagnosed)

**Location**: [`agents/orchestrator.py:604-611`](../../agents/orchestrator.py#L604-L611)

**What breaks**: `raw_and_parsed["parsed"]` can be `None` when the LLM's
output can't be coerced into `QueryAnalysisV2`. The next line calls
`.intent.response_format.value` on `None` → `AttributeError`, which the
outer `except` catches and logs as a misleading `NoneType.intent`
message that hides the real `parsing_error` LangChain captured.

**Turn 1 evidence** (server.log:61):
```
Intent extraction failed; falling back to default_intent()
  error=AttributeError: 'NoneType' object has no attribute 'intent'
                       ^^^^^^^^^^^ the log tells us nothing about WHY
```

**Why it's logically broken, not a gap**: `include_raw=True` in
`with_structured_output(...)` documents that `parsed=None +
parsing_error=Exception` is a normal outcome — the code must handle it.
It doesn't. So we lose the diagnostic that would tell us WHY parsing
failed (LLM emitted non-JSON, Pydantic validation drift, tool-call
truncation, safety-filter strip, ...).

**Blast radius**: every intent extraction that fails to parse. Silent
degradation to `default_intent()` breaks language override, party voice,
response format, depth, and the self-refine grounding — all invisibly.
Repro rate on the mega-prompt: 1 in ~10 runs (I got 0 in 5 attempts on
the retry).

---

## 🟡 Break #6 — `_validate_and_enrich_plan` is asymmetric — enriches but doesn't correct

**Location**: [`agents/orchestrator.py:842-881`](../../agents/orchestrator.py#L842-L881)

**What breaks**: the validator (a) vetoes Legislation when Newacts is
present, (b) adds SCI_Judgment when `intent.wants_supreme_court`,
(c) adds GST_Judgment when `intent.wants_gst_rulings`. But it never
adds `Scenario` even when `intent.wants_scenario_analysis=True`, never
adds `Constitution` for `intent.wants_constitution=True`, never adds
`Maxim` for `intent.wants_maxim=True`.

**Why it's logically broken, not a gap**: the typed UserIntent has 6
`wants_*` fields; the validator honors 2 of them. If the LLM classifier
misses a required agent, only 2 of the 6 typed signals can pull it back
in. The other 4 (`wants_scenario_analysis`, `wants_constitution`,
`wants_maxim`, `wants_statute_text`) exist in the schema and get
populated by the extractor but no code reads them for plan enrichment.

**Turn 1 evidence** — extraction succeeded on retry with:
```
wants_scenario_analysis=True,   ← never added Scenario
wants_statute_text=True,         ← never added Newacts/Legislation
named_acts=[CrPC, IEA, IPC, CPA] ← never influenced routing
```

If either turn 1 mega-prompt agent-list had been dropped (which it was,
by Break #1) and the LLM classifier had also missed one of these, the
validator would have had no way to recover.

**Blast radius**: intent extraction populates signals that the validator
ignores. Silent inconsistency between "what the intent layer knows the
user wants" and "what agents the plan actually runs."

---

## Summary — logic breaks by severity

| # | Break | Severity | Fix complexity |
|---|-------|----------|----------------|
| 1 | Non-drafting agents stripped when `cite_appendix=off` | **Critical** — silently kills multi-intent | Small — change filter to set-difference |
| 2 | `agent_results` reducer leaks turn N-1 into turn N | **Critical** — mixes stale content into every follow-up synth | Small — clear or timestamp per turn |
| 3 | Directive regex misses "into" + "convert" + off by default | Major | Small — expand regex + turn flag on |
| 4 | REWRITE_PROMPT picks wrong "above text" | Major | Medium — add explicit "assistant response is the target of translation directives" rule |
| 5 | Intent extractor `None`-check missing | Major (silent) | Trivial — 4-line defensive fix |
| 6 | Plan validator only reads 2 of 6 `wants_*` intent fields | Moderate | Small — add branches for the other 4 |

**The two that actually broke this test** are Breaks #1 and #2.
Both are one-line filter mistakes that produced multi-hundred-line
downstream failures. Fixing them independently is easy; the tricky part
is that they interact — Break #2 keeps turn 1's Drafting result alive
in state, and Break #1 caused turn 1 to be Drafting-only in the first
place. Fix #1 and the Break #2 leak still happens on any turn boundary
where the plan changes. Fix #2 and the multi-intent drop still happens.
Both need fixing.

**The confusion isn't in the language layer.** The multilingual
pipeline analysis we wrote yesterday is correct — every listed layer
is doing what the doc says. The failures are in the ORCHESTRATION and
STATE MANAGEMENT layers upstream of the language work. That's actually
good news: fixes are localised to two files (`orchestrator.py`,
`state.py`), don't touch prompts, don't touch `core/language.py`, and
don't require re-verifying the 14-language directive tables.