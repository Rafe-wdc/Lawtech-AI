"""Live sections while a draft is being written (artifact_start /
artifact_section), built from the drafting agent's raw plan and pair events."""

from __future__ import annotations

import asyncio

import pytest

from core import artifact_followup as af
from core.draft_blocks import Section

THREAD = "4c583f65-3e59-4764-a69d-b77b7baca1fc"
PLAN = [{"index": 1, "headings": ["CAUSE TITLE", "STATEMENT OF FACTS"]},
        {"index": 3, "headings": ["GROUNDS", "PRAYER"]},
        {"index": 5, "headings": ["VERIFICATION"]}]
PAIR_1 = ("**IN THE COURT OF SESSIONS AT PUNE**\n\nMr. Anil Joshi\n\n.....Applicant\n\n**vs**\n\n"
          "The State of Maharashtra\n\n.....Respondent\n\n## STATEMENT OF FACTS\n\n"
          "1. The Applicant is a law-abiding citizen.")
PAIR_3 = "## GROUNDS\n\n2. The investigation is complete.\n\n## PRAYER\n\n3. Bail may be granted."


@pytest.fixture
def on(monkeypatch, tmp_path):
    from core import artifact_store as store_mod
    from core import chat_store as cs
    monkeypatch.setattr(cs, "chat_store", cs._SqliteChatHistoryStore(str(tmp_path / "t.db")))
    monkeypatch.setattr(af, "artifact_store", store_mod._Store())
    monkeypatch.setenv("DRAFT_ARTIFACT_EVENTS", "1")


def test_start_lists_the_planned_pairs(on):
    ev = asyncio.run(af.preview_start(THREAD, PLAN))
    assert ev["type"] == "artifact_start" and ev["artifact_id"] == "art_4c583f653e59"
    assert ev["version"] == 1
    assert [s["id"] for s in ev["sections"]] == ["p1", "p3", "p5"]
    assert ev["sections"][0]["heading"] == "CAUSE TITLE / STATEMENT OF FACTS"


def test_start_gives_the_next_version_when_a_draft_exists(on):
    asyncio.run(af.record_draft(THREAD, PAIR_1 + "\n\n" + PAIR_3))
    assert asyncio.run(af.preview_start(THREAD, PLAN))["version"] == 2


def test_first_pair_is_read_as_a_cause_title_and_later_pairs_as_body(on):
    first = af.preview_section(THREAD, 1, PLAN[0]["headings"], PAIR_1)
    sec = Section.model_validate(first["section"])
    assert first["status"] == "preview" and sec.id == "p1"
    types = [b.type for b in sec.blocks]
    assert types[0] == "title" and "row" in types and types[-1] == "para"
    assert all(b.id.startswith("p1.b") for b in sec.blocks)

    later = Section.model_validate(af.preview_section(THREAD, 3, PLAN[1]["headings"], PAIR_3)["section"])
    assert [b.type for b in later.blocks] == ["heading", "para", "heading", "para"]
    assert [b.number for b in later.blocks if b.type == "para"] == [2, 3]


def test_nothing_is_sent_without_the_flag_or_without_text(on, monkeypatch):
    assert af.preview_section(THREAD, 3, ["GROUNDS"], "   ") is None
    assert asyncio.run(af.preview_start(THREAD, [])) is None
    monkeypatch.delenv("DRAFT_ARTIFACT_EVENTS")
    assert asyncio.run(af.preview_start(THREAD, PLAN)) is None
    assert af.preview_section(THREAD, 1, ["X"], PAIR_1) is None


def test_drafting_emits_raw_events_safely_outside_a_stream():
    from agents.drafting import _emit_draft_stream
    _emit_draft_stream({"type": "draft_plan", "sections": []})     # no stream context: no error
