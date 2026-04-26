"""Tests for the on_agent_register onboarding hook.

Each test patches SaferClient.emit so we can assert which events the
instrument() call would have sent without actually opening a transport
to a backend.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import safer
from safer.client import clear_client
from safer.events import Hook, OnAgentRegisterPayload
from safer.instrument import _reset_registered_agents_for_tests


@pytest.fixture(autouse=True)
def _reset_state() -> Any:
    clear_client()
    _reset_registered_agents_for_tests()
    yield
    clear_client()
    _reset_registered_agents_for_tests()


def _capture_emissions(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    captured: list[Any] = []

    def _fake_emit(self: Any, event: Any) -> None:  # noqa: ARG001
        captured.append(event)

    monkeypatch.setattr("safer.client.SaferClient.emit", _fake_emit)
    return captured


def test_instrument_emits_on_agent_register_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")

    captured = _capture_emissions(monkeypatch)

    safer.instrument(
        api_url="http://127.0.0.1:59999",
        agent_id="agent_test",
        agent_name="Test Agent",
        project_root=str(tmp_path),
    )
    # Second call must not re-register the same agent.
    safer.instrument(api_url="http://127.0.0.1:59999", agent_id="agent_test")

    registers = [e for e in captured if isinstance(e, OnAgentRegisterPayload)]
    assert len(registers) == 1
    evt = registers[0]
    assert evt.hook == Hook.ON_AGENT_REGISTER
    assert evt.agent_id == "agent_test"
    assert evt.agent_name == "Test Agent"
    assert evt.file_count >= 1
    assert evt.code_snapshot_hash
    assert evt.code_snapshot_b64
    assert str(tmp_path) in evt.project_root


def test_auto_register_can_be_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    captured = _capture_emissions(monkeypatch)

    safer.instrument(
        api_url="http://127.0.0.1:59999",
        agent_id="agent_silent",
        project_root=str(tmp_path),
        auto_register=False,
    )

    assert not [e for e in captured if isinstance(e, OnAgentRegisterPayload)]


def test_second_agent_id_still_registers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    captured = _capture_emissions(monkeypatch)

    safer.instrument(
        api_url="http://127.0.0.1:59999",
        agent_id="agent_one",
        project_root=str(tmp_path),
    )
    # Different agent_id on the same client → should emit another register.
    safer.instrument(
        api_url="http://127.0.0.1:59999",
        agent_id="agent_two",
        project_root=str(tmp_path),
    )

    ids = [
        e.agent_id for e in captured if isinstance(e, OnAgentRegisterPayload)
    ]
    assert ids == ["agent_one", "agent_two"]


def test_second_instrument_call_carries_detected_framework(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second instrument() for a new agent_id must reuse the framework
    detected on the first call. Previously the hint fell back to
    "custom" because _register_adapters wasn't cached."""
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    captured = _capture_emissions(monkeypatch)

    safer.instrument(
        api_url="http://127.0.0.1:59999",
        agent_id="agent_one",
        project_root=str(tmp_path),
    )
    safer.instrument(
        api_url="http://127.0.0.1:59999",
        agent_id="agent_two",
        project_root=str(tmp_path),
    )

    registers = [e for e in captured if isinstance(e, OnAgentRegisterPayload)]
    assert len(registers) == 2
    assert registers[0].framework == registers[1].framework
    assert registers[1].framework in {
        "anthropic",
        "langchain",
        "openai",
        "openai-agents",
        "google-adk",
        "strands",
        "otel-bridge",
        "custom",
    }


def test_register_payload_carries_framework_and_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "main.py").write_text("print(1)\n", encoding="utf-8")
    captured = _capture_emissions(monkeypatch)

    safer.instrument(
        api_url="http://127.0.0.1:59999",
        agent_id="agent_p",
        project_root=str(tmp_path),
        system_prompt="You are a helpful test agent.",
    )

    registers = [e for e in captured if isinstance(e, OnAgentRegisterPayload)]
    assert len(registers) == 1
    evt = registers[0]
    assert evt.system_prompt == "You are a helpful test agent."
    # framework should be one of the known values; pytest env likely has anthropic
    assert evt.framework in {
        "anthropic",
        "langchain",
        "openai",
        "openai-agents",
        "google-adk",
        "strands",
        "otel-bridge",
        "custom",
    }


# ---------- framework auto-detection coverage ----------


def test_detects_google_adk_when_installed() -> None:
    """If google.adk is importable, _register_adapters must report it."""
    import importlib.util

    if importlib.util.find_spec("google.adk") is None:
        pytest.skip("google-adk not installed in this environment")

    from safer.instrument import _register_adapters

    class _DummyClient:
        pass

    label = _register_adapters(_DummyClient())
    # Framework-native detections win over raw-LLM SDKs, so when ADK is
    # installed the label must be at least in the detected set.
    assert label in {
        "openai-agents",
        "langchain",
        "google-adk",
        "strands",
    }, f"unexpected label with ADK installed: {label!r}"


def test_detects_strands_when_installed() -> None:
    import importlib.util

    if importlib.util.find_spec("strands") is None:
        pytest.skip("strands-agents not installed in this environment")

    from safer.instrument import _register_adapters

    class _DummyClient:
        pass

    label = _register_adapters(_DummyClient())
    assert label in {
        "openai-agents",
        "langchain",
        "google-adk",
        "strands",
    }


def test_otel_only_environment_returns_otel_bridge_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With only opentelemetry.sdk importable (and no LangChain / ADK /
    Strands / Anthropic / OpenAI), the label must fall through to
    `otel-bridge` — not `custom`."""
    import importlib.util

    original_find_spec = importlib.util.find_spec

    def _fake_find_spec(name: str, *args, **kwargs):
        # Hide every framework-native + raw-LLM SDK that the detector
        # probes for. Update this set whenever a new probe is added to
        # `_register_adapters` or this test will start returning the
        # newly-added label instead of the OTel fallback.
        hidden = {
            "anthropic",
            "openai",
            "agents",
            "langchain",
            "google.adk",
            "strands",
            "crewai",
            "boto3",
        }
        if name in hidden:
            return None
        return original_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec)

    from safer.instrument import _register_adapters

    class _DummyClient:
        pass

    # opentelemetry.sdk is installed (it's a transitive backend dep).
    assert original_find_spec("opentelemetry.sdk") is not None
    label = _register_adapters(_DummyClient())
    assert label == "otel-bridge"
