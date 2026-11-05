"""One-line "case focus" from the user's uploaded documents, for retrieval.

Prod 2026-11-01 (thread 91bcd70a): a lawyer attached an NDPS anticipatory-bail
application (ganja, ss. 8(c) / 20(b)(ii)(B)) and asked for case law "based on
the attached FIR". The retrieval agents searched with the question alone, found
generic anticipatory-bail judgments (several on IPC 406/420), and the answer
was about cheating. The file text reached only the writers' system prompts;
search-query enrichment was left as "Phase B" in core/file_context.py.

`extract_case_focus` reads the attachments once and returns a short line
(statute and sections, offence and the facts that decide the legal test,
stage, relief, forum). The orchestrator's per-agent query rewriter folds it
into each search agent's query. Returns "" on any failure or when the
documents are not about a legal matter, so callers keep today's behaviour.
"""
from __future__ import annotations

import hashlib
import threading

from core.logger import get_logger, log_time
from core.state import FileContextData

log = get_logger("CaseFocus")

# Sections and the charge sit on the first pages of an FIR, chargesheet,
# bail application or plaint; the rest is narrative.
_MAX_CHARS_PER_FILE = 15_000
_MAX_TOTAL_CHARS = 40_000
_MAX_FOCUS_CHARS = 600

CASE_FOCUS_PROMPT = """You read legal documents a lawyer has uploaded and write ONE line (at most 60 words, in English) that a case-law and statute search must match. Include, when the documents state them:
- every statute and section number charged or invoked, exactly as written (e.g. "NDPS Act ss. 8(c), 20(b)(ii)(B)"; "IPC 498A / BNS 85")
- the offence or cause of action in plain words, with the facts that decide the legal test (substance and quantity in NDPS cases, amount in cheating, weapon and injury in hurt cases, age of the victim in POCSO cases)
- the stage: FIR registered, accused arrested or not, chargesheet filed, trial, conviction, appeal
- the relief sought (anticipatory bail, regular bail, quashing, ...)
- the court and state
Use only what the documents say. No party names, no advice, no case law, no commentary. Separate the parts with "; ".
If the documents are not about a legal matter, reply exactly: NONE"""

_cache: dict[str, str] = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 256


def _documents_text(fc: FileContextData) -> str:
    from core.file_context import _gather_file_blocks
    parts: list[str] = []
    total = 0
    for block in _gather_file_blocks(fc):
        block = block[:_MAX_CHARS_PER_FILE]
        if total + len(block) > _MAX_TOTAL_CHARS:
            block = block[:max(0, _MAX_TOTAL_CHARS - total)]
        if not block:
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts)


def extract_case_focus(fc: FileContextData | None) -> str:
    """Return the case-focus line for the attached documents, or "".

    Sync (one Gemini Flash call); run it in a thread. Cached per document
    text, so follow-up turns on the same upload do not pay again.
    """
    if fc is None or not fc.has_content:
        return ""
    try:
        docs = _documents_text(fc)
        if not docs.strip():
            return ""
        key = hashlib.sha1(docs.encode("utf-8", "ignore")).hexdigest()
        with _cache_lock:
            if key in _cache:
                return _cache[key]

        from core.clients import get_gemini_flash_full
        with log_time(log, "Case focus extraction"):
            llm = get_gemini_flash_full(temperature=0, max_output_tokens=2048)
            response = llm.invoke([
                {"role": "system", "content": CASE_FOCUS_PROMPT},
                {"role": "user", "content": docs},
            ])
        from core.token_tracker import record as _record_tokens
        _record_tokens("Orchestrator", "case_focus", response)

        focus = " ".join((response.text or "").split())
        if not focus or focus.upper().strip(" .") == "NONE":
            focus = ""
        focus = focus[:_MAX_FOCUS_CHARS]
        with _cache_lock:
            if len(_cache) >= _CACHE_MAX:
                _cache.pop(next(iter(_cache)))
            _cache[key] = focus
        log.info("Case focus extracted", chars=len(focus), files=len(fc.file_names))
        return focus
    except Exception as e:
        log.warning("Case focus extraction failed; agents search with the question only",
                    error=str(e)[:200])
        return ""
