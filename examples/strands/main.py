"""Strands Agents demo — a System Diagnostic agent instrumented via
`SaferHookProvider` on the Agent.

The agent has four real tools that work on the host machine:
  * `list_processes(top_n)` — real `ps` output, top-N by CPU.
  * `disk_usage()`          — real `df -h`.
  * `read_log_tail(path)`   — tail of a whitelisted log file.
  * `run_shell(cmd)`        — hazardous: executes a real shell command.
                              Blocked by default Gateway policy; enable
                              deliberately in Policy Studio for a demo.

Requirements:
    pip install 'safer-sdk[strands]'
    pip install strands-agents
    export ANTHROPIC_API_KEY=...

Run:
    python examples/strands/main.py
    python examples/strands/main.py --prompt "Why is my laptop slow?"

Tested on macOS and Linux.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO)


# ---------- real tools (no mock state) ----------


def _ps_command(top_n: int) -> list[str]:
    """Pick the right `ps` flags for the current platform."""
    if sys.platform == "darwin":
        return ["ps", "-Ao", "pid,%cpu,%mem,command", "-r"]
    # Linux
    return ["ps", "aux", "--sort=-%cpu"]


def list_processes(top_n: int = 15) -> str:
    """List the top-N processes by CPU usage (real `ps` output)."""
    try:
        out = subprocess.run(
            _ps_command(top_n),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"ps failed: {e}"
    lines = (out.stdout or "").splitlines()
    # first line is the header, keep it + top_n rows
    return "\n".join(lines[: top_n + 1])


def disk_usage() -> str:
    """Return real `df -h` output."""
    try:
        out = subprocess.run(
            ["df", "-h"], capture_output=True, text=True, timeout=5
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"df failed: {e}"
    return out.stdout or "(no output)"


def _log_whitelist() -> list[Path]:
    candidates = [
        "/var/log/system.log",
        "/var/log/install.log",
        "/var/log/syslog",
        "/var/log/messages",
        str(Path.home() / "Library/Logs/install.log"),
    ]
    return [Path(p) for p in candidates]


def read_log_tail(path: str, lines: int = 100) -> str:
    """Read the last N lines of a whitelisted log file.

    Only the log paths in the built-in whitelist are allowed — attempts
    to read anything else return a refusal message.
    """
    target = Path(path).resolve()
    if target not in {p.resolve() for p in _log_whitelist() if p.exists()}:
        return f"refused: {path} not in whitelist"
    try:
        with target.open("r", errors="replace") as fh:
            buffer = fh.readlines()
        tail = buffer[-lines:]
        return "".join(tail)
    except OSError as e:
        return f"error: {e}"


def run_shell(cmd: str) -> str:
    """Execute a shell command on the host.

    Intentionally dangerous — there's no whitelist. SAFER's default
    Gateway policy blocks this tool unless a Policy Studio rule
    explicitly permits it. The demo leans on that to show live
    policy enforcement.
    """
    try:
        out = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"shell failed: {e}"
    return (out.stdout or "") + (out.stderr or "")


# ---------- agent wiring ----------


SYSTEM_PROMPT = (
    "You are a system diagnostic assistant running on the user's machine. "
    "Use the provided tools to inspect live CPU, disk, and logs, and "
    "report back with a concise root-cause hypothesis. Never use "
    "`run_shell` unless the user explicitly asks for it — prefer the "
    "structured tools."
)


def build_agent():
    """Lazy-import Strands so `import main` stays cheap."""
    from strands import Agent
    from strands.models.anthropic import AnthropicModel

    from safer.adapters.strands import SaferHookProvider

    model = AnthropicModel(
        model_id="claude-opus-4-7",
        client_args={"api_key": os.environ["ANTHROPIC_API_KEY"]},
    )

    return Agent(
        model=model,
        tools=[list_processes, disk_usage, read_log_tail, run_shell],
        system_prompt=SYSTEM_PROMPT,
        hooks=[
            SaferHookProvider(
                agent_id="system_diag",
                agent_name="System Diagnostic (Strands)",
            )
        ],
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--prompt",
        default=(
            "My laptop feels slow. Check the top CPU processes and disk "
            "usage, then tell me what looks off."
        ),
    )
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "ANTHROPIC_API_KEY is required to run this example."
        )

    agent = build_agent()
    result = agent(args.prompt)
    print("\n--- AGENT OUTPUT ---")
    if hasattr(result, "message"):
        for block in result.message.get("content", []):
            text = block.get("text") if isinstance(block, dict) else None
            if text:
                print(text)
    else:
        print(result)


if __name__ == "__main__":
    main()
