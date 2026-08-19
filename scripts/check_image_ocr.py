"""Run the real OCR + readability gate against an image, and show the verdict.

The fastest way to answer "would we warn the user about this image?" without
starting the server. Point it at any upload a user complained about.

USAGE
=====
    python scripts/check_image_ocr.py path/to/image.jpg

    # several at once — useful for comparing good vs bad
    python scripts/check_image_ocr.py samples/*.jpg

    # show what the OCR actually returned
    python scripts/check_image_ocr.py image.jpg --show-text

COST
====
One Gemini Vision call per image (~$0.001). Needs OPENAI_API_KEY and
GOOGLE_API_KEY in .env — the same ones the server uses.

READING THE OUTPUT
==================
    WARN   OCR produced nothing trustworthy. The user is told the image
           could not be read, the text is withheld, and they get a
           "Use it anyway" override.

    PASS   Usable text came back and flows to the agents as normal.

    PASS but low chars/megapixel is the case to watch: a dense page that
    returned very little text is what PARTIAL blur looks like from the OCR
    side — half the page readable, half lost, and the gate cannot see it.
    That is the known gap this metric exists to measure.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("images", nargs="+", help="image file(s) to check")
    ap.add_argument("--show-text", action="store_true",
                    help="print the OCR output (first 600 chars)")
    args = ap.parse_args()

    from core.file_processor import (
        _image_ocr_telemetry,
        _ocr_output_is_unreadable,
        _ocr_result_is_usable,
        _vision_ocr_image,
    )

    paths = [p for p in args.images if os.path.isfile(p)]
    missing = [p for p in args.images if not os.path.isfile(p)]
    for p in missing:
        print(f"  skip (not found): {p}")
    if not paths:
        return 2

    print()
    print(f"{'file':<28} {'chars':>7} {'ch/MP':>8} {'verdict':<14} decision")
    print("-" * 74)

    for path in paths:
        name = os.path.basename(path)
        try:
            text = _vision_ocr_image(path, name)
        except Exception as e:
            print(f"{name[:27]:<28} {'-':>7} {'-':>8} {'OCR ERROR':<14} {str(e)[:24]}")
            continue

        warn, reason = _ocr_output_is_unreadable(text)
        raw_usable, raw_reason = _ocr_result_is_usable(text)
        tel = _image_ocr_telemetry(path, text, reason)

        decision = "WARN user" if warn else "PASS"
        print(f"{name[:27]:<28} {tel['ocr_char_count']:>7} "
              f"{tel['chars_per_megapixel']:>8.0f} {reason[:13]:<14} {decision}")

        # Surface the too_short divergence explicitly — it is the false
        # positive the gate is deliberately built to avoid, and seeing it
        # here is how you confirm the distinction is doing its job.
        if not raw_usable and not warn:
            print(f"{'':<28} {'':>7} {'':>8} "
                  f"-> raw gate said {raw_reason!r}; not warned (short != bad)")

        if args.show_text:
            snippet = (text or "").strip().replace("\n", " ")[:600]
            print(f"\n    OCR OUTPUT: {snippet or '(nothing)'}\n")

    print()
    print("chars/MP is the partial-blur signal: a dense page returning few")
    print("characters means text was lost that the gate cannot detect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
