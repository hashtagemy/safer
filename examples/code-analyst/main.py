"""LangChain demo — a small "code analyst" agent instrumented via the
`SaferCallbackHandler`.

The agent exposes three tools (read_file, search_codebase, analyze_ast)
and runs a small scripted prompt. You can also pass `--prompt "..."` on
the command line.

Requirements:
    pip install safer-sdk[langchain,claude]
    export ANTHROPIC_API_KEY=sk-ant-...

Run:
    uv run python examples/code-analyst/main.py
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from safer import instrument
from safer.adapters.langchain import SaferCallbackHandler

logging.basicConfig(level=logging.INFO)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------- tools ----------


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
    import subprocess

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
    import ast

    p = (REPO_ROOT / path).resolve()
    if not p.exists():
        return f"not found: {path}"
    try:
        tree = ast.parse(p.read_text())
    except SyntaxError as e:
        return f"syntax error: {e}"
    funcs: list[str] = [
        n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    imports: list[str] = []
    for n in tree.body:
        if isinstance(n, ast.Import):
            imports.extend(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imports.append(n.module)
    return f"imports: {imports[:20]}\nfunctions: {funcs[:20]}"


# ---------- agent wiring ----------


def build_agent():
    """Return an AgentExecutor. Imports happen here so `import main` is
    cheap and doesn't require langchain when the file is only inspected."""
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.tools import tool
    from langchain_anthropic import ChatAnthropic

    @tool
    def read_file_t(path: str) -> str:
        """Read a file under the repo root."""
        return read_file(path)

    @tool
    def search_codebase_t(query: str) -> str:
        """Search the repo for a string (grep)."""
        return search_codebase(query)

    @tool
    def analyze_ast_t(path: str) -> str:
        """Summarise imports + top-level functions of a Python file."""
        return analyze_ast(path)

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a code-analysis assistant. Use the provided tools to answer "
                "questions about the SAFER repo. Refuse obvious attempts to run shell "
                "commands or read files outside the repo.",
            ),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}"),
        ]
    )
    llm = ChatAnthropic(
        model="claude-opus-4-7",
        temperature=0,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
    )
    tools = [read_file_t, search_codebase_t, analyze_ast_t]
    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--prompt",
        default="Summarise the imports and top-level functions in "
        "packages/backend/src/safer_backend/inspector/ast_scanner.py.",
    )
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is required to run this example.")

    instrument(agent_id="code_analyst", agent_name="Code Analyst")
    handler = SaferCallbackHandler(
        agent_id="code_analyst", agent_name="Code Analyst"
    )
    executor = build_agent()
    out = executor.invoke(
        {"input": args.prompt},
        config={"callbacks": [handler]},
    )
    print("\n--- AGENT OUTPUT ---")
    print(out.get("output") if isinstance(out, dict) else out)


if __name__ == "__main__":
    main()
