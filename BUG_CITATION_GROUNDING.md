# P0 — Citation grounding: drafts cite cases the pipeline never retrieved

**Filed:** 3 Sep 2026 · **Status:** open, own branch · **Estimate:** ~1 week
**Do NOT enable on `fix/regional-language-draft-depth`** — see "Why this is not a quick fix".

---

## The finding

A drafting request that asked for supporting case law produced **7 case citations. Zero appeared anywhere in what the pipeline retrieved.** The Supreme Court tool had returned *"No matching judgments found"*, so every citation came from model memory.

```
NOT FOUND  Dataram Singh v. State of Uttar Pradesh & Anr.
NOT FOUND  Hridaya Ranjan Prasad Verma v. State of Bihar
NOT FOUND  Sanjay Chandra v. CBI
NOT FOUND  P. Chidambaram v. Directorate of Enforcement
NOT FOUND  Parag Kishore Satoskar v. State of Jharkhand
NOT FOUND  Shailesh Kumar Singh v. State of Uttar Pradesh
NOT FOUND  Jaipur v. Balchand alias Baliay

grounded in retrieval: 0    NOT in retrieval: 7
retrieval IDs leaked into the draft: 2  ['DB ID: 46429', 'DB ID: 45087']
```

Two carried **invented retrieval identifiers** and one was **dated in the future** (`decided on 12-08-2026`). A `DB ID` is the system's own row identifier — its presence tells a reader the case was looked up and verified. Here it was manufactured.

Some of these are real, well-known bail authorities (*Sanjay Chandra*, *Dataram Singh*). That does not rescue it: a citation carrying invented provenance is unverifiable in a document filed before a judge.

## Why the guard never fired

`self_refine`'s `unretrieved_citation` category exists precisely for this. It never ran on a single drafting request, because it is skipped when the caller passes no `source_registry`, and drafting never passed one:

```
"(none — the caller passed no source registry; skip the
  `unretrieved_citation` category for this call)"
```

Same root shape as the refiner language bug: **a rule referencing data its stage never receives.**

---

## Shipped already (partial mitigation)

`core/fabricated_provenance.py`, wired into `validate_draft`. Scoped to signals with **no legitimate use in a filing**, so flagging them cannot produce a false positive:

| Signal | Action |
|---|---|
| Retrieval identifiers (`DB ID: 46429`, `doc_id:`, `RECORD ID`) | **stripped** |
| Placeholder markers (`[citation needed]`, `[verify]`, `[TBD]`) | **stripped** |
| Links to non-court domains | **stripped** |
| Judgment dated after today | **reported, NOT deleted** + banner in the draft |

Future dates are reported rather than stripped deliberately: deleting only the date would leave a fabricated authority looking clean, which is the opposite of the goal. The advocate sees a banner instead.

Real case numbers, reporter citations (`(2012) 1 SCC 40`, `AIR 2012 SC 830`, `Crl.A. No.-003803`) and court/government URLs are explicitly preserved. 23 tests.

**This does not decide whether a case is real.** That is the work below.

---

## Why this is not a quick fix

The obvious move — populate the whitelist and switch the rule on — is dangerous, and a first attempt proved it twice:

1. **Chunk-level section numbers.** The registry initially whitelisted `Section 1, BNS` / `Section 10, IPC` — whichever chunk matched, not the section the draft concerns. The rule flags any *"quoted statutory provision"* not present, so a draft correctly citing the user's own Section 316(2) would be reported as hallucinated and the refiner would strip it. Statutes now enter at Act level only.
2. **The SCI tool returns a pre-formatted string**, not structured hits, so retrieved Supreme Court cases are absent from the whitelist entirely. Enabling the rule now would flag **legitimately retrieved** authorities as fabricated.

Either failure ends with the refiner **deleting correct case law from a filing**. That is worse than the bug being fixed, and it is exactly how the doc-type flip worked: a wrong signal the refiner faithfully acts on.

---

## Requirements before enabling

1. **Pin the SCI tool's output format.** Document it. Write a parser with tests. Today it returns a JSON-ish string that is `"No matching judgments found."` on a miss — the contract is unspecified.
2. **Build the whitelist parser** — SCI + HC + statutes into `SourceRegistry`, with the Act-level rule for statutes already established.
3. **Verify against 20 real drafting requests.** For each: citations in the whitelist, citations in the draft, and how often the whitelist correctly covers the draft's citations. The number that matters is the **false-positive rate** — legitimate citations the whitelist fails to cover.
4. **Only then** enable the refiner's citation-stripping action.
5. **First 100 production drafts with it on go to human review** before the flag is trusted. "Looks right in tests" and "safe on production traffic" are different questions, and this is the class of failure where they diverge.

---

## Then: prevention at generation time

Once (a) runs clean, make it two-layer:

- **Writer prompt** — "cite only cases from the retrieved sources; if none are relevant, cite no cases."
- **Code check** — intersect draft citations with `source_registry` before shipping.

Same principle as the script gate: **measurable properties get code checks, not prompt rules.** Prevent at write time, catch at critique time. Either layer alone is fragile.

---

## Related

- `BUG2_EXPLAINED.md` — the refiner language directive; same "data without instruction" root shape.
- CLAUDE.md drafting invariant 2 — records why script and section-plan checks are deterministic rather than critic rules.
