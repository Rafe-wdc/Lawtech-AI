"""LangGraph Checkpointer — persistent conversation state.

Uses AsyncPostgresSaver (psycopg3 connection pool) when POSTGRES_URL is set.
Falls back to MemorySaver in dev/local mode with a clear startup warning.

PostgreSQL tables are created automatically on first run via saver.setup().
Required tables: checkpoints, checkpoint_blobs, checkpoint_migrations,
                 checkpoint_writes (created by langgraph-checkpoint-postgres).

Usage (managed via FastAPI lifespan in gateway.py):
    checkpointer, pool = await create_checkpointer()
    graph = compile_graph(checkpointer=checkpointer)
    ...
    if pool:
        await pool.close()

Schema note:
    All LangGraph checkpoint data is stored in these tables.
    They live alongside the app's own tables in the same DB if desired,
    or in a separate langgraph schema. The default is public schema.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver

from .settings import POSTGRES_URL
from .logger import get_logger

log = get_logger("Checkpointer")


async def create_checkpointer():
    """Create and return a (checkpointer, pool) pair.

    Returns:
        (AsyncPostgresSaver, AsyncConnectionPool) if POSTGRES_URL is set.
        (MemorySaver, None) otherwise (dev mode).

    The pool must be closed at app shutdown:
        if pool: await pool.close()
    """
    if not POSTGRES_URL:
        log.warning(
            "POSTGRES_URL not set — using in-memory MemorySaver. "
            "Conversation state will be lost on server restart. "
            "Set POSTGRES_URL in .env for persistent checkpointing."
        )
        return MemorySaver(), None

    try:
        from psycopg_pool import AsyncConnectionPool
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        pool = AsyncConnectionPool(
            conninfo=POSTGRES_URL,
            max_size=10,
            min_size=2,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0},
        )
        await pool.open(wait=True, timeout=10.0)

        saver = AsyncPostgresSaver(pool)
        await saver.setup()  # creates checkpoint tables if they don't exist

        log.info("PostgreSQL checkpointer initialized", url=POSTGRES_URL[:30] + "...")
        return saver, pool

    except Exception as e:
        log.error(
            "Failed to connect to PostgreSQL — falling back to MemorySaver",
            error=str(e),
            url=POSTGRES_URL[:30] + "...",
        )
        return MemorySaver(), None
