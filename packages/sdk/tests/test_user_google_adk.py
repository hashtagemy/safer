"""End-to-end test for the Google ADK adapter.

Drives a real `google.adk.runners.InMemoryRunner` through a complete
agent loop with `SaferAdkPlugin` instrumentation.

The only SAFER-specific lines are the ones from the README:

    from safer.adapters.google_adk import SaferAdkPlugin
    runner = Runner(... plugins=[SaferAdkPlugin(agent_id=..., agent_name=...)])

The agent is a repo-analyst that uses a `read_file` function tool to
fetch source content and a `lookup_secret` tool with an intentional
PII surface (returns API keys + email) — exactly the kind of
weakness SAFER's Compliance and Security personas are meant to flag.

Every other moving part (LlmAgent, InMemoryRunner, function-tool
dispatch, plugin callback chain) is real ADK code; only the model is
a custom `BaseLlm` subclass that yields canned `LlmResponse` events.
"""

from __future__ import annotations

import asyncio

import pytest


pytest.importorskip("google.adk.plugins.base_plugin")


def test_google_adk_runner_emits_full_safer_lifecycle(captured_events):
    from google.adk.agents import LlmAgent
    from google.adk.models import BaseLlm, LlmResponse
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    # --- Tools the user would actually write -----------------------------

    def read_file(path: str) -> str:
        """Return the contents of a repo file.  Path-traversal surface
        — accepts arbitrary paths."""
        if path == "README.md":
            return "# SAFER\nObservability for AI agents."
        return f"(stub for {path})"

    def lookup_secret(name: str) -> dict:
        """Return a credentials record by name.

        Intentional PII / secrets egress for SAFER detection."""
        return {
            "name": name,
            "owner_email": "ops@example.com",
            "value": "sk-very-secret-do-not-leak",
        }

    # --- Custom model that scripts a 2-step tool-use turn ---------------

    class ScriptedLlm(BaseLlm):
        """Yields canned LlmResponse events: first a function_call,
        then the final text answer.  Same shape an ADK production model
        would produce."""

        responses: list

        async def generate_content_async(self, llm_request, stream=False):
            idx = getattr(self, "_idx", 0)
            self._idx = idx + 1
            yield self.responses[min(idx, len(self.responses) - 1)]

        @classmethod
        def supported_models(cls):
            return [r"scripted-.*"]

    # First model response: emit a function_call to read_file
    call_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="fc_1",
                        name="read_file",
                        args={"path": "README.md"},
                    )
                )
            ],
        ),
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=42, candidates_token_count=10
        ),
    )
    # Second model response: final text answer
    final_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    text="The README starts with `# SAFER` — observability for AI agents."
                )
            ],
        ),
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=58, candidates_token_count=18
        ),
    )

    agent = LlmAgent(
        model=ScriptedLlm(
            model="scripted-test", responses=[call_resp, final_resp]
        ),
        name="repo_analyst",
        instruction="Use the tools to answer questions about this repository.",
        tools=[read_file, lookup_secret],
    )

    # ====== USER CODE (verbatim from README integration) ===============
    from safer.adapters.google_adk import SaferAdkPlugin

    runner = InMemoryRunner(
        agent=agent,
        app_name="repo_analyst_app",
        plugins=[SaferAdkPlugin(agent_id="repo_analyst", agent_name="Repo Analyst")],
    )

    async def go():
        session = await runner.session_service.create_session(
            app_name="repo_analyst_app", user_id="user_1"
        )
        user_msg = types.Content(
            role="user",
            parts=[types.Part(text="What does the README say SAFER is?")],
        )
        async for _ in runner.run_async(
            user_id="user_1", session_id=session.id, new_message=user_msg
        ):
            pass

    asyncio.run(go())
    # ====================================================================

    events = captured_events
    hook_names = [e["hook"] for e in events]

    # 1. Onboarding event from `ensure_runtime` + the runtime session
    assert hook_names[0] == "on_agent_register"
    assert "on_session_start" in hook_names
    assert hook_names[-1] == "on_session_end"

    # 2. Two LLM turns (call + final)
    assert hook_names.count("before_llm_call") == 2
    assert hook_names.count("after_llm_call") == 2

    # 3. Tool decision + before/after_tool_use
    decisions = [e for e in events if e["hook"] == "on_agent_decision"]
    assert any(
        "read_file" in (d["chosen_action"] or "") for d in decisions
    ), f"expected a read_file decision, got {[d['chosen_action'] for d in decisions]}"

    before_tools = [e for e in events if e["hook"] == "before_tool_use"]
    after_tools = [e for e in events if e["hook"] == "after_tool_use"]
    assert any(t["tool_name"] == "read_file" for t in before_tools)
    read_after = next(t for t in after_tools if t["tool_name"] == "read_file")
    assert "SAFER" in read_after["result"]

    # 4. Final output captured
    finals = [e for e in events if e["hook"] == "on_final_output"]
    assert len(finals) >= 1
    assert "observability" in finals[-1]["final_response"].lower()

    # 5. Cost via shared pricing — Gemini fallback rate is fine; what matters
    #    is that the AfterLLMCall payload was constructed with model + tokens
    afters = [e for e in events if e["hook"] == "after_llm_call"]
    assert all(a["tokens_in"] > 0 for a in afters)
    assert all(a["tokens_out"] > 0 for a in afters)
