"""When the Supreme Court ReAct agent hits its time wall, the searches it
already completed are returned instead of an empty error.

Prod 2026-10-21..27: 1,072 SCI runs, 99 over 60 s, 28 hit the 90 s wall. On
the wall the node returned content="" with an error, synthesis dropped it
with a log line, and a lawyer asking for 2025-26 Supreme Court judgments on
NI Act notices was told that "no verified Supreme Court judgments from that
window are present in the authoritative record" while the index held 54.

The ReAct agent and the LLM are replaced by fakes; the node's streaming,
timeout, source parsing and salvage text are exercised for real.
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import agents.sci_judgment as sj

SEARCH_RESULT = (
    "Found 2 relevant judgment(s):\n\n"
    "**KUNTEGOWDA VS THURUBAIAH** (DB ID: 46399)\n"
    "- Case No: Crl.A. No.-003654-003654 - 2026\n"
    "- Date: 04-08-2026\n"
    "- Bench: HON'BLE MRS. JUSTICE B.V. NAGARATHNA\n"
    "- Judgment By: HON'BLE MRS. JUSTICE B.V. NAGARATHNA\n"
    "- PDF: https://lawttorney.s3.ap-south-1.amazonaws.com/sc_pdfs/46399.pdf\n"
    "- Relevance Score: 31.2\n\n"
    "**SAINABA VS STATE OF KERALA** (DB ID: 46654)\n"
    "- Case No: Crl.A. No.-004640-004640 - 2026\n"
    "- Date: 30-09-2026\n"
    "- Bench: N/A\n"
    "- Judgment By: HON'BLE MR. JUSTICE AHSANUDDIN AMANULLAH\n"
    "- PDF: https://lawttorney.s3.ap-south-1.amazonaws.com/sc_pdfs/46654.pdf\n"
    "- Relevance Score: 28.9"
)


class FakeReActAgent:
    """Yields the trajectory states the real agent would, then either
    finishes with a final answer or hangs until cancelled."""

    def __init__(self, states, hang: bool):
        self.states, self.hang = states, hang

    async def astream(self, inputs, stream_mode="values"):
        msgs = [HumanMessage(content=inputs["messages"][0][1])]
        yield {"messages": list(msgs)}
        for m in self.states:
            msgs.append(m)
            yield {"messages": list(msgs)}
        if self.hang:
            await asyncio.sleep(30)

    async def ainvoke(self, inputs):  # the forced-search retry path; must not run on a timeout
        raise AssertionError("ainvoke must not be called after a timeout")


def _searched_then_hung():
    return [
        AIMessage(content="", tool_calls=[{"name": "search_by_topic", "args": {"query": "x"}, "id": "c1"}]),
        ToolMessage(content=SEARCH_RESULT, tool_call_id="c1"),
        AIMessage(content="", tool_calls=[{"name": "get_case_details", "args": {"db_id": 46399}, "id": "c2"}]),
        ToolMessage(content="## Full Case Details\n\n**Parties**: KUNTEGOWDA VS THURUBAIAH\n...", tool_call_id="c2"),
    ]


@pytest.fixture
def wired(monkeypatch):
    recorded = []
    monkeypatch.setattr(sj, "SCI_REACT_TIMEOUT_S", 0.2)
    monkeypatch.setattr(sj, "get_gemini_flash_full", lambda **k: object())
    monkeypatch.setattr(sj, "_case_name_lookup", lambda q: None)
    import core.metrics as metrics
    monkeypatch.setattr(metrics, "record_agent_error", lambda agent, exc=None, **k: recorded.append((agent, type(exc).__name__)))

    class NoSearch:
        def invoke(self, *a, **k):
            raise AssertionError("forced topic search must not run after a timeout")
    monkeypatch.setattr(sj, "search_by_topic", NoSearch())

    def use(agent):
        monkeypatch.setattr(sj, "create_react_agent", lambda *a, **k: agent)
    return use, recorded


STATE = {"query": "Supreme Court judgments 2025-2026 on NI Act notice", "original_query": "Supreme Court judgments 2025-2026 on NI Act notice",
         "agent_queries": {}, "user_context": "", "user_language": "en", "thread_id": "t1"}


def test_timeout_returns_the_judgments_found_so_far_not_an_error(wired):
    use, recorded = wired
    use(FakeReActAgent(_searched_then_hung(), hang=True))
    out = asyncio.run(sj.sci_judgment_node(dict(STATE)))
    r = out["agent_results"]["SCI_Judgment"]
    assert r.error is None
    assert "cut short" in r.content and "retry" in r.content.lower()
    assert "KUNTEGOWDA VS THURUBAIAH" in r.content and "SAINABA VS STATE OF KERALA" in r.content
    assert "sc_pdfs/46399.pdf" in r.content and "decided 04-08-2026" in r.content
    assert "not exhaustive" in r.content
    assert [s.db_id for s in r.sources] == ["46399", "46654"]
    assert recorded == [("SCI_Judgment", "TimeoutError")]       # prod counter still counts it


def test_timeout_before_any_search_says_so_and_skips_the_forced_search(wired):
    use, recorded = wired
    use(FakeReActAgent([], hang=True))
    out = asyncio.run(sj.sci_judgment_node(dict(STATE)))
    r = out["agent_results"]["SCI_Judgment"]
    assert r.error is None and r.sources == []
    assert "No judgment had been retrieved" in r.content
    assert "not a finding that no Supreme Court judgment exists" in r.content
    assert recorded == [("SCI_Judgment", "TimeoutError")]


def test_a_run_that_finishes_in_time_is_unchanged(wired):
    use, recorded = wired
    states = _searched_then_hung() + [AIMessage(content="### 1. *Kuntegowda v. Thurubaiah*, 2026 INSC 790\nThe Court held ...")]
    use(FakeReActAgent(states, hang=False))
    out = asyncio.run(sj.sci_judgment_node(dict(STATE)))
    r = out["agent_results"]["SCI_Judgment"]
    assert r.error is None
    assert r.content.startswith("### 1. *Kuntegowda v. Thurubaiah*")
    assert "cut short" not in r.content
    assert [s.db_id for s in r.sources] == ["46399", "46654"]
    assert recorded == []


def test_salvage_text_lists_at_most_ten_and_keeps_pdf_links():
    from core.state import SourceMetadata
    srcs = [SourceMetadata(source_type="sci_judgment", title=f"CASE {i} VS STATE", parties=f"CASE {i} VS STATE",
                           db_id=str(i), case_no=f"C.A. {i}/2025", judgment_date=f"0{i % 9 + 1}-01-2025",
                           doc_link=f"https://x/{i}.pdf", agent_name="SCI_Judgment") for i in range(12)]
    text = sj._salvage_after_timeout(srcs, 90)
    assert text.count("\n- ") == 10
    assert "([Judgment PDF](https://x/0.pdf))" in text and "C.A. 0/2025" in text
