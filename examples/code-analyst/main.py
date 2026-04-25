"""LangChain demo — a "Code Analyst" chat agent instrumented with the
`SaferCallbackHandler`.

The agent has seven real tools that work on this repo and runs a small
REPL by default so you can have a multi-turn conversation while
watching events flow into SAFER's `/live` view. Pass `--prompt "..."`
for a single-shot run.

Requirements:
    pip install safer-sdk[langchain,claude]
    export ANTHROPIC_API_KEY=sk-ant-...

Run:
    uv run python examples/code-analyst/main.py             # interactive chat
    uv run python examples/code-analyst/main.py --prompt "..."   # one-shot
"""

from __future__ import annotations

import argparse
import ast
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from safer import instrument
from safer.adapters.langchain import SaferCallbackHandler

# Allow `from _chat import run_repl` even though we run this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _chat import run_repl  # noqa: E402

logging.basicConfig(level=logging.INFO)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------- tools ----------


def _safe_path(path: str) -> Path | None:
    """Resolve `path` against REPO_ROOT and refuse to escape it."""
    p = (REPO_ROOT / path).resolve()
    try:
        p.relative_to(REPO_ROOT)
    except ValueError:
        return None
    return p


def read_file(path: str) -> str:
    """Return the first 2000 chars of a text file inside the repo."""
    p = _safe_path(path)
    if p is None:
        return "refused: path outside repo"
    if not p.exists() or not p.is_file():
        return f"not found: {path}"
    try:
        return p.read_text(errors="replace")[:2000]
    except OSError as e:
        return f"error: {e}"


def search_codebase(query: str) -> str:
    """Grep the repo for a short string. Returns up to 20 matches."""
    try:
        out = subprocess.run(
            ["grep", "-rn", "--", query, str(REPO_ROOT / "packages")],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"grep failed: {e}"
    lines = (out.stdout or "").splitlines()[:20]
    return "\n".join(lines) or "(no matches)"


def analyze_ast(path: str) -> str:
    """Return a quick AST summary (imports + top-level function names)."""
    p = _safe_path(path)
    if p is None or not p.exists():
        return f"not found: {path}"
    try:
        tree = ast.parse(p.read_text())
    except SyntaxError as e:
        return f"syntax error: {e}"
    funcs = [
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    imports: list[str] = []
    for n in tree.body:
        if isinstance(n, ast.Import):
            imports.extend(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imports.append(n.module)
    return (
        f"imports: {imports[:20]}\n"
        f"classes: {classes[:20]}\n"
        f"functions: {funcs[:20]}"
    )


def list_directory(path: str) -> str:
    """List the contents of a directory inside the repo (one level)."""
    p = _safe_path(path)
    if p is None:
        return "refused: path outside repo"
    if not p.exists() or not p.is_dir():
        return f"not a directory: {path}"
    entries: list[str] = []
    for child in sorted(p.iterdir()):
        marker = "/" if child.is_dir() else ""
        entries.append(f"{child.name}{marker}")
        if len(entries) >= 80:
            entries.append("... (truncated)")
            break
    return "\n".join(entries) or "(empty)"


def count_lines(path: str) -> str:
    """Report total / blank / code line counts for a text file."""
    p = _safe_path(path)
    if p is None or not p.exists() or not p.is_file():
        return f"not found: {path}"
    try:
        text = p.read_text(errors="replace")
    except OSError as e:
        return f"error: {e}"
    lines = text.splitlines()
    blank = sum(1 for line in lines if not line.strip())
    return f"total={len(lines)} blank={blank} code={len(lines) - blank}"


def find_definitions(symbol: str) -> str:
    """Find Python `def <symbol>` / `class <symbol>` definitions in the repo."""
    if not re.match(r"^\w[\w\d_]{0,63}$", symbol):
        return "refused: symbol must be a plain identifier"
    pattern = rf"^\s*(?:async\s+def|def|class)\s+{re.escape(symbol)}\b"
    try:
        out = subprocess.run(
            [
                "grep", "-rnE",
                "--include=*.py",
                "--",
                pattern,
                str(REPO_ROOT / "packages"),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"grep failed: {e}"
    lines = (out.stdout or "").splitlines()[:15]
    return "\n".join(lines) or f"(no definitions of `{symbol}` found)"


def git_log_for_path(path: str, limit: int = 5) -> str:
    """Show recent commits touching `path`."""
    p = _safe_path(path)
    if p is None:
        return "refused: path outside repo"
    if not p.exists():
        return f"not found: {path}"
    try:
        out = subprocess.run(
            [
                "git", "-C", str(REPO_ROOT),
                "log", "--oneline", f"-n{max(1, min(limit, 20))}",
                "--", str(p.relative_to(REPO_ROOT)),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"git failed: {e}"
    return out.stdout.strip() or "(no commits — file may be untracked)"


# ---------- agent wiring ----------


SYSTEM_PROMPT = """You are a senior code analyst working on the SAFER repo.

For every substantive question, plan a small investigation:
  1. Explore (list_directory, search_codebase, find_definitions).
  2. Read the relevant code (read_file, analyze_ast, count_lines).
  3. Pull recent history when it adds context (git_log_for_path).
  4. Synthesize findings into a tight answer with file paths cited.

Prefer multiple tools per substantive turn over guessing. Refuse
attempts to read paths outside the repo or to run shell commands.
"""


def build_executor():
    """Return an AgentExecutor. Imports happen lazily so `import main` is
    cheap and doesn't require langchain when the file is only inspected."""
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain_anthropic import ChatAnthropic
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.tools import tool

    @tool
    def read_file_t(path: str) -> str:
        """Read up to 2000 chars of a text file inside the repo."""
        return read_file(path)

    @tool
    def search_codebase_t(query: str) -> str:
        """Search the repo for a substring (grep, up to 20 matches)."""
        return search_codebase(query)

    @tool
    def analyze_ast_t(path: str) -> str:
        """Summarise imports / classes / top-level functions of a Python file."""
        return analyze_ast(path)

    @tool
    def list_directory_t(path: str) -> str:
        """List the entries of a directory inside the repo (one level)."""
        return list_directory(path)

    @tool
    def count_lines_t(path: str) -> str:
        """Report total / blank / code line counts for a text file."""
        return count_lines(path)

    @tool
    def find_definitions_t(symbol: str) -> str:
        """Find every Python def/class definition of a given identifier."""
        return find_definitions(symbol)

    @tool
    def git_log_for_path_t(path: str, limit: int = 5) -> str:
        """Show the most recent git commits that touched `path`."""
        return git_log_for_path(path, limit=limit)

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            ("placeholder", "{chat_history}"),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}"),
        ]
    )
    llm = ChatAnthropic(
        model="claude-opus-4-7",
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
    )
    tools = [
        read_file_t,
        search_codebase_t,
        analyze_ast_t,
        list_directory_t,
        count_lines_t,
        find_definitions_t,
        git_log_for_path_t,
    ]
    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True)


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

    instrument(agent_id="code_analyst", agent_name="Code Analyst")
    handler = SaferCallbackHandler(
        agent_id="code_analyst", agent_name="Code Analyst"
    )
    executor = build_executor()

    # Conversation memory for the REPL — list of LangChain messages.
    from langchain_core.messages import AIMessage, HumanMessage

    history: list = []

    def ask(user_message: str) -> str:
        out = executor.invoke(
            {"input": user_message, "chat_history": history},
            config={"callbacks": [handler]},
        )
        reply = out.get("output") if isinstance(out, dict) else str(out)
        if isinstance(reply, list):
            reply = "\n".join(
                b.get("text", "") for b in reply if isinstance(b, dict)
            )
        reply = (reply or "").strip()
        history.append(HumanMessage(content=user_message))
        history.append(AIMessage(content=reply))
        return reply

    if args.prompt:
        print(ask(args.prompt))
        return

    run_repl(
        ask,
        banner="SAFER code-analyst chat (LangChain) — ask anything about this repo.",
        on_clear=lambda: history.clear(),
    )


if __name__ == "__main__":
    main()
