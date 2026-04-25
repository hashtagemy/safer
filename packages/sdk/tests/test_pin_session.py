"""`pin_session=True` end-to-end verification per adapter.

For each adapter that rotates session_id by default, drive TWO
invocations with `pin_session=True` and assert:

- both invocations' events share the same SAFER session_id,
- exactly one `on_session_start` was emitted,
- no `on_session_end` was emitted (atexit defers it),
- `close_session()` does emit `on_session_end` exactly once.

The scripted models / mocks deliberately stay tiny (text-only LLM
responses, no tool calls) so the tests stay fast and focused on the
session-lifecycle invariant — the per-adapter happy-path behaviour
is covered separately by `test_user_<framework>.py`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


# -----------------------------------------------------------------------
# 1. Strands: SaferHookProvider(pin_session=True)
# -----------------------------------------------------------------------


def test_strands_pin_session_one_session_for_two_invocations(captured_events):
    pytest.importorskip("strands.hooks")
    from strands import Agent
    from strands.models import Model

    from safer.adapters.strands import SaferHookProvider

    class _ScriptedTextModel(Model):
        model_id = "claude-haiku-4-5"

        def __init__(self):
            self.config = {"model_id": "claude-haiku-4-5"}

        def update_config(self, **kw):
            self.config.update(kw)

        def get_config(self):
            return self.config

        def structured_output(self, output_model, prompt, **kw):
            raise NotImplementedError

        async def stream(self, *a, **kw):
            for ev in [
                {"messageStart": {"role": "assistant"}},
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": 0,
                        "delta": {"text": "ack."},
                    }
                },
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {"messageStop": {"stopReason": "end_turn"}},
                {
                    "metadata": {
                        "usage": {
                            "inputTokens": 10,
                            "outputTokens": 2,
                            "totalTokens": 12,
                            "cacheReadInputTokens": 0,
                            "cacheWriteInputTokens": 0,
                        },
                        "metrics": {"latencyMs": 1},
                    }
                },
            ]:
                yield ev

    provider = SaferHookProvider(
        agent_id="strands_pin", agent_name="Strands Pin", pin_session=True
    )
    agent = Agent(
        model=_ScriptedTextModel(),
        tools=[],
        system_prompt="ack everything",
        hooks=[provider],
    )

    agent("first turn")
    agent("second turn")

    runtime = [e for e in captured_events if e["hook"] != "on_agent_register"]
    session_ids = {e["session_id"] for e in runtime}
    assert len(session_ids) == 1, (
        f"expected one pinned session_id; got {session_ids}"
    )

    hooks = [e["hook"] for e in runtime]
    assert hooks.count("on_session_start") == 1
    assert "on_session_end" not in hooks
    assert hooks.count("before_llm_call") == 2
    assert hooks.count("after_llm_call") == 2
    assert hooks.count("on_final_output") == 2

    # Manual close emits the single on_session_end.
    provider.close_session(success=True)
    end = [e for e in captured_events if e["hook"] == "on_session_end"]
    assert len(end) == 1
    assert end[0]["session_id"] == session_ids.pop()


def test_strands_default_rotates_session_per_invocation(captured_events):
    pytest.importorskip("strands.hooks")
    from strands import Agent
    from strands.models import Model

    from safer.adapters.strands import SaferHookProvider

    class _ScriptedTextModel(Model):
        model_id = "claude-haiku-4-5"

        def __init__(self):
            self.config = {"model_id": "claude-haiku-4-5"}

        def update_config(self, **kw):
            self.config.update(kw)

        def get_config(self):
            return self.config

        def structured_output(self, output_model, prompt, **kw):
            raise NotImplementedError

        async def stream(self, *a, **kw):
            for ev in [
                {"messageStart": {"role": "assistant"}},
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": 0,
                        "delta": {"text": "ack."},
                    }
                },
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {"messageStop": {"stopReason": "end_turn"}},
                {
                    "metadata": {
                        "usage": {
                            "inputTokens": 10,
                            "outputTokens": 2,
                            "totalTokens": 12,
                        },
                        "metrics": {"latencyMs": 1},
                    }
                },
            ]:
                yield ev

    agent = Agent(
        model=_ScriptedTextModel(),
        tools=[],
        system_prompt="ack everything",
        hooks=[SaferHookProvider(agent_id="strands_default", agent_name="X")],
    )

    agent("first turn")
    agent("second turn")

    runtime = [e for e in captured_events if e["hook"] != "on_agent_register"]
    session_ids = {e["session_id"] for e in runtime}
    assert len(session_ids) == 2, f"expected two distinct sessions; got {session_ids}"

    hooks = [e["hook"] for e in runtime]
    assert hooks.count("on_session_start") == 2
    assert hooks.count("on_session_end") == 2


# -----------------------------------------------------------------------
# 2. LangChain: SaferCallbackHandler(pin_session=True)
# -----------------------------------------------------------------------


def _langchain_text_model_class():
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class _TextModel(BaseChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            msg = AIMessage(content="ack.")
            msg.response_metadata = {
                "model": "claude-haiku-4-5",
                "token_usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }
            return ChatResult(
                generations=[ChatGeneration(message=msg)],
                llm_output={
                    "model": "claude-haiku-4-5",
                    "token_usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            )

        @property
        def _llm_type(self):
            return "text-only"

        def bind_tools(self, tools, **kwargs):
            return self

    return _TextModel


def test_langchain_pin_session_one_session_for_two_invocations(captured_events):
    pytest.importorskip("langchain")
    from langchain.agents import create_agent

    from safer.adapters.langchain import SaferCallbackHandler

    Model = _langchain_text_model_class()
    agent = create_agent(
        model=Model(), tools=[], system_prompt="ack everything"
    )
    handler = SaferCallbackHandler(
        agent_id="lc_pin", agent_name="LC Pin", pin_session=True
    )
    agent.invoke(
        {"messages": [{"role": "user", "content": "first"}]},
        config={"callbacks": [handler]},
    )
    agent.invoke(
        {"messages": [{"role": "user", "content": "second"}]},
        config={"callbacks": [handler]},
    )

    runtime = [e for e in captured_events if e["hook"] != "on_agent_register"]
    session_ids = {e["session_id"] for e in runtime}
    assert len(session_ids) == 1, f"expected one session; got {session_ids}"

    hooks = [e["hook"] for e in runtime]
    assert hooks.count("on_session_start") == 1
    assert "on_session_end" not in hooks

    handler.close_session(success=True)
    end = [e for e in captured_events if e["hook"] == "on_session_end"]
    assert len(end) == 1
    assert end[0]["session_id"] == session_ids.pop()


def test_langchain_default_rotates_session_per_invocation(captured_events):
    pytest.importorskip("langchain")
    from langchain.agents import create_agent

    from safer.adapters.langchain import SaferCallbackHandler

    Model = _langchain_text_model_class()
    agent = create_agent(
        model=Model(), tools=[], system_prompt="ack everything"
    )
    handler = SaferCallbackHandler(agent_id="lc_default", agent_name="LC")
    agent.invoke(
        {"messages": [{"role": "user", "content": "first"}]},
        config={"callbacks": [handler]},
    )
    agent.invoke(
        {"messages": [{"role": "user", "content": "second"}]},
        config={"callbacks": [handler]},
    )

    runtime = [e for e in captured_events if e["hook"] != "on_agent_register"]
    session_ids = {e["session_id"] for e in runtime}
    assert len(session_ids) == 2, f"expected two sessions; got {session_ids}"


# -----------------------------------------------------------------------
# 3. Google ADK: SaferAdkPlugin(pin_session=True)
# -----------------------------------------------------------------------


def test_google_adk_pin_session_one_session_for_two_invocations(captured_events):
    pytest.importorskip("google.adk.plugins.base_plugin")
    import asyncio

    from google.adk.agents import LlmAgent
    from google.adk.models import BaseLlm, LlmResponse
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from safer.adapters.google_adk import SaferAdkPlugin

    class _ScriptedTextLlm(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="ack.")],
                ),
                usage_metadata=types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=10, candidates_token_count=2
                ),
            )

        @classmethod
        def supported_models(cls):
            return [r"scripted-.*"]

    plugin = SaferAdkPlugin(
        agent_id="adk_pin", agent_name="ADK Pin", pin_session=True
    )
    agent = LlmAgent(
        model=_ScriptedTextLlm(model="scripted-test"),
        name="adk_pin_agent",
        instruction="ack everything",
        tools=[],
    )
    runner = InMemoryRunner(
        agent=agent,
        app_name="adk_pin_app",
        plugins=[plugin],
    )

    async def _go(text: str):
        session = await runner.session_service.create_session(
            app_name="adk_pin_app", user_id="user_1"
        )
        msg = types.Content(role="user", parts=[types.Part(text=text)])
        async for _ in runner.run_async(
            user_id="user_1", session_id=session.id, new_message=msg
        ):
            pass

    asyncio.run(_go("first"))
    asyncio.run(_go("second"))

    runtime = [e for e in captured_events if e["hook"] != "on_agent_register"]
    session_ids = {e["session_id"] for e in runtime}
    assert len(session_ids) == 1, f"expected one session; got {session_ids}"

    hooks = [e["hook"] for e in runtime]
    assert hooks.count("on_session_start") == 1
    assert "on_session_end" not in hooks

    plugin.close_session(success=True)
    end = [e for e in captured_events if e["hook"] == "on_session_end"]
    assert len(end) == 1
    assert end[0]["session_id"] == session_ids.pop()


def test_google_adk_default_rotates_session_per_invocation(captured_events):
    pytest.importorskip("google.adk.plugins.base_plugin")
    import asyncio

    from google.adk.agents import LlmAgent
    from google.adk.models import BaseLlm, LlmResponse
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from safer.adapters.google_adk import SaferAdkPlugin

    class _ScriptedTextLlm(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text="ack.")]),
                usage_metadata=types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=10, candidates_token_count=2
                ),
            )

        @classmethod
        def supported_models(cls):
            return [r"scripted-.*"]

    plugin = SaferAdkPlugin(agent_id="adk_default", agent_name="X")
    agent = LlmAgent(
        model=_ScriptedTextLlm(model="scripted-test"),
        name="adk_default_agent",
        instruction="ack",
        tools=[],
    )
    runner = InMemoryRunner(
        agent=agent, app_name="adk_default_app", plugins=[plugin]
    )

    async def _go(text):
        session = await runner.session_service.create_session(
            app_name="adk_default_app", user_id="user_1"
        )
        msg = types.Content(role="user", parts=[types.Part(text=text)])
        async for _ in runner.run_async(
            user_id="user_1", session_id=session.id, new_message=msg
        ):
            pass

    asyncio.run(_go("first"))
    asyncio.run(_go("second"))

    runtime = [e for e in captured_events if e["hook"] != "on_agent_register"]
    session_ids = {e["session_id"] for e in runtime}
    # ADK's `on_user_message_callback` emits one `on_session_start` with a
    # default-random session_id BEFORE `before_run_callback` rotates to the
    # invocation-prefixed id, so the default path naturally surfaces 2-3
    # distinct session_ids across two runs. The point of this test is just
    # to confirm that without pin_session, the session_id does NOT stay
    # stable across invocations.
    assert len(session_ids) > 1, f"expected rotation; got pinned to {session_ids}"


# -----------------------------------------------------------------------
# 4. OpenAI Agents SDK: install_safer_for_agents(pin_session=True)
# -----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_agents_processor_cache():
    """Drop the install_safer_for_agents idempotence cache between tests."""
    try:
        from safer.adapters import openai_agents as oa_mod
    except Exception:
        yield
        return
    try:
        oa_mod._reset_for_tests()
        yield
    finally:
        oa_mod._reset_for_tests()


def _openai_agents_text_only_handler():
    """httpx handler that always returns a 1-message text completion."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "id": "chatcmpl_text",
            "object": "chat.completion",
            "created": 1735_000_000,
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ack."},
                    "finish_reason": "stop",
                    "logprobs": None,
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        }
        return httpx.Response(200, json=body)

    return handler


def test_openai_agents_pin_session_one_session_for_two_runs(captured_events):
    pytest.importorskip("agents")
    import asyncio

    import httpx
    from agents import (
        Agent,
        Runner,
        set_default_openai_api,
        set_default_openai_client,
        set_tracing_disabled,
    )
    from openai import AsyncOpenAI

    from safer.adapters.openai_agents import install_safer_for_agents

    set_tracing_disabled(True)
    transport = httpx.MockTransport(_openai_agents_text_only_handler())
    set_default_openai_client(
        AsyncOpenAI(api_key="sk-test", http_client=httpx.AsyncClient(transport=transport)),
        use_for_tracing=False,
    )
    set_default_openai_api("chat_completions")

    agent = Agent(
        name="text_agent",
        instructions="ack everything",
        tools=[],
        model="gpt-4o",
    )

    hooks = install_safer_for_agents(
        agent_id="oai_pin", agent_name="OAI Pin", pin_session=True
    )

    async def _go(prompt: str):
        return await Runner.run(agent, prompt, hooks=hooks)

    asyncio.run(_go("first"))
    asyncio.run(_go("second"))

    runtime = [e for e in captured_events if e["hook"] != "on_agent_register"]
    session_ids = {e["session_id"] for e in runtime}
    assert len(session_ids) == 1, f"expected one session; got {session_ids}"

    hookname = [e["hook"] for e in runtime]
    assert hookname.count("on_session_start") == 1
    assert "on_session_end" not in hookname

    hooks.close_session(success=True)
    end = [e for e in captured_events if e["hook"] == "on_session_end"]
    assert len(end) == 1
    assert end[0]["session_id"] == session_ids.pop()


def test_openai_agents_default_rotates_session_per_run(captured_events):
    pytest.importorskip("agents")
    import asyncio

    import httpx
    from agents import (
        Agent,
        Runner,
        set_default_openai_api,
        set_default_openai_client,
        set_tracing_disabled,
    )
    from openai import AsyncOpenAI

    from safer.adapters.openai_agents import install_safer_for_agents

    set_tracing_disabled(True)
    transport = httpx.MockTransport(_openai_agents_text_only_handler())
    set_default_openai_client(
        AsyncOpenAI(api_key="sk-test", http_client=httpx.AsyncClient(transport=transport)),
        use_for_tracing=False,
    )
    set_default_openai_api("chat_completions")

    agent = Agent(
        name="text_agent_default",
        instructions="ack",
        tools=[],
        model="gpt-4o",
    )

    hooks = install_safer_for_agents(agent_id="oai_default", agent_name="X")

    async def _go(prompt):
        return await Runner.run(agent, prompt, hooks=hooks)

    asyncio.run(_go("first"))
    asyncio.run(_go("second"))

    runtime = [e for e in captured_events if e["hook"] != "on_agent_register"]
    session_ids = {e["session_id"] for e in runtime}
    assert len(session_ids) == 2, f"expected two sessions; got {session_ids}"


# -----------------------------------------------------------------------
# 5. Atexit hooks fire close_session once per pinned provider
# -----------------------------------------------------------------------


def test_strands_pin_session_atexit_closes_once(captured_events):
    """Simulate process exit by calling the registered atexit handler
    directly. It must emit exactly one on_session_end and become a
    no-op on subsequent invocations."""
    pytest.importorskip("strands.hooks")
    from strands import Agent
    from strands.models import Model

    from safer.adapters.strands import SaferHookProvider

    class _M(Model):
        model_id = "claude-haiku-4-5"

        def __init__(self):
            self.config = {"model_id": "claude-haiku-4-5"}

        def update_config(self, **kw):
            self.config.update(kw)

        def get_config(self):
            return self.config

        def structured_output(self, output_model, prompt, **kw):
            raise NotImplementedError

        async def stream(self, *a, **kw):
            for ev in [
                {"messageStart": {"role": "assistant"}},
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": 0,
                        "delta": {"text": "ok"},
                    }
                },
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {"messageStop": {"stopReason": "end_turn"}},
                {
                    "metadata": {
                        "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                        "metrics": {"latencyMs": 1},
                    }
                },
            ]:
                yield ev

    provider = SaferHookProvider(
        agent_id="strands_atexit", agent_name="X", pin_session=True
    )
    agent = Agent(
        model=_M(), tools=[], system_prompt="ack", hooks=[provider]
    )
    agent("once")

    # simulate atexit
    provider._atexit_close_session()
    provider._atexit_close_session()  # second call must be a no-op

    end = [e for e in captured_events if e["hook"] == "on_session_end"]
    assert len(end) == 1
