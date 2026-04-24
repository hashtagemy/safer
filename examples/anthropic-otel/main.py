"""Anthropic + SAFER OTel bridge demo.

Raw Anthropic SDK code instrumented with SAFER through the OTel
bridge. No wrap_anthropic call, no manual hook helpers — just
`configure_otel_bridge(...)` once, then every `messages.create` call
is observed by SAFER via the GenAI span pipeline.

Requirements:
    pip install 'safer-sdk[otel-anthropic]'
    export ANTHROPIC_API_KEY=...

Run:
    python examples/anthropic-otel/main.py
"""

from __future__ import annotations

import argparse
import logging
import os

logging.basicConfig(level=logging.INFO)


TOOL_SPEC = {
    "name": "read_tech_news",
    "description": (
        "Return the first line of a text file so the model can quote it "
        "back. The file path is a repo-relative string."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
}


def read_tech_news(path: str) -> str:
    """Read the first line of a real repo-local file."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    target = (repo_root / path).resolve()
    if not str(target).startswith(str(repo_root)):
        return "refused: path escapes repo"
    if not target.is_file():
        return f"not found: {path}"
    return target.read_text(errors="replace").splitlines()[0][:500]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--prompt",
        default=(
            "Use the read_tech_news tool to fetch the first line of "
            "`README.md`, then tell me what project this is."
        ),
    )
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is required to run this example.")

    # --- SAFER integration: two lines ---
    from safer.adapters.otel import configure_otel_bridge

    configure_otel_bridge(
        agent_id="anthropic_otel_demo",
        agent_name="Anthropic OTel Demo",
        instrument=["anthropic"],
    )
    # -------------------------------------

    from anthropic import Anthropic

    client = Anthropic()
    messages = [{"role": "user", "content": args.prompt}]

    while True:
        resp = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=1024,
            tools=[TOOL_SPEC],
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            # final text response
            for b in resp.content:
                if b.type == "text":
                    print(b.text)
            return

        # Execute every tool and feed the results back
        tool_results = []
        for tu in tool_uses:
            if tu.name == "read_tech_news":
                result = read_tech_news(tu.input["path"])
            else:
                result = f"unknown tool: {tu.name}"
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result,
                }
            )
        messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    main()
