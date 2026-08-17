# Flow Analysis — `test prompt.txt` (Local Run)

Step-by-step walk-through of what the LangGraph pipeline actually did for
each of the two turns, grounded in the [server.log](server.log) captured
during the local run.

**Endpoint**: `http://localhost:5000/pyapi/search`
**Thread ID**: `2a6a8314-5c1d-4e6f-896b-76b9fe96fc96`
**Payload language field**: NOT SET — the pipeline auto-detects.

---

## Turn 1 — the mega-prompt

**Sent**: 4,731 chars — verbatim body of `prompt 1:` in `test prompt.txt`.
The prompt is dominantly English (a Section 125 CrPC label + 2 drafting
scenarios + 3 citation asks + English argument-generation ask +
Marathi argument-generation ask + Section 420 IPC ask + Consumer Laws
scenario + "Pdf: Review this draft").

**Response**: HTTP 200, **116.1 s**, 12,084 chars, agents=`[Drafting]`,
**0 Devanagari chars**, 9,443 Latin alpha chars — pure English draft.

### Flow trace

```
20:02:40.814  [Guardrail]     Input passthrough
20:02:40.819  [Memory]        Agent started (thread_id=2a6a8314...)
20:02:42.753  [Memory]        Language detected  lang=en
                              ^ langdetect returned "en" because the
                                Marathi paragraph is one block inside a
                                dominantly-Latin 4.7 kB body.
20:02:42.761  [Memory]        Abbreviations expanded  CrPC → Code of
                              Criminal Procedure 1973
20:02:42.777  [Memory]        SQLite history load  (placeholder_history=
                              True, turns=0) — fresh thread, no rewrite.
20:02:42.789  [Memory]        Agent completed
20:02:42.795  [Orchestrator]  Plan phase started  query_len=4777
20:02:42.796  [Orchestrator]  Skipping normalization (English, short query)
20:02:46.354  [Orchestrator]  Classify + plan (merged)  duration_ms=3557
                              task=Drafting
                              agents=[Drafting, Legal_Concepts, Legislation,
                                      Maxim]   <-- LLM initially planned 4
                              reasoning="user asking to draft a civil suit
                                for partition and a civil suit for damages"
20:02:51.272  [Orchestrator]  Intent extraction (v2)  duration_ms=8476
20:02:51.273  [Orchestrator]  Intent extraction FAILED
                              error=AttributeError: 'NoneType' object has
                                    no attribute 'intent'
                              -> fell back to default_intent()  ⚠️  BUG
20:02:51.274  [Orchestrator]  Drafting citation appendix skipped
                              source=env_default
20:02:51.274  [Orchestrator]  Plan phase completed
                              agents_planned=[Drafting]   <-- dropped from 4
                              agent_count=1
                              agent_queries_generated=0
20:02:51.275  [Graph]         Fan-out routing  nodes=[drafting]
20:02:51.283  [Drafting]      Agent started
20:02:51.470  [Drafting]      ES match for reference candidates  184 ms
20:02:54.543  [Drafting]      Reference picker  1391 ms
20:02:54.544  [Drafting]      Picker chose
                              /content/formats_and_all_drafts/
                              SUIT FOR PARTITION AND SEPARATE POSSESSION.csv
                              (based on the FIRST scenario — the partition
                              suit — the picker ignored the 5 other asks
                              in the mega-prompt)
20:02:54.588  [Drafting]      Reference draft acquired from corpus  9041 chars
20:02:57.254  [Drafting]      Context gather  blocks=[newacts, legislation,
                                                       judgments, sci]
                              total_chars=5981
20:03:02.311  [Drafting]      Fan-out judge  should_fanout=True  sections=9
20:03:19.341  [Drafting]      Section pair 1-2  15.6 s   (1086 chars)
20:03:41.348  [Drafting]      Section pair 3-4  22.0 s   (4021 chars)
20:03:59.745  [Drafting]      Section pair 5-6  18.4 s   (3802 chars)
20:04:24.764  [Drafting]      Section pair 7-8  25.0 s   (2600 chars)
20:04:32.154  [Drafting]      Section pair 9    7.4 s    ( 380 chars)
20:04:32.156  [Drafting]      Agent completed  draft_len=11897
20:04:32.158  [Orchestrator]  Synthesize phase started  agents=[Drafting]
20:04:32.160  [Orchestrator]  Drafting solo - passing through unmodified
                              (BUG-03 fix)  draft_len=11897 -> 11854 after
                              stripping [CITE: ...] markers
20:04:32.170  [Guardrail]     Output sanitization  11854 -> 12084 chars
                              (disclaimer appended)
```

### What the pipeline decided

| Stage | Decision | Why |
|---|---|---|
| Language | `en` | langdetect saw a Latin-dominant 4.7 kB body; the embedded Marathi paragraph didn't tip the balance. Correct signal — the user's overarching request is English. |
| Task | `Drafting` | The prompt starts with two "Draft a civil suit …" scenarios. LLM classifier picked this over Citations/Scenario. |
| Reference draft | `SUIT FOR PARTITION AND SEPARATE POSSESSION.csv` | Reference picker matched the FIRST scenario in the mega-prompt (partition suit for Rupa) and ignored the other 5 asks. |
| Fan-out | Section-wise, 9 sections | Cause title / Parties / Facts / Property Description / Cause of Action / Grounds / Prayer / Verification / Affidavit. |
| Synthesis | Solo pass-through | Only one agent completed; no LLM merge (BUG-03 fix bypasses `_auto_cite_draft`). |

### What the pipeline missed on turn 1

1. **Intent extraction failed** with `AttributeError` — pipeline silently
   fell back to `default_intent()`. This means:
   - `intent.language` = defaulted to `"en"` (fine, matches langdetect)
   - `intent.language_explicit` = False
   - `intent.arguments_for_party` = default "none" — the sub-prompt
     "give me arguments on behalf of plaintiff and defendant" was NEVER
     surfaced to the drafting agent.
   - `intent.wants_table`, `include_case_law`, `strict_language` — all
     lost.
   This is worth investigating: the intent-extractor bug on long multi-
   scenario prompts appears reproducible.
2. **5 of the 6 asks in the mega-prompt were dropped** — the pipeline
   produced only the partition suit. It ignored:
   - the second drafting scenario (Anjali medical negligence)
   - the 3 citation asks (adopted daughter, Section 65B, anticipatory bail)
   - the arguments generation for plaintiff/defendant
   - the Section 420 IPC defense arguments
   - the Consumer Protection Act scenario
   - the "Review this draft" (which has no PDF anyway)

   The classifier picked ONE task (`Drafting`) and one reference file,
   then the drafting agent produced ONE document.
3. **No self-refine loop ran**. The trace shows no `[SelfRefine]` entries.
   Solo drafting bypasses the critic when there's no synthesis merge.

---

## Turn 2 — `"Convert above text into marathi"`

**Sent**: 31 chars, same thread.

**Response**: HTTP 200, **16.4 s**, 15,420 chars, agents=`[Drafting, Scenario]`,
**2,694 Devanagari** + 9,524 Latin alpha — MIXED language.

### Flow trace

```
20:04:34.873  [Guardrail]     Input passthrough  query_len=31
20:04:34.875  [Memory]        Agent started  query="Convert above text
                                                    into marathi"
20:04:34.881  [Memory]        Language detected  lang=en
                              ^ 31 chars of English — langdetect correctly
                                calls the DIRECTIVE English. The intent
                                extractor is expected to catch the "into
                                marathi" clause later.
20:04:34.888  [Memory]        Chat history loaded from SQLite
                              turns=1, has_prev_intent=True,
                              prev_task=Drafting, prev_artifact_kind=draft
20:04:36.482  [Memory]        Query rewrite (LLM)  duration_ms=1582
20:04:36.484  [Memory]        Query rewritten
                              original="Convert above text into marathi"
                              rewritten="माझ्या क्लायंट, श्रीमती अंजली
                                देशमुख, वय ४२, पुण्यातील रहिवासी, यांची १२
                                मार्च २०२४ रोजी XYZ मल्टीस्पेशालिटी
                                हॉस्पिटलमध्ये पित्ताशयाची शस्त्रक्रिया झाली…"
                              ⚠️  The rewriter did NOT translate the
                              DIRECTIVE. It grabbed the Marathi paragraph
                              embedded in turn 1's prompt and made THAT
                              the new query. See "Root cause" below.
20:04:36.485  [Memory]        Agent completed  query_changed=True
20:04:36.487  [Orchestrator]  Plan phase started  query_len=764
                              (the Marathi rewrite)
20:04:37.758  [Orchestrator]  Classify + plan  duration_ms=1253
                              task=Scenario   <-- NOT Drafting !!
                              agents=[Scenario]
                              reasoning="user describing a specific fact
                                pattern involving medical negligence and
                                seeking arguments for compensation"
20:04:38.424  [Orchestrator]  Intent extraction (v2)  duration_ms=1936
20:04:38.424  [Orchestrator]  User intent extracted
                              format=prose, format_explicit=False,
                              language=mr, language_explicit=True,
                              depth=standard, confidence=0.9
                              ✓ Intent CORRECTLY caught language=mr from
                              the rewritten Marathi query.
20:04:38.426  [Graph]         Fan-out routing  nodes=[scenario]
20:04:38.429  [Scenario]      Agent started  has_history=True
20:04:50.145  [Scenario]      Flash + Google Search  11.7 s
20:04:50.146  [Scenario]      Agent completed  response_len=3277
                              tokens=11472  web_sources=1
20:04:50.147  [Orchestrator]  Synthesize phase started
                              agents_received=[Drafting, Scenario]
                              registry_size=2
                              ^ Registry has TWO entries — Drafting from
                                turn 1 (persisted across turns) + Scenario
                                from this turn.
20:04:50.148  [Orchestrator]  Draft-aware synthesis starting
                              draft_len=11897  <-- turn 1's English draft
                              citation_agents=[Scenario]
20:04:50.149  [Orchestrator]  Draft synthesis completed (append-only)
                              enriched_len=15190  citation_agents=[Scenario]
                              total_tokens=11472
                              ^ APPEND-ONLY: keeps turn 1's English draft
                                verbatim, appends Scenario's Marathi output
                                as a citations block. Explains the mixed
                                output.
20:04:50.160  [Guardrail]     Output sanitization  15190 -> 15420 chars
                              (disclaimer appended)
```

### Root cause of the mixed-language response

Three chained effects produced the mixed output:

1. **Rewriter mis-fired**. The `_rewrite_query` LLM sees the follow-up
   directive "Convert above text into marathi" plus turn 1's history.
   It's supposed to produce a SEARCH-optimised standalone query. Instead
   it interpreted the directive as "translate the Marathi passage in the
   previous prompt" and returned that Marathi paragraph verbatim (the
   Anjali medical negligence text). This is the language-switch branch
   overshooting — it grabbed the wrong text as the "content to translate."

   This looks like the failure mode fixed by [project_language_switch_followup_plan.md](../../docs/language_switch_followup_plan.md) (shipped 2026-06-30) but re-surfacing on
   long multi-paragraph histories where the rewriter picks the wrong
   paragraph to promote.

2. **Classifier re-routed to Scenario**. Once the query became "…Anjali
   Deshmukh's medical negligence facts, give me arguments…", the LLM
   classifier saw a fact-pattern with an arguments ask and picked
   `Scenario`, not `Drafting`. Scenario has web-grounded fact-pattern
   analysis in its remit.

3. **Draft-aware synthesis kept turn 1's English draft**. The registry
   still holds turn 1's `Drafting` result (`draft_len=11897`). The synth
   layer at `_draft_aware_synthesis` combines the persisted draft +
   this turn's citation agents (Scenario). Because it's `append-only`,
   the English draft body is preserved verbatim and Scenario's Marathi
   analysis is glued onto the end.

**Net effect**: the user asked "translate the above to Marathi", and the
system delivered "turn 1's original English draft + a fresh Marathi
argument passage about medical negligence." Not a translation.

### What the pipeline got RIGHT on turn 2

- **Intent extractor caught `language_explicit=True, language=mr,
  confidence=0.9`** — the multilingual pipeline analysis (see
  [docs/multilingual_pipeline_analysis.md](../../docs/multilingual_pipeline_analysis.md)) says this should
  override `state["user_language"]` and localize every downstream prompt
  to Marathi. That override DID happen — the Scenario agent's output is
  correctly in Marathi.
- **Scenario agent used `localize_prompt` correctly** — its 3,277-char
  contribution is pure Marathi Devanagari.
- **Cache handling**: fresh call, no cache hit (turn 2 has a thread_id
  so first-turn cache is bypassed).

### What the pipeline got WRONG on turn 2

- **Rewriter picked the wrong "text" to translate** — should have
  identified turn 1's ASSISTANT RESPONSE (the partition draft) as the
  "above text", not the Marathi paragraph in the USER PROMPT.
- **No "translate previous response" fast-path**. The system treats a
  language-switch directive as "re-generate the answer in the new
  language" (which requires re-classifying and re-running agents), not
  as "translate the previous assistant message in place." A dedicated
  fast-path would (a) skip classification entirely and (b) produce a
  faithful translation instead of a semantically different re-generation.
- **Draft-aware synthesis produced a mixed-language artefact**. Combining
  the persisted English draft with fresh Marathi output violates the
  language-consistency invariant. This is exactly the failure mode
  `LANGUAGE INSTRUCTION (STRICT)` in `localize_prompt` was built to
  prevent — but the synthesis-append path skips the localize step
  entirely on the persisted content.

---

## Prod comparison

Prod ran the same two turns with cleaner rewriter behaviour on turn 2:

| Signal | Local | Prod |
|---|---|---|
| Turn 1 elapsed | 116.1 s | 103.8 s |
| Turn 1 agents | `[Drafting]` | `[Drafting]` |
| Turn 1 resp chars | 12,084 | 9,663 |
| Turn 2 elapsed | 16.4 s | 38.9 s |
| Turn 2 agents | `[Drafting, Scenario]` | `[Drafting, Scenario]` |
| Turn 2 rewriter output | Full Marathi paragraph (grabbed from turn 1 prompt) | `वरील मजकूर मराठीत रूपांतरित करा` ("Convert the above text to Marathi" in Devanagari) |
| Turn 2 Devanagari chars | 2,694 | **6,195** |
| Turn 2 Latin chars | 9,524 | 7,890 |
| Turn 2 resp chars | 15,420 | 17,798 |

Prod's rewriter TRANSLATED the directive itself (much better) — but the
downstream synthesis still produced a mixed-language response because the
persisted turn 1 draft is glued in unchanged. **Both endpoints exhibit
the mixed-language failure on the same-thread language-switch follow-up.**
Prod's version has more Marathi than local's because the cleaner rewrite
gave the classifier a shorter, more directive-shaped query that the
Scenario agent could translate more of.

---

## Summary of pipeline behaviours observed

| Layer | Turn 1 | Turn 2 |
|---|---|---|
| Guardrail input | pass | pass |
| Language detection | `en` (correct) | `en` (correct for the 31-char directive) |
| Abbreviation expand | CrPC → Code of Criminal Procedure 1973 | n/a |
| History load | placeholder (fresh) | 1 turn, `prev_task=Drafting`, `has_prev_intent=True` |
| Query rewrite | skipped | ⚠️ **mis-fired** — returned Marathi paragraph from turn 1 prompt |
| Task classify | `Drafting` (correct) | `Scenario` (should have been "translate previous") |
| Intent extraction | ⚠️ **failed** — AttributeError, fell back to default | `language=mr, language_explicit=True, confidence=0.9` (correct) |
| Agent fan-out | `[drafting]` | `[scenario]` |
| Drafting picker | `SUIT FOR PARTITION AND SEPARATE POSSESSION.csv` | n/a |
| Section fan-out | 9 sections, 5 pair calls | n/a |
| Synthesis | solo pass-through | draft-aware append-only |
| Self-refine | not triggered | not triggered |
| Guardrail output | sanitize + disclaimer | sanitize + disclaimer |

---

## Actionable observations

1. **Intent extractor crashes on long multi-scenario prompts** (turn 1)
   — `AttributeError: 'NoneType' object has no attribute 'intent'` at
   `agents/orchestrator.py:1273` (approximate — see
   `Intent extraction (v2) completed` then `failed` sequence). Every long
   drafting prompt likely hits this; the pipeline degrades silently to
   `default_intent()`. Worth a bug report.
2. **The mega-prompt splitting problem** — a single request with 6
   distinct asks (2 drafts + 3 citations + arguments + defense arguments +
   consumer scenario + PDF review) is collapsed to ONE task and ONE
   reference file. The orchestrator has no mechanism to detect and
   split multi-intent prompts of this shape. The pipeline is optimised
   for one-ask-per-turn.
3. **Language-switch follow-up is unreliable** — the rewriter picks the
   wrong "above text" on long histories, and the synthesis path preserves
   the persisted English draft. Design gap: no "translate previous
   response verbatim" fast-path. This is a real UX bug — every user
   who types "in Marathi" or "translate this" after a long draft turn
   will get a mixed-language response.
4. **Draft-aware synthesis violates the language-consistency invariant**
   documented in [docs/multilingual_pipeline_analysis.md](../../docs/multilingual_pipeline_analysis.md).
   The append-only path skips `localize_prompt` on the persisted content,
   so a turn 1 draft in language A + a turn 2 result in language B
   produces a mixed-language artefact — no critic run to catch it.