"""Magic-byte sniffing and filename hygiene for case uploads.

The browser PUTs straight to S3, so the API never holds the whole file. At
``complete`` it reads the first ``SNIFF_BYTES`` of the object and checks that
what is there agrees with the extension the lawyer uploaded under. A dozen
signatures cover every type the ingestion pipeline accepts (the same set as
``core/file_processor.ALLOWED_EXTENSIONS``) plus the audio and video
containers that are stored as ``unsupported`` until transcription ships.
Anything else (executables, legacy ``.doc``, unknown binaries) is a mismatch.

No third-party sniffer: ``python-magic`` is absent and ``filetype`` is only a
transitive dependency (design, section 9).
"""
from __future__ import annotations

import re
import struct
import unicodedata
from dataclasses import dataclass
from typing import Optional

SNIFF_BYTES = 8192
FILENAME_MAX = 200

# extension -> (family, canonical mime). Families: pdf | image | ooxml | text | media.
DOCUMENT_TYPES: dict[str, tuple[str, str]] = {
    "pdf": ("pdf", "application/pdf"),
    "docx": ("ooxml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "xlsx": ("ooxml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "csv": ("text", "text/csv"),
    "txt": ("text", "text/plain"),
    "md": ("text", "text/markdown"),
}
IMAGE_TYPES: dict[str, tuple[str, str]] = {
    "png": ("image", "image/png"),
    "jpg": ("image", "image/jpeg"),
    "webp": ("image", "image/webp"),
}
MEDIA_TYPES: dict[str, tuple[str, str]] = {
    "mp3": ("media", "audio/mpeg"), "wav": ("media", "audio/wav"), "m4a": ("media", "audio/mp4"),
    "aac": ("media", "audio/aac"), "ogg": ("media", "audio/ogg"), "opus": ("media", "audio/ogg"),
    "flac": ("media", "audio/flac"), "amr": ("media", "audio/amr"),
    "mp4": ("media", "video/mp4"), "mov": ("media", "video/quicktime"),
    "mkv": ("media", "video/x-matroska"), "webm": ("media", "video/webm"),
    "3gp": ("media", "video/3gpp"), "avi": ("media", "video/x-msvideo"),
}
SUPPORTED_TYPES: dict[str, tuple[str, str]] = {**DOCUMENT_TYPES, **IMAGE_TYPES, **MEDIA_TYPES}
_EXT_ALIASES = {"jpeg": "jpg"}


def is_media_ext(ext: str) -> bool:
    return ext in MEDIA_TYPES


def canonical_mime(ext: str) -> str:
    spec = SUPPORTED_TYPES.get(ext)
    return spec[1] if spec else "application/octet-stream"


@dataclass(frozen=True)
class Sniff:
    family: str        # pdf | image | ooxml | zip | ole | text | media | exec | unknown
    mime: str          # best guess, "" when unknown
    detail: str = ""   # docx | xlsx | png | mp3 | ...


# ---------------------------------------------------------------- signatures

def sniff(head: bytes) -> Sniff:
    """Classify the first bytes of a file. Never raises."""
    h = bytes(head or b"")
    if b"%PDF-" in h[:1024]:                      # the header may follow up to 1 KB of junk
        return Sniff("pdf", "application/pdf", "pdf")
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return Sniff("image", "image/png", "png")
    if h.startswith(b"\xff\xd8\xff"):
        return Sniff("image", "image/jpeg", "jpg")
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return Sniff("image", "image/webp", "webp")
    if h[:4] in (b"II*\x00", b"MM\x00*"):
        return Sniff("image", "image/tiff", "tiff")
    if h[:6] in (b"GIF87a", b"GIF89a"):
        return Sniff("image", "image/gif", "gif")
    if h[:2] == b"BM" and len(h) > 14:
        return Sniff("image", "image/bmp", "bmp")
    if h[:4] == b"PK\x03\x04":
        return _sniff_zip(h)
    if h[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return Sniff("ole", "application/x-ole-storage", "ole")   # legacy .doc / .xls
    if h[:2] == b"MZ" or h[:4] == b"\x7fELF" or h[:4] in (b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xce"):
        return Sniff("exec", "application/octet-stream", "executable")
    media = _sniff_media(h)
    if media:
        return media
    text = _sniff_text(h)
    if text:
        return text
    return Sniff("unknown", "", "")


def _sniff_zip(h: bytes) -> Sniff:
    """Walk the local file headers inside the sniffed window and look at the
    member names: Word packages carry ``word/``, Excel ``xl/``. When the walk
    stops early (streamed writers use data descriptors) fall back to a byte
    search of the window; the worker re-checks the whole package on extraction."""
    names: list[bytes] = []
    off = 0
    while off + 30 <= len(h) and h[off:off + 4] == b"PK\x03\x04":
        flags = struct.unpack_from("<H", h, off + 6)[0]
        comp_size = struct.unpack_from("<I", h, off + 18)[0]
        name_len = struct.unpack_from("<H", h, off + 26)[0]
        extra_len = struct.unpack_from("<H", h, off + 28)[0]
        name = h[off + 30: off + 30 + name_len]
        if not name:
            break
        names.append(name)
        if flags & 0x08 or comp_size == 0xFFFFFFFF:
            break
        off = off + 30 + name_len + extra_len + comp_size
    walked = len(names) >= 3

    def has(prefix: bytes) -> bool:
        if any(n.startswith(prefix) for n in names):
            return True
        return (not walked) and prefix in h

    if has(b"word/"):
        return Sniff("ooxml", DOCUMENT_TYPES["docx"][1], "docx")
    if has(b"xl/"):
        return Sniff("ooxml", DOCUMENT_TYPES["xlsx"][1], "xlsx")
    if has(b"ppt/"):
        return Sniff("ooxml", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx")
    if has(b"[Content_Types].xml"):
        return Sniff("ooxml", "application/vnd.openxmlformats-officedocument", "ooxml")
    return Sniff("zip", "application/zip", "zip")


def _sniff_media(h: bytes) -> Optional[Sniff]:
    if h[:3] == b"ID3" or (len(h) > 1 and h[0] == 0xFF and h[1] in (0xFB, 0xF3, 0xF2)):
        return Sniff("media", "audio/mpeg", "mp3")
    if h[:4] == b"RIFF" and h[8:12] == b"WAVE":
        return Sniff("media", "audio/wav", "wav")
    if h[:4] == b"RIFF" and h[8:12] == b"AVI ":
        return Sniff("media", "video/x-msvideo", "avi")
    if h[:4] == b"OggS":
        return Sniff("media", "audio/ogg", "ogg")
    if h[:4] == b"fLaC":
        return Sniff("media", "audio/flac", "flac")
    if h[:6] == b"#!AMR\n" or h[:9] == b"#!AMR-WB\n":
        return Sniff("media", "audio/amr", "amr")
    if len(h) > 1 and h[0] == 0xFF and h[1] in (0xF1, 0xF9):
        return Sniff("media", "audio/aac", "aac")
    if h[4:8] == b"ftyp":
        brand = h[8:12]
        if brand in (b"M4A ", b"M4B "):
            return Sniff("media", "audio/mp4", "m4a")
        if brand == b"qt  ":
            return Sniff("media", "video/quicktime", "mov")
        if brand[:3] in (b"3gp", b"3g2"):
            return Sniff("media", "video/3gpp", "3gp")
        return Sniff("media", "video/mp4", "mp4")
    if h[:4] == b"\x1aE\xdf\xa3":
        if b"webm" in h[:64]:
            return Sniff("media", "video/webm", "webm")
        return Sniff("media", "video/x-matroska", "mkv")
    return None


_TEXT_CTRL = set(range(0, 9)) | {11, 12} | set(range(14, 32)) | {127}


def _sniff_text(h: bytes) -> Optional[Sniff]:
    """UTF-8 (or BOM-marked UTF-16) with no NULs and almost no control
    characters. A multibyte character cut by the window edge is tolerated."""
    if not h:
        return None
    if h[:2] in (b"\xff\xfe", b"\xfe\xff"):
        enc = "utf-16-le" if h[:2] == b"\xff\xfe" else "utf-16-be"
        s = h[2:].decode(enc, errors="ignore")
    else:
        if b"\x00" in h:
            return None
        try:
            s = h.decode("utf-8")
        except UnicodeDecodeError as e:
            if e.start < len(h) - 4:
                return None
            s = h[:e.start].decode("utf-8", errors="ignore")
    if not s.strip():
        return None
    bad = sum(1 for ch in s if ord(ch) in _TEXT_CTRL or unicodedata.category(ch) in ("Cs", "Co"))
    if bad / max(1, len(s)) > 0.02:
        return None
    return Sniff("text", "text/plain", "text")


# ------------------------------------------------------------------ matching

def matches(head: bytes, ext: str) -> tuple[bool, str]:
    """(agrees, sniffed_mime): do the first bytes agree with the extension?

    Images must match exactly (a PNG under ``.jpg`` is a mismatch), OOXML must
    be the right package, any audio or video container satisfies any media
    extension (the row becomes ``unsupported`` either way), and text
    extensions accept any clean text regardless of what it starts with.
    """
    spec = SUPPORTED_TYPES.get(ext)
    if spec is None:
        return False, sniff(head).mime
    family = spec[0]
    if family == "text":
        return (True, spec[1]) if _sniff_text(head) else (False, sniff(head).mime)
    s = sniff(head)
    if family == "pdf":
        return s.family == "pdf", s.mime
    if family == "image":
        return s.family == "image" and s.detail == ext, s.mime
    if family == "ooxml":
        return s.family == "ooxml" and s.detail in (ext, "ooxml"), s.mime
    if family == "media":
        return s.family == "media", s.mime
    return False, s.mime


# ----------------------------------------------------------------- filenames

_FORBIDDEN = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]+')
_EXT_RE = re.compile(r"^[a-z0-9]{1,5}$")


def sanitise_filename(name: str) -> tuple[str, str]:
    """(display name, normalised extension).

    Keeps Unicode letters (Hindi, Marathi and Gujarati filenames are common),
    drops path components, control and reserved characters, caps the length
    and never returns an empty name. The extension is lower-cased and aliased
    (``jpeg`` -> ``jpg``); the display name keeps the lawyer's spelling.
    """
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = unicodedata.normalize("NFC", base)
    base = _FORBIDDEN.sub("", base)
    base = " ".join(base.split()).strip(" .")
    stem, dot, raw_ext = base.rpartition(".")
    ext = raw_ext.lower()
    if not dot or not stem or not _EXT_RE.match(ext):
        stem, raw_ext, ext = base, "", ""
    ext = _EXT_ALIASES.get(ext, ext)
    room = FILENAME_MAX - (len(raw_ext) + 1 if raw_ext else 0)
    stem = stem[:room].strip(" .") or "document"
    display = f"{stem}.{raw_ext}" if raw_ext else stem
    return display, ext
