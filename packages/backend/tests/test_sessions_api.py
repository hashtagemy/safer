"""Tests for the sessions list + events endpoints."""

from __future__ import annotations

import asyncio
import importlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_client(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "sessions.db")
        monkeypatch.setenv("SAFER_DB_PATH", db_path)

        from safer_backend.storage.db import init_db_sync

        init_db_sync(db_path)

        import safer_backend.storage.db as db_mod
        import safer_backend.storage.dao as dao_mod
        import safer_backend.sessions_api as sessions_mod
        import safer_backend.main as main_mod

        for m in (db_mod, dao_mod, sessions_mod, main_mod):
            importlib.reload(m)

        with TestClient(main_mod.app) as client:
            yield client, dao_mod


async def _seed(dao_mod, *, session_id="sess_demo", agent_id="agent_demo"):
    await dao_mod.upsert_agent(agent_id, name="Demo")
    now = datetime.now(timezone.utc)
    async with dao_mod.get_db() as db:
        await db.execute(
            """
            INSERT INTO sessions
            (session_id, agent_id, started_at, ended_at, total_steps, total_cost_usd)
            VALUES (?, ?, ?, ?, 3, 0.01)
            """,
            (session_id, agent_id, now.isoformat(), (now + timedelta(seconds=2)).isoformat()),
        )
        for seq, hook, risk in [
            (0, "on_session_start", "LOW"),
            (1, "before_tool_use", "MEDIUM"),
            (2, "after_tool_use", "LOW"),
            (3, "on_final_output", "LOW"),
            (4, "on_session_end", "LOW"),
        ]:
            await db.execute(
                """
                INSERT INTO events
                (event_id, session_id, agent_id, sequence, hook, timestamp, risk_hint, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, '{}')
                """,
                (
                    f"evt_{uuid4().hex[:8]}",
                    session_id,
                    agent_id,
                    seq,
                    hook,
                    (now + timedelta(seconds=seq)).isoformat(),
                    risk,
                ),
            )
        await db.commit()


def _run_sync(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_list_sessions_returns_rows(app_client):
    client, dao_mod = app_client
    _run_sync(_seed(dao_mod))

    resp = client.get("/v1/sessions")
    assert resp.status_code == 200
    payload = resp.json()
    assert len(payload["sessions"]) == 1
    row = payload["sessions"][0]
    assert row["session_id"] == "sess_demo"
    assert row["agent_id"] == "agent_demo"
    assert row["total_steps"] == 3


def test_list_sessions_agent_filter(app_client):
    client, dao_mod = app_client
    _run_sync(_seed(dao_mod, session_id="sess_a", agent_id="agent_a"))
    _run_sync(_seed(dao_mod, session_id="sess_b", agent_id="agent_b"))

    resp = client.get("/v1/sessions?agent_id=agent_b")
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert [s["session_id"] for s in sessions] == ["sess_b"]


def test_events_endpoint_returns_ordered_events(app_client):
    client, dao_mod = app_client
    _run_sync(_seed(dao_mod))

    resp = client.get("/v1/sessions/sess_demo/events")
    assert resp.status_code == 200
    payload = resp.json()
    seq = [e["sequence"] for e in payload["events"]]
    hooks = [e["hook"] for e in payload["events"]]
    assert seq == sorted(seq)
    assert "on_session_start" in hooks
    assert "on_session_end" in hooks


def test_events_endpoint_404(app_client):
    client, _ = app_client
    resp = client.get("/v1/sessions/sess_missing/events")
    assert resp.status_code == 404
