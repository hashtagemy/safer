"""End-to-end test for the AWS Bedrock adapter.

Drives a fake `bedrock-runtime` client (one converse round-trip with a
toolUse block + a tool_result on the second turn) through `wrap_bedrock`
and verifies SAFER captures the full 9-hook lifecycle.

The fake client is a tiny dataclass returning canned dicts shaped like
the real Bedrock Converse response — no network, no boto3 needed at
test-time. The adapter doesn't care: it intercepts `converse(...)` on
whichever object you hand it.
"""

from __future__ import annotations

from typing import Any


class _FakeBedrockClient:
    """Two-turn fake: turn 1 returns a toolUse block; turn 2 returns
    final text. Records every `converse(...)` call for inspection."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._idx = 0

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self._idx += 1
        self.calls.append(kwargs)
        if self._idx == 1:
            return {
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"text": "Let me check the weather."},
                            {
                                "toolUse": {
                                    "toolUseId": "tu_abc",
                                    "name": "get_weather",
                                    "input": {"city": "Istanbul"},
                                }
                            },
                        ],
                    }
                },
                "stopReason": "tool_use",
                "usage": {
                    "inputTokens": 100,
                    "outputTokens": 30,
                    "cacheReadInputTokens": 0,
                    "cacheWriteInputTokens": 0,
                },
            }
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"text": "Istanbul is 18°C and partly cloudy."}
                    ],
                }
            },
            "stopReason": "end_turn",
            "usage": {
                "inputTokens": 150,
                "outputTokens": 20,
                "cacheReadInputTokens": 0,
                "cacheWriteInputTokens": 0,
            },
        }


def test_bedrock_converse_full_lifecycle(captured_events):
    from safer.adapters.bedrock import wrap_bedrock

    raw = _FakeBedrockClient()
    client = wrap_bedrock(raw, agent_id="bedrock_demo", agent_name="Bedrock Demo")

    # Turn 1: user message → response with tool_use.
    resp1 = client.converse(
        modelId="anthropic.claude-haiku-4-5",
        messages=[{"role": "user", "content": [{"text": "Weather in Istanbul?"}]}],
        toolConfig={
            "tools": [
                {
                    "toolSpec": {
                        "name": "get_weather",
                        "description": "Get current weather",
                        "inputSchema": {"json": {}},
                    }
                }
            ]
        },
        inferenceConfig={"maxTokens": 600, "temperature": 0.0},
    )
    assert resp1["stopReason"] == "tool_use"

    # Turn 2: caller relays the tool result back; adapter pairs it.
    resp2 = client.converse(
        modelId="anthropic.claude-haiku-4-5",
        messages=[
            {"role": "user", "content": [{"text": "Weather in Istanbul?"}]},
            resp1["output"]["message"],
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "tu_abc",
                            "content": [{"text": "Istanbul: 18°C, partly cloudy."}],
                        }
                    }
                ],
            },
        ],
    )
    assert "18°C" in resp2["output"]["message"]["content"][0]["text"]

    # Force atexit-style close so on_session_end shows up in captured.
    client.close_session(success=True)

    hooks = [e["hook"] for e in captured_events]
    # Onboarding event from ensure_runtime + the runtime session
    assert hooks[0] == "on_agent_register"
    assert "on_session_start" in hooks
    assert hooks.count("before_llm_call") == 2
    assert hooks.count("after_llm_call") == 2
    # tool_use auto-detected from response
    assert "on_agent_decision" in hooks
    assert "before_tool_use" in hooks
    # tool_result on the SECOND request paired into after_tool_use
    assert "after_tool_use" in hooks
    # End-of-turn final output (from end_turn stopReason)
    assert "on_final_output" in hooks
    assert "on_session_end" in hooks

    # Tool detection: name + args carried through
    before = next(e for e in captured_events if e["hook"] == "before_tool_use")
    assert before["tool_name"] == "get_weather"
    assert before["args"] == {"city": "Istanbul"}
    after = next(e for e in captured_events if e["hook"] == "after_tool_use")
    assert after["tool_name"] == "get_weather"
    assert "18°C" in after["result"]

    # Cost tracking — Claude Haiku 4.5 priced via the bundled table.
    afters = [e for e in captured_events if e["hook"] == "after_llm_call"]
    assert all(a["cost_usd"] >= 0 for a in afters)
    assert any(a["cost_usd"] > 0 for a in afters)


def test_bedrock_proxy_passes_through_unknown_attrs(captured_events):
    """`wrap_bedrock` only intercepts converse + converse_stream; every
    other attribute on the underlying client passes through unchanged."""
    from safer.adapters.bedrock import wrap_bedrock

    class _ClientWithExtras:
        meta = {"region": "us-east-1"}

        def list_foundation_models(self):
            return {"modelSummaries": []}

    proxied = wrap_bedrock(
        _ClientWithExtras(), agent_id="bedrock_pass", agent_name="X"
    )
    assert proxied.meta == {"region": "us-east-1"}
    assert proxied.list_foundation_models() == {"modelSummaries": []}


def test_bedrock_emit_error_on_converse_exception(captured_events):
    """If the wrapped client raises, SAFER emits on_error and re-raises."""
    from safer.adapters.bedrock import wrap_bedrock

    class _Boom:
        def converse(self, **kwargs):
            raise RuntimeError("aws denied you")

    client = wrap_bedrock(_Boom(), agent_id="bedrock_err", agent_name="X")
    import pytest

    with pytest.raises(RuntimeError):
        client.converse(
            modelId="anthropic.claude-haiku-4-5",
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
        )

    hooks = [e["hook"] for e in captured_events]
    assert "on_error" in hooks
    err = next(e for e in captured_events if e["hook"] == "on_error")
    assert "aws denied" in err["message"]
