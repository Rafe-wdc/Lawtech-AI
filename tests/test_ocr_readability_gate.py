"""Tests for the post-OCR readability gate on image uploads.

Standalone runner — the project venv has no pytest:

    python tests/test_ocr_readability_gate.py     # no pytest needed
    pytest tests/test_ocr_readability_gate.py -v  # also works

BACKGROUND
==========
The first implementation scored image sharpness BEFORE the Vision call and
skipped OCR on a low score. Measured against real uploads it was worse than
useless — a genuinely blurry scanned memo scored 13,244 ("very sharp") while
a sharp, readable Aadhaar photo scored 107. Scan grain is high-frequency
energy and so is sharpness; no edge statistic separates them. Global scalar
metrics also missed partial blur entirely (75% of a page blurred still
scored sharp).

The gate now runs AFTER OCR and asks `_ocr_result_is_usable` whether the
output is trustworthy — measuring the thing that actually matters. These
tests pin that contract plus the override and telemetry paths.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PIL import Image  # noqa: E402

from core.file_processor import (  # noqa: E402
    OCR_STATUS_UNREADABLE,
    ProcessedFile,
    _ILLEGIBLE_WARN_THRESHOLD,
    _MIN_LONG_EDGE_PX,
    _image_ocr_telemetry,
    _ocr_output_is_unreadable,
    _ocr_result_is_usable,
    _resolution_warning,
    count_illegible_markers,
    estimate_dpi,
    low_resolution_user_message,
    unreadable_user_message,
)


class ReadabilityGateContract(unittest.TestCase):
    """What the gate must decide. These drive the branch in the image path."""

    def test_empty_ocr_warns(self):
        warn, reason = _ocr_output_is_unreadable("")
        self.assertTrue(warn)
        self.assertEqual(reason, "empty")

    def test_whitespace_only_warns(self):
        warn, _ = _ocr_output_is_unreadable("   \n\t  ")
        self.assertTrue(warn)

    def test_degenerate_loop_warns(self):
        """The 2026-07-22 WhatsApp-scan failure: OCR emitting one token
        forever. This is the case the gate exists for."""
        warn, reason = _ocr_output_is_unreadable("the the the the " * 500)
        self.assertTrue(warn, f"token loop passed the gate (reason={reason})")

    def test_long_clean_legal_text_does_not_warn(self):
        text = (
            "IN THE COURT OF THE SESSIONS JUDGE AT MUMBAI. "
            "Criminal Bail Application Number 442 of 2026. "
            "The applicant is a permanent resident of Mumbai and has "
            "cooperated fully with the investigation throughout. He "
            "undertakes to attend every hearing without fail and to abide "
            "by any conditions this Honourable Court may impose upon him. "
        ) * 6
        warn, reason = _ocr_output_is_unreadable(text)
        self.assertFalse(warn, f"clean legal text triggered a warning: {reason}")

    # --- the distinction this wrapper exists for --------------------------

    def test_short_document_does_NOT_warn_even_though_unusable(self):
        """THE false positive this gate must not have.

        `_ocr_result_is_usable` calls anything under 50 long tokens
        'too_short' and returns unusable, because in the PDF path that
        triggers a free high-DPI retry. An Aadhaar card, a PAN card, a
        one-paragraph notice and a receipt are ALL under 50 tokens — warning
        on them would tell users their perfectly good documents are bad.
        """
        aadhaar_like = (
            "Government of India. Unique Identification Authority of India. "
            "Address: B-Wing, Flat 1004, Mumbai, Maharashtra 400097. "
            "VID 9135 5336 0627 3337."
        )
        usable, why = _ocr_result_is_usable(aadhaar_like)
        self.assertFalse(usable, "precondition: this sample should read as too_short")
        self.assertEqual(why, "too_short")

        warn, reason = _ocr_output_is_unreadable(aadhaar_like)
        self.assertFalse(
            warn,
            "a short but perfectly readable document must NOT be flagged "
            f"(reason={reason})",
        )

    def test_too_short_is_never_grounds_to_warn(self):
        for sample in ("Receipt No. 42. Paid in full.",
                       "NOTICE. The premises must be vacated by 30 June.",
                       "PAN ABCDE1234F"):
            warn, reason = _ocr_output_is_unreadable(sample)
            self.assertFalse(warn, f"{sample!r} warned with reason={reason}")

    def test_verdict_is_always_a_two_tuple_of_bool_and_str(self):
        """The image branch unpacks this directly; a shape change would break
        the gate silently rather than loudly."""
        for sample in ("", "short", "IN THE HIGH COURT OF JUDICATURE " * 20):
            v = _ocr_output_is_unreadable(sample)
            self.assertIsInstance(v, tuple)
            self.assertEqual(len(v), 2)
            self.assertIsInstance(v[0], bool)
            self.assertIsInstance(v[1], str)


class TelemetryTests(unittest.TestCase):
    """chars_per_megapixel is the partial-blur tell — the known gap in this
    design. It must be recorded for every image, and must never raise."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = cls._tmp.name
        cls.img = os.path.join(cls.tmp, "page.png")
        Image.new("L", (1000, 1000), color=255).save(cls.img)   # exactly 1 MP

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_records_all_expected_fields(self):
        t = _image_ocr_telemetry(self.img, "hello world", "clean")
        for key in ("ocr_char_count", "ocr_quality_verdict",
                    "chars_per_megapixel", "width", "height"):
            self.assertIn(key, t)

    def test_chars_per_megapixel_is_correct(self):
        t = _image_ocr_telemetry(self.img, "x" * 2500, "clean")
        self.assertEqual(t["width"], 1000)
        self.assertEqual(t["height"], 1000)
        # 2500 chars over exactly 1 MP
        self.assertAlmostEqual(t["chars_per_megapixel"], 2500.0, places=1)

    def test_sparse_text_on_a_dense_page_is_visible(self):
        """A full page returning a handful of characters is what partial blur
        looks like from the OCR side. The number must make that obvious."""
        dense = _image_ocr_telemetry(self.img, "x" * 3000, "clean")
        sparse = _image_ocr_telemetry(self.img, "x" * 90, "clean")
        self.assertGreater(dense["chars_per_megapixel"],
                           sparse["chars_per_megapixel"] * 10)

    def test_unreadable_image_does_not_raise(self):
        """Telemetry must never be able to fail an upload."""
        bad = os.path.join(self.tmp, "corrupt.png")
        with open(bad, "wb") as fh:
            fh.write(b"not an image")
        t = _image_ocr_telemetry(bad, "some text", "clean")
        self.assertEqual(t["ocr_char_count"], 9)
        self.assertEqual(t["chars_per_megapixel"], 0.0)   # unknown, not fatal

    def test_missing_file_does_not_raise(self):
        t = _image_ocr_telemetry(os.path.join(self.tmp, "gone.png"), "abc", "clean")
        self.assertEqual(t["ocr_char_count"], 3)


class ResolutionCheckTests(unittest.TestCase):
    """The signal that would actually have caught the 2026-08-10 case.

    A 467x401 scan of a full memorandum page (~55 DPI) produced fluent,
    confident, WRONG output — the surname "Kanayan" became "KHACHATAN".
    Nothing downstream can catch a plausible misread; only the pixel count
    can. Unlike blur, resolution is objectively measurable.
    """

    def test_the_real_failure_case_is_flagged(self):
        low, _ = _resolution_warning(467, 401)
        self.assertTrue(low, "the exact image that produced a wrong name "
                             "must be flagged as low resolution")

    def test_a_300_dpi_page_is_not_flagged(self):
        """8.5x11in at 300 DPI = 2550x3300. Must pass cleanly."""
        low, _ = _resolution_warning(2550, 3300)
        self.assertFalse(low)

    def test_a_typical_phone_photo_is_not_flagged(self):
        """12 MP phone camera — the common upload. Must not nag."""
        low, _ = _resolution_warning(3024, 4032)
        self.assertFalse(low)

    # --- the false positives an earlier threshold produced ---------------

    def test_id_card_scans_are_NOT_flagged(self):
        """REGRESSION GUARD. An earlier cut estimated DPI by assuming every
        image spanned an 11in page, and flagged BOTH of these — real cards
        that OCR'd perfectly. A card is ~3.4in, so 1200px across it is ~350
        DPI, not the 145 the estimate claimed. Physical size is unknowable
        from the file, which is why the decision is made on pixels."""
        for w, h, label in ((1200, 1600, "Aadhaar"), (1040, 780, "PAN")):
            low, _ = _resolution_warning(w, h)
            self.assertFalse(
                low, f"{label} card ({w}x{h}) wrongly flagged — this is the "
                     f"false positive that trains users to ignore warnings")

    def test_threshold_sits_in_the_observed_gap(self):
        """Known-bad long edges: 467, 566. Known-good: 1040, 1599, 1600.
        The threshold must separate them, not clip either side."""
        self.assertGreater(_MIN_LONG_EDGE_PX, 566)
        self.assertLess(_MIN_LONG_EDGE_PX, 1040)

    def test_boundary_is_where_the_constant_says(self):
        # Second dimension kept well below the threshold so the FIRST one is
        # unambiguously the long edge being tested.
        just_under, _ = _resolution_warning(_MIN_LONG_EDGE_PX - 1, 400)
        exactly_at, _ = _resolution_warning(_MIN_LONG_EDGE_PX, 400)
        just_over, _ = _resolution_warning(_MIN_LONG_EDGE_PX + 1, 400)
        self.assertTrue(just_under)
        self.assertFalse(exactly_at, "threshold is a floor, not exclusive")
        self.assertFalse(just_over)

    def test_orientation_does_not_matter(self):
        """Landscape and portrait of the same page must agree — the long
        edge is what carries the resolution."""
        self.assertEqual(_resolution_warning(2550, 3300)[0],
                         _resolution_warning(3300, 2550)[0])

    def test_zero_dimensions_fail_open(self):
        low, dpi = _resolution_warning(0, 0)
        self.assertFalse(low, "unknown dimensions must not warn")
        self.assertEqual(dpi, 0.0)

    def test_message_is_actionable_and_avoids_the_word_blurry(self):
        """A low-res image may be perfectly in focus. Calling it blurry
        would be wrong AND would send the user chasing the wrong fix."""
        msg = low_resolution_user_message("scan.png", 467, 401)
        self.assertIn("scan.png", msg)
        self.assertIn("467", msg, "must quote the actual pixel dimensions")
        self.assertIn("300", msg, "must state the target resolution")
        self.assertNotIn("blurry", msg.lower())
        self.assertNotIn("blur", msg.lower())
        # Must warn about the fields that actually matter in a legal document
        self.assertTrue(any(w in msg.lower() for w in ("names", "numbers", "dates")))


class IllegibleMarkerTests(unittest.TestCase):
    """The OCR prompt tells the model to write [illegible] instead of
    guessing. Nothing counted them before, so even when the model DID admit
    defeat, the pipeline ignored it."""

    def test_counts_markers(self):
        self.assertEqual(count_illegible_markers(
            "The applicant [illegible] on [illegible] before the court."), 2)

    def test_case_insensitive(self):
        self.assertEqual(count_illegible_markers("[ILLEGIBLE] and [Illegible]"), 2)

    def test_clean_text_has_none(self):
        self.assertEqual(count_illegible_markers("Perfectly readable text."), 0)

    def test_empty_and_none_safe(self):
        self.assertEqual(count_illegible_markers(""), 0)
        self.assertEqual(count_illegible_markers(None), 0)

    def test_threshold_tolerates_a_couple_of_smudges(self):
        """One or two markers on a long page is normal — a stamp over a word,
        a smudge. Warning there would be noise."""
        self.assertGreater(_ILLEGIBLE_WARN_THRESHOLD, 1)

    def test_telemetry_carries_the_count(self):
        import tempfile, os as _os
        from PIL import Image as _Image
        with tempfile.TemporaryDirectory() as d:
            p = _os.path.join(d, "x.png")
            _Image.new("L", (900, 900), color=255).save(p)
            t = _image_ocr_telemetry(p, "a [illegible] b [illegible]", "clean")
            self.assertEqual(t["illegible_markers"], 2)
            self.assertGreater(t["estimated_dpi"], 0)


class DpiEstimateTests(unittest.TestCase):

    def test_matches_the_documented_case(self):
        """467px long edge over an assumed 11in page ~= 42-55 DPI."""
        self.assertLess(estimate_dpi(467, 401), 100)

    def test_zero_is_safe(self):
        self.assertEqual(estimate_dpi(0, 0), 0.0)
        self.assertEqual(estimate_dpi(-5, 10), 0.0)


class PersistenceAndMessagingTests(unittest.TestCase):

    def test_processed_file_carries_ocr_status(self):
        """The withheld-file marker has to survive to the thread_files row —
        without it, 'use it anyway' cannot find the image."""
        pf = ProcessedFile("scan.jpg", "image", "image/jpeg", 1234)
        self.assertEqual(pf.ocr_status, "")
        pf.ocr_status = OCR_STATUS_UNREADABLE
        self.assertEqual(pf.ocr_status, "ocr_unreadable")

    def test_status_constant_is_distinct_from_existing_values(self):
        """'failed' already means the OCR CALL errored, and '' means nothing
        to OCR. Collision would make the retry query pick up wrong rows."""
        self.assertNotIn(OCR_STATUS_UNREADABLE, ("", "pending", "complete", "failed"))

    def test_user_message_names_the_file_and_offers_the_override(self):
        msg = unreadable_user_message("FIR_page1.jpg")
        self.assertIn("FIR_page1.jpg", msg)
        self.assertIn("use it anyway", msg.lower())

    def test_user_message_has_no_jargon_or_internals(self):
        msg = unreadable_user_message("scan.png").lower()
        for leak in ("ocr", "garbled", "laplacian", "threshold",
                     "chromadb", "gemini", "null"):
            self.assertNotIn(leak, msg, f"user-facing text leaks {leak!r}")

    def test_user_message_is_actionable(self):
        """Telling someone their image is bad without saying what to do is
        the complaint this feature exists to prevent."""
        msg = unreadable_user_message("scan.png").lower()
        self.assertTrue(any(w in msg for w in ("light", "flat", "steady")),
                        "message gives no practical guidance")


def _run_standalone() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite([
        loader.loadTestsFromTestCase(ReadabilityGateContract),
        loader.loadTestsFromTestCase(TelemetryTests),
        loader.loadTestsFromTestCase(ResolutionCheckTests),
        loader.loadTestsFromTestCase(IllegibleMarkerTests),
        loader.loadTestsFromTestCase(DpiEstimateTests),
        loader.loadTestsFromTestCase(PersistenceAndMessagingTests),
    ])
    # Names must be collected BEFORE running — unittest empties the suite.
    names = []
    for group in suite:
        for t in group:
            names.append(f"{type(t).__name__}.{t._testMethodName}")

    result = unittest.TextTestRunner(verbosity=0).run(suite)
    failed = {f"{type(t).__name__}.{t._testMethodName}"
              for t, _ in result.failures + result.errors}
    for n in names:
        print(f"  {'FAIL' if n in failed else 'PASS'}  {n}")

    print()
    if result.wasSuccessful():
        print(f"ALL PASSED ({result.testsRun} tests)")
        return 0
    print(f"FAILED: {len(failed)} of {result.testsRun}")
    for t, tb in result.failures + result.errors:
        print(f"\n--- {t._testMethodName} ---\n{tb}")
    return 1


if __name__ == "__main__":
    sys.exit(_run_standalone())
