"""Never reveal the model or provider behind Lawttorney.

Report 2026-09-10: "what is the model running behind you" was answered
"powered by Google's Gemini models"; "which AI model are you" with "built on
Google's Gemini model"; "who built you" with "developed by Google". The
prompts now forbid it; this is the guarantee for when a model ignores them.

Scope matters. core.redact.redact_brands is a blanket token replacement
meant for logs and error strings; on a legal answer it would turn "Google
India Pvt Ltd v. Visakha Industries" into "[model] India ...". This guard
acts only on a sentence in which the assistant describes ITSELF with a
brand ("I am built on X", "powered by X", "the model behind me is X",
"developed by X"), and it replaces that whole sentence with one fixed
identity line, because token-by-token substitution left grammar like
"powered by Lawttorney's own legal AI, a Lawttorney's own legal AI".

A brand followed by a party marker (v., Pvt, Ltd, LLC, India, Industries,
Distilleries ...) is a litigant, never a self-description, and is skipped.
"""
from __future__ import annotations

import re

IDENTITY_LINE = (
    "I am Lawttorney, a legal AI built for Indian law; details of the "
    "underlying technology are not shared."
)

_BRAND = (
    r"(?:google(?:'s)?|gemini|anthropic(?:'s)?|claude|openai(?:'s)?|chatgpt|gpt(?:[ \t\-]*\d)?|"
    r"llama|mistral|meta(?:'s)? ai|vertex ai|deepmind|large language models?|llms?)"
)
_BRAND_RE = re.compile(r"\b" + _BRAND + r"\b", re.IGNORECASE)

# The brand must sit inside a self-description construction. Up to four
# filler words are allowed between the cue and the brand ("built on a
# fine-tuned version of Gemini", "powered by Gemini, a large language
# model developed by Google").
_FILL = r"(?:[\w'\-,]+[ \t]+){0,4}?"
_SELF_DESC_ALTS = [
    # "powered by X", "built on X", "developed by Google"
    r"(?:powered|built|based|running|runs|developed|created|trained|made|operated|backed)"
    r"[ \t]+(?:by|on|with|using|upon)[ \t]+" + _FILL + _BRAND,
    # "I am (a) X", "I'm a Gemini-based assistant", "this assistant is X"
    r"(?:i am|i'm|i was|we are|we're|this (?:assistant|tool|system|model) (?:is|uses|runs on|was)|"
    r"lawttorney (?:is|uses|runs on|was))[ \t]+(?:not[ \t]+)?(?:(?:a|an|the|also|currently|actually|just)[ \t]+){0,2}"
    + _FILL + _BRAND,
    # "the model behind me is X"
    r"(?:model|llm|engine|technology|ai)[ \t]+(?:behind|powering|running|underlying)[ \t]+(?:me|us|this|it|lawttorney)"
    r"[ \t]+(?:is|are|was)[ \t]+" + _FILL + _BRAND,
    # "my underlying model is X"
    r"\b(?:my|our|its)[ \t]+(?:underlying[ \t]+)?(?:model|llm|engine|technology)[ \t]+(?:is|are|was)[ \t]+" + _FILL + _BRAND,
    # "uses Google's Gemini API"
    r"(?:uses?|using|leverag(?:es|ing)|relies on|rely on)[ \t]+(?:(?:a|an|the)[ \t]+)?" + _FILL + _BRAND
    + r"(?:'s)?[ \t]+(?:models?|api|llms?|technology)",
]
_SELF_DESC_RE = re.compile("(?:" + "|".join(_SELF_DESC_ALTS) + ")", re.IGNORECASE)
_PARTY_AFTER_RE = re.compile(
    r"^[ \t]*(?:india|llc|inc\.?|pvt\.?|private|ltd\.?|limited|corporation|corp\.?|industries|"
    r"distilleries|technologies|services|v\.?|vs\.?|versus)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])[ \t]+|\n")


def _is_self_description(sentence: str) -> bool:
    for m in _SELF_DESC_RE.finditer(sentence):
        tail = sentence[m.end():]
        if not _PARTY_AFTER_RE.match(tail):
            return True
    return False


def hide_provider(text: str) -> tuple[str, int]:
    """Replace every self-description sentence that names a model or
    provider with IDENTITY_LINE. Returns (text, sentences_replaced). Pure."""
    if not text or not _BRAND_RE.search(text):
        return text, 0
    out: list[str] = []
    count = 0
    pos = 0
    replaced_once = False

    def _handle(seg: str) -> str:
        nonlocal count, replaced_once
        if seg.strip() and _is_self_description(seg):
            count += 1
            if replaced_once:
                return ""              # one identity line is enough
            replaced_once = True
            lead = seg[: len(seg) - len(seg.lstrip())]
            return lead + IDENTITY_LINE
        return seg

    for m in _SENTENCE_SPLIT_RE.finditer(text):
        out.append(_handle(text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_handle(text[pos:]))
    if count == 0:
        # Audit 2026-09-11: with no sentence replaced this used to collapse
        # every 2+-space run in the whole answer, flattening nested lists in
        # any response that merely cited a Google/Meta litigant. Untouched
        # text goes back untouched.
        return text, 0
    result = "".join(out)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result, count
