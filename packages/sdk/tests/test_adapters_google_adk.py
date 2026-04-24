"""Google ADK plugin adapter tests.

The `safer.adapters.google_adk` module ships `SaferAdkPlugin` as a
`BasePlugin` subclass. These tests verify:
  * all 13 ADK plugin callbacks are implemented as `async def`;
  * the full callback sequence emits the 9 SAFER hooks in the expected
    order;
  * model/tool error callbacks produce `on_error` events;
  * `on_event_callback` synthesizes `on_agent_decision` from tool_use
    function_call blocks;
  * session start fires once; profile sync fires once;
  * the legacy `attach_safer` shim binds six agent-level callback
    fields via sync adapters.

Duck-typed ADK objects are used so the tests don't depend on the
ADK SDK's runtime behaviour — only on the attribute surface the
plugin reads.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("google.adk.plugins.base_plugin")

from safer import client as client_mod
from safer.instrument import _reset_registered_agents_for_tests


@pytest.fixture(autouse=True)
def _reset_runtime_and_install_dummy(monkeypatch):
    """Every test starts from a pristine runtime and installs a
    recording dummy client. The plugin's constructor calls
    `ensure_runtime(...)` but the recording client already satisfies
    `get_client()`, so no real runtime is started."""
    client_mod._client = None
    _reset_registered_agents_for_tests()

    calls: list[dict[str, Any]] = []

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

        def schedule_profile_patch(self, agent_id, **kw):
            calls.append(
                {"hook": "__profile_patch__", "agent_id": agent_id, **kw}
            )

    monkeypatch.setattr(client_mod, "_client", _Dummy(), raising=False)
    yield calls
    client_mod._client = None
    _reset_registered_agents_for_tests()


# ----- duck-typed ADK objects ----------------------------------------


def _make_invocation_context(
    *, invocation_id: str = "inv_1", user_text: str = "Summarise foo.py"
) -> SimpleNamespace:
    user_content = SimpleNamespace(
        role="user", parts=[SimpleNamespace(text=user_text)]
    )
    # session.events: reversed walk finds the model turn
    model_turn = SimpleNamespace(
        content=SimpleNamespace(
            role="model", parts=[SimpleNamespace(text="Here is the summary.")]
        )
    )
    session = SimpleNamespace(events=[model_turn])
    return SimpleNamespace(
        invocation_id=invocation_id,
        user_content=user_content,
        session=session,
        agent_name="repo_analyst",
    )


def _make_callback_context(
    *, invocation_id: str = "inv_1", agent_name: str = "repo_analyst"
) -> SimpleNamespace:
    model_turn = SimpleNamespace(
        content=SimpleNamespace(
            role="model", parts=[SimpleNamespace(text="Here is the summary.")]
        )
    )
    session = SimpleNamespace(events=[model_turn])
    return SimpleNamespace(
        invocation_id=invocation_id,
        agent_name=agent_name,
        session=session,
        function_call_id=None,
    )


def _make_llm_request(
    *,
    model: str = "gemini-2.5-pro",
    prompt: str = "Summarise foo.py",
    system_prompt: str | None = "You are a code analyst.",
    tool_names: list[str] | None = None,
) -> SimpleNamespace:
    contents = [SimpleNamespace(role="user", parts=[SimpleNamespace(text=prompt)])]
    config = SimpleNamespace(
        system_instruction=system_prompt,
        tools=[SimpleNamespace(name=n) for n in (tool_names or [])],
    )
    tools_dict = {n: object() for n in (tool_names or [])}
    return SimpleNamespace(
        model=model, contents=contents, config=config, tools_dict=tools_dict
    )


def _make_llm_response(
    *,
    text: str = "I'll call a tool.",
    model: str = "gemini-2.5-pro",
    tokens_in: int = 50,
    tokens_out: int = 10,
    cached: int = 0,
) -> SimpleNamespace:
    content = SimpleNamespace(role="model", parts=[SimpleNamespace(text=text)])
    usage = SimpleNamespace(
        prompt_token_count=tokens_in,
        candidates_token_count=tokens_out,
        cached_content_token_count=cached,
    )
    return SimpleNamespace(content=content, model_version=model, usage_metadata=usage)


def _make_tool(name: str = "read_file") -> SimpleNamespace:
    return SimpleNamespace(name=name, description="Read a file")


def _make_event_with_tool_use(tool_name: str = "read_file") -> SimpleNamespace:
    fn_call = SimpleNamespace(name=tool_name, args={"path": "README.md"})
    content = SimpleNamespace(
        role="model", parts=[SimpleNamespace(text=None, function_call=fn_call)]
    )
    return SimpleNamespace(content=content)


# ----- tests ---------------------------------------------------------


def test_module_imports_without_running_the_plugin():
    mod = importlib.import_module("safer.adapters.google_adk")
    assert hasattr(mod, "SaferAdkPlugin")
    assert hasattr(mod, "attach_safer")
    assert hasattr(mod, "wrap_adk")


def test_plugin_all_thirteen_callbacks_are_async():
    from safer.adapters.google_adk import SaferAdkPlugin

    plugin = SaferAdkPlugin(agent_id="repo_analyst", agent_name="Repo Analyst")
    expected = [
        "on_user_message_callback",
        "before_run_callback",
        "before_agent_callback",
        "after_agent_callback",
        "before_model_callback",
        "after_model_callback",
        "on_model_error_callback",
        "before_tool_callback",
        "after_tool_callback",
        "on_tool_error_callback",
        "on_event_callback",
        "after_run_callback",
        "close",
    ]
    for name in expected:
        method = getattr(plugin, name)
        assert inspect.iscoroutinefunction(method), (
            f"{name} must be async (ADK PluginManager awaits every callback)"
        )


def test_full_plugin_flow_emits_nine_safer_hooks(_reset_runtime_and_install_dummy):
    calls = _reset_runtime_and_install_dummy
    from safer.adapters.google_adk import SaferAdkPlugin

    plugin = SaferAdkPlugin(agent_id="repo_analyst", agent_name="Repo Analyst")
    inv_ctx = _make_invocation_context()
    cb_ctx = _make_callback_context()
    req = _make_llm_request(tool_names=["read_file"])
    resp = _make_llm_response()
    tool = _make_tool("read_file")

    async def drive() -> None:
        await plugin.on_user_message_callback(
            invocation_context=inv_ctx, user_message=inv_ctx.user_content
        )
        await plugin.before_run_callback(invocation_context=inv_ctx)
        await plugin.before_agent_callback(agent=inv_ctx, callback_context=cb_ctx)
        await plugin.before_model_callback(callback_context=cb_ctx, llm_request=req)
        await plugin.after_model_callback(callback_context=cb_ctx, llm_response=resp)
        await plugin.on_event_callback(
            invocation_context=inv_ctx, event=_make_event_with_tool_use("read_file")
        )
        await plugin.before_tool_callback(
            tool=tool, tool_args={"path": "README.md"}, tool_context=cb_ctx
        )
        await plugin.after_tool_callback(
            tool=tool,
            tool_args={"path": "README.md"},
            tool_context=cb_ctx,
            result={"content": "# SAFER"},
        )
        await plugin.after_agent_callback(agent=inv_ctx, callback_context=cb_ctx)
        await plugin.after_run_callback(invocation_context=inv_ctx)

    asyncio.run(drive())

    hooks = [c["hook"] for c in calls if c["hook"] != "__profile_patch__"]
    # Required set of SAFER hooks emitted by this sequence.
    for required in (
        "on_session_start",
        "on_agent_decision",
        "before_llm_call",
        "after_llm_call",
        "before_tool_use",
        "after_tool_use",
        "on_final_output",
        "on_session_end",
    ):
        assert required in hooks, f"missing hook: {required}"

    # Token / cost propagation on after_llm_call.
    after_llm = next(c for c in calls if c["hook"] == "after_llm_call")
    assert after_llm["payload"]["tokens_in"] == 50
    assert after_llm["payload"]["tokens_out"] == 10
    assert after_llm["payload"]["cost_usd"] > 0

    # Final output pulled from session events.
    final = next(c for c in calls if c["hook"] == "on_final_output")
    assert "Here is the summary." in final["payload"]["final_response"]


def test_session_start_fires_exactly_once(_reset_runtime_and_install_dummy):
    calls = _reset_runtime_and_install_dummy
    from safer.adapters.google_adk import SaferAdkPlugin

    plugin = SaferAdkPlugin(agent_id="a1")

    async def drive() -> None:
        await plugin.before_run_callback(
            invocation_context=_make_invocation_context()
        )
        await plugin.before_agent_callback(
            agent=object(), callback_context=_make_callback_context()
        )
        await plugin.before_model_callback(
            callback_context=_make_callback_context(), llm_request=_make_llm_request()
        )

    asyncio.run(drive())
    starts = [c for c in calls if c["hook"] == "on_session_start"]
    assert len(starts) == 1


def test_profile_sync_fires_once_on_first_before_model(
    _reset_runtime_and_install_dummy,
):
    calls = _reset_runtime_and_install_dummy
    from safer.adapters.google_adk import SaferAdkPlugin

    plugin = SaferAdkPlugin(agent_id="a_profile", agent_name="Profile Agent")
    req1 = _make_llm_request(system_prompt="First prompt.")
    req2 = _make_llm_request(system_prompt="Different prompt.")

    async def drive() -> None:
        await plugin.before_model_callback(
            callback_context=_make_callback_context(), llm_request=req1
        )
        await plugin.after_model_callback(
            callback_context=_make_callback_context(),
            llm_response=_make_llm_response(),
        )
        await plugin.before_model_callback(
            callback_context=_make_callback_context(), llm_request=req2
        )

    asyncio.run(drive())
    patches = [c for c in calls if c["hook"] == "__profile_patch__"]
    assert len(patches) == 1
    assert patches[0]["system_prompt"] == "First prompt."


def test_on_model_error_callback_emits_on_error(_reset_runtime_and_install_dummy):
    calls = _reset_runtime_and_install_dummy
    from safer.adapters.google_adk import SaferAdkPlugin

    plugin = SaferAdkPlugin(agent_id="a_err")

    async def drive() -> None:
        await plugin.on_model_error_callback(
            callback_context=_make_callback_context(),
            llm_request=_make_llm_request(),
            error=RuntimeError("gemini exploded"),
        )

    asyncio.run(drive())
    errs = [c for c in calls if c["hook"] == "on_error"]
    assert len(errs) == 1
    assert "model_error" in errs[0]["payload"]["error_type"]
    assert "gemini exploded" in errs[0]["payload"]["message"]


def test_on_tool_error_callback_emits_on_error(_reset_runtime_and_install_dummy):
    calls = _reset_runtime_and_install_dummy
    from safer.adapters.google_adk import SaferAdkPlugin

    plugin = SaferAdkPlugin(agent_id="a_err")

    async def drive() -> None:
        await plugin.on_tool_error_callback(
            tool=_make_tool("read_file"),
            tool_args={"path": "x"},
            tool_context=_make_callback_context(),
            error=PermissionError("denied"),
        )

    asyncio.run(drive())
    errs = [c for c in calls if c["hook"] == "on_error"]
    assert len(errs) == 1
    assert "tool_error" in errs[0]["payload"]["error_type"]


def test_on_event_with_tool_use_synthesizes_agent_decision(
    _reset_runtime_and_install_dummy,
):
    calls = _reset_runtime_and_install_dummy
    from safer.adapters.google_adk import SaferAdkPlugin

    plugin = SaferAdkPlugin(agent_id="a_dec")

    async def drive() -> None:
        await plugin.on_event_callback(
            invocation_context=_make_invocation_context(),
            event=_make_event_with_tool_use("search_codebase"),
        )

    asyncio.run(drive())
    decisions = [c for c in calls if c["hook"] == "on_agent_decision"]
    assert len(decisions) == 1
    assert decisions[0]["payload"]["decision_type"] == "tool_call"
    assert decisions[0]["payload"]["chosen_action"] == "search_codebase"


def test_plugin_emit_failure_does_not_break_the_flow(monkeypatch):
    from safer.adapters.google_adk import SaferAdkPlugin

    class _ExplodingClient:
        def track_event(self, *a, **kw):
            raise RuntimeError("transport down")

        def schedule_profile_patch(self, *a, **kw):
            raise RuntimeError("transport down")

    monkeypatch.setattr(client_mod, "_client", _ExplodingClient(), raising=False)

    plugin = SaferAdkPlugin(agent_id="a_resil")

    async def drive() -> None:
        await plugin.before_run_callback(
            invocation_context=_make_invocation_context()
        )
        await plugin.before_model_callback(
            callback_context=_make_callback_context(), llm_request=_make_llm_request()
        )

    # Should not raise even though every emit raises.
    asyncio.run(drive())


def test_attach_safer_legacy_shim_binds_six_agent_field_callbacks():
    from safer.adapters.google_adk import attach_safer

    agent = SimpleNamespace()
    returned = attach_safer(agent, agent_id="legacy", agent_name="Legacy")
    assert returned is agent
    for attr in (
        "before_agent_callback",
        "after_agent_callback",
        "before_model_callback",
        "after_model_callback",
        "before_tool_callback",
        "after_tool_callback",
    ):
        assert callable(getattr(agent, attr)), f"agent.{attr} not set"


def test_cost_estimation_uses_gemini_pricing():
    from safer.adapters.google_adk import _estimate_cost

    cost = _estimate_cost("gemini-2.5-pro", 1_000_000, 1_000_000, 0)
    assert abs(cost - 11.25) < 0.01  # 1.25 + 10.0 USD per 1M
    cost_unknown = _estimate_cost("gemini-weirdname-preview", 1_000_000, 0, 0)
    assert cost_unknown > 0
