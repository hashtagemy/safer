"""Tests for GET /v1/sessions/active — the /live card list feed."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from safer_backend.storage.db import init_db_sync


def _snapshot(files: dict[str, str]) -> tuple[str, str, int]:
    ordered = {k: files[k] for k in sorted(files)}
    raw = json.dumps(ordered, separators=(",", ":")).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    gz = gzip.compress(raw, mtime=0)
    return base64.b64encode(gz).decode("ascii"), sha, sum(len(v) for v in ordered.values())


def _register(client: TestClient, agent_id: str, name: str) -> None:
    b64, sha, total = _snapshot({"main.py": "print(1)\n"})
    evt = {
        "session_id": f"boot_{agent_id}",
        "agent_id": agent_id,
        "sequence": 0,
        "hook": "on_agent_register",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "risk_hint": "LOW",
        "source": "sdk",
        "agent_name": name,
        "framework": "anthropic",
        "project_root": "/tmp",
        "code_snapshot_b64": b64,
        "code_snapshot_hash": sha,
        "file_count": 1,
        "total_bytes": total,
        "snapshot_truncated": False,
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
    r = client.post("/v1/events", json={"events": [evt]})
    assert r.status_code == 200


def _runtime_event(
    agent_id: str,
    session_id: str,
    seq: int,
    hook: str,
    risk: str = "LOW",
    agent_name: str | None = None,
    **extra,
) -> dict:
    base = {
        "session_id": session_id,
        "agent_id": agent_id,
        "sequence": seq,
        "hook": hook,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "risk_hint": risk,
        "source": "sdk",
    }
    if hook == "on_session_start":
        base["agent_name"] = agent_name or agent_id
    if hook == "before_tool_use":
        base["tool_name"] = extra.get("tool_name", "echo")
    if hook == "on_final_output":
        base["final_response"] = extra.get("final_response", "done")
    return base


@pytest.fixture()
def app_client(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        monkeypatch.setenv("SAFER_DB_PATH", db_path)
        init_db_sync(db_path)
        import importlib

        import safer_backend.main as main_mod
        import safer_backend.storage.db as db_mod

        importlib.reload(db_mod)
        importlib.reload(main_mod)
        with TestClient(main_mod.app) as client:
            yield client


def test_active_endpoint_returns_only_unended_sessions(app_client: TestClient) -> None:
    _register(app_client, "agent_a", "Agent A")
    _register(app_client, "agent_b", "Agent B")

    # Session 1 on agent_a — started, not ended (active).
    e1 = _runtime_event("agent_a", "sess_alpha", 0, "on_session_start")
    e2 = _runtime_event("agent_a", "sess_alpha", 1, "before_tool_use")
    # Session 2 on agent_b — started and ended.
    e3 = _runtime_event("agent_b", "sess_beta", 0, "on_session_start")
    e4 = _runtime_event("agent_b", "sess_beta", 1, "on_final_output")
    e5 = {
        "session_id": "sess_beta",
        "agent_id": "agent_b",
        "sequence": 2,
        "hook": "on_session_end",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "risk_hint": "LOW",
        "source": "sdk",
        "total_duration_ms": 1234,
        "success": True,
    }
    app_client.post("/v1/events", json={"events": [e1, e2, e3, e4, e5]})

    r = app_client.get("/v1/sessions/active")
    assert r.status_code == 200
    body = r.json()
    session_ids = [row["session_id"] for row in body]
    assert session_ids == ["sess_alpha"]
    assert body[0]["agent_name"] == "Agent A"
    assert body[0]["last_event_hook"] == "before_tool_use"
    assert body[0]["recent_hooks"][-1] == "before_tool_use"


def test_active_endpoint_orders_by_most_recent_event(app_client: TestClient) -> None:
    _register(app_client, "agent_a", "Agent A")
    _register(app_client, "agent_b", "Agent B")

    # Start agent_a first, then agent_b.
    app_client.post(
        "/v1/events",
        json={
            "events": [
                _runtime_event("agent_a", "sess_1", 0, "on_session_start"),
                _runtime_event("agent_b", "sess_2", 0, "on_session_start"),
            ]
        },
    )
    # Now agent_a gets a fresh event — it should move to the top.
    app_client.post(
        "/v1/events",
        json={"events": [_runtime_event("agent_a", "sess_1", 1, "before_tool_use")]},
    )

    r = app_client.get("/v1/sessions/active")
    body = r.json()
    assert [row["session_id"] for row in body] == ["sess_1", "sess_2"]


def test_active_endpoint_separates_parallel_sessions_for_same_agent(
    app_client: TestClient,
) -> None:
    _register(app_client, "agent_x", "Agent X")
    app_client.post(
        "/v1/events",
        json={
            "events": [
                _runtime_event("agent_x", "sess_one", 0, "on_session_start"),
                _runtime_event("agent_x", "sess_two", 0, "on_session_start"),
            ]
        },
    )
    r = app_client.get("/v1/sessions/active")
    body = r.json()
    session_ids = {row["session_id"] for row in body}
    assert session_ids == {"sess_one", "sess_two"}
    # Same agent on both cards.
    assert all(row["agent_id"] == "agent_x" for row in body)


def test_active_endpoint_recent_hooks_limit(app_client: TestClient) -> None:
    _register(app_client, "agent_q", "Agent Q")
    # 25 events, ask for last 5.
    events = [_runtime_event("agent_q", "sess_q", 0, "on_session_start")]
    for i in range(1, 25):
        events.append(_runtime_event("agent_q", "sess_q", i, "before_tool_use"))
    app_client.post("/v1/events", json={"events": events})

    r = app_client.get("/v1/sessions/active?recent_hook_count=5")
    body = r.json()
    assert len(body) == 1
    assert len(body[0]["recent_hooks"]) == 5
