"""core/case_sniff.py — signatures, extension agreement, filename hygiene."""
from __future__ import annotations

import io
import struct
import zipfile

import pytest

from core.case_sniff import SUPPORTED_TYPES, matches, sanitise_filename, sniff

PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
MP3_ID3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64
MP3_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 64
WAV = b"RIFF\x24\x08\x00\x00WAVEfmt " + b"\x00" * 32
OGG = b"OggS\x00\x02" + b"\x00" * 64
FLAC = b"fLaC\x00\x00\x00\x22" + b"\x00" * 64
M4A = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A mp42isom" + b"\x00" * 32
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2mp41" + b"\x00" * 32
MOV = b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  " + b"\x00" * 32
MKV = b"\x1aE\xdf\xa3\x93B\x82\x88matroska" + b"\x00" * 32
WEBM = b"\x1aE\xdf\xa3\x9fB\x86\x81\x01B\xf7\x81\x01B\xf2\x81\x04B\xf3\x81\x08B\x82\x84webm" + b"\x00" * 16
THREE_GP = b"\x00\x00\x00\x14ftyp3gp4\x00\x00\x00\x00" + b"\x00" * 32
AVI = b"RIFF\x00\x10\x00\x00AVI LIST" + b"\x00" * 32
EXE = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
TEXT = "Legal Notice under section 138 of the Negotiable Instruments Act, 1881.\nTo,\nMr. Mehta\n".encode()
HINDI = "कानूनी सूचना: धारा 138 परक्राम्य लिखत अधिनियम, 1881\n".encode("utf-8")
UTF16 = "\ufeffNotice to tenant\n".encode("utf-16")
RANDOM = bytes(range(256)) * 4


def _ooxml(*names: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("_rels/.rels", "<Relationships/>")
        for n in names:
            z.writestr(n, "<x/>" * 50)
    return buf.getvalue()


DOCX = _ooxml("word/_rels/document.xml.rels", "word/document.xml")
XLSX = _ooxml("xl/workbook.xml", "xl/worksheets/sheet1.xml")
PPTX = _ooxml("ppt/presentation.xml")
PLAIN_ZIP = (lambda b: (zipfile.ZipFile(b, "w").writestr("notes.txt", "hi"), b.getvalue())[1])(io.BytesIO())


@pytest.mark.parametrize("head,family,detail", [
    (PDF, "pdf", "pdf"), (b"junk\n" * 50 + PDF, "pdf", "pdf"),
    (PNG, "image", "png"), (JPG, "image", "jpg"), (WEBP, "image", "webp"),
    (DOCX, "ooxml", "docx"), (XLSX, "ooxml", "xlsx"), (PPTX, "ooxml", "pptx"), (PLAIN_ZIP, "zip", "zip"),
    (MP3_ID3, "media", "mp3"), (MP3_FRAME, "media", "mp3"), (WAV, "media", "wav"), (OGG, "media", "ogg"),
    (FLAC, "media", "flac"), (M4A, "media", "m4a"), (MP4, "media", "mp4"), (MOV, "media", "mov"),
    (MKV, "media", "mkv"), (WEBM, "media", "webm"), (THREE_GP, "media", "3gp"), (AVI, "media", "avi"),
    (EXE, "exec", "executable"), (ELF, "exec", "executable"), (OLE, "ole", "ole"),
    (TEXT, "text", "text"), (HINDI, "text", "text"), (UTF16, "text", "text"),
    (b"", "unknown", ""), (RANDOM, "unknown", ""),
])
def test_signatures(head, family, detail):
    s = sniff(head)
    assert (s.family, s.detail) == (family, detail)


def test_zip_with_data_descriptor_falls_back_to_byte_search():
    # streamed writers set bit 3 and leave sizes zero: the header walk must stop, not loop
    name = b"[Content_Types].xml"
    header = b"PK\x03\x04" + struct.pack("<HHHHHIIIHH", 20, 0x08, 8, 0, 0, 0, 0, 0, len(name), 0) + name
    blob = header + b"\x00" * 200 + b"word/document.xml" + b"\x00" * 100
    assert sniff(blob).detail == "docx"


def test_text_tolerates_a_cut_multibyte_character_but_not_binary():
    cut = HINDI * 300
    cut = cut[: len(cut) - 1]                      # ends mid-character
    assert sniff(cut).family == "text"
    assert sniff(TEXT + b"\x00\x01\x02").family == "unknown"


@pytest.mark.parametrize("ext,head,ok", [
    ("pdf", PDF, True), ("pdf", PNG, False), ("pdf", EXE, False), ("pdf", TEXT, False),
    ("png", PNG, True), ("jpg", PNG, False), ("jpg", JPG, True), ("webp", WEBP, True), ("png", WEBP, False),
    ("docx", DOCX, True), ("docx", XLSX, False), ("docx", PLAIN_ZIP, False), ("xlsx", XLSX, True),
    ("xlsx", _ooxml(), True),                     # package seen, members not yet: accepted, worker re-checks
    ("docx", OLE, False), ("docx", PPTX, False),
    ("txt", TEXT, True), ("txt", HINDI, True), ("csv", b"a,b,c\n1,2,3\n", True), ("md", TEXT, True),
    ("txt", PDF, False), ("txt", MP3_ID3, False),
    ("mp3", MP3_ID3, True), ("mp3", MP4, True), ("mp4", WAV, True), ("mp3", TEXT, False), ("mp4", EXE, False),
    ("exe", EXE, False), ("doc", OLE, False), ("gif", b"GIF89a" + b"\x00" * 20, False),
])
def test_matches_extension_agreement(ext, head, ok):
    got, mime = matches(head, ext)
    assert got is ok
    assert isinstance(mime, str)


def test_matches_reports_canonical_mime_for_text_and_sniffed_mime_otherwise():
    assert matches(b"a,b\n", "csv") == (True, "text/csv")
    assert matches(PNG, "pdf") == (False, "image/png")


@pytest.mark.parametrize("raw,display,ext", [
    (r"C:\Users\rafe\Downloads\Legal Notice.PDF", "Legal Notice.PDF", "pdf"),
    ("../../etc/passwd", "passwd", ""),
    ("photo.JPEG", "photo.JPEG", "jpg"),
    ("नोटिस धारा 138.pdf", "नोटिस धारा 138.pdf", "pdf"),
    ('evil<>:"|?*name.docx', "evilname.docx", "docx"),
    ("tabs\tand\nnewlines  .txt", "tabsandnewlines.txt", "txt"),
    ("   ", "document", ""),
    ("", "document", ""),
    (".hidden", "hidden", ""),
    ("archive.tar.gz", "archive.tar.gz", "gz"),
    ("noext", "noext", ""),
    ("trailing dots....", "trailing dots", ""),
])
def test_sanitise_filename(raw, display, ext):
    assert sanitise_filename(raw) == (display, ext)


def test_sanitise_filename_caps_length_and_keeps_extension():
    display, ext = sanitise_filename("x" * 500 + ".pdf")
    assert ext == "pdf" and display.endswith(".pdf") and len(display) <= 200


def test_supported_types_match_the_chat_pipeline_plus_media():
    docs = {"pdf", "docx", "xlsx", "csv", "txt", "md", "png", "jpg", "webp"}
    assert docs <= set(SUPPORTED_TYPES)
    assert {"mp3", "wav", "m4a", "mp4", "mov", "mkv", "webm"} <= set(SUPPORTED_TYPES)
    assert "exe" not in SUPPORTED_TYPES and "doc" not in SUPPORTED_TYPES and "zip" not in SUPPORTED_TYPES
