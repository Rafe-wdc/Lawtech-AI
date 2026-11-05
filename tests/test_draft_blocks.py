"""The block format behind the draft side panel.

The sample in docs/ is what the frontend team builds against, so it must
always validate against the schema the backend emits, and the preview page
must embed exactly that sample.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.draft_blocks import (
    Artifact, ParaBlock, number_paragraphs, placeholders, to_markdown,
)

DOCS = Path(__file__).resolve().parent.parent / "docs"
SAMPLE = json.loads((DOCS / "draft_panel_sample.json").read_text(encoding="utf-8"))


def _sample() -> Artifact:
    return Artifact.model_validate(SAMPLE)


def test_sample_validates():
    a = _sample()
    assert a.sections and all(s.blocks for s in a.sections)
    ids = [b.id for s in a.sections for b in s.blocks]
    assert len(ids) == len(set(ids)), "block ids must be unique"


def test_preview_page_embeds_the_sample():
    html = (DOCS / "draft_panel_preview.html").read_text(encoding="utf-8")
    m = re.search(r'<script id="sample" type="application/json">(.*?)</script>', html, re.S)
    assert m and json.loads(m.group(1)) == SAMPLE


def test_every_block_type_is_documented():
    doc = (DOCS / "draft_panel_frontend_contract.md").read_text(encoding="utf-8")
    for t in ("title", "heading", "text", "row", "para", "signature", "table", "page_break"):
        assert f"| `{t}` |" in doc, t


def test_paragraphs_are_numbered_by_the_backend_in_order():
    a = _sample()
    for s in a.sections:
        for b in s.blocks:
            if isinstance(b, ParaBlock):
                b.number = 7          # a model that miscounts
    number_paragraphs(a)
    numbered = [b.number for s in a.sections for b in s.blocks
                if isinstance(b, ParaBlock) and not b.label]
    assert numbered == list(range(1, len(numbered) + 1))
    labelled = [b for s in a.sections for b in s.blocks if isinstance(b, ParaBlock) and b.label]
    assert labelled and all(b.number is None for b in labelled)


def test_placeholders_are_listed_once_in_order():
    assert placeholders(_sample()) == [
        "number", "date of charge-sheet", "date", "advocate name", "day", "month"]


def test_markdown_fallback_cannot_renumber_or_blank_a_paragraph():
    md = to_markdown(_sample())
    assert "IN THE HIGH COURT OF JUDICATURE AT BOMBAY" in md
    assert "1\\. The Applicant is a law-abiding citizen" in md
    # no line opens a markdown list item, so no renderer can renumber
    assert not re.search(r"(?m)^\d+\.\s", md)
    assert "[[" not in md and "[advocate name]" in md
    assert "...Applicant" in md and "A. BECAUSE" in md


def test_unknown_block_type_is_rejected_by_the_backend():
    bad = json.loads(json.dumps(SAMPLE))
    bad["sections"][0]["blocks"].append({"id": "x1", "type": "marquee", "text": "no"})
    with pytest.raises(ValidationError):
        Artifact.model_validate(bad)


def test_extra_fields_are_rejected():
    bad = json.loads(json.dumps(SAMPLE))
    bad["sections"][0]["blocks"][0]["color"] = "red"
    with pytest.raises(ValidationError):
        Artifact.model_validate(bad)
