"""Hinglish is never detected as Punjabi, and a long English prompt stays English.

Lawyer test report, 2026-11-25: "the tool sometimes responds in Punjabi or
another unintended language, even when the user has not requested it."

Cause: the Punjabi marker list held the ordinary Hindi words "ki", "karo",
"hai" and "nahi", so "notice ki reply draft karo, client ne payment nahi
kiya, cheque bounce case hai" scored as Punjabi and the strict Punjabi
directive forced a Gurmukhi answer. Two marker hits anywhere in the text
were also enough, so a 100-word English drafting instruction ending "client
ne kaha hai ki sale deed nahi hogi" was answered in Punjabi too.

The fix keeps only Punjabi words in the Punjabi list and requires the hits
to be at least 10 % of the distinct words. The Hindi words were NOT moved to
the Hindi list: that would switch everyday Hinglish questions that are
answered in English today ("anticipatory bail kaise milti hai?") to
Devanagari Hindi answers, which is a product decision of its own. So the
prompts that used to go to Punjabi now go where the Hindi list sends them:
English when it finds fewer than two Hindi markers, Hindi otherwise.
"""

from __future__ import annotations

from core.language import _detect_romanized, detect_language

# Detected as Punjabi before the fix.
HINGLISH_SENT_TO_PUNJABI = [
    "Draft a bail application for my client, FIR 420 IPC, accused ki age 25 hai, "
    "wo innocent hai, police ne galat case kiya",
    "Notice ki reply draft karo, client ne payment nahi kiya, cheque bounce case hai",
    "Legal notice draft karo tenant ko, rent nahi de raha hai 6 months se",
]

# Detected as Hindi before and after the fix.
HINGLISH_DETECTED_AS_HINDI = [
    "What is the procedure for anticipatory bail? Client ko police bula rahi hai, "
    "kya karna chahiye",
    "Section 302 IPC aur BNS mein kya badlav hai",
    "mujhe kya karna chahiye bail ke liye",
]

# Answered in English before and after the fix (deliberately unchanged).
HINGLISH_ANSWERED_IN_ENGLISH = [
    "anticipatory bail kaise milti hai?",
    "IPC 420 ka punishment kya hai?",
]

PUNJABI = [
    "Tusi mainu dasso ki karna chahida hai, bank ne notice bhejeya hai, tenu pata hai "
    "kithe jana hai",
    "Mainu bail application di loda hai, saanu kiwe file karni chahidi hai",
]

LONG_ENGLISH_WITH_A_HINDI_TAIL = (
    "Please draft a detailed written statement on behalf of the defendant in a suit "
    "for specific performance of an agreement to sell dated 10 March 2024 for a flat "
    "in Pune. The plaintiff paid an advance of Rs 10 lakh and alleges the defendant "
    "refused to execute the sale deed. The defendant says the plaintiff failed to pay "
    "the balance within the agreed 90 days and the agreement stood cancelled after a "
    "notice. Include preliminary objections on limitation and readiness and "
    "willingness, a para-wise reply, and a prayer. Client ne kaha hai ki sale deed "
    "nahi hogi."
)


def test_hinglish_is_never_punjabi():
    for q in HINGLISH_SENT_TO_PUNJABI:
        assert _detect_romanized(q) != "pa", q
        assert detect_language(q) == "en", q


def test_hinglish_already_detected_as_hindi_is_unchanged():
    for q in HINGLISH_DETECTED_AS_HINDI:
        assert detect_language(q) == "hi", q


def test_hinglish_answered_in_english_is_unchanged():
    for q in HINGLISH_ANSWERED_IN_ENGLISH:
        assert detect_language(q) == "en", q


def test_punjabi_markers_still_detect_punjabi():
    for q in PUNJABI:
        assert detect_language(q) == "pa", q


def test_long_english_prompt_with_a_few_hindi_words_stays_english():
    assert _detect_romanized(LONG_ENGLISH_WITH_A_HINDI_TAIL) is None
    assert detect_language(LONG_ENGLISH_WITH_A_HINDI_TAIL) == "en"


def test_plain_english_is_untouched():
    for q in (
        "Section 138 of the NI Act notice format",
        "What is the limitation period for filing a securitisation application "
        "before the DRT?",
    ):
        assert _detect_romanized(q) is None, q
        assert detect_language(q) == "en", q
