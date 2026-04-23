"""Tiny in-memory conversation store shared between supervisor and worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConversationMemory:
    """Append-only transcript. Last N turns used as Claude message history."""

    turns: list[dict[str, Any]] = field(default_factory=list)
    max_history: int = 20

    def add_user(self, text: str) -> None:
        self.turns.append({"role": "user", "content": text})

    def add_assistant(self, text: str) -> None:
        self.turns.append({"role": "assistant", "content": text})

    def as_messages(self) -> list[dict[str, Any]]:
        """Messages formatted for `anthropic.messages.create`."""
        return list(self.turns[-self.max_history :])

    def clear(self) -> None:
        self.turns.clear()
