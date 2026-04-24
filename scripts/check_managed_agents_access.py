"""Ping Anthropic's Managed Agents API to verify beta access.

Run:
    uv run python scripts/check_managed_agents_access.py

Exit code 0 = access confirmed. Non-zero = problem (no API key, no beta
access, network error). Does NOT create any persistent resources; a
best-effort list call is enough to prove the endpoint is reachable.
"""

from __future__ import annotations

import os
import sys


BETA_HEADER = "managed-agents-2026-04-01"


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 2

    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic SDK not installed. Run `uv sync`.", file=sys.stderr)
        return 3

    client = anthropic.Anthropic(default_headers={"anthropic-beta": BETA_HEADER})

    try:
        agents = client.beta.agents.list(limit=1)
    except anthropic.APIStatusError as e:
        status = getattr(e, "status_code", None)
        body = getattr(e, "message", str(e))
        print(
            f"ERROR: Managed Agents API returned {status}: {body}",
            file=sys.stderr,
        )
        if status == 403:
            print(
                "Hint: your API key may not have Managed Agents beta access. "
                "Check https://platform.claude.com.",
                file=sys.stderr,
            )
        return 4
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 5

    count = len(getattr(agents, "data", []) or [])
    print(
        f"OK: Managed Agents beta reachable. "
        f"Existing agents visible to this key: {count}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
