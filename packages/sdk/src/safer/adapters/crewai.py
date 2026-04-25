"""CrewAI adapter — `SaferCrewListener`.

CrewAI ships a global event bus (`crewai.events.crewai_event_bus`) and
a `BaseEventListener` API. Subclassing the listener gives us a single,
deterministic place to translate every CrewAI event into the SAFER
9-hook contract:

| CrewAI event                | SAFER hook                         |
|-----------------------------|------------------------------------|
| CrewKickoffStartedEvent     | on_session_start                   |
| LLMCallStartedEvent         | before_llm_call                    |
| LLMCallCompletedEvent       | after_llm_call                     |
| LLMCallFailedEvent          | on_error                           |
| ToolUsageStartedEvent       | on_agent_decision + before_tool_use|
| ToolUsageFinishedEvent      | after_tool_use                     |
| ToolUsageErrorEvent         | on_error                           |
| CrewKickoffCompletedEvent   | on_final_output + on_session_end   |
| CrewKickoffFailedEvent      | on_error + on_session_end          |

Usage:

    from crewai import Agent, Task, Crew
    from safer.adapters.crewai import SaferCrewListener

    listener = SaferCrewListener(
        agent_id="research_crew",
        agent_name="Research Crew",
    )

    crew = Crew(agents=[...], tasks=[...])
    crew.kickoff(inputs={"topic": "..."})  # listener fires automatically

The listener attaches itself to the global event bus on construction;
`pin_session=True` (default) keeps every `crew.kickoff()` invocation
under the same SAFER session_id with `on_session_end` deferred to
process exit (atexit). Pass `pin_session=False` for one session per
kickoff (the more granular dashboard view).
"""

from __future__ import annotations

import atexit
import json
import logging
import time
import uuid
from typing import Any

from ..client import SaferClient, get_client
from ..events import (
    AfterLLMCallPayload,
    AfterToolUsePayload,
    BeforeLLMCallPayload,
    BeforeToolUsePayload,
    OnAgentDecisionPayload,
    OnErrorPayload,
    OnFinalOutputPayload,
    OnSessionEndPayload,
    OnSessionStartPayload,
)

log = logging.getLogger("safer.adapters.crewai")


# ---------- pricing ----------


# Same pricing table used by other adapters; CrewAI hands us model ids
# verbatim so we can route through whichever provider's pricing applies.
_PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-4-7": (15.0, 75.0, 1.5, 18.75),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75),
    "claude-haiku-4-5": (0.80, 4.0, 0.08, 1.0),
    "gpt-4o": (2.50, 10.0, 0.0, 0.0),
    "gpt-4o-mini": (0.15, 0.60, 0.0, 0.0),
    "gpt-4-turbo": (10.0, 30.0, 0.0, 0.0),
}


def _estimate_cost(model: str, tin: int, tout: int) -> float:
    if not model:
        return 0.0
    p_in, p_out, _, _ = _PRICING.get(
        model,
        next(
            (v for k, v in _PRICING.items() if model.startswith(k)),
            (0.0, 0.0, 0.0, 0.0),
        ),
    )
    return (tin * p_in + tout * p_out) / 1_000_000


# ---------- the listener ----------


def _build_listener_class() -> type:
    """Build the SaferCrewListener class lazily so importing this module
    is safe even when crewai is not installed."""
    try:
        from crewai.events import (  # type: ignore[import-not-found]
            BaseEventListener,
            CrewKickoffCompletedEvent,
            CrewKickoffFailedEvent,
            CrewKickoffStartedEvent,
            LLMCallCompletedEvent,
            LLMCallFailedEvent,
            LLMCallStartedEvent,
            ToolUsageErrorEvent,
            ToolUsageFinishedEvent,
            ToolUsageStartedEvent,
        )
    except ImportError as e:
        raise ImportError(
            "SaferCrewListener requires `crewai`. "
            "Install with `pip install crewai`."
        ) from e

    class _SaferCrewListener(BaseEventListener):  # type: ignore[misc, valid-type]
        """SAFER instrumentation for CrewAI's global event bus."""

        def __init__(
            self,
            *,
            agent_id: str,
            agent_name: str | None = None,
            session_id: str | None = None,
            client: SaferClient | None = None,
            pin_session: bool = True,
        ) -> None:
            from ._bootstrap import ensure_runtime

            ensure_runtime(agent_id, agent_name)
            self.agent_id = agent_id
            self.agent_name = agent_name or agent_id
            self._initial_session_id = session_id
            self._current_session_id: str | None = None
            self._client = client
            self._pin_session = pin_session
            self._sequence = 0
            self._step_count = 0
            self._session_started = False
            self._session_start_ts: float | None = None
            self._total_cost_usd = 0.0
            # call_id -> started_ts (LLM)
            self._llm_start_ts: dict[str, float] = {}
            # tool tracking key -> started_ts (no native id; use agent+name)
            self._tool_start_ts: dict[str, float] = {}
            self._atexit_done = False
            atexit.register(self._atexit_close)
            super().__init__()

        # ---------- internal plumbing ----------

        @property
        def session_id(self) -> str:
            if self._current_session_id is None:
                self._current_session_id = (
                    self._initial_session_id or f"sess_{uuid.uuid4().hex[:16]}"
                )
            return self._current_session_id

        def _next_seq(self) -> int:
            client = self._client or get_client()
            if client is not None:
                try:
                    return client.next_sequence(self.session_id)
                except Exception:
                    pass
            n = self._sequence
            self._sequence += 1
            return n

        def _emit(self, event: Any) -> None:
            client = self._client or get_client()
            if client is None:
                return
            try:
                client.emit(event)
            except Exception as e:  # pragma: no cover
                log.debug("crewai listener emit failed: %s", e)

        def _ensure_session(self) -> None:
            if self._session_started:
                return
            self._session_started = True
            self._session_start_ts = time.monotonic()
            self._emit(
                OnSessionStartPayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_seq(),
                    agent_name=self.agent_name,
                    source="adapter:crewai",
                )
            )

        def _close_session(self, *, success: bool) -> None:
            if not self._session_started:
                return
            duration = (
                int((time.monotonic() - self._session_start_ts) * 1000)
                if self._session_start_ts is not None
                else 0
            )
            self._emit(
                OnSessionEndPayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_seq(),
                    total_duration_ms=duration,
                    total_cost_usd=self._total_cost_usd,
                    success=success,
                    source="adapter:crewai",
                )
            )
            self._session_started = False
            if not self._pin_session:
                self._current_session_id = None  # rotate

        def _atexit_close(self) -> None:
            if self._atexit_done:
                return
            self._atexit_done = True
            try:
                self._close_session(success=True)
            except Exception:  # pragma: no cover
                pass

        def close_session(self, *, success: bool = True) -> None:
            """Manually emit `on_session_end` (mostly for tests/REPLs)."""
            self._close_session(success=success)

        # ---------- listener registration ----------

        def setup_listeners(self, bus: Any) -> None:
            """Wire every CrewAI event we care about onto the bus."""
            ev = self  # captured for closures

            @bus.on(CrewKickoffStartedEvent)
            def _on_kickoff_start(_source, event):
                try:
                    ev._ensure_session()
                except Exception as e:  # pragma: no cover
                    log.debug("crew kickoff start emit failed: %s", e)

            @bus.on(CrewKickoffCompletedEvent)
            def _on_kickoff_done(_source, event):
                try:
                    output = getattr(event, "output", None)
                    text = ""
                    if output is not None:
                        # CrewOutput.raw is a string
                        text = str(getattr(output, "raw", "") or output)[:4000]
                    ev._emit(
                        OnFinalOutputPayload(
                            session_id=ev.session_id,
                            agent_id=ev.agent_id,
                            sequence=ev._next_seq(),
                            final_response=text,
                            total_steps=ev._step_count,
                            source="adapter:crewai",
                        )
                    )
                    if not ev._pin_session:
                        ev._close_session(success=True)
                except Exception as e:  # pragma: no cover
                    log.debug("crew kickoff complete emit failed: %s", e)

            @bus.on(CrewKickoffFailedEvent)
            def _on_kickoff_fail(_source, event):
                try:
                    msg = str(getattr(event, "error", "") or "")
                    ev._emit(
                        OnErrorPayload(
                            session_id=ev.session_id,
                            agent_id=ev.agent_id,
                            sequence=ev._next_seq(),
                            error_type="crew_kickoff_failed",
                            message=msg[:2000],
                            source="adapter:crewai",
                        )
                    )
                    if not ev._pin_session:
                        ev._close_session(success=False)
                except Exception as e:  # pragma: no cover
                    log.debug("crew kickoff fail emit failed: %s", e)

            @bus.on(LLMCallStartedEvent)
            def _on_llm_start(_source, event):
                try:
                    ev._ensure_session()
                    call_id = str(getattr(event, "call_id", "") or "")
                    if call_id:
                        ev._llm_start_ts[call_id] = time.monotonic()
                    model = str(getattr(event, "model", "unknown") or "unknown")
                    messages = getattr(event, "messages", None) or []
                    prompt_text = _summarize_messages(messages)
                    tools = getattr(event, "tools", None) or []
                    norm_tools = []
                    for t in tools or []:
                        if isinstance(t, dict):
                            fn = (t.get("function") or {}) if "function" in t else t
                            norm_tools.append(
                                {
                                    "name": str(fn.get("name") or ""),
                                    "description": fn.get("description"),
                                }
                            )
                    ev._emit(
                        BeforeLLMCallPayload(
                            session_id=ev.session_id,
                            agent_id=ev.agent_id,
                            sequence=ev._next_seq(),
                            model=model,
                            prompt=prompt_text[:8000],
                            tools=norm_tools,
                            source="adapter:crewai",
                        )
                    )
                except Exception as e:  # pragma: no cover
                    log.debug("crew llm start emit failed: %s", e)

            @bus.on(LLMCallCompletedEvent)
            def _on_llm_done(_source, event):
                try:
                    call_id = str(getattr(event, "call_id", "") or "")
                    started = ev._llm_start_ts.pop(call_id, None)
                    latency_ms = (
                        int((time.monotonic() - started) * 1000) if started else 0
                    )
                    model = str(getattr(event, "model", "unknown") or "unknown")
                    response = getattr(event, "response", None)
                    response_text = _safe_str(response, 8000)
                    usage = getattr(event, "usage", None) or {}
                    if not isinstance(usage, dict):
                        # Some CrewAI versions hand us a Pydantic model
                        usage = (
                            usage.model_dump()
                            if hasattr(usage, "model_dump")
                            else {}
                        )
                    tokens_in = int(
                        usage.get("prompt_tokens")
                        or usage.get("input_tokens")
                        or 0
                    )
                    tokens_out = int(
                        usage.get("completion_tokens")
                        or usage.get("output_tokens")
                        or 0
                    )
                    cost = _estimate_cost(model, tokens_in, tokens_out)
                    ev._total_cost_usd += cost
                    ev._step_count += 1
                    ev._emit(
                        AfterLLMCallPayload(
                            session_id=ev.session_id,
                            agent_id=ev.agent_id,
                            sequence=ev._next_seq(),
                            model=model,
                            response=response_text,
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                            cost_usd=cost,
                            latency_ms=latency_ms,
                            source="adapter:crewai",
                        )
                    )
                except Exception as e:  # pragma: no cover
                    log.debug("crew llm complete emit failed: %s", e)

            @bus.on(LLMCallFailedEvent)
            def _on_llm_fail(_source, event):
                try:
                    msg = str(getattr(event, "error", "") or "")
                    ev._emit(
                        OnErrorPayload(
                            session_id=ev.session_id,
                            agent_id=ev.agent_id,
                            sequence=ev._next_seq(),
                            error_type="llm_call_failed",
                            message=msg[:2000],
                            source="adapter:crewai",
                        )
                    )
                except Exception as e:  # pragma: no cover
                    log.debug("crew llm fail emit failed: %s", e)

            @bus.on(ToolUsageStartedEvent)
            def _on_tool_start(_source, event):
                try:
                    ev._ensure_session()
                    tool_name = str(getattr(event, "tool_name", "") or "tool")
                    tool_args = getattr(event, "tool_args", None) or {}
                    if isinstance(tool_args, str):
                        try:
                            tool_args = json.loads(tool_args)
                        except Exception:
                            tool_args = {"_raw": tool_args}
                    if not isinstance(tool_args, dict):
                        tool_args = {"input": tool_args}
                    args_repr = _safe_str(tool_args, 1000)
                    ev._emit(
                        OnAgentDecisionPayload(
                            session_id=ev.session_id,
                            agent_id=ev.agent_id,
                            sequence=ev._next_seq(),
                            decision_type="tool_call",
                            chosen_action=f"{tool_name}({args_repr})",
                            source="adapter:crewai",
                        )
                    )
                    ev._emit(
                        BeforeToolUsePayload(
                            session_id=ev.session_id,
                            agent_id=ev.agent_id,
                            sequence=ev._next_seq(),
                            tool_name=tool_name,
                            args=tool_args,
                            source="adapter:crewai",
                        )
                    )
                    key = _tool_key(event)
                    ev._tool_start_ts[key] = time.monotonic()
                except Exception as e:  # pragma: no cover
                    log.debug("crew tool start emit failed: %s", e)

            @bus.on(ToolUsageFinishedEvent)
            def _on_tool_done(_source, event):
                try:
                    tool_name = str(getattr(event, "tool_name", "") or "tool")
                    output = getattr(event, "output", "")
                    key = _tool_key(event)
                    started = ev._tool_start_ts.pop(key, None)
                    duration_ms = (
                        int((time.monotonic() - started) * 1000) if started else 0
                    )
                    ev._emit(
                        AfterToolUsePayload(
                            session_id=ev.session_id,
                            agent_id=ev.agent_id,
                            sequence=ev._next_seq(),
                            tool_name=tool_name,
                            result=_safe_str(output, 4000),
                            duration_ms=duration_ms,
                            source="adapter:crewai",
                        )
                    )
                except Exception as e:  # pragma: no cover
                    log.debug("crew tool done emit failed: %s", e)

            @bus.on(ToolUsageErrorEvent)
            def _on_tool_err(_source, event):
                try:
                    err = getattr(event, "error", None)
                    ev._emit(
                        OnErrorPayload(
                            session_id=ev.session_id,
                            agent_id=ev.agent_id,
                            sequence=ev._next_seq(),
                            error_type="tool_error",
                            message=_safe_str(err, 2000),
                            source="adapter:crewai",
                        )
                    )
                except Exception as e:  # pragma: no cover
                    log.debug("crew tool err emit failed: %s", e)

    return _SaferCrewListener


# ---------- helpers ----------


def _summarize_messages(messages: list[Any]) -> str:
    parts: list[str] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role") or "?"
        content = m.get("content")
        if isinstance(content, str):
            parts.append(f"[{role}] {content}")
        elif isinstance(content, list):
            text_bits = [
                c.get("text", "") if isinstance(c, dict) else "" for c in content
            ]
            parts.append(f"[{role}] " + " ".join(t for t in text_bits if t))
    return "\n".join(parts)


def _safe_str(obj: Any, limit: int = 2000) -> str:
    try:
        if obj is None:
            return ""
        if isinstance(obj, (dict, list)):
            return json.dumps(obj, default=str)[:limit]
        return str(obj)[:limit]
    except Exception:
        return ""


def _tool_key(event: Any) -> str:
    """A stable-enough identifier to pair tool start/finish in our state."""
    name = str(getattr(event, "tool_name", "") or "tool")
    agent_key = str(getattr(event, "agent_key", "") or "")
    eid = str(getattr(event, "event_id", "") or "")
    return f"{agent_key}:{name}:{eid}"


# ---------- public proxy ----------


_CACHED_LISTENER_CLASS: type | None = None


def _get_listener_class() -> type:
    global _CACHED_LISTENER_CLASS
    if _CACHED_LISTENER_CLASS is None:
        _CACHED_LISTENER_CLASS = _build_listener_class()
    return _CACHED_LISTENER_CLASS


class SaferCrewListener:
    """Public entry point — constructs the real listener on first use.

    Pass an instance to be alive while your CrewAI code runs (or assign
    it to a module-level variable so it persists for the process)."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        real = _get_listener_class()
        return real(*args, **kwargs)


def wrap_crew(crew: Any, *, agent_id: str, agent_name: str | None = None) -> Any:
    """Backward-compatible helper.

    The CrewAI integration is event-bus based, so the crew object
    itself doesn't need wrapping — instantiating `SaferCrewListener`
    once is enough. This function constructs a listener (for side
    effect) and returns the crew unchanged so older code doing
    `crew = wrap_crew(crew, ...)` still works."""
    SaferCrewListener(agent_id=agent_id, agent_name=agent_name)
    return crew


__all__ = ["SaferCrewListener", "wrap_crew"]
