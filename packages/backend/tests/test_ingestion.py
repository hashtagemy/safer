"""End-to-end ingestion tests: POST /v1/events and WebSocket /ws/stream."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from safer_backend.storage.db import init_db_sync


@pytest.fixture()
def app_client(monkeypatch):
    """Spin up the FastAPI app with a temp DB."""
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        monkeypatch.setenv("SAFER_DB_PATH", db_path)
        # Initialize DB synchronously; lifespan will re-init (idempotent).
        init_db_sync(db_path)

        # Re-import so env var is picked up.
        import importlib

        import safer_backend.storage.db as db_mod
        import safer_backend.main as main_mod

        importlib.reload(db_mod)
        importlib.reload(main_mod)

        with TestClient(main_mod.app) as client:
            yield client


def _event(hook: str, seq: int, **extra):
    base = {
        "session_id": "sess_e2e",
        "agent_id": "agent_e2e",
        "sequence": seq,
        "hook": hook,
    }
    base.update(extra)
    return base


def test_health(app_client):
    r = app_client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_post_events_accepts_valid_batch(app_client):
    r = app_client.post(
        "/v1/events",
        json={
            "events": [
                _event("on_session_start", 0, agent_name="demo"),
                _event("before_llm_call", 1, model="claude-opus-4-7", prompt="hi"),
                _event(
                    "after_llm_call", 2, model="claude-opus-4-7", response="hi",
                    tokens_in=1, tokens_out=1,
                ),
            ]
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 3
    assert body["rejected"] == []


def test_post_events_rejects_malformed(app_client):
    r = app_client.post(
        "/v1/events",
        json={
            "events": [
                {"foo": "bar"},  # no hook
                _event("before_llm_call", 0, model="claude-opus-4-7", prompt="hi"),
            ]
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 1
    assert len(body["rejected"]) == 1


def test_stats_reflects_ingested_events(app_client):
    app_client.post(
        "/v1/events",
        json={
            "events": [
                _event("on_session_start", 0, agent_name="demo"),
                _event("on_session_end", 1, total_duration_ms=500),
            ]
        },
    )
    r = app_client.get("/v1/stats")
    assert r.status_code == 200
    stats = r.json()
    assert stats["agents"] >= 1
    assert stats["sessions"] >= 1
    assert stats["events"] >= 2


def test_ws_stream_receives_broadcast(app_client):
    with app_client.websocket_connect("/ws/stream") as ws:
        # Ingest an event via HTTP.
        app_client.post(
            "/v1/events",
            json={
                "events": [
                    _event("before_tool_use", 0, tool_name="get_order", args={"id": 1})
                ]
            },
        )
        # Expect a broadcast message.
        msg = ws.receive_text()
        data = json.loads(msg)
        assert data["type"] == "event"
        assert data["hook"] == "before_tool_use"
        assert data["session_id"] == "sess_e2e"


def test_ws_ingest_accepts_ndjson(app_client):
    with app_client.websocket_connect("/ingest") as ingest_ws:
        batch = [
            _event("on_session_start", 0, agent_name="demo"),
            _event("before_llm_call", 1, model="claude-opus-4-7", prompt="hi"),
        ]
        ndjson = "\n".join(json.dumps(e) for e in batch)
        ingest_ws.send_text(ndjson)
        ack = ingest_ws.receive_json()
        assert ack == {"accepted": 2, "rejected": 0}

    # After ingest connection closes, stats should reflect events.
    r = app_client.get("/v1/stats")
    stats = r.json()
    assert stats["events"] >= 2
