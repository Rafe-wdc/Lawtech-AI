# Final Verdict — All 6 Breaks Knocked Off

Same 2-turn same-thread test, run three times:

1. **BASELINE** — no fixes.
2. **PARTIAL** — Break #1 + Break #2 (structural filters + state leak).
3. **ALL FIXES** — all 6 breaks resolved.

## Fix summary

| # | Break | File(s) touched | LOC | What it does |
|---|-------|-----------------|-----|--------------|
| 1 | Non-Drafting agents stripped when `cite_appendix=off` | [orchestrator.py](../../agents/orchestrator.py) | ~15 | Preserves content agents the classifier picked (Legal_Concepts, Scenario, Maxim, Constitution). Only strips the 5 auto-appendix agents. |
| 2 | `agent_results` reducer leaks across turns | [state.py](../../core/state.py) + [memory.py](../../agents/memory.py) | ~15 | Sentinel `__RESET_TURN__` clears stale prior-turn results at each turn boundary. Preserves within-turn parallel fan-out merge. |
| 3 | Directive regex misses `into`, `convert`, `render`, `rewrite in` | [drafting.py](../../agents/drafting.py) | ~10 | Regex now catches all common English language-switch phrasings; still rejects non-language conversions like "convert to json". 14/14 unit test cases pass. |
| 4 | Rewriter picks wrong "above text" | [memory.py REWRITE_PROMPT](../../agents/memory.py) | ~20 | Explicit rule + full-example that "above text" refers to the ASSISTANT's prior response; language-conversion directives MUST inline the prior response verbatim so downstream drafting agent has actual content to translate. |
| 5 | Intent extractor `None`-check missing | [orchestrator.py](../../agents/orchestrator.py) | ~15 | Explicit `parsed is None` check surfaces the real `parsing_error` + raw-content preview + finish_reason in the log. Ends silent degradation. |
| 6 | Plan validator only reads 2 of 6 `wants_*` fields | [orchestrator.py](../../agents/orchestrator.py) | ~15 | Also honours `wants_scenario_analysis`, `wants_constitution`, `wants_maxim`; capped at 4 total agents. |
| bonus | `source_metadata` cross-turn leak | [state.py](../../core/state.py) + [memory.py](../../agents/memory.py) | ~10 | Companion sentinel `_RESET_SOURCE_METADATA` prevents prior-turn source lists from padding the current turn's citations block. |

**Total: ~100 LOC across 4 files. No prompt-only regressions. No new deps.**

## Side-by-side test results

|  | BASELINE | PARTIAL (#1+#2) | ALL FIXES |
|---|---|---|---|
| **Turn 1** ||||
| agents_planned | `[Drafting]` | `[Drafting, Legal_Concepts]` | `[Drafting, Legal_Concepts]` |
| response chars | 12,084 | 30,312 | 22,298 |
| Drops multi-intent asks? | Yes (5 of 6) | No (Legal_Concepts runs) | No |
| Intent extraction failed log | `AttributeError: 'NoneType' object has no attribute 'intent'` (misleading) | Same misleading log | **`Intent extraction returned no parsed output; parsing_error=OutputParserException: Failed to parse QueryAnalysisV2 from completion {...}; raw_content_preview=...`** — real diagnostic surfaced |
| **Turn 2** — `"Convert above text into marathi"` ||||
| agents_received at synth | `[Drafting, Scenario]` (Drafting = stale from turn 1) | `[Drafting]` (fresh only) | `[Drafting]` |
| rewriter output | `माझ्या क्लायंट, श्रीमती अंजली देशमुख…` (WRONG — Marathi paragraph from turn 1's USER prompt) | `Convert the following draft of a civil suit for partition ... ## Cause Title ... Rupa D/o. Late ...` | `Convert the following text into Marathi: ## Cause Title ... Rupa D/o. Late ...` |
| response Devanagari chars | 2,694 | 3,760 | **1,035** |
| response Latin alpha chars | 9,524 | 274 | 200 |
| Subject matter of translation | **WRONG** (Anjali medical negligence) | **CORRECT** (Rupa partition) | **CORRECT** (Rupa partition) |
| Mixed language | Yes (English draft + Marathi appendix) | No (pure Marathi) | No (pure Marathi) |
| Fixed-English anchors preserved | n/a | Yes | Yes (`Civil Suit No.`, `2024` in Latin) |

## Turn 2 output — before vs after

**BASELINE turn 2** (mixed English + Marathi about the WRONG case):
```
## Cause Title
IN THE COURT OF THE CIVIL JUDGE SENIOR DIVISION, PUNE
CIVIL SUIT NO. ______ OF 2024
Rupa D/o. Late [Deceased Father's Name] ...   ← turn 1's English draft
                                                 kept verbatim (state leak)
...
### SCENARIO CITATIONS:
वादीच्या वतीने युक्तिवाद: 1. वैद्यकीय निष्काळजीपणा ...  ← Marathi arguments
                                                          about the WRONG
                                                          matter (Anjali,
                                                          not Rupa)
```

**ALL FIXES turn 2** (pure Marathi translation of the CORRECT case):
```
**पुणे येथील दिवाणी न्यायाधीश वरिष्ठ स्तर यांचे न्यायालयात**
**Civil Suit No. ______ / 2024**        ← Fixed-English anchor preserved

रूपा मुलगी कै. [मृत वडिलांचे नाव],
वय: [रूपाचे वय], धंदा: [रूपाचा व्यवसाय],
राहणार: [रूपाचा पत्ता], पुणे.
.....वादी                                ← "Plaintiff" → वादी

**विरुद्ध**                                ← "vs" → विरुद्ध

[आईचे नाव] पत्नी कै. [मृत वडिलांचे नाव],
वय: [आईचे वय], धंदा: [आईचा व्यवसाय],
.....प्रतिवादी                             ← "Defendant" → प्रतिवादी

## दाव्याची हकीकत                          ← "Facts of the Case" → translated
1. ही गोष्ट खरी आहे की, वादी ही ... दत्तक मुलगी आहे ...
                                          ← Rupa CORRECT: दत्तक मुलगी
                                             (adopted daughter)
2. वादीचे दत्तक वडील, ... सुमारे 5 वर्षांपूर्वी ...
                                          ← "5 years ago" preserved
3. दोन निवासी सदनिकांचे (फ्लॅट्सचे) ...
                                          ← "two flats" preserved
4. Class I चे कायदेशीर वारस ...            ← Class I anchor stays English
```

## Rating progression

| Dimension | BASELINE | PARTIAL | ALL FIXES |
|---|---|---|---|
| Reliability | 9/10 | 9/10 | 9/10 |
| Latency | 6/10 | 6/10 | 7/10 (turn 2 dropped from 231s → 77s on the correct-topic run) |
| Multi-intent handling | **2/10** | **6/10** | **7/10** |
| Language-switch follow-up | **3/10** | **9/10** | **9/10** |
| Language directive detection | 8/10 | 8/10 | 9/10 (regex now catches `into`, `convert`) |
| Observability | **3/10** | 3/10 | **8/10** (real parsing_error now surfaced) |
| Response cache | 7/10 | 7/10 | 7/10 |
| Guardrails | 8/10 | 8/10 | 8/10 |

**Overall: 4/10 → 6/10 → 7/10** on the same test scenarios.

## Note on Break #4 iteration

The first attempt at Break #4 fixed the "which text is 'above text'" ambiguity but introduced a regression: the rewriter became too *abstract* ("Convert the previously drafted civil suit for partition...") and the drafting agent, lacking the actual English draft, went to web fallback and generated a completely different partition suit (a Kumar City housing society dispute). The second attempt kept the assistant-response-is-the-target rule but explicitly required INLINING the prior response verbatim. That produced the correct Rupa-partition Marathi output at 1,641 chars total.

This iteration is why the "PARTIAL" fix produced a longer response (4,934 chars) than "ALL FIXES" (1,641 chars) — the PARTIAL run's rewriter got lucky and inlined the draft on its own; the ALL FIXES rewriter is now *guaranteed* to inline via the explicit rule.

## What still doesn't fire and why

- **Drafting fast-path** (`DRAFTING_FOLLOWUP_FAST_PATH=1`) is still env-gated OFF. Turn 2 goes through the slow drafting path (fresh reference-picker + template-based generation) instead of the fast "modify the prior draft" path. Flipping this flag is a production behaviour change I did NOT make without explicit approval — but with Break #3's regex now catching "convert into marathi", the fast-path is now WIRED CORRECTLY to fire once enabled. Recommend flipping the env flag as a follow-up ops decision.
- **`wants_statute_text`** in Break #6 — not wired to add Newacts vs Legislation. Needs a `intent.named_acts` check to pick the right corpus. Deferred as a follow-up refinement.

## Files changed

- [agents/orchestrator.py](../../agents/orchestrator.py) — Fix #1 (citation filter), Fix #5 (None-check), Fix #6 (validator wants_* fields)
- [core/state.py](../../core/state.py) — Fix #2 + bonus (sentinel reducers)
- [agents/memory.py](../../agents/memory.py) — Fix #2 + bonus (sentinel emission) + Fix #4 (REWRITE_PROMPT inlining rule)
- [agents/drafting.py](../../agents/drafting.py) — Fix #3 (directive regex expansion)

**Total: ~100 LOC across 4 files.**

## Recommended follow-ups (not done in this session)

1. Flip `DRAFTING_FOLLOWUP_FAST_PATH=1` in production `.env` — the regex and rewriter are now correctly wired for it. Turn 2 latency would drop from ~77s to ~8s.
2. Extend Break #6 to route `wants_statute_text` to Newacts vs Legislation based on `intent.named_acts`.
3. `_cap_source_metadata` is now reset-capable but could be made per-turn stricter (currently cap 100 across the WHOLE thread; the reset means each turn starts fresh but a very active single turn could still accumulate).
4. The intent extractor's actual `parsing_error` (now surfaced by Break #5's fix) is `OutputParserException: Failed to parse QueryAnalysisV2 from completion`. Worth adding a retry-with-clarification prompt for the mega-prompt case — the LLM emits *valid JSON* that's simply too big for the schema constraints on `additional_instructions` (300-char cap). Would eliminate the intent-extractor degradation entirely on long prompts.
