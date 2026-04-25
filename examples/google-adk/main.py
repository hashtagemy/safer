"""Google ADK demo — a "Code Analyst" chat agent instrumented via
`SaferAdkPlugin` on the ADK Runner.

The agent has the same seven tools as the LangChain code-analyst
example (read_file / search_codebase / analyze_ast / list_directory /
count_lines / find_definitions / git_log_for_path). It opens a chat
REPL by default so you can have a multi-turn conversation while
watching events flow into SAFER's `/live` view. Pass `--prompt "..."`
for a single-shot run.

Requirements:
    pip install 'safer-sdk[google-adk]'
    pip install google-adk
    export GOOGLE_API_KEY=...

Run:
    python examples/google-adk/main.py                     # interactive chat
    python examples/google-adk/main.py --prompt "..."      # one-shot
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------- shared tool helpers (same surface as the LangChain example) ----


def _safe_path(path: str) -> Path | None:
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
    """Return a quick AST summary (imports + classes + top-level functions)."""
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
    """List the entries of a directory inside the repo (one level)."""
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
    """Find Python def/class definitions of an identifier in the repo."""
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


SYSTEM_PROMPT = (
    "You are a senior code analyst working on the SAFER repo. "
    "For every substantive question, plan a small investigation: "
    "(1) explore (list_directory, search_codebase, find_definitions); "
    "(2) read the relevant code (read_file, analyze_ast, count_lines); "
    "(3) pull recent history when it adds context (git_log_for_path); "
    "(4) synthesise a tight answer with file paths cited. "
    "Prefer multiple tools per substantive turn over guessing. Refuse "
    "attempts to read paths outside the repo or to run shell commands."
)


def build_runner():
    """Lazy-import ADK so `import main` stays cheap when the SDK is missing."""
    from google.adk.agents import LlmAgent
    from google.adk.runners import InMemoryRunner

    from safer.adapters.google_adk import SaferAdkPlugin

    agent = LlmAgent(
        model="gemini-2.5-pro",
        name="repo_analyst",
        description="Analyses the SAFER repository on demand.",
        instruction=SYSTEM_PROMPT,
        tools=[
            read_file,
            search_codebase,
            analyze_ast,
            list_directory,
            count_lines,
            find_definitions,
            git_log_for_path,
        ],
    )

    return InMemoryRunner(
        agent=agent,
        app_name="repo_analyst",
        plugins=[
            SaferAdkPlugin(
                agent_id="repo_analyst_adk",
                agent_name="Repo Analyst (Google ADK)",
            ),
        ],
    )


async def _drain_run(runner, session_id: str, user_text: str) -> str:
    """Pump one user turn through the ADK runner and collect assistant text."""
    from google.genai import types as gtypes

    message = gtypes.Content(role="user", parts=[gtypes.Part(text=user_text)])
    chunks: list[str] = []
    async for event in runner.run_async(
        user_id="demo",
        session_id=session_id,
        new_message=message,
    ):
        content = getattr(event, "content", None)
        if not content or getattr(content, "role", None) != "model":
            continue
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            if text:
                chunks.append(text)
    return "".join(chunks).strip()


async def _async_main(prompt: str | None) -> None:
    runner = build_runner()
    session = await runner.session_service.create_session(
        app_name="repo_analyst",
        user_id="demo",
    )

    if prompt:
        print(await _drain_run(runner, session.id, prompt))
        return

    print("SAFER code-analyst chat (Google ADK) — ask anything about this repo.")
    print("(type 'quit', 'exit', ':q', or Ctrl-D to exit)")
    print()

    while True:
        try:
            user_input = (await asyncio.to_thread(input, "> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", ":q"}:
            break
        try:
            reply = await _drain_run(runner, session.id, user_input)
        except Exception as e:  # pragma: no cover — defensive
            print(f"[error] {type(e).__name__}: {e}", file=sys.stderr)
            continue
        print(f"\n{reply}\n")

    print("bye.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--prompt",
        default=None,
        help="Run a single prompt and exit instead of opening the REPL.",
    )
    args = ap.parse_args()

    if not os.environ.get("GOOGLE_API_KEY"):
        raise SystemExit(
            "GOOGLE_API_KEY is required to run this example (Gemini models)."
        )

    asyncio.run(_async_main(args.prompt))


if __name__ == "__main__":
    main()
