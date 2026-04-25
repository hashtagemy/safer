"""Tiny shared chat REPL used by every example.

Each example builds its own callable that maps `(user_message: str) -> str`
and passes it here. The helper keeps the loop dumb on purpose: read a
line, call the callable, print the reply. Examples remain free to manage
their own conversation memory, sessions, and SAFER instrumentation.

Usage:

    from _chat import run_repl

    def on_message(user: str) -> str:
        return agent.invoke(user)

    run_repl(on_message, banner="Coding analyst chat")
"""

from __future__ import annotations

import sys
from typing import Callable, Optional

QUIT_WORDS = {"quit", "exit", ":q"}


def run_repl(
    on_message: Callable[[str], str],
    *,
    banner: str = "",
    on_clear: Optional[Callable[[], None]] = None,
    prompt: str = "> ",
) -> None:
    """Run a blocking chat loop until the user quits.

    Parameters
    ----------
    on_message:
        Callback invoked with each user message. Whatever it returns is
        printed back to the user. Exceptions are caught and shown so a
        bad turn does not kill the REPL.
    banner:
        Printed once at start. Empty string skips it.
    on_clear:
        Optional callback fired when the user types `clear`. Lets the
        host example wipe its own conversation memory. If not provided
        the keyword is treated as ordinary input.
    prompt:
        The input prompt displayed each turn.
    """
    if banner:
        print(banner)
    print("(type 'quit', 'exit', ':q', or Ctrl-D to exit)")
    if on_clear is not None:
        print("(type 'clear' to reset conversation memory)")
    print()

    while True:
        try:
            user_input = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input:
            continue
        lowered = user_input.lower()
        if lowered in QUIT_WORDS:
            break
        if lowered == "clear" and on_clear is not None:
            on_clear()
            print("(memory cleared)")
            continue
        try:
            reply = on_message(user_input)
        except Exception as e:  # pragma: no cover — defensive
            print(f"[error] {type(e).__name__}: {e}", file=sys.stderr)
            continue
        print(f"\n{reply}\n")

    print("bye.")
