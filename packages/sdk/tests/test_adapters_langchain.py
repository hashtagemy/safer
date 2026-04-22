"""LangChain adapter tests.

Skipped when `langchain_core` isn't installed. The SDK itself remains
importable either way — that invariant is checked first.
"""

from __future__ import annotations

import importlib

import pytest


def test_adapters_package_imports_without_langchain():
    """`from safer.adapters import langchain` must NOT crash even when
    langchain-core isn't installed — the handler class defers the
    import to __init__."""
    mod = importlib.import_module("safer.adapters.langchain")
    assert hasattr(mod, "SaferCallbackHandler")


def test_handler_without_langchain_raises_on_construct():
    """Instantiating SaferCallbackHandler when langchain-core is missing
    should surface a helpful error, not a generic ImportError."""
    import safer.adapters.langchain as m

    try:
        import langchain_core  # noqa: F401

        pytest.skip("langchain_core is installed; this test is for the fallback path")
    except ImportError:
        pass

    with pytest.raises(ImportError, match="langchain-core"):
        m.SaferCallbackHandler(agent_id="agent_x")


# --- The rest of the file only runs if langchain_core is importable. ---

langchain_core = pytest.importorskip("langchain_core")


@pytest.fixture()
def recording_client(monkeypatch):
    """Capture every track_event call via a dummy client."""
    calls: list[dict] = []

    class _Dummy:
        def track_event(self, hook, payload, session_id=None, agent_id=None):
            calls.append(
                {
                    "hook": hook.value if hasattr(hook, "value") else str(hook),
                    "payload": payload,
                    "session_id": session_id,
                    "agent_id": agent_id,
                }
            )

    from safer import client as client_mod

    monkeypatch.setattr(client_mod, "_client", _Dummy(), raising=False)
    return calls


def test_handler_emits_9_hook_flow(recording_client):
    from langchain_core.agents import AgentAction, AgentFinish

    from safer.adapters.langchain import SaferCallbackHandler

    h = SaferCallbackHandler(agent_id="agent_demo", agent_name="Demo")

    # Typical AgentExecutor callback order.
    h.on_chain_start({"name": "chain"}, {"input": "hello"})
    h.on_llm_start(
        {"name": "ChatAnthropic", "kwargs": {"model": "claude-opus-4-7"}},
        ["you are helpful"],
        run_id="r1",
    )
    h.on_llm_end(
        _fake_llm_result(
            text="I'll call a tool.",
            usage={"prompt_tokens": 50, "completion_tokens": 10},
            model="claude-opus-4-7",
        ),
        run_id="r1",
    )
    h.on_agent_action(AgentAction(tool="read_file", tool_input="readme.md", log="reading"))
    h.on_tool_start({"name": "read_file"}, "readme.md", run_id="t1")
    h.on_tool_end("# SAFER", run_id="t1", name="read_file")
    h.on_llm_start(
        {"name": "ChatAnthropic", "kwargs": {"model": "claude-opus-4-7"}},
        ["summarise"],
        run_id="r2",
    )
    h.on_llm_end(
        _fake_llm_result(text="Done.", usage={"prompt_tokens": 20, "completion_tokens": 5}, model="claude-opus-4-7"),
        run_id="r2",
    )
    h.on_agent_finish(AgentFinish(return_values={"output": "Done."}, log="fin"))

    hooks = [c["hook"] for c in recording_client]
    # Must include each of the canonical hooks in order.
    assert hooks[0] == "on_session_start"
    assert "before_llm_call" in hooks
    assert "after_llm_call" in hooks
    assert "on_agent_decision" in hooks
    assert "before_tool_use" in hooks
    assert "after_tool_use" in hooks
    assert "on_final_output" in hooks
    assert hooks[-1] == "on_session_end"


def test_handler_emits_on_error(recording_client):
    from safer.adapters.langchain import SaferCallbackHandler

    h = SaferCallbackHandler(agent_id="agent_err")
    h.on_chain_start({"name": "chain"}, {"input": "go"})
    h.on_tool_error(RuntimeError("boom"))

    error_events = [c for c in recording_client if c["hook"] == "on_error"]
    assert len(error_events) == 1
    payload = error_events[0]["payload"]
    assert payload["error_type"] == "RuntimeError"
    assert "boom" in payload["message"]


# ---------- helpers ----------


def _fake_llm_result(*, text: str, usage: dict, model: str):
    """Build a minimal object shaped like LangChain's LLMResult."""
    from types import SimpleNamespace

    generation = SimpleNamespace(text=text, message=SimpleNamespace(content=text))
    return SimpleNamespace(
        generations=[[generation]],
        llm_output={"token_usage": usage, "model_name": model},
    )
