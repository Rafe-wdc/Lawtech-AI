"""Changing part of a draft without rewriting all of it.

Three ways a draft changes after it is generated:

  1. The advocate types in the panel         -> apply_user_sections
  2. The advocate selects a section and asks -> edit_section
  3. The advocate asks in the chat           -> route_instruction, then
                                                edit_section per section, or
                                                "whole" (caller regenerates)

Only the sections that change are rewritten. A section goes to the model as
text (core.draft_to_blocks.section_to_markdown) with the document outline for
context, comes back as text, and is read back into blocks; every other
section is left byte-for-byte as it was. Paragraph numbers are then
re-assigned across the document, because adding a paragraph in one section
shifts the numbers after it.
"""

from __future__ import annotations

import asyncio
import re

from pydantic import BaseModel, Field

from core.draft_blocks import Artifact, ParaBlock, Section
from core.draft_to_blocks import markdown_to_section, section_to_markdown
from core.logger import get_logger

log = get_logger("ArtifactEdit")

MAX_SECTIONS_PER_EDIT = 4


class EditError(Exception):
    """The edit could not be made; the artifact is unchanged."""


# ---------------------------------------------------------------------------
# Numbering
# ---------------------------------------------------------------------------

def renumber(artifact: Artifact) -> Artifact:
    """Number paragraphs in document order.

    A paragraph numbered 1 that is the first numbered paragraph of its section
    starts a new sequence (an affidavit, a list of documents); everything else
    continues from the paragraph before it. Lettered paragraphs keep their
    label and take no number.
    """
    counter = 0
    for sec in artifact.sections:
        first_in_section = True
        for b in sec.blocks:
            if not isinstance(b, ParaBlock) or b.label:
                continue
            if first_in_section and b.number == 1:
                counter = 1
            else:
                counter += 1
            b.number = counter
            first_in_section = False
    return artifact


def _reid(section: Section) -> Section:
    for n, b in enumerate(section.blocks, 1):
        b.id = f"{section.id}.b{n}"
    return section


def changed_sections(before: Artifact, after: Artifact) -> list[Section]:
    old = {s.id: s.model_dump() for s in before.sections}
    return [s for s in after.sections if old.get(s.id) != s.model_dump()]


# ---------------------------------------------------------------------------
# 1. What the advocate typed
# ---------------------------------------------------------------------------

def apply_user_sections(artifact: Artifact, sections: list[dict]) -> Artifact:
    """Replace whole sections with what the client sent. Unknown section ids
    and invalid blocks raise EditError; nothing is partially applied."""
    if not sections:
        raise EditError("No sections to save")
    by_id = {s.id: i for i, s in enumerate(artifact.sections)}
    new = artifact.model_copy(deep=True)
    for raw in sections:
        sid = (raw or {}).get("id")
        if sid not in by_id:
            raise EditError(f"Unknown section id: {sid!r}")
        try:
            raw = dict(raw)
            # ids are the server's; accept blocks without them
            raw["blocks"] = [{**b, "id": b.get("id") or f"{sid}.b{n}"}
                             for n, b in enumerate(raw.get("blocks") or [], 1)]
            raw.setdefault("heading", artifact.sections[by_id[sid]].heading)
            sec = Section.model_validate(raw)
        except Exception as e:
            raise EditError(f"Section {sid}: invalid content ({str(e)[:160]})") from e
        new.sections[by_id[sid]] = _reid(sec)
    return renumber(new)


# ---------------------------------------------------------------------------
# 2. One section, rewritten on instruction
# ---------------------------------------------------------------------------

SECTION_EDIT_PROMPT = """You are revising ONE section of an Indian legal draft. \
The rest of the document is not yours to change and is not shown in full.

DOCUMENT: {title}
SECTIONS OF THE DOCUMENT (for context only):
{outline}

THE SECTION YOU ARE REVISING: "{heading}"
<<<SECTION
{section_text}
SECTION>>>
{selection_block}
THE ADVOCATE'S INSTRUCTION:
{instruction}

RULES
1. Make the change the advocate asked for, and nothing else. Every sentence \
the instruction does not touch must come back word for word.
2. Return the COMPLETE revised section, in exactly the same notation as above:
   - headings as "## HEADING"; numbered paragraphs as "N. text"; sub-items as \
"(a) text"; lettered grounds as "A. text"
   - lines starting ">> " are signature lines; a line "[page break]" is a page \
break; " || " separates a left and a right column. Keep these markers.
   - keep every [Blank In Square Brackets] exactly unless the instruction \
supplies its value.
3. Keep the paragraph numbers already used. If you add a paragraph, give it \
the next number in place; the numbers after it are corrected automatically.
4. Do not invent facts, dates, amounts, names, case citations or statutory \
provisions. If the instruction needs a fact you do not have, leave a \
[Blank Describing It].
5. Write in the same language and register as the section. Formal court \
English unless the section is in another language.
6. Output the section only. No preface, no explanation, no code fence."""

_FENCE_RE = re.compile(r"^```[a-z]*\s*|\s*```$", re.I)


def _outline(artifact: Artifact, current: str) -> str:
    return "\n".join(f"  {'->' if s.id == current else '  '} {s.heading}"
                     for s in artifact.sections)


async def edit_section(artifact: Artifact, section_id: str, instruction: str, *,
                       selected_text: str = "", llm=None) -> Section:
    """Rewrite one section per `instruction`. Returns the new Section (not yet
    placed in the artifact). Raises EditError on failure."""
    idx = next((i for i, s in enumerate(artifact.sections) if s.id == section_id), None)
    if idx is None:
        raise EditError(f"Unknown section id: {section_id!r}")
    if not (instruction or "").strip():
        raise EditError("No instruction given")
    section = artifact.sections[idx]
    # The first section opens the document (cause title, or a letterhead).
    cause_title = idx == 0
    text = section_to_markdown(section, cause_title=cause_title)

    selection_block = ""
    if selected_text and selected_text.strip():
        selection_block = ("\nTHE ADVOCATE SELECTED THIS PART (change this part; leave the rest "
                           f"of the section as it is):\n\"{selected_text.strip()[:2000]}\"\n")
    prompt = SECTION_EDIT_PROMPT.format(
        title=artifact.title, outline=_outline(artifact, section_id),
        heading=section.heading, section_text=text,
        selection_block=selection_block, instruction=instruction.strip()[:4000])

    if llm is None:
        from core.clients import get_drafting_llm
        llm = get_drafting_llm(max_output_tokens=min(16000, 2000 + len(text) // 2),
                               thinking_budget=1024)
    from langchain_core.messages import HumanMessage, SystemMessage
    try:
        response = await asyncio.to_thread(
            llm.invoke, [SystemMessage(content=prompt),
                         HumanMessage(content="Return the complete revised section now.")])
    except Exception as e:
        raise EditError(f"The edit could not be generated ({str(e)[:120]})") from e
    try:
        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "edit_section", response)
    except Exception:
        pass

    out = (getattr(response, "text", None) or getattr(response, "content", "") or "")
    if callable(out):
        out = out()
    out = _FENCE_RE.sub("", str(out).strip()).strip()
    out = re.sub(r"^<<<SECTION\s*|\s*SECTION>>>$", "", out).strip()
    if not out:
        raise EditError("The edit came back empty")
    # Same runaway / malformed-output repair every other LLM output gets.
    from core.sanitize import sanitize_output
    out = sanitize_output(out)
    # A section that shrinks to a fraction was truncated or replaced by a
    # comment about the change, not edited.
    if len(out) < 0.4 * len(text) and len(text) > 600 and "remove" not in instruction.lower() \
            and "delete" not in instruction.lower() and "shorten" not in instruction.lower():
        raise EditError("The edit dropped most of the section; it was not applied")

    # No citation may enter a draft through a partial edit unless the advocate
    # typed it: there is no retrieval here to verify it against.
    invented = _citations(out) - _citations(text) - _citations(instruction) \
        - _citations(selected_text)
    if invented:
        raise EditError("The edit added a case citation that was not supplied; "
                        "it was not applied")

    new_section = markdown_to_section(out, section, cause_title=cause_title)
    if not new_section.blocks:
        raise EditError("The edit produced no content")
    return new_section


def place_sections(artifact: Artifact, sections: list[Section]) -> Artifact:
    new = artifact.model_copy(deep=True)
    pos = {s.id: i for i, s in enumerate(new.sections)}
    for s in sections:
        new.sections[pos[s.id]] = _reid(s)
    return renumber(new)


# ---------------------------------------------------------------------------
# 3. An instruction typed in the chat: which sections does it touch?
# ---------------------------------------------------------------------------

# The user asked for the document to be done again, or for a change that
# touches every part of it. These go to the full drafting pipeline.
_WHOLE_DRAFT_RE = re.compile(
    r"\b(?:re-?draft|re-?generate|re-?write|re-?do|start\s+(?:over|again)|from\s+scratch"
    r"|draft\s+(?:it\s+|this\s+)?(?:again|afresh)|whole\s+(?:draft|document|thing)"
    r"|entire\s+(?:draft|document)|complete(?:ly)?\s+(?:new|different|re)"
    r"|translate|convert\s+(?:it|this|the\s+draft)?\s*(?:in)?to"
    r"|(?:in|into)\s+(?:marathi|hindi|english|tamil|telugu|kannada|malayalam|bengali|punjabi"
    r"|gujarati|urdu|odia|assamese|sanskrit)"
    r"|change\s+(?:it|this)\s+(?:in)?to\s+an?\s+\w+|make\s+(?:it|this)\s+an?\s+(?:\w+\s+){0,2}"
    r"(?:plaint|petition|application|notice|appeal|affidavit|reply|written\s+statement))\b", re.I)


class _Route(BaseModel):
    scope: str = Field(description="'sections' when the instruction changes specific parts of "
                                   "the draft; 'whole' when it asks to redo the entire draft, "
                                   "change its language, type or overall tone/length; 'none' when "
                                   "it is a question or not a request to change the draft.")
    section_ids: list[str] = Field(default_factory=list,
                                   description="For scope 'sections': ids of the sections that "
                                               "must change, fewest possible, most relevant first.")
    reason: str = Field("", description="One short sentence.")


ROUTE_PROMPT = """An advocate has a draft open and typed an instruction. Decide \
which parts of the draft must change.

DRAFT: {title}
SECTIONS (id | heading | how it starts):
{sections}

INSTRUCTION: {instruction}

- scope "sections": the instruction fixes, adds to, removes from or rewords \
specific content. Pick the FEWEST sections that must change (usually one). \
A new ground goes in the grounds section; a new prayer in the prayer; a \
party's name or address in the cause title and wherever else it is written.
- scope "whole": the advocate asks to redraft or regenerate the document, to \
change its language, its type (e.g. notice into plaint), or its tone or \
length throughout.
- scope "none": a question, a request for case law or explanation, or anything \
that is not a request to change this draft."""


# A partial edit has no retrieval behind it. Measured 2026-10-07 on "add
# relevant case laws": the section edit wrote four reporter citations from the
# model's memory despite rule 4. Anything that asks for authorities goes to
# the full pipeline, which searches the judgment indexes and links the PDFs.
_NEEDS_RESEARCH_RE = re.compile(
    r"\b(?:case\s*laws?|judge?ments?|precedents?|citations?|rulings?"
    r"|(?:supreme|high)\s+court\s+(?:has\s+held|held|cases?|decisions?)"
    r"|cite|citing|relied\s+upon|landmark)\b", re.I)

# Reporter citations: "(2020) 11 SCC 648", "AIR 1978 SC 597", "2024 INSC 32",
# "2021 SCC OnLine Bom 1234".
_CITATION_RE = re.compile(
    r"\(\d{4}\)\s*\d+\s*SCC\s*(?:\([A-Za-z]+\)\s*)?\d+|\bAIR\s+\d{4}\s+[A-Za-z]+\s+\d+"
    r"|\b\d{4}\s+INSC\s+\d+|\b\d{4}\s+SCC\s+OnLine\s+[A-Za-z]+\s+\d+"
    r"|\b\d{4}\s*\(\d+\)\s*(?:SCC|SCALE|SCR)\s+\d+", re.I)


def _citations(text: str) -> set[str]:
    return {re.sub(r"\s+", "", m.group(0)).lower() for m in _CITATION_RE.finditer(text or "")}


def is_whole_draft_request(instruction: str) -> bool:
    return bool(_WHOLE_DRAFT_RE.search(instruction or ""))


def needs_research(instruction: str) -> bool:
    """The instruction asks for case law or authorities, which a partial
    edit cannot look up."""
    return bool(_NEEDS_RESEARCH_RE.search(instruction or ""))


async def route_instruction(artifact: Artifact, instruction: str, *, llm=None) -> dict:
    """{'scope': 'whole'|'sections'|'none', 'section_ids': [...], 'reason': str}.

    Deterministic for explicit whole-draft requests; one small model call
    otherwise. Any failure returns 'whole' — the caller then takes the
    existing full-draft path, which is today's behaviour.
    """
    if is_whole_draft_request(instruction):
        return {"scope": "whole", "section_ids": [], "reason": "explicit whole-draft request"}
    if needs_research(instruction):
        return {"scope": "whole", "section_ids": [],
                "reason": "asks for case law, which needs the judgment search"}
    valid = [s.id for s in artifact.sections]
    listing = "\n".join(
        f"{s.id} | {s.heading} | "
        f"{section_to_markdown(s, cause_title=i == 0)[:160].replace(chr(10), ' ')}"
        for i, s in enumerate(artifact.sections))
    try:
        if llm is None:
            from core.clients import get_gemini_flash_planning
            llm = get_gemini_flash_planning(temperature=0.0).with_structured_output(_Route)
        route = await asyncio.to_thread(
            llm.invoke, ROUTE_PROMPT.format(title=artifact.title, sections=listing,
                                            instruction=(instruction or "").strip()[:2000]))
        scope = (getattr(route, "scope", "") or "").strip().lower()
        ids = [i for i in (getattr(route, "section_ids", None) or []) if i in valid]
        reason = getattr(route, "reason", "") or ""
    except Exception as e:
        log.warning("Edit routing failed; treating as whole-draft", error=str(e)[:160])
        return {"scope": "whole", "section_ids": [], "reason": "routing failed"}
    if scope == "none":
        return {"scope": "none", "section_ids": [], "reason": reason}
    if scope != "sections" or not ids or len(ids) > MAX_SECTIONS_PER_EDIT:
        return {"scope": "whole", "section_ids": [], "reason": reason or "broad change"}
    return {"scope": "sections", "section_ids": ids, "reason": reason}


async def apply_instruction(artifact: Artifact, section_ids: list[str], instruction: str, *,
                            selected_text: str = "", llm=None) -> tuple[Artifact, list[Section]]:
    """Edit the given sections in parallel. Returns (new_artifact, changed
    sections including any whose paragraph numbers moved). If every section
    edit fails, raises EditError; a partial failure keeps the ones that
    worked."""
    results = await asyncio.gather(
        *(edit_section(artifact, sid, instruction, selected_text=selected_text, llm=llm)
          for sid in section_ids), return_exceptions=True)
    done = [r for r in results if isinstance(r, Section)]
    errors = [r for r in results if isinstance(r, Exception)]
    if not done:
        raise EditError(str(errors[0]) if errors else "Nothing was changed")
    for e in errors:
        log.warning("One section edit failed; others applied", error=str(e)[:160])
    new = place_sections(artifact, done)
    return new, changed_sections(artifact, new)
