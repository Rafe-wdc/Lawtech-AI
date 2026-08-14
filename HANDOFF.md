# Handoff — retrieval & drafting fixes

**For the next Claude Code session picking this up.**

Branch `fix/audit-coverage`, based on `3a0d77b`. Last updated 13 Aug 2026.
**15 commits, all local. Nothing has been pushed.**

---

## Read this first — three things that will bite you

### 1. The working tree is SHARED with another session

A second Claude Code session is actively building **OCR blur detection** here. These are
**not yours** and must never be committed by you:

```
core/file_processor.py   core/gateway.py   frontend.html
scripts/check_image_ocr.py               tests/test_blur_detect.py
blurry image.jpg   real_blur.png   non-disclosure-agreement-uplead-791x1024.jpg
```

**Never `git add .`** — stage by explicit path, always. Their tests pass (14) and there is
no file overlap with the work below, but the two change-sets must stay separate.

### 2. Two directory trees exist — use the nested one

```
C:\Users\Admin\Downloads\Lawtech-AI\             ← stale copy, NOT the repo
C:\Users\Admin\Downloads\Lawtech-AI\Lawtech-AI\  ← the repo (.git, models/, frontend.html)
```

The user has already lost a test run to this: uvicorn started from the parent, served a
different codebase, 404'd the frontend and reported "models not found".

### 3. Tests are gitignored and there is no pytest

`.gitignore:157` excludes `tests/test_*.py`, so committing any test needs `git add -f`.
(`HANDOFF.md` is excluded too, at `.gitignore:88`.)

Every suite has a `__main__` runner:

```powershell
.\.venv\Scripts\python.exe tests\test_drafting_grounding.py
```

The venv has **no pip** either — use `uv pip install --python .\.venv\Scripts\python.exe ...`.

⚠️ Running the full suite in one loop has **timed out at 5 minutes**. Run suites
individually, or in batches of three or four.

---

## Where the real tracker lives

**`FIX_REGISTER.md`** — every item, its status, why it matters, and four proposals that
were investigated and **deliberately rejected** with reasons. Item IDs (Q-1, R-4 …) are
stable; use them. It is one commit behind the newest work — the commit messages below
carry the rest.

`IMPROVEMENT_GAPS.md` and `PROPOSAL_improvements.md` are older analysis. Both were
corrected on 9 Aug — they contained a **false security assurance** and four stale entries.
Background, not tracking.

---

## What was done — 15 commits

Read `git log` for the full reasoning; each message states root cause and evidence.

```
fd96a44  fix(drafting): statutory currency (IPC/BNS) and omissions that expose the litigant
e73494d  fix(drafting): require explicit paragraph numbering; stop truncating the critique
0191b22  fix(drafting): bracket the whole assertion, not just the values inside it
5806a4b  fix(drafting): make document type a hard gate in the template picker
dc32b1a  fix(ci): repair deploy.yml encoding damaged by a PowerShell rewrite
cd6301a  fix(drafting): give the template picker content, not just file names
d1594ce  security: read the test API key from the environment, never hardcode it
28480c7  docs: add HANDOFF.md
7e9a4ac  ci(deploy): gate deployment on real tests, and add the fix register
108bac8  fix(constitution-maxim): add the missing tier-2 query-rewrite retry
4e473a9  fix(self-refine): stop reporting a crashed critique as a pass
944128b  fix(clients): guard singleton construction against concurrent first requests
f753722  fix(newacts): run the retry search off the event loop
7def69a  fix(drafting): ground the citation critic and stop inventing case facts
1560099  docs(fallback): correct model name and the 3-tier fallback claim
```

**8 test suites, 77 tests.** All passing as of `e73494d`; `fd96a44` is prompt text only
and was verified by import plus the picker suite.

```
tests/test_drafting_source_registry.py      13   Q-1
tests/test_constitution_maxim_retry.py      10   Q-2
tests/test_source_registry_state_channel.py 12   Q-3
tests/test_no_blocking_calls.py              4   R-1  (AST guard, validated pre-fix)
tests/test_client_singletons.py              5   R-4  (has a negative control)
tests/test_drafting_grounding.py            12   Q-18
tests/test_self_refine_unparseable.py        7   R-6
tests/test_drafting_template_picker.py      14   Q-8
```

---

## VERIFIED in production vs merely reasoned

**Do not inherit optimism.** Everything else is a mechanism argument from reading code.

| Fix | Evidence from real request logs |
|---|---|
| **Q-1** | ✅ `registry_records=14` |
| **Q-3** | ✅ `registry_size=9`, `=15` |
| **Q-18** | ✅ `placeholder_mode=True`, `placeholder_count=31–41`, zero invented names/FIR/police stations |
| **Q-8** | ✅ `Picker chose … First bail Application under s.483 BNSS`, reasoning states the type match |
| **R-6** | ✅ Caught a live failure and named its cause (see below) |
| R-1, R-4, P-1 | Structural — tests only |
| **Q-2** | ❌ **Never fired in production** |

### R-6 earned its keep immediately

It surfaced a failure that had been invisible:

```
Critique output failed schema validation — NOT verified
  parsing_error=OutputParserException: Failed to parse Critique from completion
  {"passes": false, "confidence": 0.9, "violations": [{"field": "legal_artifact", …
  raw_chars=4397
Self-refine returning UNVERIFIED — refined=True
```

The critique JSON was **truncated** at `max_output_tokens=4096`. The critic had found 5
violations; the output was cut off mid-string, the parse failed, and **none were applied**
— while the log said "Self-refine passed". Raised to 16384 in `e73494d`.

Expect `self_refine` to change drafts **more** now that its findings actually land.

### Q-2 is correct code for a situation that barely occurs

The retry only fires when ES returns **zero** results. Three things prevent that:

1. The orchestrator **already rewrites the query per agent**, so a plain-English question
   arrives as `"Right to property compensation eminent domain Article 31 Article 300A"`.
   (Ironically the same call `C-6` flags as redundant.)
2. The ES query uses `should` + `minimum_should_match: 1` — any single matching term
   returns hits. English queries return *irrelevant* results, never zero.
3. Non-English queries that *do* return zero often route to `Legal_Concepts`, which is
   web-only and never touches ES.

Probed empirically: **no English query returned zero hits.** Only Devanagari and
transliterated regional ones did. Q-2's real value is non-English queries.

**The bigger gap it exposed, not yet an item:** when the relevance gate **rejects**
results, the agent goes straight to web search and the tier-2 retry never gets a chance.
That is where the volume is.

---

## Environment — already set up, do not redo

| | State |
|---|---|
| `.env` | Real keys present, including ES. Gitignored (`.gitignore:2`). |
| Embedding models | Downloaded and verified — BGE-large 1279 MB, MiniLM 87 MB |
| **Elasticsearch** | ✅ **Reachable** — confirmed by live queries against the real corpus |
| pytest | Not installed |

```powershell
cd C:\Users\Admin\Downloads\Lawtech-AI\Lawtech-AI
.\.venv\Scripts\python.exe -m uvicorn core.gateway:app --host 127.0.0.1 --port 5000 --log-level info
```

gunicorn does **not** work on Windows (`import fcntl`). UI is at `/`, not `/frontend`.
`/pyapi/health` returns 503 without a local ES — expected. The search field is
`Promptquery`, not `query`.

⚠️ **Test runs write into `logs/agent.log`.** This already caused one wrong conclusion —
72 "successful retries" that were entirely test fixtures. When reading logs for production
evidence, **filter out `req=-`** and look only at real request IDs.

⚠️ **Do not rewrite whole files through PowerShell `Set-Content`.** It re-encoded
`deploy.yml` and mangled every box-drawing character (`dc32b1a` was the repair). Use the
Edit tool.

---

## What is still open

### ✅ Q-19 — THE CRITIC INVENTED CASE FACTS BY FILLING IN PLACEHOLDERS (FIXED 14 Aug, commit `464673e`)

**RESOLVED — see the "RESOLVED (14 Aug)" block at the END of this entry for the fix and the
verified before/after. Everything below it (the original diagnosis, the prompt-wiring
audit, the consolidated plan) is retained as the record of how it was found and fixed.**

Observed 13 Aug, request `b8553d2f`, query
`"Draft a bail application for cheating under Section 420 Indian Penal Code"` with **no
case facts supplied**. The delivered draft contained:

```
Rohan Sharma · Age 35 · Businessman · 123, ABC Colony, Kothrud, Pune - 411038
Crime Register No. 123 of 2024 · Kothrud Police Station · Yerwada Central Jail
arrested 01/01/2024 · "aged parents, wife, and two minor children"
```

All fabricated. None of it was in the query.

**The generator did its job.** The log proves the draft was correct when it left
`_generate_draft`:

```
16:58:27  DraftingValidation | unsupported_facts=5 | placeholder_count=46
16:58:39  Critique   | violation_count=4
16:59:14  Refinement | 7912 -> 8508
16:59:26  Critique   | violation_count=12 | critical=2
16:59:55  Refinement | 8508 -> 9124
17:00:01  Self-refine max iterations exhausted
```

**46 placeholders went in; three critique-and-refine cycles turned them into invented
particulars.**

#### Mechanism — three changes on this branch colliding

1. **Q-18** makes the generator emit `[ACCUSED'S NAME]`-style placeholders when no case
   facts exist. Correct behaviour.
2. **`placeholder_marker`** in `CRITIQUE_PROMPT` (`core/self_refine.py:496`, pre-existing)
   flags surviving placeholders as a **MAJOR** violation — *"the drafting prompt forbids
   these"*.
3. **Q-1** forces `self_refine` to run whenever a populated registry exists, and **R-6**
   raised `max_output_tokens` 4096 -> 16384 so the critique now parses and its violations
   are actually applied. Before R-6 the JSON truncated and everything was silently
   discarded — which is why this only surfaced now.

So the critic treats correct placeholders as defects and the refiner **invents values to
remove them**. The two halves of the system are enforcing opposite rules.

`_enforce_grounding` cannot catch it: it runs inside `_generate_draft`, **before**
`self_refine` in `drafting_node`.

#### Fix direction (not implemented — the user asked for no code changes)

The critic must know when placeholder mode was active. Options, roughly in order:

1. **Thread the no-case-facts signal into `self_refine`** and suppress
   `placeholder_marker` for that call. Smallest change, addresses the root.
2. **Re-run `validate_draft_grounding` after `self_refine`** and prefer the
   pre-refinement draft when the refined one has more ungrounded facts. A safety net
   rather than a fix, but cheap and catches any future variant.
3. **Narrow `placeholder_marker`** so it targets only genuine leakage markers
   (`[CITE: ...]`, `TBD`, `FILL IN`, `<insert party>`) and never legitimate field
   placeholders like `[ACCUSED'S NAME]`.

1 and 2 together are probably right. **Verify by re-running the query above and confirming
the delivered draft still contains placeholders, not names.**

#### Also visible in that run

- `Intent extraction failed; using default_intent | error=` — the extractor threw an
  empty error and the request proceeded with a default intent. Unrelated, worth a look.
- **244 seconds** end to end, of which `self_refine` was ~83s across three cycles. It also
  grew the draft 7912 -> 9124 chars while never reaching `passes=True`
  (`max iterations exhausted`, 4 violations still open). Relevant to `C-2`.

#### UPDATE (14 Aug) — verified against the real run + a prompt-wiring audit

The `b8553d2f` draft was read end-to-end against the "Untested — last three commits"
checklist below. **It failed 4/4 criteria, not just the invented facts:**

| Criterion | Result |
|---|---|
| `unsupported_facts` → 0 | ❌ became a fully-populated fiction (the Q-19 core, above) |
| Numbered paragraphs (`e73494d`) | ❌ delivered as unnumbered running prose *(verify vs literal `\n1.` in logs — a rendered list can paste without visible numbers)* |
| BNS counterpart inline (`fd96a44`) | ❌ only `s.420 IPC` / `s.439 CrPC`; **no** `s.318 BNS` / `s.483 BNSS` — and the picker chose the **BNSS §483** template, so the draft mixes a new-code template with old-code citations |
| Prior-bail disclosure (`fd96a44`) | ❌ no such paragraph |

**Prompt-wiring audit settled the "stripped vs never-there" fork by code read, not a re-run:**

- The section-wise path uses `DRAFTING_SECTION_PAIR_PROMPT` (`agents/drafting.py:1902`).
- `4A` (statutory currency, worked example `s.439 CrPC / s.483 BNSS`) and `4B` (procedural
  disclosures incl. **prior-bail**) are inline in `DRAFTING_SYSTEM_PROMPT` **only**
  (`config/prompts.py:1358`, `:1374`) — *before* its `+=` append. The section prompt
  inherits only the shared `INDIAN_LEGAL_*` blocks (`config/prompts.py:1772`), which
  contain **neither** 4A nor 4B.
- Consequence: **prior-bail disclosure NEVER reaches the section-wise generator** (bail
  always fans out), so it was *never wired*, not stripped. **Currency** reaches the section
  path only as a weak one-liner (`config/prompts.py:1735`); the strong version is
  `SYSTEM_PROMPT`-only. **Numbering** *is* in the section prompt (`:1721`, `:1749`).

**Corrected attribution (this retracts the "self_refine is the common regressor for all
four" reframe):**

| Failure | Owner |
|---|---|
| Invented facts | `self_refine` — the placeholder/critic collision above. Confirmed. |
| Prior-bail missing | **Prompt-wiring gap** (generation), NOT `self_refine`. |
| Currency missing | **Weak section-prompt wiring** (generation); possibly compounded by refine. |
| Numbering missing | Wired to the section path → still ambiguous; the probe below decides it. |

#### Consolidated fix set (supersedes the "Fix direction" list above)

0. **One-line pre-refine probe — now needed ONLY for numbering.** Log whether the draft
   leaving `_generate_draft` contains literal `\n1.` markers. Currency and prior-bail are
   already resolved by the grep; this splits "generator didn't follow" from "refiner
   stripped" for numbering alone.
1. **Suppress `placeholder_marker` in placeholder mode.** Thread `facts_present=False`
   from `drafting_node` into `self_refine`. Removes the destructive pressure while keeping
   Q-1's `unretrieved_citation` check alive. *(fixes invented facts)*
2. **Post-refine grounding net.** Re-run `validate_draft_grounding` after `self_refine`;
   keep the pre-refine draft if the refined one has more ungrounded facts. The **only**
   catch on the section-wise path, where `_enforce_grounding` merely logs. *(fixes facts)*
3. **Wire 4A (full) + 4B into the section path** — best done by **moving 4A/4B into the
   shared block both prompts append**, so a document-type disclosure can't silently diverge
   between the two generators again. *(fixes prior-bail + currency — generation, not
   `self_refine`)*
- ⛔ **REJECTED — 3(b) "short-circuit `self_refine` on placeholder-mode section-wise
  drafts."** That re-disables Q-1's fabricated-citation check on precisely the drafts it
  was built for — trading invented facts for uncaught invented case-law, the worse defect
  for a filed document. Do not.
4. **C-2** (abort-if-not-improving + tighter gating): measure **after** — Q-1 and C-2 pull
   opposite ways and must be priced together.

**Do 0 + 1 + 2 + 3 together.** 1+2 close the facts regression (Q-1-safe); 3 closes
prior-bail + currency; 0 tells you whether numbering also needs 3 or a prompt fix.

#### Generalize once closed — this is not bail-specific

A document-type-agnostic component (`self_refine`'s placeholder rule) plus a section-path
wiring gap (4A/4B) break document-type-specific fixes. **Every** doc type routed through
section-wise generation + refine is exposed. Run the regression across **legal notice,
plaint, affidavit** — not bail alone — since the standing goal is broad drafting quality,
not patching one type at a time.

**Verify (all four must hold on re-run, no case facts):** placeholders not names
(`unsupported_facts=0`); numbered paragraphs; `s.318 BNS` + `s.483 BNSS` counterparts
inline; a prior-bail disclosure paragraph.

#### RESOLVED (14 Aug) — fix landed (commit `464673e`) and verified against real runs

Implemented **0 + 1 + 2 + 3** (3(b) stayed rejected). Three files, +73 lines, staged by
explicit path, **not pushed**:

- **`core/self_refine.py`** — `self_refine` gained `placeholder_mode`; drops
  `placeholder_marker` violations before the refiner sees them. `unretrieved_citation`
  (Q-1) and every other category still run.
- **`agents/drafting.py`** — post-refine grounding net (revert to pre-refine draft if
  refinement increases ungrounded facts) + the pre-refine numbering probe.
- **`config/prompts.py`** — ported 4A currency + 4B disclosures (incl. prior-bail) into
  `DRAFTING_SECTION_PAIR_PROMPT` via a new `DRAFTING_SECTIONWISE_DISCLOSURES` block.

**Verified before/after** (fresh runs, delivered output read from bytes, not paste):

| | Baseline (`b8553d2f` bail / `b8f89040` notice) | After (`022faf96` bail / `53281276` notice) |
|---|---|---|
| Fabricated identity facts | Rohan Sharma, C.R.123, Kothrud PS, Yerwada / Arjun Sharma, Rajesh Kumar, INV/PCI | **NONE** — clean bracketed placeholders on both |
| Character assertions | asserted as fact | bracketed `[IF APPLICABLE: …]` |
| Numbered paragraphs | reported missing | present in raw (1→13; probe `has_numbered_paragraphs=True`) — paste flattens the ordered list |
| Prior-bail disclosure | absent | present (fix #3) |
| BNSS §483 procedural counterpart | absent | present (fix #3) |

**Proven vs shadowed:** fix #2 (net) and fix #3 (disclosures) are demonstrated in delivered
output. **Fix #1 was NOT exercised on bail** — because the critique kept failing to parse
(see Q-20), `self_refine` returned UNVERIFIED and never ran the refiner, so the clean
generator draft shipped by default. Fix #1 remains correct as the safety layer for when the
critique parses; the notice run (`53281276`) is where the refine loop actually ran and the
net confirmed 0 ungrounded facts.

**Residuals** (none block the fix; ranked): (1) **Q-20** — critique truncates/fails schema
on bail, disabling `self_refine` + Q-1 + `duplicate_section_block`; (2) duplicate
cause-title block (would be caught by `duplicate_section_block` if the critique parsed);
(3) BNS §318 substantive counterpart still not emitted (only procedural §483 — 4A
half-followed by the generator); (4) `self_refine` non-convergence → `C-2`; (5) Grounds
numbering continues past 10 rather than restarting (prompt-design choice, not a bug).

---

### 🔵 Q-20 — THE DRAFTING CRITIQUE INTERMITTENTLY RETURNS UNPARSEABLE JSON

Surfaced while verifying Q-19. On the bail runs (`022faf96`, `a11d72dc`) the critic returned
`OutputParserException: Failed to parse Critique from completion {…}` with `raw_chars≈4300`
— the JSON is cut off mid-string (e.g. `"issue": "…the intent was extracted `). `_critique`
handles it gracefully (returns `passes=True, confidence=0`, `_UNVERIFIED_NOTE`), so the
request survives — **but `self_refine` then does nothing.**

Why it matters: a silently-disabled `self_refine` also disables **Q-1's `unretrieved_citation`
check** (fabricated case-law would not be caught) and the `duplicate_section_block` check
(the duplicate cause-title in `a11d72dc` slipped through). On Q-19 it happened to *help*
(the refiner could not invent), but that is luck, not design.

Not a truncation-budget issue: `max_output_tokens=16384`, `thinking_budget=0`, yet it cuts
at ~1,100 tokens — so it is Gemini structured-output flakiness on the nested
`Critique`→`list[Violation]` schema, not R-6 again. Candidate directions: retry once on
`parsing_error`; simplify/loosen the structured-output schema; or fall back to a
non-structured critique parse. Needs its own investigation — do not fold into Q-19.

---

### Q-19b — SECTION-WISE NUMBERING RESTARTS + DUPLICATED CAUSE TITLE (FIXED 14 Aug, commit `f7f0d0e`)

User reported the rendered bail draft showed numbering repeating "1., 1., 1." and the cause
title appearing 2-3x. Root cause (found via a 5-agent diagnosis workflow, adversarially
verified): the section-wise path generated each pair as a stateless call with NO computed
continuation state - numbering continuity was left to the model re-reading free-text
prior_text, and the per-pair task list was re-numbered from 1. every call (drafting.py:1921),
nudging a restart; later pairs also re-emitted the cause title / party block.

Fix (in _generate_sectionwise / _generate_section_pair): compute next_para_no as authoritative
CODE state, SCOPED to the body region before the Prayer/Verification/List-of-Documents tail (so
the List's own 1.,2.,3. and 3-digit statute numbers cannot inflate or forward-jump it); pass
header_already_emitted=bool(completed) so every pair after the first is hard-told NOT to emit a
court header / cause title / vs / party block; and bullet the per-pair task list instead of
re-numbering it from 1. Invariant-safe (generation-input state, not a critic/skeleton/regex-gate).

Verified (`9babdea4`): cause title exactly once (was 2-3x), one party block, body numbering
continuous 1->15, List of Documents correctly its own 1->11. Q-19 placeholders + BNS/prior-bail
disclosures intact. The adversarial verifier's corrections are baked in (body-scoped counter,
not a naive global max; body-region-only acceptance test).

Residual (scoped out): the fan-out JUDGE prompt (config/prompts.py:1568) still positively splits
a bail application into overlapping SUBSTANTIVE sections (grounds + parity + medical/family) ->
occasional grounds-content overlap. Plan-time judge-merge / CRITIQUE_PROMPT issue, separate from
this numbering/header fix. Header suppression substantially reduces but does not GUARANTEE zero
duplication; for a hard guarantee, add an assembly-time collapse of a duplicated cause-title/vs
block inside _generate_sectionwise (keep it there, not validate_draft, to stay invariant-#4-safe).

---

### Q-15 — court awareness in template selection

Nothing tells the picker which court the user is filing in. Q-8 fixed *document type*;
**forum** is still unaddressed, and it is the lawyer's original complaint. The
`UserIntent` extractor already runs on every request — extract the forum and boost or
filter on it. May need a re-index if the drafting index has no court field.

### Drafting gaps from a lawyer's review — 2 of 6 remain

A lawyer reviewed real output. Fixed: paragraph numbering (`e73494d`), statutory currency
IPC↔BNS, prior-bail disclosure, verification clause, advocate enrolment block (`fd96a44`).

**Still open:**
- **Court designation hardcoded to "Sessions Judge"** — regular bail for a 420-only matter
  may be maintainable before a Magistrate. This is Q-15, not a prompt fix.
- **Ungrounded assertions persist** — `unsupported_facts=4`, still asserting sole
  breadwinner / permanent resident / no antecedents / judicial custody. `0191b22` made
  bracketing a hard rule and compliance is partial across the 5 section-wise calls. May
  improve now the critic can actually apply its findings.

### Untested — the last three commits

Nobody has run a draft since `0191b22`, `e73494d`, `fd96a44`. One run of
`"Draft a bail application for cheating under Section 420 Indian Penal Code"` should
confirm: numbered paragraphs, the BNS counterpart cited inline, a prior-bail disclosure
paragraph, and `unsupported_facts` trending toward 0.

### S-1 — security, partially done

The hardcoded key is **removed from all 12 files** (`d1594ce`) and reads from
`LAWTECH_TEST_API_KEY` with an empty default. **The user still needs to rotate it** — it
remains in git history — and set `ADMIN_API_KEY` to a value *different* from `API_KEYS`.
They are currently identical, which is what turned a leaked user key into a leaked admin
key. Repo is private, so urgency is moderate.

### Other standing items

- **Q-18 gap:** the section-wise path logs grounding violations but does not regenerate —
  N sections means N Pro calls. Bail applications take that path.
- **`self_refine` costs ~33–35% of request latency** (25.6s of 72s; 36s of 110s). Relevant
  to `C-2` — but **Q-1 increased** its usage, so the two pull in opposite directions and
  must be priced together.
- **C-1 is a trap.** `IMPROVEMENT_GAPS.md` calls four scenario calls "cheap extraction on
  an expensive model". They are **Google-Search-grounded generation**, two of which produce
  the user-facing answer. Downgrading is a quality regression. Corrected in the register.

---

## Hard constraints

`CLAUDE.md` has a **"Drafting invariants (do not regress)"** section — six of them, all
load-bearing. Read it before touching `agents/drafting.py`.

Invariant #5 forbids reintroducing `GENERIC_COURT_SKELETONS`, `DOC_TYPE_TO_FOOTER_KIND`,
or any doc-type taxonomy. **The reference template is the structural anchor.** Fixes belong
in *choosing a better template*, never in hard-coding formats. The `4B` block added in
`fd96a44` sits close to that line deliberately — it is scoped to omissions that *expose
the litigant*, not general enrichment. Keep it that way.

---

## The lesson that matters most

**Six times** a documented claim did not survive being checked:

| Claim | Reality |
|---|---|
| "Reuse the existing adapters" | They read attributes; ES tools return dicts — would have shipped an empty registry |
| "The rewrite helper exists and works" | For 5 agent names; silently returns the query unchanged for the rest |
| `translate_draft` is wired up | Registered in `AGENT_TOOLS`, which drafting never reads — unreachable |
| "Four cheap extraction calls" (C-1) | All four are grounded generation |
| `SYSTEM_MAP.md` documents kNN + RRF | Zero matches in `*.py` — never built |
| "The picker funnel collapses" *(mine)* | Probing showed 100 passages already gave 100 distinct templates |

Every one would have produced a change that **looks landed and is not** — green tests,
clean diff, no behaviour change.

**Rule:** treat every *"X already exists, just call it"* as unverified until you have read
X's signature and confirmed your data is the shape it expects.

**A second lesson, from Q-8 and Q-18:** in prompts, *guidance loses to competing pressure;
an ordered hard gate does not.* The picker was given content previews and still chose a
complaint for a bail application — its own reasoning said so. Only a Step 1 / Step 2 gate
fixed it. The same pattern applied to assertion bracketing. If a prompt fix half-works,
make it a gate rather than adding more words.

Two tests exist purely to defend this discipline and both caught real errors:
`test_no_blocking_calls.py` (validated against the pre-fix commit, where it correctly
flags both original bugs) and the negative control in `test_client_singletons.py` (proves
the harness generates real concurrency). Keep them.

---

## Before you push

Nothing is pushed. Committing to this branch deploys nothing — `deploy.yml` fires only on
push to `dev`, and prod is a manual dispatch from `main`. **Ask the user before pushing.**
