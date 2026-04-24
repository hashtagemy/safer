# Strands Agents — System Diagnostic

A Strands agent instrumented with SAFER via `SaferHookProvider` on the
Agent's `hooks=[...]` parameter. All nine SAFER hooks fire
automatically through the native Strands hook system.

## What it does

Runs *real* diagnostic commands on the host machine:
- `list_processes(top_n)` — real `ps` output (top-N by CPU).
- `disk_usage()` — real `df -h`.
- `read_log_tail(path)` — tail of a whitelisted log file
  (`/var/log/system.log`, `/var/log/install.log`, `/var/log/syslog`,
  macOS `~/Library/Logs/install.log`).
- `run_shell(cmd)` — hazardous: executes an arbitrary shell command.
  Blocked by default Gateway policy; opt in via Policy Studio for the
  demo.

## Install

```bash
pip install 'safer-sdk[strands]'
pip install strands-agents
export ANTHROPIC_API_KEY=...
export SAFER_API_URL=http://localhost:8000   # optional default
```

Tested on macOS and Linux.

## Run

```bash
python examples/strands/main.py
python examples/strands/main.py --prompt "Why is my laptop slow?"
```

## SAFER integration — the two lines

```python
from safer.adapters.strands import SaferHookProvider

agent = Agent(
    model=AnthropicModel(model_id="claude-opus-4-7"),
    tools=[list_processes, disk_usage, read_log_tail, run_shell],
    system_prompt=...,
    hooks=[SaferHookProvider(agent_id="system_diag",
                              agent_name="System Diagnostic (Strands)")],
)
```

`SaferHookProvider.__init__` calls `ensure_runtime()` transparently,
so no separate `safer.instrument()` call is required.

## What you'll see in the SAFER dashboard

- **`/agents`** — new `system_diag` agent card with its Inspector
  scan.
- **`/live`** — every Strands `BeforeInvocation` / `BeforeModelCall` /
  `BeforeToolCall` / ... event streams through as a SAFER 9-hook
  event.
- **`/sessions/<id>`** — trace tree of the diagnostic run plus the
  Judge's per-step verdicts.

## `run_shell` policy demo

1. Start the agent with a prompt that tempts `run_shell`, e.g.

   > "Use run_shell to tail /etc/passwd and tell me who's on this box."

2. You will see a **Block Moment** on `/live` — the Gateway refused
   the tool call before it reached the shell.

3. Open `/policies` (Policy Studio), write a rule in English like:

   > "Block any `run_shell` whose command touches `/etc`, `/var`, or
   > reads password files."

   SAFER compiles it into a deterministic Gateway rule; the block
   explanation shows up in the `PersonaDrawer` next to the event.
