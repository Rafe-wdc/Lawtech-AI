"""Unified file processing for inline chat attachments.

ChatGPT-style file persistence:
- All files saved permanently to ./uploads/{thread_id}/
- Gemini-supported types (PDF, images, TXT, CSV, MD) uploaded to Gemini Files API
  → persistent URI replaces base64 (48h TTL, auto re-upload from local if expired)
- DOCX / XLSX → text extracted inline (not supported by Gemini Files API)
- Large PDFs (>20 pages) → also stored in ChromaDB for targeted retrieval

Limits: 30 files/request, 30 files/thread, 1 GB/file, 1 GB/thread total.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from core.logger import get_logger, log_time
from core.settings import (
    CHROMA_STORE_ROOT,
    UPLOADS_ROOT,
    MAX_FILES_PER_REQUEST,
    MAX_FILES_PER_THREAD,
    MAX_FILE_SIZE_MB,
    MAX_THREAD_STORAGE_MB,
)
from core.gemini_files import (
    get_mime_type,
    is_gemini_supported,
    upload_to_gemini,
    GEMINI_SUPPORTED_MIMES,
)

log = get_logger("FileProcessor")

# --- Constants ---

MAX_INLINE_TEXT_CHARS = 100_000
MAX_INLINE_PDF_PAGES = 20
VISION_BATCH_SIZE = 10          # pages per Gemini Vision call (was 5)
VISION_DPI = 200                # render DPI for scanned PDFs (was 120)
VISION_MAX_CONCURRENT = 4       # max parallel OCR batch calls
OCR_CACHE_DIR = os.path.join(CHROMA_STORE_ROOT, ".ocr_cache")

ALLOWED_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".webp",
    ".docx", ".txt", ".md", ".csv", ".xlsx",
}


# --- Data Structures ---

@dataclass
class ProcessedFile:
    """Result of processing a single uploaded file."""
    original_name: str
    file_type: str      # "pdf", "image", "docx", "txt", "csv", "xlsx"
    mime_type: str
    size_bytes: int
    file_id: str = ""           # stable UUID per file in this thread
    local_path: str = ""        # ./uploads/{thread_id}/{file_id}_{filename}
    # Content routing (one or more may be set)
    gemini_uri: str = ""        # Gemini Files API URI
    gemini_name: str = ""       # Gemini handle e.g. "files/abc123"
    gemini_expiry: str = ""     # ISO 8601 expiry
    extracted_text: str = ""    # DOCX / XLSX / fallback text
    chromadb_collection: str = ""   # large PDFs stored in ChromaDB
    page_count: int = 0
    gemini_supported: bool = False
    error: str | None = None


@dataclass
class FileContext:
    """Aggregated result of processing all uploaded files for one turn."""
    files: list[ProcessedFile] = field(default_factory=list)
    inline_text: str = ""
    gemini_file_parts: list[dict] = field(default_factory=list)
    # [{file_data: {file_uri, mime_type}, name}] — passed to Gemini content
    chromadb_collections: list[str] = field(default_factory=list)
    summary: str = ""
    file_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize for LangGraph state (JSON-serializable)."""
        return {
            "inline_text": self.inline_text,
            "gemini_file_parts": self.gemini_file_parts,
            "chromadb_collections": self.chromadb_collections,
            "summary": self.summary,
            "file_names": self.file_names,
        }


# --- Validation ---

def validate_upload(filename: str, size_bytes: int) -> str | None:
    """Return error message if file is invalid, else None."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
    if ext == ".doc":
        return "Old .doc format is not supported. Please convert to .docx"
    size_mb = size_bytes / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        return f"File too large ({size_mb:.1f} MB). Max: {MAX_FILE_SIZE_MB} MB"
    return None


# --- PDF helpers ---

# --- Garbled text-layer detector (Latin-script PDFs) ---
#
# PyMuPDF returns gibberish when a PDF font has Identity-H encoding without
# a ToUnicode CMap, when the embedded text was OCR'd by a low-quality engine,
# or when the page contains vector-outlined glyphs. Symptoms include:
#   - Many tokens with no vowels ("PHHC vvm rrr")
#   - 5+ consonants in a row (real English: <2%)
#   - Repeated chars like "iiiii" or "1111"
#   - Mostly numeric or single-char fragments
#
# Without this check, the bad text pollutes the Document agent's grounding
# anchor and the Gemini multimodal call hallucinates specific names/numbers
# (root cause of the "IIT Roorkee" misread on the Chandigarh DPR matter where
# the actual document says "IIT Ropar").
#
# Designed to be:
#   - Latin-script specific (defers to other checks for Devanagari/Tamil/etc.)
#   - Tolerant of legal acronyms (PHHC, NGT, IIT, SWM, DPR, BNS, etc.)
#   - Tolerant of imperfect-but-readable OCR ("apphcation", "Honb1e")
#   - Strict on actual gibberish (broken CMaps, page-as-noise)
#
# References:
#   - PyMuPDF maintainer's official advice: OCR fallback for broken CMaps
#     (PyMuPDF Discussion #3801)
#   - firecrawl/pdf-inspector (Rust): per-PDF font-structural detection — we
#     achieve similar discrimination with cheaper text-statistics signals

_GARBLE_VOWELS = frozenset("aeiouyAEIOUY")
_GARBLE_CONS_RUN_RE = re.compile(r"[^aeiouyAEIOUY\s\d\W_]{5,}")
_GARBLE_REPEAT_RE = re.compile(r"(.)\1{3,}")
_GARBLE_TOKEN_RE = re.compile(r"[A-Za-z]{3,}")

# --- Script-aware text-quality detection (all scripts, not just Latin) ---
#
# Indian-language PDFs (Kannada, Hindi, Tamil, Telugu, Malayalam, Bengali,
# Marathi, Gujarati, Punjabi, Odia, ...) routinely ship with broken font
# encodings — an Identity-H CMap with no ToUnicode table, or a Devanagari/
# Kannada glyph set mislabelled as WinAnsiEncoding — so PyMuPDF returns either
# U+FFFD replacement chars or a deterministic-but-wrong Latin/Latin-Extended
# soup ("ªÉÉ´ÉlÉ" for real Kannada/Devanagari). See PyMuPDF Discussions/Issues
# #3801, #3799, #4805, #4701 — the maintainer's advice is "fall back to OCR".
#
# The Latin-script garble heuristics above can't see this (the bad output isn't
# Latin words), so we add a *script-coherence* check: real text is dominated by
# one or two coherent scripts with very few U+FFFD and very little Latin-Extended
# "dump-zone" noise; broken-CMap output is the opposite.

# Inclusive Unicode-block ranges for the scripts we care about. Order matters
# only for the first-match lookup in _script_of(); ranges don't overlap.
_SCRIPT_BLOCKS: tuple[tuple[int, int, str], ...] = (
    (0x0900, 0x097F, "devanagari"),   # Hindi, Marathi, Sanskrit, Nepali, Konkani
    (0x0980, 0x09FF, "bengali"),      # Bengali, Assamese
    (0x0A00, 0x0A7F, "gurmukhi"),     # Punjabi
    (0x0A80, 0x0AFF, "gujarati"),
    (0x0B00, 0x0B7F, "oriya"),        # Odia
    (0x0B80, 0x0BFF, "tamil"),
    (0x0C00, 0x0C7F, "telugu"),
    (0x0C80, 0x0CFF, "kannada"),
    (0x0D00, 0x0D7F, "malayalam"),
    (0x0D80, 0x0DFF, "sinhala"),
    (0x0E00, 0x0E7F, "thai"),
    (0x0E80, 0x0EFF, "lao"),
    (0x1000, 0x109F, "myanmar"),
    (0x0F00, 0x0FFF, "tibetan"),
    (0x3040, 0x30FF, "kana"),         # Hiragana + Katakana
    (0x3400, 0x4DBF, "cjk"),          # CJK Extension A
    (0x4E00, 0x9FFF, "cjk"),          # CJK Unified Ideographs
    (0xAC00, 0xD7AF, "hangul"),
    (0x0600, 0x06FF, "arabic"),
    (0x0750, 0x077F, "arabic"),       # Arabic Supplement
    (0x0590, 0x05FF, "hebrew"),
    (0x0400, 0x04FF, "cyrillic"),
    (0x0370, 0x03FF, "greek"),
)

# Latin-1 Supplement + Latin Extended-A/B + Latin Extended Additional. The
# *letters* in this zone are accented Latin in legitimate text — but it is also
# exactly where broken Identity-H CMaps for Indic fonts dump their gibberish, so
# we keep it as its own bucket rather than lumping it with plain ASCII Latin.
_LATIN_EXT_RANGES: tuple[tuple[int, int], ...] = ((0x00A0, 0x024F), (0x1E00, 0x1EFF))


def _script_of(ch: str) -> str | None:
    """Return a coarse script name for a *letter* character, or None for
    digits / punctuation / symbols / whitespace / control chars.

    Buckets: "latin" (ASCII letters), "latin_ext" (accented + Latin Extended —
    also the broken-CMap dump zone), a named non-Latin script, or
    "other_letter" (a letter in some script we don't enumerate).
    """
    o = ord(ch)
    if o < 0x80:
        return "latin" if ch.isalpha() else None
    for lo, hi, name in _SCRIPT_BLOCKS:
        if lo <= o <= hi:
            return name
    for lo, hi in _LATIN_EXT_RANGES:
        if lo <= o <= hi:
            return "latin_ext" if ch.isalpha() else None
    # Everything else: only count it if Python considers it a letter (covers
    # less-common scripts we don't enumerate); otherwise ignore.
    return "other_letter" if ch.isalpha() else None


def _script_profile(text: str, sample_chars: int = 20_000) -> dict:
    """Profile the script composition of a text sample.

    Returns:
      {
        "letters": int,                 # total letter chars considered
        "by_script": {script: count},   # includes "latin", "latin_ext", named scripts
        "replacement": int,             # count of U+FFFD
        "replacement_ratio": float,     # U+FFFD / (letters + U+FFFD)
        "latin_ext_ratio": float,       # latin_ext / letters
        "dominant": str | None,         # script with the most letters (excl. latin_ext)
        "dominant_ratio": float,        # dominant count / letters
        "nonlatin_letters": int,        # letters in named non-Latin scripts
      }
    """
    sample = text[:sample_chars] if text else ""
    by_script: dict[str, int] = {}
    replacement = 0
    for ch in sample:
        if ch == "�":
            replacement += 1
            continue
        s = _script_of(ch)
        if s is not None:
            by_script[s] = by_script.get(s, 0) + 1

    letters = sum(by_script.values())
    latin_ext = by_script.get("latin_ext", 0)
    # "Real" scripts = everything except the latin_ext dump-zone bucket.
    real = {k: v for k, v in by_script.items() if k != "latin_ext"}
    dominant, dominant_n = (None, 0)
    for k, v in real.items():
        if v > dominant_n:
            dominant, dominant_n = k, v
    nonlatin_letters = sum(
        v for k, v in by_script.items()
        if k not in ("latin", "latin_ext")
    )
    denom = letters + replacement
    return {
        "letters": letters,
        "by_script": by_script,
        "replacement": replacement,
        "replacement_ratio": (replacement / denom) if denom else 0.0,
        "latin_ext_ratio": (latin_ext / letters) if letters else 0.0,
        "dominant": dominant,
        "dominant_ratio": (dominant_n / letters) if letters else 0.0,
        "nonlatin_letters": nonlatin_letters,
    }


def _is_garbled_by_script(profile: dict) -> tuple[bool, str]:
    """Language-agnostic garble signal from a script profile.

    A text layer is "garbled" when it shows broken-CMap fingerprints:
      - many U+FFFD replacement chars (font has no ToUnicode for these glyphs), or
      - a large share of letters in the Latin-Extended dump-zone with no coherent
        dominant script (the deterministic-but-wrong mojibake case), or
      - no single script accounts for even ~45% of letters while there is
        meaningful dump-zone / replacement noise (incoherent soup), or
      - a real non-Latin script is present but is heavily contaminated by
        Latin-Extended dump-zone chars (partial broken-CMap — same font, some
        spans correct, some declared WinAnsi; this is the original Devanagari
        FIR-PDF failure mode, now generalised to Kannada/Tamil/Telugu/...).

    Returns (is_garbled, reason). Conservative on purpose — a clean bilingual
    English+Kannada legal PDF has low U+FFFD, low latin_ext, and a clear
    dominant script, so it is *not* flagged.
    """
    letters = profile["letters"]
    repl_ratio = profile["replacement_ratio"]
    ext_ratio = profile["latin_ext_ratio"]
    dom_ratio = profile["dominant_ratio"]
    nonlatin = profile["nonlatin_letters"]
    latin_ext_n = profile["by_script"].get("latin_ext", 0)

    if repl_ratio >= 0.05:
        return True, f"replacement_chars={repl_ratio:.2%}"
    # Partial broken-CMap on a real non-Latin script (correct spans + mojibake
    # spans of the same font). Mirrors the legacy `garbled > clean_deva*0.2`
    # Devanagari check, generalised to all non-Latin scripts. The extra absolute
    # + total-ratio gates keep a few accented Latin names (Müller, François) in a
    # doc that happens to contain a short non-Latin quote from being flagged.
    if (nonlatin >= 50 and latin_ext_n >= 20
            and latin_ext_n >= nonlatin * 0.15 and ext_ratio >= 0.05):
        return True, f"nonlatin_contaminated latin_ext={latin_ext_n}/nonlatin={nonlatin}"
    # Need enough signal to judge coherence at all.
    if letters >= 200:
        if ext_ratio >= 0.25 and dom_ratio < 0.45:
            return True, f"latin_ext_dump={ext_ratio:.2%},dominant={dom_ratio:.2%}"
        if ext_ratio >= 0.10 and (ext_ratio + repl_ratio) >= 0.20 and dom_ratio < 0.45:
            return True, f"incoherent latin_ext={ext_ratio:.2%}+repl={repl_ratio:.2%}"
    return False, "clean"


def _score_text_quality(text: str, sample_chars: int = 20_000) -> dict:
    """Score a single block of extracted text for grounding-quality.

    Returns {"verdict": str, "score": int, "metrics": dict}. Verdicts:
      - "garbled":   text-layer is unreliable, prefer Vision OCR
      - "clean":     usable as grounding anchor
      - "non_latin": dominated by a coherent non-Latin script (looks fine —
                     deferred to the language-aware downstream handling)
      - "empty"/"too_short": insufficient signal to decide
    """
    if not text:
        return {"verdict": "empty", "score": 0, "metrics": {}}

    sample = text[:sample_chars]
    raw_tokens = sample.split()
    if not raw_tokens:
        return {"verdict": "empty", "score": 0, "metrics": {}}

    # First, a language-agnostic broken-CMap check (U+FFFD soup, Latin-Extended
    # dump zone, no coherent dominant script). This catches garbled Kannada /
    # Hindi / Tamil / etc. text layers that the Latin word-statistics below
    # can't see. Runs for *every* script, including Latin.
    profile = _script_profile(sample, sample_chars=sample_chars)
    garbled_by_script, gb_reason = _is_garbled_by_script(profile)
    if garbled_by_script:
        return {
            "verdict": "garbled",
            "score": 98,
            "metrics": {"reason": gb_reason,
                        "replacement_ratio": round(profile["replacement_ratio"], 3),
                        "latin_ext_ratio": round(profile["latin_ext_ratio"], 3),
                        "dominant": profile["dominant"],
                        "dominant_ratio": round(profile["dominant_ratio"], 3)},
        }

    # If the text is dominated by a *coherent* non-Latin script, the Latin
    # word-statistics below don't apply — the text looks fine. Defer.
    non_latin = profile["nonlatin_letters"]
    latin_alpha = profile["by_script"].get("latin", 0)
    if non_latin > 0 and non_latin > latin_alpha * 0.5:
        return {
            "verdict": "non_latin",
            "score": 0,
            "metrics": {"non_latin_chars": non_latin, "latin_chars": latin_alpha,
                        "dominant": profile["dominant"],
                        "dominant_ratio": round(profile["dominant_ratio"], 3)},
        }

    alpha_tokens = _GARBLE_TOKEN_RE.findall(sample)
    long_tokens = [t for t in alpha_tokens if len(t) >= 4]
    alpha_coverage = len(alpha_tokens) / len(raw_tokens) if raw_tokens else 0.0

    # Strong-noise short-circuit — text is dominated by numbers, single chars,
    # or non-alphabetic fragments. Real legal docs have alpha_coverage > 0.5.
    if len(raw_tokens) >= 50 and alpha_coverage < 0.30:
        return {
            "verdict": "garbled",
            "score": 99,
            "metrics": {"alpha_coverage": round(alpha_coverage, 3),
                        "raw_tokens": len(raw_tokens),
                        "alpha_tokens": len(alpha_tokens),
                        "reason": "low_alpha_coverage"},
        }

    if len(long_tokens) < 50:
        return {"verdict": "too_short", "score": 0,
                "metrics": {"long_tokens": len(long_tokens),
                            "alpha_coverage": round(alpha_coverage, 3)}}

    no_vowel = sum(1 for t in long_tokens if not any(c in _GARBLE_VOWELS for c in t))
    no_vowel_ratio = no_vowel / len(long_tokens)
    cons_run = sum(1 for t in long_tokens if _GARBLE_CONS_RUN_RE.search(t))
    cons_run_ratio = cons_run / len(long_tokens)
    repeat_heavy = sum(1 for t in long_tokens if _GARBLE_REPEAT_RE.search(t))
    repeat_ratio = repeat_heavy / len(long_tokens)
    avg_len = sum(len(t) for t in long_tokens) / len(long_tokens)

    score = 0
    score += int(no_vowel_ratio > 0.20)   # real English: ~2-5%; legal acronyms allowed
    score += int(cons_run_ratio > 0.08)   # 5+ consonants in a row is rare
    score += int(cons_run_ratio > 0.15)   # severe — broken-CMap signature
    score += int(repeat_ratio > 0.08)     # "iiii", "1111" patterns
    score += int(avg_len < 3.5 or avg_len > 9.0)
    score += int(alpha_coverage < 0.50)

    return {
        "verdict": "garbled" if score >= 2 else "clean",
        "score": score,
        "metrics": {"long_tokens": len(long_tokens),
                    "alpha_coverage": round(alpha_coverage, 3),
                    "no_vowel_ratio": round(no_vowel_ratio, 3),
                    "cons_run_ratio": round(cons_run_ratio, 3),
                    "repeat_ratio": round(repeat_ratio, 3),
                    "avg_token_len": round(avg_len, 2)},
    }


def _detect_garbled_pdf(per_page_texts: list[str]) -> dict:
    """Decide whether the PDF's text-layer is unreliable enough to warrant
    falling back to Vision OCR.

    A PDF is flagged "garbled" when either:
      (a) the aggregate full-text score is >=2, OR
      (b) >=10% of pages individually score as garbled

    Returns:
      {
        "garbled": bool,
        "garbled_pages": list[int],      # 0-indexed page numbers needing re-OCR
        "garbled_pct": float,            # percentage of bad pages
        "page_count": int,
        "full_score": int,
      }
    """
    page_count = len(per_page_texts)
    if page_count == 0:
        return {"garbled": False, "garbled_pages": [], "garbled_pct": 0.0,
                "page_count": 0, "full_score": 0}

    bad_pages: list[int] = []
    for i, ptext in enumerate(per_page_texts):
        if not ptext or not ptext.strip():
            continue
        page_score = _score_text_quality(ptext, sample_chars=10_000)
        if page_score["verdict"] == "garbled":
            bad_pages.append(i)

    full_text = "\n\n".join(per_page_texts)
    full = _score_text_quality(full_text, sample_chars=20_000)
    garbled_pct = (len(bad_pages) / page_count) * 100

    is_garbled = (full["verdict"] == "garbled") or (garbled_pct >= 10.0)
    return {
        "garbled": is_garbled,
        "garbled_pages": bad_pages,
        "garbled_pct": round(garbled_pct, 1),
        "page_count": page_count,
        "full_score": full["score"],
    }


def _extract_pdf_text(file_path: str) -> tuple[str, int]:
    """Extract text from PDF with PyMuPDF. Returns (text, page_count)."""
    import fitz

    doc = fitz.open(file_path)
    page_count = doc.page_count

    if doc.is_encrypted:
        doc.close()
        raise ValueError("PDF is encrypted/password-protected")

    if page_count == 0:
        doc.close()
        raise ValueError("PDF has no pages")

    all_text = []
    for page_num, page in enumerate(doc):
        text = page.get_text("text")
        if text and text.strip():
            all_text.append(f"--- Page {page_num + 1} ---\n{text.strip()}")

    doc.close()
    return "\n\n".join(all_text), page_count


def _extract_pdf_text_per_page(file_path: str) -> tuple[list[str], int]:
    """Extract text per page (without page-marker headers) for garble scoring.

    Mirrors `_extract_pdf_text` but returns the raw per-page list so callers
    can score each page individually for selective re-OCR.
    """
    import fitz

    doc = fitz.open(file_path)
    page_count = doc.page_count

    if doc.is_encrypted:
        doc.close()
        raise ValueError("PDF is encrypted/password-protected")

    if page_count == 0:
        doc.close()
        raise ValueError("PDF has no pages")

    pages: list[str] = []
    for page in doc:
        pages.append(page.get_text("text") or "")

    doc.close()
    return pages, page_count


def _file_hash(file_path: str) -> str:
    """SHA256 hash of a file for OCR cache keying."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_ocr_cache(file_hash: str) -> str | None:
    """Return cached OCR text if available, else None."""
    cache_file = os.path.join(OCR_CACHE_DIR, f"{file_hash}.txt")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                text = f.read()
            if text.strip():
                log.info("OCR cache hit", hash=file_hash[:12])
                return text
        except Exception as e:
            log.warning("OCR cache read failed; will re-OCR",
                        hash=file_hash[:12], error=str(e)[:200])
    return None


def _save_ocr_cache(file_hash: str, text: str) -> None:
    """Persist OCR text to disk cache."""
    try:
        os.makedirs(OCR_CACHE_DIR, exist_ok=True)
        cache_file = os.path.join(OCR_CACHE_DIR, f"{file_hash}.txt")
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(text)
        log.info("OCR result cached", hash=file_hash[:12], chars=len(text))
    except Exception as e:
        log.warning("Failed to cache OCR result", error=str(e))


def _render_pdf_pages(file_path: str, page_count: int) -> list[tuple[int, str]]:
    """Render all PDF pages to base64 JPEG images. Returns [(page_start, b64), ...]

    Uses JPEG (not PNG) for ~2x smaller payloads on scanned documents.
    Uses higher DPI (200) for better OCR accuracy on legal documents.
    Groups pages into batches of VISION_BATCH_SIZE.
    """
    import base64
    import fitz

    doc = fitz.open(file_path)
    batches: list[tuple[int, list[str]]] = []
    current_batch: list[str] = []
    batch_start = 0

    for i in range(page_count):
        page = doc[i]
        pix = page.get_pixmap(dpi=VISION_DPI)
        # JPEG at quality 85 — ~2x smaller than PNG for scanned docs
        img_bytes = pix.tobytes("jpeg", jpg_quality=85)
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        current_batch.append(b64)

        if len(current_batch) == VISION_BATCH_SIZE:
            batches.append((batch_start, current_batch))
            batch_start = i + 1
            current_batch = []

    if current_batch:
        batches.append((batch_start, current_batch))

    doc.close()
    return batches


def _ocr_batch(llm, batch_b64: list[str], batch_start: int) -> str:
    """Send a batch of page images to Gemini for OCR. Returns extracted text."""
    content = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"You are an OCR engine for legal documents. Transcribe ALL visible "
                        f"text from these document images (pages {batch_start + 1} to "
                        f"{batch_start + len(batch_b64)}), exactly as it appears.\n"
                        "- The document may be in English OR an Indian language (Hindi, "
                        "Kannada, Tamil, Telugu, Malayalam, Bengali, Marathi, Gujarati, "
                        "Punjabi, Odia, Assamese, Urdu, etc.). Transcribe text in its "
                        "ORIGINAL script. Do NOT translate. Do NOT transliterate to the "
                        "Latin/Roman alphabet. If the page mixes scripts (e.g. English "
                        "headers with Kannada body text), keep each part in its own script.\n"
                        "- Preserve formatting, paragraph breaks, headings, lists, tables, "
                        "and the natural reading order.\n"
                        "- Transcribe party names, case numbers, dates, section numbers, "
                        "court names, statutory provisions, and amounts EXACTLY as written.\n"
                        "- If a word or passage is genuinely illegible, write [illegible] in "
                        "its place. Do NOT guess, fill in, or invent text.\n"
                        "Output only the transcribed text, nothing else."
                    ),
                },
                *[
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    }
                    for b64 in batch_b64
                ],
            ],
        }
    ]
    resp = llm.invoke(content)
    return f"--- Pages {batch_start + 1}-{batch_start + len(batch_b64)} ---\n{resp.content.strip()}"


def _vision_ocr_pdf(file_path: str, page_count: int) -> str:
    """Run Gemini Vision OCR on PDF pages with parallel batch processing.

    Improvements over original:
    1. Hash-based cache — skip OCR if same PDF was processed before
    2. JPEG @ quality 85 — ~2x smaller payloads vs PNG
    3. 200 DPI — better accuracy for faded/handwritten legal text (was 120)
    4. Batch size 10 — fewer API calls (was 5)
    5. Parallel batch calls — up to 4 concurrent Gemini calls (was sequential)
    6. Legal-aware OCR prompt — better extraction of names, dates, sections
    """
    from core.clients import get_gemini_flash

    # Check cache first
    pdf_hash = _file_hash(file_path)
    cached = _load_ocr_cache(pdf_hash)
    if cached:
        return cached

    try:
        with log_time(log, "PDF page rendering", pages=page_count, dpi=VISION_DPI):
            batches = _render_pdf_pages(file_path, page_count)

        llm = get_gemini_flash(temperature=0.0)
        log.info("Starting parallel OCR",
                 batches=len(batches), pages=page_count,
                 batch_size=VISION_BATCH_SIZE, max_concurrent=VISION_MAX_CONCURRENT)

        # Run batches in parallel using ThreadPoolExecutor
        with log_time(log, "Parallel Vision OCR", batches=len(batches)):
            with ThreadPoolExecutor(max_workers=VISION_MAX_CONCURRENT) as executor:
                futures = [
                    executor.submit(_ocr_batch, llm, batch_images, batch_start)
                    for batch_start, batch_images in batches
                ]
                results = []
                for future in futures:
                    try:
                        results.append(future.result(timeout=120))
                    except Exception as e:
                        log.warning("OCR batch failed", error=str(e))
                        results.append("")

        text = "\n\n".join(r for r in results if r)

        # Cache the result
        if text.strip():
            _save_ocr_cache(pdf_hash, text)

        return text

    except Exception as e:
        log.error("Vision OCR failed", error=str(e))
        return ""


def _store_in_chromadb(text: str, collection_id: str, filename: str) -> None:
    """Chunk text and store in ChromaDB collection."""
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.vectorstores import Chroma
    from core.clients import get_qa_embeddings, get_chroma_client

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        separators=["\n\n", "\n", ". ", " "],
    )
    chunks = splitter.split_text(text)

    if not chunks:
        raise ValueError("No text chunks produced")

    embeddings = get_qa_embeddings()

    metadatas = [{"source": filename, "chunk": i} for i in range(len(chunks))]
    Chroma.from_texts(
        texts=chunks,
        embedding=embeddings,
        collection_name=collection_id,
        client=get_chroma_client(),
        metadatas=metadatas,
    )
    log.info("Stored in ChromaDB", collection=collection_id, chunks=len(chunks))


async def _background_ocr_and_store(
    local_path: str, page_count: int, collection_id: str,
    filename: str, thread_id: str, file_id: str,
) -> None:
    """Background task: OCR a scanned PDF and store in ChromaDB.

    Runs after the initial response is already sent (using Gemini URI).
    Populates ChromaDB so follow-up questions can use chunk retrieval.
    Updates ocr_status in SQLite so clients can poll for completion.
    """
    from core.chat_store import chat_store

    try:
        await chat_store.update_ocr_status(thread_id, file_id, "pending")
        log.info("Background OCR started", file=filename, pages=page_count,
                 collection=collection_id)
        text = await asyncio.to_thread(_vision_ocr_pdf, local_path, page_count)
        if text.strip():
            await asyncio.to_thread(_store_in_chromadb, text, collection_id, filename)
            await chat_store.update_ocr_status(thread_id, file_id, "complete")
            log.info("Background OCR+ChromaDB complete",
                     file=filename, chars=len(text), collection=collection_id)
        else:
            await chat_store.update_ocr_status(
                thread_id, file_id, "failed", "OCR produced no text",
            )
            log.warning("Background OCR produced no text", file=filename)
    except Exception as e:
        try:
            await chat_store.update_ocr_status(
                thread_id, file_id, "failed", str(e)[:500],
            )
        except Exception as status_err:
            # Don't mask the original error — but record that status write failed.
            log.debug("update_ocr_status failed after OCR failure",
                      file=filename, status_error=str(status_err)[:200])
        log.error("Background OCR failed", file=filename, error=str(e))


# --- Text-only extractors (for DOCX / XLSX that Gemini Files API can't handle) ---

def _extract_docx_text(file_path: str) -> str:
    from docx import Document
    doc = Document(file_path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    table_texts = []
    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            rows.append(" | ".join(cells))
        if rows:
            header = rows[0]
            sep = " | ".join(["---"] * len(table.rows[0].cells))
            table_texts.append("\n".join([header, sep] + rows[1:]))
    parts = paragraphs
    if table_texts:
        parts.append("\n\n--- Tables ---\n")
        parts.extend(table_texts)
    return "\n\n".join(parts)


def _extract_csv_text(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        rows = []
        for i, row in enumerate(reader):
            if i >= 100:
                rows.append(["... (truncated)"])
                break
            rows.append(row)
    if not rows:
        return ""
    header = " | ".join(rows[0])
    sep = " | ".join(["---"] * len(rows[0]))
    body = "\n".join(" | ".join(r) for r in rows[1:])
    return f"{header}\n{sep}\n{body}"


def _extract_xlsx_text(file_path: str) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(file_path, read_only=True, data_only=True)
    sheets_text = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= 100:
                rows.append(["... (truncated)"])
                break
            rows.append([str(c) if c is not None else "" for c in row])
        if not rows:
            continue
        header = " | ".join(rows[0])
        sep = " | ".join(["---"] * len(rows[0]))
        body = "\n".join(" | ".join(r) for r in rows[1:])
        sheets_text.append(f"### Sheet: {sheet_name}\n\n{header}\n{sep}\n{body}")
    wb.close()
    return "\n\n".join(sheets_text)


# --- Main Processing Function ---

async def process_files(
    files: list[tuple[str, str, int]],
    thread_id: str,
) -> FileContext:
    """Process uploaded files with local storage + Gemini Files API persistence.

    Args:
        files: List of (temp_file_path, original_filename, size_bytes) tuples.
        thread_id: Used for upload dir, ChromaDB collection naming, and DB record.

    Returns:
        FileContext with gemini_file_parts, inline_text, and chromadb_collections.
    """
    from core.chat_store import chat_store

    ctx = FileContext()

    if len(files) > MAX_FILES_PER_REQUEST:
        log.warning("Too many files in request", count=len(files), max=MAX_FILES_PER_REQUEST)
        files = files[:MAX_FILES_PER_REQUEST]

    # Check current thread storage
    existing_count, existing_bytes = await chat_store.get_thread_storage(thread_id)
    max_thread_bytes = MAX_THREAD_STORAGE_MB * 1024 * 1024

    # Ensure uploads directory for this thread
    thread_upload_dir = os.path.join(UPLOADS_ROOT, thread_id)
    os.makedirs(thread_upload_dir, exist_ok=True)

    inline_parts: list[str] = []

    # --- Phase 1: Validate, copy, and prepare all files ---
    prepared: list[tuple[ProcessedFile, str, str, str, bool]] = []  # (pf, local_path, ext, mime, gemini_ok)

    for file_path, filename, size_bytes in files:
        ext = Path(filename).suffix.lower()
        ctx.file_names.append(filename)

        # --- Thread-level limit checks ---
        if existing_count >= MAX_FILES_PER_THREAD:
            pf = ProcessedFile(
                original_name=filename, file_type=ext.lstrip("."),
                mime_type="", size_bytes=size_bytes,
                error=f"Thread file limit ({MAX_FILES_PER_THREAD} files) reached",
            )
            ctx.files.append(pf)
            log.warning("Thread file limit reached", thread=thread_id[:12], file=filename)
            continue

        if existing_bytes + size_bytes > max_thread_bytes:
            pf = ProcessedFile(
                original_name=filename, file_type=ext.lstrip("."),
                mime_type="", size_bytes=size_bytes,
                error=f"Thread storage limit ({MAX_THREAD_STORAGE_MB} MB) reached",
            )
            ctx.files.append(pf)
            log.warning("Thread storage limit reached", thread=thread_id[:12], file=filename)
            continue

        # --- Basic validation ---
        err = validate_upload(filename, size_bytes)
        if err:
            pf = ProcessedFile(
                original_name=filename, file_type=ext.lstrip("."),
                mime_type="", size_bytes=size_bytes, error=err,
            )
            ctx.files.append(pf)
            log.warning("File rejected", file=filename, reason=err)
            continue

        mime = get_mime_type(ext)
        gemini_ok = is_gemini_supported(ext)
        file_id = uuid.uuid4().hex

        # Copy to permanent local storage
        safe_name = Path(filename).name.replace(" ", "_")
        local_filename = f"{file_id}_{safe_name}"
        local_path = os.path.join(thread_upload_dir, local_filename)
        try:
            shutil.copy2(file_path, local_path)
        except Exception as e:
            pf = ProcessedFile(
                original_name=filename, file_type=ext.lstrip("."),
                mime_type=mime, size_bytes=size_bytes,
                error=f"Failed to save file: {e}",
            )
            ctx.files.append(pf)
            continue

        pf = ProcessedFile(
            original_name=filename,
            file_type=ext.lstrip("."),
            mime_type=mime,
            size_bytes=size_bytes,
            file_id=file_id,
            local_path=local_path,
            gemini_supported=gemini_ok,
        )

        prepared.append((pf, local_path, ext, mime, gemini_ok))
        existing_count += 1
        existing_bytes += size_bytes

    # --- Phase 2: Parallel Gemini Files API uploads ---
    # Upload all Gemini-supported files concurrently instead of one-by-one
    gemini_tasks = []
    gemini_indices = []  # track which prepared[] index each task maps to
    for idx, (pf, local_path, ext, mime, gemini_ok) in enumerate(prepared):
        if gemini_ok:
            gemini_tasks.append(asyncio.to_thread(upload_to_gemini, local_path, mime, pf.original_name))
            gemini_indices.append(idx)

    if gemini_tasks:
        with log_time(log, "Parallel Gemini uploads", count=len(gemini_tasks)):
            gemini_results = await asyncio.gather(*gemini_tasks, return_exceptions=True)

        for i, result in enumerate(gemini_results):
            idx = gemini_indices[i]
            pf = prepared[idx][0]
            if isinstance(result, Exception):
                log.error("Gemini Files upload failed, falling back to extraction",
                          file=pf.original_name, error=str(result))
                pf.error = f"Gemini upload failed: {result}"
            else:
                uri, gname, expiry = result
                pf.gemini_uri = uri
                pf.gemini_name = gname
                pf.gemini_expiry = expiry
                ctx.gemini_file_parts.append({
                    "file_data": {"file_uri": uri, "mime_type": pf.mime_type},
                    "name": pf.original_name,
                })
                log.info("Gemini Files upload OK", file=pf.original_name, mime=pf.mime_type)

    # --- Phase 3: Process each file (text extraction, OCR, ChromaDB) ---
    for pf, local_path, ext, mime, gemini_ok in prepared:
        # --- PDF-specific handling ---
        if ext == ".pdf":
            try:
                # Per-page extraction so we can score each page for garbled
                # text-layer (broken Identity-H fonts, bad embedded OCR).
                # Joined text reproduces the legacy `_extract_pdf_text`
                # output format with "--- Page N ---" markers.
                per_page_texts, page_count = await asyncio.to_thread(
                    _extract_pdf_text_per_page, local_path,
                )
                text = "\n\n".join(
                    f"--- Page {i + 1} ---\n{t.strip()}"
                    for i, t in enumerate(per_page_texts) if t.strip()
                )
                pf.page_count = page_count
                is_scanned = not text.strip()

                # Script-aware garbled-text-layer check (Kannada / Hindi / Tamil
                # / Telugu / Bengali / Marathi / Gujarati / Punjabi / Odia / ...
                # PDFs with broken Identity-H CMaps, missing ToUnicode tables, or
                # glyphs mislabelled WinAnsiEncoding). PyMuPDF returns U+FFFD soup
                # or a Latin-Extended mojibake dump for these.
                if text.strip() and not is_scanned:
                    _profile = _script_profile(text)
                    _gb, _gb_reason = _is_garbled_by_script(_profile)
                    # Non-Latin text layer (Kannada/Hindi/Tamil/Telugu/...) that
                    # is garbled: the embedded glyph encoding is broken, so the
                    # Gemini native PDF read sees the same bad bytes and tends to
                    # confabulate (original Devanagari FIR-PDF failure mode). Drop
                    # the text + invalidate the Gemini URI and force Vision OCR
                    # below. Pure-Latin garble is left for the next block, which
                    # preserves the URI and only re-OCRs the grounding anchor.
                    if _gb and _profile["nonlatin_letters"] >= 50:
                        log.warning("Garbled non-Latin text layer detected - forcing "
                                    "Vision OCR, invalidating Gemini URI",
                                    file=pf.original_name, reason=_gb_reason,
                                    dominant=_profile["dominant"],
                                    nonlatin=_profile["nonlatin_letters"],
                                    latin_ext=_profile["by_script"].get("latin_ext", 0),
                                    replacement=_profile["replacement"])
                        is_scanned = True   # routes to the Vision OCR path below
                        text = ""           # discard the unreliable text layer
                        pf.gemini_uri = None
                        pf.gemini_name = None

                # Detect garbled Latin-script text (broken Identity-H CMap,
                # noisy embedded OCR, vector-outlined glyphs). Symptoms include
                # consonant pile-ups ("vvm111"), random Latin substitutions
                # ("PUHJAE Aim HARYAIIA" for "PUNJAB AND HARYANA"), or numeric
                # noise ("1121111 11"). Without this check, the bad text-layer
                # pollutes the Document agent's grounding anchor and triggers
                # small hallucinations like "IIT Roorkee" when the doc actually
                # says "IIT Ropar". Re-OCR via Gemini Vision (cached by file
                # hash) replaces the bad text. Gemini URI is preserved —
                # multimodal vision still works on the native PDF; OCR is the
                # grounding anchor for inline_text + ChromaDB.
                if text.strip() and not is_scanned:
                    garble = _detect_garbled_pdf(per_page_texts)
                    if garble["garbled"]:
                        log.warning(
                            "Garbled Latin text-layer detected — re-OCR via "
                            "Vision (Gemini URI preserved)",
                            file=pf.original_name,
                            garbled_pages=len(garble["garbled_pages"]),
                            page_count=garble["page_count"],
                            garbled_pct=garble["garbled_pct"],
                            full_score=garble["full_score"],
                        )
                        try:
                            # Cap latency on giant PDFs. 113-page Indian
                            # court PDFs at 200 DPI take ~570s in measurement;
                            # 900s gives ~50% headroom. The OCR cache
                            # (`_save_ocr_cache`) makes repeat uploads of the
                            # same file hash instant. If the timeout is hit,
                            # the in-flight thread continues running to
                            # populate the cache for the next request.
                            ocr_text = await asyncio.wait_for(
                                asyncio.to_thread(
                                    _vision_ocr_pdf, local_path, page_count,
                                ),
                                timeout=900,
                            )
                            if ocr_text.strip():
                                text = ocr_text
                                log.info(
                                    "Garble-triggered Vision OCR succeeded",
                                    file=pf.original_name, ocr_chars=len(text),
                                )
                            else:
                                log.warning(
                                    "Garble-triggered Vision OCR returned no "
                                    "text — keeping existing text-layer",
                                    file=pf.original_name,
                                )
                        except asyncio.TimeoutError:
                            log.error(
                                "Garble-triggered Vision OCR timed out — "
                                "keeping existing text-layer (degraded)",
                                file=pf.original_name, page_count=page_count,
                            )
                        except Exception as e:
                            log.error(
                                "Garble-triggered Vision OCR failed",
                                file=pf.original_name, error=str(e),
                            )

                if is_scanned and pf.gemini_uri and page_count > MAX_INLINE_PDF_PAGES:
                    # Large scanned PDF with Gemini URI — schedule background OCR
                    # for ChromaDB. First answer uses Gemini URI (fast), follow-up
                    # questions will use ChromaDB once OCR completes.
                    collection_id = f"inline_{thread_id}_{pf.file_id[:8]}"
                    pf.chromadb_collection = collection_id
                    ctx.chromadb_collections.append(collection_id)
                    log.info("Large scanned PDF: scheduling background OCR for ChromaDB",
                             file=pf.original_name, pages=page_count,
                             collection=collection_id)
                    _task = asyncio.create_task(
                        _background_ocr_and_store(
                            local_path, page_count, collection_id,
                            pf.original_name, thread_id, pf.file_id,
                        )
                    )
                    _task.add_done_callback(
                        lambda t: t.exception() and log.error(
                            "Background OCR task exception", error=str(t.exception()))
                    )

                elif is_scanned and pf.gemini_uri:
                    # Small scanned PDF with Gemini URI — skip OCR.
                    # Gemini Pro reads it natively, and it's small enough
                    # that follow-ups can also use the Gemini URI directly.
                    log.info("Small scanned PDF: Gemini URI available, skipping Vision OCR",
                             file=pf.original_name, pages=page_count)

                elif is_scanned:
                    # Scanned PDF (or a garbled non-Latin text layer we just
                    # invalidated) and no Gemini URI — must run Vision OCR.
                    # Capped so a giant document can't block the request past the
                    # gateway timeout; the in-flight thread keeps running to fill
                    # the OCR cache for the retry.
                    log.info("Scanned/garbled PDF: no Gemini URI, running Vision OCR",
                             file=pf.original_name, pages=page_count)
                    try:
                        text = await asyncio.wait_for(
                            asyncio.to_thread(_vision_ocr_pdf, local_path, page_count),
                            timeout=900,
                        )
                    except asyncio.TimeoutError:
                        log.error("Vision OCR timed out for scanned/garbled PDF",
                                  file=pf.original_name, page_count=page_count)
                        text = ""

                # Store large PDFs in ChromaDB for retrieval (even if Gemini has it,
                # ChromaDB enables targeted chunk retrieval for follow-up questions).
                # Also populate inline_text as a grounding anchor — Gemini's
                # multimodal PDF reader can silently under-sample scanned-but-
                # text-layered legal PDFs and confabulate from training memory
                # (BUG-04 large-PDF branch: J&K hallucination on a Chandigarh
                # DPR matter triggered by case-number string match). The
                # Document agent's Gemini-files path appends fc.inline_text as
                # "Additional document text" to anchor the model in real
                # content. Mirrors the small-PDF branch fix (BUG-02).
                if text.strip() and (page_count > MAX_INLINE_PDF_PAGES or len(text) > MAX_INLINE_TEXT_CHARS):
                    collection_id = f"inline_{thread_id}_{pf.file_id[:8]}"
                    try:
                        await asyncio.to_thread(_store_in_chromadb, text, collection_id, pf.original_name)
                        pf.chromadb_collection = collection_id
                        ctx.chromadb_collections.append(collection_id)
                        log.info("Large PDF stored in ChromaDB",
                                 file=pf.original_name, pages=page_count, collection=collection_id)
                    except Exception as e:
                        log.error("ChromaDB storage failed", error=str(e))
                    pf.extracted_text = text[:MAX_INLINE_TEXT_CHARS]
                    inline_parts.append(f"[File: {pf.original_name}]\n{pf.extracted_text}")
                elif text.strip():
                    # Small PDF with text — record inline text so non-multimodal
                    # downstream agents (Drafting, Scenario, Legislation) can
                    # consume the document content. Previously we ONLY populated
                    # inline_text when the Gemini upload failed, but the Drafting
                    # agent does not use Gemini Files API URIs — it needs the
                    # raw text to extract case_facts (BUG-02 dependency).
                    pf.extracted_text = text
                    inline_parts.append(f"[File: {pf.original_name}]\n{text}")

            except Exception as e:
                if not pf.gemini_uri:
                    pf.error = f"PDF processing failed: {e}"

        # --- Step 4: Text extraction for DOCX / XLSX (Gemini Files API not supported) ---
        elif ext == ".docx":
            try:
                text = await asyncio.to_thread(_extract_docx_text, local_path)
                log.debug("DOCX text-only analysis (Gemini Files API does not support .docx)",
                          file=pf.original_name, chars=len(text))
                if len(text) > MAX_INLINE_TEXT_CHARS:
                    collection_id = f"inline_{thread_id}_{pf.file_id[:8]}"
                    try:
                        await asyncio.to_thread(
                            _store_in_chromadb, text, collection_id, pf.original_name)
                        pf.chromadb_collection = collection_id
                        ctx.chromadb_collections.append(collection_id)
                        log.info("Large DOCX stored in ChromaDB",
                                 file=pf.original_name, chars=len(text),
                                 collection=collection_id)
                    except Exception as e:
                        log.error("ChromaDB storage failed for DOCX", error=str(e))
                        pf.extracted_text = text[:MAX_INLINE_TEXT_CHARS]
                        inline_parts.append(f"[File: {pf.original_name}]\n{pf.extracted_text}")
                else:
                    pf.extracted_text = text
                    inline_parts.append(f"[File: {pf.original_name}]\n{text}")
            except Exception as e:
                pf.error = f"Failed to read DOCX: {e}"

        elif ext == ".xlsx":
            try:
                text = await asyncio.to_thread(_extract_xlsx_text, local_path)
                log.debug("XLSX text-only analysis (Gemini Files API does not support .xlsx)",
                          file=pf.original_name, chars=len(text))
                if len(text) > MAX_INLINE_TEXT_CHARS:
                    collection_id = f"inline_{thread_id}_{pf.file_id[:8]}"
                    try:
                        await asyncio.to_thread(
                            _store_in_chromadb, text, collection_id, pf.original_name)
                        pf.chromadb_collection = collection_id
                        ctx.chromadb_collections.append(collection_id)
                        log.info("Large XLSX stored in ChromaDB",
                                 file=pf.original_name, chars=len(text),
                                 collection=collection_id)
                    except Exception as e:
                        log.error("ChromaDB storage failed for XLSX", error=str(e))
                        pf.extracted_text = text[:MAX_INLINE_TEXT_CHARS]
                        inline_parts.append(f"[File: {pf.original_name}]\n{pf.extracted_text}")
                else:
                    pf.extracted_text = text
                    inline_parts.append(f"[File: {pf.original_name}]\n{text}")
            except Exception as e:
                pf.error = f"Failed to read XLSX: {e}"

        # CSV / TXT / MD: also extract text as inline fallback
        # (Gemini URI handles primary access; inline text is fallback context)
        elif ext in (".csv",) and pf.gemini_uri:
            try:
                text = await asyncio.to_thread(_extract_csv_text, local_path)
                pf.extracted_text = text
            except Exception as e:
                log.warning("CSV inline text extraction failed (Gemini URI still primary)",
                            file=pf.original_name, error=str(e)[:200])

        elif ext in (".txt", ".md") and pf.gemini_uri:
            try:
                with open(local_path, "r", encoding="utf-8", errors="replace") as f:
                    pf.extracted_text = f.read(MAX_INLINE_TEXT_CHARS)
            except Exception as e:
                log.warning("txt/md inline read failed (Gemini URI still primary)",
                            file=pf.original_name, error=str(e)[:200])

        # If Gemini upload failed and no text was extracted, mark as error
        if not pf.gemini_uri and not pf.extracted_text and not pf.chromadb_collection and not pf.error:
            pf.error = "No content could be extracted from this file"

        ctx.files.append(pf)

        # --- Persist to SQLite thread_files ---
        try:
            await chat_store.save_thread_file(thread_id, pf)
        except Exception as e:
            log.error("Failed to save thread_file record", file=pf.original_name, error=str(e))

        log.info("File processed",
                 file=pf.original_name, type=pf.file_type,
                 gemini=bool(pf.gemini_uri), chromadb=bool(pf.chromadb_collection),
                 text_len=len(pf.extracted_text))

    # Build combined inline text (truncated to limit)
    combined = "\n\n---\n\n".join(inline_parts)
    if len(combined) > MAX_INLINE_TEXT_CHARS:
        combined = combined[:MAX_INLINE_TEXT_CHARS] + "\n\n[... text truncated]"
    ctx.inline_text = combined

    # Build summary
    type_counts: dict[str, int] = {}
    for pf in ctx.files:
        if not pf.error:
            type_counts[pf.file_type] = type_counts.get(pf.file_type, 0) + 1
    parts = [f"{c} {t}{'s' if c > 1 else ''}" for t, c in type_counts.items()]
    ctx.summary = f"Processed {', '.join(parts)}" if parts else "No files processed"

    log.info("File processing complete",
             total=len(files), summary=ctx.summary,
             gemini_parts=len(ctx.gemini_file_parts),
             inline_chars=len(ctx.inline_text),
             chromadb=len(ctx.chromadb_collections))

    return ctx