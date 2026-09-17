"""Conversational follow-up detection.

Problem (advocate report, 2026-09-10): after receiving a draft the user
types "ok" and the pipeline regenerates the whole draft. The memory node's
rewriter knows two follow-up shapes, SEARCH and DIRECTIVE, and has none for
"no new ask", so it reconstructs "ok" / "hmm" / "yes" into the full prior
drafting request, the classifier then sees that reconstruction and routes
to Drafting. Measured: 3 of 5 acknowledgements re-drafted a 22K-char
application; only "thanks" happened to land on Non_legal.

`is_conversational_followup` is the deterministic gate that runs BEFORE the
rewriter. It is intentionally conservative: anything that carries an
instruction, a question, a legal anchor or a number is NOT conversational,
because a false positive here would answer a real request with small talk.
The rule of thumb is "a message a person sends when they are done, not when
they want something".
"""
from __future__ import annotations

import re

# Words a person uses to close or acknowledge a turn. English, Hindi in
# Latin script, and the Devanagari forms advocates actually type. Every
# token of the message must come from this set (after stripping
# punctuation and emoji) for the message to count as conversational.
_ACK_WORDS = frozenset("""
ok okay okk okie k kk fine good great nice cool perfect superb awesome
excellent wonderful brilliant lovely wow
thanks thank thankyou thx ty tysm thanku thnx
yes yeah yep yup ya haan han ha ji hmm hm hmmm mm mmm oh ah okey
no nope nah nahi nai
done noted got it understood sure alright right correct
bro bhai sir madam dear buddy dude mate boss ji
you u this that it its is was very much really so a the and lot lots
thik theek accha achha acha sahi badhiya bahut bohot bahot mast hai hain
shukriya dhanyavad dhanyawad
ठीक है अच्छा बढ़िया धन्यवाद शुक्रिया हाँ हां नहीं सही ओके ठीक
""".split())

# Anything that means "and now do something": verbs, question words, legal
# nouns, digits. One hit disqualifies the message.
_INTENT_RE = re.compile(
    r"\d|\?|"
    r"\b(add|remove|delete|change|make|draft|prepare|write|give|send|show|"
    r"explain|translate|convert|rewrite|redo|regenerate|again|shorten|expand|"
    r"summari[sz]e|cite|include|insert|replace|update|edit|modify|fix|"
    r"what|why|how|when|where|which|who|can|could|would|should|please|pls|plz|"
    r"section|act|court|judge|order|petition|application|notice|draft|prayer|"
    r"ground|clause|hindi|marathi|english|tamil|telugu|kannada|bengali|gujarati|"
    r"now|next|also|but|instead|more|less|another|different|"
    r"karo|kar|do|batao|bata|dikhao|bhejo|likho|banao|karna|chahiye|kya|kyu|kaise|kab|kahan)\b",
    re.IGNORECASE,
)

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF‍️]+"
)
_PUNCT_RE = re.compile(r"[^\w\sऀ-ॿ]+", re.UNICODE)

MAX_WORDS = 6
MAX_CHARS = 48


def is_conversational_followup(query: str | None) -> bool:
    """True when `query` is an acknowledgement, thanks, or bare yes/no with
    no instruction in it. Pure, no I/O.

    >>> is_conversational_followup("ok")
    True
    >>> is_conversational_followup("great, thanks bro")
    True
    >>> is_conversational_followup("ok now translate it to hindi")
    False
    >>> is_conversational_followup("yes add a ground on parity")
    False
    """
    if not query:
        return False
    q = query.strip()
    if len(q) > MAX_CHARS:
        return False
    if _INTENT_RE.search(q):
        return False
    stripped = _EMOJI_RE.sub(" ", q)
    stripped = _PUNCT_RE.sub(" ", stripped).strip().lower()
    if not stripped:
        # Emoji-only or punctuation-only message: a thumbs-up is an ack.
        return bool(_EMOJI_RE.search(q))
    words = stripped.split()
    if len(words) > MAX_WORDS:
        return False
    return all(w in _ACK_WORDS for w in words)
