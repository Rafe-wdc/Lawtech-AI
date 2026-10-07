"""Draft artifacts in the chat flow.

Two hooks used by core.chat_runner, both behind DRAFT_ARTIFACT_EVENTS=1 and
both written so that any failure falls back to today's behaviour:

  record_draft      after the drafting agent finishes: store the draft as the
                    next version and build the `artifact_done` event.

  try_partial_edit  before the agent graph runs on a follow-up: if the thread
                    has a draft and the message asks to change part of it,
                    rewrite only those sections and return an `artifact_patch`.
                    Returns None for anything else (a question, a whole-draft
                    request, a new matter), and the normal pipeline runs.
"""

from __future__ import annotations

import os

from core.artifact_edit import EditError, apply_instruction, route_instruction
from core.artifact_store import (
    SOURCE_ASSISTANT, SOURCE_DRAFT, StaleVersion, artifact_id_for_thread, artifact_store,
)
from core.draft_to_blocks import artifact_to_markdown, markdown_to_artifact
from core.logger import get_logger

log = get_logger("ArtifactFollowup")


def enabled() -> bool:
    return os.getenv("DRAFT_ARTIFACT_EVENTS", "").strip().lower() in ("1", "true", "yes", "on")


async def record_draft(thread_id: str, draft_md: str, language: str = "en") -> dict | None:
    """`artifact_done` for a finished draft, stored as the next version."""
    if not enabled() or not (draft_md or "").strip():
        return None
    try:
        art = markdown_to_artifact(draft_md, artifact_id=artifact_id_for_thread(thread_id),
                                   language=language or "en")
        if not art.sections:
            return None
        try:
            art = await artifact_store.save(art, thread_id=thread_id, source=SOURCE_DRAFT,
                                            change_summary="Draft generated")
        except Exception as e:
            # The panel still gets the draft; only history is missing.
            log.warning("Draft version not stored", error=str(e)[:200])
        log.info("Draft artifact emitted", version=art.version, sections=len(art.sections),
                 blocks=sum(len(s.blocks) for s in art.sections))
        return {"type": "artifact_done", "artifact_id": art.artifact_id,
                "version": art.version, "artifact": art.model_dump()}
    except Exception as e:
        log.warning("Draft artifact skipped", error=str(e)[:200])
        return None


async def preview_start(thread_id: str, plan: list[dict]) -> dict | None:
    """`artifact_start` when the drafting agent announces its section plan.

    Preview sections are the pairs the writer drafts together, with ids
    "p1", "p3", ... They are placeholders: `artifact_done` carries the final
    sections (ids "s1", "s2", ...) and replaces them.
    """
    if not enabled() or not plan:
        return None
    try:
        artifact_id = artifact_id_for_thread(thread_id)
        try:
            latest = await artifact_store.get(artifact_id)
            version = (latest.version if latest else 0) + 1
        except Exception:
            version = 1
        sections = []
        for p in plan:
            headings = [h for h in (p.get("headings") or []) if h]
            sections.append({"id": f"p{int(p['index'])}",
                             "heading": " / ".join(headings)[:120] or "Section"})
        return {"type": "artifact_start", "artifact_id": artifact_id, "version": version,
                "sections": sections}
    except Exception as e:
        log.warning("Artifact preview start skipped", error=str(e)[:160])
        return None


def preview_section(thread_id: str, index: int, headings: list[str], text: str) -> dict | None:
    """`artifact_section` (status "preview") for one drafted pair."""
    if not enabled() or not (text or "").strip():
        return None
    try:
        sid = f"p{int(index)}"
        art = markdown_to_artifact(text, artifact_id="preview", start_in_body=int(index) > 1)
        blocks = [b for s in art.sections for b in s.blocks]
        for n, b in enumerate(blocks, 1):
            b.id = f"{sid}.b{n}"
        if not blocks:
            return None
        return {"type": "artifact_section", "artifact_id": artifact_id_for_thread(thread_id),
                "status": "preview",
                "section": {"id": sid,
                            "heading": " / ".join(h for h in headings if h)[:120] or "Section",
                            "blocks": [b.model_dump() for b in blocks]}}
    except Exception as e:
        log.warning("Artifact preview section skipped", error=str(e)[:160])
        return None


async def _input_allowed(query: str) -> bool:
    """The same input guardrail the agent graph applies."""
    try:
        from agents.guardrail import guardrail_input_node
        out = await guardrail_input_node({"original_query": query, "query": query})
        return not (out or {}).get("is_blocked")
    except Exception as e:
        log.warning("Input guardrail unavailable; using the full pipeline", error=str(e)[:160])
        return False


async def try_partial_edit(thread_id: str, query: str) -> dict | None:
    """Handle `query` as an edit to part of the thread's draft, or return None.

    On success: {"patch": <artifact_patch event>, "markdown": <whole draft as
    Markdown>, "summary": <one line for the chat>, "sections": [headings]}.
    """
    if not enabled() or not (query or "").strip():
        return None
    try:
        artifact = await artifact_store.get(artifact_id_for_thread(thread_id))
    except Exception as e:
        log.warning("Artifact lookup failed", error=str(e)[:160])
        return None
    if artifact is None:
        return None
    if not await _input_allowed(query):
        return None

    route = await route_instruction(artifact, query)
    log.info("Draft follow-up routed", scope=route["scope"],
             sections=route["section_ids"], reason=route["reason"][:120])
    if route["scope"] != "sections":
        return None

    base = artifact.version
    try:
        new, changed = await apply_instruction(artifact, route["section_ids"], query)
    except EditError as e:
        log.warning("Partial edit failed; using the full pipeline", error=str(e)[:200])
        return None
    edited = [s for s in changed if s.id in route["section_ids"]]
    if not edited:
        return None
    headings = [s.heading for s in edited]
    if len(artifact.sections) == 1:
        summary = "Updated the draft"
    else:
        summary = "Updated " + ", ".join(headings[:3]) + ("…" if len(headings) > 3 else "")
    try:
        new = await artifact_store.save(new, thread_id=thread_id, source=SOURCE_ASSISTANT,
                                        change_summary=f"{summary}: {query.strip()[:160]}",
                                        base_version=base)
    except StaleVersion:
        log.warning("Draft changed during the edit; using the full pipeline")
        return None
    except Exception as e:
        log.warning("Edited version not stored; using the full pipeline", error=str(e)[:200])
        return None

    return {
        "patch": {
            "type": "artifact_patch", "artifact_id": new.artifact_id,
            "base_version": base, "version": new.version, "summary": summary,
            "edited_section_ids": [s.id for s in edited],
            "sections": [s.model_dump() for s in changed],
        },
        "markdown": artifact_to_markdown(new),
        "summary": summary,
        "sections": headings,
    }


async def current_draft_markdown(thread_id: str) -> str | None:
    """The thread's latest draft as Markdown, when it has been edited since
    the chat history last saw it (typed in the panel). None otherwise."""
    if not enabled():
        return None
    try:
        art = await artifact_store.get(artifact_id_for_thread(thread_id))
        return artifact_to_markdown(art) if art is not None else None
    except Exception as e:
        log.warning("Current draft lookup failed", error=str(e)[:160])
        return None
