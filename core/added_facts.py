"""Take out facts a draft adds that the client's own account does not state.

WHY THIS EXISTS
---------------
Lawyer test, 2026-11-10: a typed Gujarati police complaint was uploaded with
"draft a private complaint before the suitable court". The draft called the
complainant "illiterate, poor and rustic", had the accused come "accompanied
by their musclemen" and "hurl filthy abuses", had him sign "without
permitting him to read the text", and called a litigant "the Complainant's
wife". The file says none of it. A complaint is verified on oath, so every
added fact is a false statement the client swears to.

Typed facts get the same treatment. Local runs, 2026-11-11:
  * a bail application for "the sole earning member of his family" gained
    "aged and ailing parents wholly dependent on him";
  * a divorce petition about beating gained hair-pulling, slaps and kicks,
    the husband's family as co-abusers, and "no child was born".

What did not hold:
  * the NO ADDED ALLEGATIONS rule in both drafting prompts (#156) reduced it
    but did not stop it;
  * the self-refine critic, given the source text, passed "filthy abuse" and
    flagged formatting instead. So this is a check of its own, recorded as an
    exception under CLAUDE.md drafting invariant 2.

HOW IT WORKS
------------
1. Find. Flash calls, one per ~8K-char part of the draft and in parallel,
   read the client's account and that part. Each lists every added detail
   with the sentence it sits in and that sentence with only the detail
   taken out.
2. Confirm. Two Flash calls, with the items in opposite orders, quote the
   words of the account closest to each detail and say whether the account
   supports it. A detail goes only when BOTH say it is unsupported. So a
   true fact the finder misread stays, for example "at gunpoint" written
   with an OCR misspelling in a Gujarati file. Where a confirmed detail's
   words recur elsewhere in the draft, each place is confirmed again in
   its own sentence: the same words in a licence clause are a term, and in
   the complainant's verification his address is true.
3. Apply. Code, not the model, edits the draft. Only the words the
   correction drops are removed, word by word, so bold markers, numbering
   and the rest of the sentence are untouched. A correction that brings in
   words of its own is refused, except a [placeholder].

The details taken out are listed in a short note at the end of the draft,
so the lawyer can put one back when the client confirms it.

Fails closed (CLAUDE.md "Verifier fail-closed discipline"). If a call fails
or times out, the draft ships unchanged, the request is marked unverified,
and the failure is counted.

ADDED_FACTS_CHECK=on (default) | shadow (find and log, change nothing) | off.
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from pydantic import BaseModel, Field

from core.logger import get_logger

log = get_logger("AddedFacts")

# The account is sent whole. A 250-page bundle would cost a large prompt on
# every call, and such drafts are not where invented colour shows up; skip.
MAX_SOURCE_CHARS = 400_000
# Wall-clock ceiling for the whole check (the finder parts in parallel, then
# the two confirms in parallel). Measured 5-25 s on 15-70K-char drafts with
# Gemini Flash.
LOCAL_TIMEOUT_S = 60.0
# The finder reads the draft in parts of about this size, in parallel. One
# pass over a 36K-char complaint missed "hurled filthy abuses" three times
# over (live run, 2026-11-11); a part is short enough to be read closely.
PART_CHARS = 8000
# A run of edits that would cut more than this share of the draft (and more
# than 600 characters) means something went wrong, so nothing is applied.
# Not lower: a complaint drafted from four typed sentences can carry a
# quarter of invented narrative, every piece confirmed unsupported twice
# (live run 2026-11-11: 13% on one such draft).
MAX_SHRINK = 0.40
NOTE_MARKER = "**Details taken out**"
_NOTE_MAX_LINES = 6


def mode() -> str:
    m = (os.getenv("ADDED_FACTS_CHECK") or "on").strip().lower()
    return m if m in ("on", "shadow", "off") else "on"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

FIND_PROMPT = """You compare a legal draft with the client's own account of the matter.

CLIENT'S ACCOUNT. This is the only source of facts: the uploaded documents, pasted text and the user's own messages. It may be in Gujarati, Hindi, Marathi or another language.
<<<
{source}
>>>

DRAFT (this may be one part of a longer draft):
<<<
{draft}
>>>

Find every detail in the DRAFT that states a fact about the people or events of THIS matter that the CLIENT'S ACCOUNT does not state.

Details to catch:
- a person's education or literacy, age, health, wealth or poverty, caste, religion, occupation, character or reputation;
- a relationship between people (wife, brother, son, partner, employee, friend);
- words someone spoke: abuse, insults, the wording of a threat;
- acts and events: visits, assaults, force, weapons, "musclemen" or companions, stalking, trespass, being taken somewhere;
- how or where something happened: manner, time, place, who was present, "without letting him read it";
- family members, dependants or children; witnesses, documents, payments, dates and amounts.

A NEW act is an added detail even when it sits next to one the account states: "threatened him" does not cover "hurled filthy abuse at him", "assaulted him", "came with musclemen" or "in full public view".

Do NOT report:
- a restatement, summary or translation of what the account says, even when the draft words the same act in legal terms ("cheated", "criminally intimidated", "fraudulently", "under threat");
- legal characterisations, conclusions and arguments ("these acts amount to criminal intimidation"). A fact stated inside a ground or argument (who did or said what, where, with whom) is still a fact: check it like any other;
- statutes, case law and what courts held in other cases;
- the court, jurisdiction, limitation, cause of action, valuation and other procedural statements;
- standard averments every such filing makes (the accused is liable, no other complaint is pending on the same cause, the applicant will abide by conditions);
- terms, clauses and obligations the draft proposes in an agreement, deed, notice or undertaking;
- prayers, the verification, signature and advocate blocks, and the draft's own date and place;
- lists of documents, annexures or witnesses to be filed or summoned;
- [bracketed placeholders].

Read the account carefully before you report anything: it may be OCR text with spelling mistakes, and the same fact may be stated in another language or other words.

Give one item per added detail:
- sentence: the draft sentence it sits in, copied EXACTLY, including any ** markers;
- added: the words that state the detail, copied exactly from the sentence. Only the added words: leave out any name, place or act in the sentence that the account does state;
- account_says: what the account says on that point, or "nothing";
- corrected: the same sentence with only this detail taken out. Keep every other word exactly as it is and add no words of your own, except a [placeholder] where the sentence cannot stand without the detail. A placeholder is a label of at most five words naming what is missing, such as [Occupation] or [details of the threats]; it never states a fact. Use "" when the whole sentence is the detail, unless it is the whole of a numbered paragraph: then keep the number and put a placeholder, such as "4. [details of the representations]".

When one sentence adds several details, give an item for each, each with the same sentence and a corrected version that takes out only that detail. Report every place an added detail appears, including repeats. Return an empty list when the draft adds nothing."""

CONFIRM_PROMPT = """CLIENT'S ACCOUNT (uploaded documents, pasted text and the user's own messages; it may be in Gujarati, Hindi, Marathi or another language):
<<<
{source}
>>>

A legal draft states each DETAIL below. For each one decide whether the CLIENT'S ACCOUNT supports it.

It is supported when the account states it, says the same thing in other words or another language, or when it follows necessarily from what the account states (for example "his wife" when the client writes "my wife"; "2016" when the account gives a date in 2016).
It is NOT supported when the account says nothing about it, or only something weaker or different (the account says "under threat" and the draft adds "with musclemen"). Check that the account says it about the SAME person or event as the draft sentence: when the account calls one woman his wife, that does not make another woman his wife. A shared surname, or a father's or husband's name inside a person's name, does not establish a relationship.
Judge what the DETAIL claims, not its incidental words: when the account states that claim (the draft calls the accused "a prominent financier operating Sai Kripa Finance" and the account says he runs Sai Kripa Finance), answer supported=true; when it does not, a place or name from the account inside the detail does not make it supported ("lodged at Central Jail, Bhopal" when the account only names a Bhopal police station).
A term, condition, permission or obligation the draft proposes (in an agreement, notice or undertaking: who may use the premises, what the licensee must pay) is not a claim about what happened: answer supported=true.

The account may be OCR text with spelling mistakes; read it in full before you answer.

For every item answer, in this order: n (its number); evidence: the words of the account that come closest to the detail, copied in its own language, or "" when it says nothing on the point; supported: true or false.

ITEMS:
{items}"""


class _Item(BaseModel):
    sentence: str = Field(description="the draft sentence, copied exactly")
    added: str = Field(description="the words that state the added detail")
    account_says: str = Field(description="what the account says on this point, or 'nothing'")
    corrected: str = Field(description="the sentence with only this detail taken out; '' to delete it")


class _Found(BaseModel):
    items: list[_Item] = Field(default_factory=list)


class _Verdict(BaseModel):
    n: int
    evidence: str = Field(description="the closest words of the account, copied, or ''")
    supported: bool


class _Verdicts(BaseModel):
    verdicts: list[_Verdict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    draft: str
    removed: list[str] = field(default_factory=list)   # the `added` text of each detail taken out
    found: int = 0           # details the finder listed
    confirmed: int = 0       # of those, unsupported in both confirm passes
    applied: int = 0         # of those, edits that landed in the draft
    status: str = "skipped"  # skipped | clean | edited | shadow | unverified | refused


# ---------------------------------------------------------------------------
# Source text
# ---------------------------------------------------------------------------

def _message_role(msg) -> str:
    if isinstance(msg, dict):
        return (msg.get("role") or msg.get("type") or "").lower()
    return (getattr(msg, "type", "") or "").lower()


def _message_text(msg) -> str:
    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
    if isinstance(content, list):   # LangChain content blocks
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return (content or "").strip()


def build_source(user_facts: str, user_query: str, chat_history=None,
                 summary_text: str = "") -> str:
    """Everything the client or the user told us: uploads, pasted text,
    integration context (all already in `user_facts`), the user's earlier
    messages, the summary of older turns, and this request.

    The planner's rewritten query is NOT a source: it has invented facts
    before ("cheating" for an NDPS upload, 2026-11-02). Earlier AI answers
    are not either, since they may carry the very additions this removes.
    """
    parts: list[str] = []
    if (user_facts or "").strip():
        parts.append(user_facts.strip())
    if (summary_text or "").strip():
        parts.append(f"[Summary of the earlier conversation]\n{summary_text.strip()}")
    for msg in chat_history or []:
        if _message_role(msg) not in ("human", "user"):
            continue
        text = _message_text(msg)
        if text and text != "Previous summary:":
            parts.append(f"[Earlier message from the user]\n{text}")
    if (user_query or "").strip():
        parts.append(f"[The user's request]\n{user_query.strip()}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Applying corrections: word-level, in code
# ---------------------------------------------------------------------------

# Letters and digits of every script the product writes, with Indic and
# Arabic combining marks (a Gujarati matra is not \w in Python's re).
_WORD = r"\w\u0900-\u0dff\u0e00-\u0e7f\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed"
_TOKEN_RE = re.compile(rf"[{_WORD}]+|[^\s{_WORD}*#>|~`]")
_WORD_TOKEN_RE = re.compile(rf"[{_WORD}]")
_FOLD = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
                       "\u2013": "-", "\u2014": "-"})
_STRUCTURE_RE = re.compile(r"[#>|]")
_ATTACH_LEFT = {".", ",", ";", ":", "!", "?", ")", "]"}
# Words a correction may bring in to keep the grammar ("an illiterate
# farmer" -> "a farmer"). None of them states a fact.
_GLUE = {"a", "an", "the", "and", "or", "of", "is", "are", "was", "were"}
# A cut that would leave one of these just before a full stop or comma is not
# made: the sentence would not read.
_DANGLING = {"a", "an", "the", "in", "at", "of", "to", "from", "by", "with", "on", "for",
             "under", "before", "into", "upon", "as", "than", "and", "or", "his", "her", "their"}
_SENTENCE_END = {".", "?", "!"}
# "4." or "(b)" left alone on its line.
_EMPTY_ITEM_RE = re.compile(r"(?m)^([ \t]*(?:\d{1,3}\.|\(?[a-z]\)|\(?[ivx]{1,5}\)))[ \t]*$")
_EMPTIED_PARAGRAPH = "[details to be confirmed by the client]"
# A list marker the finder put in front of a quoted sentence: "3. ", "(b) ".
_LEAD_MARKER_RE = re.compile(r"^[ \t]*(?:\*\*)?(?:\d{1,3}\.|\(?[a-z]\)|\(?[ivx]{1,5}\))(?:\*\*)?[ \t]+")
# A new [placeholder] of more words than this is a claim in brackets, not a
# label ("[the Accused unlawfully grabbed possession of the land]"); it is
# written as the standard placeholder instead.
_PLACEHOLDER_MAX_WORDS = 5


@dataclass
class _Tok:
    start: int
    end: int
    norm: str
    is_word: bool


def _tokens(text: str) -> list[_Tok]:
    return [_Tok(m.start(), m.end(), m.group().translate(_FOLD).casefold(),
                 bool(_WORD_TOKEN_RE.match(m.group())))
            for m in _TOKEN_RE.finditer(text)]


def _occurrences(toks: list[_Tok], sentence: str) -> list[tuple[int, int]]:
    """Token ranges [a, b) where `sentence` appears in the draft: an exact
    token match first, then one that ignores punctuation (the model drops or
    adds a comma when it copies)."""
    stoks = _tokens(sentence)
    want = [t.norm for t in stoks]
    if not want:
        return []
    have = [t.norm for t in toks]
    m = len(want)
    exact = [i for i in range(len(have) - m + 1)
             if have[i] == want[0] and have[i:i + m] == want]
    if exact:
        return [(i, i + m) for i in exact]
    want_w = [t.norm for t in stoks if t.is_word]
    k = len(want_w)
    if k < 4:
        return []
    words = [i for i, t in enumerate(toks) if t.is_word]
    have_w = [toks[i].norm for i in words]
    out = []
    for j in range(len(have_w) - k + 1):
        if have_w[j] == want_w[0] and have_w[j:j + k] == want_w:
            a, b = words[j], words[j + k - 1] + 1
            if stoks[-1].norm in (".", ")", "]") and b < len(toks) and toks[b].norm == stoks[-1].norm:
                b += 1
            out.append((a, b))
    return out


def _bracket_groups(toks: list[_Tok]) -> list[tuple[int, int]]:
    """Index ranges [i, j) of "[ ... ]" groups (no nesting)."""
    out, i = [], 0
    while i < len(toks):
        if toks[i].norm == "[":
            j = next((k for k in range(i + 1, min(len(toks), i + 40)) if toks[k].norm in ("]", "[")), None)
            if j is not None and toks[j].norm == "]":
                out.append((i, j + 1))
                i = j + 1
                continue
        i += 1
    return out


def _same_stem(word: str, have: set[str]) -> bool:
    """An inflected form of a word already in the sentence. Hindi and the
    other Indic languages change a verb's ending when a phrase before it is
    cut (मारपीट करता था -> मारता था); English ones are matched exactly."""
    if word.isascii() or len(word) < 4:
        return False
    return any(len(h) >= 4 and not h.isascii() and h[:3] == word[:3] for h in have)


def _plan(span: list[_Tok], corrected: str, added: str = "") -> tuple[set[int], dict[int, str]] | None:
    """Which span tokens the correction drops, and any [placeholder] it puts
    in their place, keyed by span index.

    None when the correction does more than take the detail out:
      * it brings in words of its own (a few grammar words, words already in
        the sentence and their inflected forms may move; a new [placeholder]
        may stand in for what was cut);
      * it deletes words beyond the detail the finder named in `added`.

    A new placeholder longer than a label ("[unlawfully grabbed possession
    of the land]") is a claim in brackets; it becomes the standard
    "[details to be confirmed by the client]".
    """
    ctoks = _tokens(corrected)
    # A placeholder the correction brings in is one unit, so the word inside
    # it ("[threats]") cannot pair up with the same word in the sentence.
    # One the sentence already had is left as words, so it pairs as equal.
    have_ph = {tuple(t.norm for t in span[i:j]) for i, j in _bracket_groups(span)}
    cunits: list[_Tok] = []
    generic: list[_Tok] = []     # placeholders written out as the standard one
    pos = 0
    for i, j in _bracket_groups(ctoks):
        cunits.extend(ctoks[pos:i])
        group = ctoks[i:j]
        if tuple(t.norm for t in group) in have_ph:
            cunits.extend(group)
        else:
            unit = _Tok(group[0].start, group[-1].end,
                        "\x00" + " ".join(t.norm for t in group), False)
            if sum(t.is_word for t in group) > _PLACEHOLDER_MAX_WORDS:
                generic.append(unit)
            cunits.append(unit)
        pos = j
    cunits.extend(ctoks[pos:])

    def written(run: list[_Tok]) -> str:
        lo, text = run[0].start, corrected[run[0].start:run[-1].end]
        for u in reversed([u for u in generic if u in run]):
            text = text[:u.start - lo] + _EMPTIED_PARAGRAPH + text[u.end - lo:]
        return text

    snorm = [t.norm for t in span]
    sset = set(snorm) | _GLUE
    deleted: set[int] = set()
    inserts: dict[int, str] = {}
    sm = SequenceMatcher(None, snorm, [t.norm for t in cunits], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        deleted.update(range(i1, i2))
        if j2 <= j1:
            continue
        run = cunits[j1:j2]
        # "[absconded]" for a cut "absconded from his whereabouts" keeps the
        # claim in brackets: a placeholder made only of cut words is dropped.
        cut_words = {span[i].norm for i in range(i1, i2) if span[i].is_word}
        run = [t for t in run if not (t.norm.startswith("\x00")
                                      and set(t.norm[1:].split()) - {"[", "]"} <= cut_words)]
        if not run:
            continue
        stray = [t.norm for t in run if t.is_word]
        if len(stray) > 3 or any(w not in sset and not _same_stem(w, sset) for w in stray):
            return None
        if stray or any(t.norm.startswith("\x00") for t in run):
            inserts[i1] = written(run)
    if added.strip():
        named = {t.norm for t in _tokens(added) if t.is_word}
        span_words = [t.norm for t in span if t.is_word]
        if not cunits:
            # The whole sentence goes: the named detail must be most of it.
            if span_words and sum(w in named for w in span_words) < 0.5 * len(span_words):
                return None
        else:
            extra = [span[i].norm for i in deleted
                     if span[i].is_word and span[i].norm not in named and span[i].norm not in _GLUE]
            if len(extra) > max(3, len(named) // 2):
                return None
    # A comma or bracket goes only with the words it belonged to. One the
    # correction merely left out (the quote was copied without it) stays.
    # Punctuation inside a cut ("Rs. 6,50,000/-") goes when the nearest token
    # on either side within the cut is a cut word.
    words_cut = {i for i in deleted if span[i].is_word}
    kept_cut: set[int] = set()
    for i in deleted:
        if span[i].is_word or not ctoks or i in inserts:
            kept_cut.add(i)
            continue
        lo, hi = i - 1, i + 1
        while lo in deleted and not span[lo].is_word:
            lo -= 1
        while hi in deleted and not span[hi].is_word:
            hi += 1
        if lo in words_cut or hi in words_cut:
            kept_cut.add(i)
    return kept_cut, inserts


_STARS_RE = re.compile(r"\*+")


def _join(draft: str, spans: list[tuple[int, int]], next_tok: _Tok | None,
          at_start: bool) -> str | None:
    """The text that stands in for a deleted run: what is left of its
    separators.

    `spans` locate the separator before the run, those inside it, and the
    one after it. Bold markers in them are kept or dropped by what they
    belong to: a pair opened and closed inside the run goes with it; a
    marker closing bold that began before the run stays on the left, and one
    opening bold that ends after it stays on the right. Which is which is
    read from the count of markers before it in the paragraph.

    None when the run cannot be cut cleanly: it crosses a heading, quote or
    table marker, or holds emphasis other than ** pairs.
    """
    seps = [draft[s:e] for s, e in spans]
    first, last, inner = seps[0], seps[-1], seps[1:-1]
    if any(_STRUCTURE_RE.search(s) for s in inner):
        return None
    marks: list[str] = []
    for s, e in spans:
        for m in _STARS_RE.finditer(draft, s, e):
            if len(m.group()) != 2:
                return None
            para = draft.rfind("\n\n", 0, m.start())
            before = draft.count("**", para + 1 if para >= 0 else 0, m.start())
            mark = "close" if before % 2 else "open"
            if mark == "close" and marks and marks[-1] == "open":
                marks.pop()          # bold wholly inside the run
            else:
                marks.append(mark)
    closers, openers = marks.count("close"), marks.count("open")
    if marks != ["close"] * closers + ["open"] * openers:
        return None
    if _STRUCTURE_RE.search(first) or _STRUCTURE_RE.search(last):
        # A "> " marker at the start of a line survives only when the run
        # starts that line and nothing else needs placing.
        if (not marks and "\n" in first and _STRUCTURE_RE.search(first)
                and not _STRUCTURE_RE.search(last) and "\n" not in last):
            return first.replace("*", "")
        return None
    widest = max(seps, key=lambda s: s.count("\n"))
    if next_tok is not None and next_tok.norm in _ATTACH_LEFT and "\n" not in first:
        # "Age: Adult,\n\nOccupation: Business,\n\nResiding" with the
        # occupation line cut: the comma closes "Adult", the break follows it.
        gap = ""
    elif "\n" in widest:
        gap = widest.replace("*", "")
    elif next_tok is None or next_tok.norm in _ATTACH_LEFT or at_start:
        gap = ""
    else:
        gap = " "
    return "**" * closers + gap + "**" * openers


def _repeat_sites(draft: str, toks: list[_Tok], cuts: list[set[int]]) -> list[tuple[int, int]]:
    """Other places where the words of a confirmed cut recur, as token ranges.

    The finder reports a detail where it notices it; a bail application said
    "aged and ailing" parents in eight sentences and the finder named one.
    A run of three or more words is looked for as it is; a shorter one
    ("aged" in "his aged mother") only with its neighbours, so "aged adult"
    in a verification is not even a candidate.

    Only descriptive words are looked for: lower-case English with no
    digits. A name, place, date or amount is true of one person and not of
    another, and Indic scripts have no case to tell a name by. Every site
    found is still sent back through the confirm passes in its own
    sentence (see `repeat_items`): the same words are not always the same
    claim. Carried over unchecked, a witness's unsupported "residing at
    <village>" took the complainant's address out of his own verification,
    and a recital's "and her family" narrowed a licence clause (local runs,
    2026-11-11).
    """
    def draft_text(t: _Tok) -> str:
        return draft[t.start:t.end]

    norms = [t.norm for t in toks]
    n = len(norms)
    first: dict[str, list[int]] = {}
    for i, w in enumerate(norms):
        first.setdefault(w, []).append(i)
    out: list[tuple[int, int]] = []
    for cut in cuts:
        run: list[int] = []
        for k in sorted(cut) + [None]:
            if k is not None and (not run or k == run[-1] + 1):
                run.append(k)
                continue
            if run:
                words = [norms[i] for i in run if toks[i].is_word]
                raw = [draft_text(toks[i]) for i in run if toks[i].is_word]
                descriptive = all(w.isascii() and w.isalpha() and w.islower() for w in raw)
                if words and descriptive and any(w not in _GLUE for w in words):
                    lo, hi = run[0], run[-1] + 1
                    if len(words) < 3:
                        lo, hi = lo - 1, hi + 1
                    if lo >= 0 and hi <= n:
                        pat = norms[lo:hi]
                        off = run[0] - lo
                        for j in first.get(pat[0], []):
                            site = (j + off, j + off + len(run))
                            if norms[j:j + len(pat)] == pat and site[0] != run[0] and site not in out:
                                out.append(site)
            run = [k] if k is not None else []
    return out


def _sentence_around(draft: str, toks: list[_Tok], s: int, e: int) -> tuple[int, int]:
    """Token range of the sentence (or list line) holding tokens [s, e)."""
    n = len(toks)

    def gap(k: int) -> str:              # text between token k-1 and token k
        return draft[toks[k - 1].end:toks[k].start]
    lo = s
    while lo > 0 and toks[lo - 1].norm not in _SENTENCE_END and "\n" not in gap(lo):
        lo -= 1
    hi = e
    while hi < n and toks[hi - 1].norm not in _SENTENCE_END and "\n" not in gap(hi):
        hi += 1
    return lo, hi


def repeat_items(draft: str, items: list) -> list:
    """Items for the other places a confirmed cut's words recur, each with
    its own sentence, so the confirm passes can judge it where it stands."""
    toks = _tokens(draft)
    cuts: list[set[int]] = []
    for it in items:
        sentence, corrected = it.sentence or "", it.corrected or ""
        occ = _occurrences(toks, sentence)
        lead = _LEAD_MARKER_RE.match(sentence)
        if not occ and lead:
            occ = _occurrences(toks, sentence[lead.end():])
            lead_c = _LEAD_MARKER_RE.match(corrected)
            corrected = corrected[lead_c.end():] if lead_c else corrected
        for a, b in occ:
            plan = _plan(toks[a:b], corrected, it.added or "")
            if plan:
                dels, ins = plan
                cuts.append({a + i for i in dels if i not in ins})
    covered = set().union(*cuts) if cuts else set()
    out, seen = [], set()
    for s, e in _repeat_sites(draft, toks, cuts):
        if all(i in covered for i in range(s, e)):
            continue
        lo, hi = _sentence_around(draft, toks, s, e)
        if (lo, hi, s, e) in seen:
            continue
        seen.add((lo, hi, s, e))
        sent = draft[toks[lo].start:toks[hi - 1].end]
        corr = draft[toks[lo].start:toks[s].start] + draft[toks[e - 1].end:toks[hi - 1].end]
        out.append(_Item(sentence=sent, added=draft[toks[s].start:toks[e - 1].end],
                         account_says="", corrected=corr))
    return out


def apply_corrections(draft: str, items: list) -> tuple[str, list]:
    """Apply the corrections in `items` (objects with .sentence, .corrected
    and .added) to `draft`, word by word. Returns (new draft, the items that
    landed). Every place a sentence appears is corrected."""
    toks = _tokens(draft)
    if not toks:
        return draft, []
    deleted: set[int] = set()
    inserts: dict[int, list[str]] = {}
    landed = []
    for it in items:
        sentence, corrected = it.sentence or "", it.corrected or ""
        occ = _occurrences(toks, sentence)
        lead = _LEAD_MARKER_RE.match(sentence)
        if not occ and lead:
            # The finder put the paragraph's number in front of a sentence
            # from the middle of that paragraph: match without it, on both.
            occ = _occurrences(toks, sentence[lead.end():])
            lead_c = _LEAD_MARKER_RE.match(corrected)
            corrected = corrected[lead_c.end():] if lead_c else corrected
        if not occ:
            log.info("Added detail: sentence not found in draft", added=(it.added or "")[:80])
            continue
        plans = [(a, _plan(toks[a:b], corrected, it.added or "")) for a, b in occ]
        if any(p is None for _, p in plans):
            log.info("Added detail: correction does more than take the detail out; refused",
                     added=(it.added or "")[:80], corrected=(it.corrected or "")[:120])
            continue
        hit = False
        for a, (dels, ins) in plans:
            if dels or ins:
                hit = True
            deleted.update(a + i for i in dels)
            for i, v in ins.items():
                if v not in inserts.get(a + i, []):
                    inserts.setdefault(a + i, []).append(v)
        if hit:
            landed.append(it)
    if not landed:
        return draft, []

    n = len(toks)
    # A paragraph number at the start of a line is never cut with the
    # sentence after it: the next sentence takes the number, or the empty
    # paragraph gets a placeholder below.
    for k in range(n - 1):
        line_start = k == 0 or "\n" in draft[toks[k - 1].end:toks[k].start]
        if (line_start and toks[k].norm.isdigit() and len(toks[k].norm) <= 3
                and toks[k + 1].norm == "." and toks[k + 1].start == toks[k].end):
            deleted.discard(k)
            deleted.discard(k + 1)
    # "3. <name>, residing at ..." with the name cut must not read "3., residing":
    # a cut that opens a sentence or line takes the comma after it too.
    runs: list[list[int]] = []
    for k in sorted(deleted):
        if runs and k == runs[-1][-1] + 1:
            runs[-1].append(k)
        else:
            runs.append([k])
    for run in runs:
        a, b = run[0], run[-1] + 1
        if b < n and b not in deleted and toks[b].norm in (",", ";"):
            if (a == 0 or toks[a - 1].norm in _SENTENCE_END
                    or "\n" in draft[toks[a - 1].end:toks[a].start]):
                deleted.add(b)

    def span(k: int) -> tuple[int, int]:   # the gap before token k; span(n) is the tail
        return (toks[k - 1].end if k > 0 else 0, toks[k].start if k < n else len(draft))

    def sep(k: int) -> str:
        lo, hi = span(k)
        return draft[lo:hi]

    def text(k: int) -> str:
        return draft[toks[k].start:toks[k].end]

    def ins_before(k: int) -> str:
        v = inserts.get(k)
        if not v or k in deleted:
            return ""
        return " ".join(v) + ("" if toks[k].norm in _ATTACH_LEFT else " ")

    out: list[str] = []
    last: int | None = None          # the last token written out
    tail_done = False
    k = 0
    while k < n:
        if k not in deleted:
            out.append(sep(k) + ins_before(k) + text(k))
            last, k = k, k + 1
            continue
        a = k
        while k < n and k in deleted:
            k += 1
        b = k
        seps = [sep(i) for i in range(a, b + 1)]
        nxt = toks[b] if b < n else None
        ins = [v for i in range(a, b) for v in inserts.get(i, [])]
        joined = _join(draft, [span(i) for i in range(a, b + 1)], nxt, at_start=(a == 0))
        if joined is not None and ins:
            if any("*" in s for s in seps):
                joined = None
            else:
                trail = seps[-1] if "\n" in seps[-1] else (
                    "" if nxt is None or nxt.norm in _ATTACH_LEFT else " ")
                joined = (seps[0] or ("" if a == 0 else " ")) + " ".join(ins) + trail
        if (joined is not None and not ins and last is not None and last == a - 1
                and toks[last].norm in _DANGLING
                and (nxt is None or nxt.norm in _ATTACH_LEFT or "\n" in joined)):
            joined = None            # "confined in [cut]." would read "confined in."
        if joined is None:           # cannot cut cleanly: keep the run as it was
            log.info("Added detail: run kept, markup in the way",
                     text=draft[toks[a].start:toks[b - 1].end][:80])
            out.extend(sep(i) + text(i) for i in range(a, b))
            last = b - 1
            continue
        if (not ins and last is not None and last == a - 1 and nxt is not None
                and toks[last].norm in (",", ";") and nxt.norm in _ATTACH_LEFT):
            out[-1] = out[-1][:-len(text(last))]     # "him, [cut]." -> "him."
        out.append(joined)
        if nxt is None:
            tail_done = True
            break
        word = text(b)
        if (not ins and word[:1].isascii() and word[:1].islower()
                and (last is None or toks[last].norm in _SENTENCE_END or "\n" in joined)):
            word = word[:1].upper() + word[1:]
        out.append(ins_before(b) + word)
        last, k = b, b + 1
    if not tail_done:
        out.append(sep(n))
    new = "".join(out)
    # Only what actually left the draft counts: a cut kept for markup or
    # grammar leaves its sentence as it was, and the note must not list it.
    new_toks = _tokens(new)

    def still_there(sentence: str) -> bool:
        lead = _LEAD_MARKER_RE.match(sentence)
        return bool(_occurrences(new_toks, sentence)
                    or (lead and _occurrences(new_toks, sentence[lead.end():])))
    landed = [it for it in landed if not still_there(it.sentence or "")]
    if not landed:
        return draft, []

    if new.count("**") % 2 != draft.count("**") % 2:
        log.warning("Added detail edits unbalanced bold markers; none applied")
        return draft, []
    if len(draft) - len(new) > max(len(draft) * MAX_SHRINK, 600):
        log.warning("Added detail edits would cut too much; none applied",
                    before=len(draft), after=len(new))
        return draft, []
    new = re.sub(r"[ \t]+\n", "\n", new)
    new = re.sub(r"\n{3,}", "\n\n", new)
    # A numbered paragraph the cuts emptied keeps its number, so the count
    # and any "para 7 above" stay right, and says what is missing.
    # validate_draft would otherwise drop the bare number and leave a gap.
    if not _EMPTY_ITEM_RE.search(draft):
        new = _EMPTY_ITEM_RE.sub(lambda m: f"{m.group(1)} {_EMPTIED_PARAGRAPH}", new)
    return new, landed


# ---------------------------------------------------------------------------
# The note
# ---------------------------------------------------------------------------

def removal_note(removed: list[str]) -> str:
    seen, lines = set(), []
    for r in removed:
        r = " ".join((r or "").split()).strip(" ,;.")
        key = r.casefold()
        if not r or key in seen:
            continue
        seen.add(key)
        lines.append(r)
    if not lines:
        return ""
    shown = [f'> - "{r[:140]}{"…" if len(r) > 140 else ""}"' for r in lines[:_NOTE_MAX_LINES]]
    more = len(lines) - len(shown)
    if more > 0:
        shown.append(f"> - and {more} more.")
    return (
        f"\n\n---\n\n> {NOTE_MARKER}: these were not in your documents or "
        "messages, so this draft leaves them out. Add one back only if your "
        "client confirms it.\n" + "\n".join(shown) + "\n"
    )


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def _listing(items: list[_Item], order: list[int]) -> str:
    return "\n".join(
        f'{i + 1}. DETAIL: "{items[i].added.strip()}"   '
        f'(draft sentence: "{" ".join(items[i].sentence.split())[:500]}")'
        for i in order)


async def _call(llm, model, prompt: str):
    """One structured call, retried once when the reply does not parse (a
    Hindi confirm pass came back as invalid JSON once in 8 live runs)."""
    for attempt in (1, 2):
        out = await llm.with_structured_output(model, include_raw=True).ainvoke(prompt)
        try:
            from core.token_tracker import record as _record_tokens
            _record_tokens("Drafting", f"added_facts_{model.__name__.strip('_').lower()}",
                           out.get("raw"))
        except Exception:
            pass
        parsed = out.get("parsed")
        if parsed is not None:
            return parsed
        log.info("Added-facts call did not parse", model=model.__name__, attempt=attempt,
                 error=str(out.get("parsing_error"))[:120])
    raise ValueError(f"unparsed {model.__name__}: {str(out.get('parsing_error'))[:120]}")


def _parts(draft: str, size: int = PART_CHARS) -> list[str]:
    """The draft cut at paragraph breaks into parts of about `size` chars."""
    parts, cur = [], ""
    for para in draft.split("\n\n"):
        if cur and len(cur) + len(para) + 2 > size:
            parts.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur.strip():
        parts.append(cur)
    return parts


async def _find_and_confirm(draft: str, source: str, llm) -> tuple[list[_Item], list[_Item]]:
    founds = await asyncio.gather(*[
        _call(llm, _Found, FIND_PROMPT.format(source=source, draft=part))
        for part in _parts(draft)])
    items = [it for f in founds for it in f.items
             if (it.sentence or "").strip() and (it.added or "").strip()]
    if not items:
        return [], []
    unsupported = await _confirm(items, source, llm)
    # The same words elsewhere in the draft are judged again in their own
    # sentence, and go only if that confirm also finds them unsupported.
    repeats = repeat_items(draft, unsupported) if unsupported else []
    if repeats:
        unsupported = unsupported + await _confirm(repeats, source, llm)
    return items, unsupported


async def _confirm(items: list[_Item], source: str, llm) -> list[_Item]:
    """The items both confirm passes find unsupported."""
    order = list(range(len(items)))
    a, b = await asyncio.gather(
        _call(llm, _Verdicts, CONFIRM_PROMPT.format(source=source, items=_listing(items, order))),
        _call(llm, _Verdicts, CONFIRM_PROMPT.format(source=source, items=_listing(items, order[::-1]))),
    )
    supported_a = {v.n for v in a.verdicts if v.supported}
    supported_b = {v.n for v in b.verdicts if v.supported}
    answered = {v.n for v in a.verdicts} & {v.n for v in b.verdicts}
    # Unsupported means: both passes answered for it, and neither supports it.
    return [it for i, it in enumerate(items, 1)
            if i in answered and i not in supported_a and i not in supported_b]


async def check_added_facts(draft: str, source: str, *, llm=None,
                            local_timeout: float = LOCAL_TIMEOUT_S) -> CheckResult:
    """Find details in `draft` that `source` does not state and take them out.

    Never raises. On any failure the draft comes back unchanged with
    status "unverified" and the request is marked unverified.
    """
    m = mode()
    if m == "off" or not (draft or "").strip():
        return CheckResult(draft=draft)
    if len(source or "") > MAX_SOURCE_CHARS:
        log.info("Added-facts check skipped: source too large", source_chars=len(source))
        return CheckResult(draft=draft)
    if llm is None:
        from core.clients import get_gemini_flash_full
        llm = get_gemini_flash_full(temperature=0.0, max_output_tokens=16384)
    try:
        from core.deadline import bounded_wait_for
        items, unsupported = await bounded_wait_for(
            _find_and_confirm(draft, source or "", llm), local_timeout=local_timeout)
    except Exception as e:
        from core.audit_status import mark_unverified
        from core.self_refine import _bump_verifier_failure
        reason = "timeout" if isinstance(e, asyncio.TimeoutError) else type(e).__name__
        mark_unverified()
        _bump_verifier_failure("added_facts", reason)
        log.warning("added_facts_check_unavailable", reason=reason, error=str(e)[:200])
        return CheckResult(draft=draft, status="unverified")

    res = CheckResult(draft=draft, found=len(items), confirmed=len(unsupported))
    if not unsupported:
        res.status = "clean"
        log.info("Added-facts check: nothing to take out", found=len(items))
        return res
    if m == "shadow":
        res.status = "shadow"
        log.info("Added-facts check (shadow): would take out", found=len(items),
                 confirmed=len(unsupported),
                 details=[it.added[:60] for it in unsupported][:12])
        return res
    new, landed = apply_corrections(draft, unsupported)
    res.applied = len(landed)
    if not landed:
        res.status = "refused"
        log.info("Added-facts check: no correction could be applied",
                 confirmed=len(unsupported))
        return res
    res.removed = [it.added for it in landed]
    res.draft = new.rstrip() + removal_note(res.removed)
    res.status = "edited"
    log.info("Added-facts check: details taken out", found=len(items),
             confirmed=len(unsupported), applied=len(landed),
             chars_before=len(draft), chars_after=len(new),
             details=[it.added[:60] for it in landed][:12])
    return res
