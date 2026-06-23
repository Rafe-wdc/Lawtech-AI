# Drafting agent — Writ of Mandamus failure report

**Date:** 2026-06-22
**User input:** `user_prompt.txt` — Writ of Mandamus skeleton (Prasad Nandkumar Avachat v. State of Maharashtra & Ors), Bombay HC
**System output:** `test_pdfs/IN THE HON.pdf` (~90 KB PDF, ~31 KB rendered, 18 pages)
**User feedback:** "not at all happy with the response (not useful) also at the end it got truncated"

---

## Summary

User asked for a Writ Petition with **17 named substantive sections** (Parts A–O = 15 + GROUNDS + PRAYER), each with 7–13 paragraphs (Part G: 13–15; GROUNDS: 40–50).

What the system returned:
- 15 substantive sections only (Part O conflated with GROUNDS)
- **GROUNDS as a separate 40–50 paragraph section: MISSING**
- **PRAYER: MISSING**
- **Verification / List of Documents / Affidavit / Annexures Index: MISSING**
- Paragraph numbering scrambled across sections
- Output truncated mid-sentence at "and ac" with the literal banner:
  > _Response truncated -- the answer was longer than the display budget. Try asking a narrower question (specific section / specific act / single comparison) for a focused result._
- Same 4 Supreme Court cases repeated across all 15 sections (Raja Ram Jaiswal, S.B. Vohra, Common Cause, P.R. Murlidharan)
- Section 4 has a duplicated `## 4.` heading rendering
- Section 10 has an empty `69.` paragraph (gap)

The user is right — for a long-form drafting workload this is broken in several independent places.

---

## Defect 1 — Hard 60 KB final-response cap (the visible "truncation" message)

**Location:** [agents/guardrail.py:38](../agents/guardrail.py#L38)

```python
MAX_FINAL_RESPONSE_CHARS = 60_000
TRUNCATION_SUFFIX = (
    "\n\n---\n_Response truncated -- the answer was longer than the display "
    "budget. Try asking a narrower question (specific section / specific act / "
    "single comparison) for a focused result._\n"
)
```

The output guardrail unconditionally clamps every `final_response` to 60 K characters and appends the visible "Response truncated…" suffix the user saw. The clamp lives in [guardrail.py:232-237](../agents/guardrail.py#L232-L237):

```python
if len(cleaned) > MAX_FINAL_RESPONSE_CHARS:
    keep = MAX_FINAL_RESPONSE_CHARS - len(TRUNCATION_SUFFIX)
    log.warning("Response exceeded hard char cap -- truncating", ...)
    cleaned = cleaned[:keep] + TRUNCATION_SUFFIX
```

**Why it bites Drafting:** the orchestrator's own cap allows **250 K** ([orchestrator.py:1877](../agents/orchestrator.py#L1877)). Drafting's full assembled output for a 17-section writ runs ~80–120 K chars. The guardrail re-trims to 60 K on the way out — i.e. the system computes the full draft, then throws away the back third before sending it to the user.

**Impact:** invisible to the orchestrator (it sees its own cap was respected); the suggestion "ask a narrower question" is wrong advice for Drafting (drafts are inherently long).

**Fix:** raise to 250 K to match orchestrator, OR route Drafting around this cap (drafts are not Q&A answers — the "narrower question" hint is nonsensical for a writ).

---

## Defect 2 — Section cap of 16 → GROUNDS, PRAYER, and procedural blocks silently dropped

**Location:** [agents/drafting.py:58](../agents/drafting.py#L58)

```python
_MAX_SECTIONS = 16
```

Cap is enforced twice:
- [drafting.py:1772-1775](../agents/drafting.py#L1772-L1775) — after outline generation, lops the tail
- [drafting.py:4539-4542](../agents/drafting.py#L4539-L4542) — again after procedural-section injection from doctrinal stance

```python
if len(outline.sections) > _MAX_SECTIONS:
    log.warning("Outline expanded past cap after procedural injection",
                before=_MAX_SECTIONS, after=len(outline.sections))
    outline.sections = outline.sections[:_MAX_SECTIONS]
```

**Why it bites this writ:**

| Section group | Count |
|---|---|
| User-named Parts A–O | 15 |
| Separate GROUNDS section (user asked 40–50 paras) | 1 |
| PRAYER | 1 |
| Verification (mandatory for writ) | 1 |
| List of Documents | 1 |
| Affidavit in Support | 1 |
| Annexures Index | 1 |
| **Total needed** | **21** |

Cap = 16 → first cut drops the last 5. The outline LLM, seeing the cap, then **merged** Part O ("Exhaustion of Remedies") with GROUNDS into Section 15 ("Exhaustion of Remedies and Grounds for Writ") and dropped everything after — including PRAYER, which is **mandatory** for a writ petition.

The comment at [drafting.py:55-58](../agents/drafting.py#L55-L58) acknowledges the cap was sized for civil suits with 14–16 sections:

```python
# Max sections the outline can contain. Civil suits with the full procedural
# pack (Schedule, Court Fee, List of Docs, separate IA for TI, Verification,
# Affidavit) routinely need 14-16 sections, so the cap is generous.
_MAX_SECTIONS = 16
```

A writ with user-enumerated Parts A–O blows that budget on substantive sections alone.

**Fix options:**
1. Raise cap to 24–28
2. Make cap dynamic — split substantive cap from procedural cap (e.g. 20 substantive + unlimited procedural, since procedural blocks are short)
3. When the user explicitly enumerates parts (regex/LLM detection of "Part A / Part B / ..." or numbered headings), respect the user's count

---

## Defect 3 — Paragraph numbering computed from `estimated_paragraphs`, not actual output

**Location:** [agents/drafting.py:3496-3504](../agents/drafting.py#L3496-L3504)

```python
start_para_offsets: list[int] = []
running = 1
for plan in outline.sections:
    start_para_offsets.append(running)
    if not _is_procedural(plan.title):
        running += max(1, plan.estimated_paragraphs)
```

`start_para_offsets` is precomputed **before any section LLM call runs**, using the outline LLM's `estimated_paragraphs` guess. Sections then generate in parallel ([drafting.py:3568](../agents/drafting.py#L3568)) and each receives a frozen `start_para_num`. When actual paragraph count diverges from the estimate, every downstream section's offset is wrong.

### Observed numbering in the response

| Section | Heading | Para range | Notes |
|---|---|---|---|
| 1 | Particulars of the Cause | 1–5 | ✓ |
| 2 | Facts Leading to Filing | 6–15 | ✓ (running counter at 16) |
| 3 | Prolonged Administrative Defiance | **6–13** | **WRONG — restarted at 6** |
| 4 | Systematic Manipulation | **1–9** | **WRONG — restarted at 1** + duplicate `## 4.` heading |
| 5 | Historical Background | **23–29** | **WRONG — jumps to 23** (expected ~17) |
| 6 | Genesis of Fraud | 30–37 | ✓ continues from 29 |
| 7 | Legal Challenges / Quashing | 38–44 | ✓ |
| 8 | Contumacious Abuse of Power | 45–50 | ✓ (but only 6 paras — user asked 13-15) |
| 9 | Misuse of Fraudulent Entries | **59–65** | **WRONG — jumps from 50 to 59** (gap of 8) |
| 10 | Parallel Revenue Appeals | 66–74 | ✓ but **para 69 is EMPTY** |
| 11 | Repeated Illegal Attempts | **74–80** | **WRONG — restarts at 74, colliding with Sec 10** |
| 12 | Complaints against Tahsildar | 81–88 | ✓ |
| 13 | Failure to Produce Documents | 89–95 | ✓ |
| 14 | Illegality of 'Vahiwat' Entries | 96–103 | ✓ |
| 15 | Exhaustion of Remedies + Grounds | 104–111 | truncated mid 111 |

**Two root causes for the bad sections (3, 4, 5, 9, 11):**

1. **Local-range echo (Sec 3, 4):** the user prompt literally contains `"PART A: ... PARAGRAPH DESCRIPTIVE 7 TO 13"` for every part. The section LLM appears to have interpreted "7 to 13" as the desired paragraph *range* and emitted those literal numbers, ignoring `start_para_num` (Sec 3 used 6–13, Sec 4 restarted at 1). The Gemini Flash section call has no defence against the literal user text in `section.description`.

2. **Estimate drift (Sec 5, 9, 11):** outline LLM guessed paragraph counts that didn't match what the section LLM produced. By Sec 5 the cumulative drift was 6 (jumped to 23, expected ~17); by Sec 9 it was 8 more (jumped 51→59); by Sec 11 it collided with Sec 10.

**Why no fallback caught it:** there is no post-assembly renumbering pass. Once sections are assembled in [drafting.py:3941-3960](../agents/drafting.py#L3941-L3960), the numbering is final.

**Fix:** add a global renumber pass after `_assemble_document`. Walk substantive sections in order, count actual `^\d+\.` paragraphs per section, rewrite numbers globally. Procedural sections (Verification, Schedule, Court Fee, List of Documents) keep their own local schemes — `_is_procedural` keyword check at [drafting.py:3492-3494](../agents/drafting.py#L3492-L3494) is the right gate.

---

## Defect 4 — Duplicate `## 4.` heading rendering

**Observed in PDF page 6:**

```
## 4. Systematic Manipulation of Records and Continued Failure to Initiate Criminal Action

  4.

## Systematic Manipulation of Records and Continued Failure to Initiate Criminal Action

  1. The Petitioner submits...
```

The title appears twice with a stray `4.` between them.

**Root cause:** [drafting.py:3946-3958](../agents/drafting.py#L3946-L3958)

```python
for i, (section_plan, section_text) in enumerate(zip(outline.sections, sections)):
    text = section_text.strip()
    if not text.startswith("#"):
        num = localize_number(i + 1, user_language)
        clean_title = strip_leading_numeric_prefix(section_plan.title)
        text = f"## {num}. {clean_title}\n\n{text}"
    parts.append(text)
```

The assembler injects `## N. Title` **only if** the section LLM's output doesn't already start with `#`. The section prompt at [drafting.py:3347](../agents/drafting.py#L3347) literally includes `"## {section_title}\n{section_desc}\n\n"` as part of the instructions to the LLM:

```python
("user",
 "NOW WRITE section {section_num} of {total} IN FULL DETAIL.\n"
 "{facts_reminder}\n\n"
 "## {section_title}\n{section_desc}\n\n"
 "PARAGRAPH NUMBERING — apply ONE of these schemes..."
)
```

The LLM in Section 4 echoed this `##` line in its body output — so `text.startswith("#")` was true, the assembler skipped its own prefix injection, AND the LLM emitted a separate `4.` line of its own. End result: two title renderings.

**Fix:** either
- Remove the `## {section_title}` echo from the section prompt and instruct "DO NOT emit your own heading; the assembler will add it" — kill the duplicate at the source
- OR strip leading `##.*` lines from `section_text` before the `startswith("#")` check, always inject the canonical heading

The second is safer (defence-in-depth against any future prompt variant).

---

## Defect 5 — Empty paragraph 69 in Section 10

**Observed:** Section 10 numbered paragraphs are 66, 67, 68, **69 (empty)**, 70, 71, 72, 73, 74.

**Root cause:** Section-internal LLM truncation or skip. The section completed (didn't trigger the failure handler at [drafting.py:3552-3555](../agents/drafting.py#L3552-L3555)), but produced a 9-paragraph block where one paragraph was emitted as bare `69.` with no body.

There is no per-paragraph validation inside a section — only section-level success/failure tracking. The validator pass at [drafting.py:1824-1830](../agents/drafting.py#L1824-L1830) checks for mojibake, `[CITE:...]` placeholders, and orphan citation tails, but not empty numbered paragraphs.

**Fix:** add a check to `validate_draft` for orphan numbered lines (`^\d+\.\s*$`) — log a warning, optionally renumber to close the gap. Cheap and additive.

---

## Defect 6 — Same 4 Supreme Court cases repeated across all 15 sections

**Observed:** every section cites some combination of:
- _State of U.P. v. Raja Ram Jaiswal, AIR 1985 SC 1308_
- _Union of India v. S.B. Vohra, (2004) 2 SCC 150_
- _Common Cause v. Union of India, (1996) 1 SCC 753_
- _P.R. Murlidharan v. Swami Dharmananda Theertha Padar, (2006) 4 SCC 501_

For a writ that touches Articles 14 / 19(1)(a) / 300A, Maharashtra Land Revenue Code 1966, Public Records Act 2005, RTI Act 2005, BNS 2023, and discusses fraud, mutation, Vahiwat entries, suo motu proceedings, and contempt — citing the same 4 mandamus-generic cases across all 15 sections is shallow.

**Root cause:** the doctrinal stance ([drafting.py:2125-2167](../agents/drafting.py#L2125-L2167)) generates 3–6 anchor cases ONCE for the whole draft. Every parallel section call is told:

> "Yes — cite ONLY the cases listed in the DOCTRINAL STANCE block above under 'USE THESE CASE LAWS' (each is corpus-verified). If the stance lists no case relevant to this paragraph's point, cite the doctrine WITHOUT a case label" ([drafting.py:3419-3427](../agents/drafting.py#L3419-L3427))

So the stance LLM's 4-case shortlist becomes the entire authority pool for a 15-section document. The user's specific facts (Mutation Entry 2562/1986, RTS/REVISION/PUNE/632/2022, named officials, Section 10 Maharashtra Public Records Act, Section 318 BNS) ARE in the body — but they're not anchored to authority that matches each section's specific argument.

**Why this design exists:** the stance was added to prevent **contradictions** between parallel sections (e.g. one section calling property "self-acquired" while another calls it "ancestral") — `_generate_doctrinal_stance` in CLAUDE.md Drafting invariant #2. The unintended consequence is monotone case-law citation.

**Fix options:**
1. **Per-section case-law fan-out:** after stance is generated, run a cheap per-section call that picks 1–2 *additional* cases relevant to that section's specific argument from the Judgment ES index. Cost: 15 extra ES queries + 15 short Gemini Flash Lite calls. Adds ~5-10s latency for parallel queries.
2. **Expand stance pool:** instead of 3–6 cases, ask the stance LLM for 12–20 cases tagged by sub-topic (records-manipulation, mandamus-compelling-statutory-duty, Article-300A, Vahiwat-entries, contempt-of-court, etc.). Section prompt then picks the tagged subset.
3. **Cite the doctrine without a label** (the existing fallback in [drafting.py:3422-3424](../agents/drafting.py#L3422-L3424)) instead of the same 4 cases — but this is a regression in citation density.

Option 2 is the lowest-cost win.

---

## Defect 7 — Section 8 generated 6 paragraphs when user asked for 13-15

**Observed:** User prompt: `"PART G: CONTUMACIOUS ABUSE OF POWER: THE SUO MOTU PROCEEDINGS OF 2015 PARAGRAPH DESCRIPTIVE 13 to 15"`. Section 8 came back with paragraphs 45–50 = **6 paragraphs**, less than half.

**Root cause:** the outline LLM's `estimated_paragraphs` field for the SectionPlan didn't pick up the user's "13 to 15" request. Likely because:
- The user's prompt is in compressed all-caps notation ("PARAGRAPH DESCRIPTIVE 13 to 15") which the outline LLM may have parsed as a generic instruction rather than a hard count
- The outline prompt at [config/prompts.py:1368-1376](../config/prompts.py#L1368-L1376) suggests 4–8 paragraphs for "Grounds / Arguments" sections — the outline LLM may have defaulted to ~6 because the section maps to a "Contumacious Abuse" argument

**Fix:** when the user's query contains explicit per-section paragraph counts (regex or LLM extraction of "PART X: ... PARAGRAPH DESCRIPTIVE N to M"), inject those counts into the outline's `estimated_paragraphs` field. Cheap pre-processing step.

---

## Defect 8 — User's compressed skeleton notation not understood by outline LLM

**User prompt format (excerpt):**

```
PART G: CONTUMACIOUS ABUSE OF POWER: THE SUO MOTU PROCEEDINGS OF 2015 PARAGRAPH DESCRIPTIVE 13 to 15
PART H: MISUSE OF FRAUDULENT ENTRIES IN CIVIL COURT PROCEEDINGS PARAGRAPH DESCRIPTIVE 7 TO 13
PART I: PARALLEL REVENUE APPEALS PERPETUATING THE ILLEGALITY PARAGRAPH DESCRIPTIVE 7 TO 13
...
GROUNDS PARAGRAPH DESCRIPTIVE 40 TO 50
PRAYER
```

This is a structured outline the user has hand-authored, telling the system exactly what headings and paragraph counts they want. The drafting pipeline treats this as free-form prose — the outline LLM imagines its OWN sections instead of mirroring the user's enumerated Parts A–O. The fact that the response heading set DOES match Parts A–O reasonably well suggests the outline LLM partially understood — but it lost PRAYER, dropped procedural blocks, and didn't honour the paragraph counts.

**Fix:** add a pre-outline step that detects user-enumerated structures (Part A/B/C…, numbered headings, "PARAGRAPH N to M" notation, explicit "GROUNDS"/"PRAYER" markers) and converts them into a structured `UserOutlineHint` that the outline LLM is required to honour verbatim. This is the difference between "outline LLM uses creativity" and "outline LLM is constrained by user's explicit demands."

---

## Recommended fix priority

| # | Fix | Effort | Impact | Files |
|---|---|---|---|---|
| 1 | Raise `MAX_FINAL_RESPONSE_CHARS` to 250_000 OR exempt Drafting from this cap | 1-line | Kills visible truncation banner | [agents/guardrail.py:38](../agents/guardrail.py#L38) |
| 2 | Raise `_MAX_SECTIONS` to 24–28 (or split substantive / procedural) | 1-line | GROUNDS + PRAYER + procedural blocks survive | [agents/drafting.py:58](../agents/drafting.py#L58) |
| 3 | Strip leading `##` lines from `section_text` before assembler's `startswith("#")` check; always inject canonical heading | 5-line | Kills duplicate-heading class | [agents/drafting.py:3946-3958](../agents/drafting.py#L3946-L3958) |
| 4 | Post-assembly global renumber pass (walks substantive sections, counts actual `^\d+\.` paragraphs, rewrites numbers) | ~30-line | Kills all paragraph-numbering chaos | [agents/drafting.py:3900-3980](../agents/drafting.py#L3900-L3980) |
| 5 | Detect empty `^\d+\.\s*$` lines in `validate_draft`, log warning | 5-line | Catches Sec 10 para 69 class | [agents/drafting.py:1791-1830](../agents/drafting.py#L1791-L1830) |
| 6 | Pre-outline parser for user-enumerated structures ("PART X…", "PARAGRAPH N TO M", "GROUNDS", "PRAYER") → typed `UserOutlineHint` | ~80-line | User's explicit skeleton is honoured verbatim | new pre-step in [agents/drafting.py](../agents/drafting.py) before `_generate_outline` |
| 7 | Expand doctrinal stance to 12–20 cases tagged by sub-topic; section prompt picks tagged subset | ~50-line | Citation diversity across sections | [agents/drafting.py:2125-2167](../agents/drafting.py#L2125-L2167) + [drafting.py:3419-3427](../agents/drafting.py#L3419-L3427) |

Fixes 1–3 are 1-day; 4–5 are 2-day; 6–7 are week-scale. Fixes 1+2 alone would let the user re-run and see the full 17-section draft with PRAYER + procedural blocks present — a substantial qualitative jump even before numbering and citation diversity are fixed.

---

## Out of scope for this report (but related)

- **Streaming behaviour**: the user said "at the end it got truncated" — this is the SSE final-response payload, not a mid-stream interruption. The streamed tokens during generation were likely fine; the visible truncation appears on the final `final_response` event after the guardrail post-processes the assembled draft.
- **PDF upload integration**: pipeline does read uploaded PDFs into `user_facts` (capped 30 K chars). The user said "ATTCHED PDF READ AND THEN MAKE" but did not appear to actually attach a separate evidence PDF in this run — the typed `user_prompt.txt` was self-contained as the skeleton. If the user does have evidence PDFs to incorporate, that's a separate workflow involving `/pyapi/chat` with multipart upload.
- **Web template fallback**: not exercised in this run; the writ matched a corpus template successfully.
