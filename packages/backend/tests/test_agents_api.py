"""End-to-end tests for the Agent Registry — ingest + REST + DAO."""

from __future__ import annotations

import base64
import gzip
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from safer_backend.storage.db import init_db_sync


def _make_snapshot(files: dict[str, str]) -> tuple[str, str, int, int]:
    """Return (b64, sha256, file_count, total_bytes) just like the SDK would."""
    import hashlib

    ordered = {k: files[k] for k in sorted(files)}
    raw = json.dumps(ordered, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    gz = gzip.compress(raw, compresslevel=6, mtime=0)
    b64 = base64.b64encode(gz).decode("ascii")
    total = sum(len(v.encode("utf-8")) for v in ordered.values())
    return b64, sha, len(ordered), total


def _register_event(agent_id: str, files: dict[str, str], **overrides: object) -> dict:
    b64, sha, count, total = _make_snapshot(files)
    base = {
        "session_id": f"boot_{agent_id}",
        "agent_id": agent_id,
        "sequence": 0,
        "hook": "on_agent_register",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "risk_hint": "LOW",
        "source": "sdk",
        "agent_name": overrides.get("agent_name", f"Agent {agent_id}"),
        "agent_version": overrides.get("agent_version"),
        "framework": overrides.get("framework", "anthropic"),
        "system_prompt": overrides.get("system_prompt"),
        "project_root": overrides.get("project_root", "/tmp/test-project"),
        "code_snapshot_b64": b64,
        "code_snapshot_hash": sha,
        "file_count": count,
        "total_bytes": total,
        "snapshot_truncated": False,
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
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


def test_register_event_populates_agents_table(app_client: TestClient) -> None:
    evt = _register_event("agent_alpha", {"main.py": "print('hi')\n"})
    r = app_client.post("/v1/events", json={"events": [evt]})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 1

    r = app_client.get("/v1/agents")
    assert r.status_code == 200
    agents = r.json()
    assert len(agents) == 1
    assert agents[0]["agent_id"] == "agent_alpha"
    assert agents[0]["framework"] == "anthropic"
    assert agents[0]["file_count"] == 1


def test_register_is_not_persisted_as_runtime_event(app_client: TestClient) -> None:
    evt = _register_event("agent_beta", {"a.py": ""})
    app_client.post("/v1/events", json={"events": [evt]})

    # The sessions table should not have a "boot_*" session row.
    r = app_client.get("/v1/agents/agent_beta/sessions")
    assert r.status_code == 200
    assert r.json() == []


def test_register_is_idempotent_on_hash(app_client: TestClient) -> None:
    evt = _register_event("agent_gamma", {"main.py": "pass\n"})
    app_client.post("/v1/events", json={"events": [evt]})
    # Re-post an identical event (same hash).
    app_client.post("/v1/events", json={"events": [evt]})
    r = app_client.get("/v1/agents")
    ids = [a["agent_id"] for a in r.json()]
    assert ids.count("agent_gamma") == 1


def test_register_updates_snapshot_when_hash_changes(app_client: TestClient) -> None:
    evt1 = _register_event("agent_delta", {"main.py": "v1\n"})
    app_client.post("/v1/events", json={"events": [evt1]})
    evt2 = _register_event("agent_delta", {"main.py": "v2-changed\n"})
    app_client.post("/v1/events", json={"events": [evt2]})

    r = app_client.get("/v1/agents/agent_delta")
    assert r.status_code == 200
    rec = r.json()
    assert rec["code_snapshot_hash"] == evt2["code_snapshot_hash"]


def test_detail_endpoints_404_for_unknown_agent(app_client: TestClient) -> None:
    for path in (
        "/v1/agents/nope",
        "/v1/agents/nope/sessions",
        "/v1/agents/nope/redteam-reports",
    ):
        assert app_client.get(path).status_code == 404
    assert (
        app_client.patch(
            "/v1/agents/nope/profile", json={"system_prompt": "x"}
        ).status_code
        == 404
    )


def test_profile_patch_updates_system_prompt(app_client: TestClient) -> None:
    evt = _register_event(
        "agent_eps", {"main.py": "pass\n"}, system_prompt="initial"
    )
    app_client.post("/v1/events", json={"events": [evt]})

    r = app_client.patch(
        "/v1/agents/agent_eps/profile",
        json={"system_prompt": "updated prompt"},
    )
    assert r.status_code == 200
    assert r.json()["system_prompt"] == "updated prompt"

    # Name and version left alone.
    assert r.json()["name"] == "Agent agent_eps"


def test_last_seen_advances_on_runtime_event(app_client: TestClient) -> None:
    reg = _register_event("agent_zeta", {"a.py": ""})
    app_client.post("/v1/events", json={"events": [reg]})

    r1 = app_client.get("/v1/agents/agent_zeta").json()
    ts1 = r1["last_seen_at"]

    runtime = {
        "session_id": "sess_z1",
        "agent_id": "agent_zeta",
        "sequence": 0,
        "hook": "on_session_start",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "risk_hint": "LOW",
        "source": "sdk",
        "agent_name": "Agent zeta",
    }
    app_client.post("/v1/events", json={"events": [runtime]})

    r2 = app_client.get("/v1/agents/agent_zeta").json()
    ts2 = r2["last_seen_at"]
    assert ts2 is not None
    assert ts2 >= ts1  # monotonic (string ISO compare is safe for UTC)
