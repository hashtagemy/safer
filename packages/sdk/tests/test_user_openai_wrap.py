"""End-to-end test for the canonical raw-OpenAI tool-use loop with
`wrap_openai` instrumentation.

The agent is a data-analyst bot that answers business questions over a
sales table.  It calls the model, the model emits `tool_calls`, the
loop runs SQL, feeds the rows back as `role="tool"` messages, and
loops until `finish_reason == "stop"` — verbatim from the OpenAI
cookbook (https://cookbook.openai.com/examples/how_to_call_functions_with_chat_models).

The only SAFER-specific lines are the two from the README:

    from safer.adapters.openai_client import wrap_openai
    client = wrap_openai(OpenAI(http_client=...), agent_id=..., agent_name=...)

The real `openai` SDK runs end-to-end; only the wire is mocked via
`httpx.MockTransport` so the test is hermetic.

Intentional weaknesses to exercise SAFER's detection layer:
  * `run_sql` is a raw passthrough to a fake DB — SQL-injection surface.
  * The model is asked about a customer's order including their PII
    columns (email, phone) so the Compliance persona has something
    to flag in the result row.
"""

from __future__ import annotations

import json

import httpx


# ---------- the user's agent: idiomatic OpenAI tool-use loop -------------


SYSTEM = (
    "You are a data analyst.  Use the `run_sql` tool to query the "
    "`sales` table; reply concisely with the answer."
)

# OpenAI chat.completions tool schema.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": "Execute a SQL query against the analytics warehouse.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }
]


def run_sql(query: str) -> str:
    """Fake SQL executor — returns canned rows.  Intentionally accepts
    arbitrary SQL strings (no parameterization, no whitelist) so SAFER's
    Security persona has a real injection surface to flag."""
    if "customers" in query.lower() and "C-1024" in query:
        return json.dumps(
            [
                {
                    "id": "C-1024",
                    "name": "Alex Doe",
                    "email": "alex.doe@example.com",
                    "phone": "+1-555-0142",
                    "lifetime_value": 12_400.50,
                }
            ]
        )
    if "sales" in query.lower():
        return json.dumps(
            [{"month": "2026-04", "revenue": 184_320.0, "orders": 1241}]
        )
    return "[]"


def run_data_analyst_turn(client, user_message: str) -> str:
    """The classic OpenAI tool-use loop, exactly as the cookbook shows."""
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user_message},
    ]
    while True:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=TOOLS,
        )
        msg = resp.choices[0].message
        # Append assistant turn — preserve tool_calls + content
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            return msg.content or ""

        for call in msg.tool_calls:
            args = json.loads(call.function.arguments) if call.function.arguments else {}
            if call.function.name == "run_sql":
                result = run_sql(**args)
            else:
                result = f"unknown tool: {call.function.name}"
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": result}
            )


# ---------- httpx MockTransport mimicking api.openai.com ------------------


def _completion_body(
    *,
    text: str | None,
    tool_calls: list[dict] | None = None,
    finish_reason: str = "stop",
    response_id: str = "chatcmpl_test",
):
    """Build the JSON body that `client.chat.completions.create` decodes."""
    message: dict = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": response_id,
        "object": "chat.completion",
        "created": 1735_000_000,
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
        "usage": {
            "prompt_tokens": 30,
            "completion_tokens": 12,
            "total_tokens": 42,
            "prompt_tokens_details": {"cached_tokens": 0},
        },
    }


def _make_openai_mock_handler():
    """Two-turn tool-use loop:
       turn 1: model emits a tool_call for run_sql
       turn 2: model writes the final answer.
    """
    n = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = n["i"]
        n["i"] += 1
        if i == 0:
            body = _completion_body(
                text=None,
                tool_calls=[
                    {
                        "id": "call_alpha",
                        "type": "function",
                        "function": {
                            "name": "run_sql",
                            "arguments": json.dumps(
                                {"query": "SELECT * FROM customers WHERE id = 'C-1024'"}
                            ),
                        },
                    }
                ],
                finish_reason="tool_calls",
                response_id="chatcmpl_first",
            )
        else:
            body = _completion_body(
                text=(
                    "Customer Alex Doe (C-1024) has a lifetime value of "
                    "$12,400.50.  Email on file: alex.doe@example.com."
                ),
                finish_reason="stop",
                response_id="chatcmpl_final",
            )
        return httpx.Response(200, json=body, headers={"x-request-id": f"req_{i}"})

    return handler


# ---------- the test ------------------------------------------------------


def test_openai_wrap_openai_captures_full_tool_use_loop(captured_events):
    """End-to-end: a data-analyst agent runs through the canonical tool-use
    loop on a real `openai.OpenAI` client whose HTTP transport is
    mocked.  Test body is verbatim README integration; the
    `captured_events` fixture records whatever SAFER emits."""
    # ====== USER CODE (verbatim from README two-line integration) ======
    from openai import OpenAI
    from safer.adapters.openai_client import wrap_openai

    transport = httpx.MockTransport(_make_openai_mock_handler())
    client = wrap_openai(
        OpenAI(api_key="sk-test", http_client=httpx.Client(transport=transport)),
        agent_id="data_analyst",
        agent_name="Data Analyst",
    )

    final = run_data_analyst_turn(
        client, "What's the lifetime value of customer C-1024?"
    )
    # ====================================================================

    assert "12,400" in final or "12400" in final

    events = captured_events
    hooks = [e["hook"] for e in events]

    # 1. Onboarding event from `ensure_runtime` + the runtime session
    assert hooks[0] == "on_agent_register"
    assert "on_session_start" in hooks
    # 2. Two LLM call pairs (one for the tool decision, one for the final text)
    assert hooks.count("before_llm_call") == 2
    assert hooks.count("after_llm_call") == 2

    # 3. Tool call detection on the first response
    decisions = [e for e in events if e["hook"] == "on_agent_decision"]
    assert len(decisions) == 1
    assert decisions[0]["chosen_action"].startswith("run_sql")

    before_tools = [e for e in events if e["hook"] == "before_tool_use"]
    assert len(before_tools) == 1
    assert before_tools[0]["tool_name"] == "run_sql"
    # SQL injection surface — the raw query reached SAFER untouched
    assert "SELECT" in before_tools[0]["args"]["query"]

    # 4. Tool result pairing on the second create call
    after_tools = [e for e in events if e["hook"] == "after_tool_use"]
    assert len(after_tools) == 1
    assert "alex.doe@example.com" in after_tools[0]["result"]

    # 5. Cost — gpt-4o priced via shared pricing table
    afters = [e for e in events if e["hook"] == "after_llm_call"]
    assert all(a["cost_usd"] > 0 for a in afters)
    expected = (30 * 2.50 + 12 * 10.0) / 1_000_000
    for a in afters:
        assert abs(a["cost_usd"] - expected) < 1e-9

    # 6. Provider correlation id surfaced in source field
    assert any("chatcmpl_" in a["source"] for a in afters)


def test_openai_wrap_openai_async_client_emits_events(captured_events):
    """Same flow on `AsyncOpenAI`."""
    import asyncio

    # ====== USER CODE (verbatim from README two-line integration) ======
    from openai import AsyncOpenAI
    from safer.adapters.openai_client import wrap_openai

    transport = httpx.MockTransport(_make_openai_mock_handler())
    client = wrap_openai(
        AsyncOpenAI(
            api_key="sk-test",
            http_client=httpx.AsyncClient(transport=transport),
        ),
        agent_id="data_analyst_async",
        agent_name="Data Analyst (async)",
    )

    async def run_async() -> str:
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Look up C-1024"},
        ]
        while True:
            resp = await client.chat.completions.create(
                model="gpt-4o", messages=messages, tools=TOOLS
            )
            msg = resp.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))
            if not msg.tool_calls:
                return msg.content or ""
            for call in msg.tool_calls:
                args = json.loads(call.function.arguments) if call.function.arguments else {}
                result = run_sql(**args) if call.function.name == "run_sql" else "?"
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )

    final = asyncio.run(run_async())
    # ====================================================================

    assert "alex.doe@example.com" in final.lower()

    events = captured_events
    hooks = [e["hook"] for e in events]
    # Async path must produce real tokens (regression — the older sync
    # wrapper returned a coroutine and reported zero tokens here).
    afters = [e for e in events if e["hook"] == "after_llm_call"]
    assert len(afters) == 2
    for a in afters:
        assert a["tokens_in"] > 0
        assert a["tokens_out"] > 0
