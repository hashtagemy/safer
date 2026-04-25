"""OpenAI Agents SDK + Ollama chat REPL with SAFER hook.

The OpenAI Agents SDK (`agents` package) is OpenAI's multi-agent
framework — Agent / Runner / handoffs / tool-calling. We point its
default OpenAI client at Ollama's OpenAI-compatible endpoint
(`/v1`) and run Gemma 4 locally; SAFER instrumentation comes from
`install_safer_for_agents(pin_session=True)`.

Prerequisites:

    ollama serve  &
    ollama pull gemma4:31b
    uv pip install openai-agents

Run:

    SAFER_API_URL=http://127.0.0.1:8000 \\
    SAFER_WS_URL=ws://127.0.0.1:8000/ingest \\
        uv run python examples/openai-agents-ollama/main.py

    # one-shot
    uv run python examples/openai-agents-ollama/main.py --once \\
        --prompt "Hello, what is 17 + 25?"

Type `exit`, `quit`, or hit Ctrl+D to stop.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("safer.examples.openai_agents_ollama")
log.setLevel(logging.INFO)


def _apply_log_level(verbose: bool) -> None:
    if verbose:
        logging.getLogger().setLevel(logging.INFO)
        return
    for name in ("httpx", "httpcore", "openai", "agents", "safer"):
        logging.getLogger(name).setLevel(logging.WARNING)


SYSTEM_PROMPT = (
    "You are a concise, friendly assistant. Keep replies short. "
    "Use tools when explicitly helpful."
)


# ---------- tools ----------


def _build_tools():
    from agents import function_tool

    @function_tool
    def add_numbers(a: float, b: float) -> str:
        """Return the sum of two numbers."""
        return f"{a} + {b} = {a + b}"

    @function_tool
    def get_current_weather(city: str) -> str:
        """Canned weather (offline demo)."""
        canned = {
            "istanbul": "Istanbul: 18°C, partly cloudy.",
            "ankara": "Ankara: 12°C, clear.",
            "izmir": "Izmir: 21°C, sunny.",
        }
        return canned.get(city.strip().lower(), f"{city}: weather data unavailable.")

    return [add_numbers, get_current_weather]


def build_agent_and_hooks(*, model_id: str, host: str):
    from agents import (
        Agent,
        set_default_openai_api,
        set_default_openai_client,
        set_tracing_disabled,
    )
    from openai import AsyncOpenAI

    from safer.adapters.openai_agents import install_safer_for_agents

    # Point the Agents SDK's default OpenAI client at Ollama's
    # OpenAI-compatible endpoint. Ollama doesn't validate the key but
    # the SDK requires one.
    client = AsyncOpenAI(
        base_url=f"{host.rstrip('/')}/v1",
        api_key=os.environ.get("OLLAMA_API_KEY", "ollama-local"),
    )
    set_default_openai_client(client, use_for_tracing=False)
    set_default_openai_api("chat_completions")
    set_tracing_disabled(True)  # drop OpenAI's hosted tracing exporter

    agent = Agent(
        name="ollama_chat_agent",
        instructions=SYSTEM_PROMPT,
        tools=_build_tools(),
        model=model_id,
    )

    hooks = install_safer_for_agents(
        agent_id="ollama_gemma4_openai_agents",
        agent_name="Ollama Gemma4 (OpenAI Agents)",
        pin_session=True,
    )
    return agent, hooks


_EXIT_WORDS = {"exit", "quit", "q", ":q", "bye"}


async def _chat_loop_async(agent, hooks) -> None:
    from agents import Runner

    print(
        "→ Chat mode. The whole conversation is ONE SAFER session.\n"
        "  Type 'exit' / 'quit' or Ctrl+D to stop.\n"
    )
    while True:
        try:
            user_input = input("you ▸ ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n→ Goodbye.")
            return
        if not user_input:
            continue
        if user_input.lower() in _EXIT_WORDS:
            print("→ Goodbye.")
            return
        try:
            result = await Runner.run(agent, user_input, hooks=hooks)
        except KeyboardInterrupt:
            print("\n→ Interrupted; back to prompt.")
            continue
        except Exception as e:
            log.exception("agent turn failed: %s", e)
            print(f"agent ▸ (error: {type(e).__name__}: {e})\n")
            continue
        print(f"agent ▸ {result.final_output}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prompt", default=None, help="Optional opening turn.")
    ap.add_argument("--once", action="store_true", help="Run a single turn and exit.")
    ap.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", "gemma4:31b"))
    ap.add_argument("--host", default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    _apply_log_level(args.verbose)
    os.environ.setdefault("SAFER_API_URL", "http://127.0.0.1:8000")
    os.environ.setdefault("SAFER_WS_URL", "ws://127.0.0.1:8000/ingest")

    print(
        f"→ OpenAI Agents SDK + Ollama · model={args.model} · host={args.host}/v1\n"
        f"→ SAFER backend = {os.environ['SAFER_API_URL']}\n"
        f"→ Dashboard      http://127.0.0.1:5174/live\n"
    )

    agent, hooks = build_agent_and_hooks(model_id=args.model, host=args.host)

    async def _run():
        from agents import Runner

        if args.prompt:
            print(f"you ▸ {args.prompt}")
            try:
                result = await Runner.run(agent, args.prompt, hooks=hooks)
            except Exception as e:
                log.exception("opening turn failed: %s", e)
                print(f"agent ▸ (error: {type(e).__name__}: {e})\n")
            else:
                print(f"agent ▸ {result.final_output}\n")
        if args.once:
            return
        await _chat_loop_async(agent, hooks)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
