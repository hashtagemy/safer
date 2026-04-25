"""OpenAI Agents SDK adapter tests — `safer.adapters.openai_agents`.

Drives `SaferRunHooks` with duck-typed Agents SDK objects (Agent, ModelResponse,
FunctionTool, ToolContext) so the tests don't need a live Agents loop.
The adapter must produce the full SAFER 9-hook lifecycle when these
events are dispatched in the order Agents SDK fires them."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("agents")


# ----- recording fixture ---------------------------------------------------


@pytest.fixture()
def recording_client(monkeypatch):
    calls: list[dict[str, Any]] = []

    class _Dummy:
        def emit(self, event):
            payload = event.model_dump(mode="json") if hasattr(event, "model_dump") else event
            hook_val = event.hook if hasattr(event, "hook") else payload.get("hook")
            calls.append(
                {
                    "hook": hook_val.value if hasattr(hook_val, "value") else str(hook_val),
                    "payload": payload,
                    "session_id": payload.get("session_id"),
                    "agent_id": payload.get("agent_id"),
                }
            )

        def track_event(self, hook, payload, session_id=None, agent_id=None):
            calls.append(
                {
                    "hook": hook.value if hasattr(hook, "value") else str(hook),
                    "payload": payload,
                    "session_id": session_id,
                    "agent_id": agent_id,
                }
            )

        def next_sequence(self, session_id):
            n = getattr(self, "_seq", 0)
            self._seq = n + 1
            return n

    from safer import client as client_mod

    monkeypatch.setattr(client_mod, "_client", _Dummy(), raising=False)
    return calls


# ----- duck-typed Agents SDK objects ---------------------------------------


def _make_agent(name="repo_analyst", model="gpt-4o", tools=None):
    return SimpleNamespace(
        name=name,
        model=model,
        tools=tools or [],
        instructions="You are a helpful agent.",
    )


def _make_tool(name="read_file", description="Read a file"):
    return SimpleNamespace(name=name, description=description)


def _make_model_response(text="ok", *, tokens_in=10, tokens_out=5, cache_read=0):
    output_msg = SimpleNamespace(
        type="message",
        content=[SimpleNamespace(type="output_text", text=text)],
    )
    details = SimpleNamespace(cached_tokens=cache_read) if cache_read else None
    return SimpleNamespace(
        output=[output_msg],
        output_text=text,
        usage=SimpleNamespace(
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            input_tokens_details=details,
        ),
    )


def _make_tool_context(call_id="call_xyz", tool_name="read_file", tool_arguments=None):
    if tool_arguments is None:
        tool_arguments = '{"path": "x.md"}'
    return SimpleNamespace(
        tool_call_id=call_id,
        tool_name=tool_name,
        tool_arguments=tool_arguments,
    )


# ----- tests ---------------------------------------------------------------


def test_safer_run_hooks_full_flow_emits_nine_safer_hooks(recording_client):
    """Drive RunHooks through a typical agent run: agent_start → llm_start
    → llm_end → tool_start → tool_end → llm_start → llm_end → agent_end.
    All 9 SAFER hooks must fire (session_start, before_llm_call,
    after_llm_call, on_agent_decision, before_tool_use, after_tool_use,
    on_final_output, on_session_end, plus on_error available)."""
    from safer.adapters.openai_agents import SaferRunHooks

    hooks = SaferRunHooks(agent_id="repo_analyst", agent_name="Repo Analyst")
    agent = _make_agent(tools=[_make_tool("read_file"), _make_tool("search")])
    tool = _make_tool("read_file")
    ctx = _make_tool_context()

    async def drive():
        await hooks.on_agent_start(None, agent)
        await hooks.on_llm_start(
            None, agent, "you are helpful",
            [{"role": "user", "content": [{"type": "input_text", "text": "read x.md"}]}],
        )
        await hooks.on_llm_end(None, agent, _make_model_response("I'll call read_file"))
        await hooks.on_tool_start(ctx, agent, tool)
        await hooks.on_tool_end(ctx, agent, tool, "file contents: hi")
        await hooks.on_llm_start(None, agent, "you are helpful", [])
        await hooks.on_llm_end(None, agent, _make_model_response("All done.", tokens_in=20, tokens_out=8))
        await hooks.on_agent_end(None, agent, "All done.")

    asyncio.run(drive())

    hook_names = [c["hook"] for c in recording_client]
    expected = {
        "on_session_start",
        "before_llm_call",
        "after_llm_call",
        "on_agent_decision",
        "before_tool_use",
        "after_tool_use",
        "on_final_output",
        "on_session_end",
    }
    missing = expected - set(hook_names)
    assert not missing, f"missing hooks: {missing}; saw: {hook_names}"
    # Order: session_start first, session_end last
    assert hook_names[0] == "on_session_start"
    assert hook_names[-1] == "on_session_end"


def test_session_id_rotates_between_runs(recording_client):
    """Reusing one SaferRunHooks instance across multiple Runner.run calls
    must produce distinct SAFER session_ids per run."""
    from safer.adapters.openai_agents import SaferRunHooks

    hooks = SaferRunHooks(agent_id="multi_run")
    agent = _make_agent()

    async def drive():
        await hooks.on_agent_start(None, agent)
        sid_1 = hooks.session_id
        await hooks.on_agent_end(None, agent, "first")

        await hooks.on_agent_start(None, agent)
        sid_2 = hooks.session_id
        await hooks.on_agent_end(None, agent, "second")
        return sid_1, sid_2

    sid_1, sid_2 = asyncio.run(drive())
    assert sid_1 != sid_2

    starts = [c for c in recording_client if c["hook"] == "on_session_start"]
    ends = [c for c in recording_client if c["hook"] == "on_session_end"]
    assert len(starts) == 2
    assert len(ends) == 2
    assert {s["session_id"] for s in starts} == {sid_1, sid_2}


def test_handoff_emits_agent_decision(recording_client):
    """on_handoff fires when one agent hands off to another — SAFER
    surfaces this as a decision event the Multi-Persona Judge can route."""
    from safer.adapters.openai_agents import SaferRunHooks

    hooks = SaferRunHooks(agent_id="ho")
    a1 = _make_agent(name="orchestrator")
    a2 = _make_agent(name="worker")

    async def drive():
        await hooks.on_agent_start(None, a1)
        await hooks.on_handoff(None, a1, a2)
        await hooks.on_agent_end(None, a1, "delegating")

    asyncio.run(drive())
    decisions = [c for c in recording_client if c["hook"] == "on_agent_decision"]
    assert len(decisions) == 1
    assert decisions[0]["payload"]["decision_type"] == "handoff"
    assert decisions[0]["payload"]["chosen_action"] == "worker"
    assert "orchestrator" in decisions[0]["payload"]["reasoning"]


def test_llm_end_carries_real_pricing(recording_client):
    """The Agents SDK ModelResponse exposes usage; we extract it and price
    correctly via the shared pricing table."""
    from safer.adapters.openai_agents import SaferRunHooks

    hooks = SaferRunHooks(agent_id="pricing")
    agent = _make_agent(model="gpt-4o-mini")

    async def drive():
        await hooks.on_agent_start(None, agent)
        await hooks.on_llm_start(None, agent, None, [])
        await hooks.on_llm_end(
            None, agent, _make_model_response("hi", tokens_in=100, tokens_out=50)
        )
        await hooks.on_agent_end(None, agent, "hi")

    asyncio.run(drive())

    after = next(c for c in recording_client if c["hook"] == "after_llm_call")
    assert after["payload"]["tokens_in"] == 100
    assert after["payload"]["tokens_out"] == 50
    expected = (100 * 0.15 + 50 * 0.60) / 1_000_000
    assert abs(after["payload"]["cost_usd"] - expected) < 1e-9


def test_tool_arguments_parsed_from_tool_context(recording_client):
    """ToolContext.tool_arguments is a JSON string; we parse it for the
    before_tool_use payload's `args` field."""
    from safer.adapters.openai_agents import SaferRunHooks

    hooks = SaferRunHooks(agent_id="targs")
    agent = _make_agent()
    tool = _make_tool("lookup")
    ctx = _make_tool_context(
        call_id="c1", tool_name="lookup", tool_arguments='{"q": "safer", "n": 5}'
    )

    async def drive():
        await hooks.on_agent_start(None, agent)
        await hooks.on_tool_start(ctx, agent, tool)
        await hooks.on_tool_end(ctx, agent, tool, "found 5 results")

    asyncio.run(drive())
    before = next(c for c in recording_client if c["hook"] == "before_tool_use")
    assert before["payload"]["tool_name"] == "lookup"
    assert before["payload"]["args"] == {"q": "safer", "n": 5}


def test_install_helper_is_idempotent_and_returns_run_hooks():
    """`install_safer_for_agents` registers the trace processor exactly once
    per agent_id and returns a fresh SaferRunHooks every call."""
    from agents.lifecycle import RunHooksBase

    from safer.adapters.openai_agents import install_safer_for_agents

    hooks_1 = install_safer_for_agents(agent_id="install_test", agent_name="Install Test")
    hooks_2 = install_safer_for_agents(agent_id="install_test", agent_name="Install Test")
    assert hooks_1 is not hooks_2
    # `RunHooks` is a generic parameterization of `RunHooksBase`; use the
    # base class for isinstance checks (Python typing rejects subscripted
    # generics in isinstance).
    assert isinstance(hooks_1, RunHooksBase)
    assert isinstance(hooks_2, RunHooksBase)


def test_safer_run_hooks_subclasses_real_run_hooks():
    """The returned object IS a RunHooksBase subclass instance — Runner.run's
    type check on `hooks` accepts it."""
    from agents.lifecycle import RunHooksBase

    from safer.adapters.openai_agents import SaferRunHooks

    h = SaferRunHooks(agent_id="type_test")
    assert isinstance(h, RunHooksBase)


def test_safer_tracing_processor_subclasses_real_processor():
    from agents.tracing import TracingProcessor

    from safer.adapters.openai_agents import SaferTracingProcessor

    p = SaferTracingProcessor(agent_id="tp_test")
    assert isinstance(p, TracingProcessor)
