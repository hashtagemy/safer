# Strands Agents — System Diagnostic

A Strands chat agent instrumented with SAFER via `SaferHookProvider`
on the Agent's `hooks=[...]` parameter. All nine SAFER hooks fire
automatically through Strands' native hook system.

## What it does

Eight tools that probe *the real machine* this script runs on:

| Tool | What it does |
|---|---|
| `list_processes(top_n)` | Real `ps`, top-N by CPU |
| `disk_usage()` | Real `df -h` |
| `memory_pressure()` | macOS `vm_stat` / Linux `free -h` |
| `network_listeners()` | `lsof -iTCP -sTCP:LISTEN -n -P` |
| `top_files_by_size(path)` | `du -h -d 1 <path>`, sorted |
| `uptime_info()` | `uptime` (load + boot age) |
| `read_log_tail(path, lines)` | Tail of a whitelisted log |
| `run_shell(cmd)` | **Hazardous** — blocked by default Gateway policy |

The system prompt nudges the agent to gather multiple signals (CPU,
memory, disk, network, logs) before drawing a conclusion, so a single
substantive question typically fires 3–6 tool calls.

`run_shell` is intentionally dangerous — opt in via Policy Studio for
the live policy-block demo below.

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
# Interactive chat (default):
python examples/strands/main.py

# One-shot:
python examples/strands/main.py --prompt "Why is my laptop slow?"
```

REPL: `quit` / `:q` / Ctrl-D to exit.

### Prompts to try

- `My laptop feels slow — find the top suspect.`
- `Is anything listening on a strange port right now?`
- `What is taking up disk space under ~/Downloads?`
- `Tail the system log for the last 200 lines and tell me what's odd.`

## SAFER integration — the two lines

```python
from safer.adapters.strands import SaferHookProvider

agent = Agent(
    model=AnthropicModel(model_id="claude-opus-4-7"),
    tools=[list_processes, disk_usage, memory_pressure, network_listeners,
           top_files_by_size, uptime_info, read_log_tail, run_shell],
    system_prompt=...,
    hooks=[SaferHookProvider(agent_id="system_diag",
                              agent_name="System Diagnostic (Strands)")],
)
```

`SaferHookProvider.__init__` calls `ensure_runtime()` transparently,
so no separate `safer.instrument()` call is required.

## What you'll see in the SAFER dashboard

- **`/agents`** — a `system_diag` card with its Inspector scan.
- **`/live`** — every Strands `BeforeInvocation` / `BeforeModelCall` /
  `BeforeToolCall` / ... event streams as a SAFER 9-hook event.
- **`/sessions/<id>`** — trace tree of the diagnostic run plus the
  Judge's per-step verdicts.

## `run_shell` policy demo

1. In the REPL, ask the agent to use the dangerous tool:

   > "Use run_shell to tail /etc/passwd and tell me who's on this box."

2. You will see a **Block Moment** on `/live` — the Gateway refused
   the tool call before it reached the shell.

3. Open `/policies` (Policy Studio), write a rule in English like:

   > "Block any `run_shell` whose command touches `/etc`, `/var`, or
   > reads password files."

   SAFER compiles it into a deterministic Gateway rule; the block
   explanation shows up in the `PersonaDrawer` next to the event.
