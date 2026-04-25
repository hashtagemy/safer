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
    """Drive a full AgentExecutor turn; verify all 9 SAFER hooks fire."""
    from langchain_core.agents import AgentAction, AgentFinish

    from safer.adapters.langchain import SaferCallbackHandler

    h = SaferCallbackHandler(agent_id="agent_demo", agent_name="Demo")

    # AgentExecutor outer chain (parent_run_id is None = SAFER session start)
    h.on_chain_start({"name": "AgentExecutor"}, {"input": "hello"}, run_id="root", parent_run_id=None)
    h.on_llm_start(
        {"name": "ChatAnthropic", "kwargs": {"model": "claude-opus-4-7"}},
        ["you are helpful"],
        run_id="r1",
        parent_run_id="root",
    )
    h.on_llm_end(
        _fake_llm_result(
            text="I'll call a tool.",
            usage={"prompt_tokens": 50, "completion_tokens": 10},
            model="claude-opus-4-7",
        ),
        run_id="r1",
        parent_run_id="root",
    )
    h.on_agent_action(
        AgentAction(tool="read_file", tool_input="readme.md", log="reading"),
        run_id="root",
    )
    h.on_tool_start({"name": "read_file"}, "readme.md", run_id="t1", parent_run_id="root")
    h.on_tool_end("# SAFER", run_id="t1", parent_run_id="root", name="read_file")
    h.on_llm_start(
        {"name": "ChatAnthropic", "kwargs": {"model": "claude-opus-4-7"}},
        ["summarise"],
        run_id="r2",
        parent_run_id="root",
    )
    h.on_llm_end(
        _fake_llm_result(
            text="Done.",
            usage={"prompt_tokens": 20, "completion_tokens": 5},
            model="claude-opus-4-7",
        ),
        run_id="r2",
        parent_run_id="root",
    )
    h.on_agent_finish(AgentFinish(return_values={"output": "Done."}, log="fin"), run_id="root")
    # AgentExecutor closes — chain_end on the root run_id triggers session_end
    h.on_chain_end({"output": "Done."}, run_id="root", parent_run_id=None)

    hooks = [c["hook"] for c in recording_client]
    assert hooks[0] == "on_session_start"
    assert "before_llm_call" in hooks
    assert "after_llm_call" in hooks
    assert "on_agent_decision" in hooks
    assert "before_tool_use" in hooks
    assert "after_tool_use" in hooks
    assert "on_final_output" in hooks
    assert hooks[-1] == "on_session_end"
    # Exactly one final_output and one session_end
    assert hooks.count("on_final_output") == 1
    assert hooks.count("on_session_end") == 1


def test_chat_model_start_syncs_system_prompt_once(recording_client, monkeypatch):
    """SystemMessage content in on_chat_model_start should schedule a
    profile patch exactly once per handler instance."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from safer.adapters.langchain import SaferCallbackHandler

    calls: list[dict] = []

    # recording_client fixture already installs a Dummy client on the
    # module; replace its schedule_profile_patch too.
    from safer import client as client_mod

    dummy = client_mod._client

    def _fake_patch(agent_id, **kw):
        calls.append({"agent_id": agent_id, **kw})

    monkeypatch.setattr(
        dummy, "schedule_profile_patch", _fake_patch, raising=False
    )

    h = SaferCallbackHandler(agent_id="lc_agent", agent_name="LC Agent")
    h.on_chain_start({"name": "chain"}, {"input": "hi"})
    h.on_chat_model_start(
        {"name": "ChatAnthropic"},
        [[SystemMessage(content="Be concise."), HumanMessage(content="hi")]],
        run_id="r1",
    )
    # Second call must not re-sync.
    h.on_chat_model_start(
        {"name": "ChatAnthropic"},
        [[SystemMessage(content="Be verbose."), HumanMessage(content="again")]],
        run_id="r2",
    )

    assert len(calls) == 1
    assert calls[0]["agent_id"] == "lc_agent"
    assert calls[0]["system_prompt"] == "Be concise."
    assert calls[0]["name"] == "LC Agent"


def test_handler_emits_on_error(recording_client):
    from safer.adapters.langchain import SaferCallbackHandler

    h = SaferCallbackHandler(agent_id="agent_err")
    h.on_chain_start({"name": "chain"}, {"input": "go"}, run_id="root", parent_run_id=None)
    h.on_tool_error(RuntimeError("boom"))

    error_events = [c for c in recording_client if c["hook"] == "on_error"]
    assert len(error_events) == 1
    payload = error_events[0]["payload"]
    assert payload["error_type"] == "RuntimeError"
    assert "boom" in payload["message"]


def test_lcel_session_closes_without_agent_finish(recording_client):
    """Critical regression: in earlier versions `on_chain_end` was a no-op
    so plain LCEL pipelines (no AgentExecutor) NEVER emitted on_session_end.
    The dashboard saw open sessions accumulate forever.

    Now `on_chain_end` on the root run_id (parent_run_id=None) closes the
    SAFER session — even without `on_agent_finish`."""
    from safer.adapters.langchain import SaferCallbackHandler

    h = SaferCallbackHandler(agent_id="lcel_demo")
    # Plain LCEL: chain = prompt | llm | parser
    h.on_chain_start({"name": "RunnableSequence"}, {"input": "hi"}, run_id="root", parent_run_id=None)
    h.on_chat_model_start(
        {"name": "ChatAnthropic", "kwargs": {"model": "claude-haiku-4-5"}},
        [[]],
        run_id="r1",
        parent_run_id="root",
        invocation_params={"model": "claude-haiku-4-5", "tools": []},
    )
    h.on_llm_end(
        _fake_llm_result(text="42", usage={"input_tokens": 10, "output_tokens": 1}, model="claude-haiku-4-5"),
        run_id="r1",
        parent_run_id="root",
    )
    h.on_chain_end({"output": "42"}, run_id="root", parent_run_id=None)

    hooks = [c["hook"] for c in recording_client]
    assert "on_session_start" in hooks
    assert "on_final_output" in hooks
    assert hooks[-1] == "on_session_end"


def test_langgraph_style_session_closes_on_root_chain_end(recording_client):
    """LangGraph nodes never emit on_agent_action / on_agent_finish — the
    root chain end is the only session-close signal we get.  Verify we
    handle it correctly with multiple sub-chains (LangGraph nodes) inside."""
    from safer.adapters.langchain import SaferCallbackHandler

    h = SaferCallbackHandler(agent_id="lg_demo")
    h.on_chain_start({"name": "Pregel"}, {"input": "x"}, run_id="root", parent_run_id=None)
    # Sub-chain (a node) — must NOT close the SAFER session
    h.on_chain_start({"name": "node_1"}, {"input": "x"}, run_id="node1", parent_run_id="root")
    h.on_chain_end({"output": "intermediate"}, run_id="node1", parent_run_id="root")
    # Another sub-chain
    h.on_chain_start({"name": "node_2"}, {"input": "intermediate"}, run_id="node2", parent_run_id="root")
    h.on_chain_end({"output": "final"}, run_id="node2", parent_run_id="root")
    # Root closes — this is when SAFER session ends
    h.on_chain_end({"output": "final"}, run_id="root", parent_run_id=None)

    hooks = [c["hook"] for c in recording_client]
    # Exactly one start + one end despite 3 chain_start / chain_end pairs
    assert hooks.count("on_session_start") == 1
    assert hooks.count("on_session_end") == 1
    assert hooks[-1] == "on_session_end"


def test_multi_invocation_session_id_rotates(recording_client):
    """A single SaferCallbackHandler used for multiple AgentExecutor calls
    must produce a distinct SAFER session_id per invocation — earlier
    versions cached session_id in __init__, causing every turn to write
    to the same backend session."""
    from langchain_core.agents import AgentFinish

    from safer.adapters.langchain import SaferCallbackHandler

    h = SaferCallbackHandler(agent_id="multi")

    # Invocation 1
    h.on_chain_start({"name": "AgentExecutor"}, {"input": "first"}, run_id="r1", parent_run_id=None)
    sid_1 = h.session_id
    h.on_agent_finish(AgentFinish(return_values={"output": "1"}, log=""), run_id="r1")
    h.on_chain_end({"output": "1"}, run_id="r1", parent_run_id=None)

    # Invocation 2
    h.on_chain_start({"name": "AgentExecutor"}, {"input": "second"}, run_id="r2", parent_run_id=None)
    sid_2 = h.session_id
    h.on_agent_finish(AgentFinish(return_values={"output": "2"}, log=""), run_id="r2")
    h.on_chain_end({"output": "2"}, run_id="r2", parent_run_id=None)

    assert sid_1 != sid_2

    starts = [c for c in recording_client if c["hook"] == "on_session_start"]
    ends = [c for c in recording_client if c["hook"] == "on_session_end"]
    assert len(starts) == 2
    assert len(ends) == 2
    assert {s["session_id"] for s in starts} == {sid_1, sid_2}


def test_tool_name_preserved_via_run_id_cache(recording_client):
    """LangChain doesn't pass `name` reliably to on_tool_end; older code
    stored 'tool' as the name, losing the actual tool identity.  Now we
    cache by run_id at on_tool_start."""
    from safer.adapters.langchain import SaferCallbackHandler

    h = SaferCallbackHandler(agent_id="tn")
    h.on_chain_start({"name": "AgentExecutor"}, {}, run_id="root", parent_run_id=None)
    h.on_tool_start({"name": "read_file"}, "x.txt", run_id="t1", parent_run_id="root")
    # Note: NO `name` kwarg passed to on_tool_end — kwargs.get("name") returns None
    h.on_tool_end("contents", run_id="t1", parent_run_id="root")
    h.on_chain_end({"output": "done"}, run_id="root", parent_run_id=None)

    after = [c for c in recording_client if c["hook"] == "after_tool_use"]
    assert len(after) == 1
    assert after[0]["payload"]["tool_name"] == "read_file"


def test_anthropic_model_name_resolved_from_llm_output_model_singular(recording_client):
    """Critical regression: langchain-anthropic populates `llm_output["model"]`
    (singular), not `llm_output["model_name"]`.  Older adapter only checked
    `model_name` → every Anthropic call resolved to "unknown" → 0 cost
    (or, in the prior bug, fake Opus pricing)."""
    from types import SimpleNamespace

    from safer.adapters.langchain import SaferCallbackHandler

    h = SaferCallbackHandler(agent_id="m_resolve")
    h.on_chain_start({"name": "Chain"}, {}, run_id="root", parent_run_id=None)
    h.on_llm_start({}, ["q"], run_id="r1", parent_run_id="root")
    # llm_output uses "model" (singular) — Anthropic style
    response = SimpleNamespace(
        generations=[[SimpleNamespace(text="a", message=SimpleNamespace(content="a"))]],
        llm_output={"model": "claude-sonnet-4-6", "token_usage": {"prompt_tokens": 100, "completion_tokens": 50}},
    )
    h.on_llm_end(response, run_id="r1", parent_run_id="root")

    after = next(c for c in recording_client if c["hook"] == "after_llm_call")
    assert after["payload"]["model"] == "claude-sonnet-4-6"
    # Cost should be Sonnet, not Opus or zero
    assert after["payload"]["cost_usd"] > 0
    expected = (100 * 3.0 + 50 * 15.0) / 1_000_000
    assert abs(after["payload"]["cost_usd"] - expected) < 1e-9


def test_session_end_carries_total_duration_and_cost(recording_client):
    """OnSessionEndPayload must include accumulated duration + cost from
    the LLM calls during the session.  Older versions hardcoded zeros."""
    from types import SimpleNamespace

    from safer.adapters.langchain import SaferCallbackHandler

    h = SaferCallbackHandler(agent_id="metrics")
    h.on_chain_start({"name": "Chain"}, {}, run_id="root", parent_run_id=None)
    h.on_llm_start({}, ["q"], run_id="r1", parent_run_id="root")
    h.on_llm_end(
        SimpleNamespace(
            generations=[[SimpleNamespace(text="a", message=SimpleNamespace(content="a"))]],
            llm_output={"model": "claude-haiku-4-5", "token_usage": {"prompt_tokens": 1000, "completion_tokens": 500}},
        ),
        run_id="r1",
        parent_run_id="root",
    )
    h.on_chain_end({"output": "a"}, run_id="root", parent_run_id=None)

    end = next(c for c in recording_client if c["hook"] == "on_session_end")
    assert end["payload"]["total_duration_ms"] >= 0
    assert end["payload"]["total_cost_usd"] > 0
    expected_cost = (1000 * 1.0 + 500 * 5.0) / 1_000_000
    assert abs(end["payload"]["total_cost_usd"] - expected_cost) < 1e-9


def test_content_blocks_extracted_to_text(recording_client):
    """`BaseMessage.content` can be a list of content blocks (Anthropic).
    Older code did `str(content)` → `"[{'type': 'text', 'text': '...'}]"`;
    the Judge couldn't parse that.  Now we walk content blocks."""
    from langchain_core.messages import HumanMessage

    from safer.adapters.langchain import SaferCallbackHandler

    h = SaferCallbackHandler(agent_id="cb")
    h.on_chain_start({"name": "Chain"}, {}, run_id="root", parent_run_id=None)

    # Build a HumanMessage with a content-block list (Anthropic-style)
    msg = HumanMessage(
        content=[
            {"type": "text", "text": "Hello"},
            {"type": "image", "source_type": "url", "url": "https://example.com/x.png"},
            {"type": "text", "text": "world"},
        ]
    )
    h.on_chat_model_start(
        {"name": "ChatAnthropic"},
        [[msg]],
        run_id="r1",
        parent_run_id="root",
        invocation_params={"model": "claude-haiku-4-5"},
    )

    before = next(c for c in recording_client if c["hook"] == "before_llm_call")
    prompt = before["payload"]["prompt"]
    # Text blocks present
    assert "Hello" in prompt
    assert "world" in prompt
    # Image block NOT stringified into the prompt preview
    assert "example.com" not in prompt
    # Role label preserved
    assert "[human]" in prompt or "[user]" in prompt


def test_tools_extracted_from_invocation_params(recording_client):
    """The tools list passed to a chat model lives at
    `invocation_params["tools"]` after `bind_tools(...)`.  Older adapter
    hardcoded `tools=[]` → Judge lost tool surface metadata."""
    from safer.adapters.langchain import SaferCallbackHandler

    h = SaferCallbackHandler(agent_id="tools_ext")
    h.on_chain_start({"name": "Chain"}, {}, run_id="root", parent_run_id=None)
    h.on_llm_start(
        {"name": "ChatAnthropic"},
        ["q"],
        run_id="r1",
        parent_run_id="root",
        invocation_params={
            "model": "claude-opus-4-7",
            "tools": [
                {"name": "read_file", "description": "Read a file"},
                {"name": "search", "description": "Search"},
            ],
        },
    )

    before = next(c for c in recording_client if c["hook"] == "before_llm_call")
    tools = before["payload"]["tools"]
    assert sorted([t["name"] for t in tools]) == ["read_file", "search"]


def test_chain_error_on_root_emits_session_end(recording_client):
    """If the root chain errors out, we must close the session — older
    code emitted on_error but left the session open forever."""
    from safer.adapters.langchain import SaferCallbackHandler

    h = SaferCallbackHandler(agent_id="err_close")
    h.on_chain_start({"name": "Chain"}, {}, run_id="root", parent_run_id=None)
    h.on_chain_error(RuntimeError("kaboom"), run_id="root", parent_run_id=None)

    hooks = [c["hook"] for c in recording_client]
    assert "on_error" in hooks
    assert "on_session_end" in hooks
    end = next(c for c in recording_client if c["hook"] == "on_session_end")
    assert end["payload"]["success"] is False


def test_tool_output_tuple_extracts_content_half(recording_client):
    """Tools with `response_format='content_and_artifact'` return
    `(content, artifact)` tuples.  We surface only the content half."""
    from safer.adapters.langchain import SaferCallbackHandler

    h = SaferCallbackHandler(agent_id="tup")
    h.on_chain_start({"name": "Chain"}, {}, run_id="root", parent_run_id=None)
    h.on_tool_start({"name": "search"}, "x", run_id="t1", parent_run_id="root")
    h.on_tool_end(
        ("results: foo, bar", {"raw_documents": [1, 2, 3]}),
        run_id="t1",
        parent_run_id="root",
    )

    after = next(c for c in recording_client if c["hook"] == "after_tool_use")
    assert "foo, bar" in after["payload"]["result"]
    assert "raw_documents" not in after["payload"]["result"]


def test_retriever_events_map_to_tool_use(recording_client):
    """Retrievers in RAG pipelines should produce SAFER tool events so the
    Judge sees retrieval as a tool call."""
    from types import SimpleNamespace

    from safer.adapters.langchain import SaferCallbackHandler

    h = SaferCallbackHandler(agent_id="rag")
    h.on_chain_start({"name": "Chain"}, {}, run_id="root", parent_run_id=None)
    h.on_retriever_start(
        {"name": "VectorStoreRetriever"}, "what is safer?", run_id="ret1", parent_run_id="root"
    )
    h.on_retriever_end(
        [SimpleNamespace(page_content="SAFER is an agent control plane.", metadata={})],
        run_id="ret1",
        parent_run_id="root",
    )
    h.on_chain_end({"output": "..."}, run_id="root", parent_run_id=None)

    hooks = [c["hook"] for c in recording_client]
    assert hooks.count("before_tool_use") == 1
    assert hooks.count("after_tool_use") == 1
    before = next(c for c in recording_client if c["hook"] == "before_tool_use")
    assert before["payload"]["tool_name"] == "VectorStoreRetriever"
    assert before["payload"]["args"]["query"] == "what is safer?"


def test_async_handler_class_is_subclass_of_async_callback_handler():
    """For native async LangChain agents we ship `AsyncSaferCallbackHandler`
    which subclasses `AsyncCallbackHandler` — so LangChain's async dispatch
    path runs our callbacks in the same event loop instead of a thread pool."""
    from langchain_core.callbacks import AsyncCallbackHandler

    from safer.adapters.langchain import AsyncSaferCallbackHandler

    h = AsyncSaferCallbackHandler(agent_id="async_test")
    assert isinstance(h, AsyncCallbackHandler)


def test_agent_action_includes_tool_input(recording_client):
    """Older adapter dropped action.tool_input on the floor.  Now the
    decision payload's chosen_action carries `tool_name(args_json)`."""
    from langchain_core.agents import AgentAction

    from safer.adapters.langchain import SaferCallbackHandler

    h = SaferCallbackHandler(agent_id="aa")
    h.on_chain_start({"name": "AgentExecutor"}, {}, run_id="root", parent_run_id=None)
    h.on_agent_action(
        AgentAction(tool="read_file", tool_input={"path": "x.md"}, log="reasoning"),
        run_id="root",
    )

    decision = next(c for c in recording_client if c["hook"] == "on_agent_decision")
    chosen = decision["payload"]["chosen_action"]
    assert "read_file" in chosen
    assert "x.md" in chosen


# ---------- helpers ----------


def _fake_llm_result(*, text: str, usage: dict, model: str):
    """Build a minimal object shaped like LangChain's LLMResult."""
    from types import SimpleNamespace

    generation = SimpleNamespace(text=text, message=SimpleNamespace(content=text))
    return SimpleNamespace(
        generations=[[generation]],
        llm_output={"token_usage": usage, "model_name": model},
    )
