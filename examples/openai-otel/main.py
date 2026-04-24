"""OpenAI + SAFER OTel bridge demo.

Raw OpenAI Python SDK code instrumented with SAFER through the OTel
bridge. No `wrap_openai`, no manual helpers — just one
`configure_otel_bridge(...)` call.

Requirements:
    pip install 'safer-sdk[otel-openai]'
    export OPENAI_API_KEY=...

Run:
    python examples/openai-otel/main.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os

logging.basicConfig(level=logging.INFO)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "summarize_url",
            "description": (
                "Return the first 500 chars of a URL's body. Uses a "
                "real HTTP GET — external network required."
            ),
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    }
]


def summarize_url(url: str) -> str:
    """Real HTTP GET — no fake payload."""
    import httpx

    try:
        resp = httpx.get(url, timeout=5.0, follow_redirects=True)
    except httpx.RequestError as e:
        return f"network error: {e}"
    if resp.status_code >= 400:
        return f"HTTP {resp.status_code}"
    body = resp.text[:500]
    return body


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--prompt",
        default=(
            "Use the summarize_url tool on https://example.com and tell "
            "me in one sentence what the page says."
        ),
    )
    args = ap.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required to run this example.")

    # --- SAFER integration: two lines ---
    from safer.adapters.otel import configure_otel_bridge

    configure_otel_bridge(
        agent_id="openai_otel_demo",
        agent_name="OpenAI OTel Demo",
        instrument=["openai"],
    )
    # -------------------------------------

    from openai import OpenAI

    client = OpenAI()
    messages = [{"role": "user", "content": args.prompt}]

    while True:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=TOOLS,
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_unset=True))

        if not msg.tool_calls:
            print(msg.content)
            return

        for call in msg.tool_calls:
            if call.function.name == "summarize_url":
                args_ = json.loads(call.function.arguments)
                result = summarize_url(args_.get("url", ""))
            else:
                result = f"unknown tool: {call.function.name}"
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                }
            )


if __name__ == "__main__":
    main()
