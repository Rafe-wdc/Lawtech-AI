"""Attached documents reach the search agents; a failed upload is not answered (2026-11-02).

Prod 2026-11-01 (thread 91bcd70a): an NDPS anticipatory-bail application was
attached to "case laws based on the attached FIR where the accused gets
anticipatory bail". The search agents saw the question only and the answer was
about IPC 406/420 cheating. Prod 2026-11-02 (thread 9e29f471): the same
question was answered with no file on record.

1. core.case_focus reads the attachments into one focus line.
2. The per-agent query rewriter receives it; without attachments its prompt
   is unchanged.
3. Judgment / SCI_Judgment / Newacts web fallbacks receive the file text.
4. An upload that fails with no file saved stops with an error instead of
   answering without the document.
5. Postgres pools test connections before handing them out.
Pure tests, no network.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import agents.orchestrator as orch
import core.case_focus as cf
from core.state import FileContextData

NDPS_TEXT = (
    "APPLICATION FOR ANTICIPATORY BAIL U/S 482 BNSS. Rahata Police Station "
    "Cr. No. 384/2026 u/s 8(c), 20(b)(ii)(B) of the NDPS Act. Ganja found in "
    "the cattle shed. The applicant has not been arrested."
)
FOCUS = ("NDPS Act ss. 8(c), 20(b)(ii)(B); ganja, intermediate quantity; FIR "
         "registered, accused not arrested; anticipatory bail u/s 482 BNSS; Maharashtra")


def _fc(text=NDPS_TEXT):
    return FileContextData(file_names=["fir.pdf"],
                           extracted_texts=[{"name": "fir.pdf", "text": text}])


class _FakeLLM:
    def __init__(self, reply="", exc=None):
        self.reply, self.exc, self.calls = reply, exc, []

    def invoke(self, messages):
        self.calls.append(messages)
        if self.exc:
            raise self.exc
        return AIMessage(content=self.reply)


@pytest.fixture(autouse=True)
def _clear_cache():
    cf._cache.clear()
    yield
    cf._cache.clear()


def _patch_llm(monkeypatch, llm):
    import core.clients as clients
    monkeypatch.setattr(clients, "get_gemini_flash_full", lambda *a, **k: llm)


# ------------------------------------------------------- 1. case focus
def test_focus_reads_the_document_and_normalises_whitespace(monkeypatch):
    llm = _FakeLLM("  NDPS Act ss. 8(c),\n 20(b)(ii)(B); ganja ")
    _patch_llm(monkeypatch, llm)
    assert cf.extract_case_focus(_fc()) == "NDPS Act ss. 8(c), 20(b)(ii)(B); ganja"
    assert "Cr. No. 384/2026" in llm.calls[0][1]["content"]


def test_focus_empty_without_files_or_for_non_legal_documents(monkeypatch):
    llm = _FakeLLM("NONE")
    _patch_llm(monkeypatch, llm)
    assert cf.extract_case_focus(None) == ""
    assert cf.extract_case_focus(FileContextData()) == ""
    assert cf.extract_case_focus(_fc("A recipe for dal.")) == ""


def test_focus_failure_degrades_to_empty(monkeypatch):
    _patch_llm(monkeypatch, _FakeLLM(exc=RuntimeError("503 UNAVAILABLE")))
    assert cf.extract_case_focus(_fc()) == ""


def test_focus_cached_per_document_text(monkeypatch):
    llm = _FakeLLM(FOCUS)
    _patch_llm(monkeypatch, llm)
    assert cf.extract_case_focus(_fc()) == FOCUS
    assert cf.extract_case_focus(_fc()) == FOCUS
    assert len(llm.calls) == 1


def test_focus_input_capped_per_file_and_in_total():
    fc = FileContextData(file_names=["a", "b", "c", "d"], extracted_texts=[
        {"name": n, "text": "x" * 50_000} for n in "abcd"])
    docs = cf._documents_text(fc)
    assert len(docs) <= cf._MAX_TOTAL_CHARS + 10


# ------------------------------------------------- 2. per-agent rewrite
def test_rewrite_prompt_unchanged_without_focus():
    assert orch._rewrite_prompt_template("") is orch.AGENT_QUERY_REWRITE_PROMPT


def test_rewrite_prompt_carries_focus_before_the_user_query():
    t = orch._rewrite_prompt_template(FOCUS)
    assert t.index("{case_focus}") < t.index("User Query: {query}")
    assert t.count("User Query: {query}") == 1


def _capture_rewrite(monkeypatch):
    seen = {}

    def _fake(prompt_value):
        seen["prompt"] = prompt_value.to_string()
        return AIMessage(content=json.dumps({"Judgment": "q1", "Newacts": "q2"}))

    monkeypatch.setattr(orch, "get_gemini_flash", lambda *a, **k: RunnableLambda(_fake))
    return seen


def test_rewrite_llm_receives_the_focus(monkeypatch):
    seen = _capture_rewrite(monkeypatch)
    out = orch._rewrite_queries_for_agents("case laws on the attached FIR",
                                           ["Judgment", "Newacts", "Document"],
                                           "NDPS {braces} ss. 8(c)")
    assert out == {"Judgment": "q1", "Newacts": "q2"}
    assert "NDPS {braces} ss. 8(c)" in seen["prompt"]
    assert "Never replace the document's statute or offence" in seen["prompt"]


def test_rewrite_llm_without_focus_sends_the_old_prompt(monkeypatch):
    seen = _capture_rewrite(monkeypatch)
    orch._rewrite_queries_for_agents("anticipatory bail", ["Judgment", "Newacts"])
    assert "ATTACHED DOCUMENTS" not in seen["prompt"]


# ------------------------------------------- plan node wires focus in
def _plan(monkeypatch, plan, file_context, focus=FOCUS):
    seen = {"focus_calls": 0}

    def _fake_focus(fc):
        seen["focus_calls"] += 1
        return focus

    def _fake_rewrite(q, agents, case_focus=""):
        seen["rewrite"] = (list(agents), case_focus)
        return {a: q for a in agents}

    monkeypatch.setenv("DYNAMIC_ORCHESTRATOR", "0")
    monkeypatch.setattr(cf, "extract_case_focus", _fake_focus)
    monkeypatch.setattr(orch, "_classify_and_plan", lambda *a, **k: plan)
    monkeypatch.setattr(orch, "_extract_user_intent", lambda q, *a, **k: (q, orch.default_intent()))
    monkeypatch.setattr(orch, "_rewrite_queries_for_agents", _fake_rewrite)
    q = "Provide me case laws based on the attached FIR where the accused gets anticipatory bail"
    state = {"query": q, "original_query": q, "user_language": "en",
             "chat_history": [], "messages": [], "file_context": file_context}
    out = asyncio.run(orch.orchestrator_plan_node(state))
    return out, seen


def _fc_dict():
    return {"file_names": ["fir.pdf"], "chromadb_collections": [],
            "extracted_texts": [{"name": "fir.pdf", "text": NDPS_TEXT}]}


def test_plan_with_file_passes_focus_to_the_rewriter(monkeypatch):
    out, seen = _plan(monkeypatch, ("Judgment", ["Judgment", "SCI_Judgment", "Newacts"]), _fc_dict())
    assert "Document" in out["tasks_planned"]
    assert seen["focus_calls"] == 1
    assert seen["rewrite"][1] == FOCUS


def test_plan_without_file_never_extracts_focus(monkeypatch):
    out, seen = _plan(monkeypatch, ("Judgment", ["Judgment", "SCI_Judgment"]), None)
    assert seen["focus_calls"] == 0
    assert seen["rewrite"][1] == ""


def test_document_only_plan_does_not_use_the_focus(monkeypatch):
    out, seen = _plan(monkeypatch, ("Document", ["Document"]), _fc_dict())
    assert out["tasks_planned"] == ["Document"]
    assert "rewrite" not in seen


# ------------------------------------- 3. web fallbacks see the file
@pytest.mark.parametrize("module, calls", [
    ("agents/judgment.py", 4), ("agents/sci_judgment.py", 1), ("agents/newacts.py", 4),
])
def test_web_fallbacks_receive_the_file_text(module, calls):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, module), encoding="utf-8").read()
    starts = [i for i in range(len(src)) if src.startswith("await web_search_fallback(", i)]
    assert len(starts) == calls
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(src)
        kw = src.find("file_context_prefix=_file_prefix", start)
        assert start < kw < end and kw - start < 400, f"call {n} at char {start}"


# ------------------------------------- 4. failed upload is not answered
@pytest.fixture
def chat_client(monkeypatch):
    from fastapi.testclient import TestClient
    import core.file_processor as file_processor
    import core.gateway as gateway
    from core.auth import require_user_key

    state = {"saved": [], "pipeline_ran": False}

    async def _boom(*a, **k):
        raise RuntimeError("OperationalError: the connection is closed")

    async def _load(thread_id):
        return state["saved"]

    async def _pipeline(*a, **k):
        state["pipeline_ran"] = True
        yield 'data: {"type": "done"}\n\n'

    import core.chat_runner as chat_runner
    monkeypatch.setattr(file_processor, "process_files", _boom)
    monkeypatch.setattr(gateway.chat_store, "load_thread_files", _load)
    monkeypatch.setattr(chat_runner, "run_chat_pipeline", _pipeline)
    monkeypatch.setattr(gateway.limiter, "enabled", False, raising=False)
    gateway.app.dependency_overrides[require_user_key] = lambda: None
    gateway.app.state.agent_graph = None
    yield TestClient(gateway.app, raise_server_exceptions=False), state
    gateway.app.dependency_overrides.pop(require_user_key, None)


def _events(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def _pdf_bytes():
    import fitz
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), NDPS_TEXT[:80])
    data = doc.tobytes()
    doc.close()
    return data


def _post_pdf(client):
    return client.post("/pyapi/chat", data={"query": "case laws on the attached FIR"},
                       files=[("files", ("fir.pdf", _pdf_bytes(), "application/pdf"))])


def test_failed_upload_with_nothing_saved_stops_with_an_error(chat_client):
    client, state = chat_client
    ev = _events(_post_pdf(client))
    kinds = [e["type"] for e in ev]
    assert "error" in kinds and kinds[-1] == "done"
    err = next(e for e in ev if e["type"] == "error")
    assert "could not read the file" in err["data"]
    assert ev[-1].get("upload_failed") is True
    assert state["pipeline_ran"] is False


def test_failed_upload_with_a_saved_file_still_answers(chat_client, monkeypatch):
    client, state = chat_client
    import uuid
    import core.gateway as gateway
    fixed = uuid.uuid4()
    monkeypatch.setattr(gateway.uuid, "uuid4", lambda: fixed)
    state["saved"] = [{"batch_id": fixed.hex, "filename": "fir.pdf"}]
    _post_pdf(client)
    assert state["pipeline_ran"] is True


def test_upload_guard_counts_a_failed_lookup_as_nothing_saved(monkeypatch):
    import core.gateway as gateway

    async def _down(thread_id):
        raise RuntimeError("the connection is closed")

    monkeypatch.setattr(gateway.chat_store, "load_thread_files", _down)
    assert asyncio.run(gateway._batch_has_saved_files("t", "b")) is False


# ------------------------------------------- 5. pools check connections
def test_postgres_pools_check_connections_before_use():
    from core import chat_store, checkpointer
    assert "check=ConnectionPool.check_connection" in inspect.getsource(chat_store._PostgresChatHistoryStore._get_pool)
    assert "check=AsyncConnectionPool.check_connection" in inspect.getsource(checkpointer)
    from psycopg_pool import AsyncConnectionPool, ConnectionPool
    assert callable(ConnectionPool.check_connection) and callable(AsyncConnectionPool.check_connection)
