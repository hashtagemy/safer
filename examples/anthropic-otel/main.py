"""Anthropic + SAFER OTel bridge demo.

Raw Anthropic SDK code instrumented with SAFER through the OpenTelemetry
bridge — no `wrap_anthropic`, no manual hook helpers, just one
`configure_otel_bridge(...)` call.

This example is a small "research assistant" agent with six tools
(`web_search`, `fetch_url`, `extract_links`, `save_note`, `read_note`,
`list_notes`). Notes are persisted to `examples/anthropic-otel/.notes/`
so they survive across REPL turns.

Default mode is an interactive chat REPL — every turn pushes a full
agent loop into SAFER's `/live` view via the OTel bridge. Pass
`--prompt "..."` to run one shot and exit.

Requirements:
    pip install 'safer-sdk[otel-anthropic]'
    export ANTHROPIC_API_KEY=...

Run:
    python examples/anthropic-otel/main.py
    python examples/anthropic-otel/main.py --prompt "..."
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

import httpx

# Allow `from _chat import run_repl` even though we run this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _chat import run_repl  # noqa: E402

logging.basicConfig(level=logging.INFO)

NOTES_DIR = Path(__file__).resolve().parent / ".notes"


# ---------- tools ----------


def _slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return base[:60] or "untitled"


def web_search(query: str, max_results: int = 5) -> str:
    """Search Wikipedia (opensearch API) and return title + URL pairs."""
    if not query.strip():
        return "refused: empty query"
    try:
        resp = httpx.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "opensearch",
                "search": query,
                "limit": max(1, min(max_results, 10)),
                "namespace": 0,
                "format": "json",
            },
            timeout=10.0,
            headers={"User-Agent": "safer-research-demo/1.0"},
        )
    except httpx.RequestError as e:
        return f"network error: {e}"
    if resp.status_code >= 400:
        return f"HTTP {resp.status_code}"
    try:
        _, titles, snippets, urls = resp.json()
    except (ValueError, KeyError):
        return "(unexpected response shape)"
    if not titles:
        return "(no results)"
    rows = [
        f"- {title} — {snippet or '(no snippet)'} → {url}"
        for title, snippet, url in zip(titles, snippets, urls)
    ]
    return "\n".join(rows)


def fetch_url(url: str) -> str:
    """Fetch a URL and return up to the first 2000 chars of the body."""
    if not re.match(r"^https?://", url):
        return "refused: only http/https URLs allowed"
    try:
        resp = httpx.get(
            url,
            timeout=10.0,
            follow_redirects=True,
            headers={"User-Agent": "safer-research-demo/1.0"},
        )
    except httpx.RequestError as e:
        return f"network error: {e}"
    if resp.status_code >= 400:
        return f"HTTP {resp.status_code}"
    return resp.text[:2000] or "(empty body)"


def extract_links(url: str, limit: int = 20) -> str:
    """Fetch a URL and return up to `limit` outbound `<a href>` links."""
    if not re.match(r"^https?://", url):
        return "refused: only http/https URLs allowed"
    try:
        resp = httpx.get(
            url,
            timeout=10.0,
            follow_redirects=True,
            headers={"User-Agent": "safer-research-demo/1.0"},
        )
    except httpx.RequestError as e:
        return f"network error: {e}"
    if resp.status_code >= 400:
        return f"HTTP {resp.status_code}"
    hrefs = re.findall(r'<a[^>]+href="([^"#]+)"', resp.text, flags=re.IGNORECASE)
    seen: list[str] = []
    for href in hrefs:
        if href.startswith("javascript:"):
            continue
        if href not in seen:
            seen.append(href)
        if len(seen) >= max(1, min(limit, 50)):
            break
    return "\n".join(seen) or "(no links found)"


def save_note(title: str, body: str) -> str:
    """Persist a markdown note to disk so it survives across REPL turns."""
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(title)
    target = NOTES_DIR / f"{slug}.md"
    target.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    return f"saved: {target.relative_to(NOTES_DIR.parent)}"


def read_note(title: str) -> str:
    """Read a previously saved note by title (slugified)."""
    target = NOTES_DIR / f"{_slug(title)}.md"
    if not target.exists():
        return f"not found: {title}"
    return target.read_text(encoding="utf-8")[:4000]


def list_notes() -> str:
    """List the titles of every saved note."""
    if not NOTES_DIR.exists():
        return "(no notes yet)"
    titles: list[str] = []
    for path in sorted(NOTES_DIR.glob("*.md")):
        try:
            first_line = path.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError):
            first_line = path.stem
        titles.append(first_line.lstrip("# ").strip() or path.stem)
    return "\n".join(f"- {t}" for t in titles) or "(no notes yet)"


TOOL_FUNCS = {
    "web_search": web_search,
    "fetch_url": fetch_url,
    "extract_links": extract_links,
    "save_note": save_note,
    "read_note": read_note,
    "list_notes": list_notes,
}

TOOL_SPECS = [
    {
        "name": "web_search",
        "description": (
            "Search Wikipedia for a query and return up to N "
            "(title, snippet, URL) results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "HTTP GET a URL and return the first 2000 chars of the body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "extract_links",
        "description": "Fetch a URL and list its outbound <a href> links.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["url"],
        },
    },
    {
        "name": "save_note",
        "description": "Save a markdown note (persisted across turns).",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["title", "body"],
        },
    },
    {
        "name": "read_note",
        "description": "Read a previously saved note by title.",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    },
    {
        "name": "list_notes",
        "description": "List the titles of every saved note.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


SYSTEM_PROMPT = (
    "You are a research assistant. For each user request, plan a small "
    "investigation: search the web, read primary sources, extract links "
    "to drill into, and save notes for findings the user might want "
    "later. Use multiple tools per substantive turn. When you have an "
    "answer, cite the URLs you read. Refuse to fetch URLs the user did "
    "not ask for or imply."
)


def _run_tool(name: str, raw_input: dict) -> str:
    fn = TOOL_FUNCS.get(name)
    if fn is None:
        return f"unknown tool: {name}"
    try:
        return fn(**raw_input)
    except TypeError as e:
        return f"bad arguments for {name}: {e}"


def _agent_turn(client, messages: list[dict], user_text: str) -> str:
    """Drive one user turn through the Anthropic loop."""
    messages.append({"role": "user", "content": user_text})

    while True:
        resp = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOL_SPECS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            text_parts = [b.text for b in resp.content if b.type == "text"]
            return "\n".join(text_parts).strip() or "(no text reply)"

        tool_results: list[dict] = []
        for tu in tool_uses:
            args = dict(tu.input or {})
            result = _run_tool(tu.name, args)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": str(result),
                }
            )
        messages.append({"role": "user", "content": tool_results})


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

    # --- SAFER integration: two lines ---
    from safer.adapters.otel import configure_otel_bridge

    configure_otel_bridge(
        agent_id="anthropic_otel_demo",
        agent_name="Anthropic OTel Research Assistant",
        instrument=["anthropic"],
    )
    # -------------------------------------

    from anthropic import Anthropic

    client = Anthropic()
    messages: list[dict] = []

    def ask(user_text: str) -> str:
        return _agent_turn(client, messages, user_text)

    if args.prompt:
        print(ask(args.prompt))
        return

    run_repl(
        ask,
        banner=(
            "SAFER research assistant chat (Anthropic via OTel bridge) — "
            "ask the agent to research a topic and take notes."
        ),
        on_clear=lambda: messages.clear(),
    )


if __name__ == "__main__":
    main()
