"""End-to-end test for the LangChain adapter.

Drives a real `langchain.agents.create_agent(...)` (the modern LangChain
1.x recommended way to build a tool-calling agent — what used to live
under `create_tool_calling_agent` / LangGraph `create_react_agent`)
with `SaferCallbackHandler` instrumentation.

The only SAFER-specific lines are the ones from the README:

    from safer.adapters.langchain import SaferCallbackHandler
    handler = SaferCallbackHandler(agent_id=..., agent_name=...)
    agent.invoke({"messages": [...]}, config={"callbacks": [handler]})

The agent is a code-analyst that grep's a repo and reads files.  Two
intentional weaknesses for SAFER's detection:
  * `read_file` accepts arbitrary paths (path-traversal surface).
  * The model's final reply quotes the file contents verbatim — if a
    secret leaks into the file it leaks into the answer.

The model is a tiny custom `BaseChatModel` subclass that returns canned
AIMessages (with `tool_calls`) so the test is hermetic.  Every other
moving part — the agent loop, callback dispatch, message routing — is
real LangChain code.
"""

from __future__ import annotations

import pytest


pytest.importorskip("langchain_core")
pytest.importorskip("langchain")


# ---------- the user's agent: idiomatic LangChain create_agent ----------


def test_langchain_create_agent_emits_full_safer_lifecycle(captured_events):
    from langchain.agents import create_agent
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool

    class ScriptedToolModel(BaseChatModel):
        """Tiny BaseChatModel that returns canned AIMessages.  Used in
        place of ChatAnthropic/ChatOpenAI so the test stays hermetic but
        every other LangChain machinery (agent loop, callbacks) runs
        natively."""

        responses: list

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            idx = getattr(self, "_idx", 0)
            self._idx = idx + 1
            msg = self.responses[min(idx, len(self.responses) - 1)]
            # Stamp realistic provider metadata so the adapter's pricing
            # path can resolve the model.
            if not msg.response_metadata:
                msg.response_metadata = {
                    "model": "claude-sonnet-4-6",
                    "token_usage": {"prompt_tokens": 30, "completion_tokens": 15},
                }
            return ChatResult(
                generations=[ChatGeneration(message=msg)],
                llm_output={
                    "model": "claude-sonnet-4-6",
                    "token_usage": {"prompt_tokens": 30, "completion_tokens": 15},
                },
            )

        @property
        def _llm_type(self) -> str:
            return "scripted-tool-model"

        def bind_tools(self, tools, **kwargs):
            return self

    @tool
    def read_file(path: str) -> str:
        """Read a file from the repository.  Intentionally accepts any
        path (no whitelist) — SAFER Security persona surface."""
        if path == "README.md":
            return "# SAFER\nObservability for AI agents.  API_KEY=sk-secret-leak"
        return f"# {path}\n(stub)"

    @tool
    def grep_repo(pattern: str) -> str:
        """Return file paths in the repo matching the pattern."""
        return "README.md\nCLAUDE.md"

    # Two-turn scripted dialog: model calls grep + read_file, then summarises.
    ai_first = AIMessage(
        content="Let me search the repo.",
        tool_calls=[
            {"name": "grep_repo", "args": {"pattern": "SAFER"}, "id": "tc_1"},
        ],
    )
    ai_second = AIMessage(
        content="Reading the README.",
        tool_calls=[
            {"name": "read_file", "args": {"path": "README.md"}, "id": "tc_2"},
        ],
    )
    ai_final = AIMessage(
        content=(
            "SAFER is an observability platform for AI agents — the README "
            "starts with `# SAFER`."
        )
    )

    model = ScriptedToolModel(responses=[ai_first, ai_second, ai_final])
    agent = create_agent(
        model=model,
        tools=[read_file, grep_repo],
        system_prompt="You are a code analyst.  Use the tools to answer.",
    )

    # ====== USER CODE (verbatim from README integration) =============
    from safer.adapters.langchain import SaferCallbackHandler

    handler = SaferCallbackHandler(
        agent_id="code_analyst", agent_name="Code Analyst"
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "What is SAFER?"}]},
        config={"callbacks": [handler]},
    )
    # ==================================================================

    final_msg = result["messages"][-1]
    assert "SAFER" in final_msg.content

    events = captured_events
    hooks = [e["hook"] for e in events]

    # Onboarding event from `ensure_runtime` + the runtime session
    assert hooks[0] == "on_agent_register"
    assert "on_session_start" in hooks
    assert hooks[-1] == "on_session_end"
    assert hooks.count("on_session_start") == 1
    assert hooks.count("on_session_end") == 1

    # 2. Three LLM call pairs (one per scripted response)
    assert hooks.count("before_llm_call") == 3
    assert hooks.count("after_llm_call") == 3

    # 3. Two tool calls: grep_repo + read_file
    before_tools = [e for e in events if e["hook"] == "before_tool_use"]
    after_tools = [e for e in events if e["hook"] == "after_tool_use"]
    assert len(before_tools) == 2
    assert len(after_tools) == 2
    tool_names = sorted(t["tool_name"] for t in before_tools)
    assert tool_names == ["grep_repo", "read_file"]

    # 4. Tool name preserved on `after_tool_use` via run_id cache
    after_names = sorted(t["tool_name"] for t in after_tools)
    assert after_names == ["grep_repo", "read_file"]

    # 5. PII / secret leak present in the captured tool result — the
    #    Compliance / Trust personas on the SAFER backend would flag this.
    read_after = next(t for t in after_tools if t["tool_name"] == "read_file")
    assert "sk-secret-leak" in read_after["result"]

    # 6. Cost — Sonnet 4.6 priced via shared pricing
    afters = [e for e in events if e["hook"] == "after_llm_call"]
    assert all(a["cost_usd"] > 0 for a in afters), (
        f"expected non-zero cost; got {[a['cost_usd'] for a in afters]}"
    )
    expected = (30 * 3.0 + 15 * 15.0) / 1_000_000
    for a in afters:
        assert abs(a["cost_usd"] - expected) < 1e-9

    # 7. Final session metrics — duration + cumulative cost on session_end
    end = next(e for e in events if e["hook"] == "on_session_end")
    assert end["total_duration_ms"] >= 0
    assert end["total_cost_usd"] > 0
