"""Static guarantee that every tool-emitting adapter wires
`check_or_raise` into its `before_tool_use` path.

The adapter integrations are different shapes (Strands has
`BeforeToolCallEvent.cancel_tool`, ADK returns a dict from
`before_tool_callback`, LangChain raises out of `on_tool_start`,
etc.) but they all share one contract: the adapter MUST call
`check_or_raise` at the same call site where it emits the
`BeforeToolUsePayload`. If a future adapter (or a refactor) emits
the event but skips the gateway check, a Sonnet-compiled
"Block any X" rule would silently fail to stop the tool.

This test is a tripwire — when it fails, the new adapter is missing
the gateway-check call. Add the call, then the test passes again.
"""

from __future__ import annotations

import importlib
import inspect

# Every adapter that exposes a tool-execution surface. Stub adapters
# (e.g. anything that only logs without driving a tool dispatch loop)
# wouldn't appear here.
TOOL_ADAPTERS = (
    "strands",
    "google_adk",
    "langchain",
    "openai_agents",
    "claude_sdk",
    "openai_client",
    "bedrock",
    "crewai",
)


def test_every_tool_adapter_calls_check_or_raise() -> None:
    missing: list[str] = []
    for adapter_name in TOOL_ADAPTERS:
        module = importlib.import_module(f"safer.adapters.{adapter_name}")
        src = inspect.getsource(module)
        if "check_or_raise(" not in src:
            missing.append(adapter_name)
    assert not missing, (
        "These tool-emitting adapters do not call `check_or_raise(...)` "
        "at the before_tool_use site, so a Policy Studio-authored "
        f"`Block any X` rule cannot stop the tool there: {missing}"
    )


def test_every_tool_adapter_imports_safer_blocked() -> None:
    """The cancellation contract assumes the adapter knows how to
    handle SaferBlocked — either by re-raising it (LangChain, OpenAI
    Agents, raw clients, CrewAI) or by translating it into the
    framework's native cancel signal (Strands `cancel_tool`, ADK dict
    return). Either way the symbol must be imported so the except
    branch can name it."""
    missing: list[str] = []
    for adapter_name in TOOL_ADAPTERS:
        module = importlib.import_module(f"safer.adapters.{adapter_name}")
        src = inspect.getsource(module)
        if "SaferBlocked" not in src:
            missing.append(adapter_name)
    assert not missing, (
        "These adapters wire check_or_raise but do not import "
        f"SaferBlocked, so they cannot intercept the block: {missing}"
    )
