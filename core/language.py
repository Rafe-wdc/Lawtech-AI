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
      3. If langdetect returns "en" but text is Roman script → check
         Romanized-hint heuristic (catches Hinglish / transliterated queries)
      4. If detected language not in SUPPORTED_LANGUAGES → fall back to "en"

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
        signature label.

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
            "sub-sections, provisos, explanations, and material context."
        )
    party = getattr(intent, "arguments_for_party", "none")
    if party == "plaintiff":
        parts.append(
            "USER PARTY: write in the PLAINTIFF / PETITIONER / APPLICANT / "
            "COMPLAINANT / APPELLANT voice. The document speaks AS that "
            "party. Cause-title still names both sides, but the body, "
            "prayer, verification, and affidavit are the plaintiff-side "
            "party's pleading. Pick the synonym that matches the document "
            "type (Plaint→Plaintiff, Writ Petition→Petitioner, Notice→"
            "Complainant/Sender, Appeal→Appellant). If the document type "
            "is a Written Statement / Reply / Counter-Affidavit "
            "(structurally the responsive pleading), treat the user's "
            "instruction as 'draft the plaintiff's REPLICATION (Order VIII "
            "Rule 9 CPC) / rejoinder' — DO NOT silently flip and write as "
            "the defendant."
        )
    elif party == "defendant":
        parts.append(
            "USER PARTY: write in the DEFENDANT / RESPONDENT / OPPOSITE "
            "PARTY / ACCUSED voice. The document speaks AS that party. "
            "Cause-title still names both sides, but the body, prayer, "
            "verification, and affidavit are the defendant-side party's "
            "pleading. Pick the synonym that matches the document type "
            "(Written Statement→Defendant, Counter-Affidavit→Respondent, "
            "Bail Application→Accused/Applicant)."
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


# Native-script numeral examples for the strict-language directive.
# This is prompt-side grounding data, not routing logic — the LLM picks
# the right glyphs for the target language based on the example listed
# here. Removing the dict and trusting the LLM to recall scripts on its
# own caused a measurable regression in Marathi WS smoke (95.4% → 83.6%
# Devanagari, Latin-digit numbered starts 24 → 84). The explicit glyph
# anchors are doing real work — they stay. Adding a new language with
# its own numeral system = one entry here.
_NATIVE_NUMERAL_HINTS: dict[str, str] = {
    "hi": "Devanagari numerals (०, १, २, ३, ४, ५, ६, ७, ८, ९)",
    "mr": "Devanagari numerals (०, १, २, ३, ४, ५, ६, ७, ८, ९)",
    "sa": "Devanagari numerals (०, १, २, ३, ४, ५, ६, ७, ८, ९)",
    "bn": "Bengali numerals (০, ১, ২, ৩, ৪, ৫, ৬, ৭, ৮, ৯)",
    "as": "Bengali-Assamese numerals (০, ১, ২, ৩, ৪, ৫, ৬, ৭, ৮, ৯)",
    "or": "Odia numerals (୦, ୧, ୨, ୩, ୪, ୫, ୬, ୭, ୮, ୯)",
    "te": "Telugu numerals (౦, ౧, ౨, ౩, ౪, ౫, ౬, ౭, ౮, ౯)",
    "ta": "Tamil numerals (௦, ௧, ௨, ௩, ௪, ௫, ௬, ௭, ௮, ௯)",
    "kn": "Kannada numerals (೦, ೧, ೨, ೩, ೪, ೫, ೬, ೭, ೮, ೯)",
    "ml": "Malayalam numerals (൦, ൧, ൨, ൩, ൪, ൫, ൬, ൭, ൮, ൯)",
    "gu": "Gujarati numerals (૦, ૧, ૨, ૩, ૪, ૫, ૬, ૭, ૮, ૯)",
    "pa": "Gurmukhi numerals (੦, ੧, ੨, ੩, ੪, ੫, ੬, ੭, ੮, ੯)",
    "ur": "Eastern Arabic numerals (۰, ۱, ۲, ۳, ۴, ۵, ۶, ۷, ۸, ۹)",
}


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
        if _is_strict_language(intent, lang):
            numeral_hint = _NATIVE_NUMERAL_HINTS.get(lang)
            numeral_line = (
                f"Use {numeral_hint} for ALL numbers — paragraph numbers, "
                f"dates, years, amounts, list items. "
            ) if numeral_hint else ""
            # NB: Phrasing matters. Earlier drafts used "keep X, Y, Z in
            # English" which the LLM read as a green light to keep
            # citations in English wholesale. The phrasing below frames
            # English as a NARROW exception ("only verbatim case names")
            # so the model treats Marathi/Hindi/etc. as the default for
            # everything else.
            # Court-document ceremonial-block examples — extend hand to
            # the model by showing exact translations for the standard
            # court-document scaffolding. Hindi and Marathi share
            # Devanagari script but differ in grammar: emit DIFFERENT
            # ceremonial blocks for each so the LLM doesn't blend them.
            # (Bug report 2026-06-19: Hindi requested, Marathi delivered —
            # the model picked up Marathi forms from the template AND from
            # the previously-shared ceremonial block which lumped both
            # together as "वादी (Marathi/Hindi: वादी)". Splitting per-
            # language removes that ambiguity.)
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
                    "e.g. 'राजूचा अर्ज', 'कलम १३८ च्या तरतुदी')\n"
                    "- 'age' → 'वय'\n"
                    "- 'resident of' → 'रा.' (short for 'रहिवासी')\n"
                    "- 'district' → 'जिल्हा'\n"
                    "- 'tehsil' → 'तालुका'\n"
                    "- 'number' (case/serial) → 'क्रमांक'\n"
                    "- 'year' → 'सन' or 'वर्ष'\n"
                    "- 'in this matter' → 'या प्रकरणी'\n"
                    "- 'and / also' → 'व / तसेच'\n\n"
                    "DO NOT use Hindi-specific forms (का/की/के, है/हैं, आयु/उम्र, "
                    "निवासी, जिला, तहसील, संख्या) — they are wrong language even "
                    "though they share Devanagari script.\n\n"
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
                    "e.g. 'राजू का आवेदन', 'धारा १३८ की उपधाराएँ')\n"
                    "- 'age' → 'आयु' or 'उम्र'\n"
                    "- 'resident of' → 'निवासी'\n"
                    "- 'district' → 'जिला' (NOT 'जिल्हा' — that is Marathi)\n"
                    "- 'tehsil' → 'तहसील' (NOT 'तालुका' — that is Marathi)\n"
                    "- 'number' (case/serial) → 'संख्या' (or 'क्रमांक' "
                    "only in headers, never in body prose)\n"
                    "- 'year' → 'वर्ष' or 'साल'\n"
                    "- 'in this matter' → 'इस मामले में' or 'इस प्रकरण में'\n"
                    "- 'and' → 'और / तथा'\n"
                    "- 'because' → 'क्योंकि' (Hindi; Marathi is 'कारण की')\n\n"
                    "DO NOT use Marathi-specific forms (चा/ची/चे/च्या, आहे/आहेत, "
                    "वय, रा., जिल्हा, तालुका, क्रमांक in body prose, सन for year, "
                    "या प्रकरणी, अर्जदार, तसेच, व) — they share Devanagari script "
                    "with Hindi but are WRONG LANGUAGE. This bug surfaces when "
                    "the reference template happens to be Marathi: ignore the "
                    "template's vocabulary, write fresh in Hindi grammar.\n\n"
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
            else:
                ceremonial_examples = ""
            out += (
                f"\n\nLANGUAGE INSTRUCTION (STRICT): The user demanded PURE "
                f"{lang_name}. Do NOT mix in English words, phrases, or "
                f"clauses anywhere in the response. {numeral_line}"
                f"Translate act/code titles ('Civil Procedure Code, 1908' "
                f"→ 'दिवाणी प्रक्रिया संहिता, १९०८'), section labels "
                f"('Section 138' → 'कलम १३८'), placeholder brackets "
                f"('[Place]' → '[ठिकाण]'), and signature labels into "
                f"{lang_name}.\n\n"
                f"{ceremonial_examples}"
                f"The ONLY narrow exception: when citing a specific "
                f"case-law decision (e.g. 'Kesavananda Bharati v. State of "
                f"Kerala'), preserve the case name as printed — case names "
                f"are proper nouns. Even then, the surrounding clause "
                f"(\"In the case of …, the court held …\") must be in "
                f"{lang_name}.\n\n"
                f"Do NOT pad or duplicate content to appear comprehensive."
            )
        else:
            out += (
                f"\n\nLANGUAGE INSTRUCTION: Respond entirely in {lang_name}. "
                f"The ONLY content that may stay in English is a verbatim "
                f"case-law citation block — the printed party names + reporter "
                f"cite of a real decided case (e.g. 'Mohan Lal v. State of "
                f"Punjab, (2018) 17 SCC 627'). Surrounding clause stays in "
                f"{lang_name}.\n"
                f"Everything else MUST be in {lang_name}: act / code / "
                f"statute titles ('Section 480 of the Bharatiya Nagarik "
                f"Suraksha Sanhita, 2023' → '{lang_name} equivalent'), "
                f"section / article / rule labels, court names, party-role "
                f"labels, placeholder brackets, and inline references. "
                f"DO NOT append English citation tails like 'as per Section "
                f"X of the <English Act name>, YYYY' or 'in accordance with "
                f"the <English Act>' — translate the full reference inline."
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
# Numeral script mapping — used by the drafting assembler (and any other
# layer that needs to render an integer in the user's native script).
#
# Hindi / Marathi / Sanskrit drafts MUST render section numbers in Devanagari
# (१, २, ३), not Latin (1, 2, 3) — mixing the two is a major mismatch the
# self-refine critic already flags. Same applies to every Indic script with
# a distinct digit family. Languages whose written form uses Latin digits
# (English, plus any future Latin-script language) keep ASCII digits.
# ---------------------------------------------------------------------------

# Per-language Latin → native digit map. Keys are ISO 639-1 codes.
# Only languages whose digit family DIFFERS from Latin are listed here;
# anything else falls through to Latin.
_NATIVE_DIGITS: dict[str, str] = {
    "hi": "०१२३४५६७८९",
    "mr": "०१२३४५६७८९",
    "sa": "०१२३४५६७८९",
    "bn": "০১২৩৪৫৬৭৮৯",
    "as": "০১২৩৪৫৬৭৮৯",
    "or": "୦୧୨୩୪୫୬୭୮୯",
    "te": "౦౧౨౩౪౫౬౭౮౯",
    "ta": "௦௧௨௩௪௫௬௭௮௯",
    "kn": "೦೧೨೩೪೫೬೭೮೯",
    "ml": "൦൧൨൩൪൫൬൭൮൯",
    "gu": "૦૧૨૩૪૫૬૭૮૯",
    "pa": "੦੧੨੩੪੫੬੭੮੯",
    "ur": "۰۱۲۳۴۵۶۷۸۹",
}


def localize_number(n: int, lang: str) -> str:
    """Render an integer in the user_language's native digit script.

    Falls back to Latin digits ("1", "23") when:
      - lang is English / unrecognised
      - n is non-positive (uncommon caller case; pass through as-is)

    Used by the drafting assembler to localize section-number prefixes
    ("## १. ..." in Hindi instead of "## 1. ..."), and is safe for any
    other layer that wants a script-localized integer.
    """
    if n < 0:
        return str(n)  # negatives are a caller bug; don't transliterate the sign
    digits = _NATIVE_DIGITS.get(lang)
    if not digits:
        return str(n)
    return "".join(digits[int(c)] for c in str(n))


# Regex matching a leading numeric prefix in ANY Indic / Arabic-Indic / Latin
# digit script, optionally followed by `.`, `)`, `:`, `-`, or whitespace.
# Used to strip prefixes the outline LLM put on section titles (e.g.
# "१. याचिका के तथ्य" → "याचिका के तथ्य") so the assembler's own
# localized prefix doesn't produce double-numbered headings.
_LEADING_NUMERIC_PREFIX_RE = re.compile(
    r"^\s*[0-9"
    # Hindi/Marathi/Sanskrit Devanagari (U+0966-096F)
    r"०-९"
    # Bengali / Assamese (U+09E6-09EF)
    r"০-৯"
    # Odia (U+0B66-0B6F)
    r"୦-୯"
    # Telugu (U+0C66-0C6F)
    r"౦-౯"
    # Tamil (U+0BE6-0BEF)
    r"௦-௯"
    # Kannada (U+0CE6-0CEF)
    r"೦-೯"
    # Malayalam (U+0D66-0D6F)
    r"൦-൯"
    # Gujarati (U+0AE6-0AEF)
    r"૦-૯"
    # Gurmukhi (U+0A66-0A6F)
    r"੦-੯"
    # Extended Arabic-Indic (Urdu) (U+06F0-06F9)
    r"۰-۹"
    r"]+[\.\)\:\-\s]+",
)


def strip_leading_numeric_prefix(title: str) -> str:
    """Remove any leading numeric prefix the outline LLM put on a title.

    Examples:
      "१. याचिका के तथ्य"     → "याचिका के तथ्य"
      "1. Statement of Facts" → "Statement of Facts"
      "(1) Prayer"            → "(1) Prayer"  (only LEADING `1.` style stripped)
      "Statement of Facts"    → "Statement of Facts"  (no-op)
    """
    if not title:
        return title
    return _LEADING_NUMERIC_PREFIX_RE.sub("", title, count=1).strip()
