"""End-to-end test for the AWS Strands adapter.

Drives a real `strands.Agent` through a complete tool-use turn with
`SaferHookProvider` instrumentation.

The only SAFER-specific lines are the ones from the README:

    from safer.adapters.strands import SaferHookProvider
    agent = Agent(model=..., tools=[...],
                  hooks=[SaferHookProvider(agent_id=..., agent_name=...)])

The agent is a system-diagnostic bot with `disk_usage` and a deliberately
dangerous `run_shell` tool — the pattern from the existing
`examples/strands` directory.  `run_shell` accepts arbitrary command
strings (no whitelist) — exactly the surface SAFER's Security persona
should flag at runtime.

Every other moving part — `Agent`, the event loop, tool dispatch, hook
registry — is real Strands code.  Only the model is a custom `Model`
subclass that yields canned `StreamEvent` dicts so the test stays
hermetic.
"""

from __future__ import annotations

import json

import pytest

import safer
from safer.client import clear_client


pytest.importorskip("strands.hooks")


@pytest.fixture(autouse=True)
def _reset_safer_runtime():
    clear_client()
    yield
    clear_client()


def _capture_events(client) -> list[dict]:
    captured: list[dict] = []

    def _patched(event):
        captured.append(event)

    client.transport.emit = _patched
    return captured


def test_strands_agent_emits_full_safer_lifecycle():
    from strands import Agent, tool
    from strands.models import Model

    # --- Tools the user would actually write -----------------------------

    @tool
    def disk_usage() -> str:
        """Stub `df -h` output for hermetic tests."""
        return "Filesystem  Size  Used  Avail  Use%\n/dev/disk1  500G  214G  286G   42%"

    @tool
    def run_shell(cmd: str) -> str:
        """Run an arbitrary shell command.

        Intentionally dangerous — no whitelist, no quoting, no sandbox.
        SAFER's Security persona surface."""
        return f"(stub for: {cmd})"

    # --- Model: yields canned StreamEvent dicts -------------------------

    class ScriptedModel(Model):
        """Yields canned StreamEvent dicts.  Each call to `stream()`
        consumes one scripted turn from the queue."""

        # `model_id` is what the SAFER adapter (and Strands itself)
        # surfaces for cost / dashboard.
        model_id = "claude-haiku-4-5"

        def __init__(self, turns):
            self._turns = turns
            self._idx = 0
            self.config = {"model_id": "claude-haiku-4-5"}

        def update_config(self, **kw): self.config.update(kw)
        def get_config(self): return self.config
        def structured_output(self, output_model, prompt, **kw): raise NotImplementedError

        async def stream(self, *args, **kw):
            events = self._turns[min(self._idx, len(self._turns) - 1)]
            self._idx += 1
            for ev in events:
                yield ev

    # First turn: model emits a tool_use(disk_usage) — Strands will run it
    tool_args = json.dumps({})
    turn_1 = [
        {"messageStart": {"role": "assistant"}},
        {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"toolUseId": "tu_disk", "name": "disk_usage"}},
            }
        },
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": tool_args}}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "tool_use"}},
        {
            "metadata": {
                "usage": {
                    "inputTokens": 30,
                    "outputTokens": 8,
                    "totalTokens": 38,
                    "cacheReadInputTokens": 0,
                    "cacheWriteInputTokens": 0,
                },
                "metrics": {"latencyMs": 42},
            }
        },
    ]
    # Second turn: model produces final text answer
    turn_2 = [
        {"messageStart": {"role": "assistant"}},
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"text": "Disk usage is 42% on /dev/disk1 — plenty of headroom."},
            }
        },
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "end_turn"}},
        {
            "metadata": {
                "usage": {
                    "inputTokens": 50,
                    "outputTokens": 18,
                    "totalTokens": 68,
                    "cacheReadInputTokens": 0,
                    "cacheWriteInputTokens": 0,
                },
                "metrics": {"latencyMs": 51},
            }
        },
    ]

    safer_client = safer.instrument(api_url="http://127.0.0.1:59999")
    events = _capture_events(safer_client)

    # --- README-pattern integration -------------------------------------
    from safer.adapters.strands import SaferHookProvider

    agent = Agent(
        model=ScriptedModel([turn_1, turn_2]),
        tools=[disk_usage, run_shell],
        system_prompt="You are a system-diagnostic assistant.",
        hooks=[
            SaferHookProvider(
                agent_id="system_diag",
                agent_name="System Diagnostic (Strands)",
            )
        ],
    )
    # ---------------------------------------------------------------------

    result = agent("What's my disk usage like?")
    final_text = ""
    for block in result.message.get("content", []):
        if isinstance(block, dict) and block.get("text"):
            final_text = block["text"]
            break
    assert "42%" in final_text or "headroom" in final_text.lower()

    hook_names = [e["hook"] for e in events]

    # 1. Session boundary
    assert hook_names[0] == "on_session_start"
    assert hook_names[-1] == "on_session_end"

    # 2. Two LLM call pairs (one per scripted turn)
    assert hook_names.count("before_llm_call") == 2
    assert hook_names.count("after_llm_call") == 2

    # 3. Tool decision + before/after_tool_use
    decisions = [e for e in events if e["hook"] == "on_agent_decision"]
    assert any(
        (d["chosen_action"] or "").startswith("disk_usage") for d in decisions
    ), f"expected a disk_usage decision; got {[d['chosen_action'] for d in decisions]}"

    before_tools = [e for e in events if e["hook"] == "before_tool_use"]
    after_tools = [e for e in events if e["hook"] == "after_tool_use"]
    assert any(t["tool_name"] == "disk_usage" for t in before_tools)
    disk_after = next(t for t in after_tools if t["tool_name"] == "disk_usage")
    assert "Filesystem" in disk_after["result"] or "/dev/" in disk_after["result"]

    # 4. Final output captured
    finals = [e for e in events if e["hook"] == "on_final_output"]
    assert len(finals) >= 1
    assert "42" in finals[-1]["final_response"] or "headroom" in finals[-1]["final_response"].lower()

    # 5. Cost — Haiku 4.5 priced via the shared pricing table.  Strands
    # quirk: `agent.event_loop_metrics.accumulated_usage` is updated AFTER
    # the AfterModelCallEvent fires, so the per-call delta the adapter
    # records is off-by-one (first call: 0 tokens; second call: first
    # turn's tokens).  The total session cost is still correct because
    # `total_cost_usd` accumulates across calls and `accumulated_usage`
    # reaches its final value by AfterInvocation.
    afters = [e for e in events if e["hook"] == "after_llm_call"]
    assert sum(a["cost_usd"] for a in afters) > 0, (
        f"expected non-zero total cost across LLM calls; got {[a['cost_usd'] for a in afters]}"
    )

    # 6. Session-level total reflects the cumulative real cost
    end = next(e for e in events if e["hook"] == "on_session_end")
    assert end["total_cost_usd"] > 0
    # The shared accumulator produces the same total as summing afters
    assert abs(end["total_cost_usd"] - sum(a["cost_usd"] for a in afters)) < 1e-9
