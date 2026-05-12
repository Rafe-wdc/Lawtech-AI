"""Unit tests for core.file_processor's script-aware text-quality detection
(the Kannada / Indic-script garbled-PDF handling).

Pure, fast, no API / no PDF files needed:

    python tests/test_pdf_extraction_quality.py
    # or: pytest tests/test_pdf_extraction_quality.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.file_processor import (
    _script_of,
    _script_profile,
    _is_garbled_by_script,
    _score_text_quality,
    _detect_garbled_pdf,
)

# --- Sample texts -----------------------------------------------------------

# Clean Kannada (Karnataka High Court header-ish), repeated to get enough mass.
KANNADA_CLEAN = (
    "ಕರ್ನಾಟಕ ಉಚ್ಚ ನ್ಯಾಯಾಲಯ ಬೆಂಗಳೂರು ರಿಟ್ ಅರ್ಜಿ ಸಂಖ್ಯೆ ತೀರ್ಪು ದಿನಾಂಕ "
    "ಅರ್ಜಿದಾರರು ಪ್ರತಿವಾದಿಗಳು ವಕೀಲರು ಆದೇಶ ಪ್ರಕರಣ ಸಾಕ್ಷ್ಯ ಕಾನೂನು ಸೆಕ್ಷನ್ "
) * 8

# Clean English legal text.
ENGLISH_CLEAN = (
    "In the High Court of Karnataka at Bengaluru. Writ Petition number filed by "
    "the petitioner against the respondents. The learned counsel for the petitioner "
    "submitted that the impugned order dated is liable to be quashed. Having heard "
    "the parties and perused the records, this Court is of the considered opinion "
    "that the petition deserves to be allowed. Section of the Act provides that. "
) * 6

# Bilingual English + Kannada (very common in Indian court PDFs) — must NOT flag.
BILINGUAL_OK = ENGLISH_CLEAN + "\n" + KANNADA_CLEAN

# Garbled Kannada/Devanagari: broken Identity-H CMap dumps glyphs into the
# Latin-1 Supplement / Latin Extended ranges (U+00C0..U+024F). This is the
# real "ªÉÉ´ÉlÉ" failure mode.
LATIN_EXT_DUMP = (
    "ªÉÉ´ÉlÉ ºÉ¨É "
    "ºÉÆÉ¨Æ ÀÁÂÃÄ "
    "ŒœŸƒƠ ÐÑÞßæ "
) * 20

# U+FFFD soup: font has no ToUnicode table at all.
FFFD_SOUP = ("��� word �� legal ��� "
             "court � order ���� section ") * 15

# Garbled Latin: consonant pile-ups / random substitutions / numeric noise.
LATIN_GARBLE = (
    "PUHJAE Aim HARYAIIA vvm111 1121111 rrr nnn ttttt qqqqq mmmmm "
    "phhc bbbbb cccccc ddddd ggggg kkkkk llllll ppppp ssssss "
) * 12

# Partial broken-CMap: a real non-Latin script (Kannada) heavily contaminated
# by Latin-Extended dump-zone chars (same font, mixed encoding declarations).
PARTIAL_CONTAMINATED = KANNADA_CLEAN[:200] + LATIN_EXT_DUMP[:1500]


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


# --- _script_of -------------------------------------------------------------

def test_script_of_buckets():
    assert _script_of("ಕ") == "kannada"          # U+0C95
    assert _script_of("क") == "devanagari"        # U+0915
    assert _script_of("அ") == "tamil"             # U+0B85
    assert _script_of("A") == "latin"
    assert _script_of("z") == "latin"
    assert _script_of("é") == "latin_ext"         # U+00E9
    assert _script_of("Œ") == "latin_ext"    # OE ligature, Latin Extended-A
    assert _script_of("ª") == "latin_ext"    # feminine ordinal, Latin-1 Supplement
    assert _script_of("5") is None                # digit
    assert _script_of(" ") is None
    assert _script_of(".") is None


# --- _script_profile --------------------------------------------------------

def test_profile_clean_kannada_dominant():
    p = _script_profile(KANNADA_CLEAN)
    assert p["dominant"] == "kannada"
    assert p["dominant_ratio"] > 0.9
    assert p["nonlatin_letters"] > 50
    assert p["replacement_ratio"] == 0.0
    assert p["latin_ext_ratio"] == 0.0


def test_profile_bilingual_has_both():
    p = _script_profile(BILINGUAL_OK)
    by = p["by_script"]
    assert by.get("latin", 0) > 50
    assert by.get("kannada", 0) > 50
    assert p["latin_ext_ratio"] < 0.02
    assert p["replacement_ratio"] == 0.0


# --- _is_garbled_by_script --------------------------------------------------

def test_clean_texts_not_garbled():
    for label, txt in [("kannada", KANNADA_CLEAN), ("english", ENGLISH_CLEAN),
                       ("bilingual", BILINGUAL_OK)]:
        gb, reason = _is_garbled_by_script(_script_profile(txt))
        assert gb is False, f"{label} wrongly flagged garbled ({reason})"


def test_fffd_soup_garbled():
    gb, reason = _is_garbled_by_script(_script_profile(FFFD_SOUP))
    assert gb is True
    assert "replacement" in reason


def test_latin_ext_dump_garbled():
    gb, reason = _is_garbled_by_script(_script_profile(LATIN_EXT_DUMP))
    assert gb is True


def test_partial_contaminated_nonlatin_garbled():
    gb, reason = _is_garbled_by_script(_script_profile(PARTIAL_CONTAMINATED))
    assert gb is True
    assert "nonlatin_contaminated" in reason or "latin_ext" in reason


# --- _score_text_quality ----------------------------------------------------

def test_score_clean_kannada_is_non_latin():
    v = _score_text_quality(KANNADA_CLEAN)
    assert v["verdict"] == "non_latin", v


def test_score_clean_english_is_clean():
    v = _score_text_quality(ENGLISH_CLEAN)
    assert v["verdict"] == "clean", v


def test_score_garbled_kannada_dump_is_garbled():
    v = _score_text_quality(LATIN_EXT_DUMP)
    assert v["verdict"] == "garbled", v


def test_score_fffd_soup_is_garbled():
    v = _score_text_quality(FFFD_SOUP)
    assert v["verdict"] == "garbled", v


def test_score_garbled_latin_is_garbled():
    v = _score_text_quality(LATIN_GARBLE)
    assert v["verdict"] == "garbled", v


# --- _detect_garbled_pdf ----------------------------------------------------

def test_detect_pdf_flags_garbled_kannada_pages():
    # 5-page "PDF": 4 garbled Kannada pages + 1 clean -> > 10% garbled -> flagged.
    pages = [LATIN_EXT_DUMP, LATIN_EXT_DUMP, LATIN_EXT_DUMP, LATIN_EXT_DUMP, ENGLISH_CLEAN]
    res = _detect_garbled_pdf(pages)
    assert res["garbled"] is True
    assert len(res["garbled_pages"]) >= 4


def test_detect_pdf_clean_bilingual_not_flagged():
    pages = [ENGLISH_CLEAN, KANNADA_CLEAN, BILINGUAL_OK, ENGLISH_CLEAN]
    res = _detect_garbled_pdf(pages)
    assert res["garbled"] is False


# --- gateway.safe_upload_name (non-ASCII filenames) -------------------------

def test_safe_upload_name_keeps_extension():
    from core.gateway import safe_upload_name
    # ASCII name -> unchanged-ish, extension preserved.
    n = safe_upload_name("Writ Petition 123.PDF")
    assert n.lower().endswith(".pdf")
    assert n != ".pdf" and "writ" in n.lower()


def test_safe_upload_name_non_ascii_gets_uuid_stem_keeps_pdf():
    from core.gateway import safe_upload_name
    # Fully-Kannada filename -> would become '' or 'pdf' under bare
    # secure_filename(); must keep '.pdf' and get a usable stem.
    n = safe_upload_name("ಕನ್ನಡ ದಾಖಲೆ.pdf")
    assert n.lower().endswith(".pdf")
    assert len(n) > len(".pdf")
    assert n != ".pdf"


def test_safe_upload_name_non_ascii_ext_dropped():
    from core.gateway import safe_upload_name
    n = safe_upload_name("ದಾಖಲೆ.ಪಿಡಿಎಫ್")
    # Non-ASCII extension dropped -> validate_upload will reject with a message.
    assert n.isascii()
    assert "." not in n


if __name__ == "__main__":
    sys.exit(_run_all())
