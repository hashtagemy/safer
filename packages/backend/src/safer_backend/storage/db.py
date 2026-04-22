"""SQLite connection + migration runner.

WAL mode, sync=NORMAL. One connection per request for FastAPI; async via
aiosqlite. Schema is in schema.sql — run init_db() at startup.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DEFAULT_DB_PATH = os.environ.get("SAFER_DB_PATH", "./safer.db")


async def init_db(db_path: str | None = None) -> None:
    """Create tables if they don't exist. Idempotent."""
    path = db_path or DEFAULT_DB_PATH
    schema_sql = SCHEMA_PATH.read_text()
    async with aiosqlite.connect(path) as db:
        await db.executescript(schema_sql)
        await db.commit()


@asynccontextmanager
async def get_db(db_path: str | None = None):
    """Async context manager for a single-use connection."""
    path = db_path or DEFAULT_DB_PATH
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA synchronous = NORMAL")
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("PRAGMA busy_timeout = 5000")
        db.row_factory = aiosqlite.Row
        yield db


def init_db_sync(db_path: str | None = None) -> None:
    """Sync helper for CLI / scripts / tests."""
    import asyncio

    asyncio.run(init_db(db_path))


if __name__ == "__main__":
    # Allow: uv run python -m safer_backend.storage.db
    print(f"Initializing DB at {DEFAULT_DB_PATH}")
    init_db_sync()
    print("Done.")
