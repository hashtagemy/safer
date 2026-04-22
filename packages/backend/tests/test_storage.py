"""Integration test: schema.sql loads and tables exist."""

from __future__ import annotations

import tempfile
from pathlib import Path

import aiosqlite
import pytest

from safer_backend.storage import init_db


EXPECTED_TABLES = {
    "agents",
    "sessions",
    "events",
    "verdicts",
    "findings",
    "policies",
    "red_team_runs",
    "claude_calls",
}


@pytest.mark.asyncio
async def test_init_db_creates_all_tables():
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        await init_db(db_path)

        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ) as cur:
                rows = await cur.fetchall()

        tables = {r[0] for r in rows if not r[0].startswith("sqlite_")}
        missing = EXPECTED_TABLES - tables
        assert not missing, f"Missing tables: {missing}"


@pytest.mark.asyncio
async def test_init_db_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        await init_db(db_path)
        # Second call should not error
        await init_db(db_path)


@pytest.mark.asyncio
async def test_wal_mode_active():
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        await init_db(db_path)

        async with aiosqlite.connect(db_path) as db:
            async with db.execute("PRAGMA journal_mode") as cur:
                row = await cur.fetchone()
        assert row[0].lower() == "wal"
