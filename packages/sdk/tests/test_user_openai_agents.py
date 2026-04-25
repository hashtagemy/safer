"""End-to-end test for the OpenAI Agents SDK adapter.

Drives a real `agents.Runner.run(...)` with two `@function_tool`-decorated
tools through a complete agent loop.  The only SAFER-specific lines are
the ones from the README:

    from safer.adapters.openai_agents import install_safer_for_agents
    hooks = install_safer_for_agents(agent_id=..., agent_name=...)
    result = await Runner.run(agent, "...", hooks=hooks)

The Agents SDK's full machinery — `Agent`, `Runner`, `function_tool`,
`RunHooks`, `TracingProcessor`, the agent loop — runs natively.  We
inject a mocked OpenAI client via `set_default_openai_client(...)` so
no network calls happen.

The agent has an intentional weakness so SAFER's detection layer has
something to flag: the `dispatch_email` tool accepts an arbitrary body
and recipient — a textbook PII-egress surface.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest


pytest.importorskip("agents")


@pytest.fixture(autouse=True)
def _reset_agents_processor_cache():
    """Drop the install_safer_for_agents idempotence cache between tests
    so each test gets a fresh global TracingProcessor registration."""
    from safer.adapters import openai_agents as oa_mod

    oa_mod._reset_for_tests()
    yield
    oa_mod._reset_for_tests()


# ---------- httpx mock for OpenAI chat.completions ------------------------


def _completion_body(*, text=None, tool_calls=None, finish_reason="stop", response_id="chatcmpl"):
    msg: dict = {"role": "assistant", "content": text}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "id": response_id,
        "object": "chat.completion",
        "created": 1735_000_000,
        "model": "gpt-4o",
        "choices": [
            {"index": 0, "message": msg, "finish_reason": finish_reason, "logprobs": None}
        ],
        "usage": {
            "prompt_tokens": 40,
            "completion_tokens": 14,
            "total_tokens": 54,
            "prompt_tokens_details": {"cached_tokens": 0},
        },
    }


def _make_handler():
    """Two-turn loop: model emits tool_call → final text answer."""
    n = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = n["i"]
        n["i"] += 1
        if i == 0:
            body = _completion_body(
                tool_calls=[
                    {
                        "id": "call_alpha",
                        "type": "function",
                        "function": {
                            "name": "lookup_invoice",
                            "arguments": json.dumps({"invoice_id": "INV-2026-0042"}),
                        },
                    }
                ],
                finish_reason="tool_calls",
                response_id="chatcmpl_call",
            )
        else:
            body = _completion_body(
                text=(
                    "Invoice INV-2026-0042 is paid in full ($1,240). "
                    "Receipt sent to billing@acme.example."
                ),
                finish_reason="stop",
                response_id="chatcmpl_final",
            )
        return httpx.Response(200, json=body, headers={"x-request-id": f"req_{i}"})

    return handler


# ---------- the test ------------------------------------------------------


def test_openai_agents_sdk_runner_emits_full_safer_lifecycle(captured_events):
    """A real `Runner.run(agent, ..., hooks=hooks)` exercises the full
    Agents SDK loop: agent_start → llm_start → llm_end → tool_start →
    tool_end → llm_start → llm_end → agent_end."""
    import asyncio

    from agents import (
        Agent,
        Runner,
        function_tool,
        set_default_openai_api,
        set_default_openai_client,
        set_tracing_disabled,
    )
    from openai import AsyncOpenAI

    # Test setup: disable the Agents SDK's own tracing exporter and
    # inject a mocked OpenAI client.  This is test plumbing — a real
    # user wouldn't need it (their OPENAI_API_KEY would be set).
    set_tracing_disabled(True)
    transport = httpx.MockTransport(_make_handler())
    mock_client = AsyncOpenAI(
        api_key="sk-test", http_client=httpx.AsyncClient(transport=transport)
    )
    set_default_openai_client(mock_client, use_for_tracing=False)
    set_default_openai_api("chat_completions")

    # --- The agent the user would actually write ------------------------
    @function_tool
    def lookup_invoice(invoice_id: str) -> str:
        """Return the status of an invoice as a JSON string."""
        return json.dumps(
            {
                "invoice_id": invoice_id,
                "status": "paid",
                "amount_usd": 1240.00,
                "billing_email": "billing@acme.example",
            }
        )

    @function_tool
    def dispatch_email(to: str, subject: str, body: str) -> str:
        """Send an internal notification email.

        Intentional weakness: arbitrary recipient + body — a textbook PII
        egress surface for SAFER's Compliance persona to flag."""
        return f"sent email to {to}"

    billing_agent = Agent(
        name="billing_agent",
        instructions=(
            "You answer invoice questions.  Use `lookup_invoice` to fetch "
            "details, then reply concisely."
        ),
        tools=[lookup_invoice, dispatch_email],
        model="gpt-4o",
    )

    # ====== USER CODE (verbatim from README integration) ==============
    from safer.adapters.openai_agents import install_safer_for_agents

    hooks = install_safer_for_agents(
        agent_id="billing_demo", agent_name="Billing Demo"
    )

    async def go():
        return await Runner.run(
            billing_agent, "Is invoice INV-2026-0042 paid?", hooks=hooks
        )

    result = asyncio.run(go())
    # ===================================================================

    assert "paid" in result.final_output.lower()

    events = captured_events
    hook_names = [e["hook"] for e in events]

    # SAFER captured the full lifecycle.  The very first event is the
    # one-shot `on_agent_register` from `ensure_runtime`; the runtime
    # session_start follows.
    assert hook_names[0] == "on_agent_register"
    assert "on_session_start" in hook_names
    assert hook_names[-1] == "on_session_end"
    assert "before_llm_call" in hook_names
    assert "after_llm_call" in hook_names
    assert "before_tool_use" in hook_names
    assert "after_tool_use" in hook_names
    assert "on_agent_decision" in hook_names
    assert "on_final_output" in hook_names

    # Tool detection — the agent called lookup_invoice
    decisions = [e for e in events if e["hook"] == "on_agent_decision"]
    assert any("lookup_invoice" in d["chosen_action"] for d in decisions)

    before_tool = next(
        e for e in events if e["hook"] == "before_tool_use" and e["tool_name"] == "lookup_invoice"
    )
    assert before_tool["args"] == {"invoice_id": "INV-2026-0042"}

    after_tool = next(
        e for e in events if e["hook"] == "after_tool_use" and e["tool_name"] == "lookup_invoice"
    )
    # The PII surface — billing email — flowed through the tool result
    # and SAFER captured it untouched.
    assert "billing@acme.example" in after_tool["result"]

    # Cost via shared pricing table on every LLM step
    afters = [e for e in events if e["hook"] == "after_llm_call"]
    assert all(a["cost_usd"] > 0 for a in afters)
