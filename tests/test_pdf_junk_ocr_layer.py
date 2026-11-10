"""Scanner-app junk OCR layers on uploaded PDFs.

Run:
    pytest tests/test_pdf_junk_ocr_layer.py -v

BACKGROUND (2026-10-29)
=======================
A lawyer uploaded a 3-page handwritten Hindi client statement saved through a
phone scanner app. Each page was one full-page JPEG plus an OCR text layer of
Devanagari word-salad. Every garble check in core/file_processor.py passed it
(no U+FFFD, no Latin-Extended dump zone, 72% coherent Devanagari -> verdict
"non_latin"), so the junk layer became the document's content, Vision OCR
never ran, and the drafting agent produced a placeholder petition with no
hint that the file was unread. Vision OCR on the same pages reads the
handwriting cleanly.

These tests pin the shape-based detector that catches such layers, the
image-backed-page guard that keeps born-digital documents out of it, the
PDF branch's new readability warnings, and the "use it anyway" retry for
PDFs. No real client text is used: the junk below is generated with the
measured statistical shape of the incident file.
"""
from __future__ import annotations

import asyncio
import io
import os
import random
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.file_processor import (  # noqa: E402
    OCR_STATUS_UNREADABLE,
    _SCANNER_LAYER_MIN_COVERAGE,
    _SCANNER_LAYER_MIN_TOKENS,
    _detect_garbled_pdf,
    _detect_junk_ocr_layer,
    _extract_pdf_layer_profile,
    _extract_pdf_text_per_page,
    _score_scanner_layer,
    _score_text_quality,
    unreadable_user_message,
)


# ---------------------------------------------------------------------------
# Synthetic samples
# ---------------------------------------------------------------------------

_DEVA_SHORT = ["जा", "ऐ", "सि", "मा", "व", "की", "को", "नो", "गा", "कै", "रण", "ऋत", "सुर", "2", "7", "५"]
_DEVA_LONG = ["सवार", "निवासी", "चन्युवसी", "लागा", "चिता", "जगर", "साया", "मेशि", "लित्द", "सिरे",
              "जिठा", "नगर", "सभया", "स्वाद", "किसी", "ताल", "पारि", "जेवमीसे"]
_LATIN_SINGLE = list("eTPSYRIOHWNAB")
_LATIN_SHORT = ["IR", "BT", "TR", "YA", "RS", "AR", "ST", "ATt~y", "P\\", "Ty"]
_SYMBOLS = ["—", "|", "*", "«४", "&", "'-'", "%"]


def synthetic_junk_layer(seed: int = 7, lines: int = 150) -> str:
    """Devanagari word-salad with the incident file's shape: 1-3 tokens per
    line, ~65% tokens of <=2 chars, ~17% single Latin letters."""
    rng = random.Random(seed)
    out = []
    for _ in range(lines):
        n = rng.choice([1, 1, 2, 2, 2, 3])
        toks = []
        for _ in range(n):
            r = rng.random()
            if r < 0.45:
                toks.append(rng.choice(_DEVA_SHORT))
            elif r < 0.62:
                toks.append(rng.choice(_LATIN_SINGLE))
            elif r < 0.80:
                toks.append(rng.choice(_DEVA_LONG))
            elif r < 0.90:
                toks.append(rng.choice(_LATIN_SHORT))
            else:
                toks.append(rng.choice(_SYMBOLS))
        out.append(" ".join(toks))
    return "\n".join(out)


def synthetic_latin_soup(lines: int = 120) -> str:
    """Letter soup of the kind a scanner app emits for a handwritten English
    page (seen on a second upload the same day)."""
    rng = random.Random(3)
    pool = ["B", "uta", "s", "viwimen", "BB", "gy", "S", "x", "li", "o", "w&/w_q", "b",
            "D", "SRR", "OO", "-", "e", "0\\7324'", "8\\", "%", "37", "2o", "4", "TEH", "I",
            "A", "91", "y", "6", "T", "P"]
    return "\n".join(" ".join(rng.choice(pool) for _ in range(rng.choice([1, 1, 2, 3])))
                     for _ in range(lines))


_HINDI_PROSE = (
    "यह कि आवेदक ने दिनांक 12 मार्च 2023 को प्रतिवादी से एक दुकान किराये पर ली थी "
    "और प्रतिमाह किराया नियमित रूप से अदा किया जाता रहा है। "
    "यह कि प्रतिवादी ने बिना किसी पूर्व सूचना के दुकान का ताला बदल दिया जिससे आवेदक "
    "को अपने व्यापार में भारी आर्थिक हानि उठानी पड़ी है। "
    "यह कि आवेदक ने अनेक बार प्रतिवादी से संपर्क करके समस्या के समाधान का प्रयास किया "
    "परन्तु प्रतिवादी ने कोई संतोषजनक उत्तर नहीं दिया। "
    "अतः आवेदक इस माननीय न्यायालय से प्रार्थना करता है कि प्रतिवादी को दुकान का कब्जा "
    "वापस सौंपने का निर्देश दिया जाये तथा हुई हानि की समुचित क्षतिपूर्ति दिलाई जाये। "
)


def clean_hindi_layer(repeats: int = 4, words_per_line: int = 9) -> str:
    words = (_HINDI_PROSE * repeats).split()
    return "\n".join(" ".join(words[i:i + words_per_line])
                     for i in range(0, len(words), words_per_line))


_ENGLISH_PROSE = (
    "The petitioner respectfully submits that the respondent company failed to "
    "deliver the goods within the agreed period despite repeated reminders sent "
    "by registered post. The petitioner has suffered loss of business and "
    "reputation and is entitled to recover the advance paid together with "
    "interest at the rate agreed between the parties under the contract. "
)


def clean_english_layer(repeats: int = 4, words_per_line: int = 8) -> str:
    words = (_ENGLISH_PROSE * repeats).split()
    return "\n".join(" ".join(words[i:i + words_per_line])
                     for i in range(0, len(words), words_per_line))


def bilingual_form_layer() -> str:
    """A scanned deposition form: English headings, Hindi entries, short
    lines. The clean control that rules out Latin-share as a signal."""
    rows = [
        "Schedule XLII-Form No. (1) 12",
        "FORM OF HEADING OF DEPOSITION",
        "Deposition of Witness No. 1 for the अभियोजन aged about",
        "61 Taken on solemn affirmation on the 19th January, 2026",
        "My name is रामलाल son of श्यामलाल resident of",
        "ग्राम रामपुर district इन्दौर occupation कृषि",
        "I state on oath that on the date of incident I was present",
        "at the शादी समारोह in the village and saw the accused",
        "Question: क्या आप अभियुक्त को पहचानते हैं Answer: हाँ",
        "Read over to the witness and admitted by him to be correct",
    ]
    return "\n".join(rows * 3)


# ---------------------------------------------------------------------------
# _score_scanner_layer
# ---------------------------------------------------------------------------

class TestScoreScannerLayer:

    def test_devanagari_junk_is_junk(self):
        res = _score_scanner_layer(synthetic_junk_layer())
        assert res["verdict"] == "junk", res
        assert res["score"] >= 2
        m = res["metrics"]
        assert m["short_token_ratio"] >= 0.45
        assert m["single_latin_ratio"] >= 0.10
        assert m["tokens_per_line"] <= 2.6

    def test_latin_letter_soup_is_junk(self):
        res = _score_scanner_layer(synthetic_latin_soup())
        assert res["verdict"] == "junk", res

    def test_clean_hindi_prose_is_ok(self):
        res = _score_scanner_layer(clean_hindi_layer())
        assert res["verdict"] == "ok", res
        # Hindi has many 2-char function words (कि, ने, से, को); the rule must
        # not fire on that alone.
        assert res["metrics"]["single_latin_ratio"] < 0.10
        assert res["metrics"]["tokens_per_line"] > 2.6

    def test_clean_english_prose_is_ok(self):
        res = _score_scanner_layer(clean_english_layer())
        assert res["verdict"] == "ok", res
        assert res["score"] == 0

    def test_bilingual_form_is_ok(self):
        """Latin share here is ~0.5 — must not matter; shape is fine."""
        res = _score_scanner_layer(bilingual_form_layer())
        assert res["verdict"] == "ok", res

    def test_too_short_is_not_judged(self):
        res = _score_scanner_layer("e T P जा ऐ")
        assert res["verdict"] == "too_short"
        assert res["metrics"]["tokens"] < _SCANNER_LAYER_MIN_TOKENS

    def test_one_signal_is_not_enough(self):
        """Low tokens-per-line alone (an index of annexures, a table of
        contents rendered one cell per line) must not be junk."""
        text = "\n".join(f"Annexure P-{i}" for i in range(1, 120))
        res = _score_scanner_layer(text)
        assert res["metrics"]["tokens_per_line"] <= 2.6
        assert res["metrics"]["short_token_ratio"] < 0.45
        assert res["verdict"] == "ok", res

    def test_existing_garble_checks_do_not_catch_devanagari_junk(self):
        """Documents WHY this detector exists: the incident layer is
        script-coherent, so `_score_text_quality` calls it non_latin."""
        junk = synthetic_junk_layer()
        assert _score_text_quality(junk)["verdict"] != "garbled"
        assert _detect_garbled_pdf([junk, junk, junk])["garbled"] is False


# ---------------------------------------------------------------------------
# _detect_junk_ocr_layer — the page-coverage guard and the 10% rule
# ---------------------------------------------------------------------------

class TestDetectJunkOcrLayer:

    def test_image_backed_junk_pages_flag_the_pdf(self):
        pages = [synthetic_junk_layer(s) for s in (1, 2, 3)]
        res = _detect_junk_ocr_layer(pages, [1.0, 1.0, 1.0])
        assert res["junk"] is True
        assert res["whole_doc"] is True        # 100% junk -> whole-file OCR
        assert res["junk_pages"] == [0, 1, 2]
        assert res["checked_pages"] == 3

    def test_born_digital_pages_are_never_judged(self):
        """Same text, no raster behind it: a born-digital page with odd
        layout is not a scanner layer and must be left alone."""
        pages = [synthetic_junk_layer(s) for s in (1, 2, 3)]
        res = _detect_junk_ocr_layer(pages, [0.0, 0.0, 0.0])
        assert res["junk"] is False
        assert res["checked_pages"] == 0

    def test_coverage_threshold(self):
        pages = [synthetic_junk_layer(1)]
        below = _detect_junk_ocr_layer(pages, [_SCANNER_LAYER_MIN_COVERAGE - 0.01])
        above = _detect_junk_ocr_layer(pages, [_SCANNER_LAYER_MIN_COVERAGE])
        assert below["junk"] is False and above["junk"] is True

    def test_clean_scanned_pdf_is_not_flagged(self):
        pages = [clean_hindi_layer(), clean_english_layer(), bilingual_form_layer()]
        res = _detect_junk_ocr_layer(pages, [1.0, 1.0, 1.0])
        assert res["junk"] is False
        assert res["junk_pages"] == []
        assert res["checked_pages"] == 3

    def test_one_junk_page_in_a_long_paperbook_is_re_read_alone(self):
        """1 of 20: that page is junk and gets re-read; the other 19 keep
        their layer (no whole-document OCR)."""
        pages = [synthetic_junk_layer(9)] + [clean_english_layer() for _ in range(19)]
        res = _detect_junk_ocr_layer(pages, [1.0] * 20)
        assert res["junk_pages"] == [0]
        assert res["junk"] is True
        assert res["whole_doc"] is False
        assert res["junk_pct"] == 5.0

    def test_one_junk_page_of_three_is_selective(self):
        pages = [synthetic_junk_layer(9), clean_hindi_layer(), clean_hindi_layer()]
        res = _detect_junk_ocr_layer(pages, [1.0, 1.0, 1.0])
        assert res["junk"] is True and res["whole_doc"] is False

    def test_majority_junk_means_whole_document(self):
        pages = [synthetic_junk_layer(9), synthetic_junk_layer(10), clean_hindi_layer()]
        res = _detect_junk_ocr_layer(pages, [1.0, 1.0, 1.0])
        assert res["junk"] is True and res["whole_doc"] is True

    def test_empty_and_short_pages_are_skipped(self):
        pages = ["", "   ", "e T P जा ऐ", clean_english_layer()]
        res = _detect_junk_ocr_layer(pages, [1.0, 1.0, 1.0, 1.0])
        assert res["checked_pages"] == 1
        assert res["junk"] is False

    def test_missing_coverage_entries_mean_not_image_backed(self):
        res = _detect_junk_ocr_layer([synthetic_junk_layer(1)], [])
        assert res["junk"] is False


# ---------------------------------------------------------------------------
# Real PDFs built with PyMuPDF: coverage measurement end to end
# ---------------------------------------------------------------------------

def _page_image_bytes(w: int = 400, h: int = 560) -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    for y in range(40, h, 28):
        d.line((30, y, w - 30, y), fill="black", width=2)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def _make_scanner_pdf_pages(path: str, page_layers: list[list[str] | None]) -> None:
    """A scanner-app style PDF with one entry per page: full-page JPEG plus
    an optional invisible text layer. PyMuPDF's built-in fonts only cover
    Latin, so the layer text is Latin."""
    import fitz
    doc = fitz.open()
    for layer_lines in page_layers:
        page = doc.new_page(width=400, height=560)
        page.insert_image(page.rect, stream=_page_image_bytes())
        if layer_lines:
            y = 30
            for line in layer_lines:
                page.insert_text((20, y), line, fontsize=9, render_mode=3)
                y += 12
                if y > 540:
                    break
    doc.save(path)
    doc.close()


def _make_scanner_pdf(path: str, layer_lines: list[str] | None, pages: int = 1) -> None:
    _make_scanner_pdf_pages(path, [layer_lines] * pages)


def _make_born_digital_pdf(path: str, lines: list[str]) -> None:
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=400, height=560)
    y = 30
    for line in lines:
        page.insert_text((20, y), line, fontsize=9)
        y += 12
        if y > 540:
            break
    doc.save(path)
    doc.close()


class TestLayerProfileOnRealPdfs:

    def test_full_page_image_reports_full_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "scan.pdf")
            _make_scanner_pdf(p, clean_english_layer().splitlines(), pages=2)
            texts, cov, n = _extract_pdf_layer_profile(p)
            assert n == 2 and len(texts) == 2 and len(cov) == 2
            assert all(c >= 0.95 for c in cov), cov
            assert "petitioner" in texts[0]

    def test_born_digital_reports_zero_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "digital.pdf")
            _make_born_digital_pdf(p, synthetic_latin_soup().splitlines())
            texts, cov, n = _extract_pdf_layer_profile(p)
            assert cov == [0.0]
            # The soup would score junk on shape, but the page is not image
            # backed so the PDF is left alone.
            assert _detect_junk_ocr_layer(texts, cov)["junk"] is False

    def test_scanner_pdf_with_junk_layer_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "junk_scan.pdf")
            _make_scanner_pdf(p, synthetic_latin_soup().splitlines())
            texts, cov, n = _extract_pdf_layer_profile(p)
            res = _detect_junk_ocr_layer(texts, cov)
            assert res["junk"] is True, (res, cov, texts[0][:120])

    def test_scanner_pdf_with_clean_layer_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "good_scan.pdf")
            _make_scanner_pdf(p, clean_english_layer().splitlines())
            texts, cov, n = _extract_pdf_layer_profile(p)
            assert _detect_junk_ocr_layer(texts, cov)["junk"] is False

    def test_legacy_per_page_helper_still_returns_two_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "x.pdf")
            _make_born_digital_pdf(p, ["hello world"])
            texts, n = _extract_pdf_text_per_page(p)
            assert n == 1 and "hello" in texts[0]


# ---------------------------------------------------------------------------
# process_files: the PDF branch routes junk layers to Vision OCR and now
# warns like the image branch does
# ---------------------------------------------------------------------------

def _stub_chat_store():
    from core.chat_store import chat_store
    chat_store.get_thread_storage = AsyncMock(return_value=(0, 0))
    chat_store.save_thread_file = AsyncMock(return_value=None)
    chat_store.update_ocr_status = AsyncMock(return_value=None)


def _run_pdf(pdf_path: str, ocr_text: str, *, force_ocr: bool = False,
             page_ocr: dict[int, str] | None = None):
    """Run process_files on one PDF with whole-file Vision OCR stubbed to
    `ocr_text` and selective page OCR stubbed to `page_ocr`.
    Returns (events, stored_texts, ocr_calls, file_context); `ocr_calls`
    records ("whole", page_count) and ("pages", [indexes]) entries."""
    from core import file_processor

    events: list[dict] = []
    stored: list[str] = []
    ocr_calls: list[tuple] = []

    def fake_ocr(path, page_count, emit, name):
        ocr_calls.append(("whole", page_count))
        return ocr_text

    def fake_page_ocr(path, pages, emit, name):
        ocr_calls.append(("pages", list(pages)))
        return {i: (page_ocr or {}).get(i, "") for i in pages}

    async def _inner():
        with (
            patch.object(file_processor, "upload_to_gemini",
                         lambda *a, **k: ("gemini://uri", "files/abc", "2099-01-01")),
            patch.object(file_processor, "is_gemini_supported", lambda *a, **k: True),
            patch.object(file_processor, "_store_in_chromadb",
                         lambda text, cid, name: stored.append(text)),
            patch.object(file_processor, "_vision_ocr_pdf", fake_ocr),
            patch.object(file_processor, "_vision_ocr_pdf_pages", fake_page_ocr),
        ):
            _stub_chat_store()
            return await file_processor.process_files(
                [(pdf_path, os.path.basename(pdf_path), os.path.getsize(pdf_path))],
                thread_id="junk-layer-test",
                writer=events.append,
                force_ocr=force_ocr,
            )

    fc = asyncio.run(_inner())
    return events, stored, ocr_calls, fc


def _stages(events):
    return [e.get("stage") for e in events if e.get("type") == "file_processing"]


class TestPdfBranch:

    def test_junk_layer_is_replaced_by_vision_ocr(self):
        """Every page junk (the incident file): whole-document OCR."""
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "statement.pdf")
            _make_scanner_pdf(p, synthetic_latin_soup().splitlines(), pages=2)
            good = clean_hindi_layer()
            events, stored, ocr_calls, fc = _run_pdf(p, good)
            rejected = [e for e in events if e.get("stage") == "pdf_layer_rejected"]
            assert rejected and rejected[0]["scope"] == "document"
            assert ocr_calls == [("whole", 2)]
            assert stored and stored[0] == good      # OCR text, not the layer
            assert fc.files[0].ocr_status != OCR_STATUS_UNREADABLE

    def test_minority_junk_pages_are_re_read_selectively(self):
        """Page 2 of 3 junk: only that page is re-read and spliced in; the
        clean pages keep their layer and whole-file OCR never runs."""
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "chargesheet.pdf")
            clean = clean_english_layer().splitlines()
            _make_scanner_pdf_pages(p, [clean, synthetic_latin_soup().splitlines(), clean])
            hindi = clean_hindi_layer()
            events, stored, ocr_calls, fc = _run_pdf(p, "WHOLE-FILE OCR MUST NOT RUN",
                                                     page_ocr={1: hindi})
            rejected = [e for e in events if e.get("stage") == "pdf_layer_rejected"]
            assert rejected and rejected[0]["scope"] == "pages"
            assert rejected[0]["page_numbers"] == [2]
            assert ocr_calls == [("pages", [1])]
            assert len(stored) == 1
            assert "--- Page 1 ---" in stored[0] and "petitioner" in stored[0]
            assert "--- Page 2 ---" in stored[0] and "आवेदक" in stored[0]
            assert "viwimen" not in stored[0]          # junk never reaches agents
            assert "partial_read_warning" not in _stages(events)
            assert "unreadable_warning" not in _stages(events)

    def test_unread_selective_page_raises_partial_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "chargesheet.pdf")
            clean = clean_english_layer().splitlines()
            _make_scanner_pdf_pages(p, [clean, synthetic_latin_soup().splitlines(), clean])
            events, stored, ocr_calls, fc = _run_pdf(p, "", page_ocr={})
            warn = [e for e in events if e.get("stage") == "partial_read_warning"]
            assert warn and warn[0]["unread_pages"] == [2]
            assert len(stored) == 1 and "viwimen" not in stored[0]
            assert "--- Page 2 ---" not in stored[0]   # blank, not junk

    def test_clean_layer_never_calls_vision_ocr(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "filing.pdf")
            _make_scanner_pdf(p, clean_english_layer().splitlines())
            events, stored, ocr_calls, fc = _run_pdf(p, "SHOULD NOT BE USED")
            assert "pdf_layer_rejected" not in _stages(events)
            assert ocr_calls == []
            assert stored and "petitioner" in stored[0]

    def test_illegible_markers_raise_partial_read_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "statement.pdf")
            _make_scanner_pdf(p, synthetic_latin_soup().splitlines())
            holey = clean_hindi_layer() + "\n" + " ".join(["[illegible]"] * 6)
            events, stored, ocr_calls, fc = _run_pdf(p, holey)
            warn = [e for e in events if e.get("stage") == "partial_read_warning"]
            assert warn, _stages(events)
            assert warn[0]["illegible_markers"] == 6
            assert "statement.pdf" in warn[0]["message"]
            assert stored and stored[0] == holey   # still used, just flagged

    def test_two_markers_on_one_page_do_not_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "statement.pdf")
            _make_scanner_pdf(p, synthetic_latin_soup().splitlines())
            text = clean_hindi_layer() + "\n[illegible] [illegible]"
            events, *_ = _run_pdf(p, text)
            assert "partial_read_warning" not in _stages(events)

    def test_empty_ocr_withholds_and_marks_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "statement.pdf")
            _make_scanner_pdf(p, synthetic_latin_soup().splitlines())
            events, stored, ocr_calls, fc = _run_pdf(p, "")
            warn = [e for e in events if e.get("stage") == "unreadable_warning"]
            assert warn and warn[0]["can_force_ocr"] is True
            assert "scan is too unclear" in warn[0]["message"]
            assert stored == []
            assert fc.files[0].ocr_status == OCR_STATUS_UNREADABLE

    def test_garbled_ocr_withholds_unless_forced(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "statement.pdf")
            _make_scanner_pdf(p, synthetic_latin_soup().splitlines())
            loop = " ".join(["ज."] * 400)          # the token-loop signature
            events, stored, *_ = _run_pdf(p, loop)
            assert "unreadable_warning" in _stages(events)
            assert stored == []
            events, stored, *_ = _run_pdf(p, loop, force_ocr=True)
            assert "unreadable_warning" not in _stages(events)
            assert stored == [loop]

    def test_scanned_pdf_without_layer_gets_the_same_gate(self):
        """No text layer at all (the common scan) now reaches the gate too."""
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "scan.pdf")
            _make_scanner_pdf(p, None)
            events, stored, ocr_calls, fc = _run_pdf(p, "")
            assert ocr_calls and "unreadable_warning" in _stages(events)
            assert "pdf_layer_rejected" not in _stages(events)


# ---------------------------------------------------------------------------
# _vision_ocr_pdf_pages: header stripping, per-page cache, DPI escalation
# ---------------------------------------------------------------------------

class TestSelectivePageOcr:

    def _run(self, pdf_path, pages, batch_fn, cache_dir):
        from core import file_processor
        with (
            patch.object(file_processor, "OCR_CACHE_DIR", cache_dir),
            patch.object(file_processor, "_ocr_batch", batch_fn),
            patch("core.clients.get_gemini_vision", lambda **k: object()),
        ):
            return file_processor._vision_ocr_pdf_pages(pdf_path, pages, None, "x.pdf")

    def test_header_stripped_and_result_cached_per_page(self):
        calls = []
        long_text = clean_english_layer()

        def batch(llm, imgs, start):
            calls.append(start)
            return f"--- Pages {start + 1}-{start + 1} ---\n{long_text}"

        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "s.pdf")
            _make_scanner_pdf(p, None, pages=3)
            out = self._run(p, [2, 0], batch, os.path.join(tmp, "cache"))
            assert set(out) == {0, 2}
            assert out[0].startswith("The petitioner") and "--- Pages" not in out[0]
            assert sorted(calls) == [0, 2]
            # second call: both pages served from the per-page cache
            calls.clear()
            out2 = self._run(p, [0, 2], batch, os.path.join(tmp, "cache"))
            assert out2 == out and calls == []

    def test_thin_page_escalates_to_high_dpi_and_is_not_cached(self):
        calls = []

        def batch(llm, imgs, start):
            calls.append(start)
            return "--- Pages 1-1 ---\nstamp"       # too short to be usable

        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "s.pdf")
            _make_scanner_pdf(p, None, pages=1)
            out = self._run(p, [0], batch, os.path.join(tmp, "cache"))
            assert out == {0: "stamp"}
            assert calls == [0, 0]                 # low-DPI pass, then high-DPI
            assert not os.path.exists(os.path.join(tmp, "cache")) or \
                not os.listdir(os.path.join(tmp, "cache"))

    def test_failed_page_is_empty_not_raised(self):
        def batch(llm, imgs, start):
            raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "s.pdf")
            _make_scanner_pdf(p, None, pages=2)
            out = self._run(p, [1], batch, os.path.join(tmp, "cache"))
            assert out == {1: ""}


# ---------------------------------------------------------------------------
# "Use it anyway" retry handles PDFs with the PDF OCR path
# ---------------------------------------------------------------------------

class TestRetryHandlesPdf:

    def test_retry_uses_pdf_ocr_for_pdf_rows(self):
        from core import file_processor
        from core.chat_store import chat_store

        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "statement.pdf")
            _make_scanner_pdf(p, None, pages=3)
            rows = [{"file_id": "abc12345", "filename": "statement.pdf",
                     "mime_type": "application/pdf", "local_path": p,
                     "page_count": 0, "ocr_status": OCR_STATUS_UNREADABLE}]
            pdf_calls, img_calls, stored = [], [], []

            def fake_pdf(path, page_count, emit, name):
                pdf_calls.append((path, page_count))
                return "अब पढ़ा जा सका"

            async def _inner():
                with (
                    patch.object(chat_store, "load_thread_files",
                                 AsyncMock(return_value=rows)),
                    patch.object(chat_store, "update_ocr_status",
                                 AsyncMock(return_value=None)),
                    patch.object(file_processor, "_vision_ocr_pdf", fake_pdf),
                    patch.object(file_processor, "_vision_ocr_image",
                                 lambda *a, **k: img_calls.append(a) or "x"),
                    patch.object(file_processor, "_store_in_chromadb",
                                 lambda text, cid, name: stored.append((cid, text))),
                ):
                    return await file_processor.ocr_retry_unreadable_files("thread-1")

            cols = asyncio.run(_inner())
            assert pdf_calls == [(p, 3)]        # page count read from the file
            assert img_calls == []
            assert cols == ["inline_thread-1_abc12345"]
            assert stored and "पढ़ा" in stored[0][1]


class TestMessages:

    def test_scan_wording_and_default_unchanged(self):
        assert "image is too unclear" in unreadable_user_message("a.jpg")
        assert "scan is too unclear" in unreadable_user_message("a.pdf", kind="scan")
        assert "use it anyway" in unreadable_user_message("a.pdf", kind="scan")


# ---------------------------------------------------------------------------
# 2026-11-10: typed Gujarati read as Latin soup (the content rule)
# ---------------------------------------------------------------------------
#
# A scanned typed Gujarati document carried a layer in which an English-only
# OCR engine had read the Gujarati letters as long Latin tokens with digits
# and punctuation glued in. The shape rule saw one signal of three on every
# page and the Latin garble check called every page "too short", so the
# drafter received the soup as the case facts and wrote a cheque-dishonour
# complaint on placeholders. The generator below has that file's measured
# statistical profile (word-like tokens 0.10-0.22, function words <= 0.05,
# short tokens ~0.13, ~2.2 tokens per line). Every token is invented; no
# client text is used.

_SOUP_SHORT = ["t", "Q", "38", ",", "&", "k7", "(L)", "3-9", "is", "W,"]
_SOUP_PSEUDO = ["Pmmeka", "Bovira", "Tulen", "Kvobta", "ganemo", "Wesuda", "Hirota", "nupal"]


def synthetic_typed_gujarati_soup(seed: int = 11, lines: int = 45) -> str:
    rng = random.Random(seed)
    lower, upper, digits, punct = "bcdfghjklmnpqrstvwxzaeiou", "RQAEZLSKW", "0123456789", ".,-);('"

    def tok() -> str:
        r = rng.random()
        if r < 0.15:
            return rng.choice(_SOUP_SHORT)
        if r < 0.29:
            return rng.choice(_SOUP_PSEUDO)
        out = []
        for _ in range(rng.randint(4, 10)):
            q = rng.random()
            out.append(rng.choice(digits) if q < 0.40 else rng.choice(lower) if q < 0.75
                       else rng.choice(upper) if q < 0.87 else rng.choice(punct))
        return "".join(out)

    return "\n".join(" ".join(tok() for _ in range(rng.choice([1, 2, 2, 3, 3])))
                     for _ in range(lines))


def brochure_layer() -> str:
    """Real English with almost no function words: bullet points."""
    items = ["Course Duration: 2 months", "On-Demand Content", "Practical Assignments",
             "Recent Updates", "Profiling Call", "Lifetime Community Membership",
             "Personal Brand Building Suite", "Profile Access by Corporates",
             "Live Session By Industry Expert", "Certificate of Completion"]
    return "\n".join(" • ".join(items[i:i + 2]) for i in range(0, len(items), 2)) * 3


def cause_list_layer() -> str:
    """A scanned cause list: case numbers, party names, counsel."""
    rows = []
    for i in range(1, 16):
        rows.append(f"Item {i} CRL.A. {300 + i}/2024 State v. Ramesh Kumar")
        rows.append(f"Mr. A.K. Sharma Adv. for Appellant Listed for Hearing")
    return "\n".join(rows)


def statement_layer() -> str:
    """A scanned bank statement: UPI narrations joined by slashes."""
    rows = []
    for i in range(1, 31):
        rows.append(f"0{1 + i % 9}/04/2024 UPI/4123456789{i:02d}/RAMESH KUMAR/PAYMENT "
                    f"{1000 + 37 * i}.00 {45000 - 120 * i}.50 CR")
    return "\n".join(rows)


def roman_hindi_layer() -> str:
    """A printed chat in Roman Hindi: the function-word list covers it."""
    rows = ["mai kal aapke ghar aaunga aur paise de dunga",
            "tum ne pichle mahine bhi yahi kaha tha",
            "paise nahi mile to mai police me shikayat karunga",
            "bhai thoda samay de do mai sab chuka dunga",
            "ye aakhri baar hai uske baad kuch nahi suna jayega"]
    return "\n".join(rows * 4)


class TestTypedGujaratiLatinSoup:

    @pytest.mark.parametrize("seed", [11, 12, 13])
    def test_long_token_soup_is_junk_by_content(self, seed):
        res = _score_scanner_layer(synthetic_typed_gujarati_soup(seed))
        assert res["verdict"] == "junk", res
        assert res["rule"] == "latin_content"
        m = res["metrics"]
        assert m["latin_share"] >= 0.6
        assert m["wordlike_ratio"] < 0.28
        assert m["function_word_ratio"] < 0.06

    @pytest.mark.parametrize("seed", [11, 12, 13])
    def test_shape_rule_alone_misses_it(self, seed):
        """Why the content rule exists: long tokens keep the shape score
        at one signal, the incident file's figure on every page."""
        res = _score_scanner_layer(synthetic_typed_gujarati_soup(seed))
        assert res["score"] < 2
        assert res["metrics"]["short_token_ratio"] < 0.45
        assert res["metrics"]["single_latin_ratio"] < 0.10

    def test_existing_garble_checks_miss_it(self):
        pages = [synthetic_typed_gujarati_soup(s) for s in (11, 12, 13)]
        assert _detect_garbled_pdf(pages)["garbled"] is False
        for p in pages:
            assert _score_text_quality(p)["verdict"] != "garbled"

    @pytest.mark.parametrize("layer", [
        clean_english_layer, brochure_layer, cause_list_layer,
        statement_layer, roman_hindi_layer,
    ])
    def test_real_latin_text_is_kept(self, layer):
        res = _score_scanner_layer(layer())
        assert res["verdict"] == "ok", (layer.__name__, res)
        assert res["rule"] is None

    def test_slash_joined_words_count_as_words(self):
        """A UPI narration is three tokens of which two are words, not one
        non-word; without the split this statement would read as junk."""
        m = _score_scanner_layer(statement_layer())["metrics"]
        assert m["wordlike_ratio"] >= 0.28

    def test_non_latin_layers_never_reach_the_content_rule(self):
        """Clean Hindi has almost no Latin letters, so the rule cannot apply."""
        res = _score_scanner_layer(clean_hindi_layer())
        assert res["verdict"] == "ok", res
        assert res["metrics"]["latin_share"] < 0.6

    def test_bilingual_form_is_kept_on_its_real_words(self):
        """The deposition form is Latin-dominant by letter count, so the rule
        does look at it; its English labels are real words, so it stays."""
        res = _score_scanner_layer(bilingual_form_layer())
        assert res["verdict"] == "ok", res
        assert res["metrics"]["latin_share"] >= 0.6
        assert res["metrics"]["wordlike_ratio"] >= 0.28

    def test_short_soup_is_not_judged(self):
        res = _score_scanner_layer(synthetic_typed_gujarati_soup(11, lines=10))
        assert res["verdict"] == "too_short"
        assert res["rule"] is None

    def test_six_scanned_pages_go_to_whole_document_ocr(self):
        pages = [synthetic_typed_gujarati_soup(s) for s in range(11, 17)]
        res = _detect_junk_ocr_layer(pages, [1.0] * 6)
        assert res["junk_pages"] == [0, 1, 2, 3, 4, 5]
        assert res["whole_doc"] is True

    def test_born_digital_soup_is_never_judged(self):
        pages = [synthetic_typed_gujarati_soup(s) for s in range(11, 14)]
        res = _detect_junk_ocr_layer(pages, [0.0] * 3)
        assert res["junk"] is False and res["checked_pages"] == 0

    def test_real_scanner_pdf_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "complaint.pdf")
            _make_scanner_pdf_pages(p, [synthetic_typed_gujarati_soup(s).splitlines()
                                        for s in (11, 12, 13)])
            texts, cov, n = _extract_pdf_layer_profile(p)
            res = _detect_junk_ocr_layer(texts, cov)
            assert n == 3
            assert res["junk"] is True and res["whole_doc"] is True

    def test_upload_reads_the_pages_instead_of_the_soup(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "complaint.pdf")
            _make_scanner_pdf_pages(p, [synthetic_typed_gujarati_soup(s).splitlines()
                                        for s in (11, 12, 13)])
            good = "ફરિયાદી રમેશભાઈ નાથાભાઈ પટેલ, આરોપી દિનેશભાઈ ... " * 20
            events, stored, ocr_calls, fc = _run_pdf(p, good)
            rejected = [e for e in events if e.get("stage") == "pdf_layer_rejected"]
            assert rejected and rejected[0]["scope"] == "document"
            assert ocr_calls == [("whole", 3)]
            assert stored and stored[0] == good


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
