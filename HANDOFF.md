# Handoff — retrieval & drafting fixes

**For the next Claude Code session picking this up.**

Branch `fix/audit-coverage`, based on `3a0d77b`. Written 13 Aug 2026.
**Nothing is committed.** Everything below sits in the working tree.

---

## Read this first — three things that will bite you

### 1. The working tree is SHARED with another session

A second Claude Code session is actively building **OCR blur detection** in this same
tree. Its files are **not yours**:

```
core/file_processor.py   core/gateway.py   frontend.html
scripts/check_image_ocr.py
tests/test_blur_detect.py
blurry image.jpg   real_blur.png   non-disclosure-agreement-uplead-791x1024.jpg
```

**Never run `git add .`** — it would commit their half-finished feature. Stage by
explicit path, always. Their tests pass (14) and there is no overlap with the files
below, but the two change-sets must not be merged into one commit.

### 2. Test files are gitignored

`.gitignore:157` excludes `tests/test_*.py`. Committing any test written here needs
`git add -f`.

### 3. There is no pytest in the venv

Every test suite has a `__main__` runner, so run them directly:

```powershell
.\.venv\Scripts\python.exe tests\test_drafting_grounding.py
```

The venv also has **no pip** — use `uv pip install --python .\.venv\Scripts\python.exe ...`
if you need something.

---

## Where the real tracker lives

**`FIX_REGISTER.md`** — the single source of truth: every item, its status, why it
matters, and four proposals that were investigated and **deliberately rejected** (with
reasons, so nobody re-proposes them). Item IDs (Q-1, R-4, …) are stable; use them.

`IMPROVEMENT_GAPS.md` and `PROPOSAL_improvements.md` are older analysis documents. Both
were corrected on 9 Aug — they had a **false security assurance** and four stale entries.
Treat them as background, not as tracking.

---

## What was done — 9 fixes

**9 files, +757 / −36. 7 test files, 63 tests, all passing.**

| ID | Fix | Files |
|---|---|---|
| **Q-1** | Drafting critic now receives the retrieved-source whitelist, so invented case citations get checked | `agents/drafting.py` |
| **Q-2** | Constitution & Maxim retry with a rewritten query before falling back to web search | `agents/constitution_maxim.py` |
| **Q-3** | `source_registry` state channel made live — first production writer for a reducer that had zero callers | `agents/drafting.py`, `agents/orchestrator.py` |
| **Q-18** | Drafting invented case facts when none were supplied — placeholder mode + regex validator | `agents/drafting.py` |
| **R-1** | Two blocking calls freezing the event loop | `agents/newacts.py`, `agents/drafting.py` |
| **R-4** | Three singletons could be built twice under concurrency (two load models) | `core/clients.py` |
| **R-6** | `self_refine` crashed on unparseable critique output and shipped drafts under a "passed" log | `core/self_refine.py` |
| **P-1** | Deploy pipeline ran zero tests; now gated on 63 | `.github/workflows/deploy.yml` |
| **P-3a** | Doc corrections — wrong model name, false "all agents have 3-tier fallback" claim | `CLAUDE.md`, `core/agent_fallback.py` |

Full write-ups with root-cause analysis are in `FIX_REGISTER.md`. Do not re-derive them.

### Test suites

```
tests/test_drafting_source_registry.py      13   Q-1
tests/test_constitution_maxim_retry.py      10   Q-2
tests/test_source_registry_state_channel.py 12   Q-3
tests/test_no_blocking_calls.py              4   R-1  (AST guard, all agent modules)
tests/test_client_singletons.py              5   R-4  (has a negative control)
tests/test_drafting_grounding.py            12   Q-18
tests/test_self_refine_unparseable.py        7   R-6
```

---

## What is actually VERIFIED vs merely reasoned

This distinction matters. Do not inherit optimism.

| Fix | Evidence |
|---|---|
| **Q-3** | ✅ Live — `registry_size=9` and `=15` observed in real request logs |
| **Q-1** | ✅ Live — `registry_records=14` observed on a real drafting request |
| **Q-18** | ✅ Live — `placeholder_mode=True`, `placeholder_count=41`, and **no invented names / FIR numbers / police stations** in the output |
| **R-1, R-4, R-6, P-1** | Structural — proven by tests, not by traffic |
| **Q-2** | ❌ **Never fired in production.** See below. |

### Q-2 is correct code for a situation that barely occurs

Worth knowing before anyone builds on it. The retry only fires when Elasticsearch returns
**zero** results. Three things prevent that:

1. The orchestrator **already rewrites the query per agent** before the agent runs — a
   plain-English question arrives as `"Right to property compensation eminent domain
   Article 31 Article 300A"`. (Ironically this is the same call `C-6` flags as redundant.)
2. The ES query uses `should` + `minimum_should_match: 1`, so **any single matching term
   returns hits**. English queries return *irrelevant* results, never zero.
3. Non-English queries that *do* return zero often route to `Legal_Concepts`, which is
   web-only and never touches ES.

Probed empirically: no English query returned zero hits; only Devanagari / transliterated
regional queries did. **Q-2's real value is non-English queries**, not the vocabulary
mismatch originally claimed.

The bigger gap this exposed: when the relevance gate **rejects** results, the agent goes
straight to web search and the tier-2 retry never gets a chance. That is where the volume
is, and it is not yet an item.

---

## Environment — already set up, do not redo

| | State |
|---|---|
| `.env` | Real keys present, including ES. Gitignored (`.gitignore:2`). |
| Embedding models | Downloaded and verified — BGE-large 1279 MB, MiniLM 87 MB |
| **Elasticsearch** | ✅ **Reachable from this machine** — confirmed by real query results |
| Server | Runs; the user has been driving it manually |
| pytest | Not installed (see above) |

### Running the server

```powershell
cd C:\Users\Admin\Downloads\Lawtech-AI\Lawtech-AI
.\.venv\Scripts\python.exe -m uvicorn core.gateway:app --host 127.0.0.1 --port 5000 --log-level info
```

gunicorn does **not** work on Windows (`import fcntl`). UI is at `/`, not `/frontend`.
`/pyapi/health` returns **503** without a local ES on `localhost:9200` — that is expected
and not a fault. The search field is `Promptquery`, not `query`.

### Running tests

```powershell
$env:OPENAI_API_KEY='x'; $env:GOOGLE_API_KEY='x'; $env:EMBEDDING_SERVICE_URL='http://localhost:1'
.\.venv\Scripts\python.exe tests\test_drafting_grounding.py
```

The suites set those themselves, but setting them explicitly avoids surprises. No test
makes an API call. `EMBEDDING_SERVICE_URL` skips the ~1.4 GB eager model load.

⚠️ **Test runs write into `logs/agent.log`.** This has already caused one wrong
conclusion — 72 "successful retries" that were entirely test fixtures. When reading logs
for production evidence, **filter out `req=-`** (test/startup noise) and look only at
real request IDs.

---

## The next task — Q-8, template selection

This is the biggest remaining problem and the analysis is already done. It explains both
the varying document structure **and** the lawyer's wrong-court-format complaint.

### The bug

`agents/drafting.py` picks its reference template like this:

```
BM25 match on page_content (size 100)
  → dedupe to unique file paths
  → an LLM picks one, seeing ONLY THE FILENAMES
```

The template **is** the document's structure, so a wrong pick means a wrong document.
Four runs of the identical query produced **three different templates**:

| Run | Template | Sections |
|---|---|---|
| 1, 2 | Bail Application under **Section 439** CrPC | 9 |
| 3 | First bail Application under **Section 436** CrPC | 5 |
| 4 | Bail Application for **Section 307** IPC *(attempted murder)* | 6 |

Run 4 is the clearest failure — a Section 307 template for a cheating case. The picker's
own logged reasoning shows it guessing from a filename that lists several sections.

### The fix, in order of cost

1. **Show the picker the first ~200 chars of each candidate.** The opening line of every
   template names the court and provision:
   ```
   IN THE COURT OF HON'BLE SESSIONS COURT _____   ← Section 439, Sessions
   BEFORE THE HON'BLE MAGISTRATE __               ← Section 436, Magistrate
   ```
   No re-index. Likely ends the roulette on its own.
2. **ES `collapse` on `source.keyword`** so `size: 100` returns 100 *distinct* templates
   rather than 100 passages. Log `len(file_paths)` first to confirm the funnel is
   actually collapsing. No re-index.
3. **Q-15** — feed the court/forum from `UserIntent` into selection. May need a re-index
   if the drafting index has no court field.

### ⚠️ Hard constraint

`CLAUDE.md` invariant #5 **forbids** reintroducing `GENERIC_COURT_SKELETONS`,
`DOC_TYPE_TO_FOOTER_KIND`, or any doc-type taxonomy. That approach existed and was
deliberately removed. **The reference template is the structural anchor** — the fix
belongs in *choosing a better template*, never in hard-coding formats.

Read the "Drafting invariants (do not regress)" section of `CLAUDE.md` before touching
`agents/drafting.py`. There are six of them and they are load-bearing.

---

## Other open items worth knowing

**S-1 — security, unscheduled.** The API key is hardcoded in **12 committed files**, and
`ADMIN_API_KEY` is set to the same value, so that committed string grants admin access.
Independent of all code work; needs a rotation decision.

**Q-18's known gap.** The section-wise generation path **logs** grounding violations but
does not regenerate — redoing N sections costs N Pro calls. Bail applications take that
path, so a violation there still reaches the user, visibly in logs.

**`self_refine` costs ~33–35% of request latency** (measured twice: 25.6s of 72s, 36s of
110s). Relevant to `C-2`. Note **Q-1 increased** its usage on drafting, so Q-1 and C-2
pull in opposite directions and should be priced together.

**C-1 is a trap.** `IMPROVEMENT_GAPS.md` describes four "cheap extraction calls on an
expensive model". They are actually **Google-Search-grounded generation** — two of them
produce the user-facing answer. Downgrading them is a quality regression, not a saving.
Already corrected in the register; do not act on the original claim.

---

## The lesson that matters most

**Five times** a documented claim did not survive being checked:

| Claim | Reality |
|---|---|
| "Reuse the existing adapters" | They read attributes; the ES tools return dicts — would have shipped an empty registry |
| "The rewrite helper exists and works" | For 5 agent names; silently returns the query unchanged for all others |
| `translate_draft` is wired up | Registered in `AGENT_TOOLS`, which drafting never reads — unreachable |
| "Four cheap extraction calls" (C-1) | All four are grounded generation |
| `SYSTEM_MAP.md` documents kNN + RRF | Zero matches in `*.py` — never built |

Every one would have produced a change that **looks landed and is not**: green tests,
clean diff, no behaviour change.

**Rule:** treat every *"X already exists, just call it"* as unverified until you have read
X's signature and confirmed the data you plan to hand it is the shape it expects.

Two tests here exist purely because of this pattern and both caught real errors —
`tests/test_no_blocking_calls.py` (validated against the pre-fix commit, where it
correctly flags both original bugs) and the negative control in
`tests/test_client_singletons.py` (proves the harness generates real concurrency).
Keep that discipline.

---

## Suggested commit plan

Six commits, staged by explicit path:

| # | Scope | Files |
|---|---|---|
| 1 | Q-1 + Q-3 citation grounding *(also carries R-1's drafting hunk)* | `drafting.py`, `orchestrator.py`, 2 tests |
| 2 | Q-2 + P-3a | `constitution_maxim.py`, `agent_fallback.py`, `CLAUDE.md`, 1 test |
| 3 | R-1 blocking calls | `newacts.py`, 1 test |
| 4 | R-4 singleton races | `clients.py`, 1 test |
| 5 | Q-18 + R-6 drafting grounding & critic fail-open | `drafting.py`, `self_refine.py`, 2 tests |
| 6 | P-1 CI gate + docs | `deploy.yml`, `FIX_REGISTER.md` |

Commits 1 and 5 both touch `drafting.py`; hunk-splitting was judged too risky in a shared
tree, so state that in the message rather than pretending it is clean.

**Do not push without asking the user.** Committing to this branch deploys nothing —
`deploy.yml` fires only on push to `dev`, and prod is a manual dispatch from `main`.
