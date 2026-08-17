# Before / After — the 2 pipeline fixes

Same 2-turn test, same thread pattern, same input file. Local server, in-memory
checkpointer, no client language hint. Two code changes:

| # | File | Change |
|---|------|--------|
| 1 | [agents/orchestrator.py:1382-1400](../../agents/orchestrator.py#L1382) | Preserve non-citation content agents when `cite_appendix=off` (was stripping to `[Drafting\|Document]`) |
| 2 | [core/state.py:88-108](../../core/state.py#L88) + [agents/memory.py:558-576](../../agents/memory.py#L558) | Sentinel-based reset of `agent_results` at each turn boundary |

## Side-by-side signals

| Signal | BEFORE fix | AFTER fix | Change |
|--------|-----------|----------|--------|
| **Turn 1 `agents_planned`** | `['Drafting']` | `['Drafting', 'Legal_Concepts']` | Fix #1 preserved Legal_Concepts (consumer scenario handler) |
| **Turn 1 `parallel_count`** | 1 | 2 | Legal_Concepts fanned out in parallel with Drafting |
| **Turn 1 response chars** | 12,084 | **30,312** | +150% content — now covers the consumer-law ask the old pipeline silently dropped |
| **Turn 1 elapsed** | 116 s | 127 s | +11 s for the parallel Legal_Concepts run (parallel, not sequential) |
| **Turn 2 `agents_received`** | `['Drafting', 'Scenario']` | `['Drafting']` | Fix #2 stripped stale turn-1 Drafting from state; only fresh agents surface |
| **Turn 2 `registry_size`** | 2 | 1 | Confirms the state leak is closed |
| **Turn 2 effective query** (rewriter output) | `माझ्या क्लायंट, श्रीमती अंजली देशमुख…` (grabbed Marathi paragraph from turn-1 USER prompt) | `Convert the following draft of a civil suit for partition, declaration, and permanent and temporary [injunction]...` (grabbed turn-1 ASSISTANT draft — the correct target) | Rewriter now picks the right "above text" |
| **Turn 2 response Devanagari chars** | 2,694 | **3,760** | +40% |
| **Turn 2 response Latin alpha chars** | 9,524 | **274** | -97% |
| **Turn 2 response chars** | 15,420 | 5,164 | Shorter because there's no stale English draft glued on top |
| **Turn 2 output shape** | English partition draft + Marathi appendix (Scenario's fresh med-negligence arguments) | Pure Marathi translation of turn-1 partition draft | ✅ Matches user intent |

## Turn 2 output preview (after fix)

```
## न्यायालयीन शीर्षक

पुणे येथील दिवाणी न्यायाधीश वरिष्ठ स्तर यांच्या न्यायालयात

दिवाणी दावा क्र. ______ / 2024

रूपा
मुलगी कै. [मृत वडिलांचे नाव]
वय: [वादीचे वय], व्यवसाय: [वादीचा व्यवसाय]
राहणार: [वादीचा पत्ता], पुणे.

.....वादी
...
```

Fixed-English anchors correctly preserved inline (numerals `2024`, Latin
`Cause Title` → `न्यायालयीन शीर्षक` translated, party role labels correctly
localised to `वादी`).

## What happened under the hood on turn 2 (after fix)

```
20:14:07.661  [Memory]        Agent started        query="Convert above text into marathi"
20:14:07.667  [Memory]        Language detected    lang=en  (short English directive — correct)
                              ← memory_node emits {"agent_results":{"__RESET_TURN__":True}}
                                → reducer discards turn-1 state, state["agent_results"] = {}

20:14:09.376  [Memory]        Query rewrite (LLM)  1.7s
20:14:09.380  [Memory]        Query rewritten
                              original="Convert above text into marathi"
                              rewritten="Convert the following draft of a civil suit for
                                partition, declaration, and permanent and temporary
                                injunction into Marathi:  ## Cause Title  IN THE COURT
                                OF THE CIVIL JUDGE SENIOR DIVISION, PUNE  CIVIL SUIT
                                NO. ______ OF 2024  Rupa D/o. Late [Deceased ..."
                              ✓ Rewriter correctly identified the ASSISTANT'S turn-1
                                draft as the "above text" (previously it grabbed the
                                Marathi paragraph from the user's turn-1 prompt).

20:14:11.407  [Orchestrator]  Plan phase completed   agents_planned=['Drafting']
                              ← Classifier saw a Drafting directive with attached
                                draft content — correctly routed to Drafting only.

20:14:11.410  [Drafting]      Agent started
20:16:36.385  [Drafting]      Agent completed        draft_len=4934
                              ← Drafting picked reference:
                                "A Suit for Partition and Permanent Injunction.csv"
                                and produced a Marathi translation of the prior draft.

20:16:36.387  [Orchestrator]  Synthesize phase started
                              agents_received=['Drafting']  ← ONLY Drafting, no stale
                              registry_size=1                Scenario/anything from turn 1

20:16:36.388  [Orchestrator]  Draft synthesis completed
                              enriched_len=4934             ← pure Marathi draft, no
                              citation_agents=[]              English appendix leak
```

## Final rating — after fixes

| Dimension | Before | After | Δ |
|---|---|---|---|
| Reliability | 9/10 | 9/10 | — |
| Latency | 6/10 | 6/10 | +11s on T1 (parallel Legal_Concepts) but user gets 2.5× the content |
| Multi-intent handling | **2/10** | **6/10** | Legal_Concepts now runs; Legislation still stripped (Break #1 followup — needs `intent.named_acts` check) |
| Language-switch follow-up | **3/10** | **9/10** | Pure Marathi output matching user intent |
| Language directive detection | 8/10 | 8/10 | Unchanged (was already correct) |
| Observability | 3/10 | 3/10 | Break #5 (intent-extractor None-check) not fixed — still logs `AttributeError` on turn 1 |
| Response cache | 7/10 | 7/10 | — |
| Guardrails | 8/10 | 8/10 | — |

**Overall: 4/10 → 7/10** on the same test scenarios.

## What's still broken (breaks we didn't touch)

1. **Break #3** — directive regex misses "into marathi" + "convert". Not fixed;
   didn't matter this run because Break #2's fix let the REWRITE_PROMPT LLM
   do the right thing anyway. But regex is still narrow.
2. **Break #4** — REWRITE_PROMPT has no explicit "assistant response is the
   translation target" rule. Worked correctly this run (LLM luck?), may
   flip on other prompts. Still worth adding an example to the prompt.
3. **Break #5** — intent-extractor `None`-check missing. Confirmed still
   fires on turn 1 (`Intent extraction failed; falling back to
   default_intent()` at 21:12:14). Silent degradation — the whole intent
   layer's signal was lost on turn 1 both before AND after these fixes.
4. **Break #6** — plan validator only reads 2/6 `wants_*` fields. Not
   fixed; the intent-extractor failure on turn 1 made this moot for this
   run.
5. **`source_metadata` cross-turn leak** — same reducer pattern as
   `agent_results` (bounded by 100-cap so lower blast radius). Deferred.

## Files changed

- [agents/orchestrator.py](../../agents/orchestrator.py) — added
  `_CITATION_APPENDIX_AGENTS` constant, rewrote the `cite_appendix=off`
  branch to preserve content agents.
- [core/state.py](../../core/state.py) — added `_RESET_AGENT_RESULTS`
  sentinel; `_merge_agent_results` now handles it as reset.
- [agents/memory.py](../../agents/memory.py) — imports the sentinel and
  emits `{"agent_results": {"__RESET_TURN__": True}}` at the end of every
  memory node invocation, resetting stale prior-turn results.

Total: **~30 lines of code changed across 3 files.** No prompt changes.
No new dependencies. No agent changes. No test suite changes.
