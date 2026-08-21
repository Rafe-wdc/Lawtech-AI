"""Language detection and prompt localization utilities.

Supports auto-detection of 13+ Indian languages via langdetect,
with a Romanized-script heuristic for Hinglish and other Roman-script inputs.

Used by:
  agents/memory.py  — detect user_language from original_query
  config/prompts.py — localize_prompt() injected at each agent call site
"""

from __future__ import annotations

import re
from functools import lru_cache

from core.logger import get_logger

log = get_logger("Language")

# ---------------------------------------------------------------------------
# Language registry
# ---------------------------------------------------------------------------

SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "bn": "Bengali",
    "te": "Telugu",
    "mr": "Marathi",
    "ta": "Tamil",
    "kn": "Kannada",
    "ml": "Malayalam",
    "gu": "Gujarati",
    "pa": "Punjabi",
    "ur": "Urdu",
    "or": "Odia",
    "as": "Assamese",
    "sa": "Sanskrit",
}

# Languages where legal citations must clearly stay in English
_CITATION_LANGUAGES = SUPPORTED_LANGUAGES.keys() - {"en"}

# ---------------------------------------------------------------------------
# Romanized (Roman-script) keyword heuristics
# Catches "Hinglish" and other Indian-language queries typed in Latin script.
# ---------------------------------------------------------------------------

_ROMANIZED_HINTS: dict[str, list[str]] = {
    "hi": [
        "kaise", "kya", "karo", "karein", "batao", "dijiye", "chahiye",
        "lagta", "hoga", "wala", "mujhe", "humko", "mere", "hamare",
        "iska", "uska", "yeh", "woh", "bhi", "aur", "lekin", "kyunki",
        "dhara", "mukadma", "adalat", "vakil", "nyay", "jameen",
        # NB: do NOT include Indian-legal-act acronyms like "ipc", "crpc",
        # "bns", "bnss", "bsa" here. Indian lawyers writing in English
        # routinely say "Section 302 IPC" or "BNS vs IPC comparison".
        # Treating them as Hindi markers misroutes English queries to
        # Hindi responses AND causes Newacts ES retrieval to fail on
        # language-mismatched lookups (the corpus is English-indexed).
        # Real Hindi-language queries about these acts will be caught
        # by the langdetect call on the Hindi prose surrounding the
        # acronyms, not the acronyms themselves.
    ],
    "mr": ["kasa", "aahe", "sangaa", "karayche", "mhanje", "tyala", "aapan"],
    "ta": ["eppadi", "enna", "sollunga", "pannum", "irukku", "seiyya"],
    "te": ["ela", "cheyali", "cheppandi", "undi", "kavali", "chesukonu"],
    "kn": ["hege", "enu", "helri", "madbekhu", "ide", "alla"],
    "ml": ["engane", "enna", "cheyyuka", "aanu", "undo"],
    "gu": ["kem", "shu", "karo", "che", "nathi", "tame"],
    "pa": ["kiwe", "ki", "karo", "hai", "nahi", "tenu", "saanu"],
}

# Minimum keyword hits to trigger Romanized detection
_ROMANIZED_MIN_HITS = 2


# ---------------------------------------------------------------------------
# Script-family helpers + Devanagari (Hindi / Marathi) disambiguation
# ---------------------------------------------------------------------------
# Hindi, Marathi and Sanskrit share the Devanagari script, and langdetect's
# n-gram profiles for them overlap heavily because the CONTENT words in Indian
# legal text — statute names, doctrinal terms, Sanskrit-derived nouns — are
# nearly identical across the three. On short queries it flips a coin.
# Measured 2026-08-21:
#     "भारतीय न्याय संहिता कलम 105 काय सांगते?"  → langdetect says "hi"
# even though "काय सांगते" is unambiguously Marathi. The user asks in Marathi
# and gets Hindi back.
#
# The fix is to score GRAMMATICAL FUNCTION WORDS instead: copulas, question
# words, postpositions and conjunctions are completely disjoint between Hindi
# and Marathi (आहे/है, काय/क्या, आणि/और, मध्ये/में) even when every content
# word in the sentence is shared. Deterministic, offline, no LLM call.
#
# Both sets are function words ONLY. Do NOT add legal vocabulary here — that
# is precisely the shared material that confuses langdetect in the first place.

_MARATHI_MARKERS: frozenset[str] = frozenset({
    # copulas / negation
    "आहे", "आहेत", "आहात", "आहेस", "नाही", "नाहीत", "नव्हता", "नव्हते",
    "नव्हती", "असेल", "असतो", "असते", "असतात", "होईल",
    # question words
    "काय", "कसे", "कसा", "कशी", "कशा", "कुठे", "कुठला", "कोणता", "कोणते",
    "कोणती", "कोणत्या", "केव्हा", "किती", "का",
    # postpositions / connectives
    "मध्ये", "मधील", "मधून", "बद्दल", "बाबत", "साठी", "करिता", "तसेच",
    "आणि", "किंवा", "परंतु", "पण", "मात्र", "त्यामुळे", "यामुळे", "नुसार",
    "म्हणजे", "म्हणून", "वरील", "येथे", "तेथे", "इथे", "तिथे",
    # verbs / auxiliaries in imperative + potential moods (very common in
    # "explain this to me" style queries)
    "करावे", "करावी", "करावा", "करायचे", "करायची", "करून",
    "शकते", "शकतो", "शकतात", "शकता", "पाहिजे", "हवे", "हवी",
    "द्या", "सांगा", "सांगावे", "सांगते", "सांगतो", "दाखवा",
    "केली", "केले", "केला", "केल्या", "झाली", "झाले", "झाला", "झाल्या",
    "मिळते", "मिळाले", "मिळाली",
    # pronominal possessives
    "माझ्या", "माझा", "माझी", "माझे", "तुमच्या", "तुमचा", "तुमची",
    "त्यांच्या", "त्यांचा", "यांच्या", "आमच्या", "वतीने", "प्रकरणी",
})

# Suffixes that only occur in Marathi inflection. Bare "ची"/"चा"/"चे" are
# deliberately EXCLUDED — Hindi legal text is full of "अनुसूची" (schedule)
# and "सूची" (list), which a bare-suffix match would score as Marathi.
_MARATHI_SUFFIXES: tuple[str, ...] = (
    "च्या", "ाची", "ाचा", "ाचे", "ांनी", "ातील", "ासाठी", "ामध्ये",
    "ाबद्दल", "ण्याची", "ण्यात",
)

_HINDI_MARKERS: frozenset[str] = frozenset({
    # copulas / negation
    "है", "हैं", "हूँ", "हूं", "था", "थे", "थी", "नहीं", "नही",
    "होगा", "होगी", "होंगे", "होता", "होती", "होते",
    # question words
    "क्या", "कैसे", "कैसा", "कैसी", "कौन", "कौनसा", "कब", "कहाँ", "कहां",
    "कितना", "कितनी", "कितने", "क्यों",
    # postpositions / connectives
    "में", "के", "का", "की", "को", "से", "और", "लिए", "लिये", "पर",
    "अगर", "यदि", "अनुसार", "बारे", "द्वारा", "तक", "साथ",
    # verbs / auxiliaries
    "मतलब", "बताओ", "बताइए", "बताएं", "बतायें", "करें", "कीजिए", "चाहिए",
    "सकता", "सकती", "सकते", "गया", "गई", "गयी", "हुआ", "हुई", "हुए",
    "रहा", "रही", "रहे", "वाला", "वाले", "वाली", "समझाओ", "समझाइए",
    # pronominal possessives
    "मेरे", "मेरा", "मेरी", "आपके", "आपका", "आपकी", "उनके", "उनका",
    "उनकी", "इनके", "इसके", "इसका", "जिसमें", "जिसके",
})

_DEVANAGARI_TOKEN_RE = re.compile(r"[\u0900-\u097F]+")

# Unicode block -> the SUPPORTED_LANGUAGES code that block implies. Used as a
# floor: when langdetect returns a language we do not support (it emits "ne"
# for Devanagari fairly often, and has no "as" profile at all), an Indic-script
# query must never fall through to English.
_SCRIPT_RANGES: tuple[tuple[str, str], ...] = (
    ("deva", "\u0900-\u097F"),   # Devanagari — Hindi / Marathi / Sanskrit
    ("beng", "\u0980-\u09FF"),   # Bengali / Assamese
    ("guru", "\u0A00-\u0A7F"),   # Gurmukhi — Punjabi
    ("gujr", "\u0A80-\u0AFF"),   # Gujarati
    ("orya", "\u0B00-\u0B7F"),   # Odia
    ("taml", "\u0B80-\u0BFF"),   # Tamil
    ("telu", "\u0C00-\u0C7F"),   # Telugu
    ("knda", "\u0C80-\u0CFF"),   # Kannada
    ("mlym", "\u0D00-\u0D7F"),   # Malayalam
    ("arab", "\u0600-\u06FF"),   # Arabic script — Urdu
)

_SCRIPT_DEFAULT_LANG: dict[str, str] = {
    "deva": "hi",   # refined to hi/mr by _disambiguate_devanagari
    "beng": "bn", "guru": "pa", "gujr": "gu", "orya": "or",
    "taml": "ta", "telu": "te", "knda": "kn", "mlym": "ml", "arab": "ur",
}

# Which script each supported language is written in — lets callers ask
# "are these two codes even distinguishable by script?" before trusting one
# detector over another.
_LANG_SCRIPT: dict[str, str] = {
    "en": "latn", "hi": "deva", "mr": "deva", "sa": "deva",
    "bn": "beng", "as": "beng", "pa": "guru", "gu": "gujr", "or": "orya",
    "ta": "taml", "te": "telu", "kn": "knda", "ml": "mlym", "ur": "arab",
}

_SCRIPT_PATTERNS: dict[str, "re.Pattern[str]"] = {
    name: re.compile(f"[{rng}]") for name, rng in _SCRIPT_RANGES
}

# A query has to be substantially in one script before that script overrides
# langdetect. A dominantly-English 4.7 kB prompt with one quoted Marathi
# paragraph stays English (that exact case is in
# tests/multilingual_test_2026_08_17/flow_analysis.md).
_SCRIPT_DOMINANCE_MIN = 0.30


def script_of(lang: str) -> str:
    """Return the script code a language is written in ('deva', 'latn', ...)."""
    return _LANG_SCRIPT.get(lang, "latn")


def dominant_script(text: str) -> str | None:
    """Return the script code covering >=30% of *text*'s letters, else None."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return None
    best: str | None = None
    best_ratio = 0.0
    total = len(letters)
    for name, pattern in _SCRIPT_PATTERNS.items():
        ratio = sum(1 for c in letters if pattern.match(c)) / total
        if ratio > best_ratio:
            best_ratio, best = ratio, name
    return best if best_ratio >= _SCRIPT_DOMINANCE_MIN else None


def devanagari_language(text: str) -> str | None:
    """Score Hindi vs Marathi function words in *text*.

    Returns "hi" / "mr" when the markers give a verdict, or None when the text
    carries no discriminating function words at all (e.g. a bare "कलम 302"
    citation). None means "no evidence" — callers keep whatever their other
    detector said rather than guessing.
    """
    tokens = _DEVANAGARI_TOKEN_RE.findall(text)
    if not tokens:
        return None
    mr = sum(
        1 for t in tokens
        if t in _MARATHI_MARKERS or t.endswith(_MARATHI_SUFFIXES)
    )
    hi = sum(1 for t in tokens if t in _HINDI_MARKERS)
    if mr > hi:
        return "mr"
    if hi > mr:
        return "hi"
    return None


def _disambiguate_devanagari(text: str, detected: str) -> str:
    """Resolve a Devanagari query to "hi" or "mr"."""
    verdict = devanagari_language(text)
    if verdict:
        if verdict != detected:
            log.debug("Devanagari marker override", langdetect=detected,
                      markers=verdict, snippet=text[:60])
        return verdict
    # No discriminating markers — keep langdetect's guess when it is at least
    # a Devanagari language, otherwise fall back to Hindi. Never English: the
    # text is demonstrably Devanagari.
    return detected if detected in ("hi", "mr", "sa") else "hi"


def _detect_romanized(text: str) -> str | None:
    """Check if a Roman-script text is likely a transliterated Indian language.

    Returns ISO code of the best match, or None.
    """
    lower = text.lower()
    tokens = set(re.findall(r"\b[a-z]+\b", lower))
    best_lang: str | None = None
    best_count = 0
    for lang, keywords in _ROMANIZED_HINTS.items():
        count = sum(1 for kw in keywords if kw in tokens)
        if count >= _ROMANIZED_MIN_HITS and count > best_count:
            best_count = count
            best_lang = lang
    return best_lang


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_language(text: str) -> str:
    """Detect the language of *text*.

    Strategy:
      1. If text is very short (< 10 chars) → default "en"
      2. Try langdetect on the full text
      3. If the text is dominantly written in an Indic (or Arabic) script,
         the SCRIPT decides the language family — langdetect only gets to
         pick within it. Devanagari is resolved to Hindi vs Marathi by
         function-word markers (`devanagari_language`), because langdetect
         cannot tell them apart on short legal queries. This also stops an
         unsupported langdetect verdict ("ne" for Devanagari, no profile at
         all for Assamese) from collapsing a native-script query to English.
      4. If langdetect returns "en" but text is Roman script → check
         Romanized-hint heuristic (catches Hinglish / transliterated queries)
      5. If detected language not in SUPPORTED_LANGUAGES → fall back to "en"

    Returns ISO 639-1 code (e.g. "hi", "ta", "en").
    """
    if not text or len(text.strip()) < 10:
        return "en"

    detected = "en"
    try:
        from langdetect import detect, LangDetectException  # lazy import
        detected = detect(text)
    except Exception as e:
        # langdetect not installed or detection failed — heuristic still runs.
        # Debug-level: this happens on very short or ambiguous inputs and is normal.
        log.debug("langdetect failed; using Romanized heuristic",
                  error=str(e)[:120])

    # langdetect sometimes returns "zh-cn", "pt" etc. for short Indian texts
    # Normalise: strip region suffix ("zh-cn" → "zh"), keep only known codes
    detected = detected.split("-")[0].lower()

    # Script beats langdetect. A query written in an Indic script simply IS
    # that script's language; langdetect's job is only to choose within the
    # family, and for Devanagari it demonstrably cannot (hi/mr overlap), so
    # function-word markers decide there. Without this, two failure modes hit
    # real users: a Marathi query answered in Hindi, and a native-script query
    # whose langdetect verdict is outside SUPPORTED_LANGUAGES (e.g. "ne")
    # silently collapsing to English.
    script = dominant_script(text)
    if script == "deva":
        detected = _disambiguate_devanagari(text, detected)
    elif script is not None:
        script_lang = _SCRIPT_DEFAULT_LANG[script]
        if detected != script_lang:
            log.debug("Script override", langdetect=detected,
                      script=script, lang=script_lang, snippet=text[:60])
        detected = script_lang

    if detected not in SUPPORTED_LANGUAGES:
        detected = "en"

    # If langdetect said English, double-check with Romanized heuristic
    if detected == "en":
        roman_lang = _detect_romanized(text)
        if roman_lang:
            log.debug("Romanized script override", roman_lang=roman_lang,
                      snippet=text[:60])
            detected = roman_lang

    log.debug("Language detected", lang=detected,
              lang_name=SUPPORTED_LANGUAGES.get(detected, "Unknown"),
              snippet=text[:80])
    return detected


def _format_intent_directives(intent) -> str:
    """Render a UserIntent's typed fields into a directive block for agent prompts.

    Empty string when the intent expresses no explicit preferences — that case
    must produce a no-op append so backwards-compatible behaviour is preserved
    for callers that don't pass an intent at all.

    Typed as `intent: Any` (not `UserIntent`) to avoid a config↔core import
    cycle; we duck-type on the expected attributes.

    Currently surfaces:
      - response_depth ("brief" / "detailed") — drives length
      - additional_instructions (free-text catchall)
      - arguments_for_party ("plaintiff" / "defendant" / "both") — forces
        the drafting / arguments LLM to write in the named party's voice.
        Drives both the section text (first-person stance) and the footer
        signature label. The directive also tells the LLM to honour the
        user's LITERAL party label when present in the query (Respondent
        vs Defendant, Applicant vs Plaintiff, etc.) instead of defaulting
        to the doc-type-implied synonym — i.e. "WS on behalf of Respondent
        no. 1" stays as "Respondent", not silently rewritten to "Defendant".

    Format and language live in their own layers: synthesis-template picker
    handles wants_table, localize_prompt itself handles the language block.
    Adding format here would duplicate the synth layer's directive without
    benefit.
    """
    if intent is None:
        return ""
    parts: list[str] = []
    depth = getattr(intent, "response_depth", "standard")
    if depth == "brief":
        parts.append(
            "USER DEPTH: keep the response under 200 words. Prefer the most "
            "load-bearing facts. Skip background and elaboration."
        )
    elif depth == "detailed":
        parts.append(
            "USER DEPTH: comprehensive coverage. Include all relevant "
            "sub-sections, provisos, explanations, and material context. "
            "For LEGAL DRAFTS (writ petitions, plaints, bail applications, "
            "anticipatory bail, complaints, appeals, revisions, rejoinders, "
            "counter-affidavits, written statements) the following are "
            "MINIMUM expectations — a draft that falls short of any of "
            "these when the user asked for detail is INADEQUATE:\n"
            "  1. GROUNDS — at least 15 numbered grounds where the "
            "     document type carries a Grounds section (writ, plaint, "
            "     bail, anticipatory bail, revision, appeal). Each ground "
            "     is a full paragraph (3-5 sentences), not a one-line "
            "     assertion. Each ground weaves in the legal reasoning "
            "     that supports it — cite the statutory provision, "
            "     principle, or doctrine that makes the ground legally "
            "     viable.\n"
            "  2. LANDMARK CASE-LAW — cite 2-4 landmark Supreme Court "
            "     precedents INLINE inside the grounds where they are "
            "     doctrinally on point. Do NOT append them as an "
            "     afterword or bibliography. Concrete anchors by doc "
            "     type: anticipatory bail — Gurbaksh Singh Sibbia v. "
            "     State of Punjab (1980), Siddharam Satlingappa Mhetre "
            "     v. State of Maharashtra (2011), Arnesh Kumar v. State "
            "     of Bihar (2014); regular bail — Sanjay Chandra v. CBI "
            "     (2012), Dataram Singh v. State of UP (2018); writ "
            "     quashing — State of Haryana v. Bhajan Lal (1992), "
            "     R.P. Kapur v. State of Punjab (1960); plaint / civil "
            "     matters — Salem Advocate Bar Assn. (II) v. Union of "
            "     India (2005), Kailash v. Nanhku (2005).\n"
            "  3. FACTS OF THE CASE — chronological, paragraph-by-"
            "     paragraph, tied to the uploaded source document's "
            "     dates and events. If the source names dates, FIR "
            "     numbers, sections, complainants, witnesses, articles "
            "     recovered, they ALL appear in the Facts section. Do "
            "     not compress into a single summary paragraph.\n"
            "  4. CONDUCT / CHARACTER PARAGRAPHS for BAIL-TYPE drafts "
            "     (anticipatory / regular / interim / cancellation-"
            "     opposing) — the applicant's roots in society, family "
            "     ties, occupation, past clean record, willingness to "
            "     cooperate with investigation, readiness to abide by "
            "     any conditions imposed. Multiple paragraphs, not a "
            "     single sentence.\n"
            "  5. CUSTODIAL INTERROGATION SUBMISSIONS for anticipatory "
            "     bail — why custodial interrogation is NOT required "
            "     (nothing to recover, cooperation offered, allegations "
            "     already documented, disclosure-statement-only case, "
            "     parity with released co-accused, medical / age "
            "     considerations). A full ground on this alone.\n"
            "  6. STATUTORY-FRAMEWORK section that reproduces the "
            "     text of each statutory provision the draft relies on, "
            "     followed by application of that provision to the "
            "     matter at hand.\n"
            "  7. PRAYER with MULTIPLE reliefs — main relief, interim "
            "     relief, alternative reliefs, costs, and an \"any "
            "     other and further order this Hon'ble Court may deem "
            "     fit\" clause. Each relief as a separate (a)/(b)/(c) "
            "     item.\n"
            "  8. SUBSTANTIVE LENGTH — for a fanned-out detailed "
            "     drafting task, expect the assembled document to run "
            "     20,000+ characters (roughly 8-12 printed pages). "
            "     Short outputs are almost certainly under-detailed."
        )
    party = getattr(intent, "arguments_for_party", "none")
    if party == "plaintiff":
        parts.append(
            "USER PARTY: write in the PLAINTIFF / PETITIONER / APPLICANT / "
            "COMPLAINANT / APPELLANT voice. The document speaks AS that "
            "party. Cause-title still names both sides, but the body, "
            "prayer, verification, and affidavit are the plaintiff-side "
            "party's pleading. "
            "LABEL CHOICE: "
            "(a) FIRST priority — if the user's query explicitly names a "
            "party label (e.g. 'on behalf of the Applicant', 'for the "
            "Complainant', 'arguments for Petitioner no. 2'), use THAT "
            "EXACT word everywhere it appears in the draft: subject "
            "heading ('APPLICATION ON BEHALF OF THE APPLICANT'), "
            "cause-title designation ('.....Applicant'), body voice "
            "('the Applicant most respectfully submits...'), and any "
            "numbered references ('Applicant no. 2'). DO NOT silently "
            "swap it for a doctrinally-implied synonym — honour the "
            "user's word. "
            "(b) FALLBACK — only when the user did NOT name a specific "
            "label, pick the synonym that matches the document type: "
            "Plaint→Plaintiff, Writ Petition→Petitioner, Notice→"
            "Complainant/Sender, Appeal→Appellant. "
            "If the document type is a Written Statement / Reply / "
            "Counter-Affidavit (structurally the responsive pleading), "
            "treat the user's instruction as 'draft the plaintiff's "
            "REPLICATION (Order VIII Rule 9 CPC) / rejoinder' — DO NOT "
            "silently flip and write as the defendant."
        )
    elif party == "defendant":
        parts.append(
            "USER PARTY: write in the DEFENDANT / RESPONDENT / OPPOSITE "
            "PARTY / ACCUSED voice. The document speaks AS that party. "
            "Cause-title still names both sides, but the body, prayer, "
            "verification, and affidavit are the defendant-side party's "
            "pleading. "
            "LABEL CHOICE: "
            "(a) FIRST priority — if the user's query explicitly names a "
            "party label (e.g. 'on behalf of Respondent no. 1', 'for the "
            "Opposite Party', 'reply by the Accused'), use THAT EXACT "
            "word everywhere it appears in the draft: subject heading "
            "('WRITTEN STATEMENT ON BEHALF OF RESPONDENT NO. 1'), "
            "cause-title designation ('.....Respondent No. 1'), body "
            "voice ('the Respondent submits that...'), and any numbered "
            "references ('Respondent no. 1'). DO NOT silently swap it "
            "for a doctrinally-implied synonym just because Written "
            "Statement is usually a Defendant's pleading — honour the "
            "user's word. "
            "(b) FALLBACK — only when the user did NOT name a specific "
            "label, pick the synonym that matches the document type: "
            "Written Statement→Defendant, Counter-Affidavit→Respondent, "
            "Bail Application→Accused/Applicant."
        )
    elif party == "both":
        parts.append(
            "USER PARTY: the user asked for arguments on BOTH sides. "
            "Structure the response with clearly labelled sections — "
            "'Arguments for the Plaintiff/Petitioner' and 'Arguments for "
            "the Defendant/Respondent' — so the two perspectives are not "
            "blended into a single narrative."
        )
    extra = getattr(intent, "additional_instructions", "") or ""
    if extra.strip():
        # Already capped at 300 chars at extraction; safe to inject verbatim.
        parts.append(f"USER ADDITIONAL INSTRUCTIONS: {extra.strip()}")
    if not parts:
        return ""
    return "\n\n## USER DIRECTIVES (must be honoured)\n" + "\n".join(
        f"- {p}" for p in parts
    )


# Indian languages where users expect FULL native-script output by default
# — citations + numerals + statute names all in the target language.
# Carve-out: English remains opt-in for citations. Match V1 Lawttorney's
# behaviour (Sagar's bug #1 + #4, 2026-06-16: "Marathi mixing with English"
# and "Marathi answer mixes Marathi + English numbers").
_INDIAN_LANGS_DEFAULT_STRICT: frozenset[str] = frozenset({
    "hi", "mr", "bn", "ta", "te", "kn", "ml", "gu", "pa", "ur", "or",
    "as", "sa",
})


def _is_strict_language(intent, lang: str = "") -> bool:
    """Decide whether to emit the STRICT language directive.

    Strict mode (citations + numerals + statute names ALL in target script)
    is the user default for Indian languages because that matches V1
    Lawttorney behaviour. The intent extractor can still flip strict_language
    explicitly — when it does, that decision wins:
      - intent.strict_language = True  → strict (unconditional)
      - intent.strict_language = False → non-strict ONLY when the user has
        explicitly asked for English citations / mixed-script output
        (intent.language_explicit is True). Otherwise default to strict
        for Indian langs so we don't silently mix scripts.
      - intent is None / unset         → default to strict for Indian langs.

    Args:
        intent: Optional typed UserIntent.
        lang:   ISO 639-1 code (e.g. 'mr'). Empty string for the legacy
                callers — they get the prior False-default behaviour.
    """
    if intent is not None:
        v = getattr(intent, "strict_language", None)
        if v is True:
            return True
        if v is False:
            # Honour an EXPLICIT non-strict signal from the intent extractor
            # when the user actually said something explicit. Without that,
            # fall through to the Indian-language default below.
            if bool(getattr(intent, "language_explicit", False)):
                return False
    # Default: strict for Indian languages, non-strict otherwise.
    return lang in _INDIAN_LANGS_DEFAULT_STRICT


# Policy (2026-07-11): Numerals and full statutory references stay in
# English regardless of response language. The prior `_NATIVE_NUMERAL_HINTS`
# dict (Devanagari १,२,३ / Bengali ০-৯ / etc.) was removed together with
# `_NATIVE_DIGITS` + `localize_number` + `strip_leading_numeric_prefix`
# because every response — draft or otherwise — is now expected to emit
# Latin digits and English act names inline. The prompt itself is the
# grounding; the self-refine critic is the safety net when Gemini slips.


def localize_prompt(
    base_prompt: str,
    lang: str,
    intent=None,
    *,
    source_languages: tuple[str, ...] = (),
) -> str:
    """Append a language instruction (and optionally a user-intent directive
    block) to any agent system prompt.

    Behaviour by `lang`:
      - lang == "en"  → ALWAYS appends an English-strict directive instructing
        the model to respond entirely in English and translate any quoted
        non-English content. When `source_languages` includes any non-English
        code, OR when callers explicitly mark strict mode via the intent,
        emits the STRONG variant naming the source script (e.g. "Devanagari")
        as forbidden in the response.
        Rationale: previously this branch was a no-op, leaving Gemini free to
        code-switch into the source-document's language. That produced the
        Marathi-PDF + English-query → mixed-language bug. The directive
        closes that hole at the localization layer so every downstream agent
        inherits the fix.
      - lang != "en" and in SUPPORTED_LANGUAGES → emits the existing
        strict / non-strict directive set (script + numerals + ceremonial-
        block translations). Unchanged.
      - lang unsupported → no language line is appended (intent block only).

    Args:
        base_prompt: The agent's system prompt.
        lang: ISO 639-1 code (e.g. "en", "mr").
        intent: Optional typed UserIntent — drives strict-mode + depth
            directives.
        source_languages: Optional tuple of ISO codes detected in the
            retrieved/uploaded source content (e.g. ("mr",) when a Marathi
            PDF is in play). Used ONLY to escalate the English-strict
            directive — the model is told NOT to leak Marathi script into
            an English response. Empty tuple = caller didn't bother to
            detect (still safe: the default English directive prevents
            most mixing).

    Returns the prompt with directives appended; never mutates input.
    """
    out = base_prompt

    if lang == "en":
        # English path: the previous behaviour was to emit nothing, which left
        # Gemini free to mix in non-English script when source documents (PDFs,
        # ES hits, chat history) were in another language. We now always emit
        # a positive English directive. The strength scales with signal:
        #   - no source-language hint and default intent → soft directive
        #   - non-English source detected OR strict intent → strong directive
        non_en_sources = tuple(s for s in source_languages if s and s != "en")
        strict_intent = bool(intent and getattr(intent, "strict_language", False)
                              and getattr(intent, "language", "en") == "en")
        if non_en_sources or strict_intent:
            source_names = ", ".join(
                SUPPORTED_LANGUAGES.get(s, s) for s in non_en_sources
            )
            source_clause = (
                f" The source content contains {source_names} text — "
                f"TRANSLATE or paraphrase it into English; do NOT quote the "
                f"original script verbatim."
            ) if non_en_sources else ""
            out += (
                f"\n\nLANGUAGE INSTRUCTION (STRICT): Respond entirely in "
                f"English using the Latin script. Do NOT insert any "
                f"non-Latin script anywhere in the response — no Devanagari "
                f"(Hindi/Marathi/Sanskrit), Bengali, Tamil, Telugu, Kannada, "
                f"Malayalam, Gujarati, Gurmukhi, Odia, or Arabic-script "
                f"text.{source_clause}\n\n"
                f"The ONLY narrow exception: a verbatim case-name proper "
                f"noun in a CASE-LAW CITATION (e.g. 'Kesavananda Bharati v. "
                f"State of Kerala, AIR 1973 SC 1461') where the reported "
                f"case name happens to be in a non-Latin script. Even then, "
                f"the surrounding sentence must be in English.\n\n"
                f"This exception does NOT cover the following — each MUST "
                f"be rendered in English / Latin script even when the "
                f"source uses the original script:\n"
                f"- Document identifiers and case-numbering labels — e.g. "
                f"'दस्त क्र. 637/2023' must become 'Document No. 637/2023'; "
                f"'मु.अ.क्र.' → 'Case No.'; 'CTS क्र.' → 'CTS No.'. These "
                f"are common-noun labels for document types, not proper "
                f"nouns; translate the label and keep the number.\n"
                f"- Party names from notices / contracts / FIRs / pleadings "
                f"— transliterate to Latin script (e.g. 'श्री. कैलास "
                f"जगताप' → 'Mr. Kailas Jagtap', 'सौ. निशा' → 'Smt. Nisha').\n"
                f"- Place names, district names, taluka names, village "
                f"names — transliterate to Latin (e.g. 'मौजे शिवरी, ता. "
                f"पुरंदर, जि. पुणे' → 'Mauje Shivri, Tal. Purandar, "
                f"Dist. Pune').\n"
                f"- Statute / act / code titles — translate to standard "
                f"English form (e.g. 'भारतीय करार अधिनियम, १८७२' → "
                f"'Indian Contract Act, 1872').\n"
                f"- Section / article / rule / paragraph numbers — Latin "
                f"digits only (e.g. '१३८' → '138', '२०२३' → '2023').\n"
                f"- All paragraph numbers, dates, monetary amounts, and "
                f"list-item prefixes — Latin digits (1, 2, 3...).\n\n"
                f"When in doubt, render in English. A short Marathi/Hindi "
                f"parenthetical inserted 'for accuracy' is STILL a "
                f"violation — translate it inline instead."
            )
        else:
            out += (
                "\n\nLANGUAGE INSTRUCTION: Respond entirely in English. "
                "If quoted source material is in another language, "
                "translate or paraphrase it into English in your response. "
                "Do not insert non-Latin script (Devanagari, Tamil, etc.) "
                "except for verbatim case-name proper nouns; the "
                "surrounding sentence stays in English."
            )

    elif lang != "en" and lang in SUPPORTED_LANGUAGES:
        lang_name = SUPPORTED_LANGUAGES[lang]
        # Policy (2026-07-11): Numerals + FULL statutory references stay
        # English regardless of strict/non-strict. The only difference:
        # strict additionally forbids stray English clauses in body prose.
        # The per-language ceremonial-block tables below help the LLM keep
        # party-role labels, courtroom forms of address, and placeholder
        # brackets in {lang_name} while leaving digits + Act references
        # untouched.
        if lang == "mr":
            ceremonial_examples = (
                "Court-document ceremonial blocks must be translated to Marathi:\n"
                "- 'Plaintiff / Petitioner' → 'वादी / अर्जदार'\n"
                "- 'Defendant / Respondent' → 'प्रतिवादी / गैरअर्जदार'\n"
                "- 'Versus' → 'विरुद्ध'\n"
                "- 'Prayer' → 'विनंती'\n"
                "- 'Verification' → 'प्रमाणीकरण'\n"
                "- 'Affidavit' → 'शपथपत्र'\n"
                "- 'Hon'ble Court' → 'मा. न्यायालय'\n"
                "- 'Most respectfully sheweth' → 'अत्यंत आदरपूर्वक विनंती'\n"
                "- 'is / are' → 'आहे / आहेत' (Marathi verb)\n"
                "- Possessive 'of' → 'चा / ची / चे / च्या' (Marathi suffixes; "
                "e.g. 'राजूचा अर्ज', 'Section 138 च्या तरतुदी')\n"
                "- 'age' → 'वय'\n"
                "- 'resident of' → 'रा.' (short for 'रहिवासी')\n"
                "- 'district' → 'जिल्हा'\n"
                "- 'tehsil' → 'तालुका'\n"
                "- 'number' (case/serial) → 'क्रमांक'\n"
                "- 'year' → 'सन' or 'वर्ष' (but the digit stays Latin: 'सन 2024')\n"
                "- 'in this matter' → 'या प्रकरणी'\n"
                "- 'and / also' → 'व / तसेच'\n\n"
                "DO NOT use Hindi-specific forms (का/की/के, है/हैं, आयु/उम्र, "
                "निवासी, जिला, तहसील, संख्या) — they are wrong language even "
                "though they share Devanagari script.\n\n"
                "STATUTORY REFERENCE PRINCIPLE (mandatory for every "
                "Marathi drafted document):\n"
                "A statutory reference is emitted as ONE UNINTERRUPTED "
                "ENGLISH SPAN composed of the English label ('Section' / "
                "'Article' / 'Order' / 'Rule'), the canonical English Act "
                "or Code name, and the year in Latin digits. A Marathi "
                "connector carries the anchor into the surrounding prose. "
                "The connector is Marathi; every character inside the "
                "anchor is English.\n"
                "This rule is UNIVERSAL. It covers every Indian statute "
                "regardless of subject-matter (criminal, civil, "
                "constitutional, procedural, revenue, corporate, family, "
                "consumer, tax) and every mention (title, subject line, "
                "body, prayer, verification, footer). The very first "
                "mention and every subsequent mention obey the same rule.\n"
                "You are the drafter, not a mirror of the client. When "
                "the query names an Act in Marathi (e.g. because the "
                "client phrased it casually), you produce filing-quality "
                "output by rendering that Act's canonical English name; "
                "you do not carry the native phrasing into the draft.\n"
                "Neither native section labels (कलम / धारा / अनुच्छेद / "
                "आदेश / नियम) nor native Act titles (translations of "
                "\"Indian Penal Code\", \"Bharatiya Nagarik Suraksha "
                "Sanhita\", \"Right to Information Act\", \"Constitution of "
                "India\", etc. into Devanagari) appear in a drafted legal "
                "document. A Latin digit does not rescue a native label "
                "or a native Act title; the entire anchor is English or "
                "the anchor is wrong.\n\n"
            )
        elif lang == "hi":
            ceremonial_examples = (
                "Court-document ceremonial blocks must be translated to Hindi:\n"
                "- 'Plaintiff / Petitioner' → 'वादी / याचिकाकर्ता / आवेदक'\n"
                "- 'Defendant / Respondent' → 'प्रतिवादी / गैर-आवेदक'\n"
                "- 'Versus' → 'बनाम'\n"
                "- 'Prayer' → 'प्रार्थना'\n"
                "- 'Verification' → 'सत्यापन'\n"
                "- 'Affidavit' → 'शपथ-पत्र'\n"
                "- 'Hon'ble Court' → 'माननीय न्यायालय'\n"
                "- 'Most respectfully sheweth' → 'सादर निवेदन है कि'\n"
                "- 'is / are' → 'है / हैं' (Hindi verb)\n"
                "- Possessive 'of' → 'का / की / के' (Hindi suffixes; "
                "e.g. 'राजू का आवेदन', 'Section 138 की उपधाराएँ')\n"
                "- 'age' → 'आयु' or 'उम्र'\n"
                "- 'resident of' → 'निवासी'\n"
                "- 'district' → 'जिला' (NOT 'जिल्हा' — that is Marathi)\n"
                "- 'tehsil' → 'तहसील' (NOT 'तालुका' — that is Marathi)\n"
                "- 'number' (case/serial) → 'संख्या' (or 'क्रमांक' "
                "only in headers, never in body prose)\n"
                "- 'year' → 'वर्ष' or 'साल' (but the digit stays Latin: 'वर्ष 2024')\n"
                "- 'in this matter' → 'इस मामले में' or 'इस प्रकरण में'\n"
                "- 'and' → 'और / तथा'\n"
                "- 'because' → 'क्योंकि' (Hindi; Marathi is 'कारण की')\n\n"
                "DO NOT use Marathi-specific forms (चा/ची/चे/च्या, आहे/आहेत, "
                "वय, रा., जिल्हा, तालुका, क्रमांक in body prose, सन for year, "
                "या प्रकरणी, अर्जदार, तसेच, व) — they share Devanagari script "
                "with Hindi but are WRONG LANGUAGE. This bug surfaces when "
                "the reference template happens to be Marathi: ignore the "
                "template's vocabulary, write fresh in Hindi grammar.\n\n"
                "STATUTORY REFERENCE PRINCIPLE (mandatory for every "
                "Hindi drafted document):\n"
                "A statutory reference is emitted as ONE UNINTERRUPTED "
                "ENGLISH SPAN composed of the English label ('Section' / "
                "'Article' / 'Order' / 'Rule'), the canonical English Act "
                "or Code name, and the year in Latin digits. A Hindi "
                "connector carries the anchor into the surrounding prose. "
                "The connector is Hindi; every character inside the "
                "anchor is English.\n"
                "This rule is UNIVERSAL. It covers every Indian statute "
                "regardless of subject-matter (criminal, civil, "
                "constitutional, procedural, revenue, corporate, family, "
                "consumer, tax) and every mention (title, subject line, "
                "body, prayer, verification, footer). The very first "
                "mention and every subsequent mention obey the same rule.\n"
                "You are the drafter, not a mirror of the client. When "
                "the query names an Act in Hindi (e.g. because the client "
                "phrased it casually), you produce filing-quality output "
                "by rendering that Act's canonical English name; you do "
                "not carry the native phrasing into the draft.\n"
                "Neither native section labels (धारा / अनुच्छेद / आदेश / "
                "नियम) nor native Act titles (translations of \"Indian "
                "Penal Code\", \"Bharatiya Nagarik Suraksha Sanhita\", "
                "\"Right to Information Act\", \"Constitution of India\", "
                "etc. into Devanagari) appear in a drafted legal document. "
                "A Latin digit does not rescue a native label or a native "
                "Act title; the entire anchor is English or the anchor is "
                "wrong.\n\n"
            )
        elif lang == "sa":
            ceremonial_examples = (
                "Court-document ceremonial blocks must be translated to Sanskrit:\n"
                "- 'Plaintiff' → 'वादी'\n"
                "- 'Defendant' → 'प्रतिवादी'\n"
                "- 'Versus' → 'विरुद्धम्'\n"
                "- 'Prayer' → 'प्रार्थना'\n"
                "- 'Verification' → 'प्रमाणीकरणम्'\n"
                "- 'Affidavit' → 'शपथपत्रम्'\n"
                "- 'Hon'ble Court' → 'माननीय-न्यायालयम्'\n\n"
            )
        # G-30 (P2 2026-08-02): starter ceremonial vocab for the other
        # 10 supported Indic languages. Uses widely-attested Sanskrit-
        # derived legal terminology; individual jurisdictions may prefer
        # local variants — flag through with the intent's
        # additional_instructions if a specific bar-council form is
        # needed. All follow the same fixed-English-anchors rule (digits
        # + statutory refs stay Latin).
        elif lang == "bn":
            ceremonial_examples = (
                "Court-document ceremonial blocks must be translated to Bengali:\n"
                "- 'Plaintiff / Petitioner' → 'বাদী / আবেদনকারী'\n"
                "- 'Defendant / Respondent' → 'বিবাদী / প্রতিবাদী'\n"
                "- 'Versus' → 'বনাম'\n"
                "- 'Prayer' → 'প্রার্থনা'\n"
                "- 'Verification' → 'সত্যায়ন'\n"
                "- 'Affidavit' → 'হলফনামা'\n"
                "- 'Hon'ble Court' → 'মাননীয় আদালত'\n"
                "- 'Most respectfully sheweth' → 'সবিনয়ে নিবেদন এই যে'\n"
                "- 'is / are' → 'হয় / হইতেছেন'\n"
                "- 'resident of' → 'নিবাসী'\n"
                "- 'district' → 'জেলা'\n"
                "- 'year' → 'সাল' (digit stays Latin: 'সাল 2024')\n\n"
            )
        elif lang == "ta":
            ceremonial_examples = (
                "Court-document ceremonial blocks must be translated to Tamil:\n"
                "- 'Plaintiff / Petitioner' → 'வாதி / மனுதாரர்'\n"
                "- 'Defendant / Respondent' → 'பிரதிவாதி / எதிர்மனுதாரர்'\n"
                "- 'Versus' → 'எதிர்'\n"
                "- 'Prayer' → 'வேண்டுகோள்'\n"
                "- 'Verification' → 'உறுதிமொழி'\n"
                "- 'Affidavit' → 'உறுதிமொழிப்படிவம்'\n"
                "- 'Hon'ble Court' → 'மாண்புமிகு நீதிமன்றம்'\n"
                "- 'Most respectfully sheweth' → 'பணிவுடன் விண்ணப்பம் செய்வதாவது'\n"
                "- 'resident of' → 'வசிப்பிடம்'\n"
                "- 'district' → 'மாவட்டம்'\n"
                "- 'year' → 'ஆண்டு' (digit stays Latin: 'ஆண்டு 2024')\n\n"
            )
        elif lang == "te":
            ceremonial_examples = (
                "Court-document ceremonial blocks must be translated to Telugu:\n"
                "- 'Plaintiff / Petitioner' → 'వాది / అర్జీదారు'\n"
                "- 'Defendant / Respondent' → 'ప్రతివాది / ప్రతివాదిదారు'\n"
                "- 'Versus' → 'వర్సెస్' (or 'కు వ్యతిరేకంగా')\n"
                "- 'Prayer' → 'ప్రార్థన'\n"
                "- 'Verification' → 'ధృవీకరణ'\n"
                "- 'Affidavit' → 'అఫిడవిట్'\n"
                "- 'Hon'ble Court' → 'గౌరవనీయ న్యాయస్థానం'\n"
                "- 'resident of' → 'నివాసి'\n"
                "- 'district' → 'జిల్లా'\n"
                "- 'year' → 'సంవత్సరం' (digit stays Latin: 'సంవత్సరం 2024')\n\n"
            )
        elif lang == "kn":
            ceremonial_examples = (
                "Court-document ceremonial blocks must be translated to Kannada:\n"
                "- 'Plaintiff / Petitioner' → 'ವಾದಿ / ಅರ್ಜಿದಾರ'\n"
                "- 'Defendant / Respondent' → 'ಪ್ರತಿವಾದಿ / ಪ್ರತಿವಾದಿಗಳು'\n"
                "- 'Versus' → 'ವಿರುದ್ಧ'\n"
                "- 'Prayer' → 'ಪ್ರಾರ್ಥನೆ'\n"
                "- 'Verification' → 'ದೃಢೀಕರಣ'\n"
                "- 'Affidavit' → 'ಪ್ರಮಾಣಪತ್ರ'\n"
                "- 'Hon'ble Court' → 'ಗೌರವಾನ್ವಿತ ನ್ಯಾಯಾಲಯ'\n"
                "- 'resident of' → 'ನಿವಾಸಿ'\n"
                "- 'district' → 'ಜಿಲ್ಲೆ'\n"
                "- 'year' → 'ವರ್ಷ' (digit stays Latin: 'ವರ್ಷ 2024')\n\n"
            )
        elif lang == "ml":
            ceremonial_examples = (
                "Court-document ceremonial blocks must be translated to Malayalam:\n"
                "- 'Plaintiff / Petitioner' → 'വാദി / ഹര്‍ജിക്കാരന്‍'\n"
                "- 'Defendant / Respondent' → 'പ്രതി / എതിര്‍കക്ഷി'\n"
                "- 'Versus' → 'എതിര്‍'\n"
                "- 'Prayer' → 'അപേക്ഷ'\n"
                "- 'Verification' → 'സാക്ഷ്യപ്പെടുത്തല്‍'\n"
                "- 'Affidavit' → 'സത്യവാങ്മൂലം'\n"
                "- 'Hon'ble Court' → 'ബഹുമാനപ്പെട്ട കോടതി'\n"
                "- 'resident of' → 'താമസക്കാരന്‍'\n"
                "- 'district' → 'ജില്ല'\n"
                "- 'year' → 'വര്‍ഷം' (digit stays Latin: 'വര്‍ഷം 2024')\n\n"
            )
        elif lang == "gu":
            ceremonial_examples = (
                "Court-document ceremonial blocks must be translated to Gujarati:\n"
                "- 'Plaintiff / Petitioner' → 'વાદી / અરજદાર'\n"
                "- 'Defendant / Respondent' → 'પ્રતિવાદી / બિનઅરજદાર'\n"
                "- 'Versus' → 'વિરુદ્ધ'\n"
                "- 'Prayer' → 'વિનંતી'\n"
                "- 'Verification' → 'ખાતરી'\n"
                "- 'Affidavit' → 'સોગંદનામું'\n"
                "- 'Hon'ble Court' → 'મા. અદાલત'\n"
                "- 'resident of' → 'રહેવાસી'\n"
                "- 'district' → 'જિલ્લો'\n"
                "- 'year' → 'વર્ષ' (digit stays Latin: 'વર્ષ 2024')\n\n"
            )
        elif lang == "pa":
            ceremonial_examples = (
                "Court-document ceremonial blocks must be translated to Punjabi (Gurmukhi):\n"
                "- 'Plaintiff / Petitioner' → 'ਵਾਦੀ / ਦਰਖ਼ਾਸਤਗੁਜ਼ਾਰ'\n"
                "- 'Defendant / Respondent' → 'ਪ੍ਰਤੀਵਾਦੀ / ਗੈਰ-ਦਰਖ਼ਾਸਤਗੁਜ਼ਾਰ'\n"
                "- 'Versus' → 'ਬਨਾਮ'\n"
                "- 'Prayer' → 'ਪ੍ਰਾਰਥਨਾ'\n"
                "- 'Verification' → 'ਤਸਦੀਕ'\n"
                "- 'Affidavit' → 'ਹਲਫ਼ਨਾਮਾ'\n"
                "- 'Hon'ble Court' → 'ਮਾਣਯੋਗ ਅਦਾਲਤ'\n"
                "- 'resident of' → 'ਵਾਸੀ'\n"
                "- 'district' → 'ਜ਼ਿਲ੍ਹਾ'\n"
                "- 'year' → 'ਸਾਲ' (digit stays Latin: 'ਸਾਲ 2024')\n\n"
            )
        elif lang == "ur":
            ceremonial_examples = (
                "Court-document ceremonial blocks must be translated to Urdu (Arabic script, RTL):\n"
                "- 'Plaintiff / Petitioner' → 'مدعی / درخواست گزار'\n"
                "- 'Defendant / Respondent' → 'مدعا علیہ / جوابدہندہ'\n"
                "- 'Versus' → 'بمقابلہ'\n"
                "- 'Prayer' → 'استدعا'\n"
                "- 'Verification' → 'تصدیق'\n"
                "- 'Affidavit' → 'حلف نامہ'\n"
                "- 'Hon'ble Court' → 'معزز عدالت'\n"
                "- 'resident of' → 'رہائشی'\n"
                "- 'district' → 'ضلع'\n"
                "- 'year' → 'سنہ' (digit stays Latin: 'سنہ 2024')\n\n"
            )
        elif lang == "or":
            ceremonial_examples = (
                "Court-document ceremonial blocks must be translated to Odia:\n"
                "- 'Plaintiff / Petitioner' → 'ବାଦୀ / ଆବେଦନକାରୀ'\n"
                "- 'Defendant / Respondent' → 'ପ୍ରତିବାଦୀ / ପ୍ରତିପକ୍ଷ'\n"
                "- 'Versus' → 'ବନାମ'\n"
                "- 'Prayer' → 'ପ୍ରାର୍ଥନା'\n"
                "- 'Verification' → 'ସତ୍ୟାପନ'\n"
                "- 'Affidavit' → 'ଶପଥନାମା'\n"
                "- 'Hon'ble Court' → 'ମାନ୍ୟବର ଅଦାଲତ'\n"
                "- 'resident of' → 'ନିବାସୀ'\n"
                "- 'district' → 'ଜିଲ୍ଲା'\n"
                "- 'year' → 'ବର୍ଷ' (digit stays Latin: 'ବର୍ଷ 2024')\n\n"
            )
        elif lang == "as":
            ceremonial_examples = (
                "Court-document ceremonial blocks must be translated to Assamese:\n"
                "- 'Plaintiff / Petitioner' → 'বাদী / আবেদনকাৰী'\n"
                "- 'Defendant / Respondent' → 'বিবাদী / প্ৰতিপক্ষ'\n"
                "- 'Versus' → 'বনাম'\n"
                "- 'Prayer' → 'প্ৰাৰ্থনা'\n"
                "- 'Verification' → 'সত্যাপন'\n"
                "- 'Affidavit' → 'শপত পত্ৰ'\n"
                "- 'Hon'ble Court' → 'মাননীয় ন্যায়ালয়'\n"
                "- 'resident of' → 'নিবাসী'\n"
                "- 'district' → 'জিলা'\n"
                "- 'year' → 'বছৰ' (digit stays Latin: 'বছৰ 2024')\n\n"
            )
        else:
            ceremonial_examples = ""

        # Shared FIXED-ENGLISH ANCHORS block — appears in both strict and
        # non-strict directives. This is the load-bearing part of the
        # 2026-07-11 policy: numerals + statutory references stay English
        # even when the response body is in Hindi / Marathi / etc.
        fixed_english_anchors = (
            "\n\nFIXED-ENGLISH ANCHORS (ALWAYS in English regardless of "
            "response language):\n\n"
            "1. ALL NUMERALS — every digit is Latin (0, 1, 2, 3, 4, 5, 6, "
            "7, 8, 9). This covers paragraph numbers, list-item prefixes, "
            "section / article / rule / order numbers, dates ('15 May 2024' "
            "NOT '१५ मे २०२४'), years ('2023' NOT '२०२३'), monetary amounts "
            "('Rs. 5,00,000' NOT 'रु. ५,००,०००'), addresses, quantities, "
            "ages, cheque numbers, case numbers. Do NOT emit Devanagari "
            "(०-९), Bengali (০-৯), Tamil (௦-௯), Telugu (౦-౯), Kannada "
            "(೦-೯), Malayalam (൦-൯), Gujarati (૦-૯), Gurmukhi (੦-੯), "
            "Odia (୦-୯), or Eastern-Arabic (۰-۹) digits anywhere in the "
            "response.\n\n"
            "2. STATUTORY / STATUTE / ACT / CODE REFERENCES — the full "
            "reference stays English inline:\n"
            "   - 'Section 138 of the Negotiable Instruments Act, 1881'\n"
            "   - 'Article 226 of the Constitution of India'\n"
            "   - 'Order XXXIX Rules 1 and 2 of the Code of Civil "
            "Procedure, 1908'\n"
            "   - 'Section 480 of the Bharatiya Nagarik Suraksha Sanhita, "
            "2023'\n"
            "   - 'Section 34 of the Indian Penal Code, 1860'\n"
            "   Do NOT translate the section / article / rule / order label "
            "to native (कलम, धारा, अनुच्छेद, अध्याय, नियम, आदेश). Do NOT "
            "translate the Act / Code title to native ('भारतीय करार अधिनियम, "
            "१८७२' is WRONG — must be 'Indian Contract Act, 1872'). Do NOT "
            "transliterate section numbers to native (कलम १३८ is WRONG — "
            "must be 'Section 138'). The FULL statutory reference travels "
            f"as one English span; the surrounding clause remains in "
            f"{lang_name}:\n"
            f"   ✓ CORRECT: '... Section 138 of the Negotiable Instruments "
            f"Act, 1881 च्या तरतुदींनुसार, ...'\n"
            f"   ✗ WRONG:   '... परक्राम्य लिखत अधिनियम, १८८१ च्या कलम १३८ "
            f"च्या तरतुदींनुसार, ...'\n"
            f"   Two further patterns that are equally WRONG — both keep "
            f"some English but still break the span:\n"
            f"   ✗ WRONG:   'भारतीय दंड संहिता (Indian Penal Code), 1860 "
            f"की Section 302'\n"
            f"                A native Act title with an English gloss in "
            f"parentheses is still a translated Act title. Do NOT gloss, "
            f"do NOT provide the English in brackets alongside a native "
            f"rendering — emit the English name ONLY.\n"
            f"   ✗ WRONG:   '<native Act name> की / चा Section 302'\n"
            f"                The reference has been re-ordered into "
            f"{lang_name} syntax with the Act name first. The span keeps "
            f"CANONICAL ENGLISH WORD ORDER as a unit — 'Section 302 of "
            f"the Indian Penal Code, 1860' — and a {lang_name} connector "
            f"attaches to the OUTSIDE of that unit, never inside it.\n\n"
            "3. CASE-LAW CITATIONS — party names + reporter citation stay "
            "English: 'Kesavananda Bharati v. State of Kerala, AIR 1973 SC "
            "1461'. Surrounding clause stays in "
            f"{lang_name}.\n\n"
            "4. PLAIN-TEXT EMISSION — the English anchors above (numerals, "
            "statutory references, act / code / rule names, article "
            "numbers, case-law citations) MUST be emitted as PLAIN "
            "RUNNING TEXT, character-for-character part of the surrounding "
            "sentence. They are NOT literals, code identifiers, or "
            "keywords — they are ordinary prose.\n\n"
            "STRICTLY FORBIDDEN wrappers around ANY English anchor (this "
            "is a MAJOR formatting error that the frontend renders in "
            "monospaced typewriter font, visually breaking the paragraph):\n"
            "  - Markdown inline code: the backtick character U+0060 "
            "before or after the anchor (either one or two backticks on "
            "each side). NEVER put a backtick immediately before or after "
            "the words 'Section', 'Article', 'Order', 'Rule', 'Indian', "
            "'Code', 'Bharatiya', 'Constitution', 'Act', or any statutory "
            "phrase, and never close a run of anchor text with a backtick.\n"
            "  - Triple-backtick code fences: opening/closing rows of "
            "three backtick characters around the anchor.\n"
            "  - HTML code tags: the literal characters '<code>' and "
            "'</code>' anywhere adjacent to an anchor.\n"
            "  - Any other verbatim / literal-content formatting.\n\n"
            "PERMITTED formatting AROUND anchors:\n"
            "  - Bold (double-asterisk pairs) is fine when the anchor "
            "sits inside a subject heading.\n"
            "  - Italic (single-asterisk pairs) is fine for emphasis.\n"
            "  - Regular sentence punctuation (comma, full stop, "
            "brackets) is fine.\n\n"
            "The instruction to keep an anchor 'verbatim in English' or "
            "'as one span' is a LANGUAGE / SCRIPT instruction, NOT a "
            "typography instruction. Do NOT reach for code-formatting "
            "syntax to signal 'do not translate' — the surrounding "
            "prompt already tells you not to translate the anchor.\n\n"
            f"CORRECT emission (Marathi example): the sentence '..., "
            f"Section 138 of the Negotiable Instruments Act, 1881 च्या "
            f"तरतुदींनुसार, ...' contains ZERO backtick characters and "
            f"ZERO angle-bracket code tags. The anchor 'Section 138 of "
            f"the Negotiable Instruments Act, 1881' is just seven English "
            f"words plus a comma and a year, sitting inline between "
            f"Marathi words, rendered in the SAME font as those Marathi "
            f"words.\n"
        )

        # Shared SUBSTANTIVE DEPTH block — appended to BOTH the strict and
        # the non-strict directive so the length policy is identical on
        # every regional path.
        #
        # Replaces the bare fragment "Do NOT pad or duplicate content to
        # appear comprehensive." that previously closed the STRICT branch
        # ONLY. That line was the single brevity instruction a regional
        # request received and an English request never did (English block
        # ≈ 300 chars, regional ≈ 5.2 KB), and because strict is the default
        # for every Indian language it reached almost all regional traffic.
        # With response_depth='standard' the intent-directive block that
        # follows is empty, so it landed in the recency position as the
        # literal last sentence of the system prompt — while also
        # contradicting the 'USER DEPTH: comprehensive coverage' directive
        # that _format_intent_directives emits on detailed requests.
        #
        # The anti-repetition intent is preserved; the compression
        # side-effect is removed. Policy: complete legal reasoning without
        # unnecessary repetition. See Issue 3 / Group A —
        # docs/issue3_fix_implementation_plan.md.
        substantive_depth_policy = (
            f"\n\nSUBSTANTIVE DEPTH (length policy):\n"
            f"Responding in {lang_name} changes the SCRIPT and VOCABULARY "
            f"of your answer. It does NOT change its DEPTH. Produce the "
            f"same legal substance you would produce for this identical "
            f"request in English.\n"
            f"- PRESERVE every piece of substantive legal content: legal "
            f"reasoning, factual detail and its application to the matter, "
            f"statutory provisions, section numbers, case-law references, "
            f"explanations, arguments, consequences, and conclusions.\n"
            f"- PRESERVE structure: the same headings, numbered "
            f"paragraphs and list hierarchy appear, in the same order and "
            f"the same count. Do not collapse sections or drop numbering. "
            f"'Unchanged' refers to the STRUCTURE, not to the language of "
            f"the label text — heading wording is translated like all "
            f"other prose (see STRUCTURAL LABELS below).\n"
            f"- Each numbered point, ground, or averment is a DEVELOPED "
            f"paragraph — state the point, give the reasoning that "
            f"supports it, and apply it to the facts. Reducing a point to "
            f"a single-line assertion when reasoning is available is a "
            f"DEFECT, not brevity.\n"
            f"- Do NOT summarise, compress, or shorten substantive legal "
            f"content unless the user explicitly asked for a summary or a "
            f"brief answer.\n"
            f"- Equally, do NOT add redundant or repetitive content merely "
            f"to increase length. Restating the same proposition in "
            f"different words adds no depth. The standard is COMPLETE "
            f"LEGAL REASONING WITHOUT UNNECESSARY REPETITION."
        )

        # Agent system prompts specify their output format with LITERAL
        # English heading templates — Newacts has "### Old Provision:
        # Section X of <Old Act Name>, <Year>" / "### New Provision: ..." /
        # "### Key Differences"; Judgment has "### Key Legal Issues" /
        # "### Detailed Narrative"; Document, Scenario, SCI and GST each
        # have their own. Until this block existed nothing told the model
        # what to do with them, so it copied them verbatim into a Marathi /
        # Hindi answer, which then opened in English and switched language
        # mid-response (observed 2026-08-21 on "भारतीय न्याय संहिता कलम
        # 105 काय सांगते?"). Compliance was a coin flip: the same prompt
        # translated the heading on one run and not the next.
        #
        # This lives here, not in the ten agent prompts, because it is a
        # language-layer rule: every agent that grows a heading scaffold
        # inherits it automatically.
        structural_labels_policy = (
            f"\n\nSTRUCTURAL LABELS (headings, table headers, section "
            f"names):\n"
            f"The output-format instructions above quote heading and label "
            f"text in English ('### New Provision: ...', '### Old "
            f"Provision: ...', '### Key Differences', '### Key Legal "
            f"Issues', 'Section No. | Heading | Brief', '## PDF Links'). "
            f"Those quotes specify STRUCTURE — which sections exist, in "
            f"what order, at what heading level. They are NOT literal "
            f"strings to copy.\n"
            f"- Format acronyms inside a label stay English (PDF, URL, "
            f"FIR, NOC); the words AROUND them are translated. '## PDF "
            f"Links' becomes the {lang_name} phrase for 'PDF links' with "
            f"'PDF' still in Latin letters. The URLs listed under it are "
            f"never altered.\n"
            f"- Translate every heading, sub-heading, table column header, "
            f"labelled list prefix and section name into {lang_name}. A "
            f"heading is prose like any other and follows the response "
            f"language.\n"
            f"- Keep the heading LEVEL and ORDER exactly as specified "
            f"(### stays ###, the section that comes first still comes "
            f"first).\n"
            f"- A heading has exactly TWO parts and they follow "
            f"DIFFERENT rules. The LABEL is translated into {lang_name}. "
            f"The STATUTORY REFERENCE inside it is a fixed English anchor "
            f"and is emitted as ONE UNINTERRUPTED ENGLISH SPAN in "
            f"canonical English word order — 'Section <N> of the <Act "
            f"Name>, <Year>' — exactly as the FIXED-ENGLISH ANCHORS rule "
            f"above requires. Translating the label does NOT license "
            f"translating the reference, and the reference is NOT "
            f"re-ordered into {lang_name} syntax:\n"
            f"    ✓ CORRECT: '### [the natural {lang_name} wording for "
            f"\"New Provision\"]: Section 105 of the Bharatiya Nyaya "
            f"Sanhita, 2023'\n"
            f"    ✗ WRONG:   '### New Provision: Section 105 of the "
            f"Bharatiya Nyaya Sanhita, 2023'\n"
            f"                 (label left in English)\n"
            f"    ✗ WRONG:   '### [label]: भारतीय न्याय संहिता, 2023 चा "
            f"Section 105'\n"
            f"                 (Act name translated AND the reference "
            f"re-ordered into native syntax — the whole span must read "
            f"'Section 105 of the Bharatiya Nyaya Sanhita, 2023')\n"
            f"    ✗ WRONG:   '### [label]: भारतीय दंड संहिता, 1860 की "
            f"धारा 304'\n"
            f"                 (must be 'Section 304 of the Indian Penal "
            f"Code, 1860')\n"
            f"  The bracketed [label] above is a SLOT, and any Devanagari "
            f"in these examples is an illustrative sample of the DEFECT — "
            f"never copy those words into your answer. Write the label in "
            f"idiomatic {lang_name}, using {lang_name}'s own vocabulary "
            f"and not another Indian language's ({lang_name} is the "
            f"response language; a Marathi word in a Hindi answer, or a "
            f"Hindi word in a Marathi answer, is a defect even though "
            f"both use Devanagari).\n"
            f"- The same applies to any opening summary line the format "
            f"asks for ('open with a one-sentence statement of what the "
            f"section deals with'). That sentence is BODY PROSE: write it "
            f"in {lang_name} with only the statutory reference in English. "
            f"An answer whose first sentence is English and whose "
            f"remainder is {lang_name} is a DEFECT — the response is in "
            f"one language from the first word to the last.\n"
            f"- Do NOT emit a heading twice (once in English, once in "
            f"{lang_name}) and do NOT append the English label in "
            f"parentheses. One heading, in {lang_name}."
        )

        if _is_strict_language(intent, lang):
            out += (
                f"\n\nLANGUAGE INSTRUCTION (STRICT): The user demanded PURE "
                f"{lang_name} for the BODY PROSE. Do NOT insert English "
                f"words, phrases, or narrative clauses ('It is submitted "
                f"that…', 'as per', 'in accordance with', 'kindly note') "
                f"into the body prose — write in {lang_name}. Ceremonial "
                f"blocks (party role labels, court address forms), "
                f"placeholder brackets ('[Place]' → '[ठिकाण]'), and "
                f"signature labels are ALSO in {lang_name}."
                f"{fixed_english_anchors}\n"
                f"{ceremonial_examples}"
                f"{structural_labels_policy}"
                f"{substantive_depth_policy}"
            )
        else:
            out += (
                f"\n\nLANGUAGE INSTRUCTION: Respond in {lang_name}. Body "
                f"prose, ceremonial blocks (Plaintiff/Defendant/Versus/"
                f"Prayer/Verification/court forms of address), placeholder "
                f"brackets, and signature labels in {lang_name}."
                f"{fixed_english_anchors}"
                f"{structural_labels_policy}"
                f"{substantive_depth_policy}"
            )

        # Cross-language source mismatch warning: when the user wants the
        # answer in Indian-language X but the retrieved source content is in
        # a DIFFERENT non-English language (e.g. user asked in Hindi, file
        # is Marathi, or vice versa). The per-language ceremonial blocks
        # above only handle the Hindi↔Marathi pair; this generic warning
        # covers every other combination (Tamil source + Hindi query, etc.).
        cross_script = tuple(
            s for s in source_languages
            if s and s != "en" and s != lang and s in SUPPORTED_LANGUAGES
        )
        if cross_script:
            other_names = ", ".join(
                SUPPORTED_LANGUAGES[s] for s in cross_script
            )
            out += (
                f"\n\nSOURCE-LANGUAGE NOTE: The source content (uploaded "
                f"files, retrieved passages, or quoted text) contains "
                f"{other_names} material. Translate or paraphrase that "
                f"material into {lang_name} in your response. Do NOT "
                f"insert {other_names} sentences, clauses, or phrases "
                f"verbatim — write everything in {lang_name}."
            )

    out += _format_intent_directives(intent)
    return out


# ---------------------------------------------------------------------------
# Source-content language detection helper
# ---------------------------------------------------------------------------
#
# Used by callers that have access to retrieved text (uploaded file extract,
# ES hit text, chat-history snippets) to derive the `source_languages` hint
# for `localize_prompt`. Returning a tuple (not a single code) lets callers
# join evidence from multiple sources without per-call branching.
# ---------------------------------------------------------------------------

def detect_source_languages(
    *text_samples: str | None,
    sample_chars: int = 800,
) -> tuple[str, ...]:
    """Detect the dominant language(s) across one or more text samples.

    Each non-empty sample is truncated to `sample_chars` and passed through
    `detect_language()`. The returned tuple is deduplicated and ordered by
    first appearance — useful when callers want a stable signal across
    multiple files.

    Empty / None samples are skipped silently. Returns an empty tuple when
    nothing usable was passed, which `localize_prompt` interprets as "no
    source-language hint" (i.e. defaults to soft directive).
    """
    seen: list[str] = []
    for sample in text_samples:
        if not sample:
            continue
        snippet = sample.strip()[:sample_chars]
        if len(snippet) < 10:
            continue
        lang = detect_language(snippet)
        if lang and lang not in seen:
            seen.append(lang)
    return tuple(seen)


@lru_cache(maxsize=None)
def language_name(lang: str) -> str:
    """Return human-readable name for an ISO code."""
    return SUPPORTED_LANGUAGES.get(lang, "English")


# ---------------------------------------------------------------------------
# NOTE (2026-07-11 policy reversal): the code-level helpers that used to
# transliterate Latin digits to Devanagari / Bengali / Tamil / etc. — namely
# `_NATIVE_DIGITS`, `localize_number()`, `_LEADING_NUMERIC_PREFIX_RE`, and
# `strip_leading_numeric_prefix()` — have been removed. Under the current
# policy, ALL numerals stay Latin regardless of response language, and
# full statutory references stay English inline. The policy is enforced
# exclusively via prompt directives in `localize_prompt` above and the
# self-refine critic — never via code-level regex/substitution. If a future
# regression needs an English-anchor guard, extend the CRITIQUE_PROMPT
# category — do NOT reintroduce a substitution helper here.
# ---------------------------------------------------------------------------
