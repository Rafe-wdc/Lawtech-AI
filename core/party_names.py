"""Is a party name found in our judgment index the party a draft cites?

The same party is written many ways. Our Supreme Court index writes
"ADDL.COMNR.,TRIBAL DEV.THANE", "MAH.ADIWASI THAKUR JAMAT ...",
"STATE OF U.P.", "RAJASTHAN STATE ROAD TRPT. CORPN.", "B.S.E.S. LTD.",
"BHAIYALAL", and carries OCR slips ("VANKAMAMIDL"). Drafts and the judgments
citing them write "Additional Commissioner", "Maharashtra Adiwasi ...",
"State of Uttar Pradesh", "Kerala SRTC", "ONGC", "Bhaiya Lal".

Two words are taken as the same word when one is:
  - the other word, or its singular or plural;
  - an abbreviation of the other. It is either written with a full stop
    ("Addl.", "Mah.", "Trpt.", "Regult."), or a contraction with at most one
    vowel ("Govt", "Commr", "Secy");
  - the acronym or initials of consecutive words of the other name ("SRTC",
    "U.P.", "CIT", "UOI");
  - the same letters joined or split differently ("Bhaiya Lal" / "BHAIYALAL");
  - a spelling variant of a long name: one letter in 6 to 8 letters, two in
    9 or more. When a variant is the only kind of match on a side, it does
    not count.

`match_parties` then applies the side rules (see there). Nothing here is
specific to a court, a state or a case. The common-word list is measured from
the index (see `COMMON_WORDS`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_VOWELS = set("aeiou")

# Small words that join a name ("State of Punjab", "Oil and Natural Gas").
CONNECTORS = frozenset({"of", "and", "the", "for", "in", "at", "on", "to", "by"})

# Words that say nothing about which party it is: connectors, the "and others"
# tail, honorifics and titles, legal-representative tails, company suffixes,
# and the citing words a name can pick up ("See", "Per").
STOPWORDS = CONNECTORS | frozenset({
    "vs", "v", "versus", "anr", "ors", "another", "others", "etc",
    "dr", "mr", "mrs", "ms", "smt", "shri", "sri", "kumari", "km", "kum", "mst",
    "major", "maj", "capt", "captain", "col", "colonel", "lt", "brig", "prof",
    "justice", "hon", "ble", "hon'ble", "late", "dead", "deceased", "lrs", "lr",
    "thr", "through", "its", "retd", "ltd", "pvt", "private", "limited", "co",
    "inc", "see", "per", "vide", "also", "cf",
})

# Acronyms whose expansion does not spell them letter by letter.
ALIASES: dict[str, tuple[str, ...]] = {
    "cbi": ("central", "bureau", "investigation"),
    "ed": ("directorate", "enforcement"),
    "nia": ("national", "investigation", "agency"),
    "ncb": ("narcotics", "control", "bureau"),
    "uoi": ("union", "india"),
    "rbi": ("reserve", "bank", "india"),
    "sebi": ("securities", "exchange", "board", "india"),
    "lic": ("life", "insurance", "corporation", "india"),
    "dda": ("delhi", "development", "authority"),
}

# Words that appear in many party names: places, government bodies, offices
# and company words. Measured 2026-11-13 on 65,322 party names (33,008
# Supreme Court index entries): a word is common when it is in at least 0.4%
# of them. The frequent personal names on that list (singh, kumar, ram, lal,
# prasad, devi, sharma, chand, chandra, rao, gupta, prakash, jain, nath, raj,
# das, reddy, khan) are left out, so a person's name must always match.
# A respondent may lack some common words ("Commissioner of Income Tax,
# Bombay" / "COMMISSIONER OF INCOME TAX"). Every other word must match:
# "Meerut Development Authority" is not "Delhi Development Authority".
COMMON_WORDS: frozenset[str] = frozenset("""
state india union pradesh up tax commissioner delhi maharashtra punjab income
haryana bihar bank corporation central rajasthan gujarat karnataka board kerala
mp bengal west excise authority uttar officer madhya insurance development ap
bombay mills company nadu commnr industries collector national tamil secretary
orissa andhra corpn new municipal govt director police madras indian general nct
""".split())


@dataclass(frozen=True)
class Word:
    text: str                 # lower-case letters or digits
    dotted: bool = False      # written with a full stop after it: an abbreviation
    acronym: bool = False     # initials ("U.P.") or a short all-capitals word ("ONGC")
    optional: bool = False    # a person's initials before a name ("T.R. Balu")
    stop: bool = False        # carries no identity (see STOPWORDS)
    initials: bool = False    # built from single letters ("U.P.", "T. R.")


def words(text: str) -> list[Word]:
    """The words of a party name, in order, including the stop words (they are
    needed to read initials such as "Union of India" = "UOI")."""
    text = text or ""
    mixed = any(c.islower() for c in text) and any(c.isupper() for c in text)
    out: list[Word] = []
    run = ""
    for m in _TOKEN_RE.finditer(text):
        raw = m.group()
        low = raw.lower()
        if len(raw) == 1 and raw.isalpha():
            run += low                     # "U.P.", "B. S. E. S.", "T.R."
            continue
        if run:
            out.append(_initials(run))
            run = ""
        dotted = text[m.end():m.end() + 1] == "."
        if mixed:
            acronym = raw.isupper() and 2 <= len(raw) <= 6 and raw.isalpha()
        else:
            acronym = 2 <= len(raw) <= 5 and raw.isalpha() and sum(c in _VOWELS for c in low) <= 1
        stop = low in STOPWORDS or low.isdigit() or (len(low) < 3 and not acronym)
        out.append(Word(low, dotted=dotted, acronym=acronym and not stop, stop=stop))
    if run:
        out.append(_initials(run))
    # Initials followed by a name ("T.R. Balu", "M.C. Mehta") are a person's;
    # the other side may leave them out. Initials before a common word
    # ("U.P. State Road ...") or at the end ("State of U.P.") are required.
    for i, w in enumerate(out):
        if w.acronym and not w.stop and i + 1 < len(out):
            nxt = out[i + 1]
            if w.initials and len(w.text) <= 3 and not nxt.stop and not nxt.acronym \
                    and nxt.text not in COMMON_WORDS:
                out[i] = Word(w.text, acronym=True, optional=True, initials=True)
    return out


def _initials(run: str) -> Word:
    return Word(run, acronym=len(run) >= 2, stop=run in STOPWORDS or len(run) < 2,
                initials=True)


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("es") and w[-3] in "sxz":
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(c in it for c in short)


def _abbreviates(short: Word, long: Word) -> bool:
    s, ln = short.text, long.text
    if not (s.isalpha() and ln.isalpha()) or len(s) >= len(ln) or s[0] != ln[0]:
        return False
    if not _subsequence(s, ln):
        return False
    if short.dotted:
        return len(s) >= 2
    # A contraction written without a full stop: "govt", "commr", "addl".
    return len(s) >= 4 and s[-1] == ln[-1] and sum(c in _VOWELS for c in s) <= 1


def _distance(a: str, b: str, limit: int) -> int:
    """Edit distance counting a swap of neighbours as one edit; stops early."""
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def _variant(a: str, b: str) -> bool:
    if not (a.isalpha() and b.isalpha()) or a[0] != b[0]:
        return False
    n = min(len(a), len(b))
    if n < 6:
        return False
    limit = 1 if n < 9 else 2
    return abs(len(a) - len(b)) <= limit and _distance(a, b, limit) <= limit


# "State of U.P." and "Govt. of U.P." name the same party.
_SAME_PARTY_WORD = {"government": "state", "govt": "state", "gov": "state"}


def same_word(a: Word, b: Word) -> str | None:
    """"exact", "abbr" or "variant" when the two words are the same word."""
    if a.text == b.text or _stem(a.text) == _stem(b.text):
        return "exact"
    if _SAME_PARTY_WORD.get(a.text, a.text) == _SAME_PARTY_WORD.get(b.text, b.text):
        return "abbr"
    if _abbreviates(a, b) or _abbreviates(b, a):
        return "abbr"
    if _variant(a.text, b.text):
        return "variant"
    return None


def _spells(acronym: str, seq: list[Word], start: int) -> int | None:
    """End index when the first letters of seq[start:end] spell `acronym`,
    reading every word or skipping the connectors ("Union of India" is both
    "UOI" and "UI"). The run must start and end on a word that is not a
    connector."""
    for skip in (True, False):
        letters, i = "", start
        while i < len(seq) and len(letters) < len(acronym):
            w = seq[i]
            if skip and w.text in CONNECTORS:
                i += 1
                continue
            if not w.text or (w.stop and w.text not in CONNECTORS):
                break
            letters += w.text[0]
            i += 1
            if not acronym.startswith(letters):
                break
        if letters == acronym and i - start >= 2 and seq[start].text not in CONNECTORS:
            return i
    return None


@dataclass
class SideMatch:
    required: list[str] = field(default_factory=list)   # words that identify the party
    missing: list[str] = field(default_factory=list)    # required words not found
    exact: int = 0
    variant: int = 0
    exact_uncommon: int = 0   # exact matches of words not in COMMON_WORDS
    extra: int = 0            # words of the index name nothing matched
    lead: bool = False        # both names start with the same word

    @property
    def matched(self) -> int:
        return len(self.required) - len(self.missing)


def match_side(wanted: str, found: str) -> SideMatch:
    """How the party as cited (`wanted`) is matched by the name in the index."""
    want, have = words(wanted), words(found)
    core = [i for i, w in enumerate(want) if not w.stop]
    have_core = [h for h in have if not h.stop]
    kind: dict[int, str] = {}

    # 1. word to word
    used: set[int] = set()
    for i in core:
        best, best_j = None, None
        for j, h in enumerate(have_core):
            k = same_word(want[i], h)
            if k == "exact":
                best, best_j = k, j
                break
            if k and not best:
                best, best_j = k, j
        if best:
            kind[i] = best
            used.add(best_j)
    # 2. an acronym in the citation spelled by words in the index, or an alias
    have_texts = {h.text for h in have_core}
    for i in core:
        w = want[i]
        if i in kind:
            continue
        if w.acronym and any(_spells(w.text, have, j) for j in range(len(have))):
            kind[i] = "abbr"
        elif w.text in ALIASES and set(ALIASES[w.text]) <= have_texts:
            kind[i] = "abbr"
    # 3. an acronym in the index spelling words of the citation, or an alias
    for h in have_core:
        spans = []
        if h.acronym:
            spans = [(j, end) for j in range(len(want)) if (end := _spells(h.text, want, j))]
        if not spans and h.text in ALIASES:
            exp = set(ALIASES[h.text])
            idx = [i for i in core if want[i].text in exp]
            if exp <= {want[i].text for i in idx}:
                spans = [(min(idx), max(idx) + 1)]
        for j, end in spans:
            for i in range(j, end):
                if i in core and i not in kind:
                    kind[i] = "abbr"
    # 4. the same letters joined or split differently
    for n in (2, 3):
        for a in range(len(core) - n + 1):
            run = core[a:a + n]
            if all(i in kind for i in run) or run[-1] - run[0] != n - 1:
                continue
            joined = "".join(want[i].text for i in run)
            if any(h.text == joined or _variant(h.text, joined) for h in have_core):
                for i in run:
                    kind.setdefault(i, "exact")
    joined_have = [("".join(h.text for h in have_core[a:a + n])) for n in (2, 3)
                   for a in range(len(have_core) - n + 1)]
    for i in core:
        if i not in kind and any(want[i].text == j or _variant(want[i].text, j) for j in joined_have):
            kind[i] = "exact"

    required = [i for i in core if not want[i].optional]
    m = SideMatch(required=[want[i].text for i in required],
                  missing=[want[i].text for i in required if i not in kind])
    m.exact = sum(1 for k in kind.values() if k == "exact")
    m.variant = sum(1 for k in kind.values() if k == "variant")
    m.exact_uncommon = sum(1 for i, k in kind.items()
                           if k == "exact" and want[i].text not in COMMON_WORDS)
    m.extra = max(0, len(have_core) - len(used) - sum(1 for k in kind.values() if k == "abbr"))
    first = next((want[i] for i in core if not want[i].optional), None)
    m.lead = bool(first and have_core and same_word(first, have_core[0]))
    return m


def petitioner_ok(m: SideMatch) -> bool:
    """Every identifying word of the cited petitioner is in the index name."""
    return bool(m.required) and not m.missing and (m.variant == 0 or m.exact > 0)


def respondent_ok(m: SideMatch) -> bool:
    """At least two thirds of the words match, and every uncommon word does,
    except one misspelt word in a name whose other uncommon words match
    exactly twice or more ("Kathi Kalu Gohad" for "KATHI KALU OGHAD").
    "Meerut Development Authority" still is not "Delhi Development
    Authority": its one uncommon word does not match."""
    if not m.required or m.matched == 0:
        return False
    missing_uncommon = [w for w in m.missing if w not in COMMON_WORDS]
    if len(missing_uncommon) > 1 or (missing_uncommon and m.exact_uncommon < 2):
        return False
    return m.matched * 3 >= len(m.required) * 2 and (m.variant == 0 or m.exact > 0)


def match_parties(petitioner: str, respondent: str, found_pet: str,
                  found_resp: str) -> float | None:
    """None when the index entry is not the cited case. Otherwise a closeness
    score to choose between entries that both match (a judgment and its
    review order, two banks of one district): exact word matches, plus one
    when the petitioner names start alike, less a quarter for each word of
    the index names that nothing matched."""
    p = match_side(petitioner, found_pet)
    if not petitioner_ok(p):
        return None
    r = match_side(respondent, found_resp)
    if not respondent_ok(r):
        return None
    return p.exact + r.exact + (1 if p.lead else 0) - 0.25 * (p.extra + r.extra)
