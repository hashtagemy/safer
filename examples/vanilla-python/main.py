"""Custom-SDK example — no framework, just `safer.track_event`.

If your agent runs on something SAFER doesn't have an adapter for yet,
you still get the 9-hook contract by calling `safer.track_event` at
each lifecycle point. That's the whole integration.

Run:
    uv run python examples/vanilla-python/main.py
"""

from __future__ import annotations

import time

from safer import Hook, SaferBlocked, instrument, track_event

AGENT_ID = "vanilla_agent"
SESSION_ID = f"sess_{int(time.time())}"


def main() -> None:
    instrument(agent_id=AGENT_ID, agent_name="Vanilla Demo")

    track_event(
        Hook.ON_SESSION_START,
        {"agent_name": "Vanilla Demo", "context": {"intent": "lookup"}},
        session_id=SESSION_ID,
        agent_id=AGENT_ID,
    )

    track_event(
        Hook.BEFORE_LLM_CALL,
        {"model": "claude-opus-4-7", "prompt": "What is the capital of France?"},
        session_id=SESSION_ID,
        agent_id=AGENT_ID,
    )
    # ...your LLM call here...
    track_event(
        Hook.AFTER_LLM_CALL,
        {
            "model": "claude-opus-4-7",
            "response": "Paris.",
            "tokens_in": 42,
            "tokens_out": 2,
            "cost_usd": 0.0005,
            "latency_ms": 350,
        },
        session_id=SESSION_ID,
        agent_id=AGENT_ID,
    )

    try:
        track_event(
            Hook.BEFORE_TOOL_USE,
            {"tool_name": "lookup_city", "args": {"query": "France"}},
            session_id=SESSION_ID,
            agent_id=AGENT_ID,
        )
    except SaferBlocked as blocked:
        print(f"SAFER blocked the tool call: {blocked}")
        return

    track_event(
        Hook.AFTER_TOOL_USE,
        {"tool_name": "lookup_city", "result": {"city": "Paris"}, "duration_ms": 12},
        session_id=SESSION_ID,
        agent_id=AGENT_ID,
    )

    track_event(
        Hook.ON_FINAL_OUTPUT,
        {"final_response": "The capital of France is Paris.", "total_steps": 3},
        session_id=SESSION_ID,
        agent_id=AGENT_ID,
    )

    track_event(
        Hook.ON_SESSION_END,
        {"total_duration_ms": 400, "total_cost_usd": 0.0005, "success": True},
        session_id=SESSION_ID,
        agent_id=AGENT_ID,
    )
    print("Emitted 6 lifecycle events to the SAFER backend.")


if __name__ == "__main__":
    main()
