# =============================================================================
# Gunicorn config — OPTIONAL deployment path.
#
# The canonical prod runner is uvicorn (see start.sh / DEPLOYMENT.md). This
# file exists so that a multi-worker gunicorn deploy can reuse the
# post_worker_init warm-up below (which fixes BUG-02 — case-law summary
# returned instead of a draft because the first request to a cold worker
# missed the orchestrator deadline).
#
# All paths/counts are env-driven so the file is portable across machines.
#   GUNICORN_WORKERS     default 1 (matches single-worker uvicorn deploy)
#   HOST / PORT          server bind  (defaults 0.0.0.0:5001)
#   GUNICORN_ACCESSLOG   default "-" (stdout) — pipe to systemd / logrotate
#   GUNICORN_ERRORLOG    default "-" (stderr)
# =============================================================================
import os

workers = int(os.getenv("GUNICORN_WORKERS", "1"))
worker_class = "uvicorn.workers.UvicornWorker"
bind = f"{os.getenv('HOST', '0.0.0.0')}:{os.getenv('PORT', '5001')}"
timeout = int(os.getenv("GUNICORN_TIMEOUT", "300"))
graceful_timeout = 30
# keepalive: idle-connection timeout between requests on a kept-alive
# socket. Original 5s was too short — SSE streams take 30–120 s and the
# httpx test client (and real browsers) hold the connection open between
# turns with 0–3 s think time. 5 s meant the server closed many
# inter-request connections, forcing reconnects and surfacing as
# `ConnectError` under sustained load. 75 s safely covers think time plus
# slack while still freeing dead idle connections.
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "75"))
# Listen backlog: kernel-level queue of pending TCP connections waiting
# for accept(). Original implicit default of 2048 was the bottleneck
# under bursty load (kernel dropped SYNs → client `All connection
# attempts failed`). Bump to match sysctl somaxconn=4096.
backlog = int(os.getenv("GUNICORN_BACKLOG", "4096"))
# Recycle each worker after N requests to bound slow memory growth from
# PDF processing + Gemini file uploads. Jitter prevents synchronized
# restart storms when all workers cross the threshold at the same time.
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "100"))
accesslog = os.getenv("GUNICORN_ACCESSLOG", "-")
errorlog = os.getenv("GUNICORN_ERRORLOG", "-")
daemon = os.getenv("GUNICORN_DAEMON", "false").lower() in ("1", "true", "yes", "on")
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


# ── Prometheus multiproc cleanup ──────────────────────────────────────────
# When PROMETHEUS_MULTIPROC_DIR is set, each worker writes its own counter
# and gauge files into that directory. If a worker dies (crash or rotation),
# its files would otherwise stick around and corrupt sum/livesum reads from
# MultiProcessCollector. mark_process_dead() removes the dead worker's files
# so the next scrape only sees live data.
def child_exit(server, worker):  # pragma: no cover  — gunicorn-only hook
    if not os.environ.get("PROMETHEUS_MULTIPROC_DIR", "").strip():
        return
    try:
        from prometheus_client import multiprocess
        multiprocess.mark_process_dead(worker.pid)
    except Exception as e:
        import logging
        logging.getLogger("gunicorn.error").warning(
            "prometheus mark_process_dead failed for pid=%s: %s", worker.pid, e
        )
