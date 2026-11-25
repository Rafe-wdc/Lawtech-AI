"""Substantive judgments before routine procedural orders, unless the
question is about that procedure.

judgements_v2 added the 2025-26 Bombay High Court loads, and most of them
are routine orders: a sample of 60 entries for 2025 labelled 51 procedural
(2026-11-17). Short, with the query's words in the cause title, they
outranked the judgments that decide something. The relevance judge then
rightly rejected them ("procedural withdrawal and dismissal-as-infructuous
orders without any substantive legal reasoning") and the answer went to web
search.

An order is procedural when its operative part (the end, where the court
says what it does) disposes of or moves the case, and the text shows little
or no sign of a decision on the merits. Length alone is not used: a three-page
bail order with reasons is substantive, and a long order that only directs
an authority to "consider the representation" is not.

- Strong disposals end the case without a decision: withdrawal, infructuous,
  default, restoration, condonation of delay, correction of an order, or a
  bare direction ("without adverting to the merits").
- Weak ones, listing and notice, also close many real decisions ("Stand over
  to 12th January" after granting leave to travel abroad), so they count only
  when the order shows no merits decision at all.
- Merits signs are decision phrases ("prima facie", "no case is made out",
  "is quashed", "released on bail"), precedents cited, and provisions applied.
  Generic connectors ("in view of", "therefore") are not counted: routine
  orders use them as much as judgments do.

The ordering is query-aware. When the question asks about one of these
matters ("condonation of delay in land acquisition references"), orders on
that matter keep their place. Nothing is removed: procedural orders move
after the substantive ones, so they still fill the list when there is
nothing better. The relevance judge still checks the final list.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_STRONG: dict[str, tuple[str, ...]] = {
    "withdrawal": (
        r"\b(?:dismissed|disposed of|disposed off|closed)\s+as\s+withdrawn\b",
        r"\b(?:allowed|permitted|granted leave|leave is granted)\s+to\s+(?:be\s+)?withdraw(?:n)?\b"
        r"(?!\s+(?:the\s+|\d+%?\s+(?:of\s+)?(?:the\s+)?|an?\s+)?(?:amount|money|sum|deposit|compensation))",
        r"\bwithdrawn\s+with\s+(?:leave|liberty)\b",
        r"\bseeks?\s+(?:leave|permission)\s+to\s+withdraw\b",
        r"\bleave and liberty\b", r"\bnot pressed\b"),
    "infructuous": (r"\binfructuous\b", r"\bnothing (?:further )?survives\b", r"\bdoes not survive\b"),
    # "in default of payment" is a bail condition, not a dismissal.
    "default": (r"\bwant of prosecution\b", r"\bdismissed for default\b", r"\bnon[- ]?prosecution\b"),
    # A remand that "restores" a matter to the authority decides the writ.
    "restoration": (r"\brestored to (?:file|its original)\b", r"\b(?:application|petition) for restoration\b",
                    r"\brestoration (?:application|of the (?:petition|appeal|application))\b",
                    r"\brecall(?:ed|ing)? (?:of )?(?:the |its )?order\b"),
    "delay": (r"\bdelay\b.{0,60}?\b(?:is|stands|be|being) condoned\b", r"\bcondonation of delay\b",
              r"\bcondone the delay\b"),
    "correction": (r"\bspeaking to (?:the )?minutes\b", r"\bpraecipe\b", r"\btypographical\b",
                   r"\bcorrected order\b", r"\border be corrected\b",
                   # modifying a date or term in an earlier order
                   r"\bremainder of the (?:said |earlier )?order shall remain unchanged\b",
                   r"\b(?:earlier |said )?order dated\b.{0,40}?\b(?:is|stands|be) (?:hereby )?modified\b"),
    "direction only": (
        r"\bwithout (?:going into|adverting to|entering into|examining|expressing any (?:opinion|view) on)"
        r" (?:the )?merits\b",
        r"\b(?:consider|decide) (?:and decide )?the (?:said |pending )?(?:representation|proposal)\b",
        # "the trial court to expedite" closes many bail decisions; only a
        # disposal that is nothing but that direction counts.
        r"\bdisposed of with (?:a )?direction to (?:the )?(?:learned )?(?:trial )?court to (?:expedite|decide)\b",
        r"\bdecide the (?:pending )?(?:suit|case|trial|complaint)\b.{0,40}?\bwithin\b",
        r"\btransmit the record\b", r"\bsigned judgment be kept\b"),
}
_WEAK: dict[str, tuple[str, ...]] = {
    "listing": (r"\bS\.\s*O\.\s*to\b", r"\bstands?\s+over\s+to\b", r"\bpost (?:the matter |it )?on\b",
                r"\blist (?:the matter |it )?(?:again )?on\b", r"\badjourned\b", r"\bto be listed\b",
                r"\bnot on board\b", r"\blisted for (?:final )?(?:hearing|disposal)\b"),
    "notice": (r"\bissue notice\b", r"\bnotice returnable\b", r"\bmade returnable\b"),
    "office": (r"\boffice objections?\b", r"\bcause[- ]title\b", r"\bcause list\b"),
}
_MATTER_RES = {m: re.compile("|".join(p), re.I) for m, p in {**_STRONG, **_WEAK}.items()}

# The words a question uses when it asks about one of those matters.
_ASKS: dict[str, re.Pattern] = {
    "withdrawal": re.compile(r"\bwithdra", re.I),
    "infructuous": re.compile(r"\binfructuous", re.I),
    "default": re.compile(r"\b(?:default|want of prosecution|non[- ]?prosecution)\b", re.I),
    "restoration": re.compile(r"\b(?:restor|recall)", re.I),
    "delay": re.compile(r"\b(?:delay|condon|limitation)", re.I),
    "correction": re.compile(r"\b(?:speaking to (?:the )?minutes|correction of (?:the )?order|praecipe)", re.I),
    "direction only": re.compile(r"\b(?:representation|expedit)", re.I),
    "listing": re.compile(r"\badjourn|\blisting\b", re.I),
    "notice": re.compile(r"\bnotice\b", re.I),
    "office": re.compile(r"\boffice objection|\bcause[- ]?(?:title|list)", re.I),
}

# Signs that the court decided something on the merits.
_REASONING = re.compile(
    r"\bprima facie\b|\bin (?:my|our)(?: considered)? (?:view|opinion)\b"
    r"|\b(?:we are|i am) (?:of the|not)(?: considered)? (?:view|opinion|inclined|satisfied)\b"
    r"|\bfor the (?:foregoing |aforesaid |above )?reasons\b|\bit is (?:well )?settled\b"
    r"|\b(?:held|observed) that\b|\bno case (?:is )?made out\b"
    r"|\bthe (?:question|issue|point)s? (?:that|which) (?:arises?|falls?)\b"
    r"|\b(?:is|are|was|were) (?:not )?(?:entitled|justified|sustainable|maintainable)\b"
    r"|\bdeserves? to be (?:allowed|dismissed|quashed|set aside|granted|rejected|accepted)\b"
    r"|\b(?:is|are|stands?) (?:hereby )?(?:quashed|set aside)\b|\bbe released on (?:regular |anticipatory |interim )?bail\b"
    r"|\bbail (?:is|stands) (?:granted|rejected|refused)\b|\b(?:conviction|sentence) (?:is|stands)\b"
    r"|\bthe (?:learned )?(?:trial|appellate|lower|reference) court (?:has |had )?(?:erred|rightly|failed)\b"
    r"|\bthe evidence on record\b|\bon (?:a )?(?:careful )?(?:perusal|consideration) of\b"
    r"|\bnot inclined to\b|\b(?:not )?a fit case\b|\bfinds? no (?:merit|ground|reason|substance)\b"
    r"|\bno (?:merit|substance) in\b|\bconsidering the (?:nature|gravity|seriousness|period|role|facts)\b"
    r"|\bmade out a case\b|\b(?:we|i) (?:hereby )?(?:set aside|quash)\b|\bthe impugned (?:order|judgment) "
    r"(?:is|stands|deserves)\b|\bsetting aside\b|\binclined to (?:allow|grant|reject|dismiss|accept|interfere)\b"
    r"|\bmaterial on record\b|\b(?:confirmed and )?made absolute\b",
    re.I)

# The main matter disposed of on its own terms ("Hence, petition is granted",
# "this petition is allowed. Petitioner be admitted to bail"), not "as
# withdrawn", "as infructuous" or "for default". Worth two merits signs: an
# order can condone delay or dismiss a stay application as infructuous and
# still decide the case.
_DECIDES = re.compile(
    r"\b(?:appeal|petition|revision|reference|suit|bail application|application for bail)s?\b[^.]{0,60}?"
    r"\b(?:is|are|stands?|be)\s+(?:hereby\s+)?(?:partly\s+)?(?:allowed|granted|dismissed|rejected)\b"
    r"(?!\s+(?:to\b|as\s+(?:withdrawn|infructuous|not\s+pressed)|for\s+(?:default|want|non)))",
    re.I)
_SETS_ASIDE = re.compile(r"\bsetting aside the (?:impugned )?(?:order|judgment|award|decree)\b"
                         r"|\b(?:order|judgment|award|decree)\b[^.]{0,80}?\b(?:is|are|stands?) (?:hereby )?"
                         r"(?:quashed|set aside)\b|\b(?:confirmed and )?made absolute\b"
                         # a bail case decided by this court: "shall be released on bail", "be
                         # admitted to regular bail". Not a recital such as "the applicants were
                         # arrested and in fact are released on regular bail" (by another court),
                         # which closes many applications dismissed as infructuous.
                         r"|\bbe (?:released|enlarged) on (?:regular |anticipatory |interim )?bail\b"
                         r"|\bbe admitted to (?:regular |anticipatory )?bail\b"
                         r"|\b(?:regular |anticipatory )?bail is (?:hereby )?(?:granted|rejected|refused)\b",
                         re.I)

# Keyword fields name procedural steps ("withdrawal", "liberty") for routine
# orders and the subject ("succession certificate", "legal heirs") for
# decisions: 96% of labelled decisions carried 3+ subject keywords.
_PROCEDURAL_KEYWORD = re.compile(
    r"withdraw|liberty|infructuous|default|prosecution|restor|recall|delay|condon|adjourn|listing|notice"
    r"|correction|speaking to minutes|praecipe|typographical|direction|representation|expedit|disposal"
    r"|disposed|office objection|cause title|interim application|extension of time|compliance|registry"
    r"|transfer|vakalat|appearance|leave|permission|stand over|time to file|reply", re.I)
_CITATION = re.compile(r"\(\d{4}\)\s*\d+\s*SCC\b|\bAIR\s*\d{4}\b|\bSCC OnLine\b|\b\d{4}\s*INSC\b"
                       r"|\bMh\.?\s*L\.?\s*J\b|\bBom\.?\s*C\.?R\b|\bALL MR\b|\bSCR\b", re.I)

_OPERATIVE_CHARS = 1500     # the end of a long order, where the court says what it does
_SHORT_ORDER_CHARS = 2500   # below this the whole order is the operative part
_STUB_CHARS = 400           # less text than this and no merits sign: not a decision
_STRONG_BELOW = 3           # a strong disposal with fewer merits signs than this: procedural
_WEAK_BELOW = 1             # a listing / notice order with no merits sign at all: procedural


@dataclass
class OrderKind:
    procedural: bool
    matters: set[str] = field(default_factory=set)
    merits: int = 0
    reason: str = ""


def classify(hit: dict) -> OrderKind:
    """Procedural or substantive, with the reason in words."""
    text = " ".join(str(hit.get("content") or "").split())
    reasoning = len(_REASONING.findall(text))
    cited = len(_CITATION.findall(text))
    provisions = len(hit.get("acts_or_sections_invoked") or [])
    keywords = hit.get("keywords") or []
    keywords = [keywords] if isinstance(keywords, str) else keywords
    subject = sum(1 for k in keywords if k and not _PROCEDURAL_KEYWORD.search(str(k)))
    # Provisions count only next to reasoning: the index tags the provisions
    # named in a cause title too.
    main_decided = bool(_DECIDES.search(text) or _SETS_ASIDE.search(text))
    decides = 2 if main_decided else 0
    in_text = reasoning + decides + min(cited, 3) + (1 if provisions and reasoning else 0)
    merits = in_text + (1 if subject >= 4 else 0)
    operative = text if len(text) <= _SHORT_ORDER_CHARS else text[-_OPERATIVE_CHARS:]
    strong = {m for m in _STRONG if _own_disposal(_MATTER_RES[m], operative, m, main_decided)}
    weak = {m for m in _WEAK if _MATTER_RES[m].search(operative)}
    if len(text) < _STUB_CHARS and merits == 0 and not (strong or weak):
        return OrderKind(True, {"no decision text"}, merits,
                         f"procedural: {len(text)} characters of text and no merits decision")
    signs = (f"{reasoning} reasoning, {cited} precedent(s), {provisions} provision(s), "
             f"{subject} subject keyword(s)")
    # An order that decides the main matter ("Hence, petition is granted",
    # "dispose of the appeal setting aside the impugned order") is a decision
    # whatever else it disposes of. Otherwise, against a routine disposal
    # only the text's own signs count: an order condoning delay in a land
    # acquisition appeal carries the subject's keywords without deciding it.
    if strong and not main_decided and in_text < _STRONG_BELOW:
        return OrderKind(True, strong | weak, in_text,
                         f"procedural: ends in {', '.join(sorted(strong))}; {in_text} merits sign(s) in the text")
    # A listing line at the end of a long order closes a decision ("Stand
    # over to 12th January" after four pages granting leave to travel).
    if weak and not strong and merits < _WEAK_BELOW and len(text) <= _SHORT_ORDER_CHARS:
        return OrderKind(True, weak, merits,
                         f"procedural: only {', '.join(sorted(weak))}; no merits decision")
    found = strong | weak
    if found:
        return OrderKind(False, found, merits,
                         f"substantive: mentions {', '.join(sorted(found))} but decides on the merits ({signs})")
    return OrderKind(False, set(), merits, f"substantive: no routine disposal ({signs})")


_SENTENCE_RE = re.compile(r"[^.;]*(?:[.;]|$)")
# A party's account of events: "submits that the suit was dismissed for default".
_REPORTED = re.compile(r"\b(?:submits?|submitted|contends?|contended|states? that|stated that|argued|"
                       r"pointed out|according to|it is the case of)\b", re.I)
# Interlocutory applications, closed as infructuous once the case is decided.
_SIDE_APPLICATION = re.compile(r"\b(?:C\.?\s?M\.?(?:\s*APPL)?|I\.?\s?A\.?|interim application|civil application|"
                               r"stay application|misc(?:ellaneous)?\.? application|notice of motion|"
                               r"chamber summons)\b", re.I)


def _own_disposal(rx: re.Pattern, operative: str, matter: str, main_decided: bool) -> bool:
    """A routine disposal made by the court, of the case itself."""
    for m in rx.finditer(operative):
        sentence = next((s.group() for s in _SENTENCE_RE.finditer(operative)
                         if s.start() <= m.start() < s.end()), "")
        if _REPORTED.search(sentence):
            continue
        if main_decided and matter in ("infructuous", "withdrawal") and _SIDE_APPLICATION.search(sentence):
            continue
        return True
    return False


def asked_matters(query: str) -> set[str]:
    """The procedural matters the question itself is about."""
    return {m for m, rx in _ASKS.items() if rx.search(query or "")}


def prefer_substantive(query: str, hits: list[dict]) -> list[dict]:
    """The hits with substantive judgments first, keeping the search order
    within each group. A procedural order on a matter the question asks
    about stays with the substantive ones. Each hit is labelled with
    `order_kind` ("substantive" / "procedural" / "procedural-asked") and
    `order_reason`."""
    asked = asked_matters(query)
    first, later = [], []
    for h in hits:
        k = classify(h)
        if k.procedural and (k.matters & asked):
            h["order_kind"] = "procedural-asked"
            h["order_reason"] = (k.reason + f"; kept in place: the question asks about "
                                 f"{', '.join(sorted(k.matters & asked))}")
            first.append(h)
        elif k.procedural:
            h["order_kind"], h["order_reason"] = "procedural", k.reason
            later.append(h)
        else:
            h["order_kind"], h["order_reason"] = "substantive", k.reason
            first.append(h)
    return first + later
