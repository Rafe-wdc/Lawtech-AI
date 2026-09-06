"""Gap #3 — file-nature classifier accuracy test on real fixtures.

Reads real files from test_pdfs/ and test_docx/, extracts text via the
same PyMuPDF / python-docx path the ingest pipeline uses, runs the
Gemini Flash Lite classifier, and asserts the returned `kind` matches
the human-labelled expectation.

Fixtures deliberately chosen with obvious markers so the classifier
should score high; if this test fails, either the classifier prompt
needs tuning or the fixture is genuinely ambiguous.

Run: `python -m tests.test_file_classifier_gap3`
"""
from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.file_classifier import classify_file_kind, ALL_KINDS  # noqa: E402

_PDFS = _ROOT / "test_pdfs"
_DOCX = _ROOT / "test_docx"


@dataclass
class Case:
    path: Path
    expected: str
    note: str
    # Allow multiple acceptable answers when a document genuinely
    # straddles two kinds (e.g., a court order that also functions as
    # a judgment). Test passes if actual is in `acceptable`.
    acceptable: tuple[str, ...] = ()

    def ok(self, kind: str) -> bool:
        if kind == self.expected:
            return True
        if self.acceptable and kind in self.acceptable:
            return True
        return False


# Fixture bank. Add / edit as we learn what the classifier struggles with.
CASES: list[Case] = [
    Case(_PDFS / "0156 Publish FIR.pdf",
         expected="fir",
         note="Standalone FIR from Pune Market Yard PS"),
    Case(_PDFS / "Adv. P. P. Deshmukh Sec. 9.pdf",
         expected="pleading",
         acceptable=("pleading", "notice"),
         note="Advocate's Section 9 application — pleading or notice"),
    Case(_PDFS / "Notice_1 (1) (1).pdf",
         expected="notice",
         acceptable=("notice", "correspondence"),
         note="Legal notice"),
    Case(_PDFS / "judgment and decree nisha vs jai mata.pdf",
         expected="judgment",
         acceptable=("judgment", "court_order"),
         note="Judgment + decree — should hit judgment"),
    Case(_PDFS / "judgement123.pdf",
         expected="judgment",
         acceptable=("judgment", "court_order"),
         note="Judgment file"),
    Case(_PDFS / "Index.pdf",
         expected="other",
         acceptable=("other", "correspondence", "pleading"),
         note="Index page — likely 'other'"),
    Case(_DOCX / "APPLICATION UNDER SECTION 290 OF BNSS.docx",
         expected="pleading",
         note="Application under Section 290 BNSS — pleading"),
    Case(_DOCX / "WS 1 on old tool.docx",
         expected="pleading",
         note="Written statement — pleading"),
    Case(_DOCX / "MATTER For FIR 156 3 CRPC.docx",
         expected="pleading",
         acceptable=("pleading", "correspondence", "fir"),
         note="Filing about an FIR — pleading-shaped material"),
    Case(_DOCX / "Nilamber_Behra_vs_Lrs_Of_Kuldeep_Singh_on_24_January_2026.docx",
         expected="judgment",
         acceptable=("judgment", "court_order"),
         note="Indian Kanoon-style judgment DOCX"),
]


# ---------------------------------------------------------------------
# Text extraction (uses the same path as ingest)
# ---------------------------------------------------------------------

def _extract_pdf_text(path: Path) -> str:
    """Extract text from a PDF using PyMuPDF (matches ingest primary path)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return ""
    text_parts = []
    try:
        doc = fitz.open(path.as_posix())
        for page in doc:
            text_parts.append(page.get_text())
            if len(''.join(text_parts)) > 20000:
                break  # classifier only uses first ~3000 chars anyway
        doc.close()
    except Exception as e:
        print(f"    [warn] PDF extract failed for {path.name}: {e}")
    return "\n".join(text_parts)


def _extract_docx_text(path: Path) -> str:
    """Extract text from a DOCX using python-docx."""
    try:
        from docx import Document
    except ImportError:
        return ""
    try:
        doc = Document(path.as_posix())
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception as e:
        print(f"    [warn] DOCX extract failed for {path.name}: {e}")
        return ""


def _extract(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _extract_pdf_text(path)
    if ext == ".docx":
        return _extract_docx_text(path)
    return ""


# ---------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------

async def main() -> int:
    print("=" * 78)
    print(f"Gap #3 — file-nature classifier accuracy  ({len(CASES)} fixtures)")
    print(f"Taxonomy: {', '.join(ALL_KINDS)}")
    print("=" * 78)

    if not _PDFS.exists() and not _DOCX.exists():
        print(f"\n[SKIP] fixture dirs not present ({_PDFS.name}/, {_DOCX.name}/).")
        print("       This test needs real client PDFs which are not committed.")
        print("       Drop fixtures into those dirs locally to run classifier accuracy.")
        return 0

    passed = 0
    total = 0
    fails: list[tuple[Case, str, float]] = []

    for case in CASES:
        total += 1
        print(f"\n---- {case.path.name} ----")
        print(f"     expected: {case.expected} "
              f"{'(acceptable: ' + ', '.join(case.acceptable) + ')' if case.acceptable else ''}")
        print(f"     note:     {case.note}")

        if not case.path.exists():
            print(f"     [SKIP] fixture missing: {case.path}")
            continue

        text = _extract(case.path)
        if not text.strip():
            print(f"     [SKIP] no text extracted (extractor gap)")
            continue

        start = time.perf_counter()
        try:
            kind, confidence = await asyncio.to_thread(
                classify_file_kind, text, case.path.name,
            )
        except Exception as e:
            print(f"     [ERROR] classifier: {type(e).__name__}: {e}")
            fails.append((case, "ERROR", 0.0))
            continue
        elapsed = time.perf_counter() - start

        verdict = "PASS" if case.ok(kind) else "FAIL"
        print(f"     got:      {kind}  (confidence={confidence:.2f}, {elapsed*1000:.0f}ms)")
        print(f"     verdict:  {verdict}")

        if verdict == "PASS":
            passed += 1
        else:
            fails.append((case, kind, confidence))

    print()
    print("=" * 78)
    print(f"SUMMARY: {passed}/{total} correct")
    print("=" * 78)
    if fails:
        print()
        print("FAILURES / MISCLASSIFICATIONS:")
        for c, got, conf in fails:
            acc = f"  acceptable={c.acceptable}" if c.acceptable else ""
            print(f"  [{c.path.name}] expected={c.expected}  got={got}  conf={conf:.2f}{acc}")

    # Pass threshold: at least 70% correct is acceptable for phase A
    # (classifier is Flash Lite; misclassifications are handled downstream
    # by treating unknown/other as "no specialisation").
    return 0 if passed / max(total, 1) >= 0.7 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
