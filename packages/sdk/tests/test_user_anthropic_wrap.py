"""End-to-end test that drives a real `anthropic.Anthropic` client through
its idiomatic tool-use loop with `wrap_anthropic` instrumentation.

This is the agent a developer would actually write to run a customer
support bot on Claude — the only SAFER-specific lines are the two
shown in the README:

    from safer.adapters.claude_sdk import wrap_anthropic
    client = wrap_anthropic(Anthropic(http_client=...), agent_id=..., agent_name=...)

The real `Anthropic` SDK runs end to end; only the HTTP transport is
mocked via `httpx.MockTransport` so we don't hit the API.  The mock
acts like the real Anthropic API: it inspects the request body and
returns a scripted sequence of `Message` responses that drive a full
tool-use turn (model → tool_use → user feeds back tool_result → model
finishes with text).

The agent contains intentional weaknesses so SAFER's detection has
something to flag:
  * `lookup_customer` returns plaintext email + phone (PII surface).
  * `run_diagnostic` accepts an arbitrary shell string.
"""

from __future__ import annotations

import json

import httpx


# ---------- the user's agent: idiomatic Anthropic tool-use loop -----------


CUSTOMER_SUPPORT_SYSTEM = (
    "You are a customer-support agent for ACME orders.  Look up customer "
    "and order details with the provided tools, then answer concisely."
)

# Anthropic tool schema (the canonical shape from the SDK docs).
TOOLS = [
    {
        "name": "lookup_customer",
        "description": "Fetch a customer's contact details by id.",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "run_diagnostic",
        "description": "Run an internal diagnostic script.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
]


def lookup_customer(customer_id: str) -> str:
    """Tool implementation — intentionally returns PII (email + phone)
    so SAFER's Compliance persona can flag the egress."""
    fixtures = {
        "C-1024": {
            "name": "Alex Doe",
            "email": "alex.doe@example.com",
            "phone": "+1-555-0142",
            "tier": "gold",
        }
    }
    record = fixtures.get(customer_id)
    if record is None:
        return f"unknown customer: {customer_id}"
    return json.dumps(record)


def run_diagnostic(command: str) -> str:
    """Intentionally accepts an arbitrary shell string (no sandbox).
    SAFER's Security Auditor should flag tool-use targeting this."""
    # No real exec — keep the test hermetic.
    return f"(diagnostic stub for: {command!r})"


def run_customer_support_turn(client, user_message: str) -> str:
    """The classic Anthropic tool-use loop (verbatim from anthropic-cookbook
    style).  Loops until the model emits an `end_turn` stop_reason."""
    messages = [{"role": "user", "content": user_message}]
    while True:
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=512,
            system=CUSTOMER_SUPPORT_SYSTEM,
            tools=TOOLS,
            messages=messages,
        )
        # Append assistant turn (preserve full content list incl. tool_use blocks)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Final answer
            for block in response.content:
                if block.type == "text":
                    return block.text
            return ""

        # Execute every tool the model called and reply with tool_result blocks
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name == "lookup_customer":
                result = lookup_customer(**block.input)
            elif block.name == "run_diagnostic":
                result = run_diagnostic(**block.input)
            else:
                result = f"unknown tool: {block.name}"
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                }
            )
        messages.append({"role": "user", "content": tool_results})


# ---------- httpx MockTransport that pretends to be api.anthropic.com ----


def _msg_body(content_blocks: list[dict], stop_reason: str = "end_turn", model: str = "claude-opus-4-7"):
    """Build a Message JSON the way Anthropic's HTTP API returns it."""
    return {
        "id": f"msg_{abs(hash(json.dumps(content_blocks))) % 10**12}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 25,
            "output_tokens": 18,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


def _make_anthropic_mock_handler():
    """Returns a handler that scripts a 2-turn tool-use loop:
       turn 1: model emits tool_use(lookup_customer) → tool_use(run_diagnostic)
       turn 2: model emits final text answer (end_turn).
    """
    call_index = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        # Decide the response based on which call this is.
        n = call_index["n"]
        call_index["n"] += 1
        if n == 0:
            # First call: model decides to call two tools
            body = _msg_body(
                [
                    {"type": "text", "text": "Looking up the customer..."},
                    {
                        "type": "tool_use",
                        "id": "toolu_01a",
                        "name": "lookup_customer",
                        "input": {"customer_id": "C-1024"},
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_01b",
                        "name": "run_diagnostic",
                        "input": {"command": "systemctl status acme-orders"},
                    },
                ],
                stop_reason="tool_use",
            )
        else:
            # Final turn: model produces its text answer
            body = _msg_body(
                [
                    {
                        "type": "text",
                        "text": (
                            "Hi Alex — your account looks healthy.  "
                            "I'll email you a follow-up at alex.doe@example.com."
                        ),
                    }
                ],
                stop_reason="end_turn",
            )
        return httpx.Response(
            200, json=body, headers={"x-request-id": f"req_test_{n}"}
        )

    return handler


# ---------- the test ------------------------------------------------------


def test_anthropic_wrap_anthropic_captures_full_tool_use_loop(captured_events):
    """End-to-end: a customer-support agent runs through a multi-step
    tool-use loop on a real `anthropic.Anthropic` client whose HTTP
    transport is mocked.  The body of this test below is exactly what a
    user would copy-paste from the README — adapter import, adapter
    line, the framework's own agent code.  The `captured_events`
    fixture records what SAFER emits; the test never touches SAFER
    internals."""
    # ====== USER CODE (verbatim from README's two-line integration) ======
    from anthropic import Anthropic
    from safer.adapters.claude_sdk import wrap_anthropic

    transport = httpx.MockTransport(_make_anthropic_mock_handler())
    client = wrap_anthropic(
        Anthropic(api_key="sk-test", http_client=httpx.Client(transport=transport)),
        agent_id="customer_support",
        agent_name="Customer Support",
    )

    final_text = run_customer_support_turn(
        client, "Hi, can you check on customer C-1024 and confirm everything's fine?"
    )
    # =====================================================================

    assert "Alex" in final_text or "alex" in final_text.lower()

    # Now verify what SAFER captured (test-only, not user code).
    events = captured_events
    hook_names = [e["hook"] for e in events]

    # 1. The very first event is the onboarding `on_agent_register` —
    #    SAFER's `ensure_runtime` fires this once when the SDK boots.
    assert hook_names[0] == "on_agent_register"
    # ...followed immediately by the runtime session_start.
    assert "on_session_start" in hook_names

    # 2. Each create call gives us a before/after_llm pair
    assert hook_names.count("before_llm_call") == 2
    assert hook_names.count("after_llm_call") == 2

    # 3. Tool-use auto-detection on the first response (2 tools called)
    decisions = [e for e in events if e["hook"] == "on_agent_decision"]
    assert len(decisions) == 2, f"expected 2 tool decisions, got {len(decisions)}"
    decision_targets = [d["chosen_action"].split("(")[0] for d in decisions]
    assert sorted(decision_targets) == ["lookup_customer", "run_diagnostic"]

    before_tools = [e for e in events if e["hook"] == "before_tool_use"]
    assert len(before_tools) == 2
    lookup = next(e for e in before_tools if e["tool_name"] == "lookup_customer")
    assert lookup["args"] == {"customer_id": "C-1024"}
    diag = next(e for e in before_tools if e["tool_name"] == "run_diagnostic")
    assert "systemctl" in diag["args"]["command"]

    # 4. After the user feeds tool_result back on the second create call,
    #    SAFER's `_drain_pending_tool_results` must synthesize after_tool_use.
    after_tools = [e for e in events if e["hook"] == "after_tool_use"]
    assert len(after_tools) == 2
    after_lookup = next(e for e in after_tools if e["tool_name"] == "lookup_customer")
    # PII leak in the tool's stub — SAFER captured the literal egress.
    assert "alex.doe@example.com" in after_lookup["result"]

    # 5. Cost — Anthropic Opus 4.7 priced via the shared pricing table.
    afters = [e for e in events if e["hook"] == "after_llm_call"]
    assert all(a["cost_usd"] > 0 for a in afters), (
        f"expected non-zero cost on every Opus call; got {[a['cost_usd'] for a in afters]}"
    )
    expected_cost = (25 * 15.0 + 18 * 75.0) / 1_000_000
    for a in afters:
        assert abs(a["cost_usd"] - expected_cost) < 1e-9


def test_anthropic_wrap_anthropic_async_client_emits_events(captured_events):
    """Same demo via `AsyncAnthropic` — proves the README two-line pattern
    works for async code too."""
    import asyncio

    # ====== USER CODE (verbatim from README two-line integration) =======
    from anthropic import AsyncAnthropic
    from safer.adapters.claude_sdk import wrap_anthropic

    transport = httpx.MockTransport(_make_anthropic_mock_handler())
    client = wrap_anthropic(
        AsyncAnthropic(
            api_key="sk-test",
            http_client=httpx.AsyncClient(transport=transport),
        ),
        agent_id="customer_support_async",
        agent_name="Customer Support (async)",
    )

    async def run_async_turn() -> str:
        messages = [{"role": "user", "content": "Check on C-1024 please"}]
        while True:
            response = await client.messages.create(
                model="claude-opus-4-7",
                max_tokens=512,
                system=CUSTOMER_SUPPORT_SYSTEM,
                tools=TOOLS,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "end_turn":
                for block in response.content:
                    if block.type == "text":
                        return block.text
                return ""
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.name == "lookup_customer":
                    result = lookup_customer(**block.input)
                elif block.name == "run_diagnostic":
                    result = run_diagnostic(**block.input)
                else:
                    result = "unknown tool"
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )
            messages.append({"role": "user", "content": tool_results})

    final = asyncio.run(run_async_turn())
    # =====================================================================

    assert "alex" in final.lower()

    events = captured_events
    hook_names = [e["hook"] for e in events]
    # Same lifecycle as the sync test
    assert "on_session_start" in hook_names
    assert hook_names.count("before_llm_call") == 2
    assert hook_names.count("after_llm_call") == 2
    assert hook_names.count("on_agent_decision") == 2
    assert hook_names.count("before_tool_use") == 2
    assert hook_names.count("after_tool_use") == 2

    # Async path: tokens MUST be real — the regression where the sync
    # proxy dropped the await reported zero tokens here.
    afters = [e for e in events if e["hook"] == "after_llm_call"]
    for a in afters:
        assert a["tokens_in"] > 0
        assert a["tokens_out"] > 0
