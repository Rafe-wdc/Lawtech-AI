"""Targeted edits to an existing draft.

A follow-up such as "change paragraph 3" used to regenerate the whole
document: the full drafting pipeline (~9 LLM calls, ~53k tokens measured on
dev 2026-10-19 for a two-page notice), or with the follow-up fast path one
call that re-emits every paragraph. Either way, paragraphs the user never
mentioned came back reworded.

Here the draft is split into numbered blocks, the model returns only edit
operations on those blocks (DraftEditPlan), and the operations are applied in
code. Blocks the model did not name are copied through byte for byte, and
numbered paragraphs are renumbered after an insert or delete.

Pure functions only; the LLM call lives in agents/drafting.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


class DraftEdit(BaseModel):
    op: Literal["replace", "insert_after", "insert_before", "delete"] = Field(
        description="replace: new text for the block. insert_after / "
                    "insert_before: a NEW block next to the given one. "
                    "delete: remove the block.")
    block: str = Field(description="Block id from the numbered draft, e.g. 'B7'.")
    text: str = Field(default="", description="Full text of the new or replaced "
                                              "block. Empty for delete.")


class DraftEditPlan(BaseModel):
    whole_document: bool = Field(
        default=False,
        description="True when the instruction cannot be done block by block "
                    "(translate, shorten or reformat the whole document). Then "
                    "leave edits empty.")
    edits: list[DraftEdit] = Field(default_factory=list, max_length=60)


class EditError(ValueError):
    """The plan cannot be applied to this draft; the caller falls back."""


# --- splitting --------------------------------------------------------------

# A block is a run of non-blank lines. Tables and lists without blank lines
# between rows stay one block. The separators are kept so untouched text is
# reproduced exactly.
_BLANK_RUN = re.compile(r"(\n[ \t]*\n\s*)")


@dataclass
class SplitDraft:
    lead: str              # whitespace before the first block
    blocks: list[str]
    seps: list[str]        # seps[i] follows blocks[i]; the last one is the tail

    @property
    def ids(self) -> list[str]:
        return [f"B{i + 1}" for i in range(len(self.blocks))]


def split_blocks(draft: str) -> SplitDraft:
    lead = draft[: len(draft) - len(draft.lstrip())]
    body = draft[len(lead):]
    parts = _BLANK_RUN.split(body)
    blocks = parts[0::2]
    seps = parts[1::2]
    # A trailing separator leaves an empty last block: fold it into the tail.
    if blocks and blocks[-1] == "" and seps:
        blocks.pop()
    else:
        seps.append("")
    trail = blocks[-1][len(blocks[-1].rstrip()):] if blocks else ""
    if trail:
        blocks[-1] = blocks[-1].rstrip()
        seps[-1] = trail + seps[-1]
    return SplitDraft(lead=lead, blocks=blocks, seps=seps)


def render_numbered(split: SplitDraft) -> str:
    """The draft as the model sees it: every block under its id."""
    return "\n\n".join(f"[{bid}]\n{text}" for bid, text in zip(split.ids, split.blocks))


# --- numbering --------------------------------------------------------------

# "5. That ...", "**5.** That ...", "5) That ..." at the start of a block.
_NUM = re.compile(r"^(\s*(?:\*\*)?)(\d{1,3})([.)])")


def _number(block: str) -> int | None:
    m = _NUM.match(block)
    return int(m.group(2)) if m else None


def _chains(blocks: list[str]) -> dict[int, int]:
    """Original block index -> numbering chain id.

    A chain continues while each number is the previous one plus one, across
    headings (pleadings often number Facts 1-8 and Grounds 9-15 as one
    series); a number that does not follow starts a new chain (the prayer's
    own 1, 2, 3)."""
    chain_of: dict[int, int] = {}
    chain, prev = -1, None
    for i, b in enumerate(blocks):
        n = _number(b)
        if n is None:
            continue
        if prev is None or n != prev + 1:
            chain += 1
        chain_of[i] = chain
        prev = n
    return chain_of


def _renumber(out_blocks: list[str], origin: list[int | None],
              orig_blocks: list[str], deleted: set[int]) -> list[str]:
    """Renumber the chains that gained or lost a numbered paragraph.

    origin[k] is the original index of output block k, None for an inserted
    block. An inserted numbered block joins the chain of the numbered block
    before it (or after it, at the very start). Chains are renumbered from
    their original first number; untouched chains are left alone."""
    chain_of = _chains(orig_blocks)
    starts: dict[int, int] = {}
    for i, c in chain_of.items():
        starts.setdefault(c, _number(orig_blocks[i]))

    out_chain: list[int | None] = [None] * len(out_blocks)
    for k, o in enumerate(origin):
        if o is not None and o in chain_of and _number(out_blocks[k]) is not None:
            out_chain[k] = chain_of[o]
    touched = {chain_of[i] for i in deleted if i in chain_of}
    for k, o in enumerate(origin):
        if (o is None or o not in chain_of) and _number(out_blocks[k]) is not None:
            prev = next((out_chain[j] for j in range(k - 1, -1, -1) if out_chain[j] is not None), None)
            nxt = next((out_chain[j] for j in range(k + 1, len(out_blocks)) if out_chain[j] is not None), None)
            c = prev if prev is not None else nxt
            if c is not None:
                out_chain[k] = c
                touched.add(c)

    out = list(out_blocks)
    counter = {c: starts[c] for c in touched}
    for k, c in enumerate(out_chain):
        if c in touched:
            n = counter[c]
            out[k] = _NUM.sub(lambda m: f"{m.group(1)}{n}{m.group(3)}", out[k], count=1)
            counter[c] += 1
    return out


# --- applying ---------------------------------------------------------------

@dataclass
class EditResult:
    text: str
    changed_blocks: list[str]      # ids of replaced / deleted originals
    inserted: int


def apply_edits(draft: str, plan: DraftEditPlan) -> EditResult:
    """Apply the plan; untouched blocks keep their exact text and spacing."""
    split = split_blocks(draft)
    index = {bid: i for i, bid in enumerate(split.ids)}
    if not plan.edits:
        raise EditError("no edits")

    replaced: dict[int, str] = {}
    deleted: set[int] = set()
    before: dict[int, list[str]] = {}
    after: dict[int, list[str]] = {}
    for e in plan.edits:
        bid = e.block.strip().strip("[]").upper()
        if bid not in index:
            raise EditError(f"unknown block {e.block!r}")
        i = index[bid]
        text = (e.text or "").strip("\n")
        if e.op in ("replace", "insert_after", "insert_before") and not text.strip():
            raise EditError(f"{e.op} on {bid} without text")
        if e.op == "replace":
            if i in replaced or i in deleted:
                raise EditError(f"{bid} edited twice")
            replaced[i] = text
        elif e.op == "delete":
            if i in replaced or i in deleted:
                raise EditError(f"{bid} edited twice")
            deleted.add(i)
        elif e.op == "insert_after":
            after.setdefault(i, []).append(text)
        else:
            before.setdefault(i, []).append(text)
    if len(deleted) == len(split.blocks):
        raise EditError("plan deletes the whole draft")

    # Each original position becomes a group: inserts before, the block (unless
    # deleted, possibly replaced), inserts after. New blocks inside a group are
    # separated by a blank line; the group ends with the original separator.
    out_blocks: list[str] = []
    out_seps: list[str] = []
    origin: list[int | None] = []   # original index of each output block
    for i, block in enumerate(split.blocks):
        group: list[tuple[str, int | None]] = [(t, None) for t in before.get(i, [])]
        if i not in deleted:
            group.append((replaced.get(i, block), i))
        group += [(t, None) for t in after.get(i, [])]
        for k, (t, o) in enumerate(group):
            out_blocks.append(t)
            origin.append(o)
            out_seps.append("\n\n" if k < len(group) - 1 else split.seps[i])
    out_seps[-1] = split.seps[-1]   # the draft's own trailing whitespace

    if before or after or deleted:
        out_blocks = _renumber(out_blocks, origin, split.blocks, deleted)

    text = split.lead + "".join(b + s for b, s in zip(out_blocks, out_seps))
    changed = sorted({split.ids[i] for i in replaced} | {split.ids[i] for i in deleted},
                     key=lambda b: int(b[1:]))
    return EditResult(text=text, changed_blocks=changed,
                      inserted=sum(len(v) for v in before.values()) + sum(len(v) for v in after.values()))
