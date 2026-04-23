"""Multi-agent chat CLI — supervisor + worker, instrumented with SAFER.

Run:
    export ANTHROPIC_API_KEY=sk-ant-...
    uv run python examples/coding_assistant/main.py

The supervisor decides whether to answer directly or hand off to the
worker. The worker has six tools (filesystem / web / shell) and runs a
tool loop until it produces a final answer. Both run under their own
SAFER agent_id so `/live` shows two parallel session cards per user
turn.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Put the demo's own directory on sys.path so sibling modules (agents,
# tools, config, memory) import without a package prefix. Keeping it
# simple also makes SAFER's static import-graph walk resolve the full
# tree from main.py alone.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from anthropic import Anthropic  # noqa: E402

from safer import instrument  # noqa: E402

from agents import SupervisorAgent, WorkerAgent  # noqa: E402
from config import (  # noqa: E402
    SUPERVISOR_AGENT_ID,
    SUPERVISOR_AGENT_NAME,
    WORKER_AGENT_ID,
    WORKER_AGENT_NAME,
)
from memory import ConversationMemory  # noqa: E402

BANNER = """\
SAFER coding-assistant demo
  · two agents (supervisor + worker), each streaming into the SAFER
    dashboard at the URL printed below
  · type your question, hit enter; 'quit' or Ctrl-D exits
"""


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 1

    api_url = os.environ.get("SAFER_API_URL", "http://localhost:8000")

    # Two instrument() calls: one per agent_id. The onboarding hook fires
    # once per agent_id per process, so both get their own dashboard card
    # with a code snapshot.
    instrument(
        api_url=api_url,
        agent_id=SUPERVISOR_AGENT_ID,
        agent_name=SUPERVISOR_AGENT_NAME,
    )
    instrument(
        api_url=api_url,
        agent_id=WORKER_AGENT_ID,
        agent_name=WORKER_AGENT_NAME,
    )

    anthropic_client = Anthropic()
    memory = ConversationMemory()
    worker = WorkerAgent(anthropic_client)
    supervisor = SupervisorAgent(anthropic_client, worker, memory)

    print(BANNER)
    print(f"SAFER backend: {api_url}")
    print(f"Dashboard (local default): http://localhost:5173")
    print()

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", ":q"}:
            break
        if user_input.lower() == "clear":
            memory.clear()
            print("(memory cleared)")
            continue
        reply = supervisor.handle(user_input)
        print(f"\n{reply}\n")
    print("bye.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
