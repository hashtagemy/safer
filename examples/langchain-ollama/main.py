"""LangChain + Ollama (Gemma 4) chat REPL with SAFER hook.

A minimal local-only chat agent built on LangChain's modern
`create_agent`. The model is Gemma 4 via `langchain-ollama`, two simple
tools, and `SaferCallbackHandler(pin_session=True)` so the whole
conversation lands as one SAFER session on the dashboard.

Prerequisites:

    ollama serve  &
    ollama pull gemma4:31b
    uv pip install langchain-ollama

Run:

    SAFER_API_URL=http://127.0.0.1:8000 \\
    SAFER_WS_URL=ws://127.0.0.1:8000/ingest \\
        uv run python examples/langchain-ollama/main.py

    # one-shot
    uv run python examples/langchain-ollama/main.py --once \\
        --prompt "What is 17 + 25 and the weather in Istanbul?"

Type `exit`, `quit`, or hit Ctrl+D to stop.
"""

from __future__ import annotations

import argparse
import logging
import os

logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("safer.examples.langchain_ollama")
log.setLevel(logging.INFO)


def _apply_log_level(verbose: bool) -> None:
    if verbose:
        logging.getLogger().setLevel(logging.INFO)
        return
    for name in ("httpx", "httpcore", "langchain", "safer"):
        logging.getLogger(name).setLevel(logging.WARNING)


# ---------- tools ----------


def _build_tools():
    from langchain_core.tools import tool

    @tool
    def add_numbers(a: float, b: float) -> str:
        """Return the sum of two numbers."""
        return f"{a} + {b} = {a + b}"

    @tool
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

    return [add_numbers, get_current_weather]


# ---------- agent ----------


SYSTEM_PROMPT = (
    "You are a helpful assistant with two tools: `add_numbers` and "
    "`get_current_weather`. Use a tool when one is clearly needed; "
    "otherwise answer directly. Keep answers concise."
)


def build_agent_and_handler(*, model_id: str, host: str):
    from langchain.agents import create_agent
    from langchain_ollama import ChatOllama

    from safer.adapters.langchain import SaferCallbackHandler

    model = ChatOllama(model=model_id, base_url=host)
    agent = create_agent(model=model, tools=_build_tools(), system_prompt=SYSTEM_PROMPT)
    handler = SaferCallbackHandler(
        agent_id="ollama_gemma4_langchain",
        agent_name="Ollama Gemma4 (LangChain)",
        pin_session=True,
    )
    return agent, handler


def _format_response(result) -> str:
    msgs = result.get("messages") if isinstance(result, dict) else None
    if not msgs:
        return str(result)
    last = msgs[-1]
    content = getattr(last, "content", None)
    if isinstance(content, list):
        parts = [c.get("text", "") if isinstance(c, dict) else str(c) for c in content]
        return "\n".join(p for p in parts if p).strip()
    return str(content or last).strip()


_EXIT_WORDS = {"exit", "quit", "q", ":q", "bye"}


def _chat_loop(agent, handler) -> None:
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
            result = agent.invoke(
                {"messages": [{"role": "user", "content": user_input}]},
                config={"callbacks": [handler]},
            )
        except KeyboardInterrupt:
            print("\n→ Interrupted; back to prompt.")
            continue
        except Exception as e:
            log.exception("agent turn failed: %s", e)
            print(f"agent ▸ (error: {type(e).__name__}: {e})\n")
            continue
        print(f"agent ▸ {_format_response(result)}\n")


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
        f"→ LangChain + Ollama · model={args.model} · host={args.host}\n"
        f"→ SAFER backend = {os.environ['SAFER_API_URL']}\n"
        f"→ Dashboard      http://127.0.0.1:5174/live\n"
    )

    agent, handler = build_agent_and_handler(model_id=args.model, host=args.host)

    if args.prompt:
        print(f"you ▸ {args.prompt}")
        try:
            result = agent.invoke(
                {"messages": [{"role": "user", "content": args.prompt}]},
                config={"callbacks": [handler]},
            )
        except Exception as e:
            log.exception("opening turn failed: %s", e)
            print(f"agent ▸ (error: {type(e).__name__}: {e})\n")
        else:
            print(f"agent ▸ {_format_response(result)}\n")

    if args.once:
        return
    _chat_loop(agent, handler)


if __name__ == "__main__":
    main()
