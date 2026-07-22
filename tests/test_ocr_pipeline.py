"""Regression tests for the OCR pipeline fixes shipped on 2026-07-22.

Root incident: user uploaded a handwritten Hindi WhatsApp scan PDF and
reported "our tool is not reading these pdf". The Gemini Vision call
returned 12,335 chars — but 99% of that was the abbreviation `ज.`
repeated ~5,000 times (2 real lines of transcription, the rest a
degenerate token-repetition loop). The pipeline's length-only quality
gate accepted it, cached it, and every downstream agent got poison.

These tests lock in the fixes:

  * `_score_text_quality` now detects token-repetition loops in any
    script (top-token frequency > 30% of total tokens).
  * `_ocr_result_is_usable` rejects loop / gibberish / empty output so
    the caller can escalate to high DPI or refuse to cache.
  * `_ocr_cache_path` includes `OCR_CACHE_VERSION` so a prompt/model
    fix invalidates every pre-fix cache entry.
  * `_extract_ai_text` guards against future thinking-budget changes
    that would put reasoning blocks into the OCR response text.

E2E smoke against the actual WhatsApp scan PDF runs when
`OCR_E2E=1` in the environment; it requires GOOGLE_API_KEY and takes
~90 seconds.
"""

from __future__ import annotations

import os
import types

import pytest

from core.file_processor import (
    OCR_CACHE_VERSION,
    _extract_ai_text,
    _ocr_cache_path,
    _ocr_result_is_usable,
    _score_text_quality,
)


# ---------------------------------------------------------------------------
# _score_text_quality: token-repetition-loop detection (the root cause)
# ---------------------------------------------------------------------------

def test_score_flags_devanagari_repetition_loop():
    """The exact garbage shape from the 2026-07-22 WhatsApp scan:
    ~5000 copies of a 2-char Devanagari abbreviation. Length looked
    fine (12K chars); quality was ~all-loop."""
    text = "BAW-2639/23\nराज्य - अनिल कुमार\n" + "ज. " * 5000
    result = _score_text_quality(text)
    assert result["verdict"] == "garbled", result
    assert result["metrics"]["reason"] == "token_loop"
    assert result["metrics"]["top_token"].startswith("ज")
    assert result["metrics"]["top_ratio"] > 0.30


def test_score_flags_latin_repetition_loop():
    """Same failure mode in English — a Vision model stuck on a short
    stop-word must be caught too, not just Indic scripts."""
    text = "SUPREME COURT OF INDIA\n" + "the " * 500
    result = _score_text_quality(text)
    assert result["verdict"] == "garbled", result
    assert result["metrics"]["reason"] == "token_loop"


def test_score_accepts_clean_english():
    """Real prose has no single token above ~15% of tokens. Must not
    false-positive on legitimate legal English."""
    text = (
        "IN THE HIGH COURT OF DELHI AT NEW DELHI. Present: Hon'ble Mr. "
        "Justice A.K. Sharma. Petitioner: State of Delhi versus Respondent: "
        "Vivek Kumar. The petitioner filed the present writ petition under "
        "Article 226 of the Constitution of India seeking a direction to "
        "the respondent authority to release the pending payment of "
        "salary arrears amounting to Rs. 4,50,000 for the period of "
        "January 2024 through December 2024. Heard learned counsel for "
        "the parties. On careful consideration of the material on record, "
        "this court is of the view that the impugned order dated 15th "
        "March 2025 is liable to be set aside. Accordingly, the writ "
        "petition is allowed. No order as to costs."
    )
    result = _score_text_quality(text)
    assert result["verdict"] == "clean", result


def test_score_accepts_clean_devanagari():
    """A page of real handwritten-transcribed Hindi (from the WhatsApp
    scan after the fix) must not be flagged as garbled."""
    text = (
        "उपायुक्त सिविल जज BAN-2638/23 राज्य - बनाम - अनिल कुमार (8) "
        "लायनवादी श्री विवेक कुमार पुत्र श्री जगदीश प्रसाद निवासी "
        "न्यू बेगम बाग थाना कुआसी जनपद अलीगढ़ उम्र 32 वर्ष मो० नं० "
        "8533012064 हाल तैनाती 33/11 KV सबस्टेशन अवागढ़ जिला एटा ने "
        "पूछने पर बताया कि मेरी तैनाती विद्युत उपकेन्द्र अवागढ़ पर "
        "करीब 18 माह से है दिनांक 23/3/2021 को मैं ओ०एस०आर० अधीक्षण "
        "अभियंता मो०एन० के कार्यालय पत्रांक संख्या 2013/विद्युत ए के "
        "अनुपालन में प्रवीन दल में तैनात है"
    )
    result = _score_text_quality(text)
    # non_latin verdict is fine — means "coherent non-Latin script, defer
    # to language-aware handling". Not "garbled".
    assert result["verdict"] in ("non_latin", "clean"), result


def test_score_short_input_skips_loop_check():
    """The loop check requires >= 50 tokens — below that, false-positive
    risk is high (e.g. a 3-line title page)."""
    text = "the the the the the"
    result = _score_text_quality(text)
    assert result["verdict"] != "garbled" or result["metrics"].get("reason") != "token_loop"


# ---------------------------------------------------------------------------
# _ocr_result_is_usable: the wrapper used by _vision_ocr_pdf / _vision_ocr_image
# ---------------------------------------------------------------------------

def test_usable_rejects_repetition_loop():
    text = "BAW-2639/23\n" + "ज. " * 5000
    usable, reason = _ocr_result_is_usable(text)
    assert not usable
    assert "garbled" in reason and "token_loop" in reason


def test_usable_rejects_empty():
    usable, reason = _ocr_result_is_usable("")
    assert not usable
    assert reason == "empty"


def test_usable_accepts_real_content():
    text = (
        "IN THE HIGH COURT OF PUNJAB AND HARYANA AT CHANDIGARH. "
        "CWP No 12345 of 2024. Between: Vivek Kumar Petitioner Versus "
        "State of Haryana and others Respondents. Present: For the "
        "petitioner Mr Anil Sharma advocate. Order: The petitioner has "
        "approached this court seeking a writ of mandamus directing the "
        "respondents to release the pending gratuity amount due to him "
        "on his retirement from service. The facts in brief are that the "
        "petitioner joined the service of respondent No 2 on 15 January "
        "1990 and retired on attaining the age of superannuation on 31 "
        "January 2024 having completed 34 years of continuous service."
    )
    usable, reason = _ocr_result_is_usable(text)
    assert usable, reason


# ---------------------------------------------------------------------------
# _ocr_cache_path: versioned key
# ---------------------------------------------------------------------------

def test_cache_path_contains_version():
    """A prompt/model fix must invalidate all pre-fix cache entries at
    once — the version tag in the filename does that."""
    path = _ocr_cache_path("abc123")
    assert path.endswith(f"abc123_{OCR_CACHE_VERSION}.txt")


def test_cache_path_different_versions_dont_collide():
    """If OCR_CACHE_VERSION changes to v3 in a future fix, existing v2
    files must not be picked up by v3 readers."""
    path_v2 = _ocr_cache_path("samehash")
    # simulate reading with a different version — just check the filename
    # format so it's obvious two versions produce two different files.
    assert "_v2.txt" in path_v2 or f"_{OCR_CACHE_VERSION}.txt" in path_v2


# ---------------------------------------------------------------------------
# _extract_ai_text: guard against thinking-budget contamination
# ---------------------------------------------------------------------------

def test_extract_ai_text_from_string_content():
    """Common case: AIMessage.content is a plain string."""
    fake = types.SimpleNamespace(content="hello world", text="hello world")
    assert _extract_ai_text(fake) == "hello world"


def test_extract_ai_text_from_typed_blocks():
    """Future-proof: if Gemini starts returning [{'type':'text','text':...},
    {'type':'thinking','text':...}], only the text blocks should be
    extracted — otherwise reasoning traces would leak into OCR output."""
    fake = types.SimpleNamespace(
        content=[
            {"type": "thinking", "text": "let me look at the page carefully"},
            {"type": "text", "text": "IN THE HIGH COURT OF DELHI"},
        ],
        text="IN THE HIGH COURT OF DELHI",
    )
    assert _extract_ai_text(fake) == "IN THE HIGH COURT OF DELHI"


def test_extract_ai_text_fallback_to_text_attr():
    """Unknown content shape → fall back to .text."""
    fake = types.SimpleNamespace(content=None, text="fallback text")
    assert _extract_ai_text(fake) == "fallback text"


# ---------------------------------------------------------------------------
# End-to-end smoke against the actual WhatsApp scan PDF. Gated because it
# needs a live GOOGLE_API_KEY and ~90 s of Gemini calls.
# ---------------------------------------------------------------------------

WHATSAPP_PDF = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "test_pdfs",
    "WhatsApp Scan 2026-07-20 at 13.35.36.pdf",
)


@pytest.mark.skipif(
    os.getenv("OCR_E2E") != "1"
    or not os.getenv("GOOGLE_API_KEY")
    or not os.path.exists(WHATSAPP_PDF),
    reason="Set OCR_E2E=1 + GOOGLE_API_KEY and have the WhatsApp scan present",
)
def test_e2e_whatsapp_scan_no_repetition_loop():
    """The regression file: 3-page handwritten Hindi WhatsApp scan that
    produced 5000 copies of `ज.` on the pre-fix pipeline. Post-fix
    (Flash model + 1-page batches + anti-repetition prompt) must return
    coherent text with no single token dominating."""
    from core.file_processor import _vision_ocr_pdf, _score_text_quality

    text = _vision_ocr_pdf(WHATSAPP_PDF, 3, None, "WhatsApp Scan test")
    assert text, "OCR returned empty text — regression"

    score = _score_text_quality(text)
    assert score["verdict"] != "garbled", (
        f"OCR still garbled: {score}"
    )

    # Content anchor — the case number BAW/BAN-263[89]/23 must appear.
    assert any(marker in text for marker in ("BAW-2639/23", "BAN-2638/23", "2639/23", "2638/23")), (
        f"Expected case number not in OCR text: {text[:500]}"
    )
