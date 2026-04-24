"""Inspector Managed Agents bootstrap — idempotent helpers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from safer_backend.storage import init_db


@pytest.fixture
def isolated_db(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "bootstrap.db")
        monkeypatch.setenv("SAFER_DB_PATH", db_path)
        # Reload the module-level constant that `get_db` reads.
        import importlib
        import safer_backend.storage.db as dbmod

        importlib.reload(dbmod)
        yield db_path


class _FakeAgents:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id="agent_abc123", version=1)


class _FakeEnvironments:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id="env_abc123")


class _FakeBeta:
    def __init__(self):
        self.agents = _FakeAgents()
        self.environments = _FakeEnvironments()


class _FakeClient:
    def __init__(self):
        self.beta = _FakeBeta()


@pytest.fixture
def fake_memory_store_http(monkeypatch):
    """Replace the raw-HTTP memory-store creator with a capture.

    Matches the real function's signature: takes a payload dict and
    returns a dict shaped like the Anthropic API response.
    """
    calls: list[dict] = []

    async def _fake(payload):
        calls.append(payload)
        return {"id": "memstore_abc123", "name": payload.get("name")}

    import safer_backend.inspector.managed_bootstrap as mb_pre

    monkeypatch.setattr(mb_pre, "_raw_post_memory_store", _fake)
    return calls


@pytest.mark.asyncio
async def test_ensure_all_creates_then_caches(
    isolated_db, monkeypatch, fake_memory_store_http
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    await init_db(isolated_db)

    import importlib
    import safer_backend.inspector.managed_bootstrap as mb

    importlib.reload(mb)

    # Reload re-imported the module, so the fixture's monkeypatch is
    # against the pre-reload module object. Re-apply on the fresh one.
    async def _fake(payload):
        fake_memory_store_http.append(payload)
        return {"id": "memstore_abc123", "name": payload.get("name")}

    monkeypatch.setattr(mb, "_raw_post_memory_store", _fake)

    client = _FakeClient()

    agent_id = await mb.ensure_inspector_agent(client)
    store_id = await mb.ensure_memory_store()
    env_id = await mb.ensure_environment(client)

    assert agent_id == "agent_abc123"
    assert store_id == "memstore_abc123"
    assert env_id == "env_abc123"

    # Second pass: no new API calls, same IDs.
    agent_id2 = await mb.ensure_inspector_agent(client)
    store_id2 = await mb.ensure_memory_store()
    env_id2 = await mb.ensure_environment(client)

    assert (agent_id2, store_id2, env_id2) == (agent_id, store_id, env_id)
    assert len(client.beta.agents.calls) == 1
    assert len(fake_memory_store_http) == 1
    assert len(client.beta.environments.calls) == 1


@pytest.mark.asyncio
async def test_agent_create_uses_inspector_system_prompt(isolated_db, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    await init_db(isolated_db)

    import importlib
    import safer_backend.inspector.managed_bootstrap as mb

    importlib.reload(mb)

    client = _FakeClient()
    await mb.ensure_inspector_agent(client)

    call = client.beta.agents.calls[0]
    assert call["name"] == "SAFER Inspector"
    assert call["model"] == "claude-opus-4-7"
    assert "Security Auditor" in call["system"]
    assert "Compliance Officer" in call["system"]
    assert "Policy Warden" in call["system"]
    # Toolset required for memory mounts:
    assert {"type": "agent_toolset_20260401"} in call["tools"]


@pytest.mark.asyncio
async def test_missing_api_key_raises(isolated_db, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    await init_db(isolated_db)

    import importlib
    import safer_backend.inspector.managed_bootstrap as mb

    importlib.reload(mb)

    with pytest.raises(mb.ManagedBootstrapError):
        # No client injected -> tries to build one from env -> fails.
        await mb.ensure_inspector_agent(client=None)


@pytest.mark.asyncio
async def test_memory_store_post_raises_on_non_2xx(isolated_db, monkeypatch):
    """Real httpx path: a 4xx should surface as ManagedBootstrapError."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    await init_db(isolated_db)

    import importlib
    import safer_backend.inspector.managed_bootstrap as mb

    importlib.reload(mb)

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **kw):
            return SimpleNamespace(
                status_code=403,
                text='{"error":{"type":"forbidden","message":"no beta"}}',
                json=lambda: {},
            )

    monkeypatch.setattr(mb.httpx, "AsyncClient", _FakeAsyncClient)

    with pytest.raises(mb.ManagedBootstrapError) as excinfo:
        await mb.ensure_memory_store()
    assert "403" in str(excinfo.value)
