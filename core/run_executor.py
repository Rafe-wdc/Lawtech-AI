"""Runs a claimed background run through the unchanged chat pipeline.

The runner process builds its own checkpointer and agent graph exactly as the
web tier's lifespan does, then for each run calls the same generators the
endpoints call inline:

  /pyapi/chat           core.gateway._chat_request_events  (file processing + pipeline)
  /pyapi/search/stream  core.gateway._search_stream_events (pipeline)

with emit_thread_id=False (the front door already sent it) and run_id set,
which makes the turn save idempotent if the run is ever retried.
"""

from __future__ import annotations

import shutil
import threading
from typing import AsyncIterator

from core.logger import get_logger
from core.run_store import Claim

log = get_logger("RunExecutor")


def _warm_up() -> None:
    """Same warm-up as gunicorn's post_worker_init: open the search and model
    connections before the first run so it does not pay the cold start
    against the request deadline (BUG-02). Best effort, in a thread."""
    steps = (
        ("search backend", lambda: __import__("core.clients", fromlist=["x"]).get_es_client().info()),
        ("Gemini Flash", lambda: __import__("core.clients", fromlist=["x"]).get_gemini_flash().invoke("ok")),
        ("drafting LLM", lambda: __import__("core.clients", fromlist=["x"]).get_drafting_llm()),
        ("embeddings", lambda: __import__("core.clients", fromlist=["x"]).get_qa_embeddings().embed_query("ok")),
    )
    for name, fn in steps:
        try:
            fn()
            log.info("Runner warm-up ok", step=name)
        except Exception as e:
            log.warning("Runner warm-up skipped", step=name, error=str(e)[:160])


class PipelineExecutor:
    def __init__(self, agent_graph, pg_pool=None):
        self.agent_graph = agent_graph
        self._pg_pool = pg_pool

    @classmethod
    async def create(cls, *, warm_up: bool = True) -> "PipelineExecutor":
        from core.checkpointer import create_checkpointer
        from core.graph import compile_graph
        checkpointer, pool = await create_checkpointer()
        graph = compile_graph(checkpointer=checkpointer)
        try:
            from tools.shared.newacts_vector import warm_in_background
            warm_in_background()
        except Exception as e:
            log.warning("Newacts embedding warm-up not started", error=str(e)[:160])
        if warm_up:
            threading.Thread(target=_warm_up, name="runner-warmup", daemon=True).start()
        return cls(graph, pool)

    def __call__(self, claim: Claim) -> AsyncIterator[str]:
        return self._events(claim)

    async def _events(self, claim: Claim) -> AsyncIterator[str]:
        from core import gateway as gw
        run = claim.run
        req = run.request or {}
        common = dict(
            query=req.get("query", ""),
            thread_id=run.thread_id,
            is_first_turn=bool(req.get("is_first_turn")),
            preferred_language=req.get("preferred_language"),
            integration_token=(claim.secrets or {}).get("integration_token"),
            cite_appendix=req.get("cite_appendix"),
            regenerate_of=req.get("regenerate_of"),
            user_id=run.user_id or None,
            run_id=run.run_id,
            emit_thread_id=False,
        )
        if run.endpoint == "/pyapi/chat":
            files = [(f["path"], f["name"], int(f.get("size") or 0)) for f in req.get("files") or []]
            gen = gw._chat_request_events(
                self.agent_graph,
                file_tuples=files,
                page_counts=req.get("page_counts") or {},
                rejected_files=req.get("rejected_files") or [],
                force_ocr=req.get("force_ocr"),
                **common,
            )
        elif run.endpoint == "/pyapi/search/stream":
            gen = gw._search_stream_events(self.agent_graph, **common)
        else:
            raise ValueError(f"unknown run endpoint {run.endpoint!r}")
        try:
            async for chunk in gen:
                yield chunk
        finally:
            await gen.aclose()

    async def cleanup(self, claim: Claim) -> None:
        """Delete the run's staged uploads once it has ended."""
        from core.run_api import staging_dir
        shutil.rmtree(staging_dir(claim.run.run_id), ignore_errors=True)

    async def close(self) -> None:
        if self._pg_pool is not None:
            await self._pg_pool.close()
        try:
            from core.chat_store import chat_store
            pool = getattr(chat_store, "_pool", None)
            if pool is not None:
                pool.close()
        except Exception:
            pass
