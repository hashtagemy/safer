"""End-to-end test for the CrewAI adapter.

Drives the real CrewAI event bus by hand-emitting the events a real
`crew.kickoff()` would produce — same path as the production listener,
no mock buses. We're verifying the listener wires up correctly and
each CrewAI event maps to the documented SAFER hook.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


pytest.importorskip("crewai")


def _emit(bus, event, source: object | None = None):
    """Emit synchronously: wait on the future the bus returns so all
    sync handlers (including ours) finish before the test asserts."""
    fut = bus.emit(source if source is not None else SimpleNamespace(), event)
    if fut is not None:
        try:
            fut.result(timeout=5.0)
        except Exception:
            pass


def test_crewai_listener_translates_full_lifecycle(captured_events):
    from crewai.events import (
        CrewKickoffCompletedEvent,
        CrewKickoffStartedEvent,
        LLMCallCompletedEvent,
        LLMCallStartedEvent,
        ToolUsageFinishedEvent,
        ToolUsageStartedEvent,
        crewai_event_bus,
    )

    from safer.adapters.crewai import SaferCrewListener

    # `scoped_handlers()` saves the bus's handler registry and restores
    # it on exit so this test cannot pollute later tests (and vice
    # versa). Without scoping, every `SaferCrewListener(...)` instance
    # would still receive events from later tests because the bus is a
    # process-wide singleton.
    with crewai_event_bus.scoped_handlers():
        listener = SaferCrewListener(
            agent_id="crewai_demo",
            agent_name="CrewAI Demo",
            # `pin_session=False` lets `kickoff_complete` close the
            # session synchronously so we can assert on_session_end
            # without touching atexit machinery.
            pin_session=False,
        )

        crew = SimpleNamespace(name="research_crew", fingerprint=None)

        _emit(
            crewai_event_bus,
            CrewKickoffStartedEvent(
                crew_name="research_crew",
                crew=crew,
                inputs={"topic": "agents"},
            ),
        )
        _emit(
            crewai_event_bus,
            LLMCallStartedEvent(
                from_task=None,
                from_agent=None,
                model="claude-haiku-4-5",
                call_id="call_1",
                messages=[{"role": "user", "content": "research agents"}],
                tools=[
                    {
                        "function": {
                            "name": "web_search",
                            "description": "Search the web",
                        }
                    }
                ],
                callbacks=[],
                available_functions={},
            ),
        )
        _emit(
            crewai_event_bus,
            LLMCallCompletedEvent(
                from_task=None,
                from_agent=None,
                model="claude-haiku-4-5",
                call_id="call_1",
                messages=[{"role": "user", "content": "research agents"}],
                response="Sure, searching now.",
                call_type="llm_call",
                usage={"prompt_tokens": 50, "completion_tokens": 10},
            ),
        )
        _emit(
            crewai_event_bus,
            ToolUsageStartedEvent(
                agent_key="researcher",
                tool_name="web_search",
                tool_args={"query": "agents 2026"},
                tool_class="WebSearchTool",
                run_attempts=1,
                delegations=0,
                agent=None,
                from_task=None,
                from_agent=None,
            ),
        )
        now = datetime.now(timezone.utc)
        _emit(
            crewai_event_bus,
            ToolUsageFinishedEvent(
                agent_key="researcher",
                tool_name="web_search",
                tool_args={"query": "agents 2026"},
                tool_class="WebSearchTool",
                run_attempts=1,
                delegations=0,
                agent=None,
                from_task=None,
                from_agent=None,
                started_at=now,
                finished_at=now,
                from_cache=False,
                output="3 results found.",
            ),
        )
        _emit(
            crewai_event_bus,
            CrewKickoffCompletedEvent(
                crew_name="research_crew",
                crew=crew,
                output=SimpleNamespace(raw="Final report: agents are everywhere."),
                total_tokens=60,
            ),
        )

        listener.close_session(success=True)

    hooks = [e["hook"] for e in captured_events]
    assert hooks[0] == "on_agent_register"
    assert "on_session_start" in hooks
    assert hooks.count("before_llm_call") == 1
    assert hooks.count("after_llm_call") == 1
    assert hooks.count("on_agent_decision") == 1
    assert hooks.count("before_tool_use") == 1
    assert hooks.count("after_tool_use") == 1
    assert hooks.count("on_final_output") == 1
    assert hooks.count("on_session_end") == 1

    # Tool name + args carried through faithfully
    before_tool = next(e for e in captured_events if e["hook"] == "before_tool_use")
    assert before_tool["tool_name"] == "web_search"
    assert before_tool["args"] == {"query": "agents 2026"}
    after_tool = next(e for e in captured_events if e["hook"] == "after_tool_use")
    assert after_tool["tool_name"] == "web_search"
    assert "3 results" in after_tool["result"]

    # Final output carries the crew's raw text
    final = next(e for e in captured_events if e["hook"] == "on_final_output")
    assert "agents are everywhere" in final["final_response"]

    # Cost tracking — Claude Haiku priced via the shared table
    afters = [e for e in captured_events if e["hook"] == "after_llm_call"]
    assert all(a["tokens_in"] > 0 for a in afters)


def test_crewai_listener_pin_session_keeps_one_session(captured_events):
    """With pin_session=True, two consecutive kickoffs share one session."""
    from crewai.events import (
        CrewKickoffCompletedEvent,
        CrewKickoffStartedEvent,
        crewai_event_bus,
    )

    from safer.adapters.crewai import SaferCrewListener

    with crewai_event_bus.scoped_handlers():
        SaferCrewListener(
            agent_id="crewai_pin",
            agent_name="CrewAI Pin",
            pin_session=True,
        )
        crew = SimpleNamespace(name="pin_crew", fingerprint=None)

        for _ in range(2):
            _emit(
                crewai_event_bus,
                CrewKickoffStartedEvent(
                    crew_name="pin_crew", crew=crew, inputs={}
                ),
            )
            _emit(
                crewai_event_bus,
                CrewKickoffCompletedEvent(
                    crew_name="pin_crew",
                    crew=crew,
                    output=SimpleNamespace(raw="ok"),
                    total_tokens=0,
                ),
            )

    runtime = [e for e in captured_events if e["hook"] != "on_agent_register"]
    session_ids = {e["session_id"] for e in runtime}
    assert len(session_ids) == 1
    hooks = [e["hook"] for e in runtime]
    assert hooks.count("on_session_start") == 1
    # `on_session_end` deferred to atexit / manual close — not here
    assert "on_session_end" not in hooks
    assert hooks.count("on_final_output") == 2
