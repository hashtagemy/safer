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


# Additive column migrations — each entry is (table, column, type).
# SQLite only supports adding columns via ALTER TABLE; we apply each one
# iff the column isn't already present. Never drop, never rename here.
_COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("agents", "system_prompt", "TEXT"),
    ("agents", "project_root", "TEXT"),
    ("agents", "code_snapshot_blob", "BLOB"),
    ("agents", "code_snapshot_hash", "TEXT"),
    ("agents", "file_count", "INTEGER DEFAULT 0"),
    ("agents", "total_bytes", "INTEGER DEFAULT 0"),
    ("agents", "snapshot_truncated", "INTEGER DEFAULT 0"),
    ("agents", "registered_at", "TEXT"),
    ("agents", "latest_scan_id", "TEXT"),
    ("sessions", "parent_session_id", "TEXT"),
)


async def _apply_additive_migrations(db: aiosqlite.Connection) -> None:
    for table, column, coltype in _COLUMN_MIGRATIONS:
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            rows = await cur.fetchall()
        existing = {row[1] for row in rows}
        if column in existing:
            continue
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


async def init_db(db_path: str | None = None) -> None:
    """Create tables if they don't exist, then apply additive migrations."""
    path = db_path or DEFAULT_DB_PATH
    schema_sql = SCHEMA_PATH.read_text()
    async with aiosqlite.connect(path) as db:
        await db.executescript(schema_sql)
        await _apply_additive_migrations(db)
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
