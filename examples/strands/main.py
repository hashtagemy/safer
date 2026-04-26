"""Strands Agents demo — a "System Diagnostic" chat agent instrumented
via `SaferHookProvider`.

The agent has eight real tools that work on the host machine:
  * `list_processes(top_n)`  — top-N processes by CPU (real `ps`).
  * `disk_usage()`           — `df -h`.
  * `memory_pressure()`      — `vm_stat` (macOS) or `free -h` (Linux).
  * `network_listeners()`    — `lsof -iTCP -sTCP:LISTEN -n -P`.
  * `top_files_by_size(path)`— `du -h -d 1 <path> | sort -rh`.
  * `uptime_info()`          — `uptime`.
  * `read_log_tail(path)`    — tail of a whitelisted log file.
  * `run_shell(cmd)`         — hazardous; blocked by default Gateway
                                policy. Enable deliberately in Policy
                                Studio for a demo.

Default mode is an interactive chat REPL — every turn is a fresh agent
call, so SAFER's `/live` view shows the full hook stream as you talk.
Pass `--prompt "..."` to run one shot and exit.

Requirements:
    pip install 'safer-sdk[strands]'
    pip install strands-agents
    export ANTHROPIC_API_KEY=...

Run:
    python examples/strands/main.py                     # interactive chat
    python examples/strands/main.py --prompt "..."      # one-shot

Tested on macOS and Linux.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import warnings
from pathlib import Path

# Strands' anthropic adapter ships block types (ParsedTextBlock, etc.)
# that pydantic + the installed anthropic SDK don't fully recognise.
# The mismatch is harmless and noisy — silence it for a clean REPL.
warnings.filterwarnings("ignore", category=UserWarning, module=r"pydantic\.")

from strands import tool  # noqa: E402

# Allow `from _chat import run_repl` even though we run this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _chat import run_repl  # noqa: E402

logging.basicConfig(level=logging.INFO)


# ---------- real tools (no mock state) ----------
#
# Each tool is decorated with `@tool` from strands so the agent's tool
# registry actually picks them up. Without the decorator Strands logs
# `unrecognized tool specification` and silently drops the function.


def _ps_command() -> list[str]:
    if sys.platform == "darwin":
        return ["ps", "-Ao", "pid,%cpu,%mem,command", "-r"]
    return ["ps", "aux", "--sort=-%cpu"]


@tool
def list_processes(top_n: int = 15) -> str:
    """List the top-N processes by CPU usage (real `ps` output)."""
    try:
        out = subprocess.run(
            _ps_command(), capture_output=True, text=True, timeout=5
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"ps failed: {e}"
    lines = (out.stdout or "").splitlines()
    return "\n".join(lines[: top_n + 1])  # header + top_n


@tool
def disk_usage() -> str:
    """Return real `df -h` output."""
    try:
        out = subprocess.run(
            ["df", "-h"], capture_output=True, text=True, timeout=5
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"df failed: {e}"
    return out.stdout or "(no output)"


@tool
def memory_pressure() -> str:
    """Show RAM pressure — `vm_stat` on macOS, `free -h` on Linux."""
    cmd = ["vm_stat"] if sys.platform == "darwin" else ["free", "-h"]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"{cmd[0]} failed: {e}"
    return out.stdout or "(no output)"


@tool
def network_listeners() -> str:
    """List TCP ports currently in LISTEN state (`lsof -iTCP -sTCP:LISTEN`)."""
    try:
        out = subprocess.run(
            ["lsof", "-iTCP", "-sTCP:LISTEN", "-n", "-P"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"lsof failed: {e}"
    lines = (out.stdout or "").splitlines()
    if len(lines) <= 1:
        return out.stdout or "(no listeners)"
    # Keep the header + 30 rows so the model has room to reason.
    return "\n".join(lines[:31])


@tool
def top_files_by_size(path: str = ".") -> str:
    """Return the largest entries one level deep under `path` (`du -h -d 1`)."""
    target = Path(path).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        return f"not a directory: {path}"
    try:
        out = subprocess.run(
            ["du", "-h", "-d", "1", str(target)],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"du failed: {e}"
    rows = sorted(
        (line for line in (out.stdout or "").splitlines() if line.strip()),
        key=lambda r: _parse_du_size(r.split("\t", 1)[0]),
        reverse=True,
    )
    return "\n".join(rows[:15]) or "(no entries)"


def _parse_du_size(token: str) -> float:
    units = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    token = token.strip()
    if not token:
        return 0.0
    unit = token[-1].upper()
    if unit not in units:
        try:
            return float(token)
        except ValueError:
            return 0.0
    try:
        return float(token[:-1]) * units[unit]
    except ValueError:
        return 0.0


@tool
def uptime_info() -> str:
    """Return `uptime` output (load average + boot age)."""
    try:
        out = subprocess.run(
            ["uptime"], capture_output=True, text=True, timeout=5
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"uptime failed: {e}"
    return (out.stdout or "(no output)").strip()


def _log_whitelist() -> list[Path]:
    candidates = [
        "/var/log/system.log",
        "/var/log/install.log",
        "/var/log/syslog",
        "/var/log/messages",
        str(Path.home() / "Library/Logs/install.log"),
    ]
    return [Path(p) for p in candidates]


@tool
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


@tool
def run_shell(cmd: str) -> str:
    """Execute a shell command on the host.

    Intentionally dangerous — there's no whitelist. SAFER's default
    Gateway policy blocks this tool unless a Policy Studio rule
    explicitly permits it. The demo leans on that to show live policy
    enforcement.
    """
    try:
        out = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=10
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"shell failed: {e}"
    return (out.stdout or "") + (out.stderr or "")


# ---------- agent wiring ----------


SYSTEM_PROMPT = (
    "You are a system diagnostic assistant running on the user's "
    "machine. For each substantive question, gather multiple signals "
    "(CPU, memory, disk, network, logs) before drawing a conclusion. "
    "Plan the investigation: identify candidate tools, run them, then "
    "synthesise a concise root-cause hypothesis grounded in the "
    "captured numbers. Never use `run_shell` unless the user explicitly "
    "asks for it — prefer the structured tools."
)


def build_agent():
    """Lazy-import Strands so `import main` stays cheap."""
    from strands import Agent
    from strands.models.anthropic import AnthropicModel

    from safer.adapters.strands import SaferHookProvider

    model = AnthropicModel(
        model_id="claude-opus-4-7",
        max_tokens=1024,
        client_args={"api_key": os.environ["ANTHROPIC_API_KEY"]},
    )

    return Agent(
        model=model,
        tools=[
            list_processes,
            disk_usage,
            memory_pressure,
            network_listeners,
            top_files_by_size,
            uptime_info,
            read_log_tail,
            run_shell,
        ],
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
        hooks=[
            SaferHookProvider(
                agent_id="system_diag",
                agent_name="System Diagnostic (Strands)",
            )
        ],
    )


def _format_result(result) -> str:
    if hasattr(result, "message"):
        parts = []
        for block in result.message.get("content", []) or []:
            text = block.get("text") if isinstance(block, dict) else None
            if text:
                parts.append(text)
        return "\n".join(parts).strip() or str(result)
    return str(result)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--prompt",
        default=None,
        help="Run a single prompt and exit instead of opening the REPL.",
    )
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is required to run this example.")

    agent = build_agent()

    def ask(user_message: str) -> str:
        return _format_result(agent(user_message))

    if args.prompt:
        print(ask(args.prompt))
        return

    run_repl(
        ask,
        banner=(
            "SAFER system-diagnostic chat (Strands) — ask about CPU, "
            "memory, disk, network, or logs."
        ),
    )


if __name__ == "__main__":
    main()
