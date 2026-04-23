"""Supervisor agent — decides whether to delegate to the Worker.

Small Opus call routes each user turn either to a direct text answer
or to the Worker (which has tool access). Opens its own SAFER session
per user turn so the dashboard shows the supervisor + worker side by
side in `/live`.
"""

from __future__ import annotations

import json
import re
from typing import Any

from anthropic import Anthropic

from safer.adapters.claude_sdk import wrap_anthropic

from agents.worker import WorkerAgent
from config import (
    DEFAULT_MODEL,
    SUPERVISOR_AGENT_ID,
    SUPERVISOR_AGENT_NAME,
)
from memory import ConversationMemory

SUPERVISOR_SYSTEM_PROMPT = """You are the Supervisor of a coding assistant.

For each user turn, decide what should happen next and answer with a
SINGLE JSON object — no prose, no markdown — using this shape:

{
  "decision": "answer" | "delegate",
  "message": "string — either the direct answer to the user, or the sub-task for the Worker"
}

Rules:
- Use "answer" for greetings, clarifying questions, explanations that
  need no file / shell / web access.
- Use "delegate" when the Worker should run tools (filesystem, web,
  shell). In that case, make the sub-task specific and self-contained.
- Do not invent facts. If unsure what the user wants, ask.
"""


class SupervisorAgent:
    def __init__(
        self,
        anthropic_client: Anthropic,
        worker: WorkerAgent,
        memory: ConversationMemory,
    ) -> None:
        self._client = anthropic_client
        self._worker = worker
        self._memory = memory

    def handle(self, user_message: str) -> str:
        self._memory.add_user(user_message)

        agent = wrap_anthropic(
            self._client,
            agent_id=SUPERVISOR_AGENT_ID,
            agent_name=SUPERVISOR_AGENT_NAME,
        )
        agent.start_session(context={"user_message": user_message})
        reply: str | None = None
        try:
            response = agent.messages.create(
                model=DEFAULT_MODEL,
                max_tokens=512,
                system=SUPERVISOR_SYSTEM_PROMPT,
                messages=self._memory.as_messages(),
            )
            text = _collect_text(response.content)
            decision_obj = _parse_json_decision(text)

            if decision_obj.get("decision") == "delegate":
                sub_task = decision_obj.get("message") or user_message
                agent.agent_decision(
                    decision_type="delegate_to_worker",
                    reasoning="Sub-task requires tool access",
                    chosen_action="worker.run",
                )
                worker_reply = self._worker.run(
                    task=sub_task,
                    user_context=user_message,
                    parent_session_id=agent.session_id,
                )
                reply = worker_reply
            else:
                reply = decision_obj.get("message") or text
                agent.agent_decision(
                    decision_type="direct_answer",
                    reasoning="No tool access required",
                    chosen_action="respond",
                )

            if reply:
                agent.final_output(reply)
        except Exception as e:  # pragma: no cover — defensive
            reply = f"[supervisor error] {type(e).__name__}: {e}"
        finally:
            agent.end_session(success=bool(reply))

        final = reply or "(supervisor produced no reply)"
        self._memory.add_assistant(final)
        return final


def _collect_text(content: list[Any]) -> str:
    parts = [b.text for b in content if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip()


def _parse_json_decision(text: str) -> dict[str, Any]:
    """Pull out a `{decision, message}` object even if the model adds fences."""
    if not text:
        return {"decision": "answer", "message": ""}
    stripped = text.strip()
    # Trim code fences if the model ignored the no-markdown instruction.
    fence = re.match(r"```(?:json)?\s*(.+?)\s*```", stripped, flags=re.DOTALL)
    if fence:
        stripped = fence.group(1)
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return {"decision": "answer", "message": text}
    if not isinstance(obj, dict):
        return {"decision": "answer", "message": text}
    return obj
