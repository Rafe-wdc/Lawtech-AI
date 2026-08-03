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
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

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

# Vision OCR DPI. V1 ran at 96 for years on production legal documents with
# acceptable accuracy. V2 raised this to 200 for sharper Indic-script OCR,
# but the resulting 4x increase in pixels per page is the main driver of
# OCR latency on long PDFs. We keep a high-DPI option for genuinely poor
# text-layer cases and default to V1's 96 for the common path.
# (Bug analysis 2026-06-23: V2 file-processing regression — V1 baseline.)
VISION_DPI_LOW = 96             # V1 default — fast, acceptable for most cases
VISION_DPI_HIGH = 200            # used when low-DPI OCR returns very little text
VISION_DPI = VISION_DPI_LOW      # default; _vision_ocr_pdf may escalate

# Pages per Gemini Vision call. Was 10, dropped to 1 on 2026-07-22 after
# the WhatsApp Devanagari-scan incident: multi-page batching is the primary
# trigger of Gemini's degenerate token-repetition loop on handwritten Indic
# scripts (12,335-char response was 2 real lines + `ज.` repeated ~5,000
# times). Single-page batches produce coherent output on the same file.
# Throughput is still handled by VISION_MAX_CONCURRENT parallel calls.
VISION_BATCH_SIZE = 1
VISION_MAX_CONCURRENT = 4       # max parallel OCR batch calls
OCR_CACHE_DIR = os.path.join(CHROMA_STORE_ROOT, ".ocr_cache")

# Bump when the OCR prompt or model changes so pre-existing cache entries
# (which may contain garbage produced by the old prompt / weaker model) are
# not served to users. Change to "v3" etc. on any future OCR-quality fix.
OCR_CACHE_VERSION = "v2"

# ChromaDB chunk size for evidence PDFs. Raised to 15000 in the RAG
# attachment routing plan (docs/rag_attachment_routing_plan.md, Phase A,
# 2026-06-28) — large chunks pair with Gemini Pro's 1M context window so
# the default `get_full_attachment` tool returns sensible chunk counts
# and per-collection MMR (k=5) returns ~75K chars when used.
CHROMA_CHUNK_SIZE = 15000
CHROMA_CHUNK_OVERLAP = 200

# PDF compression timeout. PikePDF lossless re-streaming is fast (<10s for
# a 30 MB PDF on commodity hardware). Ghostscript lossy can take 30-60s
# on the same input. 60s is a generous upper bound; on timeout we skip
# compression and proceed with the original file.
PDF_COMPRESS_TIMEOUT_S = 60

# Per-task timeouts for process_files sub-tasks. None of these existed before
# 2026-06-23; their absence was the root cause of the silent hangs reported on
# 29 MB / 524-page evidence bundles (V2 file-processing regression vs V1, see
# Buglist/v2_file_processing_regression_2026-06-23.md). V1's lean pipeline did
# not need timeouts because the payload was always small enough to keep every
# stage fast. V2's richer pipeline (200 DPI render, 1k-char chunks, Gemini
# Files upload) blows past V1's implicit budgets and needs explicit ceilings.
#
# On timeout: log + skip the affected file with a clear file_processing
# rejection event. Never hang the whole request.
GEMINI_UPLOAD_TIMEOUT_S = 180        # per-file Gemini Files API upload
PDF_EXTRACT_TIMEOUT_S = 120          # per-file PyMuPDF per-page extraction
DOCX_EXTRACT_TIMEOUT_S = 60          # per-file DOCX text extraction
XLSX_EXTRACT_TIMEOUT_S = 60          # per-file XLSX text extraction
CSV_EXTRACT_TIMEOUT_S = 30           # per-file CSV text extraction
CHROMA_STORE_TIMEOUT_S = 30          # per-file embedding + Chroma write
# Lowered from 180s on 2026-06-28: the chromadb HTTP server's default
# SQLAlchemy pool (~5 conns, 30s pool_timeout) exhausts under 6+
# concurrent worker writes. The inner SQLAlchemy "pool timed out"
# fires at ~30s anyway; waiting another 150s past that just makes
# the user stare at a spinner for 3 min before degraded fallback.
# The text is preserved in pf.extracted_text so the Document agent
# still has grounding even if chroma write fails — degraded but
# functional. _store_in_chromadb retries 3× with backoff to absorb
# transient pool bursts before giving up.
#
# 2026-06-29: paired with the Drafting agent fallback (agents/drafting.py
# now prefers FileContextData.extracted_texts over Chroma), this timeout
# can stay aggressive — drafting still gets full facts when Chroma fails.
CHROMA_WRITE_CONCURRENCY = max(1, int(os.getenv("CHROMA_WRITE_CONCURRENCY", "2")))
# Per-worker cap on concurrent _store_in_chromadb invocations. ChromaDB's
# default SQLAlchemy pool is 5 connections; without a client-side cap,
# asyncio.to_thread's default executor can park many writes against a
# single worker (default thread pool = min(32, cpu+4)). Each parked write
# burns a Chroma pool slot for up to pool_timeout (~30s) before raising.
# With 6 gunicorn workers × 2 concurrent writes = 12 max in-flight
# requests against a 5-slot pool — still over but bounded; combined with
# _store_in_chromadb's retry-with-backoff the success rate is high enough
# to keep the Document agent useful. Drafting itself no longer depends on
# Chroma succeeding (extracted_text fallback).
SQLITE_SAVE_TIMEOUT_S = 30           # per-file thread_files row insert

# Per-worker bounded semaphore guarding the chromadb HTTP server's
# connection pool. threading.BoundedSemaphore (not asyncio.Semaphore)
# because _store_in_chromadb runs inside asyncio.to_thread, so the
# blocking acquire keeps the worker thread parked until a slot is free.
_CHROMA_WRITE_SEMAPHORE = threading.BoundedSemaphore(CHROMA_WRITE_CONCURRENCY)

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
    """Aggregated result of processing all uploaded files for one turn.

    Phase F (2026-06-28): single text pipeline. inline_text and
    gemini_file_parts fields removed; content lives in ChromaDB.

    2026-06-29: ``to_dict()`` also surfaces ``extracted_texts`` so that
    downstream agents (notably Drafting, which wants the *entire* uploaded
    document — not semantic-search chunks) have a Chroma-independent path
    to the facts. When the ChromaDB embed step times out (the documented
    pool-exhaustion incident from 2026-06-28), ``chromadb_collections`` is
    empty but ``extracted_texts`` still carries the PDF/DOCX/XLSX/CSV text
    that PyMuPDF/python-docx/etc. already produced — so the Drafting prompt
    has factual anchors instead of falling back to placeholder boilerplate.
    """
    files: list[ProcessedFile] = field(default_factory=list)
    chromadb_collections: list[str] = field(default_factory=list)
    summary: str = ""
    file_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize for LangGraph state (JSON-serializable)."""
        extracted_texts = [
            {"name": pf.original_name, "text": pf.extracted_text}
            for pf in self.files
            if pf.extracted_text
        ]
        return {
            "chromadb_collections": self.chromadb_collections,
            "summary": self.summary,
            "file_names": self.file_names,
            "extracted_texts": extracted_texts,
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

    # Token-repetition-loop check. Gemini Vision on handwritten Indic
    # scripts occasionally enters a degenerate loop where a single 1-3-char
    # abbreviation (e.g. Devanagari `ज.`) is emitted thousands of times.
    # The Latin-script word-statistics below cannot see this; the broken-
    # CMap check below cannot see it either (the script profile looks
    # perfectly Devanagari). Real prose never has one token > 15% of
    # tokens; we flag > 30% as a clear loop signature. Runs *before* the
    # script check so a loop in ANY script is caught. See the WhatsApp
    # Scan 2026-07-20 incident.
    if len(raw_tokens) >= 50:
        from collections import Counter
        top_tok, top_n = Counter(raw_tokens).most_common(1)[0]
        top_ratio = top_n / len(raw_tokens)
        if top_ratio > 0.30:
            return {
                "verdict": "garbled",
                "score": 100,
                "metrics": {"reason": "token_loop",
                            "top_token": top_tok[:20],
                            "top_ratio": round(top_ratio, 3),
                            "total_tokens": len(raw_tokens)},
            }

    # A language-agnostic broken-CMap check (U+FFFD soup, Latin-Extended
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


def _ocr_cache_path(file_hash: str) -> str:
    """Cache filename includes OCR_CACHE_VERSION so a prompt/model fix
    invalidates every previously-cached result at once."""
    return os.path.join(OCR_CACHE_DIR, f"{file_hash}_{OCR_CACHE_VERSION}.txt")


def _load_ocr_cache(file_hash: str) -> str | None:
    """Return cached OCR text if available, else None.

    Only reads entries produced by the current OCR_CACHE_VERSION — older
    entries (potentially garbage from the pre-fix pipeline) are ignored.
    """
    cache_file = _ocr_cache_path(file_hash)
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                text = f.read()
            if text.strip():
                log.info("OCR cache hit",
                         hash=file_hash[:12], version=OCR_CACHE_VERSION)
                return text
        except Exception as e:
            log.warning("OCR cache read failed; will re-OCR",
                        hash=file_hash[:12], error=str(e)[:200])
    return None


def _save_ocr_cache(file_hash: str, text: str) -> None:
    """Persist OCR text to disk cache under the current version key."""
    try:
        os.makedirs(OCR_CACHE_DIR, exist_ok=True)
        cache_file = _ocr_cache_path(file_hash)
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(text)
        log.info("OCR result cached",
                 hash=file_hash[:12], chars=len(text), version=OCR_CACHE_VERSION)
    except Exception as e:
        log.warning("Failed to cache OCR result", error=str(e))


def _render_pdf_pages(
    file_path: str, page_count: int, dpi: int | None = None,
) -> list[tuple[int, str]]:
    """Render all PDF pages to base64 JPEG images. Returns [(page_start, b64), ...]

    Uses JPEG (not PNG) for ~2x smaller payloads on scanned documents.
    DPI defaults to VISION_DPI (96 — V1 baseline). Callers can pass DPI
    explicitly to request the high-DPI path (200) when low-DPI OCR
    produced very thin text (the adaptive-DPI second pass).
    Groups pages into batches of VISION_BATCH_SIZE.
    """
    import base64
    import fitz

    use_dpi = dpi if dpi is not None else VISION_DPI

    doc = fitz.open(file_path)
    batches: list[tuple[int, list[str]]] = []
    current_batch: list[str] = []
    batch_start = 0

    for i in range(page_count):
        page = doc[i]
        pix = page.get_pixmap(dpi=use_dpi)
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


# Shared OCR system prompt. Extracted so _ocr_batch (PDF pages) and
# _vision_ocr_image (single image) stay in sync — one prompt, one place to
# edit. The anti-repetition + handwriting-hint clauses were added on
# 2026-07-22 after the WhatsApp Devanagari-scan repetition-loop incident
# (see project memory + audit notes). Prompt-first defense per the
# `feedback_no_mechanical_patterns` rule.
_OCR_PROMPT_BODY = (
    "You are an OCR engine for legal documents. Transcribe ALL visible text "
    "from the image(s) EXACTLY as written.\n"
    "- The document may be in English or any Indian language (Hindi, Kannada, "
    "Tamil, Telugu, Malayalam, Bengali, Marathi, Gujarati, Punjabi, Odia, "
    "Assamese, Urdu, etc.) and may be printed OR handwritten. Transcribe "
    "text in its ORIGINAL script. Do NOT translate. Do NOT transliterate to "
    "the Latin/Roman alphabet. If the page mixes scripts, keep each part in "
    "its own script.\n"
    "- Preserve formatting, paragraph breaks, headings, lists, and the "
    "natural reading order. For tables, use markdown pipe-tables so columns "
    "stay readable downstream (date / UTR / amount / bank / etc.).\n"
    "- Transcribe party names, case numbers, dates, section numbers, court "
    "names, statutory provisions, amounts, and bank/account/UTR references "
    "EXACTLY as written.\n"
    "- If a word or passage is genuinely illegible (blurred, cut off, glare, "
    "unclear handwriting), write [illegible] in its place and continue with "
    "the next word. Do NOT guess, fill in, or invent text.\n"
    "- CRITICAL: Do NOT repeat the same word, character, or abbreviation "
    "more than a few times in a row. If a section of the image is unreadable "
    "or you find yourself repeating output, stop and write [illegible] once, "
    "then move on to the next legible section. Never fill space by echoing "
    "the same short token over and over.\n"
    "Output only the transcribed text, nothing else."
)


def _extract_ai_text(resp) -> str:
    """Best-effort text extraction from a LangChain AIMessage.

    Prefers explicit text-typed content blocks (guards against future
    thinking-budget changes that would put reasoning into `resp.text`).
    Falls back to `resp.text` for the common case where content is already
    a flat string.
    """
    content = getattr(resp, "content", None)
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                t = b.get("text")
                if isinstance(t, str):
                    parts.append(t)
            elif isinstance(b, str):
                parts.append(b)
        if parts:
            return "".join(parts)
    return (getattr(resp, "text", None) or "") if not isinstance(content, str) else content


def _ocr_batch(llm, batch_b64: list[str], batch_start: int) -> str:
    """Send a batch of page images to Gemini for OCR. Returns extracted text.

    Retries up to 3 times with exponential backoff (1s, 2s) on transient
    errors (rate limits, connection resets — literally what the 2026-07-22
    WhatsApp local repro hit on the low-DPI pass). A whole-batch failure
    that gets swallowed silently at the pass level is the mask that hides
    the underlying issue from users — retrying inside `_ocr_batch` keeps
    the outer contract ("returns text, empty on failure") intact but
    absorbs the common transient class before it bubbles up.
    """
    content = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"{_OCR_PROMPT_BODY}\n\n"
                        f"(These are pages {batch_start + 1} to "
                        f"{batch_start + len(batch_b64)} of a legal document.)"
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

    from core.token_tracker import record as _record_tokens

    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            resp = llm.invoke(content)
            _record_tokens(
                "FileProcessor",
                f"ocr_pdf_pages_{batch_start + 1}_{batch_start + len(batch_b64)}",
                resp,
                model="gemini-2.5-flash",
            )
            text = _extract_ai_text(resp).strip()
            return (
                f"--- Pages {batch_start + 1}-{batch_start + len(batch_b64)} ---\n{text}"
                if text else ""
            )
        except Exception as e:
            last_exc = e
            if attempt < 3:
                backoff = 2 ** (attempt - 1)  # 1s, 2s
                log.warning("OCR batch attempt failed; retrying",
                            batch_start=batch_start, attempt=attempt,
                            backoff_s=backoff, error=str(e)[:200])
                time.sleep(backoff)
    log.error("OCR batch failed after retries",
              batch_start=batch_start, error=str(last_exc)[:200])
    raise last_exc if last_exc else RuntimeError("OCR batch failed with no exception recorded")


def _vision_ocr_image(file_path: str, filename: str = "") -> str:
    """Run Gemini Vision OCR on a single image file.

    Hash-cached by file content (mirrors `_vision_ocr_pdf`) so repeat uploads
    of the same image are zero-cost. Uses the SAME shared OCR prompt as the
    PDF path (`_OCR_PROMPT_BODY`) and the SAME Gemini 2.5 Flash model
    (`get_gemini_flash_full`, not Flash Lite) so transcription quality is
    identical whether the user uploaded a PDF-of-scans or a raw JPEG.

    3-attempt retry with exponential backoff on transient errors (rate
    limits, connection resets — mirrors _ocr_batch). Cached result must
    pass the same quality check as the PDF path — degenerate-loop output
    is NOT cached, so a re-upload of a problem image gets a real re-run.

    Bug report 2026-06-19: user attached 3 JPEGs of a real apartment dispute
    + 1 scanned PDF and asked "Prepare a legal notice". Drafting produced an
    employment-dues notice with 40 [placeholders], zero references to the
    real facts. Root cause: images had no OCR branch at all, so inline_text
    was empty and the agent had no grounding. This function was added then;
    2026-07-22 upgraded model + prompt + added retry + quality gate to
    match the PDF path.
    """
    from core.clients import get_gemini_flash_full
    import base64

    img_hash = _file_hash(file_path)
    cached = _load_ocr_cache(img_hash)
    if cached:
        log.debug("Image OCR cache hit",
                  file=filename or os.path.basename(file_path))
        return cached

    try:
        with open(file_path, "rb") as f:
            img_bytes = f.read()
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        # Best-effort mime sniff from extension; data: URLs accept image/jpeg
        # for jpg/jpeg, image/png for png, etc. Default to image/jpeg
        # because Gemini Vision treats all common raster formats uniformly.
        ext = os.path.splitext(file_path)[1].lower()
        mime_for_data_url = {
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
        }.get(ext, "image/jpeg")

        llm = get_gemini_flash_full(temperature=0.0)
        content = [{
            "role": "user",
            "content": [
                {"type": "text", "text": _OCR_PROMPT_BODY},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_for_data_url};base64,{b64}"},
                },
            ],
        }]

        from core.token_tracker import record as _record_tokens

        last_exc: Exception | None = None
        text = ""
        for attempt in range(1, 4):
            try:
                with log_time(log, "Image Vision OCR",
                              file=filename or "image", attempt=attempt):
                    resp = llm.invoke(content)
                _record_tokens(
                    "FileProcessor",
                    f"ocr_image_{filename or os.path.basename(file_path)}",
                    resp,
                    model="gemini-2.5-flash",
                )
                text = _extract_ai_text(resp).strip()
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                if attempt < 3:
                    backoff = 2 ** (attempt - 1)  # 1s, 2s
                    log.warning("Image OCR attempt failed; retrying",
                                file=filename or os.path.basename(file_path),
                                attempt=attempt, backoff_s=backoff,
                                error=str(e)[:200])
                    time.sleep(backoff)
        if last_exc:
            raise last_exc

        # Only cache usable output — same discipline as the PDF path.
        # Bad output (loop, gibberish) returns to the caller but doesn't
        # poison the cache; a re-upload gets a real re-run.
        usable, reason = _ocr_result_is_usable(text)
        if text and usable:
            _save_ocr_cache(img_hash, text)
        elif text:
            log.warning("Image OCR produced unusable text — NOT caching",
                        file=filename or os.path.basename(file_path),
                        reason=reason, chars=len(text))
        return text

    except Exception as e:
        log.error("Image Vision OCR failed",
                  file=filename or os.path.basename(file_path),
                  error=str(e)[:200])
        return ""


def _ocr_pdf_at_dpi(
    file_path: str, page_count: int, dpi: int,
    emit: Optional[Callable[[dict], None]] = None,
    pass_label: str = "",
) -> tuple[str, int, int]:
    """Run Vision OCR over a PDF at a specific DPI.

    Returns (joined_text, failed_batch_count, total_batches). The caller
    (currently _vision_ocr_pdf) can then distinguish "empty scan" from
    "every batch errored" — the older API returned "" identically for
    both, which masked hard failures behind the adaptive-DPI retry.

    Uses `get_gemini_flash_full` (Gemini 2.5 Flash) — NOT Flash Lite. Lite
    was the default before 2026-07-22 and was the direct trigger of the
    Devanagari repetition-loop on handwritten Indic scripts. Flash has
    demonstrably better multilingual + handwriting OCR at ~2x the cost per
    call; single-page batching (VISION_BATCH_SIZE = 1) keeps total spend
    reasonable.

    Optional ``emit`` callback streams per-batch progress events for the
    SSE channel — useful for 100+ page scanned PDFs where OCR alone takes
    1-3 minutes and the user would otherwise see dead silence.
    """
    from core.clients import get_gemini_flash_full

    with log_time(log, "PDF page rendering", pages=page_count, dpi=dpi):
        batches = _render_pdf_pages(file_path, page_count, dpi=dpi)

    total = len(batches)
    llm = get_gemini_flash_full(temperature=0.0)
    log.info("Starting parallel OCR",
             batches=total, pages=page_count, dpi=dpi,
             batch_size=VISION_BATCH_SIZE, max_concurrent=VISION_MAX_CONCURRENT,
             model="gemini-2.5-flash")

    if emit and total:
        emit({
            "type": "file_processing",
            "stage": "ocr_render_done",
            "message": (
                f"Rendered {page_count} pages at {dpi} DPI"
                f"{(' ' + pass_label) if pass_label else ''}; "
                f"running OCR across {total} batch(es) of {VISION_BATCH_SIZE}..."
            ),
            "pages": page_count,
            "batches": total,
            "dpi": dpi,
            "pass": pass_label or "single",
        })

    failed_batches = 0
    # ThreadPoolExecutor threads do NOT inherit ContextVars by default —
    # the request-scoped token tracker set by /pyapi/chat would be invisible
    # inside `_ocr_batch`, so OCR usage would silently drop out of the
    # `token_usage.by_agent["FileProcessor"]` roll-up. Capture the current
    # context once and dispatch each submit through `ctx.run(...)` so every
    # worker thread sees the same tracker.
    import contextvars as _ctxvars
    _ocr_ctx = _ctxvars.copy_context()
    with log_time(log, "Parallel Vision OCR", batches=total):
        with ThreadPoolExecutor(max_workers=VISION_MAX_CONCURRENT) as executor:
            future_batch = {
                executor.submit(_ocr_ctx.run, _ocr_batch, llm, batch_images, batch_start):
                    (batch_start, len(batch_images))
                for batch_start, batch_images in batches
            }
            results: list[str] = []
            done = 0
            for future in future_batch:
                batch_start, batch_len = future_batch[future]
                # Timeout scales with batch size: 45s base + 40s per page at
                # single-page batching (85s cap) is comfortably above the
                # observed 200-DPI Devanagari p95 (~55s) but tight enough
                # that a wedged call fails fast.
                batch_timeout = 45 + 40 * batch_len
                try:
                    result = future.result(timeout=batch_timeout)
                    if result:
                        results.append(result)
                    else:
                        # Batch returned empty (image was blank / all illegible).
                        # Not a failure — just no text.
                        pass
                except Exception as e:
                    failed_batches += 1
                    log.warning("OCR batch failed after retries",
                                batch_start=batch_start,
                                timeout_s=batch_timeout,
                                error=str(e)[:200])
                done += 1
                if emit:
                    pages_done = min(done * VISION_BATCH_SIZE, page_count)
                    emit({
                        "type": "file_processing",
                        "stage": "ocr_progress",
                        "message": (
                            f"OCR batch {done}/{total} done "
                            f"({pages_done} of {page_count} pages)"
                            + (f" [{pass_label}]" if pass_label else "")
                            + (f", {failed_batches} failed" if failed_batches else "")
                        ),
                        "done": done,
                        "total": total,
                        "pages_done": pages_done,
                        "pages": page_count,
                        "failed_batches": failed_batches,
                        "dpi": dpi,
                        "pass": pass_label or "single",
                    })

    return "\n\n".join(results), failed_batches, total


# Minimum chars-per-page floor for the low-DPI pass. Below this the low-DPI
# OCR clearly under-read the doc (faded scan, tiny handwriting, thin
# strokes) and we escalate to high DPI regardless of quality verdict.
# Retained from the pre-fix pipeline as the *lower* of two triggers; the
# *upper* trigger is a quality-verdict check (see _vision_ocr_pdf) which
# now catches repetition-loops and CMap-style garble that length-based
# checks miss entirely.
LOW_DPI_TEXT_PER_PAGE_FLOOR = 200


def _ocr_result_is_usable(text: str) -> tuple[bool, str]:
    """Judge whether an OCR result is safe to return / cache.

    Runs _score_text_quality (which now includes token-loop detection) and
    returns (usable, reason). "usable" means the text is likely real
    content, not the degenerate-loop garbage the 2026-07-22 WhatsApp scan
    produced. Empty / too-short results are treated as unusable so the
    high-DPI retry fires — a short page can escalate for free, and a
    genuinely blank page just costs one extra call.
    """
    if not text or not text.strip():
        return False, "empty"
    score = _score_text_quality(text, sample_chars=20_000)
    verdict = score["verdict"]
    if verdict == "garbled":
        return False, f"garbled:{score['metrics'].get('reason', 'score')}"
    if verdict in ("empty", "too_short"):
        return False, verdict
    # "clean" and "non_latin" are both acceptable (non_latin = coherent
    # Indic/CJK script, deferred to language-aware downstream handling).
    return True, verdict


def _vision_ocr_pdf(
    file_path: str, page_count: int,
    emit: Optional[Callable[[dict], None]] = None,
    file_name: str = "",
) -> str:
    """Run Gemini Vision OCR on PDF pages with parallel batch processing.

    Adaptive DPI:
      1. First pass at VISION_DPI_LOW (96) — fast baseline.
      2. Escalate to VISION_DPI_HIGH (200) when EITHER
         (a) the low-DPI text is unusably thin (< page_count × 200 chars), OR
         (b) the low-DPI output fails the quality check
             (`_ocr_result_is_usable` — catches token-loops and CMap-style
             garble the length gate cannot see; added 2026-07-22).
      3. Compare the two passes by quality VERDICT first, then length —
         a clean short result beats a longer garbled one.
      4. Only cache when the result is usable (bad results never poison
         the cache; a re-upload of the same file triggers a real re-run).

    Uses Gemini 2.5 Flash (not Flash Lite) via `get_gemini_flash_full`.
    Model + batch size + prompt were all changed on 2026-07-22 after the
    WhatsApp Devanagari-scan incident where Flash Lite entered a
    repetition loop on handwritten Hindi.
    """
    pdf_hash = _file_hash(file_path)
    cached = _load_ocr_cache(pdf_hash)
    if cached:
        if emit:
            emit({
                "type": "file_processing",
                "stage": "ocr_cache_hit",
                "message": (
                    f"{file_name or os.path.basename(file_path)}: "
                    f"OCR result reused from cache ({len(cached)} chars)"
                ),
                "file": file_name or os.path.basename(file_path),
                "chars": len(cached),
            })
        return cached

    label = file_name or os.path.basename(file_path)
    try:
        if emit:
            emit({
                "type": "file_processing",
                "stage": "ocr_start",
                "message": (
                    f"{label}: starting Vision OCR on {page_count} scanned "
                    f"page(s) — this may take 1-3 minutes for large documents"
                ),
                "file": label,
                "pages": page_count,
                "dpi": VISION_DPI_LOW,
            })

        low_text, low_failed, low_total = _ocr_pdf_at_dpi(
            file_path, page_count, VISION_DPI_LOW,
            emit=emit, pass_label=f"low-DPI {VISION_DPI_LOW}",
        )
        low_usable, low_reason = _ocr_result_is_usable(low_text)

        text = low_text
        chosen_pass = "low-DPI"
        chosen_reason = low_reason

        # Escalate on either length floor OR quality failure. The length
        # floor catches faded scans; the quality check catches token-loops
        # and CMap noise that length alone cannot see.
        thin_by_length = (
            len(low_text.strip())
            < max(LOW_DPI_TEXT_PER_PAGE_FLOOR,
                  page_count * LOW_DPI_TEXT_PER_PAGE_FLOOR)
        )
        should_escalate = (
            (thin_by_length or not low_usable)
            and VISION_DPI_HIGH > VISION_DPI_LOW
        )
        if should_escalate:
            escalation_reason = (
                "thin_text" if thin_by_length
                else f"quality_check_failed:{low_reason}"
            )
            log.info("Escalating Vision OCR to high DPI",
                     file=label,
                     pages=page_count,
                     low_dpi_chars=len(low_text.strip()),
                     low_dpi=VISION_DPI_LOW,
                     high_dpi=VISION_DPI_HIGH,
                     reason=escalation_reason)
            if emit:
                emit({
                    "type": "file_processing",
                    "stage": "ocr_dpi_escalation",
                    "message": (
                        f"{label}: low-DPI OCR unusable "
                        f"({escalation_reason}, {len(low_text.strip())} chars) — "
                        f"retrying at {VISION_DPI_HIGH} DPI"
                    ),
                    "file": label,
                    "low_dpi_chars": len(low_text.strip()),
                    "low_dpi": VISION_DPI_LOW,
                    "high_dpi": VISION_DPI_HIGH,
                    "reason": escalation_reason,
                })
            high_text, high_failed, high_total = _ocr_pdf_at_dpi(
                file_path, page_count, VISION_DPI_HIGH,
                emit=emit, pass_label=f"high-DPI {VISION_DPI_HIGH}",
            )
            high_usable, high_reason = _ocr_result_is_usable(high_text)

            # Prefer the pass that produced USABLE text. If both are usable,
            # keep whichever is longer (more content extracted). If both are
            # unusable, still pick the longer non-empty one so the user at
            # least sees partial output — but we WON'T cache it (below).
            if high_usable and not low_usable:
                text, chosen_pass, chosen_reason = high_text, "high-DPI", high_reason
            elif low_usable and not high_usable:
                text, chosen_pass, chosen_reason = low_text, "low-DPI", low_reason
            elif high_usable and low_usable:
                if len(high_text.strip()) > len(low_text.strip()):
                    text, chosen_pass, chosen_reason = high_text, "high-DPI", high_reason
            else:
                # Both unusable — return the longer one, marked unusable.
                if len(high_text.strip()) > len(low_text.strip()):
                    text, chosen_pass, chosen_reason = high_text, "high-DPI", high_reason

        chosen_usable, _ = _ocr_result_is_usable(text)
        # Cache ONLY when the winning pass is usable. Prevents poisoning the
        # cache with repetition-loop garbage — a re-upload of the same file
        # will trigger a real re-OCR (potentially at high DPI) instead of
        # instantly serving the cached bad text.
        if text.strip() and chosen_usable:
            _save_ocr_cache(pdf_hash, text)
        elif text.strip():
            log.warning("OCR produced unusable text — NOT caching",
                        file=label, pass_chosen=chosen_pass,
                        reason=chosen_reason, chars=len(text))

        if emit:
            emit({
                "type": "file_processing",
                "stage": "ocr_done" if chosen_usable else "ocr_low_quality",
                "message": (
                    f"{label}: OCR complete — extracted {len(text)} chars "
                    f"from {page_count} pages "
                    f"({chosen_pass}, {chosen_reason})"
                    + (" [low quality: not cached]" if not chosen_usable else "")
                ),
                "file": label,
                "chars": len(text),
                "pages": page_count,
                "pass": chosen_pass,
                "usable": chosen_usable,
            })

        return text

    except Exception as e:
        log.error("Vision OCR failed", error=str(e))
        if emit:
            emit({
                "type": "file_processing",
                "stage": "ocr_failed",
                "message": f"{label}: OCR failed ({str(e)[:120]})",
                "file": label,
            })
        return ""


def _compress_pdf(input_path: str) -> tuple[str, dict]:
    """Compress a PDF using PikePDF lossless re-streaming + optional
    Ghostscript lossy fallback. Returns the path to USE for downstream
    processing (compressed when smaller; original otherwise) and a stats
    dict for logging.

    Ported from V1 (mainqa_test.py + utils/pdf_utils.py at
    C:\\lawtech_backup) where this ran as the first step of every PDF
    processing flow. V2 dropped it; the resulting larger payloads
    contributed to the silent-hang regression on large bundles
    (see Buglist/v2_file_processing_regression_2026-06-23.md).

    Strategy:
      1. PikePDF stream-compression (lossless). Always available.
      2. Ghostscript /ebook preset (lossy). Skipped silently when the
         `gs` binary is not on PATH — we don't ship Ghostscript as a
         hard dependency.
      3. Return whichever output is smallest. If neither compressed
         beats `original × 0.97`, return the original (no point shipping
         a basically-identical re-encoded file downstream).

    Side effects: writes one or two sibling files
    (`<base>_pike.pdf`, `<base>_gs.pdf`). The temp dir is the same as
    `input_path`'s parent; callers should expect those files until the
    request completes. Files that lose are cleaned up before return;
    the chosen file (if not the original) is the caller's responsibility.
    """
    import shutil as _shutil
    import subprocess as _subprocess

    original_size = os.path.getsize(input_path)
    stats = {
        "original": original_size,
        "pike": None,
        "gs": None,
        "chosen": "original",
        "saved_bytes": 0,
    }

    base, ext = os.path.splitext(input_path)
    pike_out: str | None = f"{base}_pike{ext}"
    gs_out: str | None = f"{base}_gs{ext}"

    # ---------- PikePDF lossless ----------
    try:
        import pikepdf
        with pikepdf.open(input_path) as pdf:
            pdf.save(pike_out, compress_streams=True)
        stats["pike"] = os.path.getsize(pike_out)
    except Exception as e:
        log.warning("PikePDF compression failed (continuing with Ghostscript / original)",
                    file=os.path.basename(input_path), error=str(e)[:200])
        if pike_out and os.path.exists(pike_out):
            try: os.remove(pike_out)
            except OSError: pass
        pike_out = None

    # ---------- Ghostscript lossy (optional) ----------
    gs_bin = _shutil.which("gs") or _shutil.which("gswin64c") or _shutil.which("gswin32c")
    if gs_bin:
        try:
            _subprocess.run(
                [
                    gs_bin,
                    "-sDEVICE=pdfwrite",
                    "-dCompatibilityLevel=1.4",
                    "-dPDFSETTINGS=/ebook",
                    "-dNOPAUSE",
                    "-dQUIET",
                    "-dBATCH",
                    f"-sOutputFile={gs_out}",
                    input_path,
                ],
                check=True,
                # Hard cap subprocess time so a bad GS install can't hang.
                # Re-using the same budget the async caller gives us.
                timeout=PDF_COMPRESS_TIMEOUT_S,
            )
            stats["gs"] = os.path.getsize(gs_out)
        except (_subprocess.SubprocessError, _subprocess.TimeoutExpired, OSError) as e:
            log.warning("Ghostscript compression failed (continuing with PikePDF / original)",
                        file=os.path.basename(input_path), error=str(e)[:200])
            if gs_out and os.path.exists(gs_out):
                try: os.remove(gs_out)
                except OSError: pass
            gs_out = None
    else:
        # GS not installed — that's fine, PikePDF alone is enough on
        # most inputs. Don't log a warning per file; this is the
        # expected baseline on most dev / container images.
        gs_out = None

    # ---------- Pick the smallest winner ----------
    threshold = int(original_size * 0.97)
    candidates: list[tuple[int, str, str]] = []
    if pike_out and stats["pike"] is not None:
        candidates.append((stats["pike"], pike_out, "pike"))
    if gs_out and stats["gs"] is not None:
        candidates.append((stats["gs"], gs_out, "gs"))

    if candidates:
        candidates.sort()
        winner_size, winner_path, winner_kind = candidates[0]
        # Only switch away from the original when we save at least 3%.
        if winner_size < threshold:
            stats["chosen"] = winner_kind
            stats["saved_bytes"] = original_size - winner_size
            # Clean up the loser(s).
            for _sz, _p, _k in candidates[1:]:
                if _p and os.path.exists(_p):
                    try: os.remove(_p)
                    except OSError: pass
            return winner_path, stats

    # No worthwhile compression — drop both compressed files.
    for _p in (pike_out, gs_out):
        if _p and os.path.exists(_p):
            try: os.remove(_p)
            except OSError: pass
    return input_path, stats


def _is_pool_timeout(err: Exception) -> bool:
    """Detect chromadb's underlying SQLAlchemy pool-exhaustion exception
    by message shape (the exception class lives inside chromadb's vendored
    SQLAlchemy and is awkward to import). Used to widen retry backoff so
    we don't burn the whole CHROMA_STORE_TIMEOUT_S budget on rapid retries
    that hit the same exhausted pool."""
    msg = str(err).lower()
    return (
        "pool timed out" in msg
        or "pool timeout" in msg
        or "queuepool limit" in msg
        or "timed out while waiting for an open connection" in msg
    )


def _store_in_chromadb(text: str, collection_id: str, filename: str) -> None:
    """Chunk text and store in ChromaDB collection.

    Retries up to 3 times with exponential backoff (1s, 2s) on transient
    chromadb errors — most commonly SQLAlchemy "pool timed out while
    waiting for an open connection" when concurrent writes exhaust
    chromadb's default 5-connection pool. Each retry first deletes any
    partial collection from a prior attempt to avoid duplicate chunks
    on eventual success.

    2026-06-29: gated by `_CHROMA_WRITE_SEMAPHORE` so each worker can only
    have CHROMA_WRITE_CONCURRENCY (default 2) writes in flight at once.
    Without this, asyncio.to_thread's default executor parks many writes
    in parallel against a single worker, each holding a chromadb pool
    slot until it errors — making pool exhaustion self-reinforcing.
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.vectorstores import Chroma
    from core.clients import get_qa_embeddings, get_chroma_client

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHROMA_CHUNK_SIZE,
        chunk_overlap=CHROMA_CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " "],
    )
    chunks = splitter.split_text(text)

    if not chunks:
        raise ValueError("No text chunks produced")

    embeddings = get_qa_embeddings()
    client = get_chroma_client()
    metadatas = [{"source": filename, "chunk": i} for i in range(len(chunks))]

    # Bounded acquire — block this thread until a slot frees, but cap the
    # wait so wedged threads don't pile up. asyncio.wait_for cancels the
    # awaitable but cannot cancel a Python thread already running here, so
    # an unbounded blocking acquire could leave threads stuck indefinitely
    # behind a hung semaphore holder. The acquire timeout matches the
    # outer CHROMA_STORE_TIMEOUT_S — if we can't even get a slot in that
    # window, the surrounding asyncio.wait_for has already given up.
    acquired = _CHROMA_WRITE_SEMAPHORE.acquire(
        blocking=True, timeout=CHROMA_STORE_TIMEOUT_S,
    )
    if not acquired:
        raise TimeoutError(
            "ChromaDB write semaphore: no slot free within "
            f"{CHROMA_STORE_TIMEOUT_S}s ({CHROMA_WRITE_CONCURRENCY} concurrent writers)"
        )
    try:
        last_exc: Exception | None = None
        for attempt in range(1, 4):  # 3 attempts: 1, 2, 3
            try:
                # On retry, drop any partial collection from a prior failed
                # attempt so we don't accumulate duplicate chunks if attempt
                # N-1 wrote some chunks before erroring.
                if attempt > 1:
                    try:
                        client.delete_collection(name=collection_id)
                    except Exception:
                        pass  # collection may not exist; that's fine
                Chroma.from_texts(
                    texts=chunks,
                    embedding=embeddings,
                    collection_name=collection_id,
                    client=client,
                    metadatas=metadatas,
                )
                log.info("Stored in ChromaDB", collection=collection_id,
                         chunks=len(chunks), attempt=attempt)
                return
            except Exception as e:
                last_exc = e
                err_msg = str(e)[:200]
                pool_timeout = _is_pool_timeout(e)
                if attempt < 3:
                    # Pool-timeout errors: the pool is saturated *right now*,
                    # so a 1s wait will likely hit the same condition. Use a
                    # longer backoff to let in-flight writes drain.
                    backoff_s = (2 ** attempt) if pool_timeout else (2 ** (attempt - 1))
                    log.warning(
                        "ChromaDB write failed; retrying",
                        collection=collection_id, attempt=attempt,
                        max_attempts=3, backoff_s=backoff_s,
                        pool_timeout=pool_timeout, error=err_msg,
                    )
                    time.sleep(backoff_s)
                else:
                    log.error(
                        "ChromaDB write failed after retries",
                        collection=collection_id, attempts=attempt,
                        pool_timeout=pool_timeout, error=err_msg,
                    )
    finally:
        _CHROMA_WRITE_SEMAPHORE.release()

    # All retries exhausted; surface the last exception to the caller
    # (which logs + emits the chroma_store_timeout event and continues
    # with pf.extracted_text as grounding).
    if last_exc is not None:
        raise last_exc


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
    """Extract text from a .docx, handling Krutidev / DV-TT legacy fonts.

    Sagar's bug #3 (2026-06-16): "Hindi mai krutidev word ki file attach
    karne ke baad usko read nahi kar paa Raha hai." Krutidev stores Latin
    codepoints that LOOK like Devanagari only when rendered with the
    Krutidev font — python-docx's `paragraph.text` returns the raw Latin,
    which is useless for retrieval and the LLM gets gibberish.

    Approach: walk run-by-run, detect runs whose font name is a known
    legacy-Hindi font (Krutidev, DV-TT, Shusha, Devlys, …), and run those
    runs through the lookup-table converter in core/krutidev. Unicode/
    English runs pass through unchanged.

    If a paragraph ends up with NO Devanagari after conversion (the
    `looks_like_unicode_devanagari` heuristic returns False) AND the
    paragraph contained legacy-font runs, log a warning so we know a
    vision-OCR fallback is needed for that document.
    """
    from docx import Document
    from core.krutidev import (
        is_legacy_hindi_font, krutidev_to_unicode, looks_like_unicode_devanagari,
    )

    doc = Document(file_path)

    def _para_text(paragraph) -> tuple[str, bool]:
        """Returns (text, had_legacy_font)."""
        chunks: list[str] = []
        had_legacy = False
        for run in paragraph.runs:
            run_text = run.text or ""
            if not run_text:
                continue
            font_name = run.font.name
            # Inherit font from style if not set explicitly on the run
            if not font_name:
                try:
                    font_name = run.style.font.name if run.style else None
                except Exception:
                    font_name = None
            if is_legacy_hindi_font(font_name):
                had_legacy = True
                chunks.append(krutidev_to_unicode(run_text))
            else:
                chunks.append(run_text)
        return "".join(chunks), had_legacy

    paragraphs: list[str] = []
    legacy_paragraphs_garbled = 0
    legacy_paragraphs_clean = 0
    for p in doc.paragraphs:
        text, had_legacy = _para_text(p)
        if not text.strip():
            continue
        if had_legacy:
            if looks_like_unicode_devanagari(text):
                legacy_paragraphs_clean += 1
            else:
                legacy_paragraphs_garbled += 1
        paragraphs.append(text)

    if legacy_paragraphs_clean or legacy_paragraphs_garbled:
        log.info("Legacy Hindi font runs detected in DOCX",
                 file=file_path,
                 paragraphs_converted_clean=legacy_paragraphs_clean,
                 paragraphs_still_garbled=legacy_paragraphs_garbled)
        if legacy_paragraphs_garbled > 0:
            log.warning("Some DOCX paragraphs failed Krutidev conversion; "
                        "vision-OCR fallback recommended",
                        file=file_path,
                        garbled_count=legacy_paragraphs_garbled)

    table_texts = []
    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells: list[str] = []
            for cell in row.cells:
                # Cells may contain their own paragraphs with runs — walk
                # them so legacy-font conversion applies inside tables too.
                cell_parts: list[str] = []
                for cp in cell.paragraphs:
                    ctext, _ = _para_text(cp)
                    if ctext.strip():
                        cell_parts.append(ctext.strip())
                cells.append("\n".join(cell_parts))
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


def _noop_writer(_evt: dict) -> None:
    """Default writer when no SSE channel is wired — silently drops events."""
    return


async def process_files(
    files: list[tuple[str, str, int]],
    thread_id: str,
    writer: Optional[Callable[[dict], None]] = None,
) -> FileContext:
    """Process uploaded files with local storage + Gemini Files API persistence.

    Args:
        files: List of (temp_file_path, original_filename, size_bytes) tuples.
        thread_id: Used for upload dir, ChromaDB collection naming, and DB record.
        writer: Optional callback that receives in-flight progress events.
            Each call is `writer({"type": "file_processing", "stage": ..., ...})`.
            The gateway wires this to the SSE stream so users see progress
            instead of dead silence during 60-180s extraction / upload / embed
            stages. Defaults to a no-op so callers that don't care (tests,
            background OCR) still work.

    Returns:
        FileContext with gemini_file_parts, inline_text, and chromadb_collections.
    """
    from core.chat_store import chat_store

    # Default to a no-op so every emit call can be unconditional.
    emit = writer or _noop_writer

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

    # --- Phase 2 (Gemini Files API uploads) removed in RAG attachment
    # routing plan Phase F (2026-06-28). All file content now flows through
    # the single text pipeline → ChromaDB. ---

    # --- Phase 3: Process each file (text extraction, OCR, ChromaDB) ---
    for pf, local_path, ext, mime, gemini_ok in prepared:
        # --- PDF-specific handling ---
        if ext == ".pdf":
            # --- Step 3.0: Optional PDF compression (V1 port) ---
            # PikePDF lossless re-streaming reduces file size for downstream
            # fitz extraction and Vision OCR rendering. Ghostscript /ebook
            # adds further lossy reduction when available. We swap
            # local_path to the smaller file when compression saves at
            # least 3% — otherwise we proceed with the original. NEVER
            # blocks: if compression times out or both engines fail, we
            # log and continue with the uncompressed file.
            emit({
                "type": "file_processing",
                "stage": "pdf_compress_start",
                "message": f"Compressing {pf.original_name}...",
                "file": pf.original_name,
            })
            try:
                new_path, comp_stats = await asyncio.wait_for(
                    asyncio.to_thread(_compress_pdf, local_path),
                    timeout=PDF_COMPRESS_TIMEOUT_S,
                )
                if new_path != local_path:
                    log.info("PDF compressed",
                             file=pf.original_name,
                             original_bytes=comp_stats["original"],
                             chosen=comp_stats["chosen"],
                             saved_bytes=comp_stats["saved_bytes"])
                    emit({
                        "type": "file_processing",
                        "stage": "pdf_compress_done",
                        "message": (
                            f"{pf.original_name}: compressed "
                            f"({comp_stats['saved_bytes'] // 1024} KB saved, "
                            f"engine={comp_stats['chosen']})"
                        ),
                        "file": pf.original_name,
                        "saved_bytes": comp_stats["saved_bytes"],
                        "engine": comp_stats["chosen"],
                    })
                    local_path = new_path  # use compressed file downstream
                else:
                    log.debug("PDF compression skipped (no worthwhile saving)",
                              file=pf.original_name, original_bytes=comp_stats["original"])
            except asyncio.TimeoutError:
                log.warning("PDF compression timed out — using original",
                            file=pf.original_name, timeout_s=PDF_COMPRESS_TIMEOUT_S)
            except Exception as e:
                log.warning("PDF compression error — using original",
                            file=pf.original_name, error=str(e)[:200])

            emit({
                "type": "file_processing",
                "stage": "pdf_extract_start",
                "message": f"Extracting text from {pf.original_name}...",
                "file": pf.original_name,
            })
            try:
                # Per-page extraction so we can score each page for garbled
                # text-layer (broken Identity-H fonts, bad embedded OCR).
                # Joined text reproduces the legacy `_extract_pdf_text`
                # output format with "--- Page N ---" markers.
                # Wrapped in wait_for: PyMuPDF can rarely hang on damaged
                # PDFs; the timeout converts that into a clean per-file fail.
                per_page_texts, page_count = await asyncio.wait_for(
                    asyncio.to_thread(_extract_pdf_text_per_page, local_path),
                    timeout=PDF_EXTRACT_TIMEOUT_S,
                )
                emit({
                    "type": "file_processing",
                    "stage": "pdf_extract_done",
                    "message": f"{pf.original_name}: {page_count} pages",
                    "file": pf.original_name,
                    "pages": page_count,
                })
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
                                    emit, pf.original_name,
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

                elif is_scanned:
                    # Scanned PDF (or a garbled non-Latin text layer we just
                    # invalidated). Run Vision OCR REGARDLESS of whether a
                    # Gemini URI is available — the Document agent can use
                    # the Gemini URI multimodally, but Drafting / Scenario /
                    # Legislation read fc.inline_text exclusively and need
                    # OCR text to ground on. Skipping OCR when Gemini URI is
                    # available (the old optimization) was the root cause of
                    # the 2026-06-19 bug where a 25-page scanned PDF + 3
                    # JPEGs produced an employment-dues template-with-
                    # placeholders notice instead of a notice grounded on
                    # the actual apartment dispute. Hash-cached so repeat
                    # uploads of the same file are zero-cost.
                    log.info("Scanned/garbled PDF: running Vision OCR for inline_text",
                             file=pf.original_name, pages=page_count,
                             has_gemini_uri=bool(pf.gemini_uri))
                    try:
                        text = await asyncio.wait_for(
                            asyncio.to_thread(
                                _vision_ocr_pdf, local_path, page_count,
                                emit, pf.original_name,
                            ),
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
                if text.strip():
                    # Phase B: every PDF with text goes to ChromaDB. Agents
                    # read content via get_full_attachment(collection_id)
                    # rather than fc.inline_text. Size threshold removed.
                    collection_id = f"inline_{thread_id}_{pf.file_id[:8]}"
                    emit({
                        "type": "file_processing",
                        "stage": "chroma_store_start",
                        "message": f"Embedding {pf.original_name} into vector store...",
                        "file": pf.original_name,
                    })
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(_store_in_chromadb, text, collection_id, pf.original_name),
                            timeout=CHROMA_STORE_TIMEOUT_S,
                        )
                        pf.chromadb_collection = collection_id
                        ctx.chromadb_collections.append(collection_id)
                        log.info("PDF stored in ChromaDB",
                                 file=pf.original_name, pages=page_count,
                                 chars=len(text), collection=collection_id)
                    except asyncio.TimeoutError:
                        log.error("ChromaDB storage timed out",
                                  file=pf.original_name, pages=page_count,
                                  timeout_s=CHROMA_STORE_TIMEOUT_S)
                        emit({
                            "type": "file_processing",
                            "stage": "chroma_store_timeout",
                            "message": f"{pf.original_name}: vector embedding timed out",
                            "file": pf.original_name,
                        })
                    except Exception as e:
                        log.error("ChromaDB storage failed", error=str(e))
                    pf.extracted_text = text  # retained for failure detection only

            except asyncio.TimeoutError:
                # PyMuPDF extract timed out (or any inner wait_for not handled
                # specifically). Record per-file failure but DO NOT abort
                # the whole upload — other files still get processed.
                log.error("PDF processing timed out", file=pf.original_name,
                          timeout_s=PDF_EXTRACT_TIMEOUT_S)
                emit({
                    "type": "file_processing",
                    "stage": "pdf_extract_timeout",
                    "message": f"{pf.original_name}: text extraction timed out",
                    "file": pf.original_name,
                })
                if not pf.gemini_uri:
                    pf.error = f"PDF text extraction timed out after {PDF_EXTRACT_TIMEOUT_S}s"
            except Exception as e:
                if not pf.gemini_uri:
                    pf.error = f"PDF processing failed: {e}"

        # --- Step 4: Text extraction for DOCX / XLSX (Gemini Files API not supported) ---
        elif ext == ".docx":
            emit({
                "type": "file_processing",
                "stage": "docx_extract_start",
                "message": f"Extracting text from {pf.original_name}...",
                "file": pf.original_name,
            })
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(_extract_docx_text, local_path),
                    timeout=DOCX_EXTRACT_TIMEOUT_S,
                )
                log.debug("DOCX text extracted",
                          file=pf.original_name, chars=len(text))
                if text.strip():
                    # Phase B: every DOCX goes to ChromaDB. Size threshold removed.
                    collection_id = f"inline_{thread_id}_{pf.file_id[:8]}"
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(
                                _store_in_chromadb, text, collection_id, pf.original_name),
                            timeout=CHROMA_STORE_TIMEOUT_S,
                        )
                        pf.chromadb_collection = collection_id
                        ctx.chromadb_collections.append(collection_id)
                        log.info("DOCX stored in ChromaDB",
                                 file=pf.original_name, chars=len(text),
                                 collection=collection_id)
                    except asyncio.TimeoutError:
                        log.error("ChromaDB storage timed out for DOCX",
                                  file=pf.original_name, timeout_s=CHROMA_STORE_TIMEOUT_S)
                    except Exception as e:
                        log.error("ChromaDB storage failed for DOCX", error=str(e))
                    pf.extracted_text = text  # retained for failure detection only
            except asyncio.TimeoutError:
                log.error("DOCX extraction timed out", file=pf.original_name,
                          timeout_s=DOCX_EXTRACT_TIMEOUT_S)
                pf.error = f"DOCX extraction timed out after {DOCX_EXTRACT_TIMEOUT_S}s"
            except Exception as e:
                pf.error = f"Failed to read DOCX: {e}"

        elif ext == ".xlsx":
            emit({
                "type": "file_processing",
                "stage": "xlsx_extract_start",
                "message": f"Extracting text from {pf.original_name}...",
                "file": pf.original_name,
            })
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(_extract_xlsx_text, local_path),
                    timeout=XLSX_EXTRACT_TIMEOUT_S,
                )
                log.debug("XLSX text extracted",
                          file=pf.original_name, chars=len(text))
                if text.strip():
                    # Phase B: every XLSX goes to ChromaDB. Size threshold removed.
                    collection_id = f"inline_{thread_id}_{pf.file_id[:8]}"
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(
                                _store_in_chromadb, text, collection_id, pf.original_name),
                            timeout=CHROMA_STORE_TIMEOUT_S,
                        )
                        pf.chromadb_collection = collection_id
                        ctx.chromadb_collections.append(collection_id)
                        log.info("XLSX stored in ChromaDB",
                                 file=pf.original_name, chars=len(text),
                                 collection=collection_id)
                    except asyncio.TimeoutError:
                        log.error("ChromaDB storage timed out for XLSX",
                                  file=pf.original_name, timeout_s=CHROMA_STORE_TIMEOUT_S)
                    except Exception as e:
                        log.error("ChromaDB storage failed for XLSX", error=str(e))
                    pf.extracted_text = text  # retained for failure detection only
            except asyncio.TimeoutError:
                log.error("XLSX extraction timed out", file=pf.original_name,
                          timeout_s=XLSX_EXTRACT_TIMEOUT_S)
                pf.error = f"XLSX extraction timed out after {XLSX_EXTRACT_TIMEOUT_S}s"
            except Exception as e:
                pf.error = f"Failed to read XLSX: {e}"

        # Image branch — JPEG / PNG / WebP. Vision OCR extracts text and
        # routes it through the single ChromaDB pipeline. Phase B: no more
        # inline_text population; agents read via get_full_attachment.
        elif ext in (".jpg", ".jpeg", ".png", ".webp"):
            emit({
                "type": "file_processing",
                "stage": "image_ocr_start",
                "message": f"OCR-ing {pf.original_name}...",
                "file": pf.original_name,
            })
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(_vision_ocr_image, local_path, pf.original_name),
                    timeout=120,
                )
                if text.strip():
                    # Phase B: every image's OCR text goes to ChromaDB.
                    collection_id = f"inline_{thread_id}_{pf.file_id[:8]}"
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(
                                _store_in_chromadb, text, collection_id, pf.original_name),
                            timeout=CHROMA_STORE_TIMEOUT_S,
                        )
                        pf.chromadb_collection = collection_id
                        ctx.chromadb_collections.append(collection_id)
                        log.info("Image OCR stored in ChromaDB",
                                 file=pf.original_name, chars=len(text),
                                 collection=collection_id)
                    except asyncio.TimeoutError:
                        log.error("ChromaDB storage timed out for image OCR text",
                                  file=pf.original_name, timeout_s=CHROMA_STORE_TIMEOUT_S)
                    except Exception as e:
                        log.error("ChromaDB storage failed for image OCR text",
                                  file=pf.original_name, error=str(e))
                    pf.extracted_text = text  # retained for failure detection only
                else:
                    log.warning("Image OCR returned no text",
                                file=pf.original_name)
            except asyncio.TimeoutError:
                log.error("Image OCR timed out",
                          file=pf.original_name)
            except Exception as e:
                pf.error = f"Image OCR failed: {e}"

        # CSV: extract → ChromaDB (Phase B: no more dependence on Gemini URI).
        elif ext == ".csv":
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(_extract_csv_text, local_path),
                    timeout=CSV_EXTRACT_TIMEOUT_S,
                )
                if text.strip():
                    collection_id = f"inline_{thread_id}_{pf.file_id[:8]}"
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(
                                _store_in_chromadb, text, collection_id, pf.original_name),
                            timeout=CHROMA_STORE_TIMEOUT_S,
                        )
                        pf.chromadb_collection = collection_id
                        ctx.chromadb_collections.append(collection_id)
                        log.info("CSV stored in ChromaDB",
                                 file=pf.original_name, chars=len(text),
                                 collection=collection_id)
                    except asyncio.TimeoutError:
                        log.error("ChromaDB storage timed out for CSV",
                                  file=pf.original_name, timeout_s=CHROMA_STORE_TIMEOUT_S)
                    except Exception as e:
                        log.error("ChromaDB storage failed for CSV", error=str(e))
                    pf.extracted_text = text
            except asyncio.TimeoutError:
                log.warning("CSV extraction timed out",
                            file=pf.original_name, timeout_s=CSV_EXTRACT_TIMEOUT_S)
            except Exception as e:
                log.warning("CSV extraction failed",
                            file=pf.original_name, error=str(e)[:200])

        elif ext in (".txt", ".md"):
            # Phase B: txt/md content goes to ChromaDB. No truncation; users'
            # no-info-loss principle applies — read the full file.
            try:
                with open(local_path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                if text.strip():
                    collection_id = f"inline_{thread_id}_{pf.file_id[:8]}"
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(
                                _store_in_chromadb, text, collection_id, pf.original_name),
                            timeout=CHROMA_STORE_TIMEOUT_S,
                        )
                        pf.chromadb_collection = collection_id
                        ctx.chromadb_collections.append(collection_id)
                        log.info("txt/md stored in ChromaDB",
                                 file=pf.original_name, chars=len(text),
                                 collection=collection_id)
                    except asyncio.TimeoutError:
                        log.error("ChromaDB storage timed out for txt/md",
                                  file=pf.original_name, timeout_s=CHROMA_STORE_TIMEOUT_S)
                    except Exception as e:
                        log.error("ChromaDB storage failed for txt/md", error=str(e))
                    pf.extracted_text = text
            except Exception as e:
                log.warning("txt/md read failed",
                            file=pf.original_name, error=str(e)[:200])

        # If Gemini upload failed and no text was extracted, mark as error
        if not pf.gemini_uri and not pf.extracted_text and not pf.chromadb_collection and not pf.error:
            pf.error = "No content could be extracted from this file"

        ctx.files.append(pf)

        # --- Persist to SQLite thread_files ---
        # Wrapped in wait_for so a slow PostgreSQL connection cannot hang the
        # whole request after the heavy lifting (extract / OCR / embed) is
        # already done. SQLite save is normally < 10 ms; the 30 s ceiling
        # catches pathological cases (connection pool exhaustion etc).
        try:
            await asyncio.wait_for(
                chat_store.save_thread_file(thread_id, pf),
                timeout=SQLITE_SAVE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            log.error("save_thread_file timed out — file processed but not persisted",
                      file=pf.original_name, timeout_s=SQLITE_SAVE_TIMEOUT_S)
        except Exception as e:
            log.error("Failed to save thread_file record", file=pf.original_name, error=str(e))

        log.info("File processed",
                 file=pf.original_name, type=pf.file_type,
                 gemini=bool(pf.gemini_uri), chromadb=bool(pf.chromadb_collection),
                 text_len=len(pf.extracted_text))

    # Build summary
    type_counts: dict[str, int] = {}
    for pf in ctx.files:
        if not pf.error:
            type_counts[pf.file_type] = type_counts.get(pf.file_type, 0) + 1
    parts = [f"{c} {t}{'s' if c > 1 else ''}" for t, c in type_counts.items()]
    ctx.summary = f"Processed {', '.join(parts)}" if parts else "No files processed"

    emit({
        "type": "file_processing",
        "stage": "all_files_done",
        "message": ctx.summary,
        "files": ctx.file_names,
        "errors": [{"name": pf.original_name, "error": pf.error}
                   for pf in ctx.files if pf.error],
    })

    log.info("File processing complete",
             total=len(files), summary=ctx.summary,
             chromadb=len(ctx.chromadb_collections))

    return ctx