"""Word and PDF downloads leave out the notes meant for the lawyer.

A draft's answer carries notes for the advocate: "Details taken out",
"Please check before filing", "Draft incomplete", the language and
case-law warnings, and the failed-court-search line. They stay on screen.
The file the advocate files must end at the signature. See
core/lawyer_notes.py.
"""
from __future__ import annotations

import asyncio
import inspect
import io

import pytest
from docx import Document

from core import lawyer_notes as ln
from core.added_facts import removal_note

BODY = (
    "**IN THE COURT OF THE JUDICIAL MAGISTRATE, RAMPUR**\n\n"
    "**COMPLAINT UNDER SECTION 318 BNS**\n\n"
    "1. The Accused threatened the Complainant.\n\n"
    "- first ground of the complaint\n"
    "- second ground of the complaint\n\n"
    "**VERIFICATION**\n\nVerified at Rampur.\n\nPlace: Rampur\n\nAdvocate for the Complainant"
)
SEARCH_FAILED = ("> ⚠ The Supreme Court judgment search could not be completed in this run, so "
                 "those authorities are missing below. Please retry the query for them.\n\n")
TOP_NOTES = (
    SEARCH_FAILED
    + "> ⚠ **Draft incomplete** — 1 of 5 section(s) could not be generated ('PRAYER'). "
      "Please re-send your prompt to retry.\n\n"
    + "> ⚠ **Verify the case law before filing.** One citation could not be checked.\n\n"
)
REVIEW = ("\n\n---\n\n> ⚠ **Please check before filing** — our review found 1 point(s) in this draft "
          "that it could not fix automatically:\n> - the date of the offence is missing.\n")
SAMPLE = TOP_NOTES + BODY + removal_note(["illiterate", "accompanied by their musclemen"]) + REVIEW

NOTE_WORDS = ("Details taken out", "Please check before filing", "Draft incomplete",
              "Verify the case law", "judgment search could not be completed",
              "illiterate", "musclemen", "date of the offence")


def _no_notes(text: str) -> None:
    for w in NOTE_WORDS:
        assert w not in text, w


# --- The markers match the code that writes each note -----------------------

def test_markers_match_the_code_that_writes_the_notes():
    import agents.drafting as d
    from agents.orchestrator import _missing_sources_banner
    from core.added_facts import NOTE_MARKER
    assert NOTE_MARKER in ln.DRAFT_NOTE_MARKERS
    assert d._UNRESOLVED_REVIEW_BANNER_MARKER in ln.DRAFT_NOTE_MARKERS
    src = inspect.getsource(d)
    for marker in ln.DRAFT_NOTE_MARKERS[2:]:
        assert f"> ⚠ {marker}" in src, marker
    banner = _missing_sources_banner({"SCI_Judgment": "timeout", "Judgment": "error"})
    assert ln.strip_from_markdown(banner + BODY, is_draft=True).startswith("**IN THE COURT")


# --- Markdown (the normal download) -------------------------------------------

def test_every_note_goes_and_the_draft_stays_whole():
    out = ln.strip_from_markdown(SAMPLE, is_draft=True)
    _no_notes(out)
    assert out == BODY + "\n"


def test_draft_only_notes_mark_the_text_as_a_draft():
    assert ln.has_draft_note(SAMPLE)
    assert ln.has_draft_note(BODY + removal_note(["illiterate"]))
    assert not ln.has_draft_note(SEARCH_FAILED + "Research answer.")


def test_research_answer_keeps_the_failed_search_line():
    research = SEARCH_FAILED + "Section 318 BNS punishes cheating.\n"
    assert ln.strip_from_markdown(research, is_draft=False) == research
    assert ln.strip_from_markdown(research, is_draft=True) == "Section 318 BNS punishes cheating.\n"


def test_text_without_notes_is_returned_as_is():
    for text in (BODY, "", "Plain answer with a > quote\n\n> quoted line\n"):
        assert ln.strip_from_markdown(text, is_draft=True) == text


def test_sections_after_the_notes_are_kept():
    tail = "\n\n## Judgments Cited\n\n- *A v. B* (2019) — [PDF](https://example.org/a.pdf)\n"
    out = ln.strip_from_markdown(BODY + removal_note(["illiterate"]) + REVIEW + tail, is_draft=True)
    _no_notes(out)
    assert out.startswith(BODY) and "## Judgments Cited" in out and "*A v. B*" in out


def test_a_quoted_details_note_also_goes():
    quoted = BODY + '\n\n---\n\n> **Details taken out**: these were not in your documents.\n> - "illiterate"\n'
    assert ln.strip_from_markdown(quoted) == BODY + "\n"


# --- The draft panel's blocks ---------------------------------------------------

def _artifact(md: str):
    from core.draft_to_blocks import markdown_to_artifact
    return markdown_to_artifact(md, artifact_id="t", language="en")


def test_panel_blocks_lose_the_notes_and_keep_the_draft():
    from core.draft_blocks import to_markdown
    art = ln.strip_from_artifact(_artifact(SAMPLE))
    text = to_markdown(art)
    _no_notes(text)
    for keep in ("IN THE COURT", "threatened the Complainant", "first ground", "second ground",
                 "VERIFICATION", "Advocate for the Complainant"):
        assert keep in text, keep


def test_panel_note_items_as_bullet_paragraphs_go_too():
    """A real draft's note list arrives as unnumbered "•" para blocks (seen
    2026-11-12); the bullets in the next section stay."""
    from core.draft_blocks import Artifact
    art = Artifact.model_validate({
        "artifact_id": "t", "title": "Bail Application", "doc_type": "bail_application_regular",
        "sections": [
            {"id": "s1", "heading": "Verification", "blocks": [
                {"id": "b1", "type": "text", "text": "Advocate for the Applicant"},
                {"id": "b2", "type": "text",
                 "text": "**Details taken out**: these were not in your documents or messages."},
                {"id": "b3", "type": "para", "number": None, "label": "•", "text": '"aged parents"'},
                {"id": "b4", "type": "para", "number": None, "label": "•", "text": "and 2 more."},
            ]},
            {"id": "s2", "heading": "Judgments Cited", "blocks": [
                {"id": "b5", "type": "para", "number": None, "label": "•", "text": "*A v. B* (2019)"},
            ]},
        ],
    })
    out = ln.strip_from_artifact(art)
    texts = [b.text for s in out.sections for b in s.blocks]
    assert texts == ["Advocate for the Applicant", "*A v. B* (2019)"]


def test_panel_draft_without_notes_is_the_same_object():
    art = _artifact(BODY)
    assert ln.strip_from_artifact(art) is art


# --- The /pyapi/export endpoint ---------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import core.gateway as gw
    from core import chat_store as cs
    store = cs._SqliteChatHistoryStore(str(tmp_path / "t.db"))
    monkeypatch.setattr(cs, "chat_store", store)
    gw.app.dependency_overrides[gw.require_user_key] = lambda: None
    try:
        yield TestClient(gw.app), store
    finally:
        gw.app.dependency_overrides.pop(gw.require_user_key, None)


def _docx_text(resp) -> str:
    assert resp.status_code == 200, resp.text[:300]
    return "\n".join(p.text for p in Document(io.BytesIO(resp.content)).paragraphs)


def _pdf_markdown(c, monkeypatch, payload: dict) -> str:
    """The Markdown the PDF is rendered from (both PDF paths go through
    core.export.export_document)."""
    import core.export as ex
    seen = {}
    real = ex.export_document

    def spy(*args, **kwargs):
        seen["md"] = kwargs.get("md_text", args[1] if len(args) > 1 else "")
        return real(*args, **kwargs)

    monkeypatch.setattr(ex, "export_document", spy)
    resp = c.post("/pyapi/export", json={**payload, "format": "pdf"})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/pdf")
    return seen["md"]


def test_word_and_pdf_from_text_end_at_the_signature(client, monkeypatch):
    c, _ = client
    word = _docx_text(c.post("/pyapi/export", json={"raw_text": SAMPLE, "format": "docx"}))
    _no_notes(word)
    assert word.rstrip().endswith("Advocate for the Complainant")
    pdf_md = _pdf_markdown(c, monkeypatch, {"raw_text": SAMPLE})
    _no_notes(pdf_md)
    assert pdf_md.rstrip().endswith("Advocate for the Complainant")


def test_saved_turns_use_the_turn_kind(client):
    c, store = client
    research = SEARCH_FAILED + "Section 318 BNS punishes cheating."
    t1 = asyncio.run(store.save_turn("th1", "what is cheating", research, task="Legislation"))
    t2 = asyncio.run(store.save_turn("th1", "draft a complaint", SEARCH_FAILED + BODY,
                                     task="Drafting", primary_artifact_kind="draft"))
    kept = _docx_text(c.post("/pyapi/export", json={"thread_id": "th1", "turn_number": t1}))
    assert "judgment search could not be completed" in kept
    draft = _docx_text(c.post("/pyapi/export", json={"thread_id": "th1", "turn_number": t2}))
    _no_notes(draft)
    assert "Advocate for the Complainant" in draft


def test_panel_download_ends_at_the_signature(client, monkeypatch):
    c, _ = client
    art = _artifact(SAMPLE).model_dump()
    word = _docx_text(c.post("/pyapi/export", json={"artifact": art, "format": "docx"}))
    _no_notes(word)
    assert word.rstrip().endswith("Advocate for the Complainant")
    pdf_md = _pdf_markdown(c, monkeypatch, {"artifact": art})
    _no_notes(pdf_md)
    assert "Advocate for the Complainant" in pdf_md
