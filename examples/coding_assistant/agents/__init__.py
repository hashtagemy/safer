"""Supervisor + Worker agent pair for the coding-assistant demo."""

from coding_assistant.agents.supervisor import SupervisorAgent
from coding_assistant.agents.worker import WorkerAgent

__all__ = ["SupervisorAgent", "WorkerAgent"]
