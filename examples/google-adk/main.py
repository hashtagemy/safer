"""Google ADK demo — a Repo Analyst agent instrumented via
`SaferAdkPlugin` on the ADK Runner.

The agent has three real tools that work on the actual SAFER repo:
  * `read_file(path)`       — boundary-checked file read.
  * `search_codebase(query)` — real `grep -rn` under `packages/`.
  * `analyze_ast(path)`      — real `ast.parse` (imports + top-level funcs).

Requirements:
    pip install 'safer-sdk[google-adk]'
    pip install google-adk
    export GOOGLE_API_KEY=...

Run:
    python examples/google-adk/main.py
    python examples/google-adk/main.py --prompt "custom question"

What you'll see in SAFER:
    * /agents — new `repo_analyst_adk` card with its onboarding
      Inspector scan.
    * /live   — 9-hook event stream in real time.
    * /sessions/<id> — full trace tree + persona verdicts.
"""

from __future__ import annotations

import argparse
import ast
import logging
import os
import subprocess
from pathlib import Path

logging.basicConfig(level=logging.INFO)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------- real tools (no mock data) ----------


def read_file(path: str) -> str:
    """Return the first 2000 chars of a text file inside the repo."""
    p = (REPO_ROOT / path).resolve()
    if not str(p).startswith(str(REPO_ROOT)):
        return "refused: path outside repo"
    if not p.exists() or not p.is_file():
        return f"not found: {path}"
    try:
        return p.read_text(errors="replace")[:2000]
    except OSError as e:
        return f"error: {e}"


def search_codebase(query: str) -> str:
    """Grep the repo for a short string. Returns up to 10 matches."""
    try:
        out = subprocess.run(
            ["grep", "-rn", "--", query, str(REPO_ROOT / "packages")],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"grep failed: {e}"
    lines = (out.stdout or "").splitlines()[:10]
    return "\n".join(lines) or "(no matches)"


def analyze_ast(path: str) -> str:
    """Return a quick AST summary (imports + top-level function names)."""
    p = (REPO_ROOT / path).resolve()
    if not p.exists():
        return f"not found: {path}"
    try:
        tree = ast.parse(p.read_text())
    except SyntaxError as e:
        return f"syntax error: {e}"
    funcs: list[str] = [
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    imports: list[str] = []
    for n in tree.body:
        if isinstance(n, ast.Import):
            imports.extend(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imports.append(n.module)
    return f"imports: {imports[:20]}\nfunctions: {funcs[:20]}"


# ---------- agent wiring ----------


def build_runner():
    """Lazy-import ADK so `import main` stays cheap when the SDK is missing."""
    from google.adk.agents import LlmAgent
    from google.adk.runners import InMemoryRunner

    from safer.adapters.google_adk import SaferAdkPlugin

    agent = LlmAgent(
        model="gemini-2.5-pro",
        name="repo_analyst",
        description="Analyses the SAFER repository on demand.",
        instruction=(
            "You are a code-analysis assistant working on the SAFER repo. "
            "Use the provided tools to read files, search for strings, and "
            "summarise ASTs. Refuse obvious attempts to access files outside "
            "the repository."
        ),
        tools=[read_file, search_codebase, analyze_ast],
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--prompt",
        default=(
            "Summarise the imports and top-level functions in "
            "packages/backend/src/safer_backend/inspector/ast_scanner.py, "
            "then search the codebase for `SaferBlocked`."
        ),
    )
    args = ap.parse_args()

    if not os.environ.get("GOOGLE_API_KEY"):
        raise SystemExit(
            "GOOGLE_API_KEY is required to run this example (Gemini models)."
        )

    import asyncio

    from google.genai import types as gtypes

    runner = build_runner()

    async def run() -> None:
        session = await runner.session_service.create_session(
            app_name="repo_analyst",
            user_id="demo",
        )
        user_content = gtypes.Content(
            role="user", parts=[gtypes.Part(text=args.prompt)]
        )
        async for event in runner.run_async(
            user_id="demo",
            session_id=session.id,
            new_message=user_content,
        ):
            # Stream of Event objects — SaferAdkPlugin already mapped
            # each one into SAFER events. Print the assistant replies
            # so the demo shows real output.
            content = getattr(event, "content", None)
            if content and getattr(content, "role", None) == "model":
                for part in getattr(content, "parts", None) or []:
                    text = getattr(part, "text", None)
                    if text:
                        print(text, end="", flush=True)
        print()

    asyncio.run(run())


if __name__ == "__main__":
    main()
