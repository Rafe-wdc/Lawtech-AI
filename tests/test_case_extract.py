"""core/case_extract.py and the three public file_processor wrappers.

Everything runs in-process except the two child-process tests (a real
spawned child: happy path, timeout kill, abort kill). Vision OCR is
monkeypatched: no network, no credentials.
"""
from __future__ import annotations

import logging
import threading
import time

import pytest

from core import case_extract as ce
from core import file_processor as fp
from tests.case_support import make_docx, make_pdf, make_xlsx


def _write(tmp_path, name: str, data: bytes) -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def _png_bytes() -> bytes:
    import fitz
    return fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), 0).tobytes("png")


def _pdf_with_scanned_page(text_first: str = "typed page") -> bytes:
    """Page 1 has a text layer; page 2 is a full-page raster with no layer."""
    import fitz
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text_first, fontsize=11)
    pg = doc.new_page()
    pg.insert_image(pg.rect, stream=_png_bytes())
    return doc.tobytes()


def _scanned_pdf(pages: int = 3) -> bytes:
    import fitz
    doc = fitz.open()
    for _ in range(pages):
        pg = doc.new_page()
        pg.insert_image(pg.rect, stream=_png_bytes())
    return doc.tobytes()


def _encrypted_pdf() -> bytes:
    import fitz
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "secret", fontsize=11)
    return doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="x")


# --- PDF -----------------------------------------------------------------------

def test_pdf_pages_keep_numbering_including_empty_pages(tmp_path):
    tokens = ["TOKEN-ONE alpha", "TOKEN-TWO beta", "", "TOKEN-FOUR delta", "TOKEN-FIVE eps"]
    out = ce.extract_document(_write(tmp_path, "d.pdf", make_pdf(tokens)), "pdf", "doc-1")
    assert out["extractor"] == "pdf_text" and out["page_count"] == 5 and out["ocr_pages"] == 0
    lines = out["lines"]
    assert [ln["page"] for ln in lines] == [1, 2, 3, 4, 5] and [ln["index"] for ln in lines] == [0, 1, 2, 3, 4]
    assert all(ln["unit"] == "page" and ln["source"] == "layer" for ln in lines)
    for ln, tok in zip(lines, tokens):
        assert (tok.split()[0] in ln["text"]) if tok else ln["text"] == ""
    assert lines[2]["chars"] == 0 and out["chars_total"] == sum(ln["chars"] for ln in lines)


def test_image_only_pages_are_re_read_with_selective_ocr(tmp_path, monkeypatch):
    calls: dict = {}

    def fake_ocr(file_path, pages, emit=None, file_name=""):
        calls["pages"], calls["label"] = list(pages), file_name
        return {i: f"OCR-TEXT page {i + 1}" for i in pages}

    monkeypatch.setattr(fp, "_vision_ocr_pdf_pages", fake_ocr)
    out = ce.extract_document(_write(tmp_path, "s.pdf", _pdf_with_scanned_page()), "pdf", "doc-2")
    assert calls == {"pages": [1], "label": "doc-2"}                      # only the raster page, labelled by id
    assert out["extractor"] == "pdf_text+ocr_pages" and out["ocr_pages"] == 1 and out["page_count"] == 2
    assert out["lines"][0]["source"] == "layer" and "typed page" in out["lines"][0]["text"]
    assert out["lines"][1]["source"] == "ocr" and out["lines"][1]["text"] == "OCR-TEXT page 2"


def test_whole_document_ocr_and_ocr_outage_is_transient(tmp_path, monkeypatch):
    path = _write(tmp_path, "scan.pdf", _scanned_pdf(3))
    monkeypatch.setattr(fp, "_vision_ocr_pdf_pages",
                        lambda fp_, pages, emit=None, file_name="": {i: f"page {i + 1} text" for i in pages})
    out = ce.extract_document(path, "pdf", "doc-3")
    assert out["extractor"] == "pdf_ocr" and out["ocr_pages"] == 3
    assert [ln["text"] for ln in out["lines"]] == ["page 1 text", "page 2 text", "page 3 text"]
    monkeypatch.setattr(fp, "_vision_ocr_pdf_pages", lambda *a, **k: {})   # the call failed for every page
    with pytest.raises(fp.OCRUnavailable) as e:
        ce.extract_document(path, "pdf", "doc-3")
    assert ce.classify_exception(e.value) == ("ocr_error", False)


def test_encrypted_and_corrupt_pdfs_are_permanent_errors(tmp_path):
    with pytest.raises(ValueError) as e:
        ce.extract_document(_write(tmp_path, "enc.pdf", _encrypted_pdf()), "pdf", "d")
    assert ce.classify_exception(e.value) == ("encrypted_pdf", True)
    with pytest.raises(Exception) as e2:
        ce.extract_document(_write(tmp_path, "bad.pdf", b"%PDF-1.4\n" + b"\x00garbage" * 50), "pdf", "d")
    code, permanent = ce.classify_exception(e2.value)
    assert code in ("corrupt_file", "empty_pdf") and permanent is True


# --- images, office, text --------------------------------------------------------

def test_image_is_one_ocr_page_and_a_failed_call_is_ocr_error(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "_vision_ocr_image", lambda path, filename="": f"read {filename}")
    path = _write(tmp_path, "p.png", _png_bytes())
    out = ce.extract_document(path, "png", "doc-img")
    assert out["extractor"] == "image_ocr" and out["page_count"] == 1 and out["ocr_pages"] == 1
    assert out["lines"] == [{"unit": "page", "page": 1, "index": 0, "text": "read doc-img", "source": "ocr",
                             "confidence": None, "chars": 12, "heading": None}]

    def boom(path, filename=""):
        raise RuntimeError("401 invalid api key")

    monkeypatch.setattr(fp, "_vision_ocr_image", boom)
    with pytest.raises(ce.ExtractError) as e:
        ce.extract_document(path, "png", "doc-img")
    assert e.value.code == "ocr_error" and e.value.permanent is False


def test_docx_becomes_sections_with_a_heading(tmp_path):
    paras = ["1. Background", "The complainant advanced a loan of Rs 4,70,000 to the accused.",
             "The cheque was returned unpaid.", "2. Notice", "A legal notice was issued within thirty days."]
    out = ce.extract_document(_write(tmp_path, "n.docx", make_docx(paras)), "docx", "doc-docx")
    assert out["extractor"] == "docx" and out["page_count"] is None and out["ocr_pages"] == 0
    assert len(out["lines"]) == 1
    ln = out["lines"][0]
    assert ln["unit"] == "section" and ln["source"] == "docx" and ln["heading"] == "1. Background"
    assert "4,70,000" in ln["text"] and "2. Notice" in ln["text"] and ln["chars"] == len(ln["text"])


def test_split_sections_packs_paragraphs_and_wraps_giant_ones():
    text = "Heading A\n\n" + ("para one. " * 50).strip() + "\n\n" + "x" * 9000 + "\n\nTail para."
    secs = ce.split_sections(text, max_chars=4000)
    assert secs[0][0] == "Heading A" and all(len(body) <= 4000 for _, body in secs)
    assert len(secs) >= 4 and secs[-1][1].endswith("Tail para.")
    assert ce.split_sections("") == []
    assert ce.split_sections("Only a sentence.")[0] == (None, "Only a sentence.")


def test_xlsx_csv_txt_and_md(tmp_path):
    out = ce.extract_document(_write(tmp_path, "l.xlsx", make_xlsx({
        "Ledger": [["date", "amount"], ["2026-01-01", 1000]], "Notes": [["n"], ["hello"]]})), "xlsx", "doc-x")
    assert out["extractor"] == "xlsx" and [ln["heading"] for ln in out["lines"]] == ["Ledger", "Notes"]
    assert out["lines"][0]["unit"] == "sheet" and "1000" in out["lines"][0]["text"] and out["lines"][1]["index"] == 1

    out = ce.extract_document(_write(tmp_path, "c.csv", b"a,b\n1,2\n"), "csv", "doc-c")
    assert out["lines"][0]["unit"] == "sheet" and out["lines"][0]["source"] == "csv" and "1" in out["lines"][0]["text"]

    out = ce.extract_document(_write(tmp_path, "t.txt", "﻿नोटिस\n\nदूसरा पैरा".encode("utf-8")), "txt", "doc-t")
    assert out["lines"][0]["text"].startswith("नोटिस") and out["lines"][0]["source"] == "text"

    out = ce.extract_document(_write(tmp_path, "u.md", "# Title\n\nbody".encode("utf-16")), "md", "doc-m")
    assert out["lines"][0]["text"].startswith("# Title") and out["lines"][0]["heading"] == "# Title"


def test_empty_document_still_yields_one_complete_line(tmp_path):
    out = ce.extract_document(_write(tmp_path, "e.txt", b""), "txt", "doc-e")
    assert out["lines"] == [{"unit": "section", "page": None, "index": 0, "text": "", "source": "text",
                             "confidence": None, "chars": 0, "heading": None}]
    assert out["chars_total"] == 0


def test_unknown_extension_is_permanent():
    with pytest.raises(ce.ExtractError) as e:
        ce.extract_document("whatever", "exe", "doc-u")
    assert e.value.code == "unsupported_type" and e.value.permanent is True


# --- child process -----------------------------------------------------------------

def test_child_process_returns_the_result_file(tmp_path):
    path = _write(tmp_path, "d.pdf", make_pdf(["child says hi"]))
    out = ce.run_extraction(path, "pdf", "doc-child", str(tmp_path / "result.json"), timeout_s=180)
    assert out["ok"] is True and out["extractor"] == "pdf_text" and "child says hi" in out["lines"][0]["text"]


def test_hanging_child_is_killed_on_timeout(tmp_path):
    path = _write(tmp_path, "d.pdf", make_pdf(["x"]))
    t0 = time.monotonic()
    with pytest.raises(ce.ExtractTimeout):
        ce.run_extraction(path, "pdf", "doc-hang", str(tmp_path / "r.json"), timeout_s=3,
                          entry="tests.case_support:hanging_extract")
    assert time.monotonic() - t0 < 60


def test_child_is_killed_when_the_caller_aborts(tmp_path):
    path = _write(tmp_path, "d.pdf", make_pdf(["x"]))
    abort = threading.Event()
    threading.Timer(1.0, abort.set).start()
    with pytest.raises(ce.ExtractAborted):
        ce.run_extraction(path, "pdf", "doc-abort", str(tmp_path / "r.json"), timeout_s=120, abort=abort,
                          entry="tests.case_support:hanging_extract")


def test_entry_failure_is_classified_in_the_result(tmp_path):
    path = _write(tmp_path, "d.pdf", make_pdf(["x"]))
    out = ce.run_extraction(path, "pdf", "doc-fail", str(tmp_path / "r.json"), timeout_s=60, in_process=True,
                            entry="tests.case_support:failing_extract")
    assert out == {"ok": False, "code": "extract_error", "permanent": False,
                   "detail": "RuntimeError: extractor exploded"}


# --- logging rule -----------------------------------------------------------------

class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        kv = getattr(record, "_extra_kv", {}) or {}
        self.lines.append(record.getMessage() + " " + " ".join(f"{k}={v}" for k, v in kv.items()))


def test_no_filename_or_text_in_extraction_logs(tmp_path, monkeypatch):
    """The file on disk is named by id; the sentinel content must never be logged."""
    cap = _Capture()
    loggers = [logging.getLogger(m.log._logger.name) for m in (ce, fp)]
    for lg in loggers:
        lg.addHandler(cap)
        lg.setLevel(logging.DEBUG)
    try:
        doc_id = "11111111-2222-3333-4444-555555555555"
        data = _pdf_with_scanned_page("SENTINEL-TEXT-9931 cheque of Rs 4,70,000")
        monkeypatch.setattr(fp, "_vision_ocr_pdf_pages",
                            lambda fp_, pages, emit=None, file_name="": {i: "SENTINEL-OCR-7712" for i in pages})
        out = ce.extract_document(_write(tmp_path, f"{doc_id}.pdf", data), "pdf", doc_id)
        assert "SENTINEL-TEXT-9931" in out["lines"][0]["text"] and out["lines"][1]["text"] == "SENTINEL-OCR-7712"
    finally:
        for lg in loggers:
            lg.removeHandler(cap)
    text = "\n".join(cap.lines)
    assert cap.lines and doc_id[:8] in text
    for needle in ("SENTINEL", "9931", "7712", "4,70,000", "cheque"):
        assert needle not in text, f"{needle!r} leaked: {text[:300]}"
