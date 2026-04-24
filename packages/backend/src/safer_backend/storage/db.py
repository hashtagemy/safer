"""SQLite connection + migration runner.

WAL mode, sync=NORMAL. One connection per request for FastAPI; async via
aiosqlite. Schema is split into schema_tables.sql (CREATE TABLE) and
schema_indexes.sql (CREATE INDEX); init_db() runs tables → migrations →
indexes so that indexes can safely reference columns added via ALTER TABLE.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

SCHEMA_TABLES_PATH = Path(__file__).parent / "schema_tables.sql"
SCHEMA_INDEXES_PATH = Path(__file__).parent / "schema_indexes.sql"
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
    """Create tables, apply additive migrations, then create indexes.

    The three steps are strictly ordered: some indexes target columns that
    are added via ALTER TABLE (e.g. sessions.parent_session_id /
    idx_sessions_parent), so an old DB created before those columns
    existed would fail on index creation if we ran the full schema in one
    pass before migrations.
    """
    path = db_path or DEFAULT_DB_PATH
    tables_sql = SCHEMA_TABLES_PATH.read_text()
    indexes_sql = SCHEMA_INDEXES_PATH.read_text()
    async with aiosqlite.connect(path) as db:
        await db.executescript(tables_sql)
        await _apply_additive_migrations(db)
        await db.executescript(indexes_sql)
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
