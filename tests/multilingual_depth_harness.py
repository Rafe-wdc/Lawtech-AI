"""Multilingual drafting depth harness — measurement tooling for bug 2.

WHY THIS EXISTS
---------------
Regional-language drafts were reported as "very short and not appropriate".
Sizing that complaint requires a metric, and the obvious one — count numbered
paragraphs, compare to English — turned out to be unusable.

Running the SAME English prompt five times produced:

    run   words   numbered paras   headings
      1    1227        30              9
      2    1202        31              9
      3    1186        23              9
      4    2078        40              9
      5    1362        33              9
    median 1227        31              9

Words swing 1186-2078 (75%). Paragraphs swing 23-40 (74%). Headings do not
move at all. So a single-sample "Gujarati is at 51% of English" claim is
noise, not signal — the error bar is wider than the regression.

Two consequences, both baked into this module:

  1. `section_coverage` is the PRIMARY metric. It was stable across every
     English run and it measures the thing we actually care about: did the
     document get the sections it was planned to have.
  2. Word count is SECONDARY and only meaningful as a median of >= 3 runs.
     `paragraph_count` is kept for diagnosis but must not gate anything.

The paragraph regex itself was validated against real drafts before being
demoted — Urdu and Sanskrit output was dumped and checked for native-script
digits, bullets, bold lead-ins, Indic danda and Urdu full stop. Native-digit
numbering count was 0 and bullet count was 0, i.e. nothing was being missed.
The regex was correct; the baseline was the problem.

USAGE
-----
    # one draft per language, saved to ./drafts
    python tests/multilingual_depth_harness.py 5000 drafts

    # repeat baseline for a stable median
    python tests/multilingual_depth_harness.py 5000 drafts en en en en en

    # score drafts already on disk
    python tests/multilingual_depth_harness.py --score drafts
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import urllib.request
import uuid

# --- the bail request, in every supported language -------------------------

QUERIES: dict[str, str] = {
    "en": ("Draft a regular bail application for my client charged under Sections "
           "316(2) and 318(4) of the Bharatiya Nyaya Sanhita, 2023 in a cheating "
           "case involving Rs 17,00,000."),
    "hi": ("मेरे मुवक्किल पर भारतीय न्याय संहिता, 2023 की धारा 316(2) और 318(4) के तहत "
           "17,00,000 रुपये की धोखाधड़ी का आरोप है। उनके लिए नियमित जमानत अर्जी तैयार करें।"),
    "gu": ("મારા અસીલ સામે ભારતીય ન્યાય સંહિતા, 2023 ની કલમ 316(2) અને 318(4) હેઠળ "
           "રૂ. 17,00,000 ની છેતરપિંડીનો ગુનો દાખલ થયો છે. તેમના માટે નિયમિત જામીન અરજી તૈયાર કરો."),
    "mr": ("माझ्या पक्षकाराविरुद्ध भारतीय न्याय संहिता, 2023 च्या कलम 316(2) व 318(4) "
           "अंतर्गत 17,00,000 रुपयांच्या फसवणुकीचा गुन्हा दाखल आहे. त्यांच्यासाठी नियमित जामीन अर्ज तयार करा."),
    "ta": ("என் கட்சிக்காரர் மீது பாரதிய நியாய சன்ஹிதா, 2023 பிரிவு 316(2) மற்றும் "
           "318(4) இன் கீழ் ரூ. 17,00,000 மோசடி வழக்கு உள்ளது. அவருக்கு வழக்கமான ஜாமீன் மனு தயார் செய்யவும்."),
    "bn": ("আমার মক্কেলের বিরুদ্ধে ভারতীয় ন্যায় সংহিতা, ২০২৩ এর ধারা ৩১৬(২) এবং "
           "৩১৮(৪) অনুযায়ী ১৭,০০,০০০ টাকার প্রতারণার মামলা হয়েছে। তার জন্য নিয়মিত জামিন আবেদন তৈরি করুন।"),
    "kn": ("ನನ್ನ ಕಕ್ಷಿದಾರರ ವಿರುದ್ಧ ಭಾರತೀಯ ನ್ಯಾಯ ಸಂಹಿತಾ, 2023 ರ ಸೆಕ್ಷನ್ 316(2) ಮತ್ತು "
           "318(4) ಅಡಿಯಲ್ಲಿ ರೂ. 17,00,000 ವಂಚನೆ ಪ್ರಕರಣ ದಾಖಲಾಗಿದೆ. ಅವರಿಗಾಗಿ ಸಾಮಾನ್ಯ ಜಾಮೀನು ಅರ್ಜಿ ಸಿದ್ಧಪಡಿಸಿ."),
    "ml": ("എന്റെ കക്ഷിക്കെതിരെ ഭാരതീയ ന്യായ സംഹിത, 2023 വകുപ്പ് 316(2), 318(4) പ്രകാരം "
           "17,00,000 രൂപയുടെ വഞ്ചനാ കേസ് രജിസ്റ്റർ ചെയ്തിട്ടുണ്ട്. അദ്ദേഹത്തിനായി സാധാരണ ജാമ്യാപേക്ഷ തയ്യാറാക്കുക."),
    "te": ("నా క్లయింట్‌పై భారతీయ న్యాయ సంహిత, 2023 సెక్షన్ 316(2) మరియు 318(4) కింద "
           "రూ. 17,00,000 మోసం కేసు నమోదైంది. అతని కోసం సాధారణ బెయిల్ దరఖాస్తు తయారు చేయండి."),
    "pa": ("ਮੇਰੇ ਮੁਵੱਕਿਲ ਵਿਰੁੱਧ ਭਾਰਤੀ ਨਿਆਂ ਸੰਹਿਤਾ, 2023 ਦੀ ਧਾਰਾ 316(2) ਅਤੇ 318(4) ਤਹਿਤ "
           "17,00,000 ਰੁਪਏ ਦੀ ਧੋਖਾਧੜੀ ਦਾ ਕੇਸ ਦਰਜ ਹੈ। ਉਹਨਾਂ ਲਈ ਨਿਯਮਤ ਜ਼ਮਾਨਤ ਅਰਜ਼ੀ ਤਿਆਰ ਕਰੋ।"),
    "ur": ("میرے موکل کے خلاف بھارتیہ نیائے سنہتا، 2023 کی دفعہ 316(2) اور 318(4) کے "
           "تحت 17,00,000 روپے کی دھوکہ دہی کا مقدمہ درج ہے۔ ان کے لیے باقاعدہ ضمانت کی درخواست تیار کریں۔"),
    "or": ("ମୋ ମକ୍କେଲଙ୍କ ବିରୁଦ୍ଧରେ ଭାରତୀୟ ନ୍ୟାୟ ସଂହିତା, 2023 ର ଧାରା 316(2) ଏବଂ 318(4) "
           "ଅନୁଯାୟୀ 17,00,000 ଟଙ୍କାର ପ୍ରତାରଣା ମାମଲା ଦାୟର ହୋଇଛି। ତାଙ୍କ ପାଇଁ ନିୟମିତ ଜାମିନ ଆବେଦନ ପ୍ରସ୍ତୁତ କରନ୍ତୁ।"),
    "as": ("মোৰ মক্কেলৰ বিৰুদ্ধে ভাৰতীয় ন্যায় সংহিতা, 2023 ৰ ধাৰা 316(2) আৰু 318(4) "
           "অনুসৰি 17,00,000 টকাৰ প্ৰতাৰণাৰ গোচৰ ৰুজু হৈছে। তেওঁৰ বাবে নিয়মীয়া জামিন আবেদন প্ৰস্তুত কৰক।"),
    "sa": ("मम पक्षकारस्य विरुद्धं भारतीय न्याय संहिता, 2023 इत्यस्य धारा 316(2) तथा "
           "318(4) अनुसारं 17,00,000 रूप्यकाणां वञ्चनायाः अभियोगः पञ्जीकृतः अस्ति। तस्य कृते नियमित जामीन आवेदनं रचयतु।"),
}

# Unicode blocks per language. Used by the script gate: a draft whose
# alphabetic characters are not predominantly in the target block was not
# written in the language the user asked for.
SCRIPT_RANGES: dict[str, list[tuple[int, int]]] = {
    "hi": [(0x0900, 0x097F)], "mr": [(0x0900, 0x097F)], "sa": [(0x0900, 0x097F)],
    "bn": [(0x0980, 0x09FF)], "as": [(0x0980, 0x09FF)],
    "gu": [(0x0A80, 0x0AFF)], "pa": [(0x0A00, 0x0A7F)],
    "ta": [(0x0B80, 0x0BFF)], "te": [(0x0C00, 0x0C7F)],
    "kn": [(0x0C80, 0x0CFF)], "ml": [(0x0D00, 0x0D7F)],
    "or": [(0x0B00, 0x0B7F)], "ur": [(0x0600, 0x06FF), (0x0750, 0x077F)],
    "en": [(0x0041, 0x005A), (0x0061, 0x007A)],
}

_HEADING_RE = re.compile(r"^#{1,4}\s+(\S.*)$", re.M)
_WORD_RE = re.compile(r"\S+")

# Diagnostic only — see the module docstring. Validated against real Urdu and
# Sanskrit drafts: native-digit numbering and bullets both counted 0, so this
# is not undercounting non-Latin output. It is simply too noisy to gate on.
_PARA_RE = re.compile(r"^\s*\d{1,2}[\.\)]\s+\S", re.M)


def script_ratio(text: str, lang: str) -> float:
    """Fraction of alphabetic characters that fall in the target script.

    ~1.0 means the draft is in the requested language. A value near 0 means
    off-target generation (e.g. a Kannada request answered in English), which
    was observed in 1 of 3 Kannada runs.
    """
    ranges = SCRIPT_RANGES.get(lang, [])
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return 0.0
    hits = sum(1 for c in alpha
               if any(lo <= ord(c) <= hi for lo, hi in ranges))
    return hits / len(alpha)


def headings(text: str) -> list[str]:
    return [m.strip() for m in _HEADING_RE.findall(text)]


def section_coverage(text: str, expected: int) -> float:
    """PRIMARY metric: fraction of the planned sections that materialised."""
    if expected <= 0:
        return 0.0
    return min(len(headings(text)), expected) / expected


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def paragraph_count(text: str) -> int:
    """DIAGNOSTIC ONLY. English alone varies 23-40 on an identical prompt."""
    return len(_PARA_RE.findall(text))


def median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


# --- driver ---------------------------------------------------------------

def _api_key() -> str:
    for line in open(".env", encoding="utf-8"):
        if line.startswith("API_KEYS="):
            return line.split("=", 1)[1].strip().strip('"').split(",")[0]
    return ""


def draft(port: str, query: str, timeout: int = 700) -> tuple[str, bool, float]:
    """POST one drafting request; return (markdown, incomplete_flag, seconds)."""
    boundary = "----lt" + uuid.uuid4().hex
    body = io.BytesIO()
    body.write(f"--{boundary}\r\n".encode())
    body.write(b'Content-Disposition: form-data; name="query"\r\n')
    body.write(b"Content-Type: text/plain; charset=utf-8\r\n\r\n")
    body.write(query.encode("utf-8"))
    body.write(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/pyapi/chat", data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "X-API-Key": _api_key()})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", errors="replace")
    text, incomplete = "", False
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except Exception:
            continue
        if ev.get("type") == "response":
            text = ev["content"]
        if ev.get("type") == "draft_incomplete":
            incomplete = True
    return text, incomplete, time.time() - t0


def score_dir(outdir: str) -> None:
    """Report medians per language over every draft on disk."""
    by_lang: dict[str, list[str]] = {}
    for fn in sorted(os.listdir(outdir)):
        if not fn.endswith(".md"):
            continue
        by_lang.setdefault(fn.split("_")[0], []).append(
            open(os.path.join(outdir, fn), encoding="utf-8").read())

    base = median([word_count(t) for t in by_lang.get("en", [])]) or 1.0
    print(f"{'lang':5} {'n':>2} {'words(med)':>11} {'vs en':>6} "
          f"{'paras(med)':>11} {'heads(med)':>11} {'script':>7} {'off-target':>11}")
    for lang in sorted(by_lang):
        ts = by_lang[lang]
        w = median([word_count(t) for t in ts])
        p = median([paragraph_count(t) for t in ts])
        h = median([len(headings(t)) for t in ts])
        ratios = [script_ratio(t, lang) for t in ts]
        off = sum(1 for r in ratios if r < 0.5)
        print(f"{lang:5} {len(ts):2} {w:11.0f} {100*w/base:5.0f}% "
              f"{p:11.0f} {h:11.0f} {median(ratios):7.2f} {off:>7}/{len(ts)}")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--score":
        score_dir(sys.argv[2])
        return
    port = sys.argv[1] if len(sys.argv) > 1 else "5000"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "drafts"
    langs = sys.argv[3:] or list(QUERIES)
    os.makedirs(outdir, exist_ok=True)
    for lang in langs:
        text, incomplete, secs = draft(port, QUERIES[lang])
        n = len([f for f in os.listdir(outdir) if f.startswith(lang + "_")])
        path = os.path.join(outdir, f"{lang}_{n + 1}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"{lang}: words={word_count(text):5} "
              f"paras={paragraph_count(text):3} heads={len(headings(text)):2} "
              f"script={script_ratio(text, lang):.2f} incomplete={incomplete} "
              f"{secs:.0f}s -> {path}", flush=True)


if __name__ == "__main__":
    main()
