"""Integration test: schema_tables.sql + schema_indexes.sql load and tables exist."""

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


@pytest.mark.asyncio
async def test_init_db_upgrades_legacy_sessions_table():
    """Regression: a DB created before `sessions.parent_session_id` existed
    must upgrade cleanly. Without the tables → migrations → indexes split,
    executescript() would hit `idx_sessions_parent` and fail with
    `no such column: parent_session_id`.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "legacy.db")
        async with aiosqlite.connect(db_path) as db:
            # Minimal legacy shape: all columns that existed before the
            # parent_session_id migration landed (Phase 27.3), nothing more.
            await db.execute(
                """
                CREATE TABLE agents (
                    agent_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    framework TEXT,
                    version TEXT,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT,
                    risk_score INTEGER DEFAULT 0,
                    metadata_json TEXT DEFAULT '{}'
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE sessions (
                    session_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    total_steps INTEGER DEFAULT 0,
                    total_cost_usd REAL DEFAULT 0.0,
                    success INTEGER DEFAULT 1,
                    overall_health INTEGER,
                    thought_chain_narrative TEXT,
                    report_json TEXT
                )
                """
            )
            await db.commit()

        await init_db(db_path)

        async with aiosqlite.connect(db_path) as db:
            async with db.execute("PRAGMA table_info(sessions)") as cur:
                cols = {row[1] for row in await cur.fetchall()}
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_sessions_parent'"
            ) as cur:
                idx_row = await cur.fetchone()

        assert "parent_session_id" in cols
        assert idx_row is not None
