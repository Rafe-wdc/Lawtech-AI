"""Long model calls keep the SSE stream alive with a heartbeat.

A Claude section pair can run 60-120 s with nothing sent; proxies with a
shorter idle limit drop the connection. bounded_wait_for emits a
{"type": "heartbeat"} custom event every HEARTBEAT_EVERY_S while it waits,
and the chat stream turns it into an SSE comment line.
"""

import asyncio

import core.deadline as deadline


def test_heartbeats_are_written_while_a_call_runs(monkeypatch):
    sent = []
    monkeypatch.setattr(deadline, "_stream_writer_or_none", lambda: sent.append)
    monkeypatch.setattr(deadline, "HEARTBEAT_EVERY_S", 0.05)

    async def slow():
        await asyncio.sleep(0.32)
        return "done"

    assert asyncio.run(deadline.bounded_wait_for(slow(), local_timeout=5)) == "done"
    assert 4 <= len(sent) <= 7 and all(e == {"type": "heartbeat"} for e in sent)


def test_heartbeat_stops_when_the_call_ends(monkeypatch):
    sent = []
    monkeypatch.setattr(deadline, "_stream_writer_or_none", lambda: sent.append)
    monkeypatch.setattr(deadline, "HEARTBEAT_EVERY_S", 0.05)

    async def run():
        async def quick():
            return 1
        await deadline.bounded_wait_for(quick(), local_timeout=5)
        await asyncio.sleep(0.2)          # nothing may arrive after the call
    asyncio.run(run())
    assert sent == []


def test_heartbeat_stops_on_timeout(monkeypatch):
    sent = []
    monkeypatch.setattr(deadline, "_stream_writer_or_none", lambda: sent.append)
    monkeypatch.setattr(deadline, "HEARTBEAT_EVERY_S", 0.05)

    async def run():
        async def hang():
            await asyncio.sleep(10)
        try:
            await deadline.bounded_wait_for(hang(), local_timeout=0.12)
        except asyncio.TimeoutError:
            pass
        n = len(sent)
        await asyncio.sleep(0.2)
        return n
    n = asyncio.run(run())
    assert len(sent) == n                 # no beats after the timeout


def test_outside_a_streamed_graph_nothing_is_written():
    async def quick():
        return "x"
    assert asyncio.run(deadline.bounded_wait_for(quick(), local_timeout=5)) == "x"


def test_chat_stream_turns_heartbeat_into_an_sse_comment():
    import inspect
    import core.chat_runner as cr
    src = inspect.getsource(cr.run_chat_pipeline)
    assert 'kind == "heartbeat"' in src and r'yield ": ping\n\n"' in src
