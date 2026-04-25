# coding-assistant — multi-agent chat demo (**hero example**)

A richer SAFER demo than the single-framework examples. Exercises
onboarding, runtime, and post-run lifecycle phases simultaneously,
under **two parallel sessions** (supervisor + worker) per user turn.

## What it does

A command-line chat. The **Supervisor** agent reads each user turn
and either answers directly or delegates to the **Worker** agent. The
Worker has nine tools — filesystem, web, shell, and git — and loops
over `messages.create` + tool results until it produces a final
answer.

Both agents instrument into SAFER under their own `agent_id`:

- `coding-supervisor` — 1 small Opus call per user turn.
- `coding-worker` — 3–15 Opus calls per delegated turn (tool loop).

Each user turn therefore produces **two parallel sessions** that show
up side-by-side on `/live`, one card per agent.

### Worker tools

| Tool | What it does |
|---|---|
| `read_file(path)` | First 8 KB of a UTF-8 text file |
| `write_file(path, content)` | Overwrite a file |
| `grep_code(pattern, path)` | Regex search over a file |
| `search_web(query)` | Mock web search (canned results) |
| `fetch_url(url)` | Real HTTP GET, first 4 KB |
| `run_shell(cmd)` | **Hazardous** — Gateway-policy showcase |
| `git_diff(path?)` | Up to 200 lines of `git diff` |
| `git_log(path?, limit=10)` | Recent commits (one-line) |
| `find_test_files(query, root="tests")` | Locate tests touching a query |

The worker's system prompt nudges it to start with `git_log` or
`git_diff` for context, then drill in with `read_file` / `grep_code`
/ `find_test_files`.

## Run it

```bash
# backend + dashboard up in another terminal:
docker compose up

# then, from the repo root:
export ANTHROPIC_API_KEY=sk-ant-...
uv run python examples/coding_assistant/main.py
```

Type a prompt, press enter. `clear` wipes memory; `quit` / Ctrl-D
exits.

### Prompts to try

| Prompt | What it exercises |
|---|---|
| `hi, what can you do?` | Supervisor direct-answer path, no worker session |
| `what changed in the last 5 commits?` | Worker → `git_log`, `git_diff` |
| `grep for 'shell=True' in examples/coding_assistant` | Worker → `grep_code` |
| `which tests cover the LangChain adapter?` | Worker → `find_test_files`, `read_file` |
| `read examples/coding_assistant/config.py and tell me what risks it has` | Worker → `read_file`, Judge may flag credential |
| `create a file notes.txt with today's date` | Worker → `write_file` |
| `run 'ls -la'` | Worker → `run_shell`, Gateway / Judge may intervene |

## What SAFER features to watch

### On `/agents`

Two cards appear (`coding-supervisor`, `coding-worker`) with their
own code snapshots. Open either one and hit **Scan codebase** — the
Inspector's deterministic rules should flag **at least three**
planted issues:

- `hardcoded_credential` — the fake `sk-ant-…` in `config.py`
- `shell_injection` — `subprocess.run(cmd, shell=True)` in `tools/shell.py`
- `ssl_verify_disabled` — `requests.get(url, verify=False)` in `tools/web.py`
- `plaintext_http_url` — the `http://internal-search.example/…` URL in `tools/web.py`

The 3-persona Inspector review (if `ANTHROPIC_API_KEY` is set) will
usually add recommendations on top.

### On `/live`

During a delegated turn you will see two active session cards stacked
vertically — the supervisor card (2–4 events) on top, then the worker
card (tool-heavy) that keeps advancing as tools fire. Click a card to
drill into the event stream for just that session.

### On `/sessions`

Once turns finish, the sessions persist there. Each turn contributes
two rows (supervisor + worker). Click one to see its Session Report
(Quality + thought-chain narrative + timeline) once the post-run
aggregator runs.

## Scan scope

Because this directory has its own `pyproject.toml`, SAFER picks it
as the workspace root. The default `instrument()` import-graph walk
stays inside this folder, so the Inspector scans ~11 files (the
demo's own code) rather than the surrounding SAFER repo. To change
behaviour:

```python
instrument(
    agent_id="coding-worker",
    # scan_mode="directory",       # recursive .py walk instead of import graph
    # include=["prompts/*.md"],    # pull in extra files (non-py ok)
    # exclude=["experiments/**"],  # drop paths
)
```

## Intentional security issues

The tools module deliberately contains patterns a production agent
should never have. They exist so the Inspector and runtime Judge have
something concrete to catch. Do not copy these into real agents:

- `tools/shell.py` — `subprocess.run(..., shell=True)` on a
  user-supplied string
- `tools/web.py` — `requests.get(url, verify=False)` and a plaintext
  `http://` search endpoint
- `config.py` — a fake hardcoded `ANTHROPIC_FAKE_KEY_PLACEHOLDER`
