"""OpenAI raw SDK + Ollama chat REPL with SAFER hook.

Ollama exposes an OpenAI-compatible REST endpoint at `/v1`, so we can
point the official `openai` Python client at `http://127.0.0.1:11434/v1`
and chat with Gemma 4 locally — zero Anthropic / OpenAI key needed.

`wrap_openai` converts a vanilla `OpenAI()` client into a SAFER-
instrumented one. Every `chat.completions.create()` call (sync or
async, streaming or not) emits the full 9-hook lifecycle. The raw
SDK adapter is already chat-friendly: one client = one SAFER session
+ atexit close.

Prerequisites:

    ollama serve  &
    ollama pull gemma4:31b

Run:

    SAFER_API_URL=http://127.0.0.1:8000 \\
    SAFER_WS_URL=ws://127.0.0.1:8000/ingest \\
        uv run python examples/openai-ollama/main.py

    # one-shot
    uv run python examples/openai-ollama/main.py --once \\
        --prompt "Hello, what is 17 + 25?"

Type `exit`, `quit`, or hit Ctrl+D to stop.
"""

from __future__ import annotations

import argparse
import logging
import os

logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("safer.examples.openai_ollama")
log.setLevel(logging.INFO)


def _apply_log_level(verbose: bool) -> None:
    if verbose:
        logging.getLogger().setLevel(logging.INFO)
        return
    for name in ("httpx", "httpcore", "openai", "safer"):
        logging.getLogger(name).setLevel(logging.WARNING)


SYSTEM_PROMPT = (
    "You are a concise, friendly assistant. Keep replies short."
)


def build_client(*, host: str):
    """`wrap_openai(OpenAI(...))` returns a SAFER-instrumented client.

    `base_url=<ollama>/v1` makes the OpenAI SDK talk to Ollama's
    OpenAI-compatible endpoint — same wire format, no real OpenAI."""
    from openai import OpenAI

    from safer.adapters.openai_client import wrap_openai

    raw = OpenAI(
        base_url=f"{host.rstrip('/')}/v1",
        # Ollama doesn't check the key, but the SDK requires SOMETHING.
        api_key=os.environ.get("OLLAMA_API_KEY", "ollama-local"),
    )
    return wrap_openai(
        raw,
        agent_id="ollama_gemma4_openai",
        agent_name="Ollama Gemma4 (OpenAI SDK)",
    )


_EXIT_WORDS = {"exit", "quit", "q", ":q", "bye"}


def _chat_loop(client, *, model_id: str) -> None:
    print(
        "→ Chat mode. The whole conversation is ONE SAFER session.\n"
        "  Type 'exit' / 'quit' or Ctrl+D to stop.\n"
    )
    history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
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
            resp = client.chat.completions.create(
                model=model_id,
                messages=history,
                max_tokens=600,
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
        text = resp.choices[0].message.content if resp.choices else ""
        history.append({"role": "assistant", "content": text})
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
        f"→ OpenAI SDK + Ollama · model={args.model} · host={args.host}/v1\n"
        f"→ SAFER backend = {os.environ['SAFER_API_URL']}\n"
        f"→ Dashboard      http://127.0.0.1:5174/live\n"
    )

    client = build_client(host=args.host)

    if args.prompt:
        print(f"you ▸ {args.prompt}")
        try:
            resp = client.chat.completions.create(
                model=args.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": args.prompt},
                ],
                max_tokens=600,
            )
        except Exception as e:
            log.exception("opening turn failed: %s", e)
            print(f"agent ▸ (error: {type(e).__name__}: {e})\n")
        else:
            text = resp.choices[0].message.content if resp.choices else ""
            print(f"agent ▸ {text}\n")

    if args.once:
        return
    _chat_loop(client, model_id=args.model)


if __name__ == "__main__":
    main()
