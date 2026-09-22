"""Per-plan page limit for Word (.docx) uploads on POST /pyapi/chat.

A .docx has no stored pages. The gateway uses the count Word saved in
docProps/app.xml when present, else converts the file to PDF with LibreOffice
and counts those pages, else counts it as 1 page. The pages go against the
plan's page budget per chat (First Justice Plan 60, Basic 150). The converter
is simulated here.
"""

import io
import os
import subprocess
import zipfile

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import fitz
import pytest
from docx import Document
from fastapi.testclient import TestClient

import core.file_processor as file_processor
import core.gateway as gateway
from core.auth import require_user_key

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx(saved_pages=None) -> bytes:
    buf = io.BytesIO()
    d = Document()
    d.add_paragraph("That the Respondent denies each and every allegation.")
    d.save(buf)
    if saved_pages is None:
        return buf.getvalue()
    # Rewrite docProps/app.xml with a <Pages> count, as Word does on save.
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "docProps/app.xml":
                data = (b'<?xml version="1.0"?><Properties xmlns="http://schemas.openxmlformats.org/'
                        b'officeDocument/2006/extended-properties"><Pages>%d</Pages></Properties>' % saved_pages)
            z.writestr(item, data)
    return out.getvalue()


@pytest.fixture
def converter(monkeypatch):
    """Simulated LibreOffice: writes a PDF with `state["pages"]` pages."""
    state = {"pages": 5, "calls": 0, "installed": True}

    def _run(cmd, **kwargs):
        state["calls"] += 1
        out_dir = cmd[cmd.index("--outdir") + 1]
        stem = os.path.splitext(os.path.basename(cmd[-1]))[0]
        doc = fitz.open()
        for _ in range(state["pages"]):
            doc.new_page()
        doc.save(os.path.join(out_dir, stem + ".pdf"))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr(gateway, "_find_soffice",
                        lambda: "soffice" if state["installed"] else None)
    return state


@pytest.fixture
def client(monkeypatch, converter):
    async def _stub_process_files(*args, **kwargs):
        raise RuntimeError("stub: upload checks passed")

    monkeypatch.setattr(file_processor, "process_files", _stub_process_files)
    monkeypatch.setattr(gateway.limiter, "enabled", False, raising=False)
    gateway.app.dependency_overrides[require_user_key] = lambda: None
    gateway.app.state.agent_graph = None
    yield TestClient(gateway.app, raise_server_exceptions=False)
    gateway.app.dependency_overrides.pop(require_user_key, None)


def _post(client, data_bytes, plan="First Justice Plan", name="notice.docx"):
    data = {"query": "read this"}
    if plan:
        data["plan"] = plan
    return client.post("/pyapi/chat", data=data, files=[("files", (name, data_bytes, DOCX))])


@pytest.mark.parametrize("plan,budget", [("First Justice Plan", 60), ("Basic", 150)])
def test_docx_over_the_page_budget_is_rejected(client, converter, plan, budget):
    converter["pages"] = budget + 1
    r = _post(client, _docx(), plan)
    assert r.status_code == 403
    assert r.json()["message"] == (
        f"notice.docx has {budget + 1} pages. Your plan ({plan}) allows up to "
        f"{budget} pages per chat, and this chat has {budget} pages left."
    )


@pytest.mark.parametrize("plan,budget", [("First Justice Plan", 60), ("Basic", 150)])
def test_docx_using_the_whole_budget_is_accepted(client, converter, plan, budget):
    converter["pages"] = budget
    assert _post(client, _docx(), plan).status_code == 200


def test_page_count_saved_by_word_is_used_without_converting(client, converter):
    converter["pages"] = 99             # would block if the converter ran
    assert _post(client, _docx(saved_pages=12)).status_code == 200
    assert _post(client, _docx(saved_pages=70)).status_code == 403
    assert converter["calls"] == 0


def test_stale_python_docx_page_count_is_ignored(client, converter):
    # python-docx files say "1 page, 0 words" whatever their length.
    converter["pages"] = 61
    assert _post(client, _docx()).status_code == 403
    assert converter["calls"] == 1


def test_page_count_unknown_counts_as_one_page(client, converter):
    converter["installed"] = False
    assert _post(client, _docx()).status_code == 200


def test_no_plan_skips_the_conversion(client, converter):
    converter["pages"] = 99
    assert _post(client, _docx(), plan="").status_code == 200
    assert converter["calls"] == 0


def test_saved_page_count_reader():
    import tempfile
    for saved, want in ((7, 7), (None, None)):
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            f.write(_docx(saved_pages=saved))
        try:
            assert gateway._docx_saved_page_count(f.name) == want
        finally:
            os.unlink(f.name)
