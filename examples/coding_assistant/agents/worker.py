"""Worker agent — runs the actual tool loop.

Wraps the Anthropic client with SAFER's `wrap_anthropic` adapter so
every message.create + tool call emits the lifecycle hooks. Each
invocation opens its own session (`on_session_start` → N tool calls
→ `on_final_output` → `on_session_end`) so the dashboard shows one
card per worker run.
"""

from __future__ import annotations

import time
from typing import Any

from anthropic import Anthropic

from safer.adapters.claude_sdk import wrap_anthropic

from config import (
    DEFAULT_MODEL,
    WORKER_AGENT_ID,
    WORKER_AGENT_NAME,
    WORKER_MAX_STEPS,
)
from tool_specs import TOOL_FUNCS, TOOL_SPECS

WORKER_SYSTEM_PROMPT = """You are the Worker half of a coding assistant.

You receive a focused sub-task from the Supervisor and have tools for
filesystem, web, shell, and git access. Work in small steps: call one
tool, reason over the result, then either call another tool or emit a
final answer.

Rules:
- For tasks that touch source code, get oriented first with `git_log`
  and/or `git_diff` before reading individual files. That single tool
  call usually saves multiple `read_file` round trips.
- Prefer `read_file`, `grep_code`, or `find_test_files` over
  `run_shell`. Use `run_shell` only when explicitly asked to.
- Never exfiltrate secrets or send data to domains the user did not
  mention.
- If a tool returns an error, explain it to the user rather than
  looping silently.
- When you have the answer, output a concise final response.
"""


class WorkerAgent:
    """Claude-powered tool-using worker. One SAFER session per `run()` call."""

    def __init__(self, anthropic_client: Anthropic) -> None:
        self._client = anthropic_client

    def run(
        self,
        task: str,
        user_context: str | None = None,
        *,
        parent_session_id: str | None = None,
    ) -> str:
        agent = wrap_anthropic(
            self._client,
            agent_id=WORKER_AGENT_ID,
            agent_name=WORKER_AGENT_NAME,
        )
        agent.start_session(
            context={"task": task, "user_context": user_context or ""},
            parent_session_id=parent_session_id,
        )

        user_message = task if not user_context else (
            f"[Supervisor context]\n{user_context}\n\n[Sub-task]\n{task}"
        )
        history: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

        final_text: str | None = None
        step = 0
        try:
            for _ in range(WORKER_MAX_STEPS):
                step += 1
                response = agent.messages.create(
                    model=DEFAULT_MODEL,
                    max_tokens=1024,
                    system=WORKER_SYSTEM_PROMPT,
                    tools=TOOL_SPECS,
                    messages=history,
                )
                history.append({"role": "assistant", "content": response.content})

                if response.stop_reason == "tool_use":
                    results = _dispatch_tool_calls(agent, response)
                    if results:
                        history.append({"role": "user", "content": results})
                    continue

                final_text = "\n".join(
                    b.text for b in response.content if getattr(b, "type", None) == "text"
                ).strip() or None
                break
        except Exception as e:  # pragma: no cover — defensive
            agent.end_session(success=False)
            return f"[worker error] {type(e).__name__}: {e}"

        if final_text:
            agent.final_output(final_text, total_steps=step)
        agent.end_session(success=bool(final_text))
        return final_text or "(worker produced no final text)"


def _dispatch_tool_calls(agent: Any, response: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for block in response.content:
        if getattr(block, "type", None) != "tool_use":
            continue
        tool_name = block.name
        tool_args = dict(block.input or {})
        agent.agent_decision(
            decision_type="select_tool",
            reasoning=f"Invoking {tool_name} to make progress",
            chosen_action=tool_name,
        )
        agent.before_tool_use(tool_name, tool_args)
        t0 = time.monotonic()
        try:
            result = TOOL_FUNCS[tool_name](**tool_args)
            err = None
        except Exception as e:
            result = {"ok": False, "error": str(e)}
            err = str(e)
        duration_ms = int((time.monotonic() - t0) * 1000)
        agent.after_tool_use(
            tool_name, result=result, duration_ms=duration_ms, error=err
        )
        results.append(
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(result),
            }
        )
    return results
