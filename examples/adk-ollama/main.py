"""Google ADK + Ollama (Gemma 4) chat REPL with SAFER hook.

ADK ships first-class for Gemini, but its `LiteLlm` model adapter
opens the door to any LiteLLM-supported provider — including a local
Ollama. We wire `gemma4:31b` via the `ollama_chat/...` LiteLLM provider
prefix and instrument the Runner with `SaferAdkPlugin(pin_session=True)`.

Prerequisites:

    ollama serve  &
    ollama pull gemma4:31b
    uv pip install 'google-adk' litellm

Run:

    SAFER_API_URL=http://127.0.0.1:8000 \\
    SAFER_WS_URL=ws://127.0.0.1:8000/ingest \\
        uv run python examples/adk-ollama/main.py

    # one-shot
    uv run python examples/adk-ollama/main.py --once \\
        --prompt "Add 17 and 25 and weather in Istanbul."

Type `exit`, `quit`, or hit Ctrl+D to stop.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("safer.examples.adk_ollama")
log.setLevel(logging.INFO)


def _apply_log_level(verbose: bool) -> None:
    if verbose:
        logging.getLogger().setLevel(logging.INFO)
        return
    for name in (
        "httpx",
        "httpcore",
        "google.adk",
        "google_adk",
        "google.genai",
        "litellm",
        "LiteLLM",
        "safer",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


# ---------- tools ----------


def add_numbers(a: float, b: float) -> str:
    """Return the sum of two numbers."""
    return f"{a} + {b} = {a + b}"


def get_current_weather(city: str) -> str:
    """Return canned current weather for `city` (offline demo)."""
    canned = {
        "istanbul": "Istanbul: 18°C, partly cloudy.",
        "ankara": "Ankara: 12°C, clear.",
        "izmir": "Izmir: 21°C, sunny.",
        "san francisco": "San Francisco: 14°C, foggy.",
    }
    key = city.strip().lower()
    return canned.get(key, f"{city.title()}: weather data unavailable.")


# ---------- agent ----------


SYSTEM_PROMPT = (
    "You are a helpful assistant with two tools: `add_numbers` and "
    "`get_current_weather`. Use a tool when one is clearly needed; "
    "otherwise answer directly. Keep answers concise."
)


def build_runner_and_plugin(*, model_id: str, host: str):
    from google.adk.agents import LlmAgent
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.runners import InMemoryRunner

    from safer.adapters.google_adk import SaferAdkPlugin

    # LiteLLM provider prefix: `ollama_chat/<model>` (chat completion API)
    # against an OpenAI-compatible endpoint Ollama exposes.
    os.environ.setdefault("OLLAMA_API_BASE", host)
    model = LiteLlm(model=f"ollama_chat/{model_id}")

    agent = LlmAgent(
        model=model,
        name="adk_chat_agent",
        instruction=SYSTEM_PROMPT,
        tools=[add_numbers, get_current_weather],
    )

    plugin = SaferAdkPlugin(
        agent_id="ollama_gemma4_adk",
        agent_name="Ollama Gemma4 (ADK)",
        pin_session=True,
    )
    runner = InMemoryRunner(
        agent=agent, app_name="adk_chat_app", plugins=[plugin]
    )
    return runner, plugin


async def _ensure_session(runner) -> str:
    """Create the ADK SessionService session (separate concept from
    SAFER's session) and return its id for this REPL."""
    sess = await runner.session_service.create_session(
        app_name="adk_chat_app", user_id="user_1"
    )
    return sess.id


async def _run_one_turn(runner, adk_session_id: str, text: str) -> str:
    from google.genai import types

    msg = types.Content(role="user", parts=[types.Part(text=text)])
    last_text = ""
    async for event in runner.run_async(
        user_id="user_1", session_id=adk_session_id, new_message=msg
    ):
        content = getattr(event, "content", None)
        if content is None:
            continue
        for part in getattr(content, "parts", []) or []:
            t = getattr(part, "text", None)
            if t:
                last_text = t
    return last_text.strip()


_EXIT_WORDS = {"exit", "quit", "q", ":q", "bye"}


async def _chat_loop_async(runner) -> None:
    adk_session_id = await _ensure_session(runner)
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
            text = await _run_one_turn(runner, adk_session_id, user_input)
        except KeyboardInterrupt:
            print("\n→ Interrupted; back to prompt.")
            continue
        except Exception as e:
            log.exception("agent turn failed: %s", e)
            print(f"agent ▸ (error: {type(e).__name__}: {e})\n")
            continue
        print(f"agent ▸ {text}\n")


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
        f"→ Google ADK + Ollama (LiteLLM) · model={args.model} · host={args.host}\n"
        f"→ SAFER backend = {os.environ['SAFER_API_URL']}\n"
        f"→ Dashboard      http://127.0.0.1:5174/live\n"
    )

    runner, _plugin = build_runner_and_plugin(model_id=args.model, host=args.host)

    async def _run():
        adk_session_id = await _ensure_session(runner)
        if args.prompt:
            print(f"you ▸ {args.prompt}")
            try:
                text = await _run_one_turn(runner, adk_session_id, args.prompt)
            except Exception as e:
                log.exception("opening turn failed: %s", e)
                print(f"agent ▸ (error: {type(e).__name__}: {e})\n")
            else:
                print(f"agent ▸ {text}\n")
        if args.once:
            return
        await _chat_loop_async(runner)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
