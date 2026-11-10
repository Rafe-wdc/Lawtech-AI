"""Versions of a draft artifact.

One row per (artifact_id, version). Every change — a new draft, an edit by
the assistant, a save of what the advocate typed, a restore — writes a new
row; nothing is updated in place, so any earlier version can be read back.

Kept apart from core.chat_store's schema on purpose: the table is created
lazily on first use with CREATE TABLE IF NOT EXISTS, through the chat store's
own connection (SQLite file or Postgres pool), so the chat-history schema and
its migrations are not touched.
"""

from __future__ import annotations

import asyncio
import json
import threading

from core.draft_blocks import Artifact
from core.logger import get_logger

log = get_logger("ArtifactStore")

SOURCE_DRAFT = "draft"          # a fresh draft from the drafting agent
SOURCE_ASSISTANT = "assistant"  # an edit made by the assistant
SOURCE_USER = "user"            # the advocate typed in the panel
SOURCE_RESTORE = "restore"      # an earlier version brought back

_DDL = """
CREATE TABLE IF NOT EXISTS draft_artifacts (
    artifact_id     TEXT    NOT NULL,
    version         INTEGER NOT NULL,
    thread_id       TEXT    NOT NULL,
    source          TEXT    NOT NULL,
    change_summary  TEXT    NOT NULL DEFAULT '',
    artifact_json   TEXT    NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (artifact_id, version)
)
"""


class StaleVersion(Exception):
    """The caller's base_version is not the latest one."""

    def __init__(self, latest: int):
        super().__init__(f"latest version is {latest}")
        self.latest = latest


class _Store:
    def __init__(self):
        self._ready = False
        self._lock = threading.Lock()

    # -- connection plumbing -------------------------------------------------

    def _backend(self):
        from core import chat_store as cs
        store = cs.chat_store
        return store, isinstance(store, cs._PostgresChatHistoryStore)

    def _run(self, fn):
        """Run fn(cursor, ph) in one transaction. `ph` is the placeholder."""
        store, is_pg = self._backend()
        if is_pg:
            from psycopg.rows import tuple_row
            with store._get_pool().connection() as conn:
                # Pooled connections can carry the chat store's dict_row (it
                # sets it per connection and never resets it); rows here are
                # read by position.
                with conn.cursor(row_factory=tuple_row) as cur:
                    self._ensure(cur)
                    out = fn(cur, "%s")
                conn.commit()
                return out
        conn = store._get_connection()
        try:
            with store._write_lock:
                cur = conn.cursor()
                self._ensure(cur)
                out = fn(cur, "?")
                conn.commit()
                return out
        finally:
            conn.close()

    def _ensure(self, cur) -> None:
        if self._ready:
            return
        with self._lock:
            if not self._ready:
                cur.execute(_DDL)
                self._ready = True

    # -- sync operations -----------------------------------------------------

    def _latest_version(self, cur, ph, artifact_id: str) -> int:
        cur.execute(f"SELECT MAX(version) FROM draft_artifacts WHERE artifact_id = {ph}",
                    (artifact_id,))
        row = cur.fetchone()
        return int((row[0] if row else 0) or 0)

    def _save_sync(self, artifact: Artifact, thread_id: str, source: str,
                   change_summary: str, base_version: int | None) -> Artifact:
        def op(cur, ph):
            latest = self._latest_version(cur, ph, artifact.artifact_id)
            if base_version is not None and base_version != latest:
                raise StaleVersion(latest)
            artifact.version = latest + 1
            cur.execute(
                f"INSERT INTO draft_artifacts (artifact_id, version, thread_id, source, "
                f"change_summary, artifact_json) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
                (artifact.artifact_id, artifact.version, thread_id, source,
                 (change_summary or "")[:300], artifact.model_dump_json()),
            )
            return artifact
        return self._run(op)

    def _get_sync(self, artifact_id: str, version: int | None) -> Artifact | None:
        def op(cur, ph):
            if version is None:
                cur.execute(
                    f"SELECT artifact_json FROM draft_artifacts WHERE artifact_id = {ph} "
                    f"ORDER BY version DESC LIMIT 1", (artifact_id,))
            else:
                cur.execute(
                    f"SELECT artifact_json FROM draft_artifacts WHERE artifact_id = {ph} "
                    f"AND version = {ph}", (artifact_id, version))
            row = cur.fetchone()
            return Artifact.model_validate(json.loads(row[0])) if row else None
        return self._run(op)

    def _versions_sync(self, artifact_id: str) -> list[dict]:
        def op(cur, ph):
            cur.execute(
                f"SELECT version, source, change_summary, created_at FROM draft_artifacts "
                f"WHERE artifact_id = {ph} ORDER BY version", (artifact_id,))
            return [{"version": r[0], "source": r[1], "change_summary": r[2],
                     "created_at": str(r[3]) if r[3] is not None else None}
                    for r in cur.fetchall()]
        return self._run(op)

    def _thread_of_sync(self, artifact_id: str) -> str | None:
        def op(cur, ph):
            cur.execute(f"SELECT thread_id FROM draft_artifacts WHERE artifact_id = {ph} "
                        f"ORDER BY version DESC LIMIT 1", (artifact_id,))
            row = cur.fetchone()
            return row[0] if row else None
        return self._run(op)

    # -- async API -----------------------------------------------------------

    async def save(self, artifact: Artifact, *, thread_id: str, source: str,
                   change_summary: str = "", base_version: int | None = None) -> Artifact:
        """Store `artifact` as the next version and return it with `version`
        set. With `base_version`, raises StaleVersion unless that is still the
        latest."""
        return await asyncio.to_thread(self._save_sync, artifact, thread_id, source,
                                       change_summary, base_version)

    async def get(self, artifact_id: str, version: int | None = None) -> Artifact | None:
        return await asyncio.to_thread(self._get_sync, artifact_id, version)

    async def versions(self, artifact_id: str) -> list[dict]:
        return await asyncio.to_thread(self._versions_sync, artifact_id)

    async def thread_of(self, artifact_id: str) -> str | None:
        return await asyncio.to_thread(self._thread_of_sync, artifact_id)


artifact_store = _Store()


def artifact_id_for_thread(thread_id: str) -> str:
    """One draft artifact per conversation."""
    return f"art_{thread_id.replace('-', '')[:12]}"
