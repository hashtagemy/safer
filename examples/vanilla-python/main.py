"""Custom-SDK example — no framework, just `safer.track_event`.

If your agent runs on something SAFER doesn't have an adapter for yet,
you still get the 9-hook contract by calling `safer.track_event` at
each lifecycle point. That's the whole integration.

Default mode is an interactive REPL: each user turn opens a fresh
SAFER session, fakes a small "look up a city" tool loop, and emits the
six lifecycle events (`on_session_start` → `before_llm_call` →
`after_llm_call` → `before_tool_use` → `after_tool_use` →
`on_final_output` → `on_session_end`). Pass `--prompt "..."` to fire
one canned cycle and exit.

Run:
    uv run python examples/vanilla-python/main.py
    uv run python examples/vanilla-python/main.py --prompt "France"
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

# Allow `from _chat import run_repl` even though we run this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _chat import run_repl  # noqa: E402

from safer import Hook, SaferBlocked, instrument, track_event  # noqa: E402

AGENT_ID = "vanilla_agent"
AGENT_NAME = "Vanilla Demo"

# Toy "knowledge base" — stand-in for whatever lookup your agent does.
CAPITALS = {
    "france": "Paris",
    "turkey": "Ankara",
    "germany": "Berlin",
    "japan": "Tokyo",
    "brazil": "Brasília",
}


def _new_session_id() -> str:
    return f"sess_{int(time.time())}_{uuid.uuid4().hex[:6]}"


def fake_llm_response(question: str) -> str:
    """Stand-in for `client.messages.create(...)`. Replace with the real one."""
    lower = question.lower().strip().rstrip("?").strip()
    for country, capital in CAPITALS.items():
        if country in lower:
            return f"The capital of {country.title()} is {capital}."
    return "I don't know."


def fake_lookup_city(country: str) -> dict[str, str]:
    """Stand-in for whatever your real tool does."""
    return {"city": CAPITALS.get(country.strip().lower(), "unknown")}


def run_one_turn(user_message: str) -> str:
    """Emit one full lifecycle of SAFER events for a single user turn."""
    session_id = _new_session_id()
    common = {"session_id": session_id, "agent_id": AGENT_ID}
    t0 = time.monotonic()

    track_event(
        Hook.ON_SESSION_START,
        {"agent_name": AGENT_NAME, "context": {"user_message": user_message}},
        **common,
    )

    track_event(
        Hook.BEFORE_LLM_CALL,
        {"model": "claude-opus-4-7", "prompt": user_message},
        **common,
    )
    answer = fake_llm_response(user_message)
    track_event(
        Hook.AFTER_LLM_CALL,
        {
            "model": "claude-opus-4-7",
            "response": answer,
            "tokens_in": 42,
            "tokens_out": max(2, len(answer.split())),
            "cost_usd": 0.0005,
            "latency_ms": 350,
        },
        **common,
    )

    # Pretend the model decided to call a `lookup_city` tool.
    country = user_message.strip().split(",")[0].strip().rstrip("?")
    try:
        track_event(
            Hook.BEFORE_TOOL_USE,
            {"tool_name": "lookup_city", "args": {"query": country}},
            **common,
        )
    except SaferBlocked as blocked:
        track_event(
            Hook.ON_SESSION_END,
            {"total_duration_ms": int((time.monotonic() - t0) * 1000), "success": False},
            **common,
        )
        return f"SAFER blocked the tool call: {blocked}"

    tool_result = fake_lookup_city(country)
    track_event(
        Hook.AFTER_TOOL_USE,
        {
            "tool_name": "lookup_city",
            "result": tool_result,
            "duration_ms": 12,
        },
        **common,
    )

    final = answer
    track_event(
        Hook.ON_FINAL_OUTPUT,
        {"final_response": final, "total_steps": 3},
        **common,
    )
    track_event(
        Hook.ON_SESSION_END,
        {
            "total_duration_ms": int((time.monotonic() - t0) * 1000),
            "total_cost_usd": 0.0005,
            "success": True,
        },
        **common,
    )
    return final


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--prompt",
        default=None,
        help="Run one canned turn and exit instead of opening the REPL.",
    )
    args = ap.parse_args()

    instrument(agent_id=AGENT_ID, agent_name=AGENT_NAME)

    if args.prompt:
        print(run_one_turn(args.prompt))
        return

    run_repl(
        run_one_turn,
        banner=(
            "SAFER vanilla-python demo — every turn emits the 6 manual "
            "lifecycle events. In a real agent, replace `fake_llm_response` "
            "and `fake_lookup_city` with your actual LLM and tool calls."
        ),
    )


if __name__ == "__main__":
    main()
