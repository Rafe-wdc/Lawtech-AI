"""Feedback loop, phase 1: capture (docs/rlhf_feedback_loop_plan.md).

Three tables next to the existing `feedback` (thumbs + comment) table, all
keyed by (thread_id, turn_number):

  feedback_detail   why a thumbs-down was given (reason codes) and, when the
                    user edited a draft, the text they ended up with
  feedback_events   what the user did with an answer: copy / download / edit
                    / regenerate
  answer_trace      how the answer was produced: task, agents, models, prompt
                    version, sources, language, plan, latency, tokens

Nothing here changes an answer. It records what a reviewer needs to act on a
rating. Raw ratings are never a training or ranking signal on their own (the
plan's safety rules); they are read by people.

Works on both chat-store backends (SQLite and Postgres) through the store's
own connections. Every write is best-effort: a failure is logged and never
reaches the user's request.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from pathlib import Path

from core.chat_store import chat_store
from core.logger import get_logger

log = get_logger("FeedbackStore")

# Reason codes a thumbs-down may carry. The label is what the UI shows.
FEEDBACK_REASONS: dict[str, str] = {
    "wrong_law": "Wrong law, section or citation",
    "old_law": "Used IPC / CrPC / Evidence Act instead of BNS / BNSS / BSA",
    "hallucinated_case": "Case or judgment doesn't exist or is misquoted",
    "incomplete": "Incomplete answer or draft",
    "wrong_format": "Wrong document format or structure",
    "wrong_facts": "Ignored or changed the facts I gave",
    "language": "Wrong language or poor translation",
    "too_long": "Too long",
    "too_short": "Too short",
    "other": "Other",
}
FEEDBACK_EVENTS = frozenset({"copy", "download", "edit", "regenerate"})

_MAX_FINAL_TEXT_CHARS = 400_000
_MAX_TRACE_SOURCES = 25

_IS_PG = hasattr(chat_store, "_get_pool")
_PH = "%s" if _IS_PG else "?"
_NOW = "NOW()" if _IS_PG else "datetime('now')"
_SERIAL = "BIGSERIAL PRIMARY KEY" if _IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
_TS = "TIMESTAMPTZ NOT NULL DEFAULT NOW()" if _IS_PG else "TEXT NOT NULL DEFAULT (datetime('now'))"

_SCHEMA = [
    f"""CREATE TABLE IF NOT EXISTS feedback_detail (
        id           {_SERIAL},
        thread_id    TEXT NOT NULL,
        turn_number  INTEGER NOT NULL,
        reasons_json TEXT NOT NULL DEFAULT '[]',
        final_text   TEXT NOT NULL DEFAULT '',
        updated_at   {_TS},
        UNIQUE(thread_id, turn_number)
    )""",
    f"""CREATE TABLE IF NOT EXISTS feedback_events (
        id          {_SERIAL},
        thread_id   TEXT NOT NULL,
        turn_number INTEGER NOT NULL,
        event       TEXT NOT NULL,
        created_at  {_TS}
    )""",
    "CREATE INDEX IF NOT EXISTS idx_feedback_events_turn ON feedback_events(thread_id, turn_number)",
    f"""CREATE TABLE IF NOT EXISTS answer_trace (
        id             {_SERIAL},
        thread_id      TEXT NOT NULL,
        turn_number    INTEGER NOT NULL,
        request_id     TEXT NOT NULL DEFAULT '',
        endpoint       TEXT NOT NULL DEFAULT '',
        task           TEXT NOT NULL DEFAULT '',
        agents_json    TEXT NOT NULL DEFAULT '[]',
        models_json    TEXT NOT NULL DEFAULT '{{}}',
        prompt_version TEXT NOT NULL DEFAULT '',
        sources_json   TEXT NOT NULL DEFAULT '[]',
        language       TEXT NOT NULL DEFAULT '',
        user_plan      TEXT NOT NULL DEFAULT '',
        web_fallback   INTEGER NOT NULL DEFAULT 0,
        regenerate_of  TEXT NOT NULL DEFAULT '',
        had_files      INTEGER NOT NULL DEFAULT 0,
        latency_ms     INTEGER NOT NULL DEFAULT 0,
        total_tokens   INTEGER NOT NULL DEFAULT 0,
        cost_usd       REAL NOT NULL DEFAULT 0,
        created_at     {_TS},
        UNIQUE(thread_id, turn_number)
    )""",
]

_schema_ok = False
_schema_lock = threading.Lock()


class _Conn:
    """One connection from the chat store, committed on success."""

    def __enter__(self):
        if _IS_PG:
            self._cm = chat_store._get_pool().connection()
            self.conn = self._cm.__enter__()
        else:
            # SQLite: one writer at a time, the same lock the chat store uses.
            self._cm = None
            self._lock = getattr(chat_store, "_write_lock", None)
            if self._lock is not None:
                self._lock.acquire()
            self.conn = chat_store._get_connection()
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            if self._cm is not None:
                self._cm.__exit__(exc_type, exc, tb)
            else:
                self.conn.close()
                if self._lock is not None:
                    self._lock.release()
        return False


def _ensure_schema() -> None:
    global _schema_ok
    if _schema_ok:
        return
    with _schema_lock:
        if _schema_ok:
            return
        with _Conn() as conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)
        _schema_ok = True


def _rows(cursor) -> list[dict]:
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r)) if not isinstance(r, dict) else dict(r) for r in cursor.fetchall()]


# --- prompt version ------------------------------------------------------------

_prompt_version: str | None = None


def prompt_version() -> str:
    """Short hash of config/prompts.py: changes whenever any prompt does, so
    ratings can be compared across prompt changes."""
    global _prompt_version
    if _prompt_version is None:
        try:
            path = Path(__file__).resolve().parent.parent / "config" / "prompts.py"
            _prompt_version = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        except OSError:
            _prompt_version = "unknown"
    return _prompt_version


# --- writes --------------------------------------------------------------------

def clean_reasons(reasons) -> list[str]:
    """Known reason codes only, de-duplicated, in the order given."""
    out: list[str] = []
    for r in reasons or []:
        code = str(r).strip().lower()
        if code in FEEDBACK_REASONS and code not in out:
            out.append(code)
    return out


def _save_detail_sync(thread_id: str, turn_number: int, reasons: list[str], final_text: str) -> None:
    _ensure_schema()
    with _Conn() as conn:
        conn.execute(f"""
            INSERT INTO feedback_detail (thread_id, turn_number, reasons_json, final_text)
            VALUES ({_PH}, {_PH}, {_PH}, {_PH})
            ON CONFLICT(thread_id, turn_number) DO UPDATE SET
                reasons_json = excluded.reasons_json,
                final_text   = excluded.final_text,
                updated_at   = {_NOW}
        """, (thread_id, turn_number, json.dumps(reasons), final_text[:_MAX_FINAL_TEXT_CHARS]))


async def save_feedback_detail(thread_id: str, turn_number: int,
                               reasons: list[str] | None = None, final_text: str = "") -> None:
    await asyncio.to_thread(_save_detail_sync, thread_id, turn_number,
                            clean_reasons(reasons), final_text or "")
    log.info("Feedback detail saved", thread_id=thread_id[:12], turn=turn_number,
             reasons=clean_reasons(reasons), has_final_text=bool(final_text))


def _save_event_sync(thread_id: str, turn_number: int, event: str) -> None:
    _ensure_schema()
    with _Conn() as conn:
        conn.execute(
            f"INSERT INTO feedback_events (thread_id, turn_number, event) VALUES ({_PH}, {_PH}, {_PH})",
            (thread_id, turn_number, event))


async def save_feedback_event(thread_id: str, turn_number: int, event: str) -> None:
    if event not in FEEDBACK_EVENTS:
        raise ValueError(f"unknown feedback event: {event}")
    await asyncio.to_thread(_save_event_sync, thread_id, turn_number, event)
    log.info("Feedback event saved", thread_id=thread_id[:12], turn=turn_number, event=event)


def _trace_sources(source_metadata: list | None) -> list[dict]:
    out = []
    for s in (source_metadata or [])[:_MAX_TRACE_SOURCES]:
        if not isinstance(s, dict):
            continue
        out.append({k: s.get(k) for k in ("agent_name", "source_type", "title", "db_id")
                    if s.get(k)} | {"url": s.get("doc_link") or s.get("web_url") or ""})
    return out


def _save_trace_sync(row: tuple) -> None:
    _ensure_schema()
    with _Conn() as conn:
        conn.execute(f"""
            INSERT INTO answer_trace (
                thread_id, turn_number, request_id, endpoint, task, agents_json,
                models_json, prompt_version, sources_json, language, user_plan,
                web_fallback, regenerate_of, had_files, latency_ms, total_tokens, cost_usd)
            VALUES ({", ".join([_PH] * 17)})
            ON CONFLICT(thread_id, turn_number) DO NOTHING
        """, row)


async def save_answer_trace(
    *, thread_id: str, turn_number: int, request_id: str = "", endpoint: str = "",
    task: str = "", agents_used: list | None = None, token_usage: dict | None = None,
    source_metadata: list | None = None, language: str = "", user_plan: str = "",
    web_fallback: bool = False, regenerate_of: str = "", had_files: bool = False,
    latency_ms: int = 0, models: dict | None = None,
) -> None:
    """Record how one answer was produced. Never raises.

    `models` maps model id -> tokens used. The public token_usage payload
    leaves model names out on purpose; this table is internal.
    """
    try:
        if not thread_id or not turn_number:
            return
        usage = token_usage or {}
        models = models if models is not None else (usage.get("by_model") or {})
        row = (
            thread_id, int(turn_number), request_id or "", endpoint or "", task or "",
            json.dumps(list(agents_used or [])),
            json.dumps({m: (v.get("total") if isinstance(v, dict) else v) for m, v in models.items()}),
            prompt_version(),
            json.dumps(_trace_sources(source_metadata), ensure_ascii=False),
            language or "", (user_plan or "")[:80], int(bool(web_fallback)),
            regenerate_of or "", int(bool(had_files)), int(latency_ms or 0),
            int(usage.get("total_tokens") or 0), float(usage.get("cost_usd") or 0.0),
        )
        await asyncio.to_thread(_save_trace_sync, row)
        if regenerate_of:
            # A regenerate says the previous answer was not good enough.
            await asyncio.to_thread(_save_event_sync, thread_id, max(int(turn_number) - 1, 1), "regenerate")
    except Exception as e:
        log.warning("Answer trace not saved", thread_id=(thread_id or "")[:12], error=str(e)[:160])


# --- reads ---------------------------------------------------------------------

def _review_sync(days: int, rating: str, limit: int) -> list[dict]:
    _ensure_schema()
    since = (f"NOW() - INTERVAL '{int(days)} days'" if _IS_PG
             else f"datetime('now', '-{int(days)} days')")
    where_rating = "" if rating == "all" else f"AND f.rating = {_PH}"
    params: tuple = (() if rating == "all" else (rating,)) + (int(limit),)
    with _Conn() as conn:
        cur = conn.execute(f"""
            SELECT f.thread_id, f.turn_number, f.rating, f.comment, f.created_at,
                   d.reasons_json, d.final_text,
                   m.user_query, m.ai_response,
                   t.task, t.agents_json, t.models_json, t.prompt_version, t.sources_json,
                   t.language, t.user_plan, t.web_fallback, t.request_id, t.latency_ms
            FROM feedback f
            LEFT JOIN feedback_detail d ON d.thread_id = f.thread_id AND d.turn_number = f.turn_number
            LEFT JOIN messages m        ON m.thread_id = f.thread_id AND m.turn_number = f.turn_number
            LEFT JOIN answer_trace t    ON t.thread_id = f.thread_id AND t.turn_number = f.turn_number
            WHERE f.created_at >= {since} {where_rating}
            ORDER BY f.created_at DESC
            LIMIT {_PH}
        """, params)
        rows = _rows(cur)
        keys = {(r["thread_id"], r["turn_number"]) for r in rows}
        events: dict[tuple, list[str]] = {}
        if keys:
            cur = conn.execute(f"SELECT thread_id, turn_number, event FROM feedback_events "
                               f"WHERE created_at >= {since}")
            for e in _rows(cur):
                k = (e["thread_id"], e["turn_number"])
                if k in keys:
                    events.setdefault(k, []).append(e["event"])
    for r in rows:
        for col in ("reasons_json", "agents_json", "models_json", "sources_json"):
            try:
                r[col[:-5]] = json.loads(r.pop(col) or "null")
            except (TypeError, ValueError):
                r[col[:-5]] = None
        r["events"] = events.get((r["thread_id"], r["turn_number"]), [])
        r["created_at"] = str(r["created_at"])
    return rows


async def review_queue(days: int = 7, rating: str = "down", limit: int = 200) -> list[dict]:
    """Rated answers with everything a reviewer needs: question, answer,
    reasons, comment, what the user did, and how the answer was produced."""
    return await asyncio.to_thread(_review_sync, days, rating, limit)


def _summary_sync(days: int) -> dict:
    _ensure_schema()
    since = (f"NOW() - INTERVAL '{int(days)} days'" if _IS_PG
             else f"datetime('now', '-{int(days)} days')")
    with _Conn() as conn:
        ratings = {r["rating"]: r["n"] for r in _rows(conn.execute(
            f"SELECT rating, COUNT(*) AS n FROM feedback WHERE created_at >= {since} GROUP BY rating"))}
        reasons: dict[str, int] = {}
        for r in _rows(conn.execute(
                f"SELECT reasons_json FROM feedback_detail WHERE updated_at >= {since}")):
            for code in json.loads(r["reasons_json"] or "[]"):
                reasons[code] = reasons.get(code, 0) + 1
        events = {r["event"]: r["n"] for r in _rows(conn.execute(
            f"SELECT event, COUNT(*) AS n FROM feedback_events WHERE created_at >= {since} GROUP BY event"))}
        answers = _rows(conn.execute(
            f"SELECT COUNT(*) AS n FROM answer_trace WHERE created_at >= {since}"))[0]["n"]
    return {"days": days, "answers": answers, "ratings": ratings,
            "reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])), "events": events}


async def summary(days: int = 7) -> dict:
    return await asyncio.to_thread(_summary_sync, days)


def _purge_sync(thread_id: str | None) -> int:
    """Delete feedback data for one thread, or for every thread that no
    longer exists (thread expiry)."""
    _ensure_schema()
    n = 0
    with _Conn() as conn:
        for table in ("feedback_detail", "feedback_events", "answer_trace", "feedback"):
            if thread_id:
                cur = conn.execute(f"DELETE FROM {table} WHERE thread_id = {_PH}", (thread_id,))
            else:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE thread_id NOT IN (SELECT thread_id FROM threads)")
            n += max(cur.rowcount or 0, 0)
    return n


async def purge(thread_id: str | None = None) -> int:
    return await asyncio.to_thread(_purge_sync, thread_id)


def _scrub_expired_sync() -> int:
    """After thread expiry: remove the user's own text (edited drafts) for
    threads that no longer exist. Ratings, reasons and traces stay, since
    they hold no client content once the messages are gone."""
    _ensure_schema()
    with _Conn() as conn:
        cur = conn.execute(
            "UPDATE feedback_detail SET final_text = '' WHERE final_text <> '' "
            "AND thread_id NOT IN (SELECT thread_id FROM threads)")
        return max(cur.rowcount or 0, 0)


async def scrub_expired() -> int:
    return await asyncio.to_thread(_scrub_expired_sync)
