# Malayalam drafts are consistently ~57% of English depth

**Filed:** 2 Sep 2026 · **Status:** open, not investigated
**Explicitly NOT a blocker for the bug-2 merge** — unrelated to the depth collapse, the off-target language failure, or the section-plan deviation, all of which are fixed.

---

## The observation

Across a 14-language × 3-run harness pass, Malayalam was the only language that stayed materially short of English:

| lang | words (median) | vs English | sections |
|---|---|---|---|
| en | 1189 | 100% | 9 |
| **ml** | **675** | **57%** | 8 |
| *next lowest (te)* | *972* | *82%* | *7* |

Every other language landed between 82% and 183%. Malayalam sits 25 points below the next lowest.

It is **consistent, not noise** — 621 / 675 / 747 words across three runs, in a metric whose English baseline swings ±74%. Three samples clustering that tightly at the bottom is the opposite of the variance pattern seen everywhere else.

## What it is NOT

- **Not a structural failure.** All 8 sections present: cause title, application heading, കേസിന്റെ വസ്തുതകൾ (facts), ജാമ്യത്തിനുള്ള കാരണങ്ങൾ (grounds), സഹപ്രതികളുമായുള്ള തുല്യത (parity), നൽകുന്ന ഉറപ്പുകൾ (undertakings), പ്രാർത്ഥന (prayer), സ്ഥിരീകരണം (verification).
- **Not off-target language.** Script ratio 0.97–0.98, among the cleanest in the set.
- **Not the planner.** The plan is correct and matches the other languages.
- **Not review.** Self-refine was measured changing 0 characters on the language paths.

It is the right document, with the right sections, written tersely. An advocate would need to expand it, not rebuild it.

## Hypotheses, untested

1. **Tokenizer economics.** Malayalam is highly agglutinative — a single orthographic word can carry what English spreads over five or six. Word count may be undercounting rather than the draft genuinely being thin. **Test:** compare character counts and, better, count *clauses* or numbered assertions rather than whitespace-delimited words.
2. **Training-data representation.** Malayalam is among the lower-resource of the 13, and terser output in lower-resource languages is a documented property ([LILT](https://lilt.com/blog/multilingual-llm-performance-gap-analysis), [arXiv 2506.03051](https://arxiv.org/html/2506.03051v1)).
3. **The per-section depth anchor is landing weakly for ml.** The anchor moved regional output 61% → 80% in aggregate; Malayalam may simply be the language it helps least.

Hypothesis 1 should be tested first — it is cheap, and if word count is the wrong unit for Malayalam then there may be no defect here at all.

## How to reproduce

```bash
python tests/multilingual_depth_harness.py 5000 drafts ml ml ml en en en
python tests/multilingual_depth_harness.py --score drafts
```

## Suggested first step

Do not tune the prompt. **Establish whether the metric is valid for Malayalam first** — the paragraph-count metric in this same investigation was demoted after it turned out to carry ±74% noise, and a second metric hole (heading counting) misread three languages by up to 7×. Both were caught by validating the measurement before acting on it. The same discipline applies here.

If clause counts and character counts show Malayalam at parity, close this as a measurement artefact. If they confirm ~57%, then investigate the depth anchor for this language specifically.
