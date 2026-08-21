# -*- coding: utf-8 -*-
"""Local multilingual smoke pack — the language fixes of 2026-08-21.

Covers the three defects fixed on this branch:

  1. A query typed in a native script came back in ENGLISH. Cause: agent call
     sites passed an already-localized prompt to `web_search_fallback` but no
     `user_language=`, so the fallback re-localized with its "en" default and
     appended an English-strict directive AFTER the caller's one.
  2. A Marathi query was detected as HINDI. Cause: langdetect's hi/mr profiles
     overlap on shared legal vocabulary; `core.language` now scores
     grammatical function words instead.
  3. The answer opened in English — an English heading scaffold
     ("### New Provision:") and an English lead sentence — then switched
     language mid-response. Cause: agent prompts quote their output format in
     English and nothing told the model those quotes are structure, not text.

Usage
-----
    # start the API first, then:
    python tests/multilingual_smoke.py --port 5000
    python tests/multilingual_smoke.py --port 5000 --group B      # one group
    python tests/multilingual_smoke.py --port 5000 --id B1 --id B2
    python tests/multilingual_smoke.py --list                     # no API calls

Set API_KEYS in .env and the script picks the first key up automatically.
Each case prints its headings, its lead sentence, and any DEFECT lines, so a
failure tells you WHICH rule broke rather than just "wrong".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover — older interpreters / redirected output
    pass

from core.language import SUPPORTED_LANGUAGES, devanagari_language, dominant_script

# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

LATIN = re.compile(r"[A-Za-z]")
NATIVE_DIGITS = re.compile(r"[०-९০-৯੦-੯૦-૯୦-୯௦-௯౦-౯೦-೯൦-൯۰-۹]")
NATIVE_ACT_TITLE = re.compile(r"(संहिता|अधिनियम|कायदा|আইন|சட்டம்|చట్టం|કાયદો)")
NATIVE_SECTION_LABEL = re.compile(r"(धारा|कलम|अनुच्छेद)\s*\d")
GLOSSED_ACT = re.compile(r"(संहिता|अधिनियम)\s*\(")
REORDERED_REF = re.compile(
    r"(संहिता|अधिनियम|कायदा)[^\n]{0,25}(की|का|चा|च्या)\s+(Section|Article)\s*\d")

# Whole tokens that belong to exactly one of the Devanagari pair. Kept
# deliberately small — a word that exists in BOTH languages produces a false
# "wrong-language" report. Dropped after live testing: "मुद्दे" and "अंतर"
# (both are ordinary Hindi AND Marathi), "फरक"/"बिंदु"/"करनी" (borderline).
MARATHI_ONLY = ("तरतूद", "जुनी", "नवीन", "आहे", "आहेत", "नाही", "करावी", "म्हणजे")
HINDI_ONLY = ("प्रावधान", "पुराना", "नया", "है", "हैं", "नहीं", "क्या", "बताइए")

SCRIPT_OF_LANG = {
    "hi": "deva", "mr": "deva", "sa": "deva", "bn": "beng", "as": "beng",
    "pa": "guru", "gu": "gujr", "or": "orya", "ta": "taml", "te": "telu",
    "kn": "knda", "ml": "mlym", "ur": "arab", "en": None,
}


def audit(text: str, expect_lang: str) -> list[str]:
    """Return a list of human-readable defects. Empty list == clean."""
    defects: list[str] = []
    lines = [l for l in text.split("\n") if l.strip()]
    if not lines:
        return ["empty response"]

    headings = [l.strip() for l in lines if l.strip().startswith("#")]
    lead = next((l for l in lines if not l.strip().startswith("#")), "")
    body = "\n".join(l for l in lines if not l.strip().startswith("#"))
    want_script = SCRIPT_OF_LANG.get(expect_lang)

    if expect_lang == "en":
        # English must stay English: no native script anywhere except a
        # verbatim case-name proper noun, which these prompts never produce.
        if dominant_script(text) is not None:
            defects.append(f"English request answered in {dominant_script(text)} script")
        return defects

    # 1. overall script
    got_script = dominant_script(text)
    if got_script != want_script:
        defects.append(
            f"response script is {got_script or 'latin'}, expected {want_script} "
            f"({SUPPORTED_LANGUAGES.get(expect_lang, expect_lang)})")

    # 2. headings translated, anchors inside them left English
    for h in headings:
        label = h.lstrip("#").strip()
        if not re.search(r"[^\x00-\x7F]", label):
            defects.append(f"heading label still in English: {label[:60]}")
        if NATIVE_ACT_TITLE.search(label) or NATIVE_SECTION_LABEL.search(label):
            defects.append(f"statutory reference translated in heading: {label[:60]}")

    # 3. opening sentence
    native_chars = len(re.findall(r"[^\x00-\x7F]", lead))
    if native_chars <= len(LATIN.findall(lead)):
        defects.append(f"opening sentence is English: {lead[:70]}")

    # 4. fixed-English anchors in the body
    if NATIVE_DIGITS.search(text):
        defects.append("native-script digits in the response (must be Latin 0-9)")
    if GLOSSED_ACT.search(body):
        defects.append("native Act title with an English gloss in parentheses")
    if REORDERED_REF.search(body):
        defects.append("statutory reference re-ordered into native word order")
    if NATIVE_SECTION_LABEL.search(body):
        m = NATIVE_SECTION_LABEL.search(body)
        defects.append(f"native section label in body: '{m.group(0)}' (use 'Section N')")

    # 5. Devanagari pair drift
    if expect_lang in ("hi", "mr"):
        tokens = set(re.findall(r"[ऀ-ॿ]+", text))
        for w in (HINDI_ONLY if expect_lang == "mr" else MARATHI_ONLY):
            if w in tokens:
                defects.append(f"wrong-language word for {expect_lang}: '{w}'")
        verdict = devanagari_language(text)
        if verdict and verdict != expect_lang:
            defects.append(f"function-word verdict is {verdict}, expected {expect_lang}")

    return defects


# ---------------------------------------------------------------------------
# The pack
# ---------------------------------------------------------------------------
# group A — auto-detection, one per supported language (the headline bug)
# group B — Marathi vs Hindi, the pair langdetect flips
# group C — the web-fallback path (Legal_Concepts is ALWAYS web-grounded)
# group D — heading scaffolds (Newacts old<->new mapping, Judgment templates)
# group E — fixed-English anchors under pressure
# group F — explicit language directives, including Romanized
# group G — strict language
# group H — English regression guards (must NOT flip to a native language)
# group I — follow-up turns
# group J — client preferred_language override
# group K — native-language drafting

CASES: list[dict] = [
    # ---- A. auto-detection ------------------------------------------------
    dict(id="A1", group="A", lang="mr", expect="mr", agent="Legal_Concepts",
         prompt="मालमत्तेच्या विभाजनाची प्रक्रिया काय आहे?",
         note="The exact query that returned English before the fix."),
    dict(id="A2", group="A", lang="hi", expect="hi", agent="Legal_Concepts",
         prompt="संपत्ति के बँटवारे की प्रक्रिया क्या है?"),
    dict(id="A3", group="A", lang="bn", expect="bn", agent="Legal_Concepts",
         prompt="সম্পত্তি বিভাজনের আইনি প্রক্রিয়া কী?"),
    dict(id="A4", group="A", lang="ta", expect="ta", agent="Legal_Concepts",
         prompt="சொத்து பிரிவினைக்கான சட்ட நடைமுறை என்ன?"),
    dict(id="A5", group="A", lang="te", expect="te", agent="Legal_Concepts",
         prompt="ఆస్తి విభజన కోసం చట్టపరమైన ప్రక్రియ ఏమిటి?"),
    dict(id="A6", group="A", lang="kn", expect="kn", agent="Legal_Concepts",
         prompt="ಆಸ್ತಿ ವಿಭಜನೆಯ ಕಾನೂನು ಪ್ರಕ್ರಿಯೆ ಏನು?"),
    dict(id="A7", group="A", lang="ml", expect="ml", agent="Legal_Concepts",
         prompt="സ്വത്ത് വിഭജനത്തിനുള്ള നിയമ നടപടികൾ എന്തൊക്കെയാണ്?"),
    dict(id="A8", group="A", lang="gu", expect="gu", agent="Legal_Concepts",
         prompt="મિલકતના ભાગલા માટેની કાનૂની પ્રક્રિયા શું છે?"),
    dict(id="A9", group="A", lang="pa", expect="pa", agent="Legal_Concepts",
         prompt="ਜਾਇਦਾਦ ਦੀ ਵੰਡ ਦੀ ਕਾਨੂੰਨੀ ਪ੍ਰਕਿਰਿਆ ਕੀ ਹੈ?"),
    dict(id="A10", group="A", lang="ur", expect="ur", agent="Legal_Concepts",
         prompt="جائیداد کی تقسیم کا قانونی طریقہ کار کیا ہے؟"),
    dict(id="A11", group="A", lang="or", expect="or", agent="Legal_Concepts",
         prompt="ସମ୍ପତ୍ତି ବିଭାଜନର ଆଇନଗତ ପ୍ରକ୍ରିୟା କ'ଣ?"),

    # ---- B. Marathi vs Hindi ---------------------------------------------
    dict(id="B1", group="B", lang="mr", expect="mr", agent="Newacts",
         prompt="भारतीय न्याय संहिता कलम 105 काय सांगते?",
         note="langdetect says 'hi'; function-word markers must say 'mr'."),
    dict(id="B2", group="B", lang="hi", expect="hi", agent="Newacts",
         prompt="भारतीय न्याय संहिता की धारा 105 क्या कहती है?",
         note="Same question in Hindi — must NOT drift to Marathi."),
    dict(id="B3", group="B", lang="mr", expect="mr", agent="Legislation",
         prompt="कलम 125 सीआरपीसी नुसार पोटगी कशी मिळते?",
         note="Second query langdetect got wrong."),
    dict(id="B4", group="B", lang="hi", expect="hi", agent="Legislation",
         prompt="धारा 125 सीआरपीसी के अनुसार भरण पोषण कैसे मिलता है?"),
    dict(id="B5", group="B", lang="mr", expect="mr", agent="Newacts",
         prompt="बीएनएस कलम 103 म्हणजे काय आणि जुन्या कायद्यात ते कोणते कलम होते?"),
    dict(id="B6", group="B", lang="hi", expect="hi", agent="Newacts",
         prompt="बीएनएस धारा 103 क्या है और पुराने कानून में यह कौन सी धारा थी?"),

    # ---- C. web-fallback path --------------------------------------------
    dict(id="C1", group="C", lang="mr", expect="mr", agent="Legal_Concepts",
         prompt="जामीन म्हणजे काय आणि तो कसा मिळवावा?",
         note="Legal_Concepts is ALWAYS web-grounded — the path that was 100% broken."),
    dict(id="C2", group="C", lang="hi", expect="hi", agent="Legal_Concepts",
         prompt="अग्रिम जमानत क्या होती है और कैसे मिलती है?"),
    dict(id="C3", group="C", lang="ta", expect="ta", agent="Legal_Concepts",
         prompt="முன்ஜாமீன் என்றால் என்ன, அதை எப்படி பெறுவது?"),
    dict(id="C4", group="C", lang="mr", expect="mr", agent="Legal_Concepts",
         prompt="कंपनी नोंदणीची प्रक्रिया आणि आवश्यक कागदपत्रे कोणती?",
         note="Obscure enough to miss ES and fall through to web search."),

    # ---- D. heading scaffolds --------------------------------------------
    dict(id="D1", group="D", lang="mr", expect="mr", agent="Newacts",
         prompt="आयपीसी कलम 420 चा भारतीय न्याय संहितेतील समकक्ष कोणता?",
         note="Newacts old<->new layout: '### Old Provision' / '### New Provision' "
              "/ '### Key Differences' must all come back translated."),
    dict(id="D2", group="D", lang="hi", expect="hi", agent="Newacts",
         prompt="आईपीसी की धारा 420 का भारतीय न्याय संहिता में समकक्ष प्रावधान क्या है?"),
    dict(id="D3", group="D", lang="mr", expect="mr", agent="Judgment",
         prompt="धनादेश अनादर प्रकरणी महत्त्वाचे उच्च न्यायालयाचे निकाल सांगा",
         note="Judgment template headings (### Key Legal Issues etc)."),
    dict(id="D4", group="D", lang="hi", expect="hi", agent="SCI_Judgment",
         prompt="अनुच्छेद 21 पर सुप्रीम कोर्ट के प्रमुख फैसले बताइए",
         note="SCI template + PDF Links block label."),
    dict(id="D5", group="D", lang="mr", expect="mr", agent="Newacts",
         prompt="बीएनएसएस कलम 480 आणि जुन्या सीआरपीसी कलम 437 मधील फरक सांगा"),

    # ---- E. fixed-English anchors ----------------------------------------
    dict(id="E1", group="E", lang="mr", expect="mr", agent="Legislation",
         prompt="कलम 138 अंतर्गत धनादेश न वटल्यास काय कारवाई करावी?",
         note="Expect 'Section 138 of the Negotiable Instruments Act, 1881' "
              "in Latin, wrapped by a Marathi connector."),
    dict(id="E2", group="E", lang="hi", expect="hi", agent="Legislation",
         prompt="परक्राम्य लिखत अधिनियम की धारा 138 की प्रक्रिया बताइए",
         note="User types the NATIVE Act name — the answer must still use the "
              "English one. This is the hardest anchor case."),
    dict(id="E3", group="E", lang="mr", expect="mr", agent="Constitution",
         prompt="भारतीय संविधानातील अनुच्छेद 21 काय सांगते?",
         note="Expect 'Article 21 of the Constitution of India'."),
    dict(id="E4", group="E", lang="hi", expect="hi", agent="Legislation",
         prompt="सीपीसी के आदेश 39 नियम 1 और 2 के तहत निषेधाज्ञा कैसे मिलती है?",
         note="Expect 'Order XXXIX Rules 1 and 2'; also checks Latin digits."),

    # ---- F. explicit language directives ---------------------------------
    dict(id="F1", group="F", lang="en", expect="mr", agent="Legislation",
         prompt="Explain Section 131 of the Indian Evidence Act in Marathi",
         note="Latin script, explicit directive — langdetect says 'en', the "
              "intent extractor must catch it."),
    dict(id="F2", group="F", lang="en", expect="hi", agent="Legal_Concepts",
         prompt="Explain anticipatory bail in Hindi"),
    dict(id="F3", group="F", lang="en", expect="ta", agent="Legal_Concepts",
         prompt="Explain the process of filing a consumer complaint in Tamil"),
    dict(id="F4", group="F", lang="hi", expect="mr", agent="Legal_Concepts",
         prompt="जमानत के बारे में मराठी में बताइए",
         note="Query in Hindi, answer requested in Marathi — the directive wins "
              "over the script."),
    dict(id="F5", group="F", lang="en", expect="hi", agent="Legislation",
         prompt="Section 138 ke baare mein Hindi mein bataiye",
         note="Romanized directive."),
    dict(id="F6", group="F", lang="en", expect="mr", agent="Legal_Concepts",
         prompt="Property partition process Marathi madhe sanga",
         note="Romanized Marathi directive."),

    # ---- G. strict language ----------------------------------------------
    dict(id="G1", group="G", lang="mr", expect="mr", agent="Legislation",
         prompt="फक्त मराठीत उत्तर द्या — कलम 302 म्हणजे काय?",
         note="strict_language=True. Digits and statutory references STILL "
              "stay English — strictness bans English narrative prose, not anchors."),
    dict(id="G2", group="G", lang="hi", expect="hi", agent="Legislation",
         prompt="सिर्फ हिंदी में बताइए — धारा 302 क्या है?"),

    # ---- H. English regression guards ------------------------------------
    dict(id="H1", group="H", lang="en", expect="en", agent="Legal_Concepts",
         prompt="What is the procedure for partition of property under Hindu law?"),
    dict(id="H2", group="H", lang="en", expect="en", agent="Newacts",
         prompt="Compare Section 302 IPC with BNS Section 103",
         note="Act acronyms must NOT be read as a Hindi signal."),
    dict(id="H3", group="H", lang="en", expect="en", agent="Legislation",
         prompt="What are the essential ingredients of Section 138 of the "
                "Negotiable Instruments Act, 1881?"),
    dict(id="H4", group="H", lang="en", expect="en", agent="Drafting",
         prompt=("Draft a civil suit for partition of ancestral property. "
                 "The plaintiff is Ramesh Kumar, aged 52, resident of Pune. "
                 "The suit property is a residential house at Kothrud, Pune. "
                 "Include the cause title, facts, cause of action, and prayer. "
                 "मराठीत एक परिच्छेद जोडा."),
         note="English-dominant prompt with ONE native sentence — must stay "
              "English. Guards the script-dominance ratio."),

    # ---- K. native-language drafting -------------------------------------
    dict(id="K1", group="K", lang="mr", expect="mr", agent="Drafting",
         prompt="मराठीत परस्पर संमतीने घटस्फोटाचा अर्ज तयार करा",
         note="Ceremonial blocks (वादी/प्रतिवादी/विरुद्ध/विनंती) in Marathi; "
              "statutory references and all digits in Latin."),
    dict(id="K2", group="K", lang="hi", expect="hi", agent="Drafting",
         prompt="हिंदी में किरायेदार को बेदखली का कानूनी नोटिस तैयार करें"),
]

# Multi-turn and header-based cases are described but not auto-run: they need a
# thread id / a request header, so they are listed for manual testing.
MANUAL_CASES = [
    dict(id="I1", group="I", desc="Follow-up keeps the language",
         steps=["Turn 1: मालमत्तेच्या विभाजनाची प्रक्रिया काय आहे?",
                "Turn 2 (same thread): आणि न्यायालयात दावा कसा दाखल करावा?"],
         expect="Both turns answered in Marathi. Turn 2 is short and would "
                "detect as ambiguous on its own — it must inherit Marathi."),
    dict(id="I2", group="I", desc="Language switch mid-thread",
         steps=["Turn 1: Draft a legal notice for breach of contract",
                "Turn 2 (same thread): Convert the above text into Marathi"],
         expect="Turn 2 returns the SAME notice in Marathi, same length, "
                "statutory references still English."),
    dict(id="I3", group="I", desc="Follow-up must not drift Hindi->Marathi",
         steps=["Turn 1: धारा 302 का क्या मतलब है?",
                "Turn 2 (same thread): और सजा कितनी होती है?"],
         expect="Both in Hindi. No Marathi words (तरतूद / आहे / फरक)."),
    dict(id="J1", group="J", desc="Client preference wins over the script",
         steps=["POST with preferred_language='en' and prompt "
                "'मालमत्तेच्या विभाजनाची प्रक्रिया काय आहे?'"],
         expect="English answer. An explicit client preference is never "
                "second-guessed by detection or by the intent extractor."),
    dict(id="J2", group="J", desc="Client preference on an English query",
         steps=["POST with preferred_language='hi' and prompt "
                "'What is the procedure for partition of property?'"],
         expect="Hindi answer."),
    dict(id="J3", group="J", desc="Frontend selector",
         steps=["In frontend.html set the language dropdown to Auto-detect, "
                "then re-run A1. Then set it to English and re-run A1."],
         expect="Auto -> Marathi. English -> English. The dropdown is "
                "persisted in localStorage, so a stale selection explains a "
                "'detection is broken' report that is really a client override."),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def api_key() -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))
    except Exception:
        pass
    return (os.getenv("API_KEYS", "") or "").split(",")[0].strip()


def ask(port: str, prompt: str, key: str, timeout: int = 300) -> str:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/pyapi/search",
        json.dumps({"Promptquery": prompt}).encode(),
        {"Content-Type": "application/json", "X-API-Key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read()).get("result", "")


def show_manual() -> None:
    print("\nMANUAL CASES (need a thread id or a request header)\n" + "=" * 70)
    for c in MANUAL_CASES:
        print(f"\n[{c['id']}] {c['desc']}")
        for s in c["steps"]:
            print(f"    - {s}")
        print(f"    EXPECT: {c['expect']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="5000")
    ap.add_argument("--group", action="append", default=None,
                    help="run only these groups (A-K), repeatable")
    ap.add_argument("--id", action="append", default=None,
                    help="run only these case ids, repeatable")
    ap.add_argument("--list", action="store_true", help="print the pack, run nothing")
    args = ap.parse_args()

    selected = CASES
    if args.group:
        groups = {g.upper() for g in args.group}
        selected = [c for c in selected if c["group"] in groups]
    if args.id:
        ids = {i.upper() for i in args.id}
        selected = [c for c in selected if c["id"] in ids]

    if args.list:
        for c in selected:
            print(f"[{c['id']}] {c['lang']}->{c['expect']:3} {c['agent']:15} {c['prompt']}")
            if c.get("note"):
                print(f"       note: {c['note']}")
        show_manual()
        return 0

    key = api_key()
    if not key:
        print("WARNING: no API_KEYS found in .env — requests may 401.")

    failed: list[str] = []
    for c in selected:
        print(f"\n{'=' * 70}\n[{c['id']}] {c['lang']} -> expect {c['expect']}  "
              f"({c['agent']})\n  {c['prompt']}")
        if c.get("note"):
            print(f"  note: {c['note']}")
        t0 = time.time()
        try:
            text = ask(args.port, c["prompt"], key)
        except Exception as e:
            print(f"  REQUEST FAILED: {e}")
            failed.append(c["id"])
            continue
        defects = audit(text, c["expect"])
        print(f"  [{time.time() - t0:.0f}s, {len(text)} chars]")
        for line in text.split("\n"):
            if line.strip().startswith("#"):
                print(f"    heading: {line.strip()[:90]}")
        lead = next((l for l in text.split("\n") if l.strip()
                     and not l.strip().startswith("#")), "")
        print(f"    lead:    {lead[:100]}")
        if defects:
            failed.append(c["id"])
            for d in defects:
                print(f"    DEFECT: {d}")
        else:
            print("    PASS")

    print(f"\n{'=' * 70}\n{len(selected) - len(failed)}/{len(selected)} passed")
    if failed:
        print("failed: " + ", ".join(failed))
    show_manual()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
