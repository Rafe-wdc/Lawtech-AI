"""Front-door side of background runs (Background_Runs_Implementation_Plan.md
sections 6 to 8): queue a run, stream it to the app, let the app reattach.

The front door never generates. `start_run` stages the uploads, inserts the
run row and returns an SSE response that only *reads* the run's stored
events (`tail_run`). Closing the app ends that read; the runner keeps going.

Every forwarded event carries its `seq`. `GET /pyapi/runs/{id}/stream?after=N`
replays events after N and then streams live ones, using the same query as
the live path, so a reconnect never repeats or skips text.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from typing import AsyncIterator

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from core.logger import get_logger
from core.metrics import METRICS
from core.run_store import ActiveRunExists, RunRow, TERMINAL_STATUSES, get_run_store
from core.user_auth import Identity, may_access_run

log = get_logger("RunApi")

SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
WATCHER_TOUCH_S = 5.0          # presence heartbeat of an open tail (TTL is RUN_WATCHER_TTL_S)
PING_EVERY_S = 15.0            # SSE comment so proxies keep an idle stream open
READ_BATCH = 500
_RUNNER_CHECK_TTL_S = 5.0
_runner_seen = {"at": -1e9, "ok": False}

BUSY_DETAIL = "A response is already being generated for this thread"


def background_runs_enabled() -> bool:
    from core import settings as S
    return bool(S.BACKGROUND_RUNS)


def _store():
    return get_run_store()


def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev)}\n\n"


def sse_response(gen: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream", headers=SSE_HEADERS)


async def runner_available() -> bool:
    """Is a runner alive (liveness row within RUN_WORKER_STALE_S)? Cached for
    a few seconds per web worker. When none is, requests are answered inline
    as before the flag existed, and this is logged loudly: users still get
    answers while ops see the runner is down."""
    now = time.monotonic()
    if now - _runner_seen["at"] < _RUNNER_CHECK_TTL_S:
        return _runner_seen["ok"]
    try:
        ok = bool(await _store().aio.live_workers())
    except Exception as e:
        log.error("Runner check failed; answering inline", error=str(e)[:200])
        ok = False
    if not ok:
        log.error("runner_unavailable_fallback: no runner seen; answering inline")
    _runner_seen.update(at=now, ok=ok)
    return ok


async def runner_available_counted() -> bool:
    ok = await runner_available()
    if not ok:
        METRICS["background_runs_inline_fallback_total"].inc()
    return ok


def _busy_payload(run: RunRow) -> dict:
    return {"detail": BUSY_DETAIL, "active_run_id": run.run_id, "status": run.status,
            "last_seq": run.last_seq, "thread_id": run.thread_id}


async def conflict_response(thread_id: str, endpoint: str = "") -> JSONResponse | None:
    """409 when the thread already has a queued or running run (the app
    attaches to `active_run_id`), else None."""
    try:
        run = await _store().aio.active_run(thread_id)
    except Exception as e:
        log.warning("Active-run check failed; letting the request through",
                    thread_id=thread_id[:12], error=str(e)[:200])
        return None
    if run is None:
        return None
    METRICS["background_runs_busy_total"].labels(endpoint=endpoint).inc()
    log.info("Thread busy: request answered 409", thread_id=thread_id[:12],
             active_run=run.run_id[:12], status=run.status)
    return JSONResponse(status_code=409, content=_busy_payload(run))


def _remove_paths(paths) -> None:
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def _stage_uploads(run_id: str, file_tuples: list, page_counts: dict,
                   temp_paths: list) -> tuple[list[dict], dict, str]:
    """Move the request's validated temp uploads into a folder the run owns
    (RUN_STAGING_ROOT/<run_id>/); the request's own temp files would be gone
    by the time the runner reads them. The runner deletes the folder when
    the run ends; process_files copies what it keeps into uploads/."""
    from core import settings as S
    d = os.path.join(S.RUN_STAGING_ROOT, run_id)
    os.makedirs(d, exist_ok=True)
    files, counts = [], {}
    for i, (tmp, name, size) in enumerate(file_tuples):
        dest = os.path.join(d, f"{i:02d}{os.path.splitext(tmp)[1]}")
        shutil.move(tmp, dest)
        files.append({"path": dest, "name": name, "size": int(size)})
        if tmp in page_counts:
            counts[dest] = page_counts[tmp]
    _remove_paths(temp_paths)          # anything not accepted is already deleted; belt and braces
    return files, counts, d


def staging_dir(run_id: str) -> str:
    from core import settings as S
    return os.path.join(S.RUN_STAGING_ROOT, run_id)


async def start_run(
    *,
    endpoint: str,
    thread_id: str,
    is_first_turn: bool,
    ident: Identity,
    request_fields: dict,
    integration_token: str | None = None,
    uploads: tuple | None = None,
) -> StreamingResponse | JSONResponse:
    """Queue the run and return the SSE response that streams it."""
    store = _store()
    run_id = str(uuid.uuid4())
    request = dict(request_fields)
    request["is_first_turn"] = bool(is_first_turn)
    staged = None
    temp_paths = list(uploads[2]) if uploads else []
    try:
        if uploads is not None:
            file_tuples, page_counts, _ = uploads
            files, counts, staged = await asyncio.to_thread(
                _stage_uploads, run_id, file_tuples, page_counts, temp_paths)
            request["files"] = files
            request["page_counts"] = counts
        run = await store.aio.create_run(
            run_id=run_id, thread_id=thread_id, endpoint=endpoint, request=request,
            user_id=ident.user_id, identity_source=ident.source if ident.user_id else "",
            secrets={"integration_token": integration_token} if integration_token else None,
        )
    except ActiveRunExists as e:
        if staged:
            shutil.rmtree(staged, ignore_errors=True)
        METRICS["background_runs_busy_total"].labels(endpoint=endpoint).inc()
        log.info("Thread busy: request answered 409", thread_id=thread_id[:12],
                 active_run=e.run.run_id[:12])
        return JSONResponse(status_code=409, content=_busy_payload(e.run))
    except BaseException:
        if staged:
            shutil.rmtree(staged, ignore_errors=True)
        _remove_paths(temp_paths)
        raise
    METRICS["background_runs_started_total"].labels(endpoint=endpoint).inc()
    log.info("Background run queued", run_id=run_id[:12], thread_id=thread_id[:12],
             endpoint=endpoint, user=(ident.user_id or "")[:12], identity=ident.source,
             files=len(request.get("files") or []))
    preface = [_sse({"type": "thread_id", "data": thread_id}),
               _sse({"type": "run_id", "data": run_id, "thread_id": thread_id, "seq": 0})]
    return sse_response(tail_run(run, after=0, preface=preface))


def _remove_watcher_later(store, run_id: str, watcher_id: str) -> None:
    # From a finally that may be running under cancellation: no await.
    try:
        asyncio.get_running_loop().run_in_executor(None, store.remove_watcher, run_id, watcher_id)
    except RuntimeError:
        pass


async def tail_run(run: RunRow, *, after: int = 0,
                   preface: list[str] | None = None) -> AsyncIterator[str]:
    """Stream a run's stored events with seq > `after`, then new ones as they
    are stored, until its terminal `done`. Disconnecting only ends this read."""
    from core.run_bus import get_bus
    store = _store()
    bus = get_bus(store)
    run_id = run.run_id
    for p in preface or ():
        yield p
    watcher_id = uuid.uuid4().hex
    last = int(after)
    sub = bus.subscribe(run_id)
    last_touch = -1e9
    last_out = time.monotonic()
    tail_since = store.now()
    delay_hist = METRICS["background_run_event_delay_seconds"]
    try:
        while True:
            now = time.monotonic()
            if now - last_touch >= WATCHER_TOUCH_S:
                await store.aio.touch_watcher(run_id, watcher_id)
                last_touch = now
            events = await store.aio.read_events(run_id, last, READ_BATCH)
            for e in events:
                yield e.sse()
                last = e.seq
                last_out = time.monotonic()
                if e.created_at is not None and e.created_at >= tail_since:
                    # live delivery only (replayed history would skew it)
                    delay_hist.observe(max(0.0, (store.now() - e.created_at).total_seconds()))
                if e.type == "done":
                    return
            if events:
                continue
            if not await sub.wait(bus.fallback_s()):
                cur = await store.aio.get_run(run_id)
                if cur is None:
                    yield _sse({"type": "error", "data": "This answer is no longer available."})
                    return
                if cur.status in TERMINAL_STATUSES and cur.last_seq <= last:
                    return
            if time.monotonic() - last_out >= PING_EVERY_S:
                yield ": ping\n\n"
                last_out = time.monotonic()
    except (asyncio.CancelledError, GeneratorExit):
        log.info("Run stream closed by the client; the run continues",
                 run_id=run_id[:12], last_seq=last)
        raise
    finally:
        bus.unsubscribe(run_id, sub)
        _remove_watcher_later(store, run_id, watcher_id)


async def owned_run(run_id: str, ident: Identity, *, admin: bool = False) -> RunRow:
    """The run, if `ident` may see it; 404 otherwise (also for other users'
    runs, so run ids cannot be probed)."""
    run = await _store().aio.get_run(run_id)
    if run is None or not may_access_run(run.user_id, ident, admin=admin):
        raise HTTPException(status_code=404, detail="Run not found")
    return run


async def thread_run(thread_id: str, ident: Identity, *, admin: bool = False) -> RunRow:
    store = _store()
    run = await store.aio.active_run(thread_id) or await store.aio.latest_run(thread_id)
    if run is None or not may_access_run(run.user_id, ident, admin=admin):
        raise HTTPException(status_code=404, detail="No run for this thread")
    return run


async def cancel_run(run_id: str, ident: Identity, *, admin: bool = False) -> JSONResponse:
    """Stop button. The run turns terminal at once, so the thread is free for
    the next question immediately; the runner's next write hits the fence
    and its pipeline is cancelled. 409 when the run had already finished."""
    run = await owned_run(run_id, ident, admin=admin)
    cancelled = await _store().aio.cancel(run.run_id)
    if cancelled is None:
        cur = await _store().aio.get_run(run.run_id)
        return JSONResponse(status_code=409, content={
            "detail": "This answer has already finished",
            "run_id": run.run_id, "status": cur.status if cur else run.status})
    return JSONResponse(content={"run_id": cancelled.run_id, "status": cancelled.status,
                                 "thread_id": cancelled.thread_id})


async def health() -> dict:
    """Runner liveness and queue sizes, for /pyapi/health/runs."""
    from core import settings as S
    from core.run_bus import get_bus
    store = _store()
    try:
        counts = await store.aio.counts()
        workers = await store.aio.live_workers()
        status = "ok" if workers or not S.BACKGROUND_RUNS else "no_runner"
    except Exception as e:
        return {"status": "error", "enabled": bool(S.BACKGROUND_RUNS), "detail": str(e)[:120]}
    return {
        "status": status,
        "enabled": bool(S.BACKGROUND_RUNS),
        "backend": "postgresql" if store.pg else "sqlite",
        "runners": len(workers),
        "capacity": sum(w["concurrency"] for w in workers),
        "running_here": sum(w["running"] for w in workers),
        "listener_connected": get_bus(store).connected,
        **counts,
    }


async def prometheus_lines() -> str:
    """Queue gauges in Prometheus text format, read from the run store at
    scrape time (no single process holds these numbers)."""
    try:
        c = await _store().aio.counts()
        runners = len(await _store().aio.live_workers())
    except Exception as e:
        log.warning("Run gauges unavailable", error=str(e)[:120])
        return ""
    rows = [
        ("lawtech_background_runs_queued", "Background runs waiting for a runner", c["queued"]),
        ("lawtech_background_runs_running", "Background runs being generated", c["running"]),
        ("lawtech_background_runs_outbox_pending", "Run-complete messages not yet delivered to Node", c["outbox_pending"]),
        ("lawtech_background_runs_outbox_dead", "Run-complete messages Node never accepted", c["outbox_dead"]),
        ("lawtech_background_runners_alive", "Runner processes seen recently", runners),
    ]
    return "".join(f"# HELP {n} {h}\n# TYPE {n} gauge\n{n} {v}\n" for n, h, v in rows)


async def shutdown() -> None:
    from core.run_bus import shutdown as bus_shutdown
    await bus_shutdown()
