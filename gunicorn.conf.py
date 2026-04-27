workers = 16
worker_class = "uvicorn.workers.UvicornWorker"
bind = "0.0.0.0:5001"
timeout = 300
graceful_timeout = 30
keepalive = 5
accesslog = "/root/v2_multi_agent/logs/access.log"
errorlog = "/root/v2_multi_agent/logs/error.log"
daemon = True
preload_app = True


# ── Worker warm-up ────────────────────────────────────────────────────────
# preload_app=True copies imported modules into each forked worker, but
# open network sockets cannot be shared across forks. The first drafting
# request to a fresh worker therefore pays the full DNS + TLS handshake +
# auth cost for OpenSearch and Google GenAI synchronously — and on a fresh
# deploy this can push the pipeline past the request deadline, which causes
# the orchestrator to silently fall back to the Judgment-only synthesis
# path (BUG-02 manifested as a case-law summary instead of a draft).
#
# CRITICAL: post_worker_init runs BEFORE the worker binds its event loop.
# Doing the warm-up synchronously here would prevent the worker from
# accepting traffic for the duration of the warm-up. With 16 workers
# warming in parallel, the entire server is unreachable for ~20s after
# every deploy — long enough to break health checks and cause TLS
# handshake timeouts on inbound requests.
#
# Solution: spawn a daemon thread for the warm-up. The worker proceeds to
# bind its socket and serve traffic immediately; warm-up runs concurrently
# in the background. Worst case (warm-up not done before first real
# request), the request pays the original cold-start cost — i.e. we
# degrade to pre-warm-up behaviour, never worse.
def post_worker_init(worker):  # pragma: no cover  — gunicorn-only hook
    import logging
    import threading
    log = logging.getLogger("gunicorn.error")

    def _warmup(pid):
        log.info("[warmup] worker pid=%s starting (background thread)", pid)

        # 1. Search backend (AWS OpenSearch) — DNS + TLS handshake + basic-auth
        try:
            from core.clients import get_es_client
            get_es_client().info()
            log.info("[warmup]   search backend OK")
        except Exception as e:
            log.warning("[warmup]   search backend skipped: %s", e)

        # 2. Gemini Flash channel — auth + first-call latency
        #    Tiny prompt; cost is fractions of a cent per worker per restart.
        try:
            from core.clients import get_gemini_flash
            get_gemini_flash().invoke("ok")
            log.info("[warmup]   Gemini Flash OK")
        except Exception as e:
            log.warning("[warmup]   Gemini Flash skipped: %s", e)

        # 3. Drafting LLM — instantiates the singleton so first real call
        #    doesn't pay model-init cost.
        try:
            from core.clients import get_drafting_llm
            get_drafting_llm()
            log.info("[warmup]   drafting LLM ready")
        except Exception as e:
            log.warning("[warmup]   drafting LLM skipped: %s", e)

        # 4. Embeddings — HF model is already in-memory via preload_app,
        #    but a single embed_query primes any lazy CUDA/MPS state and
        #    confirms health.
        try:
            from core.clients import get_qa_embeddings
            get_qa_embeddings().embed_query("ok")
            log.info("[warmup]   embeddings OK")
        except Exception as e:
            log.warning("[warmup]   embeddings skipped: %s", e)

        log.info("[warmup] worker pid=%s ready", pid)

    t = threading.Thread(
        target=_warmup, args=(worker.pid,),
        name=f"warmup-{worker.pid}", daemon=True,
    )
    t.start()
    log.info("[warmup] worker pid=%s warm-up thread launched (non-blocking)",
             worker.pid)
