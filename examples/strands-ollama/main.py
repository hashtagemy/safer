"""Strands Agents demo — local Ollama (Gemma 4) instrumented with SAFER.

A minimal, fully-local agent: Strands runtime + Gemma 4 via Ollama,
with `SaferHookProvider` attached so every lifecycle hook lands on
the SAFER dashboard at http://localhost:5174.

The agent has two real tools:
  * `add_numbers(a, b)`     — pure Python arithmetic.
  * `get_current_weather(city)` — returns canned data so the demo runs
                                   offline and deterministically.

Why this combination matters:

  - **No Anthropic key required for the agent itself.** The model is
    Gemma 4 running on Ollama; only SAFER's optional Judge / Inspector
    / Red-Team would call Claude. Event ingestion + dashboard live
    feed work out of the box.
  - **Every adapter call is via the framework's native hook surface.**
    `SaferHookProvider` plugs into `strands.hooks` — no monkey-patching.
  - **Two tools = at least one tool-use round-trip.** That guarantees
    `before_tool_use` + `after_tool_use` + `on_agent_decision` events
    for the dashboard to render in the trace tree.

Prerequisites:

    # Ollama
    ollama serve  &     # if not already running
    ollama pull gemma4:31b   # the demo's default; smaller variants work too

    # Python deps (already installed if you used `uv sync`)
    uv pip install ollama

Run:

    SAFER_API_URL=http://127.0.0.1:8000 \\
    SAFER_WS_URL=ws://127.0.0.1:8000/ingest \\
        uv run python examples/strands-ollama/main.py

    # One-shot mode (skips the chat loop)
    uv run python examples/strands-ollama/main.py \\
        --once --prompt "Add 17 and 25 and weather in Istanbul."

    # Other flags
    --model  gemma4:31b   # default; switch if your box can't run 31B
    --host   http://127.0.0.1:11434

By default the script enters an interactive REPL — type a message,
press Enter, see the agent respond. Each turn becomes its own SAFER
session. Type `exit`, `quit`, or hit Ctrl+D to stop.

Open http://127.0.0.1:5174/live in another tab and watch the events
land in real time as you chat.
"""

from __future__ import annotations

import argparse
import logging
import os

from strands import tool

# Quiet by default so the chat REPL stays readable. Pass --verbose to
# get the full INFO firehose (httpx, strands.telemetry, safer SDK
# startup chatter, etc.) when you're debugging.
logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("safer.examples.strands_ollama")
log.setLevel(logging.INFO)


def _apply_log_level(verbose: bool) -> None:
    """Tame the noisier libraries unless the user asked for noise."""
    if verbose:
        logging.getLogger().setLevel(logging.INFO)
        return
    for name in (
        "httpx",
        "httpcore",
        "strands",
        "strands.telemetry",
        "strands.telemetry.metrics",
        "safer",
        "safer.transport",
        "safer.client",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


# ---------- tools ----------


@tool
def add_numbers(a: float, b: float) -> str:
    """Return the sum of two numbers."""
    result = a + b
    return f"{a} + {b} = {result}"


@tool
def get_current_weather(city: str) -> str:
    """Return canned current weather for `city`.

    Deterministic so the demo runs offline. Real weather lookup is out
    of scope for this hook-validation example.
    """
    canned = {
        "istanbul": "Istanbul: 18°C, partly cloudy, light wind from the north.",
        "ankara": "Ankara: 12°C, clear, dry.",
        "izmir": "Izmir: 21°C, sunny.",
        "san francisco": "San Francisco: 14°C, foggy.",
        "new york": "New York: 9°C, overcast.",
    }
    key = city.strip().lower()
    return canned.get(
        key,
        f"{city.title()}: weather data unavailable in this offline demo.",
    )


# ---------- agent wiring ----------


SYSTEM_PROMPT = (
    "You are a helpful assistant with two tools: `add_numbers` for "
    "arithmetic and `get_current_weather` for weather lookups. Use a "
    "tool when one is clearly needed; otherwise answer directly. Keep "
    "answers concise."
)


def build_agent(*, model_id: str, host: str):
    """Lazy-import Strands so importing this module stays cheap."""
    from strands import Agent
    from strands.models.ollama import OllamaModel

    from safer.adapters.strands import SaferHookProvider

    model = OllamaModel(host=host, model_id=model_id)

    return Agent(
        model=model,
        tools=[add_numbers, get_current_weather],
        system_prompt=SYSTEM_PROMPT,
        # callback_handler=None disables Strands' default token-by-token
        # stdout streaming, so the chat REPL prints each response exactly
        # once via _format_response(). Strands' own logs go through the
        # logging system and are silenced by --verbose default.
        callback_handler=None,
        hooks=[
            SaferHookProvider(
                agent_id="ollama_gemma4_demo",
                agent_name="Ollama Gemma4 (Strands)",
                # pin_session=True wraps the whole REPL in ONE SAFER
                # session — every chat turn lands on the same dashboard
                # session card. Without it each `agent(prompt)` call
                # produces its own short-lived session.
                pin_session=True,
            )
        ],
    )


def _format_response(result) -> str:
    """Pull text out of a Strands `AgentResult` (or fall back to repr)."""
    if hasattr(result, "message"):
        parts: list[str] = []
        for block in result.message.get("content", []):
            text = block.get("text") if isinstance(block, dict) else None
            if text:
                parts.append(text)
        if parts:
            return "\n".join(parts).strip()
    return str(result).strip()


_EXIT_WORDS = {"exit", "quit", "q", ":q", "bye"}


def _chat_loop(agent) -> None:
    """Read-eval-print loop. Each `agent(prompt)` call rotates the
    SaferHookProvider's session_id, so every turn lands as a distinct
    SAFER session on the dashboard."""
    print(
        "→ Chat mode. Each message is a new SAFER session.\n"
        "  Type 'exit' / 'quit' or hit Ctrl+D to stop.\n"
    )
    turn = 0
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

        turn += 1
        try:
            result = agent(user_input)
        except KeyboardInterrupt:
            print("\n→ Interrupted; back to prompt.")
            continue
        except Exception as e:  # noqa: BLE001 — surface any model error
            log.exception("agent turn %d failed: %s", turn, e)
            print(f"agent ▸ (error: {type(e).__name__}: {e})\n")
            continue

        text = _format_response(result)
        print(f"agent ▸ {text}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--prompt",
        default=None,
        help=(
            "Optional opening turn. With --once the script runs only this "
            "prompt and exits; without --once it runs this prompt first "
            "and then drops into the interactive chat loop."
        ),
    )
    ap.add_argument(
        "--once",
        action="store_true",
        help="Run a single turn and exit (skips the interactive REPL).",
    )
    ap.add_argument(
        "--model",
        default=os.environ.get("OLLAMA_MODEL", "gemma4:31b"),
        help="Ollama model id (default: gemma4:31b).",
    )
    ap.add_argument(
        "--host",
        default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
        help="Ollama server URL (default: http://127.0.0.1:11434).",
    )
    ap.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show INFO-level logs from httpx / strands / safer.",
    )
    args = ap.parse_args()

    _apply_log_level(args.verbose)

    # Sensible defaults so users don't need to remember every env var.
    os.environ.setdefault("SAFER_API_URL", "http://127.0.0.1:8000")
    os.environ.setdefault("SAFER_WS_URL", "ws://127.0.0.1:8000/ingest")

    print(
        f"→ Strands agent · model={args.model} · ollama={args.host}\n"
        f"→ SAFER backend = {os.environ['SAFER_API_URL']}\n"
        f"→ Dashboard      http://127.0.0.1:5174/live\n"
    )

    agent = build_agent(model_id=args.model, host=args.host)

    # Optional opening turn — printed identically in one-shot and chat modes.
    if args.prompt:
        print(f"you ▸ {args.prompt}")
        try:
            result = agent(args.prompt)
        except Exception as e:  # noqa: BLE001
            log.exception("opening turn failed: %s", e)
            print(f"agent ▸ (error: {type(e).__name__}: {e})\n")
        else:
            print(f"agent ▸ {_format_response(result)}\n")

    if args.once:
        return

    _chat_loop(agent)


if __name__ == "__main__":
    main()
