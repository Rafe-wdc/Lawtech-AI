"""Shared file-context prompt helper for domain agents.

Previously only `agents/document.py` and `agents/drafting.py` read
`FileContextData` — the other 7+ domain agents (Legislation, Judgment,
SCI_Judgment, GST_Judgment, Newacts, Constitution, Maxim, Legal_Concepts,
Scenario) ran on the user's text query alone even when the user had
uploaded a file. That meant "upload FIR + ask which BNS sections apply?"
routed to Newacts but Newacts never saw the FIR content — it produced a
generic section list instead of a targeted match against the FIR's
allegations.

This module gives every domain agent a one-line way to inject the same
file-content block Drafting already uses (`[Uploaded document — {name}]
{text}`) into its generation prompt. Follows the exact pattern in
`agents/drafting.py:2323-2387` — prefer `fc.extracted_texts` (the raw
per-file text captured before chunking), fall back to
`get_full_attachment` over `fc.chromadb_collections` when raw text is
absent, dedupe by filename.

Design notes:
- Gemini 2.5 Pro's 1M-token window absorbs full-document text for
  the common ≤5-file case. Multi-file relevance gating is Gap #5's
  scope — this helper doesn't do MMR retrieval.
- The block is prefix-only (goes into the SYSTEM prompt, not the user
  message) so the agent's downstream retrieval query is unaffected —
  this ships Phase A of Gap #1. Search-query enrichment (letting the
  file content influence ES queries) is a separate Phase B if needed.
- Failure modes (Chroma pool exhaustion, missing collection, empty
  text) degrade to empty string — the agent's normal query flow
  proceeds unchanged, no exception surfaces.
"""
from __future__ import annotations

from typing import Any

from core.logger import get_logger
from core.state import FileContextData

log = get_logger("FileContext")


# Header for the uploaded-source block. Kept short + uppercase so it
# reads as a first-class prompt section boundary alongside the domain
# agent's own headings (`## Judgments`, `## Sections`, etc.).
_BLOCK_HEADER = "## UPLOADED SOURCE DOCUMENTS"

# Per-file entry header used inside the block. Mirrors
# `agents/drafting.py:2353` verbatim so the LLM sees consistent
# formatting whether the file arrives via Drafting or a domain agent.
def _file_header(name: str) -> str:
    return f"[Uploaded document — {name}]"


def _gather_file_blocks(fc: FileContextData) -> list[str]:
    """Return list of formatted per-file blocks from FileContextData.

    Prefers `fc.extracted_texts` (raw text, Chroma-independent). Falls
    back to `get_full_attachment` over `fc.chromadb_collections` for
    files that only have a Chroma collection (legacy state, or future
    file types staged directly to Chroma). Dedupes by filename so a
    file present via both paths appears once.

    Errors from `get_full_attachment` are logged + skipped — the
    caller sees whatever blocks succeeded.
    """
    blocks: list[str] = []
    seen_names: set[str] = set()

    if fc.extracted_texts:
        for entry in fc.extracted_texts:
            text = (entry.get("text") or "").strip()
            name = entry.get("name") or "attached"
            if not text:
                continue
            blocks.append(f"{_file_header(name)}\n{text}")
            seen_names.add(name)

    if fc.chromadb_collections:
        from tools.shared.vectordb_tools import get_full_attachment
        for cid in fc.chromadb_collections:
            try:
                attached = get_full_attachment.invoke({"collection_id": cid})
                full_text = ((attached or {}).get("full_text") or "").strip()
                source_file = (attached or {}).get("source_file") or "attached"
                if full_text and source_file not in seen_names:
                    blocks.append(f"{_file_header(source_file)}\n{full_text}")
                    seen_names.add(source_file)
            except Exception as e:
                log.warning("get_full_attachment failed while assembling "
                            "file-context block",
                            collection=cid, error=str(e)[:120])

    return blocks


def format_file_context_prefix(
    fc: FileContextData | None,
    *,
    max_chars_per_file: int | None = None,
    max_total_chars: int | None = None,
) -> str:
    """Render `FileContextData` as a prompt-prefix block, or "" if empty.

    Return value is intended to be prepended to a domain agent's SYSTEM
    prompt (or the user turn — either works). When the return is empty,
    call sites should skip the concat cleanly so agents without file
    context behave identically to today.

    Args:
        fc: FileContextData or None. None / empty content → "".
        max_chars_per_file: Optional cap on each file's rendered text
            (post-header). None = no per-file cap. Used by agents with
            smaller context windows (Flash Lite) that can't absorb a
            50-page PDF. Truncated files get a trailing "…[truncated]"
            marker so the LLM knows content was cut.
        max_total_chars: Optional cap on the aggregate rendered
            block. When the sum of file blocks exceeds this, files are
            dropped from the END of the list (dropped files are logged)
            with a summary line noting how many were dropped. None =
            no aggregate cap; caller relies on the LLM's context window.

    Returns:
        Empty string when no file context; otherwise a markdown block
        starting with `## UPLOADED SOURCE DOCUMENTS` followed by one
        `[Uploaded document — <name>]\\n<text>` section per file,
        separated by blank lines.
    """
    if fc is None or not fc.has_content:
        return ""

    blocks = _gather_file_blocks(fc)
    if not blocks:
        return ""

    # Per-file cap
    if max_chars_per_file is not None and max_chars_per_file > 0:
        capped: list[str] = []
        for block in blocks:
            if len(block) > max_chars_per_file:
                capped.append(block[:max_chars_per_file].rstrip() + "\n…[truncated]")
            else:
                capped.append(block)
        blocks = capped

    # Aggregate cap
    dropped = 0
    if max_total_chars is not None and max_total_chars > 0:
        running = 0
        kept: list[str] = []
        for block in blocks:
            projected = running + len(block) + 2  # +2 for the "\n\n" separator
            if kept and projected > max_total_chars:
                dropped += 1
                continue
            kept.append(block)
            running = projected
        blocks = kept

    body = "\n\n".join(blocks)
    footer = ""
    if dropped:
        footer = (
            f"\n\n_({dropped} additional uploaded document(s) omitted from this "
            "prompt due to size; the user's next question can name a specific "
            "file to focus on.)_"
        )

    return f"{_BLOCK_HEADER}\n\n{body}{footer}"


def has_file_context(state: dict) -> bool:
    """Cheap check whether `state` carries any uploaded-file content.

    Convenience wrapper for agents that want to gate work on whether
    files are present without instantiating FileContextData.
    """
    fc = FileContextData.from_state(state)
    return bool(fc and fc.has_content)
