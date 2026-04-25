"""Anthropic raw SDK chat REPL with SAFER hook.

Smallest possible chat agent against the real Anthropic API,
instrumented with `SaferAnthropic` — a drop-in replacement for
`anthropic.Anthropic`. Every `messages.create()` call from this script
emits SAFER lifecycle events. The raw SDK adapter is already
chat-friendly out of the box: one client = one SAFER session, atexit
fires `on_session_end` once.

Prerequisites:

    # ANTHROPIC_API_KEY in your environment (your `.env` is already
    # loaded by uv via the workspace root .env file).
    export ANTHROPIC_API_KEY=sk-ant-...

Run:

    SAFER_API_URL=http://127.0.0.1:8000 \\
    SAFER_WS_URL=ws://127.0.0.1:8000/ingest \\
        uv run python examples/anthropic-chat/main.py

    # one-shot
    uv run python examples/anthropic-chat/main.py --once \\
        --prompt "Hello, what is 17 + 25?"

Type `exit`, `quit`, or hit Ctrl+D to stop.
"""

from __future__ import annotations

import argparse
import logging
import os

logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("safer.examples.anthropic_chat")
log.setLevel(logging.INFO)


def _apply_log_level(verbose: bool) -> None:
    if verbose:
        logging.getLogger().setLevel(logging.INFO)
        return
    for name in ("httpx", "httpcore", "anthropic", "safer"):
        logging.getLogger(name).setLevel(logging.WARNING)


SYSTEM_PROMPT = (
    "You are a concise, friendly assistant. Keep replies short."
)


def build_client(*, model_id: str):
    """SaferAnthropic IS an `anthropic.Anthropic` subclass — every API
    the real SDK exposes still works, plus SAFER hooks fire automatically."""
    from safer.adapters.claude_sdk import SaferAnthropic

    return SaferAnthropic(
        agent_id="anthropic_chat_demo",
        agent_name=f"Anthropic Chat ({model_id})",
    )


_EXIT_WORDS = {"exit", "quit", "q", ":q", "bye"}


def _chat_loop(client, *, model_id: str) -> None:
    print(
        "→ Chat mode. The whole conversation is ONE SAFER session.\n"
        "  Type 'exit' / 'quit' or Ctrl+D to stop.\n"
    )
    history: list[dict] = []
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
        history.append({"role": "user", "content": user_input})
        try:
            resp = client.messages.create(
                model=model_id,
                max_tokens=1000,
                system=SYSTEM_PROMPT,
                messages=history,
            )
        except KeyboardInterrupt:
            print("\n→ Interrupted; back to prompt.")
            history.pop()
            continue
        except Exception as e:
            log.exception("agent turn failed: %s", e)
            print(f"agent ▸ (error: {type(e).__name__}: {e})\n")
            history.pop()
            continue
        text_parts = [
            getattr(b, "text", "") for b in (resp.content or []) if getattr(b, "type", "") == "text"
        ]
        text = "\n".join(t for t in text_parts if t).strip()
        history.append({"role": "assistant", "content": text})
        print(f"agent ▸ {text}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prompt", default=None, help="Optional opening turn.")
    ap.add_argument("--once", action="store_true", help="Run a single turn and exit.")
    ap.add_argument(
        "--model",
        default=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        help="Claude model id (default: claude-haiku-4-5; cheap + fast for chat).",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    _apply_log_level(args.verbose)
    os.environ.setdefault("SAFER_API_URL", "http://127.0.0.1:8000")
    os.environ.setdefault("SAFER_WS_URL", "ws://127.0.0.1:8000/ingest")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "ANTHROPIC_API_KEY is required. Set it in `.env` or your shell."
        )

    print(
        f"→ Anthropic SDK · model={args.model}\n"
        f"→ SAFER backend = {os.environ['SAFER_API_URL']}\n"
        f"→ Dashboard      http://127.0.0.1:5174/live\n"
    )

    client = build_client(model_id=args.model)

    if args.prompt:
        print(f"you ▸ {args.prompt}")
        try:
            resp = client.messages.create(
                model=args.model,
                max_tokens=1000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": args.prompt}],
            )
        except Exception as e:
            log.exception("opening turn failed: %s", e)
            print(f"agent ▸ (error: {type(e).__name__}: {e})\n")
        else:
            text_parts = [
                getattr(b, "text", "")
                for b in (resp.content or [])
                if getattr(b, "type", "") == "text"
            ]
            print(f"agent ▸ {''.join(text_parts).strip()}\n")

    if args.once:
        return
    _chat_loop(client, model_id=args.model)


if __name__ == "__main__":
    main()
