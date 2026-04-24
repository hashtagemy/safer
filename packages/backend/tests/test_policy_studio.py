"""Policy Studio tests — compiler, DAO round-trip, API endpoints.

Hermetic: the Anthropic client is faked, the DB is a tempfile.
"""

from __future__ import annotations

import importlib
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from safer_backend.models.findings import Severity
from safer_backend.models.flags import FlagCategory
from safer_backend.models.policies import (
    ActivePolicy,
    CompiledPolicy,
    GuardMode,
    PolicyTestCase,
)
from safer_backend.policy_studio.compiler import compile_policy, set_client


# ---------- fake Claude client ----------


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [SimpleNamespace(type="text", text=text)]
        self.usage = SimpleNamespace(
            input_tokens=1800,
            output_tokens=600,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=1800,
        )


class _FakeAnthropic:
    def __init__(self, canned: str | list[str]):
        self._queue = [canned] if isinstance(canned, str) else list(canned)
        self.calls: list[dict] = []

        async def _create(**kwargs):
            self.calls.append(kwargs)
            text = self._queue.pop(0) if self._queue else "{}"
            return _FakeResponse(text)

        self.messages = SimpleNamespace(create=_create)


@pytest.fixture(autouse=True)
def _reset_compiler_client():
    set_client(None)
    yield
    set_client(None)


def _valid_compiled_json() -> dict:
    return {
        "name": "no-email-egress",
        "nl_text": "Never let this agent send customer email addresses out.",
        "rule_json": {
            "kind": "pii_guard",
            "tools": None,
            "pii_types": ["EMAIL"],
        },
        "code_snippet": None,
        "flag_category": "COMPLIANCE",
        "flag": "pii_sent_external",
        "severity": "HIGH",
        "guard_mode": "enforce",
        "test_cases": [
            {
                "description": "Agent tries to email customer address — must block.",
                "event": {
                    "hook": "before_tool_use",
                    "tool_name": "send_email",
                    "args": {"to": "jane@example.com"},
                },
                "expected_block": True,
                "expected_flag": "pii_exposure",
            },
            {
                "description": "Agent looks up an order — must allow.",
                "event": {
                    "hook": "before_tool_use",
                    "tool_name": "get_order",
                    "args": {"id": "123"},
                },
                "expected_block": False,
                "expected_flag": None,
            },
        ],
    }


# ---------- compiler ----------


@pytest.mark.asyncio
async def test_compile_policy_happy_path(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    fake = _FakeAnthropic(json.dumps(_valid_compiled_json()))
    set_client(fake)

    compiled = await compile_policy(
        "Never let this agent send customer email addresses out."
    )
    assert isinstance(compiled, CompiledPolicy)
    assert compiled.rule_json["kind"] == "pii_guard"
    assert compiled.severity == Severity.HIGH
    assert compiled.guard_mode == GuardMode.ENFORCE
    assert len(compiled.test_cases) == 2
    assert len(fake.calls) == 1
    # Prompt cache must be requested.
    system = fake.calls[0]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    # `temperature` was deprecated by Anthropic; we must not send it.
    assert "temperature" not in fake.calls[0]


@pytest.mark.asyncio
async def test_compile_policy_repair_on_malformed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    bad = "Sure, here you go: not valid json!"
    good = json.dumps(_valid_compiled_json())
    fake = _FakeAnthropic([bad, good])
    set_client(fake)

    compiled = await compile_policy("Block PII emails.")
    assert compiled.rule_json["kind"] == "pii_guard"
    assert len(fake.calls) == 2  # original + one repair pass


@pytest.mark.asyncio
async def test_compile_policy_rejects_unknown_rule_kind(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    bad_payload = _valid_compiled_json()
    bad_payload["rule_json"] = {"kind": "magic_box", "anything": True}
    fake = _FakeAnthropic(json.dumps(bad_payload))
    set_client(fake)

    with pytest.raises(ValidationError):
        await compile_policy("Do something magical.")


@pytest.mark.asyncio
async def test_compile_policy_rejects_unknown_flag(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    bad_payload = _valid_compiled_json()
    bad_payload["flag"] = "this_flag_does_not_exist"
    fake = _FakeAnthropic(json.dumps(bad_payload))
    set_client(fake)

    with pytest.raises(ValidationError):
        await compile_policy("Block something.")


@pytest.mark.asyncio
async def test_compile_policy_accepts_custom_flag(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    payload = _valid_compiled_json()
    payload["flag"] = "custom_no_external_email"
    fake = _FakeAnthropic(json.dumps(payload))
    set_client(fake)

    compiled = await compile_policy("Block emails to external domains.")
    assert compiled.flag == "custom_no_external_email"


@pytest.mark.asyncio
async def test_compile_policy_empty_raises():
    with pytest.raises(ValueError):
        await compile_policy("   ")


@pytest.mark.asyncio
async def test_compile_policy_no_client_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    set_client(None)
    with pytest.raises(RuntimeError):
        await compile_policy("any text")


@pytest.mark.asyncio
async def test_compile_policy_preserves_user_text_when_model_paraphrases(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    payload = _valid_compiled_json()
    payload["nl_text"] = "A REWRITTEN VERSION OF THE POLICY"
    fake = _FakeAnthropic(json.dumps(payload))
    set_client(fake)

    user_text = "Block customer emails from leaving."
    compiled = await compile_policy(user_text)
    assert compiled.nl_text == user_text


# ---------- DAO round-trip ----------


@pytest.fixture()
def tmp_db(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "policies.db")
        monkeypatch.setenv("SAFER_DB_PATH", db_path)

        import safer_backend.storage.db as db_mod

        importlib.reload(db_mod)
        from safer_backend.storage.db import init_db_sync

        init_db_sync(db_path)

        import safer_backend.storage.dao as dao_mod

        importlib.reload(dao_mod)
        yield dao_mod


@pytest.mark.asyncio
async def test_dao_policy_round_trip(tmp_db):
    policy = ActivePolicy(
        name="no-email-egress",
        nl_text="Block customer emails.",
        rule_json={"kind": "pii_guard", "pii_types": ["EMAIL"]},
        flag_category=FlagCategory.COMPLIANCE,
        severity=Severity.HIGH,
        guard_mode=GuardMode.ENFORCE,
        test_cases=[
            PolicyTestCase(
                description="positive",
                event={"hook": "before_tool_use"},
                expected_block=True,
            )
        ],
    )
    await tmp_db.insert_policy(policy)

    rows = await tmp_db.list_policies()
    assert len(rows) == 1
    assert rows[0].policy_id == policy.policy_id
    assert rows[0].severity == Severity.HIGH
    assert rows[0].guard_mode == GuardMode.ENFORCE
    assert rows[0].rule_json["kind"] == "pii_guard"
    assert len(rows[0].test_cases) == 1

    changed = await tmp_db.deactivate_policy(policy.policy_id)
    assert changed is True

    active_rows = await tmp_db.list_policies(active_only=True)
    all_rows = await tmp_db.list_policies(active_only=False)
    assert len(active_rows) == 0
    assert len(all_rows) == 1
    assert all_rows[0].active is False


@pytest.mark.asyncio
async def test_dao_deactivate_missing_returns_false(tmp_db):
    assert await tmp_db.deactivate_policy("pol_missing") is False


@pytest.mark.asyncio
async def test_dao_agent_scoped_filter(tmp_db):
    # The agent must exist because policies.agent_id has a FK to agents.
    await tmp_db.upsert_agent("agent_a")

    global_policy = ActivePolicy(
        name="global",
        nl_text="global",
        rule_json={"kind": "tool_allowlist", "allowed": ["x"]},
        severity=Severity.MEDIUM,
    )
    scoped_policy = ActivePolicy(
        agent_id="agent_a",
        name="scoped",
        nl_text="scoped",
        rule_json={"kind": "tool_allowlist", "allowed": ["y"]},
        severity=Severity.MEDIUM,
    )
    await tmp_db.insert_policy(global_policy)
    await tmp_db.insert_policy(scoped_policy)

    # agent_a sees both global + its own
    a_rows = await tmp_db.list_policies(agent_id="agent_a")
    assert {r.name for r in a_rows} == {"global", "scoped"}

    # agent_b only sees global
    b_rows = await tmp_db.list_policies(agent_id="agent_b")
    assert {r.name for r in b_rows} == {"global"}


# ---------- API endpoints ----------


@pytest.fixture()
def app_client(monkeypatch):
    """Spin up the FastAPI app with a temp DB + fake Anthropic client."""
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "api.db")
        monkeypatch.setenv("SAFER_DB_PATH", db_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")

        from safer_backend.storage.db import init_db_sync

        init_db_sync(db_path)

        import safer_backend.storage.db as db_mod
        import safer_backend.storage.dao as dao_mod
        import safer_backend.policy_studio.api as api_mod
        import safer_backend.main as main_mod

        importlib.reload(db_mod)
        importlib.reload(dao_mod)
        importlib.reload(api_mod)
        importlib.reload(main_mod)

        # Install fake Anthropic client via the freshly-reloaded compiler module.
        import safer_backend.policy_studio.compiler as compiler_mod

        importlib.reload(compiler_mod)
        fake = _FakeAnthropic(json.dumps(_valid_compiled_json()))
        compiler_mod.set_client(fake)

        with TestClient(main_mod.app) as client:
            yield client, fake


def test_api_compile_then_activate_then_list_then_delete(app_client):
    client, _fake = app_client

    # 1. Compile
    resp = client.post(
        "/v1/policies/compile",
        json={"nl_text": "Block customer emails from leaving."},
    )
    assert resp.status_code == 200
    compiled = resp.json()
    assert compiled["rule_json"]["kind"] == "pii_guard"

    # 2. Activate
    resp = client.post(
        "/v1/policies/activate",
        json={"compiled": compiled, "agent_id": None},
    )
    assert resp.status_code == 200
    active = resp.json()
    assert active["active"] is True
    policy_id = active["policy_id"]

    # 3. List
    resp = client.get("/v1/policies")
    assert resp.status_code == 200
    assert any(p["policy_id"] == policy_id for p in resp.json()["policies"])

    # 4. Delete
    resp = client.delete(f"/v1/policies/{policy_id}")
    assert resp.status_code == 204

    # 5. List again — none active.
    resp = client.get("/v1/policies?active_only=true")
    assert all(p["policy_id"] != policy_id for p in resp.json()["policies"])


def test_api_compile_without_key_returns_503(app_client, monkeypatch):
    client, _fake = app_client

    # Remove the fake client so compile_policy sees no client available.
    import safer_backend.policy_studio.compiler as compiler_mod

    compiler_mod.set_client(None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    resp = client.post(
        "/v1/policies/compile",
        json={"nl_text": "Block something."},
    )
    assert resp.status_code == 503


def test_api_delete_missing_returns_404(app_client):
    client, _ = app_client
    resp = client.delete("/v1/policies/pol_doesnotexist")
    assert resp.status_code == 404
