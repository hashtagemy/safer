"""Red-Team Managed Agents bootstrap — idempotent provisioning of the
three role-specialised agents (Strategist + Attacker + Analyst) and the
shared cloud environment, with IDs cached in `managed_agents_config`.

Mirrors the inspector_managed_bootstrap test layout: a `_FakeClient`
exposes `beta.agents.create` / `beta.environments.create` without
hitting the network, and we drive the public `ensure_*` helpers."""

from __future__ import annotations

import importlib
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest


# ---------- fakes ----------


class _FakeAgents:
    def __init__(self):
        self.calls: list[dict] = []
        self._next_id = 0

    async def create(self, **kwargs):
        self._next_id += 1
        self.calls.append(kwargs)
        return SimpleNamespace(id=f"agent_{self._next_id:03d}", version=1)


class _FakeAgentsThatFails:
    async def create(self, **kwargs):
        raise RuntimeError("API blew up")


class _FakeEnvironments:
    def __init__(self):
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id="env_xyz")


class _FakeEnvironmentsThatFails:
    async def create(self, **kwargs):
        raise RuntimeError("env API blew up")


class _FakeBeta:
    def __init__(self, *, agents=None, environments=None):
        self.agents = agents or _FakeAgents()
        self.environments = environments or _FakeEnvironments()


class _FakeClient:
    def __init__(self, *, agents=None, environments=None):
        self.beta = _FakeBeta(agents=agents, environments=environments)


# ---------- shared fixture ----------


@pytest.fixture
def isolated_db(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "rt_bootstrap.db")
        monkeypatch.setenv("SAFER_DB_PATH", db_path)

        import safer_backend.storage.db as dbmod

        importlib.reload(dbmod)

        dbmod.init_db_sync(db_path)

        # Reload the bootstrap module AFTER the DB module so it sees
        # the patched SAFER_DB_PATH.
        import safer_backend.redteam.managed_bootstrap as mb

        importlib.reload(mb)
        yield db_path


# ---------- ensure_* — happy path ----------


@pytest.mark.asyncio
async def test_ensure_strategist_creates_then_caches(isolated_db, monkeypatch):
    import safer_backend.redteam.managed_bootstrap as mb

    client = _FakeClient()
    sid1 = await mb.ensure_strategist_agent(client)
    sid2 = await mb.ensure_strategist_agent(client)

    assert sid1 == sid2 == "agent_001"
    assert len(client.beta.agents.calls) == 1


@pytest.mark.asyncio
async def test_ensure_attacker_creates_then_caches(isolated_db, monkeypatch):
    import safer_backend.redteam.managed_bootstrap as mb

    client = _FakeClient()
    aid1 = await mb.ensure_attacker_agent(client)
    aid2 = await mb.ensure_attacker_agent(client)

    assert aid1 == aid2 == "agent_001"
    assert len(client.beta.agents.calls) == 1


@pytest.mark.asyncio
async def test_ensure_analyst_creates_then_caches(isolated_db, monkeypatch):
    import safer_backend.redteam.managed_bootstrap as mb

    client = _FakeClient()
    aid1 = await mb.ensure_analyst_agent(client)
    aid2 = await mb.ensure_analyst_agent(client)

    assert aid1 == aid2 == "agent_001"
    assert len(client.beta.agents.calls) == 1


@pytest.mark.asyncio
async def test_ensure_environment_creates_then_caches(isolated_db, monkeypatch):
    import safer_backend.redteam.managed_bootstrap as mb

    client = _FakeClient()
    eid1 = await mb.ensure_environment(client)
    eid2 = await mb.ensure_environment(client)

    assert eid1 == eid2 == "env_xyz"
    assert len(client.beta.environments.calls) == 1


@pytest.mark.asyncio
async def test_ensure_all_provisions_three_distinct_agents(
    isolated_db, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    import safer_backend.redteam.managed_bootstrap as mb

    client = _FakeClient()
    mb._set_beta_client_factory(lambda: client)
    try:
        ids = await mb.ensure_all()
    finally:
        mb._set_beta_client_factory(None)

    # Three distinct agent ids + the environment.
    assert ids["strategist_agent_id"] == "agent_001"
    assert ids["attacker_agent_id"] == "agent_002"
    assert ids["analyst_agent_id"] == "agent_003"
    assert ids["env_id"] == "env_xyz"

    # Three agent.create + one environment.create — exactly.
    assert len(client.beta.agents.calls) == 3
    assert len(client.beta.environments.calls) == 1


# ---------- ensure_* — model + system prompt sanity ----------


@pytest.mark.asyncio
async def test_strategist_uses_opus_and_strategist_prompt(isolated_db):
    import safer_backend.redteam.managed_bootstrap as mb

    client = _FakeClient()
    await mb.ensure_strategist_agent(client)

    call = client.beta.agents.calls[0]
    assert call["name"] == mb.STRATEGIST_AGENT_NAME
    assert call["model"] == "claude-opus-4-7"
    # System prompt loaded from disk — sanity-check a phrase that's
    # only in the strategist prompt:
    assert "Strategist" in call["system"]
    assert {"type": "agent_toolset_20260401"} in call["tools"]


@pytest.mark.asyncio
async def test_attacker_uses_opus_and_attacker_prompt(isolated_db):
    import safer_backend.redteam.managed_bootstrap as mb

    client = _FakeClient()
    await mb.ensure_attacker_agent(client)

    call = client.beta.agents.calls[0]
    assert call["name"] == mb.ATTACKER_AGENT_NAME
    assert call["model"] == "claude-opus-4-7"
    assert "Attacker" in call["system"]


@pytest.mark.asyncio
async def test_analyst_uses_sonnet_and_analyst_prompt(isolated_db):
    import safer_backend.redteam.managed_bootstrap as mb

    client = _FakeClient()
    await mb.ensure_analyst_agent(client)

    call = client.beta.agents.calls[0]
    assert call["name"] == mb.ANALYST_AGENT_NAME
    assert call["model"] == "claude-sonnet-4-6"
    assert "Analyst" in call["system"]


# ---------- ensure_* — error paths ----------


@pytest.mark.asyncio
async def test_missing_api_key_raises(isolated_db, monkeypatch):
    """Calling _beta_client() with no API key must surface a structured error."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import safer_backend.redteam.managed_bootstrap as mb

    # Ensure no test factory is left over.
    mb._set_beta_client_factory(None)
    with pytest.raises(mb.ManagedBootstrapError) as excinfo:
        # No client passed -> ensure_* falls back to _beta_client().
        await mb.ensure_strategist_agent(client=None)
    assert "ANTHROPIC_API_KEY" in str(excinfo.value)


@pytest.mark.asyncio
async def test_agents_create_failure_wraps_as_bootstrap_error(isolated_db):
    import safer_backend.redteam.managed_bootstrap as mb

    client = _FakeClient(agents=_FakeAgentsThatFails())
    with pytest.raises(mb.ManagedBootstrapError) as excinfo:
        await mb.ensure_strategist_agent(client)
    assert "agents.create failed" in str(excinfo.value)


@pytest.mark.asyncio
async def test_environments_create_failure_wraps_as_bootstrap_error(
    isolated_db,
):
    import safer_backend.redteam.managed_bootstrap as mb

    client = _FakeClient(environments=_FakeEnvironmentsThatFails())
    with pytest.raises(mb.ManagedBootstrapError) as excinfo:
        await mb.ensure_environment(client)
    assert "environments.create failed" in str(excinfo.value)


@pytest.mark.asyncio
async def test_agents_create_returning_no_id_raises(isolated_db):
    import safer_backend.redteam.managed_bootstrap as mb

    class _NoIdAgents:
        async def create(self, **kwargs):
            # Agent object with no `id` attribute and not a dict.
            return SimpleNamespace(version=1)

    client = _FakeClient(agents=_NoIdAgents())
    with pytest.raises(mb.ManagedBootstrapError) as excinfo:
        await mb.ensure_strategist_agent(client)
    assert "no id" in str(excinfo.value)


@pytest.mark.asyncio
async def test_beta_client_factory_override_is_used(isolated_db, monkeypatch):
    """`_set_beta_client_factory()` lets tests inject a fake client even
    without an API key — used by the orchestrator tests."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import safer_backend.redteam.managed_bootstrap as mb

    fake = _FakeClient()
    mb._set_beta_client_factory(lambda: fake)
    try:
        # No API key, but factory is set, so this MUST succeed.
        ids = await mb.ensure_all()
    finally:
        mb._set_beta_client_factory(None)
    assert ids["strategist_agent_id"]
