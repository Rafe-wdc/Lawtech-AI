"""Resolve web-grounded sources to the pages they point at.

Gemini's Google Search grounding reports each source as a redirect
(``vertexaisearch.cloud.google.com/grounding-api-redirect/...``) titled with
the bare domain ("casemine.com"). Until 2026-09-23 these records were dropped
from the ``sources`` payload altogether, so an answer built from the web
reached the reader with no source at all (lawyer report, the Vinai Kumar v.
Om Prakash lookup). For the sources panel the reader needs the real page and
its title, so each redirect is followed once, in parallel, under a short
budget, and the first 64 KB of the page supply a ``<title>``. A page that
cannot be fetched keeps its redirect (it still opens in a browser) and its
domain as the title. Nothing here touches the answer text, which stays
URL-free.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from urllib.parse import urlparse

import httpx

from core.logger import get_logger

log = get_logger("WebSources")

_PER_SOURCE_TIMEOUT_S = 4.0
_TOTAL_BUDGET_S = 6.0
_MAX_WEB_SOURCES = 10
_READ_BYTES = 65_536
_USER_AGENT = "Mozilla/5.0 (compatible; Lawttorney/1.0; +https://lawttorney.ai)"

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# Titles that name the block page, not the document behind it.
_BLOCK_TITLE_RE = re.compile(
    r"^\s*(?:\d{3}\b|forbidden|access denied|access to this page|just a moment|"
    r"attention required|client challenge|security check|are you a robot|please verify|"
    r"captcha|not found|error|sign in|log in|login|untitled)",
    re.IGNORECASE)


def _domain(url: str | None) -> str:
    try:
        host = urlparse(url or "").netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _is_web_record(rec: dict) -> bool:
    url = rec.get("web_url")
    return (isinstance(url, str) and url.lower().startswith(("http://", "https://"))
            and not rec.get("doc_link") and not rec.get("pdf_links"))


def _title_from(raw: str | None, final_url: str) -> str:
    text = re.sub(r"\s+", " ", html.unescape(raw or "")).strip()
    if not text or _BLOCK_TITLE_RE.match(text):
        return _domain(final_url)
    return text[:117] + "..." if len(text) > 120 else text


async def _resolve_one(client: httpx.AsyncClient, rec: dict) -> dict:
    url = rec["web_url"]
    out = dict(rec)
    try:
        async with client.stream("GET", url) as resp:
            final_url = str(resp.url)
            body = b""
            if "html" in (resp.headers.get("content-type") or "").lower():
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) >= _READ_BYTES or b"</title>" in body.lower():
                        break
        raw_title = None
        if resp.status_code < 400 and body:
            m = _TITLE_RE.search(body.decode("utf-8", "replace"))
            raw_title = m.group(1) if m else None
        out["web_url"] = final_url
        out["web_title"] = _title_from(raw_title, final_url)
        out["resolved"] = True
    except Exception as e:  # any transport problem: keep the redirect
        log.debug("Web source not resolved", url=url[:80], error=str(e)[:100])
        out["web_title"] = rec.get("web_title") or _domain(url)
        out["resolved"] = False
    out["title"] = out["web_title"]
    return out


async def resolve_web_source_records(
    records: list | None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list:
    """Return ``records`` with every web-grounded record resolved to its
    page (final URL, page title), deduplicated by page, at most
    ``_MAX_WEB_SOURCES`` of them. Other records pass through untouched.
    Never raises; on any failure the input is returned as it was."""
    if not records:
        return records or []
    try:
        web_idx = [i for i, r in enumerate(records) if isinstance(r, dict) and _is_web_record(r)]
        if not web_idx:
            return records
        started = time.perf_counter()
        keep_idx = web_idx[:_MAX_WEB_SOURCES]
        async with httpx.AsyncClient(
            follow_redirects=True, max_redirects=5,
            timeout=httpx.Timeout(_PER_SOURCE_TIMEOUT_S),
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*;q=0.8"},
            transport=transport,
        ) as client:
            tasks = {i: asyncio.create_task(_resolve_one(client, records[i])) for i in keep_idx}
            done, pending = await asyncio.wait(tasks.values(), timeout=_TOTAL_BUDGET_S)
            for t in pending:
                t.cancel()
            resolved: dict[int, dict] = {}
            for i, t in tasks.items():
                if t in done and not t.cancelled() and t.exception() is None:
                    resolved[i] = t.result()
                else:
                    rec = dict(records[i])
                    rec["web_title"] = rec.get("web_title") or _domain(rec["web_url"])
                    rec["title"] = rec["web_title"]
                    rec["resolved"] = False
                    resolved[i] = rec
        out: list = []
        seen_pages: set[str] = set()
        dropped_idx = set(web_idx[_MAX_WEB_SOURCES:])
        for i, rec in enumerate(records):
            if i in dropped_idx:
                continue
            if i in resolved:
                page = resolved[i]["web_url"].split("#")[0].rstrip("/").lower()
                if page in seen_pages:
                    continue
                seen_pages.add(page)
                out.append(resolved[i])
            else:
                out.append(rec)
        log.info("Web sources resolved",
                 web=len(web_idx), kept=sum(1 for r in out if _is_web_record(r)),
                 resolved=sum(1 for r in resolved.values() if r.get("resolved")),
                 ms=int((time.perf_counter() - started) * 1000))
        return out
    except Exception as e:
        log.warning("Web source resolution skipped", error=str(e)[:120])
        return records
