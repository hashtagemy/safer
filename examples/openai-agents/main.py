"""OpenAI Agents SDK + SAFER demo.

A two-agent setup driven by the official `openai-agents` framework.  The
`triage_agent` decides which specialist agent to hand off to; specialists
have their own tools.  SAFER instruments the entire run via
`install_safer_for_agents(...)` — both `RunHooks` (per-call lifecycle) and
the global `TracingProcessor` (span-level telemetry) are registered.

What this exercises (no manual SAFER calls beyond the two-line install):
  * `on_agent_start` / `on_agent_end` → SAFER session_start / final_output
    + session_end
  * `on_handoff` → SAFER on_agent_decision(decision_type="handoff")
  * `on_llm_start` / `on_llm_end` → SAFER before/after_llm_call with real
    token counts + cost
  * `on_tool_start` / `on_tool_end` → SAFER before/after_tool_use with
    parsed arguments

Requirements:
    pip install openai-agents safer-sdk
    export OPENAI_API_KEY=...
    export SAFER_API_URL=http://localhost:8000   # backend on docker-compose

Run:
    python examples/openai-agents/main.py
    python examples/openai-agents/main.py --prompt "What's 17 + 25 and capital of France?"

Backend: SAFER's local FastAPI + dashboard via `docker compose up`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

logging.basicConfig(level=logging.INFO)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--prompt",
        default=(
            "I have two questions: what's 12 + 30, and what's the capital "
            "of France? Use the right specialist for each."
        ),
    )
    args = ap.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is required to run this example.")

    # Lazy import — keeps `python -c 'import main'` cheap if the user
    # hasn't installed the agents SDK yet.
    from agents import Agent, Runner, function_tool
    from safer.adapters.openai_agents import install_safer_for_agents

    # --- Tools (real implementations; no fake state) -----------------

    @function_tool
    def add(a: float, b: float) -> str:
        """Add two numbers and return the result as a string."""
        return f"{a + b}"

    @function_tool
    def country_capital(country: str) -> str:
        """Return the capital of the given country.

        This is a small lookup so the demo runs offline; real apps would
        hit a knowledge base or web search."""
        capitals = {
            "france": "Paris",
            "turkey": "Ankara",
            "germany": "Berlin",
            "japan": "Tokyo",
            "brazil": "Brasília",
        }
        return capitals.get(country.strip().lower(), f"unknown country: {country!r}")

    # --- Specialist agents -------------------------------------------

    math_agent = Agent(
        name="math_specialist",
        instructions=(
            "You answer arithmetic questions concisely.  Use the `add` tool "
            "for any addition.  Reply with just the result."
        ),
        tools=[add],
    )

    geo_agent = Agent(
        name="geo_specialist",
        instructions=(
            "You answer geography questions concisely.  Use `country_capital` "
            "to look up capitals.  Reply with just the result."
        ),
        tools=[country_capital],
    )

    # --- Triage / orchestrator ---------------------------------------

    triage = Agent(
        name="triage",
        instructions=(
            "You route the user's message to the right specialist.  If the "
            "user asks a math question, hand off to `math_specialist`.  If "
            "they ask about country capitals or geography, hand off to "
            "`geo_specialist`.  If they ask multiple questions, answer them "
            "one at a time using the appropriate handoff each time."
        ),
        handoffs=[math_agent, geo_agent],
    )

    # --- SAFER integration: ONE install line + per-run hooks ---------
    hooks = install_safer_for_agents(
        agent_id="agents_sdk_demo",
        agent_name="OpenAI Agents Demo",
    )
    # -----------------------------------------------------------------

    async def run_agent() -> None:
        result = await Runner.run(triage, args.prompt, hooks=hooks)
        print("\n--- AGENT OUTPUT ---")
        print(result.final_output)

    asyncio.run(run_agent())


if __name__ == "__main__":
    main()
