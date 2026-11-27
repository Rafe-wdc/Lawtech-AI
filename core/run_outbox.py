"""Tells Node that a background run finished (transactional outbox dispatcher).

The run's final status and its run_outbox row are written in one
transaction (core/run_store.py). This dispatcher, running inside the runner
process every couple of seconds, delivers pending rows:

  POST RUN_CALLBACK_URL
  X-Service-Secret: <RUN_CALLBACK_SECRET>     Python authenticates itself (no user token)
  Idempotency-Key:  <run_id>                  Node stores and pushes once per run
  body (RUN_CALLBACK_FORMAT=store_history, the default): Node's
        /v2/api/chats/cases/ai/store-history request, with the values the app
        sends to StoreHistory (UserId, SearchTitle, SearchResult, ThreadId,
        ParentId, Token, source, summary) plus RunId and notify;
        (run_complete): the stored payload as is (run_id, thread_id, user_id,
        turn, status, query, response, sources, agents_used, token_usage,
        connected, ...)

Which answers are sent (store_history) is decided when the run ends
(plan_delivery): finished answers of a user with a numeric id that no app was
showing when they finished. Node stores every call as a new record, so the
ones an app showed are left to the app, which saves them itself as before.
The run's `done` event carries the decision (`history_saved_by_server`), so
an app that replays a finished run knows whether to save it.

Outcomes:
  2xx                         sent
  408, 429, 5xx, no response  retried: 5 s, 30 s, 2 min, 10 min, 30 min, then
                              hourly, for up to 24 hours; then dead
  any other 4xx               dead at once (a contract error retrying cannot fix)
A dead row can be put back with POST /pyapi/admin/runs/outbox/{id}/retry.

A failed delivery never affects the answer: it is already saved in Python's
messages table and shown in the app on reopen. If this process dies between
Node accepting a delivery and the row being marked sent, the row comes due
again and is resent with the same Idempotency-Key, which Node must dedupe.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from core.logger import get_logger
from core.run_store import OutboxItem, RunStore

log = get_logger("RunOutbox")

BACKOFF_S = (5, 30, 120, 600, 1800)
HOURLY_S = 3600
GIVE_UP_AFTER = timedelta(hours=24)
USER_AGENT = "lawttorney-pyapi-runs/1"

PostFn = Callable[[str, dict, dict, float], Awaitable[int]]


def backoff_s(attempts_done: int) -> int:
    """Delay before the next attempt, given how many attempts already failed."""
    if attempts_done < 1:
        return BACKOFF_S[0]
    return BACKOFF_S[attempts_done - 1] if attempts_done <= len(BACKOFF_S) else HOURLY_S


async def _httpx_post(url: str, payload: dict, headers: dict, timeout_s: float) -> int:
    import httpx
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(url, json=payload, headers=headers)
        return resp.status_code


def _retryable(status: int) -> bool:
    return status in (408, 429) or status >= 500


def _source_urls(sources: list) -> list[str]:
    """One link per source, as the app builds it for StoreHistory: doc_link,
    else web_url, else the first pdf_links url."""
    urls = []
    for s in sources or []:
        if not isinstance(s, dict):
            continue
        url = s.get("doc_link") or s.get("web_url")
        if not url:
            url = next((p.get("url") for p in s.get("pdf_links") or []
                        if isinstance(p, dict) and p.get("url")), None)
        if url:
            urls.append(url)
    return urls


def store_history_body(payload: dict) -> dict | None:
    """The run-complete payload as Node's store-history request: the fields
    and values the app itself sends to StoreHistory when it shows an answer,
    plus notify (send the push: nobody was watching). None when Node cannot
    store it: no numeric user id, or no question or answer text (Node
    answers those with 400/404)."""
    uid = str(payload.get("user_id") or "").strip()
    query = (payload.get("query") or "").strip()
    answer = payload.get("response") or ""
    if not uid.isdigit() or not query or not answer.strip():
        return None
    sources = payload.get("sources") or []
    source = {"type": "sources", "urls": _source_urls(sources), "sources": sources}
    if payload.get("artifact"):
        source["artifact"] = payload["artifact"]
    usage = payload.get("token_usage")
    total = usage.get("total_tokens") if isinstance(usage, dict) else None
    parent = payload.get("parent_id")
    return {
        "UserId": int(uid),
        "SearchTitle": payload.get("query") or "",
        "SearchResult": answer,
        "ThreadId": payload.get("thread_id") or None,
        "ParentId": int(parent) if str(parent or "").strip().isdigit() else None,
        "Token": total if isinstance(total, int) and total > 0 else 1,
        "source": source,
        "summary": payload.get("query") or "",
        "notify": not payload.get("connected", False),
    }


def plan_delivery(payload: dict) -> tuple[bool, str]:
    """(send it, reason when not), decided when the run ends, from the
    run-complete payload and the settings.

    store_history: Node keeps every call as a new record and deducts tokens
    each time, so exactly one side may save an answer. An app that was
    showing the answer when it finished got the `done` and saves it itself
    (as before background runs); Python saves only the ones nobody saw."""
    from core import settings as S
    if S.RUN_CALLBACK_FORMAT != "store_history":
        return True, ""
    if payload.get("status") != "done":
        return False, f"status {payload.get('status')}: only finished answers are stored"
    if payload.get("connected"):
        return False, "an app was showing it and saves it itself"
    if store_history_body(payload) is None:
        return False, "no numeric user id, or no question or answer text"
    return True, ""


def request_body(payload: dict) -> dict | None:
    from core import settings as S
    if S.RUN_CALLBACK_FORMAT == "store_history":
        return store_history_body(payload)
    return payload


class OutboxDispatcher:
    def __init__(self, store: RunStore, *, url: str | None = None, secret: str | None = None,
                 timeout_s: float | None = None, post: PostFn | None = None, batch: int = 10):
        self.store = store
        self._url = url
        self._secret = secret
        self._timeout_s = timeout_s
        self._post = post or _httpx_post
        self.batch = batch

    def _config(self) -> tuple[str, str, float]:
        from core import settings as S
        return (self._url if self._url is not None else S.RUN_CALLBACK_URL,
                self._secret if self._secret is not None else S.RUN_CALLBACK_SECRET,
                self._timeout_s if self._timeout_s is not None else S.RUN_CALLBACK_TIMEOUT_S)

    async def dispatch_once(self) -> int:
        """Deliver what is due. Returns how many were attempted."""
        url, secret, timeout_s = self._config()
        if not url:
            return 0
        items = await self.store.aio.claim_outbox(self.batch, hold_s=int(timeout_s * 3) + 5)
        for item in items:
            await self._deliver(item, url, secret, timeout_s)
        return len(items)

    async def _deliver(self, item: OutboxItem, url: str, secret: str, timeout_s: float) -> None:
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": item.run_id,
            "User-Agent": USER_AGENT,
        }
        if secret:
            headers["X-Service-Secret"] = secret
        body = request_body(item.payload)
        if body is None:
            # The format changed after the row was queued, or the payload
            # cannot be stored by Node: sending would only earn a 400.
            await self.store.aio.mark_outbox_dead(item.outbox_id, "not deliverable in this format")
            log.warning("Run-complete not deliverable; not sent", run_id=item.run_id[:12])
            return
        try:
            status = await self._post(url, body, headers, timeout_s)
            error = f"HTTP {status}"
        except Exception as e:
            status, error = 0, f"{type(e).__name__}: {str(e)[:200]}"

        if 200 <= status < 300:
            await self.store.aio.mark_outbox_sent(item.outbox_id)
            log.info("Run-complete delivered to Node", run_id=item.run_id[:12],
                     attempt=item.attempts + 1, connected=item.payload.get("connected"))
            return
        if status and not _retryable(status):
            await self.store.aio.mark_outbox_dead(item.outbox_id, error)
            log.error("outbox_dead: Node rejected the run-complete message; not retried",
                      run_id=item.run_id[:12], error=error)
            return
        now = self.store.now()
        created = item.created_at or now
        if now - created >= GIVE_UP_AFTER:
            await self.store.aio.mark_outbox_dead(item.outbox_id, f"gave up after 24 h: {error}")
            log.error("outbox_dead: Node unreachable for 24 h; gave up", run_id=item.run_id[:12],
                      attempts=item.attempts + 1, error=error)
            return
        delay = backoff_s(item.attempts + 1)
        await self.store.aio.mark_outbox_retry(item.outbox_id, error, now + timedelta(seconds=delay))
        log.warning("Run-complete delivery failed; will retry", run_id=item.run_id[:12],
                    attempt=item.attempts + 1, retry_in_s=delay, error=error)
