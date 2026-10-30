"""Extraction for the case worker: the part that runs in a child process.

``extract_document(path, ext, doc_id)`` turns one local file into sidecar
lines through the three public ``core.file_processor`` wrappers (decision
12). ``run_extraction`` runs it in a **spawned child process** per job and
kills the child on timeout, on a lost lock or on shutdown; results travel
through a JSON file in the job's scratch directory, never through a pipe,
so a killed child leaves nothing half-read. ``in_process=True`` runs the
same code inline (tests that monkeypatch OCR; the default stays the child).

Why a child process and not a thread: PyMuPDF, python-docx and a stuck
Gemini call cannot be interrupted from Python; a thread that hangs holds
the worker forever, and restarting the whole worker would abort the other
jobs in flight. ``spawn`` (not ``fork``) so the child inherits neither the
psycopg pool nor boto's sockets.

Logging rule: the child is given the document id as its log name; the file
it opens is ``case_tmp/{job_id}/{doc_id}.{ext}``. No filename, no text.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from typing import Optional

from core.case_sniff import DOCUMENT_TYPES, IMAGE_TYPES
from core.logger import get_logger, short_err

log = get_logger("CaseExtract")

SIDECAR_SCHEMA = 1
SECTION_MAX_CHARS = 4000
HEADING_MAX_CHARS = 80
DEFAULT_ENTRY = "core.case_extract:extract_document"

# Error codes (design §6). Permanent errors fail the document on the first
# attempt; transient ones are retried with backoff up to ``max_attempts``.
PERMANENT_CODES = frozenset({
    "mime_mismatch", "size_mismatch", "sse_missing", "object_missing", "case_cap_exceeded",
    "encrypted_pdf", "empty_pdf", "corrupt_file", "unsupported_type",
})
TRANSIENT_CODES = frozenset({"extract_error", "ocr_error", "timeout", "storage_error", "internal", "worker_lost"})

_CORRUPT_TYPES = ("FileDataError", "EmptyFileError", "BadZipFile", "PackageNotFoundError",
                  "InvalidFileException", "XMLSyntaxError")
_CORRUPT_HINTS = ("cannot open", "not a pdf", "format error", "corrupt", "not a zip file", "bad magic")


@dataclass
class Line:
    """One sidecar record. ``unit`` is ``page`` (PDF, image), ``section``
    (DOCX, TXT, MD) or ``sheet`` (XLSX, CSV). ``page`` is 1-based for pages
    and ``None`` otherwise; ``index`` is the 0-based ordinal."""
    unit: str
    page: Optional[int]
    index: int
    text: str
    source: str
    confidence: Optional[float]
    chars: int
    heading: Optional[str] = None


class ExtractError(Exception):
    def __init__(self, code: str, detail: str = "", permanent: Optional[bool] = None):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail[:200]
        self.permanent = (code in PERMANENT_CODES) if permanent is None else bool(permanent)


class ExtractTimeout(Exception):
    """The child exceeded the budget and was killed."""


class ExtractAborted(Exception):
    """The child was killed because the caller's abort event was set."""


# ------------------------------------------------------------ classification

def classify_exception(exc: BaseException) -> tuple[str, bool]:
    """Map any exception from extraction to ``(error_code, permanent)``."""
    if isinstance(exc, ExtractError):
        return exc.code, exc.permanent
    name = type(exc).__name__
    msg = str(exc).lower()
    if name == "OCRUnavailable":
        return "ocr_error", False
    if isinstance(exc, ValueError):
        if "encrypted" in msg or "password" in msg:
            return "encrypted_pdf", True
        if "no pages" in msg:
            return "empty_pdf", True
    if name in _CORRUPT_TYPES or any(h in msg for h in _CORRUPT_HINTS):
        return "corrupt_file", True
    if isinstance(exc, TimeoutError):
        return "timeout", False
    return "extract_error", False


# ---------------------------------------------------------------- splitting

def _looks_like_heading(paragraph: str) -> bool:
    p = paragraph.strip()
    return ("\n" not in p and 0 < len(p) <= HEADING_MAX_CHARS
            and not p.endswith((".", ",", ";")) and any(ch.isalnum() for ch in p))


def _hard_wrap(paragraph: str, max_chars: int) -> list[str]:
    out, rest = [], paragraph
    while len(rest) > max_chars:
        cut = rest.rfind("\n", 0, max_chars)
        if cut < max_chars // 2:
            cut = rest.rfind(" ", 0, max_chars)
        if cut < max_chars // 2:
            cut = max_chars
        out.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        out.append(rest)
    return out


def split_sections(text: str, max_chars: int = SECTION_MAX_CHARS) -> list[tuple[Optional[str], str]]:
    """Paragraph-preserving blocks of at most ``max_chars``. A block's
    heading is its first paragraph when that looks like one (a single short
    line without a sentence end) and the block has more than that paragraph.
    A paragraph longer than ``max_chars`` is wrapped at a line or space."""
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    sep = r"\n\s*\n" if re.search(r"\n\s*\n", norm) else r"\n"     # one paragraph per line is also accepted
    paras = [p.strip() for p in re.split(sep, norm) if p.strip()]
    blocks: list[list[str]] = []
    cur: list[str] = []
    cur_len = 0
    for p in paras:
        if len(p) > max_chars:
            if cur:
                blocks.append(cur)
                cur, cur_len = [], 0
            blocks.extend([piece] for piece in _hard_wrap(p, max_chars))
            continue
        if cur and cur_len + len(p) + 2 > max_chars:
            blocks.append(cur)
            cur, cur_len = [], 0
        cur.append(p)
        cur_len += len(p) + 2
    if cur:
        blocks.append(cur)
    out: list[tuple[Optional[str], str]] = []
    for b in blocks:
        heading = b[0] if len(b) > 1 and _looks_like_heading(b[0]) else None
        out.append((heading, "\n\n".join(b)))
    return out


def split_sheets(text: str) -> list[tuple[Optional[str], str]]:
    """``_extract_xlsx_text`` output -> one (sheet name, table) per worksheet."""
    parts = re.split(r"(?m)^### Sheet: ", text)
    out: list[tuple[Optional[str], str]] = []
    for part in parts:
        if not part.strip():
            continue
        name, _, body = part.partition("\n")
        out.append((name.strip() or None, body.strip()))
    return out


# --------------------------------------------------------------- extraction

def extract_document(path: str, ext: str, doc_id: str) -> dict:
    """One local file -> ``{"lines": [...], "extractor", "page_count",
    "ocr_pages", "chars_total"}``. Raises; ``classify_exception`` names it."""
    from core import file_processor as fp   # light import; LLM clients load lazily inside

    ext = (ext or "").lower()
    lines: list[Line] = []
    page_count: Optional[int] = None
    ocr_pages = 0
    source = ext
    t0 = time.monotonic()

    if ext == "pdf":
        pages, extractor = fp.extract_pdf_pages(path, log_name=doc_id)
        page_count = len(pages)
        for i, p in enumerate(pages):
            lines.append(Line("page", p.page, i, p.text, p.source, p.confidence, len(p.text)))
        ocr_pages = sum(1 for p in pages if p.source == "ocr")
    elif ext in IMAGE_TYPES:
        try:
            p = fp.extract_image_page(path, log_name=doc_id)
        except Exception as e:                       # the call failed, not the image
            raise ExtractError("ocr_error", short_err(e), permanent=False) from e
        page_count, ocr_pages, extractor = 1, 1, "image_ocr"
        lines.append(Line("page", 1, 0, p.text, "ocr", p.confidence, len(p.text)))
    elif ext == "xlsx":
        text = fp.extract_office_text(path, ext, log_name=doc_id)
        extractor = source = "xlsx"
        for i, (heading, body) in enumerate(split_sheets(text)):
            lines.append(Line("sheet", None, i, body, "xlsx", None, len(body), heading))
    elif ext == "csv":
        text = fp.extract_office_text(path, ext, log_name=doc_id).strip()
        extractor = source = "csv"
        lines.append(Line("sheet", None, 0, text, "csv", None, len(text)))
    elif ext in ("docx", "txt", "md"):
        text = fp.extract_office_text(path, ext, log_name=doc_id)
        extractor = ext
        source = "docx" if ext == "docx" else "text"
        for i, (heading, body) in enumerate(split_sections(text)):
            lines.append(Line("section", None, i, body, source, None, len(body), heading))
    elif ext in DOCUMENT_TYPES:
        raise ExtractError("extract_error", f"no extractor for {ext}", permanent=False)
    else:
        raise ExtractError("unsupported_type", f"ext={ext}", permanent=True)

    if not lines:                                    # an empty file still gets a complete sidecar
        unit = "sheet" if ext in ("xlsx", "csv") else "section"
        lines.append(Line(unit, None, 0, "", source, None, 0))
    chars_total = sum(ln.chars for ln in lines)
    log.info("Extracted", doc=doc_id[:8], ext=ext, extractor=extractor, lines=len(lines),
             pages=page_count, ocr_pages=ocr_pages, chars=chars_total, ms=int((time.monotonic() - t0) * 1000))
    return {"lines": [asdict(ln) for ln in lines], "extractor": extractor, "page_count": page_count,
            "ocr_pages": ocr_pages, "chars_total": chars_total}


# ---------------------------------------------------------- child process

def _resolve(entry: str):
    mod, _, fn = entry.partition(":")
    return getattr(importlib.import_module(mod), fn)


def _child_entry(entry: str, path: str, ext: str, doc_id: str, out_path: str) -> None:
    """Spawn target. Everything becomes a result file: ``{"ok": true, ...}``
    or ``{"ok": false, "code", "permanent", "detail"}``."""
    try:
        result = {"ok": True, **_resolve(entry)(path, ext, doc_id)}
    except BaseException as e:  # noqa: BLE001 - the parent must learn about every failure
        code, permanent = classify_exception(e)
        result = {"ok": False, "code": code, "permanent": permanent, "detail": short_err(e)}
        log.warning("Extraction failed", doc=doc_id[:8], code=code, permanent=permanent, error=short_err(e))
    tmp = out_path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    os.replace(tmp, out_path)


def _kill(proc) -> None:
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.join(5)
    except Exception:
        pass


def run_extraction(path: str, ext: str, doc_id: str, out_path: str, *, timeout_s: float,
                   abort=None, in_process: bool = False, entry: str = DEFAULT_ENTRY,
                   poll_s: float = 0.25) -> dict:
    """Run ``entry`` on the file and return the result dict (ok or error form).

    Child mode (default): a spawned process, polled every ``poll_s``; killed
    with ``Process.kill()`` when ``timeout_s`` passes (``ExtractTimeout``) or
    when ``abort`` is set (``ExtractAborted``: lock lost or shutdown). A child
    that dies without writing a result is reported as a transient
    ``extract_error``.
    """
    if in_process:
        _child_entry(entry, path, ext, doc_id, out_path)
    else:
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        proc = ctx.Process(target=_child_entry, args=(entry, path, ext, doc_id, out_path),
                           name=f"case-extract-{doc_id[:8]}", daemon=True)
        proc.start()
        t0 = time.monotonic()
        while True:
            proc.join(poll_s)
            if not proc.is_alive():
                break
            if abort is not None and abort.is_set():
                _kill(proc)
                log.warning("Extraction aborted", doc=doc_id[:8], after_s=round(time.monotonic() - t0, 1))
                raise ExtractAborted()
            if time.monotonic() - t0 > timeout_s:
                _kill(proc)
                log.warning("Extraction timed out", doc=doc_id[:8], timeout_s=timeout_s)
                raise ExtractTimeout(f"extraction exceeded {timeout_s}s")
        if proc.exitcode != 0 and not os.path.exists(out_path):
            return {"ok": False, "code": "extract_error", "permanent": False,
                    "detail": f"child exited with code {proc.exitcode}"}
    if not os.path.exists(out_path):
        return {"ok": False, "code": "extract_error", "permanent": False, "detail": "no result file"}
    with open(out_path, encoding="utf-8") as f:
        return json.load(f)
