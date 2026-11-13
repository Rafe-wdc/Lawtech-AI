"""PDF downloads: the page box of the Markdown renderer (core/export.py) and
the draft panel's PDF, which is its Word file converted by LibreOffice
(core/gateway.py _export_artifact)."""

from __future__ import annotations

import asyncio

import fitz
import pytest

from core.export import generate_pdf

A4 = (0, 0, 595, 842)


def _box(page) -> tuple:
    return tuple(round(x) for x in page.mediabox)


def test_pdf_pages_are_a4_and_no_line_is_cut_off():
    # 2026-11-13: every page was cropped to the text area while the text
    # started at the page edge; the first inch of each line was cut off and
    # "Place: Rampur" did not appear at all.
    md = ("## FACTS\n\n1. The Applicant was arrested on 3 March 2026 by the police of "
          "Rampur and has been in custody since then.\n\nPlace: Rampur\n\nDate: 12 November 2026")
    doc = fitz.open(stream=generate_pdf("Bail Application", md).getvalue(), filetype="pdf")
    page = doc[0]
    assert _box(page) == A4
    text = page.get_text()
    assert "Place: Rampur" in text and "FACTS" in text and "The Applicant was arrested" in text
    words = page.get_text("words")
    assert min(w[0] for w in words) >= 70 and max(w[2] for w in words) <= 595 - 70


def _draft() -> dict:
    from core.draft_blocks import (
        Artifact, HeadingBlock, ParaBlock, RowBlock, Section, TextBlock, TitleBlock,
    )
    return Artifact(artifact_id="art_pdf", title="Bail Application", doc_type="bail", sections=[
        Section(id="s1", heading="Cause title", blocks=[
            TitleBlock(id="s1.b1", text="IN THE COURT OF SESSIONS AT PUNE"),
            RowBlock(id="s1.b2", left=["[[Name of Applicant]]"], right=[".....Applicant"]),
        ]),
        Section(id="s2", heading="Facts", blocks=[
            HeadingBlock(id="s2.b1", text="FACTS"),
            ParaBlock(id="s2.b2", number=1, text="The Applicant was arrested on [[Date of Arrest]]."),
            TextBlock(id="s2.b3", text="Place: Pune", align="left"),
        ]),
    ]).model_dump()


def _download(raw: dict, fmt: str):
    import core.gateway as gw

    async def body(resp):
        return b"".join([c async for c in resp.body_iterator])

    resp = gw._export_artifact(raw, fmt, None)
    return resp, asyncio.run(body(resp))


def test_panel_pdf_is_the_word_file_converted(monkeypatch):
    import core.gateway as gw
    seen = {}

    def convert(data: bytes):
        seen["docx"] = data
        return b"%PDF-1.7 converted"

    monkeypatch.setattr(gw, "_docx_to_pdf", convert)
    resp, data = _download(_draft(), "pdf")
    assert data == b"%PDF-1.7 converted" and seen["docx"][:2] == b"PK"      # a .docx went in
    assert resp.media_type == "application/pdf"
    assert resp.headers["content-disposition"].endswith('_v1.pdf"')
    resp, data = _download(_draft(), "docx")                                # Word: unchanged
    assert data[:2] == b"PK" and resp.headers["content-disposition"].endswith('_v1.docx"')


def test_panel_pdf_falls_back_to_the_markdown_renderer(monkeypatch):
    import core.gateway as gw
    monkeypatch.setattr(gw, "_docx_to_pdf", lambda data: None)
    resp, data = _download(_draft(), "pdf")
    page = fitz.open(stream=data, filetype="pdf")[0]
    assert resp.media_type == "application/pdf" and _box(page) == A4
    assert "Place: Pune" in page.get_text()


def test_a_script_without_a_server_font_uses_the_markdown_renderer(monkeypatch):
    import shutil
    import subprocess
    import core.gateway as gw

    class _Done:
        def __init__(self, stdout):
            self.stdout = stdout

    def fc_list(cmd, **kw):            # fonts for Latin and Gujarati only
        return _Done("Liberation Serif\n" if cmd[1] in (":charset=0a85", ":charset=0041") else "")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/fc-list" if name == "fc-list" else None)
    monkeypatch.setattr(subprocess, "run", fc_list)
    monkeypatch.setattr(gw, "_script_font_cache", {})
    assert gw._fonts_cover("IN THE COURT OF SESSIONS AT PUNE")
    assert gw._fonts_cover("અરજદાર")
    assert not gw._fonts_cover("आवेदक को रामपुर पुलिस ने गिरफ्तार किया")

    monkeypatch.setattr(gw, "_fonts_cover", lambda text: False)
    monkeypatch.setattr(gw, "_docx_to_pdf", lambda data: pytest.fail("LibreOffice used"))
    resp, data = _download(_draft(), "pdf")
    assert resp.media_type == "application/pdf" and data[:4] == b"%PDF"


def test_export_endpoint_serves_the_panel_pdf(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import core.gateway as gw
    from core import artifact_store as mod
    from core import chat_store as cs
    from core.draft_blocks import Artifact
    monkeypatch.setattr(cs, "chat_store", cs._SqliteChatHistoryStore(str(tmp_path / "t.db")))
    s = mod._Store()
    monkeypatch.setattr(mod, "artifact_store", s)
    asyncio.run(s.save(Artifact.model_validate(_draft()), thread_id="t1", source=mod.SOURCE_DRAFT))
    monkeypatch.setattr(gw, "_docx_to_pdf", lambda data: b"%PDF-1.7 converted")
    gw.app.dependency_overrides[gw.require_user_key] = lambda: None
    try:
        r = TestClient(gw.app).post("/pyapi/export", json={"artifact_id": "art_pdf", "format": "pdf"})
        assert r.status_code == 200 and r.content == b"%PDF-1.7 converted"
    finally:
        gw.app.dependency_overrides.pop(gw.require_user_key, None)


def _soffice():
    import core.gateway as gw
    return gw._find_soffice()


@pytest.mark.skipif(not _soffice(), reason="LibreOffice not installed")
def test_libreoffice_pdf_keeps_the_word_layout():
    resp, data = _download(_draft(), "pdf")
    page = fitz.open(stream=data, filetype="pdf")[0]
    assert _box(page) == A4
    text = page.get_text()
    assert "IN THE COURT OF SESSIONS AT PUNE" in text and "Place: Pune" in text
    words = {w[4]: w for w in page.get_text("words")}
    assert words[".....Applicant"][2] > 595 * 0.75        # the party label is on the right
    assert words["FACTS"][0] > 595 * 0.3                  # the heading is centred
